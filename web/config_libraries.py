"""Libraries screen (#157 Phase 4): repeatable multi-library entries,
matching utils.config.get_libraries's 'libraries:' schema (see the block
comment above it in utils/config.py):

    libraries:
      - id: movies            # stable, immutable once created - see below
        name: Movies
        section: Movies
        media_type: movie
        arr:
          root_folder: /data/movies
          quality_profile: HD-1080p
          tag: Curatarr
          monitor: false
          search: false
          minimum_availability: released   # movie libraries only
          series_type: standard            # tv libraries only
          season_folder: true              # tv libraries only
          instance:
            url: http://localhost:7878
            api_key: KEY

This screen reads/writes core.get('libraries') directly - NOT through
get_libraries() - because get_libraries()'s legacy-synthesis fallback
(plex.movie_library/tv_library -> two library entries) exists purely to
keep pre-#157 configs working at *run* time; materializing that
synthesized pair into config.yml just because someone opened this
*editor* screen would silently change what a hand-written config means.
An empty/missing 'libraries:' block here means exactly that: no
libraries defined yet, filled in via "Add a library" below.

`id` is a stable identity used elsewhere (recommenders/base.py's
self.library_id, the movie/tv recommendation cache keys in
recommenders/external.py, utils.cli's --library-id) - editing a
library's display name must never change its id, or a rename would
silently orphan that library's cache/provenance history. So: existing
rows keep whatever id load from disk (round-tripped through a hidden
form field, immutable in this UI), and only a brand new row gets a
fresh id, derived from its name via _derive_library_id (mirroring
utils.config._slugify_library_id's derivation without importing that
module's private helper across a package boundary).

Split out of web/config_app.py (audit remediation batch F/I, PR1(a)) -
see that module's docstring for the overall package layout this is one
quarter of. Registers its own routes via register_libraries_routes(),
called once from web.config_app.register_config_routes().
"""

import re
from typing import Dict, List, Optional

from flask import redirect, render_template, request, url_for
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from utils.plex import fetch_plex_libraries

from .config_io import commit_modules, existing_library_secret, load_module, merge_secret, module_path, secret_status
from .config_validate import validate_media_type, validate_required, validate_url
from .security import redact

MEDIA_TYPE_CHOICES = ("movie", "tv")


def register_libraries_routes(app) -> None:
    """Register GET/POST /config/libraries onto *app* - see this module's
    own docstring for what the screen owns."""
    project_root = app.config["PROJECT_ROOT"]

    @app.get("/config/libraries")
    def config_libraries():
        """Render the Libraries screen with the currently-saved entries."""
        core = load_module(module_path(project_root, "config"))
        return render_template(
            "config_libraries.html",
            saved=request.args.get("saved") == "1",
            errors={},
            **_libraries_view(core),
        )

    @app.post("/config/libraries")
    def config_libraries_save():
        """Validate and save the Libraries form (add/edit/remove rows) to
        config.yml, re-rendering with field errors on bad input or a
        failed merge dry-run (see config_io.commit_modules)."""
        core = load_module(module_path(project_root, "config"))
        errors: Dict[str, str] = {}
        parsed = _parse_libraries_form(request.form, errors)

        if errors:
            return render_template(
                "config_libraries.html",
                saved=False,
                errors=errors,
                **_libraries_view(core, overrides=parsed),
            ), 400

        modules = _apply_libraries(project_root, core, parsed)
        commit_error = commit_modules(project_root, modules)
        if commit_error:
            return render_template(
                "config_libraries.html",
                saved=False,
                errors={"_global": f"Could not save: {commit_error}"},
                **_libraries_view(load_module(module_path(project_root, "config"))),
            ), 500
        return redirect(url_for("config_libraries", saved="1"), code=303)

    @app.post("/config/libraries/fetch-plex")
    def config_libraries_fetch_plex():
        """#266: preview step - connect to the already-configured Plex
        server and list its library sections that aren't configured yet,
        for the checklist below. Never writes anything - see
        config_libraries_add_plex for the follow-up that actually saves
        a selection."""
        core = load_module(module_path(project_root, "config"))
        return render_template(
            "config_libraries.html",
            saved=False,
            errors={},
            plex_fetch=_fetch_plex_libraries_view(core),
            **_libraries_view(core),
        )

    @app.post("/config/libraries/add-plex")
    def config_libraries_add_plex():
        """#266: add every checked library from the Fetch-from-Plex
        checklist in one save - each gets no arr/instance routing yet,
        same as a single manually-added library (editable on a
        follow-up visit)."""
        core = load_module(module_path(project_root, "config"))
        selected = []
        for raw in request.form.getlist("selected_library"):
            section, sep, media_type = raw.partition("::")
            if sep:
                selected.append({"section": section, "media_type": media_type})
        modules = _apply_libraries_from_plex(core, selected)
        commit_error = commit_modules(project_root, modules)
        if commit_error:
            return render_template(
                "config_libraries.html",
                saved=False,
                errors={"_global": f"Could not save: {commit_error}"},
                **_libraries_view(load_module(module_path(project_root, "config"))),
            ), 500
        return redirect(url_for("config_libraries", saved="1"), code=303)


