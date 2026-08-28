# HAS_BYPASSED_RULE

## Blurb

A push went around this ruleset rather than satisfying it. Distinct from `BYPASSES`, and the distinction is the entire point: one is a standing permission, this is something that happened.

## Purpose

This edge is the join that turns a bypass event from a log line into a finding. The rule-suite listing says *a* bypass occurred; only this says **which control** was gone around — and a security question is almost never "did someone bypass something", it is "is the required-checks gate on `main` actually holding".

It is also the pair that makes the two halves of the bypass story queryable together. `BYPASSES` (who may) is frequently unreadable — GitHub returns bypass actors only to a caller with write access. `BYPASSED` (who did) is readable by the credential we recommend. A ruleset with no `BYPASSES` edges and several `BYPASSED` edges is not a contradiction; it is exactly what a read-only observer should expect to see, and reading it that way requires both edges to exist as separate types.

## Goals

1. Name the specific ruleset a push went around.
2. Stay rigorously separate from `BYPASSES`, so "permitted" and "occurred" can never be conflated in a query.
3. Carry enough of the evaluation — rule type, enforcement, GitHub's own explanation — that the edge is readable without re-fetching.

## Identity

Derived: `uuid5(ns, "HAS_BYPASSED_RULE__github_core:<suite_uuid>:<ruleset_uuid>")`. One suite may bypass several rules belonging to the same ruleset; that is one edge, with the first rule type on it and the full list on the suite's `bypassed_rules`.

## Boundaries

- **Not `BYPASSES`.** That edge runs from an actor to a ruleset and means *may bypass*. This runs from an event to a ruleset and means *did*. Merging them would make the most important distinction in this domain invisible.
- **Not a judgement.** The data does not say whether the bypass was authorised, reviewed, or an emergency. It says it happened.
- **Only ruleset-sourced evaluations.** A suite's evaluations can come from sources other than a ruleset — secret scanning, for instance. Those have no ruleset to point at and are kept on the suite's `bypassed_rules` rather than becoming dangling edges.

## Neutrality

Vendor-specific, following `github_ruleset`. The concept — an override of a merge gate — is general, but the object being overridden is GitHub's ruleset and the edge should not pretend otherwise until a second platform's equivalent is modelled.

## Observability

Populated from the per-suite detail at **`repository:administration:read`**: `rule_evaluations[]`, each carrying `rule_type`, `enforcement`, `result` and a `rule_source` that names the ruleset by id.

**Absent when the detail was refused, and that is recorded rather than silent.** The listing already proves a bypass occurred, so a refused detail degrades to a suite with an empty `bypassed_rules` and no `BYPASSED` edges — a finding with less resolution, not a finding dropped. A view must not read "no BYPASSED edges" as "no control was bypassed" without checking whether the suite has any bypassed rules at all.

**Not observable:** which rule *would* have caught the push had it not been bypassed, beyond what GitHub's own `details` string says. That string is preserved verbatim (`Required status check "gate" is expected.`) precisely because it names the specific check, and re-wording it would lose the only part that says *what* was skipped.

## Authoritative Source

- **Source:** GitHub REST API — Rules (`GET /repos/{owner}/{repo}/rulesets/rule-suites/{rule_suite_id}`)
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-28 (captured live; `tests/fixtures/rule_suites.json` carries a real four-evaluation detail, one failing)

## Prior Art

- GitHub REST API, version `2022-11-28` — *Get a rule suite*.
- GitHub REST API, *Get a repository ruleset* — the write-access restriction on `bypass_actors` that makes this edge the readable half of the story.
- `specs/spec-github-core-v0.md` `req-github-core-rule-suites-3` — the requirement that the bypassed control is named.
- `cartography` (CNCF) — collects rulesets, drops `bypass_actors`, and models no bypass occurrence at all. The prior art for the gap, not for the fix.

## Endpoints

- **Source:** `github_core__rule_suite` — the push that went around the gate.
- **Target:** `github_core__github_ruleset` — the gate it went around.
- **Dimensions:** `github.platform`, `github.surface: rules`, `github.observation: execution`.
- **Properties:** `rule_type` (the rule not satisfied), `enforcement` (its level at evaluation time — bypassing an `evaluate` rule means less), `details` (GitHub's own explanation, verbatim).
