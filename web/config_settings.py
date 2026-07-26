"""Settings / Tuning screen: scoring weights, quality filters, recency
decay, rating multipliers, negative signals, external-recommendations
tuning, general/logging options, and the sync-safety fields also
editable (with less context) on the Connections screen.

Split out of web/config_app.py (audit remediation batch F/I, PR1(a)) -
see that module's docstring for the overall package layout this is one
quarter of. Registers its own routes via register_settings_routes(),
called once from web.config_app.register_config_routes().
"""

from typing import Dict, Optional

from flask import redirect, render_template, request, url_for
from ruamel.yaml.comments import CommentedMap

from utils.config import UPDATE_MODES, get_update_mode

from .config_io import (
    USER_MODE_CHOICES,
    commit_modules,
    ensure_section,
    format_csv_list,
    load_module,
    module_path,
    parse_csv_list,
)
from .config_validate import validate_choice, validate_float, validate_int, validate_weights_sum

LOG_LEVEL_CHOICES = ("DEBUG", "INFO", "WARNING", "ERROR")


def register_settings_routes(app) -> None:
    """Register GET/POST /config/settings onto *app* - see this module's
    own docstring for what the screen owns."""
    project_root = app.config["PROJECT_ROOT"]

    def _load_all():
        return (
            load_module(module_path(project_root, "tuning")),
            load_module(module_path(project_root, "config")),
            load_module(module_path(project_root, "sonarr")),
            load_module(module_path(project_root, "radarr")),
            load_module(module_path(project_root, "trakt")),
        )

    @app.get("/config/settings")
    def config_settings():
        """Render the Settings screen with the currently-saved values,
        spanning tuning/config/sonarr/radarr/trakt.yml."""
        tuning, core, sonarr, radarr, trakt = _load_all()
        return render_template(
            "config_settings.html",
            saved=request.args.get("saved") == "1",
            errors={},
            **_settings_view(tuning, core, sonarr, radarr, trakt),
        )

    @app.post("/config/settings")
    def config_settings_save():
        """Validate and save the Settings form across tuning/config/
        sonarr/radarr/trakt.yml, re-rendering with field errors on bad
        input or a failed merge dry-run (see config_io.commit_modules)."""
        tuning, core, sonarr, radarr, trakt = _load_all()

        errors: Dict[str, str] = {}
        parsed = _parse_settings_form(request.form, errors)

        if errors:
            return render_template(
                "config_settings.html",
                saved=False,
                errors=errors,
                **_settings_view(tuning, core, sonarr, radarr, trakt, overrides=parsed),
            ), 400

        modules = _apply_settings(project_root, tuning, core, sonarr, radarr, trakt, parsed)
        commit_error = commit_modules(project_root, modules)
        if commit_error:
            reloaded = _load_all()
            return render_template(
                "config_settings.html",
                saved=False,
                errors={"_global": f"Could not save: {commit_error}"},
                **_settings_view(*reloaded),
            ), 500
        return redirect(url_for("config_settings", saved="1"), code=303)


