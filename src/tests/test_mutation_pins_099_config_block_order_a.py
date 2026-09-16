"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Batch ``block_order-a``
(Vollmutation-Survivor-Triage) in ``core.services.config.block_order``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-block_order-a.json``
(Triage ``pins-block_order-a/triage-099-block_order-a.json``). Das Modul ist
auf zwei Batches a/b verteilt (andere Funktionen, andere Lane) — diese Datei
deckt AUSSCHLIESSLICH die Batch-``block_order-a``-Funktionen ab (``pin_suffix``
``a``): ``_move_field_into_group`` (17 Survivors), ``block_ordered_dtf_queryset``
(14), ``_move_field_to_top_level`` (9), ``reorder_blocks`` (7), ``move_field``
(5), ``_validate_id_set`` (4), ``_block_sort_key`` (4), ``dtf_block_sort_key``
(4), ``reorder_fields`` (3), ``_next_block_position`` (2).

**Meldungsfamilien (Standardblock/Briefing, exakte statt Teilstring-Vergleiche):**
je betroffener Meldung EIN Pin mit exaktem ``exc.value.args``-Vergleich —
dieser eine Vergleich schlägt gleichermaßen auf den Meldungsverlust-Mutanten
(``ValueError(None)``, Klasse ``test``/Prio 3) WIE auf dessen ``XX``-Marker-/
Klein-/Großschreibungs-Varianten desselben Wortlauts an, weshalb alle vier
Varianten je Familie als ``test`` (nicht ``skip``) geführt werden — verifiziert
per Kill-Lauf unter jeder einzelnen Variante:

