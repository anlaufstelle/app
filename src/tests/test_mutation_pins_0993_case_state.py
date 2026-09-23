"""Pins fuer die Schreibvertraege an Fallakte, Ziel und Ereignis-Fallzuordnung (Refs #2329).

Die Guards (``test_case_event_assignment_redaction_guard``,
``test_goals_state_contract_guard``) belegen das Verhalten an den Services.
Diese Pins halten die Randzweige des Vertrags fest, die im Normalbetrieb nur
ein paralleler Schreiber ausloest: eine Zeile verschwindet oder wechselt
waehrend der Sperrwartezeit ihren Bezug. Das Fenster wird deterministisch
nachgestellt — der Wechsel passiert im Art.-18-Gate, also zwischen dem
ungesperrten Vorlesen und dem Sperren von Fall und Ereignis.
"""

import uuid

import pytest
from django.core.exceptions import ValidationError

from core.models import Case, Client, Event
from core.models.outcome import OutcomeGoal
from core.services.case import create_case
from core.services.case import state_contract as sc
from core.services.client import restriction


def _meldung(exc_info):
    return exc_info.value.messages[0]


def _wechsel_im_gate(monkeypatch, wechsel):
    """Fuehrt ``wechsel`` im Art.-18-Gate aus — nach dem Vorlesen, vor dem Sperren."""
    original = restriction.assert_processing_allowed_for

    def _gate(clients, *, lock=False):
        wechsel()
        return original(clients, lock=lock)

    monkeypatch.setattr(restriction, "assert_processing_allowed_for", _gate)


@pytest.fixture
def andere_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Andere-Person", created_by=staff_user)


@pytest.mark.django_db
class TestMeldungen:
    def test_wortlaut(self):
        assert str(sc.CASE_WRITE_REDACTED_MESSAGE) == (
            "Diese Fallakte wurde gelöscht oder anonymisiert — sie kann nicht mehr geändert werden."
        )
        assert str(sc.CASE_WRITE_GONE_MESSAGE) == (
            "Diese Fallakte hat sich inzwischen geändert — bitte die Seite neu laden."
        )
        assert str(sc.EVENT_WRITE_REDACTED_MESSAGE) == (
            "Dieses Ereignis wurde gelöscht — seine Fallzuordnung kann nicht mehr geändert werden."
        )
        assert str(sc.EVENT_WRITE_GONE_MESSAGE) == (
            "Dieses Ereignis hat sich inzwischen geändert — bitte die Seite neu laden."
        )


@pytest.mark.django_db
class TestLockCaseForWrite:
    def test_liefert_die_frische_gesperrte_zeile(self, case_open):
        Case.objects.filter(pk=case_open.pk).update(title="Frisch")
        locked = sc.lock_case_for_write(Case.objects.get(pk=case_open.pk))
        assert locked.title == "Frisch"
        assert locked is not case_open

    def test_fehlende_zeile(self, case_open):
        geist = Case(pk=uuid.uuid4(), facility_id=case_open.facility_id, client_id=case_open.client_id)
        with pytest.raises(ValidationError) as exc:
            sc.lock_case_for_write(geist)
        assert _meldung(exc) == str(sc.CASE_WRITE_GONE_MESSAGE)

    def test_papierkorb_meldet_die_schreibform(self, case_open):
        Case.objects.filter(pk=case_open.pk).update(is_deleted=True)
        with pytest.raises(ValidationError) as exc:
            sc.lock_case_for_write(case_open)
        assert _meldung(exc) == str(sc.CASE_WRITE_REDACTED_MESSAGE)

    def test_statuswechsel_behaelt_seine_meldung(self, case_open):
        """Die Statusform ist unveraendert — der neue Parameter hat einen Default."""
        Case.objects.filter(pk=case_open.pk).update(is_deleted=True)
        with pytest.raises(ValidationError) as exc:
            sc.lock_case_for_status_change(case_open)
        assert _meldung(exc) == str(sc.STATUS_CHANGE_REDACTED_MESSAGE)

    def test_statuswechsel_fehlende_zeile_behaelt_seine_meldung(self, case_open):
        geist = Case(pk=uuid.uuid4(), facility_id=case_open.facility_id, client_id=case_open.client_id)
        with pytest.raises(ValidationError) as exc:
            sc.lock_case_for_status_change(geist)
        assert _meldung(exc) == str(sc.STATUS_CHANGE_GONE_MESSAGE)


@pytest.mark.django_db
class TestLockGoalForWrite:
    def test_liefert_die_frische_gesperrte_zeile(self, outcome_goal):
        OutcomeGoal.objects.filter(pk=outcome_goal.pk).update(title="Frisch")
        assert sc.lock_goal_for_write(outcome_goal).title == "Frisch"

    def test_fehlende_zeile(self, outcome_goal):
        geist = OutcomeGoal(pk=uuid.uuid4(), case_id=outcome_goal.case_id)
        with pytest.raises(ValidationError) as exc:
            sc.lock_goal_for_write(geist)
        assert _meldung(exc) == str(sc.CASE_WRITE_GONE_MESSAGE)

    def test_getilgter_fall(self, outcome_goal):
        Case.objects.filter(pk=outcome_goal.case_id).update(is_deleted=True)
        with pytest.raises(ValidationError) as exc:
            sc.lock_goal_for_write(outcome_goal)
        assert _meldung(exc) == str(sc.CASE_WRITE_REDACTED_MESSAGE)

    def test_fall_verschwindet_waehrend_der_sperrwartezeit(self, outcome_goal, andere_person, monkeypatch):
        """Die Fallzeile wechselt nach dem Vorlesen die Person — Abbruch statt Nachsperren."""
        original = restriction.assert_processing_allowed

        def _gate(client, *, lock=False):
            Case.objects.filter(pk=outcome_goal.case_id).update(client=andere_person)
            return original(client, lock=lock)

        monkeypatch.setattr(restriction, "assert_processing_allowed", _gate)
        with pytest.raises(ValidationError) as exc:
            sc.lock_goal_for_write(outcome_goal)
        assert _meldung(exc) == str(sc.CASE_WRITE_GONE_MESSAGE)


