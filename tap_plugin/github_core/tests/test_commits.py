"""Commits: the ref/signature convergence, sliced (req-github-core-commits).

Spec: plugins/github_core/specs/spec-github-core-v0.md (req-github-core-commits)
Issue: unified-systems-com/tap-plugin-github-core#57

Shapes are the ones an executed call returned on 2026-09-02 (`gh api graphql`,
unified-systems-com/tap): a signed SSH head with a resolved signer, and `signature: null` on an
unsigned one. The tests that matter are about the null: it is an observed value, and it must
land as `unsigned` with a null validity, never as false, and never as a node of empty strings
when the field was not answered at all.
"""

from __future__ import annotations

import pytest
import tap_plugin.github_core.models as github  # noqa: F401 — trigger model registration
from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector
from tap_plugin.github_core.collectors.github_collector.graphql_client import GithubGraphQLClient
from tap_plugin.github_core.collectors.github_collector.identity import git_commit_id, git_ref_id

from tap_grid.models import Entity
from tap_grid.registry import get_model_class
from tap_grid.services import create_edge, create_node


def _create(type_slug: str, payload: dict):
    result = create_node(type_slug, payload)
    assert result.success, f"create_node failed: {result.errors}"
    entity = Entity.objects.get(pk=result.entity_id)
    return get_model_class(type_slug).objects.get(entity=entity)


_SIGNED = {
    "oid": "e" * 40,
    "committedDate": "2026-08-31T17:46:58Z",
    "authoredDate": "2026-08-31T17:46:58Z",
    "author": {"name": "George Chamales", "email": "george@example.com", "user": {"login": "notgeorge"}},
    "committer": {"name": "George Chamales", "email": "george@example.com", "user": {"login": "notgeorge"}},
    "signature": {
        "__typename": "SshSignature",
        "isValid": True,
        "state": "VALID",
        "wasSignedByGitHub": False,
        "signer": {"login": "notgeorge"},
    },
}
_UNSIGNED = {
    "oid": "c" * 40,
    "committedDate": "2026-08-09T15:33:22Z",
    "authoredDate": "2026-08-09T15:33:22Z",
    "author": {"name": "Someone", "email": "nobody@example.com", "user": None},
    "committer": {"name": "Someone", "email": "nobody@example.com", "user": None},
    "signature": None,
}


class TestIdentity:
    def test_pinned_to_a_literal_and_case_insensitive(self) -> None:
        assert str(git_commit_id("A" * 40)) == str(git_commit_id("a" * 40))
        assert git_commit_id.__code__.co_argcount == 1, "the key is the SHA alone — no repository parameter"


class TestCommitSlice:
    def test_a_signed_commit_keeps_kind_state_validity_and_signer(self) -> None:
        slice_ = GithubGraphQLClient.commit_slice(_SIGNED, "e" * 40)
        assert slice_ is not None
        assert slice_["signature_kind"] == "ssh" and slice_["signature_state"] == "valid"
        assert slice_["signature_valid"] is True and slice_["signer_login"] == "notgeorge"
        assert slice_["signed_by_github"] is False
        assert slice_["author_login"] == "notgeorge" and slice_["committer_email"] == "george@example.com"

    def test_a_null_signature_is_unsigned_with_a_null_validity_not_false(self) -> None:
        """`signature: null` is an answer. "Not valid" would be a claim about a signature that
        does not exist."""
        slice_ = GithubGraphQLClient.commit_slice(_UNSIGNED, "c" * 40)
        assert slice_ is not None
        assert slice_["signature_state"] == "unsigned"
        assert slice_["signature_valid"] is None
        assert slice_["signature_kind"] == "" and slice_["signer_login"] == ""

    def test_an_unresolved_author_is_an_empty_login_not_a_missing_field(self) -> None:
        slice_ = GithubGraphQLClient.commit_slice(_UNSIGNED, "c" * 40)
        assert slice_ is not None
        assert slice_["author_login"] == "" and slice_["author_name"] == "Someone"

    def test_a_body_without_the_fragment_is_no_slice_at_all(self) -> None:
        """A degraded field, or a stub without the fragment: nothing to observe, so None —
        never a node of empty strings that would read as an unsigned commit by nobody."""
        assert GithubGraphQLClient.commit_slice({"oid": "a" * 40}, "a" * 40) is None
        assert GithubGraphQLClient.commit_slice(_SIGNED, "") is None