* ``x__move_field_into_group__mutmut_{6,8,9,10}`` — erste Meldung ("Zielgruppe
  gehört nicht zu diesem Dokumentationstyp").
* ``x__move_field_into_group__mutmut_{36,37,38,39,40,41,42,43}`` — zweite
  Meldung (ID-Set-Mismatch der Zielgruppe, zweiteilig zusammengesetzt).
  ``_37`` ersetzt den INNEREN String-Arg von ``gettext`` durch ``None`` —
  ``gettext(None)`` wirft ``AttributeError`` (empirisch verifiziert: "'NoneType'
  object has no attribute 'replace'") NOCH VOR dem ``ValueError``-Konstruktor;
  derselbe Pin fängt das über ``pytest.raises(ValueError)``, das die
  ``AttributeError`` NICHT abfängt (Testfehler statt grüner Fang = rot).
* ``x_move_field__mutmut_{7,9,10,11}`` — Meldung bei unbekannter ``dtf_id``.
* ``x__validate_id_set__mutmut_{9,11,12,13}`` — zentrale ID-Set-Meldung
  (direkter Funktionsaufruf, keine DB nötig).

**Äquivalenz-Urteile (23 Mutanten, mit Begründung, kein Pin — Volltext je
Mutant in der Triage-JSON):**

* ``x__move_field_to_top_level__mutmut_45`` (``dtf.field_group_id = None``
  wird zu ``= ""``): ``ForeignKey.get_db_prep_save`` (Django,
  ``django/db/models/fields/related.py``) behandelt ``""`` für Zielfelder mit
  ``empty_strings_allowed = False`` (``UUIDField.empty_strings_allowed``
  ist ``False``) EXPLIZIT wie ``None`` — der beim ``.save()`` tatsächlich
  ausgeführte Schreibpfad speichert in beiden Fällen ``NULL``. (Der separat
  aufgerufene ``get_prep_value("")`` wirft zwar eine ``ValidationError`` —
  das ist ein ANDERER Code-Pfad, der beim normalen Save NICHT durchlaufen
  wird; empirisch am realen ``.save()``-Aufruf verifiziert, nicht nur an der
  isolierten Funktion.)
* ``values_list("id", flat=True)`` -> ``values_list(flat=True)`` (10 Fundstellen
  über ``_move_field_into_group``/``_move_field_to_top_level``/
  ``reorder_blocks``/``reorder_fields``): Django ersetzt ein fehlendes
  ``values_list``-Feld durch das ERSTE konkrete Modellfeld
  (``django/db/models/query.py``: ``elif not fields: fields =
  [self.model._meta.concrete_fields[0].attname]``); ``DocumentTypeField`` UND
  ``FieldGroup`` deklarieren ``id`` als erstes Feld. Empirisch verifiziert:
  ``str(queryset.query)`` ist für beide Varianten BYTE-IDENTISCH.
* Redundante ``document_type=doc_type``-/``field_group__isnull=True``-Filter
  auf den FINALEN ``.update()``-Aufrufen (7 Fundstellen, je nach der
  vorangehenden ``_validate_id_set``-Zeile): die dort verwendete ID kommt
  ausschließlich aus einer bereits mengen-exakt gegen einen korrekt
  gescopten Bestand geprüften Liste (dieselbe synchrone Transaktion, kein
  TOCTOU möglich) — der Filter der finalen Schreib-Query ist beweisbar
  redundant. NICHT verwechseln mit den strukturell ANALOGEN Mutanten an den
  VALIDIERUNGS-Zeilen selbst (``_move_field_into_group__mutmut_21``,
  ``_move_field_to_top_level__mutmut_{29,30}``, ``reorder_blocks__mutmut_{25,26}``,
  ``reorder_fields__mutmut_8``, ``_next_block_position__mutmut_{13,14}``) —
  DORT ist der fehlende Filter die Wurzel der Prüfung selbst und wird
  gepinnt (Prio 1/2).
* ``CaseExpr``/``When``-Konstanten in ``block_ordered_dtf_queryset``, bei
  denen ``then`` fehlt (``_39``, Django-Default ``then=None``) oder
  ``then=2`` statt ``1`` ist (``_41``) UND ``default=0`` dabei UNVERÄNDERT
  bleibt, sowie ``output_field`` entfällt (``_32``/``_35``): deckungsgleich
  mit dem bereits im Repo dokumentierten Präzedenzfall
  ``x__visible_field_configs__mutmut_{53,56,58,60,62,63}``
  (``test_mutation_pins_099_zeitstrom_watch_c.py``, dieselbe
  ``block_is_free``-Konstruktion in ``zeitstrom_watch.py``) — NULL sortiert
  in Postgres ASC immer hinter jedem echten Wert, der Rang zum
  ``default=0``-Zweig bleibt unverändert; ``block_is_free`` wird außerhalb
  der ``order_by``-Kette nirgends gelesen. **NICHT** verwechseln mit
  ``mutmut_34``, wo stattdessen ``default=0`` komplett entfällt (Django-
  Default dafür ist ebenfalls ``None`` — aber jetzt fehlt es beim
  GRUPPEN-Zweig, nicht beim Frei-Zweig): das ist dieselbe Umkehr wie
  ``mutmut_31`` (``default=None`` statt ``0``) und daher ``test``/Prio 1,
  NICHT äquivalent (Korrektur nach Kill-Lauf: eine frühe Diff-Lesung hatte
  ``_34`` fälschlich als "then entfällt" statt "default entfällt"
  eingeordnet).
* ``_block_sort_key``/``dtf_block_sort_key``: ein Feld-/Frei-Indikator, der
  von ``1`` auf ``2`` wechselt, bleibt größer als der Gruppen-Indikator
  ``0`` — dieselbe Ordinalbeziehung. Ein konstanter 4. Tupelwert (``""`` ->
  ``"XXXX"``) unterscheidet zwei Zeilen NIE (beide erhalten denselben
  Konstantwert) — Pythons ``sort()`` ist stabil, die Ausgangsreihenfolge
  bleibt bei vollem Gleichstand erhalten, unabhängig vom konkreten Wert.

Alle Äquivalenz-Urteile sind zusätzlich per Kill-Lauf UNTER der jeweiligen
Mutante mit der FERTIGEN Pin-Datei bestätigt (Bericht Abschnitt 3,
``killmap.tsv``: ``GRUEN-BESTAETIGT-AEQUIVALENT`` statt nur behauptet).

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection
from django.db.models import QuerySet
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from core.models import DocumentType, DocumentTypeField, FieldGroup, FieldTemplate
from core.services.config import (
    _block_sort_key,
    _next_block_position,
    _validate_id_set,
    block_ordered_dtf_queryset,
    dtf_block_sort_key,
    iter_blocks_in_order,
    move_field,
    reorder_blocks,
    reorder_fields,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def req(staff_user, facility):
    """Request mit authentifiziertem User + Facility-Scope (wie FacilityScopeMiddleware)."""
    request = RequestFactory().post("/")
    request.user = staff_user
    request.current_facility = facility
    return request


def _set_clause_column_count(sql: str) -> int:
    """Zaehlt die im SET-Teil eines UPDATE-Statements gesetzten Spalten."""
    set_part = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    return set_part.count(",") + 1


def _capture_order_by(monkeypatch):
    """Zeichnet jeden ``QuerySet.order_by()``-Aufruf als ``(Modell, Argumente)`` auf
    (Muster ``test_mutation_pins_099_facility_export_d.py::_capture_order_by``)."""
    calls: list[tuple[type, tuple]] = []
    original_order_by = QuerySet.order_by

    def _spy(self, *args, **kwargs):
        calls.append((self.model, args))
        return original_order_by(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "order_by", _spy)
    return calls


# ---------------------------------------------------------------------------
# _validate_id_set — Meldung
# ---------------------------------------------------------------------------


class TestValidateIdSetMessage:
    def test_message_is_specific(self):
        """Pin core.services.config.block_order.x__validate_id_set__mutmut_9/_11/_12/_13:
        Meldungsverlust (``ValueError(None)``) bzw. ``XX``-Marker-/Klein-/
        Großschreibungs-Varianten desselben Wortlauts — exakter Vergleich statt
        Teilstring/``match=``."""
        with pytest.raises(ValueError) as excinfo:
            _validate_id_set(["a"], {"b"})
        assert excinfo.value.args == (
            "Die übergebene Reihenfolge stimmt nicht mit dem Bestand überein (fremde, fehlende oder doppelte IDs).",
        )


# ---------------------------------------------------------------------------
# move_field — dtf-Auswahl + Meldung
# ---------------------------------------------------------------------------


class TestMoveFieldSelection:
    def test_uses_the_given_dtf_id_not_the_first_by_sort_order(self, req, doc_type_contact):
        """Pin core.services.config.block_order.x_move_field__mutmut_4: der
        ``pk=dtf_id``-Filter der Feldauswahl entfaellt — ``.first()`` liefert
        dann IMMER das erste Feld nach Default-Sortierung, unabhaengig von der
        angeforderten ``dtf_id``."""
        grp_dst = FieldGroup.objects.create(document_type=doc_type_contact, name="Dst")
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        first_by_sort_order, intended = free[0], free[1]

        move_field(req, doc_type_contact, intended.id, grp_dst.id, ordered_dtf_ids=[intended.id])

        intended.refresh_from_db()
        first_by_sort_order.refresh_from_db()
        assert intended.field_group_id == grp_dst.id
        assert first_by_sort_order.field_group_id is None

    def test_unknown_dtf_message(self, req, doc_type_contact):
        """Pin core.services.config.block_order.x_move_field__mutmut_7/_9/_10/_11:
        Meldungsverlust bzw. Text-Varianten bei unbekannter ``dtf_id`` —
        exakter Vergleich."""
        with pytest.raises(ValueError) as excinfo:
            move_field(req, doc_type_contact, uuid4(), None, ordered_blocks=[])
        assert excinfo.value.args == ("Das zu verschiebende Feld gehört nicht zu diesem Dokumentationstyp.",)


# ---------------------------------------------------------------------------
# _move_field_into_group — Meldungen
# ---------------------------------------------------------------------------


class TestMoveFieldIntoGroupMessages:
    def test_foreign_target_group_message(self, req, doc_type_contact, foreign_field_group):
        """Pin core.services.config.block_order.x__move_field_into_group__mutmut_6/_8/_9/_10:
        Meldungsverlust bzw. Text-Varianten bei fremder Zielgruppe — exakter
        Vergleich."""
        dtf = doc_type_contact.fields.first()
        with pytest.raises(ValueError) as excinfo:
            move_field(req, doc_type_contact, dtf.id, foreign_field_group.id, ordered_dtf_ids=[dtf.id])
        assert excinfo.value.args == ("Die Zielgruppe gehört nicht zu diesem Dokumentationstyp.",)

    def test_id_mismatch_message(self, req, doc_type_contact):
        """Pin core.services.config.block_order.x__move_field_into_group__mutmut_36/_37/_38/_39/_40/_41/_42/_43:
        Meldungsverlust bzw. Text-Varianten bei ID-Set-Mismatch der Zielgruppe
        — exakter Vergleich. ``_37`` (``gettext(None)`` -> ``AttributeError``
        VOR dem ``ValueError``) faengt dasselbe ``pytest.raises(ValueError)``
        NICHT ab und faellt damit ebenfalls rot aus."""
        grp_dst = FieldGroup.objects.create(document_type=doc_type_contact, name="Dst")
        dtf = doc_type_contact.fields.filter(field_group__isnull=True).first()
        with pytest.raises(ValueError) as excinfo:
            move_field(req, doc_type_contact, dtf.id, grp_dst.id, ordered_dtf_ids=[])
        assert excinfo.value.args == (
            "Die übergebene Reihenfolge stimmt nicht mit der Zielgruppe überein (fremde, fehlende oder doppelte IDs).",
        )


# ---------------------------------------------------------------------------
# _move_field_into_group — Validierungsumfang + Mengenlogik + Persistenz
# ---------------------------------------------------------------------------


class TestMoveFieldIntoGroupValidationScope:
    def test_target_member_ids_scoped_to_doc_type(self, req, facility, doc_type_contact):
        """Pin core.services.config.block_order.x__move_field_into_group__mutmut_21:
        die Ist-Mitgliederliste der Zielgruppe (``target_member_ids``) verliert
        ihren ``document_type``-Filter — ein Feld eines FREMDEN
        Dokumentationstyps, dessen ``field_group`` (per Datenfehler/Bypass von
        ``clean()``) dennoch auf die Zielgruppe zeigt, wuerde faelschlich als
        Gruppenmitglied gezaehlt und verfaelscht die Mengenpruefung."""
        grp_dst = FieldGroup.objects.create(document_type=doc_type_contact, name="Dst")
        other_dt = DocumentType.objects.create(facility=facility, name="Anderer Typ (into-group)")
        other_ft = FieldTemplate.objects.create(
            facility=facility, name="Fremdvorlage (into-group)", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(
            document_type=other_dt, field_template=other_ft, field_group_id=grp_dst.id, sort_order=0
        )
        moved = doc_type_contact.fields.filter(field_group__isnull=True).first()

        # Orig: target_member_ids bleibt auf doc_type_contact gescoped, das
        # fremde Feld zaehlt NICHT mit — die Anfrage mit NUR dem bewegten Feld
        # ist vollstaendig und muss gelingen.
        move_field(req, doc_type_contact, moved.id, grp_dst.id, ordered_dtf_ids=[moved.id])

        moved.refresh_from_db()
        assert moved.field_group_id == grp_dst.id

    def test_rejects_same_length_wrong_ids(self, req, facility, doc_type_contact):
        """Pin core.services.config.block_order.x__move_field_into_group__mutmut_32:
        ``or`` wird zu ``and`` — bei GLEICHER Laenge, aber ABWEICHENDEM Inhalt
        (vertauschte ID) greift die Pruefung nicht mehr und schreibt sort_order
        in ein voellig unbeteiligtes Feld."""
        grp_dst = FieldGroup.objects.create(document_type=doc_type_contact, name="Dst")
        ft1 = FieldTemplate.objects.create(facility=facility, name="Mitglied", field_type=FieldTemplate.FieldType.TEXT)
        member1 = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft1, field_group=grp_dst, sort_order=0
        )
        moved, other_free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))[:2]

        with pytest.raises(ValueError):
            # Erwartete Menge waere {member1.pk, moved.pk} — stattdessen wird
            # das unbeteiligte "other_free" statt "moved" eingereicht (gleiche
            # Laenge, falscher Inhalt).
            move_field(req, doc_type_contact, moved.id, grp_dst.id, ordered_dtf_ids=[member1.id, other_free.id])

        other_free.refresh_from_db()
        assert other_free.field_group_id is None
        assert other_free.sort_order == 1  # unveraendert (Fixture-Ausgangswert), kein stiller Fehlgriff


class TestMoveFieldIntoGroupPersistence:
    def test_save_updates_only_field_group_column(self, req, doc_type_contact):
        """Pin core.services.config.block_order.x__move_field_into_group__mutmut_47:
        ``dtf.save(update_fields=["field_group"])`` verliert seine
        ``update_fields``-Einschraenkung (``update_fields=None``) — ein
        Voll-Save schreibt statt der einen Spalte ALLE Spalten und verbreitert
        unnoetig das Race-Fenster."""
        grp_dst = FieldGroup.objects.create(document_type=doc_type_contact, name="Dst")
        moved = doc_type_contact.fields.filter(field_group__isnull=True).first()

        with CaptureQueriesContext(connection) as ctx:
            move_field(req, doc_type_contact, moved.id, grp_dst.id, ordered_dtf_ids=[moved.id])

        field_group_updates = [
            q["sql"]
            for q in ctx.captured_queries
            if q["sql"].startswith('UPDATE "core_documenttypefield"') and "field_group_id" in q["sql"]
        ]
        assert field_group_updates, "kein UPDATE mit field_group_id gefunden"
        assert _set_clause_column_count(field_group_updates[0]) == 1


# ---------------------------------------------------------------------------
# _move_field_to_top_level — Validierungsumfang + Feldleerung + Persistenz
# ---------------------------------------------------------------------------


class TestMoveFieldToTopLevelValidationScope:
    def test_existing_field_ids_scoped_to_doc_type(self, req, facility, doc_type_contact):
        """Pin core.services.config.block_order.x__move_field_to_top_level__mutmut_29:
        die erwartete Menge freier Top-Level-Felder verliert ihren
        ``document_type``-Filter — ein freies Feld eines FREMDEN
        Dokumentationstyps verfaelscht ``existing_field_ids``."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Src")
        moved = doc_type_contact.fields.filter(field_group__isnull=True).first()
        moved.field_group = grp
        moved.save(update_fields=["field_group"])

        other_dt = DocumentType.objects.create(facility=facility, name="Anderer Typ (top-level)")
        other_ft = FieldTemplate.objects.create(
            facility=facility, name="Fremdvorlage (top-level)", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=other_dt, field_template=other_ft, sort_order=0)

        other_free = doc_type_contact.fields.filter(field_group__isnull=True).first()

        # Orig: existing_field_ids bleibt auf doc_type_contact gescoped — die
        # Anfrage mit NUR den eigenen freien Bloecken ist vollstaendig.
        move_field(
            req,
            doc_type_contact,
            moved.id,
            None,
            ordered_blocks=[("group", grp.id), ("field", moved.id), ("field", other_free.id)],
        )

        moved.refresh_from_db()
        assert moved.field_group_id is None

    def test_existing_field_ids_excludes_grouped_fields(self, req, facility, doc_type_contact):
        """Pin core.services.config.block_order.x__move_field_to_top_level__mutmut_30:
        die erwartete Menge freier Top-Level-Felder verliert ihren
        ``field_group__isnull=True``-Filter — ein NOCH gruppiertes Feld zaehlt
        faelschlich als gueltiger freier Block-Eintrag."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Grp", sort_order=2)
        ft_inner = FieldTemplate.objects.create(
            facility=facility, name="Innen (top-level)", field_type=FieldTemplate.FieldType.TEXT
        )
        inner = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft_inner, field_group=grp, sort_order=0
        )
        dauer = doc_type_contact.fields.get(field_template__name="Dauer")
        notiz = doc_type_contact.fields.get(field_template__name="Notiz")

        # dauer wird "zum Top-Level bewegt" (Position bleibt gleich); zusaetzlich
        # zu den beiden ECHTEN freien Feldern (dauer, notiz) wird das NOCH
        # gruppierte "inner" mit eingereicht. Orig: existing_field_ids bleibt
        # {dauer, notiz} (2) -> die 3-elementige Anfrage passt nicht (Laenge)
        # und wirft. Mutant: existing_field_ids wird zu ALLEN Feldern des Typs
        # {dauer, notiz, inner} (3) -> die exakt gleiche 3-elementige Anfrage
        # passt und wuerde still durchgereicht.
        with pytest.raises(ValueError):
            move_field(
                req,
                doc_type_contact,
                dauer.id,
                None,
                ordered_blocks=[
                    ("group", grp.id),
                    ("field", dauer.id),
                    ("field", notiz.id),
                    ("field", inner.id),
                ],
            )

        notiz.refresh_from_db()
        inner.refresh_from_db()
        assert notiz.field_group_id is None
        assert inner.field_group_id == grp.id