def _derive_library_id(name: str) -> str:
    """Slugify *name* into a stable library id - see this module's
    docstring for why a brand new row gets one of these instead of an
    arbitrary/incrementing id."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "library"


def _arr_form_fields(prefix: str, form) -> Dict:
    """Read one library row's arr.* (Radarr/Sonarr) fields from *form*,
    keyed by f'{prefix}_root_folder' etc. - shared by both the indexed
    existing-row loop and the single new-row fields in
    _parse_libraries_form below."""

    def flag(name: str) -> bool:
        return form.get(name) in ("on", "true", "1")

    return {
        "root_folder": form.get(f"{prefix}_root_folder", "").strip(),
        "quality_profile": form.get(f"{prefix}_quality_profile", "").strip(),
        "tag": form.get(f"{prefix}_tag", "").strip(),
        "monitor": flag(f"{prefix}_monitor"),
        "search": flag(f"{prefix}_search"),
        "minimum_availability": form.get(f"{prefix}_minimum_availability", "").strip(),
        "series_type": form.get(f"{prefix}_series_type", "").strip(),
        "season_folder": flag(f"{prefix}_season_folder"),
    }


def _disk_library_row(entry: Dict) -> Dict:
    """Normalize one on-disk 'libraries:' entry into the row shape
    config_libraries.html renders, masking its arr.instance.api_key to a
    status string."""
    entry = entry or {}
    arr = entry.get("arr") or {}
    instance = arr.get("instance") or {}
    name = entry.get("name") or entry.get("id") or ""
    return {
        "id": entry.get("id") or _derive_library_id(name),
        "name": name,
        "section": entry.get("section") or name,
        "media_type": entry.get("media_type") or "movie",
        "arr": {
            "root_folder": arr.get("root_folder") or "",
            "quality_profile": arr.get("quality_profile") or "",
            "tag": arr.get("tag") or "",
            "monitor": bool(arr.get("monitor", False)),
            "search": bool(arr.get("search", False)),
            "minimum_availability": arr.get("minimum_availability") or "",
            "series_type": arr.get("series_type") or "",
            "season_folder": bool(arr.get("season_folder", False)),
        },
        "instance_url": instance.get("url") or "",
        "instance_api_key_status": secret_status(instance.get("api_key")),
        "remove": False,
    }


def _libraries_view(core: CommentedMap, overrides: Optional[Dict] = None) -> Dict:
    """Build the template context for config_libraries.html: one row per
    on-disk library (or, after a failed submission, *overrides*' rows
    with their masked secret status recomputed - see below)."""
    if overrides is None:
        rows = [_disk_library_row(entry) for entry in (core.get("libraries") or [])]
        return {
            "libraries": rows,
            "new_name": "",
            "new_section": "",
            "new_media_type": "movie",
            "media_type_choices": MEDIA_TYPE_CHOICES,
        }

    # Redisplay after a validation error: overrides['libraries'] already
    # has the shape _parse_libraries_form produced below. Recompute each
    # row's masked secret status the same way config_connections._connections_view
    # does for 'token_status' - merging the on-disk secret (looked up by the
    # row's immutable id) with whatever was just submitted - so the
    # redisplay never echoes the raw submitted api_key and still shows
    # what WOULD be saved if the errors were fixed and resubmitted.
    rows = []
    for row in overrides["libraries"]:
        status = secret_status(
            merge_secret(
                existing_library_secret(core, row["id"]),
                row.get("instance_api_key_submitted", ""),
            )
        )
        rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "section": row["section"],
                "media_type": row["media_type"],
                "arr": row["arr"],
                "instance_url": row["instance_url"],
                "instance_api_key_status": status,
                "remove": row["remove"],
            }
        )
    return {
        "libraries": rows,
        "new_name": overrides.get("new_name", ""),
        "new_section": overrides.get("new_section", ""),
        "new_media_type": overrides.get("new_media_type", "movie"),
        "media_type_choices": MEDIA_TYPE_CHOICES,
    }


def _parse_libraries_form(form, errors: Dict[str, str]) -> Dict:
    """Parse+validate the raw POSTed form (indexed library_id_N/... fields
    plus a single 'new_name' add-a-library field) into the same shape
    _apply_libraries expects, appending to *errors* for any invalid field
    - including cross-row duplicate name/id checks below."""

    def flag(name: str) -> bool:
        return form.get(name) in ("on", "true", "1")

    rows = []
    count = int(form.get("library_count", "0") or "0")
    for i in range(count):
        library_id = form.get(f"library_id_{i}", "").strip()
        name = form.get(f"name_{i}", "").strip()
        section = form.get(f"section_{i}", "").strip()
        media_type = form.get(f"media_type_{i}", "movie")
        instance_url = form.get(f"instance_url_{i}", "").strip()
        remove = flag(f"remove_{i}")

        validate_required(name, f"name_{i}", errors, "Library name")
        validate_required(section, f"section_{i}", errors, "Plex section")
        validate_media_type(media_type, f"media_type_{i}", errors)
        validate_url(instance_url, f"instance_url_{i}", errors, required=False)

        rows.append(
            {
                "id": library_id,
                "name": name,
                "section": section,
                "media_type": media_type,
                "arr": _arr_form_fields(f"arr_{i}", form),
                "instance_url": instance_url,
                "instance_api_key_submitted": form.get(f"instance_api_key_{i}", ""),
                "remove": remove,
            }
        )

    # Add a library: mirrors config_users._parse_users_form's
    # 'new_username' - a single always-blank-on-redisplay field, not part
    # of the indexed loop above. A non-blank name here becomes a new row
    # (arr/instance left blank, editable on a follow-up visit once it
    # exists), same as a new user only gets a username until edited again.
    new_name = form.get("new_name", "").strip()
    if new_name:
        new_section = form.get("new_section", "").strip()
        new_media_type = form.get("new_media_type", "movie")
        validate_required(new_section, "new_section", errors, "Plex section")
        validate_media_type(new_media_type, "new_media_type", errors)

        if any(r["name"] == new_name for r in rows if not r["remove"]):
            errors["new_name"] = f"{new_name} is already in the library list"

        rows.append(
            {
                "id": None,
                "name": new_name,
                "section": new_section,
                "media_type": new_media_type,
                "arr": _arr_form_fields("new_arr", form),
                "instance_url": form.get("new_instance_url", "").strip(),
                "instance_api_key_submitted": form.get("new_instance_api_key", ""),
                "remove": False,
            }
        )

    # Duplicate name/derived-id check across every row that will
    # actually be kept (post-remove). Existing rows keep their stable
    # on-disk id; a brand new row (id is None here) previews the id
    # _apply_libraries will assign it at save time, so a rename/add that
    # collides with another library is caught before anything is
    # written - not just a duplicate 'new_name' against the existing
    # list, but any two kept rows colliding with each other.
    seen_names, seen_ids = set(), set()
    for i, row in enumerate(rows):
        if row["remove"] or not row["name"]:
            continue
        derived_id = row["id"] or _derive_library_id(row["name"])
        if row["name"] in seen_names or derived_id in seen_ids:
            key = "new_name" if row["id"] is None else f"name_{i}"
            errors.setdefault(key, f'"{row["name"]}" duplicates another library (name or id already in use)')
        seen_names.add(row["name"])
        seen_ids.add(derived_id)

    if not any(not row["remove"] for row in rows):
        errors["_global"] = "At least one library is required."

    return {"libraries": rows, "new_name": "", "new_section": "", "new_media_type": "movie"}


def _fetch_plex_libraries_view(core: CommentedMap) -> Dict:
    """#266: fetch real Plex library sections and filter out ones
    already configured (matched by Plex section name) - the context
    config_libraries.html renders as the "Fetch from Plex" checklist.
    Fails open (never raises) exactly like web/config_test_connection.py's
    test_plex - a broken/unreachable Plex must never break this screen,
    just show why the fetch didn't work."""
    plex_config = core.get("plex") or {}
    url = plex_config.get("url", "")
    token = plex_config.get("token", "")
    if not url or not token:
        return {
            "ok": False,
            "message": "Plex URL/token are not configured yet - set them on the Connections screen first.",
            "available": [],
        }
    try:
        fetched = fetch_plex_libraries({"plex": {"url": url, "token": token}})
    except Exception as exc:
        return {"ok": False, "message": redact(f"Could not fetch libraries from Plex: {exc}"), "available": []}

    existing_sections = {(entry.get("section") or "").strip().lower() for entry in (core.get("libraries") or [])}
    available = [lib for lib in fetched if lib["section"].strip().lower() not in existing_sections]
    return {"ok": True, "message": "", "available": available}


