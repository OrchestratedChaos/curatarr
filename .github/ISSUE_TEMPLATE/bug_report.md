---
name: Bug report
about: Create a report to help us improve
title: ''
labels: ''
assignees: ''

---

**Describe the bug**
A clear and concise description of what the bug is.

**To Reproduce**
Steps to reproduce the behavior (config used, which recommender/mode,
CLI flags, etc.).

**Expected behavior**
A clear and concise description of what you expected to happen.

**Environment**
 - Curatarr version: [e.g. 2.10.18 - standalone binary: `curatarr --version`; source install: `python3 -c "from utils.config import __version__; print(__version__)"` or the top entry in `CHANGELOG.md`]
 - Install method: [standalone binary / Docker / source (`run.sh`/`run.ps1`)]
 - OS: [e.g. macOS 14, Windows 11, Ubuntu 22.04, Docker host OS]
 - Plex Media Server version: [e.g. 1.40.x]

**Relevant log excerpt**
Paste the relevant excerpt (not the whole file) from:
 - Source install: `logs/daily-run.log` (or console output)
 - Docker: `docker logs <container>` (see [docs/DOCKER.md](../../docs/DOCKER.md#troubleshooting))
 - Web UI: the in-app job log for the failed run

Redact your Plex token, TMDB/Trakt/Sonarr/Radarr API keys, and any
other secrets before pasting.

**Additional context**
Add any other context about the problem here.
