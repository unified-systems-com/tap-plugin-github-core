"""Per-type falsifiers for the git-provable and enumerable shapes (github-core#151, phase 3).

A retirement candidate (``tap_grid.candidates``) is a child the grid holds under a parent
whose listing this run read completely and did not name it. Nothing is retired on that
alone: a **falsifier** — one per entity type, declared in ``[falsifiers]`` of
``tap-plugin.toml`` — probes GitHub for each candidate and hands what it found to
``tap_grid.falsifiers.verdict_from_probe``, which derives the verdict. HTTP success is not a
verdict; the identity and the owner are compared, never the status line
(``req-grid-reconcile-falsifier``). Authority stays off: the verdicts are recorded on the
run's lifecycle batch and the reconcile verb (tap#652) is what would act on them.

Which types get one follows the shape table in github-core#14:

- **Shape A, git-provable** — ``github_workflow`` and ``workflow_job``. The workflow is a file
  under ``.github/workflows/`` at HEAD; its removal is a commit. The probe reads the file at
  HEAD (``GET /repos/{owner}/{repo}/contents/{path}``) and, when it is there, resolves the
  Actions workflow record behind it (``GET /repos/{owner}/{repo}/actions/workflows/{file}``)
  so a file re-created at the same path with a new workflow id reads as REIDENTIFIED rather
  than as the old one. A declared job is falsified at the same granularity: the job key is
  looked for in that file.
- **Shape B, enumerable** — ``github_repository`` and ``github_environment``. ``GET
  /repos/{owner}/{repo}`` and ``GET /repos/{owner}/{repo}/environments/{name}``: 404 is
  ``not_found``, 401/403 is ``forbidden`` (GitHub answers 404 for a private repository the
  credential may not see, so a repository ``not_found`` still means "gone from this
  credential's view", which is all the listing ever claimed), and a found object is compared
  by its stable numeric id and — for a repository — by its owner login, so a transfer ends
  the ownership edge rather than retiring the repository.
- **Shape C, immutable events** — ``github_actions_run`` and ``github_actions_job`` get NO
  falsifier on purpose: no containment edge reaches them, so they are never candidates, and
  a run does not stop having happened when GitHub ages it out.

``Expected.owner`` is the parent's source identity read off the candidate's ``parent``
(Option A, tap#650): the account login for a repository, the repository full name for a
workflow or an environment, the workflow id for a declared job. Where the grid holds no
parent, the owner is ``None`` and the probe's owner is not compared — a comparison the grid
never asked for cannot yield a transfer.

Every probe is a single-object read of the source under this plugin's own credential
(``github_core:collector``, resolved here, never taken from the run context); no response
body is recorded — ``Probe.detail`` carries an HTTP status or an error class only.
"""

from __future__ import annotations

import base64
import logging
import posixpath
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import quote

from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError, GithubClient
from tap_plugin.github_core.collectors.github_collector.auth import GithubAuth
from tap_plugin.github_core.collectors.github_collector.parser import parse_workflow_yaml
from tap_plugin.github_core.collectors.github_collector.secret import api_base_url, resolve_github_secret

from tap_grid.falsifiers import (
    UNDETERMINED,
    Candidate,
    Expected,
    Falsifier,
    FalsifyContext,
    Probe,
    Verdict,
    verdict_from_probe,
)
from tap_grid.services import get_node

logger = logging.getLogger(__name__)

#: What a probe needs of the client: one authenticated GET that raises ``GithubAPIError``.
ProbeClient = Any


def _default_client() -> GithubClient:
    """The collector's own credential, resolved from the secret store (consumer-scoped)."""
    secret = resolve_github_secret()
    data = dict(secret.data)
    auth = GithubAuth(kind=secret.kind, data=data, api_base_url=api_base_url(data))
    return GithubClient(token=auth.token(), api_base_url=api_base_url(data), retry_empty_404=False)


def probe_status_of(exc: GithubAPIError) -> str:
    """The closed probe status an API failure maps to. 404 is the only ``not_found``; a
    credential refusal is ``forbidden``; a rate limit is named as such; everything else
    (network, 5xx, 0) is ``errored`` — the probe could not answer."""
    if exc.status == 404:
        return "not_found"
    if exc.status == 429:
        return "rate_limited"
    if exc.status == 403 and "rate limit" in exc.body.lower():
        return "rate_limited"
    if exc.status in (401, 403):
        return "forbidden"
    return "errored"


