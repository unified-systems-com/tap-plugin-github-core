"""Bypass events: who actually went around a gate (req-github-core-rule-suites).

Fixtures are **captured from the live API**, not hand-authored — `tests/fixtures/rule_suites.json`
holds real responses taken with a read-only App installation token on 2026-08-28. The point of a
fixture taken off the wire is that it is not what we imagined the shape to be; hand-writing one
would test our idea of GitHub rather than GitHub.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import tap_plugin.github_core.models as github  # noqa: F401 — trigger model registration
from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector
from tap_plugin.github_core.collectors.github_collector.identity import (
    account_id,
    rule_suite_id,
    ruleset_id,
)

from tap_grid.registry import get_model_class

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "rule_suites.json").read_text())


class _FakeClient:
    """Replays the captured responses, and records what was asked for.

    `calls` is asserted against, because HOW we query this endpoint is itself a requirement:
    omitting `time_period` silently means `day`.
    """

    def __init__(self, *, list_fails: int | None = None, detail_fails: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self._list_fails = list_fails
        self._detail_fails = detail_fails

    def get_paginated(self, path: str, params: dict[str, str] | None = None, **_: Any) -> list[dict]:
        self.calls.append((path, dict(params or {})))
        if self._list_fails is not None:
            from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError

            raise GithubAPIError(status=self._list_fails, url=path, body="Resource not accessible")
        return list(_FIXTURE["list_bypass"])

    def get(self, path: str, params: dict[str, str] | None = None, **_: Any) -> dict:
        self.calls.append((path, dict(params or {})))
        if self._detail_fails:
            from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError

            raise GithubAPIError(status=403, url=path, body="nope")
        return dict(_FIXTURE["detail"])


def _collect(client: _FakeClient, ref_uuids: dict[str, Any] | None = None):
    # `__new__` rather than `__init__`: CollectorBase wants a runtime config we do not need to
    # exercise a pure emitter. Matches the pattern in test_self_tier_vocabulary.
    collector = GithubCollector.__new__(GithubCollector)
    collector._emitted_actor_logins = set()
    nodes: list[dict] = []
    edges: list[dict] = []
    warns: list[tuple] = []
    infos: list[tuple] = []
    collector.record_warn = lambda *a, **k: warns.append((a, k))  # type: ignore[method-assign]
    collector.record_info = lambda *a, **k: infos.append((a, k))  # type: ignore[method-assign]
    collector._collect_rule_suites(
        client, "unified-systems-com/tap", {"github.platform": "github.com"},
        ref_uuids or {}, nodes, edges,
    )
    return nodes, edges, warns, infos


def _of_type(nodes: list[dict], entity_type: str) -> list[dict]:
    return [n for n in nodes if n["entity"]["entity_type"] == entity_type]


def _edges_of(edges: list[dict], slug: str) -> list[dict]:
    return [e for e in edges if e["edge"]["edge_type"] == slug]


class TestTheWindowIsExplicit:
    @pytest.mark.spec("req-github-core-rule-suites-5")
    def test_time_period_is_always_sent(self) -> None:
        """Measured on the live API: `day` returned 47 suites where `month` returned 100+.

        Omitting the parameter does not fail, it silently narrows — a repository with a month of
        bypasses reads as a quiet one. This is the same absence-as-answer failure the plugin meets
        elsewhere, arriving through a query default rather than a permission.
        """
        client = _FakeClient()
        _collect(client)
        listing = [c for c in client.calls if c[0].endswith("/rule-suites")]
        assert listing, "the rule-suite listing was never requested"
        assert listing[0][1].get("time_period"), "time_period MUST be explicit — the default is `day`"

    @pytest.mark.spec("req-github-core-rule-suites-4")
    def test_only_bypasses_are_requested(self) -> None:
        """A passing suite is a routine push; ~47/day on one repository would swamp the grid."""
        client = _FakeClient()
        _collect(client)
        listing = [c for c in client.calls if c[0].endswith("/rule-suites")][0]
        assert listing[1].get("rule_suite_result") == "bypass"


class TestTheEventLands:
    @pytest.mark.spec("req-github-core-rule-suites-1")
    def test_a_suite_becomes_a_node_keyed_on_githubs_id(self) -> None:
        nodes, _, _, _ = _collect(_FakeClient())
        suites = _of_type(nodes, "github_core__rule_suite")
        assert len(suites) == len(_FIXTURE["list_bypass"])
        first = _FIXTURE["list_bypass"][0]
        assert suites[0]["entity"]["entity_id"] == str(rule_suite_id(first["id"]))
        assert suites[0]["node"]["result"] == "bypass"
        assert suites[0]["node"]["ref"] == first["ref"]

    @pytest.mark.spec("req-github-core-rule-suites-1")
    def test_the_registered_model_accepts_the_captured_shape(self) -> None:
        """The fields the collector emits are ones the model actually declares."""
        model = get_model_class("github_core__rule_suite")
        declared = set(model.FIELD_CRUD_SCHEMA)
        nodes, _, _, _ = _collect(_FakeClient())
        emitted = set(_of_type(nodes, "github_core__rule_suite")[0]["node"])
        assert emitted <= declared, f"emitted fields not on the model: {sorted(emitted - declared)}"


class TestTheActorIsAnAccount:
    @pytest.mark.spec("req-github-core-rule-suites-2")
    def test_actor_lands_as_an_account_not_a_person(self) -> None:
        """GitHub gives a login and an id. Person, bot or machine account is NOT stated, so
        `account_type` is left unobserved rather than guessed at."""
        nodes, edges, _, _ = _collect(_FakeClient())
        accounts = _of_type(nodes, "github_core__github_account")
        assert accounts, "no account node emitted for the pusher"
        login = _FIXTURE["list_bypass"][0]["actor_name"]
        assert accounts[0]["node"]["login"] == login
        assert accounts[0]["node"]["account_type"] == "", "must not claim User/Bot — the API does not say"
        assert accounts[0]["entity"]["entity_id"] == str(account_id(login))
        assert _edges_of(edges, "PUSHED_BY__github_core")

    @pytest.mark.spec("req-github-core-rule-suites-2")
    def test_one_actor_across_many_suites_is_one_node(self) -> None:
        """Three captured bypasses share a pusher; that is one account, not three."""
        nodes, _, _, _ = _collect(_FakeClient())
        accounts = _of_type(nodes, "github_core__github_account")
        assert len({a["entity"]["entity_id"] for a in accounts}) == len(accounts) == 1

    @pytest.mark.spec("req-github-core-rule-suites-2")
    def test_actor_id_rides_the_edge_so_a_rename_is_detectable(self) -> None:
        _, edges, _, _ = _collect(_FakeClient())
        edge = _edges_of(edges, "PUSHED_BY__github_core")[0]
        assert edge["edge"]["properties"]["actor_id"] == _FIXTURE["list_bypass"][0]["actor_id"]


class TestTheBypassedControlIsNamed:
    @pytest.mark.spec("req-github-core-rule-suites-3")
    def test_bypassed_edge_points_at_the_ruleset_that_was_gone_around(self) -> None:
        """Without this join the event is a log line. With it, it names the control."""
        _, edges, _, _ = _collect(_FakeClient())
        bypassed = _edges_of(edges, "HAS_BYPASSED_RULE__github_core")
        assert bypassed, "no HAS_BYPASSED_RULE edge — the suite detail names a ruleset"
        failing = [
            e for e in _FIXTURE["detail"]["rule_evaluations"]
            if e["result"] != "pass" and (e.get("rule_source") or {}).get("type") == "ruleset"
        ]
        assert failing, "fixture no longer carries a failing ruleset evaluation"
        expected = str(ruleset_id("unified-systems-com", failing[0]["rule_source"]["id"]))
        assert any(e["edge"]["to_entity_id"] == expected for e in bypassed)
        assert bypassed[0]["edge"]["properties"]["rule_type"] == failing[0]["rule_type"]

    @pytest.mark.spec("req-github-core-rule-suites-3")
    def test_githubs_own_explanation_is_preserved_verbatim(self) -> None:
        """`Required status check "gate" is expected.` names the specific check. Re-wording it
        would lose the only part that says WHAT was skipped."""
        nodes, _, _, _ = _collect(_FakeClient())
        rules = _of_type(nodes, "github_core__rule_suite")[0]["node"]["bypassed_rules"]
        details = [r.get("details") for r in rules if r.get("details")]
        assert details and any("gate" in d for d in details)

    @pytest.mark.spec("req-github-core-rule-suites-3")
    def test_passing_evaluations_are_not_recorded_as_bypassed(self) -> None:
        """The captured detail has three passing rules and one failing one."""
        nodes, _, _, _ = _collect(_FakeClient())
        rules = _of_type(nodes, "github_core__rule_suite")[0]["node"]["bypassed_rules"]
        assert all(r["result"] != "pass" for r in rules)
        assert len(rules) < len(_FIXTURE["detail"]["rule_evaluations"])


class TestRefusedIsNotEmpty:
    @pytest.mark.spec("req-github-core-rule-suites-6")
    @pytest.mark.parametrize("status", [403, 404])
    def test_a_refusal_warns_and_lands_nothing(self, status: int) -> None:
        """Landing zero bypass events on a refusal would say "nobody bypassed anything" — the
        most reassuring possible reading of a permission failure."""
        nodes, edges, warns, _ = _collect(_FakeClient(list_fails=status))
        assert not nodes and not edges
        assert warns, "a refused surface must be recorded, not silently empty"
        assert "UNREADABLE" in warns[0][0][1]

    @pytest.mark.spec("req-github-core-rule-suites-6")
    def test_a_refused_detail_still_lands_the_event(self) -> None:
        """The list already proves a bypass happened. Losing the rule names is a degraded
        finding, not a reason to drop the finding."""
        nodes, edges, _, _ = _collect(_FakeClient(detail_fails=True))
        suites = _of_type(nodes, "github_core__rule_suite")
        assert len(suites) == len(_FIXTURE["list_bypass"])
        assert suites[0]["node"]["bypassed_rules"] == []
        assert not _edges_of(edges, "HAS_BYPASSED_RULE__github_core")


class TestRefResolution:
    @pytest.mark.spec("req-github-core-rule-suites-1")
    def test_evaluated_on_edge_only_when_the_ref_was_collected(self) -> None:
        """A suite naming a since-deleted branch carries no edge; its `ref` field is the record."""
        _, edges, _, _ = _collect(_FakeClient())
        assert not _edges_of(edges, "EVALUATED_ON__github_core")

        import uuid as _uuid

        ref = _FIXTURE["list_bypass"][0]["ref"]
        known = _uuid.uuid4()
        _, edges2, _, _ = _collect(_FakeClient(), ref_uuids={ref: known})
        on_ref = _edges_of(edges2, "EVALUATED_ON__github_core")
        assert on_ref and on_ref[0]["edge"]["to_entity_id"] == str(known)
        assert on_ref[0]["edge"]["properties"]["after_sha"] == _FIXTURE["list_bypass"][0]["after_sha"]
