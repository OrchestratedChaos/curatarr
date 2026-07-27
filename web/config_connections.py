"""Setup / Connections screen: Plex, TMDB, Tautulli, Sonarr, Radarr, and
Trakt credentials/toggles, plus the "Test Connection" endpoint shared by
all of them.

Split out of web/config_app.py (audit remediation batch F/I, PR1(a)) -
see that module's docstring for the overall package layout this is one
quarter of. Registers its own routes via register_connections_routes(),
called once from web.config_app.register_config_routes().

plex.movie_library/plex.tv_library are deliberately NOT fields on this
screen (#157 Phase 4 de-scope) - the Libraries screen (web/
config_libraries.py) is now the source of truth for repeatable library
entries, including the movie/tv split. utils.config.get_libraries()'s
legacy-synthesis fallback still reads plex.movie_library/tv_library for
any hand-written config.yml that predates the 'libraries:' block, so
existing installs keep working without this screen ever writing those
two fields again.
"""

from typing import Any, Dict, Optional

from flask import jsonify, redirect, render_template, request, url_for
from ruamel.yaml.comments import CommentedMap

from .config_io import (
    USER_MODE_CHOICES,
    commit_modules,
    ensure_section,
    format_csv_list,
    load_module,
    merge_secret,
    module_path,
    parse_csv_list,
    secret_status,
)
from .config_test_connection import TESTERS
from .config_validate import validate_choice, validate_required, validate_url
from .security import redact


def register_connections_routes(app) -> None:
    """Register GET/POST /config/connections and POST /config/test/<service>
    onto *app* - see this module's own docstring for what the screen owns."""
    project_root = app.config["PROJECT_ROOT"]

    @app.get("/config/connections")
    def config_connections():
        """Render the Connections screen with the currently-saved values."""
        data = _load_connections(project_root)
        return render_template(
            "config_connections.html",
            saved=request.args.get("saved") == "1",
            errors={},
            **_connections_view(data),
        )

    @app.post("/config/connections")
    def config_connections_save():
        """Validate and save the Connections form across config/sonarr/
        radarr/trakt.yml, re-rendering with field errors on bad input or a
        failed merge dry-run (see config_io.commit_modules)."""
        data = _load_connections(project_root)
        errors: Dict[str, str] = {}
        parsed = _parse_connections_form(request.form, errors)

        if errors:
            return render_template(
                "config_connections.html",
                saved=False,
                errors=errors,
                **_connections_view(data, overrides=parsed),
            ), 400

        modules = _apply_connections(project_root, data, parsed)
        commit_error = commit_modules(project_root, modules)
        if commit_error:
            return render_template(
                "config_connections.html",
                saved=False,
                errors={"_global": f"Could not save: {commit_error}"},
                **_connections_view(_load_connections(project_root)),
            ), 500
        return redirect(url_for("config_connections", saved="1"), code=303)

    @app.post("/config/test/<service>")
    def config_test_connection(service):
        """Exercise a real (read-only) connection check for *service* using
        the submitted form values, falling back to the already-saved secret
        for a blank submission - see the url-match guard below for why that
        fallback is gated on the URL being unchanged."""
        tester = TESTERS.get(service)
        if tester is None:
            return jsonify({"ok": False, "message": f"Unknown service: {service}"}), 404

        data = _load_connections(project_root)
        form = dict(request.form)

        # Secret fields: an empty submission means "use the already-saved
        # value" so Test Connection works without retyping a token that
        # was configured on a previous save - but ONLY when the URL being
        # tested is the same URL that secret was saved against. Without
        # this check, submitting a blank token/api_key alongside an
        # attacker-supplied `url` would make this endpoint fetch the real
        # stored secret and send it straight to that URL - an
        # exfiltration path, not just a UX convenience. If the URL has
        # changed, a blank secret field stays blank and the tester below
        # fails fast with its own "required" message instead.
        existing = _existing_secret_lookup(data, service)
        saved_url = _existing_url_lookup(data, service)
        url_unchanged = saved_url is None or form.get("url", "").strip() == saved_url.strip()
        if url_unchanged:
            for key, existing_value in existing.items():
                form[key] = merge_secret(existing_value, form.get(key, ""))

        result = tester(form)
        # Defense in depth: an underlying client's exception message could
        # in principle echo a token (e.g. a Plex URL with X-Plex-Token as a
        # query param) - redact before it ever reaches the browser, same as
        # every other UI surface that displays external output (web/status.py).
        result["message"] = redact(result.get("message", ""))
        return jsonify(result)


