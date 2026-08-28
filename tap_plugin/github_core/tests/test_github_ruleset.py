"""Tests for the github_ruleset type, its identity, and config-layer emission.

Spec: plugins/github_core/specs/spec-github-core-v0.md (req-github-core-ruleset)
"""

import pytest
import tap_plugin.github_core.models as github  # noqa: F401 — trigger model registration
from tap_plugin.github_core.collectors.github_collector.identity import github_app_id, ruleset_id

from tap_grid.models import Entity
from tap_grid.registry import get_model_class
from tap_grid.services import create_node


def _create(type_slug: str, payload: dict):
    result = create_node(type_slug, payload)
    assert result.success, f"create_node failed: {result.errors}"
    entity = Entity.objects.get(pk=result.entity_id)
    return get_model_class(type_slug).objects.get(entity=entity)


@pytest.mark.django_db
class TestGithubRuleset:
    """req-github-core-ruleset-1, -6."""

    def test_create_and_display(self):
        rs = _create(
            "github_core__github_ruleset",
            {
                "ruleset_id": 20613528,
                "name": "main-required-checks",
                "enforcement": "ACTIVE",
                "target": "BRANCH",
                "source_type": "Repository",
                "source_name": "unified-systems-com/tap",
            },
        )
        rs.entity.refresh_from_db()
        assert rs.ruleset_id == 20613528
        assert rs.entity.name == "main-required-checks"
        assert rs.entity.dimensions.get("github.surface") == "rules"

    def test_name_falls_back_to_ruleset_id(self):
        """Belt-and-braces: `name` is minLength-1 validated so an empty one cannot be
        persisted, but get_name() must still not project "" onto Entity.name if it ever is."""
        from tap_plugin.github_core.models import GithubRuleset

        assert GithubRuleset(name="", ruleset_id=42).get_name() == "42"
        assert GithubRuleset(name="", ruleset_id=None).get_name() == ""

    def test_name_required(self):
        assert not create_node("github_core__github_ruleset", {"ruleset_id": 1}).success

    def test_closed_sets_reject_invalid(self):
        """req-github-core-ruleset-6: enforcement/target/source_type are closed sets."""
        for field, bad in (
            ("enforcement", "MAYBE"),
            ("target", "EVERYTHING"),
            ("source_type", "Enterprise"),
        ):
            result = create_node("github_core__github_ruleset", {"name": "n", field: bad})
            assert not result.success, f"{field}={bad!r} should have been rejected"

    def test_empty_string_permitted_so_partial_reads_land(self):
        """req-github-core-ruleset-6: a degraded field must not discard the ruleset."""
        rs = _create(
            "github_core__github_ruleset",
            {"name": "partial", "enforcement": "", "target": "", "source_type": ""},
        )
        assert rs.enforcement == ""


class TestGithubRulesetIdentity:
    """req-github-core-ruleset-2: the key is the bare databaseId."""

    def test_derivation_is_pinned(self):
        """The derived id is pinned to a literal, not compared against itself.

        `ruleset_id(x) == ruleset_id(x)` cannot fail for a pure function — it asserts
        nothing. The real risk is a change to the derivation (its namespace or its input
        string) silently re-keying every ruleset node on every existing grid, which is
        invisible to a self-comparison and caught by a pinned value.
        """
        assert str(ruleset_id(20613528)) == "cebdca2c-cf87-5863-8809-415f6a51af83"

    def test_int_and_str_agree(self):
        assert ruleset_id(20613528) == ruleset_id("20613528")

    def test_not_scoped_by_repository(self):
        """One org ruleset reported by many repos must collapse to ONE node.

        This is the whole reason the natural key is the bare id: on the fixture org,
        three organization-sourced rulesets are reported by all nineteen repositories
        (57 of 60 attachments). A repo-scoped key would mint 57 nodes for 3 rulesets.
        """
        # Asserted as a pinned literal keyed on the ruleset id ALONE. The previous form
        # compared the call against itself, which cannot detect a repo-scoped key — the
        # very thing this test is named for — because it never varies a repository.
        # The collapse itself is asserted at the emitter, in
        # `test_org_ruleset_seen_from_many_repos_emits_once`.
        assert str(ruleset_id(21242695)) == "59636923-85bb-5534-be53-e08721c5210f"
        assert ruleset_id.__code__.co_argcount == 1, (
            "ruleset_id takes the databaseId and nothing else; a second parameter would "
            "let a caller scope the key by repository and mint 57 nodes for 3 rulesets"
        )

    def test_distinct_from_other_types_on_the_same_key(self):
        assert ruleset_id("dependabot") != github_app_id("dependabot")


