"""Refs #2374: Die Lösch-Dienste für Fälle ziehen die Einrichtungsgrenze der handelnden Person selbst.

Über HTTP scopet die View, in Produktion trägt die Zeilensicherheit — der
Dienst ist trotzdem die Single Source of Truth, wie bei ``soft_delete_case``
und ``approve_case_deletion``. Je Dienst: eine Person aus einer fremden
Einrichtung und eine ohne Einrichtung werden abgewiesen, BEVOR eine Zeile
gesperrt oder geschrieben wird (fail-closed); die Gegenprobe mit der Person
der Einrichtung geht durch.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog, Case, Client, DeletionRequest, User
from core.services.case.deletion import create_or_get_case_deletion_request, request_case_deletion
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db


def _konto(username, role, facility):
    user = User.objects.create_user(
        username=username,
        role=role,
        facility=facility,
        is_staff=True,
        can_confirm_deletion=role != User.Role.STAFF,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def fachkraft_ohne_einrichtung(db):
    return _konto("loeschung_fachkraft_ohne_einrichtung", User.Role.STAFF, None)


def _antraege(case):
    return DeletionRequest.objects.filter(target_type=DeletionRequest.TargetType.CASE, target_id=case.pk)


def _antrags_audit():
    return AuditLog.objects.filter(action=AuditLog.Action.DELETION_REQUESTED)


class TestLoeschantragPrueftDieEinrichtungDerFachkraft:
    @pytest.mark.parametrize("akteur", ["foreign_staff", "fachkraft_ohne_einrichtung"])
    @pytest.mark.parametrize("dienst", [create_or_get_case_deletion_request, request_case_deletion])
    def test_fremde_oder_einrichtungslose_fachkraft_sperrt_und_schreibt_nichts(
        self, request, akteur, dienst, case_open
    ):
        user = request.getfixturevalue(akteur)

        with mitschnitt() as aufzeichnung, pytest.raises(ValidationError) as excinfo:
            dienst(case_open, user, "Auf Wunsch der Person")

        assert "Fall gehört zu einer anderen Einrichtung" in str(excinfo.value)
        assert aufzeichnung.sperrfolge() == []
        assert not _antraege(case_open).exists()
        assert not _antrags_audit().exists()

    def test_fachkraft_der_einrichtung_stellt_den_antrag(self, case_open, staff_user):
        antrag, angelegt = create_or_get_case_deletion_request(case_open, staff_user, "Auf Wunsch der Person")

        assert angelegt is True
        assert antrag.requested_by_id == staff_user.pk
        assert _antrags_audit().count() == 1


class TestLoeschantragEntscheidetAmGesperrtenFall:
    @pytest.mark.parametrize(
        ("zwischenstand", "meldung"),
        [("papierkorb", "bereits im Papierkorb"), ("anonymisiert", "endgültig anonymisiert")],
        ids=["papierkorb", "anonymisiert"],
    )
    def test_zwischenzeitlich_geloeschter_fall_bekommt_keinen_antrag(
        self, case_open, staff_user, zwischenstand, meldung
    ):
        veraltet = Case.objects.get(pk=case_open.pk)
        felder = {"papierkorb": {"is_deleted": True, "deleted_at": timezone.now()}}.get(
            zwischenstand, {"anonymized_at": timezone.now()}
        )
        Case.objects.filter(pk=case_open.pk).update(**felder)

        with pytest.raises(ValidationError) as excinfo:
            create_or_get_case_deletion_request(veraltet, staff_user, "Auf Wunsch der Person")

        assert meldung in str(excinfo.value)
        assert not _antraege(case_open).exists()
        assert not _antrags_audit().exists()

    def test_fallzeile_in_anderer_einrichtung_der_datenbank_bekommt_keinen_antrag(
        self, case_open, staff_user, second_facility
    ):
        """Die Instanz nennt die Einrichtung der Fachkraft, die Zeile liegt in
        einer anderen — entschieden wird an der Zeile."""
        Case.objects.filter(pk=case_open.pk).update(facility=second_facility)

        with pytest.raises(ValidationError):
            create_or_get_case_deletion_request(case_open, staff_user, "Auf Wunsch der Person")

        assert not _antraege(case_open).exists()
        assert not _antrags_audit().exists()

    def test_sperrt_person_vor_fall(self, case_open, staff_user):
        with mitschnitt() as aufzeichnung:
            create_or_get_case_deletion_request(case_open, staff_user, "Auf Wunsch der Person")

        assert aufzeichnung.sperrfolge()[:2] == ["core_client", "core_case"]

    def test_eingeschraenkte_verarbeitung_verhindert_den_antrag_nicht(self, case_open, staff_user):
        """Ein Löschantrag ist kein Schreibvorgang am Fall — die Einschränkung
        nach Art. 18 DSGVO steht ihm nicht entgegen."""
        Client.objects.filter(pk=case_open.client_id).update(processing_restricted=True)

        _antrag, angelegt = create_or_get_case_deletion_request(case_open, staff_user, "Auf Wunsch der Person")

        assert angelegt is True
        assert _antrags_audit().count() == 1
