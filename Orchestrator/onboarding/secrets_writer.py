"""Atomic .env file writer with backup."""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from Orchestrator.utils.paths import resolve

ENV_FILE = resolve(".env")

_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def update_env(updates: dict[str, str]) -> dict:
    """Atomically update key=value pairs in .env, preserving structure.

    - Existing keys: replaced in-place (preserves comment ordering)
    - New keys: appended in a labeled section
    - Backup created at .env.backup.<timestamp> before writing
    - Atomic rename via os.replace
    - All written files are mode 0600 (secrets must not leak via umask)
    - Validates env-var names and rejects newline/null in values
    """
    for k, v in updates.items():
        if not _KEY_RE.match(k):
            raise ValueError(f"invalid env-var name: {k!r} (must match {_KEY_RE.pattern})")
        if any(ch in v for ch in ("\n", "\r", "\x00")):
            raise ValueError(f"value for {k!r} contains newline or null — would corrupt .env")

    if not updates:
        return {"backup": None, "updated_keys": []}

    if not ENV_FILE.exists():
        ENV_FILE.touch()
        os.chmod(ENV_FILE, 0o600)

    ts = int(time.time())
    backup = ENV_FILE.with_suffix(f".backup.{ts}")
    shutil.copy2(ENV_FILE, backup)
    os.chmod(backup, 0o600)

    # Keep only the 5 most recent backups; older ones are noise.
    backups = sorted(
        ENV_FILE.parent.glob(".env.backup.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[5:]:
        try:
            old.unlink()
        except OSError:
            pass  # best-effort cleanup, never fatal

    lines = ENV_FILE.read_text().splitlines(keepends=True)
    seen_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}\n")
                seen_keys.add(k)
                continue
        new_lines.append(line)

    new_keys = [k for k in updates if k not in seen_keys]
    if new_keys:
        new_lines.append("\n# Added by onboarding wizard\n")
        for k in new_keys:
            new_lines.append(f"{k}={updates[k]}\n")

    tmp = ENV_FILE.with_suffix(".tmp")
    tmp.write_text("".join(new_lines))
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV_FILE)

    return {"backup": str(backup), "updated_keys": list(updates.keys())}


def remove_env_keys(keys: list[str]) -> dict:
    """Atomically delete specified env-var keys from .env. Backup created first.

    - Validates each key matches _KEY_RE (defense-in-depth — caller may not gate).
    - Creates a timestamped backup before mutation; prunes to 5 most recent.
    - Drops matching lines entirely (does NOT just blank the value).
    - File mode 0600 preserved on backup, tmp, and final .env.
    - Returns {backup, removed_keys} — removed_keys reflects what was ACTUALLY in
      the file (a key in `keys` that wasn't in .env doesn't appear in removed_keys).
    """
    if not keys:
        return {"backup": None, "removed_keys": []}
    for k in keys:
        if not _KEY_RE.match(k):
            raise ValueError(f"invalid env-var name: {k!r} (must match {_KEY_RE.pattern})")
    if not ENV_FILE.exists():
        return {"backup": None, "removed_keys": []}

    ts = int(time.time())
    backup = ENV_FILE.with_suffix(f".backup.{ts}")
    shutil.copy2(ENV_FILE, backup)
    os.chmod(backup, 0o600)

    # Prune to 5 most recent backups (matches update_env behavior)
    backups = sorted(
        ENV_FILE.parent.glob(".env.backup.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[5:]:
        try:
            old.unlink()
        except OSError:
            pass

    keys_set = set(keys)
    lines = ENV_FILE.read_text().splitlines(keepends=True)
    out: list[str] = []
    removed: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in keys_set:
                removed.append(k)
                continue  # drop the line entirely
        out.append(line)

    tmp = ENV_FILE.with_suffix(".tmp")
    tmp.write_text("".join(out))
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV_FILE)

    return {"backup": str(backup), "removed_keys": removed}
