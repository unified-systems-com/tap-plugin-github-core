"""Git commit — the object a ref resolves to, sliced to identity and signature state.

The convergence node between refs and signatures (`specs/spec-github-core-vocabulary.md`:
7 sources model a *revision*; ranking criterion 3, "a commit joins refs to signatures").
Pulled forward from the friends tier because a ruleset's `required_signatures` rule asks a
question only this node can answer: is the commit on the protected ref actually signed, by
whom, and did GitHub verify it (github-core#57).

A NARROW slice on purpose: no message, tree or parents. This is not commit history — the
grid's own field history on `git_ref.head_sha` is where movement lives.
"""

from typing import Any, ClassVar

from django.db import models

from tap_grid.models import BaseModel


class GitCommit(BaseModel):
    """One commit as the config-layer query observes it at a ref's head.

    Keyed on the repository plus the SHA — not the bare SHA, although a commit is
    content-addressed: GitHub persists the signature VERIFICATION record per repository
    network, so the same SHA in two unrelated networks can carry two different verdicts and a
    SHA-only node would merge them. Author and committer are recorded **as observed**: a `_login` is present only when GitHub resolved the email to an account, and
    an empty login is observed-absent, not unknown.

    **The signature has three states, never two.** `signature_state` is GitHub's own
    verification enum when a signature exists; `unsigned` when GitHub returned `signature:
    null` (an observed value); and a commit whose signature field could not be read at all is
    not emitted as an observation — the degraded field is surfaced on the run instead.
    `signature_valid` is null, not false, on an unsigned commit.

    Spec: plugins/github_core/specs/spec-github-core-v0.md (req-github-core-commits)
    """

    ENTITY_TYPE: ClassVar[str] = "github_core__git_commit"
    ENTITY_NAME: ClassVar[str] = "Git Commit"
    ENTITY_DESCRIPTION: ClassVar[str] = (
        "A commit at a ref's head — who authored and committed it as GitHub observed them, and "
        "whether its signature verified. The object a required-signatures rule checks."
    )
    ENTITY_ICON: ClassVar[str] = "git-commit"
    DEFAULT_DIMENSIONS: ClassVar[dict[str, str]] = {
        "github.observation": "declaration",
        "github.platform": "github.com",
        "github.surface": "git",
    }
    DEFAULT_DISPLAY: ClassVar[dict[str, Any]] = {
        "tap_viz": {
            "shape": "ellipse",
            "colors": {"fill": "#FFFFFF", "border": "#8250DF", "label": "#1F2328"},
        }
    }

    #: GitHub returned `signature: null` — observed, not signed.
    SIGNATURE_UNSIGNED = "unsigned"

    FIELD_CRUD_SCHEMA: ClassVar[dict[str, Any]] = {
        "full_name": {"type": "string", "minLength": 1},
        "sha": {"type": "string", "minLength": 7},
        "committed_date": {"type": ["string", "null"]},
        "authored_date": {"type": ["string", "null"]},
        "author_name": {"type": "string"},
        "author_email": {"type": "string"},
        "author_login": {"type": "string"},
        "committer_name": {"type": "string"},
        "committer_email": {"type": "string"},
        "committer_login": {"type": "string"},
        "signature_kind": {"type": "string", "enum": ["", "gpg", "smime", "ssh"]},
        "signature_state": {"type": "string"},
        "signature_valid": {"type": ["boolean", "null"]},
        "signer_login": {"type": "string"},
        "signed_by_github": {"type": "boolean"},
        "configuration": {"type": "object"},
        "tags": {"type": "object"},
    }

    FIELD_VALIDATION_SCHEMA: ClassVar[dict[str, Any]] = {
        "full_name": {"validation": "jsonschema", "schema": {"type": "string", "minLength": 1}},
        "sha": {"validation": "jsonschema", "schema": {"type": "string", "minLength": 7}},
        "committed_date": {"validation": "jsonschema", "schema": {"type": ["string", "null"]}},
        "authored_date": {"validation": "jsonschema", "schema": {"type": ["string", "null"]}},
        "author_name": {"validation": "jsonschema", "schema": {"type": "string"}},
        "author_email": {"validation": "jsonschema", "schema": {"type": "string"}},
        "author_login": {"validation": "jsonschema", "schema": {"type": "string"}},
        "committer_name": {"validation": "jsonschema", "schema": {"type": "string"}},
        "committer_email": {"validation": "jsonschema", "schema": {"type": "string"}},
        "committer_login": {"validation": "jsonschema", "schema": {"type": "string"}},
        "signature_kind": {
            "validation": "jsonschema",
            "schema": {"type": "string", "enum": ["", "gpg", "smime", "ssh"]},
        },
        "signature_state": {"validation": "jsonschema", "schema": {"type": "string"}},
        # null on an unsigned commit: "not valid" would be a claim about a signature that
        # does not exist.
        "signature_valid": {"validation": "jsonschema", "schema": {"type": ["boolean", "null"]}},
        "signer_login": {"validation": "jsonschema", "schema": {"type": "string"}},
        "signed_by_github": {"validation": "jsonschema", "schema": {"type": "boolean"}},
        "configuration": {"validation": "jsonschema", "schema": {"type": "object"}},
        "tags": {"validation": "jsonschema", "schema": {"type": "object"}},
    }
    CREATE_REQUIRED: ClassVar[list[str]] = ["full_name", "sha"]

    # The repository this observation was made in — half the key, because the verification
    # record is network-scoped and a repository is inside exactly one network.
    full_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    sha = models.CharField(max_length=64, blank=True, default="", db_index=True)
    committed_date = models.DateTimeField(null=True, blank=True)
    authored_date = models.DateTimeField(null=True, blank=True)
    author_name = models.CharField(max_length=255, blank=True, default="")
    author_email = models.CharField(max_length=255, blank=True, default="")
    # The account GitHub resolved the author email to; empty when it resolved to none.
    author_login = models.CharField(max_length=255, blank=True, default="", db_index=True)
    committer_name = models.CharField(max_length=255, blank=True, default="")
    committer_email = models.CharField(max_length=255, blank=True, default="")
    committer_login = models.CharField(max_length=255, blank=True, default="", db_index=True)
    # gpg | smime | ssh, or "" when unsigned.
    signature_kind = models.CharField(max_length=16, blank=True, default="")
    # GitHub's verification state, lower-cased (`valid`, `unknown_key`, `bad_email`, …), or
    # `unsigned` when GitHub returned no signature object at all.
    signature_state = models.CharField(max_length=32, blank=True, default="", db_index=True)
    signature_valid = models.BooleanField(null=True, blank=True)
    signer_login = models.CharField(max_length=255, blank=True, default="", db_index=True)
    # Web-flow commits (merged or edited in the browser) are signed by GitHub's own key.
    signed_by_github = models.BooleanField(default=False)
    configuration = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "github_core__git_commit"

    def get_name(self) -> str:
        return self.sha[:12]

    def __str__(self) -> str:
        return self.get_name()
