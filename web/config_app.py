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
from typing import Dict, Optional

from flask import jsonify, redirect, render_template, request, url_for
from ruamel.yaml.comments import CommentedMap

from utils import load_config
from utils.plex import MOVIE_RATING_HIERARCHY, TV_RATING_HIERARCHY

from .config_io import (
    format_csv_list,
    load_module,
    merge_secret,
    module_path,
    parse_csv_list,
    save_module,
    secret_status,
)
from .config_test_connection import TESTERS
from .security import redact
from .config_validate import (
    validate_choice,
    validate_float,
    validate_int,
    validate_required,
    validate_url,
    validate_weights_sum,
)

logger = logging.getLogger('curatarr')

USER_MODE_CHOICES = ('mapping', 'per_user', 'combined')
LOG_LEVEL_CHOICES = ('DEBUG', 'INFO', 'WARNING', 'ERROR')
RATING_CHOICES = MOVIE_RATING_HIERARCHY + TV_RATING_HIERARCHY


def _reload_error(project_root: str) -> Optional[str]:
    """Post-write sanity check: reload everything just written through the
    same utils.load_config merge path the recommenders use at run time.

    Field-level validation (weights sum, URLs, choices - see
    config_validate.py) happens *before* any file is touched, which is
    what actually prevents bad input from ever reaching disk. This is a
    second, defense-in-depth check that the files we just wrote still
    parse and merge cleanly - it should never fire given the writers
    above only ever set well-typed values, but if it does, the operator
    finds out immediately instead of on the next scheduled run.
    """
    try:
        load_config(module_path(project_root, 'config'))
    except Exception as exc:
        logger.error(f"Post-write config reload check failed: {exc}")
        return str(exc)
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

        _apply_connections(project_root, data, parsed)
        reload_error = _reload_error(project_root)
        if reload_error:
            return render_template(
                'config_connections.html',
                saved=False,
                errors={'_global': f'Saved, but the config failed to reload: {reload_error}'},
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
        # was configured on a previous save.
        existing = _existing_secret_lookup(data, service)
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

        _apply_users(project_root, core, parsed)
        reload_error = _reload_error(project_root)
        if reload_error:
            return render_template(
                'config_users.html',
                saved=False,
                errors={'_global': f'Saved, but the config failed to reload: {reload_error}'},
                **_users_view(load_module(module_path(project_root, 'config'))),
            ), 500
        return redirect(url_for('config_users', saved='1'), code=303)

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

        _apply_settings(project_root, tuning, core, sonarr, radarr, trakt, parsed)
        reload_error = _reload_error(project_root)
        if reload_error:
            return render_template(
                'config_settings.html',
                saved=False,
                errors={'_global': f'Saved, but the config failed to reload: {reload_error}'},
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

def _load_connections(project_root: str) -> Dict[str, CommentedMap]:
    return {
        'core': load_module(module_path(project_root, 'config')),
        'sonarr': load_module(module_path(project_root, 'sonarr')),
        'radarr': load_module(module_path(project_root, 'radarr')),
        'trakt': load_module(module_path(project_root, 'trakt')),
    }


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
            'movie_library': pick('plex', 'movie_library', plex.get('movie_library', '')),
            'tv_library': pick('plex', 'tv_library', plex.get('tv_library', '')),
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
    plex_movie_library = form.get('plex_movie_library', '').strip()
    validate_required(plex_movie_library, 'plex_movie_library', errors, 'Movie library')
    plex_tv_library = form.get('plex_tv_library', '').strip()
    validate_required(plex_tv_library, 'plex_tv_library', errors, 'TV library')

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
            'movie_library': plex_movie_library,
            'tv_library': plex_tv_library,
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


def _apply_connections(project_root: str, data: Dict[str, CommentedMap], parsed: Dict) -> None:
    core = data['core']

    core.setdefault('plex', CommentedMap())
    core['plex']['url'] = parsed['plex']['url']
    core['plex']['movie_library'] = parsed['plex']['movie_library']
    core['plex']['tv_library'] = parsed['plex']['tv_library']
    core['plex']['token'] = merge_secret(core['plex'].get('token'), parsed['plex']['token_submitted'])

    core.setdefault('tmdb', CommentedMap())
    core['tmdb']['api_key'] = merge_secret(core['tmdb'].get('api_key'), parsed['tmdb']['api_key_submitted'])

    core.setdefault('tautulli', CommentedMap())
    core['tautulli']['enabled'] = parsed['tautulli']['enabled']
    core['tautulli']['url'] = parsed['tautulli']['url']
    core['tautulli']['api_key'] = merge_secret(core['tautulli'].get('api_key'), parsed['tautulli']['api_key_submitted'])

    save_module(module_path(project_root, 'config'), core)

    sonarr = data['sonarr']
    sonarr['enabled'] = parsed['sonarr']['enabled']
    sonarr['url'] = parsed['sonarr']['url']
    sonarr['api_key'] = merge_secret(sonarr.get('api_key'), parsed['sonarr']['api_key_submitted'])
    sonarr['auto_sync'] = parsed['sonarr']['auto_sync']
    sonarr['user_mode'] = parsed['sonarr']['user_mode']
    sonarr['plex_users'] = parse_csv_list(parsed['sonarr']['plex_users'])
    save_module(module_path(project_root, 'sonarr'), sonarr)

    radarr = data['radarr']
    radarr['enabled'] = parsed['radarr']['enabled']
    radarr['url'] = parsed['radarr']['url']
    radarr['api_key'] = merge_secret(radarr.get('api_key'), parsed['radarr']['api_key_submitted'])
    radarr['auto_sync'] = parsed['radarr']['auto_sync']
    radarr['user_mode'] = parsed['radarr']['user_mode']
    radarr['plex_users'] = parse_csv_list(parsed['radarr']['plex_users'])
    save_module(module_path(project_root, 'radarr'), radarr)

    trakt = data['trakt']
    trakt['enabled'] = parsed['trakt']['enabled']
    trakt['client_id'] = parsed['trakt']['client_id']
    trakt['client_secret'] = merge_secret(trakt.get('client_secret'), parsed['trakt']['client_secret_submitted'])
    export = trakt.get('export') or CommentedMap()
    export['auto_sync'] = parsed['trakt']['auto_sync']
    export['user_mode'] = parsed['trakt']['user_mode']
    export['plex_users'] = parse_csv_list(parsed['trakt']['plex_users'])
    trakt['export'] = export
    save_module(module_path(project_root, 'trakt'), trakt)


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


def _apply_users(project_root: str, core: CommentedMap, parsed: Dict) -> None:
    core.setdefault('users', CommentedMap())
    kept = [row for row in parsed['users'] if not row['remove']]

    core['users']['list'] = ', '.join(row['username'] for row in kept)

    preferences = core['users'].get('preferences')
    if preferences is None:
        preferences = CommentedMap()
        core['users']['preferences'] = preferences

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

    save_module(module_path(project_root, 'config'), core)


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
            'auto_update': bool(general.get('auto_update', False)),
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
    general = {
        'auto_update': flag('general_auto_update'),
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
    }


def _apply_settings(project_root: str, tuning: CommentedMap, core: CommentedMap,
                     sonarr: CommentedMap, radarr: CommentedMap, trakt: CommentedMap,
                     parsed: Dict) -> None:
    tuning.setdefault('movies', CommentedMap())
    tuning['movies']['limit_results'] = parsed['movies']['limit_results']
    tuning['movies'].setdefault('weights', CommentedMap())
    tuning['movies']['weights'].update(parsed['movies']['weights'])
    tuning['movies'].setdefault('quality_filters', CommentedMap())
    tuning['movies']['quality_filters']['min_rating'] = parsed['movies']['min_rating']
    tuning['movies']['quality_filters']['min_vote_count'] = parsed['movies']['min_vote_count']

    tuning.setdefault('tv', CommentedMap())
    tuning['tv']['limit_results'] = parsed['tv']['limit_results']
    tuning['tv'].setdefault('weights', CommentedMap())
    tuning['tv']['weights'].update(parsed['tv']['weights'])
    tuning['tv'].setdefault('quality_filters', CommentedMap())
    tuning['tv']['quality_filters']['min_rating'] = parsed['tv']['min_rating']
    tuning['tv']['quality_filters']['min_vote_count'] = parsed['tv']['min_vote_count']

    tuning.setdefault('recency_decay', CommentedMap())
    tuning['recency_decay'].update(parsed['recency'])

    tuning.setdefault('rating_multipliers', CommentedMap())
    tuning['rating_multipliers'].update(parsed['rating_multipliers'])

    ns = parsed['negative_signals']
    tuning.setdefault('negative_signals', CommentedMap())
    tuning['negative_signals']['enabled'] = ns['enabled']
    tuning['negative_signals'].setdefault('bad_ratings', CommentedMap())
    tuning['negative_signals']['bad_ratings']['enabled'] = ns['bad_ratings_enabled']
    tuning['negative_signals']['bad_ratings']['threshold'] = ns['bad_ratings_threshold']
    tuning['negative_signals']['bad_ratings']['cap_penalty'] = ns['bad_ratings_cap_penalty']
    tuning['negative_signals'].setdefault('dropped_shows', CommentedMap())
    tuning['negative_signals']['dropped_shows']['enabled'] = ns['dropped_enabled']
    tuning['negative_signals']['dropped_shows']['min_episodes_watched'] = ns['dropped_min_episodes']
    tuning['negative_signals']['dropped_shows']['max_completion_percent'] = ns['dropped_max_completion']
    tuning['negative_signals']['dropped_shows']['penalty_multiplier'] = ns['dropped_penalty_multiplier']

    ext = parsed['external']
    tuning.setdefault('external_recommendations', CommentedMap())
    tuning['external_recommendations']['enabled'] = ext['enabled']
    tuning['external_recommendations']['movie_limit'] = ext['movie_limit']
    tuning['external_recommendations']['show_limit'] = ext['show_limit']
    tuning['external_recommendations']['min_relevance_score'] = ext['min_relevance_score']
    tuning['external_recommendations']['min_votes'] = ext['min_votes']
    tuning['external_recommendations']['max_iterations'] = ext['max_iterations']
    tuning['external_recommendations']['language'] = ext['language'] or None
    tuning['external_recommendations']['auto_open_html'] = ext['auto_open_html']

    save_module(module_path(project_root, 'tuning'), tuning)

    core.setdefault('general', CommentedMap())
    core['general'].update(parsed['general'])
    core.setdefault('logging', CommentedMap())
    core['logging']['level'] = parsed['logging']['level']
    save_module(module_path(project_root, 'config'), core)

    sonarr['auto_sync'] = parsed['sync_safety']['sonarr']['auto_sync']
    sonarr['user_mode'] = parsed['sync_safety']['sonarr']['user_mode']
    sonarr['plex_users'] = parse_csv_list(parsed['sync_safety']['sonarr']['plex_users'])
    save_module(module_path(project_root, 'sonarr'), sonarr)

    radarr['auto_sync'] = parsed['sync_safety']['radarr']['auto_sync']
    radarr['user_mode'] = parsed['sync_safety']['radarr']['user_mode']
    radarr['plex_users'] = parse_csv_list(parsed['sync_safety']['radarr']['plex_users'])
    save_module(module_path(project_root, 'radarr'), radarr)

    trakt_export = trakt.get('export') or CommentedMap()
    trakt_export['auto_sync'] = parsed['sync_safety']['trakt']['auto_sync']
    trakt_export['user_mode'] = parsed['sync_safety']['trakt']['user_mode']
    trakt_export['plex_users'] = parse_csv_list(parsed['sync_safety']['trakt']['plex_users'])
    trakt['export'] = trakt_export
    save_module(module_path(project_root, 'trakt'), trakt)
