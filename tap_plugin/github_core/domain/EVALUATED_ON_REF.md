# EVALUATED_ON_REF

## Blurb

The ref a rule suite evaluated a push against. Emitted only when that ref was itself collected, which makes its absence uninformative by design.

## Purpose

A bypass matters differently depending on where it landed. Going around required checks on a throwaway feature branch is noise; doing it on `main` is the finding. This edge is what lets a view sort one from the other without string-matching ref names, and what connects a bypass to everything else known about that ref — whether it is a default branch, what rulesets protect it, what caches are scoped to it.

## Goals

1. Place a bypass on a ref that the graph already knows, so "what happened to `main`" is one traversal.
2. Stay silent rather than wrong when the ref is unknown.

## Identity

Derived: `uuid5(ns, "EVALUATED_ON_REF__github_core:<suite_uuid>:<ref_uuid>")`. A suite evaluates exactly one ref.

## Boundaries

- **Emitted only for observed refs.** A suite naming a branch that has since been deleted — a common shape, since bypasses often happen on short-lived branches — carries no edge. The suite's `ref` field remains the record, so the fact is not lost, only the join.
- **Not a commit link.** `before_sha` and `after_sha` ride as properties rather than pointing at commit nodes, because `git_commit` is a proposed type this plugin does not yet build.
- **Not causal.** The edge says the push was evaluated against this ref, not that the ref changed as a result. A bypassed push usually did land, but the suite alone does not establish it.

## Neutrality

The relationship is neutral; both endpoints are GitHub-specific today. `git_ref` was deliberately built to cover branches and tags together, so this edge needs no variant for tag pushes.

## Observability

Populated from the rule-suite listing's `ref` at **`repository:administration:read`**, resolved against the refs collected earlier in the same run.

**Absence is expected and carries no information.** Refs are collected per repository up to a page limit, and bypasses frequently occur on branches that were deleted after merge. A missing edge means "we did not observe that ref", never "the push had no ref" — the suite's `ref` field is always populated and is the authority.

**Not observable:** whether the ref existed at the time of the push. The grid holds refs as they are now; a suite from three weeks ago may name one that has since gone, which is precisely the case that produces no edge.

## Authoritative Source

- **Source:** GitHub REST API — Rules (`GET /repos/{owner}/{repo}/rulesets/rule-suites`), joined against the GraphQL config layer's `refs` connection
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-28 (captured live; `tests/fixtures/rule_suites.json`)

## Prior Art

- GitHub REST API, version `2022-11-28` — *Get rule suites for a repository*.
- `specs/spec-github-core-v0.md` `req-github-core-refs` — the `git_ref` identity this edge resolves against, and the branch-and-tag-in-one-type ruling.
- `tap_plugin/github_core/domain/SCOPED_TO.md` — the same emitted-only-when-matchable discipline, for caches.

## Endpoints

- **Source:** `github_core__rule_suite` — the evaluated push.
- **Target:** `github_core__git_ref` — the ref it targeted.
- **Dimensions:** `github.platform`, `github.surface: rules`, `github.observation: execution`.
- **Properties:** `before_sha` (ref tip before the push; all zeroes for a creation), `after_sha` (tip after).
