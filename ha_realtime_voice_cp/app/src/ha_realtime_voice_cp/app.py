from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status

from . import __version__
from .auth import new_session_id, verify_device
from .config import Settings, get_settings
from .ha_auth import HaAuthService, TokenStore
from .models import (
    AudioBootstrap,
    HaSessionInfo,
    HealthResponse,
    SessionBootstrap,
    SessionStartRequest,
    SessionStartResponse,
    XaiSessionInfo,
)
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
            "control plane starting host=%s port=%s devices=%d",
            settings.listen_host,
            settings.listen_port,
            len(settings.device_tokens),
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

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        settings_dep = get_app_settings(request)
        store: TokenStore = request.app.state.token_store
        data_has_tokens = bool(
            store.get_refresh_token("_probe_")
            or settings_dep.ha_refresh_token
            or settings_dep.ha_long_lived_token
        )
        return HealthResponse(
            ok=True,
            version=__version__,
            xai_configured=bool(settings_dep.xai_api_key),
            devices_configured=len(settings_dep.device_tokens),
            ha_base_url_configured=bool(settings_dep.ha_base_url),
            ha_credentials_configured=data_has_tokens,
        )

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
                    "HA base URL configured but no credentials available for device %s",
                    device_id,
                )

        tools_mode = body.capabilities.tools_mode
        if tools_mode == "client_functions":
            tools = default_client_function_tools(include_ha=include_ha_tools)
        else:
            tools = default_client_function_tools(include_ha=False)

        # Keep tools_mode honest in the bootstrap payload.
        effective_tools_mode = tools_mode
        if tools_mode == "client_functions" and not include_ha_tools:
            effective_tools_mode = "client_functions"  # still has end_conversation

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

    @app.post("/v1/admin/ha-refresh-token")
    async def set_shared_ha_refresh_token(
        request: Request,
        payload: dict,
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    ) -> dict[str, str]:
        """Dev helper to store a shared HA OAuth refresh token.

        Auth: any configured device token as X-Admin-Token for now.
        """
        settings_dep = get_app_settings(request)
        if not x_admin_token or x_admin_token not in settings_dep.device_tokens.values():
            raise HTTPException(status_code=401, detail="Invalid admin token")
        refresh = payload.get("refresh_token")
        if not refresh or not isinstance(refresh, str):
            raise HTTPException(status_code=400, detail="refresh_token required")
        store: TokenStore = request.app.state.token_store
        store.set_shared_refresh_token(refresh)
        return {"status": "ok"}

    return app
