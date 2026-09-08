"""Pull requests: the proposal and what stands between it and merge (github-core#82;
req-github-core-pull-requests).

The fixture (`tests/fixtures/pull_requests.json`) is **captured from the live GraphQL API**
(2026-09-08, `unified-systems-com/tap`, a representative window: one pull request per state and
rollup state, `totalCount` preserved) plus two rows derived from the captured shape and
labelled SYNTHETIC: a fork head with no rollup, and a Bot author.

What matters most is what an EMPTY answer means: a degraded `pullRequests` field, a head with no
rollup, a window smaller than the total. None of them may render as "nothing there" or "green".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import tap_plugin.github_core.models as github  # noqa: F401 — trigger model registration
from tap_plugin.git_core.identity import git_commit_id, git_ref_id, git_repository_id
from tap_plugin.github_core.collectors.github_collector.batch import node_envelope
from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector
from tap_plugin.github_core.collectors.github_collector.graphql_client import GithubGraphQLClient
from tap_plugin.github_core.collectors.github_collector.identity import (
    account_id,
    github_app_id,
    pull_request_id,
    release_id,
    repository_id,
)
from tap_plugin.github_core.models.pull_request import PullRequest

from tap_grid.services import create_node

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "pull_requests.json").read_text())
_REPO = "unified-systems-com/tap"
_OWNER = "unified-systems-com"
_TYPE = "github_core__pull_request"
_DIMS = {"github.platform": "github.com", "github.owner": _OWNER, "github.repo": "tap"}
_GIT_REPO = git_repository_id("github.com", "123456789")
_FORK_PR = 99001
_BOT_PR = 99002


def _repo_envelope(full_name: str = _REPO) -> dict[str, Any]:
    return node_envelope(
        entity_id=repository_id(full_name),
        entity_type="github_core__github_repository",
        name=full_name,
        dimensions=_DIMS,
        fields={"full_name": full_name, "pull_requests_observability": "", "pull_requests_total": None},
    )


def _collector(config: dict[str, Any] | None = None):
    # `__new__` rather than `__init__`: CollectorBase wants a runtime config we do not need to
    # exercise a pure emitter. Matches the pattern in test_outputs.
    collector = GithubCollector.__new__(GithubCollector)
    collector._config = {_REPO: json.loads(json.dumps(_FIXTURE["repository"]))} if config is None else config  # type: ignore[misc]
    collector._emitted_actor_logins = set()
    collector._emitted_app_ids = set()
    warns: list[tuple[Any, ...]] = []
    infos: list[tuple[Any, ...]] = []
    collector.record_warn = lambda *a, **k: warns.append((a, k))  # type: ignore[method-assign]
    collector.record_info = lambda *a, **k: infos.append((a, k))  # type: ignore[method-assign]
    collector.warns = warns  # type: ignore[attr-defined]
    collector.infos = infos  # type: ignore[attr-defined]
    return collector


def _emit(collector: GithubCollector, *, git_repo_uuid: Any = _GIT_REPO, full_name: str = _REPO):
    envelope = _repo_envelope(full_name)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    collector._emit_pull_requests(full_name, envelope, git_repo_uuid, _DIMS, nodes, edges)
    return envelope["node"], nodes, edges


def _pulls(nodes: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {n["node"]["number"]: n for n in nodes if n["entity"]["entity_type"] == _TYPE}


def _edges_of(edges: list[dict[str, Any]], slug: str) -> list[dict[str, Any]]:
    return [e for e in edges if e["edge"]["edge_type"] == slug]


def _codes(records: list[tuple[Any, ...]]) -> list[str]:
    return [a[1] for a, _ in records]


def _captured(author_type: str | None = None) -> dict[str, Any]:
    """The first captured (non-synthetic) row, as the parser shapes it — optionally by author kind."""
    parsed = GithubGraphQLClient.pull_requests(_FIXTURE["repository"])
    assert parsed is not None
    return next(p for p in parsed[0] if p["number"] < 90000 and (author_type is None or p["author_type"] == author_type))


#: Captured rows (one per state x rollup state on the wire that day) plus the two synthetic ones.
_ROWS = len(_FIXTURE["repository"]["pullRequests"]["nodes"])


@pytest.mark.spec("req-github-core-pull-requests-1")
class TestPullRequestsLand:
    def test_one_node_per_pull_request_keyed_on_base_repository_and_number(self) -> None:
        node, nodes, _ = _emit(_collector())
        pulls = _pulls(nodes)
        assert len(pulls) == _ROWS == 5  # three captured + two synthetic
        first = _captured()
        envelope = pulls[first["number"]]
        assert envelope["entity"]["entity_id"] == str(pull_request_id(_REPO, first["number"]))
        assert envelope["entity"]["name"] == f"{_REPO}#{first['number']}"
        assert envelope["entity"]["dimensions"]["github.surface"] == "pulls"
        assert envelope["entity"]["dimensions"]["github.observation"] == "execution"
        fields = envelope["node"]
        assert fields["full_name"] == _REPO
        assert fields["state"] in PullRequest.STATES
        assert fields["head_ref"] and len(fields["head_sha"]) == 40
        assert fields["base_ref"] == "main"
        assert fields["mergeable"] in {"MERGEABLE", "CONFLICTING", "UNKNOWN"}
        assert fields["review_decision"] in {"APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED", ""}
        assert isinstance(fields["review_requests"], list) and isinstance(fields["latest_reviews"], list)
        assert node["pull_requests_observability"] == "observed"
        assert node["pull_requests_total"] == _FIXTURE["repository"]["pullRequests"]["totalCount"]

    def test_the_captured_states_are_all_present(self) -> None:
        _, nodes, _ = _emit(_collector())
        states = {n["node"]["state"] for n in _pulls(nodes).values()}
        assert {"OPEN", "MERGED", "CLOSED"} <= states

    @pytest.mark.django_db
    def test_the_registered_model_accepts_the_captured_shape(self) -> None:
        _, nodes, _ = _emit(_collector())
        first = _captured()
        result = create_node(_TYPE, _pulls(nodes)[first["number"]]["node"])
        assert result.success, result.errors
        row = PullRequest.objects.get(entity_id=result.entity_id)
        row.entity.refresh_from_db()
        assert row.entity.name == f"{_REPO}#{first['number']}"
        assert row.number == first["number"]
        assert row.checks_rollup_state == first["checks_rollup_state"]

    def test_identity_is_deterministic_and_scoped_to_the_base_repository(self) -> None:
        minted = pull_request_id(_REPO, 338)
        assert pull_request_id("unified-systems-com/tap", "338") == minted
        assert pull_request_id("unified-systems-com/git-serious-tap", 338) != minted
        assert pull_request_id(_REPO, 338) != release_id(_REPO, 338)


@pytest.mark.spec("req-github-core-pull-requests-2")
class TestJoinedToTheNeutralNodes:
    def test_head_and_base_refs_and_head_commit_are_edges_onto_git_core_ids(self) -> None:
        _, nodes, edges = _emit(_collector())
        first = _captured()
        pr_uuid = str(pull_request_id(_REPO, first["number"]))
        proposes = [e for e in _edges_of(edges, "PROPOSES_REF__github_core") if e["edge"]["from_entity_id"] == pr_uuid]
        assert len(proposes) == 1
        assert proposes[0]["edge"]["to_entity_id"] == str(git_ref_id(_GIT_REPO, f"refs/heads/{first['head_ref']}"))
        assert proposes[0]["edge"]["properties"] == {"ref_name": first["head_ref"]}
        base = [e for e in _edges_of(edges, "TARGETS_BASE_REF__github_core") if e["edge"]["from_entity_id"] == pr_uuid]
        assert base[0]["edge"]["to_entity_id"] == str(git_ref_id(_GIT_REPO, "refs/heads/main"))
        assert base[0]["edge"]["properties"] == {"ref_name": "main"}
        commit = [e for e in _edges_of(edges, "PROPOSES_COMMIT__github_core") if e["edge"]["from_entity_id"] == pr_uuid]
        assert commit[0]["edge"]["to_entity_id"] == str(git_commit_id("sha1", first["head_sha"]))
        assert commit[0]["edge"]["properties"] == {}

    def test_a_fork_head_draws_no_ref_edge_and_keeps_the_fact_on_the_node(self) -> None:
        _, nodes, edges = _emit(_collector())
        fork = _pulls(nodes)[_FORK_PR]["node"]
        assert fork["head_repository"] == "someone-else/tap"
        pr_uuid = str(pull_request_id(_REPO, _FORK_PR))
        assert [e for e in _edges_of(edges, "PROPOSES_REF__github_core") if e["edge"]["from_entity_id"] == pr_uuid] == []
        # The base is ours, so that edge still exists; the head commit edge is computed and may dangle.
        assert [e for e in _edges_of(edges, "TARGETS_BASE_REF__github_core") if e["edge"]["from_entity_id"] == pr_uuid]

    def test_without_a_neutral_repository_no_ref_edge_is_computed(self) -> None:
        _, _, edges = _emit(_collector(), git_repo_uuid=None)
        assert _edges_of(edges, "PROPOSES_REF__github_core") == []
        assert _edges_of(edges, "TARGETS_BASE_REF__github_core") == []
        # The commit identity is global, so the commit edge still is.
        assert _edges_of(edges, "PROPOSES_COMMIT__github_core")


@pytest.mark.spec("req-github-core-pull-requests-3")
class TestAuthorIsAnEdge:
    def test_a_user_author_is_an_account_with_the_association_on_the_edge(self) -> None:
        _, nodes, edges = _emit(_collector())
        first = _captured("User")
        pr_uuid = str(pull_request_id(_REPO, first["number"]))
        opens = [e for e in _edges_of(edges, "OPENS_PULL_REQUEST__github_core") if e["edge"]["to_entity_id"] == pr_uuid]
        assert len(opens) == 1
        assert opens[0]["edge"]["from_entity_id"] == str(account_id(first["author_login"]))
        assert opens[0]["edge"]["properties"] == {"author_association": first["author_association"]}
        accounts = [n for n in nodes if n["entity"]["entity_type"] == "github_core__github_account"]
        assert {a["node"]["login"] for a in accounts} >= {first["author_login"], "outsider"}
        assert next(a for a in accounts if a["node"]["login"] == first["author_login"])["node"]["account_type"] == "User"

    def test_a_bot_author_is_the_app(self) -> None:
        _, nodes, edges = _emit(_collector())
        pr_uuid = str(pull_request_id(_REPO, _BOT_PR))
        opens = [e for e in _edges_of(edges, "OPENS_PULL_REQUEST__github_core") if e["edge"]["to_entity_id"] == pr_uuid]
        assert opens[0]["edge"]["from_entity_id"] == str(github_app_id("renovate"))
        apps = [n for n in nodes if n["entity"]["entity_type"] == "github_core__github_app"]
        slugs = [a["node"]["slug"] for a in apps]
        assert "renovate" in slugs
        # The captured window's own bots (tap-renovate, tap-release-please) are Apps too, once each.
        assert len(slugs) == len(set(slugs))
        assert {a["entity"]["dimensions"]["github.surface"] for a in apps} == {"apps"}

    def test_an_author_is_minted_once_across_pull_requests(self) -> None:
        _, nodes, _ = _emit(_collector())
        accounts = [n["node"]["login"] for n in nodes if n["entity"]["entity_type"] == "github_core__github_account"]
        assert len(accounts) == len(set(accounts))


@pytest.mark.spec("req-github-core-pull-requests-4")
class TestBuildStatusFromTheRollup:
    def test_the_rollup_state_and_its_contexts_ride_the_node(self) -> None:
        _, nodes, _ = _emit(_collector())
        first = _captured()
        fields = _pulls(nodes)[first["number"]]["node"]
        assert fields["checks_rollup_state"] in {"SUCCESS", "FAILURE", "PENDING", "ERROR", "EXPECTED"}
        assert fields["checks"], "the captured head carries check runs"
        run = next(c for c in fields["checks"] if c["kind"] == "check_run")
        assert set(run) == {"kind", "check_run_id", "name", "status", "conclusion", "app", "url"}
        assert run["app"] == "github-actions"
        assert isinstance(run["check_run_id"], int)  # the rerun-dedupe key: a rerun mints a higher id
        ids = [c["check_run_id"] for c in fields["checks"] if c["kind"] == "check_run"]
        assert len(ids) == len(set(ids))
        assert fields["configuration"]["checks_total"] == len(fields["checks"])
        assert fields["configuration"]["checks_truncated"] is False

    def test_a_head_with_no_rollup_is_blank_not_green(self) -> None:
        _, nodes, _ = _emit(_collector())
        fork = _pulls(nodes)[_FORK_PR]["node"]
        assert fork["checks_rollup_state"] == ""
        assert fork["checks"] == []
        assert fork["configuration"]["checks_total"] is None

    def test_a_failed_and_a_succeeded_rollup_are_both_captured(self) -> None:
        _, nodes, _ = _emit(_collector())
        states = {n["node"]["checks_rollup_state"] for n in _pulls(nodes).values()}
        assert {"SUCCESS", "FAILURE"} <= states

    def test_a_commit_status_context_is_kept_beside_check_runs(self) -> None:
        status = GithubGraphQLClient._check_context(
            {"__typename": "StatusContext", "context": "Codacy Static Code Analysis", "state": "SUCCESS",
             "targetUrl": "https://app.codacy.com/x", "creator": {"login": "codacy-production"}}
        )
        assert status == {
            "kind": "status", "check_run_id": None, "name": "Codacy Static Code Analysis", "status": "COMPLETED",
            "conclusion": "SUCCESS", "app": "codacy-production", "url": "https://app.codacy.com/x",
        }

    def test_a_rollup_wider_than_the_page_is_marked_truncated(self) -> None:
        repo = json.loads(json.dumps(_FIXTURE["repository"]))
        first = next(n for n in repo["pullRequests"]["nodes"] if n["number"] < 90000)
        first["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["totalCount"] = 250
        collector = _collector({_REPO: repo})
        _, nodes, _ = _emit(collector)
        fields = _pulls(nodes)[first["number"]]["node"]
        assert fields["configuration"] == {"checks_total": 250, "checks_truncated": True}
        assert "PULL_REQUEST_CHECKS_TRUNCATED" in _codes(collector.warns)


@pytest.mark.spec("req-github-core-pull-requests-5")
class TestRefusedIsNotEmpty:
    def test_a_degraded_field_is_unobservable_with_a_warning_and_nothing_minted(self) -> None:
        repo = json.loads(json.dumps(_FIXTURE["repository"]))
        del repo["pullRequests"]  # what prune_errored_paths leaves behind
        collector = _collector({_REPO: repo})
        node, nodes, edges = _emit(collector)
        assert node["pull_requests_observability"] == "unobservable"
        assert node["pull_requests_total"] is None
        assert nodes == [] and edges == []
        assert _codes(collector.warns) == ["PULL_REQUESTS_UNOBSERVABLE"]
        assert collector.infos == []

    def test_a_repos_only_scope_never_asked(self) -> None:
        collector = _collector({})
        node, nodes, _ = _emit(collector)
        assert node["pull_requests_observability"] == ""
        assert nodes == []
        assert collector.warns == [] and collector.infos == []

    def test_an_answered_field_with_no_rows_is_a_fact(self) -> None:
        collector = _collector({_REPO: {"pullRequests": {"totalCount": 0, "nodes": []}}})
        node, nodes, _ = _emit(collector)
        assert node["pull_requests_observability"] == "observed"
        assert node["pull_requests_total"] == 0
        assert nodes == []
        assert _codes(collector.infos) == ["PULL_REQUESTS_COLLECTED"]

    def test_the_window_cap_is_visible_as_the_total_and_a_warning(self) -> None:
        collector = _collector()
        node, nodes, _ = _emit(collector)
        total = _FIXTURE["repository"]["pullRequests"]["totalCount"]
        assert node["pull_requests_total"] == total > len(_pulls(nodes))
        assert "PULL_REQUESTS_TRUNCATED" in _codes(collector.warns)
        truncated = next(k for a, k in collector.warns if a[1] == "PULL_REQUESTS_TRUNCATED")
        assert truncated["message_data"]["missing"] == total - _ROWS
        summary = next(k for a, k in collector.infos if a[1] == "PULL_REQUESTS_COLLECTED")
        assert summary["message_data"]["total"] == total


class TestTheParserSaysWhatTheBlankMeans:
    def test_a_missing_field_is_none_and_an_empty_connection_is_a_list(self) -> None:
        assert GithubGraphQLClient.pull_requests({}) is None
        assert GithubGraphQLClient.pull_requests({"pullRequests": None}) == ([], 0)

    def test_mergeable_is_kept_as_githubs_string(self) -> None:
        parsed = GithubGraphQLClient.pull_requests(_FIXTURE["repository"])
        assert parsed is not None
        assert {p["mergeable"] for p in parsed[0]} <= {"MERGEABLE", "CONFLICTING", "UNKNOWN", ""}
        assert all(isinstance(p["mergeable"], str) for p in parsed[0])