def _load_connections(project_root: str) -> Dict[str, CommentedMap]:
    """Load the four module files this screen reads/writes from, keyed by
    module name (see config_io.MODULE_FILES)."""
    return {
        "core": load_module(module_path(project_root, "config")),
        "sonarr": load_module(module_path(project_root, "sonarr")),
        "radarr": load_module(module_path(project_root, "radarr")),
        "trakt": load_module(module_path(project_root, "trakt")),
    }


def _existing_url_lookup(data: Dict[str, CommentedMap], service: str) -> Optional[str]:
    """The already-saved URL for *service*, or None for services with no
    user-suppliable destination URL (tmdb/trakt always talk to their
    fixed real API, so there's no URL for a submission to redirect a
    stored secret to). Used by config_test_connection to gate the
    saved-secret auto-fill on 'is this actually still the saved URL'."""
    core = data["core"]
    if service == "plex":
        return (core.get("plex") or {}).get("url", "") or ""
    if service == "tautulli":
        return (core.get("tautulli") or {}).get("url", "") or ""
    if service in ("sonarr", "radarr"):
        service_config: Dict[str, Any] = data[service] or {}
        return service_config.get("url", "") or ""
    return None


def _existing_secret_lookup(data: Dict[str, CommentedMap], service: str) -> Dict[str, str]:
    """The already-saved secret field(s) for *service*, keyed by the same
    field name(s) config_test_connection's tester functions expect."""
    core = data["core"]
    if service == "plex":
        return {"token": (core.get("plex") or {}).get("token", "")}
    if service == "tmdb":
        return {"api_key": (core.get("tmdb") or {}).get("api_key", "")}
    if service == "tautulli":
        return {"api_key": (core.get("tautulli") or {}).get("api_key", "")}
    if service in ("sonarr", "radarr"):
        service_config: Dict[str, Any] = data[service] or {}
        return {"api_key": service_config.get("api_key", "")}
    if service == "trakt":
        trakt: Dict[str, Any] = data["trakt"] or {}
        return {
            "client_secret": trakt.get("client_secret", ""),
            "access_token": trakt.get("access_token", ""),
            "refresh_token": trakt.get("refresh_token", ""),
        }
    return {}


