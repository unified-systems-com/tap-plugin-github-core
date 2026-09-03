# github_package_version

## Blurb

One published version of a package — a container image digest with the tags pointing at it, or a registry version string — and its purl. The registry side of `BUILDS_PACKAGE_VERSION`, the corpus edge whose absence is the finding.

## Purpose

"A registry version with no run behind it is how five incidents read" — the corpus's justification for `BUILDS_PACKAGE_VERSION`, and it needs the version node to exist before its absence can mean anything. This is that node. Each is one thing a consumer can pull: a digest on ghcr.io, a version on npm.pkg.github.com.

For containers it also carries the tags pointing at the digest *when observed*. `latest` moving to a digest no run built is the same detection as a tag moved under a release, and it lives in this node's field history the same way.

## Goals

1. Land every pullable version the credential can see, with the purl the substrate will key on.
2. Join a version to the run that built it when the evidence exists, label the evidence, and leave the join absent — visibly — when it does not.
3. Keep `attested` three-valued until an attestation surface reads it.

## Identity

Natural key: the package's key (owner + type + name) + GitHub's version **id**. Entity id is `uuid5(ns, "github_core__github_package_version:<owner>#<type>#<name>#<id>")`.

GitHub's id rather than the version name. For a container the name is a digest — content-addressed, and it would have keyed correctly. For npm and Maven a version string can be unpublished and republished as different bytes, and only the id tells those apart. One rule for every type beats a per-type exception on the field that can never change.

## Boundaries

- **Not `supply_chain_core__package_version`.** Same seam as the package: this is GitHub's description, carrying the corpus's identity as a field.
- **Not an OCI manifest.** Layers, platforms, referrers and signatures are registry content. `sha256-<digest>` tags in `container_tags` are cosign's naming convention for signatures and attestations stored beside the image; they are recorded as tags, not interpreted.
- **Not proof of provenance.** `BUILDS_PACKAGE_VERSION` is derived from a tag naming convention. `attested` is null until something reads an attestation.

## Neutrality

**Vendor-specific by construction**, like its package. `container_tags` is a GitHub Packages metadata shape; the `purl` is the neutral half.

## Observability

Populated from `GET /orgs/{owner}/packages/{type}/{name}/versions?per_page=100` (newest first, one page) at **`organization:packages:read`**, for each package the listing returned. Same `enabledForGitHubApps: false` caveat as [`github_package`](github_package.md): measured 2026-09-02, the endpoint **did** answer a read-only App installation token with no packages permission for a public image — `200`, four of 1,973 versions captured, each carrying `metadata.container.tags` — and that is a measurement about a public package, not a guarantee about the surface.

**Reached only through the listing.** The collector does not guess package names, so when the listing is unobservable so are the versions, however readable the per-package endpoint would have been. Naming `tap-web` by hand would be an inventory of one dressed as an inventory.

**The cap is stated against GitHub's count.** `version_count` on the package says what one page left behind, and the run warns with it.

**Not observable:** which run pushed a version — GitHub records nothing. The `BUILDS_PACKAGE_VERSION` edge is derived from the `sha-<short commit>` tag the product's `publish-images` workflow applies, matched against the head commit of a collected run. A version tagged any other way carries no edge, and that absence is a limit of the derivation, which the edge's `match_kind` makes readable, before it is a finding.

## Authoritative Source

- **Source:** GitHub REST API — Packages (`GET /orgs/{org}/packages/{package_type}/{package_name}/versions`, and the `/users/` form)
- **Version:** REST API version `2022-11-28`; OpenAPI description pinned in `github_openapi_extract.json` (`spec_commit`)
- **Retrieved:** 2026-09-02 (four versions of `unified-systems-com/tap-web` captured live into `tests/fixtures/outputs.json`)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `BUILDS_PACKAGE_VERSION` `{attested}`, "its absence is the finding"; decision 4.
- Package URL specification (`purl-spec`, read 2026-09-02) — the version component and the `docker` type's digest form.
- `cosign` documentation, *Signature spec / tag-based discovery* (read 2026-09-02) — the `sha256-<digest>.sig` tag convention, which is why `sha256-` tags are recorded and not matched.
- `unified-systems-com/tap` `.github/workflows/publish-images.yml` (read 2026-09-02) — the `:sha-<short>` tag the derivation matches on, and the statement that `:latest` moves only on tip builds.

## Fields

- `version_id` — GitHub's version id; the last component of the natural key.
- `owner_login` / `package_type` / `package_name` — the package's key, carried so a version is attributable without walking the containment edge.
- `version` — GitHub's version `name`: the manifest digest (`sha256:...`) for a container, the version string for a registry package.
- `purl` — Package-URL **with** the version (`pkg:docker/ghcr.io/<owner>/<name>@sha256:...`). Minted by the same derivation as the package's.
- `container_tags` — the tags pointing at this digest when observed (`latest`, `sha-2e39bdf`, `buildcache-amd64`, `sha256-<digest>` for cosign artifacts). Containers only; `[]` otherwise. A moving tag is field history on the node it moved to.
- `html_url` — the version page.
- `created_at` / `updated_at` — GitHub's timestamps. Null is unobserved.
- `configuration` — JSONB residue for what the API returns that is not lifted into a column.
- `tags` — TAP's own tag map — not to be confused with `container_tags`, which is GitHub's.
