FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POLICYNIM_LANCEDB_URI=/app/data/lancedb-baked

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY policies ./policies
COPY evals ./evals

ARG NVIDIA_API_KEY

RUN uv sync --frozen
RUN export NVIDIA_API_KEY && uv run policynim ingest


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POLICYNIM_LANCEDB_URI=/app/data/lancedb-baked \
    POLICYNIM_MCP_HOST=0.0.0.0 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/policies /app/policies
COPY --from=builder /app/evals /app/evals
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY --from=builder /app/README.md /app/README.md
COPY --from=builder /app/data/lancedb-baked /app/data/lancedb-baked

CMD ["policynim", "mcp", "--transport", "streamable-http"]
