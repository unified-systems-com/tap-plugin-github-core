"""Settings surfaces: the organization's policy block, its Actions policy, a repository's settings
and an environment's gatekeepers (github-core#110, req-github-core-settings).

The behaviour under test is one rule applied four times: **a key GitHub did not return is a key we
did not observe**, and the node says so beside the values rather than defaulting. The values
themselves are copied verbatim under GitHub's names — there is nothing to assert about them except
that they arrive unrenamed and that nothing is invented when they do not.

Three states, never two, at every surface: observed / observed-but-only-the-public-half or
refused / not applicable (a user account has no organization policy).
"""

from __future__ import annotations

from typing import Any

import tap_plugin.github_core.models as github  # noqa: F401 — trigger model registration
from tap_plugin.github_core.collectors.github_collector.api_client import GithubAPIError
from tap_plugin.github_core.collectors.github_collector.collector import GithubCollector

_ORG = "acme"


class _Client:
    """Answers canned GETs; a path in `statuses` is refused with that status."""

    last_walk_complete = True

    def __init__(self, rows: dict[str, Any], statuses: dict[str, int] | None = None) -> None:
        self.rows = rows
        self.statuses = statuses or {}
        self.calls: list[str] = []

    def get(self, path: str, **_: Any) -> Any:
        self.calls.append(path)
        status = self.statuses.get(path)
        if status is not None:
            raise GithubAPIError(status=status, url=path, body='{"message":"Forbidden"}')
        if path not in self.rows:
            raise GithubAPIError(status=404, url=path, body='{"message":"Not Found"}')
        return self.rows[path]

    def get_paginated(self, path: str, **_: Any) -> list[Any]:
        return list(self.get(path))


def _collector(account_type: str = "Organization") -> GithubCollector:
    c = GithubCollector.__new__(GithubCollector)
    c._account_type = account_type
    c._org_detail = None
    c._org_detail_state = ""
    c._org_actions_policy = None
    c._org_actions_policy_state = ""
    c.records: list[tuple[str, str, str]] = []  # type: ignore[attr-defined]
    c.record_warn = lambda site, code, message, **kw: c.records.append(("warn", code, message))  # type: ignore[method-assign]
    c.record_info = lambda site, code, message, **kw: c.records.append(("info", code, message))  # type: ignore[method-assign]
    return c


def _codes(c: GithubCollector, level: str) -> list[str]:
    return [code for lvl, code, _ in c.records if lvl == level]  # type: ignore[attr-defined]


_ORG_PUBLIC = {"login": _ORG, "id": 1, "type": "Organization", "is_verified": False, "public_repos": 24}
_ORG_POLICY = {
    **_ORG_PUBLIC,
    "two_factor_requirement_enabled": True,
    "default_repository_permission": "none",
    "members_can_create_repositories": False,
    "members_can_fork_private_repositories": False,
    "web_commit_signoff_required": False,
    "secret_scanning_push_protection_enabled_for_new_repositories": True,
    "plan": {"name": "team", "seats": 3, "filled_seats": 2},
    # Not a settings key: must not be copied.
    "billing_email": "ops@acme.example",
}


# ---------------------------------------------------------------------------------------------
# Organization policy block
# ---------------------------------------------------------------------------------------------


def test_policy_keys_are_copied_verbatim_and_marked_observed() -> None:
    c = _collector()
    client = _Client({f"/orgs/{_ORG}": _ORG_POLICY})
    configuration = c._account_configuration(client, {"login": _ORG, "type": "Organization"})
    assert configuration["two_factor_requirement_enabled"] is True
    assert configuration["default_repository_permission"] == "none"
    assert configuration["plan"] == {"name": "team", "seats": 3, "filled_seats": 2}
    assert configuration["settings_observability"] == "observed"
    # The method restriction ("only secure two-factor methods") has no API field at any tier: it is
    # written as an explicit ceiling, never inferred from the requirement boolean.
    assert configuration["two_factor_secure_methods_required"] is None
    assert configuration["two_factor_methods_observability"] == "unobservable"
    assert "billing_email" not in configuration, "only settings keys travel; contact data does not"
    assert _codes(c, "warn") == [] and _codes(c, "info") == []