class TestMoveFieldToTopLevelPersistence:
    def test_save_updates_only_field_group_column(self, req, doc_type_contact):
        """Pin core.services.config.block_order.x__move_field_to_top_level__mutmut_46:
        ``dtf.save(update_fields=["field_group"])`` verliert seine
        ``update_fields``-Einschraenkung — Voll-Save statt gezieltem
        Partial-Update."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Src")
        moved = doc_type_contact.fields.filter(field_group__isnull=True).first()
        moved.field_group = grp
        moved.save(update_fields=["field_group"])
        other_free = doc_type_contact.fields.filter(field_group__isnull=True).first()

        with CaptureQueriesContext(connection) as ctx:
            move_field(
                req,
                doc_type_contact,
                moved.id,
                None,
                ordered_blocks=[("group", grp.id), ("field", moved.id), ("field", other_free.id)],
            )

        field_group_updates = [
            q["sql"]
            for q in ctx.captured_queries
            if q["sql"].startswith('UPDATE "core_documenttypefield"') and "field_group_id" in q["sql"]
        ]
        assert field_group_updates, "kein UPDATE mit field_group_id gefunden"
        assert _set_clause_column_count(field_group_updates[0]) == 1


# ---------------------------------------------------------------------------
# reorder_blocks — Validierungsumfang
# ---------------------------------------------------------------------------


class TestReorderBlocksValidationScope:
    def test_existing_field_ids_scoped_to_doc_type(self, req, facility, doc_type_contact):
        """Pin core.services.config.block_order.x_reorder_blocks__mutmut_25:
        die erwartete Menge freier Top-Level-Felder verliert ihren
        ``document_type``-Filter — ein freies Feld eines FREMDEN
        Dokumentationstyps verfaelscht ``existing_field_ids``."""
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        other_dt = DocumentType.objects.create(facility=facility, name="Anderer Typ (reorder-blocks)")
        other_ft = FieldTemplate.objects.create(
            facility=facility, name="Fremdvorlage (reorder-blocks)", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=other_dt, field_template=other_ft, sort_order=0)

        reorder_blocks(req, doc_type_contact, [("field", free[1].id), ("field", free[0].id)])

        free[0].refresh_from_db()
        free[1].refresh_from_db()
        assert (free[1].sort_order, free[0].sort_order) == (0, 1)

    def test_existing_field_ids_excludes_grouped_fields(self, req, facility, doc_type_contact):
        """Pin core.services.config.block_order.x_reorder_blocks__mutmut_26:
        die erwartete Menge freier Top-Level-Felder verliert ihren
        ``field_group__isnull=True``-Filter — ein noch gruppiertes Feld zaehlt
        faelschlich als gueltiger freier Block-Eintrag."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Grp", sort_order=2)
        ft_inner = FieldTemplate.objects.create(
            facility=facility, name="Innen (reorder-blocks)", field_type=FieldTemplate.FieldType.TEXT
        )
        inner = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft_inner, field_group=grp, sort_order=0
        )
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))

        with pytest.raises(ValueError):
            reorder_blocks(
                req,
                doc_type_contact,
                [("group", grp.id), ("field", free[0].id), ("field", free[1].id), ("field", inner.id)],
            )

        inner.refresh_from_db()
        assert inner.field_group_id == grp.id


