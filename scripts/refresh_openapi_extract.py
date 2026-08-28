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

import json
import re
import sys
import urllib.request
from pathlib import Path

SPEC_URL = (
    "https://raw.githubusercontent.com/github/rest-api-description/main/"
    "descriptions/api.github.com/api.github.com.json"
)
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


def build() -> dict:
    with urllib.request.urlopen(SPEC_URL, timeout=120) as response:  # nosec B310 — constant https URL
        spec = json.loads(response.read().decode("utf-8"))

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
        "spec_url": SPEC_URL,
        "paths": out,
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
    EXTRACT.write_text(json.dumps(fresh, indent=2, sort_keys=True) + "\n")
    print(f"wrote {EXTRACT.name}: {len(fresh['paths'])} path(s), spec version {fresh['spec_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
