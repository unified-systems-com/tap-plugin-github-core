# PUBLISHES_PACKAGE_VERSION

## Blurb

A package has this published version. Containment: the version's identity is scoped under its package.

## Purpose

Versions are reached through their package in GitHub's API and keyed under it on the grid. The edge is the structural fact that makes "every version of `tap-web`" a one-hop query and lets `BUILDS_PACKAGE_VERSION` stay about the run alone.

## Goals

1. Anchor every collected version to its package.
2. Stay a plain containment edge.

## Identity

Derived: `uuid5(ns, "PUBLISHES_PACKAGE_VERSION__github_core:<package_uuid>:<version_uuid>")`.

## Boundaries

- **Not `latest`.** Which version a moving tag points at is on the version's `container_tags`, not on this edge.
- **No properties, on purpose.** Everything is a field on the version.

## Neutrality

Vendor-specific with its endpoints; the concept is every registry's.

## Observability

From the versions listing (`organization:packages:read`), reached only through a package the listing returned. Capped per package with GitHub's `version_count` reported, so absence of a version past the cap is stated rather than implied.

## Authoritative Source

- **Source:** GitHub REST API — `GET /orgs/{org}/packages/{package_type}/{package_name}/versions`
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-09-02

## Prior Art

- `specs/spec-github-core-v0.md` `req-github-core-packages-1`.

## Endpoints

- **Source:** `github_core__github_package` — the package.
- **Target:** `github_core__github_package_version` — one of its versions.
- **Dimensions:** `github.platform`, `github.surface: packages`, `github.observation: execution` — the layer follows the source, which is an execution.
