"""Prometheus metrics and the latest plan over HTTP (stdlib `http.server`).

`/metrics` in the text exposition format; `/plan.json` the latest round's log line (read-only,
for a morning report). Both read one snapshot that the service replaces after each round.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from heatpump_control.rounds import Result


class Snapshot:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.result: Result | None = None
        self.log: dict | None = None
        self.rounds_ok = 0
        self.rounds_failed = 0
        self.last_error = ""
        self.mqtt_connected = False

    def update(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def count(self, ok: bool, error: str = "") -> None:
        with self._lock:
            if ok:
                self.rounds_ok += 1
            else:
                self.rounds_failed += 1
                self.last_error = error

    def render(self) -> str:
        with self._lock:
            res, ok, failed, conn = self.result, self.rounds_ok, self.rounds_failed, self.mqtt_connected
        lines = [
            "# HELP heatpump_control_rounds_total Planning rounds by outcome.",
            "# TYPE heatpump_control_rounds_total counter",
            f'heatpump_control_rounds_total{{outcome="ok"}} {ok}',
            f'heatpump_control_rounds_total{{outcome="failed"}} {failed}',
            "# HELP heatpump_control_mqtt_connected 1 while connected to the broker.",
            "# TYPE heatpump_control_mqtt_connected gauge",
            f"heatpump_control_mqtt_connected {int(conn)}",
        ]
        if res is not None:
            r = res.round
            gauges = {
                "last_round_timestamp_seconds": ("Time of the last successful round.", res.at.timestamp()),
                "bedroom_median_celsius": ("Bedroom median at the last round.", res.t_room),
                "slab_estimate_celsius": ("Floor slab estimate at the last round.", res.t_slab),
                "heating_planned": ("1 if the plan asks for heat now.", int(r.plan.heating[0])),
                "program_heating": ("1 if the pump's own time program asks for heat now.", int(r.baseline.heating[0])),
                "plan_cost_euros": ("Plan's electricity over the horizon, spot incl. VAT.", r.plan.cost_eur),
                "program_cost_euros": ("The time program's electricity over the horizon.", r.baseline.cost_eur),
                "savings_estimate_euros": ("Program minus plan, heat left in the house valued.", r.savings_eur),
                "planned_writes": ("Setpoint writes in the plan over the horizon.", len(r.setpoints.writes)),
                "horizon_slots": ("15-minute slots planned.", len(r.slots)),
                "comfort_min_celsius": ("Comfort band, lower end.", res.settings.band()[0]),
                "comfort_max_celsius": ("Comfort band, upper end.", res.settings.band()[1]),
                "predicted_bedroom_4h_celsius": ("Planned bedroom median 4 h ahead.", r.plan.t_room[min(15, len(r.plan.t_room) - 1)]),
            }
            for name, (help_, v) in gauges.items():
                lines += [f"# HELP heatpump_control_{name} {help_}", f"# TYPE heatpump_control_{name} gauge", f"heatpump_control_{name} {v}"]
        return "\n".join(lines) + "\n"

    def plan_json(self) -> bytes:
        with self._lock:
            return json.dumps(self.log).encode() if self.log else b"null"


def serve_http(snapshot: Snapshot, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/metrics":
                body, ctype = snapshot.render().encode(), "text/plain; version=0.0.4"
            elif self.path == "/plan.json":
                body, ctype = snapshot.plan_json(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
