# GitHub Ruleset

## Blurb

The enforcement gate on a repository's refs — the control that decides whether a push, a branch or a tag is allowed, and the thing an auditor actually means when they ask "is this repository protected?"

## Purpose

Seven of the surveyed sources model a ruleset, and it is the node the whole control question turns on. A repository with a clean workflow set and no gate on its default branch is not protected; a ruleset in `EVALUATE` mode looks like a control and enforces nothing.

The vocabulary corpus uses this type as its cleanest illustration of the node test — the rule that a concept earns a node only if something needs to point at it. Organisation and Actions **policy** objects were rejected as fields precisely because nothing points at them, and `github_ruleset` was accepted because many repositories do. Measured on our own organisation: 19 repositories, 6 rulesets, **60 attachments**. It is 6 nodes and 60 edges, not 60 rulesets.

It is also the gateway type. Every other ruleset surface — the bypass-actor list, the rule suites that record actual bypass events, and version history — is keyed by the ruleset id, so nothing beyond the list was reachable until this node existed to hold it.

## Goals

- Give the control question one node per gate, shared by every repository the gate covers.
- Carry `enforcement`, so "the control exists" and "the control is enforcing" are different answers.
- Carry `source_type`, which decides both how the gate is administered and which credential can read its history.
- Hold the ruleset id, which unlocks the bypass, rule-suite and history surfaces.

## Identity

Natural key: **GitHub's `databaseId` alone**, not scoped by repository. Entity id is `uuid5(ns, "github_core__github_ruleset:<ruleset_id>")`.

Unscoped is the whole point. An organisation-sourced ruleset is one rule set that GitHub projects onto every repository in scope, so the same id comes back from many repositories' ruleset lists; keying by repository would derive the same ruleset's facts 19 times and let the copies disagree. This is the same shape as [`github_app`](github_app.md) keying on slug alone.

**The uniqueness of `databaseId` was verified, not assumed** — it can never be changed after the key sets, so it was worth the check. Two ways it could have failed, both tested against real data on 2026-08-27:

- *Separate organisation and repository id sequences.* **Refuted by interleaving.** Sorted, the six ids alternate between sources: an org ruleset (`20595566`), then three repository rulesets (`20607443`, `20607444`, `20613528`), then two more org rulesets (`20851132`, `21242695`). One allocator, not two.
- *Per-organisation sequences colliding across organisations.* **Refuted by magnitude and by monotonicity with creation time.** Id order is exactly `created_at` order with no inversions; `20607443` and `20607444` are consecutive integers allocated 0.44 seconds apart; and an organisation owning six rulesets holds ids around 20.6 million, where a per-org sequence would have numbered them 1–6. Gaps scale with elapsed wall-clock — roughly 11,900 ids across 15.4 hours, roughly 392,000 across nine days — a steady global allocation rate.

Conclusion: one global sequence across all of GitHub, shared by organisation- and repository-sourced rulesets. If GitHub ever changes that, the fallback is `<source_type>:<source_name>#<ruleset_id>`, which preserves the 60→6 collapse while disambiguating.

## Boundaries

Deliberately **not** covered:

- **The attachment to a repository.** There is no edge from a repository to a ruleset yet. This is a known gap in the vocabulary corpus and is sharper than a missing row: corpus line 163 justifies this very node with "which many repositories point at", so the node test is currently passed by an edge that does not exist. The mint is George's, and the direction, properties and name are open. Until it lands, the many-to-one relationship this node is built around is not traversable.
- **Bypass actors.** Who is exempt from the gate is the corpus's `BYPASSES` edge, with `observable` mandatory so "nobody can bypass" and "we could not look" cannot render alike. Not built, and see Observability for why it is hard.
- **Rules themselves.** The individual requirements — required reviews, signed commits, linear history — live in `configuration`. Required status checks are the corpus's `REQUIRES_CHECK` edge to a `status_check` node, neither built.
- **Classic branch protection.** A separate, older GitHub mechanism with its own endpoint and its own `enforce_admins` flag. Not this type, and a repository can have one, both, or neither.
- **Bypass *events*.** The rule-suite endpoints record who actually bypassed which rule when. Reachable (see Observability) and not collected — detection rather than enumeration, and a genuinely different question from the exemption list.

## Neutrality

**Vendor-specific.** The corpus marks it `no`. Every forge has some branch-protection concept, but rulesets are GitHub's own model — org-level projection onto repositories, `EVALUATE` mode, ref-targeting by `BRANCH`/`TAG`/`PUSH` — and the projection semantics in particular do not survive translation. A neutral substrate would need its own gate abstraction rather than a rename of this one.

