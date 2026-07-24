"""Config screens: Setup/Connections, Users, Settings/Tuning.

Extends web/app.py with three additional screens so curatarr can be set
up entirely from the browser instead of hand-editing YAML. Reads and
writes through web.config_io's round-trip helpers, which respect the
same modular config/*.yml layout utils.config._load_module_configs
already merges at run time:

    config.yml  - plex, tmdb, tautulli, users, general, logging
    tuning.yml  - movies/tv weights+quality_filters+limit_results,
                  recency_decay, rating_multipliers, negative_signals,
                  external_recommendations
    sonarr.yml / radarr.yml / trakt.yml - one file per integration

This module is purely additive: register_config_routes() is called once
from web.app.create_app() and only adds new routes. It never touches
the dashboard/run/results routes or the recommenders themselves.

mdblist.yml/simkl.yml are deliberately NOT exposed here - see the "mdblist
/simkl gap" note in the PR description. utils.config._load_module_configs
never loads those two files into the merged config, so an mdblist.yml or
simkl.yml a user hand-writes today is silently ignored at run time. Fixing
that loader gap is a behavior change for anyone who already has one of
those files sitting in config/ (it would suddenly start exporting), so
it's left as a follow-up rather than bundled into this UI-only PR.
"""

import logging
import re
from typing import Dict, Optional

from flask import jsonify, redirect, render_template, request, url_for
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from utils.config import UPDATE_MODES, get_update_mode
from utils.plex import MOVIE_RATING_HIERARCHY, TV_RATING_HIERARCHY

from .config_io import (
    ensure_section,
    existing_library_secret,
    format_csv_list,
    load_module,
    merge_secret,
    module_path,
    parse_csv_list,
    save_module,
    secret_status,
    validate_merge,
)
from .config_test_connection import TESTERS
from .security import redact
from .config_validate import (
    validate_choice,
    validate_float,
    validate_int,
    validate_media_type,
    validate_required,
    validate_url,
    validate_weights_sum,
)

logger = logging.getLogger('curatarr')

USER_MODE_CHOICES = ('mapping', 'per_user', 'combined')
LOG_LEVEL_CHOICES = ('DEBUG', 'INFO', 'WARNING', 'ERROR')
RATING_CHOICES = MOVIE_RATING_HIERARCHY + TV_RATING_HIERARCHY
MEDIA_TYPE_CHOICES = ('movie', 'tv')


def _commit_modules(project_root: str, modules: Dict[str, CommentedMap]) -> Optional[str]:
    """Validate *modules* (module-file-name -> CommentedMap to write) via
    a dry-run merge (config_io.validate_merge) BEFORE writing anything
    for real, then save every module. Field-level validation (weights
    sum, URLs, choices - see config_validate.py) happens even earlier,
    before this is ever called, which is what actually prevents most
    bad input from being considered at all. This is the second,
    defense-in-depth check: it catches a value that's individually
    well-typed but still breaks utils.load_config's merge some other
    way - and catches it on a throwaway temp copy, so a bad save can
    never reach the real config files (nor leave some of a multi-file
    save applied and others not).

    Returns an error message (and writes nothing) if the dry-run merge
    fails, else None once every module in *modules* has been saved.
    """
    error = validate_merge(project_root, modules)
    if error:
        logger.error(f"Config merge validation failed - nothing saved: {error}")
        return error
    for name, data in modules.items():
        save_module(module_path(project_root, name), data)
    return None


def register_config_routes(app) -> None:
    project_root = app.config['PROJECT_ROOT']

    # -------------------------------------------------------------------
    # Setup / Connections
    # -------------------------------------------------------------------

    @app.get('/config/connections')
    def config_connections():
        data = _load_connections(project_root)
        return render_template(
            'config_connections.html',
            saved=request.args.get('saved') == '1',
            errors={},
            **_connections_view(data),
        )

    @app.post('/config/connections')
    def config_connections_save():
        data = _load_connections(project_root)
        errors: Dict[str, str] = {}
        parsed = _parse_connections_form(request.form, errors)

        if errors:
            return render_template(
                'config_connections.html',
                saved=False,
                errors=errors,
                **_connections_view(data, overrides=parsed),
            ), 400

        modules = _apply_connections(project_root, data, parsed)
        commit_error = _commit_modules(project_root, modules)
        if commit_error:
            return render_template(
                'config_connections.html',
                saved=False,
                errors={'_global': f'Could not save: {commit_error}'},
                **_connections_view(_load_connections(project_root)),
            ), 500
        return redirect(url_for('config_connections', saved='1'), code=303)

    @app.post('/config/test/<service>')
    def config_test_connection(service):
        tester = TESTERS.get(service)
        if tester is None:
            return jsonify({'ok': False, 'message': f'Unknown service: {service}'}), 404

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
        url_unchanged = saved_url is None or form.get('url', '').strip() == saved_url.strip()
        if url_unchanged:
            for key, existing_value in existing.items():
                form[key] = merge_secret(existing_value, form.get(key, ''))

        result = tester(form)
        # Defense in depth: an underlying client's exception message could
        # in principle echo a token (e.g. a Plex URL with X-Plex-Token as a
        # query param) - redact before it ever reaches the browser, same as
        # every other UI surface that displays external output (web/status.py).
        result['message'] = redact(result.get('message', ''))
        return jsonify(result)

    # -------------------------------------------------------------------
    # Users
    # -------------------------------------------------------------------

    @app.get('/config/users')
    def config_users():
        core = load_module(module_path(project_root, 'config'))
        return render_template(
            'config_users.html',
            saved=request.args.get('saved') == '1',
            errors={},
            **_users_view(core),
        )

    @app.post('/config/users')
    def config_users_save():
        core = load_module(module_path(project_root, 'config'))
        errors: Dict[str, str] = {}
        parsed = _parse_users_form(request.form, errors)

        if errors:
            return render_template(
                'config_users.html',
                saved=False,
                errors=errors,
                **_users_view(core, overrides=parsed),
            ), 400

        modules = _apply_users(project_root, core, parsed)
        commit_error = _commit_modules(project_root, modules)
        if commit_error:
            return render_template(
                'config_users.html',
                saved=False,
                errors={'_global': f'Could not save: {commit_error}'},
                **_users_view(load_module(module_path(project_root, 'config'))),
            ), 500
        return redirect(url_for('config_users', saved='1'), code=303)

    # -------------------------------------------------------------------
    # Libraries (#157 Phase 4)
    # -------------------------------------------------------------------

    @app.get('/config/libraries')
    def config_libraries():
        core = load_module(module_path(project_root, 'config'))
        return render_template(
            'config_libraries.html',
            saved=request.args.get('saved') == '1',
            errors={},
            **_libraries_view(core),
        )

    @app.post('/config/libraries')
    def config_libraries_save():
        core = load_module(module_path(project_root, 'config'))
        errors: Dict[str, str] = {}
        parsed = _parse_libraries_form(request.form, errors)

        if errors:
            return render_template(
                'config_libraries.html',
                saved=False,
                errors=errors,
                **_libraries_view(core, overrides=parsed),
            ), 400

        modules = _apply_libraries(project_root, core, parsed)
        commit_error = _commit_modules(project_root, modules)
        if commit_error:
            return render_template(
                'config_libraries.html',
                saved=False,
                errors={'_global': f'Could not save: {commit_error}'},
                **_libraries_view(load_module(module_path(project_root, 'config'))),
            ), 500
        return redirect(url_for('config_libraries', saved='1'), code=303)

    # -------------------------------------------------------------------
    # Settings / Tuning
    # -------------------------------------------------------------------

    @app.get('/config/settings')
    def config_settings():
        tuning = load_module(module_path(project_root, 'tuning'))
        core = load_module(module_path(project_root, 'config'))
        sonarr = load_module(module_path(project_root, 'sonarr'))
        radarr = load_module(module_path(project_root, 'radarr'))
        trakt = load_module(module_path(project_root, 'trakt'))
        return render_template(
            'config_settings.html',
            saved=request.args.get('saved') == '1',
            errors={},
            **_settings_view(tuning, core, sonarr, radarr, trakt),
        )

    @app.post('/config/settings')
    def config_settings_save():
        tuning = load_module(module_path(project_root, 'tuning'))
        core = load_module(module_path(project_root, 'config'))
        sonarr = load_module(module_path(project_root, 'sonarr'))
        radarr = load_module(module_path(project_root, 'radarr'))
        trakt = load_module(module_path(project_root, 'trakt'))

        errors: Dict[str, str] = {}
        parsed = _parse_settings_form(request.form, errors)

        if errors:
            return render_template(
                'config_settings.html',
                saved=False,
                errors=errors,
                **_settings_view(tuning, core, sonarr, radarr, trakt, overrides=parsed),
            ), 400

        modules = _apply_settings(project_root, tuning, core, sonarr, radarr, trakt, parsed)
        commit_error = _commit_modules(project_root, modules)
        if commit_error:
            return render_template(
                'config_settings.html',
                saved=False,
                errors={'_global': f'Could not save: {commit_error}'},
                **_settings_view(
                    load_module(module_path(project_root, 'tuning')),
                    load_module(module_path(project_root, 'config')),
                    load_module(module_path(project_root, 'sonarr')),
                    load_module(module_path(project_root, 'radarr')),
                    load_module(module_path(project_root, 'trakt')),
                ),
            ), 500
        return redirect(url_for('config_settings', saved='1'), code=303)


