"""Run and job timestamps and provenance (github-core#46, github-core#47).

The run's end time is derived from its jobs, never read off `updated_at` (github-core#46).

GitHub's run payload carries no `completed_at`. Its `updated_at` moves on re-run, on
artifact and log events and on check-suite updates — so the previous mapping
(`completed_at = updated_at`) made a run that was re-run days later read as having taken
days. The honest end is `max(job.completed_at)` over the run's collected jobs, and the
node says which source it came from so a reader can tell a measurement from a bound.

The payload's `created_at`, `run_attempt`, `actor` and `triggering_actor` — and the job's
`created_at` — are stored, not dropped (github-core#47): queue time is `run_started_at −
created_at`, and without `run_attempt` a re-run counts twice on any per-run strip.
"""

from __future__ import annotations

from typing import Any

import pytest
from tap_plugin.github_core.collectors.github_collector.collector import (
    COMPLETED_AT_FROM_JOBS,
    COMPLETED_AT_FROM_UPDATED_AT,
    COMPLETED_AT_IN_FLIGHT,
    GithubCollector,
)

_REPO = "unified-systems-com/tap"
_DIMS = {"github.platform": "github.com", "github.surface": "actions", "github.observation": "execution"}


def _run(**overrides: Any) -> dict[str, Any]:
    """A run payload shaped like `GET /repos/{o}/{r}/actions/runs` returns it."""
    payload: dict[str, Any] = {
        "id": 17402911001,
        "run_number": 431,
        "workflow_id": 184402,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "c4c4ee75" * 5,
        "head_branch": "session/sonar-cleanup",
        "run_started_at": "2026-09-01T18:02:11Z",
        "created_at": "2026-09-01T18:01:40Z",
        "run_attempt": 2,
        "actor": {"login": "criticalsec", "id": 1, "node_id": "U_x", "avatar_url": "https://a", "type": "User"},
        "triggering_actor": {"login": "renovate[bot]", "id": 2, "node_id": "U_y", "type": "Bot"},
        # Far later than any job finished: a re-run / artifact event touched the run.
        "updated_at": "2026-09-02T07:45:03Z",
        "html_url": "https://github.com/unified-systems-com/tap/actions/runs/17402911001",
    }
    payload.update(overrides)
    return payload


def _job(job_id: int, *, started_at: str | None, completed_at: str | None, status: str = "completed") -> dict[str, Any]:
    return {
        "id": job_id,
        "name": f"job-{job_id}",
        "status": status,
        "conclusion": "success" if completed_at else None,
        "created_at": "2026-09-01T18:01:41Z",
        "started_at": started_at,
        "completed_at": completed_at,
        "html_url": f"https://github.com/{_REPO}/actions/runs/17402911001/job/{job_id}",
        "runner_id": 1,
        "runner_name": "GitHub Actions 7",
        "labels": ["ubuntu-latest"],
        "steps": [],
    }


_JOBS = [
    _job(1, started_at="2026-09-01T18:02:20Z", completed_at="2026-09-01T18:06:02Z"),
    # The last job to finish; deliberately not the last in list order.
    _job(2, started_at="2026-09-01T18:02:25Z", completed_at="2026-09-01T18:11:47Z"),
    _job(3, started_at="2026-09-01T18:02:21Z", completed_at="2026-09-01T18:04:30Z"),
]


class _FakeClient:
    """Serves one run's `/jobs` listing; `None` means the endpoint degraded (real 404)."""

    def __init__(self, jobs: list[dict[str, Any]] | None) -> None:
        self._jobs = jobs
        self.calls: list[str] = []

    def get_paginated(self, path: str, **_: Any) -> list[dict[str, Any]]:
        from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError

        self.calls.append(path)
        if self._jobs is None:
            raise GithubAPIError(status=404, url=path, body='{"message":"Not Found"}')
        return list(self._jobs)


