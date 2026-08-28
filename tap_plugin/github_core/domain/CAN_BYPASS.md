# CAN_BYPASS

## Blurb

An actor is exempt from a ruleset — permitted to push past a gate that stops everyone else. The standing permission, where `HAS_BYPASSED` is the occurrence.

## Purpose

A gate is only as strong as its exemption list. A ruleset requiring review, checks and linear history means nothing to an actor on its bypass list, so "what protects `main`" cannot be answered by reading rules alone — it needs the exemptions too.

This edge exists to make that answerable, and to make its **absence honest**. It is the most frequently unreadable relationship in this vocabulary, and the discipline around that unreadability is most of its design.

## Goals

1. Name who is exempt from a gate, where the credential can see it.
2. Never let "we could not look" render as "nobody is exempt".
3. Stay separate from `HAS_BYPASSED`, so permission and occurrence cannot be conflated.

## Identity

Derived: `uuid5(ns, "CAN_BYPASS__github_core:<actor_uuid>:<ruleset_uuid>")`.

## Boundaries

- **Not `HAS_BYPASSED`.** This is *may*; that is *did*. The names were `BYPASSES` and `BYPASSED` until 2026-08-28 — one letter apart, for the pair whose whole value is the distinction. The modal/perfect forms make the difference visible everywhere the slug appears.
- **Not all exemption kinds are nodes.** Apps have `github_app`; teams and organization-admin roles do not yet, so those are kept as counted data on the ruleset rather than dropped. Understating who can bypass is the one direction that must never happen.
- **Not authorisation.** That an actor is on the list does not say who put them there or why. Ruleset history carries that, and is refused to every read-only credential.

## Neutrality

Vendor-specific, following `github_ruleset`. Exemption-from-a-gate is a general idea, but the object and its exemption model are GitHub's.

## Observability

**This is the hard one, and the difficulty is the point.** GitHub returns a ruleset's `bypass_actors` only to a caller with **write access to the ruleset** — quoted from their docs, *"to prevent leaking sensitive information"*. The rationale tracks: naming who may bypass a control makes those accounts targets.

Measured 2026-08-28 against probe ruleset `21756020`, carrying one real actor, with four zero-actor rulesets as control in the same response:

| Credential | REST `/rulesets/{id}` | GraphQL `bypassActors` |
| --- | --- | --- |
| Fine-grained PAT (owner-attached) | key present, full detail | `totalCount=1`, node populated |
| Read-only App (`administration: read`) | **key ABSENT** | **`totalCount: 1`, `nodes: [null]`, no `errors`** |

Three things follow, and only the first was previously understood:

1. **The App cannot name exempt actors.** Confirmed against a non-empty list, which no earlier measurement had.
2. **The App CAN count them.** GraphQL returns a truthful `totalCount` — `0` on the controls, `1` on the probe — while nulling every node. So "nobody is exempt" and "someone is, and I cannot see who" are *distinguishable*, and the product need not be blind to existence.
3. **GitHub does not signal the redaction.** No `errors` array, no partial marker. A caller that filters falsy nodes sees an empty list and nothing else, which is why `bypass_observability` lives on the ruleset node rather than being inferred from this edge's absence.

**Alternative routes, tested and closed:** the org-level rulesets endpoint returns **403** to an App holding `organization_administration: read`; ruleset history returns **403** to both an App and a fine-grained PAT; the audit-log API is Enterprise Cloud only and 404s on a Team plan even for an owner.

**Absence of this edge proves nothing on its own.** Read `bypass_observability` on the ruleset first.

## Authoritative Source

- **Source:** GitHub REST API — Rules (`GET /repos/{owner}/{repo}/rulesets/{ruleset_id}`) and the GraphQL `RepositoryRuleset.bypassActors` connection
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-28 (matrix measured against probe ruleset `21756020` on `unified-systems-com/tap`)

## Prior Art

- GitHub REST API, version `2022-11-28` — *Get a repository ruleset*, for the write-access restriction quoted above.
- GitHub community discussions [#152059](https://github.com/orgs/community/discussions/152059) and [#72148](https://github.com/orgs/community/discussions/72148) (both checked 2026-08-28, both still open) — requests to read bypass actors without write access.
- `cartography` (CNCF), `cartography/intel/github/repos.py` (`main`, read 2026-08-28) — drops `bypass_actors` entirely rather than model it, with the same reasoning. The prior art for the gap; this edge plus `bypass_observability` is the attempt to do better than dropping it.
- `specs/spec-github-core-v0.md` `req-github-core-ruleset` (2026-08-27) — the three-state observability requirement.

## Endpoints

- **Source:** `github_core__github_app` — an exempt actor this vocabulary has a node for.
- **Target:** `github_core__github_ruleset` — the gate it is exempt from.
- **Dimensions:** `github.platform`, `github.surface: rules`, `github.observation: declaration`.
- **Properties:** `actor_type`, `bypass_mode` (`always` or `pull_request`), `observable` (whether the list was actually read), `source` (which transport answered).
