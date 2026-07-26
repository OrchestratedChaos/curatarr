# Changelog

All notable changes to Curatarr will be documented in this file.

## [2.10.23] - 2026-07-26

### Fixed

- **`tuning.yml`'s `movies:`/`tv:` settings were silently ignored for
  most of the options documented there - YOUR RECOMMENDATIONS WILL
  LOOK DIFFERENT after upgrading if you'd customized any of these.**
  `recommenders/base.py` (and `recommenders/movie.py`'s
  `show_director`) read `randomize_recommendations`, `quality_filters`
  (`min_rating`/`min_vote_count`), scoring `weights`,
  `normalize_counters`, and every `show_*` display option
  (`show_summary`/`show_cast`/`show_director`/`show_genres`/
  `show_language`/`show_rating`/`show_imdb_link`) from the root
  `general:` section (or, for `weights`/`quality_filters`, straight
  off the config root) instead of the documented `movies:`/`tv:`
  section `config/tuning.example.yml` actually tells you to put them
  in - so none of these ever took effect no matter what you set in
  `tuning.yml`. A parallel, correctly-wired resolution function
  (`utils/config.py`'s `adapt_config_for_media_type()`) already existed
  and computed the right values, but nothing in the actual
  recommendation-generation path ever used its output - it was
  effectively dead code as far as `movies:`/`tv:` overrides go. Fixed
  by reading each of these from the media-specific section first,
  falling back to the old `general:`/root-level key so any install
  that (for whatever undocumented reason) had these set there keeps
  behaving exactly as before. Concretely, this means:
  - If you set `quality_filters.min_rating`/`min_vote_count` under
    `movies:`/`tv:`, low-rated/low-vote-count titles that were
    slipping through before will now actually be filtered out.
  - If you set `randomize_recommendations: false`, your recommendation
    order will now stay stable run-to-run instead of being reshuffled
    every time.
  - If you turned on `show_cast`/`show_director`/`show_language`/
    `show_rating`/`show_imdb_link`/`show_summary` (several default to
    off unless explicitly enabled), those fields will now actually
    appear in the printed recommendation output - they were silently
    missing before regardless of this setting.
  - Custom scoring `weights` under `movies:`/`tv:` now actually change
    how recommendations are ranked, instead of always using the
    built-in defaults.
  Added test coverage (`tests/test_base.py`, `tests/test_movie.py`)
  asserting the resolved runtime attribute matches what's set in the
  media-specific section, plus the pre-existing general-level fallback
  behavior for back-compat installs.

### Added

- **`utils/scoring.py`'s `select_tiered_recommendations()` now accepts
  an optional `rng: random.Random` parameter.** Purely additive -
  default (`None`) preserves the exact existing behavior of drawing
  from the module-level, process-global `random` (still unseeded by
  default in production). Lets tests/tooling pass an explicitly seeded
  `random.Random(seed)` for reproducible selection without touching
  global interpreter state.
- **`tests/harness.py` + `tests/test_harness.py`: a committed,
  reusable deterministic harness** for verifying scoring/pipeline
  refactors don't silently change output. Loads fully-synthetic, pinned
  fixtures (`tests/fixtures/scoring_harness/` - shaped like
  `cache/all_movies_cache.json` and the user-profile dict built from
  `cache/watched_cache_plex_<user>.json`, but with invented titles/
  cast/director/keyword names; no real Plex data, usernames, or watch
  history), forces a from-scratch score recompute (never the
  `profile_hash` cache-hit shortcut), seeds the RNG explicitly, and is
  meant to run under `PYTHONHASHSEED` pinned as belt-and-braces. Run
  twice with the same seed/hash-seed, output is byte-identical
  (asserted in `tests/test_harness.py`).

## [2.10.22] - 2026-07-26

### Added

- **Self-update E2E: `missing_asset` scenario.** The real end-to-end
  self-update harness (`scripts/selfupdate_e2e/`) had no coverage for
  "the requested release asset doesn't exist" (a 404 on the platform
  asset download) - the exact situation discussion #207 describes for
  pre-2.10.0 macOS binaries once the transitional
  `curatarr-macos-universal` duplicate is dropped from newer releases.
  Added a `missing_asset` scenario: a fixture release directory whose
  `SHA256SUMS.txt`/`.sig` are present and correctly signed (the
  release itself is real) but never lists or ships the requested
  platform asset filename, so the fake release server's existing
  "file not on disk -> 404" path fires exactly as a real GitHub
  release missing that one asset would. Asserts the update is refused
  before signature/hash verification ever runs, the running binary's
  SHA256 is byte-for-byte unchanged, no temp download artifact is left
  behind, and `update_apply.log` shows the same `verify failed` refusal
  line already asserted for `bad_sig`/`bad_hash`. Wired into
  `.github/workflows/selfupdate-e2e.yml` alongside the four existing
  scenarios. Also added a unit-level integration test in
  `tests/test_self_update.py` (`TestDownloadAndVerifyUpdate`) covering
  a 404 through the full `download_and_verify_update()` path - the
  existing 404 coverage
  (`TestDownloadToFile::test_http_error_status_raises_download_error`)
  only exercised the low-level `_download_to_file` helper in isolation.

  Verified this scenario has teeth: temporarily patching
  `download_and_verify_update` to swap the binary in before
  verification (bypassing the fail-closed download-error path)
  reliably fails the scenario's own hash-unchanged assertion - not
  just the E2E job, but a targeted local check of the same assertion
  path.

## [2.10.21] - 2026-07-26

### Changed

- **Audit remediation: API client consolidation** (batch 2, PR C).
  `TraktClient` and `SimklClient` now subclass `utils/api_client.py`'s
  `BaseAPIClient` (`sonarr.py`/`tautulli.py`/`mdblist.py`/`radarr.py`
  already did) instead of hand-rolling their own near-identical
  `_rate_limit()` and 429-retry-with-backoff loop. `BaseAPIClient`
  gained a new `_send_with_retries()` primitive (rate limiting + a
  bounded, `Retry-After`-honoring 429 retry loop, opt-in via
  `max_429_retries`/`max_retry_after_seconds` - default 0 retries, so
  `_make_request_to_url` and every existing subclass built on it are
  completely unchanged) that both clients now call, while keeping
  their own status-code handling local (Trakt's security-sensitive
  OAuth-refresh-on-401 retry, Simkl's 401/404 mapping) - migration
  only, no behavior change. Verified against the real, live Trakt API
  (not just mocks): identical response before/after for
  `get_username()`/`get_trending()`.
  `utils/tmdb.py`'s `fetch_tmdb_with_retry` was evaluated but left
  as a documented third implementation: it's function-based (no
  client instance to attach `BaseAPIClient` to without inventing a new
  `TMDBClient` class and touching every call site across the
  codebase) and its 429 backoff is linear (`2*(attempt+1)` seconds)
  rather than `Retry-After`-header-driven like the shared path -
  forcing it through would either change its effective backoff timing
  or require a second retry style option, both a bigger structural
  change and more regression risk than this migration's scope
  justifies.

## [2.10.20] - 2026-07-26

### Changed

- **Audit remediation: eliminated duplicated cache/recommender code**
  (batch 2, PR B). Four hand-rolled JSON cache implementations
  (`recommenders/external.py`'s `load_cache`/`save_cache`,
  `load_huntarr_cache`/`save_huntarr_cache`,
  `load_horizon_cache`/`save_horizon_cache`, and
  `recommenders/external_render.py`'s `_load_imdb_cache`/`_save_imdb_cache`)
  now route through the shared `utils/cache.py` helpers
  (`load_json_cache`/`save_json_cache`) instead of duplicating
  open/json.load/json.dump by hand - the huntarr/horizon caches also
  gain `curatarr_cache_lookups_total` hit/miss metrics as a result,
  previously invisible. Existing on-disk cache files verified to still
  load correctly (version/staleness semantics unchanged - only the
  raw I/O plumbing moved). `_load_imdb_cache`'s bare
  `except (...): pass` (silently swallowing write failures) now logs
  via the shared helper instead of hiding the error.
- **`collect_tmdb_ids` deduplicated** in `recommenders/external_sync.py`
  - was defined identically inside both `export_to_mdblist` and
  `export_to_simkl`; hoisted to one module-level function.
- **`_calculate_rating_multiplier`, `_save_cache`, and
  `_print_similarity_breakdown` deduplicated** between
  `recommenders/movie.py` and `recommenders/tv.py` - identical (or
  differing only by the `self.media_type` literal) implementations
  hoisted onto `recommenders/base.py`'s `BaseRecommender`.
  `_print_similarity_breakdown` is no longer `@abstractmethod` (now a
  concrete shared implementation); `BaseRecommender` still can't be
  instantiated directly since other abstract methods remain.

## [2.10.19] - 2026-07-26

### Added

- **README.md now documents how to actually run the test suite** - a
  brief Development section with the real commands (previously only
  discoverable by reading `.github/workflows/tests.yml`).
- **Upfront, actionable error when the TMDB API key is missing** for
  external recommendations (`recommenders/external.py`). Unlike
  `movie.py`/`tv.py` (where `tmdb_api_key` is genuinely optional -
  every use there is guarded with `if tmdb_api_key`, degrading to
  Plex-native-only scoring without one), external recommendations have
  no degraded mode: every candidate comes from TMDB, so a missing key
  previously produced a silently empty/broken watchlist instead of a
  clear error (`fetch_tmdb_with_retry()` swallows every TMDB failure
  into `None` by design). Now fails fast with a link to get a free key.

### Fixed

- **`.github/ISSUE_TEMPLATE/bug_report.md` still had the stock
  "Smartphone"/"Desktop" sections** from GitHub's default template -
  irrelevant to a self-hosted Python/Docker app. Rewritten to ask for
  Curatarr version, install method, OS, Plex version, and a relevant
  log excerpt.
- **`CLAUDE.md` said "Python 3.8+"** (the real floor is 3.10+, per
  `README.md` and `requirements.txt`'s plexapi/requests pins) - fixed
  in place (gitignored, not part of this PR).

### Changed

- **Renamed `recommenders/external_output.py` -> `external_render.py`**
  (renders markdown/HTML) and **`recommenders/external_exports.py` ->
  `external_sync.py`** (pushes to Trakt/Sonarr/Radarr/MDBList/Simkl) -
  clearer names for what each module actually does. Verified every
  import/patch-target/comment reference across the codebase (grepped
  exhaustively - 159 occurrences updated) and confirmed
  `curatarr.spec` has no explicit reference to either module name (it
  only lists `curatarr_app.py` as the Analysis entry point; PyInstaller
  follows the rest of the import graph automatically). Verified against
  the real frozen binary, not just the build log: a fresh
  `pyinstaller curatarr.spec` build succeeded, `--version` reported
  2.10.19, and `--run-recommender external` reached
  `recommenders/external.py`'s `_main_impl()` (importing both renamed
  modules) with no `ImportError`.

## [2.10.18] - 2026-07-26

### Changed

- **Extracted two byte-identical duplications across the shell install
  scripts into `scripts/lib/`:**
  - `scripts/lib/pip-install.sh` (`curatarr_pip_install`) - the
    "prefer hash-verified lockfile(s), fall back to plain pinned
    requirements" pip install logic `run.sh` and `run-ui.sh` each
    implemented independently (their own comments cross-referenced
    each other's copy). Each script keeps its own success/failure
    messaging and timing via callback functions - user-visible output
    is unchanged.
  - `scripts/lib/colors.sh` - the ANSI colour variables `run.sh` and
    `setup.sh` each defined byte-identically. `docker-entrypoint.sh`
    intentionally keeps its own (still byte-identical) RED/YELLOW/NC
    subset - `.dockerignore` excludes `scripts/` wholesale from the
    Docker build context, and carving out a negation exception for one
    3-line file wasn't worth the added build fragility.
  - `run.sh` is the one script from this pair that IS shipped inside
    the Docker image (alongside `docker-entrypoint.sh`, even though
    nothing in the image actually invokes it) - `Dockerfile` now also
    `COPY`s `scripts/lib/` and `.dockerignore` carries a matching
    negation, verified with a real `docker build` + container run
    (previously would have failed with `run.sh: line N:
    /app/scripts/lib/colors.sh: No such file or directory` if anyone
    ran it inside the container).
  - One intentional, minor behavior change: if `run.sh` finds neither
    `requirements.lock` nor `requirements.txt` at all (previously
    silent no-op), it now fails clearly with the same
    "Failed to install Python dependencies" error used for every other
    install failure, instead of continuing on to fail later with a
    more confusing error deeper in the app.
- **Documented (not converged) the 4x version-comparison duplication**
  (`utils/update_check.py`'s `parse_version()`, `run.sh`'s
  `version_gt`/`version_ge`, `run-ui.sh`'s inline copy, `run.ps1`'s
  `ConvertTo-VersionTuple`): genuinely irreducible, for two independent
  reasons now documented at each site - (1) the Python-floor gate in
  all three scripts runs before dependencies are installed, so calling
  into `utils.update_check` (which pulls in `utils/__init__.py`'s
  ~20 third-party-backed submodule imports) isn't safe on a fresh
  checkout; (2) that same floor check compares a 3-component runtime
  version against a 2-component `requirements.lock` floor string, which
  `parse_version()`'s deliberate exactly-3-component anchoring would
  reject outright. No functional shell/PowerShell logic changed for
  this item, comments only.

## [2.10.17] - 2026-07-26

### Fixed

- **`utils/trakt_auth.py` had its own local `load_config()`** (a second,
  narrower `yaml.safe_load` of `config.yml` + `trakt.yml`) instead of the
  canonical `utils.config.load_config()` the rest of the app uses - so
  Trakt device-auth never got `tuning.yml`/`radarr.yml`/`sonarr.yml`
  module merging, legacy-config auto-migration, or the
  `PLEX_URL`/`PLEX_TOKEN`/`TMDB_API_KEY` env-var overrides everything
  else gets. Wrong for Docker/env-var installs and un-migrated legacy
  configs. Now delegates to the canonical loader; added a regression
  test covering the previously-broken env-var-override case.
- **`utils/scoring.py` used an absolute `from utils.config import (...)`
  import** while most other `utils/` submodules use relative imports.
  Normalized to `from .config import (...)`.
- **Redundant `RATING_MULTIPLIERS` backwards-compat alias** in
  `utils/config.py` (`RATING_MULTIPLIERS = DEFAULT_RATING_MULTIPLIERS`)
  with both names still live. Dropped the alias; `recommenders/external.py`
  and `utils/__init__.py`'s public API now use `DEFAULT_RATING_MULTIPLIERS`
  only.

### Changed

- **Moved `utils/trakt_sync.py` to the project root (`trakt_sync.py`).**
  It's a CLI orchestrator that imports from the domain layer
  (`recommenders.external.sync_watch_history_to_trakt`), not a shared
  utility - `utils/` reaching into `recommenders/` was an inverted
  dependency. Now sits alongside `curatarr_app.py`, the other root-level
  entry point. Updated `run.sh`/`run.ps1`'s invocations
  (`python3 utils/trakt_sync.py` -> `python3 trakt_sync.py`) and the
  test suite's patch targets accordingly.

## [2.10.16] - 2026-07-25

### Fixed

- **`scripts/selfupdate_e2e/build_fixtures.py`'s pinned-signing-key patcher
  silently stopped working after the 2.10.14 `ruff format` reformat,
  breaking `selfupdate-e2e.yml` on all three platforms.** That script
  temporarily rewrites `utils/self_update.py`'s
  `PINNED_SIGNING_PUBLIC_KEY_B64`/`PINNED_SIGNING_KEY_FINGERPRINT` to a
  throwaway test key before building the CI-only test binaries (see that
  script's module docstring), by matching the constants' declaration as
  literal source text. The 2.10.14 reformat changed that declaration
  from a multi-line parenthesized single-quoted form to a single-line
  double-quoted one; the old regex then matched zero times, so
  `patch_pinned_key()` silently never ran (the built test binaries still
  trusted the real, maintainer-only pinned key instead of the throwaway
  one), and every downstream fixture-signing/verification step failed
  with cascading, confusing errors instead. Last known-green run of that
  workflow: 2026-07-25T05:58Z, before the reformat merged.
  - Fixed by parsing the file with `ast` and locating the two
    assignments by their target NAME rather than by any particular
    source-text shape, then rewriting only that AST node's exact span -
    this survives quote-style, line-wrapping, and parenthesization
    changes without needing to be touched again. Still fails loudly
    (`SystemExit`) if it can't find exactly one matching assignment,
    rather than silently skipping the patch - that fail-loud design is
    what surfaced this bug in the first place, and is preserved
    unchanged.
  - The same fragile-regex pattern was also used for `utils/config.py`'s
    `__version__` bump/read - converted to the same AST-based approach
    for the same reason, even though it hadn't broken yet.
  - Audited `scripts/selfupdate_stub_e2e/` (the separate, fast local
    hand-off-script harness) for the same class of bug: it has none -
    it never parses or regex-matches any real production source file at
    all (its stub "binaries" are built from its own template file with
    plain placeholder substitution, and it calls
    `utils/self_update_handoff.py`'s real functions directly rather than
    text-patching them).
  - Added `tests/test_selfupdate_e2e_build_fixtures.py`, which feeds the
    new AST-based patcher the old multi-line form, the new single-line
    form, a third plausible reformatting, and this repo's own actual
    current `utils/self_update.py` verbatim - reverting to the old regex
    reproduces the original 0-match failure against the new/third forms,
    confirming the test would have caught it.

### Changed

- **`selfupdate-e2e.yml` now runs on pull requests and pushes into
  `main` that touch self-update code, instead of only on push to a
  long-stale feature branch (`feat/binary-self-update-2.8.29`) plus
  manual dispatch** - that stale-branch-only trigger is exactly why the
  2.10.14 reformat's breakage above could merge into `main` completely
  undetected: nothing in the real PR pipeline ever ran this workflow.
  Scoped to a path filter (`utils/self_update.py`,
  `utils/self_update_handoff.py`, `web/update_apply.py`,
  `scripts/selfupdate_e2e/**`) rather than every PR unconditionally,
  since this is a slow (35min timeout), 3-platform real-binary job; a
  weekly scheduled run is added alongside it as a safety net for drift a
  path filter can't foresee (dependency/Python/PyInstaller version
  bumps, or a whole-repo tool pass like the 2.10.14 reformat touching
  one of these paths as a side effect of something unrelated).
  Deliberately not added to branch protection's required checks - see
  the workflow file's own comment for why.

## [2.10.15] - 2026-07-25

### Fixed

- **Two pre-existing bugs surfaced by ruff during the 2.10.14 reformat, both
  confirmed unreachable/dormant in production today - fixed rather than
  left as judgment calls, since both are the same class of latent crash
  risk (`F821` undefined name).**
  - `recommenders/external.py`'s `discover_popular_by_genre()` referenced
    `TMDB_RATE_LIMIT_DELAY` without importing it. **Not reachable from any
    production code path** - the function is never called outside its
    own tests (`is_thin_profile()` is used by
    `find_similar_content_with_profile()`, but only to shrink
    `max_iterations`; it never calls `discover_popular_by_genre()`
    itself). It *is* exercised directly by
    `tests/test_external.py::TestDiscoverPopularByGenre`, though: every
    existing test there mocks `time.sleep` but still evaluates the
    undefined name as the call's argument, raising a `NameError` that
    the function's own broad `except Exception` swallowed and logged as
    a generic `"Genre discover failed for {genre}"` warning - so the
    tests kept passing (the recommendations were already collected
    before the sleep call) while silently never rate-limiting and
    spamming a misleading warning on every genre. Fixed by importing the
    existing `TMDB_RATE_LIMIT_DELAY` constant from `utils/config.py`
    (already used identically by `recommenders/base.py`) rather than
    defining a duplicate local constant. Added
    `test_applies_rate_limit_delay_between_genre_calls`, which asserts
    `time.sleep` is actually called with the real constant - reverting
    the import reproduces the original swallowed-`NameError` warning and
    fails the new assertion (`sleep` called 0 times), confirming the
    test would have caught it.
  - `utils/radarr.py`'s `create_radarr_client()` had an unreachable
    `return RadarrClient(url, api_key)` after its real
    `return create_radarr_client_from(...)` - `url`/`api_key` aren't
    even local names in that scope, so it could never have run; it's
    vestigial code left over from before the `#157` Phase 2 per-library
    refactor extracted `create_radarr_client_from()`. The sibling
    `utils/sonarr.py::create_sonarr_client()` has no such trailing line,
    confirming the earlier `create_radarr_client_from(...)` return is
    the intended (and only correct) behavior. Deleted the dead line; no
    new test added; there's no way a runtime test can execute
    genuinely-unreachable code after an unconditional `return`, and
    ruff's `F821` is exactly the mechanism that already caught it.
  - Audited every other `F821` finding in the codebase (`ruff check
    --select F821`) - these were the only 3 in the entire repo (the
    `TMDB_RATE_LIMIT_DELAY` one above, plus the `url`/`api_key` pair on
    the same dead line above). None left unaddressed.
  - `ruff check` now finds 125 violations (down from 128), still
    non-blocking - the rest remain deliberate judgment calls per the
    2.10.14 entry above. Full suite: 2334 passed, 1 skipped (2333 from
    2.10.14 + the new regression test).

## [2.10.14] - 2026-07-25

### Changed

- **Reformatted the entire codebase with `ruff format` and applied
  `ruff check --fix`'s safe auto-fixes** (added in 2.10.13) - mechanical
  only, no hand-edited logic. 105 files reformatted (quote-style
  normalization, slice/whitespace spacing, multi-line call/import
  reflow to the configured 120-column width); `ruff check --fix` then
  cleared every safely-fixable violation it found (unsorted imports,
  most unused imports, useless f-string prefixes, one
  redefined-while-unused, one `not ... is` rewrite).
  - **Six of the "unused" imports `--fix` removed were actually
    cross-module re-exports** - a name imported into a module without
    being referenced inside that module itself, but relied on
    elsewhere (`recommenders/external.py`'s `SERVICE_DISPLAY_NAMES`,
    `get_tmdb_id_from_imdb`, and `sync_watch_history_to_trakt`;
    `web/config_test_connection.py`'s `RadarrAPIError`,
    `SonarrAPIError`, and `TautulliAPIError`, used by tests to
    construct fake client errors). Removing them broke real imports/
    tests, caught by running the full suite - reverted all six by
    hand; they remain as unresolved `F401` findings (ruff has no way to
    know a name is part of a module's public surface without an
    `__all__` it doesn't have) rather than silently "fixed" again.
  - Two guardrail tests (`test_web_docker_server.py`,
    `test_web_routes.py`) asserted on the literal single-quoted source
    text of a binding call - updated to match the reformatted
    double-quoted source; the guardrail itself (never binding
    `0.0.0.0` outside `docker_server.py`) is unchanged.
  - **`utils/self_update.py` got extra scrutiny** (it performs the
    self-update itself, and has shipped broken three times before):
    diffed in isolation after the reformat - every change is
    quote-style normalization, slice/whitespace spacing, or line
    reflow; confirmed zero remaining `ruff check` findings for the
    file and all of `tests/test_self_update.py`/
    `test_self_update_handoff.py` still passing.
  - Full suite: 2333 passed, 1 skipped (unchanged from 2.10.13).
  - `ruff check` still finds 128 violations post-fix (the six reverted
    re-exports above plus 122 that need a human judgment call - long
    lines inside strings/comments the formatter can't wrap, unused
    variables, ambiguous single-letter names, bare-`raise`-without-
    `from`, etc.) - left as-is, not hand-fixed, per this pass's
    mechanical-only scope. `mypy` is untouched (still 255 pre-existing
    errors - this codebase is only partially annotated).
  - CI's `lint` job (2.10.13): `ruff format --check` is now blocking
    (main is clean) - `ruff check` and `mypy` stay non-blocking for the
    reasons above.

## [2.10.13] - 2026-07-25

### Added

- **Linter/formatter/type-checker configuration** - this repo had none
  before (no ruff/flake8/black/mypy config anywhere, no lint step in
  any of the 6 GitHub Actions workflows). Config and CI only in this
  release; no code was reformatted (see 2.10.14 for that).
  - `ruff.toml`: `line-length = 120` (measured off this codebase's own
    per-line-length distribution - 99.7% of lines are already under
    it), `target-version = "py310"` (matches requirements.lock's
    pinned floor), and lint rules E/W (pycodestyle)/F (pyflakes)/I
    (isort)/B (bugbear). `utils/__init__.py` gets a per-file `F401`
    ignore - it's a barrel module that deliberately re-exports its
    submodules' public names, so every import in it is "unused" by
    F401's definition without actually being dead code.
  - `mypy.ini`: non-strict, `ignore_missing_imports = True` - this
    codebase is only partially annotated (`utils/self_update.py` ~94%,
    `web/app.py` ~14%), so this is a reporting baseline, not an
    immediate gate.
  - Measured before deciding what to enable: `ruff check` -> 477
    violations (179 `E501`, 101 `I001`, 71 `F401`, 28 `W293`, 22
    `F541`, 16 `F841`, 11 `E402`, 9 `B904`, 9 `E731`, 8 `B007`, 8
    `E741`, 7 `F811`, 3 `F821`, 2 `W292`, 1 `B017`, 1 `E714`, 1
    `W291`); `ruff format --diff` -> 105 files would be reformatted, 9
    already formatted; `mypy` -> 255 errors across 36 files. None of
    these were large enough, on their own, to justify disabling a rule
    outright.
  - A new `lint` job in `.github/workflows/tests.yml` runs `ruff
    check`, `ruff format --check`, and `mypy`, each with
    `continue-on-error: true` - it reports on every PR without
    wedging merges on a first pass across a never-linted codebase. The
    existing `test`/`secret-scan` required checks are unchanged.

## [2.10.12] - 2026-07-25

### Fixed

- **`run.ps1` could abort mid-run on PowerShell 7.x where a plain PS 5.1
  run would just warn and continue.** Every native-command call in the
  script (`git`, `python`, ...) is followed by a `$LASTEXITCODE` check
  that expects to handle a non-zero exit itself, matching `run.sh`'s
  `|| echo ...` fallbacks - but PowerShell 7's
  `$PSNativeCommandUseErrorActionPreference` (default has varied across
  7.x) can turn that same non-zero exit into a *terminating* error under
  this script's `$ErrorActionPreference = "Stop"`, aborting the whole
  run before the check ever gets a chance to run. Found 17 call sites
  with this exposure (the Trakt-sync step added in 2.10.11 plus 16
  others - dependency install, update-check/apply, and the
  recommendation steps). Fixed once, consistently, by forcing
  `$PSNativeCommandUseErrorActionPreference = $false` at script scope,
  restoring identical `$LASTEXITCODE`-based handling on both Windows
  PowerShell 5.1 (which has no such variable at all - setting it there
  is harmless) and PowerShell 7.x.
- **`curatarr --help` / `-h` launched the full web UI instead of
  printing usage.** `curatarr_app.py`'s argv dispatch had no case for
  `--help`/`-h`, so both fell through to the same `else` branch as a
  normal no-flag launch. Added real usage output (`--help`, `--version`,
  `--self-update`, `--run-recommender <movie|tv|external|full> [user]`,
  `--debug`) that exits 0 without touching Flask, and a regression test
  proving it works even when Flask isn't installed (CLI/cron-only
  installs).

### Changed

- Consolidated the `CHANGELOG.md` entries for `2.10.9` and `2.10.10`
  into `2.10.11` - neither was ever tagged or released (`2.10.8` shipped,
  then `2.10.11` shipped next), so a user reading the changelog could
  believe they could install versions that never existed as releases.

## [2.10.11] - 2026-07-25

_`2.10.9` and `2.10.10` were never tagged or released - their changes
are folded into this entry rather than listed as separate installable
versions._

### Fixed

- **Horizon Huntarr (`find_horizon_movies`) no longer silently skips movies added to Plex after Sequel Huntarr's last run.** It reused Sequel Huntarr's cached movie-to-collection map but trusted it wholesale instead of diffing against the current library the way Sequel Huntarr's own `find_missing_sequels` already does. A movie added to the library after Sequel Huntarr's last scan had no entry in that cache, so Horizon Huntarr never looked up its collection and never checked it for upcoming/unreleased sequels - with zero network calls even attempted, and no error or warning. It stayed invisible to Horizon recommendations until Sequel Huntarr happened to run again and refresh the shared cache. Fixed by having Horizon Huntarr diff its current library against the cached map the same way Sequel Huntarr does, fetching collection data only for the still-uncached (newly-owned) movies.
- **Windows users' Trakt watch-history sync was silently never running.**
  `run.sh` syncs Plex watch history to Trakt before generating
  recommendations when `config/trakt.yml` has `auto_sync: true` - the
  setup wizard asks Windows users this same question and saves their
  answer via `run.ps1`, but `run.ps1` had no equivalent step at all, so
  the saved answer was never honored. Added the matching step to
  `run.ps1`'s `Main` function in the same position run.sh runs it
  (before recommendations, so both internal and external recommenders
  benefit).
- **An append-only log could grow forever and was structurally
  unremovable by its own cleanup.** `run.sh`'s optional cron job
  redirected output with `>> logs/daily-run.log 2>&1` - a single file
  every run appends to, with no cap. `cleanup_old_logs()` only removes
  `.log` files by mtime, but an append-only file's mtime is refreshed
  on every write, so it could never cross the retention threshold.
  `run.sh`'s generated cron command now writes each run to its own
  timestamped log file instead, and `cleanup_old_logs()` also
  force-truncates (in place, so an already-appending process just
  keeps writing from the new end-of-file) any `.log` file over a new
  `MAX_LOG_FILE_BYTES` cap regardless of mtime, as a safety net for
  any other append-only logging setup (docker-compose cron, an
  external scheduler, etc.).
- **Cache writes could be corrupted by a mid-write crash or a
  concurrent writer.** `utils/cache.py`'s save functions truncated and
  wrote the target file directly - a process dying mid-write, or two
  processes sharing the same `./cache` volume (docker-compose runs a
  `curatarr` and a `curatarr-recommend` service), could leave a
  truncated/corrupt file that then silently forces a full re-scan.
  Switched to write-temp-then-`os.replace()`, matching the pattern
  already used by `web/config_io.py` and `utils/metrics.py`.
- Corrected `README.md`'s documented default for
  `min_relevance_score` (was `0.25`, actual shipped default is `0.65`
  - matches `config/tuning.example.yml` and every code default).
- Rewrote the README's Contributing section to describe the actual
  policy - external PRs are closed automatically
  (`.github/workflows/auto-close-prs.yml`), it previously invited PRs
  against `main`.
- `utils/plex.py` and `utils/tmdb.py` now use the `PLEX_REQUEST_TIMEOUT`
  / `TMDB_REQUEST_TIMEOUT` constants (already defined in
  `utils/config.py` but never wired up) instead of hardcoded literal
  timeouts; the one deliberately-longer Plex call (large watch-history
  page fetch) got its own named `PLEX_LONG_REQUEST_TIMEOUT` constant
  instead of an unexplained `timeout=60`. No effective timeout values
  changed.
- Added debug-level logging to previously-silent `except: pass` blocks
  in `web/job_runner.py` and `utils/self_update.py` so a real failure
  in these paths leaves a trace instead of vanishing - `self_update.py`
  is integrity-sensitive, so a swallowed `OSError` there was exactly
  the kind of bug that could hide silently. No control flow changed;
  the exceptions are still swallowed.

### Changed

- **Added direct test coverage for Sequel Huntarr (`find_missing_sequels`) and Horizon Huntarr (`find_horizon_movies`) in `recommenders/external.py`** - both functions were previously only ever referenced via `@patch('recommenders.external.find_missing_sequels'/'find_horizon_movies')` in `tests/test_external.py`, so their real gap-finding, caching, and TV-special-reconciliation logic never actually ran under CI. Added 41 new tests that mock only the TMDB HTTP boundary (`requests.get`) and the Plex library/guid scan, so the real branching logic executes: library-access failure, empty library, cache hit vs. miss, missing/failed collection lookups, fully-owned and no-released-movies skip paths, unreleased-date heuristics, live `Canceled`/`Released` status overrides, sort order, cache-save shape, and Sequel Huntarr's TV-special reconciliation (TMDB-guid, normalized-title, grandparent-title-combo, and title-suffix matching, plus not-found/search-failure/section-failure paths). `recommenders/external.py` coverage: 55% -> 73% (both target functions individually now fully covered bar one apparently-unreachable defensive line). Surfaced but deliberately left unfixed (out of scope for this pass, flagged for follow-up): when Sequel Huntarr's shared cache exists but is partial (a movie was added to the library after Sequel Huntarr's last run), `find_horizon_movies`'s cache-reuse branch trusts `movie_collections` wholesale instead of diffing against it the way `find_missing_sequels` does, so a legitimately-owned movie's collection is silently never (re)checked for upcoming releases until Sequel Huntarr happens to run again.

### Removed

- Deleted dead function `balance_genres_proportionally` in
  `recommenders/external.py` (verified zero callers repo-wide).

### Chore

- Added `.venv/` to `.gitignore` (was untracked but not ignored).

## [2.10.8] - 2026-07-25

### Added

- **Local-first observability**: structured logging, a Prometheus
  `/metrics` endpoint, and a richer authenticated status endpoint - all
  self-hosted, nothing shipped to a third-party service.
  - **Structured logging**: opt-in JSON-lines log format alongside the
    existing human-readable one, via the new `logging.format: json`
    config key (default stays `text` - existing installs are
    unaffected). Wired through `utils.display.setup_logging`, the same
    place `logging.level` already plugs in. Every record is redacted
    through the same `utils/redact.py` path every other log destination
    in this codebase uses, so a token-shaped value is masked in JSON
    output exactly as it already is in the human-readable one.
  - **`/metrics`**: Prometheus text-format metrics on the web UI -
    recommender run count/duration by engine and outcome, outbound API
    request count/latency/error count by service (Plex, Radarr, Sonarr,
    TMDB, Trakt, Simkl, MDBList, Tautulli), local cache hit/miss,
    self-update attempts/failures, unhandled error count, and
    `curatarr_build_info`. Rendered directly (no new runtime
    dependency - see `utils/metrics.py`) from a small local JSON state
    file, so scraping never makes a network call or triggers a Plex/
    TMDB request. Behind the exact same token gate as every other route
    once the server is bound non-loopback (Docker) - it surfaces
    library/integration topology, which isn't public data any more than
    the config screens are. `/login` and `/healthz` remain the only
    unauthenticated routes.
  - **`/status.json`**: authenticated readiness detail (last run time/
    outcome, whether config.yml currently loads, whether a run is in
    progress) that doesn't belong on the unauthenticated `/healthz`,
    which stays exactly as boring as before (liveness + version only -
    no library/user/integration detail).

## [2.10.7] - 2026-07-25

### Added

- **Automated post-release smoke test**
  (`.github/workflows/post-release-smoke-test.yml`) that exercises a
  published release's real artifacts the way a real user would, instead
  of re-checking them the way `release.yml` already does at build time.
  This is the check that would have caught the self-update outage
  across v2.9.2/v2.10.0/v2.10.2 (see the `[2.10.4]` entry below): it
  verifies `SHA256SUMS.txt.sig` using the shipping client's own
  verification code (`utils.self_update.verify_downloaded_asset`,
  loaded from the released tag, not `main`), asserts no CRLF and a
  correct `# curatarr-version:` binding, recomputes every published
  hash, confirms the exact expected asset set (and that the retired
  `curatarr-macos-universal` asset stays gone), re-checks both Linux
  binaries' glibc floor against the actual published bytes, boots both
  Linux binaries on `debian:12`/`ubuntu:22.04`/`rockylinux:9`/
  `ubuntu:24.04` (x86_64 and arm64, natively), downloads the PREVIOUS
  release's real binary and runs its real `--self-update` against this
  release's real published bytes to confirm it lands on the new
  version, and `cosign verify`s the published container image.
  `scripts/sign-release-checksums.sh` now dispatches it automatically
  right after uploading `SHA256SUMS.txt.sig` (the first moment the
  signature this all depends on actually exists); it's also runnable by
  hand (`gh workflow run post-release-smoke-test.yml -f
  version=X.Y.Z`) against any past release for backfill/re-verification.
  Any failure opens or updates a GitHub issue carrying the real failing
  step's output. See `RELEASING.md`'s "Post-release smoke test" section.

## [2.10.6] - 2026-07-25

### Fixed

- **The Windows self-updater's `.old` sidecar cleanup could permanently
  brick self-update on Windows if a previous swap's leftover `.old`
  file couldn't be deleted right away** (e.g. still locked by a
  slow-to-exit previous process - exactly the scenario the code's own
  cleanup already anticipated as non-fatal). `utils/self_update.py`'s
  `_swap_windows` used `os.rename()` for its current-binary ->
  `.old` step; Windows' `os.rename()` refuses to overwrite an existing
  destination (`WinError 183`), so once that best-effort pre-cleanup
  failed once, every subsequent self-update attempt would raise
  `SwapError` at that same rename, forever, until a human manually
  deleted the stale sidecar - Linux CI never caught this because
  POSIX's `os.rename()` silently overwrites. Switched to `os.replace()`
  (the cross-platform atomic form, already used for the actual binary
  swap two lines below), so a stubborn leftover `.old` no longer blocks
  future updates.

### Testing

- Triaged all 28 Windows-only test failures on a real Windows dev
  machine (stable, reproducible, none of them occur on Linux CI).
  5 turned out to be self-inflicted pollution from two OTHER tests
  (`test_update_check.py`/`test_update_dismissal.py`) that pointed
  `get_project_root()` at a hardcoded `/nonexistent/...` path assuming
  it could never be created - true on POSIX (an ordinary user can't
  `mkdir` under `/`) but false on Windows (an ordinary user CAN `mkdir`
  directly under a drive root), so the directory got created for real
  and leaked into later test runs; fixed by pointing at a path that's
  genuinely uncreatable on any OS (a plain file sitting where a needed
  parent directory would go) instead. Of the rest: one was the real
  production bug above; the remainder were test-only platform
  assumptions (NTFS chmod not preserving POSIX permission/exec bits,
  `tempfile.NamedTemporaryFile` handles held open across a call that
  needs to read/delete that same path - fine on POSIX, `WinError 32` on
  Windows, hardcoded `/`-joined path assertions, `os.path.expanduser()`
  preferring `%USERPROFILE%` over `%HOME%` on Windows, and a couple of
  tests that mocked `os.kill`/`subprocess.run` without also forcing
  `os.name` to the branch they were meant to exercise - on a real
  Windows run they silently fell through to the OS's OWN process-table
  query/`taskkill` instead of the mock, which is both the wrong branch
  under test and, for the `_shut_down_old_server` case, a real
  `taskkill` launched against whatever process actually happened to
  own an arbitrary test PID). All fixed at the test level except the
  two genuinely POSIX-only code paths (`_swap_posix` direct-call tests,
  never invoked on Windows in production), which are now
  `skipif(sys.platform == 'win32', ...)` with the specific reason named
  in each. No assertions weakened; no tests skipped to hide a real
  failure. Full suite: 2199 passed / 28 failed / 5 skipped before,
  2224 passed / 0 failed / 8 skipped after, on the same Windows machine;
  unchanged and still green on Linux CI.

### Documentation

- `docs/BINARIES.md`: documented that the macOS binary is unsigned and
  unnotarized (`spctl -a -v` reports `rejected`) and, specifically,
  that a quarantined binary run in a headless context (SSH, CI, no GUI
  session) hangs indefinitely instead of failing fast, since Gatekeeper
  has no dialog to show and nothing to wait for - clear the quarantine
  attribute (`xattr -d com.apple.quarantine curatarr-macos-arm64`)
  before running it that way.

## [2.10.5] - 2026-07-25

### Fixed

- **The published `curatarr-linux-x86_64` and `curatarr-linux-arm64`
  binaries required a newer glibc than most server distros ship,
  failing to even start.** Both Linux entries in
  `.github/workflows/release.yml`'s `build-binaries` matrix built on a
  runner that resolves to Ubuntu 24.04 (glibc 2.39); glibc is backward-
  but not forward-compatible, so the resulting binary failed on Debian
  12 (glibc 2.36), Ubuntu 22.04 LTS (2.35), and RHEL/Rocky/AlmaLinux 9
  (2.34) with `Failed to load Python shared library ... GLIBC_2.38' not
  found` - a raw loader failure, not an actionable error. Reproduced on
  a real clean Debian 12 install; confirmed Ubuntu 24.04 itself was
  unaffected. Both Linux entries now build inside a pinned
  `manylinux_2_28` container (glibc 2.28) instead of directly on the
  runner - chosen because every compiled dependency
  (`cryptography`, `cffi`, `markupsafe`, `pyyaml`, `charset-normalizer`)
  already publishes a `manylinux_2_28`- or lower-tagged wheel for both
  x86_64 and aarch64, so this floor needed compiling nothing from
  source (see `build-binaries`' own comment for the full survey). This
  covers all three baselines above with margin. A new build-time check
  (`objdump -T` against the built binary's max referenced `GLIBC_*`
  symbol) fails the job if a future dependency bump silently regresses
  the floor. Verified with real container runs of the rebuilt binaries
  on `debian:12`, `ubuntu:22.04`, `rockylinux:9`, and `ubuntu:24.04`
  (`--version` on each). See `docs/BINARIES.md` for the documented
  supported floor.

## [2.10.4] - 2026-07-25

### Fixed

- **`curatarr_app.py` imported the web UI (`from web.app import main`) at
  module level, unconditionally, before any argv dispatch.** A
  CLI/cron-only source install - `pip install --require-hashes -r
  requirements.lock`, which `requirements.txt`'s own header states is
  sufficient, with UI-only deps deliberately split into
  `requirements-ui.txt`/`.lock` so "a plain CLI/cron update never pulls
  in the heavier UI stack" - had no `flask` installed, so **every**
  invocation (`--version`, `--run-recommender`, everything) died with
  `ModuleNotFoundError: No module named 'flask'` before reaching any
  dispatch logic. Reproduced with a fresh venv + hash-verified
  `requirements.lock`-only install.
  The import is now deferred to `_launch_web_ui()`, reached only by the
  actual web-UI launch path, and a CLI-only invocation that does hit it
  (running the exe with no flags and no `requirements-ui.txt` installed)
  now gets one clear, actionable message pointing at
  `requirements-ui.txt` instead of a raw traceback. Still a plain
  function-scoped `from web.app import main` (not `importlib`/
  `__import__`), so PyInstaller's static import analysis still bundles
  it into the standalone binary - confirmed via a real local build per
  `docs/BINARIES.md`.

- **In-binary self-update was completely broken since at least v2.9.2 -
  every self-update attempt failed with `SSH signature does not verify
  against the pinned release-signing key`, even though the release
  itself was correctly signed.** Root cause:
  `utils/self_update.py::verify_downloaded_asset()` read
  `SHA256SUMS.txt` in **text mode**, which silently strips any `\r` via
  Python's universal-newline translation; the published file's Windows
  binary checksum line contained a CRLF line ending (the "Rename binary
  and compute SHA256" step in `.github/workflows/release.yml` wrote it
  with a plain text-mode `open(..., 'w')`, which turns `\n` into
  `os.linesep` - `\r\n` - on the `windows-latest` runner), so the bytes
  actually hashed for verification differed from the bytes
  `scripts/sign-release-checksums.sh` had signed. Confirmed by
  downloading the real published `SHA256SUMS.txt` for v2.9.2, v2.10.0,
  and v2.10.2: all three contain a CRLF line ending on exactly that
  line. Fixed on both ends:
  - **Client**: `verify_downloaded_asset()` now reads `SHA256SUMS.txt`
    and `SHA256SUMS.txt.sig` in binary mode and verifies the signature
    against the exact bytes on disk - never a decoded/re-encoded
    representation of them. This fixes every already-installed binary
    the next time it attempts a self-update.
  - **Release workflow**: the Windows `.sha256` sidecar is now written
    with `newline='\n'` (no more CRLF at the source), and
    `finalize-checksums` additionally strips any stray `\r` when
    aggregating and fails the build if the published `SHA256SUMS.txt`
    isn't LF-only - defense in depth, not just a fix at the one
    location this was traced to.
  Existing installs affected by this could not have self-updated to any
  version before this fix landed; once v2.10.4 is published (and its own
  `SHA256SUMS.txt` is confirmed LF-only), self-update resumes working
  for them.

Note: `2.10.1` and `2.10.3` (both above) landed on `main` but were never
cut as their own published release/tag - their changes ship for the
first time as part of this `2.10.4` release.

## [2.10.3] - 2026-07-25

### Fixed

- **`recommenders/base.py` computed its cache directory relative to its
  own `__file__` instead of going through `get_project_root()`**, the
  resolver every other cache/config/log path in the app already uses
  (`utils/cli.py`, `recommenders/external.py`, `utils/update_check.py`,
  `utils/update_dismissal.py`, `web/app.py`). Two consequences:
  - **Docker**: this resolved to `/app/cache`, which `docker-compose.yml`
    never mounts (it mounts `./cache:/data/cache`, matching the
    documented `CURATARR_CONFIG_DIR=/data` layout) - so the movie/TV
    library cache, per-user watched-history cache, and the Trakt/TMDB
    lookup caches were silently lost on every container recreate.
    Existing Docker installs will see one slower run while these
    caches rebuild in the correct, now-persistent location.
  - **Frozen (PyInstaller) binary**: this resolved inside `sys._MEIPASS`,
    a temp directory deleted on exit, so these same caches never
    persisted across runs at all for binary installs. They now persist
    in the same per-user data directory (`%APPDATA%\curatarr` /
    `~/.curatarr`) already used for config/logs.
  Plain source installs without `CURATARR_CONFIG_DIR` set are unaffected
  - old and new paths were already identical for that case. A source
    install run with `CURATARR_CONFIG_DIR` set will have its existing
    cache files moved automatically (best-effort, logged, never blocks
    a run) from the old repo-relative `cache/` to the correct location.

## [2.10.2] - 2026-07-25

### Removed

- **Transitional `curatarr-macos-universal` compat asset removed.**
  v2.10.0 dropped Intel macOS support and renamed the macOS asset to
  `curatarr-macos-arm64`, but published both names (identical bytes) for
  one release so installs still on 2.9.2 or earlier - whose self-updater
  requests the old name - could self-update at least once more instead
  of 404ing. That transitional period is now over: `release.yml` no
  longer builds or publishes `curatarr-macos-universal` or its `.sha256`
  sidecar, and only `curatarr-macos-arm64` is published for macOS going
  forward. **Installs still on 2.9.2 or earlier on macOS can no longer
  self-update** - they must download `curatarr-macos-arm64` manually
  from the [releases page](https://github.com/OrchestratedChaos/curatarr/releases)
  (see `docs/BINARIES.md`) and replace their binary by hand. Installs
  already on 2.10.0+ are unaffected, since `select_asset_name()` has
  only ever requested the canonical `curatarr-macos-arm64` name.

## [2.10.1] - 2026-07-25

### Fixed

- **Test suite leaked real network connections.** `tests/test_movie.py`
  and `tests/test_tv.py` construct `PlexMovieRecommender`/
  `PlexTVRecommender` in most of their tests; construction eagerly
  gathers watched-history data, and several of the utility calls in
  that path (`get_watched_movie_count`/`get_watched_show_count`,
  `get_plex_account_ids`, `fetch_show_completion_data`) weren't mocked
  in most tests - so the suite was making real HTTPS calls to `plex.tv`
  and real HTTP calls to whatever `plex.url` happened to be in the
  test's fake config (usually `http://localhost`). Traced by
  instrumenting `socket.socket.connect`. Beyond the token/network
  leakage itself, an unreachable/slow real connect turns into a hang -
  observed once as a 24-minute CI run that an identical re-run of the
  same commit completed in 1m35s. Both files now default those calls to
  their "nothing reachable" return values via a file-scoped autouse
  fixture; tests that care about the specific behavior continue to
  patch it themselves, same as before. The same unmocked-real-call
  pattern (relying on a real request failing a particular way rather
  than mocking it) was also found and fixed in three
  `tests/test_trakt.py` "not authenticated" tests and three
  `tests/test_external.py` tests reaching TMDB/Trakt for real.
- **Added a suite-wide regression guard** (`tests/conftest.py`) that
  blocks any `socket.connect()` to a non-loopback address during the
  test run and raises immediately with the offending host in the
  message, so a future accidental network call fails loudly and
  instantly instead of silently leaking or hanging. Loopback
  (`127.0.0.1`/`::1`/`localhost`) is still permitted, for the couple of
  tests that legitimately bind and poll a real local server socket.

## [2.10.0] - 2026-07-25

This release also carries the `scripts/release.sh` /
`scripts/sign-release-checksums.sh` fixes recorded below under
`[2.9.3]` - that version was never cut as its own tagged release (the
last published release before this one is v2.9.2), so those fixes ship
here for the first time alongside everything below.

### Removed

- **macOS Intel support removed.** `cryptography` 49.0.0 dropped
  x86_64 macOS wheels entirely (deprecated in 46.0.0/47.0.0, removed
  per its own CHANGELOG: "Support for x86_64 macOS has been removed"),
  and GitHub's `macos-13` runner (the last Intel macOS CI runner) is
  retired - only `macos-15-intel` remains, itself sunsetting Aug 2027.
  With no supported way to build or test an Intel macOS binary going
  forward, and lifetime downloads of the old `curatarr-macos-universal`
  asset across all prior releases in the low single digits, macOS
  binaries are now **Apple Silicon (arm64) only**. Intel Mac users
  should run Curatarr from source instead (no architecture restriction
  there) - see the README's Quick Start / `docs/BINARIES.md`.
  - `.github/workflows/release.yml`'s separate `build-macos-universal`
    job (python.org's universal2 interpreter pin, `delocate-merge`
    wheel-fusing for `pyyaml`/`ruamel.yaml.clib`/`markupsafe`/`cffi`) is
    gone; macOS is now a normal `macos-latest` entry in the
    `build-binaries` matrix, same `actions/setup-python` build as every
    other platform. Its `lipo -archs` sanity check is inverted from the
    old universal2-era check: it now asserts the built binary is
    **arm64-only and not fat**, so a regression back to a universal
    build is caught in CI rather than shipped.
  - **Transitional asset naming (this release only):** the canonical
    macOS asset is now `curatarr-macos-arm64`
    (`utils.self_update.select_asset_name()` returns this on macOS),
    but this release *also* publishes an identical-bytes
    `curatarr-macos-universal` duplicate (own `.sha256` sidecar, both
    listed in `SHA256SUMS.txt`) purely so installs still running a
    pre-2.10.0 binary - whose self-updater still requests the old name
    - can self-update at least once more instead of 404ing and failing
    closed. Drop the duplicate in a future release once no longer
    needed.

### Dependencies

- `cryptography` 48.0.1 → 49.0.0. Previously held back specifically
  because 49.0.0 dropped the x86_64 macOS wheel the old universal2
  binary build needed (see above) - no longer a constraint now that
  macOS builds are arm64-only. No known CVE was unpatched between the
  two versions either way (checked pyca/cryptography's GHSA advisories
  directly). `pip-audit` against all four regenerated locks
  (`requirements.lock`, `requirements-ui.lock`,
  `requirements-docker.lock`, `build-requirements.lock`) reports zero
  known vulnerabilities.

## [2.9.3] - 2026-07-25

Fixes `scripts/release.sh` and `scripts/sign-release-checksums.sh` so the
documented release path matches how releases actually get cut on this
project's machines, instead of assuming a single host with both `gh`
authenticated and the signing key present.

### Fixed

- `scripts/release.sh`'s version precondition was inverted from reality:
  it aborted whenever `__version__` already matched the target version,
  but the version bump lands via its own PR (merged to `main` like any
  other change) *before* tagging, since nothing can push to `main`
  directly. The precondition now requires `__version__` to already equal
  the target version and that `CHANGELOG.md` has a matching entry,
  failing with a clear message naming the missing bump PR otherwise. The
  script no longer bumps `__version__` or opens/merges a PR itself.
- Neither script assumed the machine holding the release-signing
  **private** key might not have `gh` authenticated on it (or installed
  at all). `scripts/sign-release-checksums.sh` now detects this and
  delegates `gh release view/download/upload` over SSH to
  `CURATARR_GH_SSH_HOST`, transferring only the public
  `SHA256SUMS.txt`/`SHA256SUMS.txt.sig` - the private key is read only
  locally and never leaves the signing machine. `scripts/release.sh` no
  longer depends on `gh` at all (tagging and pushing are plain git).
- Neither script accounted for a two-hop remote topology (a checkout
  whose own `origin` is another machine, which is the one actually
  connected to GitHub): a tag pushed from such a checkout reached only
  the intermediate host, so `.github/workflows/release.yml` silently
  never fired. `scripts/release.sh` now detects whether `origin` points
  at GitHub, pushes onward from `CURATARR_GH_SSH_HOST`/
  `CURATARR_GH_SSH_REPO_DIR` when it doesn't, and confirms via a direct
  `git ls-remote` against `github.com/OrchestratedChaos/curatarr` that
  the ref actually landed before proceeding - failing loudly instead of
  silently if it never does.
- Added `--dry-run` to both scripts: runs every precondition (including
  the `gh`-delegation detection) and prints the exact commands a real
  run would execute, without tagging, pushing, signing, or uploading
  anything.

## [2.9.2] - 2026-07-25

Suppresses stray Windows console windows from background helper
subprocesses on a windowed (console=False) build.

### Fixed

- Five subprocess spawns that were missing `CREATE_NO_WINDOW` on
  Windows could each flash a console window: `web/job_runner.py`'s
  stale-lock `tasklist` check, and `web/update_apply.py`'s
  `-CheckVerifiedUpdate`/`-ApplyVerifiedUpdate` PowerShell
  invocations, `tasklist` polling loop, and `taskkill`. All five
  already pipe/capture their child's output, so hiding the window
  loses nothing. Added a shared `utils.helpers.no_window_kwargs()`
  helper (returns `{}` on non-Windows) rather than repeating the
  `getattr(subprocess, 'CREATE_NO_WINDOW', 0)` guard at each site.
- The daily 3 AM scheduled task (`run.ps1`'s `Setup-ScheduledTask`)
  now launches with `-WindowStyle Hidden`, so it no longer pops a
  console window when the user is logged in.

## [2.9.1] - 2026-07-24

Maintenance release: dependency bump and lock file refresh. No feature
or behavior changes.

### Dependencies

- `ruamel.yaml` 0.18.6 → 0.19.1 (`requirements-ui.txt`). Verified
  round-trip YAML behavior (comment/key-order preservation used by
  `web/config_io.py`) is unaffected - all `test_web_config_*` tests
  pass and a direct load/dump of `config/config.example.yml` preserves
  all 65 comment lines and key order exactly.
- All other direct dependencies (`plexapi`, `requests`, `pyyaml`,
  `flask`, `waitress`, `pyinstaller`, `pyinstaller-hooks-contrib`) were
  already at their latest stable release; no change needed.
- `cryptography` held at 48.0.1 (latest release is 49.0.0) - 49.0.0
  drops x86_64 macOS wheels entirely, which breaks the macOS universal2
  binary build (see the pin's rationale comment in `requirements.txt`
  and PR #190). Not bumped.
- Regenerated `requirements.lock`, `requirements-ui.lock`,
  `requirements-docker.lock`, and `build-requirements.lock` via `uv pip
  compile --universal --generate-hashes` so all hashes match the
  updated resolution. `pip-audit` against every lock reports zero known
  vulnerabilities.

## [2.9.0] - 2026-07-24

Security-hardening release covering a full audit of the web UI, CI/
release supply chain, and the binary self-updater. No feature or config
schema changes; the version bump is minor (not patch) because of the
Docker authentication requirement below, which changes default runtime
behavior for existing Docker users.

### ⚠️ Upgrade note for Docker users

The container now **requires** either `CURATARR_AUTH_TOKEN` (a strong
random value, `openssl rand -hex 32`) or an explicit
`CURATARR_TRUSTED_NETWORK=true` opt-out to start at all - see
`docs/DOCKER.md`'s new **Authentication** section. Without one of these
set, the container will refuse to start and print exactly which env var
to set. This applies even behind the new loopback-only default port
publish in `docker-compose.yml` (`127.0.0.1:8787:8787`), because the
container's process always binds `0.0.0.0` *inside* its own network
namespace regardless of how the host-side port is published. Native
(non-Docker) installs are unaffected - `web/app.py`'s own server is
still hardcoded to `127.0.0.1` only and never requires a token.

### Security

- **[CRITICAL] Real authentication for any non-loopback bind.** An
  audit proved live (via `curl`) that the existing Host/Origin guard
  (`web/security.py`) is not authentication - it only stops a
  *browser*, since a browser is what actually enforces same-origin
  policy on the Host/Origin headers in the first place. A non-browser
  client setting both to `localhost` sailed straight through it: config
  writes persisted, `/run` launched a real recommender job, and
  `/config/connections` disclosed saved values, all with zero
  credentials. `web/security.py`'s new `register_token_auth` requires a
  shared secret (`CURATARR_AUTH_TOKEN`, `Authorization: Bearer`/
  `X-Curatarr-Token`/cookie, `hmac.compare_digest`) on every request
  once the server is bound anywhere other than loopback - in addition
  to the existing guard, never instead of it. `web/docker_server.py`
  fails closed at startup rather than ever booting unauthenticated by
  accident; the one exception is an explicit, non-default
  `CURATARR_TRUSTED_NETWORK=true` opt-out for an operator who has
  decided the published port is genuinely unreachable by anything
  untrusted (prints a prominent warning on every boot). A minimal
  `GET`/`POST /login` sets the browser-session cookie. Native installs
  (`web/app.py`'s own `main()`, always `127.0.0.1`) are byte-for-byte
  unchanged - no token required.
- **[HIGH] Python code injection in `setup.sh`/`run.ps1`.** Interactive
  setup interpolated Plex/Trakt/Simkl values - some sourced from live
  API responses, not just local user input - unescaped into single-
  quoted Python string literals inside `python3 -c "..."`/PowerShell
  here-strings. A crafted value (or a compromised Trakt/Simkl response)
  could break out and inject arbitrary Python. Every call site now
  passes values as real subprocess arguments (`sys.argv`), never
  text-interpolated into the script body - matching the pattern
  `run.sh`'s own version-check helpers already used correctly.
- **[HIGH] Plex token leaking into plaintext logs.** `utils/plex.py`
  built several request URLs with `?X-Plex-Token=...` directly in the
  query string (now `headers={'X-Plex-Token': ...}` everywhere, matching
  the pattern already used correctly elsewhere in that file) and
  redaction (`redact()`) was only ever applied when the web UI *read* a
  log back, not when `web/job_runner.py`'s subprocess pump or
  `utils/display.py`'s `TeeLogger`/`log_warning`/`log_error` *wrote* one
  - meaning a token could sit in plaintext on disk even though the UI
  itself never displayed it unredacted. Redaction now happens at write
  time in both places. The implementation moved from `web/security.py`
  to a new neutral `utils/redact.py` (importing `web.*` from `utils.*`
  would have been a bad layering direction); `web/security.py`
  re-exports it unchanged, so no existing import breaks.
- **[MEDIUM] Config files written world-readable.** `config.yml`/
  `tuning.yml`/`trakt.yml`/etc hold the Plex token and integration API
  keys/tokens in plaintext; a plain `open(path, 'w')` lands at the OS
  umask default (typically `0o644` on Linux/Docker). `utils/
  migrate_config.py`, `utils/trakt_auth.py`, and `web/config_io.py`'s
  `save_module` now explicitly `chmod(path, 0o600)` after every such
  write (no-op-with-warning on Windows, where POSIX permission bits
  don't apply - never crashes).
- **[MEDIUM] No clickjacking/CSP protection on the core config UI.**
  Only the watchlist-export route had a `Content-Security-Policy`
  before this. A new `web/app.py` `after_request` hook sets
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, and a baseline CSP on every response
  that doesn't already set its own - the watchlist route's own
  (stricter, Google-Fonts-allowing) CSP still always wins on that one
  route.
- **[MEDIUM] Missing request timeouts** on the last two outbound calls
  in `utils/plex.py` that lacked one (`timeout=30`, matching every other
  call in that file).
- **[MEDIUM] API keys survivable via cross-host redirects.** `requests`
  follows redirects by default and only auto-strips the `Authorization`
  header on a cross-host hop, never a custom header like `X-Api-Key` - a
  malicious/compromised configured Radarr/Sonarr/Tautulli host could
  redirect this app to an attacker-controlled host and harvest the key.
  `utils/api_client.py`'s shared `BaseAPIClient` now disables automatic
  redirect-following entirely and only ever re-issues a redirected
  request when the target is the *same host* (capped at 5 hops); an
  unfollowed redirect raises a clear error instead of silently trying to
  parse a redirect page as JSON. The same `allow_redirects=False`
  treatment was also applied to Trakt/Simkl/TMDB's own direct request
  paths for consistency, even though those hit fixed official APIs
  rather than user-configured hosts.
- **[MEDIUM] Uncapped recursive retry on HTTP 429.** `utils/trakt.py`
  and `utils/simkl.py` recursed indefinitely on a 429, sleeping for
  however long the server-controlled `Retry-After` said to - a
  misbehaving/malicious endpoint could hang or loop the process
  indefinitely. Both now use a bounded loop (3 retries, matching the
  pattern `utils/tmdb.py` already used correctly) with `Retry-After`
  clamped to a 60-second ceiling.
- **[MEDIUM] Response bodies from user-configured hosts were read into
  memory unbounded.** `BaseAPIClient` (Radarr/Sonarr/Tautulli/MDBList)
  and `utils/plex.py`'s direct requests now stream and cap the response
  body at 10MB (`utils/helpers.read_response_capped`) before parsing it
  - a misconfigured/compromised configured host serving an unbounded
  body can no longer exhaust this process's memory.
- Config-file-write hardening and the response cap both apply the same
  neutral-module layering discipline as the redaction fix above -
  reusable helpers live in `utils/helpers.py`, not duplicated per
  caller.
- **CI/release supply chain**: every CI/release workflow install step
  now uses the hash-locked `*.lock` files with `pip install
  --require-hashes` (including a newly-generated, hash-verified
  `build-requirements.lock` for the PyInstaller build step, matching
  the convention `requirements.lock`/etc already used) instead of a
  plain `pip install -r *.txt`. Published Docker images are now signed
  keylessly with `cosign` (Sigstore, no long-lived key in CI) right
  after push - see `docs/DOCKER.md`'s new **Verifying the image**
  section for the `cosign verify` command. Added least-privilege
  `permissions:` blocks to every workflow that was missing one, a
  `pip-audit` gate (blocking) in the test workflow and a Trivy image
  scan (report-only) in the Docker workflow, a `docker` ecosystem entry
  in Dependabot, and a digest pin (in addition to the tag) on the
  `python:3.12-slim` base image. Fixed a script-injection pattern in the
  test workflow (raw `${{ github.event.* }}` interpolated into a shell
  `run:` block) to match the `env:`-var discipline `release.yml` already
  used.
- **Self-update hardening**: the release pipeline now embeds a signed
  `# curatarr-version:` line inside `SHA256SUMS.txt` itself, so the
  version number - not just each asset's hash - is covered by the same
  signature (`utils/self_update.py` fails closed on a present-but-wrong
  version line; an absent one, from an older release, is not an error).
  The CLI's `--self-update` now reads back the freshly-swapped binary
  (`<exe> --version`, a new flag) and restores the previous binary if it
  doesn't confirm the expected version - mirroring the intent the web
  UI's update path already had via its own `/healthz` readback. Both
  the CLI and the web hand-off script now re-hash the verified asset one
  more time immediately before the swap (closing the TOCTOU window
  between verification and use), and both replace bare `powershell`/
  `sh`/`bash`/`tasklist`/`taskkill` invocations with fully-qualified
  system paths (falling back to the bare name if the expected path
  doesn't exist, so nothing breaks on an unusual install).
- Misc hardening: an open-redirect bypass (`/\evil.com`) in the update-
  banner dismiss route's `next` parameter; unescaped streaming-service
  fields in the external watchlist HTML output (defense-in-depth -
  currently fed only by a hardcoded allowlist); a path-traversal
  exposure in per-user cache-file migration if a Plex account's own
  username/title field contained a path separator; `urllib3.
  disable_warnings` no longer fires unconditionally at import, only when
  a config actually opts out of TLS verification; and `set -o pipefail`
  added to `run.sh`/`setup.sh` (with `|| true` preserved everywhere a
  non-matching `grep` in a pipeline is already handled gracefully
  afterward, so this doesn't turn an intentional "nothing found" path
  into an unwanted hard exit).

## [2.8.31] - 2026-07-24

### Changed
- **Update notice now shown for every `general.update_mode`, including `off`**: an opted-out install silently missing every update forever was a bug, not a feature - `off` only ever meant "don't apply automatically", never "don't tell me". The web UI's dismissible banner (`web/app.py`'s `_update_banner_context`) and the CLI's advisory notice (`utils/cli.py`'s `print_update_notice`) both now check for a newer release regardless of mode; `utils/update_check.py`'s `get_latest_version()` no longer special-cases `update_mode: off` to skip the network entirely - every mode uses the exact same ~12h-cached fetch path. Nothing about *applying* updates changed: `force` still auto-applies (source installs only), `notify`/`off` are still manual either way, and `run.sh`/`run.ps1`'s own interactive `Update available: vX. Update now? [y/N]` launch prompt is still skipped for `off` (that's now the only thing `off` actually disables)
- **Update dismissal is now a 7-day snooze, not effectively permanent**: the web banner's dismiss button used to set a cookie that suppressed one specific version string for a full year; it's now server-side state (`utils/update_dismissal.py`, new - a small `cache/dismissed_update.json`, same convention `utils/update_check.py`'s own cache file uses) snoozed for exactly 7 days, after which the same version is offered again if you're still on it. A release newer than the one dismissed always overrides an active snooze immediately - dismissal is scoped to the exact version string, never "any future update". Server-side (rather than cookie) storage is also what lets the CLI notice respect the same dismissal the web UI wrote, and vice versa - the old per-version cookie (`UPDATE_DISMISS_COOKIE`) is removed

## [2.8.30] - 2026-07-24

### Added
- **Docker support** (#188): a production-quality, multi-arch (`linux/amd64` + `linux/arm64`) image published to `ghcr.io/orchestratedchaos/curatarr`, replacing the old CLI-only image. One image now serves both the web UI (default `CMD`, `EXPOSE 8787`, `HEALTHCHECK` against `/healthz`) and one-shot recommender runs for scheduling (`docker run curatarr recommend [movie|tv|external|full]`) - see the new `docs/DOCKER.md` and `docker-compose.yml` template. The web UI runs on [waitress](https://docs.pylonsproject.org/projects/waitress/) (a production, multi-threaded WSGI server - `web/docker_server.py`, `requirements-docker.lock`) rather than Flask's dev server; the native app (`run-ui.sh`/`run-ui.ps1`, standalone binaries) is untouched and still uses Flask's dev server bound to `127.0.0.1` only. Multi-stage build installs from the hash-locked `requirements.lock`/`requirements-ui.lock`/`requirements-docker.lock` (`pip install --require-hashes`) with no build toolchain in the final layer, and runs as a non-root user (uid/gid 1000). Config/cache/logs/recommendations are separated from the app's own code via a new `CURATARR_CONFIG_DIR` environment variable override in `utils.helpers.get_project_root()` (unset for every existing source/frozen install - purely additive), pointed at `/data` - same `config/`, `cache/`, `logs/`, `recommendations/` layout a frozen binary already uses at `~/.curatarr`, individually mountable (`docker-compose.yml` maps each to its own host directory, e.g. `./config:/data/config`)
- `.github/workflows/docker.yml`: builds and pushes the image via `docker buildx` on every signed release tag (tagged with the version + `latest`, independently re-verified against the same pinned release-signing key `release.yml` uses) and as `:edge` on pushes to `main`
- `CURATARR_ALLOWED_HOSTS` environment variable (`web/security.py`) - opt-in, additive extension of the web UI's Host-header allowlist, needed to reach the UI from anything other than `localhost`/`127.0.0.1` (e.g. a LAN IP or reverse-proxy hostname) when running in a container bound to `0.0.0.0`. Unset by default, so the native app's existing localhost-only enforcement (and its test coverage) is completely unchanged

### Changed
- Self-update is now an explicit, intentional no-op inside a container (`RUNNING_IN_DOCKER=true`, set by the Dockerfile): `run.sh --check-verified-update`/`--apply-verified-update` refuse up front, `web/update_apply.py`'s "Update now" gate refuses before ever shelling out to either, and the web UI's update banner and CLI update notice both point at `docker pull` instead of a button/command that would just fail

## [2.8.29] - 2026-07-23

### Added
- **Self-updating binaries**: the standalone PyInstaller binaries (Windows/macOS/Linux) can now update themselves in place - no more manual download-and-replace. The web UI's update banner **Update now** button works for binaries the same one-click way it already did for source installs, and a new `curatarr --self-update` CLI flag does the same from a terminal. Authenticity is cryptographically verified before anything is trusted: the release publishes a `SHA256SUMS.txt` covering every asset (source archive + all four binaries - previously only covered the source archive), signed offline with the maintainer's release-signing key (`scripts/sign-release-checksums.sh`, `ssh-keygen -Y sign`) and verified in pure Python (`utils/self_update.py`, via the `cryptography` package bundled into the binary itself - no dependency on a system `ssh-keygen`) against a pinned public key, fail-closed on any missing/tampered/wrong-key signature. Only once that signature verifies is the downloaded binary's SHA256 checked against the now-trusted sums file; only then is the running executable atomically swapped (Windows: rename-while-running, since an open .exe can't be overwritten directly; macOS/Linux: atomic `os.replace()`) and relaunched. Any failure at any step - network, verification, or swap - leaves the current binary running unchanged, never a broken install
- `.github/workflows/release.yml`: new `finalize-checksums` job aggregates every published asset's checksum (source archive + all binaries) into one `SHA256SUMS.txt` after all builds finish - the file the self-updater actually verifies against
- **Self-update swap/relaunch redesigned** after real end-to-end testing on real built binaries showed the original in-frozen-process relaunch (a running curatarr.exe launching a fresh instance of itself) was fundamentally unreliable across all three platforms. The web UI's worker (`web/update_apply.py`) now only downloads+verifies the new binary itself; the actual swap and relaunch are handed off entirely to a small plain external script (`utils/self_update_handoff.py` - PowerShell on Windows, POSIX `sh` elsewhere) that runs completely decoupled from any PyInstaller onefile runtime: it waits for the old server to fully exit, swaps the binary (keeping a `.old` backup), launches the new one as a genuinely fresh top-level process, polls its `/healthz` for the new version, and automatically restores + relaunches the original binary if the new one never comes up healthy - the user is never left without a working app. Root-caused and fixed the actual underlying bug this uncovered: PyInstaller 6's bootloader sets several hand-off environment variables beyond `_MEIPASS2` (`_PYI_ARCHIVE_FILE`, `_PYI_PARENT_PROCESS_LEVEL`, `_PYI_APPLICATION_HOME_DIR`) that, if inherited by a freshly-launched instance, make its bootloader wrongly skip its own extraction and crash during Python bootstrap - `sanitize_frozen_relaunch_env` and both hand-off scripts now strip every `_PYI_*`/`_PYINSTALLER_*` variable, not just `_MEIPASS2`. Validated with a real end-to-end CI workflow (`.github/workflows/selfupdate-e2e.yml`) that builds real binaries on windows-latest/macos-latest/ubuntu-latest and exercises the full swap cycle (5x in a row), tamper rejection (bad signature, bad hash), and the auto-rollback path against a binary that passes verification but can never boot - plus a fast local stub-based harness (`scripts/selfupdate_stub_e2e/`) for iterating on the hand-off script logic itself without needing a real PyInstaller build

## [2.8.28] - 2026-07-23

### Added
- **Update notifications**: New `general.update_mode` setting (`notify` | `force` | `off`, default `notify`) replaces the old on/off `auto_update` flag. `notify` (new default) shows a one-line CLI notice and a dismissible web UI banner when a newer signed release exists, without applying anything automatically; source installs (`run.sh`/`run.ps1`) additionally prompt `Update available: vX. Update now? [y/N]` on an interactive run. `force` keeps the old auto-apply-on-launch behavior. `off` disables checking entirely. This is the first update signal binary users get at all - previously they had zero indication a newer release existed. The version check (`utils/update_check.py`) is advisory-only (unauthenticated GitHub Releases API lookup, ~12h cache, fails open on any network error) and never applies or verifies anything - the only signature-verified update path remains `run.sh`/`run.ps1`'s existing signed-tag verification. Existing configs with `auto_update` keep their exact current behavior via an automatic `true` -> `force` / `false` -> `off` fallback
- **One-click "Update now" for source installs**: the web UI's update banner now has an `Update now` button (source installs only - binaries still get a download link). Clicking it verifies a newer signed release actually exists (`run.sh --check-verified-update` / `run.ps1 -CheckVerifiedUpdate`, reusing the exact same pinned-fingerprint signature verification as the existing auto-updater - never reimplemented in Python), then hands off to a fully detached updater process that outlives the web server: it shuts the old server down, applies the verified update (`run.sh --apply-verified-update` / `-ApplyVerifiedUpdate`), and relaunches the UI on the same port - old code if nothing verified was found or the apply failed, new code on success, so a failed update can never leave the port dead. The page polls `/healthz` and reloads automatically once the server reconnects



## [2.8.27] - 2026-07-23

### Changed
- **Windows binary launches with no console window**: `curatarr-windows-x86_64.exe` is now built windowed (`console=False` in `curatarr.spec`) - double-clicking it opens straight into the browser with no black console flash, logging instead to `%APPDATA%\curatarr\logs\curatarr.log`. Running it from an existing Command Prompt/PowerShell still prints normally (`curatarr_app.py` attaches to that parent console on startup), and `--debug`/`CURATARR_DEBUG=1` allocates a console for troubleshooting. Recommender subprocesses the web UI spawns (`web/job_runner.py`) now also pass `CREATE_NO_WINDOW` on Windows so they don't flash their own console windows either. macOS/Linux binaries are unaffected

## [2.8.26] - 2026-07-23

### Changed
- Coverage measurement now includes `recommenders/` (base/movie/tv/external/external_exports/external_output) - previously the CI `--cov=.` run already collected these modules, but they were the main drag on the 90% total. Added ~120 unit tests covering the core recommendation engine and #157 per-library logic: label/collection management and candidate scoring in `base.py` (61% -> 96%), watched-history collection and rating-tier weighting in `movie.py`/`tv.py` (56%/54% -> 92%/94%), and the `process_user_movie_library`/`process_user_tv_library` per-library fan-out plus `_resolve_library_groups` routing in `external.py`/`external_exports.py`. Overall coverage 90% -> 92%. `external.py`'s HTML/markdown/watchlist generation and `external_exports.py`'s MDBList/Simkl/Trakt-sync exports remain thin (largely untested) - out of scope for this pass, flagged for follow-up

## [2.8.25] - 2026-07-23

### Added
- **Multiple Plex libraries** (#157): Each Plex library is now a first-class entity with its own Sonarr/Radarr routing - per-library root folder, quality profile, tags, monitor/search, and optionally a separate *arr instance. Recommendations (in-library Plex collections and external -> Sonarr/Radarr suggestions) run per-library, so Movies, TV, Anime, and Kids can each follow their own rules and land in their own destinations from a single instance. Existing single-library configs auto-migrate on first run. Manage via the new **Libraries** screen (`/config/libraries`) or the `libraries:` list in `config.yml`
- **Web UI**: Run curatarr from the browser (`http://127.0.0.1:8787`) - dashboard, run-with-live-log, results, and config screens for connections/users/settings/libraries. CLI/cron flow is unchanged
- **One-click binaries for every platform**: Windows (x64), macOS (universal - Intel + Apple Silicon), and Linux (x64 + arm64) downloads, each with a SHA256 checksum - no Python or terminal required

### Changed
- Auto-update now verifies a signed release tag (fail-closed) before applying

### Security
- Patched `requests` CVE; dependencies pinned and hash-locked
- Added secret-scanning (gitleaks) gate on every push

## [2.8.20] - 2026-07-20

### Added
- **Optional Tautulli watch-history integration** (#150): When `tautulli.enabled` is set, Curatarr supplements each user's Plex watch history with history pulled from a Tautulli instance, weighted the same way as Plex history (recency decay, ratings, rewatch). Mainly useful for shared/external Plex users whose Plex-native history retention is thin. Users are matched to Plex accounts by email, falling back to username. Disabled by default; if Tautulli is unreachable or a user can't be mapped, Curatarr silently falls back to Plex-only history (no regression). Configure via the new `tautulli` block in `config.yml` (`enabled`, `url`, `api_key`)

### Removed
- Dead `_get_plex_user_ids` scaffolding in `recommenders/base.py` (unused, 0 callers) - superseded by `utils/tautulli.py`'s user-mapping logic

## [2.8.19] - 2026-07-20

### Fixed
- **Renaming a Plex account no longer resets user settings** (#153): Per-user preferences, cache files, and Plex labels/collections were keyed on the mutable Plex username instead of the stable Plex account id. Renaming an account in Plex created what looked like a brand-new user, dropping `display_name`/`exclude_genres`/`max_rating` back to defaults and orphaning the old collection. Curatarr now tracks a Plex account id -> username map (`cache/user_id_map.json`) and, on detecting a rename, migrates `users.preferences.<name>` and `users.list` in `config.yml`, renames the affected cache files, and cleans up the stale collection under the old name. Falls back to today's behavior if a stable id can't be resolved

## [2.8.16] - 2026-02-06

### Fixed
- **TypeError when user_preferences is None** (#140): Fixed crash when `users.preferences` config key exists but resolves to `None` (e.g., empty YAML value). Added null-safety to `get_excluded_genres_for_user`, `get_max_rating_for_user`, and collection display name lookup

## [2.8.15] - 2026-01-21

### Added
- **Per-user content rating filter**: Each user can set a `max_rating` in their preferences (e.g., `PG-13` for movies, `TV-14` for TV). Recommendations above that rating are filtered out. Configure in `users.preferences.username.max_rating`

### Fixed
- **Private collections now fully working**: Collections are hidden from other users while items remain visible to everyone. Uses separate label prefixes: `PrivateCollection_*` for collections (excluded), `Recommended_*` for items (not excluded). Multiple users can be recommended the same item and all will see it in their library

### Removed
- Dead import `from urllib.parse import quote` in `utils/plex.py`

## [2.8.14] - 2026-01-21

### Added
- **Private collections** (enabled by default): Each user only sees their own recommendations, not other users'. Uses Plex's exclude-based label restrictions. Disable with `private_collections: false` in tuning.yml. Note: Admin always sees all (Plex limitation), restrictions work on Library tab (Home/Recommended has a known Plex bug)

## [2.8.13] - 2026-01-20

### Added
- **Clickable streaming badges**: Streaming service badges now link to JustWatch search for the title
- **Animated badge on all recommendations**: Extended the `[Animated]` badge to Movies, TV Shows, and Horizon Huntarr tabs (was previously only on Sequel Huntarr)

### Changed
- **Consolidated setup wizard**: Removed duplicate wizard code from run.sh, now delegates to setup.sh (reduces run.sh by ~1100 lines)

### Internal
- Added `original_language` field to Trakt TMDB details fetch (prep for language filtering)

## [2.8.12] - 2026-01-20

### Fixed
- **Recommendation limit was 10 instead of 50**: Default `limit_plex_results` was 10, causing only 10 recommendations to be generated even though collection target was 50. Now generates 2x candidates (100 movies, 40 TV) so more items compete for collection spots
- **Collection items not being added**: Fixed bug where fuzzy title matching found Plex items but exact title+year re-matching failed, causing recommendations to silently not be added to collections. Now matches by Plex ratingKey for reliability
- **Direct Plex item fetch**: Now uses `plex.fetchItem(ratingKey)` instead of fuzzy search when ratingKey is available, avoiding potential wrong-item matches
- **Labeled items missing from cache**: Items labeled in Plex but missing from cache are now included as candidates with score 0 instead of being silently skipped

### Improved
- **Progress output during collection update**: Added progress indicators when locating Plex items and scoring candidates to show activity during long-running operations

### Changed
- **Plex collections no longer decay over time**: Removed time-based staleness removal from internal Plex recommendation collections. Items now stay in your collection until replaced by higher-scoring recommendations or watched. Score-based eviction ensures the best recs stay
- **External recommendations no longer decay over time**: Same change for external watchlist recommendations - items persist until replaced by better-scored alternatives or acquired/ignored
- **More aggressive discovery**: Increased max iterations (5→8), wider candidate pool (1000→1500), more results per genre/keyword search. Users with large libraries should now get fuller recommendation lists
- **Smarter early termination**: Discovery no longer gives up after 2 dry iterations unless already at 80% of target. Keeps trying when far below quota
- **Lowered discovery thresholds**: Rating 6.0→5.5, votes 100→50, threshold floor 40%→35%. Wider initial net, quality filtering still happens during scoring

### Removed
- **`stale_removal_days` no longer removes recommendations**: This config option is now deprecated. Items rotate based on score, not age

## [2.8.10] - 2026-01-10

### Changed
- **Bump cache version to 4**: Auto-invalidates old TV show caches to pick up new `production_company_ids` field for franchise bonus

## [2.8.9] - 2026-01-10

### Added
- **TV franchise/spinoff bonus**: TV shows from production companies you've watched get a bonus (similar to movie collection bonus). Helps recommend Star Trek spinoffs if you watch Star Trek, NCIS spinoffs if you watch NCIS, etc.

## [2.8.8] - 2026-01-10

### Added
- **Animated badge in Sequel Huntarr**: Movies with Animation genre now show cyan `[Animated]` badge to distinguish animated remakes/sequels from live action

## [2.8.7] - 2026-01-10

### Added
- **TV rating multiplier**: TV recommender now weights shows by user ratings like movies (5-star shows boost similar content, low ratings penalize similar content)
- **Trakt source prioritization**: When same title appears in multiple Trakt sources, keeps highest quality source (recommendations > anticipated > popular > trending)

### Performance Improvements
- **Pre-computed TF-IDF thresholds**: Genre and keyword thresholds calculated once per profile instead of per-item

## [2.8.6] - 2026-01-10

### Added
- **TV recency decay**: TV recommender now applies recency weighting like movies (recently watched shows weighted higher)

### Performance Improvements
- **Memoized fuzzy keyword matching**: Fuzzy match results cached per profile to avoid O(n²) repeated lookups

## [2.8.5] - 2026-01-10

### Performance Improvements
- **Watch provider caching**: Results cached for 7 days to reduce TMDB API calls
- **Keyword ID caching**: Keyword lookups cached to avoid redundant API searches
- **Pre-normalized user profiles**: Lowercase key lookups built once instead of per-item
- **Optimized is_in_library()**: O(1) title set lookup instead of O(N) loop
- **Include genres in collection details**: Eliminates extra API call per huntarr movie
- **Reuse scored_cache**: Previously scored items re-evaluated when thresholds relax

### Changed
- **Thin profiles use reduced iterations**: Instead of skipping to generic popular content, thin profiles now run 2 quick personalized iterations
- **Slower threshold relaxation**: Drops 5% per iteration (was 10%) for better match quality
- **Higher threshold floor**: Minimum threshold is now 40% (was 25%)
- **Tuned discovery thresholds**: `DISCOVER_MIN_RATING` 6.0 (was 5.0), `DISCOVER_MIN_VOTES` 100 (was 50), `MAX_CANDIDATES` 1000 (was 1500)

## [2.8.4] - 2026-01-10

### Added
- **Filter bar for HTML watchlist**: Art Deco styled filter controls
  - Text search: Filter by title
  - Rating filter: Set minimum rating threshold
  - Year range: Filter by release year (from/to)
  - Days listed: Filter by maximum days on watchlist
  - Streaming service filter: Multi-select dropdown with brand colors for each service
  - "My Services" option to show only items on subscribed services
  - Rent/Acquire filters for non-streaming content
  - Art Deco styling: gold pinstripe, film strip motifs, beveled inputs, corner accents
- Filters apply across all tabs and affect export counts

### Changed
- **TV special scanning is now much faster**: Uses Plex search instead of iterating all episodes
- **Thin profile fast path**: Users with <40 items get genre-popular fallback (skips slow iterations)
- **Early termination**: Stop iterating after 2 consecutive iterations with no new matches

## [2.8.3] - 2026-01-09

### Changed
- Setup wizard now asks about Sequel Huntarr and Horizon Huntarr separately
- Config uses new nested `huntarr:` structure with `sequel_huntarr` and `horizon_huntarr` options

## [2.8.2] - 2026-01-09

### Changed
- Rent badges now use Blockbuster-inspired colors (blue background, yellow text)
- Increased badge font size from 9px to 12px for better readability
- Rent/buy badges show "+X more" indicator with tooltip showing all providers on hover
- Added progress indicator when scanning TV library for specials

## [2.8.1] - 2026-01-08

### Added
- **Rental/Purchase availability**: Movies not on streaming now show rent/buy options
  - Amber "Rent: Provider, Provider" badge when available for rental
  - Blue "Buy: Provider, Provider" badge when only purchasable
  - "Acquire" badge only shown when not available digitally anywhere
  - Supports: Apple TV, Amazon, Google Play, Vudu, YouTube, Microsoft, DIRECTV, Spectrum

## [2.8.0] - 2026-01-08

### Added
- **Sequel Huntarr**: Rebranded Huntarr - finds missing movies from collections you've started
- **Horizon Huntarr**: New feature - finds upcoming unreleased movies from collections you own
  - Shows release date and production status (Post Production, In Production, Planned, Rumored)
  - Color-coded status badges in HTML output
  - Separate cache for horizon data
- Huntarr tabs now displayed in dedicated row below user tabs (centered)
- New config structure for huntarr features:
  ```yaml
  huntarr:
    sequel_huntarr: true
    horizon_huntarr: true
  ```

### Changed
- Old log removal now logs at INFO level instead of WARNING (expected behavior)
- Removed `--no-huntarr` CLI flag (use config to enable/disable features)
- IMDB IDs now cached permanently (no more re-fetching 700+ IDs on every run)

## [2.7.6] - 2026-01-09

### Changed
- Smart HTML browser opening with tab reuse
  - On macOS: Detects if watchlist is already open in Chrome/Safari, brings to focus and refreshes
  - Opens in new tab of existing browser window when possible
  - Falls back to system default browser if no browser is running
  - Cross-platform support for macOS, Windows, and Linux

## [2.7.5] - 2026-01-08

### Fixed
- Collection creation now provides clear feedback instead of silently failing
  - Shows warning when `add_label` is disabled in config
  - Shows warning when no recommendations are generated
  - Shows error when no recommended items exist in Plex library
  - Shows warning when no items to add to collection
  - Exception errors now return proper failure status

### Changed
- Final message updated from "Your recommendations are ready!" to more accurate
  "Curatarr Finished" with guidance to check above for warnings
- `manage_plex_labels()` and `_sync_plex_collection()` now return boolean success status

## [2.7.4] - 2026-01-07

### Changed
- Code cleanup and audit improvements
  - Added debug logging to silent exception handlers for better troubleshooting
  - Extracted magic numbers to named constants in `utils/config.py`
  - Moved deferred imports to module level for cleaner code
  - Removed unused imports from production code

### Added
- New constants in `utils/config.py`:
  - `TMDB_REQUEST_TIMEOUT`, `SONARR_REQUEST_TIMEOUT`, `RADARR_REQUEST_TIMEOUT`
  - `COLLECTION_BONUS_BASE`, `COLLECTION_BONUS_LOG_FACTOR`, `COLLECTION_BONUS_CAP`
  - `TMDB_TV_MOVIE_GENRE_ID`

## [2.7.3] - 2026-01-07

### Changed
- Expanded test coverage from 84% to 85% (1003 tests)
- CI now enforces 85% minimum coverage (up from 80%)
- Added comprehensive tests for CLI utilities and config migration

## [2.7.2] - 2026-01-07

### Fixed
- Huntarr now detects TV specials stored in TV library
  - TV movies (TMDB genre 10770) like "Phineas and Ferb: Mission Marvel" checked against TV library
  - Uses title matching since TMDB often has separate movie/episode IDs for the same content
  - Prevents showing TV specials as "missing" when they exist as episodes
  - "TV Special" badge displayed on remaining TV movie items in Huntarr list

### Changed
- Bumped Huntarr cache version to v3 (forces rebuild to include `is_tv_movie` flag)

## [2.7.1] - 2026-01-06

### Fixed
- Huntarr now filters out unreleased movies (no release date/year)
  - Only shows movies you can actually acquire
  - Collection counts only include released movies (e.g., "2/3" not "2/4" when 1 is unreleased)
  - Bumped Huntarr cache version to invalidate stale data

### Added
- Expanded test coverage from 61% to 85%
  - New test files: `test_cli.py`, `test_external_exports.py`, `test_external_output.py`
  - Added 55+ new tests across CLI utilities, export functions, and Trakt discovery
- Cache versioning for external recommendations and Trakt discovery caches

## [2.7.0] - 2026-01-06

### Added
- **Score-sorted display with streaming icons**
  - Recommendations now displayed in flat tables sorted by match score (highest first)
  - New "Streaming" column shows colored badges for all available streaming services
  - User's streaming services highlighted with gold border
  - Replaces old grouped-by-service layout for cleaner, score-focused view
- **Huntarr** (enabled by default)
  - Hunt down missing movies from collections you've started
  - New "Huntarr" tab on HTML watchlist
  - Scans Plex library for movies with TMDB collection IDs
  - Shows collection name, owned count, and streaming availability
  - Flags: `--no-huntarr` to disable, `--huntarr-only` to run without recommendations
  - Designed for potential future spinoff as standalone tool
- **Column sorting**
  - Click any column header to sort by that column
  - Supports ascending/descending toggle
  - Works with text, numbers, percentages, and fractions (e.g., "2/4")
  - Visual indicators (arrows) show current sort state

### Changed
- `categorize_by_streaming_service()` now returns `all_items` list with streaming info attached to each item
- `generate_combined_html()` accepts optional `missing_sequels` parameter
- User tabs now centered on page with tighter background wrapping

## [2.6.1] - 2026-01-06

### Fixed
- Fixed NameError when `negative_signals.dropped_shows` disabled (show_completion_data not initialized)
- Replaced bare `except Exception` handlers with specific exception types throughout codebase
- Removed unused `used_indices` variable in scoring.py
- Removed duplicate `flatten_categorized_items` function (consolidated in external_exports.py)
- Standardized studio counter key to `'studios'` (plural) for consistency with other counter keys

### Added
- Media type constants (`MEDIA_TYPE_MOVIE`, `MEDIA_TYPE_TV`, `MEDIA_KEY_MOVIES`, `MEDIA_KEY_SHOWS`)

## [2.6.0] - 2026-01-06

### Added
- Shared count badges on external watchlist HTML
  - Shows how many users have each movie/show on their list (e.g., "4/6")
  - Higher count = higher priority to acquire
- Progressive threshold relaxation for discovery iterations
  - Iterations 1-2: use configured threshold (default 65%)
  - Iteration 3: drops 10% (55%)
  - Iteration 4: drops 10% more (45%)
  - Iteration 5: drops to 25% floor
  - Helps fill lists when strict threshold finds few matches

### Fixed
- Movies appearing multiple times in same user's recommendations
  - Now places each item in ONE streaming service bucket only

## [2.5.9] - 2026-01-06

### Fixed
- External recommendations crash when `users.preferences` not in config

## [2.5.8] - 2026-01-06

### Changed
- External recommendations now skip discovery when cache is healthy
  - If cache has enough quality items (>= target), discovery is skipped entirely
  - Removes stale items (on list longer than `stale_removal_days`) before checking
  - Dramatically faster subsequent runs when cache is already populated
- Discovery now only finds what's needed (deficit items), not full limit
  - Excludes cached items so function finds truly NEW items
  - Runs iterations until target reached OR max_iterations
  - Much faster when cache just needs a few items topped up
- Trakt watchlist exclusion only loaded when discovery is needed

## [2.5.7] - 2026-01-05

### Added
- Iterative discovery for external recommendations
  - Automatically expands search if initial pass doesn't hit target count (50 movies, 20 shows)
  - Up to 5 iterations: each explores new genre/keyword ranges and deeper TMDB pages
  - Iterations 2+ include "similar-to" queries based on top-scored items found
  - Configurable via `max_iterations` and `min_votes` in `external_recommendations` config
- New `fetch_similar_from_tmdb()` helper for finding content similar to high-scoring matches

### Changed
- Lowered output vote threshold from 200 to 50 for external recommendations
  - Profile score is the quality signal; 50 votes just filters garbage TMDB entries
  - Hidden gems that match your profile no longer excluded by popularity filter
- Default `movie_limit` increased from 30 to 50 in example config
- Default `min_relevance_score` increased from 0.25 to 0.65 (matches quality bar threshold)
- End-of-run message now includes clickable link to external watchlist HTML (if generated)

## [2.5.6] - 2026-01-05

### Fixed
- Sonarr and Radarr exports failing with `'str' object has no attribute 'get'`
  - Bug caused by nested categorized structure not being properly flattened

### Changed
- External recommender console output now matches internal recommender style
  - Added color to key status lines (CYAN for progress, GREEN for success)
  - Removed checkmarks and dashed separators from status messages
  - Section headers now use `=== Title ===` format
- Redesigned HTML watchlist page with polished theater aesthetic
  - Added red velvet curtains on left, right, and top (valance)
  - New "CURATARR Watchlist" branding with gold gradient text
  - Enhanced depth with layered shadows and subtle animations
  - Footer with "Powered by Curatarr" attribution

## [2.5.5] - 2026-01-05

### Added
- Complete setup wizard for Windows `run.ps1` (Steps 6-10: Trakt, Sonarr, Radarr, MDBList, Simkl)
- Standalone `setup.sh` wizard for Docker users to generate config files before container start
- OAuth device flow support in both setup wizards for Trakt and Simkl authentication

### Changed
- Windows setup wizard now matches Linux/Mac feature parity with all integration options
- Renamed Windows scheduled task from "PlexRecommender" to "Curatarr"

## [2.5.4] - 2026-01-05

### Fixed
- Missing `get_authenticated_trakt_client` import in `external.py` after module split

## [2.5.3] - 2026-01-05

### Changed
- Split `recommenders/external.py` (2,340 lines) into two modules for maintainability
  - `external.py` (~1,200 lines): Core recommendation engine (discovery, profiles, matching)
  - `external_exports.py` (~1,000 lines): Export functions (Trakt, Sonarr, Radarr, MDBList, Simkl)
- Silent exception in `utils/labels.py` now logs debug message instead of silently passing

### Technical
- No functional changes, improved code organization
- Export functions moved: `export_to_trakt`, `export_to_sonarr`, `export_to_radarr`, `export_to_mdblist`, `export_to_simkl`, `sync_watch_history_to_trakt`
- Helper functions moved: `get_imdb_id`, `collect_imdb_ids`, `_sync_items_in_batches`

## [2.5.2] - 2026-01-05

### Added
- New `utils/api_client.py` with `BaseAPIClient` class for shared API client functionality

### Changed
- Refactored `manage_plex_labels()` from 142 lines into 5 smaller helper functions
- API clients (Radarr, Sonarr, MDBList) now inherit from `BaseAPIClient`
  - Consolidates rate limiting, request handling, and error parsing
- Added `PLEX_REQUEST_TIMEOUT` constant to `utils/config.py`
- Added missing docstrings to `main()` and `process_recommendations()` functions

### Technical
- Continued code cleanup from v2.5.1 review
- No functional changes, improved maintainability and code reuse

## [2.5.1] - 2026-01-05

### Fixed
- Silent exception handlers now log debug messages instead of silently passing
  - Affects: `utils/radarr.py`, `utils/sonarr.py`, `utils/mdblist.py`, `utils/trakt.py`

### Changed
- Rating tier thresholds and multipliers extracted to named constants in `utils/config.py`
  - `RATING_TIER_5_STAR`, `RATING_TIER_4_STAR`, `RATING_TIER_3_STAR`
  - `RATING_MULTIPLIER_5_STAR`, `RATING_MULTIPLIER_4_STAR`, etc.
- Consolidated duplicate rating extraction logic to use `extract_rating()` utility
- Extracted duplicate Plex account ID resolution to `_resolve_myplex_account_ids()` helper

### Technical
- Code cleanup based on comprehensive codebase review
- No functional changes, improved maintainability

## [2.5.0] - 2026-01-05

### Added
- **Simkl integration** — Full integration with Simkl for anime/TV/movie tracking
  - PIN-based OAuth authentication (works in Docker/SSH)
  - Import watch history from Simkl (especially anime from Crunchyroll, etc.)
  - Discovery from Simkl trending/popular (excellent for anime recommendations)
  - Export recommendations to Simkl watchlist
  - Setup wizard integration (Step 10)
  - 51 new unit tests for Simkl client

### Technical
- New `utils/simkl.py` module with `SimklClient` class
- Supports TMDB, IMDB, MAL, AniDB, and other anime IDs
- Rate limiting with 0.2s delay between API calls

## [2.4.0] - 2026-01-05

### Added
- **MDBList integration** — Export recommendations to shareable MDBList lists
  - Push recommendations to MDBList for use with Kometa/PMM and other tools
  - Configurable via `config/mdblist.yml`
  - Simple API key authentication (no OAuth)
  - Supports user_mode: `mapping`, `per_user`, or `combined`
  - Replace or append mode for list updates
  - Setup wizard integration in `run.sh` (Step 9)
  - 36 new unit tests for MDBList client

### Technical
- New `utils/mdblist.py` module with `MDBListClient` class
- Uses TMDB IDs directly (no conversion needed)
- Rate limiting with 0.1s delay between API calls

## [2.3.0] - 2026-01-05

### Added
- **Radarr integration** — Auto-add external movie recommendations to Radarr
  - Push recommendations directly to Radarr for tracking/downloading
  - Configurable via `config/radarr.yml` (mirrors Sonarr config style)
  - Safe defaults: `monitor: false`, `search_for_movie: false` (just adds to library)
  - Tagging system for easy cleanup (`Curatarr` tag on all added movies)
  - Setup wizard integration in `run.sh` (Step 8)
  - Supports user_mode: `mapping`, `per_user`, or `combined`
  - 28 new unit tests for Radarr client

### Technical
- New `utils/radarr.py` module with `RadarrClient` class
- Uses TMDB IDs directly (no conversion needed like Sonarr)
- Rate limiting with 0.1s delay between API calls

## [2.2.0] - 2026-01-05

### Added
- **Sonarr integration** — Auto-add external TV recommendations to Sonarr
  - Push recommendations directly to Sonarr for tracking/downloading
  - Configurable via `config/sonarr.yml` (mirrors Trakt config style)
  - Safe defaults: `monitor: false`, `search_missing: false` (just adds to library)
  - Tagging system for easy cleanup (`Curatarr` tag on all added shows)
  - Setup wizard integration in `run.sh` (Step 7)
  - Supports user_mode: `mapping`, `per_user`, or `combined`
  - 27 new unit tests for Sonarr client

### Technical
- New `utils/sonarr.py` module with `SonarrClient` class
- ID conversion: TMDB → IMDB → Sonarr lookup → TVDB → add_series
- Rate limiting with 0.5s delay between API calls

## [2.1.4] - 2026-01-05

### Changed
- Skip auto-update check in Docker containers (users should rebuild to update)
- Removed git package from Docker image (no longer needed)

## [2.1.3] - 2026-01-04

### Changed
- Removed unused imports across 6 files (traceback, Type, sys, List, Optional, yaml)

## [2.1.2] - 2026-01-04

### Changed
- **Silent exception handlers now log debug messages** — All `except: pass` patterns replaced with `logger.debug()` or `log_warning()` calls for easier troubleshooting
- **Scoring constants extracted to config.py** — TF-IDF penalties and popularity dampening values now defined as named constants
- **Discovery constants extracted in external.py** — Magic numbers for candidate discovery now use named constants
- **Deferred import moved to module level** — `import random` in scoring.py moved to top of file
- **Added type hints** — Key functions in external.py and external_output.py now have proper type annotations
- **Extracted Trakt batch sync helper** — Duplicate batching code consolidated into `_sync_items_in_batches()` function

### Fixed
- Removed dead code (unused language extraction block in external.py)

## [2.1.1] - 2026-01-04

### Changed
- **Code refactoring** — Major cleanup reducing duplicate code by ~300 lines
  - Extracted shared CLI utilities to `utils/cli.py`
  - Consolidated Trakt enhancement logic to `utils/trakt.py`
  - Added `get_project_root()` utility to eliminate repeated path patterns
  - Simplified main() functions in movie.py and tv.py recommenders

### Fixed
- Bare except blocks replaced with specific exception types
- Deferred imports moved to module level for cleaner code
- Removed redundant `watched_data` variable (now uses `watched_data_counters` consistently)
- Improved type hints (e.g., `Set[tuple]` → `Set[Tuple[str, Optional[int]]]`)
- Added debug logging to silent exception handlers for easier troubleshooting

## [2.1.0] - 2026-01-04

### Added
- **Trakt Discovery** — Use Trakt's community data to find new content
  - Trending: Most watched right now (great for "what's hot")
  - Popular: Most watched all time (classic hits)
  - Anticipated: Most anticipated upcoming releases
  - Recommendations: Personalized picks based on your Trakt ratings
- Discovery results are cached for 6 hours to reduce API calls
- Discovery candidates are merged with TMDB Discover for scoring
- New config section in `config/trakt.yml`:
  ```yaml
  discovery:
    enabled: true
    use_trending: true
    use_popular: false
    use_anticipated: false
    use_recommendations: false
  ```

### Technical
- Added `utils/trakt_discovery.py` module with caching
- Added TraktClient methods: `get_trending()`, `get_popular()`, `get_anticipated()`, `get_recommendations()`, `get_related()`
- 20 new tests for Trakt discovery (698 total)

## [2.0.0] - 2026-01-04

### Changed
- **Modular config structure** — Split monolithic config.yml into feature modules
  - All configs now live in `config/` directory
  - `config/config.yml` — Core essentials only (plex, tmdb, users, general)
  - `config/tuning.yml` — Display options, weights, scoring parameters (optional)
  - `config/trakt.yml` — Trakt integration settings (created if Trakt enabled)
  - `config/radarr.yml` / `config/sonarr.yml` — Arr integration (optional)
- **Auto-migration** — Existing configs automatically split on first run
  - Original config backed up as `config.yml.backup.{timestamp}`
  - Migration runs transparently, no user action needed
- Setup wizard now generates slim config.yml (~25 lines vs ~120)
- Radarr/Sonarr configs now at root level instead of nested under movies/tv

### Added
- `config/` directory for all configuration files
- `utils/migrate_config.py` — Manual migration script (`python3 -m utils.migrate_config`)
- Example files in `config/`: `config.example.yml`, `tuning.example.yml`, etc.
- Tests for modular config loading and migration

### Migration
Existing users: Run Curatarr normally — your config will be auto-migrated.
The original config is backed up, and module files are created in `config/`.

## [1.7.7] - 2026-01-04

### Changed
- Lowered CI coverage threshold from 90% to 80% for utils
- Recommenders are integration-heavy; utils remain well-tested (92%+)

### Added
- Unit tests for `trakt_auth.py` and `trakt_sync.py` CLI entry points
- Additional cache function tests in `test_tmdb.py` and `test_trakt.py`

## [1.7.6] - 2026-01-04

### Added
- **Trakt profile enhancement caching** — Skip processing when nothing changed
  - Caches seen Trakt IDs in `trakt_enhance_cache.json`
  - Only processes new items, skips entirely if unchanged
- **IMDB→TMDB ID conversion cache** — Speeds up Trakt integration
  - One-time conversion penalty, instant lookups after
  - Shared cache in `imdb_tmdb_cache.json` with versioning
- **Plex watch history sync to Trakt** — Runs before recommenders
  - New `utils/trakt_sync.py` CLI entry point
  - Syncs watched movies/shows to Trakt with batching
  - Caches synced IDs to avoid re-syncing

### Changed
- Consolidated duplicate IMDB→TMDB functions into `utils/tmdb.py`
- Progress indicators throughout Trakt operations
- User mapping check ensures only configured users get Trakt enhancement

## [1.7.5] - 2026-01-04

### Added
- **HTML Export for Trakt** — New "Export for Trakt" button in watchlist HTML
  - Select items and download IMDB IDs to import into Trakt lists
  - Works alongside Radarr/Sonarr export buttons
- **Trakt watch history import** — Merge streaming service history into recommendations
  - Pulls watch history from Trakt (Netflix, Disney+, Hulu, etc.)
  - Enhances taste profile with content not in Plex library
  - New config: `trakt.import.merge_watch_history` (default: true)
- **Configurable auto-sync** — Control automatic Trakt list syncing
  - New config: `trakt.export.auto_sync` (default: true)
  - Set to false to only use manual HTML export

## [1.7.4] - 2026-01-04

### Added
- **Integration status display** — Shows enabled/disabled status for all integrations at startup
  - Plex, TMDB (required), Trakt, External Recommendations
  - Color-coded: green checkmark (active), yellow circle (disabled/needs auth), red X (missing)

## [1.7.3] - 2026-01-04

### Added
- **Setup wizard Trakt integration** — Interactive setup now includes optional Trakt configuration
  - Prompts for Trakt API credentials during first-run wizard
  - Auto-generates Trakt section in config.yml
  - New `utils/trakt_auth.py` script for device code authentication
- Completes full Trakt integration suite (foundation, export, import, wizard)

## [1.7.2] - 2026-01-04

### Added
- **Trakt import** — Pull data from Trakt to enhance recommendations
  - Exclude Trakt watchlist items from recommendations (you already know about them)
  - Import methods: `get_watched_movies()`, `get_watched_shows()`, `get_ratings()`, `get_watchlist()`
  - Configurable via `trakt.import.enabled` and `trakt.import.exclude_watchlist`
  - 8 new unit tests for import functionality
- **Clickable Trakt list URLs** — After exporting, console shows clickable links to view lists on Trakt

## [1.7.1] - 2026-01-04

### Added
- **Trakt list export** — Push external recommendations to Trakt lists
  - Auto-syncs recommendations to Trakt after generating external watchlists
  - Creates per-user lists: "Curatarr - {username} - Movies" and "Curatarr - {username} - TV"
  - Full sync replaces list contents each run (no duplicates)
  - Configurable list prefix and privacy settings
  - 9 new unit tests for list management and sync functionality

## [1.7.0] - 2026-01-04

### Added
- **Trakt API integration foundation** — Core module for Trakt OAuth and API access
  - `TraktClient` class with device authentication flow (works in Docker/SSH)
  - Automatic token refresh when expired
  - Rate limiting (0.2s delay, well under Trakt's 1000/5min limit)
  - 28 unit tests for Trakt module
  - Config schema for Trakt credentials (disabled by default)

## [1.6.21] - 2026-01-04

### Fixed
- **Docker auto-update now works** — Included `.git` directory in Docker image
  - Containers can now self-update just like bare metal installs
  - Only adds ~1MB to image size

## [1.6.20] - 2026-01-04

### Added
- **Clickable HTML watchlist link** — Console output now shows a clickable link to open the HTML watchlist
  - Uses OSC 8 hyperlink escape codes for modern terminal support (iTerm2, Windows Terminal, GNOME Terminal, etc.)
  - Added `clickable_link()` utility function

### Changed
- **Consolidated version to single location** — `__version__` now defined only in `utils/config.py`
  - Imported by movie.py and tv.py instead of duplicated
  - Makes version bumps and rollbacks easier
- **Added `auto_open_html` to config.example.yml** — Documents the setting (defaults to false)

## [1.6.19] - 2026-01-04

### Fixed
- **Docker Windows compatibility** — Fixed entrypoint script failing on Windows Docker
  - Strip CRLF line endings from shell scripts during build
  - Explicitly invoke bash in ENTRYPOINT to avoid shebang issues

## [1.6.18] - 2026-01-03

### Changed
- **External recommendations now prioritize match score over audience rating**
  - Match score is king - recommendations based on YOUR taste, not general audience
  - Discovery casts wider net (rating >= 5.0, votes >= 50) to find more candidates
  - Output requires 65%+ match and 200+ votes - no rating gate
  - Expanded search: 10 genres, 40 results per genre, 10 keywords, 1500 max candidates

## [1.6.17] - 2026-01-03

### Fixed
- **External recommendations cache now respects quality thresholds** — Old cached items below MIN_RATING (7.0) or MIN_VOTE_COUNT (500) are automatically filtered out on load
- **Added vote_count tracking to external cache** — Enables proper filtering of low-vote content

## [1.6.16] - 2026-01-03

### Added
- **Environment variable support for sensitive tokens** — Security best practice for Docker/CI
  - `PLEX_URL` overrides `plex.url`
  - `PLEX_TOKEN` overrides `plex.token`
  - `TMDB_API_KEY` overrides `tmdb.api_key`
  - Env vars take precedence over config file values

## [1.6.15] - 2026-01-03

### Changed
- **Raised external recommendation quality thresholds** — Filters out mediocre content
  - MIN_RATING: 6.0 → 7.0 (only recommend actually good content)
  - MIN_VOTE_COUNT: 100 → 500 (enough votes to be reliable)

## [1.6.14] - 2026-01-03

### Changed
- **Consolidated TMDB helper methods to BaseRecommender** — Removed ~130 lines of duplicated code
  - Moved `_get_plex_item_tmdb_id()` to BaseRecommender (was `_get_plex_movie_tmdb_id`/`_get_plex_show_tmdb_id`)
  - Moved `_get_plex_item_imdb_id()` to BaseRecommender (was `_get_plex_movie_imdb_id`/`_get_plex_show_imdb_id`)
  - Moved `_get_tmdb_id_via_imdb()` to BaseRecommender (identical logic, different result key)
  - Moved `_get_tmdb_keywords_for_id()` to BaseRecommender (100% identical between movie/tv)
  - Moved `_get_library_imdb_ids()` to BaseRecommender (100% identical one-liner)
  - Removed unnecessary delegate methods `_extract_genres()` and `_get_*_language()` - now call utilities directly
  - Uses `self.media_type` to handle movie vs tv differences in base class methods
  - Cleaned up unused imports from movie.py and tv.py

## [1.6.13] - 2026-01-03

### Changed
- **Deep inheritance refactor** — Eliminated ~650 lines of duplicated code between movie/tv recommenders
  - Moved `get_recommendations()` to BaseRecommender (was duplicated in both)
  - Moved `manage_plex_labels()` to BaseRecommender (was duplicated in both)
  - Moved `_get_plex_user_ids()` to BaseRecommender (was identical in both)
  - Moved `_get_managed_users_watched_data()` to BaseRecommender (was near-identical)
  - Moved `_load_watched_cache()` to BaseRecommender (cache init block was duplicated)
  - Added `_do_save_watched_cache()` helper to BaseRecommender
  - Added abstract methods: `_get_media_cache()`, `_find_plex_item()`, `_calculate_similarity_from_cache()`, `_print_similarity_breakdown()`
  - Added `media_key` class attribute to recommenders for generic cache access

## [1.6.12] - 2026-01-03

### Changed
- **Recommenders now inherit from BaseRecommender** — Major refactoring to reduce code duplication
  - PlexMovieRecommender and PlexTVRecommender now properly inherit from BaseRecommender
  - Moved common initialization logic (config, plex, display options, weights) to base class
  - Implemented abstract methods: `_load_weights()`, `_get_watched_data()`, `_get_watched_count()`, `_save_watched_cache()`
  - Renamed `watched_movie_ids`/`watched_show_ids` to `watched_ids` for consistency
  - Removed duplicate `_refresh_watched_data()` (now uses base class version)
  - Uses `_get_user_context()` from base class instead of duplicating logic
  - Updated tests to mock at `recommenders.base.*` instead of media-specific modules

## [1.6.11] - 2026-01-03

### Fixed
- **Backfill handles API failures** — Collection backfill now marks movies as processed even when TMDB API returns 404
  - Prevents infinite retry loop for movies removed from TMDB

## [1.6.10] - 2026-01-03

### Removed
- **Dead code cleanup** — Removed unused code from recommenders
  - Removed unused `import random` from movie.py and tv.py
  - Removed unused utility imports (RATING_MULTIPLIERS, DEFAULT_NEGATIVE_MULTIPLIERS, DEFAULT_RATING, TOP_POOL_PERCENTAGE)
  - Removed dead `find_similar_content()` function from external.py
  - Removed duplicate `get_tmdb_keywords()` from external.py (now uses utils version)
  - Removed unused `self.plex_only` attribute from tv.py

## [1.6.9] - 2026-01-03

### Changed
- **Improved test coverage** — Added 58 new tests across recommender modules
  - tv.py: 0% → 42% coverage (33 new tests)
  - base.py: 82% → 96% coverage (12 new tests)
  - movie.py: 30% → 39% coverage (10 new tests)
  - external.py: 21% → 24% coverage (3 new tests)
  - Overall coverage: 75% → 83% (564 total tests)

## [1.6.8] - 2026-01-03

### Added
- **Collection bonus for sequels** — Movies in franchises get a score bonus
  - Tracks TMDB collection data (e.g., "Harry Potter Collection")
  - Applies 5-15% bonus for unwatched movies in collections user has watched
  - Logarithmic scaling: more watched movies = higher bonus (capped at 15%)

## [1.6.7] - 2026-01-03

### Added
- **Score caching** — Computed similarity scores are now cached per movie/show
  - Scores only recalculated when user profile changes (detected via hash)
  - Significantly speeds up subsequent runs with unchanged watch history
  - Profile hash stored with each cached score for invalidation

## [1.6.6] - 2026-01-03

### Added
- **Popularity dampening** — Slight penalty for very popular content (50k+ votes)
  - Prevents blockbusters from dominating due to more complete metadata
  - ~3% penalty per order of magnitude above threshold (capped at 10%)
  - Configurable via `use_popularity_dampening` and `popularity_threshold` parameters

## [1.6.5] - 2026-01-03

### Added
- **TF-IDF scoring** — Penalizes content matching rare genres/keywords in user's profile
  - Genres below 15% of max count receive penalty proportional to rarity
  - Unseen genres receive mild penalty (prevents "Brave" recommendations for action fans)
  - Keywords receive similar treatment with lighter penalties (0.02 per unseen)
  - Configurable via `use_tfidf` and `tfidf_penalty_threshold` parameters

## [1.6.4] - 2026-01-03

### Fixed
- **Show-level episode aggregation** — TV shows now weighted by show, not episode count
  - Previously a show with 20 episodes had 20x the weight of a show with 1 episode
  - Now each show counts as 1 unit regardless of episode count
  - Rewatch bonus only applied when user actually rewatched episodes

## [1.6.3] - 2026-01-03

### Added
- **Tiered recommendations** — Diversified recommendation selection
  - Safe picks (60%): High-confidence items from top scores
  - Diverse options (30%): Mid-tier items for variety
  - Wildcard picks (10%): Lower-scored discoveries
  - Replaces simple random sampling from top 10%
  - New `select_tiered_recommendations()` utility function

## [1.6.2] - 2026-01-03

### Changed
- **Split external.py** — Extracted output generation to `external_output.py` (607 lines)
  - `external.py` reduced from 1720 to 1134 lines
  - Improves maintainability and readability

## [1.6.1] - 2026-01-03

### Changed
- **SSL verification default** — `verify_ssl` now defaults to `True` (secure by default)
  - Users with self-signed certs can set `verify_ssl: false` in config

## [1.6.0] - 2026-01-03

### Added
- **Negative signals** — Low-rated content and dropped shows now penalize similar recommendations
  - Ratings 0-3 apply negative multipliers (-1.0 to -0.3) instead of weak positive
  - Dropped TV shows (started but abandoned) generate negative signals
  - Configurable via `negative_signals` section in config
  - Capped penalties prevent one bad movie from destroying a genre preference
- **Tests** — Added comprehensive tests for recommenders and utilities
  - 25 new tests for `recommenders/base.py` (22% → 95% coverage)
  - 20 new tests for `recommenders/movie.py`
  - 11 new tests for `utils/plex.py` (85% → 97% coverage)
  - 5 new tests for pre-calculated weight parameter
  - Total: 488 tests passing, utils/ at 96%+ coverage

### Changed
- **Counter processing consolidation** — Removed duplicate methods from recommenders
  - Movie and TV recommenders now use shared `process_counters_from_cache()`
  - Added `weight` and `cap_penalty` parameters for pre-calculated weights
  - Removed ~55 lines of duplicate code from each recommender

### Fixed
- **Collection sort order** — Collections now sort correctly using reverse `moveItem()` approach
- **Redundant ternary expressions** — Simplified `x if x else None` patterns in recommenders

### Removed
- **combine_watch_history** — Removed unused feature and dead code assignments

## [1.5.0] - 2026-01-03

### Fixed
- **SSL verification** — Added configurable `verify_ssl` option for Plex connections
  - Defaults to `false` for backwards compatibility with self-signed certs
  - PlexAPI session now respects this setting
- **HTTP timeouts** — Added 30-second timeout to all HTTP requests
  - Prevents hangs on unresponsive servers
- **Config schema mismatch** — `get_configured_users()` now reads `config['users']['list']`
  - Previously only checked legacy `config['plex']['managed_users']` path
  - Fixes per-user collection labels not being generated correctly
- **Watched detection** — Now checks both cache AND Plex `isPlayed` flag
  - Movies manually marked as watched are now properly excluded
  - Fixes watched movies appearing in recommendation collections
- **MediaContainer iteration** — Convert to list before processing
  - Plex MediaContainer is single-use; was causing empty results on second pass

### Changed
- **Dependencies** — Removed unused packages from requirements.txt
  - Removed `tmdbv3api` (not used)
  - Removed `python-dotenv` (not used)

### Added
- **Console watchlist link** — Prints `file://` URL after generating HTML watchlist
- **Tests** — Added test for `isPlayed` watched detection
- **Tests** — Updated `init_plex` tests for new SSL session handling

## [1.4.0] - 2026-01-03

### Added
- **HTML watchlist with export buttons** — Interactive HTML view of external recommendations
  - Single page with tabs for each user
  - Selectable items with checkboxes (unchecked by default)
  - "Export to Radarr" button downloads IMDB IDs for selected movies
  - "Export to Sonarr" button downloads IMDB IDs for selected shows
  - Movie theater themed dark design with gold accents
  - Auto-open in browser after run (configurable via `auto_open_html`)

## [1.3.0] - 2026-01-03

### Added
- **Docker support** — Run Curatarr in a container
  - `Dockerfile` for building the image
  - `docker-compose.yml` for easy deployment
  - `.dockerignore` for optimized builds
  - Updated README with Docker quick start, scheduling, and troubleshooting

## [1.2.9] - 2026-01-03

### Added
- **Comprehensive unit tests** — 367 tests achieving 95% coverage
  - test_display.py: 63 tests (93% coverage)
  - test_plex.py: 92 tests (98% coverage)
  - test_scoring.py: 55 tests (95% coverage)
  - test_tmdb.py: 32 tests (99% coverage)
  - test_labels.py: 23 tests (97% coverage)
  - test_counters.py: 22 tests (96% coverage)
  - test_helpers.py: 32 tests (95% coverage)
  - test_cache.py: 19 tests (93% coverage)

### Fixed
- **Log level** — Label removal messages now log as INFO instead of WARNING

## [1.2.8] - 2026-01-03

### Added
- **Interactive setup wizard** — First-run configuration for new users
- **Unit tests** — Initial test suite for config and tmdb modules

## [1.2.7] - 2026-01-03

### Added
- **Windows support** — Full feature parity with macOS/Linux
  - `run.ps1` PowerShell script with same functionality as `run.sh`
  - Dependency checking, auto-update, first-run wizard
  - Task Scheduler integration (Windows equivalent of cron)
  - Updated README with Windows instructions throughout

## [1.2.6] - 2026-01-03

### Fixed
- **Method name bugs** — Fixed `_get_show_language` and `_get_movie_language` to call correct base class method
- **Exception handling** — Replaced bare except blocks with specific exception types
- **Config key** — Fixed `stale_removal_days` lookup (was checking wrong config section)
- **Language normalization** — Added missing `.lower()` for consistent matching
- **Return type consistency** — Aligned `tv.py` return type with `movie.py`

### Removed
- **Dead code cleanup** — Removed 5 unused methods (~200 lines):
  - `_is_show_in_library`, `_process_show_counters`, `_validate_watched_shows`
  - `_is_movie_in_library`, `_process_movie_counters`
- **Whitespace fixes** — Fixed mixed tabs/spaces throughout

## [1.2.3] - 2026-01-02

### Changed
- **Cache class refactoring** — `MovieCache` and `ShowCache` now inherit from `BaseCache`
  - Reduced ~215 lines of duplicated code
  - Each cache only implements `_process_item()` for media-specific logic
  - Shared: cache loading/saving, library updates, TMDB data fetching, language detection

## [1.2.2] - 2026-01-02

### Changed
- **Named constants** — Extracted magic numbers to `utils/config.py`:
  - `TOP_CAST_COUNT = 3`
  - `TMDB_RATE_LIMIT_DELAY = 0.5`
  - `DEFAULT_RATING = 5.0`
  - `WEIGHT_SUM_TOLERANCE = 1e-6`
  - `DEFAULT_LIMIT_PLEX_RESULTS = 10`
  - `TOP_POOL_PERCENTAGE = 0.1`

## [1.2.1] - 2026-01-02

### Fixed
- **Exception handling** — Replaced bare `except:` with specific exception types
- **Unused imports** — Removed dead imports across all files
- **Unused variables** — Cleaned up unused variable assignments
- **Pass statements** — Removed meaningless `pass` statements

## [1.2.0] - 2026-01-02

### Changed
- **Project restructure** — Reorganized recommenders into dedicated directory:
  - `movie_recommender.py` → `recommenders/movie.py`
  - `tv_recommender.py` → `recommenders/tv.py`
  - `external_recommender.py` → `recommenders/external.py`
  - `base.py` → `recommenders/base.py`
- Updated `run.sh` to use new paths
- All path references now use project root for config, cache, logs

## [1.1.0] - 2026-01-02

### Changed
- **Utils package refactoring** — Split 2500+ line `utils.py` into focused modules:
  - `utils/config.py` - Configuration utilities
  - `utils/display.py` - Output formatting, logging, colors
  - `utils/tmdb.py` - TMDB API functions
  - `utils/cache.py` - Cache I/O operations
  - `utils/labels.py` - Label management
  - `utils/scoring.py` - Similarity scoring functions
  - `utils/counters.py` - Counter utilities
  - `utils/helpers.py` - Miscellaneous helpers
  - `utils/plex.py` - Plex-specific utilities
  - `utils/__init__.py` - Re-exports 72 items for backwards compatibility

- **Scoring formula overhaul** — Changed from averaging to sum with diminishing returns
  - Multiple weak keyword matches now add up instead of averaging down
  - A movie with 15 matching keywords scores well even if each is partial
  - Typical scores now in 70-85% range instead of 20-50%

- **Weight redistribution** — When a component has no matches (e.g., unknown director),
  its weight now redistributes proportionally to components that did match

- **New default weights:**
  - Keywords: 50% (was 45%) — Most predictive signal
  - Genre: 25% (was 20%) — Baseline preference
  - Actor: 20% (was 15%) — Cast preferences
  - Director: 5% (was 15%) — Most people don't pick by director
  - Language: 0% (was 5%) — Removed due to unreliable data

### Fixed
- **format_media_output() signature** — Fixed function parameter names and order to match callers
  - Changed `media_info` to `media` parameter name
  - Added missing `show_director` and `show_genres` parameters

- **Duplicate log messages** — Warnings and errors now appear only once
  - Enabled ColoredFormatter for colored log output
  - Removed redundant print() calls from log_warning/log_error

- **Case sensitivity bugs** — Genres, directors, and actors now match case-insensitively
  - "Drama" now correctly matches "drama" in user profiles
  - Fixed major scoring undercount issue

- **External recommender cache** — Now updates scores for existing cached items
  - Previously only added new items, never updated scores
  - Scores now reflect current user profile

- **Collection smart sorting** — Collections now replace lower-scoring items with
  higher-scoring ones, not just fill gaps

### Added
- **Unit test suite** — 101 tests covering utility functions
  - Tests for plex extraction, counters, labels, cache, helpers, scoring
  - Run with: `python3 -m pytest tests/ -v`

- **Base classes** — Created `base.py` with abstract base classes for future refactoring:
  - `BaseCache` - Common cache functionality for movies and TV shows
  - `BaseRecommender` - Common recommender functionality

- **Type hints** — Added consistent type hints across utility modules:
  - `utils/helpers.py`, `utils/display.py`, `utils/plex.py`
  - Added `Any`, `Dict`, `List`, `Set`, `Tuple`, `Optional` type annotations

- Per-item weight redistribution — If a specific movie's director isn't in your
  profile, that 5% weight goes to keywords/genres/actors instead

### Removed
- **Unused imports** — Cleaned up unused imports from main modules:
  - `movie_recommender.py` - Removed `plexapi.server`, `PlexServer`, `Counter`, `quote`, `timedelta`, `math`
  - `tv_recommender.py` - Removed `plexapi.server`, `PlexServer`, `Counter`, `timedelta`, `math`
  - `base.py` - Removed `json`, `Counter`, unused utility imports

## [1.0.0] - 2026-01-02

### Added
- Initial release with movie and TV show recommendations
- External watchlist generation with streaming service grouping
- Multi-user support with per-user preferences
- Recency decay and rating multipliers
- Rewatch detection with logarithmic weighting
- Smart caching with automatic invalidation
- Auto-update from GitHub
- Consolidated utilities in utils.py
