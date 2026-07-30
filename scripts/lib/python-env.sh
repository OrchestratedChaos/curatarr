#!/bin/bash
# Shared "which Python interpreter should this run use" resolver, sourced
# by run.sh and run-ui.sh.
#
# Both scripts used to call bare `python3` and `pip3` and take whatever
# PATH happened to hand them, which broke the extremely common case of a
# project-local virtualenv: a checkout with a working ./.venv (3.12) on a
# machine whose system `python3` is older (macOS ships 3.9.6 via the
# Command Line Tools) would fail the Python floor gate and refuse to
# start, while the interpreter that satisfied the floor sat unused two
# directories away. run.ps1 has always done this properly - it resolves
# $pythonCmd once and invokes `& $pythonCmd -m pip` - so this is the bash
# side catching up to the PowerShell side, not a new contract.
#
# curatarr_resolve_python PROJECT_ROOT
#
#   Sets (and exports):
#     CURATARR_PYTHON - the interpreter every python/pip invocation
#                       should go through. An absolute path when a venv
#                       is in play, otherwise plain `python3`.
#     CURATARR_VENV   - the venv that was adopted, or "" if none. Callers
#                       use this for messaging only.
#     VIRTUAL_ENV / PATH - set the way `activate` sets them when a venv
#                       is adopted, so bare `python3` in this script, in
#                       anything it execs, and in any child process
#                       resolves to the same interpreter.
#
#   Always returns 0 - "no venv anywhere" is the normal case, not an
#   error, and these scripts run under `set -e`.
#
# Resolution order:
#   1. CURATARR_NO_VENV=1        - explicit opt-out, use PATH's python3.
#   2. An already-activated venv - $VIRTUAL_ENV wins. Someone who ran
#      `source .venv/bin/activate` (or is inside tox/CI) has stated their
#      intent more directly than a directory listing can.
#   3. PROJECT_ROOT/.venv, then PROJECT_ROOT/venv - the two conventional
#      names, checked in that order.
#   4. python3 from PATH - the historical behavior, unchanged.
#
# Note that an adopted venv is used even if it turns out to be older than
# requirements.lock's floor. Silently ignoring a venv the user explicitly
# created would be the more surprising behavior of the two; instead the
# floor gate in the caller reports which interpreter it rejected (see the
# CURATARR_VENV mention in run.sh's and run-ui.sh's floor messages), so a
# stale venv produces an obvious diagnosis rather than a confusing one.
curatarr_resolve_python() {
    local project_root="$1"
    local candidate

    CURATARR_VENV=""

    if [ -n "${CURATARR_NO_VENV:-}" ]; then
        CURATARR_PYTHON="python3"
        export CURATARR_PYTHON CURATARR_VENV
        return 0
    fi

    if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python3" ]; then
        CURATARR_VENV="$VIRTUAL_ENV"
        CURATARR_PYTHON="${VIRTUAL_ENV}/bin/python3"
        _curatarr_prepend_path "${VIRTUAL_ENV}/bin"
        export CURATARR_PYTHON CURATARR_VENV
        return 0
    fi

    for candidate in "${project_root}/.venv" "${project_root}/venv"; do
        if [ -x "${candidate}/bin/python3" ]; then
            CURATARR_VENV="$candidate"
            CURATARR_PYTHON="${candidate}/bin/python3"
            VIRTUAL_ENV="$candidate"
            export VIRTUAL_ENV
            # PYTHONHOME would override the venv's own prefix and is what
            # `activate` unsets for exactly this reason.
            unset PYTHONHOME
            _curatarr_prepend_path "${candidate}/bin"
            export CURATARR_PYTHON CURATARR_VENV
            return 0
        fi
    done

    CURATARR_PYTHON="python3"
    export CURATARR_PYTHON CURATARR_VENV
    return 0
}

# Put $1 at the front of PATH, without duplicating it if it's already
# there (re-running this shouldn't grow PATH without bound).
_curatarr_prepend_path() {
    local dir="$1"
    case ":${PATH}:" in
        *":${dir}:"*) ;;
        *) PATH="${dir}:${PATH}"; export PATH ;;
    esac
}

# Run pip for the resolved interpreter.
#
# ALWAYS `$CURATARR_PYTHON -m pip`, never a bare `pip3`, and this is the
# whole reason the resolver exists rather than just prepending to PATH.
# A `uv venv` (and `python -m venv --without-pip`) creates a venv with NO
# pip in it at all. Prepending .venv/bin to PATH therefore redirects
# `python3` into the venv while leaving `pip3` resolving to the SYSTEM
# pip - so dependencies get installed into the system interpreter, the
# venv still can't import them, and the caller's `if ! python3 -c "import
# flask"` guard re-runs the install on every single launch. That silent
# split is strictly worse than the bug this file fixes, so pip is always
# addressed through the interpreter that will actually import the result.
#
# If that interpreter has no pip module, bootstrap it with ensurepip
# (present and functional in uv-created venvs) before giving up.
curatarr_pip() {
    if ! "$CURATARR_PYTHON" -m pip --version > /dev/null 2>&1; then
        "$CURATARR_PYTHON" -m ensurepip --upgrade > /dev/null 2>&1 || return 1
    fi
    "$CURATARR_PYTHON" -m pip "$@"
}
