FROM ghcr.io/astral-sh/uv:0.9.28 AS uv

FROM python:3.12.12-slim-bookworm

ARG APP_UID=10001
ARG APP_GID=10001

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" omf-retrieval \
    && useradd --no-log-init --create-home --uid "${APP_UID}" \
        --gid "${APP_GID}" omf-retrieval

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && chown -R omf-retrieval:omf-retrieval /app

USER omf-retrieval

CMD ["omf-retrieval", "serve", "--host", "0.0.0.0", "--port", "8000"]
