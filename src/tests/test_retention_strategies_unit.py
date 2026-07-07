"""Charakterisierungstests fuer Retention-Strategien (Refs #776).

Vier Strategien × pos/neg/boundary plus eine Cross-Strategy-Intersection
fuer das Cutoff-Verhalten in :mod:`core.retention.enforcement`.

Sicherheitsnetz fuer Refactorings: wenn das Submodul umgebaut
wird, muessen die Cutoffs in Tagen weiterhin **strikt unter** dem
``occurred_at`` greifen — nicht ``<=``, sonst loeschen wir Events am
Stichtag.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Case, Event, Settings
from core.retention.enforcement import (
    enforce_anonymous,
    enforce_document_type_retention,
    enforce_identified,
    enforce_qualified,
)


def _at(days_ago: int):
    return timezone.now() - timedelta(days=days_ago)


@pytest.fixture
def settings_obj(facility):
    return Settings.objects.create(
        facility=facility,
        retention_anonymous_days=90,
        retention_identified_days=365,
        retention_qualified_days=3650,
    )


@pytest.mark.django_db
class TestEnforceAnonymous:
    def test_pos_old_event_is_doomed(self, facility, staff_user, doc_type_contact, settings_obj):
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(100),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert enforce_anonymous(facility, settings_obj, timezone.now(), dry_run=True) == {"count": 1}

    def test_neg_recent_event_is_kept(self, facility, staff_user, doc_type_contact, settings_obj):
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(1),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert enforce_anonymous(facility, settings_obj, timezone.now(), dry_run=True) == {"count": 0}

    def test_boundary_exact_cutoff_is_kept(self, facility, staff_user, doc_type_contact, settings_obj):
        """Cutoff ist strict less-than (``<``), nicht ``<=`` — Stichtag bleibt."""
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(90),  # exakt am Cutoff
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        # Etwas nach dem Cutoff schauen, damit kein clock-skew-Race entsteht.
        now = timezone.now() + timedelta(microseconds=1)
        assert enforce_anonymous(facility, settings_obj, now, dry_run=True)["count"] == 1


@pytest.mark.django_db
class TestEnforceIdentified:
    def test_pos_old_identified_event(self, facility, staff_user, doc_type_contact, client_identified, settings_obj):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=_at(400),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_identified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 1

    def test_neg_recent_identified_event(self, facility, staff_user, doc_type_contact, client_identified, settings_obj):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=_at(10),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_identified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0

    def test_boundary_qualified_client_excluded(
        self, facility, staff_user, doc_type_contact, client_qualified, settings_obj
    ):
        """Qualified-Client-Events fallen NICHT unter ``enforce_identified`` —
        die Kategorisierung haengt am ContactStage des Klienten."""
        Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=_at(400),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_identified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0


@pytest.mark.django_db
class TestEnforceQualified:
    def test_pos_event_in_long_closed_case(
        self, facility, staff_user, doc_type_contact, client_qualified, settings_obj
    ):
        case = Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Lang geschlossen",
            status=Case.Status.CLOSED,
            closed_at=_at(4000),
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_qualified,
            case=case,
            document_type=doc_type_contact,
            occurred_at=_at(5000),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_qualified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 1

    def test_neg_open_case_keeps_event(self, facility, staff_user, doc_type_contact, client_qualified, settings_obj):
        case = Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Offen",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_qualified,
            case=case,
            document_type=doc_type_contact,
            occurred_at=_at(5000),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_qualified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0

    def test_neg_recently_closed_case(self, facility, staff_user, doc_type_contact, client_qualified, settings_obj):
        case = Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Frisch geschlossen",
            status=Case.Status.CLOSED,
            closed_at=_at(10),
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_qualified,
            case=case,
            document_type=doc_type_contact,
            occurred_at=_at(5000),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_qualified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0


@pytest.mark.django_db
class TestEnforceDocumentTypeRetention:
    def test_pos_doctype_specific_retention(self, facility, staff_user, doc_type_contact):
        doc_type_contact.retention_days = 30
        doc_type_contact.save(update_fields=["retention_days"])
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(60),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert enforce_document_type_retention(facility, timezone.now(), dry_run=True)["count"] == 1

    def test_neg_no_retention_days_set(self, facility, staff_user, doc_type_contact):
        """``retention_days IS NULL`` -> DocumentType wird ignoriert."""
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(99999),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert enforce_document_type_retention(facility, timezone.now(), dry_run=True)["count"] == 0

    def test_boundary_recent_event_kept(self, facility, staff_user, doc_type_contact):
        doc_type_contact.retention_days = 30
        doc_type_contact.save(update_fields=["retention_days"])
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(1),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert enforce_document_type_retention(facility, timezone.now(), dry_run=True)["count"] == 0


@pytest.mark.django_db
class TestNonPositiveDaysSkipped:
    """Security N8 (Refs #1445): 0/negative Fristen erzeugen einen Cutoff
    ``>= jetzt`` (``now - timedelta(days=-5) == now + 5d``) und wuerden den
    Gesamtbestand zum Loeschen vorschlagen. Validator + DB-Constraint
    verhindern das beim Speichern; ``iter_strategies`` ueberspringt solche
    Kategorien zusaetzlich defensiv (Alt-Daten / Direkt-Writes).

    Die Settings-Faelle klemmen das Attribut nur IM SPEICHER (kein ``save``) —
    der DB-Constraint wuerde eine negative Frist gar nicht erst persistieren.
    """

    def test_negative_anonymous_days_yields_no_anonymous_strategy(self, facility, settings_obj):
        from core.retention.strategies import iter_strategies

        settings_obj.retention_anonymous_days = -5
        categories = [s.category for s in iter_strategies(facility, settings_obj, timezone.now())]
        assert "anonymous" not in categories
        # Die uebrigen (gueltigen) Kategorien bleiben erhalten.
        assert "identified" in categories
        assert "qualified" in categories

    def test_zero_identified_days_yields_no_identified_strategy(self, facility, settings_obj):
        from core.retention.strategies import iter_strategies

        settings_obj.retention_identified_days = 0
        categories = [s.category for s in iter_strategies(facility, settings_obj, timezone.now())]
        assert "identified" not in categories
        assert "anonymous" in categories

    def test_negative_qualified_days_yields_no_qualified_strategy(self, facility, settings_obj):
        from core.retention.strategies import iter_strategies

        settings_obj.retention_qualified_days = -1
        categories = [s.category for s in iter_strategies(facility, settings_obj, timezone.now())]
        assert "qualified" not in categories

    def test_negative_anonymous_days_collects_no_doomed_event(
        self, facility, staff_user, doc_type_contact, settings_obj
    ):
        """End-to-end ueber ``collect_doomed_events`` (Proposal-Pfad): ein altes
        anonymes Event ist bei gueltiger Frist doomed, bei negativer Frist nicht."""
        from core.retention.enforcement import collect_doomed_events

        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(100),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        # Positivkontrolle: gueltige Frist -> Event ist doomed.
        assert collect_doomed_events(facility, settings_obj, timezone.now()).count() == 1
        # Negative Frist -> Strategie uebersprungen -> nichts doomed.
        settings_obj.retention_anonymous_days = -5
        assert collect_doomed_events(facility, settings_obj, timezone.now()).count() == 0

    def test_zero_days_does_not_delete_in_enforce_path(self, facility, staff_user, doc_type_contact, settings_obj):
        """Security N8: der DIREKTE Loeschpfad (``enforce_anonymous``, nicht nur
        der Proposal-Pfad) darf bei 0/negativer Frist NICHTS soft-loeschen —
        Defense-in-Depth neben dem DB-Constraint."""
        from core.retention.enforcement import enforce_anonymous

        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(100),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        settings_obj.retention_anonymous_days = 0
        result = enforce_anonymous(facility, settings_obj, timezone.now(), dry_run=False)
        assert result == {"count": 0}
        event.refresh_from_db()
        assert event.is_deleted is False


@pytest.mark.django_db
class TestStrategyIntersection:
    """Cross-Strategy: ein Event darf nicht von zwei Strategien doppelt
    gezaehlt werden — die Strategien sind disjunkt nach ``is_anonymous`` und
    ``contact_stage`` des Klienten.
    """

    def test_anonymous_event_not_counted_by_identified(
        self, facility, staff_user, doc_type_contact, client_identified, settings_obj
    ):
        # Anonymes Event hat keinen Client → ``enforce_identified`` sieht es
        # gar nicht erst (client__in-Filter), waehrend ``enforce_anonymous``
        # es zaehlt.
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(400),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        ano = enforce_anonymous(facility, settings_obj, timezone.now(), dry_run=True)["count"]
        ident = enforce_identified(facility, settings_obj, timezone.now(), dry_run=True)["count"]
        assert (ano, ident) == (1, 0)

    def test_event_in_two_categories_collected_once(
        self, facility, staff_user, doc_type_contact, client_identified, settings_obj
    ):
        """Refs #778: ein Event matcht 'identified' (alter Klient-Event) UND
        'document_type' (DocType hat retention_days=30) — ``collect_doomed_events``
        muss es **einmal** liefern (``.distinct()``), nicht zweimal."""
        from core.retention.enforcement import collect_doomed_events

        doc_type_contact.retention_days = 30
        doc_type_contact.save(update_fields=["retention_days"])
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=_at(400),
            data_json={},
            created_by=staff_user,
        )

        doomed = collect_doomed_events(facility, settings_obj, timezone.now())
        assert doomed.count() == 1, (
            "Cross-Strategy-Intersection: ein Event in 2 Kategorien darf nur "
            "einmal in collect_doomed_events landen (sonst werden EventHistory "
            "und AuditLog doppelt geschrieben)."
        )
