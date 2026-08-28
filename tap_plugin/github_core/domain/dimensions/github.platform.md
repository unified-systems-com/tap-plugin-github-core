# `github.platform`

## Blurb

Names which GitHub instance an observation came from — `github.com` or a self-hosted Enterprise Server host — stamped on every node and edge this plugin lands.

## Purpose

The same organisation name, repository name and even numeric id can exist on `github.com` and on an internal GHES tenant, and the API shapes are close enough to be indistinguishable in a payload. Without a stamp, collecting two instances into one grid merges them — quietly, and in the direction that makes an estate look smaller and more unified than it is.

`github.platform` is the tenancy partition. It is the one dimension carried by **every** node and **every** edge this plugin declares, without exception, which is what lets a query scope to one instance without walking to the [`github_platform`](../github_platform.md) root node.

## Goals

- Keep two GitHub instances separable on one grid.
- Give every github_core entity a scope filter that costs no traversal.
- Match the containment root, so the dimension and the `HOSTS_ACCOUNT` walk never disagree.

## Identity

The key is `github.platform`, in the `github.` namespace this plugin owns. Effectively immutable — it is in the `dimensions` JSONB of every entity this plugin creates, under a GIN index.

The **value is the host**, which is deliberately the same natural key the [`github_platform`](../github_platform.md) node uses. That is a single fact with one derivation, reachable two ways: as a cheap dimension filter, and as a node you can traverse from. If the two ever disagreed the grid would be lying in one of them, so both read the host from the collector's configured API base URL.

## Boundaries

- **Not the surface.** Which part of GitHub an observation came from is [`github.surface`](github.surface.md).
- **Not an account or an organisation.** Many accounts live on one platform; ownership is the [`OWNS_REPO`](../OWNS_REPO.md) and [`HOSTS_ACCOUNT`](../HOSTS_ACCOUNT.md) chain.
- **Not a plan, tier, or enterprise account.** Those are facts about a tenant that nothing points at, and the corpus's node test makes them fields rather than nodes; they are not encoded here either.
- **Only one value is declared today.** Every type hardcodes `github.com` in its defaults. A GHES collection would need the value to come from the envelope's configured host rather than a literal — the node already does this, the dimension defaults do not. That is a real limitation, stated rather than implied by a dimension that looks tenancy-aware.

## Neutrality

**Vendor-specific.** A neutral substrate would need a forge-instance concept, but this key names GitHub and its value carries GitHub's host semantics. The *idea* — partition by tenant — is general; this spelling is not.

## Observability

**Declared, never fetched.** Applied from `DEFAULT_DIMENSIONS` and edge-type `default_dimensions` at creation, so it is always present and never ambiguous. It is derived from configuration — the API base URL the collector was pointed at — not from any response body, which is why it is available before the first call succeeds and cannot be withheld by a credential.

The honest caveat is the one in Boundaries: because the value is currently a literal in each type's defaults rather than read from the envelope, a GHES collection would land nodes stamped `github.com`. The stamp would be *present and wrong*, which is worse than absent, and is the failure to fix before the first GHES adopter rather than after.

## Authoritative Source

- **Source:** `specs/spec-github-core-v0.md` `req-github-core-dimensions` (platform on every node and edge); GitHub REST API host semantics — `https://api.github.com` for github.com, `https://<host>/api/v3` for GHES
- **Version:** REST API version `2022-11-28`; declarations as of commit `46a34b8`
- **Retrieved:** 2026-08-27

## Prior Art

- `specs/spec-github-core-v0.md` `req-github-core-dimensions` (2026-08-27) — the dimension strategy: platform on every node and edge.
- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `github_platform` as the root of the inventory, and the note that the surveyed published GitHub graph schemas carry no platform-instance node at all.
- GitHub REST API, version `2022-11-28` — the host and API-root semantics the value spells.

## Values

- `github.com` — the public GitHub instance, and the only declared value today. A GHES tenant would carry its own hostname here, matching its [`github_platform`](../github_platform.md) node's natural key; see Boundaries for why that does not yet happen automatically.
