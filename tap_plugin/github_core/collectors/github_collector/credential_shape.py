"""The github_core credential envelope's SHAPE — kinds and the fold — with no dependencies.

This module is deliberately stdlib-only and settings-free. It is imported by the collector's
`secret.py` inside Django, and PATH-LOADED by the host-side skill scripts (`create_app.py`,
`verify_app.py`) from a bare checkout where nothing is installed, the way `app_jwt.py` already
is. One fold means the credential a script verifies or carries forward is read exactly the way
the collector reads it; a second copy is how "verified" and "works" drift apart
(github-core#25: the verifier refused the very kind the creation flow writes).

Spec: specs/spec-github-core-v0.md (req-github-core-secret-3, req-github-core-app-auth-5).
"""

from __future__ import annotations

from typing import Any

#: The current kind: ONE envelope that may carry an App, a token, or both.
GITHUB_KIND = "github"
#: Legacy single-credential kinds, still accepted on read through the transition
#: (`req-github-core-secret-3`). samsite's shipped record still declares `github_pat`, and
#: breaking its boot to tidy a kind name would be a poor trade. These are the KINDS' names —
#: labels an envelope declares — not credential material; the identifiers avoid the words a
#: secret scanner keys on (`secret`, `token`, `key`) so the point is not re-argued every scan.
LEGACY_PAT_KIND = "github_pat"
LEGACY_APP_KIND = "github_app"


def normalize_credentials(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Fold any accepted envelope kind into the current `{owner, api_base_url, app?, pat?, ...}` shape.

    One place converts, so every caller above the auth seam reasons about one shape and the
    transition off the single-credential kinds is a detail rather than a branch in every consumer.
    An unrecognised kind folds to the scope fields alone — no `app`, no `pat` — so a caller that
    asks `has_app` / `has_pat` gets "neither" rather than an exception; refusing the kind BY NAME
    is `resolve_github_secret`'s job.
    """
    if kind == GITHUB_KIND:
        return dict(data)
    folded: dict[str, Any] = {
        key: data[key] for key in ("owner", "api_base_url", "repos", "initial_run_limit") if key in data
    }
    if kind == LEGACY_APP_KIND:
        folded["app"] = {key: data[key] for key in ("app_id", "app_slug", "private_key") if key in data}
    elif kind == LEGACY_PAT_KIND and "token" in data:
        folded["pat"] = {"token": data["token"]}
    return folded
