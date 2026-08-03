"""DocumentType erhaelt einen stabilen ``slug`` + ``entity_type`` (M2, Refs #1058).

``DocumentType.slug`` wird der Lookup-Key des kommenden YAML-Package-Loaders —
genau wie ``FieldTemplate.slug``/``FieldGroup.slug`` (Migration 0019/0110) ist
er nach dem Anlegen unveraenderbar. ``entity_type`` markiert, auf welche
Entitaet (Ereignis/Person/Fall) Dokumente dieses Typs sich zukuenftig beziehen
koennen — Default ``event`` spiegelt den heutigen (event-only) Bestand exakt.

Reihenfolge in dieser EINEN atomaren Datei:

1. ``AddField(slug, default="")`` — Platzhalter, wird sofort backfuellt.
2. ``AddField(entity_type, default="event")``.
3. ``RunPython(backfill)`` — deterministischer Slug je Zeile (Umlaut-Map +
   slugify, ``_`` → ``-`` und Mehrfach-Bindestriche kollabiert, Truncation auf
   120 Zeichen VOR dem Counter-Suffix ohne Rand-Bindestrich, Leerslug-
   Fallback ``"typ"``, Counter **pro Facility**), gefolgt von einem
   fail-loud Assert (kein leerer Slug, keine Duplikate je Facility) —
   *bevor* der naechste Schritt die DB-Constraint scharf schaltet.
4. ``AddConstraint`` — ``UniqueConstraint(facility, slug)``. Ohne den
   vorherigen Assert wuerde ein Backfill-Bug hier erst als kryptischer
   ``IntegrityError`` auffallen statt mit einer lesbaren Meldung.
5. ``RunSQL`` — Slug-Immutable-Trigger (Muster ``0116_slug_immutable_
   triggers.py``: ``BEFORE UPDATE`` + ``ENABLE ALWAYS``, damit auch
   Wartungspfade/Replica-Rollen den Slug nicht per UPDATE aendern koennen).
   Bewusst NACH dem Backfill-``RunPython``/``AddConstraint`` platziert — der
   Backfill selbst schreibt per ``bulk_update(["slug"])``, was der Trigger
   sonst blocken wuerde.

Der Backfill kopiert die Umlaut-Map/slugify-Logik inline statt
``core.models.document_type`` zu importieren — historische Migrations-Modelle
(``apps.get_model``) haben keine Methoden, und ein Import des aktuellen Models
waere gegen zukuenftige Modelaenderungen nicht stabil (Standard-Django-Migrations-
Praxis).
"""

import re

from django.db import migrations, models
from django.utils.text import slugify

_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_MAX_LEN = 120


def _base_slug(name: str) -> str:
    """Slugify + Umlaut-Map + Normalisierung + Truncation, Leerslug-Fallback ``"typ"``.

    Normalisierung identisch zu ``core.models.document_type.slug_base``
    (Refs #1058): ``slugify`` laesst Unterstriche durch und die Truncation kann
    auf einem Bindestrich enden — beides verletzt das ``_SLUG_RE``-Format, das
    der Paket-Loader erwartet. Bewusst inline gehalten (Migrationen importieren
    keine aktuellen Modelle), aber wertgleich.
    """
    base = slugify((name or "").lower().translate(_UMLAUT_MAP)).replace("_", "-")
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base[:_MAX_LEN].strip("-") or "typ"


def _next_free_slug(base: str, used: set) -> str:
    """Naechster freie Slug in ``used`` — Counter-Suffix, ohne 120 Zeichen zu sprengen."""
    if base not in used:
        return base
    counter = 2
    while True:
        suffix = f"-{counter}"
        candidate = f"{base[: _MAX_LEN - len(suffix)].strip('-')}{suffix}"
        if candidate not in used:
            return candidate
        counter += 1


def backfill_document_type_slugs(apps, schema_editor):
    DocumentType = apps.get_model("core", "DocumentType")

    used_by_facility: dict = {}
    updates = []
    # Deterministische Reihenfolge (Refs #1058, E16): gleicher Basis-Slug
    # innerhalb einer Facility bekommt Counter-Suffixe in stabiler
    # Reihenfolge — sonst waere das Backfill-Ergebnis von der physischen
    # Zeilenreihenfolge in der DB abhaengig.
    qs = DocumentType.objects.all().order_by("facility_id", "name", "category", "id")
    for dt in qs:
        used = used_by_facility.setdefault(dt.facility_id, set())
        slug = _next_free_slug(_base_slug(dt.name), used)
        used.add(slug)
        if dt.slug != slug:
            dt.slug = slug
            updates.append(dt)
    if updates:
        DocumentType.objects.bulk_update(updates, ["slug"], batch_size=500)

    # Fail-loud VOR AddConstraint (Refs #1058): ein Backfill-Bug soll hier
    # lesbar auffallen statt als kryptischer IntegrityError beim naechsten
    # Migrationsschritt.
    rows = list(DocumentType.objects.values_list("facility_id", "slug"))
    assert all(slug for _facility_id, slug in rows), "Backfill hat einen leeren DocumentType.slug erzeugt."
    assert len(rows) == len(set(rows)), "Backfill hat doppelte (facility, slug)-Paare erzeugt."


class Migration(migrations.Migration):
    dependencies = [("core", "0116_slug_immutable_triggers")]

    operations = [
        migrations.AddField(
            model_name="documenttype",
            name="slug",
            field=models.SlugField(
                blank=True,
                default="",
                help_text=(
                    "Stabiler Identifier — wird als Lookup-Key im M2-Paketformat verwendet. "
                    "Nach Erstellung unveränderbar."
                ),
                max_length=120,
                verbose_name="Slug",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="documenttype",
            name="entity_type",
            field=models.CharField(
                choices=[("event", "Ereignis"), ("client", "Person"), ("case", "Fall")],
                default="event",
                help_text=(
                    "Auf welche Entität sich Dokumente dieses Typs beziehen. Nach Erstellung unveränderbar."
                ),
                max_length=20,
                verbose_name="Entitätstyp",
            ),
        ),
        migrations.RunPython(backfill_document_type_slugs, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="documenttype",
            constraint=models.UniqueConstraint(fields=("facility", "slug"), name="unique_facility_documenttype_slug"),
        ),
        migrations.RunSQL(
            sql="""
                CREATE OR REPLACE FUNCTION prevent_documenttype_slug_mutation()
                RETURNS TRIGGER AS $$
                BEGIN
                    IF NEW.slug IS DISTINCT FROM OLD.slug THEN
                        RAISE EXCEPTION 'DocumentType slug is immutable (M2 package format identifier).';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER documenttype_slug_immutable
                BEFORE UPDATE ON core_documenttype
                FOR EACH ROW EXECUTE FUNCTION prevent_documenttype_slug_mutation();

                ALTER TABLE core_documenttype ENABLE ALWAYS TRIGGER documenttype_slug_immutable;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS documenttype_slug_immutable ON core_documenttype;
                DROP FUNCTION IF EXISTS prevent_documenttype_slug_mutation();
            """,
        ),
    ]
