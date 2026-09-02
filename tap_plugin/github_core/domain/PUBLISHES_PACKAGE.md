# PUBLISHES_PACKAGE

## Blurb

An account owns this package, and — when GitHub links the package to a repository — that repository publishes it. Two sources on one edge type, distinguished by `link_kind`.

## Purpose

GitHub Packages are owned by an account and only *optionally* linked to a repository (via the `org.opencontainers.image.source` label on push, or by hand). A package with no repository link is still somebody's; a package with one has a source. One edge type with two sources says both without inventing a second concept, and `link_kind` keeps the two claims distinguishable in a query — the owner edge is unconditional, the repository edge is GitHub's link and nothing else.

## Goals

1. Make every package reachable from its owner.
2. Carry GitHub's repository link when it exists, and only then.
3. Say which of the two each edge is.

## Identity

Derived: `uuid5(ns, "PUBLISHES_PACKAGE__github_core:<source_uuid>:<package_uuid>")` — one per source, so a linked package has two.

## Boundaries

- **Not an inference from the name.** A package named like a repository is not linked to it; only GitHub's `repository` object on the package produces the repository edge.
- **Repository edge only for collected repositories.** A link to a repository outside the scope keeps the name in `repository_full_name` and carries no edge, because the endpoint would dangle.
- **Under a `repos` include-filter, only linked packages land at all.** The packages API is account-scoped, but a repo-scoped envelope asked for those repositories' outputs, not the account's supply-chain inventory; packages not linked to a collected repository are counted in `outputs_observability.notes.packages` and not emitted (PR #50 review).

## Neutrality

Vendor-specific: the optional repository link is a GitHub Packages feature.

## Observability

From the package listing (`organization:packages:read`), which is `unobservable` under the product credential today — see [`github_package`](github_package.md). When the listing is unobservable there are no edges, and every collected repository's `outputs_observability.packages` says so.

## Authoritative Source

- **Source:** GitHub REST API — `GET /orgs/{org}/packages` (`owner`, `repository` on each item)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-09-02

## Prior Art

- GitHub Packages documentation, *Connecting a repository to a package* (read 2026-09-02) — the link is explicit, which is why it is trusted.
- `specs/spec-github-core-v0.md` `req-github-core-packages-2`.

## Endpoints

- **Source:** `github_core__github_account` (always, `link_kind: owner`) or `github_core__github_repository` (when GitHub links one, `link_kind: repository`).
- **Target:** `github_core__github_package` — the package.
- **Dimensions:** `github.platform`, `github.surface: packages`, `github.observation: declaration` — containment sourced on a declared object, following `HAS_CACHE`.
- **Properties:** `link_kind` (`owner` | `repository` — which of the two claims this edge is).
