from __future__ import annotations

from typing import Any


def end_conversation_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "end_conversation",
        "description": (
            "End the *current* voice conversation when the user is done "
            "(e.g. thanks, goodbye, stop, that's all). "
            "This closes only the active realtime session; the device stays "
            "ready for a later utterance. Do not use this for brief pauses."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short reason the session is ending.",
                }
            },
            "required": [],
        },
    }


def ha_search_entities_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "ha_search_entities",
        "description": (
            "REQUIRED first step when the user refers to a room, device, or light by name "
            "and you do not already know the exact entity_id from this conversation. "
            "Search Home Assistant entities by free-text query (room/name/domain keywords). "
            "Returns matching entity_id, friendly_name, domain, state, and area when known. "
            "Do NOT guess entity_ids like light.bedroom — search first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search text from the user request, e.g. 'bedroom lights', "
                        "'office lamp', 'thermostat', 'kitchen'."
                    ),
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Optional domain filter: light, switch, binary_sensor, sensor, "
                        "climate, cover, fan, media_player, scene, etc."
                    ),
                },
                "area": {
                    "type": "string",
                    "description": (
                        "Optional area/room name filter, e.g. 'Bedroom', 'Office'. "
                        "Uses Home Assistant areas when available."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 10, max 25).",
                },
            },
            "required": ["query"],
        },
    }


def ha_list_areas_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "ha_list_areas",
        "description": (
            "List Home Assistant areas/rooms. Use when you need valid room names "
            "before searching entities, or when the user asks what rooms exist."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }


def ha_call_service_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "ha_call_service",
        "description": (
            "Call a Home Assistant service to control devices "
            "(e.g. light.turn_on, switch.turn_off, scene.turn_on). "
            "entity_id must come from ha_search_entities (or an earlier tool result), "
            "never invent entity_ids."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Service domain, e.g. light, switch, scene, climate.",
                },
                "service": {
                    "type": "string",
                    "description": "Service name, e.g. turn_on, turn_off, toggle.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Exact entity_id from a prior search/state result.",
                },
                "data": {
                    "type": "object",
                    "description": "Additional service data (brightness, temperature, etc.).",
                    "additionalProperties": True,
                },
            },
            "required": ["domain", "service"],
        },
    }


def ha_get_state_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "ha_get_state",
        "description": (
            "Get the current state and attributes of a Home Assistant entity. "
            "entity_id must be exact and preferably obtained from ha_search_entities. "
            "If you are unsure of the id, search first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Exact entity id from search results, e.g. light.bedroom_main.",
                }
            },
            "required": ["entity_id"],
        },
    }


def device_timer_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "device_timer",
        "description": (
            "Set, cancel or check kitchen-style timers that run on the voice "
            "device itself. Use this for 'set a timer for 10 minutes', "
            "'cancel the pasta timer', 'how long is left'. "
            "These timers ring on the device and keep running even if Home "
            "Assistant is unavailable, so prefer them over Home Assistant "
            "timer entities for spoken timers. At most 4 at once."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "cancel", "query"],
                    "description": (
                        "start a new timer, cancel an existing one, or query "
                        "what is running. Defaults to query."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Short label the user used, e.g. 'pasta', 'laundry'. "
                        "For cancel, matches an existing timer by name; leave "
                        "empty to cancel a ringing timer, or the next one due."
                    ),
                },
                # Three units rather than one: models are unreliable at
                # arithmetic, and "an hour and a half" turning into 90 seconds
                # is a bad failure for a timer. They are summed.
                "seconds": {"type": "integer", "description": "Seconds part of the duration."},
                "minutes": {"type": "integer", "description": "Minutes part of the duration."},
                "hours": {"type": "integer", "description": "Hours part of the duration."},
            },
            "required": ["action"],
        },
    }


def default_client_function_tools(
    *, include_ha: bool, include_timers: bool = True
) -> list[dict[str, Any]]:
    tools = [end_conversation_tool()]
    if include_timers:
        # Not gated on Home Assistant: device timers work with no HA at all,
        # which is the point of running them on the edge.
        tools.append(device_timer_tool())
    if include_ha:
        tools.extend(
            [
                ha_search_entities_tool(),
                ha_list_areas_tool(),
                ha_get_state_tool(),
                ha_call_service_tool(),
            ]
        )
    return tools


def area_instructions(area: str) -> str:
    """Tell the model which room it is standing in (H7).

    A bare "turn on the lights" is ambiguous in any house with more than one
    room, and the model cannot resolve it from the request alone — it has no
    idea where the device is. With this it can search that area first and only
    ask when the answer is genuinely elsewhere.
    """
    if not area:
        return ""
    return (
        f" This device is located in the {area}. When the user does not name a "
        f"room, prefer entities in the {area} — pass area='{area}' to "
        "ha_search_entities first. If nothing matches there, widen the search "
        "and say which room you acted on."
    )
