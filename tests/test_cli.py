"""Smoke tests for the CLI entry point."""

import pytest

from maintenance_copilot import __version__
from maintenance_copilot.cli import main


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "Maintenance Copilot CLI" in capsys.readouterr().out


def test_version_flag(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out
