from __future__ import annotations

from functools import cached_property, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from .policy import ServicePolicy


# A device token is exchanged for a live Home Assistant access token carrying the
# pairing user's full privileges, plus a live xAI ephemeral token billed to the
# operator. Anything shared, published or guessable is full home control for any
# host on the LAN, so refuse to start on one.
MIN_DEVICE_TOKEN_CHARS = 24
_WEAK_TOKEN_MARKERS = ("change-me", "changeme", "example", "placeholder", "secret", "password")


def _validate_device_token(device_id: str, token: str) -> None:
    lowered = token.lower()
    for marker in _WEAK_TOKEN_MARKERS:
        if marker in lowered:
            raise ValueError(
                f"DEVICE_TOKENS entry '{device_id}' still uses an example/placeholder token. "
                "Generate one with: "
                "python3 -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
    if len(token) < MIN_DEVICE_TOKEN_CHARS:
        raise ValueError(
            f"DEVICE_TOKENS entry '{device_id}' is only {len(token)} characters; "
            f"at least {MIN_DEVICE_TOKEN_CHARS} required. Generate one with: "
            "python3 -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )


def _parse_device_tokens(value: str | dict[str, str]) -> dict[str, str]:
    if isinstance(value, dict):
        parsed = {str(k): str(v) for k, v in value.items() if k and v}
        for device_id, token in parsed.items():
            _validate_device_token(device_id, token)
        return parsed
    tokens: dict[str, str] = {}
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(
                f"Invalid DEVICE_TOKENS entry '{part}'. Expected device_id:token pairs."
            )
        device_id, token = part.split(":", 1)
        device_id = device_id.strip()
        token = token.strip()
        if not device_id or not token:
            raise ValueError(f"Invalid DEVICE_TOKENS entry '{part}'.")
        _validate_device_token(device_id, token)
        tokens[device_id] = token
    return tokens


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    xai_api_key: str = Field(alias="XAI_API_KEY")
    device_tokens_raw: str = Field(default="", alias="DEVICE_TOKENS")

    ha_base_url: str | None = Field(default=None, alias="HA_BASE_URL")
    # Public URL of this control plane as browsers reach it (OAuth client_id + redirect).
    # Example: http://homeassistant.local:8787 or http://192.168.1.10:8787
    public_base_url: str | None = Field(default=None, alias="PUBLIC_BASE_URL")
    # Override OAuth client_id (defaults to public_base_url). HA IndieAuth expects a URL.
    ha_oauth_client_id: str | None = Field(default=None, alias="HA_OAUTH_CLIENT_ID")

    xai_ephemeral_ttl_seconds: int = Field(default=300, alias="XAI_EPHEMERAL_TTL_SECONDS")
    xai_client_secrets_url: str = Field(
        default="https://api.x.ai/v1/realtime/client_secrets",
        alias="XAI_CLIENT_SECRETS_URL",
    )
    xai_realtime_base: str = Field(
        default="wss://api.x.ai/v1/realtime",
        alias="XAI_REALTIME_BASE",
    )

    listen_host: str = Field(default="127.0.0.1", alias="LISTEN_HOST")
    listen_port: int = Field(default=8787, alias="LISTEN_PORT")
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    log_level: str = Field(default="info", alias="LOG_LEVEL")

    # --- S-3: where the pairing UI may be reached from ---------------------
    # "ingress" — only through Supervisor ingress, so Home Assistant is the
    #             authenticator and the published port carries device-auth
    #             endpoints only. This is the shipping default.
    # "lan"     — only on the published port (standalone Docker, no Supervisor).
    # "both"    — transitional; leaves the UI open to any LAN host.
    ui_access: str = Field(default="both", alias="UI_ACCESS")
    # Which peers may claim to be Supervisor ingress. The X-Ingress-Path header
    # is not authenticated, so on its own it is a header any LAN host can set —
    # and it feeds the OAuth redirect_uri. Only requests arriving from the
    # Supervisor's own network are trusted to carry it. 172.30.32.0/23 is the
    # documented hassio network; override only if yours differs.
    trusted_ingress_cidrs: str = Field(
        default="172.30.32.0/23,127.0.0.1/32,::1/128", alias="TRUSTED_INGRESS_CIDRS"
    )

    # --- S-6: destructive tool policy -------------------------------------
    # Comma-separated `domain.service` patterns. Empty allow = built-in default
    # allowlist (see policy.DEFAULT_ALLOW). A hard-deny list is always applied
    # on top and cannot be widened from configuration.
    service_allow: str = Field(default="", alias="SERVICE_ALLOW")
    service_deny: str = Field(default="", alias="SERVICE_DENY")
    service_confirm: str | None = Field(default=None, alias="SERVICE_CONFIRM")
    confirm_pin: str = Field(default="", alias="CONFIRM_PIN")

    # --- H7: which room each device is in --------------------------------
    # `device_id:Area` pairs, comma separated. Injected into that device's
    # instructions so "turn on the lights" means *this* room. Without it the
    # model has to ask, every time, in a house with more than one light.
    device_areas_raw: str = Field(default="", alias="DEVICE_AREAS")

    default_model: str = Field(default="grok-voice-latest", alias="DEFAULT_MODEL")
    default_voice: str = Field(default="eve", alias="DEFAULT_VOICE")
    default_instructions: str = Field(
        default=(
            "You are a concise home voice assistant for Home Assistant. "
            "Prefer short spoken answers. "
            "CRITICAL entity rules: Never invent or guess entity_ids "
            "(for example light.bedroom is usually wrong). "
            "When the user names a room or device, first call ha_search_entities "
            "with their words (and domain=light/switch/etc when obvious). "
            "Only then call ha_get_state or ha_call_service using an exact entity_id "
            "from the search matches. If search returns multiple lights in a room, "
            "summarize them or act on all relevant matches. "
            "If a tool returns entity_not_found, search again instead of guessing. "
            "If Home Assistant is unavailable, say so briefly and continue. "
            "Call end_conversation only when the user is clearly done "
            "(goodbye/thanks/that's all). That ends this conversation session "
            "only; the device remains available afterward."
        ),
        alias="DEFAULT_INSTRUCTIONS",
    )
    default_sample_rate: int = Field(default=16000, alias="DEFAULT_SAMPLE_RATE")

    @field_validator("ha_base_url", "public_base_url", "ha_oauth_client_id", mode="before")
    @classmethod
    def _empty_url_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("ui_access", mode="before")
    @classmethod
    def _check_ui_access(cls, value: object) -> object:
        if value in (None, ""):
            return "both"
        text = str(value).strip().lower()
        if text not in {"ingress", "lan", "both"}:
            raise ValueError("UI_ACCESS must be one of: ingress, lan, both")
        return text

    @cached_property
    def service_policy(self) -> ServicePolicy:
        # Compiled once. A bad allowlist should stop the first request loudly,
        # not be re-parsed (and re-raised) on every session start.
        from .policy import build_policy

        return build_policy(
            allow=self.service_allow,
            deny=self.service_deny,
            confirm=self.service_confirm,
            pin=self.confirm_pin,
        )

    @cached_property
    def trusted_ingress_networks(self) -> list[Any]:
        import ipaddress

        nets: list[Any] = []
        for part in self.trusted_ingress_cidrs.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError as exc:
                raise ValueError(f"TRUSTED_INGRESS_CIDRS entry {part!r}: {exc}") from exc
        return nets

    @cached_property
    def device_areas(self) -> dict[str, str]:
        areas: dict[str, str] = {}
        for part in self.device_areas_raw.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            device_id, area = part.split(":", 1)
            device_id = device_id.strip()
            area = area.strip()
            if device_id and area:
                areas[device_id] = area
        return areas

    @property
    def ui_on_ingress(self) -> bool:
        return self.ui_access in ("ingress", "both")

    @property
    def ui_on_lan(self) -> bool:
        return self.ui_access in ("lan", "both")

    @property
    def devices_path(self) -> Path:
        return self.data_dir / "devices.json"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @cached_property
    def device_tokens(self) -> dict[str, str]:
        # Cached: this was re-parsed on every authenticated request, so a
        # malformed or weak value surfaced as a 500 mid-request instead of a
        # refusal to start.
        return _parse_device_tokens(self.device_tokens_raw)

    @property
    def ha_mcp_url(self) -> str | None:
        if not self.ha_base_url:
            return None
        return self.ha_base_url.rstrip("/") + "/api/mcp"

    @property
    def effective_public_base_url(self) -> str | None:
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        return None

    @property
    def effective_oauth_client_id(self) -> str:
        if self.ha_oauth_client_id:
            return self.ha_oauth_client_id.rstrip("/")
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        # Last resort — HA requires a URL-shaped client_id.
        return f"http://127.0.0.1:{self.listen_port}"

    @property
    def oauth_redirect_uri(self) -> str | None:
        base = self.effective_public_base_url
        if not base:
            return None
        return f"{base}/oauth/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
