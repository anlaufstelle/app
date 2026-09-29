"""Refs #2374: Wiederherstellen eines Falls — Einrichtung der Person, Entscheidung am gesperrten Stand.

Der Weg aus dem Papierkorb ist der Zweck des Dienstes. Die mitgegebene
Instanz stammt aber aus der Papierkorb-Liste, womöglich von vor Minuten —
ob der Fall noch im Papierkorb liegt und noch nicht endgültig anonymisiert
ist, wird deshalb an der frisch gesperrten Fallzeile entschieden, in der
kanonischen Ordnung Person -> Fall -> Ereignisse. Der veraltete Zustand wird
in jedem Test über ``update()`` in der Datenbank hergestellt.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog, Case, Client, User
from core.services.case.deletion import restore_case
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db


@pytest.fixture
def fremder_admin(second_facility):
    user = User.objects.create_user(
        username="restore_fremder_admin",
        role=User.Role.FACILITY_ADMIN,
        facility=second_facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def andere_person(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Andere-Person-02",
        created_by=staff_user,
    )


def _restore_zeilen(case):
    return AuditLog.objects.filter(action=AuditLog.Action.CASE_RESTORED, target_id=str(case.pk))


def _loeschstand(case):
    return Case.objects.filter(pk=case.pk).values("is_deleted", "deleted_at", "deleted_by_id", "anonymized_at").get()


class TestWiederherstellenPrueftDieEinrichtungDerPerson:
    @pytest.mark.parametrize("akteur", ["fremder_admin", "facility_less_admin"])
    def test_fremder_oder_einrichtungsloser_admin_sperrt_und_schreibt_nichts(self, request, akteur, case_trashed):
        user = request.getfixturevalue(akteur)
        vorher = _loeschstand(case_trashed)

        with mitschnitt() as aufzeichnung, pytest.raises(ValidationError) as excinfo:
            restore_case(case_trashed, user)

        assert "Fall gehört zu einer anderen Einrichtung" in str(excinfo.value)
        assert aufzeichnung.sperrfolge() == []
        assert _loeschstand(case_trashed) == vorher
        assert not _restore_zeilen(case_trashed).exists()

    def test_fallzeile_in_anderer_einrichtung_der_datenbank_wird_nicht_wiederhergestellt(
        self, case_trashed, admin_user, second_facility
    ):
        """Die Instanz nennt die Einrichtung des Admins, die Zeile liegt in
        einer anderen — entschieden wird an der Zeile."""
        Case.objects.filter(pk=case_trashed.pk).update(facility=second_facility)
        vorher = _loeschstand(case_trashed)

        with pytest.raises(ValidationError):
            restore_case(case_trashed, admin_user)

        assert _loeschstand(case_trashed) == vorher
        assert not _restore_zeilen(case_trashed).exists()

    def test_admin_der_einrichtung_holt_den_fall_zurueck(self, case_trashed, admin_user):
        restore_case(case_trashed, admin_user)

        stand = _loeschstand(case_trashed)
        assert stand["is_deleted"] is False
        assert stand["deleted_at"] is None
        assert case_trashed.is_deleted is False
        assert _restore_zeilen(case_trashed).count() == 1


class TestWiederherstellenEntscheidetAmGesperrtenStand:
    def test_zwischenzeitlich_wiederhergestellter_fall_wird_nicht_erneut_wiederhergestellt(
        self, case_trashed, admin_user
    ):
        veraltet = Case.objects.get(pk=case_trashed.pk)
        restore_case(case_trashed, admin_user)
        vorher = _loeschstand(case_trashed)

        with pytest.raises(ValidationError) as excinfo:
            restore_case(veraltet, admin_user)

        assert "liegt nicht im Papierkorb" in str(excinfo.value)
        assert _loeschstand(case_trashed) == vorher
        assert _restore_zeilen(case_trashed).count() == 1

    def test_zwischenzeitlich_anonymisierter_fall_bleibt_im_papierkorb(self, case_trashed, admin_user):
        veraltet = Case.objects.get(pk=case_trashed.pk)
        Case.objects.filter(pk=case_trashed.pk).update(anonymized_at=timezone.now())
        vorher = _loeschstand(case_trashed)

        with pytest.raises(ValidationError) as excinfo:
            restore_case(veraltet, admin_user)

        assert "endgültig anonymisiert" in str(excinfo.value)
        assert _loeschstand(case_trashed) == vorher
        assert vorher["is_deleted"] is True
        assert not _restore_zeilen(case_trashed).exists()

    def test_umhaengen_waehrend_der_personensperre_bricht_ohne_schreiben_ab(
        self, case_trashed, andere_person, admin_user
    ):
        """Die Personenzeile steht in der kanonischen Ordnung vor der
        Fallzeile, ihre pk kommt aus einer ungesperrten Vorlesung. Wird der
        Fall genau in diesem Fenster umgehängt, bricht der Dienst ab, statt an
        der falschen Person verankert zu schreiben."""
        vorher = _loeschstand(case_trashed)

        with mitschnitt() as aufzeichnung:
            aufzeichnung.nach_sperre(
                "core_client", lambda: Case.objects.filter(pk=case_trashed.pk).update(client=andere_person)
            )
            with pytest.raises(ValidationError):
                restore_case(case_trashed, admin_user)

        assert aufzeichnung.offene_schranken() == []
        assert _loeschstand(case_trashed) == vorher
        assert not _restore_zeilen(case_trashed).exists()

    def test_sperrt_person_vor_fall_vor_ereignissen(self, case_trashed, admin_user):
        with mitschnitt() as aufzeichnung:
            restore_case(case_trashed, admin_user)

        assert aufzeichnung.sperrfolge()[:2] == ["core_client", "core_case"]
        assert [z.traegt(id=[case_trashed.client_id]) for z in aufzeichnung.sperren("core_client")] == [True]

    def test_nach_zusammenfuehrung_haengt_der_fall_an_der_zielperson_und_bleibt_wiederherstellbar(
        self, case_trashed, andere_person, admin_user
    ):
        """Die Zusammenführung nimmt alle Fälle der Quelle mit, auch die im
        Papierkorb — ein Fall an einer Merge-Hülle entsteht nicht. Eine
        veraltete Instanz (Personenbezug noch die Quelle) holt den Fall an der
        Zielperson zurück, gesperrt wird die Zielperson."""
        quelle_id = case_trashed.client_id
        Case.objects.filter(client_id=quelle_id).update(client=andere_person)
        Client.objects.filter(pk=quelle_id).update(merged_into=andere_person)

        with mitschnitt() as aufzeichnung:
            restore_case(case_trashed, admin_user)

        assert [z.traegt(id=[andere_person.pk]) for z in aufzeichnung.sperren("core_client")] == [True]
        stand = Case.objects.filter(pk=case_trashed.pk).values("is_deleted", "client_id").get()
        assert stand == {"is_deleted": False, "client_id": andere_person.pk}
        assert _restore_zeilen(case_trashed).count() == 1