# ---------------------------------------------------------------------------
# reorder_fields — Validierungsumfang
# ---------------------------------------------------------------------------


class TestReorderFieldsValidationScope:
    def test_existing_ids_scoped_to_doc_type(self, req, facility, doc_type_contact):
        """Pin core.services.config.block_order.x_reorder_fields__mutmut_8:
        die erwartete ID-Menge der Gruppe verliert ihren ``document_type``-
        Filter — ein Feld eines FREMDEN Dokumentationstyps mit (per
        Datenfehler/Bypass) derselben ``field_group_id`` verfaelscht
        ``existing_ids``."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="RF")
        ft1 = FieldTemplate.objects.create(facility=facility, name="RF-1", field_type=FieldTemplate.FieldType.TEXT)
        ft2 = FieldTemplate.objects.create(facility=facility, name="RF-2", field_type=FieldTemplate.FieldType.TEXT)
        d1 = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft1, field_group=grp, sort_order=0
        )
        d2 = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft2, field_group=grp, sort_order=1
        )
        other_dt = DocumentType.objects.create(facility=facility, name="Anderer Typ (reorder-fields)")
        other_ft = FieldTemplate.objects.create(
            facility=facility, name="Fremdvorlage (reorder-fields)", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(
            document_type=other_dt, field_template=other_ft, field_group_id=grp.id, sort_order=0
        )

        reorder_fields(req, doc_type_contact, grp.id, [d2.id, d1.id])

        d1.refresh_from_db()
        d2.refresh_from_db()
        assert (d2.sort_order, d1.sort_order) == (0, 1)


# ---------------------------------------------------------------------------
# _next_block_position — Aggregations-Scope
# ---------------------------------------------------------------------------


class TestNextBlockPosition:
    def test_free_max_scoped_to_doc_type(self, facility, doc_type_contact):
        """Pin core.services.config.block_order.x__next_block_position__mutmut_13:
        die MAX-Aggregation ueber freie Blockpositionen verliert ihren
        ``document_type``-Filter — ein freies Feld EINES ANDEREN
        Dokumentationstyps fliesst in die Positionsberechnung ein."""
        other_dt = DocumentType.objects.create(facility=facility, name="Anderer Typ (next-position)")
        other_ft = FieldTemplate.objects.create(
            facility=facility, name="Fremdvorlage (next-position)", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=other_dt, field_template=other_ft, sort_order=99)

        # doc_type_contact: 2 freie Felder (sort_order 0,1), keine Gruppe.
        assert _next_block_position(doc_type_contact) == 2

    def test_free_max_excludes_grouped_fields(self, facility, doc_type_contact):
        """Pin core.services.config.block_order.x__next_block_position__mutmut_14:
        die MAX-Aggregation ueber freie Blockpositionen verliert ihren
        ``field_group__isnull=True``-Filter — die INNERE sort_order eines
        gruppierten Feldes (andere Skala als Blockpositionen) fliesst mit
        ein."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="G", sort_order=0)
        ft = FieldTemplate.objects.create(
            facility=facility, name="Innen (next-position)", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft, field_group=grp, sort_order=42
        )

        # group_max=0, freie Felder (sort_order 0,1) -> naechste Position = 2.
        assert _next_block_position(doc_type_contact) == 2


