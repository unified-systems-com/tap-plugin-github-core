# GitHub Core Gates Panel Specification

## Philosophy

*What gates each repository's default branch, and what can we not see?*

The gate posture of a repository is a join the graph already holds, edge by edge: `PROTECTS`
(ruleset → repository), `REQUIRES_CHECK` (ruleset → status_check), `PRODUCES_CHECK` (workflow →
status_check) and `DEFINES_WORKFLOW` (repository → workflow). This spec defines the `github-gates`
panel type: one row per repository, folded from those edges and rendered through the standard
table panel renderer so a consuming product declares columns and tooltips, never code.

It is a panel **type** rather than a declarative table panel because a table panel renders one
row per node of one search, and this row is a four-edge join with per-row aggregation — Gryphon's
only aggregate is `COUNT` (`req-grid-gryphon-count`). It lives in github_core, not in the
product, because every fact it folds is github_core's: what `~DEFAULT_BRANCH` means, that
integration `15368` is GitHub Actions itself, what a type-only `required_status_checks` rule
signifies.

Three states, never two — the discipline the whole product exists for (presence is not
correctness): a repository with no `PROTECTS` edge reads *no ruleset observed*, not
"unprotected"; a governing ruleset whose required-checks rule came back type-only reads *required
checks not observable*, never "none required"; the bypass cell is the worst of the governing
rulesets' `bypass_observability`, and an observed empty list is a fact.

## Roadmap Alignment

Serves git-serious's `req-git-serious-branch-protection-tiers-1`,
`req-git-serious-configured-the-same-1` and the gate-chain half of
`req-git-serious-why-not-merging-1` (git-serious-tap#47). Issue: #65. Stacked on the
`status_check` bake (#62, `req-github-core-status-checks`).

## Requirements

| RID | Name | Status | Notes |
| --- | --- | :---: | --- |
| req-github-core-gates-panel | [The gates panel](#the-gates-panel) | Implemented | `panels/gates/__init__.py`, `tests/test_gates_panel.py` |

### The gates panel

----
RID: `req-github-core-gates-panel`
Status: `Implemented`

#### Implementation

- `GatesPanelType` (slug `github-gates`, view `github_core/panels/gates.html`, which includes
  `tap_web/panels/table_panel.html`). The view template is the type's identity: the page view
  resolves a Panel row to its type by matching `view`, so the panel owns a path of its own.
- Reads: five declared Gryphon queries (`QUERIES`) executed through `execute_gryphon_raw` — the
  sanctioned raw-query chokepoint gated on `grid.read`. No model import on the read path. The
  repository read uses the extended layer so rows carry a viewer url; the chain reads use the
  full layer, whose edge envelopes carry `edge_type`, endpoints and properties under `data`.
- `build_rows(envelopes)` is pure: it folds the envelopes into node-shaped rows (the repository's
  spine surface + display lane, with a derived `data` lane) ordered worst gate first.
- A ruleset **governs** a repository's default branch when a `PROTECTS` edge joins them, the
  ruleset targets branches, and its `ref_name.include` carries `~DEFAULT_BRANCH`, `~ALL`, or the
  repository's default branch by name (and `exclude` does not name it).
- Producer resolution per required context, in order: an `integration_id` other than `15368`
  → **app** (no workflow can satisfy it); a `PRODUCES_CHECK` edge from a workflow this repository
  `DEFINES_WORKFLOW` → **workflow** (with the edge's `confidence` shown unless `exact`); a producer
  only in another repository → **nothing produces** (named as *only elsewhere*); none → **nothing
  produces**. The panel never derives a producer from a job name — that is the collector's
  derivation, and the edge is what carries it.
- *Missing vs peers*: a context required by at least half of the account's gated repositories
  (and at least two) that this row lacks (`PEER_SHARE_FRACTION`). Rows whose required checks are
  not observable read *not observable* here too.
- Default columns (`DEFAULT_COLUMNS`) are the panel's `config_defaults`; a consumer's panel
  `config.columns` overrides them, as do `height`, `refresh_seconds`, `quick_filter`.

#### Acceptance Criteria

| ACID | Title | Status | Description | Notes |
| --- | --- | :---: | --- | --- |
| req-github-core-gates-panel-1 | Three States On The Gate | Implemented | A repository with no `PROTECTS` edge reads *no ruleset observed*; a governing ruleset with a type-only `required_status_checks` rule and no `REQUIRES_CHECK` edge reads *required checks not observable*; neither ever reads as clean. | `test_no_protects_edge_reads_no_ruleset_observed_never_unprotected`, `test_type_only_rule_reads_not_observable_never_none_required` |
| req-github-core-gates-panel-2 | Producer Per Context | Implemented | A required context is attributed to a workflow in this repository, an App, or *nothing produces* — and a producer in another repository does not satisfy this repository's gate. | `test_workflow_app_and_nothing_are_three_columns`, `test_a_producer_in_another_repository_does_not_satisfy_this_gate` |
| req-github-core-gates-panel-3 | Bypass Is The Worst Governing Ruleset | Implemented | `unobservable` outranks `counted` outranks `observed`; an observed empty list renders *observed: none*. | `test_bypass_is_the_worst_of_the_governing_rulesets`, `test_an_observed_empty_bypass_list_is_a_fact` |
| req-github-core-gates-panel-4 | Outlier Named | Implemented | A context at least half of the gated repositories require and one lacks is named on that row. | `test_missing_vs_peers_names_the_outlier` |
| req-github-core-gates-panel-5 | Through The Graph | Implemented | A graph built through the service layer, read back through the panel's own queries, yields the row the graph says. | `TestThroughTheGraph` |

#### Future

- `required_checks_observability` on the ruleset node (#64) replaces the rule-shape inference.
- The panel bundle (node + columns) mountable from any page with one edge (git-serious-tap#36).
- A chip formatter in the table renderer so per-context states colour instead of reading as
  three text columns.