def _repo_node() -> dict:
    return {
        "nameWithOwner": "acme/widget",
        "defaultBranchRef": {"name": "main", "target": {"oid": "e" * 40}},
        "branchRefs": {
            "totalCount": 2,
            "nodes": [
                {"name": "main", "target": _SIGNED},
                {"name": "topic", "target": {"oid": "b" * 40}},
            ],
        },
        "tagRefs": {
            "totalCount": 2,
            "nodes": [
                {"name": "v1", "target": {**_UNSIGNED, "__typename": "Commit"}},
                {"name": "v2", "target": {"oid": "d" * 40, "__typename": "Tag", "target": _SIGNED}},
            ],
        },
    }


class TestRefShaping:
    def test_the_commit_rides_the_ref_and_an_annotated_tag_reaches_the_nested_commit(self) -> None:
        refs = {r["ref"]: r for r in GithubGraphQLClient.refs(_repo_node())[0]}
        assert refs["refs/heads/main"]["commit"]["sha"] == "e" * 40
        assert refs["refs/tags/v2"]["commit"]["sha"] == "e" * 40  # the commit, not the tag object
        assert refs["refs/tags/v2"]["target_sha"] == "d" * 40
        assert refs["refs/tags/v1"]["commit"]["signature_state"] == "unsigned"

    def test_a_ref_whose_commit_was_not_answered_carries_none(self) -> None:
        refs = {r["ref"]: r for r in GithubGraphQLClient.refs(_repo_node())[0]}
        assert refs["refs/heads/topic"]["commit"] is None


class TestEmission:
    @staticmethod
    def _collector() -> GithubCollector:
        c = GithubCollector.__new__(GithubCollector)
        c._config = {"acme/widget": _repo_node()}
        c._default_refs = set()
        c.record_warn = lambda *a, **k: None  # type: ignore[method-assign]
        return c

    def test_one_commit_node_per_sha_and_a_points_at_from_each_ref(self) -> None:
        nodes: list[dict] = []
        edges: list[dict] = []
        c = self._collector()
        c._emit_refs("acme/widget", git_ref_id("acme/widget", "x"), {"github.platform": "github.com",
                     "github.owner": "acme", "github.repo": "widget"}, nodes, edges)
        commits = [n for n in nodes if n["entity"]["entity_type"] == "github_core__git_commit"]
        points = [e for e in edges if e["edge"]["edge_type"] == "POINTS_AT__github_core"]
        # main and v2 share one commit (emitted twice, same id — collapse keeps one); v1 is
        # another; topic carried no slice and gets neither node nor edge.
        assert {n["entity"]["entity_id"] for n in commits} == {str(git_commit_id("e" * 40)), str(git_commit_id("c" * 40))}
        assert len(points) == 3
        assert {e["edge"]["from_entity_id"] for e in points} == {
            str(git_ref_id("acme/widget", "refs/heads/main")),
            str(git_ref_id("acme/widget", "refs/tags/v1")),
            str(git_ref_id("acme/widget", "refs/tags/v2")),
        }

    def test_the_commit_node_carries_no_repository_and_the_edge_does(self) -> None:
        nodes: list[dict] = []
        edges: list[dict] = []
        c = self._collector()
        c._emit_refs("acme/widget", git_ref_id("acme/widget", "x"), {"github.platform": "github.com",
                     "github.owner": "acme", "github.repo": "widget"}, nodes, edges)
        commit = next(n for n in nodes if n["entity"]["entity_type"] == "github_core__git_commit")
        assert "github.repo" not in commit["entity"]["dimensions"]
        assert commit["entity"]["dimensions"]["github.surface"] == "git"
        edge = next(e for e in edges if e["edge"]["edge_type"] == "POINTS_AT__github_core")
        assert edge["entity"]["dimensions"]["github.repo"] == "widget"


@pytest.mark.django_db
class TestModelAndEdge:
    def test_unsigned_stores_a_null_validity(self) -> None:
        commit = _create("github_core__git_commit", {"sha": "c" * 40, "signature_state": "unsigned", "signature_valid": None})
        assert commit.signature_valid is None and commit.get_name() == "c" * 12

    def test_signature_kind_is_constrained(self) -> None:
        assert not create_node("github_core__git_commit", {"sha": "c" * 40, "signature_kind": "pgp"}).success

    def test_points_at_is_ref_to_commit_and_property_free(self) -> None:
        ref = _create("github_core__git_ref", {"full_name": "o/r", "ref": "refs/heads/main", "ref_type": "branch"})
        commit = _create("github_core__git_commit", {"sha": "e" * 40})
        edge = create_edge(ref.entity, commit.entity, "POINTS_AT__github_core")
        assert edge.properties == {}
