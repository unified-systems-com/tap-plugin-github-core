"""The seam: every call this plugin makes to GitHub goes through here, and nowhere else retries.

Spec: specs/spec-github-core-reliability.md — taxonomy (one classifier), retry (bounded, full
jitter, GitHub's headers win when they fit the deadline, the read is covered), budget (per-run
retries and wall clock; every sleep and read capped by what remains), adaptive GraphQL pages,
rate limits, observability (structured records under the redaction rule). This module is pure
policy: it knows nothing about urllib or githubkit. A transport is a callable that performs one
attempt and either returns ``(status, headers, body_bytes)`` or raises; the seam classifies what
happened and decides.

Design sources: GitHub's rate-limit and GraphQL guidance; Octokit's retry / throttling split; AWS
SDK standard retry mode (full jitter, retry budget); one-layer retry (SRE practice).
"""

from __future__ import annotations

import http.client
import json
import logging
import random
import re
import socket
import threading
import time
import urllib.error
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from .gather import FailureClass, Gather, RateLimitSnapshot

logger = logging.getLogger(__name__)

# --- policy constants (req-github-core-reliability-retry / -budget / -adaptive-pages) -----------
ATTEMPTS_PER_CALL = 4
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0
RETRY_BUDGET_PER_RUN = 30
WALL_CLOCK_PER_RUN = timedelta(minutes=20)
CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 60.0
CALL_CEILING_SECONDS = 120.0
SECONDARY_RATE_LIMIT_MIN_WAIT = 60.0
SECONDARY_RATE_LIMIT_MAX_WAITS_PER_RUN = 2
RATE_LIMIT_LOW_FRACTION = 0.05
GRAPHQL_PAGE_SIZES = (100, 50, 25, 10)  # halving ladder; the last is the floor
BODY_MESSAGE_MAX_CHARS = 200

_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
_TERMINAL_STATUSES = frozenset({400, 401, 404, 410, 422, 451})
_GRAPHQL_TRANSIENT_TYPES = frozenset({"RATE_LIMITED"})
_GRAPHQL_PAGE_TYPES = frozenset({"MAX_NODE_LIMIT_EXCEEDED", "EXCESSIVE_PAGINATION"})
_GRAPHQL_PATH_TYPES = frozenset({"FORBIDDEN", "NOT_FOUND", "INSUFFICIENT_SCOPES", "UNPROCESSABLE"})
_GRAPHQL_TIMEOUT_TEXT = "couldn't respond to your request in time"


class Recorder(Protocol):
    """Where the seam writes its structured records (the collector's record_* under the hood)."""

    def info(self, code: str, message: str, data: dict[str, Any]) -> None: ...

    def warn(self, code: str, message: str, data: dict[str, Any]) -> None: ...


class NullRecorder:
    """A recorder for clients used outside a collection run (self-test probes, tests)."""

    def info(self, code: str, message: str, data: dict[str, Any]) -> None:
        logger.debug("[3c1a] %s: %s", code, message)

    def warn(self, code: str, message: str, data: dict[str, Any]) -> None:
        logger.info("[b47e] %s: %s", code, message)


_OWNER_REPO = re.compile(r"^/repos/[^/]+/[^/]+")
_ORG = re.compile(r"^/orgs/[^/]+")
_USER = re.compile(r"^/users/[^/]+")
_SHA = re.compile(r"/[0-9a-f]{40}(?=/|$)")
_NUM = re.compile(r"/\d+(?=/|$)")
_NAMED_TAIL = re.compile(r"^(/repos/\{owner\}/\{repo\}/(?:actions/(?:secrets|variables|workflows|environments)|environments|rulesets|git/ref|git/refs|contents|releases/tags|branches|commits/\{sha\}/check-runs)|/orgs/\{org\}/actions/(?:secrets|variables|runner-groups))/.+$")


