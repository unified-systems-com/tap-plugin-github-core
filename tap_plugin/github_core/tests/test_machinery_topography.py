"""Topographic colouring of the machinery projection.

Spec: plugins/github_core/specs/spec-github-core-machinery-projection.md
      (req-github-core-machinery-topography-1, -2)

The palette is model metadata (``DEFAULT_DISPLAY``), read at render; the layout module owns no
fill of its own. These tests pin the two halves of that contract: the land band is green and
layered (a job card lighter than its field), and ``machinery.js`` restates no palette.
"""

from __future__ import annotations

import re
from importlib import resources

from tap_plugin.github_core.models.github_workflow import GithubWorkflow
from tap_plugin.github_core.models.workflow_job import WorkflowJob


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    value = hex_colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _colours(model_cls: type) -> dict[str, str]:
    return model_cls.DEFAULT_DISPLAY["tap_viz"]["colors"]


def _is_light_green(hex_colour: str) -> bool:
    r, g, b = _rgb(hex_colour)
    return g >= r and g >= b and min(r, g, b) >= 0xD0


def test_land_band_is_green_and_layered() -> None:
    """req-github-core-machinery-topography-1: field and card are light green; card lighter."""
    field = _colours(GithubWorkflow)
    card = _colours(WorkflowJob)
    assert _is_light_green(field["fill"]), field
    assert _is_light_green(card["fill"]), card
    assert sum(_rgb(card["fill"])) > sum(_rgb(field["fill"]))
    for band in (field, card):
        r, g, b = _rgb(band["border"])
        # A green border, so the state chrome (amber / red) stays distinct.
        assert g > r and g > b, band


def test_module_restates_no_palette() -> None:
    """req-github-core-machinery-topography-2: machinery.js paints only the placeholder's slate."""
    source = (
        resources.files("tap_plugin.github_core")
        .joinpath("static/github_core/js/projections/machinery.js")
        .read_text(encoding="utf-8")
    )
    fills = re.findall(r'"background-color":\s*"(#[0-9A-Fa-f]{6})"', source)
    assert fills == ["#f8fafc"], fills
    for model_cls in (GithubWorkflow, WorkflowJob):
        for hex_colour in _colours(model_cls).values():
            assert hex_colour.lower() not in source.lower(), hex_colour