# ---------------------------------------------------------------------------
# _block_sort_key
# ---------------------------------------------------------------------------


class TestBlockSortKey:
    def test_group_before_field_at_tied_position(self, facility, doc_type_contact):
        """Pin core.services.config.block_order.x__block_sort_key__mutmut_9:
        der Gruppen-Indikator (0) faellt auf den Feld-Indikator-Wert (1) —
        bei Positionsgleichstand entscheidet dann ein alphabetischer
        Namensvergleich statt Gruppe-vor-Feld. Die Namen sind bewusst so
        gewaehlt, dass der Namensvergleich die Reihenfolge kippt ("Dauer" <
        "Tied")."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Tied", sort_order=0)
        free = doc_type_contact.fields.get(field_template__name="Dauer")  # sort_order=0 aus der Fixture.

        blocks = iter_blocks_in_order(doc_type_contact)
        relevant = [
            (b["kind"], b["group"].pk if b["kind"] == "group" else b["field"].pk)
            for b in blocks
            if (b["kind"] == "group" and b["group"].pk == grp.pk) or (b["kind"] == "field" and b["field"].pk == free.pk)
        ]
        assert relevant == [("group", grp.pk), ("field", free.pk)]

    def test_field_pk_tiebreak_when_position_and_name_tie(self):
        """Pin core.services.config.block_order.x__block_sort_key__mutmut_15:
        der pk-Tiebreak fuer Feld-Bloecke faellt auf die Konstante ``str(None)``
        — bei gleicher Position UND gleichem Feldvorlagen-Namen ist die
        Reihenfolge nicht mehr deterministisch. Reiner Funktionsaufruf gegen
        ``_block_sort_key`` mit zwei schlanken Attrappen (zwei ECHTE
        ``FieldTemplate``-Zeilen mit identischem Namen scheitern an
        ``unique_facility_fieldtemplate_name`` — die Sortierlogik selbst
        braucht dafuer keine DB). PKs explizit gesetzt (kein Zufall ueber
        echte UUID4-Werte)."""
        field_template = SimpleNamespace(name="Dup")
        dtf_hi = SimpleNamespace(
            sort_order=5, field_template=field_template, pk=uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        )
        dtf_lo = SimpleNamespace(
            sort_order=5, field_template=field_template, pk=uuid.UUID("00000000-0000-0000-0000-000000000000")
        )
        block_hi = {"kind": "field", "field": dtf_hi}
        block_lo = {"kind": "field", "field": dtf_lo}

        ordered = sorted([block_hi, block_lo], key=_block_sort_key)

        assert [b["field"].pk for b in ordered] == [dtf_lo.pk, dtf_hi.pk]


# ---------------------------------------------------------------------------
# dtf_block_sort_key
# ---------------------------------------------------------------------------


class TestDtfBlockSortKey:
    def test_grouped_before_free_at_tied_position(self, facility, doc_type_contact):
        """Pin core.services.config.block_order.x_dtf_block_sort_key__mutmut_2:
        der Gruppen-Indikator (0) faellt auf den Frei-Indikator-Wert (1) —
        bei Positionsgleichstand zwischen einem gruppierten und einem freien
        Feld entscheidet dann die innere sort_order statt Gruppe-vor-Feld."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Tied", sort_order=0)
        ft = FieldTemplate.objects.create(
            facility=facility, name="Innen (dtf-sort-key)", field_type=FieldTemplate.FieldType.TEXT
        )
        grouped_dtf = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft, field_group=grp, sort_order=9
        )
        free_dtf = doc_type_contact.fields.get(field_template__name="Dauer")  # sort_order=0, gleiche Blockposition.

        ordered = sorted([free_dtf, grouped_dtf], key=dtf_block_sort_key)

        assert [d.pk for d in ordered] == [grouped_dtf.pk, free_dtf.pk]

    def test_grouped_pk_tiebreak(self, facility, doc_type_contact):
        """Pin core.services.config.block_order.x_dtf_block_sort_key__mutmut_3:
        der pk-Tiebreak fuer gruppierte Felder faellt auf die Konstante
        ``str(None)`` — bei gleicher Gruppe UND gleicher innerer sort_order
        ist die Reihenfolge nicht mehr deterministisch."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="G", sort_order=0)
        ft_a = FieldTemplate.objects.create(facility=facility, name="A", field_type=FieldTemplate.FieldType.TEXT)
        ft_b = FieldTemplate.objects.create(facility=facility, name="B", field_type=FieldTemplate.FieldType.TEXT)
        dtf_hi = DocumentTypeField.objects.create(
            id=uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
            document_type=doc_type_contact,
            field_template=ft_a,
            field_group=grp,
            sort_order=0,
        )
        dtf_lo = DocumentTypeField.objects.create(
            id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            document_type=doc_type_contact,
            field_template=ft_b,
            field_group=grp,
            sort_order=0,
        )

        ordered = sorted([dtf_hi, dtf_lo], key=dtf_block_sort_key)

        assert [d.pk for d in ordered] == [dtf_lo.pk, dtf_hi.pk]

    def test_free_pk_tiebreak(self, facility, doc_type_contact):
        """Pin core.services.config.block_order.x_dtf_block_sort_key__mutmut_5:
        der pk-Tiebreak fuer freie Felder faellt auf die Konstante
        ``str(None)`` — bei gleicher sort_order ist die Reihenfolge nicht
        mehr deterministisch."""
        ft_a = FieldTemplate.objects.create(facility=facility, name="C", field_type=FieldTemplate.FieldType.TEXT)
        ft_b = FieldTemplate.objects.create(facility=facility, name="D", field_type=FieldTemplate.FieldType.TEXT)
        dtf_hi = DocumentTypeField.objects.create(
            id=uuid.UUID("ffffffff-ffff-ffff-ffff-fffffffffffe"),
            document_type=doc_type_contact,
            field_template=ft_a,
            sort_order=7,
        )
        dtf_lo = DocumentTypeField.objects.create(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            document_type=doc_type_contact,
            field_template=ft_b,
            sort_order=7,
        )

        ordered = sorted([dtf_hi, dtf_lo], key=dtf_block_sort_key)

        assert [d.pk for d in ordered] == [dtf_lo.pk, dtf_hi.pk]


# ---------------------------------------------------------------------------
# block_ordered_dtf_queryset
# ---------------------------------------------------------------------------


class TestBlockOrderedDtfQuerysetOrderByColumns:
    def test_order_by_keeps_all_four_columns(self, doc_type_contact, monkeypatch):
        """Pin core.services.config.block_order.x_block_ordered_dtf_queryset__mutmut_6/_8:
        ``block_is_free`` bzw. ``pk`` entfallen aus der ``order_by``-Kette.
        Plan-unabhaengiger Nachweis auf der tatsaechlich aufgerufenen
        Sortierkette (Muster ``test_mutation_pins_099_facility_export_d.py::
        _capture_order_by``) statt auf der Ergebnisreihenfolge."""
        calls = _capture_order_by(monkeypatch)

        list(block_ordered_dtf_queryset(doc_type_contact))

        matching = [args for model, args in calls if model is DocumentTypeField]
        assert matching == [("block_pos", "block_is_free", "sort_order", "pk")]


class TestBlockOrderedDtfQuerysetGroupBeforeFreeTiebreak:
    def test_group_field_sorts_before_free_field_at_tied_block_position(self, facility, doc_type_contact):
        """Pin core.services.config.block_order.x_block_ordered_dtf_queryset__mutmut_31/_33/_34/_40/_42:
        ``block_is_free`` verliert seine Gruppe/Frei-Unterscheidung
        (NULL-Zweig, entfallene ``When``-Klausel, invertierte Bedingung bzw.
        gleicher ``default``-Wert wie der ``then``-Zweig) — bei gleicher
        Blockposition sortiert die Gruppe nicht mehr zuverlaessig vor dem
        freien Feld. Die innere sort_order des gruppierten Feldes (9) liegt
        bewusst UEBER der geteilten Blockposition (0), damit ein
        Gleichstand-Fallback auf die naechste Spalte die falsche Reihenfolge
        erzwingt statt zufaellig noch zu passen."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Tied", sort_order=0)
        ft_inner = FieldTemplate.objects.create(
            facility=facility, name="Innen (queryset-tie)", field_type=FieldTemplate.FieldType.TEXT
        )
        inner = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft_inner, field_group=grp, sort_order=9
        )
        free = doc_type_contact.fields.get(field_template__name="Dauer")  # sort_order=0, gleiche Blockposition.

        ids = [dtf.pk for dtf in block_ordered_dtf_queryset(doc_type_contact) if dtf.pk in (inner.pk, free.pk)]

        assert ids == [inner.pk, free.pk]


class TestBlockOrderedDtfQuerysetSelectRelated:
    def test_field_template_and_field_group_need_no_extra_query(self, facility, doc_type_contact):
        """Pin core.services.config.block_order.x_block_ordered_dtf_queryset__mutmut_15/_16:
        ``select_related("field_template", "field_group")`` verliert je eine
        Relation — ``build_field_template_lookup``/``DynamicEventDataForm``
        lesen ``.field_template``/``.field_group`` direkt aus dem
        Queryset-Ergebnis, ohne select_related loest das eine N+1-Query pro
        Zeile aus."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="G")
        ft = FieldTemplate.objects.create(
            facility=facility, name="Innen (select-related)", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft, field_group=grp, sort_order=0
        )

        with CaptureQueriesContext(connection) as ctx:
            rows = list(block_ordered_dtf_queryset(doc_type_contact))
            for dtf in rows:
                _ = dtf.field_template.name
                _ = dtf.field_group.name if dtf.field_group_id else None

        assert len(ctx.captured_queries) == 1
