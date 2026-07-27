from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from . import __version__
from .auth import new_session_id, verify_device
from .config import Settings, get_settings
from .ha_auth import HaAuthError, HaAuthService, TokenStore
from .models import (
    AudioBootstrap,
    HaSessionInfo,
    HealthResponse,
    SessionBootstrap,
    SessionStartRequest,
    SessionStartResponse,
    XaiSessionInfo,
)
from .pairing_ui import render_pairing_page
from .tools import default_client_function_tools
from .xai_tokens import XaiTokenError, mint_ephemeral_token

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    token_store = TokenStore(settings.data_dir / "ha_tokens.json")
    ha_auth = HaAuthService(settings, token_store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.http = httpx.AsyncClient()
        app.state.token_store = token_store
        app.state.ha_auth = ha_auth
        logger.info(
            "control plane starting host=%s port=%s devices=%d oauth_client_id=%s",
            settings.listen_host,
            settings.listen_port,
            len(settings.device_tokens),
            settings.effective_oauth_client_id,
        )
        yield
        await app.state.http.aclose()

    app = FastAPI(
        title="HA Realtime Voice Control Plane",
        version=__version__,
        lifespan=lifespan,
    )

    def get_app_settings(request: Request) -> Settings:
        return request.app.state.settings

    def _pairing_html(
        request: Request,
        *,
        message: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        settings_dep = get_app_settings(request)
        ha_service: HaAuthService = request.app.state.ha_auth
        html = render_pairing_page(
            version=__version__,
            ha_base_url=settings_dep.ha_base_url,
            public_base_url=settings_dep.effective_public_base_url,
            linked=ha_service.credentials_configured(),
            xai_configured=bool(settings_dep.xai_api_key),
            devices_configured=len(settings_dep.device_tokens),
            message=message,
            error=error,
        )
        return HTMLResponse(html)

    @app.get("/", response_class=HTMLResponse)
    async def pairing_home(request: Request) -> HTMLResponse:
        return _pairing_html(request)

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        settings_dep = get_app_settings(request)
        ha_service: HaAuthService = request.app.state.ha_auth
        return HealthResponse(
            ok=True,
            version=__version__,
            xai_configured=bool(settings_dep.xai_api_key),
            devices_configured=len(settings_dep.device_tokens),
            ha_base_url_configured=bool(settings_dep.ha_base_url),
            ha_credentials_configured=ha_service.credentials_configured(),
            public_base_url_configured=bool(settings_dep.effective_public_base_url),
            ha_oauth_linked=ha_service.credentials_configured(),
        )

    @app.get("/oauth/start")
    async def oauth_start(request: Request) -> RedirectResponse:
        settings_dep = get_app_settings(request)
        ha_service: HaAuthService = request.app.state.ha_auth
        store: TokenStore = request.app.state.token_store

        redirect_uri = settings_dep.oauth_redirect_uri
        if not settings_dep.ha_base_url:
            raise HTTPException(status_code=400, detail="HA_BASE_URL is not configured")
        if not redirect_uri:
            raise HTTPException(
                status_code=400,
                detail="PUBLIC_BASE_URL is not configured (needed for OAuth redirect)",
            )

        client_id = settings_dep.effective_oauth_client_id
        state = store.begin_oauth_state(redirect_uri=redirect_uri, client_id=client_id)
        try:
            url = ha_service.authorize_url(redirect_uri=redirect_uri, state=state)
        except HaAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url, status_code=status.HTTP_302_FOUND)

    @app.get("/oauth/callback", response_class=HTMLResponse)
    async def oauth_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ) -> HTMLResponse:
        if error:
            detail = error_description or error
            return _pairing_html(request, error=f"Home Assistant denied access: {detail}")
        if not code or not state:
            return _pairing_html(request, error="Missing code/state from Home Assistant")

        store: TokenStore = request.app.state.token_store
        pending = store.consume_oauth_state(state)
        if not pending:
            return _pairing_html(request, error="Invalid or expired OAuth state — try Link again")

        ha_service: HaAuthService = request.app.state.ha_auth
        http: httpx.AsyncClient = request.app.state.http
        try:
            await ha_service.exchange_code(
                code=code,
                redirect_uri=pending["redirect_uri"],
                client_id=pending["client_id"],
                client=http,
            )
        except HaAuthError as exc:
            logger.exception("OAuth code exchange failed")
            return _pairing_html(request, error=f"Token exchange failed: {exc}")

        return _pairing_html(request, message="Home Assistant linked. Refresh tokens are stored on this add-on.")

    @app.post("/oauth/unlink", response_class=HTMLResponse)
    async def oauth_unlink(request: Request) -> HTMLResponse:
        ha_service: HaAuthService = request.app.state.ha_auth
        ha_service.unlink()
        return _pairing_html(request, message="Home Assistant unlinked.")

    @app.post("/v1/session/start", response_model=SessionStartResponse)
    async def session_start(
        request: Request,
        body: SessionStartRequest,
        x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
        x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    ) -> SessionStartResponse:
        settings_dep = get_app_settings(request)
        device_id = verify_device(settings_dep, x_device_id, x_device_token)
        http: httpx.AsyncClient = request.app.state.http
        ha_service: HaAuthService = request.app.state.ha_auth

        try:
            minted = await mint_ephemeral_token(settings_dep, http)
        except XaiTokenError as exc:
            logger.exception("xAI mint failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc

        model = settings_dep.default_model
        realtime_url = f"{settings_dep.xai_realtime_base}?model={model}"

        ha_info: HaSessionInfo | None = None
        include_ha_tools = False
        if settings_dep.ha_base_url and settings_dep.ha_mcp_url:
            access = await ha_service.get_access_token(device_id, http)
            if access:
                include_ha_tools = body.capabilities.tools_mode == "client_functions"
                ha_info = HaSessionInfo(
                    base_url=settings_dep.ha_base_url.rstrip("/"),
                    mcp_url=settings_dep.ha_mcp_url,
                    access_token=access.access_token,
                    token_type="Bearer",
                    source=access.source,
                )
            else:
                logger.warning(
                    "HA base URL configured but OAuth not linked (device %s)",
                    device_id,
                )

        tools_mode = body.capabilities.tools_mode
        if tools_mode == "client_functions":
            tools = default_client_function_tools(include_ha=include_ha_tools)
        else:
            tools = default_client_function_tools(include_ha=False)

        effective_tools_mode = tools_mode
        rate = settings_dep.default_sample_rate
        return SessionStartResponse(
            session_id=new_session_id(),
            xai=XaiSessionInfo(
                ephemeral_token=minted["ephemeral_token"],
                expires_at=minted.get("expires_at"),
                realtime_url=realtime_url,
                model=model,
            ),
            ha=ha_info,
            session=SessionBootstrap(
                voice=settings_dep.default_voice,
                instructions=settings_dep.default_instructions,
                tools_mode=effective_tools_mode,
                audio=AudioBootstrap(
                    input_rate=rate,
                    output_rate=rate,
                    format="audio/pcm",
                    transport="binary",
                ),
                tools=tools,
            ),
        )

    return app
