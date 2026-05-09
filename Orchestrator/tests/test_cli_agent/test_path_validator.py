import os
import tempfile
from pathlib import Path
import pytest
from Orchestrator.cli_agent.path_validator import PathValidator, WorkspaceViolation


@pytest.fixture
def workspace(tmp_path):
    apps = tmp_path / "Apps"
    (apps / "grocery-store").mkdir(parents=True)
    (apps / "PelvicVibeAndroid").mkdir()
    return PathValidator(apps_root=apps)


def test_validates_real_app_subdir(workspace):
    assert workspace.validate("grocery-store").name == "grocery-store"


def test_apps_root_pickable_via_empty_string(workspace):
    assert workspace.validate("") == workspace.apps_root


def test_apps_root_pickable_via_dot(workspace):
    assert workspace.validate(".") == workspace.apps_root


def test_apps_root_pickable_via_slash(workspace):
    assert workspace.validate("/") == workspace.apps_root


def test_rejects_traversal(workspace):
    with pytest.raises(WorkspaceViolation):
        workspace.validate("../../etc")


def test_rejects_absolute_path_outside(workspace):
    with pytest.raises(WorkspaceViolation):
        workspace.validate("/etc/passwd")


def test_rejects_nonexistent_dir(workspace):
    with pytest.raises(WorkspaceViolation):
        workspace.validate("does-not-exist")


def test_rejects_symlink_escaping_apps(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.apps_root / "evil-link").symlink_to(outside)
    with pytest.raises(WorkspaceViolation):
        workspace.validate("evil-link")


def test_rejects_null_byte_in_path(workspace):
    with pytest.raises(WorkspaceViolation):
        workspace.validate("foo\x00bar")


def test_accepts_self_canceling_traversal(workspace):
    # "grocery-store/../grocery-store" resolves back into the workspace and
    # MUST be accepted — the relative_to check sees grocery-store as inside.
    assert workspace.validate("grocery-store/../grocery-store").name == "grocery-store"


def test_rejects_regular_file(workspace):
    # Files (not directories) inside Apps/ are not valid pickable workspaces.
    f = workspace.apps_root / "stray.txt"
    f.write_text("not a directory")
    with pytest.raises(WorkspaceViolation):
        workspace.validate("stray.txt")


def test_accepts_symlink_into_workspace(workspace, tmp_path):
    # A symlink that points to ANOTHER folder INSIDE the workspace must be allowed.
    (workspace.apps_root / "alias").symlink_to(workspace.apps_root / "grocery-store")
    result = workspace.validate("alias")
    # resolve() follows the link, so result is the real grocery-store path
    assert result.is_dir()
    assert result.name == "grocery-store"
