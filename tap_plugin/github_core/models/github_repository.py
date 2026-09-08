"""GitHub Repository — a repository under a GitHub account."""

from typing import Any, ClassVar

from django.db import models

from tap_grid.models import BaseModel


class GithubRepository(BaseModel):
    """A GitHub repository.

    Spec: plugins/github_core/specs/spec-github-core-v0.md (req-github-core-models)
    """

    ENTITY_TYPE: ClassVar[str] = "github_core__github_repository"
    ENTITY_NAME: ClassVar[str] = "GitHub Repository"
    ENTITY_DESCRIPTION: ClassVar[str] = "A repository hosted on GitHub."
    ENTITY_ICON: ClassVar[str] = "github-repository"
    DEFAULT_DIMENSIONS: ClassVar[dict[str, str]] = {
        "github.platform": "github.com",
        "github.observation": "declaration",
    }
    # Inner level of the GitHub nesting palette — deeper accent blue; the white
    # workflow leaf cards sit on this bed. See github_platform for the scheme.
    DEFAULT_DISPLAY: ClassVar[dict[str, Any]] = {
        "tap_viz": {
            "shape": "round-rectangle",
            "colors": {"fill": "#B6E3FF", "border": "#0969DA", "label": "#0A3069"},
            "label": {"valign": "top", "halign": "center", "position": "outside"},
        }
    }

    #: The custom-property values were returned by the organization's values endpoint.
    CUSTOM_PROPERTIES_OBSERVED = "observed"
    #: The credential could not read them; `custom_properties` must not be rendered as "none set".
    CUSTOM_PROPERTIES_UNOBSERVABLE = "unobservable"

    FIELD_CRUD_SCHEMA: ClassVar[dict[str, Any]] = {
        "full_name": {"type": "string", "minLength": 1},
        "owner_login": {"type": "string"},
        "name": {"type": "string"},
        "github_id": {"type": ["integer", "null"]},
        "default_branch": {"type": "string"},
        "visibility": {"type": "string"},
        "html_url": {"type": "string"},
        "outputs_observability": {"type": "object"},
        "custom_properties": {"type": "object"},
        "custom_properties_observability": {
            "type": "string",
            "enum": ["", CUSTOM_PROPERTIES_OBSERVED, CUSTOM_PROPERTIES_UNOBSERVABLE],
        },
        "configuration": {"type": "object"},
        "tags": {"type": "object"},
    }

    FIELD_VALIDATION_SCHEMA: ClassVar[dict[str, Any]] = {
        "full_name": {
            "validation": "jsonschema",
            "schema": {"type": "string", "minLength": 1},
        },
        "owner_login": {"validation": "jsonschema", "schema": {"type": "string"}},
        "name": {"validation": "jsonschema", "schema": {"type": "string"}},
        "github_id": {
            "validation": "jsonschema",
            "schema": {"type": ["integer", "null"]},
        },
        "default_branch": {"validation": "jsonschema", "schema": {"type": "string"}},
        "visibility": {"validation": "jsonschema", "schema": {"type": "string"}},
        "html_url": {"validation": "jsonschema", "schema": {"type": "string"}},
        "outputs_observability": {
            "validation": "jsonschema",
            "schema": {"type": "object"},
        },
        # Keys are property names exactly as GitHub reports them; a value is a string, an array
        # (multi_select) or null. A null value is OBSERVED-UNSET: the definition exists and the
        # repository carries no value. A key that is absent was never read against a definition.
        "custom_properties": {
            "validation": "jsonschema",
            "schema": {"type": "object", "additionalProperties": {"type": ["string", "array", "null"]}},
        },
        "custom_properties_observability": {
            "validation": "jsonschema",
            "schema": {"type": "string", "enum": ["", CUSTOM_PROPERTIES_OBSERVED, CUSTOM_PROPERTIES_UNOBSERVABLE]},
        },
        "configuration": {"validation": "jsonschema", "schema": {"type": "object"}},
        "tags": {"validation": "jsonschema", "schema": {"type": "object"}},
    }
    CREATE_REQUIRED: ClassVar[list[str]] = ["full_name"]

    full_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    owner_login = models.CharField(
        max_length=255, blank=True, default="", db_index=True
    )
    name = models.CharField(max_length=255, blank=True, default="")
    github_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    default_branch = models.CharField(max_length=255, blank=True, default="")
    visibility = models.CharField(max_length=32, blank=True, default="")
    html_url = models.URLField(max_length=512, blank=True, default="")
    #: Three states per OUTPUT surface — `{"releases": "observed"|"unobservable", "artifacts": ...,
    #: "packages": ...}` plus a `notes` map saying why. Lives on the repository because a property
    #: that qualifies an absence belongs on the node the absence is about, never on the nodes that
    #: failed to appear (github-core#31; the same ruling as `github_ruleset.bypass_observability`).
    #: Empty `{}` means the collector never ran the output surfaces, which is itself not "none".
    outputs_observability = models.JSONField(default=dict, blank=True)
    #: `{"criticality": "critical", "lifecycle": null, ...}` — one key per custom property the
    #: owning organization DECLARES (`github_custom_property`), the value as GitHub reports it or
    #: null when the repository has not set it. Read against the definitions, so an unset value
    #: is a fact and not a blank. Empty `{}` with observability `observed` means the organization
    #: declares no properties.
    custom_properties = models.JSONField(default=dict, blank=True)
    #: Three states, the ruling of `github_ruleset.bypass_observability` applied here: `observed`
    #: (the values endpoint answered — an empty map is then a fact), `unobservable` (the credential
    #: could not read them — the map means nothing), `""` (the collector never ran the surface,
    #: e.g. a user-owned repository, where custom properties do not exist).
    custom_properties_observability = models.CharField(max_length=16, blank=True, default="")
    configuration = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "github_core__github_repository"

    def get_name(self) -> str:
        return self.full_name or self.name

    def __str__(self) -> str:
        return self.get_name()
