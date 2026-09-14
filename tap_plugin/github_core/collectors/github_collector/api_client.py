"""Minimal stdlib HTTP client for GitHub's REST API.

Stays on stdlib (`urllib`) to avoid adding a new dependency. PAT auth, basic
pagination via the `Link: rel="next"` header, and a structured `GithubAPIError`
for non-200 responses. Used by the collector during the collection phase.

Empty-body-404 retry: GitHub returns intermittent 404s with empty response
bodies on `/actions/*` endpoints under conditions that are not documented
(rate-limit docs explicitly state secondary rate limits return 403/429, not
404). Observed signature: valid `X-GitHub-Request-Id`, `Server: github.com`,
HTTP 404, zero-length body. Real GitHub 404s (missing resource, permission
denied) always carry a JSON `{"message": "Not Found"}` body. This client
retries empty-body 404s with exponential backoff bounded to a small ceiling
so real 404s still fail loudly with their explanatory body.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .github_call import CallContext, HttpFailure, RetryPolicy, Seam, call, endpoint_template, scope_from_path

logger = logging.getLogger(__name__)

_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')

# Identifies our collector in GitHub's user-agent logs (req: API politeness).
USER_AGENT = "tap-github-core-collector/0.1"



@dataclass(frozen=True)
class GithubAPIError(Exception):
    """A GitHub API call returned a non-success status."""

    status: int
    url: str
    body: str

    def __str__(self) -> str:
        return f"GitHub API {self.status} on {self.url}: {self.body[:200]}"


class GithubClient:
    """A thin authenticated HTTP wrapper around GitHub's REST API.

    `retry_empty_404=True` (default) absorbs GitHub's intermittent empty-body
    404 quirk on `/actions/*` endpoints (see module docstring). Self-test
    paths instantiate with `retry_empty_404=False` so a real auth/access
    failure surfaces immediately as a 404 rather than spending the full
    backoff budget waiting for retries that won't change the outcome.
    """

    def __init__(
        self,
        *,
        token: str,
        api_base_url: str = "https://api.github.com",
        retry_empty_404: bool = True,
        seam: Seam | None = None,
        credential_kind: str = "",
    ) -> None:
        self._token = token
        self._api_base_url = api_base_url.rstrip("/")
        # `retry_empty_404=False` is the self-test's "one attempt, surface a real 401/403 now"
        # switch; under the seam it means a single-attempt policy for THIS client.
        self._policy = RetryPolicy() if retry_empty_404 else RetryPolicy(attempts=1)
        self._seam = seam or Seam.standalone()
        #: This client's credential kind when it differs from the run's primary (the PAT-bound
        #: ruleset client under an App run) — provenance names the token that actually asked.
        self._credential_kind = credential_kind

    # Set by every `get_paginated` walk: did it reach the end of the Link chain?
    last_walk_complete: bool = True

    def get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        """Single GET; returns decoded JSON. Raises GithubAPIError on non-2xx."""
        url = self._build_url(path, params)
        return self._request(url)

    def get_paginated(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        item_path: str | None = None,
        max_pages: int = 100,
    ) -> list[Any]:
        """Walk Link-header pagination, accumulate items.

        If `item_path` is given (e.g. "workflow_runs"), extract that array
        from each page; otherwise flatten the page if it's a list.
        """
        url = self._build_url(path, params)
        items: list[Any] = []
        pages = 0
        while url and pages < max_pages:
            payload = self._request(url)
            page_items = payload.get(item_path, []) if item_path else payload
            if isinstance(page_items, list):
                items.extend(page_items)
            url = self._next_link
            pages += 1
        # True only when the walk ran off the end of the Link chain — a `max_pages` stop with
        # a next link still pending is an INCOMPLETE enumeration, and a caller asserting
        # scope completeness (req-github-core-org-scope-3) must not claim it.
        self.last_walk_complete = not url
        return items

    def _build_url(self, path: str, params: dict[str, str] | None) -> str:
        if path.startswith("http"):
            return path
        parsed = urlparse(self._api_base_url + path)
        query = urlencode(params or {})
        return urlunparse(parsed._replace(query=query))

    def _request(self, url: str) -> Any:
        """One logical GET through the seam (spec-github-core-reliability): retry, budget, records.

        Call sites keep their contract — decoded JSON on success, `GithubAPIError` on a refused
        fetch — so their three-state handling is unchanged; what changed is that a transient
        failure is retried within the run's budget before it ever reaches them, and every retry or
        degradation is a run record.
        """
        path = url[len(self._api_base_url):] if url.startswith(self._api_base_url) else url
        ctx = CallContext(
            surface=endpoint_template(path),
            scope=scope_from_path(path),
            endpoint=endpoint_template(path),
            layer="rest",
            credential_kind=self._credential_kind or self._seam.credential_kind,
        )
        seam = self._seam

        def transport(params: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
            return self._request_once(url, timeout=params.get("read_timeout") or 30.0)

        gather = call(transport, ctx, budget=seam.budget, recorder=seam.recorder, policy=self._policy)
        if not gather.ok:
            # GitHub's own explanatory body when there was one (the contract callers hold), else
            # the seam's reason (an exhausted transient has no single answer to quote).
            body = gather.last_failure_body.decode("utf-8", errors="replace") or gather.failure_reason
            raise GithubAPIError(status=gather.status or 0, url=url, body=body)
        # Pagination state is set HERE, from the attempt that won — never inside the transport,
        # which an abandoned attempt may still be running.
        link = next((v for k, v in gather.headers.items() if k.lower() == "link"), "")
        self._next_link = self._parse_next_link(link)
        return gather.parsed if gather.body else {}

    def _request_once(self, url: str, *, timeout: float = 30.0) -> tuple[int, dict[str, str], bytes]:
        """ONE attempt: ``(status, headers, body)`` for 2xx; ``HttpFailure`` for any other status;
        transport exceptions propagate for the seam to classify. Mutates nothing on the client."""
        req = Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            # nosec B310 — `url` is built from the credential envelope's https api_base_url (secret.py).
            with urlopen(req, timeout=timeout) as resp:  # noqa: S310 # nosec B310
                body = resp.read()
                headers = dict(resp.headers.items())
                return resp.status, headers, body
        except HTTPError as exc:
            body = exc.read() if exc.fp else b""
            raise HttpFailure(status=exc.code, headers=dict((exc.headers or {}).items()), body=body) from exc

    @staticmethod
    def _parse_next_link(link_header: str) -> str | None:
        if not link_header:
            return None
        m = _LINK_RE.search(link_header)
        return m.group(1) if m else None
