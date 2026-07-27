from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_device_tokens(value: str | dict[str, str]) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if k and v}
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
    ha_refresh_token: str | None = Field(default=None, alias="HA_REFRESH_TOKEN")
    ha_long_lived_token: str | None = Field(default=None, alias="HA_LONG_LIVED_TOKEN")
    ha_oauth_client_id: str = Field(
        default="https://ha-realtime-voice.local",
        alias="HA_OAUTH_CLIENT_ID",
    )

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

    @field_validator("ha_base_url", mode="before")
    @classmethod
    def _empty_url_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @property
    def device_tokens(self) -> dict[str, str]:
        return _parse_device_tokens(self.device_tokens_raw)

    @property
    def ha_mcp_url(self) -> str | None:
        if not self.ha_base_url:
            return None
        return self.ha_base_url.rstrip("/") + "/api/mcp"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
