"""The github_core collector credential: one envelope, an App and/or a token.

`github_core` owns both data shapes and validates them consumer-side via tap_cares
`require_secret_kind`. The bare kind names follow the `aws_static_access_key` precedent — a kind
name describes the credential type, not the owning plugin.

**One envelope, both credentials, chosen per source.** Neither credential dominates, and that is
measured rather than assumed: only an App sees the account's installed-App inventory and its
fine-grained PAT grants; only an owner-minted PAT sees a ruleset's bypass actors. So the envelope
carries an `app` block, a `pat` block, or both, and each collection source asks for the credential
that yields the fuller answer. Where the better one is absent the collector records WHICH
credential would have shown more, which is what turns "we could not see it" into something an
operator can act on.

The older single-credential kinds (`github_pat`, `github_app`) still validate and are folded into
this shape on read — samsite's shipped record declares `github_pat`, and breaking its boot to tidy
a kind name would be a poor trade.

Spec: plugins/github_core/specs/spec-github-core-v0.md
(req-github-core-secret, req-github-core-app-auth).
"""

from __future__ import annotations

from typing import Any

from tap_cares.exceptions import SecretValidationError
from tap_cares.secrets import SecretRef, require_secret_kind, resolve_secret
from tap_cares.secrets.models import Secret

# The well-known SecretRef for the github_core collector. v0 has no per-
# instance config; the operator drops `github_core/collector.secret.json` under
# TAP_SECRETS_ROOT (no plugin config in core infra — operator-owned, off-grid).
# `scope` names the consuming plugin's slug, not the credential provider
# (req-tap-cares-secrets-consumer-scoping).
GITHUB_SECRET_REF = SecretRef(scope="github_core", key="collector")
#: The current kind: ONE envelope that may carry an App, a token, or both.
GITHUB_KIND = "github"
#: Legacy single-credential kinds, still accepted on read through the transition
#: (`req-github-core-secret-3`). samsite's shipped record still declares `github_pat`, and
#: breaking its boot to tidy a kind name would be a poor trade.
GITHUB_SECRET_KIND = "github_pat"
GITHUB_APP_SECRET_KIND = "github_app"

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
# One derivation for the three schemas below (req-tap-derive-once). These had already
# drifted: two carried the full rule and the combined kind carried a bare `^https://`,
# which still admits `https://u:p@host` and `https://host?x=1`. A security constraint
# spelled out three times is a security constraint that will disagree with itself.
#
# https only, a real host, no userinfo, no query, no fragment. The value is interpolated
# into every request URL and handed to `urlopen`, which honours whatever scheme it is
# given; the REST and GraphQL clients carry a `# nosec B310` citing THIS as what makes
# them safe.
_HTTPS_BASE_URL_PATTERN = r"^https://[^\s/@?#]+(/[^\s?#]*)?$"
_ACCOUNT_LOGIN_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$"
_OWNER_REPO_PATTERN = r"^[^/]+/[^/]+$"

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
            # https only, and no userinfo/query/fragment. Both clients build request URLs from
            # this value and hand them to urlopen, which honours whatever scheme it is given: an
            # http:// base would carry the PAT in cleartext to a host named by the envelope, and
            # a file:// base would turn an API call into a local read. Refusing it here, once, at
            # the point the value enters the system, beats a check at each consumer.
            "pattern": _HTTPS_BASE_URL_PATTERN,
            "description": (
                "GitHub REST API base URL. Rides with the credential because a GitHub Enterprise "
                "Server tenant has its own base URL and its own PAT. Must be https with no "
                "userinfo, query or fragment — it is interpolated into every request URL."
            ),
        },
        "owner": {
            "type": "string",
            "pattern": _ACCOUNT_LOGIN_PATTERN,
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
                "pattern": _OWNER_REPO_PATTERN,
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


# github_core owns this schema for the `github_app` kind's `data` (req-github-core-app-auth).
# The App is created and installed by an operator on their own machine — GitHub has no API for
# creating one — and the private key is the whole credential: it signs a JWT that is exchanged for
# a short-lived installation token. The instance mounts its secrets root read-only and can never
# write this envelope itself.
GITHUB_APP_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "description": (
        "Data block of the github_core collector's github_app credential: the App's numeric id and "
        "PEM private key, plus the collection scope. The App's granted permissions are declared at "
        "installation time on GitHub, derived from the collection manifest — never widened here."
    ),
    "additionalProperties": False,
    "required": ["app_id", "private_key"],
    "anyOf": [{"required": ["owner"]}, {"required": ["repos"]}],
    "properties": {
        "app_id": {
            "type": ["integer", "string"],
            "description": "The App's numeric id, the JWT's `iss`. GitHub renders it as a string in some views.",
        },
        "app_slug": {
            "type": "string",
            "description": "The App's URL slug. Not used for auth — carried so a run can say which App it ran as.",
        },
        "private_key": {
            "type": "string",
            "minLength": 1,
            "description": "PEM private key. Secret material — never logged, never stored on the grid.",
        },
        "api_base_url": {
            "type": "string",
            "minLength": 1,
            "default": "https://api.github.com",
            # https only, no userinfo/query/fragment — carried over from GITHUB_PAT_SCHEMA when
            # this combined kind replaced it. The REST and GraphQL clients build request URLs
            # from this value and hand them to `urlopen`, which honours whatever scheme it is
            # given, and both carry a `# nosec B310` citing THIS constraint as what makes them
            # safe. An http:// base would carry the token in cleartext to a host the envelope
            # names; a file:// base would turn an API call into a local read. app_jwt.py
            # re-checks independently because it must hold for the App path even if this
            # schema is bypassed.
            "pattern": _HTTPS_BASE_URL_PATTERN,
            "description": (
                "GitHub REST API base URL; a GitHub Enterprise Server tenant has its own. Must be "
                "https with no userinfo, query or fragment — it is interpolated into every request URL."
            ),
        },
        "owner": {
            "type": "string",
            "pattern": _ACCOUNT_LOGIN_PATTERN,
            "description": (
                "Login of the organization or user whose repositories are the collection scope. It "
                "also SELECTS the installation to authenticate as: an App installed into several "
                "accounts must be told which one, or it would collect one account's repositories "
                "under another's name."
            ),
        },
        "repos": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": _OWNER_REPO_PATTERN, "minLength": 3},
            "description": "Explicit `owner/repo` targets — an include-filter with `owner`, the scope without it.",
        },
        "initial_run_limit": {
            "type": "integer",
            "minimum": 1,
            "default": 10,
            "description": "Number of latest workflow runs to seed per repository on first collection.",
        },
    },
}

