#!/bin/bash
# Shared "prefer hash-verified lockfile(s), fall back to plain pinned
# requirements file(s)" pip install helper. Sourced by run.sh and
# run-ui.sh, which used to each implement this same fallback contract
# independently (their own comments cross-referenced each other's
# copy - see the 2.10.18 audit-remediation pass).
#
# Requires scripts/lib/python-env.sh to have been sourced first, and
# curatarr_resolve_python to have run: the actual pip invocation goes
# through curatarr_pip (i.e. `$CURATARR_PYTHON -m pip`), never a bare
# `pip3`, so a project-local venv without its own pip can't end up
# installing into the system interpreter. See that file for why.
#
# Only the pip invocation + fallback decision is shared here - each
# caller keeps its own success/failure messaging (run.sh wants colored
# checkmark/warning output and a hard `exit 1` on total failure;
# run-ui.sh is quieter and relies on `set -e`) via optional callback
# function names, so this doesn't change either script's user-visible
# behavior or timing.
#
# curatarr_pip_install LOCK_FILES REQ_FILES [ON_HASH_FAIL_FN] [ON_NO_LOCK_FN]
#
#   LOCK_FILES / REQ_FILES: space-separated lists of one or more paths
#     (e.g. a single pair for run.sh's core deps, or two files' worth
#     for run-ui.sh's core + web-UI deps).
#   ON_HASH_FAIL_FN: name of a function to call (no args), if given,
#     right after a hash-verified install attempt fails, before
#     falling back - lets the caller print its own warning at the
#     right moment (before the fallback install runs, not after).
#   ON_NO_LOCK_FN: name of a function to call (no args), if given,
#     when LOCK_FILES doesn't apply (empty, or not every file in it
#     exists) - before falling back straight to REQ_FILES.
#
#   Sets CURATARR_PIP_INSTALL_MODE to "hash-verified" or "fallback" on
#   success (the caller distinguishes "fallback because no lockfile"
#   from "fallback because hash-verified failed" itself, e.g. via its
#   own callback setting a variable - see run.sh's call site).
#
#   Returns 0 on success (either path). Returns non-zero if the
#   hash-verified attempt (when applicable) failed AND the fallback
#   also failed, or if none of REQ_FILES exist to fall back to at all.
curatarr_pip_install() {
    local lock_files="$1"
    local req_files="$2"
    local on_hash_fail_fn="$3"
    local on_no_lock_fn="$4"
    local f
    CURATARR_PIP_INSTALL_MODE=""

    local all_locks_exist=1
    if [ -z "$lock_files" ]; then
        all_locks_exist=0
    else
        for f in $lock_files; do
            [ -f "$f" ] || { all_locks_exist=0; break; }
        done
    fi

    if [ "$all_locks_exist" -eq 1 ]; then
        local lock_args=""
        for f in $lock_files; do
            lock_args="$lock_args -r $f"
        done
        # shellcheck disable=SC2086  # intentional word-splitting of -r flags
        if curatarr_pip install --require-hashes $lock_args --quiet; then
            CURATARR_PIP_INSTALL_MODE="hash-verified"
            return 0
        fi
        [ -n "$on_hash_fail_fn" ] && "$on_hash_fail_fn"
    else
        [ -n "$on_no_lock_fn" ] && "$on_no_lock_fn"
    fi

    local req_args=""
    local any_req_exists=0
    for f in $req_files; do
        if [ -f "$f" ]; then
            req_args="$req_args -r $f"
            any_req_exists=1
        fi
    done
    [ "$any_req_exists" -eq 1 ] || return 1

    # shellcheck disable=SC2086
    if curatarr_pip install $req_args --quiet; then
        CURATARR_PIP_INSTALL_MODE="fallback"
        return 0
    fi
    return 1
}
