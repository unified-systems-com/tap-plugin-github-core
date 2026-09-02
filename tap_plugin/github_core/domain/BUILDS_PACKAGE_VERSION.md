# BUILDS_PACKAGE_VERSION

## Blurb

A workflow run produced this package version. The corpus edge whose ABSENCE is the finding — a registry version with no run behind it is how five incidents read — derived here from a tag naming convention, and labelled as such.

## Purpose

SolarWinds is the shape: a signed, published artifact that no build in the pipeline produced. The corpus adopted this edge so that shape becomes a query — versions with no `BUILDS_PACKAGE_VERSION` — and gave it `{attested}` so the query can also ask whether anything vouches for the join. GitHub records nothing about which run pushed an image, so the edge has to be derived, and a derived edge whose absence is a finding must say exactly how it was derived or every version outside the derivation becomes a false alarm.

## Goals

1. Join a version to the run that built it where the evidence supports it.
2. Name the derivation on the edge (`match_kind`) so a missing edge can be read as "outside the derivation" before it is read as "unbuilt".
3. Keep `attested` three-valued until an attestation surface reads it.

## Identity

Derived: `uuid5(ns, "BUILDS_PACKAGE_VERSION__github_core:<run_uuid>:<version_uuid>")`.

## Boundaries

- **Not GitHub's attribution.** Contrast `UPLOADS_ARTIFACT`.
- **Not an attestation.** `attested: null` is the only value this plugin writes. Reading a SLSA provenance or a cosign attestation from the registry is `sigstore_core` / supply-chain work; when it lands it sets `true` or `false` and this edge stops being the whole story.
- **One derivation.** `tag_sha`: a container tag `sha-<short>` (as `publish-images` applies) or a full commit sha matching a collected run's `head_sha`. `sha256-<digest>` tags are cosign's signature convention and are excluded by construction. No other convention is guessed at.

## Neutrality

Neutral in concept; the `sha-<short>` convention is this product's, and `docker/metadata-action`'s default `sha-` prefix, which is why it is the one implemented.

## Observability

Derived after the packages pass, against the run index of every collected repository — the linked repository's runs when GitHub links one, otherwise all of them, since a seven-plus-hex commit prefix is specific enough. **Absent** for a version older than the run window, a version tagged only with `latest` or a semver, and every version under a credential that cannot list packages. Each of those is a limit before it is a finding, and the surrounding record — `match_kind`, the run window, `outputs_observability.packages` — is what tells them apart from the SolarWinds shape.

## Authoritative Source

- **Source:** derived from GitHub REST `GET /orgs/{org}/packages/{package_type}/{package_name}/versions` (`metadata.container.tags`) and `GET /repos/{owner}/{repo}/actions/runs` (`head_sha`)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-09-02

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `BUILDS_PACKAGE_VERSION` `{attested}`, "its absence is the finding".
- `git-serious-tap/docs/doc-git-serious-vocab-from-incidents.md` row 1 (2026-08-27) — SolarWinds SUNBURST.
- `docker/metadata-action` README, *`type=sha`* (read 2026-09-02) — the `sha-` prefix default that makes the derivation portable beyond this product.

## Endpoints

- **Source:** `github_core__github_actions_run` — the run derived to have built the version.
- **Target:** `github_core__github_package_version` — the version.
- **Dimensions:** `github.platform`, `github.surface: packages`, `github.observation: execution` — the layer follows the source, which is an execution.
- **Properties:** `match_kind` (`tag_sha` — the only derivation implemented), `attested` (`true` | `false` | `null`; null until an attestation surface reads it — never `false` by default, which would be a claim).
