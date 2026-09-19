"""The four proof cases every falsifier ships (req-grid-reconcile-falsifier-6), against the fake
GitHub — one per reconcilable type (github-core#151), plus the probe-status mapping and the
two ways a falsifier declines to answer (no stable id on the grid; no credential).

Each case builds real grid rows through the service layer, hands the falsifier a candidate
naming the row and its parent, and arranges the fake so the probe meets its situation:
present, dropped (404), forbidden (403), reidentified (found under a different source id).
``run_four_cases`` calls ``batch_falsify`` ONCE with all four and asserts each verdict.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError
from tap_plugin.github_core.falsifiers import (
    EnvironmentFalsifier,
    RepositoryFalsifier,
    WorkflowFalsifier,
    WorkflowJobFalsifier,
    probe_status_of,
)
from tap_plugin.github_core.tests.fake_github import FakeGithub, contents_payload

from tap_grid.falsifier_testing import (
    CASE_DROPPED,
    CASE_FORBIDDEN,
    CASE_PRESENT,
    CASE_REIDENTIFIED,
    run_four_cases,
)
from tap_grid.falsifiers import (
    PRESENT_AT_PROBE,
    RELOCATED,
    UNDETERMINED,
    Candidate,
    FalsifyContext,
    Verdict,
    unsupported,
)
from tap_grid.services import create_node

ACCOUNT = "github_core__github_account"
REPOSITORY = "github_core__github_repository"
WORKFLOW = "github_core__github_workflow"
JOB = "github_core__workflow_job"
ENVIRONMENT = "github_core__github_environment"

WORKFLOW_YAML = (
    "name: ci\non: [push]\njobs:\n"
    "  build:\n    runs-on: ubuntu-latest\n    steps: []\n"
    "  test:\n    runs-on: ubuntu-latest\n    steps: []\n"
)


def _create(type_slug: str, payload: dict[str, Any]) -> uuid.UUID:
    result = create_node(type_slug, payload)
    assert result.success, f"create_node failed: {result.errors}"
    assert result.entity_id is not None
    return uuid.UUID(str(result.entity_id))


def _candidate(entity_id: uuid.UUID, entity_type: str, parent: uuid.UUID | None, *, surface: int = 0) -> Candidate:
    return Candidate(
        entity_id=entity_id,
        entity_type=entity_type,
        reason="dropped_from_observation",
        surface=surface,
        relation="fixture",
        subject=str(parent) if parent else None,
        edge_type=None,
        parent=parent,
        interval_first=None,
    )


def _context() -> FalsifyContext:
    return FalsifyContext(batch_id=str(uuid.uuid4()), statement=None)


def _probe(verdict: Verdict) -> dict[str, Any]:
    assert verdict.probe is not None, "a judged verdict records its probe"
    return verdict.probe


def _assert_evidence_supports(verdicts: dict[str, Any]) -> None:
    """Core re-derives the classification from the recorded sides; every verdict must survive."""
    for case, verdict in verdicts.items():
        assert unsupported(verdict) is None, f"{case}: {unsupported(verdict)}"


@pytest.mark.django_db
class TestRepositoryFalsifier:
    """Shape B: GET /repos/{owner}/{repo}; node id + owner login compared."""

    @staticmethod
    def _repo(name: str, github_id: int, owner: uuid.UUID | None) -> tuple[uuid.UUID, Candidate]:
        rid = _create(REPOSITORY, {"full_name": f"acme/{name}", "owner_login": "acme", "github_id": github_id})
        return rid, _candidate(rid, REPOSITORY, owner)

    @pytest.mark.spec("req-grid-reconcile-falsifier-6")
    def test_four_cases(self) -> None:
        account = _create(ACCOUNT, {"login": "acme"})
        fake = FakeGithub()
        cases: dict[str, Candidate] = {}
        _, cases[CASE_PRESENT] = self._repo("present", 1, account)
        fake.answer(
            "/repos/acme/present",
            {"id": 1, "full_name": "acme/present", "owner": {"login": "acme"}, "created_at": "2024-01-01T00:00:00Z"},
        )
        _, cases[CASE_DROPPED] = self._repo("dropped", 2, account)
        fake.refuse("/repos/acme/dropped", 404)
        _, cases[CASE_FORBIDDEN] = self._repo("forbidden", 3, account)
        fake.refuse("/repos/acme/forbidden", 403)
        _, cases[CASE_REIDENTIFIED] = self._repo("reborn", 4, account)
        fake.answer("/repos/acme/reborn", {"id": 44, "full_name": "acme/reborn", "owner": {"login": "acme"}})

        verdicts = run_four_cases(RepositoryFalsifier(client=fake), cases, _context())
        _assert_evidence_supports(verdicts)
        assert verdicts[CASE_PRESENT].expected == {"source_id": "1", "owner": "acme", "name": "acme/present"}
        assert _probe(verdicts[CASE_PRESENT])["owner"] == "acme"
        assert _probe(verdicts[CASE_DROPPED])["detail"] == "HTTP 404"
        assert sorted(fake.calls) == [
            "/repos/acme/dropped",
            "/repos/acme/forbidden",
            "/repos/acme/present",
            "/repos/acme/reborn",
        ]

    @pytest.mark.spec("req-grid-reconcile-falsifier-7")
    def test_a_transfer_is_relocated_not_retired(self) -> None:
        account = _create(ACCOUNT, {"login": "acme"})
        rid, candidate = self._repo("moved", 5, account)
        fake = FakeGithub({"/repos/acme/moved": {"id": 5, "full_name": "newco/moved", "owner": {"login": "newco"}}})
        [verdict] = RepositoryFalsifier(client=fake).batch_falsify([candidate], _context())
        assert (verdict.verdict, verdict.kind) == (RELOCATED, "transferred")
        assert unsupported(verdict) is None

    def test_no_parent_on_the_grid_means_the_owner_is_not_compared(self) -> None:
        """Option A: Expected.owner is None where the grid holds no parent, and a probe answer under
        some login is then not a transfer — the grid never claimed an owner to end."""
        _, candidate = self._repo("orphan", 6, None)
        fake = FakeGithub({"/repos/acme/orphan": {"id": 6, "full_name": "acme/orphan", "owner": {"login": "somebody"}}})
        [verdict] = RepositoryFalsifier(client=fake).batch_falsify([candidate], _context())
        assert verdict.verdict == PRESENT_AT_PROBE
        assert verdict.expected == {"source_id": "6", "owner": None, "name": "acme/orphan"}
        assert unsupported(verdict) is None

    def test_a_row_without_a_stable_id_is_not_answered(self) -> None:
        rid = _create(REPOSITORY, {"full_name": "acme/legacy"})
        fake = FakeGithub({"/repos/acme/legacy": {"id": 9, "full_name": "acme/legacy", "owner": {"login": "acme"}}})
        [verdict] = RepositoryFalsifier(client=fake).batch_falsify([_candidate(rid, REPOSITORY, None)], _context())
        assert (verdict.verdict, verdict.reason) == (UNDETERMINED, "scope_unknown")
        assert fake.calls == [], "nothing is probed when the grid holds nothing to compare against"


@pytest.mark.django_db
class TestWorkflowFalsifier:
    """Shape A: the file at HEAD, then the Actions workflow id behind it."""

    @staticmethod
    def _workflow(repo: uuid.UUID | None, file: str, workflow_id: int, name: str = "ci") -> Candidate:
        # `name` is the YAML `name:` the collector stores from the Actions record; the probe reads
        # the same record, so a changed name reads as RELOCATED(renamed) — a locator update.
        wid = _create(
            WORKFLOW,
            {"full_name": "acme/app", "workflow_id": workflow_id, "path": f".github/workflows/{file}", "name": name},
        )
        return _candidate(wid, WORKFLOW, repo)

    @pytest.mark.spec("req-grid-reconcile-falsifier-6")
    def test_four_cases(self) -> None:
        repo = _create(REPOSITORY, {"full_name": "acme/app", "github_id": 10})
        fake = FakeGithub()
        cases: dict[str, Candidate] = {}
        cases[CASE_PRESENT] = self._workflow(repo, "ci.yml", 100)
        fake.answer("/repos/acme/app/contents/.github/workflows/ci.yml", contents_payload(WORKFLOW_YAML))
        fake.answer(
            "/repos/acme/app/actions/workflows/ci.yml", {"id": 100, "name": "ci", "path": ".github/workflows/ci.yml"}
        )
        cases[CASE_DROPPED] = self._workflow(repo, "gone.yml", 101)
        fake.refuse("/repos/acme/app/contents/.github/workflows/gone.yml", 404)
        cases[CASE_FORBIDDEN] = self._workflow(repo, "secret.yml", 102)
        fake.refuse("/repos/acme/app/contents/.github/workflows/secret.yml", 403)
        cases[CASE_REIDENTIFIED] = self._workflow(repo, "reborn.yml", 103)
        fake.answer("/repos/acme/app/contents/.github/workflows/reborn.yml", contents_payload(WORKFLOW_YAML))
        fake.answer(
            "/repos/acme/app/actions/workflows/reborn.yml",
            {"id": 999, "name": "reborn", "path": ".github/workflows/reborn.yml"},
        )

        verdicts = run_four_cases(WorkflowFalsifier(client=fake), cases, _context())
        _assert_evidence_supports(verdicts)
        assert verdicts[CASE_PRESENT].expected == {"source_id": "100", "owner": "acme/app", "name": "ci"}
        assert _probe(verdicts[CASE_REIDENTIFIED])["source_id"] == "999"
        # The dropped and forbidden files never reach the Actions endpoint: the file at HEAD is the proof.
        assert "/repos/acme/app/actions/workflows/gone.yml" not in fake.calls
        assert "/repos/acme/app/actions/workflows/secret.yml" not in fake.calls

    def test_a_changed_yaml_name_is_a_rename_not_a_retirement(self) -> None:
        candidate = self._workflow(None, "ci.yml", 100, name="old name")
        fake = FakeGithub({"/repos/acme/app/contents/.github/workflows/ci.yml": contents_payload(WORKFLOW_YAML)})
        fake.answer("/repos/acme/app/actions/workflows/ci.yml", {"id": 100, "name": "new name"})
        [verdict] = WorkflowFalsifier(client=fake).batch_falsify([candidate], _context())
        assert (verdict.verdict, verdict.kind) == (RELOCATED, "renamed")
        assert unsupported(verdict) is None

    def test_a_file_actions_does_not_know_is_not_answered(self) -> None:
        candidate = self._workflow(None, "notes.yml", 104)
        fake = FakeGithub({"/repos/acme/app/contents/.github/workflows/notes.yml": contents_payload("hello: world\n")})
        fake.refuse("/repos/acme/app/actions/workflows/notes.yml", 404)
        [verdict] = WorkflowFalsifier(client=fake).batch_falsify([candidate], _context())
        assert (verdict.verdict, verdict.reason) == (UNDETERMINED, "errored")
        assert unsupported(verdict) is None


@pytest.mark.django_db
class TestWorkflowJobFalsifier:
    """Shape A at the job's granularity: the key inside the file at HEAD; one fetch per file."""

    @staticmethod
    def _job(workflow: uuid.UUID | None, file: str, workflow_id: int, key: str) -> Candidate:
        jid = _create(
            JOB,
            {
                "full_name": "acme/app",
                "workflow_id": workflow_id,
                "workflow_path": f".github/workflows/{file}",
                "job_key": key,
                "name": key,
            },
        )
        return _candidate(jid, JOB, workflow)

    @pytest.mark.spec("req-grid-reconcile-falsifier-6")
    def test_four_cases(self) -> None:
        workflow = _create(WORKFLOW, {"full_name": "acme/app", "workflow_id": 100, "path": ".github/workflows/ci.yml"})
        reborn = _create(
            WORKFLOW, {"full_name": "acme/app", "workflow_id": 103, "path": ".github/workflows/reborn.yml"}
        )
        fake = FakeGithub()
        cases: dict[str, Candidate] = {}
        cases[CASE_PRESENT] = self._job(workflow, "ci.yml", 100, "build")
        cases[CASE_DROPPED] = self._job(workflow, "ci.yml", 100, "deploy")  # key gone from the same file
        fake.answer("/repos/acme/app/contents/.github/workflows/ci.yml", contents_payload(WORKFLOW_YAML))
        fake.answer("/repos/acme/app/actions/workflows/ci.yml", {"id": 100, "name": "ci"})
        cases[CASE_FORBIDDEN] = self._job(workflow, "secret.yml", 102, "build")
        fake.refuse("/repos/acme/app/contents/.github/workflows/secret.yml", 403)
        # The file was deleted and re-created: same key, new workflow id behind it.
        cases[CASE_REIDENTIFIED] = self._job(reborn, "reborn.yml", 103, "build")
        fake.answer("/repos/acme/app/contents/.github/workflows/reborn.yml", contents_payload(WORKFLOW_YAML))
        fake.answer("/repos/acme/app/actions/workflows/reborn.yml", {"id": 999, "name": "reborn"})

        verdicts = run_four_cases(WorkflowJobFalsifier(client=fake), cases, _context())
        _assert_evidence_supports(verdicts)
        assert verdicts[CASE_PRESENT].expected == {"source_id": "100#build", "owner": "100", "name": "build"}
        assert "job key absent" in _probe(verdicts[CASE_DROPPED])["detail"]
        assert _probe(verdicts[CASE_REIDENTIFIED])["source_id"] == "999#build"
        # Two candidates in ci.yml, one fetch of it.
        assert fake.calls.count("/repos/acme/app/contents/.github/workflows/ci.yml") == 1


