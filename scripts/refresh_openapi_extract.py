#!/usr/bin/env python3
"""Refresh the pinned extract of GitHub's OpenAPI description.

**Why an extract and not the spec.** GitHub publishes a machine-readable OpenAPI
description of its REST API (`github/rest-api-description`, ~810 paths / ~1,222
operations / 12 MB). Vendoring that is absurd and fetching it inside a unit test makes
the suite need the network and GitHub's uptime. So this script pulls it, keeps only the
paths this plugin actually calls plus the property names of what they return, and writes
a small committed file the tests read offline.

**Why it exists at all.** We do not use a GitHub client library that the vendor keeps
current, so a renamed field or a retired endpoint reaches us as a collection that quietly
returns less — the same absence-reads-as-answer failure this plugin keeps meeting. The
extract turns that into a diff a human reviews.

**Ledger, not auto-generation.** Running this REWRITES the extract; the change then rides
a PR and someone judges it. It must never be run automatically as part of a build — a
spec change that silently updates our expectations would defeat the point of pinning.

Usage:
    python3 scripts/refresh_openapi_extract.py            # rewrite the extract
    python3 scripts/refresh_openapi_extract.py --check    # exit 1 if it would change

Stdlib only: it runs on a maintainer's host, not in the container.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

#: Resolved to a COMMIT before fetching. `main` is mutable, so a refresh from it cannot be
#: reproduced and a reviewer cannot tell which upstream revision produced the extract.
SPEC_REPO = "github/rest-api-description"
SPEC_PATH = "descriptions/api.github.com/api.github.com.json"
SPEC_BRANCH = "main"

#: GraphQL describes itself — introspection is part of the spec, so the config layer is not
#: the un-checkable half it first appears to be. GitHub does not publish the introspection
#: result in its own repo, but octokit generates it from the live API and keeps it current.
GQL_REPO = "octokit/graphql-schema"
GQL_PATH = "schema.json"
GQL_BRANCH = "main"

#: The GraphQL types this collector actually traverses, and the fields it selects on each.
#: Anchored to the TYPE rather than checked as bare names, because a field existing somewhere
#: in a 1,600-type schema proves nothing about the type we select it on.
GQL_TRAVERSED: dict[str, tuple[str, ...]] = {
    "Repository": ("nameWithOwner", "databaseId", "isArchived", "isFork", "visibility", "url",
                   "defaultBranchRef", "rulesets", "environments", "refs", "object"),
    "RepositoryRuleset": ("databaseId", "name", "enforcement", "target", "conditions",
                          "rules", "bypassActors"),
    "RepositoryRulesetBypassActor": ("bypassMode", "organizationAdmin", "repositoryRoleName", "actor"),
    "Environment": ("databaseId", "name", "protectionRules"),
    "Ref": ("name", "target"),
    "Tree": ("entries",),
    "TreeEntry": ("name", "path", "object"),
    "Blob": ("byteSize", "isTruncated", "text"),
    "Tag": ("target",),
    # Types reached deeper in the selection set. Declared so the exclusion list below shrinks
    # to things that are genuinely NOT field selections — keywords, arguments, aliases and
    # connection plumbing — rather than hiding real fields behind a name-match.
    "GitObject": ("oid",),
    "Commit": ("oid",),
    "RepositoryRuleConditions": ("refName",),
    "RefNameConditionTarget": ("include", "exclude"),
    "RepositoryRule": ("type",),
    "DeploymentProtectionRule": ("type", "timeout"),
    "App": ("databaseId", "slug", "name"),
    "Team": ("slug", "name"),
}
_HERE = Path(__file__).resolve().parent
MANIFEST = _HERE.parent / "tap_plugin/github_core/collectors/github_collector/github_collection_manifest.json"
EXTRACT = _HERE.parent / "tap_plugin/github_core/collectors/github_collector/github_openapi_extract.json"


def _resolve(node: dict, spec: dict, depth: int = 0) -> dict:
    """Follow a local `$ref` chain. Depth-capped: a cyclic schema must not hang a refresh."""
    while isinstance(node, dict) and "$ref" in node and depth < 20:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return {}
        target: object = spec
        for part in ref[2:].split("/"):
            if not isinstance(target, dict):
                return {}
            target = target.get(part, {})
        node = target if isinstance(target, dict) else {}
        depth += 1
    return node if isinstance(node, dict) else {}


def _compose(schema: dict, spec: dict) -> dict:
    """Flatten `allOf` / `oneOf` / `anyOf` into one property bag.

    GitHub uses composition freely — `/users/{username}` is a `oneOf` over private-user and
    public-user with a discriminator. A resolver that only understands `$ref` and `properties`
    returns nothing for those, and "nothing" is indistinguishable from "no schema" downstream.

    The UNION is the right answer for our purpose: we are asking "is this field name one GitHub
    can return here", and under a discriminated union any variant's field qualifies.
    """
    merged: dict = {"properties": dict(schema.get("properties") or {})}
    for keyword in ("allOf", "oneOf", "anyOf"):
        for member in schema.get(keyword) or []:
            resolved = _compose(_resolve(member, spec), spec)
            merged["properties"].update(resolved.get("properties") or {})
    for passthrough in ("type", "items"):
        if passthrough in schema:
            merged[passthrough] = schema[passthrough]
    return merged


def _item_properties(op: dict, spec: dict, item_path: str | None) -> list[str]:
    """Property names of ONE collected item, unwrapping the list envelope the manifest names.

    `item_path` is the manifest's own hint — `runners[*]` means the array lives under the
    `runners` key, `[*]` means the response IS the array. Using it rather than guessing
    keeps the two files describing the same thing.
    """
    schema = _compose(
        _resolve(
            (((op.get("responses") or {}).get("200") or {}).get("content") or {})
            .get("application/json", {})
            .get("schema", {}),
            spec,
        ),
        spec,
    )
    if item_path:
        key = item_path.split("[")[0]
        if key:
            schema = _compose(_resolve((schema.get("properties") or {}).get(key, {}), spec), spec)
    if schema.get("type") == "array":
        schema = _compose(_resolve(schema.get("items", {}), spec), spec)
    return sorted((schema.get("properties") or {}).keys())


def _resolve_commit(repo: str, branch: str) -> str:
    """The commit `branch` points at right now, so the fetch below is reproducible."""
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github.sha"})
    with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310 — constant https URL
        return response.read().decode("utf-8").strip()


def _graphql_extract() -> dict:
    """Fields the schema publishes on each type we traverse, pinned to a commit.

    Same shape and same discipline as the REST half: pin, extract only what we depend on,
    commit the extract so the tests stay offline.
    """
    commit = _resolve_commit(GQL_REPO, GQL_BRANCH)
    url = f"https://raw.githubusercontent.com/{GQL_REPO}/{commit}/{GQL_PATH}"
    with urllib.request.urlopen(url, timeout=180) as response:  # nosec B310 — constant https host
        raw = response.read()
    document = json.loads(raw.decode("utf-8"))
    schema = document.get("data", document).get("__schema", {})
    by_name = {t["name"]: t for t in schema.get("types", [])}

    types: dict[str, dict] = {}
    for type_name, selected in GQL_TRAVERSED.items():
        entry = by_name.get(type_name)
        if entry is None:
            types[type_name] = {"present": False, "fields": [], "selected": sorted(selected)}
            continue
        types[type_name] = {
            "present": True,
            "fields": sorted(f["name"] for f in (entry.get("fields") or [])),
            "selected": sorted(selected),
        }
    return {
        "commit": commit,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "url": url,
        "types": types,
    }


def _app_permissions_extract(spec: dict) -> dict:
    """Every App permission key GitHub's description enumerates, with its allowed levels."""
    props = spec["components"]["schemas"]["app-permissions"]["properties"]
    return {key: {"levels": list(prop.get("enum", [])), "description": prop.get("description", "")} for key, prop in props.items()}