## Observability

Collected via **GraphQL**, and the cost difference is the reason: `databaseId` and `source` for 19 repositories cost **1 rate-limit point** against roughly 80 REST calls and 51 seconds for the identical result — 60 attachments, 6 distinct ids, 0 nulls. That is the opposite of the [`github_actions_run`](github_actions_run.md) situation, where GraphQL exposes no Actions runs or jobs at all and REST is the only road. **GraphQL serves GitHub's configuration layer; REST serves its operation layer**, and this type sits on the configuration side.

**The four-endpoint table.** One property, four behaviours, measured for a read-only GitHub App holding `administration: read`:

| Endpoint | Embeds `bypass_actors`? | Result |
| --- | :---: | --- |
| `rulesets/rule-suites` | no | 200 |
| `rulesets` (list) | no | 200 |
| `rulesets/{id}` (detail) | yes | 200, **field silently stripped** |
| `rulesets/{id}/history` | yes | **403** |

GitHub documents the strip, verbatim from "Get a repository ruleset": *"To prevent leaking sensitive information, the `bypass_actors` property is only returned if the user making the API request has write access to the ruleset."* Measured against our own organisation, an owner-minted fine-grained PAT sees the field and a read-only App does not.

**The 403 on `/history` is a separate and unexplained matter**, parked as `tap#192`. Do not fold the two together — one is a documented field-level redaction returning 200, the other is a whole-endpoint refusal — and **do not resolve either by requesting write access**. Seeing the exemption list requires write access to the thing being audited; that is a limitation to publish, not to engineer around.

**`source_type` is operationally load-bearing, not descriptive.** Version history for an *organisation*-sourced ruleset is not reachable by the repository path at all — it 404s — and requires organisation scope. On our organisation that is 57 of 60 attachments, so for any organisation doing protection properly at the org level, history is an org-scope operation.

**Neither credential dominates.** The App uniquely sees App installations and organisation PAT grants; an owner-minted PAT uniquely sees `bypass_actors`. A complete gate picture needs both, or names the gap.

**A trap worth knowing:** the rule-suite endpoints default `time_period` to `day`. A query that omits it silently returns one day of evaluations and reads as a quiet repository.

**The gate is real, but it is not what the four-endpoint table alone suggests. Settled by experiment 2026-08-27.** A probe ruleset carrying one bypass actor (`RepositoryRole` 5, `bypass_mode: always`) was created on a personal repository, read with every available credential, and deleted:

| Credential | REST `/rulesets/{id}` | GraphQL `bypassActors` |
| --- | --- | --- |
| Admin token | actor returned | actor returned |
| Fine-grained PAT | **actor returned**, key present | **actor returned**, no `errors` |

So **GitHub surfaces bypass actors to a credential that clears the write bar**, over both transports. Read the scope of that precisely: both credentials in the table clear the bar, so the experiment shows what an *authorised* caller receives. It does **not** show how either transport behaves toward a caller that is refused — which is the only case gating is about, and the case this product is in. In particular, "GraphQL does not gate differently from REST" is **not** established: it is proven only where neither gates at all.

**The mechanism, and why the read-only App still cannot see them.** A fine-grained PAT is attached to a *user* and inherits that user's role on some surfaces. (The evidence first cited for this — `permissions.admin: true` on `GET /repos/{o}/{r}` — does **not** support it: that block reports the *user's* role by construction, so it is circular. The token's access is genuinely **mixed**, verified 2026-08-27: `403 Resource not accessible by personal access token` on `/actions/secrets` and `/actions/variables` inside its own declared scope, while `/actions/runners`, `/rulesets` and `/collaborators` return 200. Which surface answers to the grant and which to the inherited role has not been mapped.) A GitHub App installation has no user to inherit from: it holds exactly the permissions granted, and with `administration: read` it does not clear the documented "write access to the ruleset" bar. That is the asymmetry, stated as a mechanism rather than as a coincidence of credentials.

The consequence for this product is direct. Bypass actors are **not** unobservable in general; they are unobservable **to the credential this product chose**. `req-github-core-app-auth` makes the App the product credential precisely because two other surfaces are App-only, and this is the price. Three honest options, none of them free: mint the App with `administration: write` (abandons read-only on the one permission that matters most); collect this surface with an admin-attached PAT alongside the App (two credentials, and the operator must understand why); or publish the gap and rely on rule-suite *events* for detection instead of enumeration.

