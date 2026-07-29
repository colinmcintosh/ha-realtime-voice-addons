"""Destructive-tool policy for Mode C service calls (security gate S-6).

The control plane is *not* in the tool path — the device calls Home Assistant
directly — so this module does not enforce anything by itself. It compiles the
operator's configuration into a compact policy that ships inside the session
bootstrap, and the device enforces it before a request is built.

Wire format is deliberately boring: comma-separated `domain.service` patterns in
a single string per bucket. The firmware holds them in fixed-width buffers and
matches by scanning, so no array parser and no allocation are needed on the
device for something that runs on every tool call.

Evaluation order on the device (mirrored by `evaluate` here, which the tests
use as the reference implementation):

    hard deny  ->  deny     (never overridable)
    deny       ->  deny
    confirm    ->  needs confirmation (and PIN, when configured)
    allow      ->  allow
    otherwise  ->  deny

Default-deny is the point. An allowlist that has to be widened by hand is the
only shape in which "the model can invoke any HA service" stops being true.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Services that are never reachable from a spoken sentence, regardless of what
# the operator puts in the allowlist. These either escape Home Assistant
# entirely (shell_command, python_script), take the instance down
# (homeassistant.stop, hassio.*), or destroy data (recorder.purge, backup.*).
# A wildcard like `homeassistant.*` in the allowlist must not re-open them.
HARD_DENY: tuple[str, ...] = (
    "shell_command.*",
    "python_script.*",
    "hassio.*",
    "supervisor.*",
    "homeassistant.stop",
    "homeassistant.restart",
    "homeassistant.check_config",
    "recorder.purge",
    "recorder.purge_entities",
    "backup.*",
    "cloud.*",
    "logger.*",
    "system_log.*",
    "update.install",
    "persistent_notification.*",
    "conversation.*",
    "assist_satellite.*",
)

# Everyday home control. Read-only tools (search/state/areas) are not services
# and are unaffected.
DEFAULT_ALLOW: tuple[str, ...] = (
    "light.turn_on",
    "light.turn_off",
    "light.toggle",
    "switch.turn_on",
    "switch.turn_off",
    "switch.toggle",
    "fan.turn_on",
    "fan.turn_off",
    "fan.toggle",
    "fan.set_percentage",
    "fan.oscillate",
    "cover.open_cover",
    "cover.close_cover",
    "cover.stop_cover",
    "cover.set_cover_position",
    "climate.set_temperature",
    "climate.set_hvac_mode",
    "climate.set_fan_mode",
    "climate.set_preset_mode",
    "climate.turn_on",
    "climate.turn_off",
    "media_player.turn_on",
    "media_player.turn_off",
    "media_player.media_play",
    "media_player.media_pause",
    "media_player.media_stop",
    "media_player.media_next_track",
    "media_player.media_previous_track",
    "media_player.volume_set",
    "media_player.volume_mute",
    "humidifier.turn_on",
    "humidifier.turn_off",
    "humidifier.set_humidity",
    "scene.turn_on",
    "input_boolean.turn_on",
    "input_boolean.turn_off",
    "input_boolean.toggle",
    "vacuum.start",
    "vacuum.pause",
    "vacuum.stop",
    "vacuum.return_to_base",
    "timer.start",
    "timer.cancel",
    "timer.pause",
    "select.select_option",
    "input_select.select_option",
    "input_number.set_value",
    "number.set_value",
    "todo.add_item",
)

# Reachable, but only after the user says yes out loud. `cover.open_cover` is
# here as well as in the allowlist below — confirm wins, because a cover is as
# likely to be a garage door as a curtain and the service layer cannot tell.
DEFAULT_CONFIRM: tuple[str, ...] = (
    "lock.unlock",
    "lock.open",
    "alarm_control_panel.alarm_disarm",
    "alarm_control_panel.alarm_arm_away",
    "alarm_control_panel.alarm_arm_home",
    "cover.open_cover",
)

# Buffer caps mirrored by ha_rv::budget on the device. Compiling a policy larger
# than the device can hold is a configuration error, not a silent truncation.
MAX_ALLOW_CHARS = 1024
MAX_DENY_CHARS = 512
MAX_CONFIRM_CHARS = 256
MAX_PIN_CHARS = 16

Decision = Literal["allow", "deny", "confirm"]


class PolicyError(ValueError):
    """Configuration that cannot be compiled into a device-shippable policy."""


def _norm(pattern: str) -> str:
    return pattern.strip().lower()


def parse_patterns(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Accept a comma-separated string or a list; return normalised patterns."""
    if raw is None:
        return []
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for item in items:
        pattern = _norm(str(item))
        if not pattern:
            continue
        if "," in pattern:
            raise PolicyError(f"service pattern may not contain a comma: {pattern!r}")
        if pattern.count(".") != 1:
            raise PolicyError(
                f"service pattern {pattern!r} must be exactly 'domain.service' or 'domain.*'"
            )
        domain, service = pattern.split(".", 1)
        if not domain or not service:
            raise PolicyError(f"service pattern {pattern!r} must be 'domain.service'")
        for part, label in ((domain, "domain"), (service, "service")):
            if part == "*":
                continue
            if not all(c.isalnum() or c == "_" for c in part):
                raise PolicyError(f"{label} {part!r} in {pattern!r} is not a slug")
        if domain == "*":
            raise PolicyError(
                f"{pattern!r} would match every domain; list domains explicitly"
            )
        if pattern not in out:
            out.append(pattern)
    return out


