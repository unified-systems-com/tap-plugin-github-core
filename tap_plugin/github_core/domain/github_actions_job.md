# GitHub Actions Job

## Blurb

A job **as run** — one executed unit inside a workflow run. Not the job as written in the YAML; that is a different object, and conflating the two is the most common modelling error in this domain.

## Purpose

Read this section before using this type.

**`github_actions_job` is an EXECUTION.** It keys on GitHub's numeric `job_id`, carries `status` and `conclusion`, has a start and end time, and stamps the dimension `github.observation: "execution"`. It is a record of something that happened once.

The job **as declared** — the block in the workflow YAML that carries `permissions:`, `runs-on:`, `environment:`, `if:`, and which steps see which secret — is a *different object*. The corpus names it `workflow_job` and rules it into the *self* tier as **the largest gap in the model**. It does not exist yet. Until it does, the declared structure lives inside [`github_workflow`](github_workflow.md)'s un-schema'd `configuration` blob.

Why this matters more than a naming quibble: **every privilege decision in CI is made at the declared level.** Roughly 20 of the 35 compromises in the incident corpus turn on a declared property — a trigger that checks out a pull-request head while secrets are in scope, a permission left at default write, an unpinned action. None of those are properties of an execution. A query that reaches for "the job" and lands here gets the wrong object and returns confident nonsense.

The two will be joined by an edge (`INSTANCE_OF_JOB`, carrying `run_attempt`), not merged. The published GitHub graph schemas surveyed in the platform pass model only the declaration and call it `WorkflowJob`; modelling both sides is the point of this vocabulary. The slug here stays `github_actions_job` because slugs are load-bearing identity and are never renamed — so the distinction lives in this article, in the `github.observation` dimension, and nowhere else. State it explicitly or the next reader will conflate them exactly as every other tool does.

## Goals

- Record what actually executed, so declared risk can be checked against behaviour.
- Carry the observed runner, which is what makes [`EXECUTED_ON`](EXECUTED_ON.md) possible and is not derivable from the declaration.
- Hold the execution end of the future declaration↔execution bridge.

## Identity

Natural key: **`<full_name>#<job_id>`** — repository plus GitHub's numeric job id. Entity id is `uuid5(ns, "github_core__github_actions_job:<full_name>#<job_id>")`.

The numeric job id is per-execution: a re-run produces new job ids. Combined with the run's own key omitting `run_attempt` (see [`github_actions_run`](github_actions_run.md)), v0 reflects the **latest-attempt job set** only (`req-github-core-collector-8`) — the default `/jobs` endpoint returns latest-attempt jobs, and `/attempts/{n}/jobs` is backlog (`req-github-core-backlog-run-attempts`).

`name` is not identity. Two jobs in a matrix share a declared name and differ only by their id and their matrix parameters, so keying on the name would collapse a matrix into one node.

## Boundaries

Deliberately **not** covered:

- **The declared job.** See Purpose. This is the boundary that matters.
- **Steps.** Rejected as a node by the corpus on the node test — nothing points at a step, it is an ordinal position within a job. Step-level facts that *are* needed (`step_index` on `REFERENCES_SECRET`, `WRITES_CACHE`, `RESTORES_CACHE`) are corpus edge properties, not endpoints.
- **Logs.** Reachable at `actions:read` and genuinely useful — runner names in logs are the basis for non-ephemeral-runner heuristics — but a log is evidence, not an entity.
- **The effective token the job held.** Not returned by any endpoint.
- **Ephemeral runners.** A job that ran on a runner that no longer exists produces no [`EXECUTED_ON`](EXECUTED_ON.md) edge by design (`req-github-core-runner`); the observed runner name stays on this node.

## Neutrality

**Yes.** The corpus marks the executed-job concept neutral. Any CI system has executed units of work, and the kernel pressure test populates them from a non-forge project. The GitHub-specific part is the conclusion vocabulary, which travels as values rather than as structure.

## Observability

Populated from `GET /repos/{o}/{r}/actions/runs/{run_id}/jobs` at **`repository:actions:read`**, one call per run — which makes jobs the most call-expensive thing this plugin collects, and is why the collector degrades per-run rather than failing the batch when a `/jobs` call fails (`req-github-core-collector`).

The response carries the **observed runner** (`runner_id`, `runner_name`, `runner_group_name`), which is the only place execution reveals where it ran. That is retained on this node and matched against collected [`github_runner`](github_runner.md) nodes; a match emits [`EXECUTED_ON`](EXECUTED_ON.md), a non-match does not (`req-github-core-runner`).

**REST only — GitHub's GraphQL API exposes no Actions jobs at all.** Verified by execution. There is no batched alternative to the per-run call.

**Not observable:** the job's effective `permissions:` grant at run time (the declaration is visible in the workflow YAML; the resolved grant is not returned anywhere), and jobs from earlier run attempts without the attempts endpoint.

## Authoritative Source

- **Source:** GitHub REST API — Actions Workflow Jobs (`GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs`)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27 (the GraphQL absence and the latest-attempt default verified by execution)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — the declaration/execution finding, stated as the first of the three findings that shaped the corpus, and the `workflow_job` vs `github_actions_job` note.
- `git-serious-tap/docs/doc-git-serious-vocab-platform-models.md` (2026-08-27) — the platform survey: two published GitHub graph schemas model the declared job only and have no execution node anywhere.
- `git-serious-tap/docs/doc-git-serious-vocab-from-incidents.md` (2026-08-27) — the ~20-of-35 count behind "every privilege decision is made at the declared level".
- GitHub REST API, version `2022-11-28` — Workflow Jobs endpoints.

## Fields

- `full_name` — owning repository, half the natural key.
- `job_id` — GitHub's numeric job id, the other half. Per-execution: a re-run mints new ids.
- `name` — the job's declared name, or its matrix expansion. Display only, and explicitly not identity, because a matrix shares one declared name across many executions.
- `status` — `queued`, `in_progress`, `completed`. Drives incremental refresh alongside the run's status.
- `conclusion` — `success`, `failure`, `cancelled`, `skipped`. Null while in flight — the grid's unobserved convention, not a claim that there was no outcome.
- `created_at` — when GitHub created the job, i.e. when it entered the queue. `started_at − created_at` is the job's queue time — the per-job form of the run's `run_started_at − created_at`, and the number that separates "the runners are saturated" from "the job is slow" (github-core#47). A job with no `created_at` renders as not observed, never as zero.
- `started_at` — when the job began executing.
- `completed_at` — when it finished; null while running. With `started_at`, the duration that makes an anomalously long job visible.
- `html_url` — the browser URL for the job.
- `configuration` — the remainder of the job payload, and the home of the **observed runner** (`runner_id`, `runner_name`, `runner_group_name`) plus step results. The runner fields are the load-bearing part: they are the only execution-side evidence of where a job ran, and the input to [`EXECUTED_ON`](EXECUTED_ON.md) matching.
- `tags` — TAP's tag map.
