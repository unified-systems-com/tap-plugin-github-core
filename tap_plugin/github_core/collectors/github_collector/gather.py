"""The gather: what one call to GitHub returned, with the facts about the fetch.

Spec: specs/spec-github-core-reliability.md, req-github-core-reliability-layers. A gather is the
only thing the network layer hands upward. It carries the exact bytes received, their digest, the
parsed body, and the facts a downstream gate needs to decide whether to admit it: which surface and
scope it covers, whether the fetch completed, which GraphQL paths were degraded and pruned, and
enough request identity to trace the observation back to the request that produced it — the
endpoint TEMPLATE, never a URL with a query string (the redaction rule of
req-github-core-reliability-observability).

A gather is an operation in the dcom sense: it happened once and does not change. Processing code
derives from it and never reaches past it to the network.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

FailureClass = Literal["transient", "terminal", "partial"]


@dataclass(frozen=True)
class RateLimitSnapshot:
    """The rate-limit facts a response carried, typed — never a header dump."""

    limit: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None
    cost: int | None = None  # GraphQL only


@dataclass
class Gather:
    """One response, admitted or not, with its provenance facts.

    ``complete`` is the fact the absence contract reads (req-github-core-reliability-absence): a
    listing may assert that something is not in it only when the walk that produced it ran to the
    end with no degradation and no cap. A gather produced by a failed call has ``body == b""``,
    ``parsed is None``, ``complete is False`` and a ``failure`` naming the class and reason.
    """

    surface: str
    scope: str
    endpoint: str  # template, e.g. "/repos/{owner}/{repo}/actions/runs" or "graphql:config_layer"
    body: bytes = b""
    parsed: Any = None
    status: int | None = None
    complete: bool = False
    degraded_paths: list[str] = field(default_factory=list)
    failure: FailureClass | None = None
    failure_reason: str = ""
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    credential_kind: str = ""
    rate_limit: RateLimitSnapshot = field(default_factory=RateLimitSnapshot)
    attempts: int = 1
    contains_signed_urls: bool = False

    @property
    def digest(self) -> str:
        """sha256 of the exact bytes; the content address an evidence store keys on."""
        return hashlib.sha256(self.body).hexdigest()

    @property
    def ok(self) -> bool:
        return self.failure is None

    def refused(self, klass: FailureClass, reason: str) -> Gather:
        """Return this gather marked as a failed fetch (no body, not complete)."""
        self.body = b""
        self.parsed = None
        self.complete = False
        self.failure = klass
        self.failure_reason = reason
        return self
