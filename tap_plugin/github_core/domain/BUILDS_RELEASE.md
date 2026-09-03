# BUILDS_RELEASE

## Blurb

A workflow run is the one that (most likely) produced this release. DERIVED, not reported: GitHub records who published a release but not which run did, so the run is inferred and `match_kind` says how.

## Purpose

The issue that pulled the release type asked for "an edge to the run (or workflow) that produced" each release, and GitHub does not have that fact. What it has is the release's tag, the commit the tag resolves to, and every run's `head_branch` / `head_sha` / `event`. Two inferences follow, of different strength, and the edge carries which one it is rather than presenting both as the same claim.

Runs triggered *by* the release (`event: release`) are excluded: a `publish-release-tags` run that fires on `release: published` consumed the release, and joining it as the producer would invert the arrow.

## Goals

1. Give the output column a producer for each release where the evidence supports one.
2. Label the evidence on the edge, so a `same_commit` fan-out of five runs is read as co-location and a `tag_ref` match as the tag-push build it is.
3. Never join a consumer as a producer.

## Identity

Derived: `uuid5(ns, "BUILDS_RELEASE__github_core:<run_uuid>:<release_uuid>")`. Several runs may point at one release under `same_commit`; each is its own edge with its own `head_sha`.

## Boundaries

- **Not GitHub's attribution.** Contrast `UPLOADS_ARTIFACT`, which is. A reader must check `match_kind` before treating this edge as provenance.
- **Not the trigger.** Release → run ("this release caused that run") is a different relationship, not built; those runs are simply excluded here.
- **Not `BUILDS_PACKAGE_VERSION`.** Same verb, same derived nature, different output; the corpus keeps outputs as separate types and the edges follow.

## Neutrality

Neutral in concept, vendor-specific in derivation: `head_branch` being the tag name on a tag push is GitHub Actions behaviour.

## Observability

Derived after the run window and the config layer are both in hand, at no extra request. `tag_ref` matches when a run's `head_branch` equals the release's `tag_name` — GitHub sets `head_branch` to the tag on `push: tags` runs. `same_commit` matches when a run's `head_sha` equals the release's `target_sha`. Measured on the product repository: its releases are cut by release-please on a push to `main`, so they match `same_commit` against every workflow that ran on that push and `tag_ref` against none — the honest answer, which is why the property exists.

**Absent when** no collected run matches, which includes every release older than the run window. Absence here is a limit of the window before it is a finding; the corpus's "its absence is the finding" reading belongs to `BUILDS_PACKAGE_VERSION`, and even there only after the window and the derivation are accounted for.

## Authoritative Source

- **Source:** derived from GitHub REST `GET /repos/{owner}/{repo}/actions/runs` (`head_branch`, `head_sha`, `event`) and GraphQL `Release.tagName` / `Release.tagCommit`
- **Version:** REST API version `2022-11-28`; GraphQL schema as pinned in `github_openapi_extract.json`
- **Retrieved:** 2026-09-02

## Prior Art

- `git-serious-tap/docs/doc-git-serious-cicd-shape-review.md` §4.3.6 (2026-08-27) — "derive `github_actions_run BUILDS_RELEASE github_release` (exact tag match)". The tag match is kept as `tag_ref`; `same_commit` was added because the product's own releases never match by tag.
- GitHub Actions documentation, *Contexts — `github.ref` on tag pushes* (read 2026-09-02) — why `head_branch` carries the tag name.

## Endpoints

- **Source:** `github_core__github_actions_run` — the run inferred to have produced the release.
- **Target:** `github_core__github_release` — the release.
- **Dimensions:** `github.platform`, `github.surface: releases`, `github.observation: execution` — the layer follows the source, which is an execution.
- **Properties:** `match_kind` (`tag_ref` | `same_commit` — how the run was matched), `head_sha` (the run's commit at match time, so the edge is checkable without re-reading either node).
