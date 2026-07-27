# HA Realtime Voice CP (add-on)

Thin **control plane** for [ha-realtime-voice](https://github.com/colinmcintosh/ha-realtime-voice):

- Mints short-lived **xAI realtime** client secrets
- Bootstraps PE sessions (model, voice, tools, instructions)
- Issues HA credentials to the device for **Mode C** tools on the LAN

**Does not proxy audio.** Voice PE talks to xAI directly after mint.

## Configuration

| Option | Purpose |
|--------|---------|
| **xai_api_key** | xAI API key (stays on HA host, never on PE) |
| **device_tokens** | Comma-separated `device_id:token` pairs; must match PE `secrets.yaml` |
| **ha_base_url** | HA Core URL as seen from the add-on network. Default `http://homeassistant:8123` works on HA OS |
| **ha_long_lived_token** | Dev/fallback HA token for Mode C tool auth |
| **ha_refresh_token** | Preferred once OAuth pairing UI exists |
| **default_model / voice / sample_rate** | Session defaults |
| **default_instructions** | Leave empty for built-in PE-safe prompt |

## PE secrets

```yaml
ha_rv_control_plane_url: http://homeassistant.local:8787
# or http://<ha-host-lan-ip>:8787
ha_rv_device_id: voice-pe-1
ha_rv_device_token: change-me-device-token
```

`device_tokens` on the add-on must include the same `voice-pe-1:…` pair.

## Health

- In HA: Open Web UI on the add-on (hits `/health`)
- From LAN: `curl -s http://<ha-host>:8787/health`

## Ports

Host TCP **8787** → add-on (published by default). Required so PE on Wi‑Fi can mint sessions.

## Data

`/data` holds `options.json` (Supervisor) and `ha_tokens.json` (OAuth store when used).
