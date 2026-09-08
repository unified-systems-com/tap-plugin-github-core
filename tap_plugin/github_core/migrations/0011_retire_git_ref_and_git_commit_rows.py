"""Retire github_core's own `git_ref` / `git_commit` rows — the Git vocabulary moved to git_core.

github-core#76 / #78: refs and commits are now `git_core__git_ref` / `git_core__git_commit`, minted
by `tap_plugin.git_core.identity` under NEW ids (the natural key changed: repository identity +
path, algorithm + oid). The old rows cannot be retyped in place (ruling 0.4: fresh-grid
re-collect), so this deletes them — every `HAS_REF` / `POINTS_AT` edge with its spine row, every
`PROTECTS` / `SCOPED_TO` / `EVALUATED_ON_REF` / `TARGETS_REF` edge whose target was an old ref
(its target is gone; the next collection re-derives it against the neutral ref), then the two
types' entities. Hosting records, rulesets, caches, releases and everything else stay. The next
migration drops the models; the next collection repopulates the neutral types.

Direct ORM access is the sanctioned path in migrations.
"""

from typing import Any

from django.db import migrations

_RETIRED_TYPES = ("github_core__git_ref", "github_core__git_commit")
_RETIRED_EDGE_TYPES = ("HAS_REF__github_core", "POINTS_AT__github_core")


def delete_retired_rows(apps: Any, schema_editor: Any) -> None:
    Edge = apps.get_model("tap_grid", "Edge")
    Entity = apps.get_model("tap_grid", "Entity")
    retired_entities = Entity.objects.filter(entity_type__in=_RETIRED_TYPES)
    retired_ids = list(retired_entities.values_list("pk", flat=True))
    edges = (
        Edge.objects.filter(edge_type__in=_RETIRED_EDGE_TYPES)
        | Edge.objects.filter(to_entity_id__in=retired_ids)
        | Edge.objects.filter(from_entity_id__in=retired_ids)
    )
    edge_entity_ids = list(edges.values_list("entity_id", flat=True))
    edges.delete()
    Entity.objects.filter(pk__in=edge_entity_ids).delete()
    for model_name in ("GitRef", "GitCommit"):
        apps.get_model("github_core", model_name).objects.all().delete()
    retired_entities.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("github_core", "0010_outputs_releases_packages"),
        ("tap_grid", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(delete_retired_rows, migrations.RunPython.noop),
    ]
