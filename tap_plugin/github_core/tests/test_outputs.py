"""Outputs: what the pipeline produces (github-core#31; req-github-core-releases / -artifacts / -packages).

Fixtures are **captured from the live API** (`tests/fixtures/outputs.json`, 2026-09-02, read-only
App installation token, `unified-systems-com/tap`), not hand-authored. The packages half of the
fixture is the interesting one: it records what the credential we recommend actually receives —
a 400 for `container`, a 200 `[]` for `npm` — which is the shape these tests exist to keep honest.

The tests that matter most are about what an EMPTY answer means. Zero releases from a query that
did not answer the field, zero artifacts from a 403, zero packages from a credential GitHub does
not enable for the endpoint: each is a blank that reads as reassurance and must not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import tap_plugin.github_core.models as github  # noqa: F401 — trigger model registration
from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError
from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector
from tap_plugin.github_core.collectors.github_collector.graphql_client import GithubGraphQLClient
from tap_plugin.github_core.collectors.github_collector.identity import (
    account_id,
    actions_artifact_id,
    package_id,
    package_purl,
    package_version_id,
    release_id,
    repository_id,
)

from tap_grid.registry import get_model_class

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "outputs.json").read_text())
_REPO = "unified-systems-com/tap"
_OWNER = "unified-systems-com"
_DIMS = {"github.platform": "github.com", "github.owner": _OWNER, "github.repo": "tap"}


class _FakeClient:
    """Replays the captured responses per path, and records what was asked for."""

    def __init__(
        self,
        *,
        artifacts_fail: int | None = None,
        packages: dict[str, Any] | None = None,
        versions_fail: int | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self._artifacts_fail = artifacts_fail
        #: package_type -> list (answer) or int (refusal status). Default: the captured shape.
        self._packages = (
            packages
            if packages is not None
            else {
                "container": 400,
                "npm": [],
                "maven": [],
                "rubygems": [],
                "docker": [],
                "nuget": [],
            }
        )
        self._versions_fail = versions_fail

    def get(self, path: str, params: dict[str, str] | None = None, **_: Any) -> Any:
        self.calls.append((path, dict(params or {})))
        if path.endswith("/actions/artifacts"):
            if self._artifacts_fail is not None:
                raise GithubAPIError(status=self._artifacts_fail, url=path, body="Resource not accessible")
            return json.loads(json.dumps(_FIXTURE["artifacts"]))
        raise AssertionError(f"unexpected GET {path}")

    def get_paginated(self, path: str, params: dict[str, str] | None = None, **_: Any) -> list[Any]:
        self.calls.append((path, dict(params or {})))
        if path.endswith("/versions"):
            if self._versions_fail is not None:
                raise GithubAPIError(status=self._versions_fail, url=path, body="nope")
            return json.loads(json.dumps(_FIXTURE["package_versions"]))
        if path.endswith("/packages"):
            answer = self._packages.get((params or {}).get("package_type", ""), [])
            if isinstance(answer, int):
                raise GithubAPIError(status=answer, url=path, body=_FIXTURE["packages_list_container_refused"]["body"])
            return json.loads(json.dumps(answer))
        raise AssertionError(f"unexpected paginated GET {path}")


def _collector(
    *, config: dict[str, Any] | None = None, has_pat: bool = False, pat_client: Any = None
) -> GithubCollector:
    # `__new__` rather than `__init__`: CollectorBase wants a runtime config we do not need to
    # exercise a pure emitter. Matches test_rule_suites / test_self_tier_vocabulary.
    collector = GithubCollector.__new__(GithubCollector)
    collector._config = config or {}
    collector._pat_client = pat_client
    collector._run_index = {}
    collector._repo_envelopes = {}
    collector.warns: list[tuple] = []  # type: ignore[attr-defined]
    collector.infos: list[tuple] = []  # type: ignore[attr-defined]
    collector.record_warn = lambda *a, **k: collector.warns.append((a, k))  # type: ignore[method-assign,attr-defined]
    collector.record_info = lambda *a, **k: collector.infos.append((a, k))  # type: ignore[method-assign,attr-defined]
    return collector


def _run(run_id_int: int, *, head_sha: str = "", head_branch: str = "", event: str = "push") -> dict[str, Any]:
    return {"run_id": run_id_int, "uuid": uuid4(), "head_sha": head_sha, "head_branch": head_branch, "event": event}


def _of_type(nodes: list[dict], entity_type: str) -> list[dict]:
    return [n for n in nodes if n["entity"]["entity_type"] == entity_type]


def _edges_of(edges: list[dict], slug: str) -> list[dict]:
    return [e for e in edges if e["edge"]["edge_type"] == slug]


def _codes(warns: list[tuple]) -> list[str]:
    return [w[0][1] for w in warns]


# --------------------------------------------------------------------------------------------
# Releases
# --------------------------------------------------------------------------------------------

_RELEASES = _FIXTURE["releases_graphql"]
_FIRST_RELEASE = _RELEASES["nodes"][0]


def _collect_releases(collector: GithubCollector, *, ref_uuids=None, runs=None):
    nodes: list[dict] = []
    edges: list[dict] = []
    notes: dict[str, str] = {}
    state = collector._collect_releases(
        _REPO,
        repository_id(_REPO),
        {**_DIMS, "github.surface": "releases", "github.observation": "execution"},
        ref_uuids or {},
        runs or [],
        nodes,
        edges,
        notes,
    )
    return state, nodes, edges, notes


class TestReleasesLand:
    @pytest.mark.spec("req-github-core-releases-1")
    def test_a_release_becomes_a_node_keyed_on_repo_and_release_id(self) -> None:
        collector = _collector(config={_REPO: {"releases": _RELEASES}})
        state, nodes, edges, _ = _collect_releases(collector)
        releases = _of_type(nodes, "github_core__github_release")
        assert state == "observed"
        assert len(releases) == len(_RELEASES["nodes"]) == 5
        assert releases[0]["entity"]["entity_id"] == str(release_id(_REPO, _FIRST_RELEASE["databaseId"]))
        assert releases[0]["node"]["tag_name"] == _FIRST_RELEASE["tagName"]
        assert releases[0]["node"]["target_sha"] == _FIRST_RELEASE["tagCommit"]["oid"]
        assert releases[0]["node"]["author_login"] == _FIRST_RELEASE["author"]["login"]
        assert len(_edges_of(edges, "PUBLISHES_RELEASE__github_core")) == 5

    @pytest.mark.spec("req-github-core-releases-1")
    def test_the_registered_model_accepts_the_captured_shape(self) -> None:
        model = get_model_class("github_core__github_release")
        _, nodes, _, _ = _collect_releases(_collector(config={_REPO: {"releases": _RELEASES}}))
        emitted = set(_of_type(nodes, "github_core__github_release")[0]["node"])
        assert emitted <= set(model.FIELD_CRUD_SCHEMA), sorted(emitted - set(model.FIELD_CRUD_SCHEMA))

    @pytest.mark.spec("req-github-core-releases-2")
    def test_the_tag_ref_is_joined_when_observed(self) -> None:
        """Both ends carry the commit they resolved to; a re-tag is a query over two fields."""
        tag_ref = f"refs/tags/{_FIRST_RELEASE['tagName']}"
        known = uuid4()
        _, nodes, edges, _ = _collect_releases(
            _collector(config={_REPO: {"releases": _RELEASES}}), ref_uuids={tag_ref: known}
        )
        targets = _edges_of(edges, "TARGETS_REF__github_core")
        assert len(targets) == 1
        assert targets[0]["edge"]["to_entity_id"] == str(known)
        assert targets[0]["edge"]["from_entity_id"] == str(release_id(_REPO, _FIRST_RELEASE["databaseId"]))
        assert targets[0]["edge"]["properties"]["tag_name"] == _FIRST_RELEASE["tagName"]

    @pytest.mark.spec("req-github-core-releases-2")
    def test_an_unobserved_tag_carries_no_edge_and_no_error(self) -> None:
        _, _, edges, _ = _collect_releases(_collector(config={_REPO: {"releases": _RELEASES}}))
        assert not _edges_of(edges, "TARGETS_REF__github_core")


class TestTheProducingRunIsDerived:
    @pytest.mark.spec("req-github-core-releases-3")
    def test_a_tag_push_run_matches_by_tag_ref(self) -> None:
        run = _run(1, head_sha="0" * 40, head_branch=_FIRST_RELEASE["tagName"])
        _, _, edges, _ = _collect_releases(_collector(config={_REPO: {"releases": _RELEASES}}), runs=[run])
        builds = _edges_of(edges, "BUILDS_RELEASE__github_core")
        assert len(builds) == 1
        assert builds[0]["edge"]["from_entity_id"] == str(run["uuid"]), "the run is the initiator; it is the source"
        assert builds[0]["edge"]["properties"]["match_kind"] == "tag_ref"

    @pytest.mark.spec("req-github-core-releases-3")
    def test_a_run_on_the_release_commit_matches_as_same_commit(self) -> None:
        """Weaker, and labelled as such: every workflow that ran on that push shares the commit."""
        run = _run(2, head_sha=_FIRST_RELEASE["tagCommit"]["oid"], head_branch="main")
        _, _, edges, _ = _collect_releases(_collector(config={_REPO: {"releases": _RELEASES}}), runs=[run])
        builds = _edges_of(edges, "BUILDS_RELEASE__github_core")
        assert len(builds) == 1
        assert builds[0]["edge"]["properties"]["match_kind"] == "same_commit"
        assert builds[0]["edge"]["properties"]["head_sha"] == _FIRST_RELEASE["tagCommit"]["oid"]

    @pytest.mark.spec("req-github-core-releases-3")
    def test_a_run_triggered_by_the_release_is_a_consumer_not_a_producer(self) -> None:
        run = _run(
            3, head_sha=_FIRST_RELEASE["tagCommit"]["oid"], head_branch=_FIRST_RELEASE["tagName"], event="release"
        )
        _, _, edges, _ = _collect_releases(_collector(config={_REPO: {"releases": _RELEASES}}), runs=[run])
        assert not _edges_of(edges, "BUILDS_RELEASE__github_core")

    @pytest.mark.spec("req-github-core-releases-3")
    def test_an_unrelated_run_carries_no_edge(self) -> None:
        run = _run(4, head_sha="f" * 40, head_branch="feature/x")
        _, _, edges, _ = _collect_releases(_collector(config={_REPO: {"releases": _RELEASES}}), runs=[run])
        assert not _edges_of(edges, "BUILDS_RELEASE__github_core")


class TestReleasesRefusedIsNotEmpty:
    @pytest.mark.spec("req-github-core-releases-4")
    def test_a_repos_only_scope_is_unobservable_not_zero(self) -> None:
        collector = _collector(config={})
        state, nodes, _, notes = _collect_releases(collector)
        assert state == "unobservable" and not nodes
        assert "releases" in notes
        assert "RELEASES_UNOBSERVABLE" in _codes(collector.warns)  # type: ignore[attr-defined]

    @pytest.mark.spec("req-github-core-releases-4")
    def test_a_degraded_graphql_field_is_unobservable_not_zero(self) -> None:
        """GraphQL answers 200 with the field missing when it is refused; the key is absent."""
        collector = _collector(config={_REPO: {"nameWithOwner": _REPO}})
        state, nodes, _, _ = _collect_releases(collector)
        assert state == "unobservable" and not nodes

    @pytest.mark.spec("req-github-core-releases-4")
    def test_truncation_is_reported_with_the_total(self) -> None:
        capped = {"totalCount": 12, "nodes": _RELEASES["nodes"]}
        collector = _collector(config={_REPO: {"releases": capped}})
        _collect_releases(collector)
        assert "RELEASES_TRUNCATED" in _codes(collector.warns)  # type: ignore[attr-defined]

    def test_the_client_shaper_reports_what_the_cap_dropped(self) -> None:
        releases, missing = GithubGraphQLClient.releases({"releases": {"totalCount": 7, "nodes": _RELEASES["nodes"]}})
        assert len(releases) == 5 and missing == 2


# --------------------------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------------------------

_ARTIFACTS = _FIXTURE["artifacts"]
_FIRST_ARTIFACT = _ARTIFACTS["artifacts"][0]


def _collect_artifacts(collector: GithubCollector, client: _FakeClient, *, runs=None):
    nodes: list[dict] = []
    edges: list[dict] = []
    notes: dict[str, str] = {}
    dims = {**_DIMS, "github.surface": "actions", "github.observation": "execution"}
    state = collector._collect_artifacts(client, _REPO, repository_id(_REPO), dims, runs or [], nodes, edges, notes)
    return state, nodes, edges, notes


class TestArtifactsLand:
    @pytest.mark.spec("req-github-core-artifacts-1")
    def test_an_artifact_becomes_a_node_naming_its_run(self) -> None:
        state, nodes, edges, _ = _collect_artifacts(_collector(), _FakeClient())
        artifacts = _of_type(nodes, "github_core__actions_artifact")
        assert state == "observed"
        assert len(artifacts) == len(_ARTIFACTS["artifacts"]) == 3
        first = artifacts[0]
        assert first["entity"]["entity_id"] == str(actions_artifact_id(_REPO, _FIRST_ARTIFACT["id"]))
        assert first["node"]["run_id"] == _FIRST_ARTIFACT["workflow_run"]["id"]
        assert first["node"]["digest"] == _FIRST_ARTIFACT["digest"]
        assert first["node"]["head_branch"] == _FIRST_ARTIFACT["workflow_run"]["head_branch"]
        assert len(_edges_of(edges, "STORES_ARTIFACT__github_core")) == 3

    @pytest.mark.spec("req-github-core-artifacts-1")
    def test_the_registered_model_accepts_the_captured_shape(self) -> None:
        model = get_model_class("github_core__actions_artifact")
        _, nodes, _, _ = _collect_artifacts(_collector(), _FakeClient())
        emitted = set(_of_type(nodes, "github_core__actions_artifact")[0]["node"])
        assert emitted <= set(model.FIELD_CRUD_SCHEMA), sorted(emitted - set(model.FIELD_CRUD_SCHEMA))

    @pytest.mark.spec("req-github-core-artifacts-2")
    def test_uploads_edge_is_githubs_attribution_and_only_for_runs_in_the_batch(self) -> None:
        in_batch = _run(_FIRST_ARTIFACT["workflow_run"]["id"], head_sha=_FIRST_ARTIFACT["workflow_run"]["head_sha"])
        _, _, edges, _ = _collect_artifacts(_collector(), _FakeClient(), runs=[in_batch])
        uploads = _edges_of(edges, "UPLOADS_ARTIFACT__github_core")
        expected = sum(1 for a in _ARTIFACTS["artifacts"] if a["workflow_run"]["id"] == in_batch["run_id"])
        assert len(uploads) == expected >= 1
        assert uploads[0]["edge"]["from_entity_id"] == str(in_batch["uuid"]), "the run uploaded; it is the source"
        # The ref is a field on the artifact, which IS the event; the edge carries no copy (#55).
        assert uploads[0]["edge"]["properties"] == {}

    @pytest.mark.spec("req-github-core-artifacts-2")
    def test_an_artifact_whose_run_is_outside_the_window_still_lands(self) -> None:
        _, nodes, edges, _ = _collect_artifacts(_collector(), _FakeClient(), runs=[_run(1)])
        assert len(_of_type(nodes, "github_core__actions_artifact")) == 3
        assert not _edges_of(edges, "UPLOADS_ARTIFACT__github_core")

    @pytest.mark.spec("req-github-core-artifacts-3")
    def test_truncation_is_reported_with_githubs_total(self) -> None:
        """3,636 artifacts on one repository at capture; the cap is the rule, not the exception."""
        collector = _collector()
        _collect_artifacts(collector, _FakeClient())
        truncated = [w for w in collector.warns if w[0][1] == "ARTIFACTS_TRUNCATED"]  # type: ignore[attr-defined]
        assert truncated
        assert truncated[0][1]["message_data"]["total"] == _ARTIFACTS["total_count"]


class TestArtifactsRefusedIsNotEmpty:
    @pytest.mark.spec("req-github-core-artifacts-5")
    @pytest.mark.parametrize("status", [403, 404])
    def test_a_refusal_is_unobservable_and_recorded(self, status: int) -> None:
        collector = _collector()
        state, nodes, edges, notes = _collect_artifacts(collector, _FakeClient(artifacts_fail=status))
        assert state == "unobservable" and not nodes and not edges
        assert notes["artifacts"].endswith(str(status))
        assert f"ARTIFACTS_UNREADABLE_{status}" in _codes(collector.warns)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------------------------
# Packages
# --------------------------------------------------------------------------------------------

_PACKAGE = _FIXTURE["package_detail"]
_VERSIONS = _FIXTURE["package_versions"]


def _collect_packages(collector: GithubCollector, client: _FakeClient, *, owner: str | None = _OWNER):
    nodes: list[dict] = []
    edges: list[dict] = []
    state, note = collector._collect_packages(client, owner, nodes, edges)
    return state, note, nodes, edges


class TestPackagesUnderTheAppCredential:
    """What the credential we recommend actually receives, captured 2026-09-02."""

    @pytest.mark.spec("req-github-core-packages-3")
    def test_the_captured_answers_are_unobservable_never_zero(self) -> None:
        """`container` -> 400 while the org's ghcr.io images exist; `npm` -> 200 [] which proves nothing."""
        collector = _collector(has_pat=False)
        state, note, nodes, _ = _collect_packages(collector, _FakeClient())
        assert state == "unobservable" and not nodes
        assert "container: listing answered 400" in note
        assert "npm: empty answer under an App credential" in note
        assert "enabledForGitHubApps: false" in note
        codes = _codes(collector.warns)  # type: ignore[attr-defined]
        assert "PACKAGES_UNREADABLE_400" in codes and "PACKAGES_UNOBSERVABLE" in codes

    @pytest.mark.spec("req-github-core-packages-3")
    def test_every_package_type_is_asked_for_because_the_endpoint_will_not_enumerate_without_one(self) -> None:
        client = _FakeClient()
        _collect_packages(_collector(), client)
        asked = {c[1].get("package_type") for c in client.calls if c[0].endswith("/packages")}
        assert asked == {"container", "npm", "maven", "rubygems", "docker", "nuget"}

    @pytest.mark.spec("req-github-core-packages-3")
    def test_a_non_empty_answer_proves_itself_even_under_the_app(self) -> None:
        """A filtered listing cannot invent a package: presence is proof, absence is not."""
        client = _FakeClient(
            packages={t: [] for t in ("npm", "maven", "rubygems", "docker", "nuget")} | {"container": [_PACKAGE]}
        )
        collector = _collector()
        state, note, nodes, _ = _collect_packages(collector, client)
        assert _of_type(nodes, "github_core__github_package")
        assert state == "unobservable", "the five empty types under an App are still unproven"
        assert "container" not in note

    @pytest.mark.spec("req-github-core-packages-3")
    def test_a_repos_only_scope_is_unobservable(self) -> None:
        state, note, nodes, _ = _collect_packages(_collector(), _FakeClient(), owner=None)
        assert state == "unobservable" and not nodes and "repos-only" in note