@pytest.mark.django_db
class TestEnvironmentFalsifier:
    """Shape B: GET /repos/{owner}/{repo}/environments/{name}; the numeric id compared."""

    @staticmethod
    def _env(repo: uuid.UUID | None, name: str, environment_id: int) -> Candidate:
        eid = _create(ENVIRONMENT, {"full_name": "acme/app", "name": name, "environment_id": environment_id})
        return _candidate(eid, ENVIRONMENT, repo)

    @pytest.mark.spec("req-grid-reconcile-falsifier-6")
    def test_four_cases(self) -> None:
        repo = _create(REPOSITORY, {"full_name": "acme/app", "github_id": 10})
        fake = FakeGithub()
        cases: dict[str, Candidate] = {}
        cases[CASE_PRESENT] = self._env(repo, "production", 1)
        fake.answer(
            "/repos/acme/app/environments/production",
            {"id": 1, "name": "production", "created_at": "2024-01-01T00:00:00Z"},
        )
        cases[CASE_DROPPED] = self._env(repo, "staging", 2)
        fake.refuse("/repos/acme/app/environments/staging", 404)
        cases[CASE_FORBIDDEN] = self._env(repo, "vault", 3)
        fake.refuse("/repos/acme/app/environments/vault", 403)
        cases[CASE_REIDENTIFIED] = self._env(repo, "reborn", 4)
        fake.answer("/repos/acme/app/environments/reborn", {"id": 44, "name": "reborn"})

        verdicts = run_four_cases(EnvironmentFalsifier(client=fake), cases, _context())
        _assert_evidence_supports(verdicts)
        assert verdicts[CASE_PRESENT].expected == {"source_id": "1", "owner": "acme/app", "name": "production"}

    def test_a_name_needing_escaping_is_quoted_in_the_path(self) -> None:
        candidate = self._env(None, "prod/eu west", 7)
        fake = FakeGithub({"/repos/acme/app/environments/prod%2Feu%20west": {"id": 7, "name": "prod/eu west"}})
        [verdict] = EnvironmentFalsifier(client=fake).batch_falsify([candidate], _context())
        assert verdict.verdict == PRESENT_AT_PROBE


class TestProbeStatus:
    @pytest.mark.parametrize(
        ("status", "body", "want"),
        [
            (404, '{"message": "Not Found"}', "not_found"),
            (403, "{}", "forbidden"),
            (401, "{}", "forbidden"),
            (403, '{"message": "API rate limit exceeded"}', "rate_limited"),
            (429, "{}", "rate_limited"),
            (500, "{}", "errored"),
            (0, "connection refused", "errored"),
        ],
    )
    def test_mapping(self, status: int, body: str, want: str) -> None:
        assert probe_status_of(GithubAPIError(status=status, url="/x", body=body)) == want


class TestNoCredential:
    def test_a_missing_credential_answers_undetermined_for_every_candidate(self) -> None:
        def boom() -> Any:
            raise RuntimeError("no secret mounted")

        candidates = [_candidate(uuid.uuid4(), REPOSITORY, None, surface=i) for i in range(2)]
        verdicts = RepositoryFalsifier(client_factory=boom).batch_falsify(candidates, _context())
        assert [(v.verdict, v.reason) for v in verdicts] == [(UNDETERMINED, "errored")] * 2
        assert all("credential unavailable" in v.note for v in verdicts)