def _emit(run: dict[str, Any], jobs: list[dict[str, Any]] | None) -> tuple[dict[str, Any], list[dict[str, Any]], list]:
    """Drive the run emitter the way `_collect_repo` does; return (run node, job nodes, warns)."""
    collector = GithubCollector.__new__(GithubCollector)
    warns: list[tuple] = []
    collector.record_warn = lambda *a, **k: warns.append((a, k))  # type: ignore[method-assign]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    collector._emit_run_with_jobs(_FakeClient(jobs), _REPO, run, _DIMS, nodes, edges)
    runs = [n for n in nodes if n["entity"]["entity_type"] == "github_core__github_actions_run"]
    job_nodes = [n for n in nodes if n["entity"]["entity_type"] == "github_core__github_actions_job"]
    assert len(runs) == 1
    return runs[0], job_nodes, warns


class TestTheEndComesFromTheJobs:
    def test_a_late_updated_at_does_not_inflate_the_run(self) -> None:
        """updated_at is 13 hours after the last job finished; the run ends when the job did."""
        run_node, job_nodes, _ = _emit(_run(), _JOBS)
        fields = run_node["node"]
        assert fields["completed_at"] == "2026-09-01T18:11:47Z"
        assert fields["completed_at"] != _run()["updated_at"]
        assert fields["configuration"]["completed_at_source"] == COMPLETED_AT_FROM_JOBS
        assert len(job_nodes) == 3

    def test_the_latest_job_wins_regardless_of_list_order(self) -> None:
        """`max` over parsed timestamps, not the last element and not a string compare."""
        reordered = [_JOBS[1], _JOBS[0], _JOBS[2]]
        run_node, _, _ = _emit(_run(), reordered)
        assert run_node["node"]["completed_at"] == "2026-09-01T18:11:47Z"

    def test_an_unfinished_job_on_a_completed_run_is_not_a_measurement(self) -> None:
        """The listing is eventually consistent: a completed run may still show a job without an
        end. A max over the jobs that did finish would understate the run, so it is not labelled
        `jobs` — the node falls back to the labelled upper bound."""
        jobs = [*_JOBS, _job(4, started_at="2026-09-01T18:02:30Z", completed_at=None, status="in_progress")]
        run_node, _, _ = _emit(_run(), jobs)
        assert run_node["node"]["completed_at"] == _run()["updated_at"]
        assert run_node["node"]["configuration"]["completed_at_source"] == COMPLETED_AT_FROM_UPDATED_AT

    def test_an_unparseable_job_end_degrades_instead_of_aborting(self) -> None:
        """The per-repo boundary catches only GithubAPIError; a ValueError from one odd stamp must
        not fail the whole collection. It reads as "no usable end" and falls back, labelled."""
        jobs = [*_JOBS, _job(4, started_at="2026-09-01T18:02:30Z", completed_at="not a timestamp")]
        run_node, _, _ = _emit(_run(), jobs)
        assert run_node["node"]["completed_at"] == _run()["updated_at"]
        assert run_node["node"]["configuration"]["completed_at_source"] == COMPLETED_AT_FROM_UPDATED_AT


class TestTheOtherTwoStatesAreNamed:
    """Three states, never two: derived, approximated, or absent with the reason on the node."""

    def test_a_run_in_flight_has_no_end_time_yet(self) -> None:
        """Two of three jobs are done, the run is not: a partial max would be a lie."""
        jobs = [_JOBS[0], _JOBS[2], _job(9, started_at="2026-09-01T18:02:30Z", completed_at=None, status="in_progress")]
        run_node, _, _ = _emit(_run(status="in_progress", conclusion=None), jobs)
        assert run_node["node"]["completed_at"] is None
        assert run_node["node"]["configuration"]["completed_at_source"] == COMPLETED_AT_IN_FLIGHT

    def test_unobservable_jobs_fall_back_to_updated_at_and_say_so(self) -> None:
        """The jobs endpoint degraded (real 404): updated_at is the only bound left, labelled."""
        run_node, job_nodes, warns = _emit(_run(), None)
        assert run_node["node"]["completed_at"] == _run()["updated_at"]
        assert run_node["node"]["configuration"]["completed_at_source"] == COMPLETED_AT_FROM_UPDATED_AT
        assert job_nodes == []
        assert any(a[1] == "RUN_JOBS_MISSING" for a, _ in warns)

    def test_a_completed_run_with_no_jobs_falls_back_to_updated_at(self) -> None:
        """A skipped run has an empty listing (observed, not degraded); the bound is still labelled."""
        run_node, _, warns = _emit(_run(conclusion="skipped"), [])
        assert run_node["node"]["completed_at"] == _run()["updated_at"]
        assert run_node["node"]["configuration"]["completed_at_source"] == COMPLETED_AT_FROM_UPDATED_AT
        assert warns == []


