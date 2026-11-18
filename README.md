# heatpump-control

Shifts a Viessmann Vitocal 333-G's heating into cheap spot-price hours while keeping the rooms
inside a comfort band. A home project, built as a test of how an agent builds and ships a small
home-automation app; public in case it is useful to others.

**Status:** M2 (shadow mode): `python -m heatpump_control serve` (the image's entrypoint) reads
the bedroom sensors and its controls from MQTT, plans every 15 minutes and publishes Home
Assistant entities (MQTT discovery), Prometheus metrics (`:9841/metrics`) and the latest plan
(`:9841/plan.json`); it writes nothing to the pump. Settings in
[`service.py`](src/heatpump_control/service.py)'s docstring. `python -m heatpump_control plan`
runs one round with room readings on stdin. Start with [`docs/spec.md`](docs/spec.md).

- [`docs/spec.md`](docs/spec.md): goal, lever, safety, milestones
- [`analysis/`](analysis/): backtests and house-model scripts (the data is not included)

## Develop

```
uv sync
uv run pytest
```

`scripts/dev uv run pytest` does the same inside a rootless podman container, so the
dependencies never run on the host.

The MQTT test against a real broker runs when `MQTT_TEST_BROKER=host:port` points at a throwaway
mosquitto (anonymous access), e.g. one started with podman next to the test container.

CI (`.github/workflows/docker.yml`) runs the tests, then builds and pushes
`ghcr.io/chacal/heatpump-control` (`latest` and the commit sha, amd64 + arm64) from `main`.
