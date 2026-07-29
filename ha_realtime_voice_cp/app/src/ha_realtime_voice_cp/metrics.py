"""In-process metrics and a bounded tool-call audit log.

Two jobs:

1. **Metrics** (roadmap E5) — mint latency, session counts, auth failures. All
   counters live in memory and reset on restart; this is a home add-on, not a
   time-series database, and anything that needs history can scrape
   `/v1/metrics`.

2. **Audit** — the release gate asks for "an audit log of tool calls without
   storing audio". Tool calls run on the device, so the device posts a compact
   per-session summary at the end of each turn (`/v1/telemetry`). Only tool
   names and outcomes are recorded: no transcripts, no audio, no service data.

Both are bounded. The audit ring holds a fixed number of sessions in memory and
the on-disk log is rotated at a fixed size, so an add-on left running for a year
cannot fill `/data`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_AUDIT_SESSIONS = 200
MAX_TOOLS_PER_SESSION = 32
MAX_AUDIT_BYTES = 512 * 1024


@dataclass
class _Latency:
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    last_ms: float = 0.0

    def observe(self, ms: float) -> None:
        self.count += 1
        self.total_ms += ms
        self.last_ms = ms
        self.max_ms = max(self.max_ms, ms)

    def to_dict(self) -> dict[str, float | int]:
        avg = self.total_ms / self.count if self.count else 0.0
        return {
            "count": self.count,
            "avg_ms": round(avg, 1),
            "max_ms": round(self.max_ms, 1),
            "last_ms": round(self.last_ms, 1),
        }


@dataclass
class Metrics:
    """Thread-safe counters. FastAPI runs handlers on a threadpool for sync defs."""

    started_at: float = field(default_factory=time.time)
    sessions_started: int = 0
    sessions_failed: int = 0
    auth_failures: int = 0
    mint_failures: int = 0
    ha_token_failures: int = 0
    telemetry_reports: int = 0
    mint_latency: _Latency = field(default_factory=_Latency)
    # device_id -> epoch seconds of last successful session start.
    last_session_by_device: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def note_session_start(self, device_id: str) -> None:
        with self._lock:
            self.sessions_started += 1
            self.last_session_by_device[device_id] = time.time()

    def note_session_failed(self) -> None:
        with self._lock:
            self.sessions_failed += 1

    def note_auth_failure(self) -> None:
        with self._lock:
            self.auth_failures += 1

    def note_mint_failure(self) -> None:
        with self._lock:
            self.mint_failures += 1

    def note_ha_token_failure(self) -> None:
        with self._lock:
            self.ha_token_failures += 1

    def observe_mint_latency(self, ms: float) -> None:
        with self._lock:
            self.mint_latency.observe(ms)

    def note_telemetry(self) -> None:
        with self._lock:
            self.telemetry_reports += 1

    def active_sessions(self, window_s: float = 300.0) -> int:
        """Devices that started a session recently.

        The control plane never sees a session end — audio goes straight to
        xAI — so "active" can only mean "minted within the ephemeral token's
        useful life". Named honestly in the response.
        """
        now = time.time()
        with self._lock:
            return sum(1 for t in self.last_session_by_device.values() if now - t <= window_s)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "uptime_s": round(time.time() - self.started_at, 1),
                "sessions_started": self.sessions_started,
                "sessions_failed": self.sessions_failed,
                "auth_failures": self.auth_failures,
                "mint_failures": self.mint_failures,
                "ha_token_failures": self.ha_token_failures,
                "telemetry_reports": self.telemetry_reports,
                "mint_latency": self.mint_latency.to_dict(),
                "devices_seen": len(self.last_session_by_device),
            }


class AuditLog:
    """Bounded ring of per-session tool summaries, mirrored to disk."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._ring: deque[dict[str, Any]] = deque(maxlen=MAX_AUDIT_SESSIONS)
        self._lock = threading.Lock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: dict[str, Any]) -> dict[str, Any]:
        stored = dict(entry)
        stored["recorded_at"] = int(time.time())
        with self._lock:
            self._ring.append(stored)
            self._append_to_disk(stored)
        return stored

    def _append_to_disk(self, entry: dict[str, Any]) -> None:
        if self.path is None:
            return
        try:
            # Rotate before writing so the log can never exceed 2x the cap.
            if self.path.exists() and self.path.stat().st_size > MAX_AUDIT_BYTES:
                self.path.replace(self.path.with_suffix(".1"))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
            os.chmod(self.path, 0o600)
        except OSError:
            # Auditing must never take the control plane down.
            pass

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._ring)
        return items[-limit:][::-1]

    def tool_totals(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        with self._lock:
            items = list(self._ring)
        for entry in items:
            for call in entry.get("tools", []):
                name = str(call.get("name", "?"))
                totals[name] = totals.get(name, 0) + 1
        return dict(sorted(totals.items()))