def test_public_half_only_is_named_not_defaulted() -> None:
    """A member token gets `/orgs/{org}` with no policy key. That is a fact about the credential."""
    c = _collector()
    client = _Client({f"/orgs/{_ORG}": _ORG_PUBLIC})
    configuration = c._account_configuration(client, {"login": _ORG, "type": "Organization"})
    assert configuration["public_repos"] == 24
    assert "two_factor_requirement_enabled" not in configuration, "an absent key must stay absent"
    assert configuration["settings_observability"] == "public_only"
    assert "ORG_SETTINGS_PUBLIC_ONLY" in _codes(c, "info")


def test_refused_org_detail_is_unobservable_with_no_keys() -> None:
    c = _collector()
    client = _Client({}, statuses={f"/orgs/{_ORG}": 403})
    configuration = c._account_configuration(client, {"login": _ORG, "type": "Organization"})
    assert configuration["settings_observability"] == "unobservable"
    assert set(configuration) <= {
        "settings_observability",
        "actions_policy_observability",
        "two_factor_secure_methods_required",
        "two_factor_methods_observability",
    }
    assert "ORG_SETTINGS_UNOBSERVABLE_403" in _codes(c, "warn")


def test_user_account_has_no_org_policy_and_says_so() -> None:
    c = _collector(account_type="User")
    client = _Client({})
    configuration = c._account_configuration(client, {"login": "someone", "type": "User"})
    assert configuration == {"settings_observability": "not_applicable"}
    assert client.calls == [], "no organization endpoint is asked of a user"


def test_org_detail_is_fetched_once_per_run() -> None:
    """The account node is minted once per repository; the settings call must not be."""
    c = _collector()
    client = _Client({f"/orgs/{_ORG}": _ORG_POLICY})
    for _ in range(3):
        c._account_configuration(client, {"login": _ORG, "type": "Organization"})
    assert client.calls == [f"/orgs/{_ORG}"]


# ---------------------------------------------------------------------------------------------
# Actions policy
# ---------------------------------------------------------------------------------------------


def test_actions_policy_observed_and_stamped_onto_the_account() -> None:
    c = _collector()
    client = _Client(
        {
            f"/orgs/{_ORG}/actions/permissions": {
                "enabled_repositories": "all",
                "allowed_actions": "selected",
                "sha_pinning_required": True,
                "selected_actions_url": "https://api.github.example/...",
            },
            f"/orgs/{_ORG}/actions/permissions/selected-actions": {
                "github_owned_allowed": True,
                "verified_allowed": False,
                "patterns_allowed": ["unified-systems-com/*"],
            },
            f"/orgs/{_ORG}": _ORG_POLICY,
        }
    )
    policy, state = c._collect_org_actions_policy(client, _ORG)
    assert state == "observed"
    assert policy == {
        "enabled_repositories": "all",
        "allowed_actions": "selected",
        "sha_pinning_required": True,
        "selected_actions": {
            "github_owned_allowed": True,
            "verified_allowed": False,
            "patterns_allowed": ["unified-systems-com/*"],
        },
    }
    c._org_actions_policy, c._org_actions_policy_state = policy, state
    configuration = c._account_configuration(client, {"login": _ORG, "type": "Organization"})
    assert configuration["actions_policy"]["sha_pinning_required"] is True
    assert configuration["actions_policy_observability"] == "observed"


def test_actions_policy_refused_is_unobservable_not_permissive() -> None:
    c = _collector()
    client = _Client({f"/orgs/{_ORG}": _ORG_POLICY}, statuses={f"/orgs/{_ORG}/actions/permissions": 403})
    policy, state = c._collect_org_actions_policy(client, _ORG)
    assert policy is None and state == "unobservable"
    assert "ACTIONS_POLICY_UNOBSERVABLE_403" in _codes(c, "warn")
    c._org_actions_policy, c._org_actions_policy_state = policy, state
    configuration = c._account_configuration(client, {"login": _ORG, "type": "Organization"})
    assert "actions_policy" not in configuration, "a refused policy is not an empty policy"
    assert configuration["actions_policy_observability"] == "unobservable"


