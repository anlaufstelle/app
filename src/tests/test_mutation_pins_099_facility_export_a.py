"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.system.facility_export`` (Batch ``facility_export-a``).

Refs #2174.
Refs #2193.

Quelle: Vollmutations-Lauf auf main@eeca1dcd (frischer Mutantenbaum), Batch
``facility_export-a`` (58 Survivor-Mutanten, ausschliesslich
``x__serialize_workitem`` und ``x__serialize_document_type``). Beide
Funktionen bauen ein flaches Dict aus Modellfeldern; mutmut mutiert dabei
zwei Achsen:

* **Schluessel-Umbenennung** (``"XX<name>XX"``/``"<NAME>"``) — die betroffene
  Angabe fehlt danach unter ihrem erwarteten Schluessel im Export.
* **Wert-Ersetzung durch ``None``** (``_uuid_str(None)``/``_isoformat(None)``)
  bei den optionalen FK-/Datums-Feldern — der Export enthaelt danach eine
  falsche Leerstelle statt des tatsaechlichen Wertes.

Beides faengt ein **Vergleich des gesamten Rueckgabe-Dicts** in einem
Szenario, in dem JEDES betroffene Feld einen gesetzten, unverwechselbaren
Wert traegt (nur gesetzte Werte unterscheiden ``_uuid_str(x)``/
``_isoformat(x)`` von ``_uuid_str(None)``/``_isoformat(None)``) — Muster wie
``test_mutation_pins_094_export_1/2.py``.

Die zwei ``order_by``-Mutanten in ``_serialize_document_type`` (Sortier-
kriterium der ``fields``-Liste gestrichen bzw. der ``pk``-Tiebreak entfernt)
werden **plan-unabhaengig ueber die Sortierkette der internen Query**
gepinnt, nicht ueber tatsaechliche Zeilenreihenfolge (Muster
``test_mutation_pins_098_client_export.py`` — die Heap-/Einfuegereihenfolge-
Annahme bei Gleichstand ohne entscheidendes ORDER-BY-Kriterium haelt laut
dortigem Modul-Docstring seit 0.96 nicht mehr zuverlaessig).

Konventionen wie ``test_mutation_pins_098_client_export.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die ein Substring-Match uebersehen wuerde); Fixtures
kommen aus ``src/tests/conftest.py`` bzw. sind echte Model-Objekte. Jeder Test
nennt seine Mutanten in der Docstring samt dem Wert, der beim Mutanten
abweicht. ``_encryption_key`` liegt modulweit an (Lehre 112) — der
Mutantenbaum hat keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db.models.query import QuerySet
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, FieldTemplate, WorkItem
from core.services.system.facility_export import _serialize_document_type, _serialize_workitem

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# _serialize_workitem — 32 Survivors (mutmut_6..mutmut_37)
# ---------------------------------------------------------------------------


