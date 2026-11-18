"""The bedroom median from the room sensors' MQTT messages.

Each sensor publishes `/sensor/<id>/m/state` every ~5 s with JSON like
`{"instance": "S202", "temperature": 21.9, "ts": "2026-11-15T07:08:26.737Z", ...}`. A dying
battery reads 0-1 °C, so readings outside 15-35 °C are dropped, as are stale ones; the median of
what is left steers, so one bad sensor doesn't move it.
"""

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

BEDROOMS = ("S202", "S203", "S204", "S211")
VALID_C = (15.0, 35.0)
MAX_AGE = timedelta(minutes=5)
MAX_SKEW = timedelta(minutes=2)


@dataclass
class Rooms:
    sensors: tuple[str, ...] = BEDROOMS
    latest: dict[str, tuple[datetime, float]] = field(default_factory=dict)

    def update(self, topic: str, payload: object, now: datetime | None = None) -> None:
        """Keeps the newest valid reading per bedroom. Anything malformed is ignored: a reading
        without a timezone, one stamped in the future (past `now` + MAX_SKEW; a retained one
        could otherwise pin the sensor), or a temperature outside VALID_C."""
        parts = topic.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "sensor" or parts[2:] != ["m", "state"] or parts[1] not in self.sensors:
            return
        if not isinstance(payload, dict):
            return
        t, ts = payload.get("temperature"), payload.get("ts")
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not VALID_C[0] <= t <= VALID_C[1] or not isinstance(ts, str):
            return
        try:
            at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return
        if at.tzinfo is None or (now is not None and at > now + MAX_SKEW):
            return
        if parts[1] not in self.latest or at > self.latest[parts[1]][0]:
            self.latest[parts[1]] = (at, float(t))

    def update_archive_line(self, line: str) -> None:
        """One line of the sensor archive's JSONL (`{"topic": ..., "payload": {...}}`)."""
        d = json.loads(line)
        self.update(d["topic"], d["payload"])

    def fresh(self, now: datetime) -> dict[str, float]:
        return {s: t for s, (at, t) in self.latest.items() if now - at <= MAX_AGE}

    def median(self, now: datetime) -> float | None:
        fresh = self.fresh(now)
        return statistics.median(fresh.values()) if fresh else None