def _apply_libraries_from_plex(core: CommentedMap, selected: List[Dict[str, str]]) -> Dict[str, CommentedMap]:
    """#266: append *selected* ({"section", "media_type"} dicts checked
    on the Fetch-from-Plex checklist) as new library entries, skipping
    any whose section already matches an existing one - WITHOUT writing
    to disk (see config_connections._apply_connections' docstring for
    why). Each new entry gets name=section and no arr/instance routing
    yet, same as a single manually-added library via
    _parse_libraries_form's 'new_name' path (editable on a follow-up
    visit)."""
    libraries_seq = core.get("libraries")
    if libraries_seq is None:
        libraries_seq = CommentedSeq()
        core["libraries"] = libraries_seq

    existing_sections = {(entry.get("section") or "").strip().lower() for entry in libraries_seq}
    existing_ids = {entry.get("id") for entry in libraries_seq}

    for lib in selected:
        section = lib["section"].strip()
        if not section or section.lower() in existing_sections:
            continue

        base_id = _derive_library_id(section)
        library_id = base_id
        suffix = 2
        while library_id in existing_ids:
            library_id = f"{base_id}-{suffix}"
            suffix += 1

        entry = CommentedMap()
        entry["id"] = library_id
        entry["name"] = section
        entry["section"] = section
        entry["media_type"] = lib["media_type"]
        libraries_seq.append(entry)
        existing_sections.add(section.lower())
        existing_ids.add(library_id)

    return {"config": core}


