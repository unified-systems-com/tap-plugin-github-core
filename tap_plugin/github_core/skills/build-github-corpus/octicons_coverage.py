#!/usr/bin/env python3
"""Coverage delta: github_core's vocabulary against GitHub's Octicons (build-github-corpus, Step 2).

Stdlib only — runs on a host without the TAP venv. Reads the hand-authored classification beside
this file (octicons-concepts.json), the plugin's models/ and edges/ directories, and optionally the
live icons/ listing of primer/octicons at a pinned tag. Prints the summary table, the per-type
mapping, the concept-bearing glyphs with no TAP type, and — the load-bearing line — every live
glyph the classification does not know. Never classifies automatically: an unknown name is a
question for a human or an agent, and the answer is a row in the JSON.

    python3 octicons_coverage.py --plugin-root tap_plugin/github_core --tag v19.15.1
    python3 octicons_coverage.py --plugin-root tap_plugin/github_core --offline [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
VARIANT_RE = re.compile(r"-(?:fill|filled|inset)$")
SIZE_RE = re.compile(r"-(?:12|16|24|48|96)$")


def _base_name(filename: str) -> str:
    """'git-pull-request-16.svg' -> 'git-pull-request'."""
    name = filename[:-4] if filename.endswith(".svg") else filename
    return SIZE_RE.sub("", name)


def _concept(name: str) -> str:
    """Collapse style and feed variants onto their parent concept."""
    if name.startswith("feed-"):
        return name[len("feed-"):]
    return VARIANT_RE.sub("", name)


def load_classification() -> dict:
    return json.loads((HERE / "octicons-concepts.json").read_text())


def fetch_live_names(tag: str) -> set[str]:
    url = f"https://api.github.com/repos/primer/octicons/contents/icons?ref={tag}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "tap-build-github-corpus"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - pinned public GitHub API URL
        listing = json.load(resp)
    return {_base_name(item["name"]) for item in listing if item["name"].endswith(".svg")}


def plugin_types(plugin_root: Path) -> tuple[list[str], list[str]]:
    nodes: list[str] = []
    for py in sorted((plugin_root / "models").glob("*.py")):
        m = re.search(r'ENTITY_TYPE\s*:\s*ClassVar\[str\]\s*=\s*"([^"]+)"', py.read_text()) or re.search(
            r'ENTITY_TYPE\s*=\s*"([^"]+)"', py.read_text()
        )
        if m:
            nodes.append(m.group(1))
    edges = [p.name[: -len(".edge.json")] for p in sorted((plugin_root / "edges").glob("*.edge.json"))]
    return nodes, edges


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plugin-root", required=True, help="path to tap_plugin/github_core")
    ap.add_argument("--tag", default=None, help="primer/octicons tag to list live (default: the JSON's pin)")
    ap.add_argument("--offline", action="store_true", help="skip the live listing; use the checked-in file only")
    ap.add_argument("--json", action="store_true", help="emit the machine-readable delta instead of tables")
    args = ap.parse_args()

    cls = load_classification()
    glyphs = {g["name"]: g for g in cls["glyphs"]}
    tag = args.tag or cls.get("octicons_tag", "")
    nodes, edges = plugin_types(Path(args.plugin_root))

    live: set[str] | None = None
    if not args.offline:
        try:
            live = fetch_live_names(tag)
        except Exception as exc:  # noqa: BLE001 - report and fall back, never fake a listing
            print(f"live listing failed ({exc}); continuing offline", file=sys.stderr)

    unknown_live = sorted(live - set(glyphs)) if live is not None else None
    retired = sorted(set(glyphs) - live) if live is not None else None

    concepts: dict[str, dict] = {}
    for g in glyphs.values():
        c = _concept(g["name"])
        concepts.setdefault(c, g)  # first seen wins; variants share the parent's class by construction
    a_concepts = {c: g for c, g in concepts.items() if g["class"] == "a"}
    families = defaultdict(list)
    for c, g in a_concepts.items():
        families[g.get("family") or c].append(c)

    mapped_types = {t["type"]: t for t in cls.get("tap_types", [])}
    unmapped_nodes = [n for n in nodes if n not in mapped_types]
    unmapped_edges = [e for e in edges if e not in mapped_types]
    nodes_with_glyph = [n for n in nodes if mapped_types.get(n, {}).get("octicon")]
    edges_with_glyph = [e for e in edges if mapped_types.get(e, {}).get("octicon")]

    fam_status: dict[str, str] = {}
    for fam, members in families.items():
        statuses = {a_concepts[m].get("tap_status") for m in members}
        fam_status[fam] = "exists" if "exists" in statuses or "edge" in statuses else ("planned" if "planned" in statuses else ("field" if "field" in statuses else ("rejected" if "rejected" in statuses else "none")))
    fam_counts = Counter(fam_status.values())
    gaps_by_area = defaultdict(list)
    for c, g in sorted(a_concepts.items()):
        if g.get("tap_status") in (None, "none"):
            gaps_by_area[g.get("product_area") or "unknown"].append(c)

    delta = {
        "octicons_tag": tag,
        "tap_nodes": len(nodes), "tap_edges": len(edges),
        "octicons_unique_names": len(glyphs), "octicons_concepts": len(concepts),
        "concept_bearing_glyphs": sum(1 for g in glyphs.values() if g["class"] == "a"),
        "concept_bearing_concepts": len(a_concepts), "concept_families": len(families),
        "nodes_with_glyph": len(nodes_with_glyph), "edges_with_glyph": len(edges_with_glyph),
        "families_modelled": fam_counts.get("exists", 0), "families_planned": fam_counts.get("planned", 0),
        "families_field_or_rejected": fam_counts.get("field", 0) + fam_counts.get("rejected", 0),
        "families_gap": fam_counts.get("none", 0),
        "unmapped_tap_types": unmapped_nodes + unmapped_edges,
        "unknown_live_glyphs": unknown_live, "retired_glyphs": retired,
        "gaps_by_product_area": dict(gaps_by_area),
    }
    if args.json:
        print(json.dumps(delta, indent=2, sort_keys=True))
        return 0

    print(f"# Octicons coverage delta — github_core vs primer/octicons {tag}\n")
    rows = [
        ("TAP node types", delta["tap_nodes"]), ("TAP edge types", delta["tap_edges"]),
        ("Octicons unique names / concepts", f"{delta['octicons_unique_names']} / {delta['octicons_concepts']}"),
        ("Concept-bearing (class a) glyphs / concepts / families", f"{delta['concept_bearing_glyphs']} / {delta['concept_bearing_concepts']} / {delta['concept_families']}"),
        ("Our nodes with a natural Octicon", f"{delta['nodes_with_glyph']} / {delta['tap_nodes']}"),
        ("Our edges with a natural Octicon", f"{delta['edges_with_glyph']} / {delta['tap_edges']}"),
        ("Concept families modelled today", f"{delta['families_modelled']} / {delta['concept_families']}"),
        ("… planned in the corpus", delta["families_planned"]),
        ("… deliberately a field or rejected", delta["families_field_or_rejected"]),
        ("… genuine gaps", delta["families_gap"]),
    ]
    print("| Measure | Value |\n| --- | ---: |")
    for k, v in rows:
        print(f"| {k} | {v} |")
    print("\n## TAP types the classification does not map yet (add a tap_types row)")
    print("\n".join(f"- `{t}`" for t in delta["unmapped_tap_types"]) or "- none")
    if unknown_live is not None:
        print(f"\n## Live glyphs at {tag} the classification does not know (classify them)")
        print("\n".join(f"- `{n}`" for n in unknown_live) or "- none")
        print(f"\n## Classified glyphs no longer in {tag} (retired)")
        print("\n".join(f"- `{n}`" for n in retired) or "- none")
    print("\n## Concept-bearing glyphs with no TAP type, by product area")
    for area, names in sorted(gaps_by_area.items()):
        print(f"- **{area}** ({len(names)}): " + ", ".join(f"`{n}`" for n in names))
    print("\n## Per-type mapping")
    print("| TAP type | Kind | Octicon | Note |\n| --- | --- | --- | --- |")
    for t in nodes + edges:
        m = mapped_types.get(t, {})
        print(f"| `{t}` | {m.get('kind', '?')} | {m.get('octicon') or 'none'} | {m.get('note', '')} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
