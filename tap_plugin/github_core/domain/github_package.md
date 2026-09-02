# github_package

## Blurb

A package published to GitHub Packages — a container image on ghcr.io, an npm or Maven package — with its purl, so the supply-chain substrate can claim it by identity. **The collection seam for the corpus's `package`, not its identity home.**

## Purpose

The vocabulary corpus's decision 4 (2026-08-27) places `package` / `package_version` in a future `supply_chain_core`, keyed on a purl — the strongest convergence in the whole sweep, fourteen incidents and ten sources. That ruling stands for **identity**. But GitHub Packages is a GitHub surface: its API, its permission, its `enabledForGitHubApps: false`, its container-registry quirks are all GitHub's, and the collector that reads them belongs in github_core. So this type is what a GitHub collector can honestly emit — the package as GitHub describes it, carrying the purl the corpus chose — and it is written so that `supply_chain_core` can later alias or claim these nodes by that purl without a migration here.

The product repository publishes two images (`tap-web`, `tap-db`) on every merge and neither was on the grid. This is the type that puts them there — when the credential can see them, which today it cannot (see Observability).

## Goals

1. Land what GitHub Packages holds under an account, by type, with GitHub's link to the source repository when it exists.
2. Mint the purl once, from GitHub's own facts, so the substrate's identity is derivable rather than re-authored.
3. Never let an empty listing from a credential GitHub does not enable for the endpoint read as an account that publishes nothing.

## Identity

Natural key: owner + package **type** + package **name** — GitHub's own path to the package (`/orgs/{owner}/packages/{type}/{name}`). Entity id is `uuid5(ns, "github_core__github_package:<owner>#<type>#<name>")`.

Not the numeric id, and deliberately. Every consumer that pulls a package reaches it by name, and a package deleted and republished under the same name IS the same thing to every one of them — the id would split it into two nodes that every `docker pull` treats as one. The type is in the key because GitHub allows an npm package and a container of the same name under one owner.

The `purl` is carried as a field, not used as the key: it is the substrate's identity, derived from this one, and a derived value should not also be the input.

## Boundaries

- **Not `supply_chain_core__package`.** That type does not exist yet; when it does, it claims these by purl. This type does not pretend to be neutral.
- **Not a version.** Versions are `github_package_version`, scoped under the package.
- **Not a build.** Which run pushed a version is on the version's `BUILDS_PACKAGE_VERSION` edge, derived, and absent more often than present.
- **Not the registry's own metadata.** OCI manifests, SBOMs attached as referrers, cosign signatures — all registry content, all out of scope, all tracked by `sigstore_core` / the supply-chain work.

## Neutrality

**Vendor-specific by construction.** The concept is the corpus's neutral `package`; this node is GitHub's description of one. `package_type` is GitHub's closed enum, `visibility` is GitHub's, `version_count` is GitHub's. The neutral half is the `purl`, and it is the only field the substrate should read.

## Observability

Populated from `GET /orgs/{owner}/packages?package_type=<type>` (user fallback on 404), one call per type in GitHub's closed set — the endpoint refuses to enumerate without a type. Declared at **`organization:packages:read`**, which is NEW to the derived union: an existing App installation must re-accept it before the App carries it.

**Not observable with the product credential today, and this is the fact to carry.** GitHub's OpenAPI description marks every packages endpoint `enabledForGitHubApps: false`. Measured 2026-09-02 against `unified-systems-com` with a read-only App installation token that had no packages permission:

- `package_type=container` → **400 `Invalid argument.`** — while the organization's ghcr.io images exist.
- `package_type=npm`, `maven`, `docker` → **200 `[]`** — which proves nothing.
- `GET /orgs/{owner}/packages/container/tap-web` and `.../versions` → **200**, `version_count: 1973` — the per-package endpoints answered for a *public* package, which is recorded as a measurement, not a guarantee.
- GraphQL `Repository.packages` and `Organization.packages` → `totalCount: 0`, no `errors` entry, same organization. The container registry is not on that transport, and an empty connection there proves nothing either.

The rule the collector applies is the bypass-actor asymmetry: a filtered listing cannot invent a package, so a **non-empty** answer proves itself, and an **empty** answer under an App credential is `unobservable`. Whether granting `organization_packages: read` to the App turns the 400 into a listing is **unmeasured** — the App has not been re-accepted — and the credential GitHub documents for this surface is a classic personal access token with `read:packages`, which the envelope does not model. The token, when placed, is routed to this surface first.

**Three states, on every collected repository.** `outputs_observability.packages` is stamped after the walk — `observed` only when every type answered with proof, otherwise `unobservable` with the per-type reasons in `notes.packages`.

## Authoritative Source

- **Source:** GitHub REST API — Packages (`GET /orgs/{org}/packages`, `GET /users/{username}/packages`)
- **Version:** REST API version `2022-11-28`; OpenAPI description pinned in `github_openapi_extract.json` (`spec_commit`), which carries the `enabledForGitHubApps: false` marker
- **Retrieved:** 2026-09-02 (the 400 body, the `[]`, and one package detail captured live into `tests/fixtures/outputs.json`)

## Prior Art

- `specs/spec-github-core-vocabulary.md` decision 4 (2026-08-27) — `package` / `package_version` home in `supply_chain_core`; identity is a purl. This type is the collection seam recorded against that decision.
- Package URL specification, `purl-spec` type registry (`docker`, `npm`, `maven`, `gem`, `nuget`, `github`; read 2026-09-02) — the `pkg:docker/gcr.io/...` example is the form used for ghcr.io, and `repository_url` is the qualifier for GitHub-hosted registries.
- `git-serious-tap/docs/doc-git-serious-vocab-from-incidents.md` row 1 (2026-08-27) — SolarWinds: the published artifact with no build behind it, the absence `BUILDS_PACKAGE_VERSION` exists to make queryable.
- GitHub Packages documentation, *Working with the Container registry* (read 2026-09-02) — the container registry's separate support matrix, which is where the GraphQL absence comes from.

## Fields

- `package_id` — GitHub's numeric id. Not the key (see Identity); carried so a delete-and-republish is detectable as a changed id under a stable node.
- `owner_login` — the account the package lives under; one third of the key.
- `package_type` — GitHub's closed set: `container`, `npm`, `maven`, `rubygems`, `docker` (the retired registry), `nuget`. `""` permitted so a partially-read package lands, per the grid's unobserved convention.
- `name` — the package name as GitHub reports it (`tap-web`; for Maven, `group.artifact` in one string).
- `purl` — Package-URL **without** a version: `pkg:docker/ghcr.io/<owner>/<name>` for a container, the ecosystem's type with `?repository_url=<host>.pkg.github.com` for the others, `pkg:github/...` as the fallback. Minted in `identity.package_purl`, once. The identity `supply_chain_core` keys on.
- `visibility` — `public` / `private` / `internal`, GitHub's value.
- `version_count` — GitHub's own count, and the number that says how much the version cap left behind (1,973 on one image at capture).
- `repository_full_name` — `owner/repo` GitHub links the package to, when it does. GitHub's link (from the `org.opencontainers.image.source` label or a manual link), not an inference; `""` when unlinked.
- `html_url` — the package page.
- `created_at` / `updated_at` — GitHub's timestamps. Null is unobserved.
- `configuration` — JSONB residue for what the API returns that is not lifted into a column.
- `tags` — TAP's own tag map.
