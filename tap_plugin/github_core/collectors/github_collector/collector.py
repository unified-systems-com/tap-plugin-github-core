"""GitHubCollector — CollectorBase subclass driving the v0 collection.

Spec: plugins/github_core/specs/spec-github-core-v0.md
(req-github-core-collector). Two-phase run: collection + enrichment, both
through `CollectorBase.submit_grift`.
"""

from __future__ import annotations

import base64
import fnmatch
import logging
from typing import Any, ClassVar

from tap_plugin.identity_core.issuer import oidc_issuer_id, oidc_issuer_node_envelope

from tap_cares.collectors import (
    CollectorDocRef,
    CollectorReadinessStatus,
    CollectorSelfTestResult,
    check_fail,
    check_pass,
)
from tap_cares.collectors.base import CollectorBase
from tap_cares.exceptions import (
    SecretError,
    SecretNotFoundError,
    SecretValidationError,
)

from .api_client import GithubAPIError, GithubClient
from .app_jwt import GithubAppAuthError
from .auth import PREFER_APP, PREFER_PAT, GithubAuth
from .batch import (
    assemble_batch,
    edge_envelope,
    node_envelope,
)
from .enrichment import resolve_links
from .graphql_client import GithubGraphQLClient, GithubGraphQLError
from .identity import (
    account_id,
    actions_cache_id,
    app_installation_id,
    edge_id,
    environment_id,
    git_ref_id,
    github_app_id,
    job_id,
    github_action_id,
    platform_id,
    repository_id,
    ruleset_id,
    rule_suite_id,
    run_id,
    ruleset_id,
    runner_id,
    uses_action_edge_id,
    workflow_id,
    workflow_job_id,
)
from .manifest import load_collection_manifest, load_link_manifest
from .parser import (
    PIN_BRANCH,
    PIN_SHA,
    PIN_TAG,
    PIN_UNRESOLVED,
    is_pinned,
    parse_workflow_yaml,
)
from .secret import (
    GITHUB_SECRET_REF,
    api_base_url,
    collection_owner,
    explicit_repos,
    initial_run_limit,
    resolve_github_secret,
)

logger = logging.getLogger(__name__)

# Log site tokens for the recorder. Minted via scripts/log-site-id.
_SITE_RUN_STARTED = "8fb5"
_SITE_ABORT_SECRET = "64be"
_SITE_ABORT_API = "54ce"
_SITE_REPO_DONE = "d98c"
_SITE_RUNNER_DEGRADED = "b969"
_SITE_WORKFLOW_YAML_MISSING = "9573"
_SITE_BATCH_SUBMITTED = "d66b"
_SITE_ENRICHMENT_SUMMARY = "fd9e"
_SITE_RUN_JOBS_MISSING = "bcff"
_SITE_INCREMENTAL_WINDOW = "c2ca"
_SITE_NON_TERMINAL_REFRESH = "6558"
_SITE_RUN_NOT_FOUND = "f938"
_SITE_LOCAL_ACTION_DEFERRED = "b148"
_SITE_DEPENDABOT_APP = "a7c1"
_SITE_SCOPE_ENUMERATED = "462a"
_SITE_ABORT_SCOPE = "8d47"
_SITE_FILTER_UNMATCHED = "d087"
_SITE_GRAPHQL_CONFIG = "1de2"
_SITE_GRAPHQL_DEGRADED = "8630"
_SITE_EDGE_DROPPED = "0fff"
_SITE_REPO_FAILED = "4326"
_SITE_COLLECTION_PARTIAL = "11d2"
_SITE_ENVELOPE_COLLAPSED = "32c2"
_SITE_LINK_RULE_SKIPPED = "1cb8"
_SITE_REFS_TRUNCATED = "8334"
_SITE_RULESET_BYPASS_UNOBSERVABLE = "d75a"
_SITE_RULESET_DETAIL_DEGRADED = "21d1"
_SITE_CACHE_DEGRADED = "21fd"
_SITE_RULE_SUITE_DEGRADED = "3095"
_SITE_RULE_SUITE_FOUND = "3b60"
_SITE_CACHES_TRUNCATED = "0f41"
_SITE_INSTALLATIONS_UNREACHABLE = "1825"
_SITE_INSTALLATIONS_COLLECTED = "c3d0"
_SITE_BYPASS_ACTOR_UNMODELLED = "5dd2"
_SITE_AUTH_MODE = "3e4d"
_SITE_ACTION_REF_NOT_FOUND = "de95"
_SITE_ACTIONS_USED = "13f5"

#: The dimension keys that scope an envelope to ONE repository. Stripped from a node shared
#: across the scope (an action, an app), kept on the edges that use it.
_REPO_SCOPED_DIMENSION_KEYS = ("github.owner", "github.repo")


