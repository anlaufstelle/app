"""Wechselseitige Grenzen von Fall- und Personen-Loeschpfad (Refs #1818, ADR-044).

Der Fall-Loeschpfad hat die Richtung Person -> Fall vollstaendig gebaut: ein
Hold auf der Person sperrt die Tilgung ihres Falls, und der Fall-Pfad redigiert
seinen eigenen Loeschantrag. Die **Gegenrichtung** blieb offen — und genau dort
ist sie gefaehrlich, weil der Personen-Pfad breiter loescht:
``_redact_cases_and_episodes`` leert Titel, Beschreibung und ``data_json``
aller Faelle, ``_redact_case_goals`` ihre Wirkungsziele und Meilensteine, und
``_delete_owned_attachments_for_client`` entfernt die fall-eigenen Anhaenge
physisch vom Datentraeger (nicht rollback-bar).

Drei Guards:

1. **Legal-Hold-Paritaet** — ein Hold mit ``target_type="Case"`` muss die
   Anonymisierung der zugehoerigen PERSON sperren, an allen Einstiegen, die
   ueber ``exclude_clients_with_active_holds`` laufen (Papierkorb-Frist,
   Admin-Sofort-Tilgung, K-Anon-Bridge). Sonst waere der Personen-Weg die
   Hintertuer um den frisch eingefuehrten Fall-Hold (Spoliation).
2. **Struktur-Paritaet** — beide ``exclude_*_with_active_holds``-Helfer kennen
   dieselben drei Zieltypen. Ein vierter Zieltyp faellt damit auf, statt still
   nur auf einer Seite anzukommen.
3. **Redaktions-Paritaet** — Freitexte, die AM Fall haengen (Begruendung eines
   Fall-Loeschantrags, Grund eines Fall-Holds), ueberleben die vollstaendige
   Anonymisierung der Person nicht. Der Redaktions-Faecher kannte bis hierher
   nur ``Client``- und ``Event``-Ziele.
"""

import inspect
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Client, DeletionRequest, LegalHold
from core.retention import legal_holds
from core.retention.legal_holds import (
    client_has_active_hold,
    exclude_cases_with_active_holds,
    exclude_clients_with_active_holds,
)
from core.services.case import request_case_deletion
from core.services.client import anonymize_client_now, anonymize_eligible_soft_deleted_clients

pytestmark = pytest.mark.django_db


def _person_qs(client):
    """Ein-Zeilen-Queryset der Person — die Filter-Helfer nehmen ein Queryset."""
    return Client.objects.filter(pk=client.pk)


def _case_hold(facility, user, case, reason="Ermittlungsverfahren"):
    return LegalHold.objects.create(
        facility=facility,
        target_type="Case",
        target_id=case.pk,
        reason=reason,
        created_by=user,
    )


class TestCaseHoldBlocksPersonAnonymization:
    """Gegenrichtung zu ``exclude_cases_with_active_holds`` (P4)."""

    def test_single_check_sees_the_case_hold(self, facility, case_open, client_identified, lead_user):
        _case_hold(facility, lead_user, case_open)
        assert client_has_active_hold(client_identified) is True

    def test_queryset_filter_excludes_the_person(self, facility, case_open, client_identified, lead_user):
        _case_hold(facility, lead_user, case_open)
        rest = exclude_clients_with_active_holds(_person_qs(client_identified), facility)
        assert list(rest) == []

    def test_trash_expiry_defers_the_person(self, facility, settings_obj, case_open, client_identified, lead_user):
        _case_hold(facility, lead_user, case_open)
        client_identified.soft_delete(user=lead_user)
        _person_qs(client_identified).update(deleted_at=timezone.now() - timedelta(days=365))
        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 0
        case_open.refresh_from_db()
        assert "Anonymisiert" not in case_open.title

    def test_manual_person_anonymization_is_refused(
        self, facility, case_open, client_identified, lead_user, admin_user
    ):
        _case_hold(facility, lead_user, case_open)
        client_identified.soft_delete(user=admin_user)
        with pytest.raises(ValidationError):
            anonymize_client_now(client_identified, admin_user)
        case_open.refresh_from_db()
        assert "Anonymisiert" not in case_open.title

    def test_dismissed_case_hold_does_not_block(self, facility, case_open, client_identified, lead_user):
        hold = _case_hold(facility, lead_user, case_open)
        hold.dismissed_at = timezone.now()
        hold.save(update_fields=["dismissed_at"])
        assert client_has_active_hold(client_identified) is False


class TestHoldHelperParity:
    """Beide Richtungen kennen dieselben drei Zieltypen."""

    def test_both_helpers_cover_client_event_and_case(self):
        quellen = {
            "clients": inspect.getsource(exclude_clients_with_active_holds),
            "cases": inspect.getsource(exclude_cases_with_active_holds),
        }
        for name, quelle in quellen.items():
            fehlend = [t for t in ('"Client"', '"Event"', '"Case"') if t not in quelle]
            assert not fehlend, (
                f"exclude_{name}_with_active_holds kennt {fehlend} nicht — ein Hold auf diesem "
                "Zieltyp waere ueber den anderen Loeschpfad aushebelbar (Spoliation)."
            )

    def test_target_ids_helper_is_the_only_active_boundary(self):
        """Beide Helfer ziehen die Aktiv-Grenze ueber denselben Helfer (Refs #1192)."""
        for fn in (exclude_clients_with_active_holds, exclude_cases_with_active_holds):
            assert "get_active_hold_target_ids" in inspect.getsource(fn)
        assert "timezone.localdate" in inspect.getsource(legal_holds.get_active_hold_target_ids)


class TestCaseFreetextDiesWithThePerson:
    """Refs #1091/#1751 auf der Fall-Achse."""

    def test_case_deletion_request_reason_is_redacted(self, case_open, client_identified, staff_user, admin_user):
        dr = request_case_deletion(case_open, staff_user, "Fall doppelt angelegt, Betreuung laeuft bei Frau Meier")
        client_identified.soft_delete(user=admin_user)
        anonymize_client_now(client_identified, admin_user)

        dr.refresh_from_db()
        assert dr.target_type == DeletionRequest.TargetType.CASE
        assert dr.reason == "[Anonymisiert]"
        # Antrags-Meta bleibt als Vier-Augen-Nachweis stehen.
        assert dr.requested_by_id == staff_user.pk
        assert dr.status == DeletionRequest.Status.PENDING

    def test_case_hold_reason_is_redacted(self, facility, case_open, client_identified, lead_user, admin_user):
        hold = _case_hold(facility, lead_user, case_open, reason="Ermittlungsverfahren StA Musterstadt gegen Herrn X")
        # Aufgehoben — ein AKTIVER Hold blockiert die Anonymisierung (Guard oben),
        # hier geht es um den Freitext, der danach stehenbleiben wuerde.
        hold.dismissed_at = timezone.now()
        hold.save(update_fields=["dismissed_at"])

        client_identified.soft_delete(user=admin_user)
        anonymize_client_now(client_identified, admin_user)

        hold.refresh_from_db()
        assert hold.reason == "[Anonymisiert]"
        assert hold.target_type == "Case"
        assert hold.target_id == case_open.pk
