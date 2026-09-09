# Analyzes Repository

## Blurb

A code scanning analysis was uploaded for this repository — the containment edge from the scan record to the repository it scanned.

## Purpose

"Which scanners run on this repository, and when did each last run" starts at the repository and walks to its analyses. Without this edge the analysis would carry `full_name` as a string and the question would be a field match instead of a traversal; with it, the repository page's security axis is one hop, and "repositories with no analysis in ninety days" is a query over repositories with an `observed` surface and no recent edge.

## Goals

- Reach every recent analysis from its repository in one hop.
- Make "scanned, never scanned, could not look" three distinct answers at the repository: the edge's absence is only meaningful beside `code_scanning_observability = observed`.

## Identity

One edge per (source, target) pair, id minted by `identity.edge_id`; property-free. The repository id is computed from `full_name` (`identity.repository_id`), which the analysis listing was fetched by, so the target always exists in the batch.

## Boundaries

- **Not the commit join.** That is `ANALYZES_COMMIT`, which is conditional; this edge is unconditional.
- **Not the producing workflow.** `analysis_key` names a workflow path; a join to `github_workflow` by path is a field-level join and no edge is drawn, because the analysis may have been uploaded outside Actions (`sarif_id` from a direct upload) and a path in the key is not proof a collected workflow produced it.
- **Not a statement about coverage.** An analysis on `refs/pull/348/merge` says a pull request was scanned, not that `main` was; the ref is on the node.

## Neutrality

GitHub-owned edge between two GitHub-owned nodes. The relationship (a scan belongs to a repository) is universal; the source type is not yet neutral, so the edge stays here.

## Observability

Emitted from the analyses read (`repository:security_events:read`) for every analysis that landed; present iff the analysis node is. Shares the repository's `code_scanning_observability` state: absent under `unobservable`, `not_enabled` and `""` for reasons that are not "no scans", and the state field is what tells them apart.

## Authoritative Source

- **Source:** the collector's own emission over `GET /repos/{owner}/{repo}/code-scanning/analyses` (`collectors/github_collector/collector.py`); the repository is the path parameter, so the relation is the listing's own scope
- **Version:** REST API version `2022-11-28`, as pinned in `github_openapi_extract.json`
- **Retrieved:** 2026-09-09

## Prior Art

- Operator ruling, session double-tap-git-serious (2026-09-09; github-core#89) — the analysis as the "did it run" record beside the findings.
- `specs/spec-github-core-vocabulary.md` (2026-08-27) — the containment-spine pattern (`PUBLISHES_RELEASE`, `STORES_ARTIFACT`: a repository-scoped listing gets a repository → object or object → repository edge, property-free).
- OASIS SARIF 2.1.0 (2020-03-27) — `run.versionControlProvenance`: the standard's own statement that a run is about a repository at a revision.

## Endpoints

- **Source:** `github_core__code_scanning_analysis`
- **Target:** `github_core__github_repository`
- **Dimensions:** `github.platform`, `github.surface: security`, `github.observation: execution`.
- **Properties:** none — the ref, commit, tool and category are on the analysis node.
