"""The pump's own time program, and the setpoint writes that override it.

The time program switches which room setpoint is active (today Reduced 07-21, Normal 21-07,
local time). It switches only which one is active, not their values (M1), so the controller
writes the active one: the heat value to ask for heat, the idle value to let the compressor
rest. A plan that matches the program needs no writes. Writes go to the pump's EEPROM, so they
are capped per day.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo

HELSINKI = ZoneInfo("Europe/Helsinki")


@dataclass(frozen=True)
class Program:
    reduced_from: time = time(7)
    normal_from: time = time(21)
    tz: ZoneInfo = HELSINKI
    heat_c: float = 23.0  # today's Normal setpoint
    idle_c: float = 18.0  # today's Reduced setpoint

    def active(self, at: datetime) -> str:
        local = at.astimezone(self.tz).time()
        return "reduced" if self.reduced_from <= local < self.normal_from else "normal"

    def defaults(self) -> dict[str, float]:
        return {"normal": self.heat_c, "reduced": self.idle_c}

    def heating(self, at: datetime) -> bool:
        """Whether the program alone asks for heat at `at`."""
        return self.active(at) == "normal"


@dataclass(frozen=True)
class Write:
    at: datetime
    register: str  # "normal" or "reduced"
    value: float


@dataclass
class Setpoints:
    writes: list[Write] = field(default_factory=list)

    def per_day(self, tz: ZoneInfo = HELSINKI) -> Counter:
        return Counter(w.at.astimezone(tz).date() for w in self.writes)


def setpoint_writes(
    starts: list[datetime],
    heating: list[bool],
    program: Program = Program(),
    current: dict[str, float] | None = None,
) -> Setpoints:
    """The writes that make the pump follow `heating`, from the registers' `current` values
    (default: the program's own)."""
    registers = dict(current or program.defaults())
    out = Setpoints()
    for at, h in zip(starts, heating, strict=True):
        register = program.active(at)
        value = program.heat_c if h else program.idle_c
        if registers[register] != value:
            registers[register] = value
            out.writes.append(Write(at, register, value))
    return out