def endpoint_template(path: str) -> str:
    """The redacted identity of a request: ``/repos/{owner}/{repo}/actions/runs/{id}/jobs``.

    Query strings are dropped; owner/repo/org/user segments, numeric ids and 40-hex SHAs are
    replaced by placeholders; the name segment after a few well-known collections (secrets,
    workflows, environments, tags, refs, branches) is replaced too, so a secret or branch NAME
    never lands in a run record (req-github-core-reliability-observability).
    """
    path = path.split("?", 1)[0]
    if path.startswith("http"):
        path = "/" + path.split("/", 3)[3] if path.count("/") >= 3 else path
    out = _OWNER_REPO.sub("/repos/{owner}/{repo}", path)
    out = _ORG.sub("/orgs/{org}", out)
    out = _USER.sub("/users/{user}", out)
    out = _SHA.sub("/{sha}", out)
    out = _NUM.sub("/{id}", out)
    out = _NAMED_TAIL.sub(r"\1/{name}", out)  # the whole tail: names and paths may contain '/'
    return out


def scope_from_path(path: str) -> str:
    """``owner/repo`` for a repository path, ``owner`` for an org/user path, else ``account``."""
    path = path.split("?", 1)[0]
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "repos":
        return f"{parts[1]}/{parts[2]}"
    if len(parts) >= 2 and parts[0] in ("orgs", "users"):
        return parts[1]
    return "account"


# --- classification (req-github-core-reliability-taxonomy) --------------------------------------


@dataclass(frozen=True)
class Classification:
    klass: FailureClass
    reason: str
    retry_after: float | None = None  # seconds GitHub asked us to wait, when it said
    rate_limited: str | None = None  # "primary" | "secondary" when the failure is a rate limit
    reduce_page: bool = False  # GraphQL timeout-class: halve the page before retrying


class TransportResponse(Protocol):
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass
class HttpFailure(Exception):
    """A non-success HTTP status, as the transport reports it to the seam."""

    status: int
    headers: Mapping[str, str]
    body: bytes

    def __str__(self) -> str:
        return f"HTTP {self.status}: {message_from_body(self.body)}"


def message_from_body(body: bytes | str, limit: int = BODY_MESSAGE_MAX_CHARS) -> str:
    """GitHub's ``message`` field, truncated — or a byte count. Never the raw body (redaction)."""
    raw = body if isinstance(body, bytes) else body.encode("utf-8", errors="replace")
    if not raw:
        return "<empty body>"
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return f"<non-JSON body, {len(raw)} bytes>"
    if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
        return parsed["message"][:limit]
    return f"<JSON body without message, {len(raw)} bytes>"


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def _retry_after_seconds(headers: Mapping[str, str], now: datetime) -> float | None:
    value = _header(headers, "Retry-After")
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(value) - now).total_seconds())
            except (TypeError, ValueError):
                return None
    reset = _header(headers, "x-ratelimit-reset")
    remaining = _header(headers, "x-ratelimit-remaining")
    if reset and remaining == "0":
        try:
            return max(0.0, float(int(reset)) - now.timestamp())
        except ValueError:
            return None
    return None


def rate_limit_snapshot(headers: Mapping[str, str], cost: int | None = None) -> RateLimitSnapshot:
    def _int(name: str) -> int | None:
        v = _header(headers, name)
        try:
            return int(v) if v is not None else None
        except ValueError:
            return None

    reset = _int("x-ratelimit-reset")
    return RateLimitSnapshot(
        limit=_int("x-ratelimit-limit"),
        remaining=_int("x-ratelimit-remaining"),
        reset_at=datetime.fromtimestamp(reset, UTC) if reset else None,
        cost=cost,
    )


_LOCAL_OSERRORS = (PermissionError, FileNotFoundError, IsADirectoryError, NotADirectoryError, FileExistsError)


