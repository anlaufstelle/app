"""Zusammenfuehren von Personen: Einrichtung der handelnden Person vor der ersten Sperre.

Zusammenfuehren, Aufheben, Antrag stellen und Antrag entscheiden schreiben
Faelle, Ereignisse und Aufgaben zweier Personen um. Wer nicht zur Einrichtung
der Personen gehoert (fremde Einrichtung oder gar keine), wird mit der
Einrichtungsmeldung abgewiesen — bevor irgendeine Zeile gesperrt wird. Eine
fremde Leitung haelt damit weder Personen- noch Antragszeilen der Einrichtung
fest und erfaehrt nichts ueber deren Zustand.

Die Grenze zwischen Quelle und Ziel (beide Personen in derselben Einrichtung)
zieht weiterhin die Merge-Validierung mit eigener Meldung; diese Datei prueft
die handelnde Person. Die Sperren werden am ausgefuehrten SQL beobachtet.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, AuditLog, Client, DeletionRequest, Event, User
from core.services.client import (
    approve_client_merge,
    create_or_get_client_merge_request,
    merge_clients,
    reject_client_merge,
    unmerge_client,
)
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_FACILITY_MESSAGE = "Person gehört zu einer anderen Einrichtung."
_AKTEURE = ["foreign_lead", "facility_less_admin"]


@pytest.fixture
def foreign_lead(second_facility):
    user = User.objects.create_user(
        username="fremdeleitung_merge",
        role=User.Role.LEAD,
        facility=second_facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def source(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Quelle-Grenze",
        created_by=staff_user,
    )


@pytest.fixture
def target(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Ziel-Grenze",
        created_by=staff_user,
    )


@pytest.fixture
def source_event(facility, source, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        client=source,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )


def _refused(callable_):
    with mitschnitt() as abfragen, pytest.raises(ValidationError) as excinfo:
        callable_()
    return excinfo.value.messages, abfragen


def _audit_count(action, target_id):
    return AuditLog.objects.filter(action=action, target_id=str(target_id)).count()


def _activity_count(action):
    return Activity.objects.filter(context__action=str(action)).count()


class TestDirektesZusammenfuehren:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremde_leitung_abgewiesen_vor_jeder_sperre(self, request, akteur, source, target, source_event):
        user = request.getfixturevalue(akteur)

        messages, abfragen = _refused(lambda: merge_clients(source, target, user))

        assert messages == [_FACILITY_MESSAGE]
        assert abfragen.sperrfolge() == []
        source.refresh_from_db()
        source_event.refresh_from_db()
        assert source.merged_into_id is None
        assert source.is_deleted is False
        assert source_event.client_id == source.pk
        assert _audit_count(AuditLog.Action.CLIENT_MERGED, target.pk) == 0
        assert _activity_count(Activity.Action.CLIENT_MERGED) == 0

    def test_gegenprobe_lokale_leitung_fuehrt_zusammen(self, lead_user, source, target, source_event):
        with mitschnitt() as abfragen:
            merge_clients(source, target, lead_user)

        source.refresh_from_db()
        source_event.refresh_from_db()
        assert source.merged_into_id == target.pk
        assert source_event.client_id == target.pk
        assert abfragen.sperrfolge()[0] == "core_client"


class TestAufhebenDerZusammenfuehrung:
    @pytest.fixture
    def merged_source(self, lead_user, source, target, source_event):
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        return source

    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremde_leitung_abgewiesen_vor_jeder_sperre(self, request, akteur, merged_source, target, source_event):
        user = request.getfixturevalue(akteur)

        messages, abfragen = _refused(lambda: unmerge_client(merged_source, user))

        assert messages == [_FACILITY_MESSAGE]
        assert abfragen.sperrfolge() == []
        row = Client.objects.get(pk=merged_source.pk)
        source_event.refresh_from_db()
        assert row.merged_into_id == target.pk
        assert row.is_deleted is True
        assert source_event.client_id == target.pk
        assert _audit_count(AuditLog.Action.CLIENT_RESTORED, merged_source.pk) == 0
        assert _activity_count(Activity.Action.CLIENT_UNMERGED) == 0

    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_grenze_vor_der_zustandspruefung(self, request, akteur, source):
        """Einer fremden Leitung wird nicht verraten, ob die Person zusammengefuehrt ist."""
        user = request.getfixturevalue(akteur)

        messages, abfragen = _refused(lambda: unmerge_client(source, user))

        assert messages == [_FACILITY_MESSAGE]
        assert abfragen.sperrfolge() == []

    def test_gegenprobe_lokale_leitung_hebt_auf(self, lead_user, merged_source, source_event):
        with mitschnitt() as abfragen:
            unmerge_client(merged_source, lead_user)

        row = Client.objects.get(pk=merged_source.pk)
        source_event.refresh_from_db()
        assert row.merged_into_id is None
        assert source_event.client_id == merged_source.pk
        assert abfragen.sperrfolge()[0] == "core_client"


class TestZusammenfuehrungsAntragStellen:
    @pytest.mark.parametrize("akteur", ["foreign_staff", "facility_less_admin"])
    def test_fremde_person_stellt_keinen_antrag(self, request, akteur, source, target):
        user = request.getfixturevalue(akteur)

        messages, abfragen = _refused(lambda: create_or_get_client_merge_request(source, target, user, "Fremd"))

        assert messages == [_FACILITY_MESSAGE]
        assert abfragen.sperrfolge() == []
        assert not DeletionRequest.objects.filter(target_id=source.pk).exists()
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETION_REQUESTED).exists()

    def test_gegenprobe_lokale_fachkraft_stellt_antrag(self, staff_user, source, target):
        with mitschnitt() as abfragen:
            dr, created = create_or_get_client_merge_request(source, target, staff_user, "Doppelt")

        assert created is True
        assert dr.merge_into_id == target.pk
        assert abfragen.sperrfolge()[0] == "core_client"


@pytest.fixture
def pending_merge_request(facility, staff_user, source, target):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=source.pk,
        reason="Pruefsonde",
        requested_by=staff_user,
        merge_into=target,
    )


@pytest.mark.parametrize("service", [approve_client_merge, reject_client_merge], ids=["genehmigen", "ablehnen"])
class TestZusammenfuehrungsAntragEntscheiden:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremde_pruefende_sperrt_den_antrag_nicht(self, request, service, akteur, pending_merge_request, source):
        user = request.getfixturevalue(akteur)

        messages, abfragen = _refused(lambda: service(pending_merge_request, user))

        assert messages == ["Antrag gehört zu einer anderen Einrichtung."]
        assert abfragen.sperrfolge() == []
        pending_merge_request.refresh_from_db()
        assert pending_merge_request.status == DeletionRequest.Status.PENDING
        assert pending_merge_request.reviewed_by_id is None
        assert Client.objects.get(pk=source.pk).merged_into_id is None

    def test_gegenprobe_lokale_leitung_entscheidet(self, service, lead_user, pending_merge_request):
        with mitschnitt() as abfragen:
            service(pending_merge_request, lead_user)

        pending_merge_request.refresh_from_db()
        assert pending_merge_request.status != DeletionRequest.Status.PENDING
        assert pending_merge_request.reviewed_by_id == lead_user.pk
        assert abfragen.sperrfolge()[0] == "core_deletionrequest"