# github_core owns the schema for the current `github` kind: ONE envelope carrying an App, a
# personal access token, or BOTH (req-github-core-secret-3).
#
# Both, because **neither credential dominates** — measured, not assumed. Only a GitHub App can
# read the account's installed-App inventory and its fine-grained PAT grants; only an owner-minted
# PAT can read a ruleset's bypass actors, because GitHub returns that list solely to a caller with
# write access to the ruleset. An either/or envelope therefore condemns every deployment to one
# permanent blind spot, and which one it gets is decided by a field name.
#
# It also keeps the boot layer out of it: `tap_boot` compares an envelope's kind to exactly one
# declared kind (req-boot-required-secrets-5), so a record could never declare "App or PAT". One
# kind with two optional blocks needs no core change, and the shape stays owned here, which is
# where req-github-core-secret-2 already puts it.
GITHUB_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "description": (
        "Data block of the github_core collector credential. Carries a GitHub App, a read-only "
        "personal access token, or both — the collector uses whichever yields the fuller answer "
        "for each source, and records which credential a missing answer would have needed."
    ),
    "additionalProperties": False,
    "anyOf": [{"required": ["app"]}, {"required": ["pat"]}],
    "required": ["owner"],
    "properties": {
        "owner": {
            "type": "string",
            "pattern": _ACCOUNT_LOGIN_PATTERN,
            "description": (
                "Login of the organization or user whose repositories are the collection scope. "
                "Also SELECTS the App installation to authenticate as: an App installed into "
                "several accounts must be told which one, or it would collect one account's "
                "repositories under another's name."
            ),
        },
        "api_base_url": {
            "type": "string",
            "pattern": _HTTPS_BASE_URL_PATTERN,
            "default": "https://api.github.com",
            "description": (
                "GitHub REST API base URL; a GitHub Enterprise Server tenant has its own. HTTPS "
                "only, enforced here rather than trusted: the value is interpolated into a URL "
                "that carries the App JWT and the installation token, and `urlopen` honours "
                "whatever scheme it is given."
            ),
        },
        "app": {
            "type": "object",
            "additionalProperties": False,
            "required": ["app_id", "private_key"],
            "description": (
                "The GitHub App. Sees what a token cannot: the installed-App inventory and the "
                "organization's fine-grained PAT grants."
            ),
            "properties": {
                "app_id": {
                    "type": ["integer", "string"],
                    "description": "The App's numeric id, the JWT's `iss`.",
                },
                "app_slug": {
                    "type": "string",
                    "description": "The App's URL slug. Not used for auth — carried so a run can name what it ran as.",
                },
                "private_key": {
                    "type": "string",
                    "minLength": 1,
                    "description": "PEM private key. Secret material — never logged, never stored on the grid.",
                },
            },
        },
        "pat": {
            "type": "object",
            "additionalProperties": False,
            "required": ["token"],
            "description": (
                "A fine-grained, READ-ONLY personal access token whose resource owner is the "
                "observed account. Sees what an App cannot: a ruleset's bypass actors, which "
                "GitHub discloses only to a caller with write access to the ruleset."
            ),
            "properties": {
                "token": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The token value. Secret material — never logged, never stored on the grid.",
                },
            },
        },
        "repos": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": _OWNER_REPO_PATTERN, "minLength": 3},
            "description": "Explicit `owner/repo` targets — an include-filter over the enumerated repositories.",
        },
        "initial_run_limit": {
            "type": "integer",
            "minimum": 1,
            "default": 10,
            "description": "Number of latest workflow runs to seed per repository on first collection.",
        },
    },
}

