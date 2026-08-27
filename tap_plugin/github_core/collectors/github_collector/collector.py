"""GitHubCollector — CollectorBase subclass driving the v0 collection.

Spec: plugins/github_core/specs/spec-github-core-v0.md
(req-github-core-collector). Two-phase run: collection + enrichment, both
through `CollectorBase.submit_grift`.
"""

from __future__ import annotations

import base64
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
from .batch import (
    assemble_batch,
    edge_envelope,
    node_envelope,
)
from .enrichment import resolve_links
from .identity import (
    account_id,
    edge_id,
    github_app_id,
    job_id,
    platform_id,
    repository_id,
    run_id,
    runner_id,
    workflow_id,
)
from .manifest import load_collection_manifest, load_link_manifest
from .parser import parse_workflow_yaml
from .graphql_client import GithubGraphQLClient, GithubGraphQLError
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


class GithubCollector(CollectorBase):
    """GitHub Actions collector — single PAT, account-scoped (or explicit repos), two-phase run.

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

        Four checks in order; each short-circuits the rest on failure with an
        actionable readiness status:
          1. GITHUB_SECRET_PRESENT — `github_pat` secret file exists
          2. GITHUB_SECRET_VALID   — secret schema validates (token, repos, ...)
          3. GITHUB_API_REACHABLE  — `GET /rate_limit` succeeds within budget
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
                    f"github_pat secret is not configured: {exc}",
                    readiness_status=CollectorReadinessStatus.UNCONFIGURED,
                    docs=_DOCS,
                )
            )
            return CollectorSelfTestResult.from_checks(
                checks,
                summary="github_pat secret is not configured.",
                docs=_DOCS,
            )
        except (SecretValidationError, SecretError) as exc:
            # 2. Secret malformed (schema failed).
            checks.append(
                check_fail(
                    "GITHUB_SECRET_VALID",
                    f"github_pat secret is malformed: {exc}",
                    readiness_status=CollectorReadinessStatus.MISCONFIGURED,
                    docs=_DOCS,
                )
            )
            return CollectorSelfTestResult.from_checks(
                checks,
                summary="github_pat secret is malformed.",
                docs=_DOCS,
            )
        checks.append(
            check_pass(
                "GITHUB_SECRET_VALID",
                "github_pat secret resolves and is the expected kind.",
                docs=_DOCS,
            )
        )

        data = dict(secret.data)
        owner = collection_owner(data)
        repos: list[str] = explicit_repos(data)

        # 3. API reachable + PAT authenticates — GET /rate_limit.
        # No-retry client so a real 401/403 surfaces immediately.
        client = GithubClient(
            token=data["token"],
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
                summary="GitHub API unreachable or PAT auth failed.",
                docs=_DOCS,
            )
        core = rate.get("rate") or rate.get("resources", {}).get("core", {})
        checks.append(
            check_pass(
                "GITHUB_API_REACHABLE",
                f"GitHub API reachable; PAT rate-limit " f"{core.get('used', '?')}/{core.get('limit', '?')} used.",
                context={"rate": core},
                docs=_DOCS,
            )
        )

        # 4a. Account scope (req-github-core-org-scope): the PAT can see the owner and
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
                        f"PAT cannot enumerate repositories under {owner}: status={exc.status} "
                        f"body={exc.body[:200] or '(empty)'}",
                        readiness_status=CollectorReadinessStatus.ERROR,
                        docs=_DOCS,
                    )
                )
            else:
                checks.append(
                    check_pass(
                        f"GITHUB_OWNER_ACCESS:{owner}",
                        f"PAT enumerates {len(listing)} repo(s) under {owner}"
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
                        f"PAT cannot access {repo}: status={exc.status} " f"body={exc.body[:200] or '(empty)'}",
                        readiness_status=CollectorReadinessStatus.ERROR,
                        docs=_DOCS,
                    )
                )
            else:
                checks.append(
                    check_pass(
                        f"GITHUB_REPO_ACCESS:{repo}",
                        f"PAT has access to {repo}.",
                        context={"repo": repo},
                        docs=_DOCS,
                    )
                )

        return CollectorSelfTestResult.from_checks(
            checks,
            summary=(
                f"GitHub Core collector is ready; {len(repos)} repo(s) accessible."
                if repo_access_ok
                else "GitHub Core collector PAT cannot access one or more configured repos."
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

        # --- secret resolution (unrecoverable on failure) ---
        try:
            secret = resolve_github_secret(GITHUB_SECRET_REF)
        except SecretError as exc:
            self._abort(_SITE_ABORT_SECRET, "SECRET_UNUSABLE", f"github_pat secret unusable: {exc}")
        data = dict(secret.data)
        run_limit = initial_run_limit(data)

        client = GithubClient(token=data["token"], api_base_url=api_base_url(data))

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
        gql = GithubGraphQLClient(token=data["token"], api_base_url=api_base_url(data))
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

            # jobs for this run (latest-attempt endpoint per req-github-core-collector-8)
            jobs = self._fetch_run_jobs(client, full_name, r["id"])
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

        # EXECUTED_ON edges (only when an observed job runner_id matches a durable runner node)
        for r in run_payloads:
            run_jobs = self._fetch_run_jobs(client, full_name, r["id"])
            for j in run_jobs:
                if j.get("runner_id") and j["runner_id"] in runner_uuid_by_id:
                    j_uuid = job_id(full_name, j["id"])
                    rn_uuid = runner_uuid_by_id[j["runner_id"]]
                    edges.append(self._edge("EXECUTED_ON__github_core", j_uuid, rn_uuid, observation_dims))

    # ---------- helpers ----------

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
    ) -> dict[str, Any]:
        return edge_envelope(
            entity_id=edge_id(edge_type, source_uuid, target_uuid),
            edge_type=edge_type,
            source_id=source_uuid,
            target_id=target_uuid,
            dimensions=dimensions,
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
