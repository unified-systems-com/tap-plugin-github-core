# rule_suite

## Blurb

One push evaluated against the rulesets matching its ref — who pushed, onto which ref, and which controls were bypassed rather than satisfied. The record of who *did*, where `github_ruleset` can only record who *may*.

## Purpose

`github_ruleset` describes a gate and runs into a documented wall: GitHub returns a ruleset's `bypass_actors` only to a caller with **write** access to that ruleset, "to prevent leaking sensitive information". Every read-only credential — App or fine-grained token — is refused. That limitation is real, it is deliberate on GitHub's part (naming who may bypass a control makes those accounts targets), and it is published rather than engineered around.

This type answers the adjacent question instead, and answers it fully. Rule suites are pushes GitHub evaluated against a repository's rulesets, and the endpoint returns **200 to a read-only App installation token**, naming the actor of every one. Filtered to `result=bypass`, that is a list of people and machines that went around a control, when, on which ref, and which control it was.

For a security product this is usually the more useful of the two facts. "Who is permitted to bypass" is a configuration review; "who actually bypassed the required-checks gate on `main` ten times last month" is an incident.

## Goals

1. Make bypass **detectable** with the credential the product already recommends, where bypass **enumeration** is refused.
2. Name the specific control that was gone around, not merely that something was.
3. Keep the actor honest — an account observed, never an identity inferred.
4. Never let a refused read render as a quiet repository.

## Identity

Natural key: GitHub's **rule-suite `id`**. Entity id is `uuid5(ns, "github_core__rule_suite:<id>")`.

Not scoped by repository, deliberately. The id is assigned by GitHub and is unique across the platform, and the suite carries its own `repository_name`, so a repository prefix would add nothing while breaking the join if the same suite were ever reached by another path. This is the same reasoning that keys `app_installation` on the bare installation id.

The identity is stable because a rule suite is an **event**: it happened once, at a timestamp, and does not change afterwards. Unlike a ruleset, there is no later version of it to reconcile against.

## Boundaries

- **Only bypasses are collected.** `result` is retained as a field so the model can widen to `fail` or `pass` without a migration, but a passing suite is a routine push — roughly 47 a day on one active repository — and landing every one would swamp the grid to record that nothing happened.
- **Not a change record.** The vocabulary corpus rejects "change / snapshot / audit-event types" because the grid already carries field-level history, and that ruling is correct for changes to objects we collect. A rule suite is not a change to a ruleset; it is an occurrence in the world we observed, in the same category as `github_actions_run`. Modelling it duplicates nothing.
- **Not a commit.** `before_sha` and `after_sha` are fields, not edges. `git_commit` is a proposed type this plugin does not yet build; when it exists, these become the natural join.
- **Not an approval trail.** Whether the bypass was legitimate is not in the data. The event is the finding; the judgement is a human's.

## Neutrality

Vendor-specific and correctly so. "Rule suite" is GitHub's own object and its own word. A forge-neutral equivalent would be some notion of *a gate evaluation that was overridden*, which other platforms express differently or not at all — GitLab's push rules and protected-branch overrides are not the same shape. Inventing a neutral parent before a second implementation exists would be modelling an abstraction we have never tested.

## Observability

Populated from `GET /repos/{owner}/{repo}/rulesets/rule-suites` at **`repository:administration:read`**, with the per-suite detail at the same permission.

**This is the surface that works where the ruleset's own bypass list does not.** Measured on 2026-08-28 against `unified-systems-com/tap` with a read-only App installation token (`administration: read`): the listing returned **200** with ten bypass events in a month, each carrying `actor_name`, `actor_id`, `ref`, `before_sha`/`after_sha` and `pushed_at`; the detail returned the per-rule evaluations naming the ruleset gone around. The same credential is refused the ruleset's `bypass_actors` entirely — REST omits the key, GraphQL returns a truthful `totalCount` with `nodes: [null]`.