def matches(pattern: str, domain: str, service: str) -> bool:
    p_domain, p_service = pattern.split(".", 1)
    if p_domain != domain:
        return False
    return p_service in ("*", service)


def _matches_any(patterns: list[str], domain: str, service: str) -> bool:
    return any(matches(p, domain, service) for p in patterns)


@dataclass(frozen=True)
class ServicePolicy:
    """Compiled policy, ready to ship in a session bootstrap."""

    allow: list[str]
    deny: list[str]
    confirm: list[str]
    pin: str = ""

    def evaluate(self, domain: str, service: str) -> Decision:
        """Reference implementation of the device-side decision."""
        domain = _norm(domain)
        service = _norm(service)
        if _matches_any(list(HARD_DENY), domain, service):
            return "deny"
        if _matches_any(self.deny, domain, service):
            return "deny"
        if _matches_any(self.confirm, domain, service):
            return "confirm"
        if _matches_any(self.allow, domain, service):
            return "allow"
        return "deny"

    def to_wire(self) -> dict[str, str]:
        """Comma-joined form carried in the bootstrap.

        `deny` ships the hard-deny list too: the device applies one uniform rule
        set and never has to be kept in sync with a table compiled into the
        control plane.
        """
        deny = list(HARD_DENY) + [p for p in self.deny if p not in HARD_DENY]
        wire = {
            "allow": ",".join(self.allow),
            "deny": ",".join(deny),
            "confirm": ",".join(self.confirm),
        }
        if self.pin:
            wire["pin"] = self.pin
        _check_caps(wire)
        return wire


def _check_caps(wire: dict[str, str]) -> None:
    caps = {
        "allow": MAX_ALLOW_CHARS,
        "deny": MAX_DENY_CHARS,
        "confirm": MAX_CONFIRM_CHARS,
        "pin": MAX_PIN_CHARS,
    }
    for key, cap in caps.items():
        value = wire.get(key, "")
        if len(value) > cap:
            raise PolicyError(
                f"compiled service policy '{key}' is {len(value)} chars, over the "
                f"{cap}-char device buffer. Shorten the list or use domain wildcards."
            )


def build_policy(
    *,
    allow: str | list[str] | None = None,
    deny: str | list[str] | None = None,
    confirm: str | list[str] | None = None,
    pin: str = "",
) -> ServicePolicy:
    """Compile operator configuration; empty inputs fall back to the defaults."""
    allow_list = parse_patterns(allow) or list(DEFAULT_ALLOW)
    deny_list = parse_patterns(deny)
    confirm_list = parse_patterns(confirm) if confirm is not None else list(DEFAULT_CONFIRM)
    pin = (pin or "").strip()
    if pin and not pin.isdigit():
        raise PolicyError("confirm_pin must be digits only (it has to be speakable)")
    if len(pin) > MAX_PIN_CHARS:
        raise PolicyError(f"confirm_pin is longer than {MAX_PIN_CHARS} characters")
    policy = ServicePolicy(
        allow=allow_list, deny=deny_list, confirm=confirm_list, pin=pin
    )
    _check_caps(policy.to_wire())
    return policy


def policy_instructions(policy: ServicePolicy) -> str:
    """Extra model instructions describing the confirmation contract.

    The device rejects unconfirmed calls whatever the model believes, but the
    model needs to know *why* it was rejected or it will simply retry the same
    call and the user hears a loop instead of a question.
    """
    if not policy.confirm:
        return ""
    families = sorted({p.split(".", 1)[0] for p in policy.confirm})
    lines = [
        (
            " Some actions need spoken confirmation before they run "
            f"({', '.join(families)}). "
        ),
        (
            "If a tool returns confirmation_required, do NOT retry silently: "
            "ask the user out loud to confirm, and only when they clearly "
            "agree call the same service again with confirm set to true. "
        ),
    ]
    if policy.pin:
        lines.append(
            "If a tool returns pin_required, ask the user for their spoken PIN "
            "and pass it as the pin argument alongside confirm. "
        )
    lines.append(
        "If a tool returns service_not_allowed, tell the user that action is "
        "not permitted from voice and do not try a different service to work "
        "around it."
    )
    return "".join(lines)
