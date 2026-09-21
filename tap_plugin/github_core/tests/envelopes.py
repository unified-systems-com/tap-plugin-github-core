"""How a test reads a GRIFT envelope's identity, whichever way the collector named it.

github_core is core's first adopter of assigned identity (Issue# 162 -
tap-plugin-github-core): a node of a type that has declared its ``NATURAL_KEY`` is named by
a batch-local ``ref`` and gets its id from core, while a node of a type that has not — another
plugin's, or the one github_core type whose key is still open — keeps an explicit
``entity_id``. Both shapes appear in the same batch, and an edge endpoint independently takes
either. A test that asserts on WHICH node an envelope is wants the name the batch uses for it,
not the key that name is stored under, so it reads through these.

Deliberately not imported from ``collectors.github_collector.batch``: a test that read the
collector's own accessor could not tell a broken accessor from a correct one.
"""

from __future__ import annotations

from typing import Any


def envelope_key(envelope: dict[str, Any]) -> str | None:
    """A node's or edge's own name in its batch — its ``ref``, or its ``entity_id``."""
    entity = envelope.get("entity") or {}
    value = entity.get("ref", entity.get("entity_id"))
    return None if value is None else str(value)


def edge_from(envelope: dict[str, Any]) -> str | None:
    """The edge's source as its batch names it — ``from_ref`` or ``from_entity_id``."""
    edge = envelope.get("edge") or {}
    value = edge.get("from_ref", edge.get("from_entity_id"))
    return None if value is None else str(value)


def edge_to(envelope: dict[str, Any]) -> str | None:
    """The edge's target as its batch names it — ``to_ref`` or ``to_entity_id``."""
    edge = envelope.get("edge") or {}
    value = edge.get("to_ref", edge.get("to_entity_id"))
    return None if value is None else str(value)