def test_actions_policy_not_asked_of_a_user_account() -> None:
    c = _collector(account_type="User")
    client = _Client({})
    assert c._collect_org_actions_policy(client, "someone") == (None, "not_applicable")
    assert client.calls == []


def test_actions_policy_resolves_the_account_kind_when_not_yet_known() -> None:
    """The policy is read before the repository walk mints the account: an unknown kind is looked up,
    and a user owner is not asked for an organization policy (its 404 must not read as a refusal)."""
    c = _collector(account_type="")
    client = _Client({"/users/someone": {"login": "someone", "type": "User"}})
    assert c._collect_org_actions_policy(client, "someone") == (None, "not_applicable")
    assert client.calls == ["/users/someone"]
    assert _codes(c, "warn") == []


# ---------------------------------------------------------------------------------------------
# Repository settings
# ---------------------------------------------------------------------------------------------

_REPO_ADMIN = {
    "full_name": f"{_ORG}/widget",
    "allow_forking": True,
    "archived": False,
    "is_template": False,
    "delete_branch_on_merge": True,
    "topics": ["tap-fixture"],
    "security_and_analysis": {
        "secret_scanning": {"status": "enabled"},
        "secret_scanning_push_protection": {"status": "disabled"},
    },
    # Not settings: must not be copied.
    "stargazers_count": 3,
    "pushed_at": "2026-09-11T00:00:00Z",
}


def test_repository_settings_copied_verbatim_including_the_admin_block() -> None:
    configuration = GithubCollector._repository_configuration(_REPO_ADMIN)
    assert configuration["allow_forking"] is True
    assert configuration["topics"] == ["tap-fixture"]
    assert configuration["security_and_analysis"]["secret_scanning_push_protection"] == {"status": "disabled"}
    assert configuration["settings_observability"] == "observed"
    assert configuration["security_settings_observability"] == "observed"
    assert "stargazers_count" not in configuration and "pushed_at" not in configuration


def test_missing_security_block_means_not_allowed_to_look() -> None:
    """GitHub omits `security_and_analysis` for a non-admin caller. Omitted is not disabled."""
    payload = {k: v for k, v in _REPO_ADMIN.items() if k != "security_and_analysis"}
    configuration = GithubCollector._repository_configuration(payload)
    assert configuration["settings_observability"] == "observed"
    assert configuration["security_settings_observability"] == "unobservable"
    assert "security_and_analysis" not in configuration


def test_refused_repository_payload_records_only_the_refusal() -> None:
    c = _collector()
    client = _Client({}, statuses={f"/repos/{_ORG}/widget": 403})
    payload = c._fetch_repository_settings(client, f"{_ORG}/widget")
    assert payload is None
    assert "REPO_SETTINGS_UNOBSERVABLE_403" in _codes(c, "warn")
    assert GithubCollector._repository_configuration(payload) == {"settings_observability": "unobservable"}


# ---------------------------------------------------------------------------------------------
# Environment detail
# ---------------------------------------------------------------------------------------------

_ENV_DETAIL = {
    "name": "production",
    "html_url": "https://github.example/acme/widget/deployments/activity_log?environments_filter=production",
    "can_admins_bypass": False,
    "deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False},
    "protection_rules": [
        {"type": "wait_timer", "wait_timer": 30},
        {
            "type": "required_reviewers",
            "prevent_self_review": True,
            "reviewers": [
                {"type": "User", "reviewer": {"login": "notgeorge", "id": 286052}},
                {"type": "Team", "reviewer": {"slug": "maintainers", "id": 99}},
            ],
        },
    ],
}


def test_environment_detail_lifts_reviewers_admin_bypass_and_branch_policy() -> None:
    client = _Client({f"/repos/{_ORG}/widget/environments/production": _ENV_DETAIL})
    detail = GithubCollector._fetch_environment_detail(client, f"{_ORG}/widget", "production")
    assert isinstance(detail, dict)
    configuration = GithubCollector._environment_configuration(detail, client)
    assert configuration["detail_observability"] == "observed"
    assert configuration["prevent_self_review"] is True
    assert configuration["wait_timer"] == 30
    assert configuration["required_reviewers"] == [
        {"type": "User", "login": "notgeorge", "id": 286052},
        {"type": "Team", "login": "maintainers", "id": 99},
    ]


