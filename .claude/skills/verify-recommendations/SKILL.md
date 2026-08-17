---
name: verify-recommendations
description: Verify Curatarr's recommendations against the LIVE Plex server - what is actually in each user's collection, not what a run log claims. Use after a nightly run, after changing scoring/filtering/franchise logic, or whenever you need to answer "did that actually work?" Covers reaching Plex from a machine that is not the Plex host, the read-only audit script, and the traps that make log-based verification wrong.
---

# Verifying Curatarr recommendations against live Plex

## Why this exists

`CLAUDE.md` says it plainly: **the Plex collection is the artifact, not
the log.** The log prints `plex_recs` (up to `general.limit_plex_results`,
2x the target); the collection holds the smaller `final_items` set. One
real run printed 50 titles with 19 kid films while the collection held 44
with 7. Anything concluded from the log alone about *what a user got* is a
guess.

Measuring the real thing means `section.search(label="Recommended_<user>")`
against a live server — and the catch is that a Curatarr checkout is often
not on the machine Plex runs on.

## Run it

```bash
.claude/skills/verify-recommendations/run_remote.sh [--user NAME] [--media movie|tv]
```

It probes whether Plex is reachable from here and either runs locally or
delegates over SSH. Exit code is 1 if any check fails, so it also works as
a post-run gate.

When Plex is on another machine, set these once in your shell profile
(same convention as `CURATARR_GH_SSH_HOST` in `RELEASING.md`):

| Variable | Meaning |
|---|---|
| `CURATARR_PLEX_SSH_HOST` | SSH alias/host running Plex, with a checkout of this repo. No default. |
| `CURATARR_PLEX_SSH_REPO_DIR` | Absolute path to that checkout. Defaults to `~/dev/curatarr`. |

Reachability is **probed, not inferred from config** — `plex.url` is
commonly a `127-0-0-1.<hash>.plex.direct` hostname, which resolves to
`127.0.0.1` on every machine and therefore tells you nothing about where
Plex actually is. Same reasoning that rewrote `scripts/release.sh`'s
origin check in 2.19.1.

## If the repo lives on a network share

A common setup here: the checkout is an SMB/NFS mount of the Plex host's
own directory, so both machines see one set of files. Two consequences:

- **Editing the script locally is enough** — the remote runs the same
  file. Nothing to copy or sync.
- **`.venv` belongs to whichever machine built it.** Architectures differ
  (arm64 host, x86_64 laptop), so the shared venv only runs on its
  creator. `run_remote.sh` uses the remote's own `.venv` when delegating,
  and falls back to `python3` if there isn't one.

## Fallback: SSH tunnel

Only when you want *local* tooling (a scratch venv, an interactive
session) pointed at the real server:

```bash
ssh -f -N -o ExitOnForwardFailure=yes -L 32400:127.0.0.1:32400 "$CURATARR_PLEX_SSH_HOST"
# ... read-only work ...
pkill -f "ssh -f -N -o ExitOnForwardFailure=yes -L 32400"
```

**Forward to `127.0.0.1`, not the host's LAN IP.** The plex.direct
certificate is issued for 127.0.0.1, so the loopback forward is what keeps
the hostname both resolving *and* passing TLS verification, letting
`config/config.yml` work untouched. Always tear the tunnel down.

Building a local venv on x86_64 macOS needs one pin relaxed:
`requirements.lock` pins `cryptography==50.0.0`, which ships no x86_64
macOS wheel. `cryptography<49` installs; everything else is as written.

## Read-only discipline

This audits a live server other people use. The script only ever calls
`section.search`, per-user played-id reads, and local JSON reads.

**Never construct a recommender to inspect state.**
`PlexMovieRecommender.__init__` connects as the admin and calls
`MovieCache.update_cache()`, which rewrites `cache/all_movies_cache.json`;
`manage_plex_labels()` adds and removes real labels. If you want a real
run, run the app properly (`./run.sh`) — don't half-invoke it from a REPL.

## What it checks

Per configured user, against their live collection:

1. **Size** vs `movies.limit_results` / `tv.limit_results`, flagging a short collection.
2. **Watched items still labeled** — split into a real failure vs expected lag, see below.
3. **Excluded genres** (`general.exclude_genre` + per-user `exclude_genres`).
4. **`max_rating`** against each item's live `contentRating`.
5. **The franchise invariant** — every collection item belonging to a
   multi-entry TMDB collection must *be* the canonical
   earliest-eligible-unwatched entry for that user
   (`utils/franchise.find_next_unwatched`). One check covers both halves of
   franchise ordering: promotion on a started series and suppression on an
   unstarted one both converge on "the canonical entry, or the series is
   absent entirely".

## Traps that make verification wrong

- **`watched_cache_*.json` alone under-reports what a user has seen.** It's
  the shared history; each user's own Plex view knows about plays it
  doesn't. The recommender unions both (`_franchise_watched_ids()`), and so
  must any check — otherwise a started series looks unstarted and the
  franchise invariant appears to fail.
- **But the two must stay distinguishable for the watched-item check.** An
  item the last run *already knew* was watched, still labeled, is a real
  failure. An item watched *since* that run is lag the next run clears in
  `_remove_outdated_labels()`. Collapsing them makes this tool cry wolf
  every time somebody watches a film — which is exactly what the first
  draft of this script did, on its first run.
- **`cached_score` is not per-user.** `all_movies_cache.json` is shared and
  has been observed holding 6 distinct `profile_hash` values at once; each
  movie's score belongs to whichever user's run last touched it. Per-user
  *rankings* are not reconstructible offline — only score-independent facts
  (which entry of a series, genre/rating compliance) are.
- **Movie and TV logs are named identically** (`recommendations_<user>_<ts>.log`).
  The movie run comes first and its file is roughly 3x larger; sorting by
  mtime gets you the TV one.
- **`log_warning()` never reaches `logs/recommendations_<user>_*.log`.**
  That file captures stdout only; warnings go to the console / aggregate log.

## Reference output

A healthy movie run:

```
Connected to <server> (Plex 1.43.x) - READ ONLY

  alice          Recommended_alice     50 items | series 19 (11 started) | OK
       i watched since the last run, clears tonight: Some Film (2018)
  bob            Recommended_bob       50 items | series 12 (4 started)  | OK
  carol          Recommended_carol     50 items | series  8 (5 started)  | OK

All checks passed
```

Franchise items sitting at roughly 8–19 of 50 is the one-slot-per-series
cap doing its job. If that share climbs toward the whole collection,
franchise ordering has started promoting rather than suppressing — the
regression 2.21.0 exists to prevent.

On the TV library expect **0 series items**: TMDB collections are a
movie-side concept, no `collection_id` is cached for shows, and franchise
ordering is inert there by design.
