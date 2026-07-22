"""
Tests for curatarr_app.py - the PyInstaller binary entry point.

Deliberately thin (see the module docstring): the only behavior worth
asserting is "running this module calls web.app.main()", since all
real logic already lives in - and is already tested via - web/app.py.
"""

import runpy
from unittest.mock import patch

import curatarr_app


class TestCuratarrApp:
    def test_imports_main_from_web_app(self):
        """curatarr_app.main is the same function web.app.main() is."""
        from web.app import main as web_app_main
        assert curatarr_app.main is web_app_main

    @patch('web.app.main')
    def test_running_as_script_calls_main(self, mock_main):
        """PyInstaller runs this file as __main__ - confirm that path
        calls main() exactly once, matching run-ui.sh / run-ui.ps1."""
        runpy.run_module('curatarr_app', run_name='__main__')
        mock_main.assert_called_once_with()
