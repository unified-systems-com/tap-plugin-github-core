"""GitHub release — a published output of a repository, cut on a tag.

The first node in the output column (github-core#31). Six sources name it in the vocabulary
corpus (`spec-github-core-vocabulary.md`, self-lite tier); the machinery view rendered
"artifacts: not yet collected" until this landed.
"""

from typing import Any, ClassVar

from django.db import models

from tap_grid.models import BaseModel


class GithubRelease(BaseModel):
    """One release as GitHub reports it: the tag it was cut on, who cut it, and its assets.

    A release is a product of execution, not a declaration: it exists because someone — a
    person, or a release bot on a workflow run — published it. It carries
    `github.observation: execution` for that reason, even though it arrives in the config-layer
    GraphQL query beside rulesets and refs (transport is not layer).

    Spec: plugins/github_core/specs/spec-github-core-v0.md (req-github-core-releases)
    """

    ENTITY_TYPE: ClassVar[str] = "github_core__github_release"
    ENTITY_NAME: ClassVar[str] = "GitHub Release"
    ENTITY_DESCRIPTION: ClassVar[str] = (
        "A published release — the tag it was cut on, the commit that tag resolved to when observed, "
        "who published it, and the assets attached."
    )
    ENTITY_ICON: ClassVar[str] = "github-release"
    DEFAULT_DIMENSIONS: ClassVar[dict[str, str]] = {
        "github.platform": "github.com",
        "github.surface": "releases",
        "github.observation": "execution",
    }
    DEFAULT_DISPLAY: ClassVar[dict[str, Any]] = {
        "tap_viz": {
            "shape": "tag",
            "colors": {"fill": "#DAFBE1", "border": "#1A7F37", "label": "#116329"},
        }
    }

    FIELD_CRUD_SCHEMA: ClassVar[dict[str, Any]] = {
        "release_id": {"type": ["integer", "null"]},
        "full_name": {"type": "string", "minLength": 1},
        "tag_name": {"type": "string"},
        "name": {"type": "string"},
        "is_draft": {"type": ["boolean", "null"]},
        "is_prerelease": {"type": ["boolean", "null"]},
        "is_latest": {"type": ["boolean", "null"]},
        "author_login": {"type": "string"},
        "target_sha": {"type": "string"},
        "created_at": {"type": ["string", "null"]},
        "published_at": {"type": ["string", "null"]},
        "html_url": {"type": "string"},
        "asset_count": {"type": ["integer", "null"]},
        "assets": {"type": "array"},
        "configuration": {"type": "object"},
        "tags": {"type": "object"},
    }

    FIELD_VALIDATION_SCHEMA: ClassVar[dict[str, Any]] = {
        "release_id": {"validation": "jsonschema", "schema": {"type": ["integer", "null"]}},
        "full_name": {"validation": "jsonschema", "schema": {"type": "string", "minLength": 1}},
        "tag_name": {"validation": "jsonschema", "schema": {"type": "string"}},
        "name": {"validation": "jsonschema", "schema": {"type": "string"}},
        # null is "not observed", never "false": a degraded read must not claim a release is final.
        "is_draft": {"validation": "jsonschema", "schema": {"type": ["boolean", "null"]}},
        "is_prerelease": {"validation": "jsonschema", "schema": {"type": ["boolean", "null"]}},
        "is_latest": {"validation": "jsonschema", "schema": {"type": ["boolean", "null"]}},
        "author_login": {"validation": "jsonschema", "schema": {"type": "string"}},
        "target_sha": {"validation": "jsonschema", "schema": {"type": "string"}},
        "created_at": {"validation": "jsonschema", "schema": {"type": ["string", "null"]}},
        "published_at": {"validation": "jsonschema", "schema": {"type": ["string", "null"]}},
        "html_url": {"validation": "jsonschema", "schema": {"type": "string"}},
        "asset_count": {"validation": "jsonschema", "schema": {"type": ["integer", "null"]}},
        "assets": {"validation": "jsonschema", "schema": {"type": "array"}},
        "configuration": {"validation": "jsonschema", "schema": {"type": "object"}},
        "tags": {"validation": "jsonschema", "schema": {"type": "object"}},
    }
    CREATE_REQUIRED: ClassVar[list[str]] = ["full_name", "release_id"]

    #: GitHub's release id (`databaseId`), the natural key together with `full_name`.
    release_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    full_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    #: The tag the release was cut on — the join onto `git_ref` (`refs/tags/<tag_name>`).
    tag_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    name = models.CharField(max_length=512, blank=True, default="")
    is_draft = models.BooleanField(null=True, blank=True)
    is_prerelease = models.BooleanField(null=True, blank=True)
    is_latest = models.BooleanField(null=True, blank=True)
    #: The login that published it. An account, not an identity — a bot login lands as-is.
    author_login = models.CharField(max_length=255, blank=True, default="")
    #: The commit the tag resolved to WHEN OBSERVED. Compared against the tag ref's own
    #: `head_sha` later, this is what makes a re-tagged release detectable.
    target_sha = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    html_url = models.URLField(max_length=512, blank=True, default="")
    #: GitHub's `totalCount` of assets — may exceed `len(assets)` when the page cap bit.
    asset_count = models.IntegerField(null=True, blank=True)
    #: `[{"name", "size", "content_type", "download_url", "created_at"}, ...]`
    assets = models.JSONField(default=list, blank=True)
    configuration = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "github_core__github_release"

    def get_name(self) -> str:
        return self.name or self.tag_name or (str(self.release_id) if self.release_id else "")

    def __str__(self) -> str:
        return self.get_name()
