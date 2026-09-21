"""One fake GitHub estate, shared by every test that runs a whole collection.

Derive a fact once. `test_reconcile_adoption` and `test_assigned_identity` both drive the
collector end to end through `run_collection` against the same one-account / one-repository /
one-workflow / one-environment estate, and a second copy of that fixture is a second thing to
keep true: a test that quietly diverged on what the source says would still be green while
asserting about a different world.

The estate, in one place so it can be named:

- account `acme`, an Organization with numeric id 1;
- repository `acme/app` (id 10), public, default branch `main`;
- one workflow, `ci.yml` (id 100), declaring one job with the key `build`;
- one environment, `production` (id 1);
- no runs, no runners, no pull requests, no releases.

Each piece is a FUNCTION returning a fresh object rather than a module-level constant: a test
that mutates the estate to describe a second repository, or a refusal, must not reach into
another test's copy.
"""

from __future__ import annotations

from typing import Any

from tap_cares.models import Collector
from tap_cares.registry import reconcile_collector_nodes, register_collector

from .fake_github import FakeGithub

OWNER = "acme"
REPO = "acme/app"
WORKFLOW_YAML = "name: ci\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps: []\n"


class Secret:
    """The credential the collector resolves, deliberately NOT token-shaped.

    A `ghp_` prefix plus 36 characters is what every secret scanner keys on, and the collector
    only reads the prefix to name the kind — so the fixture gives it a string no scanner will
    ever flag.
    """

    kind = "github_pat"
    data: dict[str, Any] = {"token": "fixture-not-a-credential", "owner": OWNER}


def config_repo() -> dict[str, Any]:
    """`acme/app` as the GraphQL config layer shapes it: one workflow file, one environment."""
    return {
        "nameWithOwner": REPO,
        "name": "app",
        "databaseId": 10,
        "isArchived": False,
        "isFork": False,
        "visibility": "PUBLIC",
        "url": f"https://github.com/{REPO}",
        "defaultBranchRef": {"name": "main", "target": {"oid": "a" * 40}},
        "rulesets": {"nodes": []},
        "environments": {"nodes": [{"databaseId": 1, "name": "production", "protectionRules": {"nodes": []}}]},
        "branchRefs": {"totalCount": 0, "nodes": []},
        "tagRefs": {"totalCount": 0, "nodes": []},
        "releases": {"totalCount": 0, "nodes": []},
        "object": {
            "entries": [
                {
                    "name": "ci.yml",
                    "path": ".github/workflows/ci.yml",
                    "object": {"byteSize": len(WORKFLOW_YAML), "isTruncated": False, "text": WORKFLOW_YAML},
                }
            ]
        },
    }


def fake_rest() -> FakeGithub:
    """The REST surfaces the walk reads for this estate. Callers add or refuse more."""
    fake = FakeGithub()
    fake.answer(
        f"/users/{OWNER}",
        {"login": OWNER, "id": 1, "type": "Organization", "html_url": f"https://github.com/{OWNER}"},
    )
    fake.answer(
        f"/repos/{REPO}/actions/workflows",
        {"workflows": [{"id": 100, "path": ".github/workflows/ci.yml", "name": "ci", "state": "active"}]},
    )
    fake.answer(f"/repos/{REPO}/actions/runs", {"workflow_runs": []})
    fake.answer(f"/repos/{REPO}/actions/runners", {"runners": []})
    fake.answer(f"/repos/{REPO}/environments/production", {"id": 1, "name": "production"})
    return fake


def register(cls: type) -> Collector:
    """Register the collector and return its row. Call inside `isolated_registry`."""
    register_collector(
        key="github_core",
        scope="github_core",
        cls=cls,
        name="GitHub Core Collector",
        description="fixture",
    )
    reconcile_collector_nodes()
    collector = Collector.objects.get(collector_registry="github_core:github_core")
    assert isinstance(collector, Collector)
    return collector