def _settings_view(
    tuning: CommentedMap,
    core: CommentedMap,
    sonarr: CommentedMap,
    radarr: CommentedMap,
    trakt: CommentedMap,
    overrides: Optional[Dict] = None,
) -> Dict:
    """Build the template context for config_settings.html: the on-disk
    values across all five module files (or, after a failed submission,
    *overrides* verbatim - already in this same shape)."""
    if overrides is not None:
        return overrides

    movies = tuning.get("movies") or {}
    tv = tuning.get("tv") or {}
    movies_weights = movies.get("weights") or {}
    tv_weights = tv.get("weights") or {}
    movies_quality = movies.get("quality_filters") or {}
    tv_quality = tv.get("quality_filters") or {}
    recency = tuning.get("recency_decay") or {}
    rating_mult = tuning.get("rating_multipliers") or {}
    negsig = tuning.get("negative_signals") or {}
    bad_ratings = negsig.get("bad_ratings") or {}
    dropped_shows = negsig.get("dropped_shows") or {}
    external = tuning.get("external_recommendations") or {}
    general = core.get("general") or {}
    logging_cfg = core.get("logging") or {}
    trakt_export = trakt.get("export") or {}

    return {
        "movies": {
            "weights": {
                "genre": movies_weights.get("genre", 0.25),
                "director": movies_weights.get("director", 0.05),
                "actor": movies_weights.get("actor", 0.20),
                "keyword": movies_weights.get("keyword", 0.50),
            },
            "limit_results": movies.get("limit_results", 50),
            "min_rating": movies_quality.get("min_rating", 5.0),
            "min_vote_count": movies_quality.get("min_vote_count", 50),
        },
        "tv": {
            "weights": {
                "genre": tv_weights.get("genre", 0.25),
                "studio": tv_weights.get("studio", 0.10),
                "actor": tv_weights.get("actor", 0.20),
                "keyword": tv_weights.get("keyword", 0.45),
            },
            "limit_results": tv.get("limit_results", 20),
            "min_rating": tv_quality.get("min_rating", 0.0),
            "min_vote_count": tv_quality.get("min_vote_count", 0),
        },
        "recency": {
            "enabled": bool(recency.get("enabled", True)),
            "days_0_30": recency.get("days_0_30", 1.0),
            "days_31_90": recency.get("days_31_90", 0.75),
            "days_91_180": recency.get("days_91_180", 0.50),
            "days_181_365": recency.get("days_181_365", 0.25),
            "days_365_plus": recency.get("days_365_plus", 0.10),
        },
        "rating_multipliers": {
            "star_5": rating_mult.get("star_5", 2.5),
            "star_4": rating_mult.get("star_4", 1.7),
            "star_3": rating_mult.get("star_3", 1.0),
            "star_2": rating_mult.get("star_2", 0.4),
            "star_1": rating_mult.get("star_1", 0.2),
        },
        "negative_signals": {
            "enabled": bool(negsig.get("enabled", True)),
            "bad_ratings_enabled": bool(bad_ratings.get("enabled", True)),
            "bad_ratings_threshold": bad_ratings.get("threshold", 3),
            "bad_ratings_cap_penalty": bad_ratings.get("cap_penalty", 0.5),
            "dropped_enabled": bool(dropped_shows.get("enabled", True)),
            "dropped_min_episodes": dropped_shows.get("min_episodes_watched", 2),
            "dropped_max_completion": dropped_shows.get("max_completion_percent", 25),
            "dropped_penalty_multiplier": dropped_shows.get("penalty_multiplier", -0.4),
        },
        "external": {
            "enabled": bool(external.get("enabled", True)),
            "movie_limit": external.get("movie_limit", 50),
            "show_limit": external.get("show_limit", 20),
            "min_relevance_score": external.get("min_relevance_score", 0.65),
            "min_votes": external.get("min_votes", 50),
            "max_iterations": external.get("max_iterations", 5),
            "language": external.get("language") or "",
            "auto_open_html": bool(external.get("auto_open_html", False)),
        },
        "general": {
            # get_update_mode() resolves the effective mode, falling back
            # to the legacy auto_update flag for installs that predate
            # update_mode - so this screen shows the mode that's actually
            # in effect even before a user has ever saved the new field.
            "update_mode": get_update_mode(core),
            "log_retention_days": general.get("log_retention_days", 7),
            "plex_only": bool(general.get("plex_only", True)),
        },
        "logging": {
            "level": logging_cfg.get("level", "INFO"),
        },
        "sync_safety": {
            "sonarr": {
                "auto_sync": bool(sonarr.get("auto_sync", False)),
                "user_mode": sonarr.get("user_mode", "mapping"),
                "plex_users": format_csv_list(sonarr.get("plex_users")),
            },
            "radarr": {
                "auto_sync": bool(radarr.get("auto_sync", False)),
                "user_mode": radarr.get("user_mode", "mapping"),
                "plex_users": format_csv_list(radarr.get("plex_users")),
            },
            "trakt": {
                "auto_sync": bool(trakt_export.get("auto_sync", False)),
                "user_mode": trakt_export.get("user_mode", "mapping"),
                "plex_users": format_csv_list(trakt_export.get("plex_users")),
            },
        },
        "log_level_choices": LOG_LEVEL_CHOICES,
        "user_mode_choices": USER_MODE_CHOICES,
        "update_mode_choices": UPDATE_MODES,
    }


