"""Papierkorb-Verschiebung eines Falls: Zustand auf der gesperrten Zeile prüfen.

``_move_case_to_trash`` ist der gemeinsame Kern des direkten Löschwegs
(``soft_delete_case``) und der Vier-Augen-Genehmigung
(``approve_case_deletion``). Bisher prüfte er ``is_deleted`` an der
mitgegebenen, ungesperrten Instanz und ``anonymized_at`` gar nicht. Hält der
Aufrufer eine alte Instanz, während der Fall inzwischen im Papierkorb liegt oder
schon getilgt ist, setzte ein zweiter Soft-Delete ``deleted_at`` neu — die
Papierkorb-Frist begann von vorn — und schrieb eine zweite
``CASE_SOFT_DELETED``-Zeile. Ein getilgter Fall ist endgültig; er wird nicht
noch einmal in den Papierkorb gelegt.

Abgewiesen wird ohne Änderung an der Fallzeile und ohne Audit-Zeile; der
reguläre Weg bleibt unverändert und zieht die Instanz des Aufrufers mit.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AuditLog, Case, DeletionRequest
from core.services.case.deletion import approve_case_deletion, soft_delete_case

pytestmark = pytest.mark.django_db

_PAPIERKORB = "Der Fall liegt bereits im Papierkorb."
_GETILGT = "Der Fall wurde bereits endgültig anonymisiert."
_SPALTEN = ("is_deleted", "deleted_at", "deleted_by_id", "anonymized_at")


def _snapshot(case):
    return Case.objects.filter(pk=case.pk).values(*_SPALTEN).get()


def _trash_audit_count(case):
    return AuditLog.objects.filter(action=AuditLog.Action.CASE_SOFT_DELETED, target_id=str(case.pk)).count()


def _assert_refused(callable_, case, message):
    vorher = _snapshot(case)
    audit_vorher = _trash_audit_count(case)
    with pytest.raises(ValidationError) as excinfo:
        callable_()
    assert excinfo.value.messages == [message]
    assert _snapshot(case) == vorher
    assert _trash_audit_count(case) == audit_vorher


def _set_state(case, *, deleted, anonymized):
    """Zustand direkt in der Datenbank setzen — die Instanz bleibt alt."""
    earlier = timezone.now() - timedelta(days=3)
    Case.objects.filter(pk=case.pk).update(
        is_deleted=deleted,
        deleted_at=earlier if deleted else None,
        anonymized_at=earlier if anonymized else None,
    )


def _pending_request(case, requester):
    return DeletionRequest.objects.create(
        facility=case.facility,
        target_type=DeletionRequest.TargetType.CASE,
        target_id=case.pk,
        status=DeletionRequest.Status.PENDING,
        requested_by=requester,
        reason="Pruefsonde",
    )


class TestDirectPath:
    def test_stale_instance_of_a_trashed_case_is_refused(self, case_open, staff_user):
        _set_state(case_open, deleted=True, anonymized=False)
        assert case_open.is_deleted is False
        _assert_refused(lambda: soft_delete_case(case_open, staff_user), case_open, _PAPIERKORB)

    def test_stale_instance_of_an_anonymized_case_is_refused(self, case_open, staff_user):
        _set_state(case_open, deleted=True, anonymized=True)
        _assert_refused(lambda: soft_delete_case(case_open, staff_user), case_open, _PAPIERKORB)

    def test_anonymized_case_outside_the_trash_is_refused(self, case_open, staff_user):
        _set_state(case_open, deleted=False, anonymized=True)
        _assert_refused(lambda: soft_delete_case(case_open, staff_user), case_open, _GETILGT)

    def test_regular_case_is_moved_and_caller_instance_follows(self, case_open, staff_user):
        result = soft_delete_case(case_open, staff_user)
        assert result is case_open
        row = _snapshot(case_open)
        assert row["is_deleted"] is True
        assert row["deleted_by_id"] == staff_user.pk
        assert case_open.is_deleted is True
        assert case_open.deleted_at == row["deleted_at"]
        assert case_open.deleted_by_id == staff_user.pk
        assert _trash_audit_count(case_open) == 1


class TestApprovedPath:
    def test_anonymized_case_is_not_trashed_by_approval(self, case_open, staff_user, lead_user):
        dr = _pending_request(case_open, staff_user)
        _set_state(case_open, deleted=False, anonymized=True)
        _assert_refused(lambda: approve_case_deletion(dr, lead_user), case_open, _GETILGT)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING

    def test_regular_approval_still_trashes(self, case_open, staff_user, lead_user):
        dr = _pending_request(case_open, staff_user)
        approve_case_deletion(dr, lead_user)
        assert _snapshot(case_open)["is_deleted"] is True
        assert _trash_audit_count(case_open) == 1


class TestDirekterLoeschwegUeberHttp:
    """Das Zustandsrennen zwischen View-Lesung und Service endet nicht im Serverfehler.

    Die View lädt den Fall gescopet; wird er danach (zweiter Tab, zweite
    Person) in den Papierkorb gelegt oder getilgt, weist der Service ihn an der
    gesperrten Zeile ab. Die Antwort ist eine Weiterleitung mit Meldung, der
    Fall bleibt unverändert, es entsteht keine zweite Audit-Zeile.
    """

    @pytest.fixture
    def rennen_nach_dem_laden(self, monkeypatch):
        from core.views.case_deletion import CaseDeleteRequestView

        original = CaseDeleteRequestView._load
        zustand = {}

        def laden_und_dann_aendern(view, request, pk):
            geladen = original(view, request, pk)
            _set_state(geladen, **zustand)
            return geladen

        monkeypatch.setattr(CaseDeleteRequestView, "_load", laden_und_dann_aendern)
        return zustand

    @pytest.mark.parametrize(
        ("deleted", "anonymized", "meldung"),
        [(True, False, _PAPIERKORB), (False, True, _GETILGT)],
        ids=["papierkorb", "getilgt"],
    )
    def test_post_meldet_den_konflikt(
        self, client, case_open, staff_user, rennen_nach_dem_laden, deleted, anonymized, meldung
    ):
        from django.contrib import messages
        from django.contrib.messages import get_messages
        from django.urls import reverse

        rennen_nach_dem_laden.update(deleted=deleted, anonymized=anonymized)
        client.force_login(staff_user)
        client.raise_request_exception = False

        response = client.post(reverse("core:case_delete_request", kwargs={"pk": case_open.pk}))

        assert response.status_code == 302
        assert response["Location"] == reverse("core:case_list")
        gemeldet = [(m.level, str(m)) for m in get_messages(response.wsgi_request)]
        assert gemeldet == [(messages.ERROR, meldung)]
        assert _trash_audit_count(case_open) == 0


_FREMDER_FALL = "Fall gehört zu einer anderen Einrichtung."
_FREMDER_ANTRAG = "Antrag gehört zu einer anderen Einrichtung."


def _fremde_leitung(second_facility, username):
    from core.models import User

    return User.objects.create_user(
        username=username,
        role=User.Role.LEAD,
        facility=second_facility,
        is_staff=True,
        can_confirm_deletion=True,
    )


def _sperren_auf(queries, tabelle):
    return [q["sql"] for q in queries if "FOR UPDATE" in q["sql"] and f'FROM "{tabelle}"' in q["sql"]]


class TestEinrichtungDerHandelndenPerson:
    """Einrichtungsgrenze der handelnden Person vor der ersten Sperre.

    Der direkte Löschweg prüfte die Einrichtung der handelnden Person gar
    nicht; die Genehmigung prüfte sie erst nach der Sperre des Antrags. Über
    HTTP scopet die View; der Service ist trotzdem die Single Source of Truth.
    """

    def test_fremde_person_loescht_nicht_direkt(self, case_open, second_facility):
        fremd = _fremde_leitung(second_facility, "fremd-direkt")
        with CaptureQueriesContext(connection) as ctx:
            _assert_refused(lambda: soft_delete_case(case_open, fremd), case_open, _FREMDER_FALL)
        assert [q for q in ctx.captured_queries if "FOR UPDATE" in q["sql"]] == []

    def test_person_ohne_einrichtung_loescht_nicht_direkt(self, case_open, staff_user):
        staff_user.facility = None
        _assert_refused(lambda: soft_delete_case(case_open, staff_user), case_open, _FREMDER_FALL)

    def test_instanz_mit_verfaelschter_einrichtung_oeffnet_keinen_fremden_fall(
        self, case_open, second_facility, staff_user
    ):
        """Die Grenze gilt für die Fallzeile in der Datenbank, nicht für die Instanz im Speicher."""
        fremd = _fremde_leitung(second_facility, "fremd-instanz")
        case_open.facility_id = second_facility.pk
        vorher = Case.objects.filter(pk=case_open.pk).values(*_SPALTEN).get()
        with CaptureQueriesContext(connection) as ctx, pytest.raises(ValidationError) as excinfo:
            soft_delete_case(case_open, fremd)
        assert excinfo.value.messages == [_FREMDER_FALL]
        assert Case.objects.filter(pk=case_open.pk).values(*_SPALTEN).get() == vorher
        # Auch die Person des fremden Falls wird nicht gesperrt.
        assert [q for q in ctx.captured_queries if "FOR UPDATE" in q["sql"]] == []

    def test_genehmigung_prueft_die_einrichtung_vor_der_antragssperre(self, case_open, staff_user, second_facility):
        dr = _pending_request(case_open, staff_user)
        fremd = _fremde_leitung(second_facility, "fremd-genehmigung")
        with CaptureQueriesContext(connection) as ctx:
            _assert_refused(lambda: approve_case_deletion(dr, fremd), case_open, _FREMDER_ANTRAG)
        assert _sperren_auf(ctx.captured_queries, "core_deletionrequest") == []
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING

    def test_genehmigung_ohne_einrichtung_wird_abgewiesen(self, case_open, staff_user, lead_user):
        dr = _pending_request(case_open, staff_user)
        lead_user.facility = None
        with CaptureQueriesContext(connection) as ctx:
            _assert_refused(lambda: approve_case_deletion(dr, lead_user), case_open, _FREMDER_ANTRAG)
        assert _sperren_auf(ctx.captured_queries, "core_deletionrequest") == []
