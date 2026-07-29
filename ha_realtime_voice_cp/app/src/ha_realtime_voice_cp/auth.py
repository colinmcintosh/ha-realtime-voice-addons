from __future__ import annotations

import hmac
import secrets

from fastapi import Header, HTTPException, status

from .config import Settings
from .registry import DeviceRegistry


def verify_device(
    settings: Settings,
    device_id: str | None,
    device_token: str | None,
    registry: DeviceRegistry | None = None,
) -> str:
    """Authenticate a device against the registry, then configuration.

    Order matters. A revoked device must stay revoked even if its old token is
    still sitting in `DEVICE_TOKENS`, so the revocation check runs before the
    configuration fallback rather than after it.
    """
    if not device_id or not device_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Device-Id or X-Device-Token",
        )

    if registry is not None:
        if registry.is_revoked(device_id):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Device credentials revoked",
            )
        if registry.verify(device_id, device_token):
            return device_id

    expected = settings.device_tokens.get(device_id)
    if not expected or not hmac.compare_digest(expected, device_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid device credentials",
        )
    return device_id


async def require_device(
    settings: Settings,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
) -> str:
    return verify_device(settings, x_device_id, x_device_token)


def new_session_id() -> str:
    return secrets.token_urlsafe(16)
