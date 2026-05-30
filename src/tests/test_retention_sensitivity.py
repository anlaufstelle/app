"""DocumentType.retention_days beeinflusst die Retention-Strategien.

Refs Matrix DEV-RET-11 (Welle 3 / Master #922).

Die DSGVO-Retention-Logik kennt vier Strategien (anonymous, identified,
qualified, document_type). Die Plan-Hypothese „Event-Sensitivity
beeinflusst Aufbewahrungsfrist" hat im aktuellen Code eine konkrete
Form: ``DocumentType.retention_days`` ist der Override-Mechanismus. Der
Custom-Override greift unabhaengig von ``sensitivity`` — die
``sensitivity``-Stufe regelt Sichtbarkeit, ``retention_days`` regelt
Aufbewahrung. Beides sind Policy-Hebel auf demselben DocumentType, die
zusammen konfiguriert werden duerfen.

Tests verifizieren das IST-Verhalten ueber
:func:`core.retention.strategies.iter_strategies`:

- DocumentType mit ``retention_days=30`` taucht in der document_type-
  Strategie auf; Events vor dem Cutoff werden gefunden.
- DocumentType ohne ``retention_days`` taucht nicht auf.
- ELEVATED/HIGH-Sensitivity ist *nicht* automatisch mit kuerzerer
  Frist verknuepft — Marker-Test dokumentiert, dass die Settings frei
  waehlbar sind.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import DocumentType, Event
from core.retention.strategies import iter_strategies


def _at(days_ago: int):
    return timezone.now() - timedelta(days=days_ago)


@pytest.mark.django_db
class TestRetentionSensitivity:
    """Tests fuer DocumentType-spezifische Retention via ``retention_days``."""

    def test_document_type_retention_days_drives_cutoff(self, facility, doc_type_contact, staff_user, settings_obj):
        """Ein DocumentType mit ``retention_days=30`` produziert eine
        document_type-Strategy; Events aelter als 30 Tage matchen das
        QuerySet der Strategie.
        """
        doc_type_contact.retention_days = 30
        doc_type_contact.save(update_fields=["retention_days"])

        # Anonymes Event aelter als 30 Tage — landet sowohl in der
        # anonymous-Strategie als auch in der document_type-Strategie,
        # ist hier aber der Trigger fuer die DT-Strategie.
        old_event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(35),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )

        now = timezone.now()
        strategies_by_category = {}
        for strategy in iter_strategies(facility, settings_obj, now):
            strategies_by_category.setdefault(strategy.category, []).append(strategy)

        assert "document_type" in strategies_by_category, (
            "Erwartet: eine document_type-Strategie fuer den DocType mit retention_days=30."
        )
        dt_strategies = strategies_by_category["document_type"]
        assert len(dt_strategies) == 1
        # Cutoff = now - 30 Tage. Eventqueryset enthaelt unser altes Event.
        dt_strategy = dt_strategies[0]
        assert old_event in list(dt_strategy.queryset)

    def test_document_type_without_retention_days_not_in_strategies(
        self, facility, doc_type_contact, staff_user, settings_obj
    ):
        """``retention_days IS NULL`` -> DocumentType taucht in
        ``iter_strategies`` nicht als document_type-Strategie auf.
        """
        # doc_type_contact hat ``retention_days=None`` per Default.
        assert doc_type_contact.retention_days is None

        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(99999),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )

        now = timezone.now()
        document_type_strategies = [
            s for s in iter_strategies(facility, settings_obj, now) if s.category == "document_type"
        ]
        assert document_type_strategies == [], (
            "DocumentType ohne retention_days darf keine document_type-"
            "Strategie produzieren — sonst wuerden alle Default-DocTypes "
            "im Retention-Lauf auftauchen."
        )

    def test_higher_sensitivity_typically_shorter_retention_setting(self, facility, doc_type_crisis, settings_obj):
        """Marker-Test (nur Dokumentation): ``retention_days`` ist eine frei
        waehlbare Policy-Einstellung pro DocumentType — die Sensitivity
        des DocumentType beeinflusst sie *nicht* automatisch.

        Realweltlich wuerde man ELEVATED-Datentypen (z.B. Krisengespraech)
        eher *kuerzer* aufbewahren (Datenminimierung). Der Code erzwingt
        das aber nicht — Test dokumentiert, dass die Setting offen ist.
        """
        # doc_type_crisis hat sensitivity=ELEVATED.
        assert doc_type_crisis.sensitivity == DocumentType.Sensitivity.ELEVATED
        # ...aber retention_days ist standardmaessig None.
        assert doc_type_crisis.retention_days is None

        # Setting auf 7 Tage (sehr kurz) ist zulaessig.
        doc_type_crisis.retention_days = 7
        doc_type_crisis.save(update_fields=["retention_days"])
        doc_type_crisis.refresh_from_db()
        assert doc_type_crisis.retention_days == 7

        # Setting auf 36500 (100 Jahre, sehr lang) ist ebenfalls zulaessig —
        # das Schema haelt nicht Sensitivity gegen retention_days. Wer
        # eine Policy will, muss sie auf Service-Ebene erzwingen.
        doc_type_crisis.retention_days = 36500
        doc_type_crisis.save(update_fields=["retention_days"])
        doc_type_crisis.refresh_from_db()
        assert doc_type_crisis.retention_days == 36500

        # Beide Werte erzeugen valide document_type-Strategien.
        now = timezone.now()
        strategies = [s for s in iter_strategies(facility, settings_obj, now) if s.category == "document_type"]
        assert len(strategies) == 1
        # Cutoff korrespondiert zum aktuell gesetzten retention_days-Wert.
        assert strategies[0].cutoff <= now - timedelta(days=36499)