class TestPackagesLand:
    """The per-package endpoints DID answer the App token for a public package; the nodes they
    shape are asserted against that capture, driven through a listing that names the package."""

    def _client(self) -> _FakeClient:
        return _FakeClient(
            packages={t: [] for t in ("npm", "maven", "rubygems", "docker", "nuget")} | {"container": [_PACKAGE]}
        )

    @pytest.mark.spec("req-github-core-packages-1")
    def test_a_package_and_its_versions_land_with_purls(self) -> None:
        collector = _collector(pat_client=self._client())
        state, note, nodes, edges = _collect_packages(collector, _FakeClient())
        assert state == "observed" and note == "", "with a token every type answered; the PAT client is routed"
        packages = _of_type(nodes, "github_core__github_package")
        versions = _of_type(nodes, "github_core__github_package_version")
        assert len(packages) == 1 and len(versions) == len(_VERSIONS) >= 5
        pkg = packages[0]
        assert pkg["entity"]["entity_id"] == str(package_id(_OWNER, "container", _PACKAGE["name"]))
        assert pkg["node"]["purl"] == f"pkg:docker/ghcr.io/{_OWNER}/{_PACKAGE['name']}"
        assert pkg["node"]["repository_full_name"] == _PACKAGE["repository"]["full_name"]
        assert pkg["node"]["version_count"] == _PACKAGE["version_count"]
        first = versions[0]
        assert first["entity"]["entity_id"] == str(
            package_version_id(_OWNER, "container", _PACKAGE["name"], _VERSIONS[0]["id"])
        )
        assert first["node"]["version"] == _VERSIONS[0]["name"]
        assert first["node"]["purl"] == f"pkg:docker/ghcr.io/{_OWNER}/{_PACKAGE['name']}@{_VERSIONS[0]['name']}"
        assert first["node"]["container_tags"] == _VERSIONS[0]["metadata"]["container"]["tags"]
        assert len(_edges_of(edges, "PUBLISHES_PACKAGE_VERSION__github_core")) == len(_VERSIONS)

    @pytest.mark.spec("req-github-core-packages-1")
    def test_the_registered_models_accept_the_captured_shape(self) -> None:
        _, _, nodes, _ = _collect_packages(_collector(pat_client=self._client()), _FakeClient())
        for entity_type in ("github_core__github_package", "github_core__github_package_version"):
            declared = set(get_model_class(entity_type).FIELD_CRUD_SCHEMA)
            emitted = set(_of_type(nodes, entity_type)[0]["node"])
            assert emitted <= declared, f"{entity_type}: {sorted(emitted - declared)}"

    @pytest.mark.spec("req-github-core-packages-2")
    def test_owner_edge_always_and_repository_edge_only_when_github_links_a_collected_repo(self) -> None:
        collector = _collector(pat_client=self._client())
        _, _, _, edges = _collect_packages(collector, _FakeClient())
        publishes = _edges_of(edges, "PUBLISHES_PACKAGE__github_core")
        assert [e["edge"]["properties"]["link_kind"] for e in publishes] == ["owner"]
        assert publishes[0]["edge"]["from_entity_id"] == str(account_id(_OWNER))

        collector = _collector(pat_client=self._client())
        collector._repo_envelopes[_PACKAGE["repository"]["full_name"]] = {"node": {}}
        _, _, _, edges = _collect_packages(collector, _FakeClient())
        kinds = sorted(e["edge"]["properties"]["link_kind"] for e in _edges_of(edges, "PUBLISHES_PACKAGE__github_core"))
        assert kinds == ["owner", "repository"]

    @pytest.mark.spec("req-github-core-packages-4")
    def test_builds_edge_is_derived_from_the_sha_tag_convention(self) -> None:
        """A `sha-<short>` tag matching a collected run's head commit joins them; nothing else does."""
        tagged = next(
            v
            for v in _VERSIONS
            if any(t.startswith("sha-") and not t.startswith("sha256-") for t in v["metadata"]["container"]["tags"])
        )
        short = next(
            t for t in tagged["metadata"]["container"]["tags"] if t.startswith("sha-") and not t.startswith("sha256-")
        )[4:]
        run = _run(9, head_sha=short + "a" * (40 - len(short)))
        collector = _collector(pat_client=self._client())
        collector._run_index[_PACKAGE["repository"]["full_name"]] = [run, _run(10, head_sha="b" * 40)]
        _, _, _, edges = _collect_packages(collector, _FakeClient())
        builds = _edges_of(edges, "BUILDS_PACKAGE_VERSION__github_core")
        assert len(builds) == 1
        assert builds[0]["edge"]["from_entity_id"] == str(run["uuid"])
        assert builds[0]["edge"]["to_entity_id"] == str(
            package_version_id(_OWNER, "container", _PACKAGE["name"], tagged["id"])
        )
        assert builds[0]["edge"]["properties"] == {"match_kind": "tag_sha", "attested": None}

    @pytest.mark.spec("req-github-core-packages-4")
    def test_a_version_tagged_only_with_a_digest_carries_no_builds_edge(self) -> None:
        """`sha256-<digest>` is a cosign signature tag, not a commit; it must never match."""
        collector = _collector(pat_client=self._client())
        collector._run_index[_PACKAGE["repository"]["full_name"]] = [
            _run(11, head_sha="0bced15f4d9cef74e3a684859728ffb761e19ee2")
        ]
        _, _, _, edges = _collect_packages(collector, _FakeClient())
        assert not _edges_of(edges, "BUILDS_PACKAGE_VERSION__github_core")

    @pytest.mark.spec("req-github-core-packages-4")
    def test_an_unlinked_package_never_joins_another_repositorys_run(self) -> None:
        """A seven-hex prefix searched org-wide would let one repository's tag join another's run,
        and a false producer edge hides the shape the edge exists to reveal (PR #50 review)."""
        tagged = next(
            v
            for v in _VERSIONS
            if any(t.startswith("sha-") and not t.startswith("sha256-") for t in v["metadata"]["container"]["tags"])
        )
        short = next(
            t for t in tagged["metadata"]["container"]["tags"] if t.startswith("sha-") and not t.startswith("sha256-")
        )[4:]
        unlinked = dict(_PACKAGE, repository=None)
        client = _FakeClient(
            packages={t: [] for t in ("npm", "maven", "rubygems", "docker", "nuget")} | {"container": [unlinked]}
        )
        collector = _collector(pat_client=client)
        collector._run_index["unified-systems-com/other"] = [_run(12, head_sha=short + "a" * (40 - len(short)))]
        _, _, nodes, edges = _collect_packages(collector, _FakeClient())
        assert _of_type(nodes, "github_core__github_package_version"), "the versions still land"
        assert not _edges_of(edges, "BUILDS_PACKAGE_VERSION__github_core")

    @pytest.mark.spec("req-github-core-packages-4")
    def test_a_sha_tag_that_is_not_hex_never_matches(self) -> None:
        version = dict(
            _VERSIONS[0], metadata={"package_type": "container", "container": {"tags": ["sha-release", "sha-abc"]}}
        )
        client = _FakeClient(
            packages={t: [] for t in ("npm", "maven", "rubygems", "docker", "nuget")} | {"container": [_PACKAGE]}
        )
        client.get_paginated = lambda path, params=None, **_: [version] if path.endswith("/versions") else client.__class__.get_paginated(client, path, params)  # type: ignore[method-assign]
        collector = _collector(pat_client=client)
        collector._run_index[_PACKAGE["repository"]["full_name"]] = [_run(13, head_sha="abc" + "0" * 37)]
        _, _, _, edges = _collect_packages(collector, _FakeClient())
        assert not _edges_of(edges, "BUILDS_PACKAGE_VERSION__github_core")

    @pytest.mark.spec("req-github-core-packages-2")
    def test_a_repos_filter_omits_packages_not_linked_to_a_collected_repository(self) -> None:
        """A repo-scoped envelope asked for those repositories' outputs, not the account's inventory."""
        unlinked = dict(_PACKAGE, name="stray", repository=None)
        client = _FakeClient(
            packages={t: [] for t in ("npm", "maven", "rubygems", "docker", "nuget")}
            | {"container": [_PACKAGE, unlinked]}
        )
        collector = _collector(pat_client=client)
        collector._repo_envelopes[_PACKAGE["repository"]["full_name"]] = {"node": {}}
        nodes: list[dict] = []
        edges: list[dict] = []
        state, note = collector._collect_packages(_FakeClient(), _OWNER, nodes, edges, repo_filtered=True)
        names = [n["node"]["name"] for n in _of_type(nodes, "github_core__github_package")]
        assert names == [_PACKAGE["name"]]
        assert state == "observed" and "1 package(s) not linked" in note

    @pytest.mark.spec("req-github-core-packages-5")
    def test_refused_versions_degrade_to_the_package_alone(self) -> None:
        client = _FakeClient(
            packages={t: [] for t in ("npm", "maven", "rubygems", "docker", "nuget")} | {"container": [_PACKAGE]},
            versions_fail=403,
        )
        collector = _collector(pat_client=client)
        _, _, nodes, _ = _collect_packages(collector, _FakeClient())
        assert len(_of_type(nodes, "github_core__github_package")) == 1
        assert not _of_type(nodes, "github_core__github_package_version")
        assert "PACKAGE_VERSIONS_UNREADABLE_403" in _codes(collector.warns)  # type: ignore[attr-defined]

    @pytest.mark.spec("req-github-core-packages-5")
    def test_version_truncation_is_reported_against_githubs_count(self) -> None:
        """1,973 versions on one image at capture; a handful in the fixture."""
        collector = _collector(pat_client=self._client())
        _collect_packages(collector, _FakeClient())
        truncated = [w for w in collector.warns if w[0][1] == "PACKAGE_VERSIONS_TRUNCATED"]  # type: ignore[attr-defined]
        assert truncated and truncated[0][1]["message_data"]["total"] == _PACKAGE["version_count"]


