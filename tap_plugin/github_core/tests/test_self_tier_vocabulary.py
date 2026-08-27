"""The self-tier vocabulary: declared jobs, refs, rulesets, environments, caches, installations.

Spec: plugins/github_core/specs/spec-github-core-v0.md
      (req-github-core-declared-jobs, -refs, -rulesets, -environments, -caches,
       -app-installations, -app-auth)
Design record: plugins/github_core/specs/spec-github-core-vocabulary.md

The tests that matter most here are the ones about what an EMPTY answer means. A ruleset with no
visible bypass actors, a repository with no visible tags, an App inventory collected by a token
that cannot see one — each is a blank that reads as reassurance and must not.
"""

from __future__ import annotations

import json

import pytest
import tap_plugin.github_core.models as github  # noqa: F401 — trigger model registration
from tap_plugin.github_core.collectors.github_collector.auth import MODE_APP, MODE_PAT, GithubAuth
from tap_plugin.github_core.collectors.github_collector.app_jwt import GithubAppAuthError
from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector
from tap_plugin.github_core.collectors.github_collector.graphql_client import GithubGraphQLClient
from tap_plugin.github_core.collectors.github_collector.identity import (
    actions_cache_id,
    app_installation_id,
    environment_id,
    git_ref_id,
    ruleset_id,
    workflow_job_id,
)
from tap_plugin.github_core.collectors.github_collector.manifest import load_collection_manifest
from tap_plugin.github_core.collectors.github_collector.parser import parse_workflow_yaml
from tap_plugin.github_core.collectors.github_collector.secret import (
    GITHUB_APP_SCHEMA,
    SCHEMA_BY_KIND,
)

from tap_grid.models import Entity
from tap_grid.registry import get_model_class
from tap_grid.services import create_node


def _create(type_slug: str, payload: dict):
    result = create_node(type_slug, payload)
    assert result.success, f"create_node failed: {result.errors}"
    entity = Entity.objects.get(pk=result.entity_id)
    return get_model_class(type_slug).objects.get(entity=entity)


# --------------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------------


class TestIdentity:
    def test_ids_are_deterministic(self) -> None:
        assert workflow_job_id("o/r", 1, "build") == workflow_job_id("o/r", 1, "build")
        assert git_ref_id("o/r", "refs/heads/main") == git_ref_id("o/r", "refs/heads/main")
        assert ruleset_id("o", 7) == ruleset_id("o", 7)

    def test_a_branch_and_a_tag_of_the_same_name_are_different_nodes(self) -> None:
        """The reason identity keys on the full ref path rather than the short name."""
        assert git_ref_id("o/r", "refs/heads/release") != git_ref_id("o/r", "refs/tags/release")

    def test_one_ruleset_is_one_node_however_many_repositories_it_protects(self) -> None:
        """An organization ruleset is a single object. Keying it per repository would turn
        "what does this ruleset protect" into a string comparison across duplicates."""
        assert ruleset_id("acme", 20613528) == ruleset_id("acme", 20613528)

    def test_a_declared_job_is_not_keyed_on_its_display_name(self) -> None:
        """`name:` is free text an author retitles without changing what the job is."""
        assert workflow_job_id("o/r", 1, "build") != workflow_job_id("o/r", 1, "Build & Test")


# --------------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeclaredJobNode:
    def test_inherited_and_empty_permissions_are_not_the_same_value(self) -> None:
        """The distinction the whole type exists to preserve.

        No `permissions:` block means the job inherits the workflow's. `permissions: {}` means the
        job's token is granted nothing at all. Collapsing them reads the most locked-down job in a
        repository as the most permissive one.
        """
        inherits = _create(
            "github_core__workflow_job",
            {"full_name": "o/r", "job_key": "inherits", "permissions": None},
        )
        nothing = _create(
            "github_core__workflow_job",
            {"full_name": "o/r", "job_key": "nothing", "permissions": {}},
        )
        assert inherits.permissions is None
        assert nothing.permissions == {}
        assert inherits.permissions != nothing.permissions

    def test_declaration_dimension_separates_it_from_the_executed_job(self) -> None:
        job = _create("github_core__workflow_job", {"full_name": "o/r", "job_key": "build"})
        job.entity.refresh_from_db()
        assert job.entity.dimensions.get("github.observation") == "declaration"

    def test_checkout_ref_is_a_column_not_a_blob_key(self) -> None:
        job = _create(
            "github_core__workflow_job",
            {
                "full_name": "o/r",
                "job_key": "pwn",
                "checkout_ref": "${{ github.event.pull_request.head.sha }}",
            },
        )
        assert "pull_request.head.sha" in job.checkout_ref


