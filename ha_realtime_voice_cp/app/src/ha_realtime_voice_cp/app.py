from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from . import __version__
from .auth import new_session_id, verify_device
from .config import Settings, get_settings
from .ha_auth import HaAuthError, HaAuthService, TokenStore
from .metrics import AuditLog, Metrics
from .models import (
    AudioBootstrap,
    DeviceInfo,
    DeviceListResponse,
    DiagnosticsResponse,
    HaSessionInfo,
    HealthResponse,
    MetricsResponse,
    ServicePolicyBootstrap,
    SessionBootstrap,
    SessionStartRequest,
    SessionStartResponse,
    TelemetryAck,
    TelemetryReport,
    XaiSessionInfo,
)
from .pairing_ui import render_pairing_page
from .policy import policy_instructions
from .registry import DeviceRegistry, RegistryError
from .tools import area_instructions, default_client_function_tools
from .xai_tokens import XaiTokenError, mint_ephemeral_token

logger = logging.getLogger(__name__)

# The version to show a user. Supervisor bakes the add-on version from
# config.yaml into the image (see the add-on Dockerfile); the Python package
# version is a separate number and they drift. Outside the add-on there is no
# such env var, so the package version stands in.
DISPLAY_VERSION = os.environ.get("HA_RV_ADDON_VERSION") or __version__