# =========================================================================
# Connections screen
# =========================================================================
#
# plex.movie_library/plex.tv_library are deliberately NOT fields on this
# screen (#157 Phase 4 de-scope) - the Libraries screen (below) is now the
# source of truth for repeatable library entries, including the movie/tv
# split. utils.config.get_libraries()'s legacy-synthesis fallback still
# reads plex.movie_library/tv_library for any hand-written config.yml that
# predates the 'libraries:' block, so existing installs keep working
# without this screen ever writing those two fields again.

def _load_connections(project_root: str) -> Dict[str, CommentedMap]:
    return {
        'core': load_module(module_path(project_root, 'config')),
        'sonarr': load_module(module_path(project_root, 'sonarr')),
        'radarr': load_module(module_path(project_root, 'radarr')),
        'trakt': load_module(module_path(project_root, 'trakt')),
    }


def _existing_url_lookup(data: Dict[str, CommentedMap], service: str) -> Optional[str]:
    """The already-saved URL for *service*, or None for services with no
    user-suppliable destination URL (tmdb/trakt always talk to their
    fixed real API, so there's no URL for a submission to redirect a
    stored secret to). Used by config_test_connection to gate the
    saved-secret auto-fill on 'is this actually still the saved URL'."""
    core = data['core']
    if service == 'plex':
        return (core.get('plex') or {}).get('url', '') or ''
    if service == 'tautulli':
        return (core.get('tautulli') or {}).get('url', '') or ''
    if service in ('sonarr', 'radarr'):
        return (data[service] or {}).get('url', '') or ''
    return None


def _existing_secret_lookup(data: Dict[str, CommentedMap], service: str) -> Dict[str, str]:
    core = data['core']
    if service == 'plex':
        return {'token': (core.get('plex') or {}).get('token', '')}
    if service == 'tmdb':
        return {'api_key': (core.get('tmdb') or {}).get('api_key', '')}
    if service == 'tautulli':
        return {'api_key': (core.get('tautulli') or {}).get('api_key', '')}
    if service in ('sonarr', 'radarr'):
        return {'api_key': (data[service] or {}).get('api_key', '')}
    if service == 'trakt':
        trakt = data['trakt'] or {}
        return {
            'client_secret': trakt.get('client_secret', ''),
            'access_token': trakt.get('access_token', ''),
            'refresh_token': trakt.get('refresh_token', ''),
        }
    return {}


