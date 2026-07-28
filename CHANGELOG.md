# Changelog

All notable changes to Curatarr will be documented in this file.

## [2.10.80] - 2026-07-28

### Fixed

- **Two test-isolation gaps in `tests/conftest.py`'s suite-wide safety nets, found while investigating how test-fixture usernames (`alice`/`bob`) leaked into a real Plex library.**

  **Real repo `logs/` writes during a normal pytest run.** `_isolated_recommender_cache_dir` patched `recommenders.base.get_project_root` and `recommenders.external.get_project_root` but not `recommenders.movie.get_project_root`/`recommenders.tv.get_project_root` - a separate name binding each of those two modules holds from their own `from utils import get_project_root`, used by `process_recommendations()`'s `log_dir = os.path.join(get_project_root(), "logs")` (feeding `setup_log_file()`/`teardown_log_file()`/`record_run_status()`). `tests/test_movie.py`'s and `tests/test_tv.py`'s `TestProcessRecommendationsLibraryParam` classes mocked `setup_log_file`/`teardown_log_file`/the recommender class but not `record_run_status`, so a plain test run wrote real `logs/run_status_movie_alice.json` / `run_status_tv_alice.json` into the owner's own repo - confirmed reproduced and cleaned up. Fixed by extending `_isolated_recommender_cache_dir` to also patch `recommenders.movie`/`recommenders.tv`'s `get_project_root`, and by additionally patching `record_run_status` directly in both test classes (same belt-and-suspenders convention already used for `setup_log_file`/`teardown_log_file` there).

  Added a new session-scoped, autouse `_fail_if_real_logs_or_cache_written` guard: snapshots the real repo's `logs/` and `cache/` directories (path -> mtime) once at session start and once at session end, and fails the whole run with every changed path named if either directory was touched - this class of leak was previously only ever noticed by someone spotting an unexpected file in `git status`/`ls -la` afterward. Verified it actually fires (a throwaway test that wrote directly into the real `logs/` dir was correctly caught and failed the session, confirmed before removing that throwaway test).

  **The socket guard's own loopback allowance was a false guarantee.** `_block_non_loopback_sockets` (added to stop tests from making real outbound network calls) allowed any `127.0.0.1`/`::1`/`localhost` connection through unconditionally. Plex's own `plex.direct` hostnames - standard, expected Plex behavior, not exotic - resolve straight to a loopback IP (confirmed directly against this project's own `config/config.yml`, which has a real `https://127-0-0-1.<hash>.plex.direct:32400` URL); by the time a real HTTP client's `socket.connect()` fires, that hostname is already resolved to plain `127.0.0.1`, indistinguishable from this suite's own local test-server binds. A test that ended up using a real config would sail straight through the loopback check and connect to a real, live Plex Media Server running on the same machine - no current test does this, but the guard's docstring claimed a guarantee it didn't actually provide.

  Fixed by additionally blocking loopback connections to Plex's well-known default port (32400) specifically, regardless of host - nothing in this suite's legitimate loopback socket use (`test_web_routes.py`'s `TestWaitForListening`/`TestBindRetry`, which always bind an OS-assigned ephemeral port) ever targets that port. Chose this over blocking by original hostname (not reliably visible at the `.connect()` layer at all - `requests`/`urllib3`/`socket.create_connection()` all resolve via `getaddrinfo()` before `.connect()` ever fires) or requiring an explicit per-test opt-in marker (a much bigger, more invasive change to every existing legitimate local-bind test for a gap with no currently-affected test).

  New coverage in `tests/test_conftest_guards.py`: `test_blocks_genuine_external_host` (unchanged original behavior), `test_allows_loopback_on_an_ordinary_port` (the legitimate local-test-server case still works), and `test_blocks_loopback_resolving_plex_direct_style_address` (proves the guard now blocks the exact plex.direct-resolves-to-loopback case this closes).

## [2.10.79] - 2026-07-28

### Fixed

- **The optional positional `username` CLI argument (e.g. `python3 recommenders/movie.py alice`) accepted any string with zero validation and could mint real collections/labels on live Plex for a user that was never configured.** `utils/cli.py`'s `run_recommender_main()` took `args.username` straight from argparse and used it verbatim as the sole entry in `all_users` - nothing checked it against the configured user list (or a real Plex account) before it reached `recommenders/base.py`'s collection (`Recommended_<user>`) and label (`PrivateCollection_<user>`) creation. Reproduced: `python3 recommenders/movie.py alice` for a username absent from every configured location (`users.list`/`plex_users.users`/`plex.managed_users`) created a real "Alice - Recommendation" collection, applied `Recommended_alice` labels to real library items, and wrote per-user cache/log files - for a user that doesn't exist.

  `run_recommender_main()` now validates that positional argument (case-insensitively) against the actually-configured user list before it can reach any of that, and exits with a clear error naming the valid usernames if it isn't one of them. `Admin`/`Administrator` remain always accepted regardless of the configured list, unchanged - `resolve_admin_username()` already resolves either to the real Plex account username downstream, independent of what's in config.

  Deliberately a hard failure with no override flag. A `--force`-style escape hatch would be exactly what the next throwaway/manual-test invocation reaches for, reproducing this same live-Plex-mutation-for-a-typo'd-username bug; testing against a genuinely new user is one `config.yml` edit away.

  New coverage in `tests/test_cli.py::TestRunRecommenderMain`: `test_single_user_mode_rejects_unconfigured_username` (exits 1, creates nothing, error names the configured users), `test_single_user_mode_unconfigured_username_creates_nothing` (same, with zero users configured at all), `test_single_user_mode_accepts_configured_username_case_insensitively`, and `test_single_user_mode_accepts_admin_keyword_even_if_unconfigured`.

## [2.10.78] - 2026-07-28

### Fixed

- **Nightly TV runs logged "Warning: Weights sum to 1.05, expected 1.0" every run whenever tv:/weights: in config/tuning.yml provided the 4 documented keys (genre/studio/actor/keyword, already summing to 1.0 on their own per config/tuning.example.yml) without an explicit language key.** recommenders/tv.py's PlexTVRecommender._load_weights() defaulted an omitted language weight to 0.05 unconditionally, silently reintroducing a 5th scoring dimension the config never asked for on top of an already-complete 4-key block - both the spurious warning and a real, uninvited language-matching contribution to every score.

  PlexMovieRecommender._load_weights() already got this right for movies (language defaults to 0.0 there, always) - TV was the sole outlier. Fixed by making the language default conditional on whether the caller supplied any explicit weights at all: a genuinely empty/absent tv:/weights: block (no config/tuning.yml, or tv: with no weights: sub-key) still gets curatarr's own baked-in default profile unchanged, which deliberately blends in a small 0.05 language weight as part of a 5-value set already designed to sum to 1.0 - but once any explicit weights are supplied, an omitted language key now defaults to 0, matching the movie engine's behavior. Every other key (genre/studio/actor/keyword) keeps its existing per-field default regardless, unchanged - those four are the documented core set config/tuning.example.yml expects users to tune as a unit; language is the one dimension deliberately left out of that documented block.

  Audited every other optional weight key in both engines for the same gap: none exists. genre/actor/director-or-studio/keyword are always part of the documented example weights: block for both movies: and tv: (the one set config/tuning.example.yml shows as "must sum to 1.0"); language is the only key either engine treats as an implicit bonus dimension excluded from that documented block, and it's now consistently zero-defaulted (movies always; TV whenever any explicit weights are given) in both.

  New coverage in `tests/test_tv.py::TestPlexTVRecommenderWeights`: `test_fully_default_weights_with_no_config_still_sum_to_one` (regression guard - a zero-config TV install must keep summing to 1.0 exactly as before), `test_omitted_language_in_explicit_config_defaults_to_zero` (reproduces the real 1.05 case and asserts it now sums to 1.0 with no warning), and `test_explicit_language_weight_in_partial_config_still_honored` (an explicitly-set language weight is never overridden).

## [2.10.77] - 2026-07-27

### Fixed

- **A Plex watch-history fetch failure was the one integration NOT governed by the `logging.verbosity` (off/quiet/verbose) setting #306 added.** `utils/plex.py`'s `fetch_plex_watch_history_movies()` per-account except clause was a bare `print(f" {RED}ERROR: {e}{RESET}")` - no `log_error()` call at all, unlike every other choke point #306 already routed through the level-gated logging module (Plex connection init, TMDB, Trakt, Simkl, the shared Sonarr/Radarr/Tautulli/MDBList client), and unlike this exact module's own sibling functions - `fetch_plex_watch_history_shows()` and `fetch_show_completion_data()` - which already called `log_error()`/`log_warning()` correctly for the identical class of failure.

  Fixed by replacing that `print()` with `log_error(f"Error fetching watch history for account {account_id}: {e}")`, naming the failing account (the sibling functions' messages don't - this one now does, for easier triage across multiple users). Stays visible at the default `quiet` level (and even at `off`, which maps to ERROR-only, not full silence) - a failure to fetch a user's watch history is exactly the kind of thing an operator must see, and it's the class of silent failure that hid a real six-month Trakt outage. No bare `print()` reintroduced.

  New regression coverage in `tests/test_plex.py::TestFetchPlexWatchHistoryMovies::test_per_account_fetch_error_routed_through_log_error_not_bare_print` - asserts `log_error` is called with the failing account ID, and that one account's failure doesn't abort the fetch for the rest.

## [2.10.76] - 2026-07-27

### Added

- **Collection naming templates (#274) surfaced in the web UI's Settings screen (#286).** `collections.movie_name_template`/`collections.tv_name_template` were only ever editable by hand-editing `config/tuning.yml` since #274 shipped them - the only remaining item on the original web-UI-parity queue.

  The existing default renders byte-identical to today's output for anyone who never touches this screen - the two new fields fall back to the exact same `utils.labels.DEFAULT_MOVIE_NAME_TEMPLATE`/`DEFAULT_TV_NAME_TEMPLATE` constants the recommender itself uses, not a re-typed copy of them (same #261-class guardrail as this project's other config defaults). Leaving a field blank on save means "use the default" - it's written back as that exact default constant rather than a literal empty-string template, matching `config/tuning.example.yml`'s own documented way to reset this (deleting the line).

  New `web/config_validate.py::validate_collection_template()` rejects an invalid template (unknown placeholder, malformed braces) outright with a visible inline field error, rather than mirroring `utils.labels.render_collection_name()`'s own runtime behavior (silently falls back to the default and logs a warning - by design, so a bad hand-edited `tuning.yml` can never crash a run). Same deliberate divergence this screen's `validate_weights_sum` already establishes: a UI-driven typo shouldn't be silently accepted only to fall back at every actual run with nothing but a log line to explain why.

  The screen also explains that the multi-library disambiguation suffix (e.g. " (Movies 4K)") is always appended after the template renders, so a user doesn't try to add it themselves.

  Field-scoped the same way every other section on this screen already is (#290): only `movie_name_template`/`tv_name_template` are read/written here - `collections.add_label`/`label_name`/`append_usernames`/`rename_on_template_change`/`private_collections` (same YAML section, but not owned by this screen) are left untouched. Audited `web/config_connections.py` and every other config screen first - none currently render an editable `collections.*` field, so there was no #290-class duplicate-field clobber risk to begin with here.

  New coverage: `tests/test_web_config_validate.py::TestValidateCollectionTemplate` (valid/invalid templates for both media types); `tests/test_web_config_settings.py::TestCollectionNaming` (default rendering matches the real constants byte-for-byte, custom templates persist, blank/omitted fields save the real default rather than an empty string, an invalid template is rejected with a visible error and never corrupts the existing file, and saving this screen never touches the other `collections.*` keys it doesn't own).

## [2.10.75] - 2026-07-27

### Fixed

- **`mdblist.yml`/`simkl.yml` were shipped as example configs but never actually loaded - a real config bug, same class as #261.** `utils/config.py::_load_module_configs()` looped over exactly `["trakt", "radarr", "sonarr"]`, so a user who copied `config/mdblist.example.yml`/`config/simkl.example.yml`, filled in a real API key/client ID, and set `enabled: true` was silently ignored - `recommenders/external_sync.py`'s `export_to_mdblist()`/`export_to_simkl()` always saw `config.get("mdblist", {})`/`config.get("simkl", {})` come back empty, then logged "check config/mdblist.yml"/"check config/simkl.yml" - telling the user to check the exact file they'd just filled in.

  Fixed by adding `mdblist`/`simkl` to the same module-file loop `trakt`/`radarr`/`sonarr` already go through, with the identical deep-merge precedence (a module file's keys win; sibling keys it doesn't mention survive). Both shipped examples default `enabled: false`, so this is a behavior change only for an install that already has one of these files with `enabled: true` sitting in `config/` - for whom the change is "the integration I explicitly configured starts working," not a surprise. Verified no such file exists on this install before merging.

  Also closes a documented half of #289: `MDBLIST_API_KEY`/`SIMKL_CLIENT_ID`/`SIMKL_ACCESS_TOKEN` previously only took effect for an install that embedded an `mdblist:`/`simkl:` section directly in `config.yml` itself (env-var override application was already unconditional, but had nothing to override on top of when the module file was never read). Now verified to win over a value the module file itself sets, same as every other integration.

  `_load_module_configs()` now also logs one summary line at load time ("Module configs merged: ..." or "No optional module config files found") so which module files were actually found and merged is visible, not just inferred from individual per-file lines.

  Removed `web/config_app.py`'s docstring note describing this as a deliberate, deferred gap, since it no longer exists. Left `mdblist`/`simkl` unexposed on a Settings/Connections screen for now (UI work, out of scope for this config-loading fix) - both are still config-file-only, same as before.

  New coverage in `tests/test_config.py::TestModularConfigLoading`: both files load and merge; both deep-merge into a pre-existing same-named section instead of replacing it outright; the three env-var overrides apply and win even when the corresponding module file also sets that key.

## [2.10.74] - 2026-07-27

### Added

- **A log verbosity setting - off/quiet/verbose - replacing the reporter's original twelve-category proposal with the high-value slice of it (#284).** Most of that original list (per-request web access logging, a config-change audit trail, per-call latency logging for every external call) would have recreated the `docker logs` firehose v2.10.50 already fixed, so it's deliberately out of scope here. What ships instead:

  New `logging.verbosity` (`config/config.example.yml`, default **quiet**, `LOG_VERBOSITY_DEFAULT`/`LOG_VERBOSITY_LEVELS` in `utils/display.py`), overridable via the `CURATARR_LOG_LEVEL` environment variable (env always wins - Docker operators expect this, matching Sonarr/Radarr/Plex's own convention). Both accept either a friendly tier (`off`/`quiet`/`verbose`) or a standard Python level name, mapped internally onto real logging levels rather than inventing a parallel severity system: `off` -> ERROR (near-silence, errors only), `quiet` -> INFO (the same level this app already defaulted to - `quiet` is a rename, not a behavior change, for anyone who touches nothing), `verbose` -> DEBUG (surfaces the existing `logger.debug()` call sites throughout the codebase - per-item filtering decisions, discovery iterations, cache hits). The pre-existing raw `logging.level` key (DEBUG/INFO/WARNING/ERROR, already shipped and exposed in the web UI's Settings screen) is untouched and still takes precedence if explicitly set - this is additive, never a replacement, so nothing anyone already relies on changes.

  At the quiet default, four things are now genuinely visible that weren't before:
  1. **External API failures, surfaced immediately** - the single most valuable item, and the exact gap that let a real Trakt outage stay invisible for six months: a bad/expired token, or a connection/timeout failure, is now logged at the one shared choke point every request to Plex (`init_plex`), TMDB (`fetch_tmdb_with_retry` - deduplicated to once per process so a per-item lookup called hundreds of times per run can't turn this into its own firehose), Tautulli/Sonarr/Radarr/MDBList (`BaseAPIClient._handle_response`/`_make_request_to_url` - one fix, four integrations), Trakt, and Simkl goes through - never silently swallowed by whichever caller happens to catch the resulting exception.
  2. **Recommender run lifecycle** - start/completion/failure, per engine (movie/tv/external) and per user, with final counts (`recommenders/movie.py`, `recommenders/tv.py`, `recommenders/external.py`, `utils/cli.py`'s shared `run_recommender_main`).
  3. **Scheduler confirmations** - already shipped (the in-app scheduler already logged a clear "started scheduled 'full' run" line, or why it was skipped/failed) - verified, not re-implemented.
  4. **Unhandled errors** - `web/app.py`'s generic exception handler now logs a structured line (component, route, method, stack trace - timestamp comes from the log formatter itself) alongside the `curatarr_unhandled_errors_total` metric it already recorded, so an error visible in that metric can actually be traced back to a cause instead of just counted. `utils/cli.py`'s equivalent CLI-side top-level handler gets the same treatment.

  Never logs a secret's value anywhere in any of this - every new line either carries no secret-shaped content at all, or goes through the existing `log_error`/`log_warning` redaction path (`utils/redact.py`) the same as every other log line in this codebase. No bare `print()` was reintroduced; every new line goes through the logging module so `off`/`quiet`/`verbose` actually controls it.

  New unit coverage: `tests/test_display.py::TestResolveLogLevel`/`TestSetupLoggingVerbosity` (full precedence order, case-insensitivity, unrecognized-value fallback-with-warning); `tests/test_config.py::TestConfigExampleLoggingDefaultsMatch` (the documented default matches the code default - same #261-class guardrail as this project's other config defaults); `tests/test_api_client.py::TestExternalApiFailureLogging`, `tests/test_trakt.py`, `tests/test_simkl.py`, `tests/test_tmdb.py::TestFetchTmdbWithRetry` (401/error/timeout/connection-failure logging, secret-never-logged, the TMDB once-per-process dedup); `tests/test_web_metrics.py::TestUnhandledErrorMetric` (structured line present, redacted).


## [2.10.73] - 2026-07-27

### Added

- **Every integration's secrets can now be set via environment variable, the same convenience `PLEX_URL`/`PLEX_TOKEN`/`TMDB_API_KEY` already offered (#289).** New: `TAUTULLI_API_KEY`, `SONARR_API_KEY`, `RADARR_API_KEY`, `TRAKT_CLIENT_SECRET`, `TRAKT_ACCESS_TOKEN`, `TRAKT_REFRESH_TOKEN`, `SIMKL_CLIENT_ID`, `SIMKL_ACCESS_TOKEN`, `MDBLIST_API_KEY` - an environment variable always takes precedence over whatever's saved on disk, and config files keep working completely unchanged for anyone who doesn't use this. This is a convenience for operators using Docker secrets or an orchestrator's own secrets management (mounting a value as a file and exporting it, a Kubernetes Secret, etc) - it is **not** a secrets-manager replacement, and Curatarr still doesn't encrypt anything at rest either way.

  `utils.config.ENV_VAR_OVERRIDES` is now the single, shared list every override reads from (`load_config()` to apply them, and the web UI to check them) rather than two independently-hand-written lists that could silently drift apart. That mattered here: the Connections screen's "configured"/"not set" status was computed purely from what's on disk, with no awareness of the environment at all - so an install already using `PLEX_TOKEN`/`TMDB_API_KEY` this way would have shown a misleading "not set" for a field that's actually fully working. Fixed for every secret this PR covers via new `web.config_io.secret_status_with_env`.

  New unit coverage: `tests/test_config.py::TestLoadConfig` (table-driven over every entry in `ENV_VAR_OVERRIDES`, so a future addition is automatically exercised; confirms the env var is logged by name but the value never appears in the log) and `TestGetEnvOverride`; `tests/test_web_config_io.py::TestSecretHelpers` and `tests/test_web_config_connections.py::TestEnvVarSecretStatus` (env-only configuration shows "configured", never the raw value, in both the internal view dict and the rendered page). Documented in every relevant `config/*.example.yml`, `README.md`, and a new `docs/DOCKER.md` section with a `docker-compose.yml` example.

  `mdblist.yml`/`simkl.yml` have a separate, pre-existing, already-documented gap (`web/config_app.py`'s own docstring): `utils.config._load_module_configs()` doesn't merge those two files into the running config at all yet (only `trakt.yml`/`radarr.yml`/`sonarr.yml` are), so `MDBLIST_API_KEY`/`SIMKL_CLIENT_ID`/`SIMKL_ACCESS_TOKEN` only take effect today for an install that embeds an `mdblist:`/`simkl:` section directly in `config.yml` itself. Registering the override doesn't make that pre-existing gap any worse, and closes it automatically whenever that loader gap is fixed - left as-is here rather than bundled into a PR about environment variables, not module loading.

## [2.10.72] - 2026-07-27

### Added

- **Opt-out (not opt-in) skip of the Recommended collection for zero-watch-history users, plus removal of any already-existing one (#291).** Cold-start is a solved problem in recommenders, and the standard answer is falling back to well-rated/popular unwatched items, not producing nothing - a user who opens Plex and sees no collection at all concludes the app is broken. So the default stays exactly what it's always been: `movies.recommend_for_no_history` / `tv.recommend_for_no_history` (`config/tuning.example.yml`, default **true**, `RECOMMEND_FOR_NO_HISTORY_DEFAULT` in `utils/config.py`) means a zero-history user still gets a collection, no behavior change for anyone who doesn't touch the setting.

  Set either key to `false` to opt out instead: `BaseRecommender.get_recommendations()` then skips building a collection for a user with genuinely zero watch history (never for a user with any history at all, even a single watched item), logs a clear line naming the user and the reason, and additionally **removes** any collection curatarr already created for that user. That removal (`BaseRecommender._remove_collection_for_no_history` / new `utils.plex.remove_owned_collection`) only ever targets a collection it can prove it created, via the `PrivateCollection_<user>` label already applied by `manage_plex_labels()`/`update_plex_collection` - the same ownership marker #274's rename-on-template-change work trusts - never inferred from title, emoji, or name pattern. That label is applied independent of whether `collections.private_collections` itself is enabled (that setting only gates the cross-user Plex exclude-filter, never the label - see `update_plex_collection`'s own docstring), so ownership can be confirmed either way; if `collections.add_label` is disabled, curatarr never applied the label in the first place, so this path always no-ops rather than guessing. If more than one collection ever carries the same label, ownership is ambiguous and nothing is removed. Every removal (and every case where removal was skipped for being unconfirmed/ambiguous) is logged at a visible level - never silent, even when this is configured on purpose.

  New coverage: `tests/test_base.py::TestRecommendForNoHistoryGate` (default-on creates as today; explicit off skips and triggers removal for a zero-history user; a user with any history is neither skipped nor has anything removed) and `TestRemoveCollectionForNoHistory` (label computation, `add_label`-disabled no-op, defensive Plex-access handling); `tests/test_plex.py::TestRemoveOwnedCollection` (confirmed-by-label removal, un-labeled/ambiguous collections are left alone and logged, one user's removal never touches another's, delete/list failures are logged not raised). `config/tuning.example.yml`'s documented default is cross-checked against `RECOMMEND_FOR_NO_HISTORY_DEFAULT` by `tests/test_config.py`'s existing guardrail class (the same #261-class mismatch this class exists to catch).

- **Real-data investigation of #291's cold-start case turned up a second, more consequential bug: every candidate scores exactly 0.0 against a zero-history profile, and with no secondary sort key, a stable sort collapses ties to media-cache insertion order - which is alphabetical by title.** A zero-history user was getting the first ~50 titles of their library alphabetically, labeled "Recommended", with none of the library's best-rated titles making the cut. Fixed by adding a `(rating, vote_count)` tiebreaker, None-safe, at every point recommendations get sorted by score: `BaseRecommender.get_recommendations()`'s primary sort, `BaseRecommender._update_labels_by_rank`'s incremental re-rank (looks the tiebreak fields up from the media cache by item id, since the `(plex_item, score)` tuples it sorts don't carry them directly), and `utils.scoring.select_tiered_recommendations`'s own final re-sort (this one matters even when the primary sort is already tiebroken, since the tiered safe/diverse/wildcard selection above it slices and samples in a way that doesn't preserve input order). This also incidentally fixes a related concern raised alongside it: with `randomize_recommendations` at its default of `true`, tier selection was drawing its "safe" (top) pool from the same alphabetical-tie ordering, making the cold-start collection arguably worse than plain alphabetical; once the primary sort is tiebroken, that "safe" pool is genuinely the best-rated slice instead.

  New coverage: `tests/test_scoring.py::TestSelectTieredRecommendations` (a tied/all-zero-score set orders by rating then vote count); `tests/test_base.py::TestGetRecommendationsBranches::test_tied_scores_ordered_best_rated_first` and `TestUpdateLabelsByRank` (tied scores broken by rating/vote_count, including an item missing from the cache falling back to zero rather than raising).

## [2.10.71] - 2026-07-27

### Fixed

- **`migrate_legacy_cache_dir()` could silently destroy a user's real cache - confirmed data loss, not theoretical.** `recommenders/base.py`'s `BaseRecommender.__init__` unconditionally calls this on every recommender construction. Its `legacy_dir` argument is ALWAYS the real source checkout's own `cache/` directory (computed from `__file__`, never from config or `CURATARR_CONFIG_DIR`), while the destination honors `CURATARR_CONFIG_DIR`. It used `shutil.move()` - any process that set `CURATARR_CONFIG_DIR` to a directory not already holding these cache files (a scratch dir, a test harness, someone experimenting, a misconfigured environment) silently *relocated* - not copied - the real install's cache files there, with only a `log_info()` line (easy to miss, and gated behind whatever logging level happened to be configured) marking it. Deleting that destination afterward destroyed the data permanently, with no backup.

  Now **copies** (`shutil.copy2`, preserving mtime/permissions) instead of moving - the source checkout's `cache/` is never modified by this function, period, regardless of what later happens to the destination. The pre-existing per-file `if os.path.exists(new_path): continue` check (already there to avoid clobbering a destination file) is also what keeps a copy-based migration from repeating itself forever: once copied, a file exists at the destination, so every later call skips it - no separate completion marker needed, and since the source is never deleted, nothing about that check can regress into a delete.

  **Made loud**: switched from `log_info()` to `log_warning()` (visible by default, colored) AND added a direct `print()` - a silent relocation was the core defect, so visibility can't depend on log-level configuration or scrolling back through a log file. The message states exactly what was copied, from where, to where, and how many files.

  Did **not** restructure `BaseRecommender.__init__` to move this out of the constructor in this PR (flagged, not fixed - a constructor mutating the filesystem as a side effect is a separately surprising design worth revisiting, but a bigger change than a copy-not-move data-loss fix should also carry).

  Audited other file-removal/rename call sites in `utils/user_migration.py` (renamed-user cache migration) and `utils/cache_prune.py` (orphaned-cache cleanup) for the same class of bug (a source path resolved one way, a destination resolved a genuinely different way) - neither exhibits it: both operate entirely within one already-consistently-resolved `cache_dir`, never across two independently-resolved directories, and `cache_prune`'s deletion is additionally gated behind an explicit `dry_run: false` opt-in (default `true`).

  **Verified in a real container** (synthetic cache files only - never touched the real repo's own `cache/`, per this fix's own lesson): planted two cache files at the real legacy path (`/app/cache` inside the image), ran the migration against a scratch destination, confirmed both the source and destination held the files afterward, then deleted the scratch destination entirely - the legacy source files remained fully intact with their original content, exactly reproducing (safely) the scenario that previously caused permanent data loss.

  New/updated regression coverage in `tests/test_helpers.py::TestMigrateLegacyCacheDir`: the core assertion (source files still exist after migration, regardless of the destination); idempotency (a second call never re-copies over or clobbers a destination file a real run has since updated); loud logging (both the `log_warning()` message and the direct `print()` mention the filenames and both paths); a copy failure still can't touch the source.

## [2.10.70] - 2026-07-27

### Fixed

- **Web UI status badge reported "success" on a real failure (#292), and the log viewer couldn't reach the start of a long log (#283) - same `TAIL_BYTES` truncation behind both.** `web/status.py`'s `get_last_run_status()` used to infer success/failure entirely by grepping the last 200KB of whichever log file `latest_user_log()` picked for three fixed English substrings ("traceback (most recent call last)", "fatal error", "an error occurred"). Two confirmed, real failure modes: (1) a failure logged any other way (e.g. `recommenders/external_sync.py`'s `log_error(f"Failed to export {user} to Trakt: {e}")`, no traceback dump) reads as success - the exact shape that let a Trakt auth failure report success for six months before `utils/integration_status.py` (v2.10.54) covered that one specific case; (2) `movie.py`/`tv.py` both write to the identical `recommendations_<user>_<timestamp>.log` naming, so the newest-by-mtime pick can show one engine's outcome while masking a different, older failure from the other.

  **Stopped inferring status from English prose.** New `utils/run_status.py` (same explicit, structured, atomic-write shape as `utils/integration_status.py`): `recommenders/movie.py`'s and `tv.py`'s `process_recommendations()` and `recommenders/external.py`'s per-user loops now record their own real observed outcome - did processing for this user raise at all - immediately after each (engine, user) pair finishes, independent of whether `movie.py`'s own fatal-keyword check additionally decides to `sys.exit()` over it. `get_last_run_status()` now reads this back directly per user, comparing each engine's own recorded timestamp (never file mtime) to resolve which is newest - fixing failure mode 2 as a side effect. Falls back to the legacy log-tail heuristic only when neither engine has ever recorded a status for a user yet (an install predating this fix, or a run from before it shipped) - every already-written log on every existing install has no recorded status to prefer, so the fallback is permanent, not a migration step.

  `/status.json` now also includes `last_job`: the most recent (or in-progress) web-UI-triggered job's full per-stage breakdown (`stage_results`/`external_produced_output` - #282/#288, v2.10.64) - a "succeeded" job-level exit code and a "failed" per-user `last_run` are now both visible together instead of only one or the other.

  **Log viewer (#283):** new `read_log_full()` (up to `LOG_VIEW_MAX_BYTES` = 50MB, a memory-safety backstop well above `cleanup_old_logs()`'s ~20MB retention target) alongside the existing `read_log_tail()` (still the default, last 500 lines). `/results/log/<file>?view=full` and a "Show full log" / "Show last 500 lines" toggle link on the log-view page.

  **Verified in a real container**: triggered a real `external` run whose per-user processing raised inside the actual subprocess pipeline - `/run/status`/`last_job` correctly showed the job itself `succeeded` (exit 0, matching the exact "silently reports success" shape this issue described), while `/status.json`'s `last_run` and the dashboard correctly showed that user as `failed`, with the real exception message surfaced as the badge's tooltip - the accurate signal the job-level exit code alone can't express. Confirmed the log-view toggle serves the full file vs. the last-500-line tail correctly for a real generated log.

  New regression coverage in `tests/test_web_status.py` (explicit signal overrides conflicting log content in both directions; cross-engine timestamp comparison resolves which of movie/tv is newest; fallback to the legacy heuristic when no explicit signal exists; `read_log_full()`'s truncation/redaction/traversal-safety behavior) and `tests/test_movie.py`/`test_tv.py`/`test_external.py` (record_run_status called with the right engine/user/outcome).

## [2.10.69] - 2026-07-27

### Fixed

- **Settings and Connections silently clobbered each other's Sonarr/Radarr/Trakt sync-safety fields (#290).** `/config/settings` and `/config/connections` both rendered live editable inputs for the same fields (`sonarr_auto_sync`/`user_mode`/`plex_users` and the radarr/trakt equivalents) and both wrote all of them unconditionally on save, from whatever THEIR OWN form last showed - saving either page silently reverted any change made on the other, with both still showing "Saved." either way, no warning, no log line. Since these fields gate real writes to a user's Sonarr/Radarr/Trakt instances, this was treated as higher severity than a cosmetic UI bug.

  Made Connections the sole writer (it already had "more context" - each field sits directly below that service's own URL/API key, matching the module's own pre-existing docstring) and Settings display-only: `web/config_settings.py`'s `_apply_settings()` no longer touches `sonarr.yml`/`radarr.yml`/`trakt.yml` at all (dropped those three params/writes entirely, and dropped them from the modules it commits), and `web/templates/config_settings.html`'s Export Safety fieldset now shows the current on-disk value as plain text with a link to Connections, instead of `<input>`/`<select>` elements - this is the approach that cannot silently lose a setting: Settings' own `<form>` structurally has no field named e.g. `sonarr_auto_sync` for a submission to ever carry, rather than relying on save-time scoping logic to get this right every time.

  **Verified in a real container**: saved distinct sync-safety values via Connections over real HTTP (`sonarr.auto_sync: true`, `user_mode: combined`), then saved Settings' own unrelated fields - `sonarr.yml` was byte-identical afterward, and the Settings page correctly showed the Connections-saved values read-only. New regression coverage in `tests/test_web_config_settings.py::TestSaveNeverTouchesSyncSafety`: a Settings save leaves `sonarr.yml`/`radarr.yml`/`trakt.yml`'s mtimes unchanged; the full clobber scenario (save Connections, then Settings, assert Connections' values survive); and the Settings screen never renders an input/select named for any of these fields.

## [2.10.68] - 2026-07-27

### Fixed

- **Removed dead "keywords" / "tmdb_keywords" dual-key compatibility fallbacks (#273 PR4, final PR of the #273 remediation sequence).** `utils/scoring.py`'s `calculate_similarity_score()`/`normalize_user_profile()` (4 call sites) and `recommenders/external.py`'s `discover_popular_by_genre()`/`_build_profile_via_recommender()` (2 call sites, both added by #273 PR3) all defensively accepted a profile keyed either `"keywords"` (this codebase's scoring-layer convention) or `"tmdb_keywords"` (the raw `watched_data_counters`/on-disk cache storage key `utils/counters.py`'s `create_empty_counters()` uses).

  Audited every real caller of these functions - `recommenders/movie.py`'s and `recommenders/tv.py`'s own `_calculate_similarity_from_cache()`, `recommenders/external.py`'s `load_user_profile_from_cache()`/`_build_profile_via_recommender()`/its own TMDB-candidate `content_info` construction, and `tests/harness.py`'s own scoring-harness fixture - every single one already, always, translates `"tmdb_keywords"` (the storage-layer name) to `"keywords"` (the scoring-layer name) before ever calling into `calculate_similarity_score()`. The `"tmdb_keywords"` fallback branch was therefore dead code no real caller had ever actually exercised - confirmed directly: two of `tests/test_scoring.py`'s existing tests had been constructing profiles keyed `"tmdb_keywords"` specifically to test this fallback, and a third's assertion (a score-capping ceiling check) had silently stopped exercising its keyword-matching arm entirely without ever failing. All three updated to use `"keywords"` (their own tests' original intent), along with one `tests/test_external.py` test that exercised the now-removed `discover_popular_by_genre()` fallback.

  This does NOT touch the storage-layer convention at all - `create_empty_counters()`, `process_counters_from_cache()`, and every real `watched_cache_plex_<user>.json`/`tv_watched_cache_plex_<user>.json` on disk still use `"tmdb_keywords"` exactly as before; only the scoring-layer boundary's now-provably-unreachable tolerance for that same name was removed.

  **Verified against the real, live Plex library** (read-only: no Plex writes, no Trakt calls): re-scored all 161 real unwatched-movie candidates for a real user against their real, freshly-built watched profile using the current (post-PR4) code - 50 candidates still received a non-zero keyword-score contribution (confirming `"keywords"`, the one surviving key, still matches real TMDB keyword data correctly end to end), and two consecutive scoring passes produced byte-identical results (determinism unaffected). Zero behavioral delta was predicted for this PR (a pure dead-code removal, unlike PR1/PR2/PR3's genuine behavior changes) and none was observed.

## [2.10.67] - 2026-07-27

### Fixed

- **Web UI freeze under load - P0 (#287).** Reporter's own diagnosis (a late SSE subscriber to an already-finished job never receiving `done` and parking forever) turned out to be already-correct, existing behavior - verified directly in a real container: connecting to `/run/stream` after a job has already finished replays its backlog and closes within milliseconds, exactly as `Job.subscribe()`'s pre-existing `if self.returncode is not None: queue DONE_SENTINEL immediately` branch is supposed to. The freeze itself was real and reproduced independently: waitress dispatches a streaming (SSE) WSGI response to ONE task thread for the connection's ENTIRE lifetime (confirmed against `waitress/task.py`'s own source - it synchronously iterates the response generator on that one thread), and `THREADS = 8` (`web/docker_server.py`) is explicitly sized "for one open SSE live-log stream" per its own comment. Confirmed in a real container: opening as few as 8 concurrent, perfectly legitimate (not stuck, not misbehaving) `/run/stream` connections during one still-running job completely exhausted the pool - a subsequent `/run/status` request (a trivial JSON read) then hung indefinitely until a stream closed, exactly matching the reporter's `docker stats`/`/proc/1/task` findings and "significantly worse when the script is running."

  Three changes, all in `web/app.py`/`web/job_runner.py`/`static/app.js`:
  1. `run.html`/`app.js` now only open an `EventSource` when a job is genuinely `running` (`window.CURATARR_JOB_RUNNING`, replacing the old `window.CURATARR_HAS_JOB`, which was true for any job record at all, including one that finished hours ago) - unnecessary given point 1 above already worked, but still pointless connection churn worth not doing.
  2. New `Job.try_subscribe(max_subscribers)` (the same lock-protected path `subscribe()` itself now calls through to with no cap) lets `/run/stream` cap concurrent viewers of the SAME running job at `MAX_STREAM_SUBSCRIBERS_PER_JOB = 4` - only one job ever runs at a time, so every viewer beyond that is a fully redundant stream of identical output, each pinning one more of only 8 threads. A viewer over the cap gets a new `busy` SSE event instead of a connection that would just make the exhaustion worse; `app.js` falls back to polling `/run/status` for that tab instead.
  3. `run_stream()`'s `generate()` now bounds any single connection to `MAX_STREAM_SECONDS = 120.0` regardless of how long the job itself takes, closing (not erroring) once reached - `EventSource`'s own default auto-reconnect behavior (never overridden by an `onerror` handler that calls `.close()` - deliberately removed) picks the stream back up a few seconds later, replaying the backlog exactly like any other new subscriber.

  Deliberately did not blindly raise `THREADS` - a bigger pool only raises how many concurrent viewers it takes to reproduce the same freeze, it doesn't remove the underlying one-thread-per-open-stream design; capping concurrent viewers per job (point 2) and bounding how long any one of them can hold a thread (point 3) address the capacity problem directly instead.

  **Verified in a real container**: reproduced the freeze pre-fix (8 concurrent live streams during one running job → a fresh `/run/status` request hung indefinitely, recovered only once enough streams were closed); post-fix, opening 10 concurrent streams during one running job yields exactly 4 real streams and 6 immediate `busy` events, and `/run/status` stays responsive (~5ms) throughout. New regression coverage in `tests/test_web_job_runner.py` (`Job.try_subscribe()` cap enforcement, and that a finished job's `subscribe()` is never capped) and `tests/test_web_routes.py` (`/run/stream` emits `busy` once over the cap and never registers the rejected connection as a subscriber; a connection is proactively closed - without a `done` event - once `MAX_STREAM_SECONDS` elapses, and the job itself is unaffected).

## [2.10.66] - 2026-07-27

### Fixed

- **`recommenders/external.py`'s `build_user_profile()` had a fatal, unfixable-in-place bug (#273 PR3): its `username` parameter had zero effect on the output** - it always scanned whatever `plex` connection the caller already had (the one shared admin-token connection every caller in this file uses), never `username`'s own. Deleted entirely and replaced with `_build_profile_via_recommender()`, which constructs the real `PlexMovieRecommender`/`PlexTVRecommender` for `username` directly - the same "shared path" `recommenders/movie.py`'s and `recommenders/tv.py`'s own watched-data builders already use (and already benefit from #273 PR1's/PR2's fixes). As a side effect, this also persists a real, correctly-weighted watched-cache file to disk on first use, so `load_user_profile_from_cache()` finds it on the very next call instead of paying the same slow full-Plex-scan cost forever (`build_user_profile()`'s own docstring already called itself out as slow).

  `is_thin_profile()` and `discover_popular_by_genre()`'s genre/keyword iteration both called `.most_common()` directly on `user_profile["genres"]`/`user_profile["keywords"]`, assuming the caller always handed over `Counter` objects keyed exactly `"genres"`/`"keywords"` - true for `load_user_profile_from_cache()`'s own return shape, but not guaranteed for every caller (in particular, a caller handing over `watched_data_counters` in its raw, `"tmdb_keywords"`-keyed, not-necessarily-`Counter`-typed shape directly). Both now coerce to `Counter` if not already one, and the keyword lookup accepts either `"keywords"` or `"tmdb_keywords"`.

  **Verified against the real, live Plex library** (read-only: only real Plex history/library-listing calls, no writes, no Trakt calls): `_build_profile_via_recommender()` is only ever reached as a fallback when no watched cache exists yet for a user/media type - already true for every one of this install's 6 real configured users, so there's no meaningful before/after to observe in normal use. Instead verified the function's previously-broken core behavior directly: two different real users (`ericarutyunov`, `homehouse165`) now get genuinely different profiles - 27 and 15 `tmdb_ids` respectively, exactly matching each user's own real watched-movie count already independently established in #273 PR1's verification (`ericarutyunov`: 27, `homehouse165`: 15) - both `Counter`-typed and both correctly under a `"keywords"` key, not `"tmdb_keywords"`.

  While verifying, found (but did not fix - out of this PR's scope) a pre-existing, unrelated test-isolation gap: `recommenders/external_sync.py` resolves its own cache directory via a `get_project_root()` binding conftest.py's `_isolated_recommender_cache_dir` autouse fixture doesn't patch (it only patches `recommenders.base`'s and `recommenders.external`'s own bindings), so any test exercising its Trakt-export-status recording writes a real (small, harmless) `cache/integration_status_trakt_export.json` into the actual repo instead of an isolated tmp dir.

## [2.10.65] - 2026-07-27

### Added

- **GitHub Sponsor button (`.github/FUNDING.yml`) with a Ko-fi entry, plus a short "Support" note in the README.** Ko-fi only for now (other platforms undecided). Donations don't buy features, priority support, or roadmap influence - the README says so explicitly.

## [2.10.64] - 2026-07-27

### Fixed

- **Docker `full` run (#282): TV/external recommendations never ran, with zero indication why.** The web UI's `full` engine, in Docker, chains `movie.py -> tv.py -> external.py` in one `bash -c "cmd1 && cmd2 && cmd3"` invocation (`web/job_runner.py`, since v2.10.56/#277). Verified in a real container against the real production recommenders: that chain already ran every stage correctly whenever each one exited 0, and correctly stopped at whichever stage failed - the same fail-fast behavior `docker-entrypoint.sh`'s own `recommend full` has under `set -e` (also independently verified in a real container: it does not run every stage unconditionally either). The actual defect was that a stage being skipped because an earlier one failed was completely silent - no banner in the log (unlike `docker-entrypoint.sh`'s own `=== X ===` lines) and nothing in `Job.to_dict()` beyond one overall `state`/`returncode` - so a movie failure correctly skipping tv/external was indistinguishable, from `/run` and `/run/status`, from tv/external having been dropped for no reason at all.

  Replaced the bare `&&` chain with an explicit, per-stage script (`_build_docker_full_script`) that keeps the exact same semantics (movie failing stops tv and external; tv failing stops external; the script's own exit code is whichever stage failed) but adds an `=== X ===` banner per stage - including an explicit `(skipped: ... failed)` banner for whichever stage(s) never ran - plus a machine-readable `__CURATARR_STAGE__:<stage>:<returncode-or-skipped>` marker line that `_pump()` parses into a new `Job.stage_results` dict, now exposed via `/run/status`/`/status.json` and rendered on the Run page as a per-stage breakdown.

- **`/results` and `/run` never distinguished "succeeded" from "succeeded but produced nothing" (#288).** Confirmed directly in a real container: `recommenders/external.py` can catch a per-user exception internally, log it, and still print its own "Watchlists saved to: ..." success line having written nothing new - exit code 0, `state: succeeded`, and `/results` showing the same generic "No watchlists generated yet" it shows before anyone has ever run anything at all. Added `Job.external_produced_output` (`JobManager._check_external_output`): after a `full`/`external` run whose external stage exited 0, checks whether `recommendations/external/` actually gained a file newer than the run's own start time. `/results` and `/run` now show a specific message distinguishing three cases instead of one generic one: external skipped because an earlier stage failed, external itself failed, or external "succeeded" but wrote nothing - each pointing at the run log. Deliberately does not second-guess `external_recommendations.enabled: false` (tuning.yml, #271) as a cause - a deliberately disabled stage legitimately produces no new file, which is not a bug.

  New regression coverage in `tests/test_web_job_runner.py`: the full chain invokes all three stages and records `stage_results` when everything succeeds; a movie failure stops tv/external with `stage_results` showing `skipped`; a tv failure stops external the same way; `external_produced_output` is `True`/`False`/`None` (not applicable) in the write/no-write/failed cases respectively.

## [2.10.63] - 2026-07-27

### Fixed

- **`recommenders/base.py`'s `_get_managed_users_watched_data()` (used when `plex.managed_users` is configured instead of `users.list`) applied NO weighting at all - every watched item counted exactly 1.0, regardless of how many times it was rewatched, how the user rated it, or how recently it was watched (#273 PR2).** Verified: every real managed-users-mode profile's counter values were plain integers with zero negative signals, unlike `movie.py`'s/`tv.py`'s own per-user builders, which already apply recency/rating/rewatch weighting. Now calls the exact same formula those two use (`calculate_recency_multiplier` x `_calculate_rating_multiplier` x `calculate_rewatch_multiplier`, sourced from the same `switchUser()`-scoped library item this method already fetched via `#273` PR1's per-user token fix), including negative-signal support for low ratings.

  No config flag - unlike PR1, this is a straightforward internal-consistency fix (apply the SAME weighting formula the other three builders already use to this fourth one), not a new opt-in behavior with a slower-rollout rationale.

  **Verified against the real, live Plex library** (read-only): this install's real users aren't Plex Home/managed-users (they're configured via `users.list`, matched by login-style username - `get_configured_users()`'s `managed_users` validation only matches Plex's display-name `.title`, a separate, pre-existing mismatch out of this PR's scope), so this was verified via the one `managed_users` value that always resolves regardless (`"admin"`), isolating the comparison to weighting alone by patching only `process_counters_from_cache`'s call shape between an "old" and "new" pass over the exact same real watched-item set (132 watched movies both ways, confirming an isolated comparison): every counter dimension went from 100% integer-valued with zero negative signals (`genres`: min 1.0, 0 negative; `actors`: min 1.0, 0 negative; `directors`: min 1.0, 0 negative; `tmdb_keywords`: min 1.0, 0 negative) to widely fractional with real negative signals (`actors`: 271/293 non-integer, 12 negative; `directors`: 106/117 non-integer, 5 negative; `genres`: 23/24 non-integer; `tmdb_keywords`: 1273/1383 non-integer, 88 negative) - exactly the predicted shape, written down before the run.

## [2.10.62] - 2026-07-27

### Added

- **Per-user profile accuracy fix for movie/TV recommendations (#273 PR1), behind a new `profile_accuracy.enabled` config flag (default `false`).** Issue #273's audit found that `recommenders/movie.py`'s and `recommenders/tv.py`'s watched-data builders both read `viewCount`/`userRating` off ONE shared, admin-token library snapshot (`BaseRecommender._get_all_library_items()`) regardless of which configured user's profile was being built - `viewCount`/`userRating` are per-account Plex state, so the admin's own token can only ever see the admin's own values for them, never another user's. Movies had a second, independent bug on top of that: `_get_plex_watched_data()` only ever read `userRating` off Plex history items (`/status/sessions/history/all`), which - verified against 2,475 real history entries across 6 real accounts - never actually carries that attribute at all, so a disliked movie could never produce a negative signal, for any user, ever (TV was never affected by this second bug - `tv.py` already read `userRating` off the library item correctly).

  Added `recommenders/base.py`'s `BaseRecommender._get_all_library_items_for_user(username)`: switches to that user's own Plex connection (`switchUser`) before fetching their library snapshot, short-circuiting to the existing shared admin snapshot for the admin account itself (nothing to switch to) or on any Plex error (falls back rather than failing the run). `movie.py`'s `_get_plex_watched_data()` and `tv.py`'s `_get_plex_watched_shows_data()` both use it - and, for movies, also read `userRating` off the returned library items instead of history - only when `profile_accuracy.enabled: true`; the flag absent or `false` (every existing install, unconditionally) keeps every byte of the legacy behavior, verified via a new test in each file asserting the per-user fetch is never even called by default.

  Off by default because enabling this changes existing users' actual recommendations (previously-invisible dislikes can now suppress similar content) and forces a one-time full profile rescore on the next run after enabling it (the profile hash changes) - a deliberate one-time cost, not a bug.

  **Verified against the real, live Plex library** (read-only: `switchUser`/library-listing/watch-history calls only, no Plex writes, no Trakt calls) across all 6 real configured users, comparing a fresh flag-off recompute against a fresh flag-on recompute of the same live watched-item set (never a stale on-disk cache, which would confound the comparison with unrelated watch-history drift): movie top-10 recommendation overlap changed for every one of the 6 users (8/10, 8/10, 7/10, 6/10, 3/10, 1/10 - the same distribution this issue's own original diagnosis had already reported, reproduced independently here), and the admin account's own movie profile picked up 12 negative-signal counters that were always exactly 0 before, confirming the rating-source fix fires even for the one account that can never benefit from the per-user-token fix (switching to yourself is a no-op). TV negative-signal *counts* were unchanged for all 6 users (expected - `tv.py` never had the rating-source bug, only the token-scoping one), while TV top-10 overlap still shifted for 2 of 6 users from the scoping fix alone.

- **`profile_accuracy` added to `utils/migrate_config.py`'s `CORE_SECTIONS`.** Found while building the real-library verification above: a config still using the legacy single-library format (`plex.movie_library`/`tv_library`, no explicit `libraries:` list - most fresh installs) auto-migrates to modular config files on its very next run, and `build_core_config()` silently drops any root `config.yml` key not in this explicit whitelist - exactly the same gap `schedule` hit in v2.8.31 (#264). Without this fix, a user who set `profile_accuracy.enabled: true` on a not-yet-migrated install would have it silently reverted to the default the moment migration ran, with no error or warning. New `tests/test_migrate_config.py::TestCoreSectionsIncludesProfileAccuracy` mirrors the existing `schedule` regression test for this exact shape of bug.

## [2.10.61] - 2026-07-27

### Added

- **Profile-builder harness (#273 PR0) - hard gate before any of #273's production-code fixes.** Issue #273 found FOUR divergent user-profile builders - recommenders/movie.py's `_get_plex_watched_data()`, recommenders/tv.py's `_get_plex_watched_shows_data()`, recommenders/base.py's `_get_managed_users_watched_data()`, and recommenders/external.py's `build_user_profile()`/`load_user_profile_from_cache()` - three with verified production bugs. None of that sequence's downstream PRs are verifiable without a harness that can actually catch those bugs first.

  `tests/e2e_plex_fixture.py`'s existing catalog **could not** catch any of them as shipped: it emits watch-history XML with no `userRating` (faithfully reproducing the real Plex `/status/sessions/history/all` endpoint, which never carries it either), and its `FakeMediaItem` had no `userRating` attribute at all, no per-user `viewCount` variation, and no `switchUser()`/per-user-token support. Extended it with `MOVIE_PER_USER_LIBRARY_STATE`/`SHOW_PER_USER_LIBRARY_STATE` (two users, alice and bob, with genuinely different nonzero `view_count`/`user_rating` values on items they've each actually watched), `FakePlexServer.switchUser()`, `FakeMyPlexAccount.user()`, `build_fake_plex_server_for_user()`, and `FakeSection.search(unwatched=...)` support (for `base.py`'s managed-users path) - without this, a harness built on the fixture as it shipped would have pinned the bugs as correct behavior, not caught them.

  `tests/harness.py` (previously scoring-only) gained `run_profile_builders()`, driving all four builders directly against that extended fixture and snapshotting every counter key, magnitude (as `float.hex()` - exact bit pattern, same non-associativity reasoning the existing scoring harness already uses), sign, and populated-vs-empty dimension into a committed golden fixture (`tests/fixtures/profile_builder_harness/profile_builder_snapshot.json`, written via `python -m tests.harness --profile-builders --write`). `tests/test_profile_builder_harness.py` pins the live snapshot against that golden fixture, and separately proves the harness can tell buggy from fixed behavior (not just freeze whatever it happens to see today) with four direct assertions on the verified bugs:
  - Movie ratings are never negative today (Plex history never carries `userRating`, and `movie.py` only reads it off history items).
  - Both `movie.py`'s and `tv.py`'s own per-user builders show zero rewatch/rating signal for either fixture user today, despite the fixture giving them genuinely different watch state - both read that state through the one shared ADMIN-token library snapshot, not their own.
  - `base.py`'s managed-users builder weights every watched item exactly 1.0 (no `weight` argument reaches `process_counters_from_cache()` at all).
  - `external.py`'s `build_user_profile()` produces byte-identical output for two different usernames - the username parameter has zero effect on what gets scanned.

  A future #273 PR that intentionally changes one of the four builders regenerates the golden fixture and explains the diff in that PR's description, per the same convention `tests/golden_external_harness.py` already established for `recommenders/external_render.py`. No production code changed in this PR - tests and fixtures only.

### Fixed

- **Hardened `sanitize_frozen_relaunch_env` (utils/self_update.py) against PyInstaller onefile's dynamic-loader-path env-var inheritance hazard.** Follows up the v2.10.57 release verification, where a real self-update from v2.10.48 hit a SIGSEGV in the post-swap version readback (rolled itself back correctly; could not be reproduced in 10/10 clean reruns and CI's own smoke test) - not chased further per its own inconclusiveness, but it surfaced a real, separate, verified gap: the sanitizer stripped only `_MEIPASS2`/`_PYI_*`/`_PYINSTALLER_*`, never `LD_LIBRARY_PATH` - the classic PyInstaller onefile hazard where a parent's own extraction-directory-pointing loader path leaks into a relaunched child.

  Verified directly against PyInstaller 6.21.0's own bootloader source (the version this repo's build-requirements.lock pins - both the compiled bootloader binaries this repo's own release CI ships, and the upstream C source) rather than assumed: Linux's onefile bootloader unconditionally prepends its extraction directory onto `LD_LIBRARY_PATH` on every launch, saving any pre-existing value under `LD_LIBRARY_PATH_ORIG` first. macOS's bootloader does **not** touch `DYLD_LIBRARY_PATH`/`DYLD_FRAMEWORK_PATH` at all ("we rewrite the library paths on collected binaries" - PyInstaller's own source comment); confirmed empirically too (the compiled Darwin bootloader binaries contain no such strings, unlike the Linux one, which contains both `LD_LIBRARY_PATH` and `LD_LIBRARY_PATH_ORIG` verbatim).

  `sanitize_frozen_relaunch_env` now resolves all three variables using PyInstaller's own `<VAR>_ORIG` convention: restores the original value and drops the `_ORIG` marker when present, and - only for `LD_LIBRARY_PATH` on Linux, the one (variable, platform) pair confirmed to always be bootloader-injected when absent - removes the variable outright when no `_ORIG` exists. Everywhere else (the macOS variables, and `LD_LIBRARY_PATH` on any non-Linux platform), a value with no `_ORIG` is left completely untouched rather than risk clobbering something a user genuinely set themselves. All three of this module's real relaunch/subprocess call sites (the self-update worker spawn, the post-swap `--version` readback, and the web UI's external hand-off script launch) already routed through this same function, so all three are covered by this one change; the hand-off shell script templates' own redundant belt-and-suspenders re-stripping deliberately does not duplicate the loader-path handling, since that decision can only be made correctly once, before the `_ORIG` marker is consumed.

  Verified end-to-end against a real, actually-built Linux onefile binary (matching this repo's own release CI exactly): captured the real bootloader-injected environment of a live running instance in two scenarios (no pre-existing `LD_LIBRARY_PATH`, and a genuine pre-existing one) and confirmed the sanitizer strips the former outright and correctly restores the latter to its original value. The macOS side of this fix is confirmed correct by source/unit tests but is precautionary for this project specifically - PyInstaller doesn't touch these variables for a Qt-less build like curatarr's, so there was nothing to observe mutating them in a real macOS build. The original SIGSEGV itself was not re-attempted, per its own inconclusiveness.

## [2.10.59] - 2026-07-27

### Fixed

- **The clickable Trakt list URL printed after a successful export 404'd for any list name containing " - ".** `recommenders/external_sync.py` derived the slug as `list_name.lower().replace(" ", "-")`, which replaces each space independently - for a list named `Curatarr - Jason - Movies` that produces `curatarr---jason---movies` (three hyphens where the two literal " - " separators sit), while Trakt itself collapses the whole run to a single hyphen and assigns `curatarr-jason-movies` - confirmed against the live API (`GET /users/me/lists` on a real list of that exact name).

  `utils/trakt.py`'s `sync_list()` now returns the list's REAL slug (from the same API response's own `ids.slug`) under a `list_slug` key, and all four places `external_sync.py` prints a Trakt list URL now build it from that returned value instead of re-deriving one. `get_or_create_list()`'s own internal speculative direct-lookup slug (tried before a full search-by-name - Trakt has no "look up by name" endpoint) had the identical bug; it's fixed too via a new shared `derive_trakt_list_slug()` helper, which is explicitly documented as a fallback only, never authoritative - it collapses whitespace/underscore runs and the resulting hyphen runs to a single hyphen and strips leading/trailing hyphens, correctly producing `curatarr-jason-movies` for the reported name, but is not a reimplementation of Trakt's full slugification rules (apostrophes, ampersands, unicode, etc. aren't specially handled).

  Audited every other slug/URL construction site in the codebase (library-id slugs, watchlist HTML filenames, username normalization) - none of them touch Trakt at all, so none were affected.

## [2.10.58] - 2026-07-27

### Fixed

- **`python3 -m utils.trakt_auth --reauth` produced zero output and appeared to hang when run without a TTY** (`ssh host "cmd" > log` with no `-t`, or `docker exec` without `-t`) - the device code and `trakt.tv/activate` URL were written to stdout right before the script blocks in `poll_for_token`'s device-auth polling loop (up to 10 minutes), but CPython fully block-buffers stdout whenever it isn't a real terminal, so none of that text ever reached the log/pipe until the process eventually exited - there was nothing to approve, so the flow could never complete. Reproduced directly (a real, non-TTY child process showed zero bytes of output while still genuinely blocked in the poll wait) before fixing.

  `utils/trakt_auth.py`'s `main()` now reconfigures stdout to line-buffered on entry, so every print - the device code and URL in particular - is flushed immediately regardless of how the script is invoked, without relying on the caller passing `-u` or setting `PYTHONUNBUFFERED`. `utils/trakt.py`'s `poll_for_token()` also gained an optional `on_wait` callback, which `trakt_auth.py` uses to print a throttled "...still waiting for approval" line roughly every 30 seconds, so a user watching a long wait over SSH/a log can tell it's working, not hung.

  Audited `utils/simkl.py`'s equivalent PIN-code flow and `setup.sh`'s/`run.ps1`'s own inline Trakt/Simkl device-auth blocks for the same pattern - unaffected: both are bounded to a 30-second poll, gated behind an explicit `read -p`/`Read-Host` "press Enter after you've approved" prompt (already requiring a real interactive terminal), and their device-code output is captured via command substitution from a short-lived process that exits (and therefore flushes) almost immediately, never left sitting in a buffer during a long, otherwise-invisible wait.

## [2.10.57] - 2026-07-27

### Fixed

- **Trakt re-authentication instructions were inconsistent across three locations, and all three could fail with `ModuleNotFoundError: No module named 'cryptography'` when run with the wrong interpreter.** `run.ps1` said `python utils/trakt_auth.py`, `config/trakt.example.yml` said `python3 utils/trakt_auth.py`, and the Connections screen said `python3 -m utils.trakt_auth` - three different invocations, none mentioning that this needs the project's own dependencies (a virtual environment, if you're using one), which is what actually broke for a reporter running the documented command against their system Python. Confirmed `python3 -m utils.trakt_auth` and `python3 utils/trakt_auth.py` behave identically (`-m` still runs the same `__main__` block); standardized all three locations, plus the script's own internal re-auth hint, on the `-m` form, and added a plain reminder to activate a virtual environment if applicable.

  Testing this surfaced a second, more serious bug in the same area: `utils/trakt_auth.py`'s own `get_config_dir()` resolved against wherever the script itself lives on disk, not against `utils.helpers.get_project_root()` like everything else in the app - correct for a source checkout, but wrong in Docker, where the code lives at a fixed `/app` while `config/trakt.yml` actually lives on the separately mounted `/data` volume. A `docker exec` re-auth before this fix would have silently read/written the wrong, non-persisted path - confirmed in a real container. `get_config_dir()` now delegates to `get_project_root()`, and the Connections screen and `docs/DOCKER.md` both now give Docker users the `docker exec -it <container> python3 -m utils.trakt_auth` command that actually works against the real config, instead of a source-install command they have no way to run.

  Checked the frozen (PyInstaller) binary separately: unlike `movie`/`tv`/`external`/`full`, there is currently no way to run Trakt re-auth from a packaged build at all (no loose `utils/trakt_auth.py`, no `--run-recommender`-equivalent dispatch). The Connections screen now says so plainly instead of showing a command that can't work there; closing this gap needs its own follow-up.

  `config/trakt.example.yml` now also documents `--reauth` for relinking an already-linked account, not just first-time setup.

## [2.10.56] - 2026-07-27

### Fixed

- **The last piece of issue #260: the `full` engine (Run button, and now the #264 scheduler) failed inside Docker.** 2.10.51 fixed movie/tv/external but left `full` going through `run.sh` itself, which separately assumes the directory it lives in (`/app`) doubles as the data directory it reads `config/`, `cache/`, and `logs/` from - true for a source checkout, never true in Docker, where those are a separately mounted `/data` (`CURATARR_CONFIG_DIR`). Two symptoms of that one mismatch: `check_and_install_dependencies()`'s pip install always failed (the runtime image never ships `requirements.lock`/`requirements.txt` at all - only the venv those built at image-build time, confirmed against a real container), and `is_first_run()` always saw a missing `config/config.yml` and dropped into the interactive setup wizard, which isn't shipped in the image either.

  Rather than teach the whole of `run.sh` (also used by every non-Docker install, where its assumption is correct) about `CURATARR_CONFIG_DIR`, the web UI's `full` trigger now bypasses `run.sh` entirely inside the real image, chaining `movie.py` -> `tv.py` -> `external.py` directly instead - the exact order `docker-entrypoint.sh`'s own `recommend full` mode (and the frozen binary's `--run-recommender full`) already use successfully in this same image. Source checkouts and native (non-Docker) web-UI runs are untouched and still go through `run.sh`/`run.ps1` exactly as before.

  The frozen (PyInstaller) binary never had this problem - its `full` dispatch already calls `movie`/`tv`/`external` in-process and never touches `run.sh`/`run.ps1` at all.

  Verified in a real container: launching `full` now clears dependency resolution and config loading, and reaches a real `recommenders/movie.py` traceback (a fake Plex token rejected with 401) instead of a pip failure or a dead subprocess - movie/tv/external individually confirmed still launching correctly too.

## [2.10.55] - 2026-07-27

### Added

- **In-app scheduler (#264).** Settings -> Scheduling: enable a daily time (24-hour, optionally restricted to specific weekdays) to run the full pipeline (movie + tv + external) from inside the web UI process itself - no host cron or separate container needed. Off by default.
  - Timezone is always the container's own `TZ` environment variable (falls back to UTC if unset) - there's no separate timezone setting, so there's exactly one clock to reason about. The Settings screen shows the resolved timezone and the computed next-run time.
  - Shares the exact same cross-container run lock the existing `docker-compose.yml` `schedule` profile / host-cron approach already uses - a scheduled run can never overlap a run triggered any other way. If the scheduled time arrives while a run is already in progress, that occurrence is skipped and logged (never queued) - the dashboard shows the last scheduled attempt and its result.
  - Never fires a missed occurrence on restart or redeploy - only the next real occurrence ever fires. Correctly handles both DST transitions: a time inside a spring-forward gap runs shortly after the gap closes rather than being skipped for the day; a fall-back-repeated hour fires once, not twice.
  - **This does not replace host cron / the compose `schedule` profile - they coexist.** `docs/DOCKER.md` now documents both as explicit alternatives and states plainly to use only one: the shared run lock prevents the two from overlapping, but nothing prevents both from firing at different times of day if you enable the in-app scheduler while also running host cron/the compose profile. There's no reliable way to detect that combination from inside the container (no Docker socket access, no host crontab visibility), so this is a documentation warning, not an automatic check.

## [2.10.54] - 2026-07-27

### Fixed

- **Trakt exports have been silently failing for every user roughly 24 hours after linking their account, since access tokens now expire that fast (Trakt changed this 2025-03-20) - and every run still reported success.** `create_trakt_client()` built its Trakt API client with no way to save a refreshed token: the refresh itself worked and updated the token in memory, but as soon as that process exited the refreshed token was gone, and since Trakt's refresh tokens are single-use, the *next* run replayed the same already-consumed refresh token and failed - a one-way trapdoor with zero persisted state ever recovering on its own. Refreshed tokens (access + refresh + issued-at/expiry) are now saved back to `config/trakt.yml` immediately, atomically, and without disturbing any other key in the file.

  Also fixed, all part of the same root cause:
  - The refresh request was missing `redirect_uri`, which Trakt's documented refresh body (and PyTrakt) includes on a refresh grant.
  - A refresh failure was completely silent - no HTTP status, no response body, nothing logged. Both are now logged (secrets redacted first, same as every other log line).
  - `get_username()` swallowed the real authentication error into a bare `None`, which turned "your refresh was just rejected" into the identical, misleading "Cannot get lists: not authenticated" every caller already showed for "never linked at all". The real cause is now logged.
  - **The failure was invisible in the web UI** - the run still exited 0 and the dashboard still said "succeeded". A new explicit integration-health signal (`utils/integration_status.py`) records the real outcome of every Trakt export/sync attempt and surfaces it as a banner on every page plus a `trakt_export` field in `/status.json` - deliberately not log-string matching, which is exactly how this hid for months in the first place.
  - **Recovering no longer requires hand-editing `trakt.yml`.** `python3 utils/trakt_auth.py` refused to re-authenticate whenever *any* `access_token` was present, telling you to remove it from the file yourself first - awkward for a source install, effectively impossible for Docker/frozen-binary users who can't reasonably shell in to do that edit. It now takes a `--reauth`/`--force` flag that bypasses that check and starts a fresh device-code sign-in immediately. (A web-UI "re-link Trakt" button would need its own device-code-polling flow comparable in size to the existing web-triggered-run plumbing - not built this round; the CLI flag is the fix for now.)
  - The client now also tracks when its access token was issued and how long it's valid for, and refreshes proactively before it expires rather than only reactively after a request gets rejected.

  **`trakt.export.auto_sync`'s code default is now `false` (was `true`) - this changes behavior for anyone who never explicitly set this key.** `config/trakt.example.yml`, `setup.sh`, and both web UI config screens have always documented/shown `false`; only this one code path silently defaulted the opposite way, meaning recommendations could be pushed to a linked Trakt account with no explicit opt-in. If you were relying on the undocumented `true` default, add `trakt.export.auto_sync: true` to `trakt.yml` explicitly.

  **Verified against a real refresh (once, deliberately - Trakt rotates refresh tokens single-use, so every attempt is potentially consuming): the stored refresh token is already dead.** Trakt returned `HTTP 400 {"error":"invalid_grant","error_description":"session not found"}` - `invalid_grant` (not a malformed-request `invalid_request`) means the token itself is no longer valid, not that the request body was wrong. The account needs an interactive re-link: `python3 utils/trakt_auth.py --reauth`. This fix still matters going forward - every account that re-links will actually stay linked now, instead of silently breaking again on its first automatic refresh.

## [2.10.53] - 2026-07-27

### Fixed

- **Changing `collections.movie_name_template`/`tv_name_template` (#267) now renames the existing collection instead of orphaning it.** 2.10.52 documented this as a known limitation: switching templates left the previously-named collection behind in Plex, untouched and unmanaged, while a new one was created under the new name. Now, when a run's freshly-rendered collection name differs from what's currently in Plex, the existing collection is renamed in place - preserving its poster, sort title, added-at date, and any manual curation, none of which a delete-and-recreate would keep. Ownership is confirmed via the same `PrivateCollection_<user>` label curatarr already applies to every collection it manages (present regardless of whether `collections.private_collections` is on), never by guessing from the title - a collection curatarr didn't label is never touched. If both an old-named and a new-named collection already exist, nothing is renamed (that would create a duplicate title); both are logged and left alone for manual cleanup. New config key `collections.rename_on_template_change` (default `true`) controls this; set it `false` to keep the old orphan-in-place behavior.

## [2.10.52] - 2026-07-27

### Added

- **Running version is now shown in the web UI, not just /healthz and /status.json (#265).** Every page's top bar now shows `v2.10.52` (or whatever's actually running) next to the Curatarr logo - useful given how often new versions ship.

- **"Fetch from Plex" on the Users and Libraries settings screens (#266).** Both screens now have a button that connects to your already-configured Plex server/account and lists real users (server owner, Home/managed users, shared friends) or real library sections that aren't configured yet, with a checklist to add exactly the ones you want in one save - no more typing usernames/section names by hand and risking a typo that silently doesn't match anything in Plex. Re-run it any time your Plex users or libraries change. Read-only until you actually check boxes and save; a broken/unreachable Plex connection shows a message instead of breaking the page.

- **Custom collection-name templates (#267).** `collections.movie_name_template` / `collections.tv_name_template` in `tuning.yml` let you control what curatarr's generated collections are called, with `{user}` (the resolved display name) and `{media_type}` ("Movie"/"TV") placeholders - e.g. `"Recommended movies - {user}"`. Leaving these unset (the default) produces byte-for-byte the same name curatarr has always used (`🎬 {user} - Recommendation` / `📺 {user} - Recommendation`), so nobody's collections get renamed unless they opt in. An invalid template (unknown placeholder, malformed `{}`) falls back to the default and logs a warning rather than breaking a run. The multi-library disambiguation suffix (e.g. `(Movies 4K)`) is still always appended after the template renders, regardless of what it says, so a custom template can't reintroduce a same-media-type naming collision across libraries.

  **Known limitation, not solved by this change:** changing the template later doesn't rename or clean up the collection under its previous name - the old one is left behind in Plex, same as if you changed `collections.label_name` or a user's `display_name` today. If you want the old name gone, remove it in Plex yourself after switching templates.

## [2.10.51] - 2026-07-27

### Fixed

- **The web UI's Run button now actually starts a run in Docker - the second half of issue #260.** PR1 (2.10.49) fixed the 403 that blocked the request from reaching the server at all; the run itself was still silently broken behind it. `JobManager` resolved `recommenders/<engine>.py` and `run.sh`/`run.ps1` against `CURATARR_CONFIG_DIR` (the *config/data* directory - `/data` in the Docker image) instead of the directory the code actually lives in (`/app`) - every movie/tv/external/full run launched from the web UI in Docker failed immediately with `can't open file '/data/recommenders/movie.py'`, while the HTTP request itself still returned a normal 200/redirect, so nothing in the UI indicated the run never happened.

  Fixed with a new `utils.helpers.get_code_root()` - deliberately separate from `get_project_root()` (which answers "where's config/cache/logs", not "where's the code" - the two happen to coincide for a plain source checkout, which is exactly why this went unnoticed until Docker deliberately splits them). `JobManager` now resolves scripts against this instead.

  Verified end-to-end in all four ways curatarr actually ships, not just that the HTTP request returns 200: a real Docker container (movie/tv/external all launch and produce real log output, including a real recommenders/movie.py traceback instead of a file-not-found error - full/run.sh also now launches, though it separately hits a pip-install step that isn't expected to succeed inside this image and needs its own look), a real PyInstaller frozen binary build (all three engines launch via `--run-recommender`), a native (non-Docker) web-mode run, and the existing source-checkout test suite. Issue #260 stays open until this ships and the reporter confirms.

- **Two `config/tuning.example.yml` documentation mismatches, surfaced by the guardrail test added in 2.10.49, now resolved:**
  - `external_recommendations.max_iterations`: the example documented `5`; the code's actual fallback has been `8` for months and every install has been running on `8` the whole time (nothing ever wrote a real `tuning.yml` - see 2.10.49's #261 notes for why that's been true of every fresh install). The example was corrected to `8` to match reality, not the other way around - changing the code to `5` would have silently altered discovery behavior for every existing install to match a doc that was simply wrong. The web UI Settings screen's own "if not set" default (`web/config_settings.py`) was corrected to match.
  - **`external_recommendations.enabled` is now actually honored - this changes output for anyone who had set it to `false`.** The example has always documented this key, but nothing in `recommenders/external.py` ever read it - every install got external recommendations regardless of what this was set to. It's now wired up (defaulting `true`, so nobody's current setup changes unless they'd already set it `false`). **If you have `external_recommendations.enabled: false` in your `tuning.yml`, external recommendations/watchlist generation will now actually stop** - Huntarr (`huntarr.sequel_huntarr`/`huntarr.horizon_huntarr`) is unaffected, since it's a separate feature under its own config keys. This is the behavior you configured; it just wasn't being honored before.
  - The guardrail test now passes clean across every top-level section of `tuning.example.yml` with no known mismatches remaining.

## [2.10.50] - 2026-07-27

### Fixed

- **The Docker container was never actually restarting - it just looked like it - #262.**

  `utils/config.py`'s `load_config()` (and the helper it calls to merge in `tuning.yml`/`trakt.yml`/`radarr.yml`/`sonarr.yml`) printed a handful of "Loaded X.yml"/"Successfully loaded configuration" lines with `print()` on every single call, with no level control. `web/app.py` calls this 1-2x per page render (the update-banner banner shown on every page, plus whichever route is actually serving the request each load config independently) - so every dashboard view, config save, or status check produced a fresh burst of these lines in `docker logs`, which read exactly like the container repeatedly restarting.

  Fixed: those `print()` calls are now the project logger (`log_info`/`log_warning`/`log_error`), and `web/app.py` now caches the parsed config (keyed on the mtimes of every file `load_config()` reads) and the update-check result (a short TTL, deliberately excluding the dismiss check, which still takes effect immediately), so a page view costs at most one real disk read/parse instead of two-plus. A config edit - through the web UI's own save routes, or a hand edit to `tuning.yml` on disk - is still picked up on the very next request, no restart needed. The CLI is unaffected: it always configures logging with these messages visible at their existing default level, so a normal `curatarr` run looks exactly the same as before. Verified in a real container: 11 requests across the dashboard/run/status.json routes produced zero additional log lines beyond one one-time config-format-migration notice, versus roughly 15-20 lines under the old behavior.

- **An interrupted run - `docker stop`, a container recreate, or a crash - could leave a zeroed-out, unexplained log file - #263.**

  Three compounding bugs, all in the log-writing/shutdown path:
  - `utils/display.py`'s `TeeLogger` (used by every CLI/native recommender run) never flushed the log file per write - only a clean shutdown did. A hard kill lost the whole unflushed buffer, leaving a 0-byte log file. Fixed: every write is now flushed immediately (write volume here is a few hundred/thousand status lines over a multi-minute run, not a hot per-item loop, so this is negligible overhead).
  - `web/docker_server.py` (the Docker image's own entrypoint) registered no signal handling at all - it caught SIGINT but never SIGTERM. Every `docker stop`/`compose restart`/recreate therefore ate the entire 10-second stop grace period before being hard SIGKILLed (exit 137), which also guaranteed any in-flight run lost its unflushed log buffer. Fixed: it now registers the same SIGINT/SIGTERM -> stop-the-running-job handling the native app has always had. Verified in a real container: `docker stop` dropped from 10.1s/exit 137 to 0.14s/exit 0.
  - `utils/helpers.py`'s `cleanup_old_logs()` (which runs at the start of every run) truncated any `.log` file over 20MB to exactly 0 bytes in place - a legitimate, still-wanted log from an earlier run could get silently zeroed the moment the very next run's cleanup pass saw it cross that size. Fixed: it now keeps the file's last 20MB (a tail) instead of erasing it outright.

  Also: `web/status.py`'s `unknown` status (shown when a log file has no readable content) now explains itself - "log file is empty (0 bytes) - the run was likely interrupted before writing any output" versus "log file unreadable (...)" for a permissions/OS-level failure - surfaced both on the dashboard (as a tooltip on the badge) and on the log-view page itself (instead of just a blank pane), in the `/status.json` API, and available programmatically as `get_last_run_status()`'s new `reason` field.

## [2.10.49] - 2026-07-27

### Fixed

- **Every web UI form (Save/Run/Login) was returning 403 - #260.**

  `web/app.py`'s baseline security headers set `Referrer-Policy: no-referrer` on every response (added in 2.9.0, #198). Per the Fetch spec, a browser under that policy sends `Origin: null` (and no `Referer` at all) on a plain `<form method="post">` navigation - `fetch()` calls are unaffected since they use CORS mode and always send a real `Origin`. `web/security.py`'s origin guard then saw the literal string `"null"`, couldn't resolve it to this app's own host, and rejected the request with 403. Affected routes: `/run`, `/config/settings`, `/config/users`, `/config/connections`, `/config/libraries`, `/update/dismiss` - and `/login` itself, since that guard runs before token auth, so a token-protected deploy couldn't even log in.

  Fixed by changing `Referrer-Policy` to `same-origin`: this still sends NO referrer on a cross-origin request (nothing new leaks to another site), but it does send one - and therefore a real `Origin` header - on a same-origin request, which is exactly what the guard needs to tell a same-origin form POST apart from a cross-site one. A cross-origin or opaque-origin (sandboxed iframe, `data:`/`blob:` document) form POST still gets `Origin: null` under `same-origin` and is still correctly rejected with 403 - this only fixes the same-origin case, the CSRF guard itself is unchanged and was not loosened.

  **Why the test suite was green while this was broken in production:** the test client (`_BrowserLikeTestClient`) stamps a same-origin `Origin` header onto every request by default, and the one test that pinned `Referrer-Policy` asserted `no-referrer` in isolation - the two facts that combine to cause #260 were never asserted together. Added a regression test that pins the actual invariant (the response's `Referrer-Policy` must be a value that preserves `Origin` on a same-origin form POST, explicitly not `no-referrer`) and a second test using the raw Flask test client (bypassing the same-origin stamping) confirming cross-origin/opaque-origin POSTs are still rejected.

- **Multi-user installs got a single, mis-named, shared collection with recommendations hidden from every shared user instead of their own - #261.**

  `recommenders/base.py` read `collections.append_usernames` with a code default of `False`, while the shipped `config/tuning.example.yml` always documented `true` - and nothing in any install path (Dockerfile, setup.sh, the web UI) ever wrote a real `tuning.yml`, so every fresh install silently ran on the wrong default. With `append_usernames` false: every user's item label collapsed to the identical bare string `"Recommended"`; the collection title's username was then derived by stripping a `"Recommended_"` prefix that was never there, a silent no-op that left the literal word `"Recommended"` capitalized into the collection title `🎬 Recommended - Recommendation`; and - worst - `private_collections` (on by default) then pushed a `label!=Recommended` exclude filter to every non-admin user's Plex account, which matched the one shared collection's own label too, hiding it (and its items) from everyone instead of isolating each user's own recommendations.

  Fixed: `append_usernames` now defaults to `true`, matching the documented example. Collection/label identity is now built from the real username the caller already has (`self.single_user`) and threaded explicitly through `recommenders/base.py`, `utils/plex.py`, and `utils/plex_policy.py`, instead of being re-derived by stripping a prefix that wasn't always there. If `append_usernames` is ever explicitly set `false` with more than one user configured - the one combination `private_collections` genuinely cannot support - curatarr now skips applying label restrictions and logs a clear warning naming the config key and file, instead of silently sending a filter that hides content.

  **What changes on your next run if you were affected (multi-user install, no `tuning.yml`, default settings):** each user gets their own correctly-named collection (`🎬 <Username> - Recommendation`) and label going forward. The old shared `🎬 Recommended - Recommendation` collection is orphaned junk from the bug and is now cleaned up automatically (its bare `"Recommended"` item label is stripped and the collection deleted) the first time any user's run completes successfully after upgrading - this is idempotent and requires no manual action. The stale `label!=Recommended` Plex account filter that was hiding the shared collection from every non-admin user is also corrected automatically: applying label restrictions always recomputes and re-sends every configured user's filter in one pass, so it self-heals as soon as any single user's run succeeds post-upgrade - nobody needs to touch plex.tv account settings by hand.

### Documentation

- **Corrected the "private collections" claim - no functional/security change.** README.md and `config/tuning.example.yml` described per-user collections as private/hidden from other users. Verified against a live server: Plex enforces the `label!=` exclusion on the collection object (so it's absent from browse/search, and a direct `GET` on its ratingKey 404s for another user) but NOT on the items returned by that collection's own `/children` endpoint - so a user who already has, or guesses, a collection's ratingKey (Plex assigns small sequential integers) can still retrieve its full contents via the Plex API. This is Plex server-side behavior, not something curatarr can fix. Reworded README.md's feature list/FAQ, `config/tuning.example.yml`'s `private_collections` comment, and `utils/plex_policy.py`'s docstrings to describe this correctly as UI-level separation (each user's own Browse/Search only shows their own collection), not an access-control or privacy boundary. The feature itself, and its default-on behavior, are unchanged.

## [2.10.48] - 2026-07-27

### Fixed

- **`tv:` `quality_filters` (`min_rating`/`min_vote_count`) has never actually filtered anything - YOUR TV RECOMMENDATIONS WILL LOOK DIFFERENT after upgrading if you have a nonzero threshold set.**

  `recommenders/tv.py`'s `ShowCache` never populated `rating`/`vote_count` on cached show entries - only `MovieCache` did. `BaseRecommender.get_recommendations()`'s quality-filter check (the same code shared by both movies and TV) reads those two fields from each cached item and defaults to `0`/`0` when they're absent, so every TV show was invisible to the filter no matter what threshold you configured under `tv: quality_filters:` in `tuning.yml` - identical in spirit to the 2.10.23 `movies:`/`tv:` config-wiring bug, but this one was in the cache-population path, not config resolution. Movies were never affected.

  Fixed by having `ShowCache._process_item()` fetch and store `rating`/`vote_count` from TMDB the same way `MovieCache._process_item()` always has (`recommenders/base.py`'s shared `_get_tmdb_data()` now fetches them for TV too, alongside the `production_company_ids` it already fetched). Concretely, this means:
  - If you have `tv: quality_filters: min_rating`/`min_vote_count` set above `0` in `tuning.yml`, low-rated/low-vote-count shows that were slipping through before will now actually be filtered out of your TV recommendations.
  - If you've left it at the documented default (`0.0`/`0`), nothing changes - this was already a no-op for you and stays one.

  **Existing show caches:** `CACHE_VERSION` is bumped (4 -> 5) so every existing `all_shows_cache.json` (and, since this constant isn't tracked per-media-type, `all_movies_cache.json` too) is deleted and fully rebuilt from TMDB on the next run after upgrading - a one-time slower run, no partial/half-migrated state. This is deliberate: without the bump, existing show cache entries would be missing `rating`/`vote_count` entirely, and the quality-filter code's `0`/`0` default would make every one of those shows look like it scored zero and get wrongly dropped the instant you set a real threshold, with no warning. The rebuild guarantees nobody hits that window. A show TMDB genuinely can't match still has no rating data after the rebuild and is filtered the same way an unmatched movie always has been (unchanged, symmetric behavior) - it isn't a new failure mode, just parity.

## [2.10.47] - 2026-07-27

### Fixed

- **Two runtime crashes found during the 2.10.46 mypy pass (both were left as documented `# type: ignore[...]` there on purpose, since fixing them was a behavior change outside that typing-only PR's scope) are now actually fixed.**

  - `export_to_sonarr()` called `SonarrClient.add_series()` with `search_for_missing_episodes=` - the real parameter is `search_for_missing`. Every send-to-Sonarr export raised `TypeError` the moment it ran (both the combined-mode and per-user/mapping-mode call sites). Fixed by renaming the kwarg to match the actual signature; behavior is otherwise unchanged (it's now what the per-library `search` setting always should have done - actually starting a search for missing episodes on add, instead of crashing before the add ever happened).
  - `export_to_mdblist()` called `MDBListClient.get_user_info()`, which doesn't exist on that class at all (MDBList's client only ever implemented list management, never a user-info endpoint) - every MDBList export raised `AttributeError` (not caught by the surrounding `except MDBListAPIError`, so it propagated as an unhandled exception) the moment it ran. Fixed by calling `test_connection()` instead - the same "verify the API key works, then print the section header" pattern already used by `export_to_sonarr()`/`export_to_radarr()`/`export_to_simkl()`. The "Connected as: {name}" line is removed since no real user-info data was ever available to back it.
  - **Root cause of why neither was caught by tests:** both call sites were exercised only against a bare `unittest.mock.MagicMock()`, which accepts any attribute name and any keyword argument silently - so a call-site kwarg typo or a call to a method that doesn't exist on the real class both passed clean. Every `SonarrClient`/`RadarrClient`/`MDBListClient` test double in `tests/test_external_sync.py` is now built via `unittest.mock.create_autospec(...)` instead, which enforces the real class's method set and call signatures - reverting either fix locally and re-running the suite now fails loudly (`TypeError`/`AttributeError`) instead of passing. Radarr's `add_movie()` call sites and every other Sonarr/Radarr/MDBList client call in `external_sync.py` were audited for the same class of kwarg mismatch; none found.

## [2.10.46] - 2026-07-27

### Changed

- **mypy worked down from 180 errors to zero; the `lint` CI job's mypy step is now blocking.**

  - Almost entirely missing/implicit-`Optional` annotations and mismatched container element types (e.g. a dict/Counter genuinely holding `float`s typed as `int`, a function returning `Set[int]` that was actually declared `Set[str]`) - fixed at the root by correcting the annotation to match real, unchanged runtime behavior. No production logic changed; `tests/harness.py` and `tests/golden_external_harness.py` are byte-identical, confirmed via their own passing suites plus a `git diff` showing zero changes to either file.
  - A handful of narrow, individually-commented `# type: ignore[...]` for genuine typeshed/stdlib limitations: Windows-only `os.startfile`/`ctypes.windll` attributes (absent from typeshed when analyzed on non-Windows), and `subprocess.run`/`Popen` overload resolution against a dynamically-built `**dict` kwargs splat (a known mypy limitation - the keys involved, e.g. `creationflags`, are genuinely valid `Popen` arguments at runtime).
  - Three additional narrow, explicitly-commented ignores in `recommenders/external_sync.py` mark pre-existing runtime bugs discovered while doing this pass, deliberately left unfixed here since a real fix is a behavior change outside a typing-only pass's scope: `SonarrClient.add_series()` is called with a stale kwarg name (`search_for_missing_episodes` - the real parameter is `search_for_missing`), and `MDBListClient.get_user_info()` doesn't exist on that class at all. Both raise at runtime whenever that code path executes; tracked for a follow-up fix, not silently worked around.
  - `mypy.ini` and `.github/workflows/tests.yml` updated to reflect the new zero-error, blocking state.

## [2.10.45] - 2026-07-26

### Added

- **End-to-end recommendation-pipeline test (`tests/test_e2e_pipeline.py`, `tests/e2e_plex_fixture.py`) - the first test in this suite to drive config -> Plex fetch -> watched-history build -> profile -> scoring -> selection -> output/labels top to bottom, for both movies and TV.**

  - Drives the real production entrypoints - `utils.cli.run_recommender_main` (flagship movie/TV tests, with `recommenders/movie.py`'s/`tv.py`'s own `process_recommendations` as the real `process_func`) and `PlexMovieRecommender.get_recommendations()` directly (narrower isolation/determinism tests) - against a synthetic-but-realistic 30-movie/20-show Plex library and real, on-disk `config.yml`/`tuning.yml` files (via a real `CURATARR_CONFIG_DIR` override, the same mechanism Docker installs use).
  - The only seams mocked: `recommenders.base.init_plex` (a duck-typed `FakePlexServer`), `utils.plex._capped_get`/`utils.plex.MyPlexAccount` (the raw-HTTP/plexapi-account layer behind the watch-history functions - real XML parsing/business logic runs against synthetic XML instead of a live server), and `PlexMovieRecommender`/`PlexTVRecommender.manage_plex_labels` (the collection/label-writing stage, replaced with a plain function that captures what it would have written without ever reaching `FakeSection`/`FakePlexServer` - both raise if the real label-writing code path is reached by mistake). The movie/show metadata cache is pre-seeded so `BaseCache.update_cache()` takes its real "cache is up to date" branch, mirroring `tests/harness.py`'s own established fixture convention rather than re-deriving metadata from a fake TMDB.
  - Asserts on real observable outcomes: exact per-user watched-item sets and watched-genre profiles (proving per-user isolation at its source, not just call counts), already-watched exclusion, global and per-user genre exclusion, quality-filter thresholds applied against real per-item cache data, and reproducibility of both the deterministic (`randomize_recommendations: false`) and seeded-random tiered-selection paths.
  - Confirmed while building this fixture: `recommenders/tv.py`'s `ShowCache._process_item()` never populates `rating`/`vote_count` on show cache entries (only `MovieCache` does, via TMDB), so `tv:` `quality_filters` is a no-op in production today, not just in this fixture - documented in the test's own module docstring rather than silently worked around.

## [2.10.44] - 2026-07-26

### Changed

- **Extracted streaming-service categorization (`categorize_by_streaming_service`) out of `recommenders/external.py` into `recommenders/streaming.py` (PR2 step 3, external.py architecture decomposition).**

  - Pure relocation: `categorize_by_streaming_service` moved byte-for-byte;
    only the import path changed. `recommenders/streaming.py` imports
    `get_watch_providers` from `recommenders/huntarr.py` (the TMDB
    watch-provider lookup helper Sequel Huntarr originally introduced).
    `recommenders/external.py` still calls `categorize_by_streaming_service`
    directly from several of its own functions (`process_user`,
    `process_user_movie_library`, `process_user_tv_library`), so it
    stays a normal top-level import there - not an `__all__`-only
    re-export. `get_watch_providers` moved from "used internally" to
    "re-export only" status in `external.py` now that its last internal
    caller moved out too.
  - Verified via the golden-output equivalence harness: watchlist
    HTML/MD, Sequel/Horizon Huntarr results, and categorized-by-
    streaming-service output are all byte-identical to pre-move golden
    fixtures.
  - `tests/test_external.py`'s `TestCategorizeByStreamingService` and
    `TestCategorizeByStreamingServiceAllItems` patches updated from
    `recommenders.external.get_watch_providers` to
    `recommenders.streaming.get_watch_providers` - same reasoning as the
    Sequel/Horizon Huntarr moves (a mocked function is looked up in
    whichever module's own namespace calls it at runtime).

## [2.10.43] - 2026-07-26

### Changed

- **Extracted Horizon Huntarr (`find_horizon_movies`) out of `recommenders/external.py` into `recommenders/horizon.py` (PR2 step 2, external.py architecture decomposition).**

  - Pure relocation: `find_horizon_movies`, `load_horizon_cache`,
    `save_horizon_cache`, `get_movie_status`, and
    `HORIZON_HUNTARR_CACHE_VERSION` moved byte-for-byte; only import paths
    changed. `recommenders/horizon.py` imports `get_collection_details`
    and `load_huntarr_cache` from `recommenders/huntarr.py` (Horizon
    Huntarr is a deliberate sibling of Sequel Huntarr - same collection
    data model, same cache reuse it already had before this move).
    `recommenders/external.py` re-exports everything external
    callers/tests still need, so no other module's imports needed to
    change.
  - Verified via the golden-output equivalence harness: watchlist
    HTML/MD, Sequel/Horizon Huntarr results, and categorized-by-
    streaming-service output are all byte-identical to pre-move golden
    fixtures.
  - `tests/test_external.py`'s `TestFindHorizonMovies` class patches
    updated from `recommenders.external.get_project_root` to
    `recommenders.horizon.get_project_root` - same reasoning as 2.10.42's
    Sequel Huntarr move (`get_project_root()` is looked up in whichever
    module's own namespace calls it at runtime).

## [2.10.42] - 2026-07-26

### Changed

- **Extracted Sequel Huntarr (`find_missing_sequels`) out of `recommenders/external.py` into `recommenders/huntarr.py` (PR2 step 1, external.py architecture decomposition).**

  - Pure relocation: `find_missing_sequels`, `load_huntarr_cache`,
    `save_huntarr_cache`, and the TMDB detail-fetching helpers Sequel
    Huntarr introduced (`get_watch_providers`, `get_collection_details` -
    also reused by Horizon Huntarr and streaming-service categorization,
    both still in `external.py` for now) moved byte-for-byte; only import
    paths changed. `recommenders/external.py` re-exports everything
    external callers/tests still need from it, so no other module's
    import statements needed to change.
  - Verified via the golden-output equivalence harness added in
    2.10.41: watchlist HTML/MD, Sequel/Horizon Huntarr results, and
    categorized-by-streaming-service output are all byte-identical to
    pre-move golden fixtures.
  - `tests/test_external.py`'s `TestFindMissingSequels` class patches
    updated from `recommenders.external.get_project_root` to
    `recommenders.huntarr.get_project_root` - `get_project_root()` is
    looked up in whichever module's own namespace calls it at runtime
    (a `from utils import get_project_root` name binding, not a shared
    module object like `requests`), so patching the old path silently
    stopped reaching `find_missing_sequels` once it moved.

## [2.10.41] - 2026-07-26

### Added

- **Golden-output equivalence harness for the upcoming `recommenders/external.py` architecture decomposition (PR2 prep, no production code touched).**

  - `tests/golden_external_harness.py`: runs `find_missing_sequels()`
    (Sequel Huntarr), `find_horizon_movies()` (Horizon Huntarr),
    `categorize_by_streaming_service()`, and the
    `generate_markdown()`/`generate_combined_html()` render pipeline
    against fixed synthetic Plex/TMDB fixtures (no real watch history),
    and compares the result byte-for-byte against committed golden
    files (`tests/fixtures/external_golden/`). `datetime.now()` is
    pinned (both render functions embed a live "generated at" timestamp)
    so any two runs against unchanged code are byte-identical.
  - `tests/test_golden_external_harness.py` is the actual CI-enforced
    gate: every subsequent PR in the external.py decomposition sequence
    must leave it passing, or stop and report the diff rather than
    merge - a failure means moved/refactored code produced different
    output than current main did.
  - Deliberately does NOT exercise `find_similar_content_with_profile()`'s
    iterative TMDB-Discover candidate loop or `build_user_profile()`'s
    Plex watch-history scan - neither is a relocation target in this PR
    sequence; see the harness's own docstring for the full reasoning.

## [2.10.40] - 2026-07-26

### Fixed

- **`_load_module_configs()` shallow top-level merge silently wiped root config keys when a module file redefined the same key.**

  - `utils/config.py`'s `_load_module_configs()` merged `tuning.yml` into
    the root config with `for key, value in tuning.items(): config[key] = value` -
    a shallow replace. If both `config.yml` and `tuning.yml` defined the
    same top-level key, tuning.yml's value replaced config.yml's entirely,
    even for sub-keys tuning.yml never mentioned. Both shipped example
    files (`config/config.example.yml`, `config/tuning.example.yml`)
    define a top-level `users:` key - following them as-is silently wipes
    `config.yml`'s `users.list` because `tuning.example.yml` only defines
    `users.preferences`.
  - Fixed with a new `_deep_merge_dicts()` helper: dict-valued keys are
    merged recursively (a module file only needs to specify the sub-keys
    it wants to change; sibling sub-keys survive), while non-dict values
    - including lists - are still replaced outright, never concatenated.
    Precedence: the module file wins for any key/sub-key it defines; root
    keys it doesn't mention are preserved. Applied to both the
    tuning.yml-into-root merge and the trakt/radarr/sonarr feature-module
    merge.
  - Not currently biting the live install (its `tuning.yml` has no
    `users:` section), but any install following the shipped examples
    verbatim would hit it.

## [2.10.39] - 2026-07-26

### Fixed

- **Deleted the second, dead `movies:`/`tv:` config-resolution path that caused the 2.10.23 bug and left the architecture that produced it unfixed (audit remediation).**

  - `utils/config.py`'s `adapt_config_for_media_type()` and
    `recommenders/base.py`'s inline `self.media_config` resolution were
    two fully independent implementations of the same `movies:`/`tv:`
    override resolution, computed from two separate `load_config()`
    reads. Only the `base.py` one was ever live - `adapt_config_for_media_type()`'s
    output fed `utils/cli.py`'s `run_recommender_main()` as `base_config`,
    but nothing downstream (`process_recommendations()` in
    `recommenders/movie.py`/`tv.py`) ever read its media-type-resolved
    keys, only root-level passthroughs (`general`, `plex`, `users`,
    `libraries`) that were identical either way. This is exactly the
    architecture that let the 2.10.23 bug happen and stay invisible for
    months - a future key added to one implementation had no way to end
    up in the other.
  - Replaced both with one function, `utils/config.py`'s
    `resolve_media_type_overrides(config, media_type)` (plus
    `load_resolved_config(config_path, media_type)`, the `load_config()`
    + `resolve_media_type_overrides()` one-call convenience most callers
    want). It takes the FULL root config and only overlays the specific
    resolved keys on top - unlike the deleted function, there is no
    cherry-picked reconstruction that can silently drop an unrelated
    root-level key (see below).
  - `recommenders/base.py`, `movie.py`, and `utils/cli.py` now call this
    directly instead of hand-resolving `movies:`/`tv:` overrides
    themselves. `utils/cli.py`'s `run_recommender_main()` no longer takes
    an `adapt_config_func` parameter - it never needed a media-type-
    resolved config for its own bookkeeping (users/libraries/general.*/
    plex.token are all root-level, media-type-independent values), so it
    now reads `root_config` directly.
  - **Defaults are unchanged for every key this touches** - verified by
    reconstructing the OLD inline `base.py` resolution logic exactly and
    diffing it key-for-key against the new function across 6 config
    shapes (no `tuning.yml`, `movies:`-only, `tv:`-only, both, unknown/
    extra keys, and the production install's actual config shape) x 2
    media types = 12 scenarios, all matching. Where the deleted dead
    path's computed defaults disagreed with the live one (documented
    explicitly so a future maintainer doesn't "fix" the wrong one back
    in): `randomize_recommendations` (dead path defaulted to `false`,
    live/kept default is `true`), TV `weights` (dead path's `genre`
    0.25/`actor` 0.20/`studio` 0.10/`keyword` 0.50/`language` 0.0 -
    doesn't even sum to 1.0 - vs. the live, kept
    `PlexTVRecommender._load_weights()` defaults of `genre` 0.20/`actor`
    0.15/`studio` 0.15/`keyword` 0.45/`language` 0.05), and movies
    `quality_filters` (dead path defaulted `min_rating`/`min_vote_count`
    to 5.0/50 when unset; the live, kept default - shared with TV - is
    0.0/0, resolved unchanged directly in
    `BaseRecommender.get_recommendations()`, never migrated into the new
    function since it was never divergent there).
  - Added `tests/test_config.py`'s
    `TestResolveMediaTypeOverridesKeyEnumeration`: parses the real,
    committed `config/tuning.example.yml` and asserts every documented
    `movies:`/`tv:` key actually resolves, plus a closed-set membership
    check that fails loudly if a future edit to that file adds a key
    neither test method accounts for - the standing guard against this
    bug class recurring for a new key.
  - `tests/harness.py` untouched (byte-identical - it only imports
    `utils/scoring.py`, never `utils/config.py` or `recommenders/base.py`)
    and its own test (`tests/test_harness.py`) still passes, confirming
    no scoring drift.

## [2.10.38] - 2026-07-26

### Fixed

- **Login rate limiter (`web/security.py`'s `_login_failures`) could grow without bound under a distributed attack (audit remediation, PR5).**

  - `_login_failures` was only pruned when the offending IP made another
    request (`_prune_locked`, called from `_is_locked_out`/
    `_record_login_failure` for that one IP only). A distributed
    attack sending exactly one request per source IP never revisits any
    of its own IPs, so nothing ever reclaimed those one-shot entries -
    the dict grows by one entry per unique attacking IP forever.
  - Added `_LOGIN_FAILURES_MAX_TRACKED_IPS` (10,000) and
    `_sweep_login_failures_locked()`, triggered from
    `_record_login_failure` only once the dict exceeds that cap (no new
    background thread/timer - stays in keeping with this module's
    existing "in-process/in-memory, no new dependency" design). The
    sweep first drops every entry that's aged out of the rolling window
    on its own (the common case for a one-shot flood), then, only if
    still over cap, evicts the least-recently-active entries - but never
    an actively locked-out IP (`>= _LOGIN_MAX_ATTEMPTS` within the
    window) while any non-locked-out entry remains to evict instead.
  - **Must not weaken lockout, must not let a flood evict a legitimate
    lockout**: this is why locked-out entries are excluded from eviction
    whenever a non-locked-out alternative exists - a flood of throwaway
    IPs can only evict *other throwaway/expired* entries, never force out
    a genuine attacker's (or a real user's, if they collide behind a
    shared proxy) active lockout early. Only falls back to evicting a
    locked-out entry in the pathological case where the cap is entirely
    saturated by simultaneously-locked IPs (requires
    `_LOGIN_FAILURES_MAX_TRACKED_IPS * _LOGIN_MAX_ATTEMPTS` failed
    requests inside one `_LOGIN_WINDOW_SECONDS` window - already its own
    denial-of-service on the process regardless of this dict) - logged
    via `log_warning` if it ever happens.
  - Tests added to `tests/test_web_security.py`: dict size stays bounded
    under a 50-unique-IP flood with the cap monkeypatched down to 5,
    an active lockout survives an unrelated flood of throwaway IPs (via
    the real `/login` route), a legitimate under-threshold user still
    logs in successfully after the same flood, and naturally-expired
    entries are pruned before any eviction is even considered.
  - No change to the existing lockout/window behavior for the ordinary
    case - all 66 pre-existing tests in this file pass unchanged.

## [2.10.37] - 2026-07-26

### Changed

- **Removed `adapt_config_for_media_type()`'s dead, divergently-defaulted display-option output (audit remediation, PR4).**

  - Since the 2.10.23 config fix, `recommenders/base.py` resolves
    `movies:`/`tv:` `show_summary`/`show_cast`/`show_language`/`show_rating`
    overrides itself, directly from `self.media_config` (defaulting to
    `False` for all four when unset). `utils/config.py`'s
    `adapt_config_for_media_type()` still independently flattened the
    same four keys onto its returned dict with a **different default**
    (`True`).
  - Verified "read nowhere outside `utils/config.py`" myself with an
    exhaustive grep (including every test file) before touching anything:
    the only reads of these four specific keys are `recommenders/base.py`'s
    own, separate `self.media_config`-based resolution (unrelated code
    path, never touches this function's output) and this function's own
    definition - not a single caller anywhere (`utils/cli.py`'s
    `run_recommender_main`, `recommenders/movie.py`/`tv.py`'s
    `process_recommendations`, or any test) ever reads
    `result["show_summary"]` etc. from `adapt_config_for_media_type()`'s
    return value.
  - The rest of the function is NOT dead: `run_recommender_main` reads
    the returned `general`/`plex`/`plex_users`/`users`/`libraries` keys
    (passthroughs of the same root-config values, not media-specific
    adaptations) for log retention, the Plex token, the configured user
    list, and per-library-per-user looping - kept those untouched.
    `limit_results`/`randomize_recommendations`/`normalize_counters`/
    quality-filter/weights/radarr-sonarr/`add_label` keys are also
    unread outside this function's own tests, same as the four removed
    keys, but were **left in place** - out of this PR's explicitly-scoped
    finding (which named only the four *divergently-defaulted* display
    keys), and none of them have a different-default divergence risk
    from the live path the way show_summary/cast/language/rating did
    (e.g. `limit_results`'s 50/20 default here already matches
    `recommenders/base.py`'s, post-PR1).
  - Removed the four keys entirely rather than just fixing their default
    to match - fixing the default wouldn't make the output any less dead,
    and would leave the exact same "looks like it matters, doesn't" trap
    for a future maintainer.
  - Added a test locking in that `adapt_config_for_media_type()` no
    longer produces these four keys, so a future change can't silently
    reintroduce them.

## [2.10.36] - 2026-07-26

### Changed

- **`collections.remove_previous_recommendations` removed from `config/tuning.example.yml` - concluded it cannot be implemented as described without either being a no-op or regressing every existing install's default behavior (audit remediation, PR3).**

  - Confirmed zero code consumers anywhere in the codebase (including web
    UI forms and tests) before touching anything - this key was purely
    documentation.
  - **Provenance is NOT the blocker** (unlike the concern the task
    anticipated): the Plex `Recommended_<user>` label IS a reliable
    tool-added marker (`utils/labels.py`), and `recommenders/base.py`'s
    `manage_plex_labels()` collection sync
    (`utils/plex.py`'s `update_plex_collection`) only ever operates on
    items carrying that label - it would never touch a user's manually-
    curated collection.
  - **The actual blocker: the described behavior is already unconditional,
    always-on, default behavior today - there is nothing left to gate.**
    Traced the full label/collection lifecycle in
    `recommenders/base.py`/`utils/plex.py`:
    - `_update_labels_by_rank()` already removes the `Recommended_<user>`
      label from any item that falls out of the current top-`target_count`
      ranking, on every single run, unconditionally (score-based
      eviction - see that method and its docstring; a comment there notes
      staleness-day removal was already retired in favor of this).
    - `_sync_plex_collection()` -> `update_plex_collection()` then, also
      unconditionally, calls `existing_collection.removeItems(current_items)`
      before `addItems(final_items)` - i.e. the Plex *collection* itself is
      fully reset to exactly this run's top-ranked items every single run,
      regardless of any config.
    - In other words: "when a recommendation collection is refreshed,
      remove items that were previously recommended and no longer are" -
      literally the feature this key was meant to gate - is precisely
      what already happens by default, unconditionally, today.
  - Wiring `remove_previous_recommendations` as a literal on/off toggle,
    honoring its shipped `false` default, would require making that
    already-unconditional eviction *conditional* on this flag - i.e.
    inverting today's default from "always evict stale recommendations"
    to "never evict unless explicitly enabled" for every install that
    hasn't touched this dead key (100% of them, since nothing reads it).
    That would make Plex recommendation collections grow without bound
    by default - a worse regression than the key doing nothing.
  - Unverified but plausible (labeled explicitly as a theory, not fact -
    not asserted anywhere in this fix): `git log -S` on this key across
    the whole repo history shows no dedicated "wire this flag" commit -
    it most likely predates (or was copied in alongside) the
    score-based-eviction rewrite that made removal unconditional, and was
    simply never deleted once that rewrite superseded whatever gated
    behavior it may once have controlled.
  - No code or behavior changed by this PR - documentation only.
    Removed from `config/tuning.example.yml` (the tracked template);
    left the local, gitignored `config/tuning.yml` runtime file alone
    (not part of this repo - `config/*.yml` is gitignored, only
    `*.example.yml` files are tracked).

## [2.10.35] - 2026-07-26

### Fixed

- **Per-user `streaming_services` was dead config (audit remediation, PR2).**

  - `users.preferences.<user>.streaming_services` was settable via the web
    UI and shown in both example configs, but `recommenders/external.py`
    only ever read the single top-level global `streaming_services` list
    at all 3 of its per-user call sites (`process_user`'s
    `_pu_categorize_and_stamp` stage, `process_user_movie_library`, and
    `process_user_tv_library`) - a user's personal override was silently
    ignored everywhere.
  - **Assumption implemented (stated per the task, override if wrong):**
    followed the sibling per-user preference `exclude_genres`'s exact
    semantics rather than inventing new ones. `exclude_genres` is the only
    other per-user preference with a real global counterpart to reconcile
    against (`get_excluded_genres_for_user` in `utils/plex.py`), and it
    **merges** (global set UNION per-user list) rather than overriding.
    `max_rating` was considered too, but has no global counterpart at all
    to override/merge against, so it wasn't a useful precedent for a
    list-shaped preference like `streaming_services`. New
    `get_streaming_services_for_user()` in `utils/plex.py` mirrors
    `get_excluded_genres_for_user()` line-for-line: a per-user
    `streaming_services` override is UNIONed onto the global list
    (de-duplicated, original order preserved), never replacing it - so a
    user with a personal override still benefits from services the
    household/global config already lists.
  - **No behavior change for any user with no personal override**: the
    merge is a no-op when `users.preferences.<user>.streaming_services`
    isn't set, exactly matching today's global-list-only behavior.
  - Threaded `username` through to `_pu_categorize_and_stamp` (previously
    had no username parameter at all) so all 3 call sites can resolve the
    per-user override.
  - Tests added: `utils/plex.py`'s new function directly (no override, merge,
    de-dup, no-username, user-with-no-override-unaffected) in
    `tests/test_plex.py`, plus wiring tests at all 3 call sites in
    `tests/test_external.py` (including a same-config-different-user test
    proving one user's override never leaks onto another user's run).

## [2.10.34] - 2026-07-26

### Fixed

- **`limit_results` was dead config - wire it to actually control the final recommendation/collection count (audit remediation, PR1).**

  - `config/tuning.yml`'s documented `movies:`/`tv:` `limit_results` key was
    parsed by the web UI and written back to disk, but nothing in
    `recommenders/base.py` ever read it. The real output count was
    driven entirely by an undocumented `general.limit_plex_results`,
    resolved independently (with two different hardcoded 100/40-vs-50/20
    defaults) at two separate call sites: once for the number of
    candidates scored/printed per run (`self.limit_plex_results`), and
    again, completely independently, for the number of items that
    actually survive into the Plex collection (`target_count` inside
    `manage_plex_labels()`).
  - **Assumption implemented (stated per the task, override if wrong):**
    `limit_results` is the user-facing *final* recommendation/collection
    count; `limit_plex_results` is the internal candidate-scoring buffer,
    which must be at least as large as `limit_results` (candidates get
    filtered/ranked down to the final count, so the buffer can never be
    smaller in the computed-default case). `self.limit_results` now
    resolves from `movies:`/`tv:` `limit_results` (falling back to the
    same 50/20 defaults as before when unset - `DEFAULT_LIMIT_RESULTS` in
    `utils/config.py`), and `target_count` in `manage_plex_labels()` now
    reads `self.limit_results` directly instead of re-deriving its own
    independent 50/20 default from `general.limit_plex_results`.
  - The candidate buffer (`self.limit_plex_results`) is now derived from
    `self.limit_results * CANDIDATE_BUFFER_MULTIPLIER` (2x, matching the
    pre-existing "generate 2x the collection target" comment) when
    `general.limit_plex_results` is left unset - so the buffer-holds-at-
    least-the-limit invariant holds by construction for every install
    that hasn't touched either key.
  - **No behavior change for any install that hasn't set `limit_results`**:
    with both keys unset, `limit_results` resolves to the same 50/20
    defaults, and `limit_plex_results` still resolves to the same 100/40
    defaults, as before this fix.
  - **Behavior change for the narrow, previously-undocumented case of an
    install that explicitly set `general.limit_plex_results` to influence
    the final collection size**: that key no longer drives the final
    collection size - only `limit_results` does now. This is the actual
    bug being fixed (the audit specifically flagged `limit_plex_results`
    as the undocumented driver of "the actual output count"). An explicit
    `general.limit_plex_results` override is still honored exactly as
    configured for the candidate buffer itself (not clamped up to
    `limit_results`), matching this repo's existing tested behavior for
    that key.
  - Retired the dead, movie-only `DEFAULT_LIMIT_PLEX_RESULTS = 100`
    constant (added in a prior pass but never wired to anything) in favor
    of `DEFAULT_LIMIT_RESULTS` (a `{"movie": 50, "tv": 20}` dict, the
    actual pre-existing defaults) and `CANDIDATE_BUFFER_MULTIPLIER = 2`.
  - Documented both `movies:`/`tv:` `limit_results` and
    `general.limit_plex_results` in `config/tuning.example.yml`,
    `config/config.example.yml`, and `README.md` - previously only
    `limit_results` had even a one-line comment, and
    `limit_plex_results` wasn't documented anywhere.
  - Tests added to `tests/test_base.py`: `limit_results` unset (old 50/20
    +100/40 behavior preserved), `limit_results` set (honored, buffer
    scales 2x), the buffer `>=` limit invariant for the computed default,
    an explicit `general.limit_plex_results` override still honored
    exactly (regression guard for the existing, unchanged
    `test_init_loads_display_options` test), and `manage_plex_labels()`'s
    `target_count` now reading `self.limit_results`.

## [2.10.33] - 2026-07-26

### Fixed

- **`post-release-smoke-test.yml`'s `cosign-verify` bounded retry never
  retried, and the same broken pattern existed in two other workflows
  (#243).**

  - The `cosign-verify` job's retry loop (added in 2.10.25 to survive
    the race where this smoke test runs before `docker.yml` finishes
    pushing and signing the image) never executed a single retry.
    GitHub Actions runs every `run:` step as `bash -e {0}`; the step's
    own `set -uo pipefail` does not (and cannot) clear that inherited
    `-e`. `OUTPUT="$(cosign verify ...)"` is a bare command-substitution
    assignment, not inside any conditional - so on the very first
    `MANIFEST_UNKNOWN` failure, errexit killed the script immediately,
    before the following `STATUS=$?` line or any retry logic ever ran.
    Confirmed in the real v2.10.32 run: the job died in well under a
    second with zero "Attempt N/20 failed" diagnostics.
  - `release.yml` and `docker.yml`'s "Verify tag is signed by the
    trusted release key" steps had the identical shape
    (`VERIFY_OUTPUT="$(git verify-tag ...)"; VERIFY_EXIT=$?`) - lower
    severity there (the step still failed the job either way, since
    errexit's own exit code propagates), but the diagnostic
    `git verify-tag` output and the `::error::` annotation explaining
    *why* were both silently lost on every failure.
  - Fixed all three by moving the failing command substitution directly
    into the `if` condition (`if OUTPUT="$(...)"; then ... else ...
    fi`), which is the one context bash's errexit does not apply to -
    confirmed with a fake `cosign` on PATH covering fails-twice-then-
    succeeds (retries and passes), always-fails (exhausts its budget
    and fails non-zero, loudly, with every attempt's output printed),
    and succeeds-immediately (no spurious retry delay).
  - v2.10.32 itself was not affected - a manual re-dispatch of the
    smoke test after `docker.yml` finished passed every check (cosign
    verify, glibc floor, all 8 distro/arch starts, real self-update).
    The auto-filed failure was this bug plus the pre-existing
    not-published-yet race it was supposed to survive.

## [2.10.32] - 2026-07-26

### Fixed

- **Single-user runs could delete every other configured user's cache
  (#233 audit remediation batch D / PR1(b) follow-up, found in
  pre-release review).**

  - `utils.cli.run_recommender_main`'s cache-orphan pruning (added in
    2.10.31 alongside per-library caching) collected `resolved_usernames`
    only from the users actually processed *this run*, then pruned
    `cache/` against that set unconditionally. Single-user mode (`python3
    recommenders/movie.py alice`, or any web-UI-triggered per-user job)
    narrows the processed-user list to just that one user, so every
    OTHER configured user's cache was misclassified as orphaned - logged
    as a false "would remove" candidate under the `dry_run: true`
    default, and actually deleted the first time an admin flipped
    `general.cache_prune.dry_run` to `false` (a reasonable next step
    after reviewing dry-run output), forcing a full Plex re-scan for
    every user but the one just run.
  - Fixed by skipping the prune step entirely on single-user runs -
    `resolved_usernames` only ever reflects every currently-configured
    user on a full run (cron, the web UI's "full" engine, or a bare
    `python3 recommenders/movie.py` with no username), which is the only
    case this feature can tell "removed from config" apart from "just
    not in this run". A user genuinely removed from config is still
    caught on the next full run, so the feature keeps working exactly as
    designed for its actual purpose.
  - Considered instead passing every configured user (not just this
    run's) into the prune call, but that requires re-deriving each
    user's *resolved* form (e.g. `admin` -> the real Plex account
    username - see `utils.cache_prune.find_orphaned_cache_files`'s own
    docstring on why the caller must pass resolved, not raw config,
    names) for users never actually processed this run, without
    duplicating `resolve_admin_username`'s Plex API call for every
    configured user on every single-user run. Skipping is simpler and
    carries none of that risk of inverting into the same bug the other
    direction (deleting a live cache because its raw config name doesn't
    match its resolved cache-file name).
  - Added regression coverage: a single-user run leaves another
    configured user's cache untouched even with `dry_run: false`; a full
    run still prunes a user genuinely removed from `users.list`; and a
    full run classifies by the *resolved* admin username, not the raw
    `admin` config string, so this can't silently invert into deleting a
    live cache in a future change.

## [2.10.31] - 2026-07-26

### Changed

- **Lint and test-hygiene debt: `ruff check` clean, mypy reduced, real cache-dir test leak fixed.**

  - `ruff check` was at 126 violations; all fixed or explicitly disabled
    with a documented reason, and CI's `lint` job's `ruff check` step is
    now blocking (its `continue-on-error` is removed, matching `ruff
    format --check`'s own precedent from 2.10.14):
    - `F401` (6 - the DANGEROUS ones): every one confirmed live by
      grepping all callers, including tests, before touching anything -
      `recommenders/external.py`'s `sync_watch_history_to_trakt`
      (imported by `trakt_sync.py`'s entry point),
      `SERVICE_DISPLAY_NAMES`/`get_tmdb_id_from_imdb` (imported by
      `tests/test_external.py`), and `web/config_test_connection.py`'s
      `RadarrAPIError`/`SonarrAPIError`/`TautulliAPIError` (imported by
      `tests/test_web_config_test_connection.py` via its `cc` alias) are
      all cross-module re-exports. Fixed with an explicit `__all__` in
      each module (not deletion - see the [2.10.14] entry on a prior
      `ruff --fix` pass that deleted six "unused" imports outright and
      broke the suite).
    - `E501` (57): mechanically split long `print()`/`log_warning()`/
      `logger.debug()` f-string messages across adjacent string
      literals (identical message content, just wrapped). The
      `recommenders/external_render.py` (embedded HTML/CSS/JS template
      strings) and `utils/self_update_handoff.py` (embedded PowerShell/
      shell handoff scripts) cases are markup/script content, not
      Python - wrapping those to fit the 120-column convention risks
      altering the generated output for no readability gain, so both
      get a one-line-justified `ruff.toml` per-file ignore instead. Two
      `# pragma: no cover` explanatory comments (`curatarr_app.py` x2,
      `web/update_apply.py`) that must stay on their annotated line get
      a targeted `# noqa: E501` instead, matching `tests/harness.py`'s
      existing precedent - a per-file ignore would be too broad for two
      one-off lines in otherwise fully-compliant files.
    - `F841`, `E402`, `B904`, `E731`, `B007`, `E741`, `B017`: fixed for
      real (dropped pointless variable assignments while keeping the
      exercised call, added `from e`/`from None` exception chaining,
      rewrote test-only lambdas as `def`s, renamed genuinely-unused loop
      variables to `_`-prefixed, renamed ambiguous `l` to a descriptive
      name, narrowed one `pytest.raises(Exception)` to the specific
      type actually raised).
  - `mypy` (non-strict, still non-blocking in CI): reduced from 233 to
    180 errors by adding explicit type annotations to every `[var-
    annotated]` finding (44 of them) - a purely additive fix (no
    runtime behavior change) that mypy can't infer on its own for a
    dict/list/set literal. The remaining ~180 (mostly `[assignment]`/
    `[attr-defined]`/`[str]`) are left for a future pass; this codebase
    is only partially annotated by design.
  - Tests were leaking fake per-user cache files (`tv_watched_cache_
    plex_bob.json`, `tv_watched_cache_plex_user1.json`, `tv_watched_
    cache_anime_plex_user1.json`) into the REAL `cache/` directory:
    `recommenders/base.py`'s `BaseRecommender.__init__` and several
    `recommenders/external.py` functions resolve `cache_dir` via
    `get_project_root()` (`@lru_cache`'d), and most tests never
    override `CURATARR_CONFIG_DIR` or mock it - `tests/test_tv.py` in
    particular never did at all. Fixed with an autouse
    `_isolated_recommender_cache_dir` fixture in `tests/conftest.py`,
    matching the existing `_isolated_update_dismissal_dir`/
    `_isolated_metrics_dir` pattern (patches the *consuming* module's
    own `get_project_root` name binding, not the shared origin - each
    importer copied its own reference at import time). Still honors an
    explicitly-set `CURATARR_CONFIG_DIR` so it can't break the two
    tests that deliberately exercise the real resolution logic against
    one. Also discovered and fixed a related, more serious bug while
    building this: with only `get_project_root` faked out,
    `recommenders/base.py`'s `migrate_legacy_cache_dir` call (whose
    `legacy_dir` argument bypasses `get_project_root()` by design, so it
    still resolved to the REAL repo `cache/` directory) would treat that
    real directory as "legacy" relative to the fixture's fake `new_dir`
    and `shutil.move()` every real file out of it into a throwaway
    `tmp_path` on every test run - silently deleting real cache/
    contents, not just stopping new writes. The fixture also no-ops
    `recommenders.base.migrate_legacy_cache_dir` during tests to close
    that. Verified with a planted canary file that survives a full test
    run. The three leaked fake-user files above were removed from the
    real `cache/` directory (confirmed fake by content and username -
    real users' cache files were never touched).

## [2.10.30] - 2026-07-26

### Changed

- **Web hardening: login rate limiting, conditional Secure cookie, static assets.**

  Three independent hardening fixes to `web/security.py`, found during
  an audit pass:

  - `POST /login` had no failed-attempt limiting at all - an attacker
    (or a script) could hammer the endpoint as fast as the process
    could handle requests. Added a per-source-IP failed-attempt
    counter: 5 failures within a rolling 60-second window locks that
    IP out (rejected outright, without even checking the token, so
    continuing to hit the endpoint while locked out can't extend the
    lockout). Never permanent - the window ages out on its own with no
    operator intervention, and a single successful login clears the
    IP's history immediately. In-process/in-memory, no new dependency
    (flask-limiter is not already a requirement). Every failed attempt
    is logged (source IP only, never the attempted token).
  - The `curatarr_token` cookie always omitted `Secure`, on the
    reasoning that this app is served over plain HTTP by design. That's
    still true for the common case, but self-hosters commonly put a
    TLS-terminating reverse proxy (nginx/Caddy/Traefik) in front of it.
    `Secure` is now set whenever the request actually arrived over TLS
    - directly, or via a trusted proxy's `X-Forwarded-Proto`, gated
    behind an explicit `CURATARR_TRUST_PROXY_PROTO=true` opt-in (that
    header is otherwise exactly as spoofable by a direct caller as any
    other request header - unset by default, so every existing install
    is unaffected).
  - `_TOKEN_EXEMPT_PATHS` only exempted `/login` and `/healthz` - on a
    non-loopback bind, the login page's own `/static/style.css` 401'd
    before the browser had a token to present, breaking the login page
    itself. Static assets are now exempt too (nothing else is).

## [2.10.29] - 2026-07-26

### Changed

- **Audit remediation batch F (PR1): web/ layer structure.**

  `web/config_app.py` (~1246 lines - four near-identical view/parse/apply
  CRUD screens plus one dispatcher) split into one module per screen,
  each registering its own routes: `config_connections.py` (Setup /
  Connections + the Test Connection endpoint), `config_users.py`,
  `config_libraries.py` (#157 Phase 4), `config_settings.py`. The shared
  `_commit_modules` save orchestration moved to `config_io.py` (renamed
  `commit_modules`, alongside the `validate_merge`/`save_module` it
  wraps) rather than staying in `config_app.py`, since keeping a shared
  helper split behind per-screen modules that imported it back from the
  dispatcher would have created an import cycle (`config_app` ->
  `config_connections` -> `config_app`); `USER_MODE_CHOICES` (shared by
  the Connections and Settings screens) moved there too. `config_app.py`
  is now a ~15-line dispatcher: four imports, four calls.
  `tests/test_web_config_connections.py`'s one test that reached into
  `config_app`'s internals (patching `validate_merge` to simulate a merge
  failure) now patches it on `config_io` instead, matching where the
  code actually lives.

  `utils/plex.py` (~1283 lines) split into the Plex API adapter (this
  file - connection setup, watch-history fetches, collection CRUD) and
  `utils/plex_policy.py` (new - content-rating hierarchy constants,
  `get_max_rating_for_user`, `is_rating_allowed`, and
  `apply_user_label_restrictions`, i.e. Curatarr's own rating/label
  policy rather than "how do we talk to Plex"). `plex_policy` imports
  from `plex` (its `_capped_get`/`_capped_put` HTTP helpers) - never the
  reverse, so there's no import cycle; verified via a clean
  `import utils.plex; import utils.plex_policy` before committing to this
  direction. `utils/__init__.py`'s barrel re-exports the same five names
  from their new home; every caller that imports them by name (directly
  or via `from utils import ...`) is unaffected since Python resolves
  those by name, not by which submodule originally defined them - the
  two direct `from utils.plex import ...` call sites
  (`web/config_app.py`, `tests/test_plex.py`) and the handful of
  `@patch("utils.plex.MyPlexAccount"/"utils.plex.requests...")` targets
  inside `tests/test_plex.py`'s `TestApplyUserLabelRestrictions` were
  updated to point at `utils.plex_policy` instead. PyInstaller build
  and a running frozen binary's `/config/*` routes were both verified
  after the split (see RELEASING.md's own "renames have broken the
  frozen build before" history).

  While touching `utils/__init__.py`: its `__all__` was missing 31 of the
  names it already imports (config/tmdb/trakt/plex constants and
  functions ruff's F401 could only see as "unused" because they weren't
  declared re-exported) - added to `__all__`, none removed, so the
  now-redundant `"utils/__init__.py" = ["F401"]` per-file-ignore in
  `ruff.toml` was also removed (see batch I / PR3 for the rest of the
  lint pass).

  Added one Jinja macro, `field_error(errors, name)`
  (`web/templates/_macros.html`), replacing 41 hand-repeated
  `{% if errors.X %}<p class="field-error">{{ errors.X }}</p>{% endif %}`
  blocks (including the dynamic-key ones, e.g.
  `errors['name_' ~ loop.index0]`) across all four `config_*.html`
  templates - the first `{% macro %}` in this codebase.

  Docstring coverage brought to parity with the rest of the codebase for
  every module touched above: every route handler, every `_view`/
  `_parse_*_form`/`_apply_*` function, and `config_io.commit_modules` now
  has one explaining what it does and why, not filler.

## [2.10.28] - 2026-07-26

### Changed

- **Audit remediation batch E (PR3): hoisted nested helpers and split
  `process_user`.** `generate_combined_html`
  (`recommenders/external_render.py`) defined 4 helper functions INSIDE
  itself (`collect_tmdb_ids_from_categorized`, `render_table_flat`,
  `render_sequels_table`, `render_horizon_table`) - hoisted to module level
  (now `_collect_tmdb_ids_from_categorized`/`_render_table_flat`/
  `_render_sequels_table`/`_render_horizon_table`, matching this file's
  existing underscore-prefix convention for internal helpers), taking
  their previously-closure-captured variables (`imdb_cache`,
  `all_imdb_ids`, `pending_lookups`, `now`, `movie_counts`, `show_counts`,
  `total_users`) as explicit parameters instead.

  `process_user` (`recommenders/external.py`, ~373 lines, 44 branches but
  shallow/linear nesting) split into 9 named pipeline stages
  (`_pu_resolve_context`, `_pu_load_libraries_and_caches`,
  `_pu_clean_caches`, `_pu_build_profiles`, `_pu_plan_discovery`,
  `_pu_discover_new_content`, `_pu_reconcile_caches`,
  `_pu_categorize_and_stamp`, `_pu_finalize_output`), called in the exact
  original order - existing tests assert on mocked dependencies' call
  order (e.g. movie library/cache operations before tv), so stage
  boundaries were chosen to never reorder a call relative to another.
  `process_user`'s own signature and return contract are unchanged.

  Evaluated but **skipped** two other audit-flagged candidates, per the
  audit's own "skip and report if not reviewable" guidance:
  `find_similar_content_with_profile` (~249 lines) has a discovery loop
  with genuine cross-iteration mutable state (`quality_recs`, `seen_ids`,
  `scored_cache`, early-exit counters) - splitting it out is materially
  riskier than `process_user`'s linear pipeline and not attempted here.
  `export_to_sonarr`/`export_to_radarr` (~230/227 lines) are structurally
  parallel but not simple 1:1 substitutions - they use different exception
  types (`SonarrAPIError`/`RadarrAPIError`), different client methods
  (`get_series()`/`get_movies()`), different id fields (`tvdb_id`/
  `tmdb_id`), and non-overlapping per-service settings
  (`season_folder`/`series_type` vs `minimum_availability`) - factoring
  the shared part safely would need a real abstraction layer, a
  substantially bigger and harder-to-review diff than the rest of this
  batch.

  Verified behavior-preserving: `tests/harness.py` byte-identical
  `PYTHONHASHSEED=0` output (this batch doesn't touch scoring, re-checked
  anyway); a synthetic watchlist page byte-identical before/after; a full
  `pyinstaller curatarr.spec` build + running the resulting frozen binary.
  Added 11 new tests for the hoisted/extracted functions (2448 passed, up
  from 2437).

## [2.10.27] - 2026-07-26

### Changed

- **Audit remediation batch E (PR2): split `_generate_html_template`
  (`recommenders/external_render.py`) - ~1389 lines (71% of the file)
  building the entire watchlist page (markup + `<style>` + `<script>`) as
  one nested f-string - into four per-concern helpers:
  `_html_head_and_style` (meta/fonts/CSS), `_html_body_header` (curtain/
  brand/export buttons/filter bar), `_html_tabs_and_panels` (tabs/huntarr
  row/panels/instructions/footer), and `_html_script` (the `<script>`
  block). `_generate_html_template` now just concatenates them in order;
  its signature and return contract are unchanged.

  **Helper functions, not Jinja2 templates.** `web/templates/` proves this
  codebase does Jinja properly for the web UI, and Jinja2 is already a
  dependency there - but only in `requirements-ui.txt`/`requirements-ui.lock`
  (installed by `run-ui.sh`/`run-ui.ps1` and the PyInstaller binary's UI
  stack), never in core `requirements.txt`. `external_render.py` is
  imported by `recommenders/external.py` - the plain CLI/cron recommender,
  not gated behind the web UI - so a bare `./run.sh` install (no
  `requirements-ui.txt`) would hit `ModuleNotFoundError: jinja2` the first
  time it tried to render a watchlist. Since the CLI/cron path can't take
  on that dependency, this split uses plain helper functions instead.

  Two of the four fragments (`_html_head_and_style`, `_html_script`) have
  no `{variable}` interpolation at all - they were only escaped as `{{`/
  `}}` in the original because they shared one big f-string with fragments
  that do interpolate. Verified every brace in both fragments is part of
  a balanced `{{`/`}}` pair (no stray/odd braces) before un-escaping them
  to plain (non-f) strings with single braces, which resolves ruff's F541
  ("f-string without placeholders") that a naive split would have
  introduced as a new lint finding, with identical runtime string output.

  Verified behavior-preserving three ways: `tests/harness.py` byte-identical
  `PYTHONHASHSEED=0` output before/after (unaffected by this file, but
  re-checked); a synthetic two-user watchlist page generated via
  `generate_combined_html` with a frozen clock, byte-identical before/after
  (`diff` clean); and a full `pyinstaller curatarr.spec` build + running the
  resulting frozen binary (`--help`/`--version`, arm64), confirming the
  split didn't break the frozen build. Added 6 new tests for the extracted
  helpers (2437 passed, up from 2431).

## [2.10.26] - 2026-07-26

### Changed

- **Audit remediation batch E (PR1): split `calculate_similarity_score`
  (`utils/scoring.py`) out of one ~414-line function with 51 branch
  statements and nesting to depth 6-7 into named, independently testable
  helpers.** One helper per scoring dimension -
  `_score_genre_component`/`_score_director_component`/
  `_score_studio_component`/`_score_actor_component`/
  `_score_language_component`/`_score_keyword_component` (the latter two
  each also carry their dimension's embedded TF-IDF rarity penalty, via a
  new shared `_tfidf_threshold` helper) - plus
  `_apply_active_weight_redistribution` and `_apply_popularity_dampening`
  for the two cross-dimension passes. The five independent tuning
  flags/thresholds (`normalize_counters`, `use_fuzzy_keywords`,
  `use_tfidf`, `tfidf_penalty_threshold`, `use_popularity_dampening`,
  `popularity_threshold`) collapse into one frozen `ScoringOptions`
  dataclass; the public function keeps accepting the original individual
  keyword arguments unchanged (existing callers/tests untouched) and now
  also accepts an optional `options=` that takes precedence when given.
  Verified behavior-preserving via `tests/harness.py`: byte-identical
  `PYTHONHASHSEED=0` output before/after across the full fixture set,
  plus 36 new tests for the extracted helpers (2431 passed, up from
  2395). No public signature or return contract changed.

## [2.10.25] - 2026-07-26

### Fixed

- **Post-release smoke test's `cosign verify` job raced
  `.github/workflows/docker.yml`'s image build+push+sign, producing
  three false-alarm auto-filed issues (#214, #220, #225 - all
  `MANIFEST_UNKNOWN`, never a real signing problem).**
  `post-release-smoke-test.yml` is dispatched manually (by
  `scripts/sign-release-checksums.sh`, after `release.yml`'s own chain
  finishes) with no ordering relationship to `docker.yml`, a separate
  workflow independently triggered by the same tag push - its
  multi-arch (QEMU) build+push+cosign-sign can still be in flight when
  the smoke test's `cosign verify` step runs. There's no
  `needs:`/`workflow_run` link between a manually-dispatched workflow
  and a specific run of a different, independently-triggered one, and
  even if there were, matching runs by ref would be less reliable than
  just checking whether the artifact this job actually needs (a signed
  image manifest) exists yet. Fixed by retrying the exact same `cosign
  verify` command/flags (identity regexp + OIDC issuer, unchanged) up
  to 20 times with a 30s sleep (10 minutes total) instead of failing on
  the first attempt. This never weakens verification: a genuinely
  unsigned image or wrong signing identity fails identically on every
  attempt (nothing about that failure is time-dependent), so it still
  fails for real once the budget is exhausted - retrying only gives the
  "not published yet" race a chance to resolve itself. Verified the
  retry/give-up control flow directly (a fake `cosign` that fails twice
  then succeeds, and one that always fails).

## [2.10.24] - 2026-07-26

### Changed

- **Audit remediation batch D (PR1): fewer redundant Plex fetches per run.**
  `recommenders/movie.py`/`tv.py`'s init path called Plex's
  `section.all()` up to 6 times per user per run - once each from
  `MovieCache`/`ShowCache.update_cache`, `_get_library_movies_set`
  (itself called twice back-to-back for a byte-identical result),
  `_get_library_movie_titles`/`_get_library_shows_set`,
  `_get_library_imdb_ids`, and again inside `_get_plex_watched_data`/
  `_get_plex_watched_shows_data` for view counts - and `utils/cli.py`'s
  library-outer/user-inner loop constructed a fresh recommender per
  user with nothing shared across them either. `BaseRecommender` now
  fetches the full library once via a new `_get_all_library_items()`
  and every one of those consumers reuses it; `utils/cli.py` shares one
  fetch across every user processed against the same library in a run
  too. Measured before/after (instrumented mock call count, see
  `tests/test_movie.py::TestLibraryFetchedOnceNotSixTimes`): 6 calls to
  `section.all()` down to 1 for a single user's instantiation.
  Behavior is unchanged - only fewer Plex round trips.

### Added

- **Audit remediation batch D (PR1): prune orphaned per-user cache
  files.** `cache/` had no equivalent of `logs/`'s `cleanup_old_logs` -
  nothing ever removed a per-user cache file for a user no longer
  configured (a real example found in a live install: a joined
  multi-user `watched_cache_plex_<many-usernames>.json` left over from
  before every run became strictly single-user, plus
  `external_recs_<removed-user>_*.json`). New `utils/cache_prune.py`
  diffs the four known per-user cache filename patterns (shared with
  `utils/user_migration.py`'s rename-migration, now exported as
  `CACHE_FILENAME_PATTERNS`) against the run's actual resolved
  usernames. Conservative by design: a file that doesn't match one of
  those exact patterns is never touched, and the new
  `general.cache_prune` config (`enabled: true` by default) only logs
  candidates - nothing is deleted until `dry_run: false` is also set
  explicitly.
- **Audit remediation batch D (PR1): cross-container run lock.**
  `docker-compose.yml`'s `curatarr` (web UI) and `curatarr-recommend`
  services share the same bind-mounted `./cache` volume, but only the
  web UI's `job_runner.py` ever took a run lock (in-process +
  PID-lockfile, meaningless across two containers' separate PID
  namespaces) - `docker-entrypoint.sh`'s `recommend` mode had no
  locking at all. Both now take the identical `flock` on a
  `cache/.recommender_run.lock` file shared by both containers -
  `job_runner.py` via a new `utils/run_lock.py` (POSIX only; a
  documented no-op on Windows, where this race can't happen),
  `docker-entrypoint.sh` via the `flock` command directly. Verified
  against real Docker containers (not just mocks): a `recommend`
  invocation correctly refuses to start while the lock is held by a
  separate container, and the web UI's job runner correctly refuses to
  launch a subprocess while `docker-entrypoint.sh` holds it. Residual
  risk: a direct `python3 recommenders/movie.py` invocation bypassing
  both of those paths still isn't covered - threading the lock through
  every recommender entry point was judged too invasive for this pass
  (see `utils/run_lock.py`'s docstring). Cache writes were already
  atomic (temp file + `os.replace()`, added in 2.10.9) - verified still
  true across every cache write path (`utils/cache.py`'s
  `_atomic_write_json`).

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
