# PUBLISHES_RELEASE

## Blurb

A repository published this release. Containment for the output column: every release hangs off the repository whose tag it was cut on, whether or not the run that cut it was collected.

## Purpose

The output column needs one edge that is always there. `BUILDS_RELEASE` is derived and can be absent; `TARGETS_REF` needs the tag to have been observed. This edge is neither — it is the repository the release belongs to, which GitHub's own path (`/repos/{owner}/{repo}/releases`) states, and it is how a view finds a repository's releases without depending on any derivation.

## Goals

1. Anchor every release to its repository unconditionally.
2. Stay a plain containment edge — properties would only restate fields on the release.

## Identity

Derived: `uuid5(ns, "PUBLISHES_RELEASE__github_core:<repo_uuid>:<release_uuid>")`. One release, one repository, one edge.

## Boundaries

- **Not the producer.** The run that produced the release is `BUILDS_RELEASE`; the account that published it is a field (`author_login`).
- **No properties, on purpose.** The corpus requires an edge with no properties to say why: everything a property here could carry — tag, timestamp, author — is a field on the release node, and the edge exists to be found from, not read.

## Neutrality

Neutral-capable with its endpoints: a GitLab project publishes releases the same way. Vendor-prefixed slug on both ends today.

## Observability

Emitted for every release the config-layer query returned (`repository:contents:read`). When the releases field was not answered — a repos-only scope, or a degraded GraphQL field — no edges exist, and `github_repository.outputs_observability.releases` says `unobservable` so that absence is not read as a repository with no releases.

## Authoritative Source

- **Source:** GitHub GraphQL API — `Repository.releases`
- **Version:** GraphQL schema as pinned in `github_openapi_extract.json` (`graphql.commit`)
- **Retrieved:** 2026-09-02

## Prior Art

- `git-serious-tap/docs/doc-git-serious-cicd-shape-review.md` §4.3.6 (2026-08-27) — proposed this edge under this name.
- `specs/spec-github-core-v0.md` `req-github-core-releases-1`.

## Endpoints

- **Source:** `github_core__github_repository` — the repository the release belongs to.
- **Target:** `github_core__github_release` — the release.
- **Dimensions:** `github.platform`, `github.surface: releases`, `github.observation: declaration` — containment sourced on a declared object, following `HAS_CACHE`.
