from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DeviceCapabilities(BaseModel):
    audio: bool = True
    tools_mode: Literal["client_functions", "none"] = "client_functions"


class SessionStartRequest(BaseModel):
    capabilities: DeviceCapabilities = Field(default_factory=DeviceCapabilities)


class XaiSessionInfo(BaseModel):
    ephemeral_token: str
    expires_at: int | None = None
    realtime_url: str
    model: str


class HaSessionInfo(BaseModel):
    base_url: str
    mcp_url: str
    access_token: str
    token_type: str = "Bearer"
    source: Literal["oauth_refresh", "none"] = "none"


class AudioBootstrap(BaseModel):
    input_rate: int = 16000
    output_rate: int = 16000
    format: str = "audio/pcm"
    transport: Literal["binary", "json"] = "binary"


class SessionBootstrap(BaseModel):
    voice: str
    instructions: str
    tools_mode: Literal["client_functions", "none"]
    audio: AudioBootstrap
    tools: list[dict[str, Any]]


class SessionStartResponse(BaseModel):
    session_id: str
    xai: XaiSessionInfo
    ha: HaSessionInfo | None
    session: SessionBootstrap


class HealthResponse(BaseModel):
    ok: bool
    version: str
    xai_configured: bool
    devices_configured: int
    ha_base_url_configured: bool
    ha_credentials_configured: bool
    public_base_url_configured: bool = False
    ha_oauth_linked: bool = False
