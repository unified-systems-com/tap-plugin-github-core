# GitHub Core — Domain Vocabulary Corpus

**Status:** design record, not requirements. **Built:** 2026-08-27. **Method:**
[`build-domain-vocabulary`](../../../tap_grid/skills/build-domain-vocabulary/SKILL.md).

This is the answer to *which* base models and edges the CI/CD domain needs, and **why each one
earns its place**. It does not replace `req-github-core-models` / `req-github-core-edges` — those
remain the requirements. This is the record behind them: what was considered, what was accepted,
what was rejected and on what grounds, and which outside source demanded each concept.

**Read this before proposing a new type in this domain.** A concept already rejected here should
not be re-litigated without new evidence, and a concept accepted here already has its justification
written.

## How it was built

Four independent gathering passes, deliberately biased in different directions so their
disagreements would be informative:

| Pass | Direction | Report |
| --- | --- | --- |
| Incidents | 35 documented compromises + 40 observable conditions → the entities each *requires* | `doc-git-serious-cicd-security-prior-art.md` → `vocab-from-incidents` |
| Standards | 29 formal schemas, ontologies and control catalogues | `vocab-dictionaries-security` |
| Platform & tooling | 16 API/graph/IaC/query models incl. two published GitHub graph schemas | `vocab-dictionaries-platform` |
| Kernel pressure test | A structurally different implementation of the same domain | `doc-git-serious-linux-kernel-pipeline.md` |

Raw reports live in the product repo (`git-serious-tap/docs/`). **A concept named by three or more
independent sources is adopted; a concept named by one is interrogated.**

---

## The three findings that shaped everything else

**1. Declaration and execution are different objects, and almost nobody models both.**
`github_actions_job` is an *execution* — it carries `github.observation: "execution"`, keys on
`job_id`, and holds `status`/`conclusion`. The job as **written** — `permissions:`, `runs-on:`,
`environment:`, `if:`, which steps see which secret — has no node, and lives in an un-schema'd
`configuration` blob. Every privilege decision in CI is made at the declared level.

Two passes reached this independently: the incident pass found ~20 of 35 compromises need it; the
platform pass found that the published GitHub graph models declaration *only* and has no execution
node anywhere. **Only 8 of 16 surveyed sources model a pipeline run at all**, which makes spanning
both sides the distinguishing property of this vocabulary rather than a detail.

**2. Bare edges are ruled out by the field itself.** Three passes converged: the incident pass
(shape is not severity — an edge that records only *that* a relationship exists produces confident
nonsense in any view that scores risk); the platform pass (four sources independently reify *how a
permission arrived*); the standards pass (nine edge properties are **already standardised** —
lifecycle scope, completeness, threshold, identity confidence and technique, grant timestamps,
justification, enforcement level, continuity, author+timestamp). Every edge below therefore declares
its properties and the question each property settles.

**3. The naming territory is unclaimed.** The major security schema framework has **no** CI/CD
vocabulary — zero terms for pipeline, build, commit, branch or artifact, and none proposed. The
artifact and vulnerability halves of this world are thoroughly standardised; the machinery that
produces them is not. Where a standard *does* have a name we use it; where none does, we are early
rather than idiosyncratic, and we say so.

---

## Node inventory

**Tier:** `self` = understand our own pipeline · `friends` = first outside operator · `later`.
**Status:** `exists` · `proposed` (prior design pass) · `new` (this corpus).
**Neutral** = a structurally different forge or a non-forge project could populate it → belongs in a
neutral substrate when one is extracted, not in `github_core`.

