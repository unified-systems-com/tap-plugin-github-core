"""App installation — the GRANT, split apart from the application itself.

The registered application and its installation into an account are different
objects: one App is installed into many accounts, each installation carrying
its own permission set, its own repository selection and its own suspension
state. Seven sources make the split; keeping them merged would attach an
account's granted permissions to a globally-shared app node.
"""

from typing import Any, ClassVar

from django.db import models

from tap_grid.models import BaseModel


class AppInstallation(BaseModel):
    """One installation of a GitHub App into an account.

    Reading the installed-App inventory is an App-only surface — a personal
    access token gets `404` from it — so this type is populated only when the
    collector authenticates as an App (`req-github-core-app-auth`).

    Spec: plugins/github_core/specs/spec-github-core-v0.md (req-github-core-app-installations)
    """

    ENTITY_TYPE: ClassVar[str] = "github_core__app_installation"
    ENTITY_NAME: ClassVar[str] = "App Installation"
    ENTITY_DESCRIPTION: ClassVar[str] = (
        "A GitHub App installed into an account — the permissions granted, which repositories it "
        "reaches, and whether it is suspended."
    )
    ENTITY_ICON: ClassVar[str] = "app-installation"
    DEFAULT_DIMENSIONS: ClassVar[dict[str, str]] = {
        "github.observation": "declaration",
        "github.platform": "github.com",
        "github.surface": "apps",
    }
    DEFAULT_DISPLAY: ClassVar[dict[str, Any]] = {
        "tap_viz": {
            "shape": "round-rectangle",
            "colors": {"fill": "#FBEFFF", "border": "#8250DF", "label": "#1F2328"},
        }
    }

    FIELD_CRUD_SCHEMA: ClassVar[dict[str, Any]] = {
        "installation_id": {"type": ["integer", "null"]},
        "app_id": {"type": ["integer", "null"]},
        "app_slug": {"type": "string"},
        "account_login": {"type": "string"},
        "target_type": {"type": "string"},
        "repository_selection": {"type": "string"},
        "permissions": {"type": "object"},
        "events": {"type": "array"},
        "suspended": {"type": "boolean"},
        "installed_at": {"type": ["string", "null"]},
        "html_url": {"type": "string"},
        "configuration": {"type": "object"},
        "tags": {"type": "object"},
    }

    FIELD_VALIDATION_SCHEMA: ClassVar[dict[str, Any]] = {
        "installation_id": {"validation": "jsonschema", "schema": {"type": ["integer", "null"]}},
        "app_id": {"validation": "jsonschema", "schema": {"type": ["integer", "null"]}},
        "app_slug": {"validation": "jsonschema", "schema": {"type": "string"}},
        "account_login": {"validation": "jsonschema", "schema": {"type": "string"}},
        "target_type": {"validation": "jsonschema", "schema": {"type": "string"}},
        "repository_selection": {"validation": "jsonschema", "schema": {"type": "string"}},
        "permissions": {"validation": "jsonschema", "schema": {"type": "object"}},
        "events": {"validation": "jsonschema", "schema": {"type": "array"}},
        "suspended": {"validation": "jsonschema", "schema": {"type": "boolean"}},
        "html_url": {"validation": "jsonschema", "schema": {"type": "string"}},
        "configuration": {"validation": "jsonschema", "schema": {"type": "object"}},
        "tags": {"validation": "jsonschema", "schema": {"type": "object"}},
    }
    CREATE_REQUIRED: ClassVar[list[str]] = ["installation_id"]

    installation_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    app_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    app_slug = models.CharField(max_length=255, blank=True, default="", db_index=True)
    account_login = models.CharField(max_length=255, blank=True, default="", db_index=True)
    target_type = models.CharField(max_length=32, blank=True, default="")
    #: `all` or `selected` — an installation with `all` follows the account into new repositories
    #: without anyone granting it again.
    repository_selection = models.CharField(max_length=32, blank=True, default="")
    #: The permissions THIS installation was granted, which may be narrower than the App requests.
    permissions = models.JSONField(default=dict, blank=True)
    events = models.JSONField(default=list, blank=True)
    suspended = models.BooleanField(default=False)
    installed_at = models.DateTimeField(null=True, blank=True)
    html_url = models.URLField(max_length=512, blank=True, default="")
    configuration = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "github_core__app_installation"

    def get_name(self) -> str:
        if self.app_slug and self.account_login:
            return f"{self.app_slug} @ {self.account_login}"
        return self.app_slug or (str(self.installation_id) if self.installation_id else "")

    def __str__(self) -> str:
        return self.get_name()
