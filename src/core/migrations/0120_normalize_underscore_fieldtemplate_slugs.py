"""Normalisiert Unterstrich-Slugs von FieldTemplates auf das Paketformat (#1730-5, Refs #1058, #1726).

Bis M2 konnte ``FieldTemplate.slug`` Unterstriche tragen (der alte Auto-Slug
lief nur über ``slugify``, das ``_`` durchlässt). Das M2-Paketformat referenziert
Feldvorlagen aber über ``_SLUG_RE`` = ``[a-z0-9]+(?:-[a-z0-9]+)*`` — ein
Unterstrich-Slug ist dort **nicht** referenzierbar. Diese idempotente
Einmal-Migration normalisiert Bestands-Slugs (``contact_stage`` →
``contact-stage``) und schreibt die betroffenen ``data_json``-Keys mit.

Ablauf (RunPython, ``reverse_code`` = noop — eine Rück-Normalisierung wäre nicht
eindeutig und würde das Paketformat wieder verletzen):

1. **Reserved-Slug-Inventar (#1726, Datenprüfung):** ein ``print``-Report aller
   Bestands-Slugs, die in der Reserved-Liste (feste Formularfeld-Namen) stehen —
   **nur Report, kein Rename**. Umbenennen könnte einen Wert-Verweis brechen; die
   Auflösung ist eine bewusste Betreiber-Entscheidung.
2. **Unterstrich-Normalisierung:** Slugs mit ``_`` werden per Inline-Kopie der
   ``slug_base``-Logik (``_`` → ``-``, Mehrfach-Bindestriche kollabieren,
   Truncation, Rand-Bindestriche strippen — Muster Migration ``0117``; historische
   Migrations-States tragen keine Modell-Methoden) normalisiert. **Kollisionen je
   Facility** (der Wunsch-Slug ist bereits belegt ODER zwei Kandidaten wollen
   denselben) werden **nur inventarisiert** ``(facility_id, alter_slug,
   gewünschter_slug)`` (Muster ``0037``/``0080``), nicht umbenannt.
3. **Trigger-Klammer:** ``fieldtemplate_slug_immutable`` ist ``ENABLE ALWAYS``
   (Migration ``0116``) und blockiert auch die Admin-/Migrations-Rolle. Wie in
   ``0074_redact_legacy_eventhistory_delete`` wird er transaktional deaktiviert,
   der ``bulk_update`` ausgeführt und im ``finally`` wieder **exakt** als
   ``ENABLE ALWAYS`` hergestellt (Vendor-Check für den SQLite-Fallback ohne
   Trigger).
4. **Key-Rewrite:** Für jede angewandte Umbenennung werden die ``data_json``-Keys
   von **Event**, **Client**, **Case** und die ``prefilled_data``-Keys von
   **QuickTemplate** je Facility umgeschrieben. Werte werden **unverändert**
   übernommen — Fernet-Tokens sind key-unabhängig (``encrypt_field``/
   ``decrypt_field`` nutzen kein AAD), ein Key-Wechsel entwertet sie also nicht.

Bewusst **nicht** umgeschrieben: **EventHistory-Snapshots**
(``data_before``/``data_after``). Das Audit-Log ist append-only (Trigger,
Migration ``0012``/``0107``); ein nachträglicher Key-Rewrite würde seine
Integrität verletzen. Die History-Anzeige löst unbekannte Slugs bereits heute
defensiv auf, sodass alte Snapshots mit dem Alt-Slug lesbar bleiben.

Idempotent: ein zweiter Lauf findet keine Unterstrich-Kandidaten mehr (0
Änderungen); der Inventar-Report ist reine Ausgabe ohne Datenwirkung.
"""

import re
from collections import defaultdict

from django.db import migrations
from django.utils.text import slugify

from core.utils.migration_guards import assert_migration_role_bypasses_rls

_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_MAX_LEN = 120

# Inline-Spiegel von ``core.models.document_type._RESERVED_SLUGS`` (Stand
# #1726/#1058). Migrationen bleiben bewusst frozen-in-time und hängen nicht an
# App-Code (ein Refactor der Konstante dürfte diese historische Migration nicht
# brechen). Nur für den Report; keine Rename-Wirkung.
_RESERVED_SLUGS = frozenset(
    {
        "pseudonym",
        "contact_stage",
        "age_cluster",
        "notes",
        "document_type",
        "client",
        "title",
        "description",
        "lead_user",
        "occurred_at",
        "case",
        "csrfmiddlewaretoken",
        "expected_updated_at",
        "_save_and_new",
    }
)


def _normalize(slug: str) -> str:
    """Inline-Kopie der ``slug_base``-Normalisierung (Muster ``0117``)."""
    base = slugify((slug or "").lower().translate(_UMLAUT_MAP)).replace("_", "-")
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base[:_MAX_LEN].strip("-")


