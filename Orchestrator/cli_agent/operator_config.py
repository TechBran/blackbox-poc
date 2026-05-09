import os
import re
from pathlib import Path


_OPERATOR_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidOperator(ValueError):
    """Raised when an operator name is empty or contains invalid characters."""


class OperatorConfig:
    """Per-operator config dir handling.

    By default, we DO NOT override CLAUDE_CONFIG_DIR — the Claude Code CLI
    falls back to its standard `~/.claude` location, so the operator
    inherits all of their existing slash commands, custom agents, session
    history, MCP server registrations, etc. when launched on the phone.

    To re-enable per-operator config isolation (each operator gets a
    pristine `~/.claude-bbox/<op>/` of their own), set the env var
    `CLI_AGENT_ISOLATE_CONFIG=true`. The validation/ensure_dir behavior is
    unaffected by this flag — only whether we set CLAUDE_CONFIG_DIR.
    """
    def __init__(self, root: Path):
        self.root = Path(root)
        self.isolate = (
            os.environ.get("CLI_AGENT_ISOLATE_CONFIG", "").lower() == "true"
        )

    def _check(self, operator: str) -> None:
        if not isinstance(operator, str) or not operator or not _OPERATOR_RE.match(operator):
            raise InvalidOperator(f"Invalid operator name: {operator!r}")

    def ensure_dir(self, operator: str) -> Path:
        self._check(operator)
        path = self.root / operator
        path.mkdir(parents=True, exist_ok=True)
        return path

    def env_for(self, operator: str) -> dict:
        # Always validate the operator name (security check).
        self._check(operator)
        if not self.isolate:
            # Shared mode: claude uses ~/.claude (the operator's real
            # config — full slash commands, sessions, agents, MCP).
            return {}
        path = self.ensure_dir(operator)
        return {"CLAUDE_CONFIG_DIR": str(path)}
