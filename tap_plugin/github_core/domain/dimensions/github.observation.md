# `github.observation`

## Blurb

Says whether a node or edge records what CI was **declared** to do, or what it **actually did** — the distinction the vocabulary corpus calls its single largest finding, made machine-legible.

## Purpose

Read this before writing any query that touches the Actions surface.

CI security has two layers that look alike and answer opposite questions. A workflow file *declares* what may happen: which triggers fire, what `permissions:` a job gets, whether an action is pinned. A run *records* what did happen: this commit, from this fork, concluded this way. **Every privilege decision in CI is made at the declared level** — roughly 20 of the 35 compromises in the incident corpus turn on a declared property — while every piece of evidence that it happened lives at the executed level.

Almost nobody models both. The two published GitHub graph schemas surveyed in the platform pass model declaration only and have no execution node anywhere; only 8 of 16 platform sources model a pipeline run at all. Spanning both is the distinguishing property of this vocabulary, and this dimension is how the grid keeps them apart.

It carries a load the slugs cannot. `github_actions_job` is an **execution**, and the declared job — the corpus's `workflow_job`, its largest unbuilt gap — will be a separate type when it lands. The obvious name was already taken, and slugs are identity and are never renamed. So the distinction survives in exactly two places: this dimension, and the domain articles that state it in prose.

## Goals

- Let a query ask for declared-side or executed-side facts without enumerating types.
- Keep the distinction machine-legible for Player 3, which cannot be relied on to infer it from a slug that actively misleads.
- Hold the line when `workflow_job` lands and two types genuinely mean "job".

## Identity

The key is `github.observation`, in the `github.` namespace this plugin owns. Effectively immutable: it is written into the `dimensions` JSONB of every Actions-surface entity and covered by a GIN index, so a rename is a whole-grid migration.

The value is a property of *the observation*, not of the thing observed. The same repository appears under `declaration`; a run of its workflow appears under `execution`. Nothing carries both.

## Boundaries

- **Not a timestamp or a lifecycle state.** `execution` does not mean recent, finished, or successful — `status` and `conclusion` are fields on the run and job.
- **Not confidence or provenance.** Whether a link was observed or inferred is a separate question, carried by edge properties (`link_rule` / `matched_value` on the derived cross-grid edges). A declared fact can be inferred and an executed fact can be exact.
- **Not a claim that the declaration produced the execution.** That join is [`EXECUTES_WORKFLOW`](../EXECUTES_WORKFLOW.md); this dimension only labels which side each end sits on.
- **Not yet applied to `github_ruleset`.** The ruleset carries `github.surface: rules` and no observation value. Defensible — a ruleset is a gate, neither a pipeline declaration nor a pipeline run — but it is an open question rather than a settled one, and it is recorded here so the next reader knows it was noticed.
- **`REFERENCES_RESOURCE` carries no observation value either**, and that is unresolved rather than deliberate: its source union spans a workflow, a run and a job, so a single value would be wrong for two thirds of its instances. Worth confirming with whoever set the dimension across the vocabulary.

## Neutrality

**Neutral in concept, vendor-named in key.** The declaration/execution split holds for any CI system, and the kernel pressure test — a non-forge project — populates both sides. Only the `github.` prefix is vendor-specific. When a neutral substrate is extracted, this dimension's *idea* travels with the neutral node types; the key would be re-spelled at that boundary, which is a re-collect while the vocabulary is young and a migration once it is not.

## Observability

**Declared, never fetched — and therefore never ambiguous.** Applied from each model's `DEFAULT_DIMENSIONS` at entity creation and from each edge type's registered `default_dimensions`, so it is present the moment the row exists. Unlike almost everything else in this plugin's Observability sections, its absence can never mean "a credential could not look".

What its absence *does* mean is a declaration gap: a type that forgot the stamp is invisible to a partition filter and will read as neither declared nor executed. Two such gaps exist today and are named in Boundaries rather than left to be discovered.

## Authoritative Source

- **Source:** `specs/spec-github-core-vocabulary.md` — "The three findings that shaped everything else", finding 1, and the `workflow_job` vs `github_actions_job` note; `specs/spec-github-core-v0.md` `req-github-core-dimensions`
- **Version:** corpus built 2026-08-27; declarations as of commit `46a34b8` plus the in-flight observation sweep
- **Retrieved:** 2026-08-27

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — finding 1: declaration and execution are different objects, and almost nobody models both; the 8-of-16 and ~20-of-35 counts.
- `git-serious-tap/docs/doc-git-serious-vocab-platform-models.md` (2026-08-27) — the platform survey: two published GitHub graph schemas model the declared side only.
- `git-serious-tap/docs/doc-git-serious-vocab-from-incidents.md` (2026-08-27) — the incident evidence that privilege decisions are made at the declared level.

## Values

- `declaration` — the node or edge records what was **configured**: a repository, a workflow definition, an App enabled on a repo, an account. It describes a standing state that determines what *can* happen, and it is where every control question is actually answered.
- `execution` — the node or edge records what **ran**: a workflow run, an executed job, the runner a job landed on. It is evidence of one event and cannot, on its own, tell you what is permitted.
