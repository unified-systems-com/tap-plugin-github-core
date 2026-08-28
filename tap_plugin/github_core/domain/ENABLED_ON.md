# ENABLED_ON

## Blurb

A GitHub App — or a first-party platform service such as the Actions OIDC issuer — is enabled on a repository.

## Purpose

Third parties are a standing capability inside an organisation: an installed App holds permissions on repositories, acts under its own identity, and does not resign. This edge is the first, shallowest answer to "what non-human things can act here", and it is the surface a future installation model attaches to.

It also carries a second population deliberately. The Actions **OIDC issuer** (`token.actions.githubusercontent.com`) is not an App, but it is a platform service every repository's workflows use to mint identity tokens, and it reaches a repository through the same "is enabled here" relationship. Modelling it with a second edge type would have split one question in two.

## Goals

- Say which non-human actors reach which repositories.
- Give the OIDC federation chain its first link, so `repository → issuer → cloud provider` is one walk.
- Hold the place where the real grant will attach once `app_installation` exists.

## Identity

Edge id is `uuid5(ns, "edge:ENABLED_ON__github_core:<source id>:<target id>")`, deterministic from the pair. One [`github_app`](github_app.md) node is shared across every repository that enables it, so these edges fan in on the app.

## Boundaries

Carries **no properties** today, and unlike the containment edges in this plugin **that is a gap rather than a justification.** The permissions an installation holds, its `repository_selection`, its `events`, whether it is suspended, and when it was granted are exactly the security content — and they belong to the *grant*, which the corpus models as a separate `app_installation` node (*self* tier, 7 sources, not built) rather than as properties here. Read this edge as "reaches", never as "and here is what it may do".

Also not covered: what an app *does* with its access. `OPENS_PR`, `RAISES_ALERT` and similar are recorded as backlog (`req-github-core-backlog-app-relationships`), waiting for a consumer.

## Neutrality

**Vendor-specific** on the App side; the source union includes the neutral `identity_core__oidc_issuer`, which is why the edge accepts two source types rather than one.

## Observability

Two sources, and they are unequal:

- **Dependabot and other first-party services** are detected from the synthetic entry GitHub returns on the Actions surface and reclassified at collection time (`req-github-core-app`). No special permission.
- **Actual installed Apps** come from `GET /orgs/{org}/installations`, which is **GitHub App only** — proven by execution on 2026-08-27 to return **404 to any personal access token**, fine-grained or classic, whatever its permissions. GitHub's documentation also requires organization-owner standing.

The consequence: **a PAT-based collection produces an app population consisting of first-party services and nothing else** — and it looks complete, because a 404 is not an error the way a 403 is. This is one half of the credential asymmetry recorded across this plugin; the other half is that an owner-minted PAT, not the App, is what sees ruleset `bypass_actors`. Neither credential dominates.

The OIDC issuer edge is emitted structurally during enrichment rather than fetched, because every repository's workflows can use the canonical issuer.

## Authoritative Source

- **Source:** GitHub REST API — App installations for an organization (`GET /orgs/{org}/installations`); GitHub Actions OIDC documentation for the canonical issuer `token.actions.githubusercontent.com`
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27 (the App-only 404 verified by execution against our own organization)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `app_installation` split from `github_app` on 7 sources; the record that the application and the grant are different objects.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §3.9 (2026-08-27) — the App-only installation and PAT-grant rows, and the OAuth-apps no-endpoint gap.
- GitHub REST API, version `2022-11-28` — installation endpoints; GitHub Actions OIDC issuer documentation.

## Endpoints

- **Sources:** `github_core__github_app`, `identity_core__oidc_issuer` — a union, because both reach a repository through the same "enabled here" relationship and splitting them would split one question.
- **Target:** `github_core__github_repository`.
- **Dimensions:** `github.platform`, `github.surface: apps`, `github.observation: declaration`. An App being enabled is a standing configuration, not a record of the App doing anything — what it does with that access is unmodelled (see Boundaries).
