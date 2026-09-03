"""Deterministic UUIDv5 entity IDs for github_core nodes.

Spec: plugins/github_core/specs/spec-github-core-v0.md
(req-github-core-models-4 Deterministic Identity). The natural-key table in
the spec is the source of truth for each entity type's identity inputs.
"""

from __future__ import annotations

from uuid import NAMESPACE_DNS, UUID, uuid5

# A stable, github_core-specific namespace derived from the canonical DNS
# namespace. Using a fixed UUID here keeps entity IDs reproducible across
# environments without depending on a runtime random seed.
GITHUB_CORE_NAMESPACE: UUID = uuid5(NAMESPACE_DNS, "github_core.tap")


def _id(entity_type: str, natural_key: str) -> UUID:
    return uuid5(GITHUB_CORE_NAMESPACE, f"{entity_type}:{natural_key}")


def platform_id(host: str) -> UUID:
    # Natural key is the host ("github.com"); a GHES tenant gets its own id.
    return _id("github_core__github_platform", host)


def account_id(login: str) -> UUID:
    return _id("github_core__github_account", login)


def repository_id(full_name: str) -> UUID:
    return _id("github_core__github_repository", full_name)


def workflow_id(full_name: str, workflow_id_int: int | str) -> UUID:
    return _id("github_core__github_workflow", f"{full_name}#{workflow_id_int}")


def github_app_id(slug: str) -> UUID:
    # Natural key is the app slug ("dependabot"); one app node is shared across
    # every repo that enables it (ENABLED_ON edges fan in).
    return _id("github_core__github_app", slug)


def workflow_job_id(full_name: str, workflow_id_int: int | str, job_key: str) -> UUID:
    """A DECLARED job: the workflow it is written in, plus its YAML key.

    Keyed on the workflow id rather than the file path so a renamed file keeps the same job
    nodes, and on the job key rather than the display name because `name:` is free text an
    author changes without changing what the job is.
    """
    return _id("github_core__workflow_job", f"{full_name}#{workflow_id_int}#{job_key}")


def git_ref_id(full_name: str, ref: str) -> UUID:
    """A ref, keyed on its FULL path (`refs/heads/main`).

    The full path rather than the short name, because a branch and a tag may share one
    (`refs/heads/release` and `refs/tags/release` are different objects with the same name).
    """
    return _id("github_core__git_ref", f"{full_name}#{ref}")


def ruleset_id(owner: str, ruleset_id_int: int | str) -> UUID:
    """A ruleset, keyed on owner + GitHub's ruleset id.

    Not repo-scoped: one organization ruleset applies to many repositories and must be ONE node
    that many repositories point at, or the question "what does this ruleset protect" becomes a
    string comparison across duplicates. Measured on the fixture org: 3 organization rulesets
    reported by 19 repositories = 57 attachments over 3 nodes.

    The owner prefix is belt-and-braces. GitHub's ruleset `databaseId` was measured to be
    PLATFORM-global rather than per-account — org- and repo-sourced ids interleave when
    sorted, id order is exactly creation order, and an org owning six rulesets holds ids near
    20.6 million rather than 1-6 — so the bare id would also have keyed correctly. Recorded so
    nobody re-derives it: the prefix costs nothing and a natural key cannot be changed once
    nodes exist.
    """
    return _id("github_core__github_ruleset", f"{owner}#{ruleset_id_int}")


def rule_suite_id(suite_id_int: int | str) -> UUID:
    """A rule suite, keyed on GitHub's own suite id — unique across the platform.

    Not scoped by repository: the id is assigned by GitHub and the suite carries its own
    `repository_name`, so scoping would add nothing and would break the join if the same
    suite were ever reached from another path.
    """
    return _id("github_core__rule_suite", str(suite_id_int))


def environment_id(full_name: str, name: str) -> UUID:
    return _id("github_core__github_environment", f"{full_name}#{name}")


def actions_cache_id(full_name: str, cache_id_int: int | str) -> UUID:
    return _id("github_core__actions_cache", f"{full_name}#{cache_id_int}")


def actions_artifact_id(full_name: str, artifact_id_int: int | str) -> UUID:
    """An artifact, keyed on the repository plus GitHub's artifact id.

    The id is platform-global (the same generator as runs and caches), so the repository
    prefix is belt-and-braces in the same way `ruleset_id`'s owner prefix is — and a natural
    key cannot change once nodes exist, so it is recorded rather than re-derived.
    """
    return _id("github_core__actions_artifact", f"{full_name}#{artifact_id_int}")


def app_installation_id(installation_id_int: int | str) -> UUID:
    """An installation, keyed on GitHub's installation id — unique across the platform."""
    return _id("github_core__app_installation", str(installation_id_int))


def run_id(full_name: str, run_id_int: int | str) -> UUID:
    # v0 natural key is owner/repo + run_id (run_attempt deferred — see
    # req-github-core-backlog-run-attempts).
    return _id("github_core__github_actions_run", f"{full_name}#{run_id_int}")


def job_id(full_name: str, job_id_int: int | str) -> UUID:
    return _id("github_core__github_actions_job", f"{full_name}#{job_id_int}")


def runner_id(full_name: str, runner_id_int: int | str) -> UUID:
    return _id("github_core__github_runner", f"{full_name}#{runner_id_int}")


def github_action_id(action_path: str) -> UUID:
    """An action, keyed on the `uses:` path with the ref stripped.

    Platform-global rather than repository-scoped, like `github_app`: `actions/checkout` is
    ONE node every job on every repository points at, or "which jobs use an unpinned checkout"
    becomes a string comparison across duplicates. The ref is deliberately NOT here — the same
    action is pinned differently by different jobs, and the pin belongs to the edge.
    """
    return _id("github_core__github_action", action_path)


def uses_action_edge_id(job_uuid: UUID, action_uuid: UUID, declared_ref: str) -> UUID:
    """A `USES_ACTION` edge, keyed on the job, the action AND the ref as written.

    Not the generic `edge_id` (type, source, target): a job that calls the same action at two
    refs — `actions/checkout@v4` in one step and `actions/checkout@<sha>` in another — is two
    facts, and an id that ignored the ref would keep only the last one after envelope collapse.
    """
    return uuid5(GITHUB_CORE_NAMESPACE, f"edge:USES_ACTION__github_core:{job_uuid}:{action_uuid}:{declared_ref}")


def edge_id(edge_type: str, source: UUID, target: UUID) -> UUID:
    """Deterministic UUIDv5 for an edge by (type, source, target)."""
    return uuid5(GITHUB_CORE_NAMESPACE, f"edge:{edge_type}:{source}:{target}")
