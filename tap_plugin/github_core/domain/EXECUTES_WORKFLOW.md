# EXECUTES_WORKFLOW

## Blurb

A run is an execution of a workflow — the one edge in this plugin that crosses from what was declared to what happened.

## Purpose

This edge is the seam that makes the whole vocabulary worth building. The corpus's first finding is that declaration and execution are different objects and almost nobody models both: the two published GitHub graph schemas surveyed have no execution node at all, and only 8 of 16 platform sources model a run. Spanning both sides is the distinguishing property of this model, and `EXECUTES_WORKFLOW` is the span.

It turns two half-questions into one whole one. "This workflow declares `pull_request_target` with a checkout of the PR head" is a hypothesis; "and it has run 340 times from forks" is a finding.

## Goals

- Join execution to declaration so declared risk can be measured against behaviour.
- Let a run inherit its declaration without copying it.
- Carry `github.observation: execution`, so a query can separate observed facts from declared ones by dimension.

## Identity

Edge id is `uuid5(ns, "edge:EXECUTES_WORKFLOW__github_core:<source id>:<target id>")`. Deterministic, so re-collecting a run does not duplicate the edge.

## Boundaries

Carries **no properties**, and the justification is specific: everything an execution property could say — the attempt number, the event, the head SHA — is already a field on the [`github_actions_run`](github_actions_run.md) node, which is the object the execution *is*. An edge property here would be a second derivation of a fact the source node already owns.

The one property that would genuinely belong to a *relationship* is `run_attempt`, and it belongs to a different edge: the corpus's `INSTANCE_OF_JOB`, bridging an executed job to its declared `workflow_job`. Neither that edge nor `workflow_job` exists yet — see [`github_actions_job`](github_actions_job.md) for why that gap is the largest one in the model.

## Neutrality

**Neutral-capable.** Both endpoints are marked neutral; execution-instantiates-definition is the shape any CI system has, and the kernel pressure test confirms a non-forge project populates it.

## Observability

Derived from the run payload's `workflow_id`, which `GET /repos/{o}/{r}/actions/runs` returns at **`repository:actions:read`** — the same permission and the same call that mints the run, so the edge costs nothing extra.

The resolution is exact, not inferred: the run names the numeric workflow id, and the workflow node's key is `<full_name>#<workflow_id>`, so the target resolves without matching or guessing. That matters — several of this plugin's cross-grid edges *are* inferred and say so; this one is not.

**A gap worth knowing:** a run whose workflow file has since been deleted still resolves to a workflow node if that workflow was ever collected, and dangles if it was not. Runs outlive definitions.

## Authoritative Source

- **Source:** GitHub REST API — Actions Workflow Runs (`GET /repos/{owner}/{repo}/actions/runs`, the `workflow_id` field on each run)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — finding 1, "declaration and execution are different objects, and almost nobody models both"; the 8-of-16 count.
- `git-serious-tap/docs/doc-git-serious-vocab-platform-models.md` (2026-08-27) — the two published GitHub graph schemas with no execution node anywhere.
- GitHub REST API, version `2022-11-28` — Workflow Runs endpoints.

## Endpoints

- **Source:** `github_core__github_actions_run` — the execution.
- **Target:** `github_core__github_workflow` — the declaration.
- **Dimensions:** `github.platform`, `github.surface: actions`, `github.observation: execution`. The observation dimension is the machine-legible marker that this edge's source is an observed event rather than a declared structure.