def _parse_settings_form(form, errors: Dict[str, str]) -> Dict:
    """Parse+validate the raw POSTed form into the same shape
    _apply_settings expects, appending to *errors* for any invalid field
    instead of raising."""

    def flag(name: str) -> bool:
        return form.get(name) in ("on", "true", "1")

    def f(name, lo=None, hi=None, label=None):
        # On a parse failure, redisplay whatever the user typed (instead of
        # None) so the error-correction round trip doesn't show "None" in
        # the field they need to fix.
        parsed = validate_float(form.get(name), name, errors, lo=lo, hi=hi, label=label)
        return parsed if parsed is not None else form.get(name, "")

    def i(name, lo=None, hi=None, label=None):
        parsed = validate_int(form.get(name), name, errors, lo=lo, hi=hi, label=label)
        return parsed if parsed is not None else form.get(name, "")

    movies_weight_fields = (
        "movies_weight_genre",
        "movies_weight_director",
        "movies_weight_actor",
        "movies_weight_keyword",
    )
    movies_weights = {
        "genre": f("movies_weight_genre", 0, 1, "Movie genre weight"),
        "director": f("movies_weight_director", 0, 1, "Movie director weight"),
        "actor": f("movies_weight_actor", 0, 1, "Movie actor weight"),
        "keyword": f("movies_weight_keyword", 0, 1, "Movie keyword weight"),
    }
    # Only check the sum if every individual weight parsed cleanly -
    # summing raw invalid strings would raise instead of reporting a
    # clean validation error.
    if not any(field in errors for field in movies_weight_fields):
        validate_weights_sum(movies_weights, "movies_weights", errors)

    tv_weight_fields = ("tv_weight_genre", "tv_weight_studio", "tv_weight_actor", "tv_weight_keyword")
    tv_weights = {
        "genre": f("tv_weight_genre", 0, 1, "TV genre weight"),
        "studio": f("tv_weight_studio", 0, 1, "TV studio weight"),
        "actor": f("tv_weight_actor", 0, 1, "TV actor weight"),
        "keyword": f("tv_weight_keyword", 0, 1, "TV keyword weight"),
    }
    if not any(field in errors for field in tv_weight_fields):
        validate_weights_sum(tv_weights, "tv_weights", errors)

    movies = {
        "weights": movies_weights,
        "limit_results": i("movies_limit_results", 1, 1000, "Movie result limit"),
        "min_rating": f("movies_min_rating", 0, 10, "Movie min rating"),
        "min_vote_count": i("movies_min_vote_count", 0, None, "Movie min vote count"),
    }
    tv = {
        "weights": tv_weights,
        "limit_results": i("tv_limit_results", 1, 1000, "TV result limit"),
        "min_rating": f("tv_min_rating", 0, 10, "TV min rating"),
        "min_vote_count": i("tv_min_vote_count", 0, None, "TV min vote count"),
    }
    recency = {
        "enabled": flag("recency_enabled"),
        "days_0_30": f("recency_days_0_30", 0, None, "0-30 day weight"),
        "days_31_90": f("recency_days_31_90", 0, None, "31-90 day weight"),
        "days_91_180": f("recency_days_91_180", 0, None, "91-180 day weight"),
        "days_181_365": f("recency_days_181_365", 0, None, "181-365 day weight"),
        "days_365_plus": f("recency_days_365_plus", 0, None, "365+ day weight"),
    }
    rating_multipliers = {
        "star_5": f("rating_star_5", 0, None, "5-star multiplier"),
        "star_4": f("rating_star_4", 0, None, "4-star multiplier"),
        "star_3": f("rating_star_3", 0, None, "3-star multiplier"),
        "star_2": f("rating_star_2", 0, None, "2-star multiplier"),
        "star_1": f("rating_star_1", 0, None, "1-star multiplier"),
    }
    negative_signals = {
        "enabled": flag("negsig_enabled"),
        "bad_ratings_enabled": flag("negsig_bad_ratings_enabled"),
        "bad_ratings_threshold": i("negsig_bad_ratings_threshold", 0, 10, "Bad rating threshold"),
        "bad_ratings_cap_penalty": f("negsig_bad_ratings_cap_penalty", 0, 1, "Bad rating cap penalty"),
        "dropped_enabled": flag("negsig_dropped_enabled"),
        "dropped_min_episodes": i("negsig_dropped_min_episodes", 0, None, "Min episodes watched"),
        "dropped_max_completion": i("negsig_dropped_max_completion", 0, 100, "Max completion percent"),
        "dropped_penalty_multiplier": f("negsig_dropped_penalty_multiplier", None, None, "Dropped show penalty"),
    }
    external = {
        "enabled": flag("ext_enabled"),
        "movie_limit": i("ext_movie_limit", 0, None, "External movie limit"),
        "show_limit": i("ext_show_limit", 0, None, "External show limit"),
        "min_relevance_score": f("ext_min_relevance_score", 0, 1, "Min relevance score"),
        "min_votes": i("ext_min_votes", 0, None, "Min votes"),
        "max_iterations": i("ext_max_iterations", 1, None, "Max iterations"),
        "language": form.get("ext_language", "").strip(),
        "auto_open_html": flag("ext_auto_open_html"),
    }
    update_mode = form.get("general_update_mode", "notify")
    validate_choice(update_mode, "general_update_mode", errors, UPDATE_MODES)
    general = {
        "update_mode": update_mode,
        "log_retention_days": i("general_log_retention_days", 0, None, "Log retention days"),
        "plex_only": flag("general_plex_only"),
    }
    logging_level = form.get("logging_level", "INFO")
    validate_choice(logging_level, "logging_level", errors, LOG_LEVEL_CHOICES)

    sync_safety = {}
    for svc in ("sonarr", "radarr", "trakt"):
        user_mode = form.get(f"{svc}_user_mode", "mapping")
        validate_choice(user_mode, f"{svc}_user_mode", errors, USER_MODE_CHOICES)
        sync_safety[svc] = {
            "auto_sync": flag(f"{svc}_auto_sync"),
            "user_mode": user_mode,
            "plex_users": format_csv_list(parse_csv_list(form.get(f"{svc}_plex_users", ""))),
        }

    return {
        "movies": movies,
        "tv": tv,
        "recency": recency,
        "rating_multipliers": rating_multipliers,
        "negative_signals": negative_signals,
        "external": external,
        "general": general,
        "logging": {"level": logging_level},
        "sync_safety": sync_safety,
        "log_level_choices": LOG_LEVEL_CHOICES,
        "user_mode_choices": USER_MODE_CHOICES,
        "update_mode_choices": UPDATE_MODES,
    }


