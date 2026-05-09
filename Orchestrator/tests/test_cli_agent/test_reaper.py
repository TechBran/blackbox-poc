import time
from unittest.mock import MagicMock
from Orchestrator.cli_agent.reaper import reap_idle_sessions


def test_reaps_sessions_idle_beyond_threshold():
    mgr = MagicMock()
    now = int(time.time())
    mgr._tmux.return_value.stdout = (
        f"cli-agent-Brandon__claude__grocery-store {now - 8 * 86400}\n"
        f"cli-agent-Brandon__claude__fresh {now - 3600}\n"
    )
    mgr._tmux.return_value.returncode = 0
    killed = reap_idle_sessions(mgr, idle_seconds=7 * 86400)
    assert killed == ["cli-agent-Brandon__claude__grocery-store"]
    mgr.kill.assert_called_once_with("cli-agent-Brandon__claude__grocery-store")


def test_skips_non_cli_agent_sessions():
    mgr = MagicMock()
    now = int(time.time())
    mgr._tmux.return_value.stdout = (
        f"some-other-session {now - 99 * 86400}\n"
    )
    mgr._tmux.return_value.returncode = 0
    killed = reap_idle_sessions(mgr, idle_seconds=7 * 86400)
    assert killed == []
    mgr.kill.assert_not_called()


def test_returns_empty_when_no_server():
    """If tmux server isn't running, list-sessions returns nonzero
    and we should bail out gracefully with an empty list — NOT raise."""
    mgr = MagicMock()
    mgr._tmux.return_value.returncode = 1
    mgr._tmux.return_value.stdout = ""
    killed = reap_idle_sessions(mgr, idle_seconds=7 * 86400)
    assert killed == []
    mgr.kill.assert_not_called()


def test_skips_malformed_lines():
    """Lines without a parseable activity timestamp should be skipped
    silently rather than crashing the reaper."""
    mgr = MagicMock()
    now = int(time.time())
    mgr._tmux.return_value.stdout = (
        "cli-agent-Brandon__claude__broken\n"  # no activity timestamp
        f"cli-agent-Brandon__claude__valid {now - 99 * 86400}\n"
        "garbage-line-no-spaces\n"
    )
    mgr._tmux.return_value.returncode = 0
    killed = reap_idle_sessions(mgr, idle_seconds=7 * 86400)
    assert killed == ["cli-agent-Brandon__claude__valid"]
