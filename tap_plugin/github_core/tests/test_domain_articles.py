"""Domain-article coverage for github_core — `specs/spec-domain-articles.md`.

Every node type and edge type this plugin registers owes a domain article beside the
models, and every `FIELD_CRUD_SCHEMA` key owes a paragraph in it. TAP's core guard
(`tap.guards.domain_articles`) runs this check over plugins that live *in* the core
repository; github_core lives in its own repository, so it runs the same measurement
here, over its own root and against its own baseline.

The baseline is this plugin's documentation debt, and it ratchets one way. A finding
that no longer occurs fails too, so a written article cannot linger as an exception.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tap.domain_articles import baseline_path_for, finding_key, findings_for_root
from tap.ratchet import ratchet_ceiling, read_baseline_set

#: `tap_plugin/github_core` — the owner root the scanner walks.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
#: The repository root, so a baseline entry reads as a path from a clone's top.
REPO_ROOT = PACKAGE_ROOT.parents[1]

_NEW_HINT = (
    "Write the domain article beside the model: `tap_plugin/github_core/domain/<concept>.md`. "
    "See `specs/spec-github-core-vocabulary.md` for why the concept earns its place, and write "
    "Observability from an executed call rather than from GitHub's documentation."
)


@pytest.mark.spec("req-domain-articles-coverage-1")
def test_every_registered_type_has_a_conforming_article():
    """Each node and edge type has a field-complete article, or a recorded, shrinking debt."""
    current = {finding_key(f, REPO_ROOT) for f in findings_for_root(PACKAGE_ROOT)}
    ratchet_ceiling(
        current=current,
        baseline=read_baseline_set(baseline_path_for(PACKAGE_ROOT)),
        surface="Domain-article coverage — github_core",
        baseline_path=baseline_path_for(PACKAGE_ROOT),
        new_hint=_NEW_HINT,
    )


def test_the_scanner_sees_this_plugin():
    """Guard the guard: an empty scan would pass the ratchet while proving nothing."""
    from tap.domain_articles import subjects_for_root

    subjects = subjects_for_root(PACKAGE_ROOT)
    slugs = {s.slug for s in subjects}
    assert "github_core__github_workflow" in slugs
    assert "EXECUTES_WORKFLOW__github_core" in slugs
