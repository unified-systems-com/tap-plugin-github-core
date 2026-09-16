"""The wildcard target on REFERENCES_RESOURCE is a GRANT, not an absence.

github-core#148 step 1 dropped `targets` from the edge definition because the
set of resource types GitHub configuration can name is owned by OTHER plugins,
not by this one. Omitting a side is a wildcard for that side
(`tap_grid/specs/spec-grid-edge.md`, req-grid-edge-constraints-3).

That contract has a trap worth a test: in `tap_grid.constraints`,
`_edge_allows_target` treats `None` and the `WILDCARD` sentinel as OPPOSITES —
`None` grants nothing, `WILDCARD` grants everything. The omitted key has to
survive `manifest.py` -> `base.py` -> `register_edge_type_constraints()` and
arrive as `WILDCARD`. A declaration that merely *looks* open while registering
as `None` would silently stop permitting cross-grid links, and the empty
cross-grid panel would read as "AWS has not run yet."
"""

from tap_grid.constraints import WILDCARD, get_edge_type_constraints

EDGE_TYPE = "REFERENCES_RESOURCE__github_core"


class TestCrossGridReferenceConstraints:
    def test_the_target_side_registers_as_a_wildcard(self) -> None:
        """Not `None` (no grant) and not a set (a closed list) — an open grant."""
        constraints = get_edge_type_constraints(EDGE_TYPE)
        assert constraints is not None, f"{EDGE_TYPE} is not registered"
        assert constraints.targets is WILDCARD, (
            "the omitted `targets` key must reach the registry as WILDCARD; "
            f"got {constraints.targets!r}"
        )

    def test_the_source_side_stays_closed_to_types_this_plugin_owns(self) -> None:
        """Only the github end is ours to enumerate, so only the github end is enumerated."""
        constraints = get_edge_type_constraints(EDGE_TYPE)
        assert constraints is not None
        assert constraints.sources == {
            "github_core__github_workflow",
            "github_core__github_actions_run",
            "github_core__github_actions_job",
        }
