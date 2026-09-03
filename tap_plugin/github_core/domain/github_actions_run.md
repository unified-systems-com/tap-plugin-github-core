# GitHub Actions Run

## Blurb

One execution of a workflow — what actually happened, on which commit, triggered by what event.

## Purpose

Only 8 of the 16 sources surveyed in the platform pass model a pipeline run at all. That is the finding, not a footnote: most of the field models the *declaration* and stops. Modelling the run is what lets a question move from "could this happen" to "did it, and how often, and from where".

The run carries the fields that make a declared risk concrete: `event` (was this a `pull_request_target`?), `head_branch` and `head_sha` (what code ran), and — in the collected payload — whether the head repository was a fork. A workflow that *could* run untrusted code is a finding; a run that *did* is an incident.

## Goals

- Record executions so declared risk can be measured against actual behaviour.
- Carry the trigger event and head commit, the two properties that decide whether an execution was trusted.
- Join execution back to declaration via [`EXECUTES_WORKFLOW`](EXECUTES_WORKFLOW.md), and forward to its jobs via [`HAS_ACTIONS_JOB`](HAS_ACTIONS_JOB.md).

## Identity

Natural key: **`<full_name>#<run_id>`** — repository plus GitHub's numeric run id. Entity id is `uuid5(ns, "github_core__github_actions_run:<full_name>#<run_id>")`.

**`run_attempt` is deliberately not in the key in v0** (`req-github-core-backlog-run-attempts`). A re-run therefore updates the existing node rather than creating a sibling — the `run_attempt` column moves, and the grid's field history is what records that the conclusion changed. This is a known simplification with a named cost: the "re-run failed jobs" case, where a second attempt re-runs only part of the run, is not faithfully represented. Changing the key later is a re-collect, not a migration, because entity ids are derived rather than stored — which is why the simplification was affordable.

## Boundaries

Deliberately **not** covered:

- **Multiple attempts.** See Identity. `run_attempt` is stored on the node but does not fan out into separate nodes.
- **Logs.** Job logs are reachable at `actions:read` and are a real source (runner names in logs are the basis for non-ephemeral-runner heuristics), but they are not collected. A log is evidence, not an entity.
- **Artifacts, as fields.** Built 2026-09-02: what a run uploaded is an [`actions_artifact`](actions_artifact.md) reached by [`UPLOADS_ARTIFACT`](UPLOADS_ARTIFACT.md) when the run is in the batch. Nothing about artifacts is duplicated onto this node.
- **The package a run produced.** `BUILDS_PACKAGE_VERSION` is a corpus edge at the *friends* tier, and the corpus notes that **its absence is the finding**: a registry version with no run behind it is how five incidents read. `package_version` was ruled into a future `supply_chain_core`, not here.
- **The approving human.** Approval of a fork PR run is an event, not a field on the run; the corpus rules `review` to be an edge rather than a node.

## Neutrality

**Yes.** The corpus marks the run concept neutral: any CI system has executions, and the kernel pressure test populates one from a non-forge project. The GitHub-specific parts are the trigger-event vocabulary and the `head_repository` fork semantics, which would travel as properties rather than as structure.

## Observability

Populated from `GET /repos/{o}/{r}/actions/runs` at **`repository:actions:read`**, paginated, with an initial run limit and incremental refresh thereafter (`req-github-core-collector`). Non-terminal runs are refreshed on later collections rather than frozen at first sight.

**The end time is derived, not read.** The run payload carries no `completed_at`, and its `updated_at` is not one: it moves on re-run, on artifact and log events and on check-suite updates, so a run that was re-run a day later would read as having taken a day. The collector fetches the run's jobs first and sets `completed_at` to the latest `completed_at` over them, recording which of three states applied in `configuration.completed_at_source` (github-core#46):

- `jobs` — derived: `max(job.completed_at)` over the collected latest-attempt jobs. A measurement.
- `updated_at` — approximated: the run is complete but its end was not observable from the jobs — the `/jobs` call degraded, the run has no jobs (a skipped run), or the listing still carried a job without a usable end (it is eventually consistent, and a maximum over the jobs that *have* finished would understate the run). An upper bound, labelled as one.
- `in_flight` — absent: the run has not reached `completed`, so `completed_at` is null. A partial maximum over the jobs that have finished would be a lie.

Because the node is the latest-attempt snapshot (see Identity), a re-run in progress moves the node back to `in_flight` and, once complete, to that attempt's end — the earlier attempt's duration lives in field history, not on the node.

