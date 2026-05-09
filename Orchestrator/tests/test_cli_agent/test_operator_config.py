from pathlib import Path
import pytest
from Orchestrator.cli_agent.operator_config import OperatorConfig, InvalidOperator


@pytest.fixture
def cfg_root(tmp_path):
    return tmp_path / ".claude-bbox"


def test_creates_config_dir_for_new_operator(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    path = oc.ensure_dir("Brandon")
    assert path.exists() and path.is_dir()
    assert path == cfg_root / "Brandon"


def test_idempotent(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    oc.ensure_dir("Brandon")
    oc.ensure_dir("Brandon")  # second call must not raise
    assert (cfg_root / "Brandon").is_dir()


def test_isolates_two_operators(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    p1 = oc.ensure_dir("Brandon")
    p2 = oc.ensure_dir("Anna")
    assert p1 != p2
    assert p1.exists() and p2.exists()


def test_rejects_path_traversal_in_operator_name(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    with pytest.raises(InvalidOperator):
        oc.ensure_dir("../escape")


def test_rejects_empty_operator(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    with pytest.raises(InvalidOperator):
        oc.ensure_dir("")


def test_env_dict_default_does_not_set_claude_config_dir(cfg_root, monkeypatch):
    """Default (shared-config mode): no CLAUDE_CONFIG_DIR override.

    Claude Code falls back to ~/.claude so the operator gets all their
    real slash commands, sessions, agents, and MCP registrations.
    """
    monkeypatch.delenv("CLI_AGENT_ISOLATE_CONFIG", raising=False)
    oc = OperatorConfig(root=cfg_root)
    env = oc.env_for("Brandon")
    assert "CLAUDE_CONFIG_DIR" not in env


def test_env_dict_isolate_mode_sets_claude_config_dir(cfg_root, monkeypatch):
    """Opt-in isolated mode: each operator gets its own CLAUDE_CONFIG_DIR."""
    monkeypatch.setenv("CLI_AGENT_ISOLATE_CONFIG", "true")
    oc = OperatorConfig(root=cfg_root)
    env = oc.env_for("Brandon")
    assert env["CLAUDE_CONFIG_DIR"] == str(cfg_root / "Brandon")


def test_rejects_null_byte_in_operator_name(cfg_root):
    oc = OperatorConfig(root=cfg_root)
    with pytest.raises(InvalidOperator):
        oc.ensure_dir("Bra\x00ndon")


def test_rejects_shell_metachars(cfg_root):
    # Operator names with $, /, ;, &, |, spaces must be rejected since
    # they could enable shell injection if substituted into a command later.
    oc = OperatorConfig(root=cfg_root)
    for bad in ["Brandon Smith", "Brandon;rm", "$Brandon", "Bran/don", "Bran|don"]:
        with pytest.raises(InvalidOperator):
            oc.ensure_dir(bad)
