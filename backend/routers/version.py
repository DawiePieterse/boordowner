"""What release this server is running. Public, like Boord's: Boord's
heartbeat.ps1 asks every app, Boord Monitor reads it for its tiles, and so
does whoever is looking at a phone wondering why a screen looks wrong.

Copied from Boord Labour's routers/version.py. Tag, state and the frontend
VERSION line only - Boord's version.py also reports schema revision, backups
and pending updates; port those if this app grows an update check.
"""
import os
import re
import shutil
import subprocess

from fastapi import APIRouter

import config

router = APIRouter(prefix="/api/version", tags=["version"])

API_JS = os.path.join(config.FRONTEND_DIR, "shared", "api.js")
# The VERSION line in frontend/shared/api.js, anchored to its own indentation
# so a comment or another object's VERSION can never be read instead.
_VERSION_RE = re.compile(r'^  VERSION: "(.*)",$', re.MULTILINE)


def _frontend_version() -> str:
    try:
        with open(API_JS, encoding="utf-8") as fh:
            m = _VERSION_RE.search(fh.read())
        return m.group(1) if m else ""
    except OSError:
        return ""


def _git(args: list) -> str:
    """git's answer, or "" when there is no git, no repository or no tag.
    The farm server runs from a git checkout (update_owner_server.bat checks
    out the newest signed tag), but a copy unpacked from a zip has neither,
    and this endpoint must still answer."""
    git = shutil.which("git")
    if not git:
        return ""
    try:
        proc = subprocess.run([git] + args, cwd=config.REPO_ROOT,
                              capture_output=True, text=True, timeout=5,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


@router.get("")
def get_version():
    """Same field names as Boord's /api/version, because Boord's
    heartbeat.ps1 reads tag/state/frontend_version from every app.

    state is "release" when the checkout sits exactly on a tag, "ahead" when
    it is past one (tag is then git describe's "v1.9-3-gabc1234"), and
    "unknown" when git cannot say. `version` is the frontend VERSION, the
    number the header shows, so the two can be compared at a glance."""
    tag = _git(["describe", "--tags", "--exact-match"])
    if tag:
        state = "release"
    else:
        tag = _git(["describe", "--tags", "--always"])
        state = "ahead" if tag else "unknown"
    frontend = _frontend_version()
    return {"app": "Boord Owner", "version": frontend or None, "tag": tag or None,
            "state": state, "frontend_version": frontend}