def _rewrite_json_keys(model, json_field: str, renames_by_facility: dict) -> None:
    """Schreibt ``json_field``-Keys je Facility gemäß ``renames_by_facility`` um."""
    updates = []
    for obj in model.objects.filter(facility_id__in=list(renames_by_facility)).iterator():
        renames = renames_by_facility.get(obj.facility_id)
        data = getattr(obj, json_field)
        if not renames or not isinstance(data, dict):
            continue
        changed = False
        for old_slug, new_slug in renames.items():
            if old_slug in data:
                data[new_slug] = data.pop(old_slug)
                changed = True
        if changed:
            setattr(obj, json_field, data)
            updates.append(obj)
    if updates:
        model.objects.bulk_update(updates, [json_field], batch_size=500)


def normalize_underscore_fieldtemplate_slugs(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    FieldTemplate = apps.get_model("core", "FieldTemplate")
    Event = apps.get_model("core", "Event")
    Client = apps.get_model("core", "Client")
    Case = apps.get_model("core", "Case")
    QuickTemplate = apps.get_model("core", "QuickTemplate")

    # (1) Reserved-Slug-Inventar (#1726) — nur Report, kein Rename.
    for facility_id, slug in (
        FieldTemplate.objects.filter(slug__in=sorted(_RESERVED_SLUGS))
        .order_by("facility_id", "slug")
        .values_list("facility_id", "slug")
    ):
        print(
            f"  [0120] Reservierter Bestands-Slug (Datenprüfung #1726, nur Report): "
            f"facility={facility_id}, slug={slug!r}."
        )

    # (2) Unterstrich-Kandidaten je Facility sammeln + Rename-Plan bilden.
    candidates_by_facility: dict = defaultdict(list)
    for ft in FieldTemplate.objects.filter(slug__contains="_").order_by("facility_id", "slug", "id"):
        candidates_by_facility[ft.facility_id].append(ft)

    to_update = []
    renames_by_facility: dict = {}
    for facility_id, candidates in candidates_by_facility.items():
        existing = set(FieldTemplate.objects.filter(facility_id=facility_id).values_list("slug", flat=True))
        # Wunsch-Slugs vorab zählen — zwei Kandidaten auf denselben Ziel-Slug
        # kollidieren miteinander (keiner gewinnt).
        desired_counts: dict = defaultdict(int)
        desired: dict = {}
        for ft in candidates:
            new_slug = _normalize(ft.slug)
            desired[ft.pk] = new_slug
            desired_counts[new_slug] += 1

        renames: dict = {}
        for ft in candidates:
            old_slug = ft.slug
            new_slug = desired[ft.pk]
            if not new_slug or new_slug == old_slug:
                print(
                    f"  [0120] Nicht normalisierbar, kein Rename: facility={facility_id}, "
                    f"alter_slug={old_slug!r}, gewünschter_slug={new_slug!r}."
                )
                continue
            if new_slug in existing or desired_counts[new_slug] > 1:
                print(
                    f"  [0120] Kollision, kein Rename: facility={facility_id}, "
                    f"alter_slug={old_slug!r}, gewünschter_slug={new_slug!r}."
                )
                continue
            ft.slug = new_slug
            to_update.append(ft)
            renames[old_slug] = new_slug
        if renames:
            renames_by_facility[facility_id] = renames

    # (3) Slug-Umbenennung — Immutabilitäts-Trigger transaktional klammern.
    if to_update:
        connection = schema_editor.connection
        if connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute("ALTER TABLE core_fieldtemplate DISABLE TRIGGER fieldtemplate_slug_immutable")
                try:
                    FieldTemplate.objects.bulk_update(to_update, ["slug"], batch_size=500)
                finally:
                    # Exakt ENABLE ALWAYS wiederherstellen (Ausgangszustand aus 0116).
                    cur.execute("ALTER TABLE core_fieldtemplate ENABLE ALWAYS TRIGGER fieldtemplate_slug_immutable")
        else:
            # SQLite/Tests ohne Trigger — direkt updaten.
            FieldTemplate.objects.bulk_update(to_update, ["slug"], batch_size=500)

    # (4) data_json / prefilled_data Key-Rewrite je Facility (Werte unverändert).
    if renames_by_facility:
        _rewrite_json_keys(Event, "data_json", renames_by_facility)
        _rewrite_json_keys(Client, "data_json", renames_by_facility)
        _rewrite_json_keys(Case, "data_json", renames_by_facility)
        _rewrite_json_keys(QuickTemplate, "prefilled_data", renames_by_facility)


class Migration(migrations.Migration):
    dependencies = [("core", "0119_case_dynamic_data")]

    operations = [
        migrations.RunPython(normalize_underscore_fieldtemplate_slugs, migrations.RunPython.noop),
    ]