class TestSerializeWorkitemFullDict:
    def test_every_field_round_trips_with_its_own_key_and_value(
        self, facility, client_identified, staff_user, lead_user
    ):
        """Mutanten ``x__serialize_workitem__mutmut_6`` (``client_id`` ->
        ``_uuid_str(None)``), ``_7``/``_8`` (``created_by_id`` ->
        ``"XXcreated_by_idXX"``/``"CREATED_BY_ID"``), ``_9`` (``created_by_id``
        -> ``_uuid_str(None)``), ``_10``/``_11`` (``assigned_to_id`` ->
        ``"XXassigned_to_idXX"``/``"ASSIGNED_TO_ID"``), ``_12``
        (``assigned_to_id`` -> ``_uuid_str(None)``), ``_13``/``_14``
        (``item_type`` -> ``"XXitem_typeXX"``/``"ITEM_TYPE"``), ``_15``/``_16``
        (``status`` -> ``"XXstatusXX"``/``"STATUS"``), ``_17``/``_18``
        (``priority`` -> ``"XXpriorityXX"``/``"PRIORITY"``), ``_19``/``_20``
        (``title`` -> ``"XXtitleXX"``/``"TITLE"``), ``_21``/``_22``
        (``description`` -> ``"XXdescriptionXX"``/``"DESCRIPTION"``),
        ``_23``/``_24`` (``created_at`` -> ``"XXcreated_atXX"``/
        ``"CREATED_AT"``), ``_25``/``_26`` (``updated_at`` ->
        ``"XXupdated_atXX"``/``"UPDATED_AT"``), ``_27``/``_28``
        (``completed_at`` -> ``"XXcompleted_atXX"``/``"COMPLETED_AT"``),
        ``_29`` (``completed_at`` -> ``_isoformat(None)``), ``_30``/``_31``
        (``due_date`` -> ``"XXdue_dateXX"``/``"DUE_DATE"``), ``_32``
        (``due_date`` -> ``_isoformat(None)``), ``_33``/``_34``
        (``remind_at`` -> ``"XXremind_atXX"``/``"REMIND_AT"``), ``_35``
        (``remind_at`` -> ``_isoformat(None)``), ``_36``/``_37``
        (``recurrence`` -> ``"XXrecurrenceXX"``/``"RECURRENCE"``).

        ``client``, ``created_by`` und ``assigned_to`` sind drei
        verschiedene Personen (sonst faellt eine Schluessel-Umbenennung
        zwischen ihnen nicht auf); ``completed_at``/``due_date``/
        ``remind_at`` sind gesetzt statt ``None`` — nur so unterscheidet sich
        der Originalwert ueberhaupt von der ``_uuid_str(None)``-/
        ``_isoformat(None)``-Mutante.

        Original: ein Dict mit allen 16 Schluesseln und den tatsaechlichen
        Feldwerten. Jeder Mutant liefert entweder einen falschen Schluessel
        (die erwartete Angabe fehlt) oder ``None`` statt des gesetzten Wertes
        — beides bricht die Gleichheit.
        """
        completed_at = timezone.now() - timedelta(hours=3)
        due_date = date.today() + timedelta(days=5)
        remind_at = date.today() + timedelta(days=2)

        work_item = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            assigned_to=lead_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.IN_PROGRESS,
            priority=WorkItem.Priority.URGENT,
            title="Pin-Aufgabe facility_export-a",
            description="Beschreibungstext fuer den Vollmutations-Pin.",
            completed_at=completed_at,
            due_date=due_date,
            remind_at=remind_at,
            recurrence=WorkItem.Recurrence.WEEKLY,
        )

        assert _serialize_workitem(work_item) == {
            "id": str(work_item.pk),
            "client_id": str(client_identified.pk),
            "created_by_id": str(staff_user.pk),
            "assigned_to_id": str(lead_user.pk),
            "item_type": WorkItem.ItemType.TASK,
            "status": WorkItem.Status.IN_PROGRESS,
            "priority": WorkItem.Priority.URGENT,
            "title": "Pin-Aufgabe facility_export-a",
            "description": "Beschreibungstext fuer den Vollmutations-Pin.",
            "created_at": work_item.created_at.isoformat(),
            "updated_at": work_item.updated_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "due_date": due_date.isoformat(),
            "remind_at": remind_at.isoformat(),
            "recurrence": WorkItem.Recurrence.WEEKLY,
        }


# ---------------------------------------------------------------------------
# _serialize_document_type — 26 Survivors (mutmut_4/5, _14..._41 gerade+ungerade
# Paare, _53/_54)
# ---------------------------------------------------------------------------