@pytest.mark.django_db
class TestLockEventForCaseLink:
    def test_zuordnen_liefert_frische_zeilen(self, case_open, sample_event):
        Event.objects.filter(pk=sample_event.pk).update(data_json={"notiz": "frisch"})
        locked, target, previous = sc.lock_event_for_case_link(sample_event, case_open)
        assert locked.data_json == {"notiz": "frisch"}
        assert target.pk == case_open.pk
        assert previous is None

    def test_loesen_liefert_den_bisherigen_fall(self, case_open, sample_event):
        Event.objects.filter(pk=sample_event.pk).update(case=case_open)
        locked, target, previous = sc.lock_event_for_case_link(sample_event)
        assert locked.case_id == case_open.pk
        assert target is None
        assert previous.pk == case_open.pk

    def test_personenloses_ereignis_ohne_fall_braucht_keine_personensperre(self, sample_event):
        Event.objects.filter(pk=sample_event.pk).update(client=None, is_anonymous=True)
        locked, target, previous = sc.lock_event_for_case_link(sample_event)
        assert locked.pk == sample_event.pk
        assert target is None
        assert previous is None

    def test_fehlendes_ereignis(self, sample_event):
        geist = Event(pk=uuid.uuid4(), facility_id=sample_event.facility_id)
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(geist)
        assert _meldung(exc) == str(sc.EVENT_WRITE_GONE_MESSAGE)

    def test_fehlender_zielfall(self, case_open, sample_event):
        geist = Case(pk=uuid.uuid4(), facility_id=case_open.facility_id, client_id=case_open.client_id)
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event, geist)
        assert _meldung(exc) == str(sc.CASE_WRITE_GONE_MESSAGE)

    def test_anonymes_ereignis(self, case_open, sample_event):
        Event.objects.filter(pk=sample_event.pk).update(is_anonymous=True)
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event, case_open)
        assert "Anonyme Ereignisse" in _meldung(exc)

    def test_ereignis_ohne_person(self, case_open, sample_event):
        Event.objects.filter(pk=sample_event.pk).update(client=None)
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event, case_open)
        assert "Anonyme Ereignisse" in _meldung(exc)

    def test_andere_person(self, facility, staff_user, andere_person, sample_event):
        fremder_fall = create_case(facility=facility, user=staff_user, client=andere_person, title="Fremd")
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event, fremder_fall)
        assert _meldung(exc) == "Person des Ereignisses passt nicht zur Person des Falls."

    def test_fachliche_pruefung_vor_dem_widerruf(self, facility, staff_user, andere_person, sample_event):
        """„Andere Person" schlaegt den Widerruf der anderen Person — Reihenfolge gepinnt."""
        fremder_fall = create_case(facility=facility, user=staff_user, client=andere_person, title="Fremd")
        Client.objects.filter(pk=andere_person.pk).update(is_active=False)
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event, fremder_fall)
        assert _meldung(exc) == "Person des Ereignisses passt nicht zur Person des Falls."

    def test_widerrufene_person(self, case_open, sample_event):
        from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE

        Client.objects.filter(pk=sample_event.client_id).update(is_active=False)
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event, case_open)
        assert _meldung(exc) == str(CLIENT_WRITE_REVOKED_MESSAGE)

    def test_getilgter_zielfall(self, case_open, sample_event):
        from django.utils import timezone

        Case.objects.filter(pk=case_open.pk).update(anonymized_at=timezone.now())
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event, case_open)
        assert _meldung(exc) == str(sc.CASE_WRITE_REDACTED_MESSAGE)

    def test_ereignis_im_papierkorb(self, case_open, sample_event):
        Event.objects.filter(pk=sample_event.pk).update(is_deleted=True)
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event, case_open)
        assert _meldung(exc) == str(sc.EVENT_WRITE_REDACTED_MESSAGE)

    def test_bisheriger_fall_wechselt_die_person(self, case_open, sample_event, andere_person, monkeypatch):
        Event.objects.filter(pk=sample_event.pk).update(case=case_open)
        _wechsel_im_gate(monkeypatch, lambda: Case.objects.filter(pk=case_open.pk).update(client=andere_person))
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event)
        assert _meldung(exc) == str(sc.CASE_WRITE_GONE_MESSAGE)

    def test_ereignis_wird_waehrend_der_sperrwartezeit_umgehaengt(self, case_open, sample_event, monkeypatch):
        _wechsel_im_gate(monkeypatch, lambda: Event.objects.filter(pk=sample_event.pk).update(case=case_open))
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event, case_open)
        assert _meldung(exc) == str(sc.EVENT_WRITE_GONE_MESSAGE)

    def test_sperrt_personen_faelle_ereignis_in_dieser_reihenfolge(self, case_open, sample_event):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            sc.lock_event_for_case_link(sample_event, case_open)
        sperren = [q["sql"] for q in captured.captured_queries if "FOR UPDATE" in q["sql"]]
        gesperrt = [sql.split('FROM "', 1)[1].split('"', 1)[0] for sql in sperren]
        assert gesperrt == ["core_client", "core_case", "core_event"]
