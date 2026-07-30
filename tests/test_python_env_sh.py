"""
Tests for scripts/lib/python-env.sh - the shared "which Python interpreter
should this run use" resolver that run.sh and run-ui.sh both source.

These drive the real shell function through `bash` against throwaway
project roots containing fake venvs, rather than testing a Python
re-implementation of it, because the bug being guarded here was
specifically about shell-level resolution order (PATH vs $VIRTUAL_ENV vs
./.venv) and about which binary a bare `pip3` resolves to.
"""

import pathlib
import re
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PYTHON_ENV_SH = REPO_ROOT / "scripts" / "lib" / "python-env.sh"
PIP_INSTALL_SH = REPO_ROOT / "scripts" / "lib" / "pip-install.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def make_fake_venv(root: pathlib.Path, name: str = ".venv", version: str = "3.12.13") -> pathlib.Path:
    """A directory that looks enough like a venv for the resolver: an
    executable bin/python3 that reports a version."""
    venv = root / name
    bindir = venv / "bin"
    bindir.mkdir(parents=True)
    python3 = bindir / "python3"
    python3.write_text(f'#!/bin/bash\necho "Python {version}"\n', encoding="utf-8")
    python3.chmod(0o755)
    return venv


def resolve(project_root: pathlib.Path, env_prefix: str = "") -> dict:
    """Source the resolver, run it, and report what it decided."""
    script = f"""
    set -u
    {env_prefix}
    source '{PYTHON_ENV_SH}'
    curatarr_resolve_python '{project_root}'
    echo "PYTHON=$CURATARR_PYTHON"
    echo "VENV=$CURATARR_VENV"
    echo "PATH=$PATH"
    """
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"resolver failed: {proc.stderr}"
    out = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


class TestResolvePython:
    def test_adopts_project_dot_venv(self, tmp_path):
        venv = make_fake_venv(tmp_path)
        result = resolve(tmp_path)
        assert result["PYTHON"] == str(venv / "bin" / "python3")
        assert result["VENV"] == str(venv)

    def test_prepends_venv_bin_to_path(self, tmp_path):
        venv = make_fake_venv(tmp_path)
        result = resolve(tmp_path)
        assert result["PATH"].split(":")[0] == str(venv / "bin")

    def test_does_not_duplicate_path_entry_when_run_twice(self, tmp_path):
        venv = make_fake_venv(tmp_path)
        script = f"""
        set -u
        source '{PYTHON_ENV_SH}'
        curatarr_resolve_python '{tmp_path}'
        curatarr_resolve_python '{tmp_path}'
        echo "$PATH"
        """
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().split(":").count(str(venv / "bin")) == 1

    def test_falls_back_to_plain_python3_with_no_venv(self, tmp_path):
        result = resolve(tmp_path)
        assert result["PYTHON"] == "python3"
        assert result["VENV"] == ""

    def test_dot_venv_wins_over_venv(self, tmp_path):
        """Both names present - .venv is checked first."""
        dot_venv = make_fake_venv(tmp_path, ".venv")
        make_fake_venv(tmp_path, "venv")
        assert resolve(tmp_path)["VENV"] == str(dot_venv)

    def test_accepts_undotted_venv_directory(self, tmp_path):
        venv = make_fake_venv(tmp_path, "venv")
        assert resolve(tmp_path)["VENV"] == str(venv)

    def test_curatarr_no_venv_opts_out(self, tmp_path):
        make_fake_venv(tmp_path)
        result = resolve(tmp_path, env_prefix="export CURATARR_NO_VENV=1")
        assert result["PYTHON"] == "python3"
        assert result["VENV"] == ""

    def test_already_activated_virtualenv_wins_over_project_dir(self, tmp_path):
        """Someone who ran `source activate` stated their intent more
        directly than a directory listing did."""
        project = tmp_path / "project"
        project.mkdir()
        make_fake_venv(project)
        active = make_fake_venv(tmp_path, "activated")
        result = resolve(project, env_prefix=f"export VIRTUAL_ENV='{active}'")
        assert result["VENV"] == str(active)
        assert result["PYTHON"] == str(active / "bin" / "python3")

    def test_ignores_virtualenv_pointing_at_nothing(self, tmp_path):
        """A stale exported VIRTUAL_ENV (deleted venv) must not win over a
        real ./.venv sitting right there."""
        venv = make_fake_venv(tmp_path)
        result = resolve(tmp_path, env_prefix=f"export VIRTUAL_ENV='{tmp_path}/gone'")
        assert result["VENV"] == str(venv)

    def test_ignores_venv_directory_without_executable_python(self, tmp_path):
        """A bin/python3 that isn't executable isn't a usable venv."""
        bindir = tmp_path / ".venv" / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python3").write_text("not executable", encoding="utf-8")
        (bindir / "python3").chmod(0o644)
        assert resolve(tmp_path)["PYTHON"] == "python3"