@pytest.mark.django_db
class TestGitRefNode:
    def test_branch_and_tag_are_one_type(self) -> None:
        branch = _create(
            "github_core__git_ref",
            {"full_name": "o/r", "ref": "refs/heads/main", "ref_type": "branch", "name": "main"},
        )
        tag = _create(
            "github_core__git_ref",
            {"full_name": "o/r", "ref": "refs/tags/v1", "ref_type": "tag", "name": "v1"},
        )
        assert type(branch) is type(tag)

    def test_ref_type_is_constrained(self) -> None:
        result = create_node(
            "github_core__git_ref",
            {"full_name": "o/r", "ref": "refs/heads/x", "ref_type": "commit"},
        )
        assert not result.success


@pytest.mark.django_db
class TestRulesetNode:
    def test_unobservable_bypass_carries_a_null_count_not_a_zero(self) -> None:
        """A zero is a claim. Null is the absence of one, and that is the honest value when the
        credential could not read the list at all."""
        ruleset = _create(
            "github_core__github_ruleset",
            {
                "ruleset_id": 1,
                "name": "main",
                "bypass_observability": "unobservable",
                "bypass_actor_count": None,
            },
        )
        assert ruleset.bypass_actor_count is None

    def test_observability_state_is_constrained_to_the_two_it_can_be(self) -> None:
        result = create_node(
            "github_core__github_ruleset",
            {"ruleset_id": 2, "bypass_observability": "probably_fine"},
        )
        assert not result.success


@pytest.mark.django_db
class TestRemainingNodes:
    def test_environment_branch_policy_absent_is_null_not_empty(self) -> None:
        env = _create(
            "github_core__github_environment",
            {"full_name": "o/r", "name": "production", "deployment_branch_policy": None},
        )
        assert env.deployment_branch_policy is None

    def test_cache_carries_the_ref_that_produced_it(self) -> None:
        cache = _create(
            "github_core__actions_cache",
            {"full_name": "o/r", "cache_id": 1, "key": "k", "ref": "refs/pull/42/merge"},
        )
        assert cache.ref == "refs/pull/42/merge"

    def test_installation_holds_the_granted_permissions_not_the_app(self) -> None:
        """The split: permissions belong to one account's grant, not to the application that is
        shared by every account that installed it."""
        installation = _create(
            "github_core__app_installation",
            {
                "installation_id": 157103378,
                "app_slug": "git-serious-exploratory",
                "account_login": "acme",
                "repository_selection": "all",
                "permissions": {"contents": "read"},
            },
        )
        assert installation.permissions == {"contents": "read"}
        assert installation.get_name() == "git-serious-exploratory @ acme"


# --------------------------------------------------------------------------------------------
# Parser — the declaration side
# --------------------------------------------------------------------------------------------


_PWN_REQUEST = """
name: Gate
on:
  pull_request_target:
    branches: [main]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    permissions: {}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - uses: actions/cache@0c45773b623bea8c8e75f6c82b208c3cf94ea4f9
        with:
          key: ${{ runner.os }}-x
          restore-keys: |
            a-
            b-
  deploy:
    needs: build
    if: github.ref == 'refs/heads/main'
    environment:
      name: production
    runs-on: [self-hosted, linux]
    steps:
      - run: ./deploy.sh
"""


@pytest.fixture(scope="module")
def jobs() -> dict:
    """The declared jobs of a workflow written in the shape the corpus keeps finding."""
    return {j["id"]: j for j in parse_workflow_yaml(_PWN_REQUEST)["jobs"]}


