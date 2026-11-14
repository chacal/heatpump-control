# heatpump-control

Shifts a Viessmann Vitocal 333-G's heating into cheap spot-price hours while keeping the rooms
inside a comfort band. A home project, built as a test of how an agent builds and ships a small
home-automation app; public in case it is useful to others.

**Status:** design and analysis done, no controller yet. Start with [`docs/spec.md`](docs/spec.md).

- [`docs/spec.md`](docs/spec.md): goal, lever, safety, milestones
- [`analysis/`](analysis/): backtests and house-model scripts (the data is not included)

## Develop

```
uv sync
uv run pytest
```

CI (`.github/workflows/docker.yml`) runs the tests, then builds and pushes
`ghcr.io/chacal/heatpump-control` (`latest` and the commit sha, amd64 + arm64) from `main`.
