"""Die Fall- und Ziel-Dienste ziehen die Einrichtungsgrenze der handelnden Person selbst.

Ueber HTTP scopen die Views den Fall bereits, in Produktion traegt
zusaetzlich die Zeilensicherheit der Datenbank mit; im Test ist sie nicht
aktiv, der Dienst ist also die einzige Grenze. Eine Fachkraft aus einer
anderen Einrichtung und eine Fachkraft ohne Einrichtung (fail-closed) werden
mit derselben Meldung abgewiesen — und zwar vor jeder Zeilensperre: nichts
wird ueber die Mandantengrenze hinweg gesperrt, nichts angelegt oder
geaendert, es entsteht keine Audit- und keine Zeitstrom-Zeile. Die Gegenprobe
mit einer Fachkraft der eigenen Einrichtung schreibt wie bisher.

Beobachtet wird das Verhalten: Meldung, Datenbestand, Protokollzeilen und die
tatsaechlich gesperrten Tabellen (am ausgefuehrten SQL, nicht am Wortlaut).
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, AuditLog, Case, Client, User
from core.models.outcome import Milestone, OutcomeGoal
from core.services.case import (
    achieve_goal,
    close_case,
    create_case,
    create_goal,
    reopen_case,
    toggle_milestone,
    unachieve_goal,
    update_case,
    update_goal,
)
from core.services.case.state_contract import STATUS_CHANGE_GONE_MESSAGE
from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_FALL_FREMD = "Fall gehört zu einer anderen Einrichtung."
_AKTEURE = ["fremde_fachkraft", "fachkraft_ohne_einrichtung"]


@pytest.fixture
def fremde_fachkraft(second_facility):
    return User.objects.create_user(
        username="fremdfachkraft-falldienst", role=User.Role.LEAD, facility=second_facility, is_staff=True
    )


@pytest.fixture
def fachkraft_ohne_einrichtung():
    return User.objects.create_user(
        username="ohneeinrichtung-falldienst", role=User.Role.LEAD, facility=None, is_staff=True
    )


def _spuren():
    return AuditLog.objects.count(), Activity.objects.count()


def _abgewiesen_ohne_sperre(aufruf):
    """Ruft den Dienst und prueft Meldung, fehlende Spuren und fehlende Sperren."""
    spuren = _spuren()
    with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
        aufruf()
    assert excinfo.value.messages == [_FALL_FREMD]
    assert _spuren() == spuren
    assert m.sperrfolge() == []


class TestUpdateCase:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, case_open):
        user = request.getfixturevalue(akteur)
        fall = Case.objects.get(pk=case_open.pk)

        _abgewiesen_ohne_sperre(lambda: update_case(fall, user, title="Fremd", description="Fremd"))

        frisch = Case.objects.get(pk=case_open.pk)
        assert (frisch.title, frisch.description) == ("Offener Fall", "")
        assert not AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE, target_id=str(case_open.pk)).exists()

    def test_eigene_einrichtung_schreibt(self, case_open, staff_user):
        update_case(Case.objects.get(pk=case_open.pk), staff_user, title="Neu")

        assert Case.objects.get(pk=case_open.pk).title == "Neu"
        assert AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE, target_id=str(case_open.pk)).count() == 1


class TestCreateCase:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, facility, client_identified):
        user = request.getfixturevalue(akteur)
        person = Client.objects.get(pk=client_identified.pk)

        _abgewiesen_ohne_sperre(lambda: create_case(facility, user, person, "Fremd"))

        assert not Case.objects.filter(client_id=client_identified.pk).exists()

    @pytest.mark.parametrize(
        "widerruf",
        [
            pytest.param({"is_deleted": True}, id="person-papierkorb"),
            pytest.param({"is_active": False}, id="person-anonymisiert"),
        ],
    )
    def test_widerrufene_person_wird_an_der_gesperrten_zeile_abgewiesen(
        self, widerruf, facility, client_identified, staff_user
    ):
        """Die Instanz stammt aus einem Lesevorgang vor der Sperre.

        Papierkorb (auch die Quelle einer Zusammenfuehrung landet dort) oder
        Anonymisierung danach: kein Fall, keine Protokollzeile, gesperrt wurde
        nur die Personenzeile.
        """
        person = Client.objects.get(pk=client_identified.pk)
        if widerruf.get("is_deleted"):
            widerruf = {**widerruf, "deleted_at": timezone.now()}
        Client.objects.filter(pk=person.pk).update(**widerruf)
        spuren = _spuren()

        with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
            create_case(facility, staff_user, person, "Widerrufen")

        assert excinfo.value.messages == [CLIENT_WRITE_REVOKED_MESSAGE]
        assert _spuren() == spuren
        assert m.sperrfolge() == ["core_client"]
        assert not Case.objects.filter(client_id=client_identified.pk).exists()

    def test_eigene_einrichtung_legt_an(self, facility, client_identified, staff_user):
        fall = create_case(facility, staff_user, Client.objects.get(pk=client_identified.pk), "Neu")

        assert Case.objects.get(pk=fall.pk).title == "Neu"
        assert AuditLog.objects.filter(action=AuditLog.Action.CASE_CREATE, target_id=str(fall.pk)).count() == 1


class TestCloseCase:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, case_open):
        user = request.getfixturevalue(akteur)

        _abgewiesen_ohne_sperre(lambda: close_case(Case.objects.get(pk=case_open.pk), user))

        frisch = Case.objects.get(pk=case_open.pk)
        assert (frisch.status, frisch.closed_at) == (Case.Status.OPEN, None)
        assert not AuditLog.objects.filter(action=AuditLog.Action.CASE_CLOSE, target_id=str(case_open.pk)).exists()

    def test_eigene_einrichtung_schliesst(self, case_open, staff_user):
        close_case(Case.objects.get(pk=case_open.pk), staff_user)

        assert Case.objects.get(pk=case_open.pk).status == Case.Status.CLOSED
        assert AuditLog.objects.filter(action=AuditLog.Action.CASE_CLOSE, target_id=str(case_open.pk)).count() == 1


class TestReopenCase:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, case_closed):
        user = request.getfixturevalue(akteur)
        vorher = Case.objects.values_list("status", "closed_at").get(pk=case_closed.pk)

        _abgewiesen_ohne_sperre(lambda: reopen_case(Case.objects.get(pk=case_closed.pk), user))

        assert Case.objects.values_list("status", "closed_at").get(pk=case_closed.pk) == vorher
        assert not AuditLog.objects.filter(action=AuditLog.Action.CASE_REOPEN, target_id=str(case_closed.pk)).exists()

    def test_eigene_einrichtung_eroeffnet_wieder(self, case_closed, staff_user):
        reopen_case(Case.objects.get(pk=case_closed.pk), staff_user)

        assert Case.objects.get(pk=case_closed.pk).status == Case.Status.OPEN
        assert AuditLog.objects.filter(action=AuditLog.Action.CASE_REOPEN, target_id=str(case_closed.pk)).count() == 1


class TestCreateGoal:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, case_open):
        user = request.getfixturevalue(akteur)

        _abgewiesen_ohne_sperre(lambda: create_goal(Case.objects.get(pk=case_open.pk), user, "Fremd"))

        assert not OutcomeGoal.objects.filter(case_id=case_open.pk).exists()

    def test_eigene_einrichtung_legt_an(self, case_open, staff_user):
        ziel = create_goal(Case.objects.get(pk=case_open.pk), staff_user, "Neu")

        assert OutcomeGoal.objects.get(pk=ziel.pk).title == "Neu"


class TestUpdateGoal:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, outcome_goal):
        user = request.getfixturevalue(akteur)
        vorher = OutcomeGoal.objects.values_list("title", "description").get(pk=outcome_goal.pk)

        _abgewiesen_ohne_sperre(
            lambda: update_goal(OutcomeGoal.objects.get(pk=outcome_goal.pk), user, title="Fremd", description="Fremd")
        )

        assert OutcomeGoal.objects.values_list("title", "description").get(pk=outcome_goal.pk) == vorher
        assert not AuditLog.objects.filter(action=AuditLog.Action.GOAL_UPDATE, target_id=str(outcome_goal.pk)).exists()

    def test_eigene_einrichtung_schreibt(self, outcome_goal, staff_user):
        update_goal(OutcomeGoal.objects.get(pk=outcome_goal.pk), staff_user, title="Neu")

        assert OutcomeGoal.objects.get(pk=outcome_goal.pk).title == "Neu"
        assert AuditLog.objects.filter(action=AuditLog.Action.GOAL_UPDATE, target_id=str(outcome_goal.pk)).count() == 1


_PERSON, _FALL, _ZIEL, _MEILENSTEIN = "core_client", "core_case", "core_outcomegoal", "core_milestone"


def _fehlend_abgewiesen(aufruf):
    """Das Objekt fehlt schon beim Vorlesen: Absage des Zustandsvertrags, keine Sperre, keine Spur."""
    spuren = _spuren()
    with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
        aufruf()
    assert excinfo.value.messages == [STATUS_CHANGE_GONE_MESSAGE]
    assert _spuren() == spuren
    assert m.sperrfolge() == []


def _zielstatus(goal_id):
    return OutcomeGoal.objects.values_list("is_achieved", "achieved_at").get(pk=goal_id)


def _ziel_mit_status(goal_id, erreicht):
    OutcomeGoal.objects.filter(pk=goal_id).update(
        is_achieved=erreicht, achieved_at=timezone.localdate() if erreicht else None
    )
    return OutcomeGoal.objects.get(pk=goal_id)


#: (Dienst, Ausgangsstatus ``is_achieved``)
_ZIELSTATUSWECHSEL = [
    pytest.param(achieve_goal, False, id="erreichen"),
    pytest.param(unachieve_goal, True, id="zuruecknehmen"),
]


class TestGoalStatus:
    """Erreichen und Zuruecknehmen eines Wirkungsziels."""

    @pytest.mark.parametrize(("dienst", "erreicht"), _ZIELSTATUSWECHSEL)
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, dienst, erreicht, outcome_goal):
        user = request.getfixturevalue(akteur)
        ziel = _ziel_mit_status(outcome_goal.pk, erreicht)
        vorher = _zielstatus(outcome_goal.pk)

        _abgewiesen_ohne_sperre(lambda: dienst(ziel, user))

        assert _zielstatus(outcome_goal.pk) == vorher

    @pytest.mark.parametrize(("dienst", "erreicht"), _ZIELSTATUSWECHSEL)
    @pytest.mark.parametrize("akteur", [*_AKTEURE, "staff_user"])
    def test_fehlendes_ziel_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, dienst, erreicht, outcome_goal):
        user = request.getfixturevalue(akteur)
        ziel = _ziel_mit_status(outcome_goal.pk, erreicht)
        OutcomeGoal.objects.filter(pk=outcome_goal.pk).delete()

        _fehlend_abgewiesen(lambda: dienst(ziel, user))

        assert not OutcomeGoal.objects.filter(pk=outcome_goal.pk).exists()

    @pytest.mark.parametrize(("dienst", "erreicht"), _ZIELSTATUSWECHSEL)
    def test_eigene_einrichtung_wechselt_den_status(self, dienst, erreicht, outcome_goal, staff_user):
        ziel = _ziel_mit_status(outcome_goal.pk, erreicht)

        with mitschnitt() as m:
            dienst(ziel, staff_user)

        assert m.sperrfolge()[:3] == [_PERSON, _FALL, _ZIEL]
        assert _zielstatus(outcome_goal.pk)[0] is (not erreicht)


def _meilensteinstatus(milestone_id):
    return Milestone.objects.values_list("is_completed", "completed_at").get(pk=milestone_id)


class TestToggleMilestone:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, milestone):
        user = request.getfixturevalue(akteur)
        vorher = _meilensteinstatus(milestone.pk)

        _abgewiesen_ohne_sperre(lambda: toggle_milestone(Milestone.objects.get(pk=milestone.pk), user))

        assert _meilensteinstatus(milestone.pk) == vorher

    @pytest.mark.parametrize("akteur", [*_AKTEURE, "staff_user"])
    def test_fehlender_meilenstein_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, milestone):
        user = request.getfixturevalue(akteur)
        alt = Milestone.objects.get(pk=milestone.pk)
        Milestone.objects.filter(pk=milestone.pk).delete()

        _fehlend_abgewiesen(lambda: toggle_milestone(alt, user))

        assert not Milestone.objects.filter(pk=milestone.pk).exists()

    def test_eigene_einrichtung_schaltet_um(self, milestone, staff_user):
        with mitschnitt() as m:
            toggle_milestone(Milestone.objects.get(pk=milestone.pk), staff_user)

        assert m.sperrfolge()[:4] == [_PERSON, _FALL, _ZIEL, _MEILENSTEIN]
        assert _meilensteinstatus(milestone.pk) == (True, timezone.localdate())