def _group_action_calls(action_refs: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Fold a job's per-step action refs into one entry per (action path, declared ref).

    Two steps calling the same action at the same ref are one relationship with two positions;
    the same action at two refs is two relationships.
    """
    by_call: dict[tuple[str, str], dict[str, Any]] = {}
    for ref in action_refs:
        action_path = str(ref.get("action_path") or ref.get("action") or "")
        if not action_path:
            continue
        entry = by_call.setdefault((action_path, str(ref.get("ref") or "")), {**ref, "step_indexes": []})
        entry["step_indexes"].append(int(ref.get("step_index", 0)))
    return by_call

# GitHub surfaces enabled platform apps (Dependabot) in the Actions workflow
# list under synthetic ``dynamic/<app>/...`` paths. These are not repo CI
# workflows — they are platform apps enabled on the repo — so we reclassify
# them as github_app + ENABLED_ON instead of github_workflow. Map the synthetic
# path prefix to the app's stable slug + display metadata.
_SYNTHETIC_APP_BY_PATH_PREFIX: dict[str, dict[str, str]] = {
    "dynamic/dependabot/": {
        "slug": "dependabot",
        "name": "Dependabot",
        "html_url": "https://github.com/apps/dependabot",
        "description": "GitHub's managed dependency-update and security-alert app.",
    },
}

_DOCS = (
    CollectorDocRef(
        plugin="github_core",
        doc="collector",
        section="self-test",
        label="GitHub Core collector self-test",
    ),
)

# GitHub Actions `status` values that are non-terminal — runs in any of these
# states are re-fetched on every collection until they reach `completed`
# (`req-github-core-collector-4`). Enumerating terminal-vs-not is more robust
# than enumerating "the non-terminal ones": GitHub may add new in-flight
# states (`waiting`, `pending`, `requested`, `action_required`, etc.) and
# anything we don't recognize is safer treated as "keep watching."
_TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"completed"})

# How many cache entries to pull per repository. GitHub returns them most-recently-accessed
# first, so a cap keeps the freshest — and the collector says how many it left, because a
# truncated cache list that reads as complete would make "no cache from that ref" a wrong answer.
_CACHE_LIMIT_PER_REPO = 100
#: Rule suites collected per repository per run. Bypasses only, so this is a ceiling on
#: FINDINGS rather than on traffic — a repository with more than this many bypasses in the
#: window has a bigger problem than truncation.
_RULE_SUITE_LIMIT_PER_REPO = 100
#: ALWAYS sent. Omitting `time_period` makes GitHub default to `day`, so a repository with a
#: month of bypasses reads as a quiet one (req-github-core-rule-suites-5).
_RULE_SUITE_WINDOW = "month"

# Ruleset condition tokens GitHub uses in place of a ref pattern.
_REF_TOKEN_DEFAULT_BRANCH = "~DEFAULT_BRANCH"  # nosec B105 — GitHub ref token, not a secret
_REF_TOKEN_ALL = "~ALL"  # nosec B105 — GitHub ref token, not a secret

# Which ref type a ruleset's target governs. A `push` ruleset restricts the push itself (file
# sizes, secret scanning) rather than a named ref, so it resolves to no refs at all.
_REF_TYPE_BY_RULESET_TARGET = {"branch": "branch", "tag": "tag"}

# The platform instance every collected account/repo/workflow hangs under.
# v0 is github.com only; a GHES host would key on its own hostname.
_PLATFORM_HOST = "github.com"
_PLATFORM_DIMENSIONS = {"github.platform": "github.com"}

# GitHub Actions' OIDC issuer URL — the identity convergence node github enables
# on every repo. The node itself (id, canonical host, provider, display name) is
# minted by identity_core's general-case helper from this URL; github owns none
# of that vocabulary any more (see plugins/identity_core).
_OIDC_ISSUER_URL = "https://token.actions.githubusercontent.com"


class GithubCollectorError(Exception):
    """Unrecoverable error during the github_core collection run."""


def _owner_of(full_name: str) -> str:
    """`owner` from `owner/repo`. Ruleset identity keys on the owner, not the repository."""
    return full_name.partition("/")[0]


class GithubCollector(CollectorBase):
    """GitHub Actions collector — one credential, account-scoped (or explicit repos), two-phase run.

    Transport is a deliberate hybrid (req-github-core-graphql-config): the CONFIG layer arrives via
    GraphQL in one request per 100 repositories, the OPERATION layer (runs, jobs, runners) via REST
    because GitHub's GraphQL API exposes no Actions executions.
    """

    # Config layer keyed by `owner/repo`, populated in run(); empty for a repos-only scope and on
    # any path that does not go through run(), so every reader must treat it as optional.
    _config: ClassVar[dict[str, Any]] = {}

    @classmethod
    def self_test(cls) -> CollectorSelfTestResult:
        """Operator-facing readiness check for the github_core collector.

        Checks in order; each short-circuits the rest on failure with an actionable readiness
        status:
          1. GITHUB_SECRET_PRESENT — a collector credential is placed
          2. GITHUB_SECRET_VALID   — it is a kind this collector accepts and its schema validates
          3. GITHUB_CREDENTIAL_USABLE — the credential yields a bearer token. For a PAT that is
             the token itself; for an App it means the key signs a JWT, the App is installed on
             the named account, and the installation mints a token — the whole chain, before
             anything relies on it (req-github-core-app-auth-9)
          4. GITHUB_API_REACHABLE  — `GET /rate_limit` succeeds within budget
          4. GITHUB_OWNER_ACCESS   — the account scope enumerates (`/orgs|/users/{owner}/repos`)
             GITHUB_REPO_ACCESS    — per-explicit-repo `GET /repos/{owner}/{repo}`
                                     succeeds; surfaces which repo(s) fail

        The empty-body-404 retry in `api_client` is deliberately disabled for
        self-test paths: a real auth/access 404 should surface immediately,
        not after the full backoff budget (which won't change the outcome).
        """
        checks: list = []

        # 1. Secret present.
        try:
            secret = resolve_github_secret(GITHUB_SECRET_REF)
        except SecretNotFoundError as exc:
            checks.append(
                check_fail(
                    "GITHUB_SECRET_PRESENT",
                    f"No collector credential is configured: {exc}",
                    readiness_status=CollectorReadinessStatus.UNCONFIGURED,
                    docs=_DOCS,
                )
            )
            return CollectorSelfTestResult.from_checks(
                checks,
                summary="No collector credential is configured.",
                docs=_DOCS,
            )
        except (SecretValidationError, SecretError) as exc:
            # 2. Secret malformed (schema failed).
            checks.append(
                check_fail(
                    "GITHUB_SECRET_VALID",
                    f"Collector credential is unusable: {exc}",
                    readiness_status=CollectorReadinessStatus.MISCONFIGURED,
                    docs=_DOCS,
                )
            )
            return CollectorSelfTestResult.from_checks(
                checks,
                summary="Collector credential is unusable.",
                docs=_DOCS,
            )
        checks.append(
            check_pass(
                "GITHUB_SECRET_VALID",
                f"Collector credential resolves; kind {secret.kind!r}.",
                context={"kind": secret.kind},
                docs=_DOCS,
            )
        )

        data = dict(secret.data)
        owner = collection_owner(data)
        repos: list[str] = explicit_repos(data)

        # 3. The credential yields a bearer token. For an App this exercises the entire chain —
        # key signs a JWT, the App is installed on the named account, the installation mints a
        # token — which is exactly the part an operator cannot check by reading the envelope.
        auth = GithubAuth(kind=secret.kind, data=data, api_base_url=api_base_url(data))
        try:
            token = auth.token()
        except GithubAppAuthError as exc:
            checks.append(
                check_fail(
                    "GITHUB_CREDENTIAL_USABLE",
                    f"App credential could not produce an installation token: {exc}",
                    readiness_status=CollectorReadinessStatus.MISCONFIGURED,
                    docs=_DOCS,
                )
            )
            return CollectorSelfTestResult.from_checks(
                checks,
                summary="App credential could not produce an installation token.",
                docs=_DOCS,
            )
        installation = auth.installation or {}
        checks.append(
            check_pass(
                "GITHUB_CREDENTIAL_USABLE:app",
                f"App chain proven — key signs, installation {installation.get('id')} on "
                f"{(installation.get('account') or {}).get('login', '?')}, token minted.",
                context={"installation_id": installation.get("id")},
                docs=_DOCS,
            )
            if auth.has_app
            else check_pass(
                "GITHUB_CREDENTIAL_ABSENT:app",
                f"No App credential — {auth.absent_note(PREFER_APP)}.",
                context={"missing": PREFER_APP},
                docs=_DOCS,
            )
        )
        # The token gets its OWN liveness probe. A dead token beside a live App would otherwise
        # pass this check and degrade at collection time — the failure arriving through the check
        # built to catch it. A missing credential and a dead one must not read the same.
        if auth.has_pat:
            try:
                # Through GithubClient, not around it: the self-test's transport is injectable so
                # a caller can stub it, and a liveness probe that reaches past it would make a
                # stubbed self-test talk to the real GitHub.
                GithubClient(
                    token=auth.token(prefer=PREFER_PAT),
                    api_base_url=api_base_url(data),
                    retry_empty_404=False,
                ).get("/rate_limit")
            except (GithubAppAuthError, GithubAPIError) as exc:
                checks.append(
                    check_fail(
                        "GITHUB_CREDENTIAL_USABLE:pat",
                        f"A personal access token is placed but does not authenticate: {exc}. "
                        f"Surfaces only it can read — a ruleset's bypass actors — will report as "
                        f"unobservable until it is replaced.",
                        readiness_status=CollectorReadinessStatus.MISCONFIGURED,
                        docs=_DOCS,
                    )
                )
            else:
                checks.append(
                    check_pass(
                        "GITHUB_CREDENTIAL_USABLE:pat",
                        "Personal access token authenticates.",
                        docs=_DOCS,
                    )
                )
        else:
            checks.append(
                check_pass(
                    "GITHUB_CREDENTIAL_ABSENT:pat",
                    f"No personal access token — {auth.absent_note(PREFER_PAT)}.",
                    context={"missing": PREFER_PAT},
                    docs=_DOCS,
                )
            )

        # 4. API reachable + the credential authenticates — GET /rate_limit.
        # No-retry client so a real 401/403 surfaces immediately.
        client = GithubClient(
            token=token,
            api_base_url=api_base_url(data),
            retry_empty_404=False,
        )
        try:
            rate = client.get("/rate_limit")
        except GithubAPIError as exc:
            checks.append(
                check_fail(
                    "GITHUB_API_REACHABLE",
                    f"GitHub /rate_limit failed: status={exc.status} " f"body={exc.body[:200] or '(empty)'}",
                    readiness_status=CollectorReadinessStatus.ERROR,
                    docs=_DOCS,
                )
            )
            return CollectorSelfTestResult.from_checks(
                checks,
                summary="GitHub API unreachable or credential auth failed.",
                docs=_DOCS,
            )
        core = rate.get("rate") or rate.get("resources", {}).get("core", {})
        checks.append(
            check_pass(
                "GITHUB_API_REACHABLE",
                f"GitHub API reachable; rate limit " f"{core.get('used', '?')}/{core.get('limit', '?')} used.",
                context={"rate": core},
                docs=_DOCS,
            )
        )

        # 4a. Account scope (req-github-core-org-scope): the credential can see the owner and
        # enumerate its repositories. Bounded — one listing walk, not a probe per repo, so an
        # org of hundreds of repos self-tests in seconds. Explicit repos (filter or legacy
        # scope) are still probed one by one below.
        repo_access_ok = True
        if owner is not None:
            try:
                try:
                    listing = client.get_paginated(f"/orgs/{owner}/repos", params={"type": "all", "per_page": "100"})
                except GithubAPIError as exc:
                    if exc.status != 404:
                        raise
                    listing = client.get_paginated(f"/users/{owner}/repos", params={"type": "all", "per_page": "100"})
            except GithubAPIError as exc:
                repo_access_ok = False
                checks.append(
                    check_fail(
                        f"GITHUB_OWNER_ACCESS:{owner}",
                        f"Credential cannot enumerate repositories under {owner}: status={exc.status} "
                        f"body={exc.body[:200] or '(empty)'}",
                        readiness_status=CollectorReadinessStatus.ERROR,
                        docs=_DOCS,
                    )
                )
            else:
                checks.append(
                    check_pass(
                        f"GITHUB_OWNER_ACCESS:{owner}",
                        f"Credential enumerates {len(listing)} repo(s) under {owner}"
                        f"{'' if client.last_walk_complete else ' (walk INCOMPLETE — page cap hit)'}.",
                        context={"owner": owner, "enumerated": len(listing), "complete": client.last_walk_complete},
                        docs=_DOCS,
                    )
                )
        # 4b. Per-repo access. Each failure is recorded but doesn't short-
        # circuit the rest — operator wants to see ALL the broken repos in
        # one run, not just the first.
        for repo in repos:
            try:
                client.get(f"/repos/{repo}")
            except GithubAPIError as exc:
                repo_access_ok = False
                checks.append(
                    check_fail(
                        f"GITHUB_REPO_ACCESS:{repo}",
                        f"Credential cannot access {repo}: status={exc.status} " f"body={exc.body[:200] or '(empty)'}",
                        readiness_status=CollectorReadinessStatus.ERROR,
                        docs=_DOCS,
                    )
                )
            else:
                checks.append(
                    check_pass(
                        f"GITHUB_REPO_ACCESS:{repo}",
                        f"Credential has access to {repo}.",
                        context={"repo": repo},
                        docs=_DOCS,
                    )
                )

        return CollectorSelfTestResult.from_checks(
            checks,
            summary=(
                f"GitHub Core collector is ready; {len(repos)} repo(s) accessible."
                if repo_access_ok
                else "GitHub Core collector credential cannot access one or more configured repos."
            ),
            docs=_DOCS,
        )

    def _abort(self, site: str, code: str, message: str) -> None:
        self.record_error(site, code, message)
        raise GithubCollectorError(message)

    def run(self) -> None:
        self.record_info(_SITE_RUN_STARTED, "RUN_STARTED", "GitHub Core collection started.")
        # github_app nodes are singletons shared across repos; dedupe the node
        # emission across the whole run (the ENABLED_ON edges still fan in).
        self._emitted_app_ids: set[str] = set()
        #: Actor logins already emitted as accounts this run. One person bypassing in
        #: nineteen repositories is ONE account node, not nineteen.
        self._emitted_actor_logins: set[str] = set()
        # Rulesets are singletons too, and far more fan-in than apps: an
        # organization-sourced ruleset is reported by every repository it governs
        # (measured: 6 rulesets across 60 attachments on a 19-repo org).
        self._emitted_ruleset_ids: set[str] = set()

        # --- secret resolution (unrecoverable on failure) ---
        try:
            secret = resolve_github_secret(GITHUB_SECRET_REF)
        except SecretError as exc:
            self._abort(_SITE_ABORT_SECRET, "SECRET_UNUSABLE", f"github_pat secret unusable: {exc}")
        data = dict(secret.data)
        run_limit = initial_run_limit(data)

        # One seam, two credential kinds (req-github-core-app-auth-1). Nothing below this line
        # knows which arrived, except where an App-only surface is worth attempting.
        self._auth = GithubAuth(kind=secret.kind, data=data, api_base_url=api_base_url(data))
        try:
            token = self._auth.token()
        except GithubAppAuthError as exc:
            self._abort(_SITE_ABORT_SECRET, "GITHUB_APP_AUTH_FAILED", f"App credential unusable: {exc}")
        self.record_info(
            _SITE_AUTH_MODE,
            "AUTH_MODE",
            "Credentials: "
            + ", ".join(
                filter(
                    None,
                    [
                        f"App (installation {(self._auth.installation or {}).get('id')})"
                        if self._auth.has_app
                        else "",
                        "personal access token" if self._auth.has_pat else "",
                    ],
                )
            )
            + (
                ""
                if self._auth.has_app and self._auth.has_pat
                else " — one credential only; some surfaces will read as unobservable."
            ),
            message_data={"held": self._auth.held},
        )

        client = GithubClient(token=token, api_base_url=api_base_url(data))
        # A second client bound to the personal access token, when one is in the envelope. It
        # exists for exactly one reason: GitHub returns a ruleset's bypass actors only to a caller
        # with write access to the ruleset, and an owner's PAT has it where a read-only App never
        # will. Per-source rather than a global preference order, because "prefer the App" would
        # silently lose bypass actors on precisely the deployments that placed both credentials.
        self._pat_client = (
            GithubClient(token=self._auth.token(prefer=PREFER_PAT), api_base_url=api_base_url(data))
            if self._auth.has_pat
            else None
        )
        # Per-run caches for objects shared across repositories: one organization ruleset applies
        # to every repository it matches, and re-fetching its detail per repo would be 19 identical
        # calls for one answer.
        self._ruleset_details: dict[tuple[str, int], dict[str, Any] | None] = {}
        # Whether the token, when present, actually answered the ruleset endpoint. "Present" and
        # "answered" are different facts, and an unreadable bypass list means something different
        # under each.
        self._pat_ruleset_status: str = "untried"
        self._emitted_installation_ids: set[str] = set()
        # What `~DEFAULT_BRANCH` resolves to, keyed `owner/repo#refs/heads/x`. Repo-scoped on
        # purpose: one repository's default is `main` and another's is `master`, and a bare ref
        # path would let the first repo's default mark the second repo's same-named branch.
        self._default_refs: set[str] = set()
        # Refs of an in-scope repository, `ref path -> head sha`, built on first demand from the
        # config layer already in hand. What lets `uses: acme/tool@main` be resolved to a branch
        # and a commit without a request, and what leaves `actions/checkout@v4` honestly
        # unresolved: that repository is not in scope, and nothing here goes looking.
        self._refs_by_repo: dict[str, dict[str, str]] = {}
        #: Run-level tally for the actions summary, so a run can say what it saw.
        self._action_usage: dict[str, Any] = {"actions": set(), "edges": 0, "unpinned": 0, "unobservable": 0}

        # --- scope resolution: the account's repositories, enumerated (req-github-core-org-scope)
        owner = collection_owner(data)
        # The configuration layer arrives in one GraphQL request per 100 repositories — metadata,
        # default branch, rulesets, environments and every workflow file's YAML inlined. It
        # replaces the enumeration walk, one metadata call per repo, and one Contents call per
        # workflow file; measured on a 19-repo org that is ~85 REST calls collapsed into 1, at a
        # cost of 1 rate-limit point (req-github-core-graphql-config). REST still serves the
        # operation layer below, because GraphQL exposes no workflow runs or jobs.
        self._config: dict[str, dict[str, Any]] = {}
        if owner is not None:
            try:
                self._config = self._fetch_config_layer(data, owner)
            except GithubGraphQLError as exc:
                self._abort(_SITE_ABORT_SCOPE, "GITHUB_GRAPHQL_FAILED", f"config-layer fetch failed: {exc}")
        try:
            repos = self._resolve_repos(client, owner, explicit_repos(data))
        except GithubAPIError as exc:
            self._abort(_SITE_ABORT_SCOPE, f"GITHUB_SCOPE_{exc.status}", f"scope enumeration failed: {exc}")

        # --- manifests (load-time JSON Schema validation; errors abort) ---
        load_collection_manifest()  # validates; engine is procedural in v0
        link_manifest = load_link_manifest()

        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        # --- platform singleton: one github.com node per run, the top of the
        # account → repo → workflow tree. Synthesized (no API enumerates "the
        # platform"); deterministic id so re-runs upsert in place and a
        # hand-written GRIFT node with the same host upserts cleanly onto it.
        platform_uuid = platform_id(_PLATFORM_HOST)
        nodes.append(
            node_envelope(
                entity_id=platform_uuid,
                entity_type="github_core__github_platform",
                name=_PLATFORM_HOST,
                dimensions=dict(_PLATFORM_DIMENSIONS),
                fields={
                    "host": _PLATFORM_HOST,
                    "html_url": f"https://{_PLATFORM_HOST}",
                    "configuration": {},
                    "tags": {},
                },
            )
        )

        # --- OIDC issuer: GitHub Actions' identity issuer, the convergence node,
        # minted through the identity_core general-case helper (no privileged
        # creator — samsite mints the same node from its own observation and both
        # merge by deterministic id). AWS federation trusts it (TRUSTS_ISSUER,
        # resolved in enrichment) and Sigstore vouches identities by it
        # (IDENTITY_VOUCHED_BY, emitted by the sigstore consumer).
        nodes.append(oidc_issuer_node_envelope(_OIDC_ISSUER_URL))

        # --- installed Apps: an App-only surface (req-github-core-app-installations).
        self._collect_app_installations(client, owner, nodes, edges)

        # --- collection phase: per-repo walk ---
        failed: list[str] = []
        for full_name in repos:
            try:
                self._collect_repo(client, full_name, run_limit, nodes, edges, platform_uuid)
            except GithubAPIError as exc:
                # Contain the failure to its repo. A scope of one repo could treat any API error
                # as fatal; a scope of nineteen cannot, because across thousands of calls a
                # transient timeout is a certainty, and aborting throws away every repo that
                # DID collect. The run continues and reports honestly instead.
                failed.append(full_name)
                self.record_warn(
                    _SITE_REPO_FAILED,
                    f"REPO_FAILED_{exc.status}",
                    f"Skipped {full_name}: {exc}",
                    message_data={"repo": full_name, "status": exc.status},
                )
                continue
            self.record_info(_SITE_REPO_DONE, "REPO_DONE", f"Collected {full_name}")

        if failed and len(failed) == len(repos):
            # Everything failed: that is not a transient blip, it is a broken credential or a
            # dead API. Fail loudly rather than submitting an empty batch that looks like an
            # organization with nothing in it.
            self._abort(_SITE_ABORT_API, "GITHUB_API_ALL_REPOS_FAILED",
                        f"every repo in scope failed to collect ({len(failed)}/{len(repos)})")
        if failed:
            # Load-bearing for tombstoning (req-github-core-org-scope-3, tap#140): the scope was
            # completely ENUMERATED but not completely COLLECTED, so absence within this run is
            # not evidence of deletion. Anything inferring removal must read this first.
            self.record_warn(
                _SITE_COLLECTION_PARTIAL,
                "COLLECTION_PARTIAL",
                f"Collected {len(repos) - len(failed)} of {len(repos)} repo(s); "
                f"{len(failed)} skipped after API errors. Absence in this batch is NOT evidence "
                f"of deletion.",
                message_data={"collected": len(repos) - len(failed), "failed": sorted(failed),
                              "collection_complete": False},
            )

        # --- submission phase ---
        # Collapse envelopes that repeat across repos before submission. Several nodes and
        # edges are legitimately shared by every repo in a scope — the account, the platform,
        # the OIDC issuer and its ENABLED_ON edges — and the per-repo walk emits one copy each
        # time. At a one-repo scope that never showed; at 19 repos GRIFT rejected the batch
        # for duplicate entity ids and NOTHING landed. Deduping is correct rather than
        # defensive: these are the same observation seen from several repos, and identity is
        # deterministic, so the last copy is as good as the first.
        nodes, node_dupes = self._collapse_by_entity_id(nodes)
        edges, edge_dupes = self._collapse_by_entity_id(edges)
        # Every edge in the COLLECTION batch must land on a node in the same batch — cross-grid
        # edges are the enrichment phase's job and are resolved against what is already on the
        # grid. A run can name a workflow that has since been deleted or renamed, which at a
        # one-repo scope never happened and at nineteen rejected the whole batch for a dangling
        # endpoint. Drop those edges and say how many, rather than losing every repo.
        edges, dropped = self._drop_dangling_edges(edges, {e["entity"]["entity_id"] for e in nodes})
        if dropped:
            self.record_warn(
                _SITE_EDGE_DROPPED,
                "EDGES_DROPPED_DANGLING",
                f"Dropped {len(dropped)} edge(s) whose endpoint was not collected — most often a run "
                f"naming a workflow that no longer exists.",
                message_data={"count": len(dropped), "edge_types": sorted({d for d in dropped})},
            )
        if node_dupes or edge_dupes:
            self.record_info(
                _SITE_ENVELOPE_COLLAPSED,
                "ENVELOPES_COLLAPSED",
                f"Collapsed {node_dupes} repeated node envelope(s) and {edge_dupes} edge envelope(s) "
                f"shared across the scope's repos.",
                message_data={"nodes": node_dupes, "edges": edge_dupes},
            )

        scope_label = owner if owner is not None else ", ".join(repos)
        batch_dims = {"github.platform": "github.com"}
        if owner is not None:
            batch_dims["github.owner"] = owner
        github_batch = assemble_batch(
            batch_name=f"github_core collection: {scope_label}",
            description=f"GitHub Actions plumbing for {len(repos)} repo(s) in scope {scope_label}.",
            nodes=nodes,
            edges=edges,
            batch_dimensions=batch_dims,
        )
        self.submit_grift(github_batch)
        self.record_info(
            _SITE_BATCH_SUBMITTED,
            "COLLECTION_BATCH_SUBMITTED",
            f"Submitted collection batch with {len(nodes)} node(s) + {len(edges)} edge(s).",
        )
        usage = self._usage_tally()
        self.record_info(
            _SITE_ACTIONS_USED,
            "ACTIONS_USED",
            f"{len(usage['actions'])} distinct action(s) across {usage['edges']} job usage(s); "
            f"{usage['unpinned']} usage(s) pinned to a mutable name or nothing, of which "
            f"{usage['unobservable']} could not be resolved because the action lives outside the "
            f"observed scope. A zero here with workflows in scope means no `uses:` lines, not a "
            f"clean bill — check the workflow count.",
            message_data={
                "actions": len(usage["actions"]),
                "usages": usage["edges"],
                "unpinned": usage["unpinned"],
                "unobservable": usage["unobservable"],
            },
        )

        # --- enrichment phase (link resolution against landed nodes) ---
        enrichment_dims = {"github.platform": "github.com"}
        enrichment = resolve_links(
            link_manifest=link_manifest,
            repos=repos,
            edge_default_dimensions=enrichment_dims,
        )
        if enrichment.edge_envelopes:
            enrichment_batch = assemble_batch(
                batch_name=f"github_core enrichment: {scope_label}",
                description="Cross-grid link edges (REFERENCES_RESOURCE, FEDERATES_VIA) resolved from the grid-link manifest.",
                nodes=[],
                edges=enrichment.edge_envelopes,
            )
            self.submit_grift(enrichment_batch)
        self._record_enrichment_summary(enrichment)

        self.summary = (
            f"Collected {len(repos)} repo(s): {len(nodes)} node(s), "
            f"{len(edges)} spine edge(s), "
            f"{len(enrichment.edge_envelopes)} link edge(s)."
        )

    # ---------- config layer (GraphQL) ----------

    def _fetch_config_layer(self, data: dict[str, Any], owner: str) -> dict[str, dict[str, Any]]:
        """Fetch every repository's configuration for ``owner`` in one query, keyed by full name."""
        gql = GithubGraphQLClient(token=self._auth.token(), api_base_url=api_base_url(data))
        repos, notes = gql.fetch_config_layer(owner)
        config = {str(r["nameWithOwner"]): r for r in repos if r.get("nameWithOwner")}
        self.record_info(
            _SITE_GRAPHQL_CONFIG,
            "GRAPHQL_CONFIG_FETCHED",
            f"Config layer for {owner}: {len(config)} repo(s), "
            f"{sum(len(GithubGraphQLClient.workflow_files(r)) for r in repos)} workflow file(s), "
            f"cost {gql.last_cost} point(s).",
            message_data={"repos": len(config), "cost": gql.last_cost, "remaining": gql.last_remaining},
        )
        for note in notes:
            # A field the credential could not read. Surfaced, never swallowed: a silently missing
            # ruleset list is indistinguishable from an account that has no rulesets.
            self.record_warn(
                _SITE_GRAPHQL_DEGRADED,
                "GRAPHQL_FIELD_DEGRADED",
                f"Config layer partially unreadable — {note}",
                message_data={"detail": note},
            )
        return config

    @staticmethod
    def _repo_payload_from_config(gql: dict[str, Any]) -> dict[str, Any]:
        """Shape a GraphQL repository node like the REST payload the emitters already consume."""
        branch = gql.get("defaultBranchRef") or {}
        owner_login = str(gql.get("nameWithOwner", "/")).split("/", 1)[0]
        return {
            "full_name": gql.get("nameWithOwner"),
            "name": gql.get("name"),
            "id": gql.get("databaseId"),
            "owner": {"login": owner_login},
            "default_branch": branch.get("name"),
            "visibility": (gql.get("visibility") or "").lower(),
            "archived": bool(gql.get("isArchived")),
            "fork": bool(gql.get("isFork")),
            "html_url": gql.get("url"),
        }

    def _workflow_config(self, client: GithubClient, full_name: str, path: str) -> tuple[str, dict[str, Any]]:
        """Workflow YAML from the config layer when present, else the Contents API.

        The GraphQL path is the reason the per-file Contents calls disappear — they were both the
        bulk of the request count and the ones that timed out at org scale.
        """
        gql = self._config.get(full_name)
        if gql and path:
            files = GithubGraphQLClient.workflow_files(gql)
            raw = files.get(path)
            if raw is not None:
                return raw, parse_workflow_yaml(raw)
        return self._fetch_workflow_config(client, full_name, path)

    # ---------- envelope hygiene ----------

    @staticmethod
    def _drop_dangling_edges(
        edges: list[dict[str, Any]], node_ids: set[str]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Drop edges with an endpoint outside ``node_ids``; return the survivors and their types.

        Applies to the collection batch only, where both endpoints are always emitted alongside
        the edge. Enrichment edges resolve against the grid and must never be filtered this way.
        """
        kept: list[dict[str, Any]] = []
        dropped: list[str] = []
        for env in edges:
            e = env.get("edge") or {}
            src, tgt = str(e.get("from_entity_id")), str(e.get("to_entity_id"))
            if src in node_ids and tgt in node_ids:
                kept.append(env)
            else:
                dropped.append(str(e.get("edge_type", "unknown")))
        return kept, dropped

    @staticmethod
    def _collapse_by_entity_id(envelopes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Return the envelopes with duplicate ``entity.entity_id`` collapsed, and the count removed.

        Order is preserved and the LAST occurrence wins, so the freshest observation of a shared
        node survives. Envelopes without an entity id pass through untouched rather than being
        silently dropped — an id-less envelope is a different bug and GRIFT should be the one to
        say so.
        """
        seen: dict[str, int] = {}
        out: list[dict[str, Any]] = []
        removed = 0
        for env in envelopes:
            eid = (env.get("entity") or {}).get("entity_id")
            if eid is None:
                out.append(env)
                continue
            key = str(eid)
            if key in seen:
                out[seen[key]] = env
                removed += 1
            else:
                seen[key] = len(out)
                out.append(env)
        return out, removed

    # ---------- scope resolution ----------

    def _resolve_repos(self, client: GithubClient, owner: str | None, explicit: list[str]) -> list[str]:
        """The `owner/repo` full names this run collects (req-github-core-org-scope).

        With an `owner`: enumerate the account's repositories (``/orgs/{owner}/repos`` with a
        404 fallback to ``/users/{owner}/repos``), apply the explicit list as an include-filter
        if given, and record the enumeration on the run — including whether the paginated walk
        was COMPLETE, which is the assertion node-level absence (tombstoning, tap#140) will
        need before "not seen" can mean "gone". Without an `owner`: the explicit list is the
        scope (the degenerate run config, tap#142) and nothing is enumerated.
        """
        if owner is None:
            return list(explicit)
        if self._config:
            # Already enumerated by the config-layer query; do not walk REST again.
            enumerated = sorted(self._config)
            return self._apply_filter(owner, enumerated, explicit, account_kind="graphql", complete=True)
        params = {"type": "all", "per_page": "100"}
        try:
            listing = client.get_paginated(f"/orgs/{owner}/repos", params=params)
            account_kind = "org"
        except GithubAPIError as exc:
            if exc.status != 404:
                raise
            listing = client.get_paginated(f"/users/{owner}/repos", params=params)
            account_kind = "user"
        complete = bool(client.last_walk_complete)
        enumerated = [str(item["full_name"]) for item in listing if isinstance(item, dict) and item.get("full_name")]
        return self._apply_filter(owner, enumerated, explicit, account_kind=account_kind, complete=complete)

    def _apply_filter(
        self, owner: str, enumerated: list[str], explicit: list[str], *, account_kind: str, complete: bool
    ) -> list[str]:
        """Apply the optional include-filter to an enumerated scope and record the enumeration."""
        repos = enumerated
        if explicit:
            wanted = set(explicit)
            repos = [name for name in enumerated if name in wanted]
            unmatched = sorted(wanted - set(enumerated))
            if unmatched:
                self.record_warn(
                    _SITE_FILTER_UNMATCHED,
                    "SCOPE_FILTER_UNMATCHED",
                    f"{len(unmatched)} filtered repo(s) not found under {owner}: {', '.join(unmatched)}",
                    message_data={"owner": owner, "unmatched": unmatched},
                )
        self.record_info(
            _SITE_SCOPE_ENUMERATED,
            "SCOPE_ENUMERATED",
            f"Enumerated {len(enumerated)} repo(s) under {account_kind} {owner}"
            f"{' (walk INCOMPLETE — page cap hit)' if not complete else ''}; collecting {len(repos)}.",
            message_data={
                "owner": owner,
                "account_kind": account_kind,
                "enumerated": len(enumerated),
                "collecting": len(repos),
                "filtered": bool(explicit),
                "complete": complete,
            },
        )
        return repos

    # ---------- per-repo collection ----------

    def _collect_repo(
        self,
        client: GithubClient,
        full_name: str,
        run_limit: int,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        platform_uuid: Any,
    ) -> None:
        owner, _, repo = full_name.partition("/")
        repo_dims = {
            "github.platform": "github.com",
            "github.owner": owner,
            "github.repo": repo,
        }
        actions_dims = {**repo_dims, "github.surface": "actions"}
        observation_dims = {**actions_dims, "github.observation": "execution"}

        # account
        account_payload = self._fetch_account(client, owner)
        account_uuid = account_id(account_payload["login"])
        nodes.append(
            node_envelope(
                entity_id=account_uuid,
                entity_type="github_core__github_account",
                name=account_payload["login"],
                dimensions={**repo_dims},  # owner+repo carried even on account
                fields={
                    "login": account_payload["login"],
                    "github_id": account_payload.get("id"),
                    "account_type": account_payload.get("type", ""),
                    "html_url": account_payload.get("html_url", ""),
                    "configuration": {},
                    "tags": {},
                },
            )
        )
        # platform hosts this account — top-of-tree containment. Deterministic
        # edge id dedupes across repos that share an owner.
        edges.append(self._edge("HOSTS_ACCOUNT__github_core", platform_uuid, account_uuid, dict(_PLATFORM_DIMENSIONS)))

        # repository. Prefer the config layer already in hand; fall back to REST for a
        # repos-only scope, where no GraphQL enumeration ran.
        gql = self._config.get(full_name)
        repo_payload = self._repo_payload_from_config(gql) if gql else client.get(f"/repos/{full_name}")
        repo_uuid = repository_id(full_name)
        nodes.append(
            node_envelope(
                entity_id=repo_uuid,
                entity_type="github_core__github_repository",
                name=full_name,
                dimensions=repo_dims,
                fields={
                    "full_name": full_name,
                    "owner_login": owner,
                    "name": full_name,
                    "github_id": repo_payload.get("id"),
                    "default_branch": repo_payload.get("default_branch", ""),
                    "visibility": repo_payload.get("visibility", ""),
                    "html_url": repo_payload.get("html_url", ""),
                    "configuration": {},
                    "tags": {},
                },
            )
        )
        edges.append(self._edge("OWNS_REPO__github_core", account_uuid, repo_uuid, repo_dims))

        # The Actions OIDC issuer (synthesized once as a platform singleton) is
        # enabled for every repo's workflows to mint identity tokens — mirror the
        # github_app ENABLED_ON pattern so it connects into the repo it serves.
        edges.append(self._edge("ENABLED_ON__github_core", oidc_issuer_id(_OIDC_ISSUER_URL), repo_uuid, repo_dims))

        # --- configuration layer: refs, rulesets, environments.
        # All three arrive in the GraphQL config layer already in hand, so this costs no request.
        # They are emitted BEFORE the workflow walk because the declared jobs point at
        # environments, and the ruleset->ref resolution needs the refs.
        ref_uuid_by_ref = self._emit_refs(full_name, repo_uuid, repo_dims, nodes, edges)
        self._emit_rulesets(client, full_name, repo_uuid, repo_dims, ref_uuid_by_ref, nodes, edges)
        env_uuid_by_name = self._emit_environments(full_name, repo_uuid, repo_dims, nodes, edges)

        # workflows + workflow YAML
        workflows = client.get_paginated(f"/repos/{full_name}/actions/workflows", item_path="workflows")
        for wf in workflows:
            path = wf.get("path", "")
            # Synthetic platform-app entries (e.g. Dependabot) come back here but
            # are not repo CI workflows; reclassify them as github_app + ENABLED_ON
            # and skip the YAML fetch (no real file exists at the dynamic/ path).
            app_meta = next(
                (meta for prefix, meta in _SYNTHETIC_APP_BY_PATH_PREFIX.items() if path.startswith(prefix)),
                None,
            )
            if app_meta is not None:
                self._emit_github_app(app_meta, full_name, repo_uuid, repo_dims, nodes, edges)
                continue

            wf_uuid = workflow_id(full_name, wf["id"])
            raw_yaml, parsed_config = self._workflow_config(client, full_name, wf.get("path", ""))
            wf_display_name = wf.get("name") or wf.get("path") or str(wf["id"])
            nodes.append(
                node_envelope(
                    entity_id=wf_uuid,
                    entity_type="github_core__github_workflow",
                    name=wf_display_name,
                    dimensions=actions_dims,
                    fields={
                        "full_name": full_name,
                        "workflow_id": wf["id"],
                        "path": wf.get("path", ""),
                        "name": wf_display_name,
                        "state": wf.get("state", ""),
                        "html_url": wf.get("html_url", ""),
                        "configuration": parsed_config,
                        "tags": {},
                    },
                )
            )
            edges.append(self._edge("DEFINES_WORKFLOW__github_core", repo_uuid, wf_uuid, actions_dims))
            # The DECLARED jobs inside this file — the level every privilege decision is made at.
            self._emit_declared_jobs(
                full_name, wf_uuid, wf["id"], wf.get("path", ""), parsed_config, env_uuid_by_name, nodes, edges
            )
            # Local-action surfacing per req-github-core-workflow-parse-3.
            for ref in parsed_config.get("local_action_refs") or []:
                self.record_warn(
                    _SITE_LOCAL_ACTION_DEFERRED,
                    "LOCAL_ACTION_DEFERRED",
                    f"{full_name} workflow {wf.get('path', '')}: local/composite "
                    f"action reference {ref.get('uses')!r} at {ref.get('path')} "
                    f"(job {ref.get('job_id')!r}); v0 does not parse action.yml bodies.",
                )

        # runs — incremental fetch per req-github-core-collector-3:
        #   - First population (no on-grid runs): latest `run_limit` runs.
        #   - Later populations: runs created since the latest on-grid
        #     `run_started_at` for this repo, using GitHub's `created` filter.
        run_payloads = self._fetch_run_window(client, full_name, run_limit)

        # Non-terminal refresh per req-github-core-collector-4: pull every
        # on-grid run for this repo whose status is not in
        # _TERMINAL_RUN_STATUSES and re-fetch it singly. Skips any run_id
        # already in this batch's run_payloads (the incremental fetch may
        # already have re-fetched it).
        refreshed = self._fetch_non_terminal_refresh(
            client, full_name, already_fetched_run_ids={r["id"] for r in run_payloads}
        )
        run_payloads.extend(refreshed)
        jobs_by_run: dict[int, list[dict[str, Any]]] = {}
        for r in run_payloads:
            run_uuid = run_id(full_name, r["id"])
            wf_ref_uuid = workflow_id(full_name, r["workflow_id"]) if r.get("workflow_id") else None
            nodes.append(
                node_envelope(
                    entity_id=run_uuid,
                    entity_type="github_core__github_actions_run",
                    name=f"Run #{r.get('run_number', r['id'])}",
                    dimensions=observation_dims,
                    fields={
                        "full_name": full_name,
                        "run_id": r["id"],
                        "run_number": r.get("run_number"),
                        "event": r.get("event", ""),
                        "status": r.get("status", ""),
                        "conclusion": r.get("conclusion") or "",
                        "head_sha": r.get("head_sha", ""),
                        "head_branch": r.get("head_branch", ""),
                        "run_started_at": r.get("run_started_at"),
                        "completed_at": r.get("updated_at"),
                        "html_url": r.get("html_url", ""),
                        "configuration": {
                            "workflow_id": r.get("workflow_id"),
                            "raw_payload_keys": sorted(r.keys()),
                        },
                        "tags": {},
                    },
                )
            )
            if wf_ref_uuid is not None:
                edges.append(self._edge("EXECUTES_WORKFLOW__github_core", run_uuid, wf_ref_uuid, observation_dims))

            # jobs for this run (latest-attempt endpoint per req-github-core-collector-8).
            # Held for the EXECUTED_ON pass below rather than re-fetched: the runner match needs
            # the same payloads, and at account scope a second walk is one extra API call per RUN
            # — the single largest cost in the whole collection.
            jobs = self._fetch_run_jobs(client, full_name, r["id"])
            jobs_by_run[r["id"]] = jobs
            for j in jobs:
                j_uuid = job_id(full_name, j["id"])
                j_display_name = j.get("name") or str(j["id"])
                nodes.append(
                    node_envelope(
                        entity_id=j_uuid,
                        entity_type="github_core__github_actions_job",
                        name=j_display_name,
                        dimensions=observation_dims,
                        fields={
                            "full_name": full_name,
                            "job_id": j["id"],
                            "name": j_display_name,
                            "status": j.get("status", ""),
                            "conclusion": j.get("conclusion") or "",
                            "started_at": j.get("started_at"),
                            "completed_at": j.get("completed_at"),
                            "html_url": j.get("html_url", ""),
                            "configuration": {
                                "runner_id": j.get("runner_id"),
                                "runner_name": j.get("runner_name"),
                                "runner_group_id": j.get("runner_group_id"),
                                "labels": j.get("labels") or [],
                                "steps": j.get("steps") or [],
                            },
                            "tags": {},
                        },
                    )
                )
                edges.append(self._edge("HAS_ACTIONS_JOB__github_core", run_uuid, j_uuid, observation_dims))

        # runners (graceful-degrade on 403 per req-github-core-collector-5)
        try:
            runners = client.get_paginated(f"/repos/{full_name}/actions/runners", item_path="runners")
        except GithubAPIError as exc:
            if exc.status == 403:
                self.record_warn(
                    _SITE_RUNNER_DEGRADED,
                    "RUNNER_CONFIG_FORBIDDEN",
                    f"Runner config inaccessible for {full_name}: {exc.body[:120]}",
                )
                runners = []
            else:
                raise
        runner_uuid_by_id: dict[int, Any] = {}
        for rn in runners:
            rn_uuid = runner_id(full_name, rn["id"])
            runner_uuid_by_id[rn["id"]] = rn_uuid
            rn_display_name = rn.get("name") or str(rn["id"])
            nodes.append(
                node_envelope(
                    entity_id=rn_uuid,
                    entity_type="github_core__github_runner",
                    name=rn_display_name,
                    dimensions=actions_dims,
                    fields={
                        "full_name": full_name,
                        "runner_id": rn["id"],
                        "name": rn_display_name,
                        "os": rn.get("os", ""),
                        "status": rn.get("status", ""),
                        "busy": bool(rn.get("busy", False)),
                        "labels": [lab.get("name") if isinstance(lab, dict) else lab for lab in rn.get("labels", [])],
                        "configuration": {},
                        "tags": {},
                    },
                )
            )

        # stored cache entries (REST; graceful-degrade like runners). Observation dimensions:
        # a cache entry is something that HAPPENED, not something declared.
        self._collect_caches(client, full_name, repo_uuid, observation_dims, ref_uuid_by_ref, nodes, edges)
        # Bypass EVENTS. Deliberately after refs: EVALUATED_ON resolves against ref_uuid_by_ref,
        # and a suite naming a ref we did not collect simply carries no edge.
        self._collect_rule_suites(client, full_name, observation_dims, ref_uuid_by_ref, nodes, edges)

        # EXECUTED_ON edges (only when an observed job runner_id matches a durable runner node).
        # Reuses the job payloads collected above — the runner nodes simply were not known yet
        # when the jobs were first walked, which is an ordering constraint, not a reason to fetch
        # them again.
        for r in run_payloads:
            for j in jobs_by_run.get(r["id"], []):
                if j.get("runner_id") and j["runner_id"] in runner_uuid_by_id:
                    j_uuid = job_id(full_name, j["id"])
                    rn_uuid = runner_uuid_by_id[j["runner_id"]]
                    edges.append(self._edge("EXECUTED_ON__github_core", j_uuid, rn_uuid, observation_dims))

    # ---------- helpers ----------

    # ---------- configuration-layer emitters ----------

    def _emit_refs(
        self,
        full_name: str,
        repo_uuid: Any,
        repo_dims: dict[str, str],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Emit every branch and tag as a `git_ref`, returning ``{ref_path: uuid}``.

        The returned map is what makes ruleset resolution and cache scoping possible without a
        second lookup — both need to know whether a ref named elsewhere is one we actually saw.

        Tag-movement detection is not implemented here and does not need to be: the ref's
        `head_sha` is a field on a node with a deterministic id, so the grid's own field history
        records the move. Detection is a query over history, not a diff the collector keeps.
        """
        gql = self._config.get(full_name)
        if not gql:
            # A repos-only scope runs no GraphQL enumeration. Refs are config-layer data and are
            # simply not collected in that form — stated rather than silently empty.
            return {}
        git_dims = {**repo_dims, "github.surface": "git"}
        refs, truncated = GithubGraphQLClient.refs(gql)
        uuid_by_ref: dict[str, Any] = {}
        for ref in refs:
            ref_uuid = git_ref_id(full_name, ref["ref"])
            uuid_by_ref[ref["ref"]] = ref_uuid
            if ref["is_default"]:
                self._default_refs.add(f"{full_name}#{ref['ref']}")
            nodes.append(
                node_envelope(
                    entity_id=ref_uuid,
                    entity_type="github_core__git_ref",
                    name=ref["name"],
                    dimensions={**git_dims, "github.ref_type": ref["ref_type"]},
                    fields={
                        "full_name": full_name,
                        "ref": ref["ref"],
                        "ref_type": ref["ref_type"],
                        "name": ref["name"],
                        "head_sha": ref["head_sha"],
                        "target_sha": ref["target_sha"],
                        "target_type": ref["target_type"],
                        "is_default": ref["is_default"],
                        "configuration": {},
                        "tags": {},
                    },
                )
            )
            edges.append(self._edge("HAS_REF__github_core", repo_uuid, ref_uuid, git_dims))
        for ref_type, missing in sorted(truncated.items()):
            self.record_warn(
                _SITE_REFS_TRUNCATED,
                "REFS_TRUNCATED",
                f"{full_name}: {missing} {ref_type}(s) beyond the page cap were not collected. "
                f"Absence of a {ref_type} in this batch is NOT evidence it does not exist.",
                message_data={"repo": full_name, "ref_type": ref_type, "missing": missing},
            )
        return uuid_by_ref

    def _emit_rulesets(
        self,
        client: GithubClient,
        full_name: str,
        repo_uuid: Any,
        repo_dims: dict[str, str],
        ref_uuid_by_ref: dict[str, Any],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Emit the rulesets gating this repository, and what may bypass them.

        Two transports, because neither is sufficient alone. GraphQL says which rulesets apply to
        this repository and answers `bypassActors`; REST's ruleset detail is the only place the
        rules' PARAMETERS live (the required check names the gate view needs), and whether it
        carries a `bypass_actors` key at all is how we learn the credential could really see one.
        """
        gql = self._config.get(full_name)
        if not gql:
            return
        owner = full_name.partition("/")[0]
        # Deliberately NOT repo-scoped. One organization ruleset is a single node protecting many
        # repositories, and a `github.repo` dimension on it would name whichever repo happened to
        # emit it last — an assertion the node has no business making. The repository association
        # is the PROTECTS edge, which IS repo-scoped.
        ruleset_dims = {
            "github.platform": repo_dims["github.platform"],
            "github.owner": owner,
            "github.surface": "rules",
        }
        rules_dims = {**repo_dims, "github.surface": "rules"}
        for ruleset in GithubGraphQLClient.rulesets(gql):
            rid = ruleset["ruleset_id"]
            if rid is None:
                continue
            rs_uuid = ruleset_id(owner, rid)
            detail = self._ruleset_detail(client, full_name, rid)
            observability = self._bypass_observability(ruleset, detail)
            # When the answer is unreadable, say WHY in the operator's terms. "No token placed",
            # "a token is placed but was refused", and "the token answered but GitHub still
            # withheld the list" are three different situations with three different fixes, and
            # collapsing them into one blank is how a gap stops being actionable.
            absent_note = (
                self._bypass_absent_note() if observability["state"] == "unobservable" else ""
            )
            nodes.append(
                node_envelope(
                    entity_id=rs_uuid,
                    entity_type="github_core__github_ruleset",
                    name=ruleset["name"],
                    dimensions=ruleset_dims,
                    fields={
                        "owner_login": owner,
                        "ruleset_id": rid,
                        "name": ruleset["name"],
                        "target": ruleset["target"],
                        "enforcement": ruleset["enforcement"],
                        "source": str((detail or {}).get("source") or owner),
                        "source_type": str((detail or {}).get("source_type") or ""),
                        "conditions": ruleset["conditions"],
                        # REST detail carries each rule's parameters (the required check contexts
                        # among them); GraphQL gives only the rule TYPE, and a gate view that knows
                        # a repository requires *some* status check but not *which* is not a gate
                        # view. Fall back to the type-only list rather than to nothing.
                        "rules": list((detail or {}).get("rules") or ruleset["rules"]),
                        "bypass_observability": observability["state"],
                        "bypass_actor_count": observability["count"],
                        "html_url": str(((detail or {}).get("_links") or {}).get("html", {}).get("href") or ""),
                        "configuration": {
                            "current_user_can_bypass": (detail or {}).get("current_user_can_bypass"),
                            "bypass_source": observability["source"],
                            "bypass_actors_unmodelled": observability["unmodelled"],
                            "bypass_absent_note": absent_note,
                        },
                        "tags": {},
                    },
                )
            )
            edges.append(
                edge_envelope(
                    entity_id=edge_id("PROTECTS__github_core", rs_uuid, repo_uuid),
                    edge_type="PROTECTS__github_core",
                    source_id=rs_uuid,
                    target_id=repo_uuid,
                    dimensions=rules_dims,
                    properties={"match_kind": "declared"},
                )
            )
            self._emit_protected_refs(ruleset, rs_uuid, full_name, ref_uuid_by_ref, rules_dims, edges)
            self._emit_bypass_edges(ruleset, rs_uuid, observability, rules_dims, nodes, edges)
            if observability["state"] == "unobservable":
                self.record_warn(
                    _SITE_RULESET_BYPASS_UNOBSERVABLE,
                    "RULESET_BYPASS_UNOBSERVABLE",
                    f"{full_name}: ruleset {ruleset['name']!r} — the bypass list is unreadable. An "
                    f"empty 'who can bypass' cell for it means UNKNOWN, not none."
                    + (f" Note: {absent_note}." if absent_note else ""),
                    message_data={
                        "repo": full_name,
                        "ruleset": ruleset["name"],
                        "ruleset_id": rid,
                        "absent_note": absent_note,
                    },
                )

    def _emit_protected_refs(
        self,
        ruleset: dict[str, Any],
        rs_uuid: Any,
        full_name: str,
        ref_uuid_by_ref: dict[str, Any],
        dims: dict[str, str],
        edges: list[dict[str, Any]],
    ) -> None:
        """Resolve the ruleset's ref patterns against refs actually observed, one edge each.

        Resolution is additive to the declared edge, never a replacement: the patterns are kept
        verbatim on the node because they are the intent, and these edges are what that intent
        turned out to cover on the day it was collected. A pattern matching nothing is a real
        answer (a ruleset protecting a branch that does not exist), not a failure.
        """
        ref_type = _REF_TYPE_BY_RULESET_TARGET.get(ruleset["target"])
        if ref_type is None:
            return
        conditions = (ruleset.get("conditions") or {}).get("ref_name") or {}
        include = list(conditions.get("include") or [])
        exclude = list(conditions.get("exclude") or [])
        for ref_path, ref_uuid in sorted(ref_uuid_by_ref.items()):
            if not ref_path.startswith("refs/heads/" if ref_type == "branch" else "refs/tags/"):
                continue
            is_default = self._is_default_ref(full_name, ref_path)
            pattern = self._matching_pattern(ref_path, include, is_default)
            if pattern is None or self._matching_pattern(ref_path, exclude, is_default):
                continue
            edges.append(
                edge_envelope(
                    entity_id=edge_id("PROTECTS__github_core", rs_uuid, ref_uuid),
                    edge_type="PROTECTS__github_core",
                    source_id=rs_uuid,
                    target_id=ref_uuid,
                    dimensions=dims,
                    properties={"match_kind": "resolved", "ref_pattern": pattern},
                )
            )

    def _is_default_ref(self, full_name: str, ref_path: str) -> bool:
        """Whether this ref is ITS OWN repository's default branch, per the config layer."""
        return f"{full_name}#{ref_path}" in self._default_refs

    @staticmethod
    def _matching_pattern(ref_path: str, patterns: list[str], is_default: bool) -> str | None:
        """The first pattern that selects ``ref_path``, or None.

        GitHub's condition tokens are matched as tokens, not as text: `~DEFAULT_BRANCH` names
        whichever branch is default today, and `~ALL` matches everything of the ruleset's target
        type. Everything else is an fnmatch pattern over the full ref path.
        """
        for pattern in patterns:
            if pattern == _REF_TOKEN_ALL:
                return pattern
            if pattern == _REF_TOKEN_DEFAULT_BRANCH:
                if is_default:
                    return pattern
                continue
            if fnmatch.fnmatch(ref_path, pattern):
                return pattern
        return None

    def _bypass_absent_note(self) -> str:
        """Why the bypass list could not be read, phrased as something an operator can act on."""
        if not self._auth.has_pat:
            return self._auth.absent_note(PREFER_PAT)
        if self._pat_ruleset_status.startswith("refused"):
            return (
                f"a personal access token is placed but the ruleset endpoint {self._pat_ruleset_status} "
                f"it — it has expired, or its resource owner is not this account"
            )
        return (
            "the personal access token placed does not have write access to this ruleset, which is "
            "what GitHub requires before it will disclose bypass actors at all"
        )

    @staticmethod
    def _bypass_observability(ruleset: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
        """Decide whether the bypass list was actually READ, and by which transport.

            observable = REST carried the key  OR  GraphQL returned a non-empty list

        The asymmetry is the point (`spec-github-core-vocabulary.md`, open question 3). GitHub
        returns bypass actors only to a caller with write access to the ruleset: REST then omits
        the key entirely, while GraphQL answers with an empty connection and no error. A non-empty
        GraphQL answer proves itself — a filtered connection cannot invent actors — but an empty
        one proves nothing, and rendering it as "nobody can bypass" would tell an organization the
        single most reassuring thing it could hear on no evidence at all.
        """
        rest_actors = (detail or {}).get("bypass_actors")
        graphql_actors = list(ruleset.get("bypass_actors") or [])
        if rest_actors is not None:
            return {
                "state": "observed",
                "count": len(rest_actors),
                "source": "rest_ruleset_detail",
                "actors": [],
                "unmodelled": list(rest_actors),
            }
        if graphql_actors:
            modelled, unmodelled = GithubCollector._split_bypass_actors(graphql_actors)
            return {
                "state": "observed",
                "count": len(graphql_actors),
                "source": "graphql_bypass_actors",
                "actors": modelled,
                "unmodelled": unmodelled,
            }
        return {"state": "unobservable", "count": None, "source": "", "actors": [], "unmodelled": []}

    @staticmethod
    def _split_bypass_actors(actors: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split bypass actors into the ones this vocabulary has a node for, and the rest.

        Apps have a node (`github_app`, keyed by slug). Teams and organization-admin roles do not
        yet — `github_team` is a later tier and "organization admins" is a role, not an actor. The
        ones without a node are kept as data on the ruleset with their count, because dropping
        them would understate who can bypass, which is the one direction that must never happen.
        """
        modelled: list[dict[str, Any]] = []
        unmodelled: list[dict[str, Any]] = []
        for actor in actors:
            body = actor.get("actor") or {}
            if str(body.get("__typename")) == "App" and body.get("slug"):
                modelled.append(
                    {
                        "slug": str(body["slug"]),
                        "name": str(body.get("name") or body["slug"]),
                        "app_id": body.get("databaseId"),
                        "bypass_mode": str(actor.get("bypassMode") or "").lower(),
                    }
                )
                continue
            unmodelled.append(
                {
                    "actor_type": str(body.get("__typename") or ("OrganizationAdmin" if actor.get("organizationAdmin") else "")),
                    "name": str(body.get("slug") or body.get("name") or actor.get("repositoryRoleName") or ""),
                    "bypass_mode": str(actor.get("bypassMode") or "").lower(),
                }
            )
        return modelled, unmodelled

    def _emit_bypass_edges(
        self,
        ruleset: dict[str, Any],
        rs_uuid: Any,
        observability: dict[str, Any],
        dims: dict[str, str],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Emit one EXEMPTS_ACTOR edge per actor we can name, and say what we could not name."""
        for actor in observability["actors"]:
            app_uuid = github_app_id(actor["slug"])
            if str(app_uuid) not in self._emitted_app_ids:
                self._emitted_app_ids.add(str(app_uuid))
                nodes.append(
                    node_envelope(
                        entity_id=app_uuid,
                        entity_type="github_core__github_app",
                        name=actor["name"],
                        dimensions={**dims, "github.surface": "apps"},
                        fields={
                            "slug": actor["slug"],
                            "name": actor["name"],
                            "app_id": actor.get("app_id"),
                            "client_id": "",
                            "html_url": f"https://github.com/apps/{actor['slug']}",
                            "description": "",
                            "configuration": {},
                            "tags": {},
                        },
                    )
                )
            edges.append(
                edge_envelope(
                    # Ruleset -> actor. The exemption is something the RULESET declares
                    # (it is an entry in its own bypass_actors list); nobody initiates a
                    # permission, so the declaring object is the source.
                    entity_id=edge_id("EXEMPTS_ACTOR__github_core", rs_uuid, app_uuid),
                    edge_type="EXEMPTS_ACTOR__github_core",
                    source_id=rs_uuid,
                    target_id=app_uuid,
                    dimensions=dims,
                    properties={
                        "actor_type": "Integration",
                        "bypass_mode": actor["bypass_mode"],
                        "observable": True,
                        "source": observability["source"],
                    },
                )
            )
        if observability["unmodelled"]:
            self.record_warn(
                _SITE_BYPASS_ACTOR_UNMODELLED,
                "BYPASS_ACTOR_UNMODELLED",
                f"Ruleset {ruleset['name']!r}: {len(observability['unmodelled'])} bypass actor(s) "
                f"have no node type yet (teams, org-admin roles) — counted on the ruleset, not "
                f"dropped.",
                message_data={"ruleset": ruleset["name"], "actors": observability["unmodelled"]},
            )

    def _ruleset_detail(self, client: GithubClient, full_name: str, rid: int) -> dict[str, Any] | None:
        """REST ruleset detail, fetched once per ruleset per run and cached.

        One organization ruleset applies to every repository it matches, so without the cache this
        would be the same call once per repository. Degrades to None rather than failing the repo:
        the GraphQL side already carries enough to emit the node, just without rule parameters.
        """
        owner = full_name.partition("/")[0]
        cache_key = (owner, rid)
        if cache_key in self._ruleset_details:
            return self._ruleset_details[cache_key]
        # The PAT-bound client when the envelope carries one: this single call is the only place
        # a token sees more than the App does.
        detail_client = self._pat_client or client
        try:
            detail = detail_client.get(f"/repos/{full_name}/rulesets/{rid}")
        except GithubAPIError as exc:
            if self._pat_client is not None:
                self._pat_ruleset_status = f"refused ({exc.status})"
            self.record_warn(
                _SITE_RULESET_DETAIL_DEGRADED,
                f"RULESET_DETAIL_{exc.status}",
                f"{full_name}: ruleset {rid} detail unreadable ({exc.status}); rule parameters "
                f"(including required check names) are missing for it.",
                message_data={"repo": full_name, "ruleset_id": rid, "status": exc.status},
            )
            detail = None
        else:
            if self._pat_client is not None:
                self._pat_ruleset_status = "answered"
        self._ruleset_details[cache_key] = detail
        return detail

    def _emit_environments(
        self,
        full_name: str,
        repo_uuid: Any,
        repo_dims: dict[str, str],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Emit deployment environments, returning ``{name: uuid}`` for the declared jobs to use."""
        gql = self._config.get(full_name)
        if not gql:
            return {}
        deploy_dims = {**repo_dims, "github.surface": "deployments"}
        uuid_by_name: dict[str, Any] = {}
        for env in GithubGraphQLClient.environments(gql):
            name = env["name"]
            if not name:
                continue
            env_uuid = environment_id(full_name, name)
            uuid_by_name[name] = env_uuid
            nodes.append(
                node_envelope(
                    entity_id=env_uuid,
                    entity_type="github_core__github_environment",
                    name=name,
                    dimensions=deploy_dims,
                    fields={
                        "full_name": full_name,
                        "environment_id": env["environment_id"],
                        "name": name,
                        "protection_rules": env["protection_rules"],
                        # The GraphQL environment carries no branch policy; the REST environments
                        # endpoint does. Left null (unobserved) rather than defaulted to "none",
                        # which would assert an absence this transport never looked for.
                        "deployment_branch_policy": None,
                        "can_admins_bypass": None,
                        "html_url": "",
                        "configuration": {},
                        "tags": {},
                    },
                )
            )
            edges.append(self._edge("HAS_ENVIRONMENT__github_core", repo_uuid, env_uuid, deploy_dims))
        return uuid_by_name

    def _emit_declared_jobs(
        self,
        full_name: str,
        wf_uuid: Any,
        workflow_id_int: int,
        workflow_path: str,
        parsed_config: dict[str, Any],
        env_uuid_by_name: dict[str, Any],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Emit one `workflow_job` per job declared in the file, plus its `needs:` graph.

        This is the declaration side of the pipeline: `permissions`, `runs-on`, `if`, the
        environment it deploys to and the ref it checks out. The execution side
        (`github_actions_job`) keeps its own nodes; the two are deliberately not merged.
        """
        declared_dims = {
            "github.platform": "github.com",
            "github.owner": full_name.partition("/")[0],
            "github.repo": full_name.partition("/")[2],
            "github.surface": "actions",
            "github.observation": "declaration",
        }
        jobs = parsed_config.get("jobs") or []
        uuid_by_key: dict[str, Any] = {}
        for order, job in enumerate(jobs):
            job_key = str(job.get("id") or "")
            if not job_key:
                continue
            job_uuid = workflow_job_id(full_name, workflow_id_int, job_key)
            uuid_by_key[job_key] = job_uuid
            nodes.append(
                node_envelope(
                    entity_id=job_uuid,
                    entity_type="github_core__workflow_job",
                    name=str(job.get("name") or job_key),
                    dimensions=declared_dims,
                    fields={
                        "full_name": full_name,
                        "workflow_id": workflow_id_int,
                        "workflow_path": workflow_path,
                        "job_key": job_key,
                        "name": str(job.get("name") or job_key),
                        "runs_on": job.get("runs_on"),
                        # null when the job declares no block (it inherits the workflow's), {} when
                        # it declares an empty one (the token gets nothing). Not the same fact.
                        "permissions": job.get("permissions"),
                        "if_condition": str(job.get("if") or ""),
                        "environment": str(job.get("environment") or ""),
                        "uses": str(job.get("uses") or ""),
                        "needs": list(job.get("needs") or []),
                        "checkout_ref": str(job.get("checkout_ref") or ""),
                        "configuration": {
                            "steps": job.get("steps") or [],
                            "cache_steps": job.get("cache_steps") or [],
                            "action_refs": job.get("action_refs") or [],
                            # The file's own triggers and permissions, carried onto the job so a
                            # reader can adjudicate one node without walking up to the workflow:
                            # "pull_request_target + checks out the PR head" is the question, and
                            # it spans both levels.
                            "workflow_triggers": parsed_config.get("triggers") or [],
                            "workflow_permissions": parsed_config.get("permissions"),
                        },
                        "tags": {},
                    },
                )
            )
            edges.append(
                edge_envelope(
                    entity_id=edge_id("DEFINES_JOB__github_core", wf_uuid, job_uuid),
                    edge_type="DEFINES_JOB__github_core",
                    source_id=wf_uuid,
                    target_id=job_uuid,
                    dimensions=declared_dims,
                    properties={"job_key": job_key, "order": order},
                )
            )
            env_uuid = env_uuid_by_name.get(str(job.get("environment") or ""))
            if env_uuid is not None:
                edges.append(
                    self._edge(
                        "USES_ENVIRONMENT__github_core",
                        job_uuid,
                        env_uuid,
                        {**declared_dims, "github.surface": "deployments"},
                    )
                )
            # The third-party code this job hands its token to, and how each call is pinned.
            self._emit_used_actions(full_name, job_uuid, job.get("action_refs") or [], declared_dims, nodes, edges)
        # `needs:` — emitted after every job in the file has an id, because a job may need one
        # declared below it.
        for job in jobs:
            source_uuid = uuid_by_key.get(str(job.get("id") or ""))
            if source_uuid is None:
                continue
            for needed in job.get("needs") or []:
                target_uuid = uuid_by_key.get(str(needed))
                if target_uuid is None:
                    # A `needs:` naming a job that does not exist is a broken workflow, not our
                    # bug — no edge, and the name stays visible in the node's `needs` field.
                    continue
                edges.append(
                    edge_envelope(
                        entity_id=edge_id("DEPENDS_ON_JOB__github_core", source_uuid, target_uuid),
                        edge_type="DEPENDS_ON_JOB__github_core",
                        source_id=source_uuid,
                        target_id=target_uuid,
                        dimensions=declared_dims,
                        properties={"condition": str(job.get("if") or "")},
                    )
                )

    def _emit_used_actions(
        self,
        full_name: str,
        job_uuid: Any,
        action_refs: list[dict[str, Any]],
        usage_dims: dict[str, str],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        """One `github_action` per distinct path, one `USES_ACTION` per (job, action, ref).

        The node is shared across the whole run (deterministic id; envelope collapse keeps one
        copy) and carries NO owner/repo dimension, because `actions/checkout` belongs to no one
        repository in scope. The edge keeps the calling repository's dimensions: the usage is
        that repository's fact. The pin lives on the edge — it is a fact about this job's call,
        and the same action is pinned differently by different jobs.
        """
        action_dims = {k: v for k, v in usage_dims.items() if k not in _REPO_SCOPED_DIMENSION_KEYS}
        for (action_path, declared_ref), call in sorted(_group_action_calls(action_refs).items()):
            action_uuid = github_action_id(action_path)
            nodes.append(self._action_node(action_uuid, action_path, call, action_dims))
            properties = self._uses_action_properties(full_name, call, declared_ref)
            edges.append(
                edge_envelope(
                    entity_id=uses_action_edge_id(job_uuid, action_uuid, declared_ref),
                    edge_type="USES_ACTION__github_core",
                    source_id=job_uuid,
                    target_id=action_uuid,
                    dimensions=usage_dims,
                    properties=properties,
                )
            )
            self._tally_usage(action_path, properties)

    @staticmethod
    def _action_node(action_uuid: Any, action_path: str, call: dict[str, Any], dims: dict[str, str]) -> dict[str, Any]:
        return node_envelope(
            entity_id=action_uuid,
            entity_type="github_core__github_action",
            name=action_path,
            dimensions=dims,
            fields={
                "action_path": action_path,
                "kind": str(call.get("kind") or "repository"),
                "owner": str(call.get("owner") or ""),
                "repository_full_name": str(call.get("repository_full_name") or ""),
                "subpath": str(call.get("subpath") or ""),
                "name": action_path,
                "configuration": {},
                "tags": {},
            },
        )

    def _uses_action_properties(self, full_name: str, call: dict[str, Any], declared_ref: str) -> dict[str, Any]:
        """The pin, in three states — see `_resolve_action_pin`."""
        pin_kind, resolved_sha, resolution = self._resolve_action_pin(
            full_name,
            str(call.get("kind") or ""),
            str(call.get("repository_full_name") or ""),
            declared_ref,
            str(call.get("pin_kind") or ""),
        )
        properties: dict[str, Any] = {
            "declared_ref": declared_ref,
            "pin_kind": pin_kind,
            "is_pinned": is_pinned(pin_kind),
            "resolution": resolution,
            "step_indexes": sorted(call["step_indexes"]),
        }
        if resolved_sha:
            properties["resolved_sha"] = resolved_sha
        return properties

    def _tally_usage(self, action_path: str, properties: dict[str, Any]) -> None:
        usage = self._usage_tally()
        usage["actions"].add(action_path)
        usage["edges"] += 1
        if not properties["is_pinned"]:
            usage["unpinned"] += 1
        if properties["resolution"] == "unobservable":
            usage["unobservable"] += 1

    def _resolve_action_pin(
        self, full_name: str, kind: str, repository_full_name: str, declared_ref: str, parsed_pin: str
    ) -> tuple[str, str, str]:
        """Upgrade a parsed pin to what the collector can PROVE: ``(pin_kind, resolved_sha, resolution)``.

        Three states, never two. `literal`: the string settles it (a SHA, a digest, an image
        tag, nothing written). `in_scope`: the action's repository is in the observed scope, so
        its refs are in hand and the name is a `tag` or a `branch` with a head commit — or it
        matches neither, which stays `unresolved` and is warned about. `unobservable`: the
        repository is outside the scope and no call was made; the absence of a resolved SHA
        here is not evidence of anything and must not render as one.
        """
        if parsed_pin != PIN_UNRESOLVED or kind != "repository":
            return parsed_pin, declared_ref if parsed_pin == PIN_SHA else "", "literal"
        refs = self._refs_for(repository_full_name)
        if refs is None:
            return PIN_UNRESOLVED, "", "unobservable"
        tag_sha = refs.get(f"refs/tags/{declared_ref}")
        if tag_sha is not None:
            return PIN_TAG, tag_sha, "in_scope"
        branch_sha = refs.get(f"refs/heads/{declared_ref}")
        if branch_sha is not None:
            return PIN_BRANCH, branch_sha, "in_scope"
        self.record_warn(
            _SITE_ACTION_REF_NOT_FOUND,
            "ACTION_REF_NOT_FOUND",
            f"{full_name} uses {repository_full_name}@{declared_ref}, whose repository is in scope "
            f"but carries no tag or branch by that name among the refs collected — a deleted ref, "
            f"or one beyond the ref page cap. Left unresolved rather than guessed.",
            message_data={"repo": full_name, "action_repo": repository_full_name, "ref": declared_ref},
        )
        return PIN_UNRESOLVED, "", "in_scope"

    def _usage_tally(self) -> dict[str, Any]:
        """The run's action tally, created on first touch.

        Lazy rather than set in `run()` alone because the per-repo walk is exercised directly by
        tests that build the collector without running it, and a tally that only exists after
        `run()` would make every such walk raise on its first `uses:` line.
        """
        tally = getattr(self, "_action_usage", None)
        if tally is None:
            tally = {"actions": set(), "edges": 0, "unpinned": 0, "unobservable": 0}
            self._action_usage = tally
        return tally

    def _refs_for(self, repository_full_name: str) -> dict[str, str] | None:
        """``{ref path: head sha}`` for an in-scope repository, or None when it is not in scope.

        Built from the config layer already fetched, so resolution costs no request. None and
        an empty dict are different answers: None is "cannot look", {} is "looked, none".
        """
        gql = getattr(self, "_config", {}).get(repository_full_name)
        if gql is None:
            return None
        by_repo: dict[str, dict[str, str]] = getattr(self, "_refs_by_repo", None) or {}
        self._refs_by_repo = by_repo
        cached = by_repo.get(repository_full_name)
        if cached is None:
            refs, _truncated = GithubGraphQLClient.refs(gql)
            cached = {str(r["ref"]): str(r.get("head_sha") or "") for r in refs}
            by_repo[repository_full_name] = cached
        return cached

    def _collect_rule_suites(
        self,
        client: GithubClient,
        full_name: str,
        dims: dict[str, str],
        ref_uuid_by_ref: dict[str, Any],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Collect pushes that went AROUND a ruleset rather than satisfying it.

        The complement to `_emit_rulesets` (`req-github-core-rule-suites`). That records who MAY
        bypass and hits a documented ceiling — GitHub returns `bypass_actors` only to a caller with
        write access. This records who DID, and answers a read-only App with names.

        Only `bypass` results are requested. A passing suite is a routine push (~47/day on one
        active repository); landing every one would swamp the grid for no finding.
        """
        try:
            suites = client.get_paginated(
                f"/repos/{full_name}/rulesets/rule-suites",
                params={
                    "rule_suite_result": "bypass",
                    # Explicit, always. The default is `day` (req-github-core-rule-suites-5).
                    "time_period": _RULE_SUITE_WINDOW,
                    "per_page": str(_RULE_SUITE_LIMIT_PER_REPO),
                },
            )
        except GithubAPIError as exc:
            if exc.status in (403, 404):
                # Refused is not empty. Landing nothing here would say "no one bypassed anything",
                # which is the most reassuring reading of a permission failure.
                self.record_warn(
                    _SITE_RULE_SUITE_DEGRADED,
                    f"RULE_SUITES_UNREADABLE_{exc.status}",
                    f"Rule suites inaccessible for {full_name} — bypass events NOT observed, "
                    f"which is not the same as none occurring: {exc.body[:120]}",
                    message_data={"repo": full_name, "status": exc.status},
                )
                return
            raise

        for suite in suites:
            suite_uuid = rule_suite_id(suite["id"])
            actor_login = str(suite.get("actor_name") or "")
            ref = str(suite.get("ref") or "")
            bypassed = self._bypassed_rules(client, full_name, suite)
            nodes.append(
                node_envelope(
                    entity_id=suite_uuid,
                    entity_type="github_core__rule_suite",
                    name=f"{actor_login or 'unknown actor'} bypassed {ref.rsplit('/', 1)[-1] or 'a ref'}",
                    dimensions=dims,
                    fields={
                        "suite_id": suite["id"],
                        "full_name": full_name,
                        "result": str(suite.get("result") or ""),
                        "ref": ref,
                        "actor_login": actor_login,
                        "actor_id": suite.get("actor_id"),
                        "before_sha": str(suite.get("before_sha") or ""),
                        "after_sha": str(suite.get("after_sha") or ""),
                        "pushed_at": suite.get("pushed_at"),
                        "bypassed_rules": bypassed,
                        "configuration": {},
                        "tags": {},
                    },
                )
            )
            if actor_login:
                # An ACCOUNT, not an identity: GitHub says login and id, never whether the login
                # belongs to a person, a bot or a machine account (req-github-core-rule-suites-2).
                actor_uuid = account_id(actor_login)
                if actor_login not in self._emitted_actor_logins:
                    self._emitted_actor_logins.add(actor_login)
                    nodes.append(
                        node_envelope(
                            entity_id=actor_uuid,
                            entity_type="github_core__github_account",
                            name=actor_login,
                            dimensions={**dims, "github.surface": "accounts"},
                            fields={
                                "login": actor_login,
                                "github_id": suite.get("actor_id"),
                                # Unobserved, not "User": this surface does not say which.
                                "account_type": "",
                                "html_url": f"https://github.com/{actor_login}",
                                "configuration": {},
                                "tags": {},
                            },
                        )
                    )
                edges.append(
                    self._edge(
                        # Account -> suite: the push is what happened, and the account is
                        # what initiated it. The passive form had the initiator as target.
                        "TRIGGERED_EVALUATION__github_core", actor_uuid, suite_uuid, dims,
                        properties={"actor_id": suite.get("actor_id")},
                    )
                )
            ref_uuid = ref_uuid_by_ref.get(ref)
            if ref_uuid is not None:
                edges.append(
                    self._edge(
                        "EVALUATED_ON_REF__github_core", suite_uuid, ref_uuid, dims,
                        properties={
                            "before_sha": str(suite.get("before_sha") or ""),
                            "after_sha": str(suite.get("after_sha") or ""),
                        },
                    )
                )
            for rule in bypassed:
                if rule.get("ruleset_id") is None:
                    continue
                edges.append(
                    self._edge(
                        "BYPASSED_RULE__github_core",
                        suite_uuid,
                        ruleset_id(_owner_of(full_name), rule["ruleset_id"]),
                        dims,
                        properties={
                            "rule_type": str(rule.get("rule_type") or ""),
                            "enforcement": str(rule.get("enforcement") or ""),
                            "details": str(rule.get("details") or ""),
                        },
                    )
                )
        if suites:
            self.record_info(
                _SITE_RULE_SUITE_FOUND,
                "RULE_SUITES_BYPASS",
                f"{len(suites)} bypass event(s) on {full_name} in the last {_RULE_SUITE_WINDOW}.",
                message_data={"repo": full_name, "count": len(suites), "window": _RULE_SUITE_WINDOW},
            )

    def _bypassed_rules(
        self, client: GithubClient, full_name: str, suite: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """The rules a suite did not satisfy, from its detail. Degrades to [] with the list intact.

        The list endpoint says a bypass happened; only the detail says WHICH control was gone
        around, which is what turns the event from a log line into a finding.
        """
        try:
            detail = client.get(f"/repos/{full_name}/rulesets/rule-suites/{suite['id']}")
        except GithubAPIError:
            return []
        out: list[dict[str, Any]] = []
        for evaluation in detail.get("rule_evaluations") or []:
            if evaluation.get("result") == "pass":
                continue
            source = evaluation.get("rule_source") or {}
            out.append(
                {
                    "rule_type": evaluation.get("rule_type"),
                    "enforcement": evaluation.get("enforcement"),
                    "result": evaluation.get("result"),
                    "ruleset_id": source.get("id") if source.get("type") == "ruleset" else None,
                    "ruleset_name": source.get("name"),
                    "details": evaluation.get("details"),
                }
            )
        return out

    def _collect_caches(
        self,
        client: GithubClient,
        full_name: str,
        repo_uuid: Any,
        dims: dict[str, str],
        ref_uuid_by_ref: dict[str, Any],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Collect stored cache entries and scope each to the ref that produced it.

        The `SCOPED_TO` edge is emitted only when the entry's ref is one we observed. Its absence
        is usually the interesting case rather than a gap: an entry scoped to `refs/pull/42/merge`
        came from a pull request, and a cache written outside a branch and restored inside it is
        the shape five incidents share.
        """
        try:
            payload = client.get(
                f"/repos/{full_name}/actions/caches", params={"per_page": str(_CACHE_LIMIT_PER_REPO)}
            )
        except GithubAPIError as exc:
            if exc.status in (403, 404):
                self.record_warn(
                    _SITE_CACHE_DEGRADED,
                    f"CACHES_UNREADABLE_{exc.status}",
                    f"Cache list inaccessible for {full_name}: {exc.body[:120]}",
                    message_data={"repo": full_name, "status": exc.status},
                )
                return
            raise
        entries = payload.get("actions_caches") or []
        total = int(payload.get("total_count") or len(entries))
        for entry in entries:
            cache_uuid = actions_cache_id(full_name, entry["id"])
            ref = str(entry.get("ref") or "")
            nodes.append(
                node_envelope(
                    entity_id=cache_uuid,
                    entity_type="github_core__actions_cache",
                    name=str(entry.get("key") or entry["id"]),
                    dimensions=dims,
                    fields={
                        "full_name": full_name,
                        "cache_id": entry["id"],
                        "key": str(entry.get("key") or ""),
                        "version": str(entry.get("version") or ""),
                        "ref": ref,
                        "size_in_bytes": entry.get("size_in_bytes"),
                        "created_at": entry.get("created_at"),
                        "last_accessed_at": entry.get("last_accessed_at"),
                        "configuration": {},
                        "tags": {},
                    },
                )
            )
            edges.append(self._edge("HAS_CACHE__github_core", repo_uuid, cache_uuid, dims))
            ref_uuid = ref_uuid_by_ref.get(ref)
            if ref_uuid is not None:
                edges.append(self._edge("SCOPED_TO__github_core", cache_uuid, ref_uuid, dims))
        if total > len(entries):
            self.record_warn(
                _SITE_CACHES_TRUNCATED,
                "CACHES_TRUNCATED",
                f"{full_name}: collected {len(entries)} of {total} cache entries (most recently "
                f"accessed first). Absence of an entry in this batch is not evidence it is gone.",
                message_data={"repo": full_name, "collected": len(entries), "total": total},
            )

    def _collect_app_installations(
        self,
        client: GithubClient,
        owner: str | None,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Emit the App inventory — application, installation, and the account it was granted on.

        **Which endpoint answers this matters more than it looks.** `/app/installations` answers
        "where is THIS App installed" — one row, about ourselves. `/orgs/{owner}/installations`
        answers "which Apps can reach this account's repositories", which is the question the
        product exists to ask, and it is App-only: a personal access token gets `404`. We ask the
        account first and fall back to our own installation, saying which we got, because an
        inventory of one is not an inventory.

        In PAT mode nothing is emitted and, crucially, nothing is CLAIMED: an empty inventory from
        a token means the surface was unreachable, not that no Apps are installed.
        """
        if not self._auth.has_app:
            self.record_info(
                _SITE_INSTALLATIONS_UNREACHABLE,
                "APP_INVENTORY_UNREACHABLE",
                "No App credential in the envelope: the installed-App inventory is an App-only "
                "surface and was not collected. This is not an observation that no Apps are "
                "installed — "
                + (self._auth.absent_note(PREFER_APP) or "add an App to see it") + ".",
                message_data={"held": self._auth.held},
            )
            return
        scope = "account"
        installations: list[dict[str, Any]] = []
        if owner is not None:
            try:
                installations = client.get_paginated(
                    f"/orgs/{owner}/installations", item_path="installations"
                )
            except GithubAPIError as exc:
                self.record_warn(
                    _SITE_INSTALLATIONS_UNREACHABLE,
                    f"APP_INVENTORY_PARTIAL_{exc.status}",
                    f"Cannot list the Apps installed on {owner} ({exc.status}) — this credential "
                    f"lacks organization administration read, so only THIS App's own installation "
                    f"is recorded. The absence of other Apps below is not evidence there are none.",
                    message_data={"owner": owner, "status": exc.status},
                )
                scope = "self_only"
        if not installations:
            try:
                installations = self._auth.installations()
                scope = "account" if scope == "account" and owner is None else "self_only"
            except GithubAppAuthError as exc:
                self.record_warn(
                    _SITE_INSTALLATIONS_UNREACHABLE,
                    "APP_INVENTORY_FAILED",
                    f"Installed-App inventory unreadable: {exc}",
                )
                return
        apps_dims = {**_PLATFORM_DIMENSIONS, "github.surface": "apps"}
        for installation in installations:
            inst_id = installation.get("id")
            if inst_id is None:
                continue
            slug = str(installation.get("app_slug") or "")
            account = installation.get("account") or {}
            account_login = str(account.get("login") or "")
            inst_uuid = app_installation_id(inst_id)
            if str(inst_uuid) in self._emitted_installation_ids:
                continue
            self._emitted_installation_ids.add(str(inst_uuid))
            nodes.append(
                node_envelope(
                    entity_id=inst_uuid,
                    entity_type="github_core__app_installation",
                    name=f"{slug} @ {account_login}" if slug and account_login else str(inst_id),
                    dimensions=apps_dims,
                    fields={
                        "installation_id": inst_id,
                        "app_id": installation.get("app_id"),
                        "app_slug": slug,
                        "account_login": account_login,
                        "target_type": str(installation.get("target_type") or ""),
                        "repository_selection": str(installation.get("repository_selection") or ""),
                        "permissions": dict(installation.get("permissions") or {}),
                        "events": list(installation.get("events") or []),
                        "suspended": installation.get("suspended_at") is not None,
                        "installed_at": installation.get("created_at"),
                        "html_url": str(installation.get("html_url") or ""),
                        "configuration": {},
                        "tags": {},
                    },
                )
            )
            if slug:
                app_uuid = github_app_id(slug)
                if str(app_uuid) not in self._emitted_app_ids:
                    self._emitted_app_ids.add(str(app_uuid))
                    nodes.append(
                        node_envelope(
                            entity_id=app_uuid,
                            entity_type="github_core__github_app",
                            name=slug,
                            dimensions=apps_dims,
                            fields={
                                "slug": slug,
                                "name": slug,
                                "app_id": installation.get("app_id"),
                                "client_id": str(installation.get("client_id") or ""),
                                "html_url": f"https://github.com/apps/{slug}",
                                "description": "",
                                "configuration": {},
                                "tags": {},
                            },
                        )
                    )
                edges.append(self._edge("HAS_INSTALLATION__github_core", app_uuid, inst_uuid, apps_dims))
            if account_login:
                edges.append(
                    self._edge("INSTALLED_ON__github_core", inst_uuid, account_id(account_login), apps_dims)
                )
        self.record_info(
            _SITE_INSTALLATIONS_COLLECTED,
            "APP_INVENTORY_COLLECTED",
            f"Collected {len(installations)} App installation(s)"
            + (
                " on this account."
                if scope == "account"
                else " — THIS App's own only; the account-wide inventory was unreadable."
            ),
            message_data={"installations": len(installations), "scope": scope},
        )

    def _emit_github_app(
        self,
        app_meta: dict[str, str],
        full_name: str,
        repo_uuid: Any,
        repo_dims: dict[str, str],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Emit a github_app node (deduped, singleton by slug) + ENABLED_ON edge
        for a platform app detected enabled on ``full_name``."""
        apps_dims = {**repo_dims, "github.surface": "apps"}
        app_uuid = github_app_id(app_meta["slug"])
        if str(app_uuid) not in self._emitted_app_ids:
            self._emitted_app_ids.add(str(app_uuid))
            nodes.append(
                node_envelope(
                    entity_id=app_uuid,
                    entity_type="github_core__github_app",
                    name=app_meta["name"],
                    dimensions=apps_dims,
                    fields={
                        "slug": app_meta["slug"],
                        "name": app_meta["name"],
                        "app_id": None,
                        "html_url": app_meta.get("html_url", ""),
                        "description": app_meta.get("description", ""),
                        "configuration": {},
                        "tags": {},
                    },
                )
            )
        edges.append(self._edge("ENABLED_ON__github_core", app_uuid, repo_uuid, apps_dims))
        self.record_info(
            _SITE_DEPENDABOT_APP,
            "GITHUB_APP_ENABLED",
            f"{app_meta['name']} app detected enabled on {full_name} "
            f"(reclassified from the synthetic Actions workflow entry).",
            message_data={"app_slug": app_meta["slug"], "repo": full_name},
        )

    def _edge(
        self,
        edge_type: str,
        source_uuid: Any,
        target_uuid: Any,
        dimensions: dict[str, str],
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One edge envelope. `properties` defaults to empty, which every prior caller wanted."""
        return edge_envelope(
            entity_id=edge_id(edge_type, source_uuid, target_uuid),
            edge_type=edge_type,
            source_id=source_uuid,
            target_id=target_uuid,
            dimensions=dimensions,
            properties=properties or {},
        )

    def _fetch_account(self, client: GithubClient, owner: str) -> dict[str, Any]:
        try:
            return client.get(f"/users/{owner}")
        except GithubAPIError as exc:
            if exc.status == 404:
                # Maybe it's an org; let /orgs handle it (rare for user-owned repos).
                return client.get(f"/orgs/{owner}")
            raise

    def _fetch_run_window(self, client: GithubClient, full_name: str, run_limit: int) -> list[dict[str, Any]]:
        """Run-list fetch per req-github-core-collector-3.

        First population (no on-grid runs): the latest `run_limit` runs.
        Later populations: every run created since the latest on-grid
        `run_started_at` for this repo, scoped via GitHub's `?created=>=ISO`
        filter. The first-fetch cap is `run_limit`; later fetches are
        unbounded but typically small (only new runs since last collection).
        """
        from django.db.models import Max
        from tap_plugin.github_core.models import GithubActionsRun

        max_ts = GithubActionsRun.objects.filter(full_name=full_name).aggregate(Max("run_started_at"))[
            "run_started_at__max"
        ]
        if max_ts is None:
            # First population — cap at run_limit, one page is enough.
            return client.get_paginated(
                f"/repos/{full_name}/actions/runs",
                params={"per_page": str(min(run_limit, 100))},
                item_path="workflow_runs",
                max_pages=1,
            )[:run_limit]

        # Incremental — fetch everything strictly newer. GitHub's `created`
        # filter accepts ISO 8601 with comparison operators. We use `>` not
        # `>=` to avoid re-fetching the boundary run we already have.
        iso = max_ts.astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.record_info(
            _SITE_INCREMENTAL_WINDOW,
            "INCREMENTAL_WINDOW",
            f"{full_name}: incremental run fetch since {iso}.",
        )
        return client.get_paginated(
            f"/repos/{full_name}/actions/runs",
            params={"created": f">{iso}", "per_page": "100"},
            item_path="workflow_runs",
        )

    def _fetch_non_terminal_refresh(
        self,
        client: GithubClient,
        full_name: str,
        already_fetched_run_ids: set[int],
    ) -> list[dict[str, Any]]:
        """Re-fetch on-grid runs whose status is non-terminal per
        req-github-core-collector-4.

        Each run is fetched via the single-run endpoint
        `/repos/{owner}/{repo}/actions/runs/{run_id}`. A 404 on that endpoint
        (real, body-bearing — the empty-body retry in api_client already
        absorbed the intermittent ones) records a structured warn and the
        run is skipped from this refresh; mirrors the per-run /jobs degrade
        in `_fetch_run_jobs`.
        """
        from tap_plugin.github_core.models import GithubActionsRun

        non_terminal_ids = list(
            GithubActionsRun.objects.filter(full_name=full_name)
            .exclude(status__in=list(_TERMINAL_RUN_STATUSES))
            .exclude(run_id__in=list(already_fetched_run_ids))
            .values_list("run_id", flat=True)
        )
        if not non_terminal_ids:
            return []
        self.record_info(
            _SITE_NON_TERMINAL_REFRESH,
            "NON_TERMINAL_REFRESH",
            f"{full_name}: refreshing {len(non_terminal_ids)} non-terminal run(s).",
        )
        refreshed: list[dict[str, Any]] = []
        for rid in non_terminal_ids:
            try:
                payload = client.get(f"/repos/{full_name}/actions/runs/{rid}")
            except GithubAPIError as exc:
                if exc.status == 404:
                    self.record_warn(
                        _SITE_RUN_NOT_FOUND,
                        "RUN_NOT_FOUND",
                        f"{full_name} run {rid}: single-run endpoint 404 "
                        f"({exc.body[:120] or '(empty)'}). "
                        f"Skipping refresh for this run.",
                    )
                    continue
                raise
            refreshed.append(payload)
        return refreshed

    def _fetch_run_jobs(self, client: GithubClient, full_name: str, run_id_int: int) -> list[dict[str, Any]]:
        """Fetch the jobs list for a specific run.

        Per GitHub docs the endpoint documents only `200 - OK`; no 404
        condition is documented. The HTTP client retries empty-body 404s
        (undocumented intermittent quirk; see api_client module docstring).
        Real 404s — a JSON body with `{"message": "..."}` — still propagate
        and we graceful-degrade per-run to avoid aborting the whole collection
        on a single quirky run (`req-github-core-collector-5` discipline).
        """
        try:
            return client.get_paginated(f"/repos/{full_name}/actions/runs/{run_id_int}/jobs", item_path="jobs")
        except GithubAPIError as exc:
            if exc.status == 404:
                self.record_warn(
                    _SITE_RUN_JOBS_MISSING,
                    "RUN_JOBS_MISSING",
                    f"{full_name} run {run_id_int} jobs endpoint returned 404 "
                    f"with body: {exc.body[:120] or '(empty)'}. "
                    f"Skipping job collection for this run.",
                )
                return []
            raise

    def _fetch_workflow_config(self, client: GithubClient, full_name: str, path: str) -> tuple[str, dict[str, Any]]:
        """Fetch workflow YAML via Contents API; return (raw, parsed configuration)."""
        if not path:
            return "", parse_workflow_yaml("")
        try:
            payload = client.get(f"/repos/{full_name}/contents/{path}")
        except GithubAPIError as exc:
            if exc.status == 404:
                self.record_warn(
                    _SITE_WORKFLOW_YAML_MISSING,
                    "WORKFLOW_YAML_MISSING",
                    f"{full_name} workflow file not found: {path}",
                )
                return "", parse_workflow_yaml("")
            raise
        encoded = payload.get("content", "")
        raw_yaml = base64.b64decode(encoded).decode("utf-8") if encoded else ""
        return raw_yaml, parse_workflow_yaml(raw_yaml)

    def _record_enrichment_summary(self, enrichment: Any) -> None:
        emitted = sum(1 for r in enrichment.resolutions if r.emitted_edge)
        zero = sum(1 for r in enrichment.resolutions if r.candidate_count == 0)
        multi = sum(1 for r in enrichment.resolutions if r.candidate_count > 1)
        near = len(enrichment.near_matches)
        skipped = len(enrichment.skipped_rules)
        self.record_info(
            _SITE_ENRICHMENT_SUMMARY,
            "ENRICHMENT_SUMMARY",
            f"Link resolution: {emitted} edge(s) emitted, "
            f"{zero} zero-candidate, {multi} multi-candidate, "
            f"{near} near-match warning(s), {skipped} rule(s) skipped (target vocabulary not installed).",
        )
        for rule in enrichment.skipped_rules:
            self.record_warn(
                _SITE_LINK_RULE_SKIPPED,
                "LINK_RULE_SKIPPED",
                f"Link rule {rule.rule_name!r} skipped: type {rule.missing_entity_type!r} is not "
                f"installed in this composition (req-github-core-grid-links-8).",
                message_data={"rule": rule.rule_name, "missing_entity_type": rule.missing_entity_type},
            )
        # Multi-candidate failures are warnings per req-github-core-grid-links-3.
        for res in enrichment.resolutions:
            if res.candidate_count > 1:
                self.record_warn(
                    "3812",
                    "LINK_AMBIGUOUS",
                    f"Rule {res.rule_name} on {res.source_entity_type}: value "
                    f"{res.source_value!r} matched {res.candidate_count} candidates; no edge emitted.",
                )
        # Near-match warnings surface "almost looks right but not quite" rows
        # (GHES tenant URLs, typos, alternate audiences) so the operator can
        # investigate instead of silently missing the link.
        for nm in enrichment.near_matches:
            self.record_warn(
                "db00",
                "LINK_NEAR_MATCH",
                f"Rule {nm.rule_name}: no exact match for {nm.expected_value!r} on "
                f"{nm.target_entity_type}.{nm.expected_value}, but a near-match exists: "
                f"{nm.target_value!r}. Not linking — investigate whether the rule should "
                f"be extended or the target is misconfigured.",
            )
