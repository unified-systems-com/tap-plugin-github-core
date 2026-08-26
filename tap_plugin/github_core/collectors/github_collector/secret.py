"""github_pat secret kind data schema + resolution helper.

`github_core` owns the `github_pat` data shape and validates it consumer-side
via tap_cares `require_secret_kind`. The bare kind name follows the
`aws_static_access_key` precedent — kind names describe the credential type,
not the owning plugin.

Spec: plugins/github_core/specs/spec-github-core-v0.md
(req-github-core-secret).
"""

from __future__ import annotations

from typing import Any

from tap_cares.secrets import SecretRef, require_secret_kind, resolve_secret
from tap_cares.secrets.models import Secret

# The well-known SecretRef for the github_core collector. v0 has no per-
# instance config; the operator drops `github_core/collector.secret.json` under
# TAP_SECRETS_ROOT (no plugin config in core infra — operator-owned, off-grid).
# `scope` names the consuming plugin's slug, not the credential provider
# (req-tap-cares-secrets-consumer-scoping).
GITHUB_SECRET_REF = SecretRef(scope="github_core", key="collector")
GITHUB_SECRET_KIND = "github_pat"

# github_core owns this schema for the kind's `data` (req-github-core-secret-2).
# Strict: additionalProperties false. Behavioral knobs beyond `initial_run_limit`
# were pruned in the spec's Pruned Knobs section; the schema enforces that
# pruning at load time.
#
# Scope (req-github-core-org-scope): the collector's target is an ACCOUNT — `owner`, the
# login of a GitHub organization or user — and the collector enumerates that account's
# repositories itself. `repos` is then an optional include-filter. A `repos`-only envelope
# (no `owner`) remains valid: the explicit list IS the scope — the degenerate run config,
# never a parallel code path (tap#142). At least one of the two must be present.
GITHUB_PAT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "description": (
        "Data block of the github_core collector's github_pat credential: a fine-grained, "
        "READ-ONLY personal access token whose resource owner is the observed account, plus the "
        "collection scope. Least privilege: Metadata + Contents + Actions read on the scoped "
        "repositories; Administration read only if runner nodes are wanted. Never write scopes."
    ),
    "additionalProperties": False,
    "required": ["token"],
    "anyOf": [{"required": ["owner"]}, {"required": ["repos"]}],
    "properties": {
        "token": {
            "type": "string",
            "minLength": 1,
            "description": "The PAT value. Secret material — never logged, never stored on the grid.",
        },
        "api_base_url": {
            "type": "string",
            "minLength": 1,
            "default": "https://api.github.com",
            "description": (
                "GitHub REST API base URL. Rides with the credential because a GitHub Enterprise "
                "Server tenant has its own base URL and its own PAT."
            ),
        },
        "owner": {
            "type": "string",
            "pattern": r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$",
            "description": (
                "Login of the GitHub organization or user whose repositories are the collection "
                "scope. The collector enumerates the account's repositories (org first, user "
                "fallback) and records the enumeration on the run."
            ),
        },
        "repos": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "string",
                "pattern": r"^[^/]+/[^/]+$",
                "minLength": 3,
            },
            "description": (
                "Explicit `owner/repo` targets. With `owner` present: an include-filter over the "
                "enumerated repositories. Without `owner`: the scope itself (the legacy, "
                "degenerate form)."
            ),
        },
        "initial_run_limit": {
            "type": "integer",
            "minimum": 1,
            "default": 10,
            "description": "Number of latest workflow runs to seed per repository on first collection.",
        },
    },
}


class GithubCredentialError(Exception):
    """The github_pat secret is missing or otherwise unusable."""


def resolve_github_secret(ref: SecretRef = GITHUB_SECRET_REF) -> Secret:
    """Resolve and validate the github_pat collector secret.

    Raises `SecretNotFoundError` (missing) or `SecretValidationError`
    (wrong kind / bad `data` shape) from the secrets subsystem.
    """
    secret = resolve_secret(ref)
    require_secret_kind(secret, GITHUB_SECRET_KIND, data_schema=GITHUB_PAT_SCHEMA)
    return secret


def collection_owner(data: dict[str, Any]) -> str | None:
    """The account scope (`owner`) if the envelope declares one, else None (repos-only form)."""
    owner = data.get("owner")
    return str(owner) if owner else None


def explicit_repos(data: dict[str, Any]) -> list[str]:
    """The envelope's explicit `owner/repo` list — the scope, or the filter, depending on `owner`."""
    return [str(r) for r in (data.get("repos") or [])]


def api_base_url(data: dict[str, Any]) -> str:
    """Return the API base URL with the documented default applied."""
    return data.get("api_base_url") or "https://api.github.com"


def initial_run_limit(data: dict[str, Any]) -> int:
    """Return the initial-run seed limit with the documented default applied."""
    return int(data.get("initial_run_limit") or 10)