# Supervisor sets this on every request it proxies through ingress. Its presence
# is what distinguishes "Home Assistant authenticated this user" from "some host
# on the LAN opened a socket".
INGRESS_PATH_HEADER = "X-Ingress-Path"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    token_store = TokenStore(settings.data_dir / "ha_tokens.json")
    ha_auth = HaAuthService(settings, token_store)
    registry = DeviceRegistry(settings.devices_path)
    metrics = Metrics()
    audit = AuditLog(settings.audit_path)
    # Compile the service policy at construction: a malformed allowlist should
    # stop the add-on at start, not surface as a 500 on the first voice command.
    service_policy = settings.service_policy

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.http = httpx.AsyncClient()
        app.state.token_store = token_store
        app.state.ha_auth = ha_auth
        app.state.registry = registry
        app.state.metrics = metrics
        app.state.audit = audit
        app.state.service_policy = service_policy
        logger.info(
            "control plane starting host=%s port=%s devices=%d oauth_client_id=%s "
            "ui_access=%s policy_allow=%d policy_confirm=%d",
            settings.listen_host,
            settings.listen_port,
            len(settings.device_tokens),
            settings.effective_oauth_client_id,
            settings.ui_access,
            len(service_policy.allow),
            len(service_policy.confirm),
        )
        yield
        await app.state.http.aclose()

    app = FastAPI(
        title="HA Realtime Voice Control Plane",
        version=DISPLAY_VERSION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.token_store = token_store
    app.state.ha_auth = ha_auth
    app.state.registry = registry
    app.state.metrics = metrics
    app.state.audit = audit
    app.state.service_policy = service_policy

    def get_app_settings(request: Request) -> Settings:
        return request.app.state.settings

    def require_device_dep(
        request: Request,
        x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
        x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    ) -> str:
        try:
            return verify_device(
                get_app_settings(request),
                x_device_id,
                x_device_token,
                request.app.state.registry,
            )
        except HTTPException:
            request.app.state.metrics.note_auth_failure()
            raise

    # --- ingress / UI reachability (S-3) ---------------------------------

    def from_trusted_ingress(request: Request) -> bool:
        """Did this request actually come from Supervisor?

        X-Ingress-Path is a plain header: any LAN host can set it. Trusting it
        on its own would mean a LAN host could present itself as ingress, pass
        the UI gate, and — because the OAuth base is derived from the request —
        have its own Host header spliced into a redirect_uri. So the peer
        address decides, and the header only supplies the path.
        """
        import ipaddress

        client = request.client
        if client is None or not client.host:
            return False
        try:
            peer = ipaddress.ip_address(client.host)
        except ValueError:
            return False
        settings_dep = get_app_settings(request)
        return any(peer in net for net in settings_dep.trusted_ingress_networks)

    def ingress_path(request: Request) -> str | None:
        raw = request.headers.get(INGRESS_PATH_HEADER)
        if not raw:
            return None
        if not from_trusted_ingress(request):
            logger.warning(
                "%s header from untrusted peer %s; ignoring",
                INGRESS_PATH_HEADER,
                request.client.host if request.client else "?",
            )
            return None
        # Supervisor sends an absolute path. Refuse anything else rather than
        # splice an attacker-chosen string into an OAuth redirect_uri.
        if not raw.startswith("/") or "://" in raw or "\\" in raw:
            logger.warning("ignoring malformed %s header", INGRESS_PATH_HEADER)
            return None
        return raw.rstrip("/")

    def require_ui_access(request: Request) -> None:
        """Gate the pairing UI on how it was reached.

        With `ui_access: ingress` the published port serves device-authenticated
        endpoints and `/health` only. Everything that can change state — OAuth
        link/unlink, device enrollment, revocation — is reachable exclusively
        through Supervisor ingress, which means Home Assistant has already
        authenticated the human on the other end. That is the remainder of S-3:
        CSRF tokens stopped a hostile page driving these endpoints, but nothing
        stopped a LAN host driving them directly.
        """
        settings_dep = get_app_settings(request)
        via_ingress = ingress_path(request) is not None
        if via_ingress:
            if settings_dep.ui_on_ingress:
                return
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="UI is not served over ingress (ui_access=lan)",
            )
        if settings_dep.ui_on_lan:
            return
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Pairing UI is only available through Home Assistant ingress. "
                "Open it from the add-on page in Home Assistant."
            ),
        )

    def public_base(request: Request) -> str | None:
        """Base URL the *browser* is using, for OAuth client_id and redirect.

        Behind ingress the base path is rewritten per session
        (`/api/hassio_ingress/<token>`), so a configured PUBLIC_BASE_URL is the
        wrong answer there — HA would redirect the browser somewhere it cannot
        reach. Derive it from the request instead.
        """
        path = ingress_path(request)
        if path is not None:
            host = request.headers.get("host")
            if host:
                scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
                return f"{scheme}://{host}{path}"
        return get_app_settings(request).effective_public_base_url

    def ha_browser_base(request: Request) -> str | None:
        """Origin to send the *browser* to for Home Assistant's authorize page.

        Behind ingress the browser is already on the Home Assistant origin, so
        reuse it and keep the whole OAuth flow on one host. `ha_base_url` is the
        add-on's server-to-server address and is often an internal name; sending
        a browser there is a cross-origin hop through whatever proxy fronts it,
        and a redirect that drops the query string surfaces as HA's frontend
        error "Invalid redirect URI".
        """
        path = ingress_path(request)
        if path is not None:
            host = request.headers.get("host")
            if host:
                scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
                return f"{scheme}://{host}"
        return get_app_settings(request).ha_base_url

    def _pairing_html(
        request: Request,
        *,
        message: str | None = None,
        error: str | None = None,
        secret_token: tuple[str, str] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        settings_dep = get_app_settings(request)
        ha_service: HaAuthService = request.app.state.ha_auth
        store: TokenStore = request.app.state.token_store
        device_registry: DeviceRegistry = request.app.state.registry
        html = render_pairing_page(
            version=DISPLAY_VERSION,
            csrf_token=store.issue_form_token(),
            ha_base_url=settings_dep.ha_base_url,
            public_base_url=public_base(request),
            linked=ha_service.credentials_configured(),
            xai_configured=bool(settings_dep.xai_api_key),
            devices_configured=len(settings_dep.device_tokens),
            devices=[
                d.to_public()
                for d in device_registry.list_devices(list(settings_dep.device_tokens))
            ],
            policy=request.app.state.service_policy,
            metrics=request.app.state.metrics.snapshot(),
            active_sessions=request.app.state.metrics.active_sessions(),
            via_ingress=ingress_path(request) is not None,
            ui_access=settings_dep.ui_access,
            secret_token=secret_token,
            message=message,
            error=error,
        )
        return HTMLResponse(html, status_code=status_code)

    @app.get("/", response_class=HTMLResponse)
    async def pairing_home(request: Request) -> HTMLResponse:
        require_ui_access(request)
        return _pairing_html(request)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        # Unauthenticated and reachable by any LAN host, so it says only that the
        # service is up. It used to report whether an xAI key was present, how
        # many devices were enrolled and whether HA was linked — a precise
        # target-selection oracle for a scanner. Diagnostics moved to
        # /v1/diagnostics behind device auth.
        return HealthResponse(ok=True, version=DISPLAY_VERSION)

    @app.get("/v1/diagnostics", response_model=DiagnosticsResponse)
    async def diagnostics(
        request: Request,
        device_id: str = Depends(require_device_dep),
    ) -> DiagnosticsResponse:
        settings_dep = get_app_settings(request)
        ha_service: HaAuthService = request.app.state.ha_auth
        return DiagnosticsResponse(
            ok=True,
            version=DISPLAY_VERSION,
            device_id=device_id,
            xai_configured=bool(settings_dep.xai_api_key),
            devices_configured=len(settings_dep.device_tokens),
            ha_base_url_configured=bool(settings_dep.ha_base_url),
            ha_credentials_configured=ha_service.credentials_configured(),
            public_base_url_configured=bool(settings_dep.effective_public_base_url),
            ha_oauth_linked=ha_service.credentials_configured(),
        )

    # --- metrics + audit (E5, H4) ----------------------------------------

    @app.get("/v1/metrics", response_model=MetricsResponse)
    async def metrics_endpoint(
        request: Request,
        device_id: str = Depends(require_device_dep),
    ) -> MetricsResponse:
        m: Metrics = request.app.state.metrics
        a: AuditLog = request.app.state.audit
        return MetricsResponse(
            ok=True,
            version=DISPLAY_VERSION,
            metrics=m.snapshot(),
            active_sessions=m.active_sessions(),
            tool_totals=a.tool_totals(),
            recent_sessions=a.recent(20),
        )

    @app.get("/v1/metrics/prometheus", response_class=PlainTextResponse)
    async def metrics_prometheus(
        request: Request,
        device_id: str = Depends(require_device_dep),
    ) -> PlainTextResponse:
        m: Metrics = request.app.state.metrics
        snap = m.snapshot()
        lines = [
            "# HELP ha_rv_sessions_started Sessions minted since start",
            "# TYPE ha_rv_sessions_started counter",
            f"ha_rv_sessions_started {snap['sessions_started']}",
            "# TYPE ha_rv_sessions_failed counter",
            f"ha_rv_sessions_failed {snap['sessions_failed']}",
            "# TYPE ha_rv_auth_failures counter",
            f"ha_rv_auth_failures {snap['auth_failures']}",
            "# TYPE ha_rv_mint_failures counter",
            f"ha_rv_mint_failures {snap['mint_failures']}",
            "# TYPE ha_rv_mint_latency_ms gauge",
            f"ha_rv_mint_latency_ms{{stat=\"avg\"}} {snap['mint_latency']['avg_ms']}",
            f"ha_rv_mint_latency_ms{{stat=\"max\"}} {snap['mint_latency']['max_ms']}",
            "# TYPE ha_rv_active_sessions gauge",
            f"ha_rv_active_sessions {m.active_sessions()}",
        ]
        return PlainTextResponse("\n".join(lines) + "\n")

    @app.post("/v1/telemetry", response_model=TelemetryAck)
    async def telemetry(
        request: Request,
        report: TelemetryReport,
        device_id: str = Depends(require_device_dep),
    ) -> TelemetryAck:
        """Per-session summary from a device. Numbers and tool names only.

        Pydantic bounds every field, so a device (or something with a device's
        token) cannot grow the audit ring with a megabyte of `reason`.
        """
        m: Metrics = request.app.state.metrics
        a: AuditLog = request.app.state.audit
        entry = report.model_dump()
        entry["device_id"] = device_id
        a.record(entry)
        m.note_telemetry()
        return TelemetryAck(ok=True, recorded=len(report.tools))

    # --- device registry (E4) --------------------------------------------

    @app.get("/v1/devices", response_model=DeviceListResponse)
    async def list_devices(
        request: Request,
        device_id: str = Depends(require_device_dep),
    ) -> DeviceListResponse:
        settings_dep = get_app_settings(request)
        device_registry: DeviceRegistry = request.app.state.registry
        return DeviceListResponse(
            ok=True,
            devices=[
                DeviceInfo(**d.to_public())
                for d in device_registry.list_devices(list(settings_dep.device_tokens))
            ],
        )

    @app.post("/devices/enroll", response_class=HTMLResponse)
    async def enroll_device(
        request: Request,
        csrf_token: str = Form(default=""),
        device_id: str = Form(default=""),
        label: str = Form(default=""),
    ) -> HTMLResponse:
        require_ui_access(request)
        store: TokenStore = request.app.state.token_store
        if not store.verify_form_token(csrf_token):
            return _pairing_html(
                request, error="Invalid or expired form token — reload and retry.", status_code=400
            )
        device_registry: DeviceRegistry = request.app.state.registry
        try:
            token = device_registry.enroll(device_id.strip(), label.strip()[:64])
        except RegistryError as exc:
            return _pairing_html(request, error=str(exc), status_code=400)
        logger.info("device enrolled: %s", device_id)
        # Shown exactly once: only its SHA-256 is stored.
        return _pairing_html(
            request,
            message=f"Device '{device_id}' enrolled.",
            secret_token=(device_id.strip(), token),
        )

    @app.post("/devices/revoke", response_class=HTMLResponse)
    async def revoke_device(
        request: Request,
        csrf_token: str = Form(default=""),
        device_id: str = Form(default=""),
    ) -> HTMLResponse:
        require_ui_access(request)
        store: TokenStore = request.app.state.token_store
        if not store.verify_form_token(csrf_token):
            return _pairing_html(
                request, error="Invalid or expired form token — reload and retry.", status_code=400
            )
        device_registry: DeviceRegistry = request.app.state.registry
        device_registry.revoke(device_id.strip())
        logger.info("device revoked: %s", device_id)
        return _pairing_html(
            request,
            message=(
                f"Device '{device_id}' revoked. It cannot start new sessions; any "
                "session already running continues until its ephemeral token expires."
            ),
        )

    # POST, not GET: this writes the stored OAuth state, so as a GET any page
    # could invalidate a pairing flow in progress with an <img> tag.
    @app.post("/oauth/start", response_model=None)
    async def oauth_start(
        request: Request,
        csrf_token: str = Form(default=""),
    ) -> RedirectResponse | HTMLResponse:
        require_ui_access(request)
        settings_dep = get_app_settings(request)
        ha_service: HaAuthService = request.app.state.ha_auth
        store: TokenStore = request.app.state.token_store

        if not store.verify_form_token(csrf_token):
            return _pairing_html(request, error="Invalid or expired form token — reload and retry.")

        base = public_base(request)
        if not settings_dep.ha_base_url:
            raise HTTPException(status_code=400, detail="HA_BASE_URL is not configured")
        if not base:
            raise HTTPException(
                status_code=400,
                detail="PUBLIC_BASE_URL is not configured (needed for OAuth redirect)",
            )

        redirect_uri = f"{base}/oauth/callback"
        # HA IndieAuth requires redirect_uri to sit under client_id's origin.
        # Behind ingress both are the Home Assistant host, which satisfies it
        # without any configured public URL at all.
        client_id = base if ingress_path(request) is not None else (
            settings_dep.effective_oauth_client_id
        )
        state = store.begin_oauth_state(redirect_uri=redirect_uri, client_id=client_id)
        try:
            url = ha_service.authorize_url(
                redirect_uri=redirect_uri,
                state=state,
                client_id=client_id,
                browser_base=ha_browser_base(request),
            )
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
        require_ui_access(request)
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
    async def oauth_unlink(
        request: Request,
        csrf_token: str = Form(default=""),
    ) -> HTMLResponse:
        require_ui_access(request)
        # Without this check, a plain cross-origin form is a no-preflight simple
        # request: any page a household member visits could silently unlink HA
        # and take down every voice device's tool access. The app has no session
        # cookie, so SameSite protects nothing here.
        store: TokenStore = request.app.state.token_store
        if not store.verify_form_token(csrf_token):
            return _pairing_html(request, error="Invalid or expired form token — reload and retry.")
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
        m: Metrics = request.app.state.metrics
        device_registry: DeviceRegistry = request.app.state.registry
        try:
            device_id = verify_device(
                settings_dep, x_device_id, x_device_token, device_registry
            )
        except HTTPException:
            m.note_auth_failure()
            raise
        http: httpx.AsyncClient = request.app.state.http
        ha_service: HaAuthService = request.app.state.ha_auth

        started = time.perf_counter()
        try:
            minted = await mint_ephemeral_token(settings_dep, http)
        except XaiTokenError as exc:
            m.note_mint_failure()
            m.note_session_failed()
            logger.exception("xAI mint failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        m.observe_mint_latency((time.perf_counter() - started) * 1000.0)

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
                m.note_ha_token_failure()
                logger.warning(
                    "HA base URL configured but OAuth not linked (device %s)",
                    device_id,
                )

        tools_mode = body.capabilities.tools_mode
        if tools_mode == "client_functions":
            tools = default_client_function_tools(include_ha=include_ha_tools)
        else:
            tools = default_client_function_tools(include_ha=False)

        policy = request.app.state.service_policy
        instructions = settings_dep.default_instructions
        # H7: room awareness is per device, so it belongs here rather than in
        # the shared default instructions.
        area = settings_dep.device_areas.get(device_id, "")
        if area:
            instructions = f"{instructions}{area_instructions(area)}"
        policy_bootstrap: ServicePolicyBootstrap | None = None
        if include_ha_tools:
            # Only meaningful when HA tools are on the table. Appending the
            # confirmation contract to the instructions is what stops the model
            # retrying a confirm-gated call in a loop instead of asking.
            policy_bootstrap = ServicePolicyBootstrap(**policy.to_wire())
            extra = policy_instructions(policy)
            if extra:
                instructions = f"{instructions} {extra}"

        effective_tools_mode = tools_mode
        rate = settings_dep.default_sample_rate
        m.note_session_start(device_id)
        device_registry.note_seen(device_id, session=True)
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
                instructions=instructions,
                tools_mode=effective_tools_mode,
                audio=AudioBootstrap(
                    input_rate=rate,
                    output_rate=rate,
                    format="audio/pcm",
                    transport="binary",
                ),
                tools=tools,
                policy=policy_bootstrap,
            ),
        )

    return app