def _failed_probe(exc: GithubAPIError) -> Probe:
    """A probe that did not find, or could not answer. ``detail`` is the status line only:
    never the body (``Probe.detail`` contract)."""
    return Probe(status=probe_status_of(exc), detail=f"HTTP {exc.status}")  # type: ignore[arg-type]


def _created_at(payload: dict[str, Any]) -> datetime | None:
    value = payload.get("created_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _undetermined(candidate: Candidate, reason: str, note: str) -> Verdict:
    return Verdict(
        entity_id=candidate.entity_id, verdict=UNDETERMINED, reason=reason, surface=candidate.surface, note=note
    )


def _row_of(entity_id: Any) -> Any | None:
    """The typed grid row behind an entity id through the service read, or None when it
    cannot be read (unknown id, unknown type, an edge)."""
    if entity_id is None:
        return None
    try:
        return get_node(entity_id)
    except Exception:  # noqa: BLE001 — a candidate the grid cannot show is answered, not raised
        logger.warning("[82e7] falsifier could not read grid row %s", entity_id)
        return None


def _decode_file(payload: dict[str, Any]) -> str:
    """The text of a Contents API payload (base64) or "" when it carries none."""
    encoded = payload.get("content") or ""
    if not isinstance(encoded, str) or not encoded:
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except ValueError, UnicodeDecodeError:
        return ""


class _GithubFalsifier(Falsifier):
    """The shared shape: one client per batch, one verdict per candidate, fail closed.

    ``client`` is injected by tests (``tests/fake_github.py``); at runtime it is resolved from
    the collector's secret on the first batch. A credential that cannot be resolved answers
    ``UNDETERMINED(errored)`` for every candidate rather than raising — core would record the
    same verdict, but this way the note says why.
    """

    def __init__(
        self, client: ProbeClient | None = None, client_factory: Callable[[], ProbeClient] | None = None
    ) -> None:
        self._client = client
        self._client_factory = client_factory or _default_client

    def _resolve_client(self) -> ProbeClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def batch_falsify(self, candidates: Sequence[Candidate], context: FalsifyContext) -> list[Verdict]:
        try:
            client = self._resolve_client()
        except Exception as exc:  # noqa: BLE001 — a missing credential is an answer, not a crash
            note = f"credential unavailable: {type(exc).__name__}"
            logger.warning("[dffc] %s: %s", type(self).__name__, note)
            return [_undetermined(c, "errored", note) for c in candidates]
        return self.judge_all(client, list(candidates))

    def judge_all(self, client: ProbeClient, candidates: list[Candidate]) -> list[Verdict]:
        return [self.judge(client, c) for c in candidates]

    def judge(self, client: ProbeClient, candidate: Candidate) -> Verdict:
        raise NotImplementedError

    @staticmethod
    def _probe_get(client: ProbeClient, path: str) -> dict[str, Any] | Probe:
        """A single GET: the payload, or the failed probe it maps to."""
        try:
            payload = client.get(path)
        except GithubAPIError as exc:
            return _failed_probe(exc)
        return payload if isinstance(payload, dict) else {}


class RepositoryFalsifier(_GithubFalsifier):
    """Shape B: ``GET /repos/{owner}/{repo}``; compare GitHub's numeric id and the owner login.

    A repository has four causes for one listing absence (deleted, made private, transferred,
    access narrowed). The probe separates them: ``not_found`` is dropped from this credential's
    view; ``forbidden`` is undetermined; a found object under another login is a transfer
    (RELOCATED, ends the ownership edge, retires nothing); a found object with a different id
    at the same name is REIDENTIFIED.
    """

    def judge(self, client: ProbeClient, candidate: Candidate) -> Verdict:
        row = _row_of(candidate.entity_id)
        if row is None:
            return _undetermined(candidate, "errored", "the grid row could not be read")
        if getattr(row, "github_id", None) is None:
            return _undetermined(
                candidate, "scope_unknown", "the grid holds no stable id (github_id) for this repository"
            )
        full_name = str(getattr(row, "full_name", "") or "")
        if "/" not in full_name:
            return _undetermined(candidate, "scope_unknown", "the grid holds no owner/repo locator for this repository")
        parent = _row_of(candidate.parent)
        owner = str(getattr(parent, "login", "") or "") or None if parent is not None else None
        expected = Expected(source_id=str(row.github_id), owner=owner, name=full_name)
        result = self._probe_get(client, f"/repos/{full_name}")
        if isinstance(result, Probe):
            return verdict_from_probe(candidate, expected, result)
        found_owner = str((result.get("owner") or {}).get("login") or "") or None
        probe = Probe(
            status="found",
            source_id=str(result.get("id")) if result.get("id") is not None else None,
            # Compared only when the grid holds an owner (Option A): with no parent on record
            # there is no ownership relation for a transfer to end.
            owner=found_owner if owner is not None else None,
            name=str(result.get("full_name") or "") or None,
            created_at=_created_at(result),
            detail="HTTP 200",
        )
        return verdict_from_probe(candidate, expected, probe)


class EnvironmentFalsifier(_GithubFalsifier):
    """Shape B: ``GET /repos/{owner}/{repo}/environments/{name}``; compare the numeric id."""

    def judge(self, client: ProbeClient, candidate: Candidate) -> Verdict:
        row = _row_of(candidate.entity_id)
        if row is None:
            return _undetermined(candidate, "errored", "the grid row could not be read")
        if getattr(row, "environment_id", None) is None:
            return _undetermined(
                candidate, "scope_unknown", "the grid holds no stable id (environment_id) for this environment"
            )
        full_name, name = str(getattr(row, "full_name", "") or ""), str(getattr(row, "name", "") or "")
        if "/" not in full_name or not name:
            return _undetermined(
                candidate, "scope_unknown", "the grid holds no repository locator or name for this environment"
            )
        parent = _row_of(candidate.parent)
        owner = str(getattr(parent, "full_name", "") or "") or None if parent is not None else None
        expected = Expected(source_id=str(row.environment_id), owner=owner, name=name)
        result = self._probe_get(client, f"/repos/{full_name}/environments/{quote(name, safe='')}")
        if isinstance(result, Probe):
            return verdict_from_probe(candidate, expected, result)
        probe = Probe(
            status="found",
            source_id=str(result.get("id")) if result.get("id") is not None else None,
            # The environment endpoint is addressed by repository, so the owner it answers under
            # is the repository asked; compared only when the grid holds one.
            owner=full_name if owner is not None else None,
            name=str(result.get("name") or "") or None,
            created_at=_created_at(result),
            detail="HTTP 200",
        )
        return verdict_from_probe(candidate, expected, probe)


#: The probe recorded when a file read yields neither a record nor a failure — unreachable by
#: construction (``_workflow_file_at_head`` always returns one of the two), kept so the fail-closed
#: branch needs no assertion that a production build would strip.
_NO_ANSWER = Probe(status="errored", detail="the file read returned no answer")


def _workflow_file_at_head(
    client: ProbeClient, full_name: str, path: str
) -> tuple[dict[str, Any] | None, Probe | None]:
    """The file at HEAD (shape A's proof) and, when present, the Actions workflow record behind
    it. Returns ``(record, failed_probe)``: a record with ``id``/``name``/``path``/``text`` when
    the file is there and Actions knows it; otherwise the probe that explains why not."""
    contents = _GithubFalsifier._probe_get(client, f"/repos/{full_name}/contents/{path}")
    if isinstance(contents, Probe):
        return None, contents
    record = _GithubFalsifier._probe_get(
        client, f"/repos/{full_name}/actions/workflows/{quote(posixpath.basename(path), safe='')}"
    )
    if isinstance(record, Probe):
        if record.status == "not_found":
            # The file is at HEAD but Actions has no workflow record for it: not a workflow the
            # grid's row can be compared against. Not answered, rather than retired.
            return None, Probe(status="errored", detail="file present at HEAD; no Actions workflow record (HTTP 404)")
        return None, record
    return {**record, "text": _decode_file(contents)}, None


class WorkflowFalsifier(_GithubFalsifier):
    """Shape A: the file at HEAD, then the Actions workflow id behind it.

    The grid's identity is GitHub's workflow id; the file path is the locator. A missing file
    is ``not_found`` (a commit removed it — positive evidence); a present file whose Actions
    record carries a different id is REIDENTIFIED; the same id is PRESENT_AT_PROBE. The owner
    is the repository the file is asked in, so a transfer of the repository shows on the
    repository's own candidate, never here.
    """

    def judge(self, client: ProbeClient, candidate: Candidate) -> Verdict:
        row = _row_of(candidate.entity_id)
        if row is None:
            return _undetermined(candidate, "errored", "the grid row could not be read")
        workflow_id, path = getattr(row, "workflow_id", None), str(getattr(row, "path", "") or "")
        full_name = str(getattr(row, "full_name", "") or "")
        if workflow_id is None or not path or "/" not in full_name:
            return _undetermined(
                candidate,
                "scope_unknown",
                "the grid holds no workflow id, path or repository locator for this workflow",
            )
        parent = _row_of(candidate.parent)
        owner = str(getattr(parent, "full_name", "") or "") or None if parent is not None else None
        expected = Expected(source_id=str(workflow_id), owner=owner, name=str(getattr(row, "name", "") or "") or None)
        record, failed = _workflow_file_at_head(client, full_name, path)
        if record is None:
            return verdict_from_probe(candidate, expected, failed or _NO_ANSWER)
        probe = Probe(
            status="found",
            source_id=str(record.get("id")) if record.get("id") is not None else None,
            owner=full_name if owner is not None else None,
            name=str(record.get("name") or "") or None,
            created_at=_created_at(record),
            detail="HTTP 200",
        )
        return verdict_from_probe(candidate, expected, probe)


class WorkflowJobFalsifier(_GithubFalsifier):
    """Shape A at the job's granularity: the job key inside the workflow file at HEAD.

    Identity is ``<workflow id>#<job key>`` — a job re-declared under the same key in a
    re-created file (new workflow id) is a different declaration, REIDENTIFIED. The owner is
    the parent workflow's id. One file fetch per (repository, path) serves every job candidate
    of that file.
    """

    def judge_all(self, client: ProbeClient, candidates: list[Candidate]) -> list[Verdict]:
        files: dict[tuple[str, str], tuple[dict[str, Any] | None, Probe | None]] = {}
        out: list[Verdict] = []
        for candidate in candidates:
            row = _row_of(candidate.entity_id)
            if row is None:
                out.append(_undetermined(candidate, "errored", "the grid row could not be read"))
                continue
            workflow_id, path = getattr(row, "workflow_id", None), str(getattr(row, "workflow_path", "") or "")
            full_name, job_key = str(getattr(row, "full_name", "") or ""), str(getattr(row, "job_key", "") or "")
            if workflow_id is None or not path or not job_key or "/" not in full_name:
                out.append(
                    _undetermined(
                        candidate,
                        "scope_unknown",
                        "the grid holds no workflow id, path, job key or repository locator for this job",
                    )
                )
                continue
            parent = _row_of(candidate.parent)
            parent_id = getattr(parent, "workflow_id", None) if parent is not None else None
            owner = str(parent_id) if parent_id is not None else None
            expected = Expected(
                source_id=f"{workflow_id}#{job_key}", owner=owner, name=str(getattr(row, "name", "") or "") or None
            )
            key = (full_name, path)
            if key not in files:
                files[key] = _workflow_file_at_head(client, full_name, path)
            record, failed = files[key]
            if record is None:
                out.append(verdict_from_probe(candidate, expected, failed or _NO_ANSWER))
                continue
            jobs = {
                str(j.get("id") or ""): j for j in (parse_workflow_yaml(record.get("text") or "").get("jobs") or [])
            }
            job = jobs.get(job_key)
            if job is None:
                out.append(
                    verdict_from_probe(
                        candidate, expected, Probe(status="not_found", detail="job key absent from the file at HEAD")
                    )
                )
                continue
            found_id = record.get("id")
            probe = Probe(
                status="found",
                source_id=f"{found_id}#{job_key}" if found_id is not None else None,
                owner=str(found_id) if owner is not None and found_id is not None else None,
                name=str(job.get("name") or job_key),
                detail="HTTP 200",
            )
            out.append(verdict_from_probe(candidate, expected, probe))
        return out

    def judge(self, client: ProbeClient, candidate: Candidate) -> Verdict:
        return self.judge_all(client, [candidate])[0]


__all__ = [
    "EnvironmentFalsifier",
    "RepositoryFalsifier",
    "WorkflowFalsifier",
    "WorkflowJobFalsifier",
    "probe_status_of",
]