# --------------------------------------------------------------------------------------------
# Identity and purl — pinned literals, because a natural key cannot change once nodes exist
# --------------------------------------------------------------------------------------------


class TestIdentity:
    def test_ids_are_pinned(self) -> None:
        assert str(release_id("o/r", 1)) == "0f8b7e48-9625-5e7e-b96e-8a4addec545e"
        assert str(actions_artifact_id("o/r", 2)) == "07bd16a5-49c1-5a01-b97d-61b2d19d7875"
        assert str(package_id("o", "container", "n")) == "b9a442da-d5c8-5b8c-8953-8a42d1945f4e"
        assert str(package_version_id("o", "container", "n", 3)) == "377f82cf-0697-577e-9716-9ec08f1e7a9d"

    def test_a_package_is_keyed_on_its_path_not_its_numeric_id(self) -> None:
        """Deleted-and-republished under the same name IS the same thing to everything that pulls it."""
        assert str(package_id("o", "container", "n")) == "b9a442da-d5c8-5b8c-8953-8a42d1945f4e"
        assert package_id("o", "container", "n") != package_id("o", "npm", "n")

    @pytest.mark.parametrize(
        ("package_type", "owner", "name", "version", "expected"),
        [
            (
                "container",
                "Unified-Systems-Com",
                "Tap-Web",
                "sha256:ab",
                "pkg:docker/ghcr.io/unified-systems-com/tap-web@sha256:ab",
            ),
            ("container", "o", "n", "", "pkg:docker/ghcr.io/o/n"),
            ("docker", "O", "n", "1", "pkg:docker/docker.pkg.github.com/o/n@1"),
            ("npm", "o", "n", "1.0.0", "pkg:npm/%40o/n@1.0.0?repository_url=npm.pkg.github.com"),
            ("maven", "o", "com.acme.lib", "2", "pkg:maven/com.acme/lib@2?repository_url=maven.pkg.github.com"),
            ("rubygems", "o", "g", "3", "pkg:gem/g@3?repository_url=rubygems.pkg.github.com"),
            ("nuget", "o", "N", "4", "pkg:nuget/N@4?repository_url=nuget.pkg.github.com"),
            ("swift", "o", "n", "5", "pkg:github/o/n@5"),
        ],
    )
    def test_purl_per_package_type(self, package_type: str, owner: str, name: str, version: str, expected: str) -> None:
        assert package_purl(package_type, owner, name, version) == expected


class TestTheRepositoryCarriesTheThreeStates:
    """A property that qualifies an absence belongs on the node the absence is about."""

    def test_the_repository_model_declares_outputs_observability(self) -> None:
        model = get_model_class("github_core__github_repository")
        assert model.FIELD_CRUD_SCHEMA["outputs_observability"] == {"type": "object"}