def classify(exc: BaseException, *, now: datetime | None = None, attempt: int = 1) -> Classification:
    """Map anything a transport can raise to transient / terminal / partial (rule 5 closes the table).

    ``attempt`` is the 1-based attempt that raised: an UNLISTED transport exception is transient on
    the first attempt and terminal after — one retry proves it was not a blip. Local filesystem
    errors are not transport at all and fail the run closed.
    """
    now = now or datetime.now(UTC)
    if isinstance(exc, HttpFailure):
        return _classify_http(exc.status, exc.headers, exc.body, now)
    if isinstance(exc, GraphQLErrors):
        return _classify_graphql(exc)
    if isinstance(exc, MalformedBody):
        return Classification("transient", f"truncated: {exc}")
    if isinstance(exc, _CallCeilingExceeded):
        return Classification("transient", f"timeout: {exc}")
    if isinstance(exc, (http.client.IncompleteRead, http.client.RemoteDisconnected)):
        return Classification("transient", f"truncated: {type(exc).__name__}")
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return Classification("transient", "timeout")
    if isinstance(exc, (ConnectionResetError, ConnectionRefusedError, ConnectionAbortedError, BrokenPipeError)):
        return Classification("transient", f"connection: {type(exc).__name__}")
    if isinstance(exc, urllib.error.URLError):
        return Classification("transient", f"transport: {exc.reason}")
    if isinstance(exc, http.client.HTTPException):
        return Classification("transient", f"http.client: {type(exc).__name__}")
    if isinstance(exc, _LOCAL_OSERRORS):
        raise exc  # not transport: a local error fails the run closed (rule 5)
    if isinstance(exc, OSError):
        # Rule 5: an unlisted transport exception is transient ONCE, then terminal; named so the
        # table can grow.
        if attempt <= 1:
            return Classification("transient", f"unlisted transport: {type(exc).__name__}")
        return Classification("terminal", f"unlisted transport after one retry: {type(exc).__name__}")
    raise exc  # rule 5: an unanticipated exception fails the run closed, named by the run record


def _classify_http(status: int, headers: Mapping[str, str], body: bytes, now: datetime) -> Classification:
    retry_after = _retry_after_seconds(headers, now)
    remaining = _header(headers, "x-ratelimit-remaining")
    text = message_from_body(body).lower()
    if status in (403, 429):
        if _header(headers, "Retry-After") is not None or "secondary rate limit" in text or "abuse" in text:
            return Classification(
                "transient",
                "secondary rate limit",
                retry_after=retry_after if retry_after is not None else SECONDARY_RATE_LIMIT_MIN_WAIT,
                rate_limited="secondary",
            )
        if remaining == "0" or "rate limit exceeded" in text:
            return Classification("transient", "primary rate limit", retry_after=retry_after, rate_limited="primary")
        if status == 429:
            return Classification("transient", "429 without headers", retry_after=retry_after)
        return Classification("terminal", "403 permission refused")
    if status == 404 and not body.strip():
        return Classification("transient", "empty-body 404 (GitHub quirk)")
    if status in _TRANSIENT_STATUSES:
        return Classification("transient", f"HTTP {status}", retry_after=retry_after, reduce_page=(status == 504))
    if status in _TERMINAL_STATUSES:
        return Classification("terminal", f"HTTP {status}")
    if 200 <= status < 300:
        return Classification("partial", f"HTTP {status} handed to classify")
    return Classification("terminal", f"unlisted HTTP {status}")  # rule 5 default


@dataclass
class GraphQLErrors(Exception):
    """A GraphQL response carrying ``errors[]`` — with or without ``data``.

    When ``data`` is present the transport passes the raw body and headers along so the seam
    can land the partial answer (bytes, digest, degraded paths) instead of dropping it.
    """

    errors: list[dict[str, Any]]
    has_data: bool
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    status: int = 200

    def types(self) -> set[str]:
        return {str(e.get("type") or "") for e in self.errors}

    def messages(self) -> str:
        return " | ".join(str(e.get("message") or "") for e in self.errors).lower()


def _classify_graphql(exc: GraphQLErrors) -> Classification:
    types = exc.types()
    text = exc.messages()
    if types & _GRAPHQL_TRANSIENT_TYPES:
        return Classification("transient", "GraphQL RATE_LIMITED", rate_limited="primary")
    if _GRAPHQL_TIMEOUT_TEXT in text or types & _GRAPHQL_PAGE_TYPES:
        return Classification("transient", "GraphQL could not respond in time", reduce_page=True)
    if exc.has_data:
        return Classification("partial", f"GraphQL errors beside data: {sorted(types) or ['<untyped>']}")
    if types & _GRAPHQL_PATH_TYPES:
        return Classification("terminal", f"GraphQL {sorted(types)} with no data")
    return Classification("terminal", f"GraphQL unlisted {sorted(types) or ['<untyped>']} with no data")  # rule 5


# --- budget and retry (req-github-core-reliability-budget / -retry) -----------------------------


