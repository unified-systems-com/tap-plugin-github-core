"""The workflow page bundle is internally consistent (req-github-core-workflow-page).

Structure only — no grid: ids unique, every edge endpoint in the file (the projection's out-of-file
targets are none), hotlink values equal their edge targets, every search declares workflow_id as its one
required integer input and reads it, the layout's module exists in the package, and no search names a
repository.
"""

from __future__ import annotations

import json
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
BUNDLE = PKG / "grift" / "workflow-page.grift.json"


def _batch() -> dict:
    return json.loads(BUNDLE.read_text())["batches"][0]


def test_ids_unique_and_edges_resolve_in_file() -> None:
    b = _batch()
    ids = [n["entity"]["entity_id"] for n in b["nodes"]] + [e["entity"]["entity_id"] for e in b["edges"]] + [b["batch_entity"]["entity_id"]]
    assert len(ids) == len(set(ids))
    node_ids = {n["entity"]["entity_id"] for n in b["nodes"]}
    for e in b["edges"]:
        assert e["edge"]["from_entity_id"] in node_ids, e["entity"]["name"]
        assert e["edge"]["to_entity_id"] in node_ids, e["entity"]["name"]


def test_hotlink_values_name_their_targets() -> None:
    b = _batch()
    for e in b["edges"]:
        hot = (e["edge"].get("properties") or {}).get("hotlink")
        if not hot or hot["spec"] == "page-panels":
            continue
        assert hot["value"] == e["edge"]["to_entity_id"], e["entity"]["name"]


def test_page_layout_slots_match_uses_panel_edges() -> None:
    b = _batch()
    page = next(n for n in b["nodes"] if n["entity"]["entity_type"] == "page")
    slots = {row["panel-id"] for row in page["node"]["layout"]["columns"]["col-1"]["rows"].values()}
    hot = {e["edge"]["properties"]["hotlink"]["value"] for e in b["edges"] if e["edge"]["edge_type"] == "USES_PANEL"}
    assert slots == hot == {"identity", "anatomy", "runs"}
    assert page["node"]["slug"] == "/github_core/workflow"
    assert page["node"]["discoverable"] is False


def test_every_search_requires_and_reads_workflow_id_as_an_integer() -> None:
    b = _batch()
    searches = [n for n in b["nodes"] if n["entity"]["entity_type"] == "search"]
    assert len(searches) == 4
    for s in searches:
        schema = s["node"]["input_schema"]
        assert set(schema["required"]) == {"workflow_id"}, s["node"]["name"]
        # Page inputs arrive as strings and are coerced by this schema; a string or untyped
        # declaration silently matches nothing against the integer field.
        assert schema["properties"]["workflow_id"]["type"] == "integer", s["node"]["name"]
        query = "\n".join(s["node"]["definition"]["query"])
        assert "w.data.workflow_id = $workflow_id" in query, s["node"]["name"]
        assert "$repo" not in query and "CONTAINS" not in query, s["node"]["name"]
        assert "unified-systems-com" not in query and "/tap" not in query


def test_projection_composes_one_elevation_with_the_anatomy_layout() -> None:
    b = _batch()
    by_type = {t: [n for n in b["nodes"] if n["entity"]["entity_type"] == t] for t in ("projection", "elevation", "layout")}
    assert {t: len(v) for t, v in by_type.items()} == {"projection": 1, "elevation": 1, "layout": 1}
    proj, elev, lay = by_type["projection"][0], by_type["elevation"][0], by_type["layout"][0]
    assert proj["node"]["definition"]["elevations"] == [elev["entity"]["entity_id"]]
    assert proj["node"]["definition"]["default_elevation_id"] == elev["entity"]["entity_id"]
    assert elev["node"]["definition"]["layouts"] == [lay["entity"]["entity_id"]]
    js_file = lay["node"]["definition"]["js_file"]
    assert (PKG.parent.parent / js_file).is_file(), js_file


def test_layout_module_is_generic_by_grep() -> None:
    lay = next(n for n in _batch()["nodes"] if n["entity"]["entity_type"] == "layout")
    source = (PKG.parent.parent / lay["node"]["definition"]["js_file"]).read_text()
    for forbidden in ("unified-systems-com", "product-lines", "api-fuzz", "publish-images"):
        assert forbidden not in source, forbidden
