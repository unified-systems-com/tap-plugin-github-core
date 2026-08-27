"""GitHub Ruleset — the gate that governs pushes to a repository's refs."""

from typing import Any, ClassVar

from django.db import models

from tap_grid.models import BaseModel


class GithubRuleset(BaseModel):
    """A GitHub repository ruleset — the enforcement gate on a set of refs.

    One node per *ruleset*, not per attachment. An organization-sourced ruleset is a
    single rule set that GitHub projects onto every repository in scope, so the same
    `ruleset_id` is returned by many repositories' ruleset lists. Measured on
    `unified-systems-com`: 6 rulesets, 60 attachments, 19 repositories. Modelling one
    node per attachment would derive the same ruleset's facts 19 times over and let
    the copies disagree.

    `ruleset_id` is the REST `databaseId`, and it is the load-bearing field: every
    other ruleset surface — the bypass-actor list, rule suites (bypass events) and
    version history — is keyed by it. The config-layer GraphQL query returned rulesets
    without it until req-github-core-ruleset, which is why none of those surfaces were
    reachable.

    `source_type` records whether the ruleset is defined on the organization or on the
    repository. It is a property of the ruleset rather than of any one attachment
    (derive-a-fact-once), and it is operationally load-bearing: version history for an
    organization-sourced ruleset is not reachable by the repository path and requires
    organization scope.

    Spec: plugins/github_core/specs/spec-github-core-v0.md (req-github-core-ruleset)
    """

    ENTITY_TYPE: ClassVar[str] = "github_core__github_ruleset"
    ENTITY_NAME: ClassVar[str] = "GitHub Ruleset"
    ENTITY_DESCRIPTION: ClassVar[str] = "A GitHub repository ruleset — the enforcement gate on a set of refs."
    ENTITY_ICON: ClassVar[str] = "github-ruleset"
    # `surface` follows GitHub's own subcategory for these endpoints ("rules"),
    # rather than inventing a parallel word for the same thing.
    DEFAULT_DIMENSIONS: ClassVar[dict[str, str]] = {
        "github.platform": "github.com",
        "github.surface": "rules",
        "github.observation": "declaration",
    }
    # Cross-cutting node: many repositories point at one ruleset, so it is not a leaf
    # inside the repo box. White fill with the GitHub "attention" amber border marks it
    # as a gate rather than a container. See github_platform for the family scheme.
    DEFAULT_DISPLAY: ClassVar[dict[str, Any]] = {
        "tap_viz": {
            "shape": "round-rectangle",
            "colors": {"fill": "#FFFFFF", "border": "#BF8700", "label": "#1F2328"},
        }
    }

    FIELD_CRUD_SCHEMA: ClassVar[dict[str, Any]] = {
        "ruleset_id": {"type": ["integer", "null"]},
        "name": {"type": "string", "minLength": 1},
        "enforcement": {"type": "string"},
        "target": {"type": "string"},
        "source_type": {"type": "string"},
        "source_name": {"type": "string"},
        "configuration": {"type": "object"},
        "tags": {"type": "object"},
    }

    FIELD_VALIDATION_SCHEMA: ClassVar[dict[str, Any]] = {
        "ruleset_id": {"validation": "jsonschema", "schema": {"type": ["integer", "null"]}},
        "name": {"validation": "jsonschema", "schema": {"type": "string", "minLength": 1}},
        # Enumerated where GitHub enumerates. `enforcement` and `target` are closed sets in
        # the GraphQL schema (RuleEnforcement, RepositoryRulesetTarget); "" is permitted so a
        # partially-read ruleset still lands rather than being dropped on the floor.
        "enforcement": {
            "validation": "jsonschema",
            "schema": {"type": "string", "enum": ["ACTIVE", "EVALUATE", "DISABLED", ""]},
        },
        "target": {
            "validation": "jsonschema",
            "schema": {"type": "string", "enum": ["BRANCH", "TAG", "PUSH", ""]},
        },
        "source_type": {
            "validation": "jsonschema",
            "schema": {"type": "string", "enum": ["Organization", "Repository", ""]},
        },
        "source_name": {"validation": "jsonschema", "schema": {"type": "string"}},
        "configuration": {"validation": "jsonschema", "schema": {"type": "object"}},
        "tags": {"validation": "jsonschema", "schema": {"type": "object"}},
    }
    CREATE_REQUIRED: ClassVar[list[str]] = ["name"]

    ruleset_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")
    enforcement = models.CharField(max_length=32, blank=True, default="")
    target = models.CharField(max_length=32, blank=True, default="")
    source_type = models.CharField(max_length=32, blank=True, default="")
    source_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    configuration = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "github_core__github_ruleset"

    def get_name(self) -> str:
        return self.name or (str(self.ruleset_id) if self.ruleset_id is not None else "")

    def __str__(self) -> str:
        return self.get_name()
