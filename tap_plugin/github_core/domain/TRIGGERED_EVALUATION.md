# TRIGGERED_EVALUATION

## Blurb

The account whose push a rule suite evaluated. An account observed, never an identity inferred — GitHub says who pushed, not what kind of thing they are.

## Purpose

A bypass event without an actor is a log line. This edge is what makes it a finding: it names the account that went around a control, and joins it to everything else that account touches on the grid — the repositories it owns, the Apps installed under it, other bypasses elsewhere.

It is also the edge that makes "who bypasses gates around here" a graph question rather than a report. One account with `BYPASSED_RULE` edges across nineteen repositories is a different picture from nineteen accounts with one each, and only the graph shows the difference at a glance.

## Goals

1. Attribute a bypass to an account, using the same account primitive the rest of the plugin uses.
2. Claim nothing about that account beyond what GitHub returned.
3. Survive a rename, by carrying the numeric id alongside the login.

## Identity

Derived: `uuid5(ns, "TRIGGERED_EVALUATION__github_core:<account_uuid>:<suite_uuid>")`, like every edge here. A suite has exactly one pusher, so the pair is unique by construction.

## Boundaries

- **Not an identity claim.** The target is `github_account` — GitHub's user-or-organization primitive, merged on purpose — because the API returns a login and an id and nothing else. Whether it is a person, a bot, a machine account or an App acting on someone's behalf is *not stated*, and this edge does not guess. `identity_core__principal` may later carry the human-versus-robot distinction; nothing here pre-empts it.
- **Not authorship.** The pusher is who pushed, which is not necessarily who wrote the commits. Author and committer live on the commit, which this plugin does not yet model.
- **Not permission.** That someone pushed past a gate does not establish they were entitled to. `BYPASSES` carries entitlement; this carries occurrence.

## Neutrality

The relationship is neutral — every forge records who pushed — but the endpoint and the account primitive are GitHub's. The edge is named for the act rather than the platform so a second implementation could reuse it.

## Observability

Populated from the rule-suite listing at **`repository:administration:read`**: `actor_name` and `actor_id` on each suite, both returned in the same response that reports the bypass.

**This is the part that works where enumeration does not.** A read-only App installation token is refused the ruleset's `bypass_actors` list entirely, yet receives actor names here. Measured 2026-08-28 on `unified-systems-com/tap`: ten bypass events, every one carrying a login and an id.

**Not observable:** anything about the account beyond login and id from this surface — no type, no membership, no whether it still exists. The account node is minted with `account_type` left empty rather than guessed, and a later collection of the account itself fills it in.

## Authoritative Source

- **Source:** GitHub REST API — Rules (`GET /repos/{owner}/{repo}/rulesets/rule-suites`)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-28 (captured live; `tests/fixtures/rule_suites.json`)

## Prior Art

- GitHub REST API, version `2022-11-28` — *Get rule suites for a repository*.
- `specs/spec-github-core-v0.md` `req-github-core-account` — the deliberate user/organization merge this edge relies on.
- `specs/spec-github-core-vocabulary.md` (2026-08-27) — `identity_core__principal` proposed as the robot/non-human actor concept, which is why this edge deliberately does not classify.

## Endpoints

- **Source:** `github_core__github_account` — the account that pushed.
- **Target:** `github_core__rule_suite` — the evaluation its push triggered.
- **Dimensions:** `github.platform`, `github.surface: rules`, `github.observation: execution`.
- **Properties:** `actor_id` — GitHub's numeric account id at the time of the push, so a login rename is detectable.
