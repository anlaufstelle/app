"""Personen-Dienste: Einrichtung der handelnden Person vor der ersten Sperre.

Anlegen, Loeschantrag stellen, sofort anonymisieren und Loeschantraege
entscheiden pruefen die Einrichtung der handelnden Person selbst — nicht nur
ueber die aufrufende Oberflaeche. Wer nicht zur Einrichtung gehoert (fremde
Einrichtung oder gar keine), wird abgewiesen, bevor irgendeine Zeile gesperrt
oder geschrieben wird: keine Person, kein Antrag, keine Tilgung, kein Protokoll-
oder Aktivitaetseintrag, keine ``FOR UPDATE``-Anweisung.

Bei den Entscheidungsdiensten stand die Pruefung bereits vor jedem Schreiben,
aber erst nach der Sperre des Antrags; Meldung, Reihenfolge und Wortlaut der
uebrigen Pruefungen bleiben unveraendert. Die Sperren werden am ausgefuehrten
SQL beobachtet.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import Activity, AuditLog, Client, DeletionRequest, User
from core.services.client import (
    anonymize_client_now,
    approve_client_deletion,
    create_client,
    create_or_get_client_deletion_request,
    reject_client_deletion,
)
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_PERSON_MESSAGE = "Person gehört zu einer anderen Einrichtung."
_REQUEST_MESSAGE = "Antrag gehört zu einer anderen Einrichtung."


@pytest.fixture
def foreign_admin(second_facility):
    user = User.objects.create_user(
        username="fremdeverwaltung_personen",
        role=User.Role.FACILITY_ADMIN,
        facility=second_facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


def _refused(callable_):
    with mitschnitt() as abfragen, pytest.raises(ValidationError) as excinfo:
        callable_()
    return excinfo.value.messages, abfragen


def _activities(action):
    return Activity.objects.filter(context__action=str(action)).count()


class TestAnlegen:
    @pytest.mark.parametrize("akteur", ["foreign_staff", "facility_less_admin"])
    def test_fremde_person_legt_keine_person_an(self, request, akteur, facility):
        user = request.getfixturevalue(akteur)
        before = Client.objects.count()

        messages, abfragen = _refused(lambda: create_client(facility, user, pseudonym="Fremd-Angelegt"))

        assert messages == [_PERSON_MESSAGE]
        assert abfragen.sperrfolge() == []
        assert Client.objects.count() == before
        assert not AuditLog.objects.filter(action=AuditLog.Action.CLIENT_CREATE).exists()
        assert _activities(Activity.Action.CLIENT_CREATED) == 0

    def test_gegenprobe_lokale_fachkraft_legt_an(self, staff_user, facility):
        client = create_client(facility, staff_user, pseudonym="Lokal-Angelegt")

        assert client.facility_id == facility.pk
        assert client.created_by_id == staff_user.pk
        assert AuditLog.objects.filter(action=AuditLog.Action.CLIENT_CREATE, target_id=str(client.pk)).exists()


class TestLoeschantragStellen:
    @pytest.mark.parametrize("akteur", ["foreign_staff", "facility_less_admin"])
    def test_fremde_person_stellt_keinen_antrag(self, request, akteur, client_identified):
        user = request.getfixturevalue(akteur)

        messages, abfragen = _refused(
            lambda: create_or_get_client_deletion_request(client_identified, user, "Fremder Antrag")
        )

        assert messages == [_PERSON_MESSAGE]
        assert abfragen.sperrfolge() == []
        assert not DeletionRequest.objects.filter(target_id=client_identified.pk).exists()
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETION_REQUESTED).exists()

    def test_gegenprobe_lokale_fachkraft_stellt_antrag(self, staff_user, client_identified, facility):
        with mitschnitt() as abfragen:
            dr, created = create_or_get_client_deletion_request(client_identified, staff_user, "Doppelt")

        assert created is True
        assert dr.requested_by_id == staff_user.pk
        sperren = abfragen.sperren("core_client")
        assert sperren and sperren[0].traegt(id=[client_identified.pk])
        assert abfragen.sperrfolge()[0] == "core_client"


class TestSofortAnonymisieren:
    @pytest.mark.parametrize("akteur", ["foreign_admin", "facility_less_admin"])
    def test_fremde_verwaltung_tilgt_nicht(self, request, akteur, client_trashed):
        user = request.getfixturevalue(akteur)

        messages, abfragen = _refused(lambda: anonymize_client_now(client_trashed, user))

        assert messages == [_PERSON_MESSAGE]
        assert abfragen.sperrfolge() == []
        row = Client.objects.get(pk=client_trashed.pk)
        assert row.pseudonym == "Trash-01"
        assert row.anonymized_at is None
        assert not AuditLog.objects.filter(action=AuditLog.Action.CLIENT_ANONYMIZED).exists()

    def test_gegenprobe_lokale_verwaltung_tilgt(self, admin_user, client_trashed):
        with mitschnitt() as abfragen:
            anonymize_client_now(client_trashed, admin_user)

        assert Client.objects.get(pk=client_trashed.pk).anonymized_at is not None
        assert abfragen.sperrfolge()[0] == "core_client"


@pytest.fixture
def pending_request(facility, staff_user, client_identified):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=client_identified.pk,
        reason="Pruefsonde",
        requested_by=staff_user,
    )


@pytest.mark.parametrize("service", [approve_client_deletion, reject_client_deletion], ids=["genehmigen", "ablehnen"])
class TestLoeschantragEntscheiden:
    @pytest.mark.parametrize("akteur", ["foreign_admin", "facility_less_admin"])
    def test_fremde_pruefende_sperrt_den_antrag_nicht(self, request, service, akteur, pending_request):
        user = request.getfixturevalue(akteur)

        messages, abfragen = _refused(lambda: service(pending_request, user))

        assert messages == [_REQUEST_MESSAGE]
        assert abfragen.sperrfolge() == []
        pending_request.refresh_from_db()
        assert pending_request.status == DeletionRequest.Status.PENDING
        assert pending_request.reviewed_by_id is None
        assert Client.objects.get(pk=pending_request.target_id).is_deleted is False

    def test_gegenprobe_lokale_leitung_entscheidet(self, service, lead_user, pending_request):
        with mitschnitt() as abfragen:
            service(pending_request, lead_user)

        pending_request.refresh_from_db()
        assert pending_request.status != DeletionRequest.Status.PENDING
        assert pending_request.reviewed_by_id == lead_user.pk
        assert abfragen.sperrfolge()[0] == "core_deletionrequest"
