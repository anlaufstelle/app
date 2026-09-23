"""Zustandsvertrag fuer Anlage und Bearbeitung von Wirkungszielen (Refs #2329).

Die Statuswechsel der Ziele pruefen seit Refs #2295 den Zustand von Person und
Fall unter der kanonischen Sperrfolge. ``create_goal`` und ``update_goal``
nahmen dagegen nur das Art.-18-Gate: ein Ziel liess sich an einer Fallakte im
Papierkorb oder an einer bereits getilgten Akte neu anlegen, und ``update_goal``
schrieb Freitext in ein Ziel, dessen Text die Tilgung
(``redact_case_goals``) gerade ersetzt hatte. Beides ist Inhalt an einer Akte,
die es fachlich nicht mehr gibt.

Getilgt wird ueber die ECHTEN Services; die „alte" Instanz ist eine zweite
Instanz derselben Zeile von vor dem Fenster.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import Activity, AuditLog, Case, Client
from core.models.outcome import OutcomeGoal
from core.services.case import anonymize_case_now, create_goal, soft_delete_case, update_goal

_ZIEL_MARKER = "Wirkungsziel (anonymisiert)"


def _spuren():
    return AuditLog.objects.count(), Activity.objects.count(), OutcomeGoal.objects.count()


def _tilge_fall(case, staff_user, admin_user):
    soft_delete_case(Case.objects.get(pk=case.pk), staff_user)
    anonymize_case_now(Case.objects.get(pk=case.pk), admin_user)


@pytest.mark.django_db
class TestZielanlageAnGetilgterAkte:
    def test_create_goal_an_fall_im_papierkorb(self, case_open, staff_user):
        alter_fall = Case.objects.get(pk=case_open.pk)
        soft_delete_case(case_open, staff_user)
        spuren = _spuren()

        with pytest.raises(ValidationError):
            create_goal(alter_fall, staff_user, "Neues Ziel", "Freitext")

        assert _spuren() == spuren
        assert not OutcomeGoal.objects.filter(case_id=case_open.pk).exists()

    def test_create_goal_an_getilgtem_fall(self, case_open, staff_user, admin_user):
        alter_fall = Case.objects.get(pk=case_open.pk)
        _tilge_fall(case_open, staff_user, admin_user)
        spuren = _spuren()

        with pytest.raises(ValidationError):
            create_goal(alter_fall, staff_user, "Neues Ziel", "Freitext")

        assert _spuren() == spuren

    def test_create_goal_an_akte_einer_person_im_papierkorb(self, case_open, client_identified, staff_user, admin_user):
        from core.services.client import approve_client_deletion, request_client_deletion

        alter_fall = Case.objects.get(pk=case_open.pk)
        antrag = request_client_deletion(client_identified, staff_user, reason="Widerruf")
        approve_client_deletion(antrag, admin_user)
        spuren = _spuren()

        with pytest.raises(ValidationError):
            create_goal(alter_fall, staff_user, "Neues Ziel")

        assert _spuren() == spuren

    def test_create_goal_bei_eingeschraenkter_verarbeitung(self, case_open, client_identified, staff_user):
        """Art. 18: bestehende Grenze, fuer die Zustandstabelle mitgefahren."""
        Client.objects.filter(pk=client_identified.pk).update(processing_restricted=True)
        spuren = _spuren()

        with pytest.raises(ValidationError):
            create_goal(case_open, staff_user, "Neues Ziel")

        assert _spuren() == spuren

    def test_create_goal_an_lebender_akte_bleibt_moeglich(self, case_open, staff_user):
        goal = create_goal(Case.objects.get(pk=case_open.pk), staff_user, "Neues Ziel", "Freitext")

        frisch = OutcomeGoal.objects.get(pk=goal.pk)
        assert frisch.case_id == case_open.pk
        assert frisch.title == "Neues Ziel"
        assert frisch.description == "Freitext"
        assert frisch.created_by == staff_user


@pytest.mark.django_db
class TestZielbearbeitungAnGetilgterAkte:
    def test_update_goal_schreibt_keinen_freitext_in_getilgtes_ziel(
        self, outcome_goal, case_open, staff_user, admin_user
    ):
        alte_instanz = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        _tilge_fall(case_open, staff_user, admin_user)
        assert OutcomeGoal.objects.get(pk=outcome_goal.pk).title == _ZIEL_MARKER, "Aufbau kaputt: Ziel nicht getilgt"
        spuren = _spuren()

        with pytest.raises(ValidationError):
            update_goal(alte_instanz, staff_user, title="Neuer Freitext", description="Neue Beschreibung")

        frisch = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        assert frisch.title == _ZIEL_MARKER
        assert frisch.description == ""
        assert _spuren() == spuren

    def test_update_goal_an_fall_im_papierkorb(self, outcome_goal, case_open, staff_user):
        alte_instanz = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        soft_delete_case(Case.objects.get(pk=case_open.pk), staff_user)
        spuren = _spuren()

        with pytest.raises(ValidationError):
            update_goal(alte_instanz, staff_user, title="Neuer Freitext")

        assert OutcomeGoal.objects.get(pk=outcome_goal.pk).title == "Stabile Wohnsituation"
        assert _spuren() == spuren

    def test_update_goal_an_akte_einer_person_im_papierkorb(
        self, outcome_goal, client_identified, staff_user, admin_user
    ):
        from core.services.client import approve_client_deletion, request_client_deletion

        alte_instanz = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        antrag = request_client_deletion(client_identified, staff_user, reason="Widerruf")
        approve_client_deletion(antrag, admin_user)
        spuren = _spuren()

        with pytest.raises(ValidationError):
            update_goal(alte_instanz, staff_user, description="Neue Beschreibung")

        assert OutcomeGoal.objects.get(pk=outcome_goal.pk).description == ""
        assert _spuren() == spuren

    def test_update_goal_an_lebender_akte_schreibt_nur_die_uebergebenen_spalten(self, outcome_goal, staff_user):
        alte_instanz = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        OutcomeGoal.objects.filter(pk=outcome_goal.pk).update(description="Parallel ergaenzt")

        update_goal(alte_instanz, staff_user, title="Neuer Titel")

        frisch = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        assert frisch.title == "Neuer Titel"
        assert frisch.description == "Parallel ergaenzt"
        assert AuditLog.objects.filter(action=AuditLog.Action.GOAL_UPDATE, target_id=str(outcome_goal.pk)).count() == 1
