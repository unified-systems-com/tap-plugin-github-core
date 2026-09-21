# `git.host`

## Blurb

Names which forge instance an observation came from — `github.com` or a self-hosted Enterprise Server host — stamped on every node and edge this plugin lands, and on every neutral `git_core` node it mints.

## Purpose

The same organisation name, repository name and even numeric id can exist on `github.com` and on an internal GHES tenant, and the API shapes are close enough to be indistinguishable in a payload. Without a stamp, collecting two instances into one grid merges them — quietly, and in the direction that makes an estate look smaller and more unified than it is.

`git.host` is the tenancy partition. It is the one dimension carried by **every** node and **every** edge this plugin declares, without exception, which is what lets a query scope to one instance without walking to the [`github_platform`](../github_platform.md) root node.

It is also the *same* key on both layers. github_core mints neutral `git_core__git_repository`, `git_core__git_ref` and `git_core__git_commit` nodes, and it stamps this key on them too — so "every node on `ghe.acme.com`" is one filter rather than a union of two spellings. That is the whole reason the key is `git.host` and not `github.platform`: a key named for the vendor cannot be the one the neutral layer carries, and one fact must not live under two names (github-core#168).

## Goals

- Keep two forge instances separable on one grid.
- Give every entity this plugin lands — its own and the neutral ones — a scope filter that costs no traversal.
- Match the containment root, so the dimension and the `HOSTS_ACCOUNT` walk never disagree.
- Stay answerable in one filter across the forge layer and the substrate.

## Identity

The key is `git.host`, and **github_core does not own it**. It belongs to `git_core`, whose `domain/dimensions/git.host.md` is the definition of record; this article is github_core's account of a key it writes because it depends on git_core and therefore legitimately writes git_core's vocabulary. The prefix names the vocabulary, not the plugin — a future GitLab plugin stamps the same key for the same reason (git-core-tap#11).

Effectively immutable — it is in the `dimensions` JSONB of every entity this plugin creates, under a GIN index.

The **value is the host**, deliberately the same natural key the [`github_platform`](../github_platform.md) node uses, and the same value `GitRepository.forge` holds on the neutral repository. That is a single fact with one derivation, reachable three ways: as a cheap dimension filter, as a node you can traverse from, and as a field on the substrate. If they ever disagreed the grid would be lying in one of them, so all three read the host from the collector's configured API base URL — held once, in `_PLATFORM_HOST`.

## Boundaries

- **Not the surface.** Which part of GitHub an observation came from is [`github.surface`](github.surface.md), which stays vendor-specific and does not travel to the neutral layer.
- **Not an account or an organisation.** Many accounts live on one host; ownership is `github.owner` on this plugin's own records, and the [`OWNS_REPO`](../OWNS_REPO.md) and [`HOSTS_ACCOUNT`](../HOSTS_ACCOUNT.md) chain. An account is a *forge* concept and deliberately has no neutral spelling: the moment you say "account" you have said GitHub organisation or GitLab group.
- **Not a plan, tier, or enterprise account.** Those are facts about a tenant that nothing points at, and the corpus's node test makes them fields rather than nodes; they are not encoded here either.
- **Not the object kind.** The neutral rows used to carry `git.object` alongside the type that already said the same thing. It was dropped as an exact duplicate of the entity type (git-core-tap#11); the type is the filter for "every ref".
- **Only one value is declared today.** Every type hardcodes `github.com` in its defaults. A GHES collection would need the value to come from the envelope's configured host rather than a literal — the node already does this, the dimension defaults do not. That is a real limitation, stated rather than implied by a dimension that looks tenancy-aware.

## Neutrality

**Neutral, and that is the point of the rename.** The *key* names Git's own hosting notion and travels to any forge; the *value* is a hostname, which every forge has. What stays vendor-specific is `github.owner`, `github.repo` and `github.surface` — the vocabulary that would rot a neutral substrate if it were stamped on one.

## Observability

**Declared, never fetched.** Applied from `DEFAULT_DIMENSIONS` and edge-type `default_dimensions` at creation, and passed explicitly on the neutral envelopes the collector mints, so it is always present and never ambiguous. It is derived from configuration — the API base URL the collector was pointed at — not from any response body, which is why it is available before the first call succeeds and cannot be withheld by a credential.

The honest caveat is the one in Boundaries: because the value is currently a literal in each type's defaults rather than read from the envelope, a GHES collection would land nodes stamped `github.com`. The stamp would be *present and wrong*, which is worse than absent, and is the failure to fix before the first GHES adopter rather than after.

## Authoritative Source

- **Source:** `specs/spec-github-core-v0.md` `req-github-core-dimensions` (host on every node and edge); `git-core-tap` `specs/spec-git_core-v0.md` `req-git-core-dimensions`, which defines the key; GitHub REST API host semantics — `https://api.github.com` for github.com, `https://<host>/api/v3` for GHES
- **Version:** REST API version `2022-11-28`; key renamed from `github.platform` on 2026-09-20 (github-core#168, git-core-tap#11)
- **Retrieved:** 2026-09-20

## Prior Art

- `specs/spec-github-core-v0.md` `req-github-core-dimensions` (2026-09-20) — the dimension strategy: host on every node and edge, its own and the neutral ones.
- github-core#168 / git-core-tap#11 (2026-09-20) — the ruling that retired `github.platform` and `git.object` in favour of this one key on both layers.
- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `github_platform` as the root of the inventory, and the note that the surveyed published GitHub graph schemas carry no platform-instance node at all.
- GitHub REST API, version `2022-11-28` — the host and API-root semantics the value spells.

## Values

- `github.com` — the public GitHub instance, and the only declared value today. A GHES tenant would carry its own hostname here, matching its [`github_platform`](../github_platform.md) node's natural key and the neutral repository's `forge` field; see Boundaries for why that does not yet happen automatically.
