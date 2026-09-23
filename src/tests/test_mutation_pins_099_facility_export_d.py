"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Batch ``facility_export-d``
(Vollmutation-Survivor-Triage) in ``core.services.system.facility_export``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-facility_export-d.json``
(Triage ``triage-099-facility_export-d.json``). Deckt sechs Funktionen des
Facility-Exports (DSGVO Art. 20) ab: ``_build_configuration`` (Sortierung der
Konfigurationslisten), ``_serialize_event`` (Export-Schema einer Ereigniszeile
inkl. ``document_type_id``), ``_attachment_sha256`` (nur Logging-Mutanten,
siehe unten), ``_serialize_attachment`` (Anhang-Metadaten-Schema +
``content_type``-Fallback), ``_gather_attachments`` (Sortierung + Papierkorb-
Filter) und ``_serialize_field_condition`` (Feldbedingungs-Schema).

**Schema-Key-Tests vergleichen EXAKT per ``set(...) ==``** (nie Teilstring) —
das deckt sowohl den ``XX<key>XX``- als auch den ``<KEY>``-Marker in einem
einzigen Test ab, da eine umbenannte Spalte den erwarteten Schluessel aus der
Menge entfernt UND einen unerwarteten hinzufuegt.

**Sortierungs-Tests** nutzen, wo die verbleibenden ORDER-BY-Schluessel nach
dem Wegfall des mutierten Schluessels von einem echten UUID-Primary-Key
(zufaellig, nicht steuerbar) abhaengen wuerden, den Kunstgriff „PK-Reihenfolge
zuerst aus der DB lesen, dann das verbleibende Sortierfeld INVERS dazu
setzen" (``_pk_order``) — das macht den Rot-Nachweis deterministisch, ohne
auf eine zufaellig passende UUID-Reihenfolge angewiesen zu sein.

**Die fuenf ``pk``-Tiebreak-Mutanten am Ende einer ``order_by``-Kette**
(``x__build_configuration__mutmut_{7,19,31,43}``,
``x__gather_attachments__mutmut_9``) werden NICHT ueber die Ergebnis-
reihenfolge gepinnt, sondern ueber die tatsaechlich aufgerufene Sortierkette
selbst (``_capture_order_by``, Spy auf ``QuerySet.order_by`` — Muster
``test_mutation_pins_098_client_export.py::TestOrderedAttachmentsOrderByClause``).
Bewusste Festlegung (Konsistenz mit Batch ``facility_export-b``): ein
fehlender ``pk``-Tiebreak macht die Export-Reihenfolge bei Gleichstand
unspezifiziert — das ist beobachtbares Verhalten, auch wenn eine konkrete
Postgres-Planner-Wahl den Unterschied im Ergebnis nicht zuverlaessig zeigt.
Der Nachweis ist damit plan-unabhaengig statt auf eine bestimmte
Zeilenreihenfolge angewiesen.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import QuerySet

from core.models import Attachment, DocumentType, FieldCondition, FieldGroup, FieldTemplate
from core.services.file_vault import soft_delete_attachment_chain, store_encrypted_file
from core.services.system import facility_export as fx

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _pk_order(queryset, pks):
    """Tatsaechliche aufsteigende DB-PK-Reihenfolge der gegebenen PKs.

    Liest die Reihenfolge EMPIRISCH aus Postgres statt sie ueber Pythons
    ``uuid.UUID``-Vergleich vorherzusagen — macht Tests robust, die einen
    verbleibenden ``pk``-Tiebreak gezielt gegen ein anderes Sortierfeld
    ausspielen wollen.
    """
    return list(queryset.filter(pk__in=pks).order_by("pk").values_list("pk", flat=True))


