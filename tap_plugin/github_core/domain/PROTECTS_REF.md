# PROTECTS_REF

## Blurb

A ruleset's pattern matched a ref actually observed on the grid. The resolved half of what used to be one `PROTECTS` edge: what the declared intent turned out to cover on the day of collection.

## Purpose

A ruleset says `~DEFAULT_BRANCH` or `refs/heads/release/*`; a view that shows what protects `main` today needs the pattern *resolved* against real refs. This edge is that resolution, one per matched ref, keeping the pattern that selected it so an audit can see intent and coverage side by side. A pattern matching nothing yields no edge, which is a real answer, not a failure.

## Goals

1. Answer "what protects this ref" as a traversal, not a string match.
2. Keep the selecting pattern beside the match, so intent is never lost in resolution.

## Identity

Derived: `uuid5(ns, "PROTECTS_REF__github_core:<ruleset_uuid>:<ref_uuid>")`. One per (ruleset, ref); a ref that stops matching on a later collection is a re-derivation the reconciliation pass handles, not a flag here.

## Boundaries

- **Only observed refs.** Refs are collected per repository up to a page cap; a pattern that would match an uncollected ref produces nothing, and the run's truncation warning is the record.
- **Not the declared scope.** That is `PROTECTS_REPOSITORY`.
- **Not evaluation.** Whether a push was actually gated is the rule suite (`EVALUATED_ON_REF`).

## Neutrality

The relationship is neutral; the target is the neutral `git_core__git_ref` (github-core#76), the source is GitHub's ruleset.

## Observability

Resolved by the collector from the ruleset's include/exclude conditions against the refs collected in the same run, with `~DEFAULT_BRANCH` treated as a token for the default branch, never as a pattern. Absence means no observed ref matched, or the ref was not collected — the ruleset node's `conditions` remain the authority for intent.

## Authoritative Source

- **Source:** GitHub GraphQL API `Repository.rulesets` (`RepositoryRuleset` with `conditions.refName`) joined with REST `GET /repos/{owner}/{repo}/rulesets/{id}` for rule parameters; resolution against the GraphQL config layer's refs
- **Version:** GraphQL schema as pinned in `github_openapi_extract.json`; REST API version `2022-11-28`
- **Retrieved:** 2026-08-28 (rulesets captured live); split from the former `PROTECTS` edge 2026-09-08 (github-core#79)

## Prior Art

- `specs/spec-github-core-v0.md` `req-github-core-rulesets` — the ruleset node and its declared-versus-resolved application.
- github-core#79 (2026-09-08) — one edge, one relationship: the former `PROTECTS` carried both the declared application (target: repository) and the resolved match (target: ref), telling them apart by a `match_kind` property; the type now says which.
- `tap_grid/skills/add-edge/SKILL.md` — the edge-naming checklist (`<ACTION>_<OBJECT>`, never a bare verb).

## Endpoints

- **Source:** `github_core__github_ruleset` — the ruleset.
- **Target:** ``git_core__git_ref` — the observed ref a pattern selected.`
- **Dimensions:** `github.platform`, `github.surface: rules`, `github.observation: declaration`.
- **Properties:** `ref_pattern` — the condition token or fnmatch pattern that selected the target (`~DEFAULT_BRANCH`, `refs/heads/release/*`), preserved as written.
