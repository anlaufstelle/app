"""Refs #2358: Anlage und Loeschung von Meilensteinen unter dem vollen Zustandsvertrag.

Die Statuswechsel am Meilenstein (``toggle_milestone``) laufen seit Refs
#2295 durch den Zustandsvertrag (Person -> Fall -> Ziel -> Meilenstein,
frisch und gesperrt). ``create_milestone`` und ``delete_milestone`` nahmen
dagegen nur das Art.-18-Gate: an einer Fallakte im Papierkorb, an einer
bereits getilgten Akte oder an der Akte einer Person im Papierkorb liess sich
noch ein Meilenstein-Freitext anlegen bzw. ein vorhandener loeschen — mit
Audit- und Zeitstrom-Zeile an einer Akte, die es fachlich nicht mehr gibt.

Service-Ebene: Ablehnung mit der Vertragsmeldung, keine Anlage/Loeschung,
keine Audit- und keine Zeitstrom-Zeile. View-Ebene: die HTMX-Views
antworten wie ``MilestoneToggleView`` mit 409 und dem Vertragstext statt
eines HTTP 500 — sowohl bei eingeschraenkter Verarbeitung (Art. 18, die
Suche laesst die Akte durch) als auch, wenn die Akte zwischen Suche und
Schreiben in den Papierkorb faellt (nachgestellt, indem der Service erst
nach dem Loeschen der Akte laeuft).

Getilgt wird ueber die ECHTEN Services; die „alte" Instanz ist eine zweite
Instanz derselben Zeile von vor dem Fenster.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import Activity, AuditLog, Case, Client
from core.models.outcome import Milestone, OutcomeGoal
from core.services.case import (
    anonymize_case_now,
    create_milestone,
    delete_milestone,
    soft_delete_case,
    toggle_milestone,
)
from core.services.case.state_contract import CASE_WRITE_REDACTED_MESSAGE, STATUS_CHANGE_REDACTED_MESSAGE
from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def _spuren():
    return AuditLog.objects.count(), Activity.objects.count(), Milestone.objects.count()


def _fall_im_papierkorb(case, staff_user, admin_user):
    soft_delete_case(Case.objects.get(pk=case.pk), staff_user)


def _fall_getilgt(case, staff_user, admin_user):
    soft_delete_case(Case.objects.get(pk=case.pk), staff_user)
    anonymize_case_now(Case.objects.get(pk=case.pk), admin_user)


def _person_im_papierkorb(case, staff_user, admin_user):
    from core.services.client import approve_client_deletion, request_client_deletion

    antrag = request_client_deletion(Client.objects.get(pk=case.client_id), staff_user, reason="Widerruf")
    approve_client_deletion(antrag, admin_user)


def _person_eingeschraenkt(case, staff_user, admin_user):
    Client.objects.filter(pk=case.client_id).update(processing_restricted=True)


#: (Zustand, Meldung fuer Anlage/Loeschung, Meldung fuer den Statuswechsel)
ZUSTAENDE = [
    pytest.param(
        _fall_im_papierkorb, CASE_WRITE_REDACTED_MESSAGE, STATUS_CHANGE_REDACTED_MESSAGE, id="fall-papierkorb"
    ),
    pytest.param(_fall_getilgt, CASE_WRITE_REDACTED_MESSAGE, STATUS_CHANGE_REDACTED_MESSAGE, id="fall-getilgt"),
    pytest.param(
        _person_im_papierkorb, CLIENT_WRITE_REVOKED_MESSAGE, CLIENT_WRITE_REVOKED_MESSAGE, id="person-papierkorb"
    ),
    pytest.param(
        _person_eingeschraenkt, PROCESSING_RESTRICTED_MESSAGE, PROCESSING_RESTRICTED_MESSAGE, id="person-art18"
    ),
]


def _meldung(excinfo):
    return excinfo.value.messages


class TestServiceZustandsvertrag:
    @pytest.mark.parametrize(("zustand", "meldung", "_status_meldung"), ZUSTAENDE)
    def test_create_milestone(self, zustand, meldung, _status_meldung, outcome_goal, case_open, staff_user, admin_user):
        altes_ziel = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        zustand(case_open, staff_user, admin_user)
        spuren = _spuren()

        with pytest.raises(ValidationError) as excinfo:
            create_milestone(altes_ziel, staff_user, "Neuer Meilenstein")

        assert _meldung(excinfo) == [str(meldung)]
        assert _spuren() == spuren
        assert not Milestone.objects.filter(goal_id=outcome_goal.pk).exists()

    @pytest.mark.parametrize(("zustand", "meldung", "_status_meldung"), ZUSTAENDE)
    def test_delete_milestone(self, zustand, meldung, _status_meldung, milestone, case_open, staff_user, admin_user):
        alter_meilenstein = Milestone.objects.get(pk=milestone.pk)
        zustand(case_open, staff_user, admin_user)
        spuren = _spuren()

        with pytest.raises(ValidationError) as excinfo:
            delete_milestone(alter_meilenstein, staff_user)

        assert _meldung(excinfo) == [str(meldung)]
        assert _spuren() == spuren
        assert Milestone.objects.filter(pk=milestone.pk).exists()

    @pytest.mark.parametrize(("zustand", "_meldung_schreiben", "status_meldung"), ZUSTAENDE)
    def test_toggle_milestone_unveraendert(
        self, zustand, _meldung_schreiben, status_meldung, milestone, case_open, staff_user, admin_user
    ):
        """Bestehende Grenze, fuer die Zustandstabelle mitgefahren."""
        alter_meilenstein = Milestone.objects.get(pk=milestone.pk)
        zustand(case_open, staff_user, admin_user)
        spuren = _spuren()

        with pytest.raises(ValidationError) as excinfo:
            toggle_milestone(alter_meilenstein, staff_user)

        assert _meldung(excinfo) == [str(status_meldung)]
        assert _spuren() == spuren


class TestServiceNormalpfad:
    def test_create_an_lebender_akte(self, outcome_goal, staff_user):
        m = create_milestone(OutcomeGoal.objects.get(pk=outcome_goal.pk), staff_user, "Neuer Meilenstein", sort_order=3)
        frisch = Milestone.objects.get(pk=m.pk)
        assert frisch.goal_id == outcome_goal.pk
        assert frisch.title == "Neuer Meilenstein"
        assert frisch.sort_order == 3
        assert frisch.is_completed is False

    def test_delete_an_lebender_akte(self, milestone, case_open, staff_user):
        delete_milestone(Milestone.objects.get(pk=milestone.pk), staff_user)
        assert not Milestone.objects.filter(pk=milestone.pk).exists()
        log = AuditLog.objects.get(action=AuditLog.Action.MILESTONE_DELETE)
        assert log.target_id == str(milestone.pk)
        assert log.detail == {"case_id": str(case_open.pk)}
        assert log.user == staff_user
        assert Activity.objects.filter(verb=Activity.Verb.DELETED).count() == 1

    def test_delete_eines_bereits_geloeschten_meilensteins(self, milestone, staff_user):
        alter_meilenstein = Milestone.objects.get(pk=milestone.pk)
        Milestone.objects.filter(pk=milestone.pk).delete()
        spuren = _spuren()
        with pytest.raises(ValidationError):
            delete_milestone(alter_meilenstein, staff_user)
        assert _spuren() == spuren


def _assert_absage(response, meldung):
    assert response.status_code == 409, response.status_code
    assert response.content.decode() == str(meldung)


class TestViewsGesperrtePerson:
    def test_anlage(self, client, staff_user, case_open, outcome_goal):
        Client.objects.filter(pk=case_open.client_id).update(processing_restricted=True)
        client.force_login(staff_user)
        spuren = _spuren()
        response = client.post(
            reverse("core:milestone_create", kwargs={"case_pk": case_open.pk, "goal_pk": outcome_goal.pk}),
            {"title": "Neu"},
            **HTMX,
        )
        _assert_absage(response, PROCESSING_RESTRICTED_MESSAGE)
        assert _spuren() == spuren

    def test_loeschung(self, client, staff_user, case_open, milestone):
        Client.objects.filter(pk=case_open.client_id).update(processing_restricted=True)
        client.force_login(staff_user)
        spuren = _spuren()
        response = client.post(
            reverse("core:milestone_delete", kwargs={"case_pk": case_open.pk, "pk": milestone.pk}), **HTMX
        )
        _assert_absage(response, PROCESSING_RESTRICTED_MESSAGE)
        assert Milestone.objects.filter(pk=milestone.pk).exists()
        assert _spuren() == spuren


class TestViewsAkteFaelltZwischenSucheUndSchreibenInDenPapierkorb:
    """Die View hat die Akte noch lebend gefunden; der Service sieht sie im Papierkorb."""

    def test_anlage(self, client, monkeypatch, staff_user, case_open, outcome_goal):
        from core.views import case_goals

        echt = case_goals.create_milestone

        def nach_dem_loeschen(**kwargs):
            soft_delete_case(Case.objects.get(pk=case_open.pk), staff_user)
            return echt(**kwargs)

        monkeypatch.setattr(case_goals, "create_milestone", nach_dem_loeschen)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:milestone_create", kwargs={"case_pk": case_open.pk, "goal_pk": outcome_goal.pk}),
            {"title": "Neu"},
            **HTMX,
        )
        _assert_absage(response, CASE_WRITE_REDACTED_MESSAGE)
        assert not Milestone.objects.filter(goal_id=outcome_goal.pk).exists()

    def test_loeschung(self, client, monkeypatch, staff_user, case_open, milestone):
        from core.views import case_goals

        echt = case_goals.delete_milestone

        def nach_dem_loeschen(*args, **kwargs):
            soft_delete_case(Case.objects.get(pk=case_open.pk), staff_user)
            return echt(*args, **kwargs)

        monkeypatch.setattr(case_goals, "delete_milestone", nach_dem_loeschen)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:milestone_delete", kwargs={"case_pk": case_open.pk, "pk": milestone.pk}), **HTMX
        )
        _assert_absage(response, CASE_WRITE_REDACTED_MESSAGE)
        assert Milestone.objects.filter(pk=milestone.pk).exists()


class TestViewsNormalpfad:
    def test_anlage_und_loeschung(self, client, staff_user, case_open, outcome_goal):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:milestone_create", kwargs={"case_pk": case_open.pk, "goal_pk": outcome_goal.pk}),
            {"title": "Neu"},
            **HTMX,
        )
        assert response.status_code == 200
        m = Milestone.objects.get(goal_id=outcome_goal.pk)
        response = client.post(reverse("core:milestone_delete", kwargs={"case_pk": case_open.pk, "pk": m.pk}), **HTMX)
        assert response.status_code == 200
        assert not Milestone.objects.filter(pk=m.pk).exists()


_FALL_FREMD = "Fall gehört zu einer anderen Einrichtung."


@pytest.fixture
def fremde_fachkraft(second_facility):
    from core.models import User

    return User.objects.create_user(
        username="fremdfachkraft-meilenstein", role=User.Role.LEAD, facility=second_facility, is_staff=True
    )


@pytest.fixture
def fachkraft_ohne_einrichtung():
    from core.models import User

    return User.objects.create_user(
        username="ohneeinrichtung-meilenstein", role=User.Role.LEAD, facility=None, is_staff=True
    )


class TestAkteurAusFremderEinrichtung:
    """Refs #2358 (Nachzug): Anlage und Loeschung pruefen die Einrichtung des Akteurs selbst.

    Die Views scopen den Fall bereits, in Produktion traegt RLS mit; der
    Service ist aber die Single Source of Truth (Muster der Fallzuordnung).
    Fail-closed: ein Akteur ohne Einrichtung wird ebenso abgewiesen. Keine
    Anlage, keine Loeschung, keine Audit- und keine Zeitstrom-Zeile.
    """

    @pytest.mark.parametrize("akteur", ["fremde_fachkraft", "fachkraft_ohne_einrichtung"])
    def test_create_milestone(self, request, akteur, outcome_goal):
        user = request.getfixturevalue(akteur)
        spuren = _spuren()
        with pytest.raises(ValidationError) as excinfo:
            create_milestone(goal=OutcomeGoal.objects.get(pk=outcome_goal.pk), user=user, title="Fremd")
        assert _meldung(excinfo) == [_FALL_FREMD]
        assert _spuren() == spuren
        assert not Milestone.objects.filter(goal_id=outcome_goal.pk).exists()

    @pytest.mark.parametrize("akteur", ["fremde_fachkraft", "fachkraft_ohne_einrichtung"])
    def test_delete_milestone(self, request, akteur, milestone):
        user = request.getfixturevalue(akteur)
        spuren = _spuren()
        with pytest.raises(ValidationError) as excinfo:
            delete_milestone(Milestone.objects.get(pk=milestone.pk), user)
        assert _meldung(excinfo) == [_FALL_FREMD]
        assert _spuren() == spuren
        assert Milestone.objects.filter(pk=milestone.pk).exists()

    def test_eigene_einrichtung_legt_an(self, outcome_goal, staff_user):
        m = create_milestone(goal=OutcomeGoal.objects.get(pk=outcome_goal.pk), user=staff_user, title="Eigen")
        assert Milestone.objects.filter(pk=m.pk, goal_id=outcome_goal.pk).exists()