def test_environment_name_is_url_encoded() -> None:
    client = _Client(
        {f"/repos/{_ORG}/widget/environments/staging%2Feu": {"name": "staging/eu", "protection_rules": []}}
    )
    detail = GithubCollector._fetch_environment_detail(client, f"{_ORG}/widget", "staging/eu")
    assert isinstance(detail, dict) and detail["name"] == "staging/eu"


def test_refused_environment_detail_returns_the_status_and_reads_as_unobservable() -> None:
    client = _Client({}, statuses={f"/repos/{_ORG}/widget/environments/production": 403})
    detail = GithubCollector._fetch_environment_detail(client, f"{_ORG}/widget", "production")
    assert detail == 403
    assert GithubCollector._environment_configuration(None, client) == {"detail_observability": "unobservable"}


def test_environment_detail_without_a_client_is_never_looked_not_refused() -> None:
    assert GithubCollector._fetch_environment_detail(None, f"{_ORG}/widget", "production") is None
    assert GithubCollector._environment_configuration(None, None) == {"detail_observability": ""}


# ---------------------------------------------------------------------------------------------
# The environment emitter keeps its containment edge on every path (unified review on PR# 113)
# ---------------------------------------------------------------------------------------------


def _gql_with_environments(*names: str) -> dict[str, Any]:
    return {
        "environments": {
            "nodes": [{"databaseId": 100 + i, "name": n, "protectionRules": {"nodes": []}} for i, n in enumerate(names)]
        }
    }


def _emit(c: GithubCollector, client: Any, full_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from tap_plugin.github_core.collectors.github_collector.identity import repository_id

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    c._emit_environments(
        full_name,
        repository_id(full_name),
        {"github.platform": "github.com", "github.owner": _ORG, "github.repo": "widget"},
        nodes,
        edges,
        client=client,
    )
    return nodes, edges


def test_every_environment_gets_its_containment_edge_when_every_detail_succeeds() -> None:
    c = _collector()
    c._config = {f"{_ORG}/widget": _gql_with_environments("production", "staging")}  # type: ignore[assignment]
    client = _Client(
        {
            f"/repos/{_ORG}/widget/environments/production": _ENV_DETAIL,
            f"/repos/{_ORG}/widget/environments/staging": {"name": "staging", "protection_rules": []},
        }
    )
    nodes, edges = _emit(c, client, f"{_ORG}/widget")
    assert [n["node"]["name"] for n in nodes] == ["production", "staging"]
    declares = [e for e in edges if e["edge"]["edge_type"] == "DECLARES_ENVIRONMENT__github_core"]
    assert len(declares) == 2, "one containment edge per environment, refusal or not"
    assert {e["edge"]["to_entity_id"] for e in declares} == {n["entity"]["entity_id"] for n in nodes}
    assert _codes(c, "warn") == []
    assert nodes[0]["node"]["can_admins_bypass"] is False and nodes[1]["node"]["can_admins_bypass"] is None


def test_a_refused_detail_keeps_the_edge_and_warns_once_per_repository() -> None:
    c = _collector()
    c._config = {f"{_ORG}/widget": _gql_with_environments("production", "staging")}  # type: ignore[assignment]
    client = _Client(
        {f"/repos/{_ORG}/widget/environments/production": _ENV_DETAIL},
        statuses={f"/repos/{_ORG}/widget/environments/staging": 403},
    )
    nodes, edges = _emit(c, client, f"{_ORG}/widget")
    declares = [e for e in edges if e["edge"]["edge_type"] == "DECLARES_ENVIRONMENT__github_core"]
    assert len(declares) == 2
    assert _codes(c, "warn") == ["ENVIRONMENT_DETAIL_UNOBSERVABLE"]
    staging = next(n for n in nodes if n["node"]["name"] == "staging")
    assert staging["node"]["deployment_branch_policy"] is None
    assert staging["node"]["configuration"] == {"detail_observability": "unobservable"}
