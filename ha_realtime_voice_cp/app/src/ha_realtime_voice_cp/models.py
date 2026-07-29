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


class ServicePolicyBootstrap(BaseModel):
    """Compiled S-6 service policy, enforced on the device.

    Comma-separated `domain.service` patterns rather than arrays: the firmware
    holds each bucket in one fixed-width buffer and matches by scanning, so the
    tool path needs no array parser and no allocation. See policy.py.
    """

    allow: str
    deny: str
    confirm: str
    # Spoken PIN for confirm-gated services. Never appears in `instructions` —
    # the model has to hear it from the user.
    pin: str = ""


class SessionBootstrap(BaseModel):
    voice: str
    instructions: str
    tools_mode: Literal["client_functions", "none"]
    audio: AudioBootstrap
    tools: list[dict[str, Any]]
    policy: ServicePolicyBootstrap | None = None


class SessionStartResponse(BaseModel):
    session_id: str
    xai: XaiSessionInfo
    ha: HaSessionInfo | None
    session: SessionBootstrap


class HealthResponse(BaseModel):
    """Unauthenticated liveness only — see DiagnosticsResponse for the rest.

    This used to carry the configuration fingerprint below, on an endpoint any
    LAN host could read.
    """

    ok: bool
    version: str


class DiagnosticsResponse(BaseModel):
    """Configuration state. Device-authenticated (`/v1/diagnostics`)."""

    ok: bool
    version: str
    device_id: str
    xai_configured: bool
    devices_configured: int
    ha_base_url_configured: bool
    ha_credentials_configured: bool
    public_base_url_configured: bool = False
    ha_oauth_linked: bool = False


class ToolCallReport(BaseModel):
    """One tool invocation as the device saw it. No arguments, no results.

    Deliberately narrow: an audit trail of *what was asked for* must not become
    a transcript of the house. `name` plus `ok` plus a short error code is
    enough to answer "did voice control try to unlock the door", which is the
    question the audit log exists for.
    """

    name: str = Field(max_length=64)
    ok: bool = True
    error: str = Field(default="", max_length=64)
    duration_ms: float = Field(default=0.0, ge=0.0, le=600_000.0)


class TelemetryReport(BaseModel):
    """Per-session summary posted by the device at session end (H4).

    Everything here is a number or an enum. `Field` bounds are the whole point:
    this endpoint is reachable by any authenticated device, so nothing it sends
    may grow a buffer on the control plane.
    """

    session_id: str = Field(default="", max_length=64)
    reason: str = Field(default="", max_length=32)
    mode: Literal["voice", "text", ""] = ""
    ttfa_ms: float = Field(default=-1.0, ge=-1.0, le=600_000.0)
    # H1 breakdown, so a slow turn can be attributed rather than guessed at.
    mint_ms: float = Field(default=-1.0, ge=-1.0, le=600_000.0)
    ws_connect_ms: float = Field(default=-1.0, ge=-1.0, le=600_000.0)
    session_ready_ms: float = Field(default=-1.0, ge=-1.0, le=600_000.0)
    duration_s: float = Field(default=0.0, ge=0.0, le=86_400.0)
    uplink_frames: int = Field(default=0, ge=0)
    uplink_overruns: int = Field(default=0, ge=0)
    downlink_bytes: int = Field(default=0, ge=0)
    downlink_dropped: int = Field(default=0, ge=0)
    heap_internal_free: int = Field(default=0, ge=0)
    heap_internal_low_water: int = Field(default=0, ge=0)
    heap_psram_free: int = Field(default=0, ge=0)
    tools: list[ToolCallReport] = Field(default_factory=list, max_length=32)


class TelemetryAck(BaseModel):
    ok: bool = True
    recorded: int = 0


class MetricsResponse(BaseModel):
    """Device-authenticated operational metrics (`/v1/metrics`)."""

    ok: bool
    version: str
    metrics: dict[str, Any]
    active_sessions: int
    tool_totals: dict[str, int]
    recent_sessions: list[dict[str, Any]]


class DeviceInfo(BaseModel):
    device_id: str
    label: str
    created_at: int
    revoked: bool
    last_seen: int
    sessions: int
    source: str


class DeviceListResponse(BaseModel):
    ok: bool
    devices: list[DeviceInfo]