class TestDeclaredJobParsing:

    def test_absent_permissions_block_is_none_and_empty_one_is_a_dict(self, jobs) -> None:
        assert jobs["build"]["permissions"] == {}
        assert jobs["deploy"]["permissions"] is None

    def test_checkout_ref_is_lifted_out_of_the_steps(self, jobs) -> None:
        assert jobs["build"]["checkout_ref"] == "${{ github.event.pull_request.head.sha }}"
        assert jobs["deploy"]["checkout_ref"] == ""

    def test_runs_on_is_a_list_whichever_form_was_written(self, jobs) -> None:
        assert jobs["build"]["runs_on"] == ["ubuntu-latest"]
        assert jobs["deploy"]["runs_on"] == ["self-hosted", "linux"]

    def test_runs_on_absent_is_none_not_an_empty_list(self) -> None:
        """A reusable-workflow call declares no runner. None says "not declared"; [] would say
        "declared as nothing", which is not a thing a workflow can say."""
        parsed = parse_workflow_yaml("on: push\njobs:\n  call:\n    uses: ./.github/workflows/x.yml\n")
        assert parsed["jobs"][0]["runs_on"] is None

    def test_runner_group_form_is_flattened_with_its_group_named(self) -> None:
        parsed = parse_workflow_yaml(
            "on: push\njobs:\n  j:\n    runs-on:\n      group: ubuntu-runners\n      labels: [gpu]\n"
        )
        assert parsed["jobs"][0]["runs_on"] == ["gpu", "group:ubuntu-runners"]

    def test_environment_accepts_both_written_forms(self, jobs) -> None:
        assert jobs["deploy"]["environment"] == "production"
        assert parse_workflow_yaml("on: push\njobs:\n  j:\n    environment: staging\n")["jobs"][0][
            "environment"
        ] == "staging"

    def test_cache_key_is_kept_as_an_expression_never_guessed_at(self, jobs) -> None:
        cache = jobs["build"]["cache_steps"][0]
        assert cache["key_expression"] == "${{ runner.os }}-x"
        assert cache["restore_keys"] == ["a-", "b-"]
        assert cache["mode"] == "restore_and_write"

    def test_a_tag_pin_is_recorded_as_a_tag_because_a_tag_can_move(self, jobs) -> None:
        refs = {r["action"]: r for r in jobs["build"]["action_refs"]}
        assert refs["actions/checkout"]["pin_kind"] == "tag"
        assert refs["actions/cache"]["pin_kind"] == "sha"

    def test_an_unpinned_action_is_named_as_such(self) -> None:
        parsed = parse_workflow_yaml("on: push\njobs:\n  j:\n    steps:\n      - uses: foo/bar\n")
        assert parsed["jobs"][0]["action_refs"][0]["pin_kind"] == "unpinned"

    def test_local_actions_are_not_reported_as_third_party_pins(self) -> None:
        parsed = parse_workflow_yaml("on: push\njobs:\n  j:\n    steps:\n      - uses: ./.github/actions/x\n")
        assert parsed["jobs"][0]["action_refs"] == []

    def test_the_if_condition_survives_parsing(self, jobs) -> None:
        assert jobs["deploy"]["if"] == "github.ref == 'refs/heads/main'"

    def test_a_malformed_job_body_does_not_take_the_file_down(self) -> None:
        parsed = parse_workflow_yaml("on: push\njobs:\n  broken: 'just a string'\n")
        assert parsed["jobs"][0]["id"] == "broken"
        assert parsed["jobs"][0]["permissions"] is None


# --------------------------------------------------------------------------------------------
# GraphQL shaping
# --------------------------------------------------------------------------------------------


def _repo_node(**overrides) -> dict:
    node = {
        "nameWithOwner": "acme/widget",
        "defaultBranchRef": {"name": "main", "target": {"oid": "a" * 40}},
        "branchRefs": {
            "totalCount": 2,
            "nodes": [
                {"name": "main", "target": {"oid": "a" * 40}},
                {"name": "topic", "target": {"oid": "b" * 40}},
            ],
        },
        "tagRefs": {
            "totalCount": 2,
            "nodes": [
                {"name": "v1", "target": {"oid": "c" * 40, "__typename": "Commit"}},
                {
                    "name": "v2",
                    "target": {"oid": "d" * 40, "__typename": "Tag", "target": {"oid": "e" * 40}},
                },
            ],
        },
    }
    node.update(overrides)
    return node


