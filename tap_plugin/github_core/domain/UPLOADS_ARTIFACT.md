# UPLOADS_ARTIFACT

## Blurb

A workflow run uploaded this artifact — GitHub's own attribution (`workflow_run.id` on the artifact), not a derivation. The execution side of the corpus's edge; the declared side is still open.

## Purpose

This is the one producer edge in the output column that GitHub states rather than the collector infers, and it is the edge ArtiPACKED needs: *which run*, on *which ref*, uploaded the thing that leaked. The ref is on the edge as well as the node because the question is about the pair — a run on a fork's pull-request branch uploading, a privileged workflow downloading — and an edge that carries its own trust-boundary fact can be filtered without a join.

**The corpus sources this edge on `workflow_job`**, the declared job, with `{paths}` from the `actions/upload-artifact` step. That is the *declaration* side — which job is written to upload what — and it needs the step parser. What the artifact listing records is the *execution* side — which run actually did — and that is what this plugin builds first, on the run. The two are not in conflict; they are the two layers the observation dimension exists to keep apart, and the declared side is recorded as open rather than approximated by pointing this edge at the wrong source.

## Goals

1. Carry GitHub's attribution of an upload to its run, unmodified.
2. Put the producing ref on the edge so the trust-boundary filter is one hop.
3. Emit only when the run is in the batch — a dangling endpoint would be dropped by the submission guard anyway, and the `run_id` field keeps the fact.

## Identity

Derived: `uuid5(ns, "UPLOADS_ARTIFACT__github_core:<run_uuid>:<artifact_uuid>")`.

## Boundaries

- **Not the declared step.** See above; `workflow_job UPLOADS_ARTIFACT` with `{paths}` is the corpus's form and is not built.
- **Not `DOWNLOADS_ARTIFACT`.** Not built: GitHub exposes no download log, and the declared download steps need the parser too.
- **Not the job.** GitHub attributes an artifact to the run, not the job that ran the step, so the edge stops there rather than guessing.

## Neutrality

Neutral-capable: GitLab attributes job artifacts to the job, one level finer.

## Observability

From the artifact listing's `workflow_run` object (`repository:actions:read`), measured present on every item 2026-09-02. Absent for an artifact whose run is outside the collected window — the artifact's `run_id` is the record, `STORES_ARTIFACT` keeps it reachable, and the artifacts-truncation warning says how much of the listing sits past the cap.

## Authoritative Source

- **Source:** GitHub REST API — `GET /repos/{owner}/{repo}/actions/artifacts`, the `workflow_run` object on each artifact
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-09-02

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `UPLOADS_ARTIFACT` / `DOWNLOADS_ARTIFACT`, workflow_job → artifact, `{cross_workflow}`.
- `git-serious-tap/docs/doc-git-serious-vocab-from-incidents.md` row 14 (2026-08-27) — ArtiPACKED.

## Endpoints

- **Source:** `github_core__github_actions_run` — the run that uploaded it.
- **Target:** `github_core__actions_artifact` — the artifact.
- **Dimensions:** `github.platform`, `github.surface: actions`, `github.observation: execution` — the layer follows the source, which is an execution.
- **Properties:** `head_branch` (the ref the producing run was on — the trust-boundary fact), `head_sha` (the commit it built).