| Slug | Neutral | Tier | Status | Why it earns its place |
| --- | :---: | --- | --- | --- |
| `github_platform` | no | self | exists | root of the inventory |
| `github_account` | partial | self | exists | 14 sources; user/org merge is deliberate |
| `github_repository` | **yes** | self | exists | **15 sources** — the single most-modelled concept in the domain |
| `github_workflow` | yes | self | exists | 12 sources; the pipeline definition |
| **`workflow_job`** | **yes** | **self** | **new** | **The largest gap.** Declared job: `permissions`, `runs-on`, `if`, checkout ref. ~20 incidents; 7 observable conditions; the anchor for every conjunction query |
| `github_actions_run` | yes | self | exists | 8 sources — **we are one of only 8** |
| `github_actions_job` | yes | self | exists | the *executed* job. Slug retained (slugs are identity and never rename); its relationship to `workflow_job` is stated below |
| **`git_ref`** | **yes** | **self** | **reshaped** | 12 sources. Replaces the never-built `git_branch`: branch **and tag** in one type, because tag movement is the detection for three incidents and rulesets already target `branch\|tag\|push` |
| `github_ruleset` | no | self | proposed | 7 sources; the gate itself |
| `status_check` | no | self | proposed | 6 sources; convergence node — required by rulesets, produced by workflows/apps |
| `github_action` | no | self | **exists** (2026-09-02, #45) | 4 sources, and one carries `is_pinned` — the same property we proposed independently. Keyed on the action path, platform-global; the pin lives on the edge |
| `actions_secret` | partial | self | proposed | **11 sources**; 12 incidents |
| **`actions_cache`** | neutral-capable | **self** | **new** | 5 incidents including the two most recent. Convergence node: written by a low-trust job, restored by a privileged one |
| `app_installation` | no | self | proposed | 7 sources. **Splits the existing `github_app`**: the registered application and the grant are different objects |
| `pull_request` | yes | self | proposed | 10 sources |
| `github_environment` | neutral-capable | self-lite | proposed | 10 sources; 7 standards |
| `github_release` | **yes** | self-lite | proposed | 6 sources |
| `github_runner` | no | self | exists | 9 sources |
| `github_app` | no | self | exists | keeps the *application*; the grant moves to `app_installation` |
| `identity_core__oidc_issuer` | yes | self | exists | we are ahead here — the published GitHub graph has no OIDC issuer at all |
| `git_commit` | **yes** | friends | **new** | 7 sources (as *revision*); narrow slice only — not a full commit history |
| `github_team` | no | friends | proposed | 10 sources |
| `credential_grant` → `identity_core` | **yes** | friends | **new** | **9 standards + 15 incidents.** One node with a `kind` enum, not four thin types — otherwise the one query the standards ask for verbatim requires a UNION |
| `runner_group` | no | friends | **new** | the published graph is ahead of us on runner scope |
| `actions_artifact` | neutral-capable | friends | **new** | 11 sources (as *artifact*) |
| `webhook` | neutral-capable | friends | proposed | 4 standards |
| `package` / `package_version` | **yes** | friends | **new** | **14 incidents, 10 sources.** Identity is a **purl** (the strongest convergence in the whole sweep) |
| `identity_core__principal` | **yes** | later | **new** | the *robot / non-human actor* concept clears the 3-source bar |
| `deployment` | neutral-capable | later | **new** | one incident |

### `workflow_job` vs `github_actions_job` — the distinction to hold

`workflow_job` is the job **as written** in the YAML; `github_actions_job` is a job **as run**. They
are joined by an edge (`INSTANCE_OF_JOB`), not merged. The published GitHub graph models only the
first and calls it `WorkflowJob`; we model both, which is the point. Existing slugs are never
renamed (the slug is load-bearing identity), so this distinction lives in documentation and in the
`github.observation` dimension rather than in a tidier name.

---

## Edge inventory

Every edge declares its properties and **the question each property settles**. An edge with no
properties must justify why it needs none.

| Slug | Source → target | Tier | Status | Properties, and what they settle |
| --- | --- | --- | --- | --- |
| `HOSTS_ACCOUNT` `OWNS_REPO` `DEFINES_WORKFLOW` `EXECUTES_WORKFLOW` `HAS_ACTIONS_JOB` `EXECUTED_ON` `ENABLED_ON` `REFERENCES_RESOURCE` `FEDERATES_VIA` `TRUSTS_ISSUER` | — | self | exists | the current spine |
| **`DEFINES_JOB`** | workflow → workflow_job | self | new | `{job_key, order}` — job identity within the file |
| **`DEPENDS_ON_JOB`** | workflow_job → workflow_job | self | new | `{condition}` — the `needs:` graph. **Two independent sources** (a standard's `taskDependencies`, a platform model's depends-on edge); neither earlier pass proposed it. Determines what a compromised early job can reach |
| `INSTANCE_OF_JOB` | actions_job → workflow_job | friends | new | `{run_attempt}` — the declaration↔execution bridge |
| **`HAS_REF`** | repository → git_ref | self | new | — (drift lives in the ref's `head_sha` field history) |
| **`BYPASSES`** | account\|team\|app → ruleset | self | new | `{actor_type, bypass_mode, observable, source}` — **`observable: false` distinguishes "nobody can bypass" from "we cannot see"**, which a blank cell cannot |
| `REQUIRES_CHECK` | ruleset → status_check | self | proposed | `{enforcement}` |
| `PRODUCES_CHECK` | workflow\|app → status_check | self | proposed | `{confidence}` — honest about inference |
| `USES_ACTION` | workflow_job → github_action | self | **exists** (2026-09-02, #45) | `{declared_ref, pin_kind, is_pinned, resolved_sha, resolution, step_indexes}` — pinned by SHA, digest, tag or branch, **or `unresolved`**: tag-versus-branch is not observable from the string, and is resolved only when the action's repository is in scope (`resolution` says which). `resolves_to_fork` dropped — needs the fork graph, not derivable; named in the article |
| **`REFERENCES_SECRET`** | workflow_job → actions_secret | self | proposed | `{step_index, trigger_events, checks_out_pr_head, interpolates_into_run, top_level_permissions}` — **the adjudication properties**: shape versus exploitability |
| **`WRITES_CACHE`** / **`RESTORES_CACHE`** | workflow_job → actions_cache | self | new | `{step_index, ref_scope, fork_reachable}` / `{step_index, ref_scope, privileged}` — is the writer reachable by an outsider, does the reader hold publish rights |
| `CALLS_WORKFLOW` | **workflow_job** → workflow | self | **exists** (2026-09-02, #29) | `{declared_ref, pin_kind, is_pinned, resolution, resolved_sha, same_repository, secrets_inherit}` — the `USES_ACTION` pin grammar plus whether the caller forwards every secret. **Source corrected to the job**: the call is written on the job, which carries the `permissions` and `secrets` the question needs, and two jobs in one file can call two workflows. Callee not on the grid → no edge; `call_resolution` on the job (three states) |
| `TRIGGERS_WORKFLOW` | workflow → workflow | self | **exists** (2026-09-02, #52) | `{trigger_event, declared_name, types, branches, branches_ignore}` — completing → triggered, from the target's `on.workflow_run`. **`conclusion_filter` dropped**: GitHub has no such key; the check lives in job `if:` expressions and extracting it is a guess. Filters carried only as written — GitHub's `types` default is not filled in |
| **`DEFINED_IN`** | github_action → repository | self | new | — enables owner-transfer and archived-action detection |
| `HAS_REPO_PERMISSION` | account\|team → repository | self | proposed | `{permission, affiliation, granted_via}` — **four sources independently reify permission provenance**; carry it from day one |
| `MEMBER_OF_ORG` | account → account | self | proposed | `{role}` |
| `OPENS_PULL_REQUEST` | account\|app → pull_request | friends | proposed | `{author_association}` |
| `GATED_BY` | environment → account\|team\|app | self-lite | new | `{rule_kind, prevent_self_review, wait_timer}` — its *absence* beside a branch policy is itself a finding |
| `REPRESENTS_CREDENTIAL` | actions_secret → credential_grant | friends | new | `{match_kind, confidence}` |
| `HOLDS_CREDENTIAL` / `GRANTS_ACCESS_TO` | principal → grant → repository | friends | new | `{permission, granted_at, last_used_at}` — grant timestamps are a standardised property |
| `REGISTERED_ON` / `MEMBER_OF_RUNNER_GROUP` | runner → repo\|account, runner → group | friends | new | `{first_seen, scope}` — "a runner appeared where none had been" |
| `BUILDS_PACKAGE_VERSION` | run → package_version | friends | new | `{attested}` — **its absence is the finding**: a registry version with no run behind it is how five incidents read |
| `POINTS_AT` | git_ref → git_commit | friends | new | `{observed_at}` |
| `FETCHES_FROM` | workflow_job → web_host | friends | new | `{url_pattern, piped_to_shell, digest_pinned}` |
| `UPLOADS_ARTIFACT` / `DOWNLOADS_ARTIFACT` | workflow_job → artifact | friends | new | `{cross_workflow}` |
| `LINKED_IDENTITY` | account → principal | later | new | `{confidence, evidence}` |

### Naming

Where a standard names a relationship, use its verb. **`produces` / `consumes` / `uses`** clear the
three-source bar and are stated crisply in a NIST publication; a 59-verb relationship dictionary in
a major SBOM standard is the reference to check before minting any new edge name. Cross-provider
ontology work elsewhere has already settled `PACKAGED_FROM`, `HAS_ROLE`, `USES_SECRET` — prefer
those spellings over invented ones.

---

## Rejected candidates

Recorded so they are not re-litigated. Each was rejected on the node test — **does anything need to
point at it?** — unless noted.

| Candidate | Verdict | Reason |
| --- | --- | --- |
| `step` | field | Nothing points at a step; it is an ordinal position inside a job. Revisit only if an edge genuinely needs a step as an endpoint |
| `trigger` | field on workflow | A trigger has no identity across observations |
| org / actions **policy** objects | fields | Nothing points at them. **The cleanest illustration of the node test in the corpus**: contrast with `github_ruleset`, which many repositories point at |
| `review` | edge | An edge answers the approval question; a node adds nothing |
| `fork` | edge (`FORKED_FROM`) | A fork is a relationship between two repositories |
| change / snapshot / audit-event types | **already provided** | The grid carries field-level history and provenance. Modelling change is duplicating the substrate |
| `actions_variable` | **field — disputed, see open questions** | No incident turns on one; but 6 platform sources model it |

---

## Source register

Every source consulted is pinned with a version and date in the raw reports. **Poll the sources, not
the maps** — the best cross-standard crosswalk in this field was one to three versions stale on
every source it pinned. Cadence matters: one foundational field list in this domain was superseded
one month before the survey that read it.

| Tier | What to watch | Why |
| --- | --- | --- |
| 1 — vocabulary-changing | The two published GitHub graph schemas; the SBOM relationship dictionaries; the formulation schema; the purl type registry | A new node or edge kind here is a direct diff against this corpus |
| 2 — condition-changing | The workflow static analysers' rule lists; the scorecard check list; the platform's own API/webhook/audit taxonomies | New checks name new observable conditions |
| 3 — context | Attack-technique catalogues; control frameworks; incident disclosure | Slower, but they are what promote a condition to a priority |

Exact raw URLs, formats and "what changed" signals are in the raw reports' source registers.

**The update seam is recorded, not built** (`build-domain-vocabulary` Step 9): a scheduled job
diffs the pinned sources and **opens a proposal** — never mutates the vocabulary; a generated
coverage delta turns the gap into a number that moves; and where a source publishes a machine-
readable catalogue, a collector can land it on the grid so "what changed" becomes a query.

---

## Decisions taken (2026-08-27)

| # | Question | Ruling |
| ---: | --- | --- |
| 1 | `workflow_job` (the declared job) in the *self* tier? | **Yes.** Cheaper now than after `USES_ACTION` / `REFERENCES_SECRET` ship pointing at the wrong source |
| 2 | `git_ref` replacing `git_branch`? | **Yes** (2026-08-27). One type, `ref_type` ∈ `branch` \| `tag`. Tag movement is the detection for three incidents, and a ruleset's target is one enum spanning `branch\|tag\|push`, so a split type would fan the ruleset join out across two types and two edges. The slug is a modelling name: views render "Branches" and "Tags", and the word *ref* need never reach a reader |
| 3 | Where `credential_grant` lives | **`identity_core`** — neutral, sits beside `principal`, and both a non-forge and a registry collector populate it |
| 4 | Where `package` / `package_version` live | **A new `supply_chain_core`.** Supply chain is the next domain after this one, so the substrate is created there rather than borrowed. Identity remains a purl |

## Open questions

1. **`actions_variable`: field or node?** The passes disagree. No incident turns on a variable, and
   nothing points at one — the node test says field. But six platform sources model it, and a
   variable can be *half a credential* (an app ID beside a private key held as a secret). **Default
   to a field at `self`; revisit at `friends`** if anything needs to point at one.
2. ~~Where `package_version` lives.~~ **Settled:** `supply_chain_core` (see decisions above).
3. ~~**`BYPASSES` observability.**~~ **SETTLED EMPIRICALLY 2026-08-27, and the answer is worse than
   expected.** **Addendum 2026-09-02:** less bad than measured — a read credential gets GraphQL's
   `totalCount` with the nodes redacted (#11 probe, #22), so `bypass_observability` gains a `counted`
   state and the count rides the ruleset node; see `spec-github-core-v0.md` §Bypass observability. GitHub returns `bypass_actors` only to a caller with **write access to the ruleset**.
   Measured against our own org, and **read the next paragraph before relying on the comparison**:
   an owner-minted fine-grained PAT sees it; a **GitHub App with `administration: read` does not**
   (`bypass_actors` absent from the ruleset detail, HTTP 200).

   **The comparison was admin-versus-read, not read-versus-read (corrected 2026-08-27).** The PAT
   used for every early probe carries an envelope description reading *"Read-only fine-grained
   GitHub PAT"* that was authored by hand and never verified; it reports `{"admin": true,
   "maintain": true, "push": true, ...}` on `unified-systems-com/tap`. A fine-grained PAT inherits
   its user's repository role, and a GitHub App has no user to inherit from — which is the actual
   mechanism behind the asymmetry recorded here.

   **What the measurement established, and what it did not.** Every ruleset in the org has
   *genuinely zero* bypass actors, so for the most part "empty" and "withheld" are the same bytes
   and no credential could have discriminated them. One thing *was* discriminating: the PAT
   received `bypass_actors: []` while the App received **no key at all**, and absent-key versus
   present-and-empty does establish that gating exists. What has never been observed is **what the
   App receives when the list is populated** — no populated list existed until a probe ruleset was
   built on 2026-08-27, and the App half of that probe is still unmeasured.

   Three consequences that are now facts rather than precautions:

   - **Neither credential dominates.** The App uniquely sees PAT grants, installations and org
     membership; the owner-PAT uniquely sees bypass actors. A complete gate picture needs both, or
     accepts a gap. Do not tell an adopter the App is strictly better.
   - **`observable` on the `BYPASSES` edge is mandatory, not defensive.** A blank "who can bypass"
     cell reads as *nobody can bypass* — the most reassuring possible message — when it may mean
     *we could not look*. Rendering those identically would make an organization feel safer while
     being no safer. The view needs three states: **none / some / not-observable**.
   - **The read-only posture has a hard ceiling here.** Seeing the exemption list requires write
     access to the thing being audited. We do not request write. This is a limitation to publish,
     not to engineer around.

   Partial signals worth testing before accepting the gap: `current_user_can_bypass` is returned on
   the ruleset detail (answers "can *this* credential bypass", not "who else can"), and the
   rule-suite / rule-insights endpoints may expose actual bypass *events* even where the actor list
   is withheld — detection instead of enumeration.

   **Re-measured 2026-08-27 (afternoon), and the transport changes the answer.** The same App
   credential that REST refuses gets an *answer* from GraphQL: `RepositoryRuleset.bypassActors`
   returns `totalCount: 0` with **no `errors` entry at all**, where the REST ruleset detail simply
   omits the `bypass_actors` key. Checked against an owner credential, every ruleset in our
   organization genuinely has an empty bypass list — so **the distinguishing case is untested**: we
   cannot tell a truthful `0` from a silently filtered connection, and our own org cannot tell us
   (proving it would mean adding a bypass actor to a live ruleset, which is a change to our security
   posture, not a measurement).

   The derivation that follows from that, and which the collector implements:

   ```
   observable = rest_detail_carries_bypass_actors  OR  graphql_bypass_actors_is_non_empty
   ```

   A **non-empty** GraphQL answer proves itself — a filtered connection cannot invent actors. An
   **empty** one proves nothing, so it is recorded as *not observable* rather than as *none*. This
   is the asymmetry that matters: false *presence* is impossible, false *absence* is the whole risk.

   **Correction to the edge design — the absence signal cannot live on the edge.** `BYPASSES` keeps
   its `observable` property for per-actor provenance, but when the answer is *none* or *unknown*
   **there are no edges to carry it**, and a view that reads only edges renders both as an empty
   list. The three states therefore live on the **`github_ruleset` node** (`bypass_observability` ∈
   `observed` \| `unobservable`, with `bypass_actor_count` meaningful only when `observed`), which
   exists in every case. Generalized: *a property that qualifies an absence belongs on the node the
   absence is about, never on the edges that failed to appear.*
4. **When the neutral substrate is extracted.** 11 concepts are marked neutral and the kernel test
   confirms they populate from a non-forge project. Extraction while a slug change is still a
   re-collect is cheaper than a migration later.
