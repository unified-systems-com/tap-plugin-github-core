"""GitHub GraphQL client — the config-layer transport.

Why this exists alongside the REST client rather than replacing it: **GitHub's GraphQL API does
not expose Actions executions.** `Repository` has no field for workflows, workflow runs or jobs
(verified against the live schema 2026-08-27; the `WorkflowRun` type exists but is reachable only
from a `CheckSuite`, which is not an enumeration path). So the split is not a preference, it is the
API's shape:

* **GraphQL — the configuration layer.** Repository enumeration, metadata, rulesets (with their
  conditions, rules and bypass actors), environments, every branch and tag with the commit it
  points at, releases (an output that happens to ride this transport), and the *content* of
  every workflow file, for a whole account, in one request.
  Measured against a 19-repo organization: 1 request, **64 rate-limit points of 5000**, returning
  the config layer whole — 165 refs, 4 rulesets per repo, 46 workflow files, 172 KB of YAML
  inlined. (The metadata-only form of this query cost 1 point; rulesets, refs and environments are
  what take it to 64 — still under 2% of an hour's budget, for an entire account.)
* **REST — the operation layer.** Workflow registrations (the numeric id and state that runs link
  to), runs, jobs, runners and artifacts. No GraphQL equivalent exists. GitHub Packages is REST
  too, and worse: the GraphQL `packages` connection answered `totalCount: 0` with no error for an
  organization whose ghcr.io images the REST detail endpoint returned — the container registry is
  simply not on that transport, and an empty connection there proves nothing.

That division happens to fall exactly along the design/config/operation seam the product reasons
about, which is a convenience rather than a coincidence: configuration is declarative and lives in
the repository, execution is event data and lives in the Actions service.

**Partial success is the point.** GraphQL answers `200` with both `data` and `errors`, so one
forbidden field degrades that field instead of the request. A PAT without admin gets its rulesets
and loses only `branchProtectionRules` — verified. The REST client cannot do this, and it is why
the flaky per-file content fetches are worth moving here.

**One asymmetry to carry.** `bypassActors` comes back here with no error at all for a credential
REST refuses outright (REST omits the `bypass_actors` key entirely). An empty list from this
transport therefore cannot be trusted to mean "nobody" — see `spec-github-core-vocabulary.md`
open question 3. The collector treats a non-empty answer as proof and an empty one as unproven,
never as zero.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# One page of repositories per round trip. GitHub caps connection pages at 100.
_REPO_PAGE_SIZE = 100
# Per-repository sub-connection caps. Each is a place the answer can be silently truncated, so
# every one of them reports its `totalCount` and the collector warns when the walk came up short —
# a partial ref list that reads as complete is how "there is no such tag" becomes a wrong answer.
_RULESET_PAGE_SIZE = 20
_RULE_PAGE_SIZE = 30
_BYPASS_ACTOR_PAGE_SIZE = 30
_ENVIRONMENT_PAGE_SIZE = 20
_REF_PAGE_SIZE = 100
# Releases newest-first; an active repository cuts far fewer than this per collection window,
# and `totalCount` says how many the cap left behind.
_RELEASE_PAGE_SIZE = 50
_RELEASE_ASSET_PAGE_SIZE = 50
_TIMEOUT_SECONDS = 60


class GithubGraphQLError(Exception):
    """A GraphQL request failed outright (transport, auth, or a query-level error)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# The config-layer query. Deliberately does NOT request `branchProtectionRules`: it is admin-only