def _apply_libraries(project_root: str, core: CommentedMap, parsed: Dict) -> Dict[str, CommentedMap]:
    """Mutate *core* in place and return it keyed by module name, WITHOUT
    writing to disk - see config_connections._apply_connections'
    docstring for why."""
    kept = [row for row in parsed["libraries"] if not row["remove"]]

    seq = CommentedSeq()
    for row in kept:
        library_id = row["id"] or _derive_library_id(row["name"])

        entry = CommentedMap()
        entry["id"] = library_id
        entry["name"] = row["name"]
        entry["section"] = row["section"]
        entry["media_type"] = row["media_type"]

        arr_in = row["arr"]
        arr = CommentedMap()
        if arr_in["root_folder"]:
            arr["root_folder"] = arr_in["root_folder"]
        if arr_in["quality_profile"]:
            arr["quality_profile"] = arr_in["quality_profile"]
        if arr_in["tag"]:
            arr["tag"] = arr_in["tag"]
        if arr_in["monitor"]:
            arr["monitor"] = arr_in["monitor"]
        if arr_in["search"]:
            arr["search"] = arr_in["search"]
        if row["media_type"] == "movie" and arr_in["minimum_availability"]:
            arr["minimum_availability"] = arr_in["minimum_availability"]
        if row["media_type"] == "tv" and arr_in["series_type"]:
            arr["series_type"] = arr_in["series_type"]
        if row["media_type"] == "tv" and arr_in["season_folder"]:
            arr["season_folder"] = arr_in["season_folder"]

        instance = CommentedMap()
        if row["instance_url"]:
            instance["url"] = row["instance_url"]
        api_key = merge_secret(existing_library_secret(core, library_id), row["instance_api_key_submitted"])
        if api_key:
            instance["api_key"] = api_key
        if instance:
            arr["instance"] = instance

        if arr:
            entry["arr"] = arr
        seq.append(entry)

    core["libraries"] = seq
    return {"config": core}
