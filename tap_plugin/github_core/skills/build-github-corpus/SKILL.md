---
name: build-github-corpus
description: Grow and measure github_core's domain vocabulary — the GitHub instance of build-domain-vocabulary. Use when deciding what GitHub concept to model next, when measuring how much of GitHub's own object model the corpus covers, when a new GitHub product surface appears, or before any add-model / add-edge in github_core. It inventories the corpus against independent GitHub-authored concept lists (Octicons first), computes a coverage delta, ranks the bake-next list by incident weight and credential reachability, and hands each accepted concept to add-model / add-edge / build-collector with its domain article and absence shape. NOT for a domain other than GitHub (use build-domain-vocabulary directly).
allowed-tools: Read Write Edit Bash(python3 *) Bash(scripts/dc *) Bash(gh *) Bash(curl *) Bash(git *) Bash(grep *) Bash(find *) Bash(ls *) Bash(mkdir *) Glob Grep WebFetch Task
argument-hint: [measure | rank | bake <concept>...]
---

# Build the GitHub corpus

You are growing the vocabulary that every git-serious question is asked in. This skill is
[`build-domain-vocabulary`](../../../../../tap_grid/skills/build-domain-vocabulary/SKILL.md)
specialised to one domain and one owner: the corpus lives at
`specs/spec-github-core-vocabulary.md` in this plugin, the articles at `domain/`, the models at
`models/`, the edges at `edges/`, and the collector's reach at
`collectors/github_collector/github_collection_manifest.json`. The parent skill decides *how* a
vocabulary is gathered; this one records *what GitHub-specific sources exist, how the coverage is
measured, and what the ranking criteria are* — so the second pass costs an hour, not a week.

Three modes, in the order they are usually needed:

- **measure** — how much of GitHub's own object model do we cover, and where are the gaps?
- **rank** — of the gaps, which earn a place next, and why?
- **bake** — build the accepted concepts, one at a time, with everything a concept owes.

---

## Step 0 — Ground on the branch you are building on, not the clone you happen to have

The corpus moved under the last pass that used it: rule suites landed on `main` (one node type,
four edges) while a peer session's editable clone sat on a feature branch that predated them, and
the survey counted 14 nodes and 20 edges when `main` had 15 and 24. Inventory from **`origin/main`
or the branch you will commit to**, in an isolated worktree:

```bash
git -C <github_core clone> fetch origin
git -C <github_core clone> worktree add -b feat/<topic> "$CLAUDE_JOB_DIR/tmp/gc-<topic>" origin/main
```

Never `git checkout` in `_dev-plugins/github_core` — it is shared with peer sessions.

