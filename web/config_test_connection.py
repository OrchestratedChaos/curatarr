"""Test Connection checks for the Setup / Connections screen.

Reuses the same client classes/helpers the recommenders use at run time
(utils.plex.init_plex, utils.radarr.RadarrClient, etc.) so "Test
Connection" in the browser exercises exactly the same code path a real
run would. Every check here is read-only - it confirms the given
credentials can reach the service, it never writes/adds/removes
anything.

Each test_* function takes raw candidate values (not the saved config)
so a user can check a URL/token before saving it. web/config_app.py is
responsible for merging a blank "keep existing secret" submission with
the already-saved value before calling in here.
"""

import logging
from typing import Callable, Dict

from utils.plex import init_plex
from utils.radarr import RadarrAPIError, RadarrClient
from utils.sonarr import SonarrAPIError, SonarrClient
from utils.tautulli import TautulliAPIError, TautulliClient
from utils.tmdb import fetch_tmdb_with_retry
from utils.trakt import TraktClient

from .security import redact

logger = logging.getLogger('curatarr')


def _connection_failed(exc: Exception, service: str) -> Dict:
    """Build the {ok, message} failure result for a raised client
    exception, redacting it first - some client errors can echo the
    request URL (e.g. Plex's X-Plex-Token as a query param), and this
    message is both logged server-side and shown in the browser."""
    message = redact(f'Connection failed: {exc}')
    logger.debug(f"{service} test connection failed: {message}")
    return {'ok': False, 'message': message}


def test_plex(url: str, token: str) -> Dict:
    if not url or not token:
        return {'ok': False, 'message': 'URL and token are required'}
    try:
        server = init_plex({'plex': {'url': url, 'token': token}})
        count = len(server.library.sections())
        return {'ok': True, 'message': f'Connected - {count} librar{"y" if count == 1 else "ies"} found'}
    except Exception as exc:
        return _connection_failed(exc, 'Plex')


def test_tmdb(api_key: str) -> Dict:
    if not api_key:
        return {'ok': False, 'message': 'API key is required'}
    result = fetch_tmdb_with_retry(
        'https://api.themoviedb.org/3/configuration', {'api_key': api_key},
        max_retries=1, timeout=8,
    )
    if result:
        return {'ok': True, 'message': 'Connected to TMDB'}
    return {'ok': False, 'message': 'Could not authenticate with TMDB - check the API key'}


def test_tautulli(url: str, api_key: str) -> Dict:
    if not url or not api_key:
        return {'ok': False, 'message': 'URL and API key are required'}
    try:
        client = TautulliClient(url, api_key)
        users = client.get_users()
        return {'ok': True, 'message': f'Connected - {len(users)} Tautulli user(s) found'}
    except Exception as exc:
        return _connection_failed(exc, 'Tautulli')


def test_sonarr(url: str, api_key: str) -> Dict:
    if not url or not api_key:
        return {'ok': False, 'message': 'URL and API key are required'}
    try:
        SonarrClient(url, api_key).test_connection()
        return {'ok': True, 'message': 'Connected to Sonarr'}
    except Exception as exc:
        return _connection_failed(exc, 'Sonarr')


def test_radarr(url: str, api_key: str) -> Dict:
    if not url or not api_key:
        return {'ok': False, 'message': 'URL and API key are required'}
    try:
        RadarrClient(url, api_key).test_connection()
        return {'ok': True, 'message': 'Connected to Radarr'}
    except Exception as exc:
        return _connection_failed(exc, 'Radarr')


def test_trakt(client_id: str, client_secret: str, access_token: str, refresh_token: str) -> Dict:
    if not client_id or not client_secret:
        return {'ok': False, 'message': 'Client ID and client secret are required'}
    if not access_token:
        return {
            'ok': False,
            'message': "Not authenticated yet - run 'python3 -m utils.trakt_auth' to link your Trakt account",
        }
    try:
        client = TraktClient(client_id, client_secret, access_token, refresh_token)
        username = client.get_username()
    except Exception as exc:
        # Matches every other tester in this module: a raw requests/API
        # error (network failure, unexpected response shape, etc.) must
        # never bubble up as an unhandled 500 with an unredacted
        # traceback - it gets the same {ok, message} shape as an
        # ordinary "connection failed" result instead.
        return _connection_failed(exc, 'Trakt')
    if username:
        return {'ok': True, 'message': f'Connected as {username}'}
    return {
        'ok': False,
        'message': "Connection failed - token may be expired. Re-run 'python3 -m utils.trakt_auth'",
    }


# service name -> (test_fn, required form field names in order)
TESTERS: Dict[str, Callable] = {
    'plex': lambda f: test_plex(f.get('url', ''), f.get('token', '')),
    'tmdb': lambda f: test_tmdb(f.get('api_key', '')),
    'tautulli': lambda f: test_tautulli(f.get('url', ''), f.get('api_key', '')),
    'sonarr': lambda f: test_sonarr(f.get('url', ''), f.get('api_key', '')),
    'radarr': lambda f: test_radarr(f.get('url', ''), f.get('api_key', '')),
    'trakt': lambda f: test_trakt(
        f.get('client_id', ''), f.get('client_secret', ''),
        f.get('access_token', ''), f.get('refresh_token', ''),
    ),
}