def _capture_order_by(monkeypatch):
    """Zeichnet jeden ``QuerySet.order_by()``-Aufruf als ``(Modell, Argumente)`` auf.

    Bewusste Festlegung (Konsistenz mit Batch ``facility_export-b``): ein
    fehlender ``pk``-Tiebreak am Ende einer Sortierkette macht die
    Export-Reihenfolge bei Gleichstand UNSPEZIFIZIERT — das ist beobachtbares
    Verhalten, auch wenn eine konkrete Postgres-Version/Planner-Wahl den
    Unterschied im Ergebnis nicht zuverlässig zeigt (vgl. die bereits im Repo
    dokumentierte Planner-Abhängigkeit, ``test_mutation_pins_098_client_export.py``).
    Der Nachweis sitzt deshalb NICHT auf der Ergebnisreihenfolge, sondern
    plan-unabhängig direkt auf der tatsächlich aufgerufenen Sortierkette an
    der Ladestelle — Muster ``test_mutation_pins_098_client_export.py::
    TestOrderedAttachmentsOrderByClause``, hier als Spy auf
    ``QuerySet.order_by`` nachgebaut, weil ``_build_configuration``/
    ``_gather_attachments`` die Querysets intern bauen UND sofort
    konsumieren (kein Rückgabewert zum direkten Inspizieren).
    """
    calls: list[tuple[type, tuple]] = []
    original_order_by = QuerySet.order_by

    def _spy(self, *args, **kwargs):
        calls.append((self.model, args))
        return original_order_by(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "order_by", _spy)
    return calls


def _make_attachment(facility, owner, user, *, sort_order=None, content_type="application/pdf"):
    ft = FieldTemplate.objects.create(
        facility=facility, name=f"Anhang-{uuid.uuid4()}", field_type=FieldTemplate.FieldType.FILE
    )
    upload = SimpleUploadedFile("export-test.pdf", PDF_MINIMAL, content_type=content_type)
    return store_encrypted_file(facility, upload, ft, owner, user, sort_order=sort_order)


# ---------------------------------------------------------------------------
# _build_configuration — Sortierung
# ---------------------------------------------------------------------------