class TestGraphQLRefShaping:
    def test_a_lightweight_tag_points_straight_at_its_commit(self) -> None:
        refs = {r["ref"]: r for r in GithubGraphQLClient.refs(_repo_node())[0]}
        assert refs["refs/tags/v1"]["head_sha"] == "c" * 40
        assert refs["refs/tags/v1"]["target_sha"] == "c" * 40

    def test_an_annotated_tag_keeps_the_tag_object_and_the_commit_apart(self) -> None:
        """A re-tag that swaps only the tag object moves one and not the other; one field could
        not show that."""
        refs = {r["ref"]: r for r in GithubGraphQLClient.refs(_repo_node())[0]}
        assert refs["refs/tags/v2"]["target_sha"] == "d" * 40
        assert refs["refs/tags/v2"]["head_sha"] == "e" * 40

    def test_the_default_branch_is_marked_and_nothing_else_is(self) -> None:
        refs = {r["ref"]: r for r in GithubGraphQLClient.refs(_repo_node())[0]}
        assert refs["refs/heads/main"]["is_default"] is True
        assert refs["refs/heads/topic"]["is_default"] is False
        assert refs["refs/tags/v1"]["is_default"] is False

    def test_a_truncated_ref_list_reports_what_it_left_behind(self) -> None:
        """A page cap that goes unreported turns a missing tag into a deleted one."""
        node = _repo_node()
        node["tagRefs"]["totalCount"] = 400
        _, truncated = GithubGraphQLClient.refs(node)
        assert truncated == {"tag": 398}

    def test_a_complete_walk_reports_no_truncation(self) -> None:
        assert GithubGraphQLClient.refs(_repo_node())[1] == {}


