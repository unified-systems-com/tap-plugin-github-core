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
from tap_plugin.github_core.reach import (
    SELECTION_ALL,
    SELECTION_SELECTED,
    SELECTION_UNKNOWN,
    Reach,
    resolve_reach,
    unobservable,
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
    DROPPED_FROM_OBSERVATION,
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


def _reach() -> Reach:
    """A credential that can demonstrably look at every repository of the account.

    The four-case proof is about the PROBE, and a probe's 404 only means "gone" when the
    credential could prove it could look (github-core#157). Without a reach the falsifier is
    right to answer UNDETERMINED for every one of them, so each proof injects the reach it is
    implicitly asserting.
    """
    return Reach(
        credential="app",
        selection=SELECTION_ALL,
        account="acme",
        note="test: the installation follows the acme account",
    )


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

        verdicts = run_four_cases(RepositoryFalsifier(client=fake, reach=_reach()), cases, _context())
        _assert_evidence_supports(verdicts)
        assert verdicts[CASE_PRESENT].expected == {"source_id": "1", "owner": "acme", "name": "acme/present"}
        assert _probe(verdicts[CASE_PRESENT])["owner"] == "acme"
        assert _probe(verdicts[CASE_DROPPED])["detail"].startswith("HTTP 404 (GitHub also answers 404")
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
        [verdict] = RepositoryFalsifier(client=fake, reach=_reach()).batch_falsify([candidate], _context())
        assert (verdict.verdict, verdict.kind) == (RELOCATED, "transferred")
        assert unsupported(verdict) is None

    def test_no_parent_on_the_grid_means_the_owner_is_not_compared(self) -> None:
        """Option A: Expected.owner is None where the grid holds no parent, and a probe answer under
        some login is then not a transfer — the grid never claimed an owner to end."""
        _, candidate = self._repo("orphan", 6, None)
        fake = FakeGithub({"/repos/acme/orphan": {"id": 6, "full_name": "acme/orphan", "owner": {"login": "somebody"}}})
        [verdict] = RepositoryFalsifier(client=fake, reach=_reach()).batch_falsify([candidate], _context())
        assert verdict.verdict == PRESENT_AT_PROBE
        assert verdict.expected == {"source_id": "6", "owner": None, "name": "acme/orphan"}
        assert unsupported(verdict) is None

    def test_a_row_without_a_stable_id_is_not_answered(self) -> None:
        rid = _create(REPOSITORY, {"full_name": "acme/legacy"})
        fake = FakeGithub({"/repos/acme/legacy": {"id": 9, "full_name": "acme/legacy", "owner": {"login": "acme"}}})
        [verdict] = RepositoryFalsifier(client=fake, reach=_reach()).batch_falsify([_candidate(rid, REPOSITORY, None)], _context())
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
        # The 404 above is judged against a probe of the containing repository: it answers, so
        # the file's absence is the file's (github-core#157).
        fake.answer("/repos/acme/app", {"id": 10, "full_name": "acme/app", "owner": {"login": "acme"}})
        cases[CASE_FORBIDDEN] = self._workflow(repo, "secret.yml", 102)
        fake.refuse("/repos/acme/app/contents/.github/workflows/secret.yml", 403)
        cases[CASE_REIDENTIFIED] = self._workflow(repo, "reborn.yml", 103)
        fake.answer("/repos/acme/app/contents/.github/workflows/reborn.yml", contents_payload(WORKFLOW_YAML))
        fake.answer(
            "/repos/acme/app/actions/workflows/reborn.yml",
            {"id": 999, "name": "reborn", "path": ".github/workflows/reborn.yml"},
        )

        verdicts = run_four_cases(WorkflowFalsifier(client=fake, reach=_reach()), cases, _context())
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
        [verdict] = WorkflowFalsifier(client=fake, reach=_reach()).batch_falsify([candidate], _context())
        assert (verdict.verdict, verdict.kind) == (RELOCATED, "renamed")
        assert unsupported(verdict) is None

    def test_a_file_actions_does_not_know_is_not_answered(self) -> None:
        candidate = self._workflow(None, "notes.yml", 104)
        fake = FakeGithub({"/repos/acme/app/contents/.github/workflows/notes.yml": contents_payload("hello: world\n")})
        fake.refuse("/repos/acme/app/actions/workflows/notes.yml", 404)
        [verdict] = WorkflowFalsifier(client=fake, reach=_reach()).batch_falsify([candidate], _context())
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

        verdicts = run_four_cases(WorkflowJobFalsifier(client=fake, reach=_reach()), cases, _context())
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
        fake.answer("/repos/acme/app", {"id": 10, "full_name": "acme/app", "owner": {"login": "acme"}})
        cases[CASE_FORBIDDEN] = self._env(repo, "vault", 3)
        fake.refuse("/repos/acme/app/environments/vault", 403)
        cases[CASE_REIDENTIFIED] = self._env(repo, "reborn", 4)
        fake.answer("/repos/acme/app/environments/reborn", {"id": 44, "name": "reborn"})

        verdicts = run_four_cases(EnvironmentFalsifier(client=fake, reach=_reach()), cases, _context())
        _assert_evidence_supports(verdicts)
        assert verdicts[CASE_PRESENT].expected == {"source_id": "1", "owner": "acme/app", "name": "production"}

    def test_a_name_needing_escaping_is_quoted_in_the_path(self) -> None:
        candidate = self._env(None, "prod/eu west", 7)
        fake = FakeGithub({"/repos/acme/app/environments/prod%2Feu%20west": {"id": 7, "name": "prod/eu west"}})
        [verdict] = EnvironmentFalsifier(client=fake, reach=_reach()).batch_falsify([candidate], _context())
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


@pytest.mark.django_db
class TestReachJudgement:
    """github-core#157 (ruling D on #155): a 404 only means GONE inside the credential's reach.

    GitHub answers 404 both for an object that is gone and for one this credential may not see.
    Every case here holds the probe constant — the same 404 — and varies only what the
    credential could prove about its own reach, so the verdict difference is attributable to
    the reach and to nothing else.
    """

    @staticmethod
    def _repo(name: str, github_id: int) -> Candidate:
        rid = _create(REPOSITORY, {"full_name": f"acme/{name}", "owner_login": "acme", "github_id": github_id})
        return _candidate(rid, REPOSITORY, None)

    @staticmethod
    def _selected(*ids: int) -> Reach:
        return Reach(
            credential="app",
            selection=SELECTION_SELECTED,
            account="acme",
            repository_ids=frozenset(ids),
            repository_names=frozenset({"acme/inside"}),
            note="test: an installation on selected repositories",
        )

    @pytest.mark.spec("req-grid-reconcile-absence-states")
    def test_in_reach_a_404_is_dropped(self) -> None:
        candidate = self._repo("inside", 1)
        fake = FakeGithub()
        fake.refuse("/repos/acme/inside", 404)
        [verdict] = RepositoryFalsifier(client=fake, reach=self._selected(1)).batch_falsify([candidate], _context())
        assert verdict.verdict == DROPPED_FROM_OBSERVATION
        assert _probe(verdict)["detail"].startswith("HTTP 404 (GitHub also answers 404")
        assert unsupported(verdict) is None

    @pytest.mark.spec("req-grid-reconcile-absence-states")
    def test_out_of_reach_a_404_is_undetermined_not_dropped(self) -> None:
        """The narrowing case: the repository left the installation, so the 404 is what a
        credential that may not look receives — not evidence the repository is gone."""
        candidate = self._repo("outside", 2)
        fake = FakeGithub()
        fake.refuse("/repos/acme/outside", 404)
        [verdict] = RepositoryFalsifier(client=fake, reach=self._selected(1)).batch_falsify([candidate], _context())
        assert (verdict.verdict, verdict.reason) == (UNDETERMINED, "scope_unknown")
        assert "not in this credential's reach" in verdict.note
        assert unsupported(verdict) is None

    @pytest.mark.spec("req-grid-reconcile-absence-states")
    def test_an_unobservable_reach_never_drops(self) -> None:
        """A personal access token cannot introspect its own reach, so nothing it 404s on may be
        read as gone (github-core#158 / #159 are what would make it observable)."""
        candidate = self._repo("inside", 3)
        fake = FakeGithub()
        fake.refuse("/repos/acme/inside", 404)
        reach = unobservable("pat", "a personal access token cannot introspect its own reach")
        [verdict] = RepositoryFalsifier(client=fake, reach=reach).batch_falsify([candidate], _context())
        assert (verdict.verdict, verdict.reason) == (UNDETERMINED, "scope_unknown")
        assert "cannot introspect" in verdict.note

    @pytest.mark.spec("req-grid-reconcile-absence-states")
    def test_a_falsifier_with_no_credential_behind_its_client_has_no_reach(self) -> None:
        """The default is fail-closed: an injected client proves nothing about what the
        credential behind it could see, so a 404 under it is never a retirement."""
        candidate = self._repo("inside", 4)
        fake = FakeGithub()
        fake.refuse("/repos/acme/inside", 404)
        [verdict] = RepositoryFalsifier(client=fake).batch_falsify([candidate], _context())
        assert (verdict.verdict, verdict.reason) == (UNDETERMINED, "scope_unknown")

    @pytest.mark.spec("req-grid-reconcile-absence-states")
    def test_a_child_404_under_a_gone_parent_is_undetermined(self) -> None:
        """The parent probe is the tie-breaker for an object inside a repository: the repository
        did not answer either, so the child's 404 says nothing about the child."""
        eid = _create(ENVIRONMENT, {"full_name": "acme/app", "name": "staging", "environment_id": 2})
        fake = FakeGithub()
        fake.refuse("/repos/acme/app/environments/staging", 404)
        fake.refuse("/repos/acme/app", 404)
        [verdict] = EnvironmentFalsifier(client=fake, reach=_reach()).batch_falsify(
            [_candidate(eid, ENVIRONMENT, None)], _context()
        )
        assert (verdict.verdict, verdict.reason) == (UNDETERMINED, "scope_unknown")
        assert "did not answer the probe" in verdict.note

    @pytest.mark.spec("req-grid-reconcile-absence-states")
    def test_the_parent_is_probed_once_per_run_however_many_children(self) -> None:
        """One call per parent, cached for the run — and the cache is keyed on the run, so a
        second run probes again rather than trusting a stale answer."""
        candidates = [
            _candidate(
                _create(ENVIRONMENT, {"full_name": "acme/app", "name": name, "environment_id": i}),
                ENVIRONMENT,
                None,
            )
            for i, name in enumerate(("staging", "preview", "canary"), start=2)
        ]
        fake = FakeGithub()
        for name in ("staging", "preview", "canary"):
            fake.refuse(f"/repos/acme/app/environments/{name}", 404)
        fake.answer("/repos/acme/app", {"id": 10, "full_name": "acme/app", "owner": {"login": "acme"}})

        context = _context()
        verdicts = EnvironmentFalsifier(client=fake, reach=_reach()).batch_falsify(candidates, context)
        assert [v.verdict for v in verdicts] == [DROPPED_FROM_OBSERVATION] * 3
        assert fake.calls.count("/repos/acme/app") == 1, "three children, one probe of their parent"

        EnvironmentFalsifier(client=fake, reach=_reach()).batch_falsify(candidates, _context())
        assert fake.calls.count("/repos/acme/app") == 2, "a new run re-probes rather than reusing the answer"


class TestReachBoundary:
    """`all` is a statement about ONE account, not about GitHub (found in review, PR# 161)."""

    @staticmethod
    def _all(account: str | None) -> Reach:
        return Reach(credential="app", selection=SELECTION_ALL, account=account, note="test")

    def test_all_holds_the_installation_account(self) -> None:
        assert self._all("acme").holds_repository(full_name="acme/app") is True
        assert self._all("acme").holds_repository(full_name="ACME/App") is True

    def test_all_does_not_hold_another_account(self) -> None:
        """An installation on acme 404s on newco's repositories because it is not installed
        there — not because they are gone."""
        assert self._all("acme").holds_repository(full_name="newco/app") is False

    def test_all_without_an_account_cannot_say(self) -> None:
        """`all` relative to nothing is not a boundary, so it answers cannot-say rather than
        yes."""
        assert self._all(None).holds_repository(full_name="acme/app") is None
        assert self._all("acme").holds_repository(github_id=7) is None, "no owner to compare"

    def test_an_installation_reporting_all_with_no_account_is_unobservable(self) -> None:
        class _Auth:
            has_app = True
            has_pat = False
            installation = {"repository_selection": "all"}

        assert resolve_reach(_Auth(), None).selection == SELECTION_UNKNOWN


@pytest.mark.django_db
class TestReachIsScopedToTheRun:
    """A reach belongs to the RUN it was read for, and so does the credential behind it.

    An installation can be narrowed between two runs. Re-resolving the reach is not enough on its
    own: `GithubAuth` records the installation once, when the token is minted, and never re-reads
    it — so an `all` selection re-derived from that frozen record would keep authorizing exactly
    the retirement this gate exists to refuse. The falsifier therefore throws away a session it
    owns at the start of each run (found in review, PR# 161).

    The test drives that through the session factory rather than by mutating a cached record, so
    it proves the production refresh path and not a hand-written one.
    """

    @staticmethod
    def _auth(selection: str) -> Any:
        class _Auth:
            has_app = True
            has_pat = False
            installation = {"repository_selection": selection, "account": {"login": "acme"}}

        return _Auth()

    @pytest.mark.spec("req-grid-reconcile-absence-states")
    def test_a_new_run_mints_a_fresh_credential_and_re_reads_the_installation(self) -> None:
        rid = _create(REPOSITORY, {"full_name": "acme/app", "owner_login": "acme", "github_id": 1})
        candidate = _candidate(rid, REPOSITORY, None)
        fake = FakeGithub()
        fake.refuse("/repos/acme/app", 404)
        # The narrowed installation names no repositories, so the walk answers an empty listing —
        # an observation, not a failure.
        fake.answer("/installation/repositories", {"repositories": [], "total_count": 0})

        sessions = [(fake, self._auth("all")), (fake, self._auth("selected"))]
        falsifier = RepositoryFalsifier(session_factory=lambda: sessions.pop(0))

        [first] = falsifier.batch_falsify([candidate], _context())
        assert first.verdict == DROPPED_FROM_OBSERVATION, "run one: acme/app is inside `all` on acme"

        [second] = falsifier.batch_falsify([candidate], _context())
        assert (second.verdict, second.reason) == (UNDETERMINED, "scope_unknown"), (
            "run two: a fresh credential reports a narrowed installation that does not name "
            "acme/app, and the same falsifier instance must not reuse run one's session"
        )
        assert not sessions, "the second run minted its own session rather than reusing the first"

    @pytest.mark.spec("req-grid-reconcile-absence-states")
    def test_an_injected_client_is_never_thrown_away(self) -> None:
        """A caller that supplied a client owns its lifetime; a pinned credential stays pinned."""
        rid = _create(REPOSITORY, {"full_name": "acme/app", "owner_login": "acme", "github_id": 1})
        candidate = _candidate(rid, REPOSITORY, None)
        fake = FakeGithub()
        fake.refuse("/repos/acme/app", 404)
        falsifier = RepositoryFalsifier(client=fake, reach=_reach())

        for _ in range(2):
            [verdict] = falsifier.batch_falsify([candidate], _context())
            assert verdict.verdict == DROPPED_FROM_OBSERVATION
        assert falsifier._client is fake, "the injected client survived the run boundary"