def _connections_view(data: Dict[str, CommentedMap], overrides: Optional[Dict] = None) -> Dict:
    """Build the template context for config_connections.html: the
    on-disk values (or, after a failed submission, *overrides* merged
    over them) plus masked secret-status strings - never a raw secret."""
    core = data["core"]
    sonarr: Dict[str, Any] = data["sonarr"] or {}
    radarr: Dict[str, Any] = data["radarr"] or {}
    trakt: Dict[str, Any] = data["trakt"] or {}
    plex = core.get("plex") or {}
    tmdb = core.get("tmdb") or {}
    tautulli = core.get("tautulli") or {}
    trakt_export = trakt.get("export") or {}

    o = overrides or {}

    def pick(section: str, field: str, disk_value):
        return o[section][field] if section in o and field in o[section] else disk_value

    return {
        "plex": {
            "url": pick("plex", "url", plex.get("url", "")),
            "token_status": secret_status(
                merge_secret(plex.get("token"), o.get("plex", {}).get("token_submitted", ""))
                if "plex" in o
                else plex.get("token")
            ),
        },
        "tmdb": {
            "api_key_status": secret_status(
                merge_secret(tmdb.get("api_key"), o.get("tmdb", {}).get("api_key_submitted", ""))
                if "tmdb" in o
                else tmdb.get("api_key")
            ),
        },
        "tautulli": {
            "enabled": pick("tautulli", "enabled", bool(tautulli.get("enabled", False))),
            "url": pick("tautulli", "url", tautulli.get("url", "")),
            "api_key_status": secret_status(
                merge_secret(tautulli.get("api_key"), o.get("tautulli", {}).get("api_key_submitted", ""))
                if "tautulli" in o
                else tautulli.get("api_key")
            ),
        },
        "sonarr": {
            "enabled": pick("sonarr", "enabled", bool(sonarr.get("enabled", False))),
            "url": pick("sonarr", "url", sonarr.get("url", "")),
            "api_key_status": secret_status(
                merge_secret(sonarr.get("api_key"), o.get("sonarr", {}).get("api_key_submitted", ""))
                if "sonarr" in o
                else sonarr.get("api_key")
            ),
            "auto_sync": pick("sonarr", "auto_sync", bool(sonarr.get("auto_sync", False))),
            "user_mode": pick("sonarr", "user_mode", sonarr.get("user_mode", "mapping")),
            "plex_users": pick("sonarr", "plex_users", format_csv_list(sonarr.get("plex_users"))),
        },
        "radarr": {
            "enabled": pick("radarr", "enabled", bool(radarr.get("enabled", False))),
            "url": pick("radarr", "url", radarr.get("url", "")),
            "api_key_status": secret_status(
                merge_secret(radarr.get("api_key"), o.get("radarr", {}).get("api_key_submitted", ""))
                if "radarr" in o
                else radarr.get("api_key")
            ),
            "auto_sync": pick("radarr", "auto_sync", bool(radarr.get("auto_sync", False))),
            "user_mode": pick("radarr", "user_mode", radarr.get("user_mode", "mapping")),
            "plex_users": pick("radarr", "plex_users", format_csv_list(radarr.get("plex_users"))),
        },
        "trakt": {
            "enabled": pick("trakt", "enabled", bool(trakt.get("enabled", False))),
            "client_id": pick("trakt", "client_id", trakt.get("client_id", "")),
            "client_secret_status": secret_status(
                merge_secret(trakt.get("client_secret"), o.get("trakt", {}).get("client_secret_submitted", ""))
                if "trakt" in o
                else trakt.get("client_secret")
            ),
            "access_token_status": secret_status(trakt.get("access_token")),
            "auto_sync": pick("trakt", "auto_sync", bool(trakt_export.get("auto_sync", False))),
            "user_mode": pick("trakt", "user_mode", trakt_export.get("user_mode", "mapping")),
            "plex_users": pick("trakt", "plex_users", format_csv_list(trakt_export.get("plex_users"))),
        },
        "user_mode_choices": USER_MODE_CHOICES,
    }


def _parse_connections_form(form, errors: Dict[str, str]) -> Dict:
    """Parse+validate the raw POSTed form into the same shape
    _apply_connections expects, appending to *errors* for any invalid
    field instead of raising."""

    def flag(name: str) -> bool:
        return form.get(name) in ("on", "true", "1")

    plex_url = form.get("plex_url", "").strip()
    validate_url(plex_url, "plex_url", errors, required=True)

    tautulli_enabled = flag("tautulli_enabled")
    tautulli_url = form.get("tautulli_url", "").strip()
    if tautulli_enabled:
        validate_url(tautulli_url, "tautulli_url", errors, required=True)

    sonarr_enabled = flag("sonarr_enabled")
    sonarr_url = form.get("sonarr_url", "").strip()
    if sonarr_enabled:
        validate_url(sonarr_url, "sonarr_url", errors, required=True)
    sonarr_user_mode = form.get("sonarr_user_mode", "mapping")
    validate_choice(sonarr_user_mode, "sonarr_user_mode", errors, USER_MODE_CHOICES)

    radarr_enabled = flag("radarr_enabled")
    radarr_url = form.get("radarr_url", "").strip()
    if radarr_enabled:
        validate_url(radarr_url, "radarr_url", errors, required=True)
    radarr_user_mode = form.get("radarr_user_mode", "mapping")
    validate_choice(radarr_user_mode, "radarr_user_mode", errors, USER_MODE_CHOICES)

    trakt_enabled = flag("trakt_enabled")
    trakt_client_id = form.get("trakt_client_id", "").strip()
    if trakt_enabled:
        validate_required(trakt_client_id, "trakt_client_id", errors, "Client ID")
    trakt_user_mode = form.get("trakt_user_mode", "mapping")
    validate_choice(trakt_user_mode, "trakt_user_mode", errors, USER_MODE_CHOICES)

    return {
        "plex": {
            "url": plex_url,
            "token_submitted": form.get("plex_token", ""),
        },
        "tmdb": {
            "api_key_submitted": form.get("tmdb_api_key", ""),
        },
        "tautulli": {
            "enabled": tautulli_enabled,
            "url": tautulli_url,
            "api_key_submitted": form.get("tautulli_api_key", ""),
        },
        "sonarr": {
            "enabled": sonarr_enabled,
            "url": sonarr_url,
            "api_key_submitted": form.get("sonarr_api_key", ""),
            "auto_sync": flag("sonarr_auto_sync"),
            "user_mode": sonarr_user_mode,
            "plex_users": format_csv_list(parse_csv_list(form.get("sonarr_plex_users", ""))),
        },
        "radarr": {
            "enabled": radarr_enabled,
            "url": radarr_url,
            "api_key_submitted": form.get("radarr_api_key", ""),
            "auto_sync": flag("radarr_auto_sync"),
            "user_mode": radarr_user_mode,
            "plex_users": format_csv_list(parse_csv_list(form.get("radarr_plex_users", ""))),
        },
        "trakt": {
            "enabled": trakt_enabled,
            "client_id": trakt_client_id,
            "client_secret_submitted": form.get("trakt_client_secret", ""),
            "auto_sync": flag("trakt_auto_sync"),
            "user_mode": trakt_user_mode,
            "plex_users": format_csv_list(parse_csv_list(form.get("trakt_plex_users", ""))),
        },
    }