class TestGraphQLRulesetShaping:
    @staticmethod
    def _node(**overrides) -> dict:
        ruleset = {
            "databaseId": 20613528,
            "name": "main-required-checks",
            "enforcement": "ACTIVE",
            "target": "BRANCH",
            "conditions": {"refName": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            "rules": {"nodes": [{"type": "REQUIRED_STATUS_CHECKS"}]},
            "bypassActors": {"totalCount": 0, "nodes": []},
        }
        ruleset.update(overrides)
        return {"rulesets": {"nodes": [ruleset]}}

    def test_graphql_shouting_is_lowered_to_the_casing_github_documents(self) -> None:
        shaped = GithubGraphQLClient.rulesets(self._node())[0]
        assert shaped["enforcement"] == "active"
        assert shaped["target"] == "branch"
        assert shaped["rules"] == [{"type": "required_status_checks"}]

    def test_an_empty_bypass_list_is_not_treated_as_proof(self) -> None:
        assert GithubGraphQLClient.rulesets(self._node())[0]["bypass_proven"] is False

    def test_a_non_empty_bypass_list_proves_itself(self) -> None:
        node = self._node(
            bypassActors={"totalCount": 1, "nodes": [{"bypassMode": "ALWAYS", "actor": {"__typename": "App", "slug": "x"}}]}
        )
        assert GithubGraphQLClient.rulesets(node)[0]["bypass_proven"] is True


# --------------------------------------------------------------------------------------------
# Bypass observability — the derivation the product's honesty rests on
# --------------------------------------------------------------------------------------------


class TestBypassObservability:
    """`observable = REST carried the key OR GraphQL returned a non-empty list`.

    Measured 2026-08-27: GitHub returns a ruleset's bypass actors only to a caller with write
    access to it. REST then omits `bypass_actors` entirely; GraphQL answers with an empty
    connection and NO error. So a non-empty GraphQL answer proves itself — a filtered connection
    cannot invent actors — while an empty one proves nothing.
    """

    @staticmethod
    def _ruleset(actors: list | None = None) -> dict:
        return {"name": "main", "bypass_actors": actors or [], "rules": []}

    def test_rest_carrying_the_key_is_observation_even_when_the_list_is_empty(self) -> None:
        state = GithubCollector._bypass_observability(self._ruleset(), {"bypass_actors": []})
        assert state["state"] == "observed"
        assert state["count"] == 0
        assert state["source"] == "rest_ruleset_detail"

    def test_a_non_empty_graphql_answer_is_observation(self) -> None:
        actors = [{"bypassMode": "ALWAYS", "actor": {"__typename": "App", "slug": "ci", "databaseId": 1}}]
        state = GithubCollector._bypass_observability(self._ruleset(actors), {})
        assert state["state"] == "observed"
        assert state["count"] == 1
        assert state["source"] == "graphql_bypass_actors"

    def test_both_silent_is_unobservable_and_never_zero(self) -> None:
        """The failure this guards: rendering "we could not look" as "nobody can bypass" — the
        most reassuring possible message, on no evidence."""
        state = GithubCollector._bypass_observability(self._ruleset(), {})
        assert state["state"] == "unobservable"
        assert state["count"] is None

    def test_a_missing_ruleset_detail_does_not_become_an_observation(self) -> None:
        state = GithubCollector._bypass_observability(self._ruleset(), None)
        assert state["state"] == "unobservable"

    def test_actors_without_a_node_type_are_counted_rather_than_dropped(self) -> None:
        """Understating who can bypass is the one direction that must never happen, so a team or
        an org-admin role is kept as data even though neither has a node type yet."""
        actors = [
            {"bypassMode": "ALWAYS", "actor": {"__typename": "App", "slug": "ci", "databaseId": 1}},
            {"bypassMode": "PULL_REQUEST", "actor": {"__typename": "Team", "slug": "platform"}},
            {"bypassMode": "ALWAYS", "organizationAdmin": True, "actor": {}},
        ]
        state = GithubCollector._bypass_observability(self._ruleset(actors), {})
        assert state["count"] == 3
        assert [a["slug"] for a in state["actors"]] == ["ci"]
        assert {a["actor_type"] for a in state["unmodelled"]} == {"Team", "OrganizationAdmin"}


class TestDefaultRefIsRepoScoped:
    """`~DEFAULT_BRANCH` resolves per repository, not per ref path.

    The bug this exists for: one repository defaulting to `main` and another to `master` — with a
    bare ref path as the key, the first repo's default would mark the second repo's same-named
    branch as protected by a ruleset that does not protect it.
    """

    @staticmethod
    def _collector() -> GithubCollector:
        collector = GithubCollector.__new__(GithubCollector)
        collector._default_refs = {"acme/a#refs/heads/main"}
        return collector

    def test_the_default_of_one_repo_does_not_mark_another(self) -> None:
        collector = self._collector()
        assert collector._is_default_ref("acme/a", "refs/heads/main") is True
        assert collector._is_default_ref("acme/b", "refs/heads/main") is False


class TestRefPatternMatching:
    """Ruleset conditions are GitHub's tokens, matched as tokens rather than as text."""

    def test_default_branch_token_matches_only_the_default(self) -> None:
        assert GithubCollector._matching_pattern("refs/heads/main", ["~DEFAULT_BRANCH"], True) == "~DEFAULT_BRANCH"
        assert GithubCollector._matching_pattern("refs/heads/topic", ["~DEFAULT_BRANCH"], False) is None

    def test_all_token_matches_anything(self) -> None:
        assert GithubCollector._matching_pattern("refs/tags/v9", ["~ALL"], False) == "~ALL"

    def test_globs_match_over_the_full_ref_path(self) -> None:
        assert GithubCollector._matching_pattern("refs/heads/release/1", ["refs/heads/release/*"], False)
        assert GithubCollector._matching_pattern("refs/heads/main", ["refs/heads/release/*"], False) is None

    def test_no_pattern_matches_nothing(self) -> None:
        assert GithubCollector._matching_pattern("refs/heads/main", [], True) is None


# --------------------------------------------------------------------------------------------
# Credentials and the auth seam
# --------------------------------------------------------------------------------------------


class TestCredentialKinds:
    def test_both_kinds_are_accepted(self) -> None:
        assert set(SCHEMA_BY_KIND) == {"github_pat", "github_app"}

    def test_an_app_envelope_needs_a_key_and_a_scope(self) -> None:
        import jsonschema

        jsonschema.validate({"app_id": 1, "private_key": "pem", "owner": "acme"}, GITHUB_APP_SCHEMA)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"app_id": 1, "owner": "acme"}, GITHUB_APP_SCHEMA)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"app_id": 1, "private_key": "pem"}, GITHUB_APP_SCHEMA)

    def test_the_app_envelope_holds_no_token(self) -> None:
        """A github_app envelope carrying a `token` would mean somebody pasted a PAT into the
        wrong kind; strict schemas are how that is caught at load rather than at 401."""
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                {"app_id": 1, "private_key": "pem", "owner": "acme", "token": "ghp_x"},
                GITHUB_APP_SCHEMA,
            )


