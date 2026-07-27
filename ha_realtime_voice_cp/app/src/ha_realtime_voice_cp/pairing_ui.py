"""Minimal HTML for HA OAuth pairing (add-on Web UI)."""

from __future__ import annotations

from html import escape


def render_pairing_page(
    *,
    version: str,
    ha_base_url: str | None,
    public_base_url: str | None,
    linked: bool,
    xai_configured: bool,
    devices_configured: int,
    message: str | None = None,
    error: str | None = None,
) -> str:
    status = "Linked" if linked else "Not linked"
    status_class = "ok" if linked else "warn"
    msg_html = f'<p class="msg">{escape(message)}</p>' if message else ""
    err_html = f'<p class="err">{escape(error)}</p>' if error else ""

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
            "Home Assistant OAuth needs this as <code>client_id</code> and redirect base.</p>"
        )
    elif linked:
        link_block = """
        <form method="post" action="/oauth/unlink">
          <button type="submit" class="secondary">Unlink Home Assistant</button>
        </form>
        <p class="hint">Unlinking deletes the stored OAuth refresh token. PE tool turns
        will stop until you link again.</p>
        """
    else:
        link_block = """
        <a class="btn" href="/oauth/start">Link Home Assistant</a>
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
    main {{ max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }}
    .card {{
      background: var(--card); border-radius: 12px; padding: 1.25rem 1.4rem;
      border: 1px solid #2a3648;
    }}
    h1 {{ font-size: 1.25rem; margin: 0 0 .35rem; }}
    .sub {{ color: var(--muted); margin: 0 0 1rem; font-size: .92rem; }}
    .row {{ display: flex; justify-content: space-between; gap: 1rem; padding: .35rem 0;
           border-top: 1px solid #2a3648; font-size: .95rem; }}
    .row:first-of-type {{ border-top: 0; }}
    .k {{ color: var(--muted); }}
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
    code {{ font-size: .85em; }}
    .actions {{ margin-top: 1.1rem; }}
  </style>
</head>
<body>
  <main>
    <div class="card">
      <h1>HA Realtime Voice — Control Plane</h1>
      <p class="sub">v{escape(version)} · mint xAI sessions · Mode C HA tools via OAuth</p>
      {msg_html}{err_html}
      <div class="row"><span class="k">Home Assistant</span>
        <span class="{status_class}">{status}</span></div>
      <div class="row"><span class="k">HA base URL</span>
        <span>{escape(ha_base_url or "not set")}</span></div>
      <div class="row"><span class="k">Public CP URL</span>
        <span>{escape(public_base_url or "not set")}</span></div>
      <div class="row"><span class="k">xAI API key</span>
        <span class="{"ok" if xai_configured else "warn"}">
          {"configured" if xai_configured else "missing"}</span></div>
      <div class="row"><span class="k">Devices</span>
        <span>{devices_configured}</span></div>
      <div class="actions">{link_block}</div>
    </div>
  </main>
</body>
</html>
"""
