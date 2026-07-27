from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class HaAuthError(RuntimeError):
    pass


@dataclass
class HaAccessToken:
    access_token: str
    source: Literal["oauth_refresh", "long_lived"]
    expires_at: float | None = None  # epoch seconds; None = unknown/long


class TokenStore:
    """Filesystem-backed store for HA OAuth refresh tokens.

    Pairing UI will write here. Dev env HA_REFRESH_TOKEN also seeds runtime.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"devices": {}, "shared": {}})

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"devices": {}, "shared": {}}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def get_refresh_token(self, device_id: str) -> str | None:
        data = self._read()
        device = data.get("devices", {}).get(device_id) or {}
        if device.get("refresh_token"):
            return str(device["refresh_token"])
        shared = data.get("shared", {})
        if shared.get("refresh_token"):
            return str(shared["refresh_token"])
        return None

    def set_device_refresh_token(self, device_id: str, refresh_token: str) -> None:
        data = self._read()
        devices = data.setdefault("devices", {})
        devices[device_id] = {
            "refresh_token": refresh_token,
            "updated_at": int(time.time()),
        }
        self._write(data)

    def set_shared_refresh_token(self, refresh_token: str) -> None:
        data = self._read()
        data["shared"] = {
            "refresh_token": refresh_token,
            "updated_at": int(time.time()),
        }
        self._write(data)


class HaAuthService:
    def __init__(self, settings: Settings, store: TokenStore) -> None:
        self.settings = settings
        self.store = store
        # device_id -> cached access token
        self._cache: dict[str, HaAccessToken] = {}

    async def get_access_token(
        self,
        device_id: str,
        client: httpx.AsyncClient,
    ) -> HaAccessToken | None:
        cached = self._cache.get(device_id)
        if cached and (cached.expires_at is None or cached.expires_at - 60 > time.time()):
            return cached

        # Prefer OAuth refresh (store or env)
        refresh = self.store.get_refresh_token(device_id) or self.settings.ha_refresh_token
        if refresh and self.settings.ha_base_url:
            try:
                token = await self._refresh_access_token(refresh, client)
                self._cache[device_id] = token
                return token
            except HaAuthError as exc:
                logger.warning("HA OAuth refresh failed for %s: %s", device_id, exc)

        if self.settings.ha_long_lived_token:
            token = HaAccessToken(
                access_token=self.settings.ha_long_lived_token,
                source="long_lived",
                expires_at=None,
            )
            self._cache[device_id] = token
            return token

        return None

    async def _refresh_access_token(
        self,
        refresh_token: str,
        client: httpx.AsyncClient,
    ) -> HaAccessToken:
        assert self.settings.ha_base_url
        url = self.settings.ha_base_url.rstrip("/") + "/auth/token"
        try:
            response = await client.post(
                url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.settings.ha_oauth_client_id,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise HaAuthError(f"token endpoint unreachable: {exc}") from exc

        if response.status_code >= 400:
            raise HaAuthError(f"HTTP {response.status_code}: {response.text[:300]}")

        data = response.json()
        access = data.get("access_token")
        if not access:
            raise HaAuthError(f"missing access_token in response: {data!r}")
        expires_in = data.get("expires_in")
        expires_at = time.time() + float(expires_in) if expires_in is not None else None
        return HaAccessToken(
            access_token=str(access),
            source="oauth_refresh",
            expires_at=expires_at,
        )
