from pathlib import Path


class WorkspaceViolation(PermissionError):
    """Raised when a requested path escapes the Apps/ workspace."""


class PathValidator:
    def __init__(self, apps_root: Path):
        self.apps_root = Path(apps_root).resolve(strict=True)

    def validate(self, requested: str) -> Path:
        if requested in ("", "/", "."):
            return self.apps_root
        try:
            candidate = (self.apps_root / requested).resolve()
        except (ValueError, OSError) as e:
            raise WorkspaceViolation(f"{requested!r} could not be resolved: {e}") from None
        try:
            candidate.relative_to(self.apps_root)
        except ValueError:
            raise WorkspaceViolation(f"{requested} is outside Apps/") from None
        if not candidate.is_dir():
            raise WorkspaceViolation(f"{requested} is not a directory in Apps/")
        return candidate
