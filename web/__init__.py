"""Curatarr local web UI (MVP): dashboard, run-with-live-log, results.

Self-contained package - templates/ and static/ are bundled inside this
package (not read from elsewhere), which is what lets it be frozen with
PyInstaller --onefile (see curatarr.spec / curatarr_app.py / docs/BINARIES.md)
without extra data-file wiring beyond declaring those two folders as
`datas` in the spec.

This package only reads existing curatarr state (config, logs,
generated watchlists) and triggers recommender runs as subprocesses.
It never imports recommenders/*.py in-process - see web/job_runner.py.
"""
