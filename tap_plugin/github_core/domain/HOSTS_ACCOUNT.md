# HOSTS_ACCOUNT

## Blurb

The containment edge at the very top of the inventory: a GitHub instance hosts an account.

## Purpose

Every tree needs a root edge. `HOSTS_ACCOUNT` is how [`github_platform`](github_platform.md) reaches [`github_account`](github_account.md), and it is what the graph projection walks to nest the account compound inside the platform compound. Without it the platform node would exist and connect to nothing.

It also disambiguates: the same account name on `github.com` and on an internal GHES host are two accounts, and this edge is what says which instance each belongs to.

## Goals

- Root the containment chain, so the whole inventory is one connected component.
- Give the visualisation a parent→child walk from the top.
- Bind an account name to the instance it lives on.

## Identity

Edge id is `uuid5(ns, "edge:HOSTS_ACCOUNT__github_core:<source id>:<target id>")` — deterministic from the pair, so re-collection upserts rather than duplicates. The slug carries the `__github_core` suffix that namespaces every edge type this plugin owns; slugs are identity and are never renamed.

## Boundaries

Carries **no properties**, and the corpus requires an edge with none to justify that. It does: containment here is total and unconditional. An account is hosted by exactly one instance, the relationship has no lifecycle, no scope and no strength, and there is nothing about it a query could usefully filter on. Adding a property would be inventing a question nobody asks.

Not covered: the enterprise account that may sit between an instance and an organisation. Real in GitHub's model, absent here until an adopter has one.

## Neutrality

**Vendor-specific**, inheriting from both endpoints. A neutral substrate would have some forge-instance-to-account containment, but this edge's meaning depends on `github_platform`'s host semantics.

## Observability

**Synthesized, not observed.** The collector emits it alongside the platform singleton (`req-github-core-models-8`); no endpoint, no permission, no failure mode. Like the platform node itself, its absence means the run did not start, never that a credential could not see it.

## Authoritative Source

- **Source:** GitHub REST API — the host/account relationship implied by the API root and the Organizations/Users endpoints; no endpoint returns this edge directly
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — lists `HOSTS_ACCOUNT` in the existing spine, and sets the standing rule that an edge with no properties must justify why it needs none.
- `specs/spec-github-core-v0.md` `req-github-core-edges` (2026-08-27) — the registered edge vocabulary this belongs to.
- GitHub REST API, version `2022-11-28` — the host and account endpoints whose relationship this edge synthesizes.

## Endpoints

- **Source:** `github_core__github_platform` — the instance.
- **Target:** `github_core__github_account` — the user or organization.
- **Dimensions:** `github.platform`, `github.observation: declaration`. No `github.surface`, because containment is not a fact about any one product surface.
