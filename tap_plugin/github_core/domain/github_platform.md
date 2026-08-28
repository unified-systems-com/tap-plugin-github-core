# GitHub Platform

## Blurb

The GitHub instance itself — `github.com` or a GitHub Enterprise Server host — the root every other node in this plugin hangs beneath.

## Purpose

An inventory needs a top. Without a platform node, an account is a floating string and two tenants of the same organisation name (`acme` on `github.com`, `acme` on an internal GHES) are indistinguishable on the grid. `github_platform` is the containment root: the collector synthesizes one per run and hangs the account beneath it via [`HOSTS_ACCOUNT`](HOSTS_ACCOUNT.md), which is also the walk the graph projection uses to nest the account compound inside the platform compound.

It is deliberately the thinnest node in the plugin. It exists to be pointed at.

## Goals

- Give the inventory a single root, so "which GitHub is this?" is a graph question rather than a convention.
- Separate two tenants that share an account name.
- Carry the `github.platform` dimension value that every node and edge in this plugin stamps.

## Identity

Natural key: **the host** — `github.com`, or the GHES hostname. The entity id is `uuid5(github_core namespace, "github_core__github_platform:<host>")`.

The host is the right key because it is the one property of a GitHub instance that cannot change without it being a different instance, and because it is knowable offline: it comes from the collector's configured API base URL, so the node can be minted before any call succeeds. A GHES tenant therefore gets its own id automatically, and re-runs — plus hand-written GRIFT nodes naming the same host — upsert cleanly onto the same node (`req-github-core-models-8`).

## Boundaries

Deliberately **not** covered:

- **Plan, licence, seat count, enterprise account.** Nothing points at them; they are org-level facts and, per the vocabulary corpus's node test, a fact nothing points at is a field, not a node.
- **GHES version.** Not detected in v0. It would be a field here when something needs it, not a separate node.
- **The enterprise tier above an organisation.** Real in GitHub's model and absent from ours until an adopter has one.

## Neutrality

**Vendor-specific.** The corpus marks it `no` for neutrality. A forge-neutral substrate would have some "forge instance" concept, but the semantics here — that a host distinguishes `github.com` from a self-hosted GHES with an identical API — are GitHub's own, and the node's whole job is to root *this* plugin's inventory.

## Observability

**Nothing observes this node. It is synthesized, not fetched** (`req-github-core-models-8`) — the collector emits it before the per-repo walk, keyed on the host from the envelope's API base URL. No endpoint, no permission, no failure mode.

That is worth stating plainly because it is the exception in this plugin: every other node here can be absent because a credential could not see it, and this one cannot. If a platform node is missing, the run did not start.

What is **not** observable and would need a different credential: the enterprise account above the organisation, plan and licence detail, and the audit log — which is GitHub Enterprise Cloud only, so for a Team-plan organisation "what changed" must be derived by snapshot-diffing the grid rather than read from the platform.

## Authoritative Source

- **Source:** GitHub REST API — the API root and host semantics (`https://api.github.com` for github.com; `https://<host>/api/v3` for GHES)
- **Version:** REST API version `2022-11-28` (the `X-GitHub-Api-Version` header this plugin pins)
- **Retrieved:** 2026-08-27

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — the node inventory that admits this concept as "root of the inventory"; also records that the two published GitHub graph schemas surveyed in the platform pass carry no platform-instance node at all.
- `git-serious-tap/docs/doc-git-serious-vocab-platform-models.md` (2026-08-27) — the 16-source platform and tooling survey behind that judgement.
- GitHub REST API versioning, API version `2022-11-28` — the version pin this node's host semantics are read against.

## Fields

- `host` — the natural key, and the only required field (`CREATE_REQUIRED`). Comes from the configured API base URL, never from a response body, which is what lets the node be minted before the first call.
- `html_url` — the browser-facing root (`https://github.com`). Present so a view can link out without re-deriving a URL from the host; a UI convenience, not identity.
- `configuration` — the JSONB blob every node in this plugin carries for source-shaped detail that has not earned a column. Empty in practice for the platform today; it is the honest place for GHES version or plan data when something needs it, rather than a schema change per curiosity.
- `tags` — the standard TAP tag map. Not GitHub's; TAP's own labelling surface, uniform across every model so a view can filter without knowing the type.
