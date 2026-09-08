"""Commits after github-core#76: the neutral half is git_core's, the observed half is ours.

The `CommitSlice` parsing (three signature states, identity as observed) is unchanged. What
changed is where the pieces land: intrinsic metadata becomes `git_core__git_commit` under the
global `(sha1, oid)` identity with `STORES_COMMIT` / `RESOLVES_COMMIT`; the logins GitHub
resolved and its signature verdict become `github_core__commit_observation`, keyed per host +
repository stable id + commit, with `OBSERVES_COMMIT` and `OBSERVED_IN_REPOSITORY`.
"""

from __future__ import annotations

import pytest
import tap_plugin.github_core.models as github  # noqa: F401 — trigger model registration
from tap_plugin.git_core.identity import git_commit_id, git_ref_id, git_repository_id
from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector
from tap_plugin.github_core.collectors.github_collector.graphql_client import (
    GithubGraphQLClient,
)
from tap_plugin.github_core.collectors.github_collector.identity import (
    commit_observation_id,
    repository_id,
)

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
    "author": {
        "name": "George Chamales",
        "email": "george@example.com",
        "user": {"login": "notgeorge"},
    },
    "committer": {
        "name": "George Chamales",
        "email": "george@example.com",
        "user": {"login": "notgeorge"},
    },
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

GITHUB_ID = 4242
GIT_REPO = git_repository_id("github.com", str(GITHUB_ID))


class TestIdentity:
    def test_the_observation_is_scoped_by_host_and_stable_repository_id(self) -> None:
        a = commit_observation_id("github.com", GITHUB_ID, "sha1", "A" * 40)
        assert a == commit_observation_id("github.com", GITHUB_ID, "sha1", "a" * 40)
        assert a != commit_observation_id(
            "github.com", GITHUB_ID + 1, "sha1", "a" * 40
        ), "another repository, another record"
        assert a != commit_observation_id("ghe.example", GITHUB_ID, "sha1", "a" * 40)

    def test_the_commit_itself_is_global(self) -> None:
        """One commit node per oid however many repositories store it — the observation is what is scoped."""
        assert git_commit_id("sha1", "a" * 40) == git_commit_id("sha1", "A" * 40)


class TestCommitSlice:
    def test_a_signed_commit_keeps_kind_state_validity_and_signer(self) -> None:
        s = GithubGraphQLClient.commit_slice(_SIGNED, _SIGNED["oid"])
        assert s and s["signature_kind"] == "ssh" and s["signature_state"] == "valid"
        assert (
            s["signature_valid"] is True
            and s["signer_login"] == "notgeorge"
            and s["signed_by_github"] is False
        )

    def test_a_null_signature_is_unsigned_with_a_null_validity_not_false(self) -> None:
        s = GithubGraphQLClient.commit_slice(_UNSIGNED, _UNSIGNED["oid"])
        assert (
            s
            and s["signature_state"] == "unsigned"
            and s["signature_valid"] is None
            and s["signature_kind"] == ""
        )

    def test_an_unresolved_author_is_an_empty_login_not_a_missing_field(self) -> None:
        s = GithubGraphQLClient.commit_slice(_UNSIGNED, _UNSIGNED["oid"])
        assert s and s["author_login"] == "" and s["author_name"] == "Someone"

    def test_a_signature_key_that_was_not_answered_is_unobservable_not_unsigned(
        self,
    ) -> None:
        body = {k: v for k, v in _SIGNED.items() if k != "signature"}
        s = GithubGraphQLClient.commit_slice(body, body["oid"])
        assert (
            s
            and s["signature_state"] == "unobservable"
            and s["signature_valid"] is None
        )

    def test_a_body_without_the_fragment_is_no_slice_at_all(self) -> None:
        assert GithubGraphQLClient.commit_slice({"oid": "d" * 40}, "d" * 40) is None


def _repo_node() -> dict:
    return {
        "databaseId": GITHUB_ID,
        "defaultBranchRef": {"name": "main"},
        "branchRefs": {
            "totalCount": 2,
            "nodes": [
                {"name": "main", "target": {"__typename": "Commit", **_SIGNED}},
                {
                    "name": "topic",
                    "target": {"__typename": "Commit", "oid": "f" * 40},
                },  # no slice: degraded
            ],
        },
        "tagRefs": {
            "totalCount": 2,
            "nodes": [
                {"name": "v1", "target": {"__typename": "Commit", **_UNSIGNED}},
                {
                    "name": "v2",
                    "target": {
                        "__typename": "Tag",
                        "oid": "1" * 40,
                        "target": {"__typename": "Commit", **_SIGNED},
                    },
                },
            ],
        },
    }


