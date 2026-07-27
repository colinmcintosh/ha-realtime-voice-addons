from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class XaiTokenError(RuntimeError):
    pass


async def mint_ephemeral_token(settings: Settings, client: httpx.AsyncClient) -> dict[str, Any]:
    """Mint a short-lived xAI realtime client secret.

    API: POST https://api.x.ai/v1/realtime/client_secrets
    """
    payload = {"expires_after": {"seconds": settings.xai_ephemeral_ttl_seconds}}
    try:
        response = await client.post(
            settings.xai_client_secrets_url,
            headers={
                "Authorization": f"Bearer {settings.xai_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise XaiTokenError(f"Failed to reach xAI client_secrets: {exc}") from exc

    if response.status_code >= 400:
        body = response.text[:500]
        raise XaiTokenError(f"xAI client_secrets HTTP {response.status_code}: {body}")

    data = response.json()
    token = data.get("value") or data.get("client_secret") or data.get("token")
    if not token:
        raise XaiTokenError(f"xAI client_secrets response missing token field: {data!r}")

    expires_at = data.get("expires_at")
    logger.info("Minted xAI ephemeral token (expires_at=%s)", expires_at)
    return {
        "ephemeral_token": token,
        "expires_at": expires_at,
        "raw": data,
    }
