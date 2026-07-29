"""Device registry: enrol several Voice PEs, revoke one without touching the rest.

Before this, devices existed only as a `DEVICE_TOKENS` env string. That works
for one device but has no revoke story — pulling a compromised device's access
means editing add-on configuration and restarting the control plane, which drops
every other device's sessions with it.

The registry is a small JSON file next to the HA token store, holding a SHA-256
of each device token (never the token itself), so a stolen `devices.json` does
not let the thief mint HA access tokens. Tokens are 32 bytes of `secrets`
output, so a plain hash is sufficient — there is no low-entropy space to grind.

Env-configured tokens keep working: `verify_device` checks the registry first,
then falls back to configuration. A revoked device is refused by both paths.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

# Matches config.MIN_DEVICE_TOKEN_CHARS; minted tokens are far longer.
TOKEN_BYTES = 32
MAX_DEVICES = 32
MAX_DEVICE_ID_CHARS = 64


class RegistryError(ValueError):
    pass


def _hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def valid_device_id(device_id: str) -> bool:
    if not device_id or len(device_id) > MAX_DEVICE_ID_CHARS:
        return False
    return all(c.isalnum() or c in "-_." for c in device_id)


@dataclass(frozen=True)
class DeviceRecord:
    device_id: str
    label: str
    created_at: int
    revoked: bool
    last_seen: int
    sessions: int
    source: str  # "registry" | "config"

    def to_public(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "label": self.label,
            "created_at": self.created_at,
            "revoked": self.revoked,
            "last_seen": self.last_seen,
            "sessions": self.sessions,
            "source": self.source,
        }


class DeviceRegistry:
    """File-backed registry. Small enough that read-modify-write is fine."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"devices": {}})

    # --- storage ---------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"devices": {}}
        if not isinstance(data, dict):
            return {"devices": {}}
        data.setdefault("devices", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # Token hashes plus the shape of the install. Same 0600 treatment as the
        # HA refresh token store (S-5).
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        os.chmod(self.path, 0o600)

    # --- enrolment -------------------------------------------------------

    def enroll(self, device_id: str, label: str = "") -> str:
        """Create (or re-key) a device. Returns the token — shown once, never stored."""
        if not valid_device_id(device_id):
            raise RegistryError(
                "device_id must be 1-64 characters of letters, digits, '-', '_' or '.'"
            )
        data = self._read()
        devices = data["devices"]
        if device_id not in devices and len(devices) >= MAX_DEVICES:
            raise RegistryError(f"device registry is full ({MAX_DEVICES} devices)")
        token = secrets.token_urlsafe(TOKEN_BYTES)
        existing = devices.get(device_id) or {}
        devices[device_id] = {
            "token_sha256": _hash_token(token),
            "label": label or existing.get("label") or device_id,
            "created_at": int(time.time()),
            "revoked": False,
            # A re-key is a fresh credential; keep the usage history.
            "last_seen": int(existing.get("last_seen") or 0),
            "sessions": int(existing.get("sessions") or 0),
        }
        self._write(data)
        return token

    def revoke(self, device_id: str) -> bool:
        data = self._read()
        entry = data["devices"].get(device_id)
        if entry is None:
            # Revoking a config-only device still has to stick, so record a
            # tombstone the auth path will see.
            data["devices"][device_id] = {
                "token_sha256": "",
                "label": device_id,
                "created_at": int(time.time()),
                "revoked": True,
                "last_seen": 0,
                "sessions": 0,
            }
            self._write(data)
            return True
        if entry.get("revoked"):
            return False
        entry["revoked"] = True
        entry["token_sha256"] = ""
        self._write(data)
        return True

    def delete(self, device_id: str) -> bool:
        data = self._read()
        if device_id not in data["devices"]:
            return False
        del data["devices"][device_id]
        self._write(data)
        return True

    # --- auth ------------------------------------------------------------

    def is_revoked(self, device_id: str) -> bool:
        entry = self._read()["devices"].get(device_id)
        return bool(entry and entry.get("revoked"))

    def verify(self, device_id: str, token: str) -> bool:
        """True only for a live registry credential. Revoked always False."""
        entry = self._read()["devices"].get(device_id)
        if not entry or entry.get("revoked"):
            return False
        expected = str(entry.get("token_sha256") or "")
        if not expected:
            return False
        return hmac.compare_digest(expected, _hash_token(token))

    def note_seen(self, device_id: str, *, session: bool = False) -> None:
        data = self._read()
        entry = data["devices"].get(device_id)
        if entry is None:
            # Config-token device: track usage without granting it credentials.
            entry = {
                "token_sha256": "",
                "label": device_id,
                "created_at": int(time.time()),
                "revoked": False,
                "last_seen": 0,
                "sessions": 0,
            }
            data["devices"][device_id] = entry
        entry["last_seen"] = int(time.time())
        if session:
            entry["sessions"] = int(entry.get("sessions") or 0) + 1
        self._write(data)

    # --- listing ---------------------------------------------------------

    def list_devices(self, config_device_ids: list[str] | None = None) -> list[DeviceRecord]:
        data = self._read()
        config_ids = set(config_device_ids or [])
        records: list[DeviceRecord] = []
        for device_id, entry in sorted(data["devices"].items()):
            has_credential = bool(entry.get("token_sha256"))
            source = "registry" if has_credential else (
                "config" if device_id in config_ids else "revoked"
            )
            records.append(
                DeviceRecord(
                    device_id=device_id,
                    label=str(entry.get("label") or device_id),
                    created_at=int(entry.get("created_at") or 0),
                    revoked=bool(entry.get("revoked")),
                    last_seen=int(entry.get("last_seen") or 0),
                    sessions=int(entry.get("sessions") or 0),
                    source=source,
                )
            )
        known = {r.device_id for r in records}
        for device_id in sorted(config_ids - known):
            records.append(
                DeviceRecord(
                    device_id=device_id,
                    label=device_id,
                    created_at=0,
                    revoked=False,
                    last_seen=0,
                    sessions=0,
                    source="config",
                )
            )
        return records
