# TARGETS_REF

## Blurb

The release was cut on this tag ref. Both ends carry the commit they resolved to when observed, so a tag moved after the release was cut is a query over two fields, not a diff the collector keeps.

## Purpose

Tag movement is the detection for three incidents in the corpus, and a release is the object most worth protecting against it: consumers pull `v1.2.3` by name, and if the tag moves under a published release they get bytes the release notes never described. Detection needs two facts — what the tag pointed at when the release was published, and what it points at now — and they live on two nodes. This edge is the join.

## Goals

1. Join a release to the tag it names, when that tag was observed.
2. Carry nothing that either node already carries, except the tag name that makes the edge readable on its own.

## Identity

Derived: `uuid5(ns, "TARGETS_REF__github_core:<release_uuid>:<ref_uuid>")`.

## Boundaries

- **Not a claim that the tag is unchanged.** The edge says which tag; the fields say whether it moved.
- **Not emitted for an unobserved tag.** A release whose tag was deleted, or that sits past the ref page cap, carries no edge and keeps `tag_name` as the record — the same rule as `EVALUATED_ON_REF`.

## Neutrality

Neutral: a release on a tag is every forge's model.

## Observability

Resolved against the refs emitted from the same config-layer response (`refs/tags/<tag_name>` in the per-repository ref map). No request of its own. Absent when the tag was not observed — and since refs are capped per repository, a repository with more tags than the cap can have releases whose tag is real but uncollected; the refs-truncation warning is the signal to read alongside.

## Authoritative Source

- **Source:** GitHub GraphQL API — `Release.tagName` resolved against `Repository.refs(refPrefix: "refs/tags/")`
- **Version:** GraphQL schema as pinned in `github_openapi_extract.json` (`graphql.commit`)
- **Retrieved:** 2026-09-02

## Prior Art

- `specs/spec-github-core-vocabulary.md` decision 2 (2026-08-27) — tag movement as the detection for three incidents; `git_ref` spanning branches and tags for that reason.
- `git_ref` article — `head_sha` versus `target_sha` for annotated tags.

## Endpoints

- **Source:** `github_core__github_release` — the release.
- **Target:** `github_core__git_ref` — the tag ref it was cut on.
- **Dimensions:** `github.platform`, `github.surface: releases`, `github.observation: execution` — the layer follows the source, which is an execution.
- **Properties:** `tag_name` (the tag as the release names it, without `refs/tags/`).