**And in the refused case the transports may not agree — which inverts the earlier worry.** The only evidence anyone holds about a *refused* caller comes from the read-only App, and there the two transports did not look alike: REST **omitted** `bypass_actors` entirely, while GraphQL returned `bypassActors` with `totalCount: 0` and **no `errors` entry**. If that holds against a ruleset that genuinely carries an actor, then GraphQL answers *zero* where REST answers *nothing* — and an empty GraphQL bypass list is a lie this product would repeat, because omission is detectable and a confident zero is not. Being exact, since looseness is what caused this whole thread: the organisation measured had genuinely zero bypass actors, so **nothing in that data separates a truthful zero from a filtered one.** Suggestive, not proof.

**The rule this implies for the collector — and it is not "avoid the field".** The config layer runs on GraphQL (`req-github-core-graphql-config`). On `main` the ruleset selection carries no bypass data; on `feat/self-vocabulary` it already selects `bypassActors` with `totalCount` and the actor nodes, landed alongside the `BYPASSES` edge (verified against `origin/feat/self-vocabulary`). Selecting it is **correct**: the non-empty case is the only self-proving evidence that exists, so refusing to ask guarantees you never learn anything.

What makes it safe is refusing to trust the answer. That branch returns `bypass_proven`, true only for a **non-empty** list; marks a ruleset `observed` only where REST carried the key *or* GraphQL returned non-empty; and otherwise records `unobservable` with a **null** count and a per-ruleset warning. The live collection is the proof it works — all six rulesets landed `unobservable` with a null count, where a naive selection would have landed six confident zeroes.

So the standing rule is: **never let an empty answer from `bypassActors` become a count.** Keep the field, keep the distrust.

**Still unproven, and it is the case that matters.** The App half of the table has not been measured against a ruleset that actually carries an actor — every previous App observation was taken where the list was genuinely empty, which cannot discriminate. Until it is: keep `observable` on the `BYPASSES` edge at full strength, and **never render an App-sourced empty bypass list as "nobody can bypass"**. "Bypass actors are observable" and "the App cannot see them" are not in conflict; the first is true only of credentials that clear the write bar, and the product's credential does not.

**Two things the same pass settled outright.** The `403` on `/rulesets/{id}/history` is **not App-specific** — a fine-grained PAT gets it too, with the message *"Resource not accessible by personal access token"* — so `tap#192` is broader than first recorded. And `current_user_can_bypass` is returned to every credential tried: it answers "can *this* credential bypass" even where the actor list cannot be read, which is one honest row where the full list is unavailable.

**Not observable:** who may bypass, without write access; anything at all through an unauthenticated path. And an empty ruleset set is ambiguous in the reassuring direction — it means "no gates" or "we could not look" — so any view built on this type owes its reader three states: none, some, and not-observable.

## Authoritative Source

- **Source:** GitHub GraphQL API — `RepositoryRuleset` (`databaseId`, `source`, `enforcement`, `target`); GitHub REST API — Repository rules (`GET /repos/{o}/{r}/rulesets`, `/rulesets/{id}`, `/rulesets/{id}/history`, `/rulesets/rule-suites`); the fine-grained personal-access-token permissions reference
- **Version:** REST API version `2022-11-28`; GraphQL schema as served 2026-08-27
- **Retrieved:** 2026-08-27 (the four-endpoint behaviours, the id-uniqueness evidence and the GraphQL cost were all established by executing calls against our own organisation, not read from documentation)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — 7 sources; the node test illustrated by contrast with rejected policy objects; open question 3, settled empirically, on `bypass_actors` and the mandatory `observable` property.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §3.9–3.10 (2026-08-27) — the verified API-surface rows for org and repo rulesets, and the branch-protection observable conditions with their control-framework provenance.
- GitHub REST API version `2022-11-28`, Repository rules endpoints; GitHub GraphQL `RepositoryRuleset`, schema as served 2026-08-27.

## Fields

