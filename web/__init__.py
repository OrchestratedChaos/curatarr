"""Curatarr local web UI (MVP): dashboard, run-with-live-log, results.

Self-contained package - templates/ and static/ are bundled inside this
package (not read from elsewhere) so it can later be frozen with
PyInstaller --onefile without extra data-file wiring.

This package only reads existing curatarr state (config, logs,
generated watchlists) and triggers recommender runs as subprocesses.
It never imports recommenders/*.py in-process - see web/job_runner.py.
"""