class TestBuildConfigurationOrdering:
    def test_document_types_sort_order_wins_over_name(self, facility):
        """Pins ``core.services.system.facility_export.x__build_configuration__mutmut_5``:
        ``sort_order`` fiel aus dem ``order_by`` der Dokumentationstypen —
        ``name`` haette dann Vorrang, hier bewusst gegenlaeufig zu
        ``sort_order`` gewaehlt."""
        dt_first = DocumentType.objects.create(facility=facility, name="Zzz-Spaeter", sort_order=1)
        dt_second = DocumentType.objects.create(facility=facility, name="Aaa-Frueher", sort_order=10)

        config = fx._build_configuration(facility)
        ids_in_order = [row["id"] for row in config["document_types"]]

        assert ids_in_order == [str(dt_first.pk), str(dt_second.pk)]

    def test_document_types_name_breaks_sort_order_tie(self, facility):
        """Pins ``x__build_configuration__mutmut_6``: ``name`` fiel aus dem
        ``order_by`` der Dokumentationstypen — bei gleichem ``sort_order``
        entscheidet dann der (zufaellige) PK statt des Namens. Der PK wird
        vorab gelesen und ``name`` bewusst INVERS dazu vergeben, damit der
        Rot-Nachweis nicht vom Zufall abhaengt."""
        dt_a = DocumentType.objects.create(facility=facility, name="Platzhalter-A", sort_order=5)
        dt_b = DocumentType.objects.create(facility=facility, name="Platzhalter-B", sort_order=5)
        first_by_pk, second_by_pk = _pk_order(DocumentType.objects.all(), [dt_a.pk, dt_b.pk])
        # Unter dem Mutanten (sort_order gleich -> pk entscheidet) kaeme
        # first_by_pk zuerst; wir geben ihm den alphabetisch SPAETEREN Namen,
        # damit Orig (name entscheidet) die entgegengesetzte Reihenfolge liefert.
        DocumentType.objects.filter(pk=first_by_pk).update(name="Zzz-Nach-PK-zuerst")
        DocumentType.objects.filter(pk=second_by_pk).update(name="Aaa-Nach-PK-zweiter")

        config = fx._build_configuration(facility)
        ids_in_order = [
            row["id"] for row in config["document_types"] if row["id"] in {str(first_by_pk), str(second_by_pk)}
        ]

        assert ids_in_order == [str(second_by_pk), str(first_by_pk)]

    def test_field_templates_ordered_by_name(self, facility):
        """Pins ``x__build_configuration__mutmut_18``: ``name`` fiel aus dem
        ``order_by`` der Feldvorlagen (``FieldTemplate`` hat kein eigenes
        ``sort_order``-Feld) — ohne ``name`` entscheidet nur noch der PK.
        Gleicher PK-Invertierungs-Kunstgriff wie oben."""
        ft_a = FieldTemplate.objects.create(
            facility=facility, name="Platzhalter-A", field_type=FieldTemplate.FieldType.TEXT
        )
        ft_b = FieldTemplate.objects.create(
            facility=facility, name="Platzhalter-B", field_type=FieldTemplate.FieldType.TEXT
        )
        first_by_pk, second_by_pk = _pk_order(FieldTemplate.objects.all(), [ft_a.pk, ft_b.pk])
        FieldTemplate.objects.filter(pk=first_by_pk).update(name="Zzz-Nach-PK-zuerst")
        FieldTemplate.objects.filter(pk=second_by_pk).update(name="Aaa-Nach-PK-zweiter")

        config = fx._build_configuration(facility)
        ids_in_order = [
            row["id"] for row in config["field_templates"] if row["id"] in {str(first_by_pk), str(second_by_pk)}
        ]

        assert ids_in_order == [str(second_by_pk), str(first_by_pk)]

    def test_field_groups_document_type_scopes_before_sort_order(self, facility):
        """Pins ``x__build_configuration__mutmut_29``: ``document_type_id``
        fiel aus dem ``order_by`` der Feldgruppen — ``sort_order`` haette
        dann Vorrang ueber Dokumentationstyp-Grenzen hinweg. PK-Reihenfolge
        der beiden Dokumentationstypen wird vorab gelesen, ``sort_order``
        bewusst gegenlaeufig vergeben."""
        dt_a = DocumentType.objects.create(facility=facility, name="Typ A")
        dt_b = DocumentType.objects.create(facility=facility, name="Typ B")
        first_dt_by_pk, second_dt_by_pk = _pk_order(DocumentType.objects.all(), [dt_a.pk, dt_b.pk])
        dt_map = {dt_a.pk: dt_a, dt_b.pk: dt_b}
        fg_on_first = FieldGroup.objects.create(
            document_type=dt_map[first_dt_by_pk], name="Gruppe-hoher-sort", sort_order=10
        )
        fg_on_second = FieldGroup.objects.create(
            document_type=dt_map[second_dt_by_pk], name="Gruppe-niedriger-sort", sort_order=1
        )

        config = fx._build_configuration(facility)
        ids_in_order = [row["id"] for row in config["field_groups"]]

        # Orig: document_type_id zuerst -> die Gruppe des PK-kleineren Typs
        # kommt zuerst, UNABHAENGIG vom (hier absichtlich hoeheren) sort_order.
        assert ids_in_order == [str(fg_on_first.pk), str(fg_on_second.pk)]

    def test_field_groups_sort_order_breaks_document_type_tie(self, facility, doc_type_contact):
        """Pins ``x__build_configuration__mutmut_30``: ``sort_order`` fiel aus
        dem ``order_by`` der Feldgruppen — bei gleichem Dokumentationstyp
        entscheidet dann der (zufaellige) PK statt ``sort_order``."""
        fg_a = FieldGroup.objects.create(document_type=doc_type_contact, name="Gruppe-A")
        fg_b = FieldGroup.objects.create(document_type=doc_type_contact, name="Gruppe-B")
        first_by_pk, second_by_pk = _pk_order(FieldGroup.objects.all(), [fg_a.pk, fg_b.pk])
        # Unter dem Mutanten (document_type_id gleich -> pk entscheidet)
        # kaeme first_by_pk zuerst; sort_order wird INVERS dazu vergeben.
        FieldGroup.objects.filter(pk=first_by_pk).update(sort_order=10)
        FieldGroup.objects.filter(pk=second_by_pk).update(sort_order=1)

        config = fx._build_configuration(facility)
        ids_in_order = [
            row["id"] for row in config["field_groups"] if row["id"] in {str(first_by_pk), str(second_by_pk)}
        ]

        assert ids_in_order == [str(second_by_pk), str(first_by_pk)]

    def test_field_conditions_group_index_orders_before_pk(self, facility, doc_type_contact):
        """Pins ``x__build_configuration__mutmut_42``: ``group_index`` fiel
        aus dem ``order_by`` der Feldbedingungen — nur noch der (zufaellige)
        PK haette entschieden. PK-Invertierungs-Kunstgriff wie oben."""
        dauer = doc_type_contact.fields.get(field_template__slug="dauer")
        notiz = doc_type_contact.fields.get(field_template__slug="notiz")
        fc_a = FieldCondition.objects.create(
            target_field=notiz,
            depends_on_field=dauer,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=1,
            group_index=0,
        )
        fc_b = FieldCondition.objects.create(
            target_field=notiz,
            depends_on_field=dauer,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=2,
            group_index=0,
        )
        first_by_pk, second_by_pk = _pk_order(FieldCondition.objects.all(), [fc_a.pk, fc_b.pk])
        FieldCondition.objects.filter(pk=first_by_pk).update(group_index=9)
        FieldCondition.objects.filter(pk=second_by_pk).update(group_index=1)

        config = fx._build_configuration(facility)
        ids_in_order = [
            row["id"] for row in config["field_conditions"] if row["id"] in {str(first_by_pk), str(second_by_pk)}
        ]

        assert ids_in_order == [str(second_by_pk), str(first_by_pk)]


