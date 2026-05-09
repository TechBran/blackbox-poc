import os
import time
from pathlib import Path
from typing import List


def reap_idle_sessions(manager, idle_seconds: int = 7 * 86400) -> List[str]:
    """Kill CLI Agent tmux sessions whose last activity is older than
    `idle_seconds`. Returns the list of killed session names.

    Talks to tmux via `manager._tmux(...)` so the same socket-aware
    invocation pattern used by the manager is reused. Bail out cleanly
    on any tmux failure (e.g., server not running) by returning [].
    """
    res = manager._tmux("list-sessions",
                         "-F", "#{session_name} #{session_activity}")
    if res.returncode != 0:
        return []
    cutoff = int(time.time()) - idle_seconds
    killed: List[str] = []
    for line in res.stdout.splitlines():
        try:
            name, activity = line.rsplit(" ", 1)
            activity_ts = int(activity)
        except (ValueError, IndexError):
            continue
        if not name.startswith("cli-agent-"):
            continue
        if activity_ts < cutoff:
            manager.kill(name)
            killed.append(name)
    return killed


def _main() -> int:
    """CLI entry point for scheduling.

    Schedule via crontab (simplest):
        0 4 * * * /home/.../Orchestrator/venv/bin/python \\
                  -m Orchestrator.cli_agent.reaper

    Or via systemd user timer (cleaner; survives blackbox.service restarts).
    Threshold is configurable via CLI_AGENT_IDLE_DAYS env var (default 7).

    Note: the cron system at /api/cron/jobs is LLM-prompt-driven and
    overkill for this deterministic system task — schedule directly via
    crontab/systemd timer instead.
    """
    from Orchestrator.cli_agent.path_validator import PathValidator
    from Orchestrator.cli_agent.operator_config import OperatorConfig
    from Orchestrator.cli_agent.session_manager import TmuxSessionManager

    apps_root = Path(os.getenv("CLI_AGENT_APPS_ROOT")
                      or Path(__file__).resolve().parents[2] / "Apps")
    cfg_root = Path(os.getenv("CLI_AGENT_CONFIG_ROOT")
                     or Path.home() / ".claude-bbox")
    idle_days = int(os.getenv("CLI_AGENT_IDLE_DAYS", "7"))

    pv = PathValidator(apps_root=apps_root)
    oc = OperatorConfig(root=cfg_root)
    mgr = TmuxSessionManager(path_validator=pv, operator_config=oc)
    killed = reap_idle_sessions(mgr, idle_seconds=idle_days * 86400)
    print(f"[cli-agent-reaper] killed {len(killed)} session(s) "
          f"(threshold: {idle_days}d)")
    for name in killed:
        print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