@dataclass
class RunBudget:
    """The two ceilings a run has, and the enforceable deadline they imply."""

    retries: int = RETRY_BUDGET_PER_RUN
    wall_clock: timedelta = WALL_CLOCK_PER_RUN
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    retries_used: int = 0
    secondary_waits: int = 0
    rate_limit_low_recorded: bool = False
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    @property
    def deadline(self) -> datetime:
        return self.started_at + self.wall_clock

    def remaining_seconds(self) -> float:
        return max(0.0, (self.deadline - self.clock()).total_seconds())

    @property
    def retries_exhausted(self) -> bool:
        return self.retries_used >= self.retries

    def take_retry(self) -> bool:
        if self.retries_exhausted:
            return False
        self.retries_used += 1
        return True

    def read_timeout(self) -> float:
        return max(0.0, min(READ_TIMEOUT_SECONDS, self.remaining_seconds()))

    def call_ceiling(self) -> float:
        return max(0.0, min(CALL_CEILING_SECONDS, self.remaining_seconds()))

    def report(self) -> dict[str, Any]:
        return {
            "attempts_per_call": ATTEMPTS_PER_CALL,
            "retry_budget": self.retries,
            "wall_clock_seconds": int(self.wall_clock.total_seconds()),
            "retries_used": self.retries_used,
            "seconds_used": int((self.clock() - self.started_at).total_seconds()),
        }


@dataclass
class Seam:
    """One per run: the budget, the recorder and the credential kind every client shares."""

    budget: RunBudget
    recorder: Recorder
    credential_kind: str = ""

    @classmethod
    def standalone(cls) -> Seam:
        return cls(budget=RunBudget(), recorder=NullRecorder())


@dataclass
class RetryPolicy:
    attempts: int = ATTEMPTS_PER_CALL
    base: float = BACKOFF_BASE_SECONDS
    cap: float = BACKOFF_CAP_SECONDS
    rng: Callable[[float, float], float] = random.uniform

    def sleep_for(self, attempt: int, classification: Classification, budget: RunBudget) -> float | None:
        """Seconds to sleep before retry ``attempt`` (1-based), or None when the run must not wait.

        GitHub's header wins when present — exactly, no jitter — but only if it fits the remaining
        wall clock; otherwise None (degrade with ``rate_limited_until``). Computed backoff is full
        jitter over [0, min(cap, base·2^(attempt-1))], likewise capped by what remains.
        """
        remaining = budget.remaining_seconds()
        if classification.retry_after is not None:
            wait = classification.retry_after
            return wait if wait <= remaining else None
        ceiling = min(self.cap, self.base * (2 ** (attempt - 1)))
        wait = self.rng(0.0, ceiling)
        return wait if wait <= remaining else None


# --- the call (the one place that loops) --------------------------------------------------------

Transport = Callable[[dict[str, Any]], tuple[int, Mapping[str, str], bytes]]


@dataclass
class CallContext:
    surface: str
    scope: str
    endpoint: str  # template
    layer: str
    credential_kind: str = ""
    graphql: bool = False
    page_size: int | None = None  # GraphQL outermost page; halved on timeout-class failures


