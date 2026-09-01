FROM ghcr.io/astral-sh/uv:0.9.28 AS uv

FROM node:24.13.0-bookworm-slim AS web-build

RUN corepack enable

WORKDIR /build/web
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY web/index.html web/tsconfig.json web/tsconfig.app.json web/tsconfig.node.json ./
COPY web/vite.config.ts ./
COPY web/src ./src
RUN pnpm build

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
COPY --from=web-build /build/web/dist ./web/dist
COPY config/source_profiles/omf.json ./config/source_profiles/omf.json
COPY scripts/calibrate_search.py ./scripts/calibrate_search.py
COPY config/smoke/omf_mvp_v2.json ./config/smoke/omf_mvp_v2.json
RUN test -r config/source_profiles/omf.json \
    && test -r web/dist/index.html \
    && test -r scripts/calibrate_search.py \
    && test -r config/smoke/omf_mvp_v2.json \
    && uv sync --frozen --no-dev --no-editable \
    && chown -R omf-retrieval:omf-retrieval /app

USER omf-retrieval

CMD ["omf-retrieval", "serve", "--host", "0.0.0.0", "--port", "8000"]
