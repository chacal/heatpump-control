FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.13-slim
RUN useradd --system --uid 65532 --no-create-home app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 65532
# No controller yet (spec milestone M1/M2); the entrypoint is a placeholder.
ENTRYPOINT ["python", "-c", "import heatpump_control; print(heatpump_control.__doc__)"]
