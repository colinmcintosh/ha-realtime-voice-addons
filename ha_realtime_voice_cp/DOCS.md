# HA Realtime Voice CP (add-on)

Thin **control plane** for ha-realtime-voice:

- Mints short-lived **xAI realtime** client secrets
- Bootstraps PE sessions (model, voice, tools, instructions)
- Links Home Assistant via **OAuth** and issues short-lived HA access tokens to devices for Mode C tools

**Does not proxy audio.** Voice PE talks to xAI directly after mint.

## Configuration

| Option | Purpose |
|--------|---------|
| **xai_api_key** | xAI API key (stays on HA host, never on PE) |
| **device_tokens** | Comma-separated `device_id:token` pairs; must match PE `secrets.yaml` |
| **ha_base_url** | HA Core URL as seen from the add-on. Default `http://homeassistant:8123` on HA OS |
| **public_base_url** | URL **browsers** use to reach this add-on (OAuth `client_id` + redirect). Example: `http://homeassistant.local:8787` |
| **default_model / voice / sample_rate** | Session defaults |
| **default_instructions** | Prefilled PE-safe system prompt |

There are **no** long-lived / refresh token paste fields. Link HA from the Web UI.

## Link Home Assistant (OAuth)

1. Start the add-on
2. Open **Open Web UI** (or `http://<ha-host>:8787/`)
3. Click **Link Home Assistant**
4. Log in / approve in HA
5. You return to the CP UI showing **Linked**

Refresh token is stored under `/data/ha_tokens.json` on the add-on. PE receives short-lived access tokens at `POST /v1/session/start`.

## PE secrets

```yaml
ha_rv_control_plane_url: http://homeassistant.local:8787
ha_rv_device_id: voice-pe-1
ha_rv_device_token: change-me-device-token
```

`device_tokens` on the add-on must include the same `voice-pe-1:…` pair.

## Health

- Web UI: `/`
- JSON: `GET /health` → `ha_oauth_linked`, `ha_credentials_configured`

## Ports

Host TCP **8787** published by default (PE mint + OAuth callback).
