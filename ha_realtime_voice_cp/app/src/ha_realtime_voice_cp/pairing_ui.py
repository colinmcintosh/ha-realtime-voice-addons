"""Minimal HTML for the add-on Web UI: HA OAuth pairing, devices, metrics."""

from __future__ import annotations

import time
from html import escape
from typing import Any


def _ago(epoch: int) -> str:
    if not epoch:
        return "never"
    delta = int(time.time()) - epoch
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _device_rows(devices: list[dict[str, Any]], csrf_token: str) -> str:
    if not devices:
        return "<p class='hint'>No devices yet. Enrol one below.</p>"
    rows = []
    for device in devices:
        device_id = escape(str(device["device_id"]))
        revoked = bool(device["revoked"])
        source = escape(str(device["source"]))
        state = (
            "<span class='err'>revoked</span>"
            if revoked
            else f"<span class='ok'>{source}</span>"
        )
        action = ""
        if not revoked:
            action = f"""
            <form method="post" action="devices/revoke" class="inline">
              <input type="hidden" name="csrf_token" value="{escape(csrf_token)}"/>
              <input type="hidden" name="device_id" value="{device_id}"/>
              <button type="submit" class="danger small">Revoke</button>
            </form>"""
        rows.append(
            f"""
        <tr>
          <td><code>{device_id}</code></td>
          <td>{state}</td>
          <td class="num">{int(device["sessions"])}</td>
          <td class="muted">{escape(_ago(int(device["last_seen"])))}</td>
          <td>{action}</td>
        </tr>"""
        )
    return f"""
    <table class="devices">
      <thead><tr><th>Device</th><th>Status</th><th>Sessions</th><th>Last seen</th><th></th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def _policy_block(policy: Any) -> str:
    if policy is None:
        return ""
    allow = ", ".join(policy.allow[:12]) + (" …" if len(policy.allow) > 12 else "")
    confirm = ", ".join(policy.confirm) or "none"
    pin = "required" if policy.pin else "not set"
    return f"""
      <div class="row"><span class="k">Services allowed</span>
        <span class="num">{len(policy.allow)}</span></div>
      <div class="row"><span class="k">Needs spoken confirmation</span>
        <span>{escape(confirm)}</span></div>
      <div class="row"><span class="k">Confirmation PIN</span>
        <span>{escape(pin)}</span></div>
      <p class="hint">Allowed: <code>{escape(allow)}</code><br/>
      Everything not listed is refused on the device before a request is built.
      A built-in hard-deny list (shell_command, python_script, hassio, backup, …)
      is always applied and cannot be widened from configuration.</p>"""


def _metrics_block(metrics: dict[str, Any], active_sessions: int) -> str:
    mint = metrics.get("mint_latency", {})
    return f"""
      <div class="row"><span class="k">Sessions minted</span>
        <span class="num">{metrics.get("sessions_started", 0)}</span></div>
      <div class="row"><span class="k">Recently active</span>
        <span class="num">{active_sessions}</span></div>
      <div class="row"><span class="k">Mint latency (avg / max)</span>
        <span class="num">{mint.get("avg_ms", 0)} / {mint.get("max_ms", 0)} ms</span></div>
      <div class="row"><span class="k">Auth failures</span>
        <span class="num">{metrics.get("auth_failures", 0)}</span></div>
      <div class="row"><span class="k">Mint failures</span>
        <span class="num">{metrics.get("mint_failures", 0)}</span></div>
      <div class="row"><span class="k">Telemetry reports</span>
        <span class="num">{metrics.get("telemetry_reports", 0)}</span></div>"""


def render_pairing_page(
    *,
    version: str,
    csrf_token: str,
    ha_base_url: str | None,
    public_base_url: str | None,
    linked: bool,
    xai_configured: bool,
    devices_configured: int,
    devices: list[dict[str, Any]] | None = None,
    policy: Any = None,
    metrics: dict[str, Any] | None = None,
    active_sessions: int = 0,
    via_ingress: bool = False,
    ui_access: str = "both",
    secret_token: tuple[str, str] | None = None,
    message: str | None = None,
    error: str | None = None,
) -> str:
    devices = devices or []
    metrics = metrics or {}
    status = "Linked" if linked else "Not linked"
    status_class = "ok" if linked else "warn"
    msg_html = f'<p class="msg">{escape(message)}</p>' if message else ""
    err_html = f'<p class="err">{escape(error)}</p>' if error else ""

    token_html = ""
    if secret_token is not None:
        device_id, token = secret_token
        token_html = f"""
      <div class="secret">
        <strong>Device token for <code>{escape(device_id)}</code></strong>
        <pre>{escape(token)}</pre>
        <p class="hint">Copy this into the device's <code>secrets.yaml</code> as
        <code>ha_rv_device_token</code> now. Only its SHA-256 is stored here, so
        this is the only time it is shown. Reloading this page will not bring it back.</p>
      </div>"""

    reach_html = ""
    if not via_ingress and ui_access == "both":
        reach_html = (
            "<p class='warn-box'>This page is being served on the LAN port. Any host on "
            "your network can reach it. Set <code>ui_access: ingress</code> in the add-on "
            "configuration to serve it only through Home Assistant, which authenticates "
            "the user first.</p>"
        )

    link_block = ""
    if not ha_base_url:
        link_block = (
            "<p class='err'>Set <code>ha_base_url</code> in the add-on configuration "
            "(e.g. <code>http://homeassistant:8123</code>).</p>"
        )
    elif not public_base_url:
        link_block = (
            "<p class='err'>Set <code>public_base_url</code> to the URL browsers use to "
            "reach this add-on (e.g. <code>http://homeassistant.local:8787</code>). "
            "Home Assistant OAuth needs this as <code>client_id</code> and redirect base. "
            "Opening this page through Home Assistant ingress supplies it automatically.</p>"
        )
    elif linked:
        link_block = f"""
        <form method="post" action="oauth/unlink">
          <input type="hidden" name="csrf_token" value="{escape(csrf_token)}"/>
          <button type="submit" class="secondary">Unlink Home Assistant</button>
        </form>
        <p class="hint">Unlinking deletes the stored OAuth refresh token. PE tool turns
        will stop until you link again.</p>
        """
    else:
        link_block = f"""
        <form method="post" action="oauth/start">
          <input type="hidden" name="csrf_token" value="{escape(csrf_token)}"/>
          <button type="submit" class="btn">Link Home Assistant</button>
        </form>
        <p class="hint">Opens Home Assistant login. After you approve, a refresh token is
        stored on this add-on. Devices receive short-lived access tokens at session start —
        no long-lived token is pasted into configuration.</p>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>HA Realtime Voice CP</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0f1419;
      --card: #1a2332;
      --text: #e7ecf3;
      --muted: #9aa8b8;
      --ok: #3dd68c;
      --warn: #f5a524;
      --err: #f31260;
      --accent: #006fee;
    }}
    body {{
      margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.45;
    }}
    main {{ max-width: 44rem; margin: 2rem auto; padding: 0 1rem; }}
    .card {{
      background: var(--card); border-radius: 12px; padding: 1.25rem 1.4rem;
      border: 1px solid #2a3648; margin-bottom: 1rem;
    }}
    h1 {{ font-size: 1.25rem; margin: 0 0 .35rem; }}
    h2 {{ font-size: 1rem; margin: 0 0 .75rem; }}
    .sub {{ color: var(--muted); margin: 0 0 1rem; font-size: .92rem; }}
    .row {{ display: flex; justify-content: space-between; gap: 1rem; padding: .35rem 0;
           border-top: 1px solid #2a3648; font-size: .95rem; }}
    .row:first-of-type {{ border-top: 0; }}
    .k {{ color: var(--muted); }}
    .num {{ font-variant-numeric: tabular-nums; }}
    .muted {{ color: var(--muted); }}
    .ok {{ color: var(--ok); font-weight: 600; }}
    .warn {{ color: var(--warn); font-weight: 600; }}
    .err {{ color: var(--err); }}
    .msg {{ color: var(--ok); }}
    .hint {{ color: var(--muted); font-size: .88rem; margin-top: .85rem; }}
    .btn, button {{
      display: inline-block; background: var(--accent); color: white; border: 0;
      border-radius: 8px; padding: .65rem 1rem; font-weight: 600; text-decoration: none;
      cursor: pointer; font-size: .95rem;
    }}
    button.secondary {{ background: #3a4658; }}
    button.danger {{ background: #7a1330; }}
    button.small {{ padding: .3rem .6rem; font-size: .8rem; }}
    code {{ font-size: .85em; }}
    .actions {{ margin-top: 1.1rem; }}
    table.devices {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
    table.devices th {{ text-align: left; color: var(--muted); font-weight: 500;
                        border-bottom: 1px solid #2a3648; padding: .3rem 0; }}
    table.devices td {{ padding: .4rem .5rem .4rem 0; border-bottom: 1px solid #202b3a; }}
    form.inline {{ display: inline; }}
    .enroll {{ display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .9rem; }}
    .enroll input {{ background: #101823; border: 1px solid #2a3648; color: var(--text);
                     border-radius: 8px; padding: .55rem .7rem; font-size: .9rem; }}
    .secret {{ background: #10261c; border: 1px solid var(--ok); border-radius: 10px;
               padding: .8rem 1rem; margin: .8rem 0; }}
    .secret pre {{ background: #0b1116; padding: .6rem; border-radius: 6px;
                   overflow-x: auto; font-size: .85rem; }}
    .warn-box {{ background: #2a2113; border: 1px solid var(--warn); border-radius: 10px;
                 padding: .7rem .9rem; font-size: .88rem; }}
  </style>
</head>
<body>
  <main>
    <div class="card">
      <h1>HA Realtime Voice — Control Plane</h1>
      <p class="sub">v{escape(version)} · mint xAI sessions · Mode C HA tools via OAuth</p>
      {msg_html}{err_html}{token_html}{reach_html}
      <div class="row"><span class="k">Home Assistant</span>
        <span class="{status_class}">{status}</span></div>
      <div class="row"><span class="k">HA base URL</span>
        <span>{escape(ha_base_url or "not set")}</span></div>
      <div class="row"><span class="k">Public CP URL</span>
        <span>{escape(public_base_url or "not set")}</span></div>
      <div class="row"><span class="k">Reached via</span>
        <span>{"Home Assistant ingress" if via_ingress else "LAN port"}</span></div>
      <div class="row"><span class="k">xAI API key</span>
        <span class="{"ok" if xai_configured else "warn"}">
          {"configured" if xai_configured else "missing"}</span></div>
      <div class="actions">{link_block}</div>
    </div>

    <div class="card">
      <h2>Devices</h2>
      {_device_rows(devices, csrf_token)}
      <form method="post" action="devices/enroll" class="enroll">
        <input type="hidden" name="csrf_token" value="{escape(csrf_token)}"/>
        <input type="text" name="device_id" placeholder="voice-pe-1" required
               pattern="[A-Za-z0-9._-]{{1,64}}"/>
        <input type="text" name="label" placeholder="Kitchen (optional)"/>
        <button type="submit" class="btn">Enrol device</button>
      </form>
      <p class="hint">Enrolling mints a token and shows it once. Re-enrolling an existing
      device_id rotates its token; the old one stops working immediately.
      {devices_configured} device(s) also come from <code>device_tokens</code> configuration.</p>
    </div>

    <div class="card">
      <h2>Voice control policy</h2>
      {_policy_block(policy)}
    </div>

    <div class="card">
      <h2>Metrics</h2>
      {_metrics_block(metrics, active_sessions)}
      <p class="hint">Devices post a per-session summary (latency, drops, heap, tool names)
      to <code>/v1/telemetry</code>. No audio and no transcripts are stored.</p>
    </div>
  </main>
</body>
</html>
"""
