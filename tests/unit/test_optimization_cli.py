"""Test CLI optimization commands: watch, report, trends."""

import pytest
from typer.testing import CliRunner

from sales_agent.cli_optimization import app

runner = CliRunner()


class TestCliWatch:
    def test_watch_help_shows_cursor_flags(self):
        result = runner.invoke(app, ["watch", "--help"])
        assert result.exit_code == 0
        assert "--after-sequence" in result.stdout
        assert "--timeout" in result.stdout
        assert "--json" in result.stdout


class TestCliReport:
    def test_report_help_shows_format_options(self):
        result = runner.invoke(app, ["report", "--help"])
        assert result.exit_code == 0
        assert "--format" in result.stdout
        assert "--output" in result.stdout
        assert "--report-id" in result.stdout

    def test_report_rejects_invalid_format(self):
        result = runner.invoke(app, [
            "report",
            "--agent", "a1",
            "--iteration", "i1",
            "--report-id", "r1",
            "--format", "xml",
        ])
        assert result.exit_code != 0
        assert "Unsupported format" in (result.stdout or "") or "Error" in (result.stderr or "")


class TestCliTrends:
    def test_trends_help(self):
        result = runner.invoke(app, ["trends", "--help"])
        assert result.exit_code == 0
        assert "--limit" in result.stdout
        assert "--json" in result.stdout


class TestCliList:
    def test_list_requires_agent(self):
        result = runner.invoke(app, ["list", "--help"])
        assert result.exit_code == 0
        assert "--agent" in result.stdout
