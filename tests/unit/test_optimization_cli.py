"""Test CLI commands exist and enforce --yes for destructive operations."""

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


def test_iteration_start_command_exists():
    """The iteration start sub-command must be registered."""
    from sales_agent.cli_optimization import app
    commands = [c.name for c in app.registered_commands]
    assert "start" in commands


def test_approve_command_exists():
    from sales_agent.cli_optimization import app
    commands = [c.name for c in app.registered_commands]
    assert "approve" in commands


def test_rollback_requires_confirmation():
    """Rollback must abort without --yes."""
    from sales_agent.cli_optimization import app

    runner = CliRunner()
    # Without --yes and with the prompt defaulting to abort
    result = runner.invoke(app, [
        "rollback",
        "--agent", "a1",
        "--release", "r1",
    ])
    # Should fail (abort) because no --yes
    assert result.exit_code != 0 or "abort" in str(result.exception or "").lower()


def test_publish_requires_confirmation():
    """Publish must abort without --yes."""
    from sales_agent.cli_optimization import app

    runner = CliRunner()
    result = runner.invoke(app, [
        "publish",
        "--agent", "a1",
        "--candidate", "c1",
    ])
    assert result.exit_code != 0 or "abort" in str(result.exception or "").lower()


def test_all_cli_commands_registered():
    """All expected CLI commands must be registered."""
    from sales_agent.cli_optimization import app
    commands = {c.name for c in app.registered_commands}
    expected = {
        "start", "list", "watch", "approve", "reject",
        "publish", "rollback", "checkpoint-list", "checkpoint-fork",
    }
    missing = expected - commands
    assert not missing, f"Missing CLI commands: {missing}"
