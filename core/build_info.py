"""Build provenance for the health endpoint.

Why this exists: from outside the service there was no way to confirm which
commit is actually running (deploy verification, incident debugging, and the
improvement loop's own ship-check all had to guess from timestamps). The
/health response now carries a small ``build`` object.

Sources (first hit wins):
  - Render injects RENDER_GIT_COMMIT / RENDER_GIT_BRANCH at runtime for
    repo-backed services -- no Dockerfile change needed.
  - GIT_SHA / GIT_BRANCH overrides, for other hosts or manual deploys.
  - "unknown" when unset (local dev).

The commit SHA of a public repo is public information, not a secret.
"""
from __future__ import annotations

import os

UNKNOWN = "unknown"


def build_info() -> dict:
    """Return the running build's provenance: {"commit": ..., "branch": ...}."""
    commit = (
        os.environ.get("RENDER_GIT_COMMIT")
        or os.environ.get("GIT_SHA")
        or UNKNOWN
    )
    branch = (
        os.environ.get("RENDER_GIT_BRANCH")
        or os.environ.get("GIT_BRANCH")
        or UNKNOWN
    )
    return {"commit": commit, "branch": branch}