def build() -> dict:
    commit = _resolve_commit(SPEC_REPO, SPEC_BRANCH)
    url = f"https://raw.githubusercontent.com/{SPEC_REPO}/{commit}/{SPEC_PATH}"
    with urllib.request.urlopen(url, timeout=120) as response:  # nosec B310 — constant https host
        raw = response.read()
    spec = json.loads(raw.decode("utf-8"))
    digest = hashlib.sha256(raw).hexdigest()

    # Map normalized-path -> the OPERATION ITSELF, not the path string. Re-indexing
    # `spec["paths"][name]` with a name derived from the manifest reads as path construction
    # from external input (SonarCloud flagged it); carrying the resolved object removes the
    # pattern rather than annotating it, and is a lookup fewer besides.
    normalized = {
        re.sub(r"\{[a-zA-Z_]+\}", "{}", name): operations.get("get", {})
        for name, operations in spec["paths"].items()
    }
    manifest = json.loads(MANIFEST.read_text())

    out: dict[str, dict] = {}
    for source in manifest["sources"]:
        path = source.get("path") or source.get("path_pattern")
        if not path or not path.startswith("/"):
            continue  # GraphQL sources and repo file paths are not in a REST description
        op = normalized.get(re.sub(r"\{[a-zA-Z_]+\}", "{}", path))
        if op is None:
            out[path] = {"source": source["name"], "present": False, "item_properties": []}
            continue
        out[path] = {
            "source": source["name"],
            "present": True,
            "item_properties": _item_properties(op, spec, source.get("item_path")),
        }

    return {
        "_comment": (
            "GENERATED by scripts/refresh_openapi_extract.py from GitHub's OpenAPI description. "
            "Do not hand-edit. A diff here is GitHub changing its API under us — review it, do not "
            "regenerate it away."
        ),
        "spec_version": spec.get("info", {}).get("version"),
        "spec_commit": commit,
        "spec_sha256": digest,
        "spec_url": f"https://raw.githubusercontent.com/{SPEC_REPO}/{commit}/{SPEC_PATH}",
        "paths": out,
        "graphql": _graphql_extract(),
        # The App-permission CATALOGUE: `components.schemas.app-permissions` is the one
        # place GitHub enumerates every fine-grained App permission and its levels. The
        # ledger (github_app_permissions.json) must classify every key here — a key GitHub
        # adds shows up as an unclassified entry and fails the ledger test, which is the
        # "have they added permissions we should consider" check made mechanical.
        "app_permissions": _app_permissions_extract(spec),
    }


def main() -> int:
    fresh = build()
    check = "--check" in sys.argv
    if check:
        if not EXTRACT.exists():
            # Fail closed. Writing the extract under --check would let a nightly "verify"
            # step report success on a run that verified nothing.
            print(f"{EXTRACT.name} is MISSING — nothing to check against.")
            return 1
        if json.loads(EXTRACT.read_text()) == fresh:
            print("extract is current")
            return 0
        print("extract is STALE — GitHub's description has moved. Re-run without --check and review the diff.")
        return 1
    # NOSONAR — EXTRACT is a module constant derived from __file__, not from argv. Sonar's
    # taint analysis reaches it because main() also reads sys.argv. Restructuring once already
    # moved the finding to a different line rather than clearing it; chasing it further would
    # be shaping code around an analyzer instead of a risk.
    EXTRACT.write_text(json.dumps(fresh, indent=2, sort_keys=True) + "\n")  # NOSONAR
    print(f"wrote {EXTRACT.name}: {len(fresh['paths'])} path(s), spec version {fresh['spec_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