def _apply_connections(project_root: str, data: Dict[str, CommentedMap], parsed: Dict) -> Dict[str, CommentedMap]:
    """Mutate the in-memory CommentedMaps for this screen and return them
    keyed by module name, WITHOUT writing anything to disk - the caller
    (config_connections_save) commits them via config_io.commit_modules,
    which validates the full merge on a temp copy first (see M4 in the
    audit).
    """
    core = data["core"]

    plex_section = ensure_section(core, "plex")
    plex_section["url"] = parsed["plex"]["url"]
    plex_section["token"] = merge_secret(plex_section.get("token"), parsed["plex"]["token_submitted"])

    tmdb_section = ensure_section(core, "tmdb")
    tmdb_section["api_key"] = merge_secret(tmdb_section.get("api_key"), parsed["tmdb"]["api_key_submitted"])

    tautulli_section = ensure_section(core, "tautulli")
    tautulli_section["enabled"] = parsed["tautulli"]["enabled"]
    tautulli_section["url"] = parsed["tautulli"]["url"]
    tautulli_section["api_key"] = merge_secret(tautulli_section.get("api_key"), parsed["tautulli"]["api_key_submitted"])

    sonarr = data["sonarr"]
    sonarr["enabled"] = parsed["sonarr"]["enabled"]
    sonarr["url"] = parsed["sonarr"]["url"]
    sonarr["api_key"] = merge_secret(sonarr.get("api_key"), parsed["sonarr"]["api_key_submitted"])
    sonarr["auto_sync"] = parsed["sonarr"]["auto_sync"]
    sonarr["user_mode"] = parsed["sonarr"]["user_mode"]
    sonarr["plex_users"] = parse_csv_list(parsed["sonarr"]["plex_users"])

    radarr = data["radarr"]
    radarr["enabled"] = parsed["radarr"]["enabled"]
    radarr["url"] = parsed["radarr"]["url"]
    radarr["api_key"] = merge_secret(radarr.get("api_key"), parsed["radarr"]["api_key_submitted"])
    radarr["auto_sync"] = parsed["radarr"]["auto_sync"]
    radarr["user_mode"] = parsed["radarr"]["user_mode"]
    radarr["plex_users"] = parse_csv_list(parsed["radarr"]["plex_users"])

    trakt = data["trakt"]
    trakt["enabled"] = parsed["trakt"]["enabled"]
    trakt["client_id"] = parsed["trakt"]["client_id"]
    trakt["client_secret"] = merge_secret(trakt.get("client_secret"), parsed["trakt"]["client_secret_submitted"])
    export = trakt.get("export") or CommentedMap()
    export["auto_sync"] = parsed["trakt"]["auto_sync"]
    export["user_mode"] = parsed["trakt"]["user_mode"]
    export["plex_users"] = parse_csv_list(parsed["trakt"]["plex_users"])
    trakt["export"] = export

    return {"config": core, "sonarr": sonarr, "radarr": radarr, "trakt": trakt}
