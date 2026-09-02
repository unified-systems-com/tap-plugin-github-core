# STORES_ARTIFACT

## Blurb

A repository is holding this uploaded artifact. Containment for artifacts whose producing run is outside the collected run window — the artifact still lands, still names its `run_id`, and still belongs somewhere.

## Purpose

The artifact listing is repository-scoped and returns thousands of artifacts across far more runs than any collection window holds. `UPLOADS_ARTIFACT` joins an artifact to its run only when that run is in the batch; without a second edge the rest would be orphans that a view cannot reach. This edge is how every artifact is reachable, the way `HAS_CACHE` reaches every cache entry.

## Goals

1. Anchor every collected artifact unconditionally.
2. Stay a plain containment edge.

## Identity

Derived: `uuid5(ns, "STORES_ARTIFACT__github_core:<repo_uuid>:<artifact_uuid>")`.

## Boundaries

- **Not the producer.** That is `UPLOADS_ARTIFACT`, and it is GitHub's attribution rather than a derivation.
- **No properties, on purpose.** Everything is a field on the artifact.
- **Named with a verb, not `HAS_`**, per the edge-naming rule (`HAS_` is aspectual, not an action). `HAS_CACHE` predates the rule and is baselined debt; this edge was born after it.

## Neutrality

Neutral-capable with the artifact.

## Observability

Emitted for every artifact the listing returned (`repository:actions:read`). On a 403/404 there are no edges and `github_repository.outputs_observability.artifacts` says `unobservable`.

## Authoritative Source

- **Source:** GitHub REST API — `GET /repos/{owner}/{repo}/actions/artifacts`
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-09-02

## Prior Art

- `HAS_CACHE` — the containment precedent for run by-products.
- `specs/spec-github-core-v0.md` `req-github-core-artifacts-1`.

## Endpoints

- **Source:** `github_core__github_repository` — the repository holding the artifact.
- **Target:** `github_core__actions_artifact` — the artifact.
- **Dimensions:** `github.platform`, `github.surface: actions`, `github.observation: declaration` — containment sourced on a declared object, following `HAS_CACHE`.