- `ruleset_id` — GitHub's `databaseId`, and the load-bearing field on this node. It is the natural key (see Identity) and the id every other ruleset surface is addressed by: bypass actors, rule suites, version history. Nullable, because a ruleset can be known from a listing before its id is resolved — and the config-layer query returned rulesets *without* it until this work, which is precisely why none of those surfaces were reachable.
- `name` — the ruleset's name, and the only required field at creation. Operator-chosen and not unique across sources, so display rather than identity. It is `minLength: 1` validated, which makes it the one field on this node where a degraded read cannot be represented by an empty string — so a ruleset GitHub returns without a name **falls back to its `databaseId`**, for the field and for the entity name alike. That fallback is not cosmetic: without it a nameless ruleset fails validation and is dropped silently, which is exactly the "a degraded field discards the whole ruleset" failure `req-github-core-ruleset-6` forbids. Pinned by `test_nameless_ruleset_still_lands`.
- `enforcement` — `ACTIVE`, `EVALUATE` or `DISABLED`, enumerated against GitHub's closed `RuleEnforcement` set. The field that separates "a control exists" from "a control is enforcing": `EVALUATE` reports violations without blocking, which is a control-shaped object that stops nothing, and is called out as an observable condition in its own right.
- `target` — `BRANCH`, `TAG` or `PUSH`, enumerated against GitHub's `RepositoryRulesetTarget`. Tag targeting matters more than it looks: tag movement is the detection for three incidents in the corpus, and is why the corpus reshaped `git_branch` into a `git_ref` covering branches and tags together.
- `source_type` — `Organization` or `Repository`. A **node** property rather than an edge property: it is one fact about the ruleset, true across all 60 attachments, and putting it on the edge would derive it 57 times over and let the copies disagree. Operationally load-bearing — see Observability for why it decides which credential can read history.
- `source` — the organisation or repository the ruleset is defined on. With `source_type` it says where to administer the gate, and it is the disambiguator in the fallback key if `databaseId` uniqueness ever fails.
- `owner_login` — **`owner_login` is the identity input, and it is why there are 6 nodes and not 60.** A ruleset is keyed on its owner plus GitHub's ruleset id, never on the repository it was seen from — an organisation ruleset is one object that many repositories point at. Keying it per repository would turn "what does this ruleset protect" into a string comparison across duplicates, and the 60 attachments measured on our own organisation would have become 60 rulesets.
- `source` — **`source` names where the ruleset is defined; `source_type` names what kind of thing that is.** The pair is what distinguishes an organisation ruleset applying to 19 repositories from a repository ruleset applying to one. Read `source_type` first: it is the field that decides whether version history is reachable by the repository path at all.
- `conditions` — **`conditions` is stored exactly as GitHub returns it, tokens and all.** `~DEFAULT_BRANCH` and `~ALL` are kept verbatim rather than resolved at collection time, because resolving them would freeze one moment's answer into a field that outlives it — the default branch can be renamed, and a ruleset that said "the default branch" would silently come to mean the old one. Resolution happens on the edge instead: `PROTECTS` carries `match_kind: resolved` and the pattern that matched, so intent and effect are both queryable and neither is inferred from the other.
- `rules` — **`rules` is the array as returned, with each rule's parameters intact.** This is the field that makes the type a model rather than a label. The required status checks live here (`rules[].parameters.required_status_checks[].context`, ours reads `{"context": "gate", "integration_id": 15368}`), and a gate view that knows a repository requires *some* check but not *which* is not a gate view. It is populated from the REST detail; the GraphQL config layer returns rule *types* only, and the collector falls back to that type-only list rather than to nothing, warning when it does.
- `bypass_observability` — **`bypass_observability` carries the third state, and it exists because a blank cell lies.** It is `observed` only when the REST detail carried the `bypass_actors` key **or** GraphQL returned a non-empty list; otherwise `unobservable`. The asymmetry is deliberate: a non-empty answer proves itself, since a filtered connection can hide actors but cannot invent them, while an empty one proves nothing. False presence is impossible here; false absence is the entire risk. This lives on the node rather than on the `BYPASSES` edge because when the answer is *none* or *unknown* there are no edges — a view reading edges alone would render both as an empty list, and "nobody can bypass" is the most reassuring thing a security product can say.
- `bypass_actor_count` — **`bypass_actor_count` is nullable, and null is not zero.** Zero is a claim that nobody holds an exemption. Null is the absence of a claim, and it is the honest value whenever `bypass_observability` is `unobservable`. Measured on our own organisation with a read-only App: all six rulesets landed null, where a naive read of the same GraphQL response would have landed six confident zeroes.
- `html_url` — **`html_url` is the ruleset's own settings page**, taken from the REST detail's `_links.html.href`, so a reader who wants to change what a row describes has one click rather than a search. Empty when only the GraphQL config layer answered.
- `configuration` — JSONB retained alongside the typed `conditions` and `rules` fields, for whatever the API returns that those two do not yet lift out. It is the residue, not the gate: anything a view needs to query belongs in a column, and the corpus's `REQUIRES_CHECK` -> `status_check` story is the next thing due to come out of here.
- `tags` — TAP's own tag map, uniform across every model.

Note on the enumerated fields: `""` is permitted alongside each closed set so a partially-read ruleset lands rather than being dropped. That is the grid's unobserved convention — an empty string here means "we did not read it", not "GitHub returned nothing".
