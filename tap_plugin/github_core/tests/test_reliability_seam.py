"""The seam's policy, over a scripted transport — no network (spec-github-core-reliability).

Covers the taxonomy (one classifier, closed by defaults, GraphQL types), retry (bounded, jittered,
headers win only when they fit, the read is covered), budget (retries and wall clock), adaptive
GraphQL pages, secondary-rate-limit waits, records under the redaction rule, and the Gather's facts.
"""

from __future__ import annotations

import hashlib
import http.client
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tap_plugin.github_core.collectors.github_collector.github_call import (
    ATTEMPTS_PER_CALL,
    CallContext,
    GraphQLErrors,
    HttpFailure,
    RetryPolicy,
    RunBudget,
    call,
    classify,
    message_from_body,
)

T0 = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Spy:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, str, dict[str, Any]]] = []

    def info(self, code: str, message: str, data: dict[str, Any]) -> None:
        self.records.append(("info", code, message, data))

    def warn(self, code: str, message: str, data: dict[str, Any]) -> None:
        self.records.append(("warn", code, message, data))

    def codes(self) -> list[str]:
        return [r[1] for r in self.records]


def _budget(clock: Clock, **kw: Any) -> RunBudget:
    return RunBudget(started_at=clock.now, clock=clock, **kw)