class TestRulesetEmission:
    """req-github-core-ruleset-3, -4, -5 — the collector's config-layer emission."""

    @staticmethod
    def _collector():
        from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

        c = GithubCollector.__new__(GithubCollector)
        c._emitted_ruleset_ids = set()
        return c

    @staticmethod
    def _gql(*rulesets):
        return {"rulesets": {"nodes": list(rulesets)}}

    def test_emits_one_node_per_ruleset(self):
        nodes: list = []
        gql = self._gql(
            {
                "databaseId": 20613528,
                "name": "main-required-checks",
                "enforcement": "ACTIVE",
                "target": "BRANCH",
                "source": {"__typename": "Repository", "nameWithOwner": "unified-systems-com/tap"},
            }
        )
        self._collector()._emit_rulesets(gql, {"github.platform": "github.com"}, nodes)
        assert len(nodes) == 1
        assert nodes[0]["node"]["ruleset_id"] == 20613528
        assert nodes[0]["node"]["source_type"] == "Repository"
        assert nodes[0]["node"]["source_name"] == "unified-systems-com/tap"
        assert nodes[0]["entity"]["dimensions"]["github.surface"] == "rules"

    def test_org_ruleset_seen_from_many_repos_emits_once(self):
        """req-github-core-ruleset-3: dedupe is across the run, not within one repo."""
        collector = self._collector()
        nodes: list = []
        org_rs = {
            "databaseId": 21242695,
            "name": "org-require-pr",
            "enforcement": "ACTIVE",
            "target": "BRANCH",
            "source": {"__typename": "Organization", "login": "unified-systems-com"},
        }
        for _ in range(19):  # the fixture org's repository count
            collector._emit_rulesets(self._gql(org_rs), {"github.platform": "github.com"}, nodes)
        assert len(nodes) == 1
        assert nodes[0]["node"]["source_type"] == "Organization"
        assert nodes[0]["node"]["source_name"] == "unified-systems-com"

    def test_ruleset_without_id_is_skipped(self):
        """req-github-core-ruleset-5: an unfollowable ruleset is not emitted."""
        nodes: list = []
        self._collector()._emit_rulesets(
            self._gql({"databaseId": None, "name": "idless", "enforcement": "ACTIVE"}),
            {"github.platform": "github.com"},
            nodes,
        )
        assert nodes == []

    def test_nameless_ruleset_still_lands(self):
        """req-github-core-ruleset-6: a missing name must not discard the ruleset.

        `name` is minLength-1 validated, so emitting "" would fail validation and drop a
        real gate on the floor. The id is the fallback, as it is for the entity name.
        """
        nodes: list = []
        self._collector()._emit_rulesets(
            self._gql({"databaseId": 20613528, "name": None, "enforcement": "ACTIVE"}),
            {"github.platform": "github.com"},
            nodes,
        )
        assert len(nodes) == 1
        assert nodes[0]["node"]["name"] == "20613528"

    def test_no_config_layer_emits_nothing(self):
        """req-github-core-ruleset-4: no GraphQL enumeration means no rulesets, not a REST fallback."""
        nodes: list = []
        self._collector()._emit_rulesets(None, {"github.platform": "github.com"}, nodes)
        assert nodes == []