#: The data schema for each credential kind this collector accepts. The legacy kinds are read-only
#: compatibility: they still validate, and `normalize_credentials` folds them into the current
#: shape so nothing above the seam knows which arrived.
SCHEMA_BY_KIND: dict[str, dict[str, Any]] = {
    GITHUB_KIND: GITHUB_SCHEMA,
    GITHUB_SECRET_KIND: GITHUB_PAT_SCHEMA,
    GITHUB_APP_SECRET_KIND: GITHUB_APP_SCHEMA,
}


def normalize_credentials(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Fold any accepted envelope kind into the current `{owner, api_base_url, app?, pat?, ...}` shape.

    One place converts, so every caller above the auth seam reasons about one shape and the
    transition off the single-credential kinds is a detail rather than a branch in every consumer.
    """
    if kind == GITHUB_KIND:
        return dict(data)
    folded: dict[str, Any] = {
        key: data[key] for key in ("owner", "api_base_url", "repos", "initial_run_limit") if key in data
    }
    if kind == GITHUB_APP_SECRET_KIND:
        folded["app"] = {
            key: data[key] for key in ("app_id", "app_slug", "private_key") if key in data
        }
    elif kind == GITHUB_SECRET_KIND:
        folded["pat"] = {"token": data["token"]}
    return folded


class GithubCredentialError(Exception):
    """The collector's credential is missing or otherwise unusable."""


def resolve_github_secret(ref: SecretRef = GITHUB_SECRET_REF) -> Secret:
    """Resolve and validate the collector secret, whichever kind was placed.

    Dispatches on the envelope's own `kind` and validates against that kind's schema. An unknown
    kind is refused by name rather than by schema failure: "kind 'github_oauth' is not one this
    collector accepts" is a fixable message, where a wall of schema errors is not.

    Raises `SecretNotFoundError` (missing) or `SecretValidationError` (unsupported kind / bad
    `data` shape) from the secrets subsystem.
    """
    secret = resolve_secret(ref)
    schema = SCHEMA_BY_KIND.get(secret.kind)
    if schema is None:
        raise SecretValidationError(
            f"Secret {secret.ref.qualified!r} has kind {secret.kind!r}; github_core accepts "
            f"{' or '.join(sorted(SCHEMA_BY_KIND))}."
        )
    require_secret_kind(secret, secret.kind, data_schema=schema)
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