**The window is a trap.** `time_period` silently defaults to `day`. Measured on one repository: `day` returned 47 suites, `week` and `month` both hit the 100-item page cap. Omit the parameter and a repository with a month of bypasses reads as a quiet one — an absence rendering as a finished answer, arriving through a query default rather than a permission. The collector always sends it explicitly.

**Not observable:** whether a bypass was authorised, and by whom. GitHub records that the push went around the rule, not that anyone approved it. Also not observable from this surface: bypasses of controls that are not rulesets — classic branch protection overrides do not appear here.

**A refusal is recorded, never rendered as zero.** A 403 or 404 degrades with a warning naming the repository, because landing no bypass events on a permission failure would say "nobody bypassed anything", which is the most reassuring possible reading of not being allowed to look.

## Authoritative Source

- **Source:** GitHub REST API — Rules (`GET /repos/{owner}/{repo}/rulesets/rule-suites`, `GET /repos/{owner}/{repo}/rulesets/rule-suites/{rule_suite_id}`)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-28 (responses captured live and committed as `tests/fixtures/rule_suites.json`; the bypass-visibility matrix measured against probe ruleset `21756020`)

## Prior Art

- GitHub REST API, version `2022-11-28` — *Get rule suites for a repository*, and *Get a repository ruleset* for the `bypass_actors` write-access restriction, quoted verbatim in `req-github-core-rule-suites`.
- GitHub community discussions [#152059](https://github.com/orgs/community/discussions/152059) and [#72148](https://github.com/orgs/community/discussions/72148) (both checked 2026-08-28, both still open) — open requests to read bypass actors without write access. Still open, which is why the ceiling is published rather than worked around.
- `cartography` (CNCF), `cartography/intel/github/repos.py` (`main`, read 2026-08-28) — an independent security-graph project that collects rulesets and supports App auth, and drops `bypass_actors` entirely with the comment *"GitHub only returns it to callers with write access to the ruleset, and Cartography is expected to run read-only."* It does not collect rule suites, so it has no detection story to set against the enumeration gap.
- `specs/spec-github-core-vocabulary.md` (2026-08-27) — the rejection of change/audit-event types, and the note that "rule-suite / rule-insights endpoints may expose actual bypass *events* even where the actor list" is refused. This type is that note built.

## Fields

- `suite_id` — GitHub's rule-suite id, and the natural key. Nullable only because the grid's create contract wants every field declared; a suite without it is not a suite.
- `full_name` — `owner/repo`, carried so a suite is attributable without walking edges. The API returns `repository_name` and `repository_id`; the composite form is what the rest of this plugin keys on.
- `result` — `bypass`, `fail` or `pass`. Only `bypass` is collected today. `""` is permitted so a partially-read suite lands rather than being dropped, per the grid's unobserved convention.
- `ref` — the full ref path (`refs/heads/main`) as returned, matching `git_ref`'s identity so the `EVALUATED_ON_REF` join is a lookup rather than a reconstruction.
- `actor_login` — the account that pushed. An **account**, not an identity: GitHub returns a login and a numeric id and does not say whether it belongs to a person, a bot or a machine account, so nothing here claims one. See `TRIGGERED_EVALUATION`.
- `actor_id` — GitHub's numeric account id, carried so a login rename is detectable — the same id under a new login is a rename, not a new actor. Same reasoning as `github_account.github_id`.
- `before_sha` / `after_sha` — the ref tip either side of the push. All zeroes in `before_sha` means a branch creation. Fields rather than edges because `git_commit` is not built.
- `pushed_at` — when the push was evaluated. Null is "we did not observe a timestamp", never "now".
- `bypassed_rules` — the evaluations that were not satisfied, each carrying `rule_type`, `enforcement`, the `ruleset_id`/`ruleset_name` it came from, and GitHub's own `details` string verbatim (`Required status check "gate" is expected.`). Kept as data because the `BYPASSED` edge carries only the ruleset join, and the rule type plus GitHub's explanation are what make the event readable. Empty when the detail call was refused — a degraded finding, not a dropped one.
- `configuration` — JSONB residue for what the API returns that is not lifted into a column.
- `tags` — TAP's own tag map, uniform across every model.
