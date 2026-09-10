# GitHub Core — The Workflow Page

> **Draft, 2026-09-09.** Written with the code, from the double-tap-git-serious session's walkthrough
> of tap's own CI (tap-plugin-github-core#93; George, 2026-09-09: "let's build that page and visualization now, so that
> github_core has a generic workflow and job viewer that includes the artifact types / stacks").
> Every claim is **documented** (drawn from the tap_viz specs and the collector's models) unless
> marked *observed* (seen on the running instance).

## Philosophy

The machinery projection (spec-github-core-machinery-projection.md) draws a repository's CI system
as a shape: workflows as boxes of job cards, sources on one side, outputs on the other. It answers
"what is this repository's machinery?" and stops one level above the question a person asks next:
**"what does this one workflow actually do?"** That question is about jobs, the order they run in,
the steps inside them, and what each of them leaves behind. It is the level at which a pinned action,
a slow job or an unexplained artifact is found.

This page is that level, for one workflow, and it is generic: the same page reads any workflow in
any collected repository. The page names nothing; `?workflow_id=<GitHub numeric workflow id>` selects
the workflow, and every search takes it as its one required input.

Two ideas carry it:

1. **The file is the truth for structure; the runs are the truth for what happened.** Jobs, their
   `needs:`, their steps and the artifact kinds their upload steps declare come from the collected
   workflow file. Counts come from the runs in the collected window. Where the two disagree the page
   says so, rather than letting either speak for the other.
2. **Three states, never two** (req-github-core-machinery-honesty, applied here). An artifact kind is
   *declared and observed*, *declared but never observed in the window*, or *observed but undeclared
   by any upload step* — each drawn differently, none silently dropped or silently green.

Artifacts left the machinery view when this page arrived (2026-09-09): at the machinery altitude they
were noise; here they are the point.

## Goals

| # | Name | Description |
| :---: | --- | --- |
| 1 | One workflow, one screen | Identity, anatomy and recent runs of a single workflow on one page, reachable by URL from any list of workflows. |
| 2 | Anatomy, not a list | Jobs ranked by `needs:` with their steps inside — the shape GitHub's own run page never draws. |
| 3 | Artifacts belong to the job that makes them | Piles per declared kind on the declaring job, counted from the runs; undeclared kinds and unfulfilled declarations both visible. |
| 4 | Generic | No repository, workflow or job name in the bundle or the module; the page pins nothing. |

## Prior Art

GitHub's run summary renders a job graph for one **run** (needs as arrows, no steps until you open a
job, artifacts as a flat list at the bottom); it draws nothing for the workflow as a file. Buildkite
and CircleCI draw pipelines as DAGs of steps per run. The gap this page fills is the *static* shape of
the workflow with the *aggregate* of its runs painted on: what it always is, and what it lately did.

## Requirement Status