class TestEmission:
    @staticmethod
    def _collector() -> GithubCollector:
        c = GithubCollector.__new__(GithubCollector)
        c._config = {"acme/widget": _repo_node()}
        c._default_refs = set()
        c.record_warn = lambda *a, **k: None  # type: ignore[method-assign]
        return c

    @staticmethod
    def _emit(c: GithubCollector) -> tuple[list[dict], list[dict]]:
        nodes: list[dict] = []
        edges: list[dict] = []
        dims = {
            "github.platform": "github.com",
            "github.owner": "acme",
            "github.repo": "widget",
        }
        c._emit_refs(
            "acme/widget",
            repository_id("acme/widget"),
            GIT_REPO,
            GITHUB_ID,
            dims,
            nodes,
            edges,
        )
        return nodes, edges

    def test_one_neutral_commit_per_oid_and_the_resolve_edge_from_each_ref(
        self,
    ) -> None:
        nodes, edges = self._emit(self._collector())
        commits = [
            n for n in nodes if n["entity"]["entity_type"] == "git_core__git_commit"
        ]
        resolves = [
            e for e in edges if e["edge"]["edge_type"] == "RESOLVES_COMMIT__git_core"
        ]
        # main and v2 share one commit (emitted twice, same id — collapse keeps one); v1 is
        # another; topic carried no slice and gets neither node nor edge.
        assert {n["entity"]["entity_id"] for n in commits} == {
            str(git_commit_id("sha1", "e" * 40)),
            str(git_commit_id("sha1", "c" * 40)),
        }
        assert len(resolves) == 3
        assert {e["edge"]["from_entity_id"] for e in resolves} == {
            str(git_ref_id(GIT_REPO, "refs/heads/main")),
            str(git_ref_id(GIT_REPO, "refs/tags/v1")),
            str(git_ref_id(GIT_REPO, "refs/tags/v2")),
        }

    def test_the_neutral_rows_carry_only_git_core_dimensions_and_intrinsic_fields(
        self,
    ) -> None:
        nodes, edges = self._emit(self._collector())
        ref = next(
            n for n in nodes if n["entity"]["entity_type"] == "git_core__git_ref"
        )
        assert (
            ref["entity"]["dimensions"] == {"git.object": "ref"}
            and "full_name" not in ref["node"]
        )
        commit = next(
            n for n in nodes if n["entity"]["entity_type"] == "git_core__git_commit"
        )
        assert commit["entity"]["dimensions"] == {"git.object": "commit"}
        assert set(commit["node"]) == {
            "hash_algorithm",
            "oid",
            "authored_date",
            "committed_date",
            "author_name",
            "author_email",
            "committer_name",
            "committer_email",
        }
        declares = [
            e for e in edges if e["edge"]["edge_type"] == "DECLARES_REF__git_core"
        ]
        stores = [
            e for e in edges if e["edge"]["edge_type"] == "STORES_COMMIT__git_core"
        ]
        assert len(declares) == 4 and all(
            e["edge"]["from_entity_id"] == str(GIT_REPO) for e in declares
        )
        assert {e["edge"]["to_entity_id"] for e in stores} == {
            n["entity"]["entity_id"]
            for n in nodes
            if n["entity"]["entity_type"] == "git_core__git_commit"
        }

    def test_the_observation_carries_the_github_facts_and_is_repository_scoped(
        self,
    ) -> None:
        nodes, edges = self._emit(self._collector())
        obs = [
            n
            for n in nodes
            if n["entity"]["entity_type"] == "github_core__commit_observation"
        ]
        signed = next(n for n in obs if n["node"]["sha"] == "e" * 40)
        assert signed["entity"]["entity_id"] == str(
            commit_observation_id("github.com", GITHUB_ID, "sha1", "e" * 40)
        )
        assert (
            signed["node"]["full_name"] == "acme/widget"
            and signed["node"]["repository_github_id"] == GITHUB_ID
        )
        assert (
            signed["node"]["author_login"] == "notgeorge"
            and signed["node"]["signature_state"] == "valid"
        )
        assert (
            signed["entity"]["dimensions"]["github.repo"] == "widget"
            and signed["entity"]["dimensions"]["github.surface"] == "git"
        )
        observes = next(
            e
            for e in edges
            if e["edge"]["edge_type"] == "OBSERVES_COMMIT__github_core"
            and e["edge"]["from_entity_id"] == signed["entity"]["entity_id"]
        )
        assert observes["edge"]["to_entity_id"] == str(git_commit_id("sha1", "e" * 40))
        observed_in = next(
            e
            for e in edges
            if e["edge"]["edge_type"] == "OBSERVED_IN_REPOSITORY__github_core"
            and e["edge"]["from_entity_id"] == signed["entity"]["entity_id"]
        )
        assert observed_in["edge"]["to_entity_id"] == str(repository_id("acme/widget"))

    def test_no_neutral_repository_means_no_refs_and_no_commits(self) -> None:
        nodes: list[dict] = []
        edges: list[dict] = []
        c = self._collector()
        out = c._emit_refs(
            "acme/widget",
            repository_id("acme/widget"),
            None,
            None,
            {"github.platform": "github.com"},
            nodes,
            edges,
        )
        assert out == {} and nodes == [] and edges == []


@pytest.mark.django_db
class TestModelAndEdge:
    def test_unsigned_stores_a_null_validity(self) -> None:
        obs = _create(
            "github_core__commit_observation",
            {
                "full_name": "o/r",
                "sha": "c" * 40,
                "signature_state": "unsigned",
                "signature_valid": None,
            },
        )
        assert obs.signature_valid is None and obs.get_name() == "o/r@" + "c" * 12

    def test_signature_kind_is_constrained_and_sha_must_be_lower_case(self) -> None:
        assert not create_node(
            "github_core__commit_observation",
            {"full_name": "o/r", "sha": "c" * 40, "signature_kind": "pgp"},
        ).success
        assert not create_node(
            "github_core__commit_observation", {"full_name": "o/r", "sha": "C" * 40}
        ).success

    def test_observes_commit_is_observation_to_commit_and_property_free(self) -> None:
        obs = _create("github_core__commit_observation", {"full_name": "o/r", "sha": "c" * 40})
        commit = _create("git_core__git_commit", {"hash_algorithm": "sha1", "oid": "c" * 40})
        edge = create_edge(obs.entity, commit.entity, "OBSERVES_COMMIT__github_core", {})
        assert edge.edge_type == "OBSERVES_COMMIT__github_core" and edge.properties == {}
        assert edge.from_entity_id == obs.entity_id and edge.to_entity_id == commit.entity_id
