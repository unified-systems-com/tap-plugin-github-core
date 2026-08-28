# EXECUTED_ON

## Blurb

An executed job ran on a durable self-hosted runner — emitted **only** when the observation matches a known runner, which makes its absence uninformative by design.

## Purpose

This edge answers the question the incident corpus cares most about at the machine level: what has actually executed on this box. A self-hosted runner is persistent infrastructure inside a network; knowing which jobs — and therefore which repositories, triggers and contributors — reached it is the difference between an inventory and a blast radius.

## Goals

- Attribute an execution to a machine, so a runner's exposure can be enumerated.
- Do it only on evidence, never on inference.
- Keep ephemeral execution out of the node population without losing the observation.

## Identity

Edge id is `uuid5(ns, "edge:EXECUTED_ON__github_core:<source id>:<target id>")`, deterministic from the pair.

## Boundaries

Carries **no properties** — the timing and outcome of the execution are fields on the [`github_actions_job`](github_actions_job.md) source node, and duplicating them here would derive the same fact twice.

The real boundary is **when this edge is emitted at all**. It is emitted only where an observed job's `runner_id` matches a collected [`github_runner`](github_runner.md) node (`req-github-core-runner`). Three distinct situations therefore produce no edge:

1. The job ran on a **GitHub-hosted** runner — correct, there is no durable machine to point at.
2. The job ran on an **ephemeral** self-hosted runner — deliberate; ephemeral runners are not minted as nodes because they exist for one job and nothing can point at them afterwards.
3. The runner **was not collected**, because `GET .../actions/runners` needs `repository:administration:read` and degraded with a 403.

Case 3 is the trap. **Absence of this edge is not evidence that a job ran on GitHub's infrastructure.** The observed runner name and id stay on the job node in `configuration` regardless, so the observation survives even when the edge cannot be drawn — check there before drawing a conclusion.

## Neutrality

**Vendor-specific**, because its target is. [`github_runner`](github_runner.md) is marked `no` in the corpus: the registration model is GitHub Actions' own.

## Observability

Both halves, and they are asymmetric:

- The **observation** comes from `GET /repos/{o}/{r}/actions/runs/{run_id}/jobs` at **`repository:actions:read`** — the job payload carries `runner_id`, `runner_name`, `runner_group_name`.
- The **runner node** comes from `GET /repos/{o}/{r}/actions/runners` at **`repository:administration:read`**, which degrades with a warning on 403 (`req-github-core-collector-5`).

So this edge requires the *union* of a cheap permission and an expensive one. A credential with `actions:read` alone sees every execution and can draw none of these edges — the graph looks like a repository with no self-hosted runners, which is the most reassuring possible reading of a missing permission.

This is the same failure shape the corpus settled empirically for ruleset bypass actors: absence that reads as safety. The corpus's answer there was a mandatory `observable` property so a view can render **none / some / not-observable** as three states. Any view built on this edge owes its reader the same three states.

## Authoritative Source

- **Source:** GitHub REST API — Actions Workflow Jobs (runner fields on the job payload) and Self-hosted runners (`GET /repos/{owner}/{repo}/actions/runners`)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27 (the 403 degradation exercised against a credential without administration scope)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — the `CAN_BYPASS_RULE` ruling that `observable: false` must distinguish "nobody can" from "we cannot see", which is the discipline this edge's absence demands.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §3.9–3.10 (2026-08-27) — the runners endpoint permission level and the self-hosted-runner conditions.
- `specs/spec-github-core-v0.md` `req-github-core-runner` (2026-08-27) — matchable-only emission and the no-ephemeral-runner-nodes rule.
- GitHub REST API, version `2022-11-28` — Workflow Jobs and Self-hosted runners endpoints.

## Endpoints

- **Source:** `github_core__github_actions_job` — the execution that observed a runner.
- **Target:** `github_core__github_runner` — the durable registration.
- **Dimensions:** `github.platform`, `github.surface: actions`, `github.observation: execution`.