Then read, in this order: the corpus spec's node and edge inventories and its *Decisions taken*
and *Open questions* (they rule out re-litigation); the domain articles that exist and the list of
those that do not (an absent article is an absent Observability section, which is an absent input
to reconciliation — github-core#42); the collection manifest's permission triples, from which the
**credential union** is derived (`metadata:read`, `contents:read`, `actions:read`, plus
`administration:read` for repo and org at the time of writing — derive it, never quote it from
prose); and the running instance's registered types when one is up:

```bash
scripts/dc exec -T web uv run python manage.py shell -c "
from tap_grid.registry import list_entity_types
print('\n'.join(t for t in sorted(list_entity_types()) if t.startswith('github_core__')))"
```

## Step 1 — The GitHub-authored concept lists (the independent inventories)

The parent skill says to gather from independent directions. For GitHub these are concrete, all
machine-readable, and each biased differently:

| Source | What it is | Pin it by | Bias |
| --- | --- | --- | --- |
| **Octicons** (`primer/octicons`) | The set of things GitHub itself thinks deserve a glyph. An icon is a concept someone at GitHub argued for. | A release tag; inventory the `icons/` directory listing, **not** `keywords.json` (stale: 217 names against 332 live at v19.15.1, and it still lists retired glyphs) | Product-marketing weighted; strong on objects, silent on relationships |
| **REST OpenAPI description** | Every resource the API exposes. `collectors/github_collector/github_openapi_extract.json` already carries the extract the collector was built from | The description's version header | Includes everything; most of it does not matter |
| **GraphQL schema** | The object graph with its *edges* named as connections | Schema date | Best relationship source; carries no pipeline executions |
| **Webhook event taxonomy** | An event list is an entity dictionary in disguise — events happen *to* things | Docs page date | Names what changes, not what exists |
| **Audit-log action list** | What GitHub thinks is worth recording | Docs page date | Enterprise-shaped |
| **The two published GitHub graph schemas** in the corpus's source register | Someone already drew nodes and edges for this domain | Their repo tags | Their product's questions, not ours |

Octicons is listed first because it is the cheapest full pass and it answers a question the API
lists cannot: *what does GitHub consider a first-class thing?* An API resource may be plumbing; an
icon never is.

## Step 2 — Measure: the coverage delta

Classify every Octicon once, by hand, into three classes — the rule is what makes the number
defensible, so state it and keep the list checkable:

- **(a) GitHub domain concept** — the glyph's job on github.com is to name a GitHub object, product,
  or object *state* that has an API/object model behind it (repo, issue, pull request, workflow,
  commit, tag, release, package, ruleset/shield, deployment, environment, runner, app, webhook,
  org, team, codespace, copilot, dependabot, advisory, secret scanning …).
- **(b) UI chrome** — navigation, editing, layout, generic verbs (chevrons, gear, trash, kebab).
- **(c) Other** — file-type glyphs, markdown formatting, reactions, brand-neutral misc.

Collapse `-fill` / `-filled` / `-inset` and the `feed-*` activity variants into their parent, and
count concepts, not files. Only class (a) is the hit-rate denominator.

The classification is checked in beside this skill as `octicons-concepts.json` (every glyph, its
class, its concept family, its product area, and the TAP type it maps to, if any). The script
regenerates the delta from it:

```bash
python3 tap_plugin/github_core/skills/build-github-corpus/octicons_coverage.py \
  --plugin-root tap_plugin/github_core --tag v19.15.1            # live listing from GitHub
python3 tap_plugin/github_core/skills/build-github-corpus/octicons_coverage.py \
  --plugin-root tap_plugin/github_core --offline                  # from the checked-in file only
```

It prints the summary table (our nodes, our edges, Octicons total / concept-bearing, hits and
misses in both directions), the per-type mapping, the concept-bearing glyphs with no TAP type
grouped by product area, and — the load-bearing line — **every Octicon in the live listing that
the checked-in classification does not know**, so a new GitHub product surface shows up as an
unclassified name rather than as silence. Add `--json` for the machine-readable form.

Paste the summary table into the corpus spec's coverage section with the tag and date. The number
is only useful when it moves.

**Read the delta in both directions.** "Our types with a glyph" says whether we speak GitHub's
language; "GitHub's concepts we model" says how much of the platform we can see. At the 2026-09-02
pass they were 12 of 15 and 14 of 56 — the second number is the product's honest coverage claim.

**Concepts with no Octicon are the interesting list, not a defect.** The declared job, the executed
job, the environment-as-gate and the unified ref had none. That is independent confirmation that
the declared/executed split — the vocabulary spec's stated differentiator — is something GitHub
does not think in. Name each as TAP-invented in its article; never rename it to fit a glyph.

## Step 3 — Rank: what earns a place next

Score every gap concept on four axes and rank by the first two; the others break ties. Record the
scores in the corpus spec so the next pass argues with numbers.

1. **Incident weight** — how many documented failures need this concept to be representable. The
   corpus's justification column and the incident vocabulary doc carry the counts. This is the
   base case (parent skill, Step 6: demand, not the survey's momentum).
2. **Reachability with the credential we already hold** — derive from the manifest's permission
   triples. A concept the current union reaches costs a collector source; one needing a new grant
   costs a permission conversation with every adopter (github-core#12). Rank reachable first even
   when a less reachable concept has more incidents; the permission cost is paid per adopter,
   forever.
3. **Convergence value** — does it join two things we already model (a status check joins rulesets
   to workflows; a commit joins refs to signatures)? Convergence nodes unlock questions, thin leaf
   nodes add rows.
4. **GitHub-native or TAP-invented** — an Octicon exists (weak signal the concept is stable and
   nameable) or not (we are ahead; say so). Neither disqualifies.

Then apply the parent skill's node/field/edge-property discipline to each survivor before it enters
the list — a concept that fails "does anything point at it?" becomes a field on its parent and
leaves the ranking.

The 2026-09-02 ranking, for the record (no new permission needed for the first eight): the action
node with a uses-edge; calls-workflow and triggers-workflow edges; artifacts with upload/download
edges; commits with signature state; status checks with a requires-check edge; releases; declared
secrets from `${{ secrets.X }}`; runner groups; then pull requests, webhooks, deployments and
packages, each behind its own grant.

## Step 4 — Bake: what every accepted concept owes

One concept per change. The first one will teach you what the ranking got wrong.

1. **File the issue first** (issue-driven development). Title the concept, cite the corpus row and
   the incidents, name the credential and API call that populate it, and state the done-test:
   the type is registered, the article exists with its Observability section, the collector lands it
   on a running instance, and a Gryphon query over it returns the expected shape.
2. **Declare its absence shape before writing the model** — github-core#14's seven shapes (git-
   provable, enumerable-under-complete-walk, immutable event, derived, credential-shaped, cross-grid,
   …). Reconciliation reads the shape from the article; a type without one cannot be tombstoned
   safely and will pollute the grid on its first rename.
3. **`add-model` / `add-edge`**, with the edge's properties justified by the question each settles
   (a bare edge produces confident nonsense in any risk view).
4. **The domain article** at `domain/<concept>.md` — what it is in the world, its natural key, what
   it excludes, neutrality, and the credential/permission that populates it, with the *not
   observable* list. Write it while the research is in hand.
5. **The collector source** in the manifest (permission triple, degrade policy) and the collect
   step — or, for a declared-side concept, the YAML parse. Fire it against the dev instance and
   check the count is not a silent zero (three states: some / none / not observable).
6. **The icon** — derive from Octicons at a pinned tag where one exists, wrapped in a padded viewBox
   with an explicit fill, and list it in `static/github_core/icons/NOTICE` with the source glyph and
   tag; draw one where none exists and say it is TAP-drawn. Never inventory Octicons from
   `keywords.json`.
7. **Update the corpus spec** — inventory row status, source register, coverage table.

## Step 5 — Record the update seam

The parent skill's Step 9 applies unchanged. For Octicons specifically: the watch is the release
feed of `primer/octicons`; the signal is a release tag; the diff is this skill's `--tag <new>`
against the checked-in classification, and the proposal is the list of unclassified names it
prints. Never classify automatically — an unclassified glyph is a question for a human or an
agent, and the answer is a row in `octicons-concepts.json`.

---

## Tips and tricks

*Append as the method is exercised. Each entry: the observation, and the pass that taught it.*

- **An icon set is an independent concept inventory.** Every glyph is a concept someone at the
  platform argued deserved a name; the set is small enough to classify by hand in one pass and it
  is orthogonal to API lists (which include plumbing) and incident lore (which overweights drama).
  (git-serious, 2026-09-02)
- **Inventory the icons directory at a pinned tag; the keywords file lies.** `keywords.json` was
  217 names against 332 live glyphs and still carried retired ones (`github-action`, `octoface`).
  A retired glyph is itself information — GitHub dropped `github-action` — but only if you read
  the directory. (git-serious, 2026-09-02)
- **Measure both directions and report the smaller one as the coverage claim.** 80% of our types
  had a glyph; 25% of GitHub's concept families had a type. The first flatters. (git-serious,
  2026-09-02)
- **No glyph for a concept is confirmation, not a gap, when the concept is your differentiator.**
  GitHub renders jobs only by status and has no icon for the declared/executed split. (git-serious,
  2026-09-02)
- **Inventory from the branch you will commit to.** The shared editable clone was on a feature
  branch behind `main`; the survey missed a node type and four edges that had merged that week.
  (git-serious, 2026-09-02)
- **Derive the credential union from the manifest, not from the prose that describes it.** The
  prose said Metadata + Contents + Actions; the manifest also carried `administration:read` for
  repo and org, which moves runner groups from "needs a grant" to "reachable now". (git-serious,
  2026-09-02)
- **A missing domain article is a missing reconciliation input, not missing prose.** Five nodes and
  twelve edges from the self-tier had no Observability section; #14's absence shapes are read from
  there. (git-serious, 2026-09-02)
- **Four hand-drawn glyphs had exact Octicons.** Check the set before drawing; check it again at
  each release — `cache` arrived at v19. (git-serious, 2026-09-02)


### From the first bake (items 1–5, 2026-09-02)

- **The first bake in a family pays for the rest — rank resolver-sharing concepts adjacent and
  expect stacked PRs.** The action node's pin resolver, refs cache and post-pass registers were
  reused by the next four concepts; building them as independent branches would have meant four
  tagged known-dupes or four conflicts. (github-core#51 → #54 → #56 → #60 → #62)
- **Check an edge's target is identifiable from some source before it enters the list.** The
  ranking carried a downloads-artifact edge; the API exposes no observable consumer of an
  artifact, so it was rejected at bake time with the reason recorded. (github-core#55)
- **A parser label is a declaration too.** `@v4` had been labelled `tag` for a week when it is
  merely a non-SHA ref; the fix is `unresolved` until a lookup resolves it — presence is not
  correctness applies to what the parser *calls* a thing. (github-core#45)
- **Prefer repository-level listings over per-run walks.** One listing call per repository
  reached 3,831 artifacts on the primary repo; per-run fetches would have cost thousands of calls
  for the same rows. (github-core#55)
- **Content-addressed identity is not observation-scoped identity.** A commit SHA is global, but
  GitHub's signature-verification record is per repository network, so one node per SHA merged
  verification records that disagree; the key became `<full_name>#<sha>`. Ask, for every
  natural key, *which observer's view is this the identity of?* (github-core#57)
- **Done-tests need a running instance that runs the branch.** The dev stack ran the shared
  clone, so none of the five ACIDs could be *observed*; every requirement stays In Development
  until a stack boots the branch. Budget the boot when the bake is more than one concept.

## References

- [`build-domain-vocabulary`](../../../../../tap_grid/skills/build-domain-vocabulary/SKILL.md) — the method this specialises.
- [`add-model`](../../../../../tap_grid/skills/add-model/SKILL.md), [`add-edge`](../../../../../tap_grid/skills/add-edge/SKILL.md), [`build-collector`](../../../../../tap_plugins/skills/build-collector/SKILL.md).
- `specs/spec-github-core-vocabulary.md` — the corpus; `domain/` — the articles; `collectors/github_collector/github_collection_manifest.json` — the reach.
- github-core#14 (absence shapes), #15 (credential visibility), #42 (missing articles), #43 (icons).
- `tap_web/static/tap_web/icons/NOTICE` — the core trigger glyphs, the pattern for deriving from Octicons.