# and its absence would add a FORBIDDEN error to every response for a read-only credential, which
# would train us to ignore the errors array. Rulesets are the current mechanism anyway.
_CONFIG_QUERY = """
query($login: String!, $cursor: String) {
  rateLimit { cost remaining }
  repositoryOwner(login: $login) {
    __typename
    ... on RepositoryOwner {
      repositories(first: %(page)d, after: $cursor, ownerAffiliations: [OWNER]) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          nameWithOwner
          name
          databaseId
          isArchived
          isFork
          visibility
          url
          defaultBranchRef { name target { oid } }
          rulesets(first: %(rulesets)d) {
            nodes {
              databaseId
              name
              enforcement
              target
              conditions { refName { include exclude } }
              rules(first: %(rules)d) { nodes { type } }
              bypassActors(first: %(bypass)d) {
                totalCount
                nodes {
                  bypassMode
                  organizationAdmin
                  repositoryRoleName
                  actor {
                    __typename
                    ... on App { databaseId slug name }
                    ... on Team { slug name }
                  }
                }
              }
            }
          }
          environments(first: %(envs)d) {
            nodes {
              databaseId
              name
              protectionRules(first: %(rules)d) {
                nodes { __typename ... on DeploymentProtectionRule { type timeout } }
              }
            }
          }
          branchRefs: refs(refPrefix: "refs/heads/", first: %(refs)d) {
            totalCount
            nodes { name target { oid } }
          }
          tagRefs: refs(refPrefix: "refs/tags/", first: %(refs)d) {
            totalCount
            nodes { name target { oid __typename ... on Tag { target { oid } } } }
          }
          releases(first: %(releases)d, orderBy: {field: CREATED_AT, direction: DESC}) {
            totalCount
            nodes {
              databaseId
              name
              tagName
              isDraft
              isPrerelease
              isLatest
              createdAt
              publishedAt
              url
              author { login }
              tagCommit { oid }
              releaseAssets(first: %(assets)d) {
                totalCount
                nodes { name size contentType downloadUrl createdAt }
              }
            }
          }
          object(expression: "HEAD:.github/workflows") {
            ... on Tree {
              entries { name path object { ... on Blob { byteSize isTruncated text } } }
            }
          }
        }
      }
    }
  }
}
""" % {
    "page": _REPO_PAGE_SIZE,
    "rulesets": _RULESET_PAGE_SIZE,
    "rules": _RULE_PAGE_SIZE,
    "bypass": _BYPASS_ACTOR_PAGE_SIZE,
    "envs": _ENVIRONMENT_PAGE_SIZE,
    "refs": _REF_PAGE_SIZE,
    "releases": _RELEASE_PAGE_SIZE,
    "assets": _RELEASE_ASSET_PAGE_SIZE,
}