class TestBuildConfigurationOrderByClauses:
    def test_all_four_querysets_keep_the_pk_tiebreak(self, facility, monkeypatch):
        """Pins ``x__build_configuration__mutmut_7``/``_19``/``_31``/``_43``
        (bewusste Festlegung zur Konsistenz mit Batch ``facility_export-b``):
        der ``pk``-Tiebreak am Ende jeder der vier ``order_by``-Ketten
        (Dokumentationstypen, Feldvorlagen, Feldgruppen, Feldbedingungen)
        entfaellt. Ohne ihn ist die Export-Reihenfolge bei Gleichstand auf
        den vorangehenden Kriterien unspezifiziert — beobachtbares Verhalten,
        hier plan-unabhaengig direkt auf der tatsaechlich aufgerufenen
        Sortierkette geprueft statt auf der (planner-abhaengigen)
        Ergebnisreihenfolge (``_capture_order_by``)."""
        calls = _capture_order_by(monkeypatch)

        fx._build_configuration(facility)

        by_model: dict[type, list[tuple]] = {}
        for model, args in calls:
            by_model.setdefault(model, []).append(args)

        assert by_model[DocumentType] == [("sort_order", "name", "pk")]
        assert by_model[FieldTemplate] == [("name", "pk")]
        assert by_model[FieldGroup] == [("document_type_id", "sort_order", "pk")]
        assert by_model[FieldCondition] == [("group_index", "pk")]


# ---------------------------------------------------------------------------
# _serialize_event
# ---------------------------------------------------------------------------