def _connections_view(data: Dict[str, CommentedMap], overrides: Optional[Dict] = None) -> Dict:
    core = data['core']
    sonarr = data['sonarr'] or {}
    radarr = data['radarr'] or {}
    trakt = data['trakt'] or {}
    plex = core.get('plex') or {}
    tmdb = core.get('tmdb') or {}
    tautulli = core.get('tautulli') or {}
    trakt_export = trakt.get('export') or {}

    o = overrides or {}

    def pick(section: str, field: str, disk_value):
        return o[section][field] if section in o and field in o[section] else disk_value

    return {
        'plex': {
            'url': pick('plex', 'url', plex.get('url', '')),
            'token_status': secret_status(
                merge_secret(plex.get('token'), o.get('plex', {}).get('token_submitted', ''))
                if 'plex' in o else plex.get('token')
            ),
        },
        'tmdb': {
            'api_key_status': secret_status(
                merge_secret(tmdb.get('api_key'), o.get('tmdb', {}).get('api_key_submitted', ''))
                if 'tmdb' in o else tmdb.get('api_key')
            ),
        },
        'tautulli': {
            'enabled': pick('tautulli', 'enabled', bool(tautulli.get('enabled', False))),
            'url': pick('tautulli', 'url', tautulli.get('url', '')),
            'api_key_status': secret_status(
                merge_secret(tautulli.get('api_key'), o.get('tautulli', {}).get('api_key_submitted', ''))
                if 'tautulli' in o else tautulli.get('api_key')
            ),
        },
        'sonarr': {
            'enabled': pick('sonarr', 'enabled', bool(sonarr.get('enabled', False))),
            'url': pick('sonarr', 'url', sonarr.get('url', '')),
            'api_key_status': secret_status(
                merge_secret(sonarr.get('api_key'), o.get('sonarr', {}).get('api_key_submitted', ''))
                if 'sonarr' in o else sonarr.get('api_key')
            ),
            'auto_sync': pick('sonarr', 'auto_sync', bool(sonarr.get('auto_sync', False))),
            'user_mode': pick('sonarr', 'user_mode', sonarr.get('user_mode', 'mapping')),
            'plex_users': pick('sonarr', 'plex_users', format_csv_list(sonarr.get('plex_users'))),
        },
        'radarr': {
            'enabled': pick('radarr', 'enabled', bool(radarr.get('enabled', False))),
            'url': pick('radarr', 'url', radarr.get('url', '')),
            'api_key_status': secret_status(
                merge_secret(radarr.get('api_key'), o.get('radarr', {}).get('api_key_submitted', ''))
                if 'radarr' in o else radarr.get('api_key')
            ),
            'auto_sync': pick('radarr', 'auto_sync', bool(radarr.get('auto_sync', False))),
            'user_mode': pick('radarr', 'user_mode', radarr.get('user_mode', 'mapping')),
            'plex_users': pick('radarr', 'plex_users', format_csv_list(radarr.get('plex_users'))),
        },
        'trakt': {
            'enabled': pick('trakt', 'enabled', bool(trakt.get('enabled', False))),
            'client_id': pick('trakt', 'client_id', trakt.get('client_id', '')),
            'client_secret_status': secret_status(
                merge_secret(trakt.get('client_secret'), o.get('trakt', {}).get('client_secret_submitted', ''))
                if 'trakt' in o else trakt.get('client_secret')
            ),
            'access_token_status': secret_status(trakt.get('access_token')),
            'auto_sync': pick('trakt', 'auto_sync', bool(trakt_export.get('auto_sync', False))),
            'user_mode': pick('trakt', 'user_mode', trakt_export.get('user_mode', 'mapping')),
            'plex_users': pick('trakt', 'plex_users', format_csv_list(trakt_export.get('plex_users'))),
        },
        'user_mode_choices': USER_MODE_CHOICES,
    }


def _parse_connections_form(form, errors: Dict[str, str]) -> Dict:
    def flag(name: str) -> bool:
        return form.get(name) in ('on', 'true', '1')

    plex_url = form.get('plex_url', '').strip()
    validate_url(plex_url, 'plex_url', errors, required=True)

    tautulli_enabled = flag('tautulli_enabled')
    tautulli_url = form.get('tautulli_url', '').strip()
    if tautulli_enabled:
        validate_url(tautulli_url, 'tautulli_url', errors, required=True)

    sonarr_enabled = flag('sonarr_enabled')
    sonarr_url = form.get('sonarr_url', '').strip()
    if sonarr_enabled:
        validate_url(sonarr_url, 'sonarr_url', errors, required=True)
    sonarr_user_mode = form.get('sonarr_user_mode', 'mapping')
    validate_choice(sonarr_user_mode, 'sonarr_user_mode', errors, USER_MODE_CHOICES)

    radarr_enabled = flag('radarr_enabled')
    radarr_url = form.get('radarr_url', '').strip()
    if radarr_enabled:
        validate_url(radarr_url, 'radarr_url', errors, required=True)
    radarr_user_mode = form.get('radarr_user_mode', 'mapping')
    validate_choice(radarr_user_mode, 'radarr_user_mode', errors, USER_MODE_CHOICES)

    trakt_enabled = flag('trakt_enabled')
    trakt_client_id = form.get('trakt_client_id', '').strip()
    if trakt_enabled:
        validate_required(trakt_client_id, 'trakt_client_id', errors, 'Client ID')
    trakt_user_mode = form.get('trakt_user_mode', 'mapping')
    validate_choice(trakt_user_mode, 'trakt_user_mode', errors, USER_MODE_CHOICES)

    return {
        'plex': {
            'url': plex_url,
            'token_submitted': form.get('plex_token', ''),
        },
        'tmdb': {
            'api_key_submitted': form.get('tmdb_api_key', ''),
        },
        'tautulli': {
            'enabled': tautulli_enabled,
            'url': tautulli_url,
            'api_key_submitted': form.get('tautulli_api_key', ''),
        },
        'sonarr': {
            'enabled': sonarr_enabled,
            'url': sonarr_url,
            'api_key_submitted': form.get('sonarr_api_key', ''),
            'auto_sync': flag('sonarr_auto_sync'),
            'user_mode': sonarr_user_mode,
            'plex_users': format_csv_list(parse_csv_list(form.get('sonarr_plex_users', ''))),
        },
        'radarr': {
            'enabled': radarr_enabled,
            'url': radarr_url,
            'api_key_submitted': form.get('radarr_api_key', ''),
            'auto_sync': flag('radarr_auto_sync'),
            'user_mode': radarr_user_mode,
            'plex_users': format_csv_list(parse_csv_list(form.get('radarr_plex_users', ''))),
        },
        'trakt': {
            'enabled': trakt_enabled,
            'client_id': trakt_client_id,
            'client_secret_submitted': form.get('trakt_client_secret', ''),
            'auto_sync': flag('trakt_auto_sync'),
            'user_mode': trakt_user_mode,
            'plex_users': format_csv_list(parse_csv_list(form.get('trakt_plex_users', ''))),
        },
    }


