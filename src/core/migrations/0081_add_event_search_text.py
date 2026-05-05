"""Refs #827 (C-60): Search-Index fuer Events.

Ersetzt das ``data_json__icontains``-Pattern in ``services/search.py``
durch eine explizite ``search_text``-Spalte, die im create/update-Pfad
aus den unverschluesselten Feldern mit Default-Sensitivity zusammen-
gesetzt wird. GIN-trgm-Index macht ``icontains`` schnell. Backfill
laeuft fuer bestehende Events.
"""

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations, models


def _backfill_search_text(apps, schema_editor):
    Event = apps.get_model("core", "Event")
    DocumentTypeField = apps.get_model("core", "DocumentTypeField")

    elevated = {"elevated", "high"}

    # Pro DocumentType einmal die Field-Templates laden, damit wir die
    # Backfill-Schleife nicht n*Felder-Mal an die DB schicken.
    dtf_cache: dict = {}

    def field_meta_for(document_type_id):
        if document_type_id not in dtf_cache:
            dtf_cache[document_type_id] = {
                dtf.field_template.slug: dtf.field_template
                for dtf in DocumentTypeField.objects.select_related("field_template").filter(
                    document_type_id=document_type_id
                )
            }
        return dtf_cache[document_type_id]

    qs = Event.objects.all().only("id", "document_type_id", "data_json", "search_text").iterator(chunk_size=1000)
    batch: list = []
    for event in qs:
        meta = field_meta_for(event.document_type_id)
        parts: list[str] = []
        for slug, value in (event.data_json or {}).items():
            ft = meta.get(slug)
            if ft is None or ft.is_encrypted or ft.sensitivity in elevated:
                continue
            if isinstance(value, dict):
                continue
            if isinstance(value, list):
                parts.extend(str(v) for v in value if v is not None and not isinstance(v, dict))
            elif value is not None and value != "":
                parts.append(str(value))
        text = " ".join(parts)
        if text != event.search_text:
            event.search_text = text
            batch.append(event)
        if len(batch) >= 500:
            Event.objects.bulk_update(batch, ["search_text"])
            batch.clear()
    if batch:
        Event.objects.bulk_update(batch, ["search_text"])


def _no_reverse(apps, schema_editor):
    """Kein Rueckwaerts-Backfill — die alte Spalte data_json bleibt."""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0080_case_client_required"),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddField(
            model_name="event",
            name="search_text",
            field=models.TextField(blank=True, default="", verbose_name="Suchindex"),
        ),
        migrations.RunPython(_backfill_search_text, _no_reverse),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX IF NOT EXISTS core_event_search_text_trgm "
                "ON core_event USING gin (search_text gin_trgm_ops)"
            ),
            reverse_sql="DROP INDEX IF EXISTS core_event_search_text_trgm",
        ),
    ]