class GithubGraphQLClient:
    """Minimal GraphQL client: one query, cursor pagination, partial-error surfacing."""

    def __init__(self, *, token: str, api_base_url: str = "https://api.github.com") -> None:
        self._token = token
        base = api_base_url.rstrip("/")
        # GitHub.com serves GraphQL at /graphql; GitHub Enterprise Server serves it at
        # /api/graphql while its REST base already ends in /api/v3.
        self._endpoint = f"{base[: -len('/v3')]}/graphql" if base.endswith("/api/v3") else f"{base}/graphql"
        self.last_cost: int = 0
        self.last_remaining: int | None = None

    def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=payload,
            headers={
                "Authorization": f"bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "tap-github-core-collector",
            },
            method="POST",
        )
        try:
            # nosec B310 — the endpoint derives from the credential envelope's `api_base_url`,
            # which GITHUB_PAT_SCHEMA constrains to https at resolve time (see secret.py).
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # nosec B310
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GithubGraphQLError(f"GraphQL HTTP {exc.code}: {exc.read()[:300]!r}", status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GithubGraphQLError(f"GraphQL transport failure: {exc}", status=0) from exc

        if body.get("data") is None:
            # No data at all means the whole query failed — a real error, not a degraded field.
            raise GithubGraphQLError(f"GraphQL returned no data: {json.dumps(body.get('errors'))[:300]}")
        return body

    def fetch_config_layer(self, login: str) -> tuple[list[dict[str, Any]], list[str]]:
        """Return every repository under ``login`` with its config layer, plus partial-error notes.

        Walks cursor pagination to the end. The second element is a list of human-readable notes
        for fields the credential could not read — surfaced rather than swallowed, because a
        silently-missing ruleset looks exactly like an organization with no rulesets.
        """
        repos: list[dict[str, Any]] = []
        notes: list[str] = []
        cursor: str | None = None
        cost = 0

        while True:
            body = self._post(_CONFIG_QUERY, {"login": login, "cursor": cursor})
            data = body["data"]
            for err in body.get("errors") or []:
                path = ".".join(str(p) for p in (err.get("path") or []))
                note = f"{err.get('type', 'ERROR')}: {err.get('message', '')} at {path or '<root>'}"
                if note not in notes:
                    notes.append(note)

            rate = data.get("rateLimit") or {}
            cost += int(rate.get("cost") or 0)
            self.last_remaining = rate.get("remaining")

            owner = data.get("repositoryOwner")
            if owner is None:
                raise GithubGraphQLError(f"no such account: {login!r}")
            connection = owner.get("repositories") or {}
            repos.extend(n for n in (connection.get("nodes") or []) if n)

            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")

        self.last_cost = cost
        logger.info(
            "[7d1c] graphql config layer: %d repo(s) for %s, cost %d, %s point(s) remaining",
            len(repos),
            login,
            cost,
            self.last_remaining,
        )
        return repos, notes

    @staticmethod
    def refs(repo: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Branches and tags as flat payloads, plus what the page caps left behind.

        Returns ``(refs, truncated)`` where ``truncated`` maps ``"branch"``/``"tag"`` to the
        number of refs the cap dropped. Callers surface that count rather than swallowing it: an
        answer that is silently 100 of 400 tags would let a *missing* tag read as a *deleted* one.

        For an annotated tag the ref points at a tag object which in turn points at the commit, so
        ``target_sha`` (what the ref holds) and ``head_sha`` (the commit it resolves to) differ.
        Both are kept: a re-tag that swaps only the tag object moves one and not the other.
        """
        out: list[dict[str, Any]] = []
        truncated: dict[str, int] = {}
        default_ref = ((repo.get("defaultBranchRef") or {}).get("name")) or ""
        for key, ref_type, prefix in (
            ("branchRefs", "branch", "refs/heads/"),
            ("tagRefs", "tag", "refs/tags/"),
        ):
            connection = repo.get(key) or {}
            nodes = [n for n in (connection.get("nodes") or []) if n]
            missing = int(connection.get("totalCount") or 0) - len(nodes)
            if missing > 0:
                truncated[ref_type] = missing
            for node in nodes:
                target = node.get("target") or {}
                target_sha = str(target.get("oid") or "")
                # An annotated tag nests the commit one level down; a lightweight tag and a branch
                # point straight at it.
                nested = target.get("target") or {}
                head_sha = str(nested.get("oid") or target_sha)
                name = str(node.get("name") or "")
                out.append(
                    {
                        "ref": f"{prefix}{name}",
                        "ref_type": ref_type,
                        "name": name,
                        "head_sha": head_sha,
                        "target_sha": target_sha,
                        "target_type": str(target.get("__typename") or "").lower(),
                        "is_default": ref_type == "branch" and name == default_ref,
                    }
                )
        return out, truncated

    @staticmethod
    def rulesets(repo: dict[str, Any]) -> list[dict[str, Any]]:
        """Rulesets applying to one repository, shaped for the emitter.

        `bypass_actors` is returned alongside `bypass_proven`: this transport answers even for a
        credential that cannot really see the list, so only a NON-EMPTY answer proves anything.
        A filtered connection can hide actors; it cannot invent them.
        """
        out: list[dict[str, Any]] = []
        for node in ((repo.get("rulesets") or {}).get("nodes") or []):
            if not node:
                continue
            bypass = node.get("bypassActors") or {}
            actors = [a for a in (bypass.get("nodes") or []) if a]
            conditions = node.get("conditions") or {}
            ref_name = conditions.get("refName") or {}
            out.append(
                {
                    "ruleset_id": node.get("databaseId"),
                    "name": node.get("name") or "",
                    # GraphQL shouts its enums (ACTIVE, BRANCH); REST whispers them (active,
                    # branch). One casing on the grid, and it is REST's, because that is what a
                    # reader of GitHub's own documentation will type into a query.
                    "enforcement": str(node.get("enforcement") or "").lower(),
                    "target": str(node.get("target") or "").lower(),
                    "conditions": {
                        "ref_name": {
                            "include": list(ref_name.get("include") or []),
                            "exclude": list(ref_name.get("exclude") or []),
                        }
                    },
                    "rules": [
                        {"type": str(r.get("type") or "").lower()}
                        for r in ((node.get("rules") or {}).get("nodes") or [])
                        if r
                    ],
                    "bypass_actors": actors,
                    "bypass_proven": bool(actors),
                }
            )
        return out

    @staticmethod
    def environments(repo: dict[str, Any]) -> list[dict[str, Any]]:
        """Deployment environments declared on one repository."""
        out: list[dict[str, Any]] = []
        for node in ((repo.get("environments") or {}).get("nodes") or []):
            if not node:
                continue
            out.append(
                {
                    "environment_id": node.get("databaseId"),
                    "name": node.get("name") or "",
                    "protection_rules": [
                        {"type": str(r.get("type") or "").lower(), "timeout": r.get("timeout")}
                        for r in ((node.get("protectionRules") or {}).get("nodes") or [])
                        if r
                    ],
                }
            )
        return out

    @staticmethod
    def releases(repo: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        """Releases of one repository, shaped for the emitter, plus how many the cap dropped.

        Returns ``(releases, missing)``. A release is a product of execution that happens to
        ride the config-layer transport; `tagCommit` is the commit the tag resolved to at
        observation, which is the half of tag-movement detection the release side holds.
        """
        connection = repo.get("releases") or {}
        nodes = [n for n in (connection.get("nodes") or []) if n]
        missing = max(int(connection.get("totalCount") or 0) - len(nodes), 0)
        out: list[dict[str, Any]] = []
        for node in nodes:
            assets_conn = node.get("releaseAssets") or {}
            out.append(
                {
                    "release_id": node.get("databaseId"),
                    "tag_name": str(node.get("tagName") or ""),
                    "name": str(node.get("name") or ""),
                    "is_draft": node.get("isDraft"),
                    "is_prerelease": node.get("isPrerelease"),
                    "is_latest": node.get("isLatest"),
                    "author_login": str((node.get("author") or {}).get("login") or ""),
                    "target_sha": str((node.get("tagCommit") or {}).get("oid") or ""),
                    "created_at": node.get("createdAt"),
                    "published_at": node.get("publishedAt"),
                    "html_url": str(node.get("url") or ""),
                    "asset_count": assets_conn.get("totalCount"),
                    "assets": [
                        {
                            "name": str(a.get("name") or ""),
                            "size": a.get("size"),
                            "content_type": str(a.get("contentType") or ""),
                            "download_url": str(a.get("downloadUrl") or ""),
                            "created_at": a.get("createdAt"),
                        }
                        for a in (assets_conn.get("nodes") or [])
                        if a
                    ],
                }
            )
        return out, missing

    @staticmethod
    def workflow_files(repo: dict[str, Any]) -> dict[str, str]:
        """Map ``.github/workflows/<file>`` -> YAML text for one GraphQL repository node.

        Truncated blobs are omitted deliberately: a partial YAML would parse into a workflow that
        is not the one in the repository, and a missing entry is honest where a wrong one is not.
        """
        tree = repo.get("object") or {}
        out: dict[str, str] = {}
        for entry in tree.get("entries") or []:
            blob = entry.get("object") or {}
            text = blob.get("text")
            if text is None or blob.get("isTruncated"):
                continue
            out[f".github/workflows/{entry['name']}"] = text
        return out
