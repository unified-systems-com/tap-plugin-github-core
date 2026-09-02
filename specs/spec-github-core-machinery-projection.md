# GitHub Core — The Machinery Projection

> **Draft, 2026-09-02.** Written before code, per the add-panel skill's spec-first rule, from the
> viz-git-serious session's decisions (git-serious-tap#35 is the decision record; tap-plugin-github-core#28
> is the work item). Every claim below is **documented** (drawn from the tap_viz specs and the vocabulary
> corpus) unless marked *observed* (seen on the running instance) or *inferred*.

## Philosophy

Every tool that overlays GitHub shows the system as a list: runs newest first, repositories in a
table, jobs as rows under a run. None of them shows the *machinery* — the thing a push enters on one
side and an artifact leaves on the other — as a shape you can point at. This projection draws that
shape for one repository, from data github_core already collects, and it draws it generically: the
same module lays out any GitHub repository's CI system on first sight, and nothing in it may know a
repository, workflow or job by name.

Three ideas carry the picture:

1. **Nesting is the legibility.** Everything sits inside a box labelled `github.com`, and inside the
   repository's own box. A workflow is a box that contains its jobs, the way a pipeline contains
   steps. You know what you are looking at because of what it is inside.
2. **The stage axis is a derived integer, and direction is a sign.** Sources sit at one end,
   outputs at the other, and the rank of everything between is computed from edges on the grid.
   Sources are on the RIGHT by default; a projection parameter flips the sign. "Do it the opposite
   direction" is a field, not a rewrite.
3. **An unknown never renders as a known.** A job whose dependency cannot be resolved is placed in
   an *unresolved* row with a warning, never silently at rank 0. An output type the collector does
   not yet observe renders as *not yet collected*, never as an empty column that reads as "this
   pipeline produces nothing".

The static machinery comes first. Runs and executed jobs are painted onto the same machinery in a
second phase, and the same projection then replays a chosen run.

## Goals

| # | Goal | Description |
| --- | --- | --- |
| 1. | Generic | One module lays out any GitHub repository's CI system; the repository is an input, never a constant. |
| 2. | Nested | `github.com ⊃ account ⊃ repository ⊃ workflow ⊃ job`; further containment is added as it appears. |
| 3. | Directional | Stage rank is an integer; `flow` chooses the sign. Default: sources right, outputs left. |
| 4. | Honest | Unresolved ranks and unobserved output types are rendered as such (three states, never two). |
| 5. | Consumer-owned instances | github_core ships the module; the consuming plugin seeds the projection, searches, page and panel. |
| 6. | Live-ready | The static scene is the substrate the run layer paints onto; nothing in it forecloses that. |

## Roadmap Alignment

Governing step: `step-products-git-serious-self` in `plan/road-products.md` (tap core). Pulled by
git-serious-tap#6 (CI/CD projection page) via git-serious-tap#35. Serves the product spec's
"present the pages" clause and is the representation that *The Shape of a Pipeline* §6 describes in
prose (`git-serious-tap/docs/doc-git-serious-shape-of-a-pipeline.md`).

## Prior Art

- tap_viz runtime contracts: `spec-viz-layouts.md` (`req-viz-layout-module-contract`,
  `req-viz-layout-runtime-context`), `spec-viz-projection.md` (projection / elevation structure),
  `spec-viz-nested-projection.md` (`projectNested`, natural sizing, inner layouts),
  `spec-viz-stack.md` (deck collapse), `spec-viz-badges.md` (status badge sets).
- The git-serious landing module (`git-serious-tap`, `static/git_serious/js/projections/landing.js`)
  — the first `projectNested` scene over github_core types; this projection reuses its base sizes and
  edge-label silencing and departs from it on the stage axis and the tiers.
- The vocabulary corpus (`spec-github-core-vocabulary.md`) names every concept this projection
  cannot yet draw: `CALLS_WORKFLOW` (line 128), `INSTANCE_OF_JOB` (line 120), `github_release`
  (line 86), `actions_artifact` (line 94), `package_version` → `supply_chain_core` (lines 96, 200).

## Requirement Status

