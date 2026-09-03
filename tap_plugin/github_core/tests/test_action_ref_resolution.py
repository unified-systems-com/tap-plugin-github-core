"""Out-of-scope `uses:` refs resolved over REST (req-github-core-actions-used-6/-7).

`test_actions_used.py` covers what a pin proves from the string and from in-scope refs. This file
covers the case that is almost every real usage — `actions/checkout@v4`, whose repository is NOT
in the observed scope — where the name is looked up once per run: tags before heads, an annotated
tag peeled to its commit, every outcome a named state, and a cap so the run cannot spend its rate
limit on repositories it does not observe. Observed live on `unified-systems-com/git-serious-fixtures`
on 2026-09-03 (the org's own step-level actions are all SHA-pinned, so the fixture carries the shapes).
"""

from __future__ import annotations

from typing import Any

import tap_plugin.github_core.models as github  # noqa: F401 — trigger model registration
from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError
from tap_plugin.github_core.collectors.github_collector.collector import (
    _ACTION_REF_RESOLUTION_CAP,
    GithubCollector,
)
from tap_plugin.github_core.collectors.github_collector.identity import workflow_job_id
from tap_plugin.github_core.collectors.github_collector.parser import parse_workflow_yaml

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
TAG_OBJ = "d" * 40


class _StubClient:
    """Routes `GET` by path; a missing route is a real 404 with a JSON body."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        self.calls.append(path)
        answer = self.routes.get(path)
        if answer is None:
            raise GithubAPIError(status=404, url=path, body='{"message": "Not Found"}')
        if isinstance(answer, GithubAPIError):
            raise answer
        return answer


_ROUTES: dict[str, Any] = {
    "/repos/actions/checkout/git/ref/tags/v4": {"object": {"type": "commit", "sha": SHA_A}},
    "/repos/actions/checkout/git/ref/heads/main": {"object": {"type": "commit", "sha": SHA_B}},
    "/repos/actions/setup-python/git/ref/tags/v5": {"object": {"type": "tag", "sha": TAG_OBJ}},
    f"/repos/actions/setup-python/git/tags/{TAG_OBJ}": {"object": {"type": "commit", "sha": SHA_C}},
    "/repos/secret/thing/git/ref/tags/v1": GithubAPIError(status=403, url="x", body='{"message": "Forbidden"}'),
}


def _collector() -> GithubCollector:
    c = GithubCollector.__new__(GithubCollector)
    c._config = {}
    c.records: list[tuple[str, str]] = []  # type: ignore[attr-defined]
    c.record_warn = lambda site, code, message, **kw: c.records.append(("warn", code))  # type: ignore[method-assign]
    c.record_info = lambda site, code, message, **kw: c.records.append(("info", code))  # type: ignore[method-assign]
    return c


def _uses_edges(c: GithubCollector, client: Any, yaml_text: str) -> dict[str, dict[str, Any]]:
    """Run the declared-job emission over one workflow; return USES_ACTION properties by declared ref."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    parsed = parse_workflow_yaml(yaml_text)
    for job in parsed["jobs"]:
        c._emit_used_actions(
            "o/r",
            workflow_job_id("o/r", 1, job["id"]),
            job.get("action_refs") or [],
            {"github.platform": "github.com", "github.owner": "o", "github.repo": "r"},
            nodes,
            edges,
            client=client,
        )
    out: dict[str, dict[str, Any]] = {}
    for e in edges:
        if e["edge"]["edge_type"] == "USES_ACTION__github_core":
            out[e["edge"]["properties"]["declared_ref"]] = e["edge"]["properties"]
    return out


_WORKFLOW = f"""\
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/checkout@main
      - uses: actions/setup-python@v5
      - uses: actions/checkout@{SHA_A}
      - uses: actions/checkout@nope
      - uses: secret/thing@v1
      - uses: docker://alpine:3.19
"""