def _apply_settings(
    project_root: str,
    tuning: CommentedMap,
    core: CommentedMap,
    sonarr: CommentedMap,
    radarr: CommentedMap,
    trakt: CommentedMap,
    parsed: Dict,
) -> Dict[str, CommentedMap]:
    """Mutate the in-memory CommentedMaps for this screen and return them
    keyed by module name, WITHOUT writing to disk - see
    config_connections._apply_connections' docstring for why."""
    movies_section = ensure_section(tuning, "movies")
    movies_section["limit_results"] = parsed["movies"]["limit_results"]
    ensure_section(movies_section, "weights").update(parsed["movies"]["weights"])
    movies_quality = ensure_section(movies_section, "quality_filters")
    movies_quality["min_rating"] = parsed["movies"]["min_rating"]
    movies_quality["min_vote_count"] = parsed["movies"]["min_vote_count"]

    tv_section = ensure_section(tuning, "tv")
    tv_section["limit_results"] = parsed["tv"]["limit_results"]
    ensure_section(tv_section, "weights").update(parsed["tv"]["weights"])
    tv_quality = ensure_section(tv_section, "quality_filters")
    tv_quality["min_rating"] = parsed["tv"]["min_rating"]
    tv_quality["min_vote_count"] = parsed["tv"]["min_vote_count"]

    ensure_section(tuning, "recency_decay").update(parsed["recency"])
    ensure_section(tuning, "rating_multipliers").update(parsed["rating_multipliers"])

    ns = parsed["negative_signals"]
    negsig = ensure_section(tuning, "negative_signals")
    negsig["enabled"] = ns["enabled"]
    bad_ratings = ensure_section(negsig, "bad_ratings")
    bad_ratings["enabled"] = ns["bad_ratings_enabled"]
    bad_ratings["threshold"] = ns["bad_ratings_threshold"]
    bad_ratings["cap_penalty"] = ns["bad_ratings_cap_penalty"]
    dropped_shows = ensure_section(negsig, "dropped_shows")
    dropped_shows["enabled"] = ns["dropped_enabled"]
    dropped_shows["min_episodes_watched"] = ns["dropped_min_episodes"]
    dropped_shows["max_completion_percent"] = ns["dropped_max_completion"]
    dropped_shows["penalty_multiplier"] = ns["dropped_penalty_multiplier"]

    ext = parsed["external"]
    ext_section = ensure_section(tuning, "external_recommendations")
    ext_section["enabled"] = ext["enabled"]
    ext_section["movie_limit"] = ext["movie_limit"]
    ext_section["show_limit"] = ext["show_limit"]
    ext_section["min_relevance_score"] = ext["min_relevance_score"]
    ext_section["min_votes"] = ext["min_votes"]
    ext_section["max_iterations"] = ext["max_iterations"]
    ext_section["language"] = ext["language"] or None
    ext_section["auto_open_html"] = ext["auto_open_html"]

    ensure_section(core, "general").update(parsed["general"])
    ensure_section(core, "logging")["level"] = parsed["logging"]["level"]

    sonarr["auto_sync"] = parsed["sync_safety"]["sonarr"]["auto_sync"]
    sonarr["user_mode"] = parsed["sync_safety"]["sonarr"]["user_mode"]
    sonarr["plex_users"] = parse_csv_list(parsed["sync_safety"]["sonarr"]["plex_users"])

    radarr["auto_sync"] = parsed["sync_safety"]["radarr"]["auto_sync"]
    radarr["user_mode"] = parsed["sync_safety"]["radarr"]["user_mode"]
    radarr["plex_users"] = parse_csv_list(parsed["sync_safety"]["radarr"]["plex_users"])

    trakt_export = trakt.get("export") or CommentedMap()
    trakt_export["auto_sync"] = parsed["sync_safety"]["trakt"]["auto_sync"]
    trakt_export["user_mode"] = parsed["sync_safety"]["trakt"]["user_mode"]
    trakt_export["plex_users"] = parse_csv_list(parsed["sync_safety"]["trakt"]["plex_users"])
    trakt["export"] = trakt_export

    return {"tuning": tuning, "config": core, "sonarr": sonarr, "radarr": radarr, "trakt": trakt}
