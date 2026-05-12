"""Credentials file management for the onboarding wizard.

Manages JSON credential files (Google Cloud service accounts, future TLS
certs, etc.) that live in the credentials/ folder. Pattern:
- GET /onboarding/credentials → list all files with metadata (no contents)
- POST /onboarding/credentials/upload (multipart) → write to credentials/
- DELETE /onboarding/credentials/{filename} → remove + clear env if linked

Loopback-only by design — same trust model as /onboarding/config/{key}?reveal=1.

NEVER returns or logs file contents — only filename, size, and an
is_google_service_account boolean derived from inspecting the type field.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from Orchestrator.onboarding.secrets_writer import remove_env_keys, update_env
from Orchestrator.utils.paths import resolve

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding/credentials", tags=["onboarding-credentials"])

CREDS_DIR = resolve("credentials")
# Filename hard-bound to alphanumeric + dot/dash/underscore + .json suffix.
# Defends against path traversal (../) and weird characters that could break
# the atomic-write rename.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.json$")


class CredentialFileMeta(BaseModel):
    filename: str
    size_bytes: int
    modified_at: float
    is_google_service_account: bool


class CredentialsListResponse(BaseModel):
    files: list[CredentialFileMeta]
    # Active GOOGLE_APPLICATION_CREDENTIALS env var path, if any. Frontend
    # uses this to mark which file in `files` is the live one.
    google_application_credentials: str | None


def _is_google_service_account(path: Path) -> bool:
    """Inspect file contents to confirm it's a Google service-account JSON.

    Doesn't return the content — just a boolean for UI hint. Service-account
    JSONs always carry type='service_account' + a client_email field.
    """
    try:
        with path.open() as f:
            data = json.load(f)
        return data.get("type") == "service_account" and "client_email" in data
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False


def _file_meta(path: Path) -> CredentialFileMeta:
    stat = path.stat()
    return CredentialFileMeta(
        filename=path.name,
        size_bytes=stat.st_size,
        modified_at=stat.st_mtime,
        is_google_service_account=_is_google_service_account(path),
    )


@router.get("", response_model=CredentialsListResponse)
def list_credentials() -> CredentialsListResponse:
    """List all .json credential files in credentials/ + the currently-active
    GOOGLE_APPLICATION_CREDENTIALS path.
    """
    if not CREDS_DIR.exists():
        CREDS_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(CREDS_DIR, 0o700)
    files: list[CredentialFileMeta] = []
    for p in sorted(CREDS_DIR.glob("*.json")):
        if p.is_file():
            files.append(_file_meta(p))
    return CredentialsListResponse(
        files=files,
        google_application_credentials=os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or None,
    )


@router.post("/upload")
async def upload_credential(
    file: UploadFile = File(...),
    set_as_google_app_creds: bool = True,
) -> dict:
    """Upload a .json credential file. Validates JSON shape + filename.
    Optionally updates GOOGLE_APPLICATION_CREDENTIALS env var to point at it.

    File is written atomically: .tmp + chmod 0600 + os.replace. credentials/
    dir is auto-created with chmod 0700 if absent.
    """
    filename = (file.filename or "").strip()
    if not _FILENAME_RE.match(filename):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid filename {filename!r}. Must match {_FILENAME_RE.pattern}",
        )
    contents = await file.read()
    if len(contents) > 64 * 1024:  # 64KB ceiling — service-account JSONs are ~2-3KB
        raise HTTPException(status_code=413, detail="File too large (64KB max)")
    try:
        json.loads(contents.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDS_DIR, 0o700)
    target = CREDS_DIR / filename
    # Atomic write via .tmp + os.replace. chmod 0600 on the tmp file BEFORE
    # the rename so the final file lands with the right mode regardless of
    # umask.
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(contents)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)

    is_sa = _is_google_service_account(target)
    result: dict = {
        "filename": filename,
        "size_bytes": len(contents),
        "is_service_account": is_sa,
    }

    if set_as_google_app_creds:
        update_env({"GOOGLE_APPLICATION_CREDENTIALS": str(target)})
        # Mirror the .env mutation into the running process env so callers
        # that rely on os.getenv (e.g. Google client libraries) pick it up
        # without a restart.
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(target)
        result["env_updated"] = "GOOGLE_APPLICATION_CREDENTIALS"

    # CRITICAL: never log contents — only filename, size, and the SA boolean.
    logger.info(
        "credentials uploaded: %s (%d bytes, sa=%s)",
        filename, len(contents), is_sa,
    )
    return {"ok": True, **result}


@router.delete("/{filename}")
def delete_credential(filename: str) -> dict:
    """Remove a credential file. If GOOGLE_APPLICATION_CREDENTIALS pointed
    at it, also clear the env var.
    """
    if not _FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail=f"Invalid filename {filename!r}")
    target = CREDS_DIR / filename
    if not target.exists():
        return {"ok": True, "removed": False, "reason": "not present"}
    target.unlink()
    # Clear env var if it pointed here
    current_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if current_creds == str(target):
        remove_env_keys(["GOOGLE_APPLICATION_CREDENTIALS"])
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        env_cleared = True
    else:
        env_cleared = False
    logger.info("credentials deleted: %s (env_cleared=%s)", filename, env_cleared)
    return {"ok": True, "removed": True, "env_cleared": env_cleared}