class TestRestResolution:
    def test_tags_before_heads_annotated_peeled_and_every_state_named(self) -> None:
        """req-github-core-actions-used-6: a tag wins over a branch of the same name; an annotated
        tag's object is peeled to the commit; not-found is `unresolved`; a refusal is
        `unobservable` and warned; a SHA and a Docker tag stay `literal`."""
        c = _collector()
        client = _StubClient(_ROUTES)
        props = _uses_edges(c, client, _WORKFLOW)
        assert props["v4"]["pin_kind"] == "tag" and props["v4"]["resolved_sha"] == SHA_A
        assert props["v4"]["resolution"] == "rest"
        assert props["main"]["pin_kind"] == "branch" and props["main"]["resolved_sha"] == SHA_B
        assert props["v5"]["pin_kind"] == "tag" and props["v5"]["resolved_sha"] == SHA_C
        assert props["nope"]["pin_kind"] == "unresolved" and props["nope"]["resolution"] == "unresolved"
        assert "resolved_sha" not in props["nope"]
        assert props["v1"]["resolution"] == "unobservable" and props["v1"]["pin_kind"] == "unresolved"
        assert props[SHA_A]["resolution"] == "literal" and props[SHA_A]["resolved_sha"] == SHA_A
        assert props["3.19"]["resolution"] == "literal" and props["3.19"]["pin_kind"] == "tag"
        assert ("warn", "ACTION_REF_UNOBSERVABLE_403") in c.records
        assert client.calls.index("/repos/actions/checkout/git/ref/tags/main") < client.calls.index(
            "/repos/actions/checkout/git/ref/heads/main"
        )

    def test_without_a_client_out_of_scope_stays_unobservable(self) -> None:
        """A caller driving the walk without a client (the older tests do) fetches nothing."""
        props = _uses_edges(_collector(), None, "on: push\njobs:\n  j:\n    steps:\n      - uses: actions/checkout@v4\n")
        assert props["v4"]["resolution"] == "unobservable" and props["v4"]["pin_kind"] == "unresolved"

    def test_one_lookup_per_distinct_ref_and_a_cap(self) -> None:
        """req-github-core-actions-used-7: cached per (repository, ref); past the cap the edge says
        `not_attempted` and the tally counts it."""
        yaml_text = """\
on: push
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
  b:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""
        c = _collector()
        client = _StubClient(_ROUTES)
        _uses_edges(c, client, yaml_text)
        assert client.calls == ["/repos/actions/checkout/git/ref/tags/v4"]
        assert c._action_ref_state()["lookups"] == 1

        c2 = _collector()
        c2._action_ref_state()["lookups"] = _ACTION_REF_RESOLUTION_CAP
        client2 = _StubClient(_ROUTES)
        props = _uses_edges(c2, client2, yaml_text)
        assert client2.calls == []
        assert props["v4"]["resolution"] == "not_attempted"
        assert c2._action_ref_state()["skipped"] == 1
        assert c2._usage_tally()["not_attempted"] == 2

    def test_in_scope_still_wins_over_rest(self) -> None:
        """req-github-core-actions-used-3: an in-scope repository answers from the config layer and
        no REST call is made for it."""
        in_scope = {
            "nameWithOwner": "o/act",
            "defaultBranchRef": {"name": "main"},
            "branchRefs": {"totalCount": 1, "nodes": [{"name": "main", "target": {"oid": SHA_B, "__typename": "Commit"}}]},
            "tagRefs": {"totalCount": 1, "nodes": [{"name": "v1", "target": {"oid": SHA_A, "__typename": "Commit"}}]},
        }
        c = _collector()
        c._config = {"o/act": in_scope}
        client = _StubClient({})
        props = _uses_edges(c, client, "on: push\njobs:\n  j:\n    steps:\n      - uses: o/act@v1\n      - uses: o/act@main\n")
        assert props["v1"]["resolution"] == "in_scope" and props["v1"]["resolved_sha"] == SHA_A
        assert props["main"]["resolution"] == "in_scope" and props["main"]["pin_kind"] == "branch"
        assert client.calls == []

    def test_the_tally_counts_rest_states_separately(self) -> None:
        c = _collector()
        _uses_edges(c, _StubClient(_ROUTES), _WORKFLOW)
        tally = c._usage_tally()
        assert tally["rest"] == 3 and tally["unresolved"] == 1 and tally["unobservable"] == 1
        assert tally["not_attempted"] == 0