def _ok(body: dict[str, Any] | bytes, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return 200, headers or {}, raw


def _http(status: int, body: bytes = b'{"message": "nope"}', headers: dict[str, str] | None = None) -> HttpFailure:
    return HttpFailure(status=status, headers=headers or {}, body=body)


def _script(*outcomes: Any):
    """A transport that returns/raises the scripted outcomes in order and records the params."""
    calls: list[dict[str, Any]] = []
    seq = list(outcomes)

    def transport(params: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        calls.append(dict(params))
        outcome = seq.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def _ctx(**kw: Any) -> CallContext:
    base = {"surface": "runs", "scope": "o/r", "endpoint": "/repos/{owner}/{repo}/actions/runs", "layer": "rest"}
    base.update(kw)
    return CallContext(**base)


NO_JITTER = RetryPolicy(rng=lambda lo, hi: hi)
NO_SLEEP: list[float] = []


def _sleep(seconds: float) -> None:
    NO_SLEEP.append(seconds)


# --- taxonomy -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "klass", "reason_part"),
    [
        (_http(500), "transient", "HTTP 500"),
        (_http(502), "transient", "HTTP 502"),
        (_http(503), "transient", "HTTP 503"),
        (_http(504), "transient", "HTTP 504"),
        (_http(429, headers={"Retry-After": "3"}), "transient", "secondary"),
        (_http(403, headers={"Retry-After": "7"}), "transient", "secondary"),
        (_http(403, body=b'{"message": "API rate limit exceeded for x"}', headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"}), "transient", "primary"),
        (_http(403), "terminal", "permission"),
        (_http(404, body=b""), "transient", "empty-body 404"),
        (_http(404, body=b"   "), "transient", "empty-body 404"),
        (_http(404), "terminal", "HTTP 404"),
        (_http(401), "terminal", "HTTP 401"),
        (_http(422), "terminal", "HTTP 422"),
        (_http(451), "terminal", "HTTP 451"),
        (_http(418), "terminal", "unlisted HTTP 418"),
        (http.client.IncompleteRead(b"x" * 10, 5), "transient", "truncated"),
        (http.client.RemoteDisconnected("closed"), "transient", "truncated"),
        (TimeoutError(), "transient", "timeout"),
        (ConnectionResetError(), "transient", "connection"),
        (OSError("weird"), "transient", "unlisted transport"),
        (GraphQLErrors([{"type": "RATE_LIMITED", "message": "x"}], has_data=False), "transient", "RATE_LIMITED"),
        (GraphQLErrors([{"message": "We couldn't respond to your request in time."}], has_data=False), "transient", "respond in time"),
        (GraphQLErrors([{"type": "MAX_NODE_LIMIT_EXCEEDED", "message": "x"}], has_data=False), "transient", "respond in time"),
        (GraphQLErrors([{"type": "FORBIDDEN", "path": ["a", "b"], "message": "x"}], has_data=True), "partial", "beside data"),
        (GraphQLErrors([{"type": "FORBIDDEN", "message": "x"}], has_data=False), "terminal", "FORBIDDEN"),
        (GraphQLErrors([{"type": "SOMETHING_NEW", "message": "x"}], has_data=False), "terminal", "unlisted"),
    ],
)
def test_classifier_table(exc: BaseException, klass: str, reason_part: str) -> None:
    c = classify(exc, now=T0)
    assert c.klass == klass
    assert reason_part.lower() in c.reason.lower()


def test_unanticipated_exception_fails_closed() -> None:
    with pytest.raises(ValueError):
        classify(ValueError("not a transport thing"), now=T0)


def test_local_os_errors_fail_closed_and_unlisted_transport_is_transient_once() -> None:
    for exc in (PermissionError("denied"), FileNotFoundError("gone"), IsADirectoryError("dir")):
        with pytest.raises(type(exc)):
            classify(exc, now=T0)
    assert classify(OSError("weird"), now=T0, attempt=1).klass == "transient"
    assert classify(OSError("weird"), now=T0, attempt=2).klass == "terminal"
    clock, spy = Clock(), Spy()
    t = _script(OSError("weird"), OSError("weird"), _ok({}))
    g = call(t, _ctx(), budget=_budget(clock), recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert g.failure == "terminal" and len(t.calls) == 2 and spy.codes() == ["RETRY", "TERMINAL"]
    with pytest.raises(PermissionError):
        call(_script(PermissionError("denied")), _ctx(), budget=_budget(clock), recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)


def test_retry_after_header_forms() -> None:
    assert classify(_http(429, headers={"Retry-After": "12"}), now=T0).retry_after == 12.0
    http_date = (T0 + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert classify(_http(429, headers={"Retry-After": http_date}), now=T0).retry_after == pytest.approx(90.0, abs=1)
    reset = str(int((T0 + timedelta(seconds=45)).timestamp()))
    c = classify(_http(403, body=b'{"message":"API rate limit exceeded"}', headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}), now=T0)
    assert c.rate_limited == "primary" and c.retry_after == pytest.approx(45.0, abs=1)


# --- redaction ----------------------------------------------------------------------------------


def test_message_from_body_never_returns_raw_body() -> None:
    assert message_from_body(b'{"message": "Not Found", "documentation_url": "https://x"}') == "Not Found"
    assert message_from_body(b"<html>" + b"x" * 5000 + b"</html>") == "<non-JSON body, 5013 bytes>"
    assert message_from_body(b"") == "<empty body>"
    assert message_from_body(json.dumps({"message": "m" * 500}).encode()) == "m" * 200
    assert message_from_body(b'{"token": "ghs_secret"}') == "<JSON body without message, 23 bytes>"


# --- retry --------------------------------------------------------------------------------------


def test_transient_retried_then_succeeds_and_records_each_retry() -> None:
    clock, spy = Clock(), Spy()
    t = _script(_http(500), _http(502), http.client.IncompleteRead(b"", 1), _ok({"a": 1}))
    g = call(t, _ctx(), budget=_budget(clock), recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert g.ok and g.complete and g.parsed == {"a": 1} and g.attempts == 4
    assert spy.codes() == ["RETRY", "RETRY", "RETRY"]
    assert all(r[3]["endpoint"] == "/repos/{owner}/{repo}/actions/runs" for r in spy.records)
    assert "url" not in spy.records[0][3]


def test_four_failures_degrade_the_surface_not_the_run() -> None:
    clock, spy = Clock(), Spy()
    t = _script(*[_http(503)] * ATTEMPTS_PER_CALL)
    g = call(t, _ctx(), budget=_budget(clock), recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert not g.ok and g.failure == "transient" and not g.complete and g.body == b""
    assert spy.codes()[-1] == "LAYER_DEGRADED"
    assert spy.records[-1][3]["reason"].startswith("attempts_exhausted")
    assert len(t.calls) == ATTEMPTS_PER_CALL


def test_terminal_is_one_request_and_a_refused_gather() -> None:
    clock, spy = Clock(), Spy()
    for exc in (_http(401), _http(404), _http(422), _http(403)):
        t = _script(exc)
        g = call(t, _ctx(), budget=_budget(clock), recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
        assert g.failure == "terminal" and len(t.calls) == 1
    assert spy.codes() == ["TERMINAL"] * 4
    assert "nope" not in json.dumps(spy.records)  # body text is not copied wholesale
    assert all(r[3]["status"] in (401, 404, 422, 403) for r in spy.records)


def test_jitter_is_a_distribution_not_a_constant() -> None:
    import random

    rng = random.Random(7)
    policy = RetryPolicy(rng=rng.uniform)
    clock = Clock()
    sleeps = {policy.sleep_for(3, classify(_http(500), now=T0), _budget(clock)) for _ in range(200)}
    assert len(sleeps) > 50
    assert all(0.0 <= s <= 4.0 for s in sleeps)  # attempt 3 → ceiling min(30, 1·2²) = 4


def test_header_wins_exactly_when_it_fits_and_degrades_when_it_does_not() -> None:
    clock, spy = Clock(), Spy()
    t = _script(_http(429, headers={"Retry-After": "7"}), _ok({}))
    g = call(t, _ctx(), budget=_budget(clock), recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert g.ok and NO_SLEEP[-1] == 7.0
    clock2, spy2 = Clock(), Spy()
    t2 = _script(_http(429, headers={"Retry-After": "1800"}))
    g2 = call(t2, _ctx(), budget=_budget(clock2, wall_clock=timedelta(minutes=5)), recorder=spy2, policy=NO_JITTER, sleep=_sleep, now=clock2)
    assert not g2.ok and spy2.codes() == ["LAYER_DEGRADED"]
    assert spy2.records[-1][3]["reason"].startswith("rate_limited_until")
    assert len(t2.calls) == 1


def test_wall_clock_stops_before_a_sleep_that_would_cross_it() -> None:
    clock, spy = Clock(), Spy()
    budget = _budget(clock, wall_clock=timedelta(seconds=10))
    clock.advance(9.5)
    t = _script(_http(500), _ok({}))
    g = call(t, _ctx(), budget=budget, recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert not g.ok and spy.records[-1][3]["reason"] == "wall_clock"
    clock.advance(1)
    t2 = _script(_ok({}))
    g2 = call(t2, _ctx(), budget=budget, recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert not g2.ok and len(t2.calls) == 0  # no request started past the deadline


def test_read_timeout_and_call_ceiling_shrink_with_the_deadline() -> None:
    clock = Clock()
    budget = _budget(clock, wall_clock=timedelta(seconds=100))
    assert budget.read_timeout() == 60.0 and budget.call_ceiling() == 100.0
    clock.advance(70)
    assert budget.read_timeout() == 30.0 and budget.call_ceiling() == 30.0


def test_retry_budget_exhaustion_degrades_without_sleeping() -> None:
    clock, spy = Clock(), Spy()
    budget = _budget(clock, retries=2)
    t = _script(_http(500), _http(500), _http(500), _ok({}))
    g = call(t, _ctx(), budget=budget, recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert not g.ok and spy.records[-1][3]["reason"].startswith("budget_exhausted") and len(t.calls) == 3


def test_secondary_rate_limit_waited_twice_then_degrades() -> None:
    clock, spy = Clock(), Spy()
    budget = _budget(clock)
    sec = lambda: _http(403, headers={"Retry-After": "1"})  # noqa: E731
    t = _script(sec(), sec(), _ok({}))
    assert call(t, _ctx(), budget=budget, recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock).ok
    t2 = _script(sec(), _ok({}))
    g = call(t2, _ctx(), budget=budget, recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert not g.ok and "secondary" in spy.records[-1][3]["reason"]


# --- adaptive GraphQL pages ---------------------------------------------------------------------


def test_graphql_page_halves_on_timeout_class_and_is_recorded() -> None:
    clock, spy = Clock(), Spy()
    t = _script(_http(504, body=b'{"message":"x"}'), GraphQLErrors([{"message": "We couldn't respond to your request in time."}], has_data=False), _ok({"data": {}}))
    g = call(t, _ctx(graphql=True, page_size=100, layer="graphql", endpoint="graphql:config_layer"), budget=_budget(clock), recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert g.ok
    assert [c["page_size"] for c in t.calls] == [100, 50, 25]
    reductions = [r for r in spy.records if r[1] == "PAGE_SIZE_REDUCED"]
    assert [(r[3]["from"], r[3]["to"]) for r in reductions] == [(100, 50), (50, 25)]


def test_graphql_page_floor_holds() -> None:
    clock, spy = Clock(), Spy()
    t = _script(*[_http(504)] * ATTEMPTS_PER_CALL)
    call(t, _ctx(graphql=True, page_size=10, layer="graphql"), budget=_budget(clock), recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert all(c["page_size"] == 10 for c in t.calls) and "PAGE_SIZE_REDUCED" not in spy.codes()


# --- partial and the gather's facts -------------------------------------------------------------


def test_partial_graphql_lands_the_bytes_names_the_paths_and_is_not_complete() -> None:
    clock, spy = Clock(), Spy()
    raw = json.dumps({"data": {"repositoryOwner": {"repositories": {"nodes": [{}, {}, {}, {"rulesets": None}]}}}, "errors": [{"type": "FORBIDDEN", "path": ["repositoryOwner", "repositories", "nodes", 3, "rulesets"], "message": "x"}]}).encode()
    t = _script(GraphQLErrors([{"type": "FORBIDDEN", "path": ["repositoryOwner", "repositories", "nodes", 3, "rulesets"], "message": "x"}], has_data=True, body=raw, headers={"x-ratelimit-remaining": "4000", "x-ratelimit-limit": "5000"}))
    g = call(t, _ctx(graphql=True, layer="graphql", page_size=100), budget=_budget(clock), recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert g.ok and not g.complete
    assert g.degraded_paths == ["repositoryOwner.repositories.nodes.3.rulesets"]
    assert g.body == raw and g.digest == hashlib.sha256(raw).hexdigest() and g.parsed["data"] is not None
    assert g.rate_limit.remaining == 4000 and g.page_size == 100
    assert spy.codes() == ["PARTIAL"] and spy.records[0][3]["count"] == 1


def test_graphql_adapter_turns_data_beside_errors_into_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real `_post_layer` transport, not an injected exception (review finding)."""
    import io
    import urllib.request

    from tap_plugin.github_core.collectors.github_collector import graphql_client as mod
    from tap_plugin.github_core.collectors.github_collector.github_call import RunBudget, Seam

    raw = json.dumps({"data": {"rateLimit": {"cost": 1}, "repositoryOwner": None}, "errors": [{"type": "NOT_FOUND", "path": ["repositoryOwner"], "message": "x"}]}).encode()

    class _Resp(io.BytesIO):
        status = 200
        headers = {"x-ratelimit-remaining": "4999", "x-ratelimit-limit": "5000"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp(raw))
    spy = Spy()
    client = mod.GithubGraphQLClient(token="t", seam=Seam(budget=RunBudget(), recorder=spy))
    body = client._post_layer("config_layer", mod.config_query, {"login": "o", "cursor": None}, page_size=100)
    assert body["errors"][0]["type"] == "NOT_FOUND" and body["data"]["rateLimit"]["cost"] == 1
    assert spy.codes() == ["PARTIAL"] and spy.records[0][3]["degraded_paths"] == ["repositoryOwner"]


def test_malformed_2xx_body_is_transient_never_complete() -> None:
    clock, spy = Clock(), Spy()
    t = _script((200, {}, b'{"workflow_runs": [{"id": 1'), _ok({"workflow_runs": []}))
    g = call(t, _ctx(), budget=_budget(clock), recorder=spy, policy=NO_JITTER, sleep=_sleep, now=clock)
    assert g.ok and g.parsed == {"workflow_runs": []} and len(t.calls) == 2
    assert spy.codes() == ["RETRY"] and "truncated" in spy.records[0][3]["reason"]


def test_call_ceiling_bounds_a_trickling_attempt() -> None:
    import time as _time

    spy = Spy()
    budget = RunBudget(wall_clock=timedelta(seconds=0.3))  # real clock: ceiling = remaining ≈ 0.3 s

    def slow(params: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        _time.sleep(1.0)
        return 200, {}, b"{}"

    started = _time.monotonic()
    g = call(slow, _ctx(), budget=budget, recorder=spy, policy=NO_JITTER, sleep=_sleep)
    assert not g.ok and _time.monotonic() - started < 0.9
    assert spy.codes()[-1] == "LAYER_DEGRADED"


def test_endpoint_template_redacts_whole_tails_and_ids() -> None:
    from tap_plugin.github_core.collectors.github_collector.github_call import endpoint_template, scope_from_path

    assert endpoint_template("/repos/acme/r/contents/private/token.txt?ref=main") == "/repos/{owner}/{repo}/contents/{name}"
    assert endpoint_template("/repos/acme/r/git/ref/heads/feature/x") == "/repos/{owner}/{repo}/git/ref/{name}"
    assert endpoint_template("/repos/acme/r/actions/secrets/DEPLOY_KEY") == "/repos/{owner}/{repo}/actions/secrets/{name}"
    assert endpoint_template("/repos/acme/r/actions/runs/1234/jobs?per_page=100") == "/repos/{owner}/{repo}/actions/runs/{id}/jobs"
    assert endpoint_template("/repos/acme/r/commits/" + "a" * 40 + "/check-runs") == "/repos/{owner}/{repo}/commits/{sha}/check-runs"
    assert endpoint_template("/orgs/acme/actions/secrets/TOKEN") == "/orgs/{org}/actions/secrets/{name}"
    assert endpoint_template("https://api.github.com/repos/acme/r/actions/runs") == "/repos/{owner}/{repo}/actions/runs"
    assert scope_from_path("/repos/acme/r/actions/runs") == "acme/r" and scope_from_path("/orgs/acme/x") == "acme" and scope_from_path("/rate_limit") == "account"


def test_pat_bound_client_records_its_own_credential_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    from tap_plugin.github_core.collectors.github_collector import api_client as mod
    from tap_plugin.github_core.collectors.github_collector.github_call import RunBudget, Seam

    spy = Spy()
    seam = Seam(budget=RunBudget(), recorder=spy, credential_kind="app")
    pat = mod.GithubClient(token="t", seam=seam, credential_kind="pat")
    monkeypatch.setattr(pat, "_request_once", lambda url, timeout=30.0: (_ for _ in ()).throw(_http(401)))
    with pytest.raises(mod.GithubAPIError):
        pat.get("/repos/acme/r/rulesets/7")
    assert spy.codes() == ["TERMINAL"] and spy.records[0][3]["credential_kind"] == "pat"
    assert spy.records[0][3]["endpoint"] == "/repos/{owner}/{repo}/rulesets/{name}"


def test_gather_digest_is_sha256_of_the_exact_bytes_and_flags_signed_urls() -> None:
    clock, spy = Clock(), Spy()
    body = b'{"archive_download_url": "https://x/?X-Amz-Signature=abc"}'
    headers = {"x-ratelimit-limit": "5000", "x-ratelimit-remaining": "4990", "x-ratelimit-reset": str(int(T0.timestamp()) + 100)}
    g = call(_script((200, headers, body)), _ctx(credential_kind="app"), budget=_budget(clock), recorder=spy, now=clock)
    assert g.digest == hashlib.sha256(body).hexdigest()
    assert g.contains_signed_urls and g.credential_kind == "app" and g.observed_at == T0
    assert g.rate_limit.remaining == 4990 and g.rate_limit.limit == 5000 and g.rate_limit.reset_at is not None
    assert spy.codes() == []


def test_rate_limit_low_recorded_once_per_run() -> None:
    clock, spy = Clock(), Spy()
    budget = _budget(clock)
    low = {"x-ratelimit-limit": "5000", "x-ratelimit-remaining": "12"}
    for _ in range(3):
        call(_script((200, low, b"{}")), _ctx(), budget=budget, recorder=spy, now=clock)
    assert spy.codes() == ["RATE_LIMIT_LOW"]
    assert spy.records[0][3]["remaining"] == 12


def test_budget_report_names_the_ceilings() -> None:
    clock = Clock()
    b = _budget(clock)
    b.take_retry()
    clock.advance(42)
    r = b.report()
    assert r == {"attempts_per_call": 4, "retry_budget": 30, "wall_clock_seconds": 1200, "retries_used": 1, "seconds_used": 42}