| RID | Name | Status | Summary |
| --- | --- | :---: | --- |
| req-github-core-workflow-page | [The Page](#the-page) | Implemented | `/github_core/workflow?workflow_id=…` — identity, anatomy, runs; not in the top nav; every search takes the one input |
| req-github-core-workflow-anatomy | [The Anatomy Module](#the-anatomy-module) | Implemented | `workflow-anatomy.js`: jobs ranked over needs, steps inside jobs, reusable-call jobs marked, unresolved needs to a trailing column |
| req-github-core-workflow-artifact-kinds | [Artifact Kinds](#artifact-kinds) | Implemented | kinds from upload steps' `with.name` templates, matched to observed artifacts, piles per kind in three states |

## Requirements

### The Page
----
RID: `req-github-core-workflow-page`
Status: `Implemented`

Route `/github_core/workflow`, not discoverable in the top navigation (the machinery view and the
repository page are the ways in). One input on the URL: `workflow_id`, GitHub's numeric id for the
workflow. It is the key because it is the one value every node that should reach this page carries —
`github_workflow.workflow_id`, `workflow_job.workflow_id` and `github_actions_run.configuration.workflow_id`
— and it is exact. The v0.1.0 contract, `?repo=<full_name>&workflow=<file>` matched by `CONTAINS`, failed
on both counts: a run carries no workflow path, so a runs table could not link here, and a bare
`fuzz.yml` matched every file ending in it. Three rows, top to bottom:

| Slot | Panel | What it answers |
| --- | --- | --- |
| `identity` | table panel over the workflow node | which file, its state, what starts it, the permissions it grants, the link to GitHub |
| `anatomy` | graph panel with the anatomy projection | what the workflow does — see the next requirement |
| `runs` | table panel over the workflow's runs, newest first | what it lately did — trigger, run number, branch, conclusion, elapsed, started |
The page mounts the panels by `USES_PANEL` edge; a consumer page may instead mount the same panels
with fixed inputs (tap#359) to pin a workflow.

#### Implementation

`tap_plugin/github_core/grift/workflow-page.grift.json` — one batch: the page node, three panels, four
searches (the workflow by id; the anatomy scene `workflow —DEFINES_JOB→ job`; the job
dependencies `job —DEPENDS_ON_JOB→ job` scoped to the workflow; the runs `run —EXECUTES_WORKFLOW→
workflow`), and the projection with its elevation and layout. Registered in the manifest's `[grift]`
table as `workflow-page`. Structural test: `tests/test_workflow_page_bundle.py`.

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-workflow-page-1 | Selects One Workflow | Implemented | Every search declares `workflow_id` as its one required input, typed `integer`, and reads it; with it given, the identity row holds exactly the matching workflow node. | `test_every_search_requires_and_reads_workflow_id_as_an_integer`. Typed integer because page inputs arrive as strings and are coerced by the search's own schema. |
| req-github-core-workflow-page-2 | Identity Row | Implemented | Name (linking to GitHub), file, state, triggers, top-level permissions. | |
| req-github-core-workflow-page-3 | Runs Newest First | In Development | The runs table lists this workflow's collected runs by `run_started_at` descending with trigger icon, run link, branch, conclusion, elapsed, started. | The rows render and sort by header; newest-first on arrival waits on tap#372 (table `initial_sort`) — Gryphon refuses ORDER BY on the multi-hop match and run nodes carry only `workflow_id`. Column config restates git-serious's status wall subset. |
| req-github-core-workflow-page-4 | Not In The Nav, Reachable By URL | Implemented | `discoverable: false`; `GET /github_core/workflow?workflow_id=<n>` returns 200 with the three slots. | *Observed* 2026-09-09 on the 8010 grid under the v0.1.0 contract; re-observation under `workflow_id` pending on the demo-dev grid. |

### The Anatomy Module
----
RID: `req-github-core-workflow-anatomy`
Status: `Implemented`

A standard tap layout module at
`tap_plugin/github_core/static/github_core/js/projections/workflow-anatomy.js` exporting
`async function execute(context)`, owned by the projection `github_core workflow anatomy projection`
(one elevation, one layout). Configuration under `projection.definition.anatomy`: `flow` (`ltr`
default — a single workflow reads in execution order — or `rtl`), `column_gap`, `row_gap`; unknown
keys warn.

#### Implementation

- **Input.** The workflow is whatever `github_workflow` node the page's searches placed in the scene,
  with the jobs its `DEFINES_JOB` edges reach. No workflow: `anatomy_no_workflow` warning, nothing
  positioned. Several: each laid out as its own box.
- **Facts.** Configuration (jobs, steps, `needs`, `uses`, `if`), the jobs' `job_key`s and the runs'
  artifacts are fetched through `/api/v1/gryphon/execute` — the cy data carries spine fields only.
- **Ranking.** Rank = longest `needs:` path from a root, computed from the file's `jobs[].needs`
  against the job cards in the scene. A job whose need is not a job in the scene, or that sits on a
  cycle, is placed in a trailing *unresolved* column with a dashed red border and an
  `anatomy_unresolved_needs` warning — never at rank 0.
- **Labels.** Jobs display `job_key`, never `name` (matrix names carry `${{ matrix.* }}`); `name`
  is kept on the node for the info window.
- **Steps.** One node per step inside its job, in file order: an action step shows the action
  without the `actions/` prefix and the first seven characters of its pin; a `run:` step shows its
  first line; a job that is itself a call to a reusable workflow shows one italic `uses …` node.
  Upload-artifact steps are marked (amber) because they declare kinds.
- **Nesting.** `workflow ⊃ job ⊃ {step, pile}` and `workflow ⊃ undeclared pile` through
  `projectNested`; jobs by rank across (the ranked layout), steps then piles down inside a job (the
  flow layout at a tall aspect). `DEPENDS_ON_JOB` edges remain visible as arrows; containment edges
  are synthetic and hidden.
- **Generic by grep.** No repository, workflow or job name in the module.

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-workflow-anatomy-1 | Jobs Ranked Over Needs | Implemented | For a workflow whose file declares `needs`, a job's column is one more than the maximum of its needs' columns; roots are in column 0. | *Observed*: product-lines — setup/secret-scan/dco/rids at 0, line/cold-boot/lean-boot/api-fuzz at 1, gate at 2. |
| req-github-core-workflow-anatomy-2 | Steps Inside Jobs | Implemented | Each job box lists its steps in file order with action and pin; a reusable-call job shows its `uses` target. | |
| req-github-core-workflow-anatomy-3 | Unknowns Render As Unknowns | Implemented | An unresolvable need places the job in the trailing column with a warning; it never renders at rank 0. | |
| req-github-core-workflow-anatomy-4 | Generic By Grep | Implemented | The module source contains no repository, workflow or job name. | `test_layout_module_is_generic_by_grep` |

### Artifact Kinds
----
RID: `req-github-core-workflow-artifact-kinds`
Status: `Implemented`

An **artifact kind** is what an upload step declares: the `with.name` of a step whose `uses` is the
upload-artifact action, which may be a template (`digest-${{ matrix.image }}-${{ matrix.arch }}`). The
kind belongs to the job that declares it. Observed artifacts (the runs' `UPLOADS_ARTIFACT` targets in
the collected window) are matched to declared kinds by turning each template into a pattern in which
an expression matches anything and the rest is literal.

Three states, drawn differently, none silent:

| State | Where | How |
| --- | --- | --- |
| declared and observed | a pile on the declaring job, below its steps | the kind's name as the face, the count as the chip; click opens the latest run |
| declared, none observed | the same place | a dashed, empty pile: *`<kind>` — none in the window* |
| observed, undeclared | a pile on the workflow itself, in a column after the jobs | dotted violet: *`<folded name>` — no upload step declares it*, with an `anatomy_undeclared_artifact` warning |

Undeclared names are folded (long hex, 4+ character upper/digit tokens and numbers replaced) so that a
family of generated names is one pile. Artifacts whose run is outside the collected window are not on
the grid and are not counted; the collector's window, not this page, decides that.

#### Implementation

`_addSteps` collects declarations while synthesising steps; `_addPiles` matches and adds the pile
nodes and their containment edges; `_stackPiles` builds each pile after the nesting pass — the
laid-out node is the face, the other artifacts of the kind are cards added afterwards so the job box
did not grow for them — and `applyStack` (spec-viz-stack.md) draws the count.

**Derivation lives client-side for now.** The kinds are derived from collected data on every render.
Stamping each artifact with its declared kind at collection time (`actions_artifact.kind`, the step
that declared it, and a `declared_kinds` list on the workflow) is the collector follow-on that would
make this a grid fact rather than a page derivation; file it when a second consumer wants the kinds.

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-workflow-artifact-kinds-1 | Declared From Upload Steps | Implemented | Every `upload-artifact` step yields exactly one kind on its job, named by its `with.name` template. | |
| req-github-core-workflow-artifact-kinds-2 | Counted From Runs | Implemented | A kind's chip equals the number of collected artifacts whose name matches its template. | *Observed*: product-lines `schemathesis-log` 21. |
| req-github-core-workflow-artifact-kinds-3 | Three States | Implemented | Declared-only piles are dashed and empty; undeclared observed piles sit on the workflow, dotted, with a warning. | |
| req-github-core-workflow-artifact-kinds-4 | Left The Machinery View | Implemented | The machinery projection and its consumers draw no artifact piles; this page is where they live. | git-serious-double-tap PR# 15 removes them from the tap lanes layout. |

---

## Non-Goals (v0)

- Painting a chosen run onto the anatomy (which steps ran, how long each took) — the machinery spec's
  second phase, shared.
- Steps' pins verified against the action's tags (the zizmor / pinning findings attach here later).
- A workflow-level elevation inside the machinery projection (double-tap from a workflow box to this
  page's projection) — a navigation rule on the machinery panel once url templates exist.
