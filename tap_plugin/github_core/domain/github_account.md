# GitHub Account

## Blurb

A GitHub user **or** organization — one type for both, because on GitHub they are the same kind of thing wearing different clothes.

## Purpose

Everything on GitHub is owned by an account. A repository's `full_name` is `<account>/<repo>`; a workflow runs as an actor that is an account; an organization's settings are the ceiling every repository beneath it inherits. The account is the second link in the containment chain — [`HOSTS_ACCOUNT`](HOSTS_ACCOUNT.md) puts it under the platform, [`OWNS_REPO`](OWNS_REPO.md) puts repositories under it — and it is the scope the collector enumerates from (`req-github-core-org-scope`).

## Goals

- Name the owner of every repository, so ownership is an edge rather than a substring of `full_name`.
- Be the enumeration root: with `owner` set, the collector lists that account's repositories.
- Hold the account kind, so a query can tell an organization's blast radius from a personal one.

## Identity

Natural key: **`login`** — the account name as GitHub spells it. Entity id is `uuid5(ns, "github_core__github_account:<login>")`.

Not `github_id`, even though the numeric id is the more stable identifier and is collected. The reason is reachability: `login` is what appears in `full_name`, in `owner/repo` paths, in workflow `uses:` references and in every URL, so keying on it lets a node be minted from any of those without a second API call. The numeric id is carried as a field precisely so a **rename** can be detected — the same `github_id` under a new `login` is a rename, and that is a real signal, not an accident.

This is the trade recorded honestly: a renamed account produces a new node. The grid's history carries the old one, and `github_id` is what joins them.

## Boundaries

Deliberately **not** covered:

- **The user/organization split.** One type, one table. GitHub's API returns the same shape from `/users/{login}` and `/orgs/{login}` (the collector falls back from one to the other), and 14 of the surveyed sources model them together. `account_type` carries the difference. The corpus marks this merge as deliberate.
- **Membership and teams.** `github_team` and `MEMBER_OF_ORG` are named in the corpus at the *friends* tier and are not built here. An account's members are not a field on the account.
- **Organization security settings** — 2FA requirement, default repository permission, member repository-creation policy. Real and useful, but they are org-level *policy*, and the corpus's rejected-candidates table rules policy objects to be fields rather than nodes because nothing points at them. They belong in `configuration` when collected, not in a new type.
- **Non-human actors.** A bot or App acting on the platform is not this type; `identity_core__principal` is the corpus's home for that, at the *later* tier.

## Neutrality

**Partial.** The corpus marks it `partial`: every forge has an account concept and a neutral substrate could own it, but this type's merge of user and organization is a GitHub-shaped decision — a forge that models them as genuinely different objects would not inherit it cleanly. Neutral in outline, vendor-specific in exactly the property that matters.

## Observability

Populated from `GET /users/{owner}`, falling back to `GET /orgs/{owner}` on 404, at **`repository:metadata:read`** — the cheapest permission in the manifest. Scope enumeration uses `GET /orgs/{owner}/repos` with the same fallback and permission, paginated to the end of the `Link` chain; the run records whether that walk completed, so an incomplete enumeration is labelled rather than silently read as a small account (`req-github-core-org-scope-3`).

**Not observable here, and the asymmetry matters.** Two of the most valuable account-level surfaces are **GitHub App only** and return 404 to any personal access token, proven against our own organization on 2026-08-27:

- fine-grained **PAT grants** into the organization (`GET /orgs/{org}/personal-access-tokens`)
- installed **App installations** (`GET /orgs/{org}/installations`)

The reverse also holds — an owner-minted PAT sees ruleset `bypass_actors` and a read-only App does not (the corpus's open question 3, settled empirically on the same date: `specs/spec-github-core-vocabulary.md`). **Neither credential dominates.** Do not tell an adopter the App is strictly better; a complete account picture needs both, or accepts a named gap.

Also unobservable: OAuth applications authorised for the organization have **no REST or GraphQL endpoint at all** — UI only. Members lacking 2FA and full organization security detail require organization-owner access.

## Authoritative Source

- **Source:** GitHub REST API — Users (`GET /users/{username}`), Organizations (`GET /orgs/{org}`), and the fine-grained personal-access-token permissions reference
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27 (permission and App-only findings verified by execution against our own organization on this date)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — 14 independent sources model an account concept; records the user/organization merge as deliberate and marks the type `partial` on neutrality.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §3.9 (2026-08-27) — the verified API-surface table: which account-level endpoints a read-only credential can reach, and the App-only rows.
- GitHub REST API, version `2022-11-28` — Users and Organizations endpoints; the `/users` → `/orgs` shape equivalence this type's merge rests on.

## Fields

- `login` — the natural key and the only required field. What every path, URL and `full_name` spells.
- `github_id` — GitHub's numeric account id. Not the key (see Identity), carried so that a `login` change under a stable id reads as a **rename** rather than as a new account.
- `account_type` — `User` or `Organization`. The one property that survives the merge of two GitHub concepts into one type; without it the merge would lose information rather than simplify.
- `html_url` — the account's browser URL, for linking out of a view.
- `configuration` — JSONB for account detail that has not earned a column, and the correct home for organization policy settings when they are collected (per the corpus's ruling that policy objects are fields, not nodes).
- `tags` — TAP's own tag map, uniform across every model.
