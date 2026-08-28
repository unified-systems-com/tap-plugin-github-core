"""The collection manifest conforms to GitHub's published OpenAPI description.

**The gap this closes.** We do not use a GitHub client library that the vendor keeps
current — `api_client.py` is ours, and the manifest's endpoint paths and field names are
hand-authored. So a renamed field or a retired endpoint does not break a build; it
arrives as a collection that quietly returns less. That is the failure this plugin keeps
meeting from other directions: an absence that renders as a finished answer.

GitHub publishes a machine-readable description of its REST API. These tests hold the
manifest against a pinned extract of it (`github_openapi_extract.json`, refreshed
deliberately by `scripts/refresh_openapi_extract.py`), so drift on GitHub's side becomes
a reviewable diff instead of a silent narrowing.

**Offline and hermetic.** The extract is committed; nothing here touches the network. The
refresh is a maintainer's act whose output rides a PR — a ledger, not auto-generation.

**What this cannot check**, stated so the green is not over-read:
- GraphQL sources (the whole config layer) — a REST description does not describe them.
- `workflow_yaml`, which reads a file out of a repository rather than calling an endpoint.
- Permissions. GitHub's description carries **no** structured permission metadata: zero of
  its ~1,222 operations declare one, and the prose that exists describes classic scopes
  rather than the fine-grained triples this plugin derives an App manifest from. Those
  triples are authored here and have no upstream to conform to.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_COLLECTOR = Path(__file__).resolve().parent.parent / "collectors" / "github_collector"
_MANIFEST = json.loads((_COLLECTOR / "github_collection_manifest.json").read_text())
_EXTRACT = json.loads((_COLLECTOR / "github_openapi_extract.json").read_text())


def _rest_sources() -> list[dict]:
    """Manifest sources that name a REST path — the only ones a REST description covers."""
    out = []
    for source in _MANIFEST["sources"]:
        path = source.get("path") or source.get("path_pattern")
        if path and path.startswith("/"):
            out.append(source)
    return out


def test_the_extract_covers_every_rest_source() -> None:
    """A source with no extract entry is unchecked — and would pass everything below by
    simply not being looked at. Fail rather than silently narrow the scope of this file."""
    missing = [s["name"] for s in _rest_sources() if (s.get("path") or s.get("path_pattern")) not in _EXTRACT["paths"]]
    assert not missing, (
        f"REST source(s) absent from the pinned extract: {missing}. "
        "Run scripts/refresh_openapi_extract.py and review the diff."
    )


@pytest.mark.parametrize("source", _rest_sources(), ids=lambda s: s["name"])
def test_every_rest_path_exists_in_githubs_description(source: dict) -> None:
    """The endpoint we call is one GitHub still publishes."""
    path = source.get("path") or source.get("path_pattern")
    entry = _EXTRACT["paths"][path]
    assert entry["present"], (
        f"{source['name']}: GitHub's OpenAPI description has no path {path!r}. "
        "Either it was renamed or retired — this collector is calling something that no longer exists."
    )


@pytest.mark.parametrize("source", _rest_sources(), ids=lambda s: s["name"])
def test_declared_fields_exist_in_the_response_schema(source: dict) -> None:
    """Every field the manifest says it reads is a property GitHub says it returns.

    This is the half that catches a RENAME, which is the quiet one: a retired endpoint
    fails loudly on the next collection, whereas a renamed field just starts arriving as
    None and the node lands looking merely sparse.
    """
    path = source.get("path") or source.get("path_pattern")
    entry = _EXTRACT["paths"][path]
    published = set(entry["item_properties"])
    if not published:
        pytest.skip(f"{source['name']}: no item schema in the description to check against")
    declared = set(source.get("fields") or [])
    # Manifest field names are the names WE give the node; the collector maps some of them.
    # Only assert on the ones claiming to be verbatim response keys.
    unknown = {f for f in declared if f not in published} - _RENAMED_BY_THE_COLLECTOR.get(source["name"], set())
    assert not unknown, (
        f"{source['name']}: manifest declares field(s) {sorted(unknown)} that GitHub's schema for "
        f"{path} does not publish. Either GitHub renamed them, or the manifest names a mapped field "
        "without recording the mapping."
    )


#: Manifest field names that are deliberately OUR name, not GitHub's response key.
#:
#: This table exists because of a SHAPE GAP, and it should shrink to nothing rather than grow.
#: github_core's manifest declares `fields` as a LIST of the names we store. aws_core declares
#: its equivalent as a MAP — `{"account_id": "Account"}`, our name to their key — which makes
#: this check derivable instead of hand-listed. Until this manifest adopts that shape, each
#: rename is enumerated here, per source, so the check stays strict about every OTHER field
#: rather than being loosened for all of them at once.
#:
#: Nearly all of these are the same rename: GitHub returns a bare `id`, and a graph needs a
#: name that says what it is the id OF, because `id` on a node shared across four types is
#: unreadable. That is a good decision the manifest simply cannot express yet.
_RENAMED_BY_THE_COLLECTOR: dict[str, set[str]] = {
    "repository": {"github_id", "owner_login"},          # id; owner.login flattened
    "workflows": {"workflow_id"},                        # id
    "runs": {"run_id", "completed_at"},                  # id; updated_at read as completion
    "jobs": {"job_id"},                                  # id
    "runners": {"runner_id"},                            # id
    "rulesets": {"ruleset_id"},                          # id
    "caches": {"cache_id"},                              # id
    "app_installations": {"installation_id", "account_login", "suspended"},
    "app_installation_self": {"installation_id", "account_login"},
}


def test_the_rename_table_does_not_outlive_its_entries() -> None:
    """A rename listed here that GitHub now publishes verbatim is a stale exemption.

    Same discipline as the guard baselines: an exclusion that no longer excludes anything
    is a lie about the strictness of the check above it, and it only shrinks.
    """
    stale: list[str] = []
    for source in _rest_sources():
        renamed = _RENAMED_BY_THE_COLLECTOR.get(source["name"], set())
        if not renamed:
            continue
        path = source.get("path") or source.get("path_pattern")
        published = set(_EXTRACT["paths"][path]["item_properties"])
        stale += [f"{source['name']}.{f}" for f in renamed & published]
    assert not stale, f"rename exemption(s) no longer needed — GitHub publishes these verbatim: {stale}"
