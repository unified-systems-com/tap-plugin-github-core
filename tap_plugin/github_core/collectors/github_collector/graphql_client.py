"""GitHub GraphQL client — the config-layer transport.

Why this exists alongside the REST client rather than replacing it: **GitHub's GraphQL API does
not expose Actions executions.** `Repository` has no field for workflows, workflow runs or jobs
(verified against the live schema 2026-08-27; the `WorkflowRun` type exists but is reachable only
from a `CheckSuite`, which is not an enumeration path). So the split is not a preference, it is the
API's shape:

* **GraphQL — the configuration layer.** Repository enumeration, metadata, default-branch head,
  rulesets, environments, and the *content* of every workflow file, for a whole account, in one
  request. Measured against a 19-repo organization: 1 request, ~4s, **1 rate-limit point of 5000**,
  returning 46 workflow files with 172 KB of YAML inlined.
* **REST — the operation layer.** Workflow registrations (the numeric id and state that runs link
  to), runs, jobs and runners. No GraphQL equivalent exists.

That division happens to fall exactly along the design/config/operation seam the product reasons
about, which is a convenience rather than a coincidence: configuration is declarative and lives in
the repository, execution is event data and lives in the Actions service.

**Partial success is the point.** GraphQL answers `200` with both `data` and `errors`, so one
forbidden field degrades that field instead of the request. A PAT without admin gets its rulesets
and loses only `branchProtectionRules` — verified. The REST client cannot do this, and it is why
the flaky per-file content fetches are worth moving here.
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
          rulesets(first: 20) {
            nodes {
              databaseId
              name
              enforcement
              target
              source { __typename ... on Organization { login } ... on Repository { nameWithOwner } }
            }
          }
          environments(first: 20) { nodes { name } }
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
""" % {"page": _REPO_PAGE_SIZE}


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
