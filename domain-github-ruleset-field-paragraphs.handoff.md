# Handoff: `domain/github_ruleset.md` field paragraphs for the surviving model

`model-git-serious` wrote the article against an 8-key `FIELD_CRUD_SCHEMA`. The surviving model
has 14, so the domain-article guard (baseline drained to zero) fails the moment the model swaps
unless these land **in the same change**. Written in the article's voice; paste into its fields
section, adjust headings to match.

Two are renames rather than additions: their `source_name` became `source` (the article's existing
`source_type` paragraph stands unchanged and is still correct).

---

**`owner_login` is the identity input, and it is why there are 6 nodes and not 60.** A ruleset is
keyed on its owner plus GitHub's ruleset id, never on the repository it was seen from — an
organisation ruleset is one object that many repositories point at. Keying it per repository would
turn "what does this ruleset protect" into a string comparison across duplicates, and the 60
attachments measured on our own organisation would have become 60 rulesets.

**`source` names where the ruleset is defined; `source_type` names what kind of thing that is.**
The pair is what distinguishes an organisation ruleset applying to 19 repositories from a
repository ruleset applying to one. Read `source_type` first: it is the field that decides whether
version history is reachable by the repository path at all.

**`conditions` is stored exactly as GitHub returns it, tokens and all.** `~DEFAULT_BRANCH` and
`~ALL` are kept verbatim rather than resolved at collection time, because resolving them would
freeze one moment's answer into a field that outlives it — the default branch can be renamed, and
a ruleset that said "the default branch" would silently come to mean the old one. Resolution
happens on the edge instead: `PROTECTS` carries `match_kind: resolved` and the pattern that
matched, so intent and effect are both queryable and neither is inferred from the other.

**`rules` is the array as returned, with each rule's parameters intact.** This is the field that
makes the type a model rather than a label. The required status checks live here
(`rules[].parameters.required_status_checks[].context`, ours reads `{"context": "gate",
"integration_id": 15368}`), and a gate view that knows a repository requires *some* check but not
*which* is not a gate view. It is populated from the REST detail; the GraphQL config layer returns
rule *types* only, and the collector falls back to that type-only list rather than to nothing,
warning when it does.

**`bypass_observability` carries the third state, and it exists because a blank cell lies.**
It is `observed` only when the REST detail carried the `bypass_actors` key **or** GraphQL returned
a non-empty list; otherwise `unobservable`. The asymmetry is deliberate: a non-empty answer proves
itself, since a filtered connection can hide actors but cannot invent them, while an empty one
proves nothing. False presence is impossible here; false absence is the entire risk. This lives on
the node rather than on the `BYPASSES` edge because when the answer is *none* or *unknown* there
are no edges — a view reading edges alone would render both as an empty list, and "nobody can
bypass" is the most reassuring thing a security product can say.

**`bypass_actor_count` is nullable, and null is not zero.** Zero is a claim that nobody holds an
exemption. Null is the absence of a claim, and it is the honest value whenever
`bypass_observability` is `unobservable`. Measured on our own organisation with a read-only App:
all six rulesets landed null, where a naive read of the same GraphQL response would have landed
six confident zeroes.

**`html_url` is the ruleset's own settings page**, taken from the REST detail's `_links.html.href`,
so a reader who wants to change what a row describes has one click rather than a search. Empty when
only the GraphQL config layer answered.

---

## One thing worth acting on from the article's own measurements

Its endpoint table records `rulesets/rule-suites` as **not requiring write** and returning **200**.
If that holds for the App, then bypass *events* are readable by this product **today** — no probe
ruleset, no write access, no second credential. That does not answer enumeration ("who may
bypass"), but it answers the question a security product more often needs: **has anyone actually
bypassed this gate**. Detection instead of enumeration, which is precisely the partial signal the
vocabulary corpus proposed and then left parked.

It is also where the `time_period` trap bites hardest, and the trap is this domain's recurring
shape: omitting the parameter defaults to `day`, so a repository with 28 bypass events in a month
reads as a quiet one. An absence that renders as a finished answer — the same failure as a blank
bypass column, arriving through a different door.

Worth its own task rather than folding into the enumeration question, because it is cheap, it is
unblocked, and it may be the more useful of the two signals.
