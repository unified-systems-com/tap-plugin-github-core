# HAS_ACTIONS_JOB

## Blurb

A run contains an executed job — containment within a single execution.

## Purpose

A run is the unit a human reasons about; a job is the unit that actually holds a machine, a token and a set of steps. This edge lets analysis descend from one to the other: from "this run was triggered by a fork" to "and this job inside it ran on a self-hosted runner".

It is also the path by which a run reaches [`EXECUTED_ON`](EXECUTED_ON.md), since it is the job, not the run, that observes a runner.

## Goals

- Descend from execution to executed unit.
- Give the run→runner question a two-hop path that stays honest about which object observed what.
- Keep job containment explicit rather than implied by a shared key prefix.

## Identity

Edge id is `uuid5(ns, "edge:HAS_ACTIONS_JOB__github_core:<source id>:<target id>")`, deterministic from the pair.

## Boundaries

Carries **no properties**. A job belongs to exactly one run, unconditionally.

The boundary that matters is **attempts**. v0 reflects the **latest-attempt job set** only (`req-github-core-collector-8`): the default `/jobs` endpoint returns the latest attempt's jobs, `/attempts/{n}/jobs` is backlog (`req-github-core-backlog-run-attempts`), and the run's natural key omits `run_attempt`. So on a re-run this edge set is *replaced*, not extended. The subtle case the backlog entry names is "re-run failed jobs", where a second attempt re-runs only part of the run — there, the latest-attempt set is genuinely not the whole story, and the graph will not say so.

Also not covered: the **declared** job. This edge reaches an execution. The declaration — `permissions:`, `runs-on:`, `if:` — is the corpus's unbuilt `workflow_job`, reached by a different edge (`DEFINES_JOB`) from the workflow. See [`github_actions_job`](github_actions_job.md).

## Neutrality

**Neutral-capable.** Run-contains-job is the shape any CI system has; both endpoints are marked neutral in the corpus.

## Observability

Derived from `GET /repos/{o}/{r}/actions/runs/{run_id}/jobs` at **`repository:actions:read`** — one call per run, which makes this the most call-expensive relationship the plugin collects. The collector degrades per-run rather than failing the batch when a `/jobs` call fails (`req-github-core-collector`), so **a run with no job edges may mean a failed call, not a run with no jobs.** The run's warnings distinguish them.

**REST only.** GitHub's GraphQL API exposes no Actions jobs, so there is no batched alternative to the per-run call. Verified by execution.

## Authoritative Source

- **Source:** GitHub REST API — Actions Workflow Jobs (`GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs`)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27 (the latest-attempt default and the GraphQL absence verified by execution)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `HAS_ACTIONS_JOB` in the existing spine; `INSTANCE_OF_JOB` with `{run_attempt}` recorded as the declaration↔execution bridge this edge is not.
- `specs/spec-github-core-v0.md` `req-github-core-backlog-run-attempts` (2026-08-27) — the multi-attempt gap and the re-run-failed-jobs subtlety.
- GitHub REST API, version `2022-11-28` — Workflow Jobs endpoints.

## Endpoints

- **Source:** `github_core__github_actions_run`.
- **Target:** `github_core__github_actions_job`.
- **Dimensions:** `github.platform`, `github.surface: actions`, `github.observation: execution`.
