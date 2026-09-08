# PROTECTS_REPOSITORY

## Blurb

A ruleset applies to a repository. The declared half of what used to be one `PROTECTS` edge: the ruleset's ref patterns stay verbatim on the ruleset node, and this edge says the repository is in its scope.

## Purpose

One ruleset defined at the organization protects many repositories, so the ruleset is one node and *application* has to be an edge. This edge answers "which rulesets govern this repository" — the gates panel's first question — without interpreting a single pattern, which is exactly why it carries none: intent belongs on the node, coverage-on-the-day belongs on `PROTECTS_REF`.

## Goals

1. Let a view list what governs a repository from the edge alone.
2. Keep declared intent and resolved coverage as two questions with two answers.

## Identity

Derived: `uuid5(ns, "PROTECTS_REPOSITORY__github_core:<ruleset_uuid>:<repository_uuid>")`. One per (ruleset, repository).

## Boundaries

- **Not the resolved match.** Which observed refs the patterns select is `PROTECTS_REF`; a repository can be in scope while no ref matches (a ruleset protecting a branch that does not exist).
- **Not enforcement state.** Whether the ruleset is `active` is a field on the node.

## Neutrality

The relationship is neutral (a forge policy applying to a repository); both endpoints are GitHub's today, and the target is deliberately the hosting record, not the neutral git repository — policy is the forge's.

## Observability

Populated from the GraphQL config layer's `rulesets` connection at `repository:administration:read`. Emitted for every ruleset GitHub reports as applying; a repository with no edge reads *no ruleset observed*, and the gates panel says so rather than "unprotected".

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
- **Target:** ``github_core__github_repository` — the repository in the ruleset's scope.`
- **Dimensions:** `github.platform`, `github.surface: rules`, `github.observation: declaration`.
- **Properties:** none — the patterns live verbatim on the ruleset node (`conditions`).
