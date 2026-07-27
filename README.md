# HA Realtime Voice — Home Assistant add-ons

Public Supervisor add-on repository for the [ha-realtime-voice](https://github.com/colinmcintosh/ha-realtime-voice) project.

The main product repo (firmware, clients, control-plane source) stays private.
This repo only carries the built add-on(s) so Supervisor can clone something small.

## Install

**Home Assistant → Settings → Add-ons → Add-on store → ⋮ → Repositories** and add:

```text
https://github.com/colinmcintosh/ha-realtime-voice-addons
```

Then install **HA Realtime Voice CP** from the store.

Minimum config after install:

- `xai_api_key`
- `device_tokens` (e.g. `voice-pe-1:your-token`, must match PE)
- `ha_long_lived_token` (until OAuth pairing is done)

The default `ha_base_url` `http://homeassistant:8123` works on HA OS.

## Add-ons

| Slug | Description |
|------|-------------|
| [`ha_realtime_voice_cp`](ha_realtime_voice_cp/) | Control plane: xAI ephemeral mint + session bootstrap. Not an audio proxy. |

## Publishing

This repo is generated from `ha-realtime-voice` via `scripts/publish-addon-repo.sh`.
Do not edit `ha_realtime_voice_cp/` here by hand.