class TestSerializeEvent:
    def test_document_type_id_reflects_the_actual_event(self, facility, sample_event):
        """Pins ``x__serialize_event__mutmut_24``: ``document_type_id`` war
        fest auf ``str(None)`` gesetzt statt ``str(event.document_type_id)``."""
        row = fx._serialize_event(sample_event, facility, with_file_hashes=False)

        assert row["document_type_id"] == str(sample_event.document_type_id)
        assert row["document_type_id"] != "None"

    def test_schema_has_exact_keys_and_values(self, facility, sample_event):
        """Pins ``x__serialize_event__mutmut_25``/``_26`` (``occurred_at``),
        ``_30``/``_31`` (``is_anonymous``), ``_35``/``_36`` (``created_at``),
        ``_37``/``_38`` (``updated_at``): je ein ``XX``-markierter und ein
        grossgeschriebener Schluessel-Mutant. Exakter Mengenvergleich deckt
        jede der acht Varianten in einem Test ab."""
        row = fx._serialize_event(sample_event, facility, with_file_hashes=False)

        assert set(row.keys()) == {
            "id",
            "client_id",
            "case_id",
            "episode_id",
            "document_type_id",
            "occurred_at",
            "data_json",
            "is_anonymous",
            "created_by_id",
            "created_at",
            "updated_at",
            "attachments",
        }
        assert row["occurred_at"] == sample_event.occurred_at.isoformat()
        assert row["is_anonymous"] == sample_event.is_anonymous
        assert row["created_at"] == sample_event.created_at.isoformat()
        assert row["updated_at"] == sample_event.updated_at.isoformat()


# ---------------------------------------------------------------------------
# _serialize_attachment (Logging-Mutanten von _attachment_sha256 siehe unten)
# ---------------------------------------------------------------------------


class TestSerializeAttachment:
    def test_schema_has_exact_keys(self, facility, sample_event, staff_user):
        """Pins ``x__serialize_attachment__mutmut_11``/``_12`` (``content_type``)
        und ``_14``/``_15`` (``created_at``): je ein ``XX``-markierter und ein
        grossgeschriebener Schluessel-Mutant. Exakter Mengenvergleich deckt
        alle vier Varianten in einem Test ab."""
        attachment = _make_attachment(facility, sample_event, staff_user)

        row = fx._serialize_attachment(attachment, with_file_hashes=False)

        assert set(row.keys()) == {"id", "owner", "original_filename", "size", "content_type", "created_at", "sha256"}

    def test_content_type_prefers_detected_mime_over_declared(self, facility, sample_event, staff_user):
        """Pins ``x__serialize_attachment__mutmut_13``: ``detected_mime or
        mime_type`` wurde zu ``detected_mime and mime_type`` mutiert — bei
        wahrem ``detected_mime`` liefert das Original den erkannten Wert,
        der Mutant den (hier absichtlich falschen) browser-deklarierten."""
        attachment = _make_attachment(facility, sample_event, staff_user)
        Attachment.objects.filter(pk=attachment.pk).update(mime_type="text/plain-abweichend")
        attachment.refresh_from_db()
        assert attachment.detected_mime  # Vorbedingung: libmagic hat wirklich etwas erkannt.
        assert attachment.detected_mime != attachment.mime_type

        row = fx._serialize_attachment(attachment, with_file_hashes=False)

        assert row["content_type"] == attachment.detected_mime
        assert row["content_type"] != attachment.mime_type


# ---------------------------------------------------------------------------
# _gather_attachments
# ---------------------------------------------------------------------------


