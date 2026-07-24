# Docker

Curatarr publishes a single, multi-arch (`linux/amd64` + `linux/arm64`)
image that runs both the web UI and the recommender, from
`ghcr.io/orchestratedchaos/curatarr`.

- [Quick start (docker compose)](#quick-start-docker-compose)
- [Quick start (docker run)](#quick-start-docker-run)
- [Config and cache volumes](#config-and-cache-volumes)
- [Accessing from another machine](#accessing-from-another-machine)
- [Scheduling recommendation runs](#scheduling-recommendation-runs)
- [Updating](#updating)
- [Building the image yourself](#building-the-image-yourself)
- [Troubleshooting](#troubleshooting)

## Quick start (docker compose)

```bash
git clone https://github.com/OrchestratedChaos/curatarr.git
cd curatarr
mkdir -p config
./setup.sh                      # interactive setup wizard, writes config/config.yml
# or: cp config/config.example.yml config/config.yml && edit it by hand

docker compose up -d
```

Open `http://localhost:8787` - the dashboard, config screens
(Connections/Libraries/Users/Settings), and run-with-live-log all work
exactly as they do for a native install.

`docker-compose.yml` (in the repo root) is the template - it pulls the
published image by default; uncomment `build: .` in it if you'd rather
build from source.

## Quick start (docker run)

```bash
mkdir -p config cache logs recommendations
docker run -d \
  --name curatarr \
  -p 8787:8787 \
  -v "$(pwd)/config:/data/config" \
  -v "$(pwd)/cache:/data/cache" \
  -v "$(pwd)/logs:/data/logs" \
  -v "$(pwd)/recommendations:/data/recommendations" \
  --restart unless-stopped \
  ghcr.io/orchestratedchaos/curatarr:latest
```

## Config and cache volumes

Everything persistent lives under a single internal root, `/data`
(`CURATARR_CONFIG_DIR=/data` in the image - see
`utils/helpers.get_project_root()`), with the same layout a
frozen/PyInstaller install already uses at `~/.curatarr`:

```
/data/config/config.yml, tuning.yml, trakt.yml, ...   <- back this one up
/data/cache/                                            <- TMDB/Trakt cache, fully rebuildable
/data/logs/
/data/recommendations/external/
```

`docker-compose.yml` mounts each of those individually
(`./config:/data/config`, `./cache:/data/cache`, etc.) so they show up
as their own directories on the host - the same layout `setup.sh` and
the pre-2.8.30 Docker image both already used, just repointed from
`/app/...` to `/data/...` internally. You don't have to mount all four:
skip `./cache`, for example, and cache just lives under `./config/cache`
instead - still persisted, just nested inside the config mount rather
than its own host directory.

There's also a separate, top-level **`/cache`** volume declared in the
image, if you'd rather keep the (large) cache off the config volume
entirely instead of just mounting it alongside as above - set
`cache_dir: /cache` in `config.yml` *and* mount `-v
"$(pwd)/cache:/cache"` to use it. Optional; most people don't need this
on top of the plain `./cache:/data/cache` mount above.

You don't need `config.yml` to already exist before starting the
container: the web UI's Connections screen creates it for you on first
save. `docker run curatarr recommend ...` (see
[Scheduling](#scheduling-recommendation-runs)) does need it to exist
first, though, since there's no browser involved for that one.

## Accessing from another machine

By default, the UI only accepts requests whose `Host` header is
`localhost`/`127.0.0.1` (with or without a port) - the exact same rule
the native (non-Docker) app enforces, and for the same reason: it's a
defense against DNS-rebinding attacks, not just a bind-address choice.
This means `http://localhost:8787` on the machine actually running
Docker works out of the box, but reaching the container from another
device (a LAN IP, a hostname behind a reverse proxy, a Tailscale
address, etc.) will get a `400 Bad Request` until you opt that host in
explicitly:

```yaml
environment:
  - CURATARR_ALLOWED_HOSTS=192.168.1.50:8787,curatarr.example.lan
```

(comma-separated, exact host[:port] match, case-insensitive). This is
additive-only - it never weakens the localhost/127.0.0.1 default, and
is unset (i.e. has no effect) unless you set it.

## Scheduling recommendation runs

The web UI can trigger runs itself, but for a fully unattended/cron
style setup, run the recommender as a one-shot container instead of
the long-running web service:

```bash
docker run --rm \
  -v "$(pwd)/config:/data/config" \
  -v "$(pwd)/cache:/data/cache" \
  ghcr.io/orchestratedchaos/curatarr:latest \
  recommend full
```

`recommend` accepts `full` (default - movie, then tv, then external),
or `movie` / `tv` / `external` individually; any extra arguments (e.g.
`--debug`, a specific username) are passed straight through to the
underlying recommender script.

**Host cron** (the clean MVP for scheduling - this image doesn't bundle
its own cron daemon):

```cron
# Daily at 3 AM
0 3 * * * cd /path/to/curatarr && docker compose run --rm curatarr-recommend >> logs/daily-run.log 2>&1
```

`docker-compose.yml` includes `curatarr-recommend` behind the
`schedule` profile specifically for this - it's never started by a
plain `docker compose up`, only when explicitly targeted (by name, as
above, or via `docker compose --profile schedule up`).

## Updating

This image updates via `docker pull`, not the web UI's "Update now"
button or `run.sh`'s own git-based auto-update - neither of those apply
inside a container (there's no on-disk `.git` to check out against, and
it isn't a frozen binary to swap in place), and both are explicitly
disabled here (`RUNNING_IN_DOCKER=true`) rather than left to fail by
accident. The update banner in the UI reflects this: it still tells you
a newer version exists, but points at `docker pull` instead of showing
a button that would just fail.

```bash
docker compose pull && docker compose up -d
# or, without compose:
docker pull ghcr.io/orchestratedchaos/curatarr:latest
docker stop curatarr && docker rm curatarr
# then re-run your `docker run` command from Quick start above
```

Pin to a specific version instead of `:latest` if you want explicit
control over when you move to a new release:
`ghcr.io/orchestratedchaos/curatarr:v2.8.30`. A `:edge` tag is also
published on every push to `main`, for testing unreleased changes -
not recommended for normal use.

## Building the image yourself

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t curatarr:local .
```

The published image is built the same way by
`.github/workflows/docker.yml`, from the hash-locked
`requirements.lock`/`requirements-ui.lock`/`requirements-docker.lock`
(`pip install --require-hashes`), multi-stage so the build toolchain
never ends up in the final image, and runs as a non-root user (uid/gid
1000).

The web UI runs on [waitress](https://docs.pylonsproject.org/projects/waitress/)
in the container - a production-grade, multi-threaded WSGI server -
rather than Flask's own single-threaded development server. This is
Docker-specific (`web/docker_server.py`, `requirements-docker.lock`):
the native app (`run-ui.sh`/`run-ui.ps1`, the standalone binaries)
still uses Flask's dev server bound to `127.0.0.1` only, which is fine
for a single local user and unrelated to this container's needs.

## Troubleshooting

```bash
# View logs
docker compose logs -f curatarr

# Check health
docker inspect --format='{{.State.Health.Status}}' curatarr

# Rebuild after local Dockerfile changes
docker compose build --no-cache
```

- **Connection refused to Plex** - use the host's IP, not `localhost`
  (the container has its own network namespace); `host.docker.internal`
  works on Docker Desktop.
- **`400 Bad Request` from the UI** - see
  [Accessing from another machine](#accessing-from-another-machine)
  above.
- **Permission denied under `/data`** - the container runs as uid/gid
  1000; `chown -R 1000:1000 config cache logs recommendations` on the
  host if those directories were created as a different user.
- **Healthcheck never goes healthy on a `recommend` container** -
  expected: `/healthz` is only served by the `web` mode. A one-shot
  `recommend` container is meant to run to completion and exit, not
  stay up.