class TestSerializeDocumentTypeFullDict:
    def test_every_configuration_field_round_trips_with_its_own_key_and_value(self, facility):
        """Mutanten ``x__serialize_document_type__mutmut_14``/``_15``
        (``name`` -> ``"XXnameXX"``/``"NAME"``), ``_16``/``_17`` (``slug`` ->
        ``"XXslugXX"``/``"SLUG"``), ``_20``/``_21`` (``sensitivity`` ->
        ``"XXsensitivityXX"``/``"SENSITIVITY"``), ``_22``/``_23``
        (``min_contact_stage`` -> ``"XXmin_contact_stageXX"``/
        ``"MIN_CONTACT_STAGE"``), ``_24``/``_25`` (``system_type`` ->
        ``"XXsystem_typeXX"``/``"SYSTEM_TYPE"``), ``_30``/``_31``
        (``retention_days`` -> ``"XXretention_daysXX"``/``"RETENTION_DAYS"``),
        ``_32``/``_33`` (``icon`` -> ``"XXiconXX"``/``"ICON"``), ``_34``/``_35``
        (``color`` -> ``"XXcolorXX"``/``"COLOR"``), ``_36``/``_37``
        (``description`` -> ``"XXdescriptionXX"``/``"DESCRIPTION"``),
        ``_38``/``_39`` (``is_active`` -> ``"XXis_activeXX"``/``"IS_ACTIVE"``),
        ``_40``/``_41`` (``sort_order`` -> ``"XXsort_orderXX"``/
        ``"SORT_ORDER"``), ``_53``/``_54`` (der innere ``fields``-Eintrag:
        ``sort_order`` -> ``"XXsort_orderXX"``/``"SORT_ORDER"``).

        Jedes betroffene Feld traegt einen vom Modell-Default abweichenden,
        unverwechselbaren Wert (``sensitivity=HIGH`` statt ``NORMAL``,
        ``is_active=False`` statt ``True`` usw.) — eine Schluessel-
        Umbenennung laesst die erwartete Angabe im Dict fehlen, egal welchen
        Wert das Feld traegt.

        Original: ein Dict mit allen 14 Konfigurations-Schluesseln plus einer
        ``fields``-Liste mit dem einen zugeordneten Feld (4 Schluessel).
        """
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Export-Konfigurationstyp",
            sensitivity=DocumentType.Sensitivity.HIGH,
            min_contact_stage="qualified",
            system_type=DocumentType.SystemType.CRISIS,
            tally_enabled=True,
            report_key="counseling",
            retention_days=45,
            icon="stern",
            color="#112233",
            description="Testbeschreibung fuer den Vollmutations-Pin.",
            is_active=False,
            sort_order=9,
        )
        field_template = FieldTemplate.objects.create(
            facility=facility,
            name="Pin-Feldvorlage facility_export-a",
            field_type=FieldTemplate.FieldType.TEXT,
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        field = DocumentTypeField.objects.create(
            document_type=doc_type,
            field_template=field_template,
            sort_order=3,
        )

        assert _serialize_document_type(doc_type) == {
            "id": str(doc_type.pk),
            "name": "Export-Konfigurationstyp",
            "slug": doc_type.slug,
            "entity_type": doc_type.entity_type,
            "sensitivity": DocumentType.Sensitivity.HIGH,
            "min_contact_stage": "qualified",
            "system_type": DocumentType.SystemType.CRISIS,
            "tally_enabled": True,
            "report_key": "counseling",
            "retention_days": 45,
            "icon": "stern",
            "color": "#112233",
            "description": "Testbeschreibung fuer den Vollmutations-Pin.",
            "is_active": False,
            "sort_order": 9,
            "fields": [
                {
                    "id": str(field.pk),
                    "field_template_id": str(field_template.pk),
                    "field_group_id": None,
                    "sort_order": 3,
                }
            ],
        }


class TestSerializeDocumentTypeFieldOrdering:
    def test_fields_query_orders_by_sort_order_then_pk_tiebreak(self, doc_type_contact, monkeypatch):
        """Mutanten ``x__serialize_document_type__mutmut_4``
        (``.order_by("sort_order", "pk")`` -> ``.order_by("pk")``, das
        fachliche Kriterium faellt komplett weg — die ``fields``-Liste
        stuende praktisch nach zufaelliger UUID4-Reihenfolge) und
        ``x__serialize_document_type__mutmut_5`` (-> ``.order_by("sort_order",
        )``, der ``pk``-Tiebreak faellt weg — bei gleichem ``sort_order``
        waere die Reihenfolge undefiniert).

        Plan-unabhaengiger Nachweis direkt auf der Sortierkette der
        internen ``DocumentTypeField``-Query (Muster
        ``test_mutation_pins_098_client_export.py``): ``QuerySet.order_by``
        wird abgehorcht, waehrend ``_serialize_document_type`` laeuft. Das
        umgeht jede Annahme darueber, wie Postgres gleichrangige Zeilen ohne
        entscheidendes Kriterium tatsaechlich zurueckgeben wuerde.

        Original: genau ein Aufruf mit ``("sort_order", "pk")``. Mutant _4:
        ``("pk",)``. Mutant _5: ``("sort_order",)``.
        """
        captured: list[tuple] = []
        original_order_by = QuerySet.order_by

        def _spy(self, *args, **kwargs):
            captured.append(args)
            return original_order_by(self, *args, **kwargs)

        monkeypatch.setattr(QuerySet, "order_by", _spy)

        _serialize_document_type(doc_type_contact)

        assert captured == [("sort_order", "pk")]