def call(
    transport: Transport,
    ctx: CallContext,
    *,
    budget: RunBudget,
    recorder: Recorder,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] | None = None,
    parse: Callable[[bytes], Any] | None = None,
) -> Gather:
    """Perform one logical call with retry, budget, page adaptation and records; return a Gather.

    ``transport(params)`` performs ONE attempt: it receives ``{"page_size": n, "read_timeout": s,
    "call_ceiling": s}`` and returns ``(status, headers, body)`` for any 2xx, or raises
    ``HttpFailure`` / ``GraphQLErrors`` / a transport exception. The seam never sees a URL.

    **A transport must not mutate its client.** An attempt abandoned at the call ceiling keeps
    running on its worker until its socket times out; anything it wrote to shared state (a
    pagination cursor, a cache) would land after the seam has moved on. Everything the caller
    needs from the winning attempt travels back on the Gather (``headers``, ``body``).
    """
    policy = policy or RetryPolicy()
    clock = now or budget.clock
    gather = Gather(surface=ctx.surface, scope=ctx.scope, endpoint=ctx.endpoint, credential_kind=ctx.credential_kind)
    page_size = ctx.page_size
    for attempt in range(1, policy.attempts + 1):
        gather.attempts = attempt
        if budget.remaining_seconds() <= 0:
            return gather.refused("transient", "wall_clock")
        params = {"page_size": page_size, "read_timeout": budget.read_timeout(), "call_ceiling": budget.call_ceiling()}
        try:
            status, headers, body = _under_ceiling(transport, params, params["call_ceiling"])
            if body and _parse_json(body, strict=True) is _MALFORMED:
                raise MalformedBody(len(body))
        except Exception as exc:  # noqa: BLE001 — classify() re-raises what it does not know (rule 5)
            try:
                classification = classify(exc, now=clock(), attempt=attempt)
            except BaseException:
                # Rule 5: fail closed — but say so first, so the run record names the condition.
                recorder.warn(
                    "UNANTICIPATED",
                    f"{ctx.layer}: {type(exc).__name__} on {ctx.endpoint} — not a transport condition; failing the run closed",
                    {"layer": ctx.layer, "endpoint": ctx.endpoint, "exception": type(exc).__name__, "credential_kind": ctx.credential_kind},
                )
                raise
            gather.status = getattr(exc, "status", None)
            if isinstance(exc, (HttpFailure, GraphQLErrors)):
                gather.last_failure_body = exc.body
                gather.last_failure_headers = dict(exc.headers.items()) if exc.headers else {}
            if classification.klass == "terminal":
                recorder.warn(
                    "TERMINAL",
                    f"{ctx.layer}: {classification.reason} on {ctx.endpoint}",
                    {"layer": ctx.layer, "endpoint": ctx.endpoint, "status": gather.status, "reason": classification.reason, "credential_kind": ctx.credential_kind},
                )
                return gather.refused("terminal", classification.reason)
            if classification.klass == "partial":
                # A GraphQL body with data beside errors: land what arrived as PARTIAL — bytes,
                # digest, parsed body — with the degraded paths named; the caller prunes them.
                gql = exc if isinstance(exc, GraphQLErrors) else None
                gather.degraded_paths = [
                    ".".join(str(p) for p in (e.get("path") or [])) or "<root>" for e in (gql.errors if gql else [])
                ]
                if gql is not None and gql.body:
                    gather.body = gql.body
                    gather.parsed = _parse_json(gql.body)
                    gather.status = gql.status
                    gather.rate_limit = rate_limit_snapshot(gql.headers)
                gather.failure = None
                gather.complete = False
                gather.page_size = page_size
                gather.observed_at = clock()
                recorder.info(
                    "PARTIAL",
                    f"{ctx.layer}: {len(gather.degraded_paths)} degraded path(s) pruned on {ctx.endpoint}",
                    {"layer": ctx.layer, "endpoint": ctx.endpoint, "degraded_paths": gather.degraded_paths[:20], "count": len(gather.degraded_paths)},
                )
                return gather
            # transient
            if classification.rate_limited == "secondary":
                if budget.secondary_waits >= SECONDARY_RATE_LIMIT_MAX_WAITS_PER_RUN:
                    return _degrade(gather, recorder, ctx, "budget_exhausted: secondary rate limit waits")
                budget.secondary_waits += 1
            if classification.reduce_page and ctx.graphql and page_size:
                smaller = _next_page_size(page_size)
                if smaller != page_size:
                    recorder.info(
                        "PAGE_SIZE_REDUCED",
                        f"{ctx.layer}: page {page_size} → {smaller} after {classification.reason}",
                        {"layer": ctx.layer, "from": page_size, "to": smaller},
                    )
                    page_size = smaller
            if attempt >= policy.attempts:
                return _degrade(gather, recorder, ctx, f"attempts_exhausted: {classification.reason}")
            if not budget.take_retry():
                return _degrade(gather, recorder, ctx, f"budget_exhausted: {classification.reason}")
            wait = policy.sleep_for(attempt, classification, budget)
            if wait is None:
                until = (clock() + timedelta(seconds=classification.retry_after or 0)).isoformat()
                reason = f"rate_limited_until {until}" if classification.rate_limited else "wall_clock"
                return _degrade(gather, recorder, ctx, reason)
            recorder.info(
                "RETRY",
                f"{ctx.layer}: {classification.reason}; attempt {attempt}/{policy.attempts}, sleeping {wait:.1f}s",
                {
                    "layer": ctx.layer,
                    "endpoint": ctx.endpoint,
                    "class": classification.klass,
                    "reason": classification.reason,
                    "attempt": attempt,
                    "sleep_seconds": round(wait, 2),
                    "status": gather.status,
                    "credential_kind": ctx.credential_kind,
                },
            )
            sleep(wait)
            continue
        # success
        gather.status = status
        gather.body = body
        gather.parsed = parse(body) if parse else _parse_json(body)
        gather.complete = True
        gather.observed_at = clock()
        gather.rate_limit = rate_limit_snapshot(headers)
        gather.contains_signed_urls = b"X-Amz-Signature" in body or b"&sig=" in body
        gather.page_size = page_size
        gather.headers = dict(headers.items()) if headers else {}
        _check_rate_limit_low(gather.rate_limit, recorder, budget)
        return gather
    return _degrade(gather, recorder, ctx, "attempts_exhausted")  # pragma: no cover — loop returns


