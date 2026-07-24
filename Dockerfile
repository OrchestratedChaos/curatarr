# syntax=docker/dockerfile:1
#
# Curatarr container image. Two entrypoints share this same image (see
# docker-entrypoint.sh):
#   - the web UI (default CMD)          - a long-running waitress (production WSGI) server
#   - the recommender, as a one-shot    - `docker run curatarr recommend [movie|tv|external|full]`
#
# Multi-arch (linux/amd64 + linux/arm64) via `docker buildx build
# --platform linux/amd64,linux/arm64` - see .github/workflows/docker.yml.
#
# Deliberately multi-stage: the `deps` stage has a full build toolchain
# (needed under QEMU emulation for cross-arch builds, where pip can't
# always find/select a prebuilt manylinux wheel and falls back to
# compiling a dep like cryptography/cffi from sdist) that never makes
# it into the final `runtime` stage - only the resulting venv does.

# ---------------------------------------------------------------------
# Stage 1: build a venv from the hash-locked, pinned dependency set.
# ---------------------------------------------------------------------
FROM python:3.12-slim AS deps

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# requirements.lock/-ui.lock/-docker.lock are all fully SHA256-hashed
# (uv pip compile --generate-hashes) - `--require-hashes` refuses to
# install anything whose downloaded artifact doesn't match, the same
# supply-chain integrity guarantee run.sh/run-ui.sh already give source
# installs. requirements-docker.lock is Docker-only (currently just
# waitress - see web/docker_server.py) - never installed for a native
# source/binary install, which has no use for it.
COPY requirements.lock requirements-ui.lock requirements-docker.lock ./

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --require-hashes -r requirements.lock && \
    pip install --no-cache-dir --require-hashes -r requirements-ui.lock && \
    pip install --no-cache-dir --require-hashes -r requirements-docker.lock

# ---------------------------------------------------------------------
# Stage 2: lean runtime - just the venv + app code, no compiler.
# ---------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Curatarr" \
      org.opencontainers.image.description="Personalized recommendations for your Plex library" \
      org.opencontainers.image.source="https://github.com/OrchestratedChaos/curatarr" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="OrchestratedChaos"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    # Gates run.sh's own update check, web/update_apply.py's "Update
    # now" (self-update is a no-op in Docker - update via `docker
    # pull`), and web/app.py's update banner text - see docs/DOCKER.md.
    RUNNING_IN_DOCKER=true \
    # Single override point (utils/helpers.get_project_root) for where
    # config.yml/cache/logs/recommendations live - same "one root with
    # config/cache/logs/recommendations subdirectories" layout a
    # frozen/PyInstaller install already uses at ~/.curatarr (see that
    # function's docstring), just pointed at a volume instead. So
    # config.yml ends up at /data/config/config.yml, NOT /data/
    # config.yml directly - see VOLUME below and docs/DOCKER.md.
    CURATARR_CONFIG_DIR=/data

WORKDIR /app

COPY --from=deps /opt/venv /opt/venv

RUN groupadd --gid 1000 curatarr && \
    useradd --uid 1000 --gid curatarr --shell /usr/sbin/nologin --no-create-home curatarr

# App code only - no tests/, docs/, .github/, dev tooling (see
# .dockerignore for the full exclusion list).
COPY recommenders/ recommenders/
COPY utils/ utils/
COPY web/ web/
COPY run.sh docker-entrypoint.sh ./
RUN sed -i 's/\r$//' run.sh docker-entrypoint.sh && \
    chmod +x run.sh docker-entrypoint.sh

# /data: config/config.yml + tuning.yml/etc., logs/,
# recommendations/external/, and cache/ (unless cache_dir is pointed
# at the separate /cache path below - see docs/DOCKER.md). /cache is
# optional, only used if config.yml sets `cache_dir: /cache`
# explicitly - lets the (large, fully rebuildable) TMDB/Trakt cache
# live separately from what you'd actually back up.
#
# Deliberately no VOLUME instruction here: docker-compose.yml/
# docs/DOCKER.md bind-mount SUBDIRECTORIES of /data individually
# (./config:/data/config, ./cache:/data/cache, etc.) - a Dockerfile
# VOLUME on the /data parent itself creates an anonymous volume that
# shadows those more specific bind mounts instead of the other way
# around (a well-known Docker gotcha), silently losing writes to the
# host. Bind/named-volume mounting works the same with or without a
# VOLUME instruction; it isn't required for `-v`/`--mount` at `docker
# run`/compose time to work, only for Docker to auto-create an
# anonymous volume when a path is left unmounted.
RUN mkdir -p /data/config /cache && chown -R curatarr:curatarr /app /data /cache

USER curatarr

EXPOSE 8787

# Hits the web UI's own /healthz - a no-op (always unhealthy, by
# design) for a one-shot `recommend` container, which is expected to
# run to completion and exit rather than stay up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["web"]
