# Code Scanning Analysis

## Blurb

One SARIF upload GitHub accepted for a repository: which tool, at which commit and ref, under which category, with how many results and rules. The record that a scan RAN, whether or not it found anything.

## Purpose

An alert says a scanner found something; it cannot say that a scanner looked. That gap is the one that matters on the security axis — a repository with zero open alerts and zero analyses in ninety days is not clean, it is unscanned — and GitHub keeps the answer as a separate object, the analysis, one per SARIF upload. Putting it on the grid turns "is CodeQL actually running on main" and "when did Trivy last scan the web image" into traversals from the repository, and, when the commit is on the grid, from the commit.

Measured 2026-09-09 on `unified-systems-com/tap` (most recent 100): CodeQL 72 (categories `/language:actions`, `/language:javascript-typescript`, `/language:python`), SonarCloud 24 (category `""`), Trivy 2 (`trivy-tap-web`, `trivy-tap-db`), Grype 2 (`grype-declared-tap-web`, `grype-declared-tap-db`). Four tools, three of them ours to configure, and none of that was visible on the grid before this node.

## Goals

- Record every recent upload per repository as one node carrying the tool, commit, ref, category and result counts GitHub reports.
- Join each analysis to its repository always, and to the neutral commit when that commit was observed — the second join is what makes "was this commit scanned before it merged" answerable.
- Make the window honest: the most recent hundred per repository, with truncation warned, never presented as the whole history.

## Identity

Natural key: **`<owner/repo>#<analysis_id>`** — the repository plus GitHub's numeric analysis id. Entity id is `uuid5(ns, "github_core__code_scanning_analysis:<owner/repo>#<analysis_id>")` under `GITHUB_CORE_NAMESPACE` (`collectors/github_collector/identity.py`).

GitHub's id is the identity: it is what the detail and delete endpoints take, and it is unique per repository. NOT `sarif_id`: one SARIF upload can produce several analyses (one per `run` in the file, each its own category), so the SARIF id is a field that groups analyses, not a key. NOT (tool, commit, category): GitHub happily accepts two uploads of the same tool at the same commit under the same category and gives them two ids.

## Boundaries

- **Not the alerts it produced.** GitHub does not tie an alert to an analysis id; the alert carries `analysis_key` and `category`, the analysis carries the same two, and the join is loose. No `REPORTED_BY` edge is drawn from this node to a finding, deliberately — it would assert a precision GitHub's data does not have.
- **Not the SARIF body.** The results themselves live behind the alerts; the analysis carries counts (`results_count`, `rules_count`) and the download URL, not the file.
- **Not the workflow run.** `analysis_key` names a workflow path and job, and for an Actions upload that is enough to find the workflow by path; the *run* that uploaded it is not in the payload, so no `UPLOADS_ANALYSIS` edge is drawn from a run. The join a consumer can make is by workflow path, and it is a field-level join.
- **Not the default-setup configuration.** Whether code scanning is configured (default setup, advanced setup, which languages) is a separate endpoint and a repository-level fact; `github_repository.code_scanning_observability = not_enabled` is the only configuration state this change records.
- **Not history.** GitHub returns the most recent analyses first and the collector takes one page of a hundred; older uploads are gone from the grid, and `CODE_SCANNING_ANALYSES_TRUNCATED` says when the page was full.

## Neutrality

**Vendor-specific in the record, neutral in the idea.** "A scan ran at this commit with this tool" holds for any SARIF consumer and any forge; GitHub's `analysis` object (its id, `deletable`, `warning`, the `sarif_id` grouping) is the vendor's. The neutral fact a future substrate would want — tool, version, commit, results — is carried under SARIF's names (`tool_name`, `tool_version`, `results_count`) so extraction is a rename.

## Observability

