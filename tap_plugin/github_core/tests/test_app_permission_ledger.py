"""The App-permission ledger: every permission GitHub offers is classified, and the manifest agrees.

**The gap this closes.** The App's permissions are DERIVED from the collection manifest
(`req-github-core-app-auth`), which keeps the requested set honest — but says nothing about
the permissions we chose NOT to request, and nothing about the ones GitHub adds after we
looked. A permission granted on the installed App with no source behind it, or a new
permission nobody has considered, are both absences that read as decisions. The ledger
(`github_app_permissions.json`) records a decision per key in one of six states, and this
file holds it against the catalogue GitHub publishes (`github_openapi_extract.json` →
`app_permissions`, refreshed deliberately by `scripts/refresh_openapi_extract.py`) and
against the manifest. Offline and hermetic, like the conformance tests.

Three states, never two: a key must be requested / classified-not-requested / — and an
UNCLASSIFIED key fails, rather than passing by not being looked at.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_COLLECTOR = Path(__file__).resolve().parent.parent / "collectors" / "github_collector"
_MANIFEST = json.loads((_COLLECTOR / "github_collection_manifest.json").read_text())
_EXTRACT = json.loads((_COLLECTOR / "github_openapi_extract.json").read_text())
_LEDGER = json.loads((_COLLECTOR / "github_app_permissions.json").read_text())

_STATES = {"requested", "exploratory", "recommended", "deferred", "declined", "not_applicable"}
_LEVELLED = {"requested", "exploratory", "recommended", "deferred"}


def _skill_manifest_module():
    """The create-github-app skill's manifest module — the one derivation of manifest keys.

    Loaded from its path because the skill directory name is not an importable identifier;
    reusing it (rather than re-deriving `organization_` prefixing here) keeps one derivation.
    """
    path = Path(__file__).resolve().parent.parent / "skills" / "create-github-app" / "manifest.py"
    spec = importlib.util.spec_from_file_location("create_github_app_manifest", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalogue() -> dict[str, dict]:
    return _EXTRACT["app_permissions"]


def _entries() -> dict[str, dict]:
    return _LEDGER["permissions"]


@pytest.mark.spec("req-github-core-app-permissions-ledger-1")
def test_every_catalogue_key_is_classified() -> None:
    """A key GitHub added since the last review is unclassified — fail closed, do not skip it."""
    missing = sorted(set(_catalogue()) - set(_entries()))
    assert not missing, (
        f"UNCLASSIFIED App permission(s) in GitHub's catalogue: {missing}. Add each to "
        "github_app_permissions.json with a state and a reason (spec-github-core-app-permissions.md)."
    )


@pytest.mark.spec("req-github-core-app-permissions-ledger-2")
def test_every_ledger_key_exists_in_the_catalogue() -> None:
    """A ledger entry GitHub no longer publishes is a decision about nothing — surface it."""
    stale = sorted(set(_entries()) - set(_catalogue()))
    assert not stale, f"Ledger names permission(s) GitHub's catalogue no longer has: {stale}."


@pytest.mark.spec("req-github-core-app-permissions-ledger-3")
def test_entries_are_well_formed() -> None:
    for key, entry in _entries().items():
        assert entry.get("state") in _STATES, f"{key}: state {entry.get('state')!r} not in {sorted(_STATES)}"
        assert entry.get("why"), f"{key}: a classification without a reason is a presence, not a decision"
        levels = _catalogue()[key]["levels"]
        if entry["state"] in _LEVELLED:
            assert entry.get("level") == "read", f"{key}: only read may be requested/recommended (got {entry.get('level')!r})"
            assert "read" in levels, f"{key}: GitHub offers no read level ({levels}); it cannot be a read-only ask"
        else:
            assert entry.get("level") is None, f"{key}: {entry['state']} entries carry no level"
        if entry["state"] in {"exploratory", "deferred"}:
            assert entry.get("until"), f"{key}: {entry['state']} must name what resolves it (`until`)"


@pytest.mark.spec("req-github-core-app-permissions-ledger-4")
def test_manifest_and_ledger_agree_on_what_is_requested() -> None:
    """Requested ⇔ derived from a manifest source. Neither side may claim what the other does not."""
    mod = _skill_manifest_module()
    repo, org = mod.derive_permissions(_COLLECTOR / "github_collection_manifest.json")
    derived = {mod._manifest_key("repository", k): lvl for k, lvl in repo.items()}
    derived.update({mod._manifest_key("organization", k): lvl for k, lvl in org.items()})
    requested = {k for k, v in _entries().items() if v["state"] == "requested"}
    assert set(derived) == requested, (
        f"manifest derives {sorted(derived)} but the ledger marks requested {sorted(requested)} — "
        "a source landed without its ledger entry, or an entry claims a source that does not exist."
    )
    assert all(lvl == "read" for lvl in derived.values()), f"manifest requests write: {derived}"


@pytest.mark.spec("req-github-core-app-permissions-ledger-5")
def test_requested_entries_cite_exactly_the_sources_that_need_them() -> None:
    """A requested entry's `sources` must be precisely the manifest sources whose permission
    triple maps to that key — not merely names that exist somewhere in the manifest. Otherwise
    the audit trail from permission to consumer is a list that reads as verified and is not."""
    mod = _skill_manifest_module()
    needing: dict[str, set[str]] = {}
    for source in _MANIFEST["sources"]:
        triple = source.get("permission")
        if not triple:
            continue
        surface, key, _level = triple.split(":")
        needing.setdefault(mod._manifest_key(surface, key), set()).add(source["name"])
    for key, entry in _entries().items():
        if entry["state"] != "requested":
            continue
        cited = set(entry.get("sources") or [])
        assert cited == needing.get(key, set()), (
            f"{key}: ledger cites {sorted(cited)} but the manifest sources needing it are "
            f"{sorted(needing.get(key, set()))}"
        )
