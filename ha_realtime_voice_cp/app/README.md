# Control plane

Mints xAI ephemeral tokens and bootstraps PE sessions. Does **not** proxy audio.

HA credentials come from **OAuth pairing** (Web UI), not pasted long-lived tokens.

```bash
cp .env.example .env
# set XAI_API_KEY, DEVICE_TOKENS, HA_BASE_URL, PUBLIC_BASE_URL
uv sync
uv run ha-realtime-voice-cp
```

Open `PUBLIC_BASE_URL` (default `http://127.0.0.1:8787/`) → **Link Home Assistant**.

## API

### `GET /`

OAuth pairing UI (status + link / unlink).

### `GET /health`

Liveness + config flags (`ha_oauth_linked`, no secrets).

### `GET /oauth/start` → HA authorize

### `GET /oauth/callback`

Stores refresh token under `DATA_DIR/ha_tokens.json`.

### `POST /oauth/unlink`

Clears stored refresh token.

### `POST /v1/session/start`

Headers:

- `X-Device-Id`
- `X-Device-Token`

Body (optional JSON):

```json
{
  "capabilities": {
    "audio": true,
    "tools_mode": "client_functions"
  }
}
```

Response includes short-lived `ha.access_token` when OAuth is linked. If not linked, `ha` is `null` and tools are limited to `end_conversation`.
