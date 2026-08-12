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
