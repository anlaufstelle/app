"""Follow-Up-Tests für Mutation-Survivors in
``core.services.audit.diff`` (Refs #1388).

``changed_fields_vs_db`` bildet den Feld-Diff gegen den DB-Stand für die
Audit-Erfassung (Refs #1613, Threat-Model TB6 Repudiation): ohne korrekten
Diff bleibt ``changed_fields`` leer und ``*_UPDATE`` wird nie geschrieben. Der
Voll-Lauf 2026-07-16 liess zwei echte Verhaltens-Survivors stehen: den
„Zeile (noch) nicht in der DB → alle Felder gelten als geändert"-Pfad und die
FK-Reduktion auf ``value.pk`` nur für echte Modell-Objekte (roher PK aus dem
Offline-Replay bleibt, wie er ist).

Jeder Test benennt die konkret gekillte Mutation.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import Event
from core.services.audit.diff import changed_fields_vs_db


@pytest.mark.django_db
class TestChangedFieldsVsDb:
    def test_missing_row_marks_all_fields_changed(self, facility, doc_type_contact):
        """Killt ``changed_fields_vs_db__mutmut_13``:
        ``return list(fields.keys())`` → ``return list(None)``.

        Ist die Zeile (noch) nicht in der DB — neuer Datensatz oder
        Offline-Replay vor dem Insert — gelten ALLE übergebenen Felder als
        geändert. Der Mutant ``list(None)`` würde hier mit ``TypeError``
        abbrechen; kein Bestandstest deckte den ``row is None``-Zweig ab.
        """
        # Unsaved-Instanz: pk (UUID) ist gesetzt, aber keine DB-Zeile vorhanden.
        ghost = Event(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
        )
        changed = changed_fields_vs_db(ghost, {"is_deleted": True, "data_json": {"x": 1}}, lock=False)
        assert sorted(changed) == ["data_json", "is_deleted"]

    def test_raw_fk_pk_is_compared_without_dereferencing(self, facility, doc_type_contact, client_identified):
        """Killt ``changed_fields_vs_db__mutmut_17``: die FK-Reduktion
        ``value.pk if is_relation and value is not None and hasattr(value, "pk")
        else value`` → ``… and value is not None or hasattr(value, "pk") …``.

        Ein FK-Wert, der als ROHER PK (UUID/int, Offline-Replay) statt als
        Modell-Objekt kommt, hat kein ``.pk`` — er muss unverändert
        durchgereicht werden. Die ``and→or``-Mutation zwingt den ``value.pk``-
        Zweig auch für den rohen PK und bricht mit ``AttributeError`` ab.
        Aufbau: ``client`` steht in der DB auf ``client_identified``; wir
        vergleichen gegen dessen ROHEN PK → kein Diff, kein Dereferenzieren.
        """
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
        )
        changed = changed_fields_vs_db(event, {"client": client_identified.pk}, lock=False)
        assert changed == []  # roher PK == DB-Stand → keine Änderung, kein value.pk-Crash