def _apply_connections(project_root: str, data: Dict[str, CommentedMap], parsed: Dict) -> Dict[str, CommentedMap]:
    """Mutate the in-memory CommentedMaps for this screen and return them
    keyed by module name, WITHOUT writing anything to disk - the caller
    (config_connections_save) commits them via _commit_modules, which
    validates the full merge on a temp copy first (see M4 in the audit).
    """
    core = data['core']

    plex_section = ensure_section(core, 'plex')
    plex_section['url'] = parsed['plex']['url']
    plex_section['token'] = merge_secret(plex_section.get('token'), parsed['plex']['token_submitted'])

    tmdb_section = ensure_section(core, 'tmdb')
    tmdb_section['api_key'] = merge_secret(tmdb_section.get('api_key'), parsed['tmdb']['api_key_submitted'])

    tautulli_section = ensure_section(core, 'tautulli')
    tautulli_section['enabled'] = parsed['tautulli']['enabled']
    tautulli_section['url'] = parsed['tautulli']['url']
    tautulli_section['api_key'] = merge_secret(tautulli_section.get('api_key'), parsed['tautulli']['api_key_submitted'])

    sonarr = data['sonarr']
    sonarr['enabled'] = parsed['sonarr']['enabled']
    sonarr['url'] = parsed['sonarr']['url']
    sonarr['api_key'] = merge_secret(sonarr.get('api_key'), parsed['sonarr']['api_key_submitted'])
    sonarr['auto_sync'] = parsed['sonarr']['auto_sync']
    sonarr['user_mode'] = parsed['sonarr']['user_mode']
    sonarr['plex_users'] = parse_csv_list(parsed['sonarr']['plex_users'])

    radarr = data['radarr']
    radarr['enabled'] = parsed['radarr']['enabled']
    radarr['url'] = parsed['radarr']['url']
    radarr['api_key'] = merge_secret(radarr.get('api_key'), parsed['radarr']['api_key_submitted'])
    radarr['auto_sync'] = parsed['radarr']['auto_sync']
    radarr['user_mode'] = parsed['radarr']['user_mode']
    radarr['plex_users'] = parse_csv_list(parsed['radarr']['plex_users'])

    trakt = data['trakt']
    trakt['enabled'] = parsed['trakt']['enabled']
    trakt['client_id'] = parsed['trakt']['client_id']
    trakt['client_secret'] = merge_secret(trakt.get('client_secret'), parsed['trakt']['client_secret_submitted'])
    export = trakt.get('export') or CommentedMap()
    export['auto_sync'] = parsed['trakt']['auto_sync']
    export['user_mode'] = parsed['trakt']['user_mode']
    export['plex_users'] = parse_csv_list(parsed['trakt']['plex_users'])
    trakt['export'] = export

    return {'config': core, 'sonarr': sonarr, 'radarr': radarr, 'trakt': trakt}


# =========================================================================
# Users screen
# =========================================================================
#
# NOTE for a future per-library (#157) pass: preferences are keyed only
# by username today (preferences.<user>.exclude_genres/max_rating/...).
# Adding a library dimension later means changing this one key shape
# (e.g. preferences.<user>.<library>.exclude_genres) - _users_view/
# _parse_users_form/_apply_users below are the only places that shape is
# read or written, so that's a contained change when #157 lands.

def _users_view(core: CommentedMap, overrides: Optional[Dict] = None) -> Dict:
    if overrides is not None:
        return {
            'users': overrides['users'],
            'new_username': overrides.get('new_username', ''),
            'rating_choices': RATING_CHOICES,
        }

    users_section = core.get('users') or {}
    raw_list = users_section.get('list', '')
    if isinstance(raw_list, str):
        usernames = [u.strip() for u in raw_list.split(',') if u.strip()]
    else:
        usernames = list(raw_list or [])

    preferences = users_section.get('preferences') or {}
    rows = []
    for username in usernames:
        prefs = preferences.get(username) or {}
        rows.append({
            'username': username,
            'display_name': prefs.get('display_name', ''),
            'exclude_genres': format_csv_list(prefs.get('exclude_genres')),
            'max_rating': prefs.get('max_rating', ''),
            'streaming_services': format_csv_list(prefs.get('streaming_services')),
            'remove': False,
        })
    return {'users': rows, 'new_username': '', 'rating_choices': RATING_CHOICES}


def _parse_users_form(form, errors: Dict[str, str]) -> Dict:
    rows = []
    count = int(form.get('user_count', '0') or '0')
    for i in range(count):
        username = form.get(f'username_{i}', '').strip()
        if not username:
            continue
        remove = form.get(f'remove_{i}') in ('on', 'true', '1')
        max_rating = form.get(f'max_rating_{i}', '').strip()
        if max_rating and max_rating.upper() not in RATING_CHOICES:
            errors[f'max_rating_{i}'] = f'{username}: must be one of {", ".join(RATING_CHOICES)} (or blank)'
        rows.append({
            'username': username,
            'display_name': form.get(f'display_name_{i}', '').strip(),
            'exclude_genres': form.get(f'exclude_genres_{i}', ''),
            'max_rating': max_rating,
            'streaming_services': form.get(f'streaming_services_{i}', ''),
            'remove': remove,
        })

    new_username = form.get('new_username', '').strip()
    if new_username:
        if any(r['username'] == new_username for r in rows if not r['remove']):
            errors['new_username'] = f'{new_username} is already in the user list'
        rows.append({
            'username': new_username,
            'display_name': '',
            'exclude_genres': '',
            'max_rating': '',
            'streaming_services': '',
            'remove': False,
        })

    return {'users': rows, 'new_username': ''}


def _apply_users(project_root: str, core: CommentedMap, parsed: Dict) -> Dict[str, CommentedMap]:
    """Mutate *core* in place and return it keyed by module name, WITHOUT
    writing to disk - see _apply_connections' docstring for why."""
    users_section = ensure_section(core, 'users')
    kept = [row for row in parsed['users'] if not row['remove']]

    users_section['list'] = ', '.join(row['username'] for row in kept)

    preferences = ensure_section(users_section, 'preferences')

    # Drop removed users' preferences entirely.
    for row in parsed['users']:
        if row['remove']:
            preferences.pop(row['username'], None)

    for row in kept:
        entry = preferences.get(row['username'])
        if entry is None:
            entry = CommentedMap()
            preferences[row['username']] = entry
        entry['display_name'] = row['display_name'] or row['username']
        exclude_genres = parse_csv_list(row['exclude_genres'])
        if exclude_genres:
            entry['exclude_genres'] = exclude_genres
        else:
            entry.pop('exclude_genres', None)
        if row['max_rating']:
            entry['max_rating'] = row['max_rating'].upper()
        else:
            entry.pop('max_rating', None)
        streaming_services = parse_csv_list(row['streaming_services'])
        if streaming_services:
            entry['streaming_services'] = streaming_services
        else:
            entry.pop('streaming_services', None)

    return {'config': core}


