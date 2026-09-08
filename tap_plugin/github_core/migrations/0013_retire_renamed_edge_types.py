"""Delete edges of the ten edge types renamed in the github-core#79 wave.

Edge ids derive from the slug (`identity.edge_id`), so a renamed type is a NEW edge on the next
collection and the old rows would linger as unregistered types nothing reads. This removes them
(with their spine rows) so a grid upgraded in place is not left carrying two generations of the
same relation; the next collection repopulates the new types. Nodes are untouched.

Direct ORM access is the sanctioned path in migrations.
"""

from typing import Any

from django.db import migrations

_RETIRED_EDGE_TYPES = (
    "ENABLED_ON__github_core",
    "EXECUTED_ON__github_core",
    "FEDERATES_VIA__github_core",
    "HAS_ACTIONS_JOB__github_core",
    "HAS_ENVIRONMENT__github_core",
    "PROTECTS__github_core",
    "HAS_CACHE__github_core",
    "HAS_INSTALLATION__github_core",
    "INSTALLED_ON__github_core",
    "SCOPED_TO__github_core",
)


def delete_retired_edges(apps: Any, schema_editor: Any) -> None:
    Edge = apps.get_model("tap_grid", "Edge")
    Entity = apps.get_model("tap_grid", "Entity")
    edges = Edge.objects.filter(edge_type__in=_RETIRED_EDGE_TYPES)
    entity_ids = list(edges.values_list("entity_id", flat=True))
    edges.delete()
    Entity.objects.filter(pk__in=entity_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("github_core", "0012_consume_git_core"),
        ("tap_grid", "0001_initial"),
    ]

    operations = [migrations.RunPython(delete_retired_edges, migrations.RunPython.noop)]