| RID | Name | Status | Notes |
| --- | --- | :---: | --- |
| req-github-core-machinery-module | [The Layout Module](#the-layout-module) | In Development | `static/github_core/js/projections/machinery.js`; standard tap layout contract; repository is an input |
| req-github-core-machinery-nesting | [Containment](#containment) | In Development | `github.com ⊃ account ⊃ repository ⊃ workflow ⊃ job`; single-hop patterns force the account frame |
| req-github-core-machinery-stages | [Stage Ranking](#stage-ranking) | In Development | sources → pipelines → outputs; job rank = longest path over `DEPENDS_ON_JOB`; unresolved is a state |
| req-github-core-machinery-flow | [Direction](#direction) | In Development | `flow: "rtl"` (default) or `"ltr"` — a sign on the stage axis |
| req-github-core-machinery-tiers | [Tiers Outside The Repository](#tiers-outside-the-repository) | In Development | third parties top-centre; outputs to humans bottom-centre (reserved) |
| req-github-core-machinery-honesty | [Unknowns Render As Unknowns](#unknowns-render-as-unknowns) | In Development | unresolved ranks; "not yet collected" output placeholder |
| req-github-core-machinery-consumer | [Consumer Contract](#consumer-contract) | In Development | what the consuming plugin's searches must put in the scene; git-serious is the first consumer |
| req-github-core-machinery-live | [The Live Layer](#the-live-layer) | Proposed | runs and executed jobs painted onto the machinery; needs `INSTANCE_OF_JOB` (#30) |

## Requirements

### The Layout Module
----
RID: `req-github-core-machinery-module`
Status: `In Development`

A standard tap layout module at `tap_plugin/github_core/static/github_core/js/projections/machinery.js`
exporting `async function execute(context)` (`req-viz-layout-module-contract`), which lays out the
CI system of ONE repository present in the scene as nested machinery.

#### Implementation

- **Input.** The repository is whatever `github_repository` node the consumer's searches placed in
  the scene. The module never names one. If the scene holds no repository node the module warns
  (`machinery_no_repository`) and returns without positioning anything; if it holds more than one,
  each is laid out as its own box side by side along the cross axis (v0 accepts this; the org-level
  machinery view is out of scope).
- **Configuration** is read from `context.projection.definition.machinery` (object, optional). Keys:
  `flow` ("rtl" | "ltr", default "rtl"), `column_gap`, `row_gap` (numbers, px), `stack_refs_over`
  (integer, default 3 — see Stage Ranking, sources). Unknown keys are ignored with a warning.
- **Sizes.** Leaf and floor sizes per entity type follow the landing module's `BASE_SIZES`
  convention (`spec-viz-nested-projection.md`, natural sizing); `workflow_job` gets a card wide
  enough for a `job_key`.
- **Labels.** Jobs display `job_key`, never `name`: matrix jobs carry `${{ matrix.image }}` in
  `name` (*observed*: `publish-images`, `trivy-nightly`), which would render as broken text. `name`
  stays available on hover / in the info window.
- **Edge labels are silenced** at this altitude (as the landing module does); containment carries
  the structure and the remaining free-standing edges (dependencies, protection, enablement) mean
  what their line means.
- **Nothing in the module is repository-specific.** A grep for `unified-systems-com`, `tap`,
  `product-lines` or any job key in the module is a defect.
- **Facts the cy data does not carry.** panel-graph copies spine fields only (label, entity_type,
  icon, dimensions, tags) and an edge's type onto `label`; the module fetches `job_key`, `needs`,
  `permissions`, `configuration` and ref kinds through `/api/v1/gryphon/execute` (one type-scan
  per type, `WHERE x.data.full_name = $repo`) and matches edges by `edge_type` OR `label`.
- **Rendering notes (observed 2026-09-02):** the pipelines column uses the ranked layout's flowed
  columns (`columnLayout: "flow"`, tap#293) so seventeen workflow boxes read as trigger-class rows
  rather than a tower; workflows with no declared jobs render as empty boxes; container labels
  sit on the box edge. Known rough edges, not defects.

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-machinery-module-1 | Lays Out Our Own Repository | Implemented | On an instance observing unified-systems-com with `unified-systems-com/tap` in the scene, the module renders `github.com ⊃ account ⊃ tap ⊃ 17 workflow boxes ⊃ their jobs` with no node outside the outer box. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. 17 workflow boxes, 30 jobs, nothing outside github.com. |
| req-github-core-machinery-module-2 | No Repository, No Crash | In Development | A scene with no `github_repository` node produces the `machinery_no_repository` warning and no exception. | |
| req-github-core-machinery-module-3 | Generic By Grep | In Development | The module source contains no repository, workflow or job name. | A guard-shaped ACID; cheap to enforce in plugin CI later. |

---
### Containment
----
RID: `req-github-core-machinery-nesting`
Status: `In Development`

Structure is drawn as containment, not as edges, via `projectNested`
(`tap_viz/static/tap_viz/js/runtime/nested-projection.js`).

#### Implementation

Relationships, in order:

| Parent | Edge | Child | Note |
| --- | --- | --- | --- |
| `github_platform` | `HOSTS_ACCOUNT__github_core` | `github_account` | The outer box, labelled `github.com`. |
| `github_account` | `OWNS_REPO__github_core` | `github_repository` | Present because nesting patterns are single-hop (`nesting.js:18`); drawn as a thin frame so the eye reads two boxes, github.com and the repository. Collapsing it is a later chrome option, not a structural change. |
| `github_repository` | `DEFINES_WORKFLOW__github_core` | `github_workflow` | A workflow is a box. |
| `github_workflow` | `DEFINES_JOB__github_core` | `workflow_job` | A job is a card inside its workflow: the pipeline contains its steps. |

Candidates for further containment, to be taken as they appear rather than designed now: steps
inside a job (not modelled; the corpus keeps steps as properties), environments as boxes around the
jobs that deploy to them (an environment is a target, not a container — *inferred*: no), a run as a
box around its executed jobs (the live layer decides).

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-machinery-nesting-1 | Every Job Has One Workflow | Implemented | Every `workflow_job` in the scene has `data.parent` set to exactly one `github_workflow`, and every workflow to exactly one repository. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. Probe: 30/30 jobs parented, 17/17 workflows parented. |
| req-github-core-machinery-nesting-2 | Nothing Outside github.com | Implemented | Every non-badge node in the scene is a descendant of the `github_platform` node. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. Dependabot on the top tier inside github.com. |

---
### Stage Ranking
----
RID: `req-github-core-machinery-stages`
Status: `In Development`

Inside the repository box, x is a stage; inside a workflow box, x is a dependency rank. Both are
integers derived from the grid and placed by the `ranked` inner layout (tap#293); until that lands
the module positions the children itself after `projectNested` returns, and that fallback is
deleted when the core layout merges (derive-a-fact-once: one placement implementation).

#### Implementation

**Stages inside the repository box** (stage 0 is the source end):

| Stage | What | Derived from |
| --- | --- | --- |
| 0 — sources | The refs that enter the machinery: the default branch, tag refs, and any ref a workflow trigger names. Other branches collapse into one deck via `stack.js` above `stack_refs_over` (count chip carries the true cardinality). Rulesets sit beside the refs they protect. | `HAS_REF__github_core`, `PROTECTS__github_core`, `workflow.configuration.triggers` |
| 1 — pipelines | Workflow boxes. Rows within the column are grouped by trigger class in a fixed order: `pull_request`, `push`, `workflow_run`, `schedule`, `workflow_dispatch`, `workflow_call`. A workflow with several triggers takes the first in that order; a workflow with none goes to an *untriggered* row. | `workflow.configuration.triggers` (*observed* on the grid) |
| 2 — outputs | Environments the jobs deploy to; releases, artifacts and packages once their rows exist (#31). Jobs whose EFFECTIVE permissions grant `packages: write`, `contents: write` or `id-token: write` are marked *producer* in place — a derived proxy, never a fabricated artifact node. Effective = the job's own `permissions` block when present, else the workflow-level block (`configuration.workflow_permissions`); `write-all` grants every scope. When NEITHER is declared the repository default applies, and that default is not on the grid — the job's producer state is then *not observable*, rendered as such, never as "not a producer" (*inferred* that the collector stores declared, not resolved, permissions; settle by inspecting the collector before implementing — a `write-all` fixture must mark every job). | `HAS_ENVIRONMENT__github_core`, `workflow_job.environment`, `workflow_job.permissions`, `workflow.configuration.workflow_permissions` |

**Rank inside a workflow box.** `rank(job) = 0` if `needs` is empty, else `1 + max(rank(needed))`
over `DEPENDS_ON_JOB__github_core` — the longest path from a root. Ties stack vertically in
declaration order (`DEFINES_JOB.order` when present, else `job_key`). *Observed* shape for
`product-lines`: `setup`, `secret-scan` at 0; `line`, `cold-boot`, `lean-boot`, `api-fuzz`, `rids` at
1; `gate` at 2.

**Unresolved.** A job is unresolved when ANY job in its transitive `needs` closure is absent from
the scene or sits on a dependency cycle (`resolveNesting`-style cycle detection applied to the job
graph) — unresolvedness propagates downstream, so a job that depends on a cyclic job is unresolved
too. Unresolved jobs are placed in the box's *unresolved* row with warning
`machinery_unresolved_rank` naming the job and the root cause (the absent or cyclic job). They are
never placed at rank 0. Fixture: `A needs B`, `B needs A`, `C needs A` — all three are unresolved.

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-machinery-stages-1 | Three Ranks For The Gate | Implemented | `product-lines` renders three columns: {setup, secret-scan}, {line, cold-boot, lean-boot, api-fuzz, rids}, {gate}. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. gate ← {api-fuzz, cold-boot, lean-boot, line, rids} ← {setup, secret-scan}, right to left under rtl. |
| req-github-core-machinery-stages-2 | Sources Then Pipelines Then Outputs | Implemented | Refs and rulesets, workflow boxes, and environments occupy three distinct stage columns in that order along the flow direction. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. Refs + rulesets right, workflow block centre, environment + placeholders left. |
| req-github-core-machinery-stages-3 | Unresolved Is A Row | In Development | A fixture workflow whose job `needs` an absent job renders that job in the *unresolved* row and emits `machinery_unresolved_rank`. | Negative case; fixture, not live data. |
| req-github-core-machinery-stages-5 | Unresolved Propagates | In Development | Fixture `A needs B`, `B needs A`, `C needs A`: A, B and C all render in the *unresolved* row; none is at rank 0. | Codex review finding on PR #32. |
| req-github-core-machinery-stages-4 | Branches Collapse | Implemented | A repository with more than `stack_refs_over` non-default, non-tag refs shows one deck with the true count on its chip. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. One deck, chip reads 79; default branch and tags individual. Required the stack.js re-entrancy fix (tap#304). |

---
### Direction
----
RID: `req-github-core-machinery-flow`
Status: `In Development`

`flow` chooses the sign of the stage axis. Default `"rtl"`: sources on the right, outputs on the
left. `"ltr"` mirrors it. Nothing else changes.

#### Implementation

Every x placement is computed as `sign × stage × column_gap` (and `sign × rank × column_gap` inside
a workflow), `sign = -1` for `rtl`, `+1` for `ltr`. Tiers (below) are unaffected: they are placed on
the cross axis. The parameter lives in the projection definition so a consumer chooses per
projection; a per-viewer toggle is backlog and rides the same field.

Why repos-right (decided 2026-09-02): the future whole-system projection places CI/CD machinery and
backend repositories to the right of the running system, so a machinery view read from the right is
the same picture at a different altitude.

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-machinery-flow-1 | Mirror Exactly | In Development | The same scene under `ltr` and `rtl` yields x positions negated about the repository box's centre, identical y positions, identical box sizes. | |
| req-github-core-machinery-flow-2 | Default Is Right | Implemented | With no `machinery.flow` key, sources render right of outputs. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. |

---
### Tiers Outside The Repository
----
RID: `req-github-core-machinery-tiers`
Status: `In Development`

Two tiers sit inside github.com but outside the repository box, on the cross axis.

#### Implementation

- **Top-centre — third parties.** Things that weigh in on the pipeline from outside the repository:
  GitHub Apps `ENABLED_ON` the repository (their edges into the box stay visible — *which robots
  touch this repo* is a real question), the OIDC issuer, and — once their rows exist — reusable
  workflows called from other repositories (`CALLS_WORKFLOW`, #29) and external actions
  (`USES_ACTION`, corpus). Ordered left-to-right by the stage of the thing they touch, so an app
  that opens PRs sits over the sources and a reviewer that comments sits over the pipelines.
- **Bottom-centre — outputs to humans.** Reserved. Notifications, PR comments, review posts and
  release notes are not on the grid today; the tier is drawn empty with no placeholder in v0 (an
  absence of a *type*, not an unobserved instance — see Honesty). Listed under Future.

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-machinery-tiers-1 | Apps On Top | Implemented | Every `github_app` in the scene renders above the repository box, inside github.com, with its `ENABLED_ON` edge visible. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. Dependabot (the one app ENABLED_ON tap) above the account box; the five others hidden as unowned. |

---
### Unknowns Render As Unknowns
----
RID: `req-github-core-machinery-honesty`
Status: `In Development`

Three states, never two: something / nothing / not observable. The projection must not let an
absence read as a finished answer.

#### Implementation

- **Unresolved rank** is a row (Stage Ranking), never rank 0.
- **Outputs not yet collected.** While no release / artifact / package type is collected (#31), the
  outputs stage of EACH repository box carries one placeholder node per uncollected kind reading
  `releases: not yet collected` (and likewise artifacts, packages). It is a viz-owned synthetic node
  (`_synthetic: true`, excluded from searches and badges), removed per kind when that kind's nodes
  appear FOR THAT REPOSITORY — a release belonging to another repository in the scene never retires
  this box's placeholder. An
  environment column with no environments renders `environments: none observed` only when the
  credential could read environments — which is #15's visibility verdict; until #15 lands it
  renders `environments: not observable` (fail closed).
- **Untriggered workflows** are a row, not an omission.

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-machinery-honesty-1 | Placeholder Present | Implemented | With no `github_release` node for the repository, its outputs stage shows the `releases: not yet collected` placeholder; with one present for that repository it does not. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. releases / artifacts / packages placeholders in the outputs stage. |
| req-github-core-machinery-honesty-3 | Placeholder Scope Is The Box | In Development | Two repositories in one scene, only one holding a `github_release`: the other's placeholder remains. | Codex review finding on PR #32. |
| req-github-core-machinery-honesty-4 | Producer Is Three-State | In Development | A job with no declared permissions at job or workflow level renders producer state *not observable*, not unmarked; a `write-all` fixture marks every job producer. | Codex review finding on PR #32. |
| req-github-core-machinery-honesty-2 | Synthetic Is Marked | Implemented | Every placeholder carries `_synthetic: true` and is absent from the status-badge population. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. `_synthetic: true`; no badge population names a placeholder. |

---
### Consumer Contract
----
RID: `req-github-core-machinery-consumer`
Status: `In Development`

github_core ships the module; the consuming plugin seeds the instance (add-panel: consumer owns the
instance). git-serious is the first consumer (git-serious-tap#35: the tap repository's machinery
view is its landing page; the org-level view moves to its own page).

#### Implementation

The consumer's Layout entity sets `definition.js_file` to
`tap_plugin/github_core/static/github_core/js/projections/machinery.js` and its searches must place
in the scene, for the selected repository: the `github_platform` and `github_account` nodes, the
repository, its workflows and jobs, its refs and the rulesets protecting them, its environments,
the apps enabled on it, and every edge among those (`HOSTS_ACCOUNT`, `OWNS_REPO`,
`DEFINES_WORKFLOW`, `DEFINES_JOB`, `DEPENDS_ON_JOB`, `HAS_REF`, `PROTECTS`, `HAS_ENVIRONMENT`,
`ENABLED_ON`). Runs and executed jobs are NOT placed in the static scene. The repository is selected
by a page variable (`req-web-page-params`) mapped onto the searches' inputs. Missing types degrade to
warnings named after the type (`machinery_missing_<type>`), never to a crash.

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-machinery-consumer-1 | git-serious Renders It | Implemented | The git-serious landing page renders the machinery view of the instance's primary repository via this module. | Observed 2026-09-02 on the viz session (8020), git-serious landing against unified-systems-com/tap. git-serious landing v0.4.0 (node upserts: layout js_file, projection `machinery`, scoped node search). |

---
### The Live Layer
----
RID: `req-github-core-machinery-live`
Status: `Proposed`

Runs and executed jobs painted onto the static machinery: per-job status badge sets (latest
conclusion; green / red / *not observed*), a run selector that replays one run over the same
projection, and cascade reveal walking the run through the ranks. Requires `INSTANCE_OF_JOB` (#30)
so the join from executed job to declaration is a fact on the grid, not a name match in a layout.

## Out Of Scope (v0)

- The org-level machinery view (every repository's machinery at once).
- Runs on the canvas as nodes.
- A per-viewer direction toggle (backlog; `flow` is the mechanism).
- Steps inside a job.

## Future

- `CALLS_WORKFLOW` (#29) puts the thirteen `plugin-ci` callers and the AI-review handoff on the
  top tier as structure.
- `github_release`, `actions_artifact`, `package_version` (#31) fill the outputs stage and retire the
  placeholders one kind at a time.
- Outputs-to-humans tier: a node type for notifications / PR comments / review posts, then the
  bottom-centre tier draws them.
- Collapse the account frame into the github.com box label once a chrome option exists for it.
