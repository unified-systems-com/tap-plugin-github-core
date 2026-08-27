"""Derive the git-serious GitHub App manifest from the collection manifest.

Pure and stdlib-only so it can be imported by the host-side creation flow AND, later,
by an in-instance page that renders the same declaration read-only.

The permission set is DERIVED, never hand-listed: every source in
`github_collection_manifest.json` declares the canonical permission triple it needs
(`<surface>:<key>:<level>`), and the App's permissions are the union over those sources. That is
the same declaration the collector obeys, so the published claim about what we ask for cannot
drift from what we actually use.

Anything requested BEYOND that union must be passed explicitly as `--exploratory` and is printed
separately, because silently over-requesting permission in a security product is the exact
behaviour the product exists to find in other people.

"""

from __future__ import annotations

import json
from pathlib import Path

_MANIFEST = Path(__file__).resolve().parents[2] / "collectors/github_collector/github_collection_manifest.json"


def derive_permissions(manifest_path: Path = _MANIFEST) -> tuple[dict[str, str], dict[str, str]]:
    """Return ({repository perms}, {organization perms}) as the union over declared sources."""
    manifest = json.loads(manifest_path.read_text())
    repo: dict[str, str] = {}
    org: dict[str, str] = {}
    rank = {"read": 1, "write": 2}
    for source in manifest.get("sources", []):
        triple = source.get("permission")
        if not triple:
            continue
        surface, key, level = triple.split(":")
        bucket = repo if surface == "repository" else org
        # Highest level wins if two sources disagree; a union must not silently downgrade.
        if rank[level] > rank.get(bucket.get(key, "read"), 0) or key not in bucket:
            bucket[key] = level
    return repo, org


# GitHub's manifest namespaces organization permissions with an `organization_` prefix, EXCEPT a
# handful it names bare. Without this, `organization:administration` and `repository:administration`
# collapse onto the same key and one of them is silently dropped — an accept-and-drop bug in a tool
# whose whole job is to make permission grants legible. Caught 2026-08-27 by reading the rendered
# manifest instead of trusting the summary.
_ORG_BARE = {"members"}


def _manifest_key(surface: str, key: str) -> str:
    if surface == "repository" or key in _ORG_BARE:
        return key
    return f"organization_{key}"


def build(*, org: str, redirect_url: str, name: str, public: bool, exploratory: list[str]) -> dict:
    repo_perms, org_perms = derive_permissions()
    for item in exploratory:
        surface, key, level = item.split(":")
        (repo_perms if surface == "repository" else org_perms)[key] = level
    permissions = {_manifest_key("repository", k): v for k, v in repo_perms.items()}
    for k, v in org_perms.items():
        manifest_key = _manifest_key("organization", k)
        assert manifest_key not in permissions, f"permission key collision on {manifest_key!r}"
        permissions[manifest_key] = v
    return {
        "name": name,
        "url": "https://github.com/unified-systems-com/git-serious-tap",
        "description": (
            "git-serious — visualize, track, and secure your CI/CD system. Read-only: this App "
            "observes configuration and execution history and never writes to your repositories."
        ),
        "public": public,
        "redirect_url": redirect_url,
        # No webhook: this App does not receive events yet. Adding one later is a permission and
        # an endpoint decision of its own, not a default.
        "default_events": [],
        "default_permissions": permissions,
        "request_oauth_on_install": False,
        "setup_on_update": False,
    }
