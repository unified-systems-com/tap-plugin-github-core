"""GitHub Actions secret — the NAME of a credential, never its value.

GitHub's secrets API returns names and timestamps and nothing else: the value is write-only
by design and no credential can read it back. That constraint is the whole reason this node is
safe to hold, and it is stated here rather than in a comment somewhere downstream, because the
first question anyone asks of a node called "secret" is whether TAP is storing one. It is not.

What the name alone buys is the join nothing else can make. A workflow's YAML says which secrets
it *references*; this says which secrets *exist* and at what scope. Crossing the two answers three
questions no single source can:

* a reference that resolves to nothing — the workflow receives an empty string at runtime rather
  than an error, which is the failure mode that never announces itself;
* a secret nothing references — a live credential with no consumer;
* a name defined once at org scope and consumed by many repositories — the blast radius when any
  one of those workflows is compromised.

`scope` carries org vs repository rather than being inferred from `full_name` being empty: an
absent scope and an organisation scope must never read the same.
"""

from typing import Any, ClassVar

from django.db import models

from tap_grid.models import BaseModel


class ActionsSecret(BaseModel):
    """An Actions secret's name, scope and timestamps. Never its value.

    Spec: specs/spec-github-core-v0.md (req-github-core-actions-secrets)
    """

    ENTITY_TYPE: ClassVar[str] = "github_core__actions_secret"
    ENTITY_NAME: ClassVar[str] = "GitHub Actions Secret"
    ENTITY_DESCRIPTION: ClassVar[str] = (
        "The name and scope of an Actions secret. GitHub's API returns names only — the value is "
        "write-only and is never read, stored or displayed."
    )
    ENTITY_ICON: ClassVar[str] = "github-secret"
    DEFAULT_DIMENSIONS: ClassVar[dict[str, str]] = {
        "github.observation": "declaration",
        "github.platform": "github.com",
        "github.surface": "secrets",
    }
    DEFAULT_DISPLAY: ClassVar[dict[str, Any]] = {
        "tap_viz": {
            "shape": "round-tag",
            "colors": {"fill": "#FFF1E5", "border": "#A15C00", "label": "#4A2B00"},
        }
    }

    FIELD_CRUD_SCHEMA: ClassVar[dict[str, Any]] = {
        "scope": {
            "type": "string",
            "enum": ["organization", "repository", "environment"],
        },
        "owner_login": {"type": "string", "minLength": 1},
        "full_name": {"type": "string"},
        "environment_name": {"type": "string"},
        "name": {"type": "string", "minLength": 1},
        "visibility": {"type": "string"},
        "created_at": {"type": ["string", "null"]},
        "updated_at": {"type": ["string", "null"]},
        "configuration": {"type": "object"},
        "tags": {"type": "object"},
    }

    FIELD_VALIDATION_SCHEMA: ClassVar[dict[str, Any]] = {
        "scope": {
            "validation": "jsonschema",
            "schema": {
                "type": "string",
                "enum": ["organization", "repository", "environment"],
            },
        },
        "owner_login": {
            "validation": "jsonschema",
            "schema": {"type": "string", "minLength": 1},
        },
        # Empty for an organisation secret — it belongs to no single repository. Distinct from
        # unobserved: an org secret HAS no full_name, it is not that we failed to read one.
        "full_name": {"validation": "jsonschema", "schema": {"type": "string"}},
        "environment_name": {"validation": "jsonschema", "schema": {"type": "string"}},
        "name": {
            "validation": "jsonschema",
            "schema": {"type": "string", "minLength": 1},
        },
        # GitHub's own word for which repositories an org secret reaches: all / private / selected.
        "visibility": {"validation": "jsonschema", "schema": {"type": "string"}},
        "created_at": {
            "validation": "jsonschema",
            "schema": {"type": ["string", "null"]},
        },
        "updated_at": {
            "validation": "jsonschema",
            "schema": {"type": ["string", "null"]},
        },
        "configuration": {"validation": "jsonschema", "schema": {"type": "object"}},
        "tags": {"validation": "jsonschema", "schema": {"type": "object"}},
    }
    CREATE_REQUIRED: ClassVar[list[str]] = ["scope", "owner_login", "name"]

    scope = models.CharField(max_length=32, blank=True, default="", db_index=True)
    owner_login = models.CharField(max_length=255, blank=True, default="", db_index=True)
    full_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    environment_name = models.CharField(max_length=255, blank=True, default="")
    name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    visibility = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    configuration = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "github_core__actions_secret"

    def get_name(self) -> str:
        return self.name

    def __str__(self) -> str:
        where = self.full_name or self.owner_login
        return f"{self.name} ({where})"