@pytest.mark.parametrize(
    ("status", "jobs", "expected_source"),
    [
        ("completed", _JOBS, COMPLETED_AT_FROM_JOBS),
        ("completed", None, COMPLETED_AT_FROM_UPDATED_AT),
        ("queued", None, COMPLETED_AT_IN_FLIGHT),
    ],
)
def test_the_source_label_is_one_of_three(status: str, jobs: list[dict[str, Any]] | None, expected_source: str) -> None:
    _, source = GithubCollector._run_completed_at(_run(status=status), jobs)
    assert source == expected_source


class TestProvenanceIsStoredNotDropped:
    """github-core#47: the payload had these all along; `raw_payload_keys` proved it."""

    def test_run_provenance_lands_on_the_node(self) -> None:
        run_node, job_nodes, _ = _emit(_run(), _JOBS)
        fields = run_node["node"]
        assert fields["created_at"] == "2026-09-01T18:01:40Z"
        assert fields["run_attempt"] == 2
        assert fields["actor_login"] == "criticalsec"
        assert fields["triggering_actor_login"] == "renovate[bot]"
        # Logins, not user objects: nothing from the actor payload but the login survives.
        assert "avatar_url" not in str(fields)
        assert all(j["node"]["created_at"] == "2026-09-01T18:01:41Z" for j in job_nodes)

    def test_an_absent_actor_is_observed_empty_not_null(self) -> None:
        """`""` says the payload named nobody; null would say we did not look."""
        run_node, _, _ = _emit(_run(actor=None, triggering_actor={"id": 3}), _JOBS)
        assert run_node["node"]["actor_login"] == ""
        assert run_node["node"]["triggering_actor_login"] == ""

    def test_a_payload_without_the_keys_yields_null_not_zero(self) -> None:
        """A run with no `created_at` renders as not observed — never as a 0 s queue."""
        bare = _run()
        for key in ("created_at", "run_attempt"):
            bare.pop(key)
        run_node, _, _ = _emit(bare, _JOBS)
        assert run_node["node"]["created_at"] is None
        assert run_node["node"]["run_attempt"] is None


@pytest.mark.django_db
class TestTheFieldsRoundTripThroughTheServiceLayer:
    """The emitted envelope is accepted by `create_node` and reads back typed."""

    def test_run_and_job_round_trip(self) -> None:
        from datetime import UTC, datetime

        from tap_grid.models import Entity
        from tap_grid.registry import get_model_class
        from tap_grid.services import create_node

        run_node, job_nodes, _ = _emit(_run(), _JOBS)
        result = create_node("github_core__github_actions_run", run_node["node"])
        assert result.success, f"create_node failed: {result.errors}"
        run = get_model_class("github_core__github_actions_run").objects.get(
            entity=Entity.objects.get(pk=result.entity_id)
        )
        assert run.created_at == datetime(2026, 9, 1, 18, 1, 40, tzinfo=UTC)
        assert (run.run_started_at - run.created_at).total_seconds() == 31
        assert run.completed_at == datetime(2026, 9, 1, 18, 11, 47, tzinfo=UTC)
        assert run.run_attempt == 2
        assert run.actor_login == "criticalsec"
        assert run.triggering_actor_login == "renovate[bot]"
        assert run.configuration["completed_at_source"] == COMPLETED_AT_FROM_JOBS

        result = create_node("github_core__github_actions_job", job_nodes[0]["node"])
        assert result.success, f"create_node failed: {result.errors}"
        job = get_model_class("github_core__github_actions_job").objects.get(
            entity=Entity.objects.get(pk=result.entity_id)
        )
        assert job.created_at == datetime(2026, 9, 1, 18, 1, 41, tzinfo=UTC)
        assert (job.started_at - job.created_at).total_seconds() == 39
