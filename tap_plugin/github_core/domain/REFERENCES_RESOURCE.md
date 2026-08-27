# REFERENCES_RESOURCE

## Blurb

GitHub CI plumbing **names** a resource that exists elsewhere on the grid — a deliberately weak claim, and the weakness is the design.

## Purpose

A workflow file is full of strings that are also things: a region, a DNS zone, a distribution id. Those strings are the only evidence the grid has that a pipeline touches a piece of infrastructure, and they are worth surfacing — "which workflows mention this CloudFront distribution" is a question an operator asks during an incident and cannot otherwise answer.

But a mention is not a deployment. This edge is scoped to say exactly what was observed and nothing more: **this plumbing names this resource.** It does not claim deployment, ownership, or runtime control. Naming it `REFERENCES_RESOURCE` rather than `DEPLOYS_TO` was the point.

That restraint is the corpus's second finding applied: **shape is not severity.** An edge that records only *that* a relationship exists produces confident nonsense in any view that scores risk. Where the strength of a claim cannot be established, the edge says so in its name and carries its provenance.

## Goals

- Surface the infrastructure a pipeline mentions, without overstating what a mention proves.
- Keep the derivation auditable, so an inferred edge is never mistaken for an observed one.
- Fail quietly: a missing link degrades the picture, a wrong one misleads.

## Identity

Edge id is `uuid5(ns, "edge:REFERENCES_RESOURCE__github_core:<source id>:<target id>")`. Resolved during the **enrichment** phase from the grid-link manifest, after both endpoints are on the grid.

## Boundaries

**Derived, not hotlink-backed.** It carries the same provenance pair as [`FEDERATES_VIA`](FEDERATES_VIA.md) — `link_rule` (which rule matched) and `matched_value` (what it keyed on), mutually `dependentRequired` — so no edge can claim a rule without naming its evidence.

Explicitly **not** claimed: deployment, ownership, runtime control, or causation. A workflow that names a distribution id in a comment produces the same edge as one that invalidates it.

The known weakness is recorded rather than hidden: matching is regex shape-guessing over text, which produces junk references and misses values embedded in `${{ }}` expressions. `req-github-core-backlog-grid-vocab-links` is the named fix — match against the known grid vocabulary instead of guessing shapes, and carry a confidence marker. Until then, treat this edge as a search result, not a fact.

## Neutrality

**Cross-plugin by nature**, and asymmetric: the source union is GitHub-specific, the target union belongs to `aws_core`. The *pattern* — CI plumbing naming an external resource — is general and would survive an extraction; the endpoint types would not.

## Observability

Derived at enrichment time from text already collected: workflow YAML (`repository:contents:read`) and run and job payloads (`repository:actions:read`). No additional call and no additional permission.

Matching is **exact-only** and failures are **warn-only** (`req-github-core-grid-links`), and enrichment degrades rather than aborting when a rule names a vocabulary that is not installed. So this edge's population depends on which *other* plugins the instance has: the same GitHub data on a grid without `aws_core` yields none of these edges, correctly, and silently.

**A reference is not observable at all** where the value is computed at run time — assembled from a secret, a variable, or an expression. Those are exactly the cases a static read cannot reach, and they are invisible rather than absent.

## Authoritative Source

- **Source:** GitHub REST API — Repository Contents (workflow YAML) and Actions Workflow Runs / Jobs payloads, as the text this edge derives from; AWS resource identifier formats for the target shapes
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — finding 2, "bare edges are ruled out by the field itself": shape is not severity, and four independent sources reify how a relationship arrived.
- `git-serious-tap/docs/doc-git-serious-vocab-security-standards.md` (2026-08-27) — the standards pass in which nine edge properties are already standardised, including identity confidence.
- `specs/spec-github-core-v0.md` `req-github-core-grid-links` and `req-github-core-backlog-grid-vocab-links` (2026-08-27) — exact-only matching, warn-only failure, and the named fix for regex shape-guessing.
- GitHub REST API, version `2022-11-28` — Contents, Runs and Jobs endpoints.

## Endpoints

- **Sources:** `github_core__github_workflow`, `github_core__github_actions_run`, `github_core__github_actions_job` — a union, because a reference can be observed in a declaration or in either level of execution payload.
- **Targets:** `aws_core__aws_route53_zone`, `aws_core__aws_region`, `aws_core__aws_cloudfront_distribution` — cross-plugin; resolved only where the target vocabulary is installed.
- **Properties:** `link_rule` and `matched_value`, mutually `dependentRequired`, `additionalProperties: false`.
- **Dimensions:** `github.platform` only. Alone among this plugin's edges it carries **no** `github.observation` value, and that is unresolved rather than settled: its source union spans a workflow (declaration) plus a run and a job (execution), so any single value would be wrong for most instances. Recorded here so the gap stays visible; see [`github.observation`](dimensions/github.observation.md).
