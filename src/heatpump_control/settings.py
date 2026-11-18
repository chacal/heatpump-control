"""The owner's controls, set from Home Assistant over MQTT.

Commands arrive on `heatpump-control/set/<key>` (HA's switch and number entities). The values
are untrusted input: unknown keys and malformed values are ignored, numbers are clamped to fixed
bounds, and the band keeps at least 1 K between its ends. The whole settings object is published
retained on `heatpump-control/settings`, and read back at start, so a restart keeps them.
"""

import json
from dataclasses import asdict, dataclass, replace

COMFORT_MIN_C = (18.0, 22.0)
COMFORT_MAX_C = (20.0, 24.0)
MIN_BAND_K = 1.0
AWAY_MIN_C = 18.0  # the band's floor while nobody is home


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    comfort_min: float = 20.0
    comfort_max: float = 22.0
    away: bool = False

    def band(self) -> tuple[float, float]:
        return (AWAY_MIN_C if self.away else self.comfort_min), self.comfort_max

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, body: bytes | str) -> "Settings":
        s = cls()
        try:
            d = json.loads(body)
        except ValueError:
            return s
        if not isinstance(d, dict):
            return s
        # The band's ends one at a time could clamp against a default end, so widen first.
        s = replace(s, comfort_min=COMFORT_MIN_C[0], comfort_max=COMFORT_MAX_C[1])
        for key in ("enabled", "away", "comfort_min", "comfort_max"):
            if key in d:
                v = d[key]
                s = s.set(key, v if isinstance(v, str) else json.dumps(v))
        if "comfort_min" not in d:
            s = s.set("comfort_min", str(cls.comfort_min))
        if "comfort_max" not in d:
            s = s.set("comfort_max", str(cls.comfort_max))
        return s

    def set(self, key: str, raw: str) -> "Settings":
        """The settings with one command applied; unchanged if the command is invalid."""
        raw = raw.strip()
        if key in ("enabled", "away"):
            v = {"on": True, "true": True, "off": False, "false": False}.get(raw.lower())
            return self if v is None else replace(self, **{key: v})
        if key in ("comfort_min", "comfort_max"):
            try:
                v = float(raw)
            except ValueError:
                return self
            if v != v:  # NaN
                return self
            lo, hi = COMFORT_MIN_C if key == "comfort_min" else COMFORT_MAX_C
            v = min(max(v, lo), hi)
            if key == "comfort_min":
                return replace(self, comfort_min=min(v, self.comfort_max - MIN_BAND_K))
            return replace(self, comfort_max=max(v, self.comfort_min + MIN_BAND_K))
        return self
