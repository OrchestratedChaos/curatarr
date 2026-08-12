# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Users screen: the managed-user list and per-user preferences
(display name, excluded genres, max content rating, streaming services).

Split out of web/config_app.py (audit remediation batch F/I, PR1(a)) -
see that module's docstring for the overall package layout this is one
quarter of. Registers its own routes via register_users_routes(), called
once from web.config_app.register_config_routes().

NOTE for a future per-library (#157) pass: preferences are keyed only by
username today (preferences.<user>.exclude_genres/max_rating/...). Adding
a library dimension later means changing this one key shape (e.g.
preferences.<user>.<library>.exclude_genres) - _users_view/
_parse_users_form/_apply_users below are the only places that shape is
read or written, so that's a contained change when #157 lands.
"""

from typing import Dict, List, Optional

from flask import redirect, render_template, request, url_for
from ruamel.yaml.comments import CommentedMap

from utils.plex import fetch_plex_users
from utils.plex_policy import MOVIE_RATING_HIERARCHY, TV_RATING_HIERARCHY

from .config_io import commit_modules, ensure_section, format_csv_list, load_module, module_path, parse_csv_list
from .security import redact

RATING_CHOICES = MOVIE_RATING_HIERARCHY + TV_RATING_HIERARCHY


def register_users_routes(app) -> None:
    """Register GET/POST /config/users onto *app* - see this module's own
    docstring for what the screen owns."""
    project_root = app.config["PROJECT_ROOT"]

    @app.get("/config/users")
    def config_users():
        """Render the Users screen with the currently-saved user list."""
        core = load_module(module_path(project_root, "config"))
        return render_template(
            "config_users.html",
            saved=request.args.get("saved") == "1",
            errors={},
            **_users_view(core),
        )

    @app.post("/config/users")
    def config_users_save():
        """Validate and save the Users form (add/edit/remove rows) to
        config.yml, re-rendering with field errors on bad input or a
        failed merge dry-run (see config_io.commit_modules)."""
        core = load_module(module_path(project_root, "config"))
        errors: Dict[str, str] = {}
        parsed = _parse_users_form(request.form, errors)

        if errors:
            return render_template(
                "config_users.html",
                saved=False,
                errors=errors,
                **_users_view(core, overrides=parsed),
            ), 400

        modules = _apply_users(project_root, core, parsed)
        commit_error = commit_modules(project_root, modules)
        if commit_error:
            return render_template(
                "config_users.html",
                saved=False,
                errors={"_global": f"Could not save: {commit_error}"},
                **_users_view(load_module(module_path(project_root, "config"))),
            ), 500
        return redirect(url_for("config_users", saved="1"), code=303)

    @app.post("/config/users/fetch-plex")
    def config_users_fetch_plex():
        """#266: preview step - connect to the already-configured Plex
        server/account and list its users that aren't in users.list yet,
        for the checklist below. Never writes anything - see
        config_users_add_plex for the follow-up that actually saves a
        selection."""
        core = load_module(module_path(project_root, "config"))
        return render_template(
            "config_users.html",
            saved=False,
            errors={},
            plex_fetch=_fetch_plex_users_view(core),
            **_users_view(core),
        )

    @app.post("/config/users/add-plex")
    def config_users_add_plex():
        """#266: add every checked username from the Fetch-from-Plex
        checklist to users.list in one save - each gets no preferences
        yet, same as a single manually-added user (editable on a
        follow-up visit)."""
        core = load_module(module_path(project_root, "config"))
        selected = request.form.getlist("selected_username")
        modules = _apply_users_from_plex(core, selected)
        commit_error = commit_modules(project_root, modules)
        if commit_error:
            return render_template(
                "config_users.html",
                saved=False,
                errors={"_global": f"Could not save: {commit_error}"},
                **_users_view(load_module(module_path(project_root, "config"))),
            ), 500
        return redirect(url_for("config_users", saved="1"), code=303)


def _users_view(core: CommentedMap, overrides: Optional[Dict] = None) -> Dict:
    """Build the template context for config_users.html: one row per
    configured user (or, after a failed submission, *overrides*'
    resubmitted rows) plus the max_rating choice list."""
    if overrides is not None:
        return {
            "users": overrides["users"],
            "new_username": overrides.get("new_username", ""),
            "rating_choices": RATING_CHOICES,
        }

    users_section = core.get("users") or {}
    raw_list = users_section.get("list", "")
    if isinstance(raw_list, str):
        usernames = [u.strip() for u in raw_list.split(",") if u.strip()]
    else:
        usernames = list(raw_list or [])

    preferences = users_section.get("preferences") or {}
    rows = []
    for username in usernames:
        prefs = preferences.get(username) or {}
        rows.append(
            {
                "username": username,
                "display_name": prefs.get("display_name", ""),
                "exclude_genres": format_csv_list(prefs.get("exclude_genres")),
                "max_rating": prefs.get("max_rating", ""),
                "streaming_services": format_csv_list(prefs.get("streaming_services")),
                "remove": False,
            }
        )
    return {"users": rows, "new_username": "", "rating_choices": RATING_CHOICES}


def _parse_users_form(form, errors: Dict[str, str]) -> Dict:
    """Parse+validate the raw POSTed form (indexed username_N/... fields
    plus a single 'new_username' add-a-user field) into the same shape
    _apply_users expects, appending to *errors* for any invalid field."""
    rows = []
    count = int(form.get("user_count", "0") or "0")
    for i in range(count):
        username = form.get(f"username_{i}", "").strip()
        if not username:
            continue
        remove = form.get(f"remove_{i}") in ("on", "true", "1")
        max_rating = form.get(f"max_rating_{i}", "").strip()
        if max_rating and max_rating.upper() not in RATING_CHOICES:
            errors[f"max_rating_{i}"] = f"{username}: must be one of {', '.join(RATING_CHOICES)} (or blank)"
        rows.append(
            {
                "username": username,
                "display_name": form.get(f"display_name_{i}", "").strip(),
                "exclude_genres": form.get(f"exclude_genres_{i}", ""),
                "max_rating": max_rating,
                "streaming_services": form.get(f"streaming_services_{i}", ""),
                "remove": remove,
            }
        )

    new_username = form.get("new_username", "").strip()
    if new_username:
        if any(r["username"] == new_username for r in rows if not r["remove"]):
            errors["new_username"] = f"{new_username} is already in the user list"
        rows.append(
            {
                "username": new_username,
                "display_name": "",
                "exclude_genres": "",
                "max_rating": "",
                "streaming_services": "",
                "remove": False,
            }
        )

    return {"users": rows, "new_username": ""}


def _fetch_plex_users_view(core: CommentedMap) -> Dict:
    """#266: fetch real Plex account users and filter out ones already
    in users.list - the context config_users.html renders as the
    "Fetch from Plex" checklist. Fails open (never raises) exactly like
    web/config_test_connection.py's test_plex - a broken/unreachable
    Plex must never break this screen, just show why the fetch didn't
    work."""
    plex_config = core.get("plex") or {}
    token = plex_config.get("token", "")
    if not token:
        return {
            "ok": False,
            "message": "Plex token is not configured yet - set it on the Connections screen first.",
            "available": [],
        }
    try:
        fetched = fetch_plex_users({"plex": {"token": token}})
    except Exception as exc:
        return {"ok": False, "message": redact(f"Could not fetch users from Plex: {exc}"), "available": []}

    users_section = core.get("users") or {}
    raw_list = users_section.get("list", "")
    if isinstance(raw_list, str):
        existing = {u.strip().lower() for u in raw_list.split(",") if u.strip()}
    else:
        existing = {str(u).strip().lower() for u in (raw_list or [])}

    available = [u for u in fetched if u["username"].lower() not in existing]
    return {"ok": True, "message": "", "available": available}


def _apply_users_from_plex(core: CommentedMap, selected_usernames: List[str]) -> Dict[str, CommentedMap]:
    """#266: append *selected_usernames* (checked on the Fetch-from-Plex
    checklist) to users.list, skipping any already present - WITHOUT
    writing to disk (see config_connections._apply_connections'
    docstring for why). New entries get no preferences yet, same as a
    single manually-added user via 'new_username' in _apply_users."""
    users_section = ensure_section(core, "users")
    raw_list = users_section.get("list", "")
    if isinstance(raw_list, str):
        existing_usernames = [u.strip() for u in raw_list.split(",") if u.strip()]
    else:
        existing_usernames = list(raw_list or [])

    existing_lower = {u.lower() for u in existing_usernames}
    for username in selected_usernames:
        username = username.strip()
        if username and username.lower() not in existing_lower:
            existing_usernames.append(username)
            existing_lower.add(username.lower())

    users_section["list"] = ", ".join(existing_usernames)
    return {"config": core}


def _apply_users(project_root: str, core: CommentedMap, parsed: Dict) -> Dict[str, CommentedMap]:
    """Mutate *core* in place and return it keyed by module name, WITHOUT
    writing to disk - see config_connections._apply_connections'
    docstring for why."""
    users_section = ensure_section(core, "users")
    kept = [row for row in parsed["users"] if not row["remove"]]

    users_section["list"] = ", ".join(row["username"] for row in kept)

    preferences = ensure_section(users_section, "preferences")

    # Drop removed users' preferences entirely.
    for row in parsed["users"]:
        if row["remove"]:
            preferences.pop(row["username"], None)

    for row in kept:
        entry = preferences.get(row["username"])
        if entry is None:
            entry = CommentedMap()
            preferences[row["username"]] = entry
        entry["display_name"] = row["display_name"] or row["username"]
        exclude_genres = parse_csv_list(row["exclude_genres"])
        if exclude_genres:
            entry["exclude_genres"] = exclude_genres
        else:
            entry.pop("exclude_genres", None)
        if row["max_rating"]:
            entry["max_rating"] = row["max_rating"].upper()
        else:
            entry.pop("max_rating", None)
        streaming_services = parse_csv_list(row["streaming_services"])
        if streaming_services:
            entry["streaming_services"] = streaming_services
        else:
            entry.pop("streaming_services", None)

    return {"config": core}
