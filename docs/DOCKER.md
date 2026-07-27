# Docker

Curatarr publishes a single, multi-arch (`linux/amd64` + `linux/arm64`)
image that runs both the web UI and the recommender, from
`ghcr.io/orchestratedchaos/curatarr`.

- [Quick start (docker compose)](#quick-start-docker-compose)
- [Quick start (docker run)](#quick-start-docker-run)
- [Authentication](#authentication)
- [Config and cache volumes](#config-and-cache-volumes)
- [Accessing from another machine](#accessing-from-another-machine)
- [Scheduling recommendation runs](#scheduling-recommendation-runs)
- [Updating](#updating)
- [Verifying the image](#verifying-the-image)
- [Building the image yourself](#building-the-image-yourself)
- [Troubleshooting](#troubleshooting)

## Quick start (docker compose)

```bash
git clone https://github.com/OrchestratedChaos/curatarr.git
cd curatarr
mkdir -p config
./setup.sh                      # interactive setup wizard, writes config/config.yml
# or: cp config/config.example.yml config/config.yml && edit it by hand

# Required - see "Authentication" below. Uncomment CURATARR_AUTH_TOKEN in
# docker-compose.yml and set it, e.g.:
#   openssl rand -hex 32
docker compose up -d
```

Open `http://localhost:8787` and log in at `/login` with the token you
set - the dashboard, config screens (Connections/Libraries/Users/
Settings), and run-with-live-log all work exactly as they do for a
native install once logged in.

`docker-compose.yml` (in the repo root) is the template - it pulls the
published image by default; uncomment `build: .` in it if you'd rather
build from source.

## Quick start (docker run)

```bash
mkdir -p config cache logs recommendations
docker run -d \
  --name curatarr \
  -p 127.0.0.1:8787:8787 \
  -e CURATARR_AUTH_TOKEN="$(openssl rand -hex 32)" \
  -v "$(pwd)/config:/data/config" \
  -v "$(pwd)/cache:/data/cache" \
  -v "$(pwd)/logs:/data/logs" \
  -v "$(pwd)/recommendations:/data/recommendations" \
  --restart unless-stopped \
  ghcr.io/orchestratedchaos/curatarr:latest
```

Note the token above is regenerated (and lost) every time this exact
command is re-run - copy it down somewhere, or set `CURATARR_AUTH_TOKEN`
to a fixed value of your own instead, the same as you would for
`docker-compose.yml`.

## Authentication

The container always listens on `0.0.0.0` internally (see
[Accessing from another machine](#accessing-from-another-machine) for
why), so unlike the native app it needs real authentication, not just
the Host/Origin check every bind gets - `CURATARR_AUTH_TOKEN` is
required and the server refuses to start without one at least 16
characters long. Set it to a strong random value (`openssl rand -hex
32`) and keep it secret, the same as the Plex token/API keys under
`config/` - anyone who has it has full access to this instance,
including its Plex/Sonarr/Radarr/Trakt credentials.

Once set, log in at `http://localhost:8787/login` (or whatever host/port
you're reaching it on) with that token - a successful login sets a
cookie so the browser doesn't need to resend it on every request.
Scripts/reverse proxies can instead send it directly, either as
`Authorization: Bearer <token>` or `X-Curatarr-Token: <token>`.

This same token also gates `/metrics` (Prometheus text-format metrics) and
`/status.json` - see the main [README's Observability
section](../README.md#observability) for what each exposes and an example
Prometheus scrape config. `/login` and `/healthz` remain the only
unauthenticated routes.

### Opting out: `CURATARR_TRUSTED_NETWORK`

If you've decided the published port is genuinely unreachable by
anything untrusted (nothing published to the host at all, or a fully
isolated network) and don't want to manage a token, set
`CURATARR_TRUSTED_NETWORK=true` instead of `CURATARR_AUTH_TOKEN`. The
container starts without requiring a token - but every request is then
completely unauthenticated: anyone who CAN reach the port gets full
read/write access to this instance's config (Plex token, Sonarr/Radarr/
Trakt API keys, etc.) with no login at all. This is never inferred or
defaulted - you have to type it out - and the container prints a
prominent warning to its logs on every boot as a standing reminder that
it's running this way. If `CURATARR_AUTH_TOKEN` is also set,
`CURATARR_TRUSTED_NETWORK` has no effect - the token always wins.

If neither `CURATARR_AUTH_TOKEN` nor `CURATARR_TRUSTED_NETWORK=true` is
set and the bind host isn't loopback, the container refuses to start at
all (see [Troubleshooting](#troubleshooting)) rather than silently
running unauthenticated.

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

The UI only accepts requests whose `Host` header is `localhost`/
`127.0.0.1` (with or without a port) by default - the exact same rule
the native (non-Docker) app enforces. **This is a defense against
DNS-rebinding attacks and cross-site requests from a browser - it is
NOT authentication.** It only constrains what a *browser* can be
tricked into sending, because a browser is what actually enforces
same-origin policy on the Host/Origin/Referer headers in the first
place; a non-browser client (curl, a script, an attacker who found the
port) can set all three to whatever it wants and walk straight through
this check. Real access control for anything reachable from off the
Docker host is `CURATARR_AUTH_TOKEN` (see
[Authentication](#authentication)) - the container's process binds
`0.0.0.0` internally regardless of the Host-header setting below, so it
already requires that token to even start, but the Host check below is
still worth keeping tight as defense in depth on top of it.

`http://localhost:8787` on the machine actually running Docker works
without any extra configuration beyond the required
`CURATARR_AUTH_TOKEN`, but reaching the container from another device (a
LAN IP, a hostname behind a reverse proxy, a Tailscale address, etc.)
will get a `400 Bad Request` from the Host check above until you opt
that host in explicitly:

```yaml
environment:
  - CURATARR_ALLOWED_HOSTS=192.168.1.50:8787,curatarr.example.lan
```

(comma-separated, exact host[:port] match, case-insensitive). This is
additive-only - it never weakens the localhost/127.0.0.1 default, and
is unset (i.e. has no effect) unless you set it. It also has nothing to
do with `CURATARR_AUTH_TOKEN` - both are required for LAN/reverse-proxy
access; neither is a substitute for the other.

## Scheduling recommendation runs

There are TWO ways to schedule recurring runs. **Pick exactly one.**
Both ultimately call the same recommender code and share the same
cross-container run lock (`utils/run_lock.py`), so they can never
*overlap* each other or a manual web-UI-triggered run - but that lock
only prevents overlap, not duplication: if you enable the in-app
scheduler below *and* also have host cron or the `schedule` profile
active, you will get two separate runs at two different times of day,
each thinking it's the only one scheduled. Nothing in curatarr can
reliably detect that from inside the container (no Docker socket
access, no visibility into a sibling container's compose profile or
the host's crontab), so this is on you to avoid - use one mechanism,
not both.

### Option A: the in-app scheduler (recommended if you don't want to touch compose/cron)

Settings -> Scheduling: enable it, set a daily time (24-hour, in the
container's own `TZ` - see below), optionally restrict to specific
weekdays. Runs the `full` pipeline (movie, tv, external) from inside
this same container - no host cron, no second container, no compose
file editing.

- **Timezone** is always the container's `TZ` environment variable
  (`-e TZ=Australia/Melbourne`, etc.), falling back to UTC if unset -
  there's no separate timezone setting to configure or get out of sync.
  The Settings screen shows the resolved timezone and the computed
  next-run time so it's never ambiguous which clock is in play.
- If the scheduled time arrives while another run is already in
  progress (a web-UI-triggered run, or - see Option B below - the
  `schedule` profile's sibling container), that occurrence is **skipped
  and logged**, never queued. The dashboard shows the last scheduled
  attempt and its result (started / skipped / error).
- A missed occurrence (e.g. the container was down at the scheduled
  time) is **never** made up on the next start - only the next real
  occurrence ever fires. This matters for `docker compose up -d`,
  which happens on every redeploy: without this, a redeploy loop could
  trigger a surprise run (or several) every time, hammering Plex/TMDB.
- Off by default.

### Option B: host cron / the `schedule` compose profile

The web UI can trigger runs itself, but for a fully unattended/cron
style setup independent of the web container's own uptime, run the
recommender as a one-shot container instead:

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

**Host cron** (the original MVP for scheduling, still fully supported -
this image doesn't bundle its own cron daemon):

```cron
# Daily at 3 AM
0 3 * * * cd /path/to/curatarr && docker compose run --rm curatarr-recommend >> logs/daily-run.log 2>&1
```

`docker-compose.yml` includes `curatarr-recommend` behind the
`schedule` profile specifically for this - it's never started by a
plain `docker compose up`, only when explicitly targeted (by name, as
above, or via `docker compose --profile schedule up`).

This approach is recommended if you're already comfortable with
compose/cron, or want scheduling to keep working independently of
whether the web UI container happens to be up.

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

## Verifying the image

Every image `.github/workflows/docker.yml` publishes is signed
keylessly with [cosign](https://github.com/sigstore/cosign) right after
it's pushed - no long-lived private signing key exists anywhere for
this (unlike the release binaries' signing key, which is maintainer-held
and never touches CI - see `RELEASING.md`); instead cosign requests a
short-lived certificate from Sigstore's public Fulcio CA using that CI
job's own GitHub Actions OIDC identity, and records the signature in
Sigstore's public transparency log (Rekor).

Verify an image before running it with the
[cosign CLI](https://docs.sigstore.dev/cosign/installation/):

```bash
cosign verify \
  --certificate-identity-regexp '^https://github\.com/OrchestratedChaos/curatarr/\.github/workflows/docker\.yml@refs/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/orchestratedchaos/curatarr:latest
```

(swap `:latest` for a specific version tag, e.g. `:v2.9.0`, to verify
that exact release). A successful verification prints the signature's
Rekor transparency-log entry; a tampered or unsigned image fails
loudly instead of silently pulling.

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
- **Container exits immediately with a `CURATARR_AUTH_TOKEN` error on
  stderr** - expected, by design: see [Authentication](#authentication).
  Set `CURATARR_AUTH_TOKEN` (or `CURATARR_TRUSTED_NETWORK=true` if you've
  decided you don't need one) and restart.
- **`400 Bad Request` from the UI** - see
  [Accessing from another machine](#accessing-from-another-machine)
  above.
- **`401 Unauthorized` from the UI** - log in at `/login` with your
  `CURATARR_AUTH_TOKEN` value; see [Authentication](#authentication).
- **Permission denied under `/data`** - the container runs as uid/gid
  1000; `chown -R 1000:1000 config cache logs recommendations` on the
  host if those directories were created as a different user.
- **Healthcheck never goes healthy on a `recommend` container** -
  expected: `/healthz` is only served by the `web` mode. A one-shot
  `recommend` container is meant to run to completion and exit, not
  stay up.
- **Re-linking or re-authenticating Trakt** - the Connections screen
  can't complete Trakt's device-code OAuth flow itself (it's a live
  poll loop, not a single form submit); run it inside the container
  instead:

  ```bash
  docker exec -it curatarr python3 -m utils.trakt_auth
  ```

  (`curatarr` is this image's container name in `docker-compose.yml` -
  substitute your own if you run/named it differently.) Add `--reauth`
  to relink an account that's already linked (e.g. after a refresh
  failure) instead of hand-editing `config/trakt.yml` first.
