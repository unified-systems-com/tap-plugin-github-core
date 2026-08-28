"""Git ref — a named pointer at a commit: a branch or a tag.

One type covers both because that is what git is: a ref is a name and the SHA
it points at, stored under `refs/heads/` for branches and `refs/tags/` for
tags. The security-relevant difference is a social contract, not a structure —
a branch is expected to move, a tag is expected to be frozen — so tag movement
is a broken promise and is the detection for three incidents in the corpus. A
ruleset's target is one enum spanning `branch|tag|push`, so splitting the type
would fan that join across two types and two edges for no gain.

Ruled 2026-08-27 (`spec-github-core-vocabulary.md`, decision 2). The slug is a
modelling name: views render "Branches" and "Tags".
"""

from typing import Any, ClassVar

from django.db import models

from tap_grid.models import BaseModel


class GitRef(BaseModel):
    """A branch or tag in a repository, with the commit it currently points at.

    Neutral concept: any git host populates it. Moves to the neutral substrate
    when one is extracted.

    Spec: plugins/github_core/specs/spec-github-core-v0.md (req-github-core-refs)
    """

    ENTITY_TYPE: ClassVar[str] = "github_core__git_ref"
    ENTITY_NAME: ClassVar[str] = "Git Ref"
    ENTITY_DESCRIPTION: ClassVar[str] = (
        "A branch or tag — a name and the commit it points at. Movement of a tag is the signal; "
        "movement of a branch is routine."
    )
    ENTITY_ICON: ClassVar[str] = "git-ref"
    DEFAULT_DIMENSIONS: ClassVar[dict[str, str]] = {
        "github.observation": "declaration",
        "github.platform": "github.com",
        "github.surface": "git",
    }
    DEFAULT_DISPLAY: ClassVar[dict[str, Any]] = {
        "tap_viz": {
            "shape": "round-rectangle",
            "colors": {"fill": "#FFFFFF", "border": "#8250DF", "label": "#1F2328"},
        }
    }

    REF_TYPE_BRANCH = "branch"
    REF_TYPE_TAG = "tag"

    FIELD_CRUD_SCHEMA: ClassVar[dict[str, Any]] = {
        "full_name": {"type": "string", "minLength": 1},
        "ref": {"type": "string", "minLength": 1},
        "ref_type": {"type": "string", "enum": [REF_TYPE_BRANCH, REF_TYPE_TAG]},
        "name": {"type": "string"},
        "head_sha": {"type": "string"},
        "target_sha": {"type": "string"},
        "target_type": {"type": "string"},
        "is_default": {"type": "boolean"},
        "configuration": {"type": "object"},
        "tags": {"type": "object"},
    }

    FIELD_VALIDATION_SCHEMA: ClassVar[dict[str, Any]] = {
        "full_name": {"validation": "jsonschema", "schema": {"type": "string", "minLength": 1}},
        "ref": {"validation": "jsonschema", "schema": {"type": "string", "minLength": 1}},
        "ref_type": {
            "validation": "jsonschema",
            "schema": {"type": "string", "enum": [REF_TYPE_BRANCH, REF_TYPE_TAG]},
        },
        "name": {"validation": "jsonschema", "schema": {"type": "string"}},
        "head_sha": {"validation": "jsonschema", "schema": {"type": "string"}},
        "target_sha": {"validation": "jsonschema", "schema": {"type": "string"}},
        "target_type": {"validation": "jsonschema", "schema": {"type": "string"}},
        "is_default": {"validation": "jsonschema", "schema": {"type": "boolean"}},
        "configuration": {"validation": "jsonschema", "schema": {"type": "object"}},
        "tags": {"validation": "jsonschema", "schema": {"type": "object"}},
    }
    CREATE_REQUIRED: ClassVar[list[str]] = ["full_name", "ref"]

    full_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    # The full ref path (`refs/heads/main`, `refs/tags/v1.2.3`) — the identity input, because a
    # branch and a tag may share a short name.
    ref = models.CharField(max_length=512, blank=True, default="", db_index=True)
    ref_type = models.CharField(max_length=16, blank=True, default="", db_index=True)
    name = models.CharField(max_length=512, blank=True, default="")
    # The COMMIT this ref resolves to. Field history on this column is the tag-movement detection:
    # a tag whose head_sha changes between observations broke the promise its name makes.
    head_sha = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # The object the ref points AT, which differs from head_sha for an annotated tag (the ref
    # points at a tag object that points at the commit). Kept apart so a re-tag that swaps only
    # the tag object is still visible.
    target_sha = models.CharField(max_length=64, blank=True, default="")
    target_type = models.CharField(max_length=16, blank=True, default="")
    is_default = models.BooleanField(default=False)
    configuration = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "github_core__git_ref"

    def get_name(self) -> str:
        return self.name or self.ref

    def __str__(self) -> str:
        return self.get_name()