# =========================================================================
# Libraries screen (#157 Phase 4)
# =========================================================================
#
# Repeatable multi-library entries, matching utils.config.get_libraries's
# 'libraries:' schema (see the block comment above it in utils/config.py):
#
#   libraries:
#     - id: movies            # stable, immutable once created - see below
#       name: Movies
#       section: Movies
#       media_type: movie
#       arr:
#         root_folder: /data/movies
#         quality_profile: HD-1080p
#         tag: Curatarr
#         monitor: false
#         search: false
#         minimum_availability: released   # movie libraries only
#         series_type: standard            # tv libraries only
#         season_folder: true              # tv libraries only
#         instance:
#           url: http://localhost:7878
#           api_key: KEY
#
# This screen reads/writes core.get('libraries') directly - NOT through
# get_libraries() - because get_libraries()'s legacy-synthesis fallback
# (plex.movie_library/tv_library -> two library entries) exists purely to
# keep pre-#157 configs working at *run* time; materializing that
# synthesized pair into config.yml just because someone opened this
# *editor* screen would silently change what a hand-written config means.
# An empty/missing 'libraries:' block here means exactly that: no
# libraries defined yet, filled in via "Add a library" below.
#
# `id` is a stable identity used elsewhere (recommenders/base.py's
# self.library_id, the movie/tv recommendation cache keys in
# recommenders/external.py, utils.cli's --library-id) - editing a
# library's display name must never change its id, or a rename would
# silently orphan that library's cache/provenance history. So: existing
# rows keep whatever id load from disk (round-tripped through a hidden
# form field, immutable in this UI), and only a brand new row gets a
# fresh id, derived from its name via _derive_library_id (mirroring
# utils.config._slugify_library_id's derivation without importing that
# module's private helper across a package boundary).

