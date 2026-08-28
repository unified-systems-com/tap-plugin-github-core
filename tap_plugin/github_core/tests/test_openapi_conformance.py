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
    declared = set(source.get("fields") or [])
    published = set(entry["item_properties"])
    # FAIL CLOSED. An earlier version skipped here, which turned every extractor limitation
    # into a silently-passing check — and it was already happening: GitHub describes
    # /users/{username} as a `oneOf` over public-user and private-user, the resolver did not
    # understand composition, and so the `account` source's fields went unverified while the
    # suite stayed green. That is the exact defect this file exists to catch, committed inside
    # the catcher. If a source declares fields and we extracted no schema, the extractor is
    # behind GitHub's spec and the answer is to fix it, not to shrug.
    assert published or not declared, (
        f"{source['name']}: {path} is published but no item schema was extracted, while the "
        f"manifest declares {len(declared)} field(s). The refresh script cannot read this "
        "response shape — extend it rather than letting the check pass on nothing."
    )
    # Manifest field names are the names WE give the node; the collector maps some of them.
    # Only assert on the ones claiming to be verbatim response keys.
    unknown = {f for f in declared if f not in published} - set(_RENAMED_FROM.get(source["name"], {}))
    assert not unknown, (
        f"{source['name']}: manifest declares field(s) {sorted(unknown)} that GitHub's schema for "
        f"{path} does not publish. Either GitHub renamed them, or the manifest names a mapped field "
        "without recording the mapping."
    )


#: Manifest field name -> the response key it is READ FROM. A map, not a set, because a set
#: exempts a field from checking entirely and would hide the exact drift this file exists to
#: catch: if GitHub removed `id`, an exemption saying only "workflow_id is renamed" still
#: passes. Naming the upstream key makes the exemption assert something.
#:
#: This table exists because of a SHAPE GAP and should shrink to nothing. github_core's
#: manifest declares `fields` as a LIST of the names we store; aws_core declares its
#: equivalent as a MAP, our-name to their-key, which would make all of this derivable rather
#: than hand-listed. Until this manifest adopts that shape, the renames live here.
#:
#: Nearly all are the same one: GitHub returns a bare `id`, and a graph needs a name saying
#: what it is the id OF, because `id` on a node type shared across four kinds is unreadable.
_RENAMED_FROM: dict[str, dict[str, str]] = {
    "account": {"github_id": "id", "account_type": "type"},
    "repository": {"github_id": "id", "owner_login": "owner"},  # owner.login, flattened
    "workflows": {"workflow_id": "id"},
    "runs": {"run_id": "id", "completed_at": "updated_at"},
    "jobs": {"job_id": "id"},
    "runners": {"runner_id": "id"},
    "rulesets": {"ruleset_id": "id"},
    "caches": {"cache_id": "id"},
    "app_installations": {"installation_id": "id", "account_login": "account", "suspended": "suspended_at"},
    "app_installation_self": {"installation_id": "id", "account_login": "account"},
}


@pytest.mark.parametrize("source", _rest_sources(), ids=lambda s: s["name"])
def test_every_rename_names_a_key_github_still_publishes(source: dict) -> None:
    """The upstream side of each rename still exists.

    Without this, an exemption is a hole: `workflow_id` would stay exempt and green even if
    GitHub stopped returning `id` altogether. Asserting the SOURCE key is what makes the
    exemption a claim rather than a silence.
    """
    renames = _RENAMED_FROM.get(source["name"], {})
    if not renames:
        pytest.skip("no renames declared for this source")
    path = source.get("path") or source.get("path_pattern")
    published = set(_EXTRACT["paths"][path]["item_properties"])
    gone = {ours: theirs for ours, theirs in renames.items() if theirs not in published}
    assert not gone, (
        f"{source['name']}: field(s) mapped from response key(s) GitHub no longer publishes at "
        f"{path}: {gone}. The rename is stale — either the upstream key moved, or the collector "
        "now reads something else and this table was not updated with it."
    )


def test_the_rename_table_does_not_outlive_its_entries() -> None:
    """A rename listed here that GitHub now publishes verbatim is a stale exemption.

    Same discipline as the guard baselines: an exclusion that no longer excludes anything is a
    lie about the strictness of the check above it, and it only shrinks.
    """
    stale: list[str] = []
    for source in _rest_sources():
        renamed = set(_RENAMED_FROM.get(source["name"], {}))
        if not renamed:
            continue
        path = source.get("path") or source.get("path_pattern")
        published = set(_EXTRACT["paths"][path]["item_properties"])
        stale += [f"{source['name']}.{f}" for f in renamed & published]
    assert not stale, f"rename exemption(s) no longer needed — GitHub publishes these verbatim: {stale}"