class MalformedBody(Exception):
    """A 2xx whose body is not JSON — a truncated or proxy-generated answer, never an observation."""

    def __init__(self, size: int) -> None:
        super().__init__(f"malformed 2xx body ({size} bytes)")
        self.size = size


class _CallCeilingExceeded(TimeoutError):
    """The whole attempt — connect, request and read — outlived its ceiling."""


def _under_ceiling(transport: Transport, params: dict[str, Any], ceiling: float) -> tuple[int, Mapping[str, str], bytes]:
    """Run one attempt with an aggregate deadline (req-github-core-reliability-budget).

    Socket timeouts bound inactivity, not the whole request: a trickling body can outlive the
    run. The attempt runs on a **daemon** thread and is abandoned when the ceiling passes. A
    stuck attempt cannot be killed from outside (there is no cancelling a blocked socket read in
    CPython), so the guarantees are exactly these: the CALLER regains control at the ceiling; the
    abandoned attempt mutates nothing (the transport contract); and, being a daemon, it never
    pins process exit. A ceiling of zero or less means the deadline has already passed.
    """
    if ceiling <= 0:
        raise _CallCeilingExceeded("deadline passed before the attempt")
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["result"] = transport(params)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=_run, name="github-call", daemon=True)
    worker.start()
    worker.join(timeout=ceiling)
    if worker.is_alive():
        raise _CallCeilingExceeded(f"call ceiling {ceiling:.0f}s exceeded")
    if "error" in box:
        raise box["error"]
    return box["result"]


def _degrade(gather: Gather, recorder: Recorder, ctx: CallContext, reason: str) -> Gather:
    recorder.warn(
        "LAYER_DEGRADED",
        f"{ctx.layer}: {ctx.surface} for {ctx.scope} not observed this run — {reason}",
        {"layer": ctx.layer, "surface": ctx.surface, "scope": ctx.scope, "reason": reason, "attempts": gather.attempts, "credential_kind": ctx.credential_kind},
    )
    return gather.refused("transient", reason)


def _next_page_size(current: int) -> int:
    for size in GRAPHQL_PAGE_SIZES:
        if size < current:
            return size
    return current


_MALFORMED = object()


def _parse_json(body: bytes, *, strict: bool = False) -> Any:
    """Decode a JSON body; empty is None. Malformed is ``_MALFORMED`` when strict (the caller turns
    it into a transient failure — a 2xx with a broken body is never a complete observation)."""
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _MALFORMED if strict else None


def _check_rate_limit_low(snapshot: RateLimitSnapshot, recorder: Recorder, budget: RunBudget) -> None:
    """Once per run (req-github-core-reliability-ratelimit): say it when remaining drops under 5 %."""
    if budget.rate_limit_low_recorded:
        return
    if snapshot.limit and snapshot.remaining is not None and snapshot.remaining < snapshot.limit * RATE_LIMIT_LOW_FRACTION:
        budget.rate_limit_low_recorded = True
        recorder.warn(
            "RATE_LIMIT_LOW",
            f"rate limit low: {snapshot.remaining} of {snapshot.limit} remaining",
            {"remaining": snapshot.remaining, "limit": snapshot.limit, "reset": snapshot.reset_at.isoformat() if snapshot.reset_at else None},
        )