Populated from **`GET /repos/{owner}/{repo}/code-scanning/analyses`** (`per_page=100`, one page, most recent first; unfiltered by tool or ref so every tool's uploads land) at **`repository:security_events:read`** — the same sensitive read the alerts need, so a credential that can see one can see the other and the two surfaces are never in different observability states. Measured 2026-09-09 with the installed App token (`git-serious-exploratory`, installation 157103378): 200 on `unified-systems-com/tap` with the hundred-analysis population above; the page was full, so the repository's true count is higher and is not reported by the endpoint (GitHub gives no total).

**The same four states as the alerts**, read from the same `github_repository.code_scanning_observability`: `observed` (200 — zero analyses is a fact: nothing has ever uploaded), `unobservable` (403, credential or an unaccepted permission; `CODE_SCANNING_UNOBSERVABLE`), `not_enabled` (GitHub says code scanning or Advanced Security is off; `CODE_SCANNING_NOT_ENABLED`), `""` (never asked). One field for both surfaces because they share an endpoint family and a permission; a run that could list alerts and not analyses has never been observed and would be a GitHub-side change worth a warning of its own.

**Two things GitHub does not tell this endpoint:** the total number of analyses (so `results_count` sums are per-page, not per-repository), and the workflow run that uploaded each (so provenance to a run is a join by `analysis_key`'s workflow path, at field level). The commit edge is conditional: `ANALYZES_COMMIT` is drawn only when `commit_sha` is a `git_core__git_commit` on the grid, and its absence means the commit was not collected — it never means the commit was not analysed.

**Absence shape** (github-core#14): the surface is **Shape E, credential-shaped**, with `not_enabled` beside `unobservable` for the same reason as the alerts; the analyses themselves are **Shape C, immutable events** — a scan that happened does not stop having happened when it ages off the hundred-row page, so a node absent from a later listing is never tombstoned on that absence.

## Authoritative Source

- **Source:** GitHub REST API — Code scanning: "List code scanning analyses for a repository" (`GET /repos/{owner}/{repo}/code-scanning/analyses`), the `code-scanning-analysis` schema (`id`, `ref`, `commit_sha`, `analysis_key`, `environment`, `category`, `error`, `warning`, `created_at`, `results_count`, `rules_count`, `url`, `sarif_id`, `tool {name, version, guid}`, `deletable`), https://docs.github.com/en/rest/code-scanning/code-scanning
- **Version:** REST API version `2022-11-28`, as pinned in `github_openapi_extract.json`
- **Retrieved:** 2026-09-09 (the 72/24/2/2 population and the empty SonarCloud category read from the live endpoint with the App credential)

## Prior Art

- Operator ruling, session double-tap-git-serious (2026-09-09; github-core#89) — findings generic, detail behind an edge; the analysis is the "did it run" record beside them.
- OASIS SARIF 2.1.0 (OASIS Standard, 2020-03-27) — `run`, `tool.driver {name, version, guid}`, `automationDetails.id` (GitHub's `category` is derived from it): one analysis per `run` object in an upload.
- OCSF Detection Finding (class 2004) and Vulnerability Finding (class 2002), OCSF schema v1.3.0 (2024) — `finding_info.analytic` / `finding_info.product`: the finding names its detector; OCSF has no first-class "scan ran" object either, which is why GitHub's is worth carrying.
- OWASP CycloneDX VDR, CycloneDX 1.6 (2024-04) — `metadata.tools` and `vulnerabilities[].source`: a report names the tool that produced it, and a report with zero vulnerabilities is still a report.
- `specs/spec-github-core-vocabulary.md` (2026-08-27) — finding 1, declaration versus execution: an analysis is an execution record and is stamped so.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §2.11 (2026-09-03) — the CONSUME verdict.

## Fields

- `full_name` — `owner/repo`; half the natural key.
- `analysis_id` — GitHub's numeric id for the analysis; the other half.
- `ref` — the ref the upload was for (`refs/heads/main`, `refs/pull/348/merge`); a merge ref is common for pull-request scans and is not a branch on the grid.
- `commit_sha` — the commit analysed. The fact on the node; `ANALYZES_COMMIT` is the edge, drawn only when the commit is on the grid.
- `analysis_key` — the workflow path and job that uploaded (`.github/workflows/codeql.yml:analyze`), or the upload's own key outside Actions. The field-level join to the producing workflow, by path.
- `category` — GitHub's category for the analysis (`/language:python`, `trivy-tap-web`; `""` when the tool set none — SonarCloud, measured). With `tool_name`, the loose join to alerts.
- `environment` — the matrix environment string; `""` outside a matrix.
- `tool_name` — the scanner from the SARIF `driver` (`CodeQL`, `SonarCloud`, `Trivy`, `Grype`).
- `tool_version` — the driver's version; `""` when omitted.
- `tool_guid` — the driver's GUID; `""` when omitted.
- `created_at` — when GitHub recorded the analysis (the upload time, not the scan's start).
- `results_count` — how many results the upload contained, as GitHub counted them; zero is a real answer.
- `rules_count` — how many rules the upload declared.
- `sarif_id` — the id of the SARIF upload this analysis came from; several analyses share one when the file held several runs.
- `deletable` — GitHub's flag for whether this analysis can be deleted (only the most recent in a chain can); carried as reported.
- `warning` — GitHub's processing warning text, verbatim; `""` when none. The place GitHub says "results were truncated" or "the SARIF was too large".
- `error` — GitHub's processing error text, verbatim; `""` when none.
- `url` — the API URL of the analysis (the SARIF download is behind it).
- `configuration` — JSONB holding the raw analysis as returned.
- `tags` — TAP's tag map.