The endpoint yields more than this node keeps as columns. `created_at`, `run_attempt` and the two actors' logins earned columns in github-core#47 (queue time and re-run awareness need them queryable); `head_repository` (which is how a fork run is recognised) and `referenced_workflows` (the resolved SHAs of reusable workflows a run pulled in, one of the few places GitHub hands over a resolved reference rather than a declared one) are **not yet stored** — `configuration.raw_payload_keys` records that the payload carried them, and nothing more. A key listed there proves the API returned it, not that the node kept it.

Queue time is `run_started_at − created_at`: the one number that says "the runners are saturated" rather than "the job is slow". A run with no `created_at` renders as not observed, never as zero.

**REST only.** GitHub's GraphQL API exposes **no** Actions runs or jobs. If a collector needs runs, it needs REST; there is no batching escape via GraphQL. This was verified by execution, and it is the single most useful thing to know before designing a collection strategy for this domain.

**Not observable:** the effective permissions the run's token actually carried, and anything about a run older than the retention window. The audit log, which would carry the surrounding events, is GitHub Enterprise Cloud only.

## Authoritative Source

- **Source:** GitHub REST API — Actions Workflow Runs (`GET /repos/{owner}/{repo}/actions/runs`)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27 (the GraphQL absence verified by execution, not read from documentation)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — records that only 8 of 16 surveyed sources model a run at all, which makes spanning declaration and execution the distinguishing property of this vocabulary.
- `git-serious-tap/docs/doc-git-serious-vocab-platform-models.md` (2026-08-27) — the platform survey behind that count; the two published GitHub graph schemas have no execution node anywhere.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §3.9–3.10 (2026-08-27) — the runs endpoint's yielded fields, and the fork-run observable condition.
- GitHub REST API, version `2022-11-28` — Workflow Runs endpoints.

## Fields

- `full_name` — owning repository, half the natural key.
- `run_id` — GitHub's numeric run id, the other half. Required alongside `full_name` at creation.
- `run_number` — the per-workflow counter a human reads ("run #431"). Display and ordering; not identity, because it is only unique within one workflow.
- `event` — the trigger that fired: `push`, `pull_request`, `pull_request_target`, `workflow_run`, `schedule`, and so on. The single most analytically important field on this node — several of the highest-evidence conditions in the incident corpus are conjunctions that begin with the event.
- `status` — lifecycle position: `queued`, `in_progress`, `completed`. Drives incremental refresh: a non-terminal run is re-fetched, a terminal one is not.
- `conclusion` — the outcome once complete: `success`, `failure`, `cancelled`, `skipped`. Null while the run is still in flight, which is the grid's "unobserved" convention rather than a claim of no outcome.
- `head_sha` — the commit that ran. What joins an execution to code, and the anchor for "did the artifact come from the tagged tree".
- `head_branch` — the ref that ran. With `event`, decides whether the execution crossed a trust boundary.
- `created_at` — when GitHub created the run, i.e. when the trigger fired. The start of the queue: `run_started_at − created_at` is how long the run waited for a runner. Indexed, because it is the natural ordering key for "what happened when" and the value the reliability history sorts on.
- `run_started_at` — when execution began. Distinct from the run's creation, which matters for queue-time and for ordering against other observations.
- `completed_at` — when it finished; null while running. Derived from the run's jobs, not from the payload's `updated_at` — see Observability for the three states and the `completed_at_source` label beside it.
- `run_attempt` — which attempt this snapshot is: `1` for a first run, higher after a re-run. The node keys on the run id alone (see Identity), so this is how a reader tells a re-run from a run — and how a per-run strip avoids counting one workflow execution twice.
- `actor_login` — the login of the account the run is attributed to (GitHub's `actor`): the author of the push, the opener of the pull request. The login alone, not the user object — the login is the join key, and the avatar URLs and node ids are not.
- `triggering_actor_login` — the login of the account whose action actually started this attempt (GitHub's `triggering_actor`). Differs from `actor_login` when someone else re-runs a workflow or approves a fork run — which is exactly the case worth seeing. Both are `""` when the payload named nobody (observed-empty), never null.
- `html_url` — the browser URL for the run.
- `configuration` — `workflow_id`, `raw_payload_keys` (every key the API returned, whether or not the node kept it) and `completed_at_source` (`jobs` / `updated_at` / `in_flight` — how `completed_at` was established). `head_repository` and `referenced_workflows` are not stored yet; a key in `raw_payload_keys` proves the API returned it, not that the node kept it.
- `tags` — TAP's tag map.
