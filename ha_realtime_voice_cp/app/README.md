# Control plane

Mints xAI ephemeral tokens and bootstraps PE sessions. Does **not** proxy audio.

```bash
cp .env.example .env
uv sync
uv run ha-realtime-voice-cp
```

## API

### `GET /health`

Liveness + basic config presence (no secrets).

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

Response:

```json
{
  "session_id": "...",
  "xai": {
    "ephemeral_token": "...",
    "expires_at": 1234567890,
    "realtime_url": "wss://api.x.ai/v1/realtime?model=grok-voice-latest",
    "model": "grok-voice-latest"
  },
  "ha": {
    "base_url": "http://homeassistant.local:8123",
    "mcp_url": "http://homeassistant.local:8123/api/mcp",
    "access_token": "...",
    "token_type": "Bearer"
  },
  "session": {
    "voice": "eve",
    "instructions": "...",
    "tools_mode": "client_functions",
    "audio": { "input_rate": 16000, "output_rate": 16000, "transport": "binary" },
    "tools": [ ... function tool schemas ... ]
  }
}
```

If HA credentials are missing, `ha` is `null` and tools still include `end_conversation` only.
