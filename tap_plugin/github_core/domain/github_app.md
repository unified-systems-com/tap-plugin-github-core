# GitHub App

## Blurb

A GitHub App as a *registered application* — Dependabot, a CI integration, git-serious itself — distinct from the grant that installs it somewhere.

## Purpose

Apps are the third-party population of a GitHub organisation. Each one holds a set of permissions on repositories it is installed on, acts under its own identity, and — unlike a human — never leaves. Seven of the surveyed sources model the installation concept and the corpus is explicit that **the application and the grant are different objects**: `github_app` keeps the application; `app_installation` (a corpus concept at the *self* tier, not yet built) would carry the grant.

This node exists so that "what third parties can write to this repository" has somewhere to start. Today it answers the shallower question — which apps are enabled where, via [`ENABLED_ON`](ENABLED_ON.md).

There is a deliberate reflexivity here worth naming: git-serious is itself installed as a GitHub App, so a complete inventory shows the observer inside it, with its own permissions on display.

## Goals

- Give third-party applications a single node each, shared across every repository that enables them.
- Distinguish an application from its installation, so the future grant model has a clean place to attach.
- Detect and classify first-party platform services that arrive through the same surface (Dependabot).

## Identity

Natural key: **`slug`** — the app's URL slug (`dependabot`). Entity id is `uuid5(ns, "github_core__github_app:<slug>")`.

One node per application, shared across every repository that enables it; the [`ENABLED_ON`](ENABLED_ON.md) edges fan in. That is the correct shape *because* the application is the shared thing — the per-repository fact is the grant, which is the object this type deliberately does not model yet.

The slug rather than `app_id` because the slug is what appears in URLs, in actor names (`dependabot[bot]`), and in the synthetic entries GitHub returns for its own services — so an app node can be minted from a mention. `app_id` is carried as a field.

## Boundaries

Deliberately **not** covered:

- **The installation.** The grant — its granular `permissions` map, `events`, `repository_selection`, `suspended_at`, `created_at` — is the corpus's `app_installation`, a separate *self*-tier type. This is the sharpest boundary on this node, because the grant is where the security content lives: an application is inert, an installation is a standing capability.
- **What an app *does*.** Dependabot opens dependency-bump pull requests; code scanning posts alerts. Edges like `OPENS_PR` or `RAISES_ALERT` are recorded as backlog (`req-github-core-backlog-app-relationships`) and wait for a consumer.
- **OAuth applications.** A different GitHub concept, and one with **no REST or GraphQL endpoint at all** — see Observability.
- **The app's own repository.** `DEFINED_IN` is a corpus edge for actions, not apps.

## Neutrality

**Vendor-specific.** The corpus marks it `no`. "Third-party application with scoped permissions" is a general idea, but the GitHub App model — installations, granular permission maps, JWT-to-installation-token exchange — is GitHub's own. The neutral concept in this neighbourhood is `identity_core__principal` (the robot/non-human actor), which the corpus places at the *later* tier in the neutral substrate.

## Observability

There are two populations here, and they are observed completely differently.

**First-party services**, chiefly Dependabot, arrive through the Actions surface as a synthetic entry and are detected and reclassified at collection time (`req-github-core-app`). No special permission.

**Actual installed Apps** come from `GET /orgs/{org}/installations` — which is **GitHub App only**. Proven by execution on 2026-08-27: the endpoint returns **404 to any personal access token**, fine-grained or classic, regardless of the token's permissions. GitHub's documentation additionally says the caller must be an organization owner. A PAT-based collection simply cannot enumerate installed Apps; it does not get a permission error, it gets a 404, which is easy to misread as "no installations".

This is one half of the credential asymmetry that runs through this plugin. The App uniquely sees installations, PAT grants and organization membership; an owner-minted PAT uniquely sees ruleset `bypass_actors`. **Neither dominates.** A complete picture needs both, or names the gap.

**Not observable at all:** OAuth applications authorised for an organisation have no REST or GraphQL endpoint — UI only, and a genuine gap. App *activity* would come from the audit log, which is GitHub Enterprise Cloud only.

## Authoritative Source

- **Source:** GitHub REST API — App installations for an organization (`GET /orgs/{org}/installations`) and the Apps API; the fine-grained personal-access-token permissions reference for what a PAT cannot reach
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27 (the App-only 404 verified by execution against our own organization, not read from documentation)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — 7 sources for `app_installation`; the ruling that it splits from `github_app` because the application and the grant are different objects.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §3.9 (2026-08-27) — the App-only rows in the verified API-surface table, including the OAuth-apps gap.
- `specs/spec-github-core-v0.md` `req-github-core-app-auth` (2026-08-27) — why the App is the product credential, and how its permissions are derived from the collection manifest rather than hand-listed.
- GitHub REST API, version `2022-11-28` — Apps and installation endpoints.

## Fields

- `slug` — the natural key and only required field. The URL-safe name that also appears in bot actor names, which is what lets an app node be minted from a mention rather than from an installation listing.
- `name` — the app's display name. Human-facing; not stable enough to key on.
- `app_id` — GitHub's numeric app id. Not the key, carried so a slug change reads as continuity.
- `html_url` — the app's marketplace or profile URL, for linking out and for a human to judge what the app is.
- `description` — the app's self-description as GitHub returns it. Vendor-supplied prose, useful for a human deciding whether an installed app is expected.
- `configuration` — JSONB for the remainder, and the honest holding place for installation-shaped data until `app_installation` exists. Anything that belongs to the *grant* rather than the application sits here on borrowed time.
- `tags` — TAP's tag map.