class TestAuthSeam:
    @staticmethod
    def _app_auth(installations: list[dict], owner: str = "acme") -> GithubAuth:
        auth = GithubAuth(
            kind="github_app",
            data={"app_id": 1, "private_key": "pem", "owner": owner},
            api_base_url="https://api.github.com",
        )
        auth._installations = installations
        return auth

    def test_a_pat_reports_pat_mode_and_no_installations(self) -> None:
        auth = GithubAuth(kind="github_pat", data={"token": "t", "owner": "acme"}, api_base_url="x")
        assert auth.mode == MODE_PAT
        assert auth.token() == "t"
        assert auth.installations() == []

    def test_an_app_reports_app_mode(self) -> None:
        assert self._app_auth([]).mode == MODE_APP

    def test_a_pat_cannot_mint_an_app_jwt(self) -> None:
        auth = GithubAuth(kind="github_pat", data={"token": "t"}, api_base_url="x")
        with pytest.raises(GithubAppAuthError):
            auth.app_jwt()

    def test_the_installation_is_chosen_by_the_envelope_owner(self, monkeypatch) -> None:
        """An App installed into several accounts must be told which one. Taking the first would
        collect one account's repositories under another account's name — silently, and with
        results that look entirely plausible."""
        auth = self._app_auth(
            [
                {"id": 1, "account": {"login": "other"}},
                {"id": 2, "account": {"login": "acme"}},
            ]
        )
        monkeypatch.setattr(auth, "app_jwt", lambda: "jwt")
        monkeypatch.setattr(
            "tap_plugin.github_core.collectors.github_collector.auth.exchange_installation_token",
            lambda base, jwt, installation_id: (f"token-for-{installation_id}", ""),
        )
        assert auth.token() == "token-for-2"
        assert auth.installation["account"]["login"] == "acme"

    def test_an_app_not_installed_on_the_named_account_refuses(self) -> None:
        auth = self._app_auth([{"id": 1, "account": {"login": "other"}}])
        with pytest.raises(GithubAppAuthError, match="not installed"):
            auth.token()

    def test_several_installations_and_no_owner_is_refused_rather_than_guessed(self) -> None:
        auth = self._app_auth(
            [{"id": 1, "account": {"login": "a"}}, {"id": 2, "account": {"login": "b"}}], owner=""
        )
        auth._data = {"app_id": 1, "private_key": "pem"}
        with pytest.raises(GithubAppAuthError, match="several installations"):
            auth.token()

    def test_the_jwt_derivation_lives_in_one_place(self) -> None:
        """The host-side verification script proves a credential the way the collector will use
        it, because both load the same module. A second copy is how "verified" and "works" drift."""
        from pathlib import Path

        verify = (
            Path(__file__).resolve().parents[1] / "skills" / "create-github-app" / "verify_app.py"
        ).read_text()
        assert "app_jwt.py" in verify
        assert "def mint_jwt" not in verify


# --------------------------------------------------------------------------------------------
# Vocabulary registration
# --------------------------------------------------------------------------------------------


class TestVocabularyIsDeclared:
    @staticmethod
    def _manifest() -> dict:
        import tomllib
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "tap-plugin.toml"
        return tomllib.loads(path.read_text())

    def test_every_new_model_is_registered(self) -> None:
        models = self._manifest()["models"]
        assert {
            "github_core__workflow_job",
            "github_core__git_ref",
            "github_core__github_ruleset",
            "github_core__github_environment",
            "github_core__actions_cache",
            "github_core__app_installation",
        } <= set(models)

    def test_every_new_edge_is_registered_and_its_file_exists(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        edges = self._manifest()["edges"]
        expected = {
            "DEFINES_JOB__github_core",
            "DEPENDS_ON_JOB__github_core",
            "HAS_REF__github_core",
            "PROTECTS__github_core",
            "BYPASSES__github_core",
            "HAS_ENVIRONMENT__github_core",
            "USES_ENVIRONMENT__github_core",
            "HAS_CACHE__github_core",
            "SCOPED_TO__github_core",
            "HAS_INSTALLATION__github_core",
            "INSTALLED_ON__github_core",
        }
        assert expected <= set(edges)
        for slug in expected:
            assert (root / edges[slug]).is_file(), f"{slug} declares a file that is not there"

    def test_the_bypasses_edge_documents_that_its_absence_proves_nothing(self) -> None:
        """The edge cannot carry the absence signal, so its description must send a reader to the
        node that can. This assertion exists because the corpus originally put `observable` on the
        edge alone."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        edge = json.loads((root / "edges" / "BYPASSES.edge.json").read_text())
        assert "observable" in edge["property_schema"]["properties"]
        assert "ABSENCE OF THIS EDGE IS NOT ABSENCE OF BYPASS" in edge["description"]

    def test_the_collection_manifest_still_declares_a_permission_per_source(self) -> None:
        """The App's permission set is DERIVED from these declarations, so a source without one
        silently narrows what the credential is allowed to see."""
        for source in load_collection_manifest()["sources"]:
            assert source.get("permission"), f"{source['name']} declares no permission"
