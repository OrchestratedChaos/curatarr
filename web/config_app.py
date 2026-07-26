"""Config screens: Setup/Connections, Users, Libraries, Settings/Tuning.

Extends web/app.py with four additional screens so curatarr can be set
up entirely from the browser instead of hand-editing YAML. Each screen
lives in its own sibling module, reading and writing through
web.config_io's round-trip helpers, which respect the same modular
config/*.yml layout utils.config._load_module_configs already merges at
run time:

    config.yml       - plex, tmdb, tautulli, users, general, logging
    tuning.yml       - movies/tv weights+quality_filters+limit_results,
                       recency_decay, rating_multipliers, negative_signals,
                       external_recommendations
    sonarr.yml / radarr.yml / trakt.yml - one file per integration

Package layout (audit remediation batch F/I, PR1(a) - this module used
to hold all four screens' view/parse/apply CRUD logic directly; it's now
just the dispatcher):

    config_io.py               - shared YAML load/save/merge/secret
                                  helpers + commit_modules + constants
                                  used by more than one screen
    config_validate.py         - shared field-level validators
    config_test_connection.py  - the "Test Connection" checks
    config_connections.py      - Setup / Connections screen
    config_users.py            - Users screen
    config_libraries.py        - Libraries screen (#157 Phase 4)
    config_settings.py         - Settings / Tuning screen
    config_app.py (this file)  - register_config_routes(), calling each
                                  screen's own register_*_routes()

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

from .config_connections import register_connections_routes
from .config_libraries import register_libraries_routes
from .config_settings import register_settings_routes
from .config_users import register_users_routes


def register_config_routes(app) -> None:
    """Register every config screen's routes onto *app* - called once
    from web.app.create_app(). See this module's own docstring for which
    sibling module owns which screen."""
    register_connections_routes(app)
    register_users_routes(app)
    register_libraries_routes(app)
    register_settings_routes(app)