def _derive_library_id(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').strip().lower()).strip('-')
    return slug or 'library'


def _arr_form_fields(prefix: str, form) -> Dict:
    def flag(name: str) -> bool:
        return form.get(name) in ('on', 'true', '1')

    return {
        'root_folder': form.get(f'{prefix}_root_folder', '').strip(),
        'quality_profile': form.get(f'{prefix}_quality_profile', '').strip(),
        'tag': form.get(f'{prefix}_tag', '').strip(),
        'monitor': flag(f'{prefix}_monitor'),
        'search': flag(f'{prefix}_search'),
        'minimum_availability': form.get(f'{prefix}_minimum_availability', '').strip(),
        'series_type': form.get(f'{prefix}_series_type', '').strip(),
        'season_folder': flag(f'{prefix}_season_folder'),
    }


def _disk_library_row(entry: Dict) -> Dict:
    entry = entry or {}
    arr = entry.get('arr') or {}
    instance = arr.get('instance') or {}
    name = entry.get('name') or entry.get('id') or ''
    return {
        'id': entry.get('id') or _derive_library_id(name),
        'name': name,
        'section': entry.get('section') or name,
        'media_type': entry.get('media_type') or 'movie',
        'arr': {
            'root_folder': arr.get('root_folder') or '',
            'quality_profile': arr.get('quality_profile') or '',
            'tag': arr.get('tag') or '',
            'monitor': bool(arr.get('monitor', False)),
            'search': bool(arr.get('search', False)),
            'minimum_availability': arr.get('minimum_availability') or '',
            'series_type': arr.get('series_type') or '',
            'season_folder': bool(arr.get('season_folder', False)),
        },
        'instance_url': instance.get('url') or '',
        'instance_api_key_status': secret_status(instance.get('api_key')),
        'remove': False,
    }


def _libraries_view(core: CommentedMap, overrides: Optional[Dict] = None) -> Dict:
    if overrides is None:
        rows = [_disk_library_row(entry) for entry in (core.get('libraries') or [])]
        return {
            'libraries': rows,
            'new_name': '',
            'new_section': '',
            'new_media_type': 'movie',
            'media_type_choices': MEDIA_TYPE_CHOICES,
        }

    # Redisplay after a validation error: overrides['libraries'] already
    # has the shape _parse_libraries_form produced below. Recompute each
    # row's masked secret status the same way _connections_view does for
    # 'token_status' - merging the on-disk secret (looked up by the
    # row's immutable id) with whatever was just submitted - so the
    # redisplay never echoes the raw submitted api_key and still shows
    # what WOULD be saved if the errors were fixed and resubmitted.
    rows = []
    for row in overrides['libraries']:
        status = secret_status(merge_secret(
            existing_library_secret(core, row['id']),
            row.get('instance_api_key_submitted', ''),
        ))
        rows.append({
            'id': row['id'],
            'name': row['name'],
            'section': row['section'],
            'media_type': row['media_type'],
            'arr': row['arr'],
            'instance_url': row['instance_url'],
            'instance_api_key_status': status,
            'remove': row['remove'],
        })
    return {
        'libraries': rows,
        'new_name': overrides.get('new_name', ''),
        'new_section': overrides.get('new_section', ''),
        'new_media_type': overrides.get('new_media_type', 'movie'),
        'media_type_choices': MEDIA_TYPE_CHOICES,
    }


def _parse_libraries_form(form, errors: Dict[str, str]) -> Dict:
    def flag(name: str) -> bool:
        return form.get(name) in ('on', 'true', '1')

    rows = []
    count = int(form.get('library_count', '0') or '0')
    for i in range(count):
        library_id = form.get(f'library_id_{i}', '').strip()
        name = form.get(f'name_{i}', '').strip()
        section = form.get(f'section_{i}', '').strip()
        media_type = form.get(f'media_type_{i}', 'movie')
        instance_url = form.get(f'instance_url_{i}', '').strip()
        remove = flag(f'remove_{i}')

        validate_required(name, f'name_{i}', errors, 'Library name')
        validate_required(section, f'section_{i}', errors, 'Plex section')
        validate_media_type(media_type, f'media_type_{i}', errors)
        validate_url(instance_url, f'instance_url_{i}', errors, required=False)

        rows.append({
            'id': library_id,
            'name': name,
            'section': section,
            'media_type': media_type,
            'arr': _arr_form_fields(f'arr_{i}', form),
            'instance_url': instance_url,
            'instance_api_key_submitted': form.get(f'instance_api_key_{i}', ''),
            'remove': remove,
        })

    # Add a library: mirrors _parse_users_form's 'new_username' - a
    # single always-blank-on-redisplay field, not part of the indexed
    # loop above. A non-blank name here becomes a new row (arr/instance
    # left blank, editable on a follow-up visit once it exists), same
    # as a new user only gets a username until edited again.
    new_name = form.get('new_name', '').strip()
    if new_name:
        new_section = form.get('new_section', '').strip()
        new_media_type = form.get('new_media_type', 'movie')
        validate_required(new_section, 'new_section', errors, 'Plex section')
        validate_media_type(new_media_type, 'new_media_type', errors)

        if any(r['name'] == new_name for r in rows if not r['remove']):
            errors['new_name'] = f'{new_name} is already in the library list'

        rows.append({
            'id': None,
            'name': new_name,
            'section': new_section,
            'media_type': new_media_type,
            'arr': _arr_form_fields('new_arr', form),
            'instance_url': form.get('new_instance_url', '').strip(),
            'instance_api_key_submitted': form.get('new_instance_api_key', ''),
            'remove': False,
        })

    # Duplicate name/derived-id check across every row that will
    # actually be kept (post-remove). Existing rows keep their stable
    # on-disk id; a brand new row (id is None here) previews the id
    # _apply_libraries will assign it at save time, so a rename/add that
    # collides with another library is caught before anything is
    # written - not just a duplicate 'new_name' against the existing
    # list, but any two kept rows colliding with each other.
    seen_names, seen_ids = set(), set()
    for i, row in enumerate(rows):
        if row['remove'] or not row['name']:
            continue
        derived_id = row['id'] or _derive_library_id(row['name'])
        if row['name'] in seen_names or derived_id in seen_ids:
            key = 'new_name' if row['id'] is None else f'name_{i}'
            errors.setdefault(key, f'"{row["name"]}" duplicates another library (name or id already in use)')
        seen_names.add(row['name'])
        seen_ids.add(derived_id)

    if not any(not row['remove'] for row in rows):
        errors['_global'] = 'At least one library is required.'

    return {'libraries': rows, 'new_name': '', 'new_section': '', 'new_media_type': 'movie'}


def _apply_libraries(project_root: str, core: CommentedMap, parsed: Dict) -> Dict[str, CommentedMap]:
    """Mutate *core* in place and return it keyed by module name, WITHOUT
    writing to disk - see _apply_connections' docstring for why."""
    kept = [row for row in parsed['libraries'] if not row['remove']]

    seq = CommentedSeq()
    for row in kept:
        library_id = row['id'] or _derive_library_id(row['name'])

        entry = CommentedMap()
        entry['id'] = library_id
        entry['name'] = row['name']
        entry['section'] = row['section']
        entry['media_type'] = row['media_type']

        arr_in = row['arr']
        arr = CommentedMap()
        if arr_in['root_folder']:
            arr['root_folder'] = arr_in['root_folder']
        if arr_in['quality_profile']:
            arr['quality_profile'] = arr_in['quality_profile']
        if arr_in['tag']:
            arr['tag'] = arr_in['tag']
        if arr_in['monitor']:
            arr['monitor'] = arr_in['monitor']
        if arr_in['search']:
            arr['search'] = arr_in['search']
        if row['media_type'] == 'movie' and arr_in['minimum_availability']:
            arr['minimum_availability'] = arr_in['minimum_availability']
        if row['media_type'] == 'tv' and arr_in['series_type']:
            arr['series_type'] = arr_in['series_type']
        if row['media_type'] == 'tv' and arr_in['season_folder']:
            arr['season_folder'] = arr_in['season_folder']

        instance = CommentedMap()
        if row['instance_url']:
            instance['url'] = row['instance_url']
        api_key = merge_secret(existing_library_secret(core, library_id), row['instance_api_key_submitted'])
        if api_key:
            instance['api_key'] = api_key
        if instance:
            arr['instance'] = instance

        if arr:
            entry['arr'] = arr
        seq.append(entry)

    core['libraries'] = seq
    return {'config': core}


# =========================================================================
# Settings / Tuning screen
# =========================================================================

def _settings_view(tuning: CommentedMap, core: CommentedMap, sonarr: CommentedMap,
                    radarr: CommentedMap, trakt: CommentedMap,
                    overrides: Optional[Dict] = None) -> Dict:
    if overrides is not None:
        return overrides

    movies = tuning.get('movies') or {}
    tv = tuning.get('tv') or {}
    movies_weights = movies.get('weights') or {}
    tv_weights = tv.get('weights') or {}
    movies_quality = movies.get('quality_filters') or {}
    tv_quality = tv.get('quality_filters') or {}
    recency = tuning.get('recency_decay') or {}
    rating_mult = tuning.get('rating_multipliers') or {}
    negsig = tuning.get('negative_signals') or {}
    bad_ratings = negsig.get('bad_ratings') or {}
    dropped_shows = negsig.get('dropped_shows') or {}
    external = tuning.get('external_recommendations') or {}
    general = core.get('general') or {}
    logging_cfg = core.get('logging') or {}
    trakt_export = trakt.get('export') or {}

    return {
        'movies': {
            'weights': {
                'genre': movies_weights.get('genre', 0.25),
                'director': movies_weights.get('director', 0.05),
                'actor': movies_weights.get('actor', 0.20),
                'keyword': movies_weights.get('keyword', 0.50),
            },
            'limit_results': movies.get('limit_results', 50),
            'min_rating': movies_quality.get('min_rating', 5.0),
            'min_vote_count': movies_quality.get('min_vote_count', 50),
        },
        'tv': {
            'weights': {
                'genre': tv_weights.get('genre', 0.25),
                'studio': tv_weights.get('studio', 0.10),
                'actor': tv_weights.get('actor', 0.20),
                'keyword': tv_weights.get('keyword', 0.45),
            },
            'limit_results': tv.get('limit_results', 20),
            'min_rating': tv_quality.get('min_rating', 0.0),
            'min_vote_count': tv_quality.get('min_vote_count', 0),
        },
        'recency': {
            'enabled': bool(recency.get('enabled', True)),
            'days_0_30': recency.get('days_0_30', 1.0),
            'days_31_90': recency.get('days_31_90', 0.75),
            'days_91_180': recency.get('days_91_180', 0.50),
            'days_181_365': recency.get('days_181_365', 0.25),
            'days_365_plus': recency.get('days_365_plus', 0.10),
        },
        'rating_multipliers': {
            'star_5': rating_mult.get('star_5', 2.5),
            'star_4': rating_mult.get('star_4', 1.7),
            'star_3': rating_mult.get('star_3', 1.0),
            'star_2': rating_mult.get('star_2', 0.4),
            'star_1': rating_mult.get('star_1', 0.2),
        },
        'negative_signals': {
            'enabled': bool(negsig.get('enabled', True)),
            'bad_ratings_enabled': bool(bad_ratings.get('enabled', True)),
            'bad_ratings_threshold': bad_ratings.get('threshold', 3),
            'bad_ratings_cap_penalty': bad_ratings.get('cap_penalty', 0.5),
            'dropped_enabled': bool(dropped_shows.get('enabled', True)),
            'dropped_min_episodes': dropped_shows.get('min_episodes_watched', 2),
            'dropped_max_completion': dropped_shows.get('max_completion_percent', 25),
            'dropped_penalty_multiplier': dropped_shows.get('penalty_multiplier', -0.4),
        },
        'external': {
            'enabled': bool(external.get('enabled', True)),
            'movie_limit': external.get('movie_limit', 50),
            'show_limit': external.get('show_limit', 20),
            'min_relevance_score': external.get('min_relevance_score', 0.65),
            'min_votes': external.get('min_votes', 50),
            'max_iterations': external.get('max_iterations', 5),
            'language': external.get('language') or '',
            'auto_open_html': bool(external.get('auto_open_html', False)),
        },
        'general': {
            # get_update_mode() resolves the effective mode, falling back
            # to the legacy auto_update flag for installs that predate
            # update_mode - so this screen shows the mode that's actually
            # in effect even before a user has ever saved the new field.
            'update_mode': get_update_mode(core),
            'log_retention_days': general.get('log_retention_days', 7),
            'plex_only': bool(general.get('plex_only', True)),
        },
        'logging': {
            'level': logging_cfg.get('level', 'INFO'),
        },
        'sync_safety': {
            'sonarr': {
                'auto_sync': bool(sonarr.get('auto_sync', False)),
                'user_mode': sonarr.get('user_mode', 'mapping'),
                'plex_users': format_csv_list(sonarr.get('plex_users')),
            },
            'radarr': {
                'auto_sync': bool(radarr.get('auto_sync', False)),
                'user_mode': radarr.get('user_mode', 'mapping'),
                'plex_users': format_csv_list(radarr.get('plex_users')),
            },
            'trakt': {
                'auto_sync': bool(trakt_export.get('auto_sync', False)),
                'user_mode': trakt_export.get('user_mode', 'mapping'),
                'plex_users': format_csv_list(trakt_export.get('plex_users')),
            },
        },
        'log_level_choices': LOG_LEVEL_CHOICES,
        'user_mode_choices': USER_MODE_CHOICES,
        'update_mode_choices': UPDATE_MODES,
    }


def _parse_settings_form(form, errors: Dict[str, str]) -> Dict:
    def flag(name: str) -> bool:
        return form.get(name) in ('on', 'true', '1')

    def f(name, lo=None, hi=None, label=None):
        # On a parse failure, redisplay whatever the user typed (instead of
        # None) so the error-correction round trip doesn't show "None" in
        # the field they need to fix.
        parsed = validate_float(form.get(name), name, errors, lo=lo, hi=hi, label=label)
        return parsed if parsed is not None else form.get(name, '')

    def i(name, lo=None, hi=None, label=None):
        parsed = validate_int(form.get(name), name, errors, lo=lo, hi=hi, label=label)
        return parsed if parsed is not None else form.get(name, '')

    movies_weight_fields = (
        'movies_weight_genre', 'movies_weight_director', 'movies_weight_actor', 'movies_weight_keyword',
    )
    movies_weights = {
        'genre': f('movies_weight_genre', 0, 1, 'Movie genre weight'),
        'director': f('movies_weight_director', 0, 1, 'Movie director weight'),
        'actor': f('movies_weight_actor', 0, 1, 'Movie actor weight'),
        'keyword': f('movies_weight_keyword', 0, 1, 'Movie keyword weight'),
    }
    # Only check the sum if every individual weight parsed cleanly -
    # summing raw invalid strings would raise instead of reporting a
    # clean validation error.
    if not any(field in errors for field in movies_weight_fields):
        validate_weights_sum(movies_weights, 'movies_weights', errors)

    tv_weight_fields = ('tv_weight_genre', 'tv_weight_studio', 'tv_weight_actor', 'tv_weight_keyword')
    tv_weights = {
        'genre': f('tv_weight_genre', 0, 1, 'TV genre weight'),
        'studio': f('tv_weight_studio', 0, 1, 'TV studio weight'),
        'actor': f('tv_weight_actor', 0, 1, 'TV actor weight'),
        'keyword': f('tv_weight_keyword', 0, 1, 'TV keyword weight'),
    }
    if not any(field in errors for field in tv_weight_fields):
        validate_weights_sum(tv_weights, 'tv_weights', errors)

    movies = {
        'weights': movies_weights,
        'limit_results': i('movies_limit_results', 1, 1000, 'Movie result limit'),
        'min_rating': f('movies_min_rating', 0, 10, 'Movie min rating'),
        'min_vote_count': i('movies_min_vote_count', 0, None, 'Movie min vote count'),
    }
    tv = {
        'weights': tv_weights,
        'limit_results': i('tv_limit_results', 1, 1000, 'TV result limit'),
        'min_rating': f('tv_min_rating', 0, 10, 'TV min rating'),
        'min_vote_count': i('tv_min_vote_count', 0, None, 'TV min vote count'),
    }
    recency = {
        'enabled': flag('recency_enabled'),
        'days_0_30': f('recency_days_0_30', 0, None, '0-30 day weight'),
        'days_31_90': f('recency_days_31_90', 0, None, '31-90 day weight'),
        'days_91_180': f('recency_days_91_180', 0, None, '91-180 day weight'),
        'days_181_365': f('recency_days_181_365', 0, None, '181-365 day weight'),
        'days_365_plus': f('recency_days_365_plus', 0, None, '365+ day weight'),
    }
    rating_multipliers = {
        'star_5': f('rating_star_5', 0, None, '5-star multiplier'),
        'star_4': f('rating_star_4', 0, None, '4-star multiplier'),
        'star_3': f('rating_star_3', 0, None, '3-star multiplier'),
        'star_2': f('rating_star_2', 0, None, '2-star multiplier'),
        'star_1': f('rating_star_1', 0, None, '1-star multiplier'),
    }
    negative_signals = {
        'enabled': flag('negsig_enabled'),
        'bad_ratings_enabled': flag('negsig_bad_ratings_enabled'),
        'bad_ratings_threshold': i('negsig_bad_ratings_threshold', 0, 10, 'Bad rating threshold'),
        'bad_ratings_cap_penalty': f('negsig_bad_ratings_cap_penalty', 0, 1, 'Bad rating cap penalty'),
        'dropped_enabled': flag('negsig_dropped_enabled'),
        'dropped_min_episodes': i('negsig_dropped_min_episodes', 0, None, 'Min episodes watched'),
        'dropped_max_completion': i('negsig_dropped_max_completion', 0, 100, 'Max completion percent'),
        'dropped_penalty_multiplier': f('negsig_dropped_penalty_multiplier', None, None, 'Dropped show penalty'),
    }
    external = {
        'enabled': flag('ext_enabled'),
        'movie_limit': i('ext_movie_limit', 0, None, 'External movie limit'),
        'show_limit': i('ext_show_limit', 0, None, 'External show limit'),
        'min_relevance_score': f('ext_min_relevance_score', 0, 1, 'Min relevance score'),
        'min_votes': i('ext_min_votes', 0, None, 'Min votes'),
        'max_iterations': i('ext_max_iterations', 1, None, 'Max iterations'),
        'language': form.get('ext_language', '').strip(),
        'auto_open_html': flag('ext_auto_open_html'),
    }
    update_mode = form.get('general_update_mode', 'notify')
    validate_choice(update_mode, 'general_update_mode', errors, UPDATE_MODES)
    general = {
        'update_mode': update_mode,
        'log_retention_days': i('general_log_retention_days', 0, None, 'Log retention days'),
        'plex_only': flag('general_plex_only'),
    }
    logging_level = form.get('logging_level', 'INFO')
    validate_choice(logging_level, 'logging_level', errors, LOG_LEVEL_CHOICES)

    sync_safety = {}
    for svc in ('sonarr', 'radarr', 'trakt'):
        user_mode = form.get(f'{svc}_user_mode', 'mapping')
        validate_choice(user_mode, f'{svc}_user_mode', errors, USER_MODE_CHOICES)
        sync_safety[svc] = {
            'auto_sync': flag(f'{svc}_auto_sync'),
            'user_mode': user_mode,
            'plex_users': format_csv_list(parse_csv_list(form.get(f'{svc}_plex_users', ''))),
        }

    return {
        'movies': movies,
        'tv': tv,
        'recency': recency,
        'rating_multipliers': rating_multipliers,
        'negative_signals': negative_signals,
        'external': external,
        'general': general,
        'logging': {'level': logging_level},
        'sync_safety': sync_safety,
        'log_level_choices': LOG_LEVEL_CHOICES,
        'user_mode_choices': USER_MODE_CHOICES,
        'update_mode_choices': UPDATE_MODES,
    }


def _apply_settings(project_root: str, tuning: CommentedMap, core: CommentedMap,
                     sonarr: CommentedMap, radarr: CommentedMap, trakt: CommentedMap,
                     parsed: Dict) -> Dict[str, CommentedMap]:
    """Mutate the in-memory CommentedMaps for this screen and return them
    keyed by module name, WITHOUT writing to disk - see
    _apply_connections' docstring for why."""
    movies_section = ensure_section(tuning, 'movies')
    movies_section['limit_results'] = parsed['movies']['limit_results']
    ensure_section(movies_section, 'weights').update(parsed['movies']['weights'])
    movies_quality = ensure_section(movies_section, 'quality_filters')
    movies_quality['min_rating'] = parsed['movies']['min_rating']
    movies_quality['min_vote_count'] = parsed['movies']['min_vote_count']

    tv_section = ensure_section(tuning, 'tv')
    tv_section['limit_results'] = parsed['tv']['limit_results']
    ensure_section(tv_section, 'weights').update(parsed['tv']['weights'])
    tv_quality = ensure_section(tv_section, 'quality_filters')
    tv_quality['min_rating'] = parsed['tv']['min_rating']
    tv_quality['min_vote_count'] = parsed['tv']['min_vote_count']

    ensure_section(tuning, 'recency_decay').update(parsed['recency'])
    ensure_section(tuning, 'rating_multipliers').update(parsed['rating_multipliers'])

    ns = parsed['negative_signals']
    negsig = ensure_section(tuning, 'negative_signals')
    negsig['enabled'] = ns['enabled']
    bad_ratings = ensure_section(negsig, 'bad_ratings')
    bad_ratings['enabled'] = ns['bad_ratings_enabled']
    bad_ratings['threshold'] = ns['bad_ratings_threshold']
    bad_ratings['cap_penalty'] = ns['bad_ratings_cap_penalty']
    dropped_shows = ensure_section(negsig, 'dropped_shows')
    dropped_shows['enabled'] = ns['dropped_enabled']
    dropped_shows['min_episodes_watched'] = ns['dropped_min_episodes']
    dropped_shows['max_completion_percent'] = ns['dropped_max_completion']
    dropped_shows['penalty_multiplier'] = ns['dropped_penalty_multiplier']

    ext = parsed['external']
    ext_section = ensure_section(tuning, 'external_recommendations')
    ext_section['enabled'] = ext['enabled']
    ext_section['movie_limit'] = ext['movie_limit']
    ext_section['show_limit'] = ext['show_limit']
    ext_section['min_relevance_score'] = ext['min_relevance_score']
    ext_section['min_votes'] = ext['min_votes']
    ext_section['max_iterations'] = ext['max_iterations']
    ext_section['language'] = ext['language'] or None
    ext_section['auto_open_html'] = ext['auto_open_html']

    ensure_section(core, 'general').update(parsed['general'])
    ensure_section(core, 'logging')['level'] = parsed['logging']['level']

    sonarr['auto_sync'] = parsed['sync_safety']['sonarr']['auto_sync']
    sonarr['user_mode'] = parsed['sync_safety']['sonarr']['user_mode']
    sonarr['plex_users'] = parse_csv_list(parsed['sync_safety']['sonarr']['plex_users'])

    radarr['auto_sync'] = parsed['sync_safety']['radarr']['auto_sync']
    radarr['user_mode'] = parsed['sync_safety']['radarr']['user_mode']
    radarr['plex_users'] = parse_csv_list(parsed['sync_safety']['radarr']['plex_users'])

    trakt_export = trakt.get('export') or CommentedMap()
    trakt_export['auto_sync'] = parsed['sync_safety']['trakt']['auto_sync']
    trakt_export['user_mode'] = parsed['sync_safety']['trakt']['user_mode']
    trakt_export['plex_users'] = parse_csv_list(parsed['sync_safety']['trakt']['plex_users'])
    trakt['export'] = trakt_export

    return {'tuning': tuning, 'config': core, 'sonarr': sonarr, 'radarr': radarr, 'trakt': trakt}
