# Investigators backend image (API and worker share one image; Compose selects the command).
# Build context: ./backend only (declared source root). Dependencies come from uv.lock.
# PYTHON_IMAGE is pinned by digest in docker-compose.yml build args (see docs/TOOLCHAIN.md).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE} AS base

ARG UV_VERSION=0.9.18
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /srv/investigators
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project && rm -rf /root/.cache

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
# Versioned experiment content (manifests, artifacts, publication index) from the declared
# ./content source root, supplied as the named build context "content" (docker-compose.yml).
COPY --from=content . ./content

ARG INVESTIGATORS_BUILD_SOURCE_SHA256=unset
ARG INVESTIGATORS_BEHAVIOR_SOURCE_SHA256=unset
ARG INVESTIGATORS_BUILD_TIME=unset
ARG INVESTIGATORS_IMAGE_REF=investigators-backend:local
ENV INVESTIGATORS_BUILD_SOURCE_SHA256=${INVESTIGATORS_BUILD_SOURCE_SHA256} \
    INVESTIGATORS_BEHAVIOR_SOURCE_SHA256=${INVESTIGATORS_BEHAVIOR_SOURCE_SHA256} \
    INVESTIGATORS_BUILD_TIME=${INVESTIGATORS_BUILD_TIME} \
    INVESTIGATORS_IMAGE_REF=${INVESTIGATORS_IMAGE_REF}

RUN useradd --system --uid 10519 --no-create-home --shell /usr/sbin/nologin investigators
USER investigators

EXPOSE 8000
CMD ["python", "-m", "app.serve"]
