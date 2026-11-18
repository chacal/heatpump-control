"""Prometheus metrics and the latest plan over HTTP (stdlib `http.server`), read-only.

`/metrics` in the text exposition format; `/plan.json` the latest round's log line;
`/summary.json` the latest round in a few numbers per day, for a morning report (rounds.summary,
plus `age_min`: minutes since that round, so a reader sees when rounds have stopped). All read
one snapshot that the service replaces after each round. The service can run a second server
with only `/summary.json`, for the network (service.py).
"""

import json
import socket
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from heatpump_control.rounds import Result


class Snapshot:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.result: Result | None = None
        self.log: dict | None = None
        self.summary: dict | None = None
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

    def summary_json(self, now: datetime) -> bytes:
        with self._lock:
            res, summary = self.result, self.summary
        if res is None or summary is None:
            return b"null"
        return json.dumps(summary | {"age_min": round((now - res.at).total_seconds() / 60)}).encode()


MAX_CONNECTIONS = 4
CONNECTION_DEADLINE_S = 15

PATHS = ("/metrics", "/plan.json", "/summary.json")


class BoundedServer(ThreadingHTTPServer):
    """At most `max_connections` at once (more are closed at once), each closed after
    CONNECTION_DEADLINE_S: a client on the network can't grow the threads or the memory, or hold
    a connection open by trickling bytes."""

    daemon_threads = True

    def __init__(self, address, handler, max_connections: int):
        super().__init__(address, handler)
        self.slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        deadline = threading.Timer(CONNECTION_DEADLINE_S, _cut, (request,))
        deadline.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            deadline.cancel()
            self.slots.release()


def _cut(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def serve_http(snapshot: Snapshot, host: str, port: int, paths=PATHS, max_connections: int = MAX_CONNECTIONS) -> BoundedServer:
    class Handler(BaseHTTPRequestHandler):
        timeout = 10  # per read; the server's deadline bounds the whole connection

        def do_GET(self):  # noqa: N802
            if self.path not in paths:
                self.send_error(404)
                return
            if self.path == "/metrics":
                body, ctype = snapshot.render().encode(), "text/plain; version=0.0.4"
            elif self.path == "/plan.json":
                body, ctype = snapshot.plan_json(), "application/json"
            else:
                body, ctype = snapshot.summary_json(datetime.now(UTC)), "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = BoundedServer((host, port), Handler, max_connections)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
