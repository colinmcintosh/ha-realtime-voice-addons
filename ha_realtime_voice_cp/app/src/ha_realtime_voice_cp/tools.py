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


def default_client_function_tools(*, include_ha: bool) -> list[dict[str, Any]]:
    tools = [end_conversation_tool()]
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
