# GitHub Runner

## Blurb

A durable, registered self-hosted Actions runner — the machine CI jobs execute on when they are not executing on GitHub's.

## Purpose

A self-hosted runner is where CI stops being a sandbox and becomes infrastructure. It has a filesystem that persists between jobs unless it is ephemeral, it sits inside a network, and it holds whatever the last job left behind. Nine of the surveyed sources model a runner, and the incident corpus is unambiguous about why: a self-hosted runner attached to a public repository is one of the highest-evidence conditions in the whole set, because anyone who can open a pull request can, under the wrong trigger, run code on it.

This node exists so that "which repositories have self-hosted runners, and are any of them public" is a graph query rather than an audit.

## Goals

- Inventory durable runners so their scope and labels are queryable.
- Carry `labels`, which is what a workflow's `runs-on:` matches against — the join between declaration and machine.
- Anchor [`EXECUTED_ON`](EXECUTED_ON.md), so an execution can be attributed to a machine.

## Identity

Natural key: **`<full_name>#<runner_id>`** — the repository the runner is registered to, plus GitHub's numeric runner id. Entity id is `uuid5(ns, "github_core__github_runner:<full_name>#<runner_id>")`.

Not the runner **name**: names are operator-chosen, frequently templated (`runner-01`, `runner-02`), and not unique across scopes. The numeric id is GitHub's and is stable for the life of the registration.

The repository prefix encodes a v0 scope decision: only repository-level runners are collected, so the registration scope is always a repository. Organization-level runners and runner groups are corpus concepts at the *friends* tier (`runner_group`, `MEMBER_OF_RUNNER_GROUP`, `REGISTERED_ON` with `{first_seen, scope}`) and would need a key that admits an account as the scope.

## Boundaries

Deliberately **not** covered:

- **Ephemeral runners as nodes.** Explicitly excluded (`req-github-core-runner`). An ephemeral runner exists for one job and is gone; minting a node per job would fill the grid with entities nothing can point at afterwards. The runner a job *observed* stays on the [`github_actions_job`](github_actions_job.md) node instead, and an [`EXECUTED_ON`](EXECUTED_ON.md) edge is emitted only when that observation matches a durable runner node.
- **Organization runners and runner groups.** Corpus concepts at *friends*; the platform survey notes the published GitHub graph is ahead of us here.
- **The machine behind the runner.** A host, its network position, its other workloads — none of it is visible from GitHub, and none of it is modelled. The runner node is a *registration*, not a server.
- **Runner version currency.** The API returns a version; nothing consumes it yet.

## Neutrality

**Vendor-specific.** The corpus marks it `no`. Other CI systems have executors and agents, but the registration model here — a runner registered to a repository or organization, selected by label from `runs-on:` — is GitHub Actions' own. A neutral substrate would need a genuinely different abstraction, not a rename of this one.

## Observability

Populated from `GET /repos/{o}/{r}/actions/runners` at **`repository:administration:read`** — the most expensive permission this plugin asks for, and the only source in the collection manifest that declares `permission_failure: degrade_with_warning`.

That degradation is the important operational fact: a credential without repository administration gets **403**, and the collector records a warning and continues rather than failing the run (`req-github-core-collector-5`). The consequence for a reader is that **an empty runner set is ambiguous** — it means either "no self-hosted runners" or "we were not allowed to look", and those are opposite findings. Check the run's warnings before concluding a repository has no self-hosted runners.

This is the same shape as the ruleset `bypass_actors` problem recorded in the corpus: absence that reads as reassurance. There, the fix was a mandatory `observable` property on the edge so a view can render *none / some / not-observable* as three states rather than two. The same three-state discipline applies to any view of runners.

**Not observable:** anything about the machine itself; the runner's registration token or its network position; whether a runner labelled ephemeral actually is.

## Authoritative Source

- **Source:** GitHub REST API — Self-hosted runners (`GET /repos/{owner}/{repo}/actions/runners`), and the fine-grained personal-access-token permissions reference for the administration level
- **Version:** REST API version `2022-11-28`
- **Retrieved:** 2026-08-27 (the 403-degradation path exercised against a credential without administration scope)

## Prior Art

- `specs/spec-github-core-vocabulary.md` (2026-08-27) — 9 sources; marks the type vendor-specific and names `runner_group` as the gap where the published GitHub graph is ahead of us.
- `git-serious-tap/docs/doc-git-serious-cicd-security-prior-art.md` §3.9–3.10 (2026-08-27) — the runners endpoint's fields and permission level, and the self-hosted-on-public-repository condition with its incident evidence.
- GitHub REST API, version `2022-11-28` — Self-hosted runners endpoints.

## Fields

- `full_name` — the repository the runner is registered to. Half the natural key, and the scope.
- `runner_id` — GitHub's numeric runner id, the other half. Stable across the registration's life, unlike the name.
- `name` — the operator-chosen runner name. Display, and the value matched against the runner name a job observed. Not identity: names collide.
- `os` — the reported operating system. Descriptive; part of understanding what a compromise of this runner would reach.
- `status` — `online` or `offline`. A point-in-time observation, so the grid's history is what turns it into "a runner that appeared where none had been" — a signal the corpus names via `REGISTERED_ON.first_seen`.
- `busy` — whether the runner was executing a job at observation time. The most volatile field on the node and the least analytically useful; kept because it comes free and its churn is itself a rough activity signal.
- `labels` — the runner's labels. Load-bearing: `runs-on:` in a workflow matches against these, so labels are the join between a declaration and the machine that will serve it. A workflow requesting a label no runner carries will queue forever, and a label carried by an unexpected runner is how a job lands somewhere it should not.
- `configuration` — the remainder of the payload, including the reported runner version and group membership when present.
- `tags` — TAP's tag map.
