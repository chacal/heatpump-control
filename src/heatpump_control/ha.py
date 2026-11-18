"""Home Assistant entities through MQTT discovery.

Discovery configs go retained to `homeassistant/<component>/heatpump_control/<object>/config`;
the state is one retained JSON message on `heatpump-control/state`, which each entity reads with
a value template; availability follows the client's last will on `heatpump-control/availability`.
"""

import json

PREFIX = "heatpump-control"
STATE = f"{PREFIX}/state"
SETTINGS = f"{PREFIX}/settings"
AVAILABILITY = f"{PREFIX}/availability"
SET = f"{PREFIX}/set"
DISCOVERY = "homeassistant"
NODE = "heatpump_control"

DEVICE = {
    "identifiers": [NODE],
    "name": "Heat pump control",
    "manufacturer": "chacal/heatpump-control",
    "model": "Spot-price controller",
}

# object id -> (component, extra config)
SENSORS = {
    "mode": ("sensor", {"name": "Mode", "icon": "mdi:state-machine"}),
    "bedrooms": ("sensor", {"name": "Bedroom median", "device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement", "suggested_display_precision": 2}),
    "slab": ("sensor", {"name": "Floor slab estimate", "device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement", "suggested_display_precision": 1}),
    "heat_now": ("binary_sensor", {"name": "Heating planned now", "device_class": "heat"}),
    "next_change": ("sensor", {"name": "Next plan change", "device_class": "timestamp"}),
    "plan_eur": ("sensor", {"name": "Plan cost (horizon)", "unit_of_measurement": "€", "suggested_display_precision": 2, "icon": "mdi:cash"}),
    "savings_eur": ("sensor", {"name": "Savings estimate (horizon)", "unit_of_measurement": "€", "suggested_display_precision": 2, "icon": "mdi:piggy-bank"}),
    "last_round": ("sensor", {"name": "Last round", "device_class": "timestamp", "entity_category": "diagnostic"}),
}
SWITCHES = {
    "enabled": {"name": "Control", "icon": "mdi:power"},
    "away": {"name": "Away", "icon": "mdi:home-export-outline"},
}
NUMBERS = {
    "comfort_min": {"name": "Comfort min", "min": 18.0, "max": 22.0},
    "comfort_max": {"name": "Comfort max", "min": 20.0, "max": 24.0},
}


def _base(obj: str) -> dict:
    return {
        "unique_id": f"{NODE}_{obj}",
        "device": DEVICE,
        "availability_topic": AVAILABILITY,
    }


def discovery() -> list[tuple[str, str]]:
    """(topic, payload) of every entity's retained discovery config."""
    out = []
    for obj, (component, extra) in SENSORS.items():
        cfg = _base(obj) | extra | {"state_topic": STATE}
        if component == "binary_sensor":
            cfg["value_template"] = f"{{{{ 'ON' if value_json.{obj} else 'OFF' }}}}"
        else:
            cfg["value_template"] = f"{{{{ value_json.{obj} }}}}"
        out.append((f"{DISCOVERY}/{component}/{NODE}/{obj}/config", json.dumps(cfg)))
    for obj, extra in SWITCHES.items():
        cfg = _base(obj) | extra | {
            "state_topic": SETTINGS,
            "value_template": f"{{{{ 'ON' if value_json.{obj} else 'OFF' }}}}",
            "command_topic": f"{SET}/{obj}",
        }
        out.append((f"{DISCOVERY}/switch/{NODE}/{obj}/config", json.dumps(cfg)))
    for obj, extra in NUMBERS.items():
        cfg = _base(obj) | extra | {
            "state_topic": SETTINGS,
            "value_template": f"{{{{ value_json.{obj} }}}}",
            "command_topic": f"{SET}/{obj}",
            "step": 0.5,
            "mode": "box",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
        }
        out.append((f"{DISCOVERY}/number/{NODE}/{obj}/config", json.dumps(cfg)))
    return out
