"""Actions artifact — a file a workflow run uploaded and GitHub is holding.

Eleven sources name it in the vocabulary corpus (as *artifact*); one incident, ArtiPACKED,
turns entirely on it (a checkout directory uploaded with its credentials still inside). The
node is the observed upload; the declared `actions/upload-artifact` step stays on the job.
"""

from typing import Any, ClassVar

from django.db import models

from tap_grid.models import BaseModel


class ActionsArtifact(BaseModel):
    """One artifact as GitHub reports it: name, size, digest, expiry, and the run that made it.

    Spec: plugins/github_core/specs/spec-github-core-v0.md (req-github-core-artifacts)
    """

    ENTITY_TYPE: ClassVar[str] = "github_core__actions_artifact"
    ENTITY_NAME: ClassVar[str] = "Actions Artifact"
    ENTITY_DESCRIPTION: ClassVar[str] = (
        "A file a workflow run uploaded — its name, size, content digest, expiry, and the run and "
        "ref that produced it."
    )
    ENTITY_ICON: ClassVar[str] = "actions-artifact"
    DEFAULT_DIMENSIONS: ClassVar[dict[str, str]] = {
        "github.platform": "github.com",
        "github.surface": "actions",
        # A by-product of a run, like a cache entry: it exists because a workflow executed.
        "github.observation": "execution",
    }
    DEFAULT_DISPLAY: ClassVar[dict[str, Any]] = {
        "tap_viz": {
            "shape": "round-rectangle",
            "colors": {"fill": "#FFFFFF", "border": "#8250DF", "label": "#1F2328"},
        }
    }

    FIELD_CRUD_SCHEMA: ClassVar[dict[str, Any]] = {
        "artifact_id": {"type": ["integer", "null"]},
        "full_name": {"type": "string", "minLength": 1},
        "name": {"type": "string"},
        "size_in_bytes": {"type": ["integer", "null"]},
        "digest": {"type": "string"},
        "expired": {"type": ["boolean", "null"]},
        "run_id": {"type": ["integer", "null"]},
        "head_sha": {"type": "string"},
        "head_branch": {"type": "string"},
        "created_at": {"type": ["string", "null"]},
        "updated_at": {"type": ["string", "null"]},
        "expires_at": {"type": ["string", "null"]},
        "archive_download_url": {"type": "string"},
        "configuration": {"type": "object"},
        "tags": {"type": "object"},
    }

    FIELD_VALIDATION_SCHEMA: ClassVar[dict[str, Any]] = {
        "artifact_id": {"validation": "jsonschema", "schema": {"type": ["integer", "null"]}},
        "full_name": {"validation": "jsonschema", "schema": {"type": "string", "minLength": 1}},
        "name": {"validation": "jsonschema", "schema": {"type": "string"}},
        "size_in_bytes": {"validation": "jsonschema", "schema": {"type": ["integer", "null"]}},
        "digest": {"validation": "jsonschema", "schema": {"type": "string"}},
        "expired": {"validation": "jsonschema", "schema": {"type": ["boolean", "null"]}},
        "run_id": {"validation": "jsonschema", "schema": {"type": ["integer", "null"]}},
        "head_sha": {"validation": "jsonschema", "schema": {"type": "string"}},
        "head_branch": {"validation": "jsonschema", "schema": {"type": "string"}},
        "created_at": {"validation": "jsonschema", "schema": {"type": ["string", "null"]}},
        "updated_at": {"validation": "jsonschema", "schema": {"type": ["string", "null"]}},
        "expires_at": {"validation": "jsonschema", "schema": {"type": ["string", "null"]}},
        "archive_download_url": {"validation": "jsonschema", "schema": {"type": "string"}},
        "configuration": {"validation": "jsonschema", "schema": {"type": "object"}},
        "tags": {"validation": "jsonschema", "schema": {"type": "object"}},
    }
    CREATE_REQUIRED: ClassVar[list[str]] = ["full_name", "artifact_id"]

    artifact_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    full_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    name = models.CharField(max_length=512, blank=True, default="", db_index=True)
    size_in_bytes = models.BigIntegerField(null=True, blank=True)
    #: `sha256:<hex>` of the uploaded content, as GitHub reports it. Empty when not returned.
    digest = models.CharField(max_length=128, blank=True, default="")
    expired = models.BooleanField(null=True, blank=True)
    #: The run that uploaded it — GitHub's own attribution, not a derivation.
    run_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    head_sha = models.CharField(max_length=64, blank=True, default="")
    #: The ref the producing run was on. `refs/pull/...` heads do not appear here — GitHub
    #: reports the bare branch name — so a pull-request artifact reads as its source branch.
    head_branch = models.CharField(max_length=512, blank=True, default="")
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    archive_download_url = models.URLField(max_length=512, blank=True, default="")
    configuration = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "github_core__actions_artifact"

    def get_name(self) -> str:
        return self.name or (str(self.artifact_id) if self.artifact_id else "")

    def __str__(self) -> str:
        return self.get_name()