class TestPipGoesThroughTheResolvedInterpreter:
    """The regression that motivated all of this.

    A `uv venv` (and `python -m venv --without-pip`) has NO pip inside it.
    So prepending .venv/bin to PATH sends `python3` into the venv while a
    bare `pip3` still resolves to the SYSTEM pip - dependencies install
    into the system interpreter, the venv can't import them, and the
    callers' `if ! python3 -c "import flask"` guard reinstalls on every
    launch forever. Pip must always be addressed as
    `$CURATARR_PYTHON -m pip`.
    """

    def test_shared_pip_helper_never_invokes_a_bare_pip3(self):
        bare = re.compile(r"(?<![\w$/-])pip3\s+install")
        offenders = [
            f"{PIP_INSTALL_SH.name}:{n}"
            for n, line in enumerate(PIP_INSTALL_SH.read_text(encoding="utf-8").splitlines(), 1)
            if bare.search(line) and not line.lstrip().startswith("#")
        ]
        assert not offenders, (
            f"bare `pip3 install` in {offenders} - use curatarr_pip (i.e. "
            f"$CURATARR_PYTHON -m pip) so deps land in the interpreter that imports them"
        )

    def test_shared_pip_helper_calls_curatarr_pip(self):
        body = PIP_INSTALL_SH.read_text(encoding="utf-8")
        assert body.count("curatarr_pip install") == 2, (
            "expected both the hash-verified and the fallback install to go through curatarr_pip"
        )

    def test_run_scripts_check_pip_via_the_interpreter_not_via_path(self):
        """`command -v pip3` would find the system pip even when the venv
        has none - the check has to ask the resolved interpreter.

        Scans code lines only: the comment explaining precisely why
        `command -v pip3` is wrong sits directly above the fixed check, and
        matching the whole file would flag that prose as the defect.
        """
        run_sh = (REPO_ROOT / "run.sh").read_text(encoding="utf-8")
        offenders = [
            f"run.sh:{n}"
            for n, line in enumerate(run_sh.splitlines(), 1)
            if "command -v pip3" in line and not line.lstrip().startswith("#")
        ]
        assert not offenders, f"PATH-based pip check at {offenders} - ask $CURATARR_PYTHON instead"
        assert '"$CURATARR_PYTHON" -m pip --version' in run_sh

    def test_curatarr_pip_bootstraps_pip_when_the_venv_has_none(self, tmp_path):
        """Given an interpreter whose `-m pip` fails, curatarr_pip must try
        ensurepip before giving up (uv venvs ship ensurepip but not pip)."""
        fake = tmp_path / "python3"
        marker = tmp_path / "ensurepip_called"
        fake.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then\n'
            f'  if [ -f "{marker}" ]; then echo "pip 25.0"; exit 0; fi\n'
            "  exit 1\n"
            "fi\n"
            'if [ "$1" = "-m" ] && [ "$2" = "ensurepip" ]; then\n'
            f'  touch "{marker}"; exit 0\n'
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        script = f"""
        set -u
        source '{PYTHON_ENV_SH}'
        CURATARR_PYTHON='{fake}'
        curatarr_pip --version
        """
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, f"curatarr_pip did not recover via ensurepip: {proc.stderr}"
        assert marker.exists(), "ensurepip was never attempted"


class TestRunScriptsSourceTheResolver:
    """Both entry points must resolve before they install or exec, and
    pip-install.sh depends on the resolver having run."""

    @pytest.mark.parametrize("script_name", ["run.sh", "run-ui.sh"])
    def test_sources_python_env_before_pip_install(self, script_name):
        body = (REPO_ROOT / script_name).read_text(encoding="utf-8")
        env_at = body.find("scripts/lib/python-env.sh")
        pip_at = body.find("scripts/lib/pip-install.sh")
        assert env_at != -1, f"{script_name} does not source python-env.sh"
        assert pip_at != -1, f"{script_name} does not source pip-install.sh"
        assert env_at < pip_at, f"{script_name} must source python-env.sh before pip-install.sh"
        assert "curatarr_resolve_python" in body, f"{script_name} sources the lib but never calls the resolver"

    def test_run_ui_execs_the_resolved_interpreter(self):
        """The whole point: the server must run under the venv, not PATH's python3."""
        body = (REPO_ROOT / "run-ui.sh").read_text(encoding="utf-8")
        assert 'exec "$CURATARR_PYTHON" -m web.app' in body
