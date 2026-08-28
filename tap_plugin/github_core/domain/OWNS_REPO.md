# OWNS_REPO

## Blurb

An account owns a repository — the ownership edge that turns `owner/repo` from a string into a graph.

## Purpose

A repository's `full_name` already contains its owner, and that is exactly the problem: as a substring it is invisible to a graph query. `OWNS_REPO` makes ownership traversable, so "every repository under this organisation" is one hop rather than a `LIKE` against a name.

It is also the second link of the containment chain — platform → account → repository — that the projection walks to nest compounds.

## Goals

- Make ownership a traversal, not a string operation.
- Complete the containment chain from the platform root down to a repository.
- Give account-scoped enumeration a structural record: what the collector enumerated, it also linked.

## Identity

Edge id is `uuid5(ns, "edge:OWNS_REPO__github_core:<source id>:<target id>")`, deterministic from the pair. Since both endpoint ids derive from names (`login`, `full_name`), an account or repository rename produces a new edge alongside new nodes — the same continuity trade recorded on both endpoint articles.

## Boundaries

Carries **no properties**, and that is justified: ownership on GitHub is singular and total. A repository has exactly one owner, the relationship has no grade or scope, and transfer replaces it rather than qualifying it.

What ownership is **not** is *permission*. Who can write to a repository is a different relationship entirely — the corpus's `HAS_REPO_PERMISSION` edge, carrying `{permission, affiliation, granted_via}`, with the note that four sources independently reify permission provenance. That edge is not built. Do not read `OWNS_REPO` as an access claim; it says who the repository belongs to, not who can change it.

Forks are also not this edge: the corpus rules a fork to be `FORKED_FROM` between two repositories.

## Neutrality

**Neutral-capable.** [`github_repository`](github_repository.md) is the corpus's strongest neutrality claim and [`github_account`](github_account.md) is partial, so this edge travels with them to a neutral substrate when one is extracted.

## Observability

Derived, not fetched. Both endpoints come from `repository:metadata:read` — the account from `GET /users/{owner}` (org fallback) and the repository from `GET /repos/{owner}/{repo}` — and the edge is emitted from the pairing rather than from any endpoint that returns a relationship.

Under account scope the pairing follows the enumeration walk (`GET /orgs/{owner}/repos`, user fallback). **An incomplete walk means missing edges, not missing ownership**: the run records walk completeness (`req-github-core-org-scope-3`), so a sparse-looking organisation should be checked against that record before it is believed.

## Authoritative Source

- **Source:** GitHub REST API — Repositories (`GET /repos/{owner}/{repo}`, `owner` field) and organization repository listing (`GET /orgs/{org}/repos`)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `OWNS_REPO` in the existing spine; `HAS_REPO_PERMISSION` recorded separately with its provenance properties, which is what keeps ownership and access distinct.
- `git-serious-tap/docs/doc-git-serious-vocab-platform-models.md` (2026-08-27) — the platform survey in which ownership and permission are consistently modelled as different edges.
- GitHub REST API, version `2022-11-28` — Repositories and organization repository endpoints.

## Endpoints

- **Source:** `github_core__github_account` — user or organization.
- **Target:** `github_core__github_repository`.
- **Dimensions:** `github.platform`, `github.observation: declaration`. No `github.surface` — ownership is not a fact about any one product surface.