class TestGatherAttachments:
    def test_sort_order_wins_over_created_at(self, facility, sample_event, staff_user):
        """Pins ``x__gather_attachments__mutmut_7``: ``sort_order`` fiel aus
        dem ``order_by`` — ``created_at`` haette dann Vorrang. A wird zuerst
        (frueheres ``created_at``) mit hohem ``sort_order`` angelegt, B
        danach mit niedrigem ``sort_order``."""
        att_a = _make_attachment(facility, sample_event, staff_user, sort_order=10)
        att_b = _make_attachment(facility, sample_event, staff_user, sort_order=1)

        rows = fx._gather_attachments(facility, sample_event, with_file_hashes=False)
        ids_in_order = [row["id"] for row in rows]

        assert ids_in_order == [str(att_b.pk), str(att_a.pk)]

    def test_created_at_breaks_sort_order_tie(self, facility, sample_event, staff_user):
        """Pins ``x__gather_attachments__mutmut_8``: ``created_at`` fiel aus
        dem ``order_by`` — bei gleichem ``sort_order`` entscheidet dann der
        (zufaellige) PK statt ``created_at``. PK-Reihenfolge wird vorab
        gelesen, ``created_at`` bewusst INVERS dazu gesetzt (Attachment nutzt
        ``auto_now_add`` — ein direktes ``update()`` umgeht das)."""
        att_a = _make_attachment(facility, sample_event, staff_user, sort_order=3)
        att_b = _make_attachment(facility, sample_event, staff_user, sort_order=3)
        first_by_pk, second_by_pk = _pk_order(Attachment.objects.all(), [att_a.pk, att_b.pk])
        # Unter dem Mutanten (sort_order gleich -> pk entscheidet) kaeme
        # first_by_pk zuerst; wir geben ihm das SPAETERE created_at, damit
        # Orig (created_at entscheidet) die entgegengesetzte Reihenfolge liefert.
        base = att_a.created_at
        Attachment.objects.filter(pk=first_by_pk).update(created_at=base + timedelta(hours=1))
        Attachment.objects.filter(pk=second_by_pk).update(created_at=base)

        rows = fx._gather_attachments(facility, sample_event, with_file_hashes=False)
        ids_in_order = [row["id"] for row in rows if row["id"] in {str(first_by_pk), str(second_by_pk)}]

        assert ids_in_order == [str(second_by_pk), str(first_by_pk)]

    def test_excludes_soft_deleted_attachment(self, facility, sample_event, staff_user):
        """Pins ``x__gather_attachments__mutmut_14`` (Prio 1): der Filter
        ``deleted_at__isnull=True`` fiel weg — ein per UI soft-geloeschter
        Anhang bliebe im Art.-20-Export sichtbar. Widerspricht dem
        Modul-Docstring: ``Der Papierkorb ist nicht Teil des Exports``."""
        kept = _make_attachment(facility, sample_event, staff_user, sort_order=0)
        deleted = _make_attachment(facility, sample_event, staff_user, sort_order=1)
        removed = soft_delete_attachment_chain(sample_event, deleted.entry_id, staff_user, audit=False)
        assert removed == 1

        rows = fx._gather_attachments(facility, sample_event, with_file_hashes=False)
        ids = {row["id"] for row in rows}

        assert ids == {str(kept.pk)}
        assert str(deleted.pk) not in ids

    def test_order_by_keeps_the_pk_tiebreak(self, facility, sample_event, monkeypatch):
        """Pins ``x__gather_attachments__mutmut_9`` (bewusste Festlegung zur
        Konsistenz mit Batch ``facility_export-b``): der ``pk``-Tiebreak am
        Ende von ``order_by("sort_order","created_at","pk")`` entfaellt.
        Plan-unabhaengiger Nachweis auf der tatsaechlich aufgerufenen
        Sortierkette (``_capture_order_by``) statt auf der Ergebnisreihenfolge."""
        calls = _capture_order_by(monkeypatch)

        fx._gather_attachments(facility, sample_event, with_file_hashes=False)

        attachment_calls = [args for model, args in calls if model is Attachment]

        assert attachment_calls == [("sort_order", "created_at", "pk")]


# ---------------------------------------------------------------------------
# _serialize_field_condition
# ---------------------------------------------------------------------------


class TestSerializeFieldCondition:
    def test_schema_has_exact_keys_and_values(self, facility, field_condition):
        """Pins ``x__serialize_field_condition__mutmut_12``/``_13`` (``value``)
        und ``_14``/``_15`` (``group_index``): je ein ``XX``-markierter und
        ein grossgeschriebener Schluessel-Mutant. Exakter Mengenvergleich
        deckt alle vier Varianten in einem Test ab."""
        row = fx._serialize_field_condition(field_condition)

        assert set(row.keys()) == {
            "id",
            "target_field_id",
            "depends_on_field_id",
            "operator",
            "value",
            "group_index",
        }
        assert row["value"] == field_condition.value
        assert row["group_index"] == field_condition.group_index
