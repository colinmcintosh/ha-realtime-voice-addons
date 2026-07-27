from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class HaAuthError(RuntimeError):
    pass


@dataclass
class HaAccessToken:
    access_token: str
    source: Literal["oauth_refresh"]
    expires_at: float | None = None  # epoch seconds; None = unknown


class TokenStore:
    """Filesystem-backed store for HA OAuth refresh tokens + CSRF state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"devices": {}, "shared": {}, "oauth": {}})

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"devices": {}, "shared": {}, "oauth": {}}
        data.setdefault("devices", {})
        data.setdefault("shared", {})
        data.setdefault("oauth", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def has_refresh_token(self) -> bool:
        data = self._read()
        if data.get("shared", {}).get("refresh_token"):
            return True
        for device in data.get("devices", {}).values():
            if isinstance(device, dict) and device.get("refresh_token"):
                return True
        return False

    def get_refresh_token(self, device_id: str) -> str | None:
        data = self._read()
        device = data.get("devices", {}).get(device_id) or {}
        if device.get("refresh_token"):
            return str(device["refresh_token"])
        shared = data.get("shared", {})
        if shared.get("refresh_token"):
            return str(shared["refresh_token"])
        return None

    def set_shared_refresh_token(self, refresh_token: str) -> None:
        data = self._read()
        data["shared"] = {
            "refresh_token": refresh_token,
            "updated_at": int(time.time()),
        }
        self._write(data)

    def clear_shared_refresh_token(self) -> None:
        data = self._read()
        data["shared"] = {}
        data["devices"] = {}
        self._write(data)

    def begin_oauth_state(self, *, redirect_uri: str, client_id: str) -> str:
        state = secrets.token_urlsafe(24)
        data = self._read()
        data["oauth"] = {
            "state": state,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "created_at": int(time.time()),
        }
        self._write(data)
        return state

    def consume_oauth_state(self, state: str) -> dict[str, str] | None:
        data = self._read()
        oauth = data.get("oauth") or {}
        expected = oauth.get("state")
        created = int(oauth.get("created_at") or 0)
        # 15 minute CSRF window
        if not expected or not secrets.compare_digest(str(expected), state):
            return None
        if created and time.time() - created > 900:
            data["oauth"] = {}
            self._write(data)
            return None
        result = {
            "redirect_uri": str(oauth.get("redirect_uri") or ""),
            "client_id": str(oauth.get("client_id") or ""),
        }
        data["oauth"] = {}
        self._write(data)
        if not result["redirect_uri"] or not result["client_id"]:
            return None
        return result


class HaAuthService:
    def __init__(self, settings: Settings, store: TokenStore) -> None:
        self.settings = settings
        self.store = store
        self._cache: dict[str, HaAccessToken] = {}

    def credentials_configured(self) -> bool:
        return self.store.has_refresh_token()

    def authorize_url(self, *, redirect_uri: str, state: str) -> str:
        if not self.settings.ha_base_url:
            raise HaAuthError("HA_BASE_URL is not configured")
        client_id = self.settings.effective_oauth_client_id
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{self.settings.ha_base_url.rstrip('/')}/auth/authorize?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client: httpx.AsyncClient,
    ) -> str:
        """Exchange authorization code for refresh_token. Returns refresh_token."""
        if not self.settings.ha_base_url:
            raise HaAuthError("HA_BASE_URL is not configured")
        url = self.settings.ha_base_url.rstrip("/") + "/auth/token"
        try:
            response = await client.post(
                url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise HaAuthError(f"token endpoint unreachable: {exc}") from exc

        if response.status_code >= 400:
            raise HaAuthError(f"HTTP {response.status_code}: {response.text[:400]}")

        data = response.json()
        refresh = data.get("refresh_token")
        if not refresh:
            raise HaAuthError(f"missing refresh_token in response: {list(data.keys())}")
        self.store.set_shared_refresh_token(str(refresh))
        self._cache.clear()
        # Cache access token from the same response when present.
        access = data.get("access_token")
        if access:
            expires_in = data.get("expires_in")
            expires_at = time.time() + float(expires_in) if expires_in is not None else None
            token = HaAccessToken(
                access_token=str(access),
                source="oauth_refresh",
                expires_at=expires_at,
            )
            self._cache["__shared__"] = token
        return str(refresh)

    def unlink(self) -> None:
        self.store.clear_shared_refresh_token()
        self._cache.clear()

    async def get_access_token(
        self,
        device_id: str,
        client: httpx.AsyncClient,
    ) -> HaAccessToken | None:
        cached = self._cache.get(device_id) or self._cache.get("__shared__")
        if cached and (cached.expires_at is None or cached.expires_at - 60 > time.time()):
            return cached

        refresh = self.store.get_refresh_token(device_id)
        if refresh and self.settings.ha_base_url:
            try:
                token = await self._refresh_access_token(refresh, client)
                self._cache[device_id] = token
                self._cache["__shared__"] = token
                return token
            except HaAuthError as exc:
                logger.warning("HA OAuth refresh failed for %s: %s", device_id, exc)

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
                    "client_id": self.settings.effective_oauth_client_id,
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
