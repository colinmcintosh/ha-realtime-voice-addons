# HA Realtime Voice CP (add-on)

Thin **control plane** for ha-realtime-voice:

- Mints short-lived **xAI realtime** client secrets
- Bootstraps Voice PE sessions (model, voice, tools, instructions, service policy)
- Links Home Assistant via **OAuth** and issues short-lived HA access tokens to
  devices for Mode C tools

**Does not proxy audio.** Voice PE talks to xAI directly after the mint, and
talks to Home Assistant directly for tools. This add-on is only in the path at
the start of a session.

## Where the UI lives

Open this add-on **from the Home Assistant sidebar**. The pairing UI is served
over Supervisor ingress, so Home Assistant authenticates you before the request
arrives.

Port **8787** is still published, because a Voice PE cannot log in to Home
Assistant. With the default `ui_access: ingress` that port carries only
device-authenticated endpoints plus `/health` — opening `http://<ha-host>:8787/`
in a browser returns **404**, and that is correct.

## Configuration

| Option | Purpose |
|--------|---------|
| **xai_api_key** | xAI API key (stays on the HA host, never on the PE) |
| **device_tokens** | Optional. `device_id:token` pairs for devices configured by hand. Normally left **empty** — enroll devices from the UI instead |
| **ha_base_url** | HA Core as seen from the add-on. `http://homeassistant:8123` on HA OS |
| **ui_access** | `ingress` (recommended), `lan`, or `both`. See below |
| **public_base_url** | Only needed for `lan` / `both`. Ingress derives it from the request |
| **device_areas** | `device_id:Area` pairs, e.g. `voice-pe-1:Kitchen`. Lets "turn on the lights" mean *this* room |
| **service_allow / service_deny / service_confirm** | Which Home Assistant services voice may call. See below |
| **confirm_pin** | Digits the user must speak alongside a confirmation. Empty = off |
| **default_model / voice / sample_rate** | Session defaults |
| **default_instructions** | Base system prompt |

There are **no** long-lived or refresh token paste fields. Link HA from the UI.

### ui_access

| Value | Effect |
|---|---|
| `ingress` | UI only through Home Assistant. The published port serves the device API and `/health` only. **Recommended.** |
| `lan` | UI only on port 8787. For standalone Docker without Supervisor; needs `public_base_url` |
| `both` | Transitional. Any host on your network can open the pairing UI |

## Setting up

1. **Start the add-on**, then open it from the sidebar.
2. **Link Home Assistant** — log in and approve. A refresh token is stored at
   `/data/ha_tokens.json` with `0600` permissions. Devices receive short-lived
   access tokens at session start.
3. **Enroll your device** under *Devices*: enter an id such as `voice-pe-1` and
   press **Enroll device**. The token is shown **once** — only its SHA-256 is
   stored. Copy it into the PE's `secrets.yaml` now.

```yaml
ha_rv_control_plane_url: "http://<ha-host-ip>:8787"   # use the IP, not .local
ha_rv_device_id: "voice-pe-1"
ha_rv_device_token: "<the token shown once>"
```

Use the Home Assistant host's **IP address**. mDNS resolution from the ESP32 is
unreliable, and a failed lookup shows up as an orange "control plane
unreachable" ring, which sends you debugging the wrong thing.

To retire a device, press **Revoke**. It stops minting immediately, and
revocation overrides `device_tokens` — so a revoked device stays revoked even
if its old token is still in configuration.

## What voice is allowed to do

By default the model can perform ordinary home control — lights, switches,
fans, covers, climate, media, scenes, timers — and **nothing else**. Anything
not on the allowlist is refused on the device before a request is built.

Locks, alarm panels and `cover.open_cover` require spoken confirmation: the
model must ask out loud and hear you agree. Set `confirm_pin` and it must also
hear the PIN, which never appears in the model's instructions.

A built-in hard-deny list — `shell_command`, `python_script`, `hassio`,
`backup`, `recorder.purge`, `homeassistant.stop` and similar — always applies
and **cannot be widened** from configuration.

Widen or narrow with comma-separated `domain.service` patterns (`domain.*` is
allowed):

```
service_allow: light.*,switch.*,media_player.volume_set
service_confirm:            # empty string disables the confirmation gate
```

An allowlist too long for the device's fixed buffer is a **startup error**, not
a silent truncation.

## Endpoints

| Path | Auth | Purpose |
|---|---|---|
| `/` | ingress | Pairing UI, devices, policy, metrics |
| `/health` | none | Liveness only — deliberately says nothing else |
| `/v1/session/start` | device | Mint + bootstrap |
| `/v1/diagnostics` | device | Configuration state |
| `/v1/metrics` | device | Mint latency, session and auth counters, tool audit |
| `/v1/metrics/prometheus` | device | Same, as Prometheus text |
| `/v1/telemetry` | device | Per-session summary posted by the device |

`/health` used to report whether an xAI key was present, how many devices were
enrolled and whether HA was linked. On an unauthenticated endpoint reachable by
any host on the network, that is a target-selection oracle; it now returns only
`ok` and a version.

## Privacy

Devices post a per-session summary to `/v1/telemetry`: latency, dropped bytes,
free heap, and tool **names** with outcomes. No audio, no transcripts, no
service arguments. It is kept in a bounded ring in memory and a rotated log
under `/data`, and it never leaves your network.

## Troubleshooting

**The Web UI shows 404 on port 8787.** Working as intended with
`ui_access: ingress`. Open the add-on from the sidebar.

**Link Home Assistant fails.** Set `ui_access: lan` and `public_base_url` to
`http://<ha-host>:8787`, then link from that URL. Re-link after changing
`ui_access` — a refresh token is bound to the `client_id` it was granted to.

**A device gets a yellow ring.** The add-on answered and refused it: the token
is wrong or the device was revoked. Re-enroll it.

**A device gets an orange ring.** The add-on was never reached. Check that it
is running and that the PE's `ha_rv_control_plane_url` points at this host's IP.
