"""Zustandsvertrag der Ereignis-Fallzuordnung (Refs #2329).

``assign_event_to_case``/``remove_event_from_case`` bekommen ihre
Ereignisinstanz von der View, die sie VOR dem Klick gelesen hat, und aendern
an ihr nur die Fallzuordnung. Gespeichert wurde aber die ganze Zeile: faellt
in das Fenster dazwischen eine Tilgung (``_redact_live_events`` — der
Ereignisschritt der Personen-Anonymisierung), ein Papierkorb-Schritt
(``soft_delete_event``) oder eine regulaere Bearbeitung (``update_event``),
dann schreibt der volle Save ``data_json`` und ``search_text`` von vorher
zurueck. Die Zuordnung ist damit derselbe Schreibfehler wie die Fall- und
Zielstatus-Services vor ihrem Zustandsvertrag.

Die Guards stellen das Fenster deterministisch nach: zwei Instanzen derselben
Zeile, die zweite ist die „alte". Kein Nebenlauf, kein Timing — der Fehler ist
ein Schreibfehler, keine Renn-Bedingung.

Getilgt wird ueber die ECHTEN Services, nicht ueber ``queryset.update()``:
geprueft werden soll der Zustand, den der Produktivpfad tatsaechlich
hinterlaesst.
"""

import re

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import Activity, AuditLog, Case, Client, Event
from core.services.case import assign_event_to_case, create_case, remove_event_from_case, soft_delete_case
from core.services.client.main import _redact_live_events, anonymize_client
from core.services.events.crud import soft_delete_event, update_event

_UPDATE_EVENT_RE = re.compile(r'^UPDATE "core_event" SET (?P<set>.+?) WHERE ', re.DOTALL)


def _alte_instanz(event):
    """Die ZWEITE Instanz derselben Zeile — der Stand von vor dem Fenster."""
    return Event.objects.get(pk=event.pk)


def _zeilen_stand(event_pk):
    return Event.objects.values("data_json", "search_text", "case_id", "is_deleted", "updated_at").get(pk=event_pk)


def _spuren():
    """Audit- und Zeitstrom-Zeilen — eine Ablehnung hinterlaesst keine davon."""
    return AuditLog.objects.count(), Activity.objects.count()


def _event_updates(captured):
    return [q["sql"] for q in captured.captured_queries if q["sql"].startswith('UPDATE "core_event"')]


def _gesetzte_spalten(sql):
    match = _UPDATE_EVENT_RE.match(sql)
    assert match, f"unerwartete UPDATE-Form: {sql}"
    return sorted(re.findall(r'"(\w+)" = ', match.group("set")))


@pytest.fixture
def zweiter_fall(facility, staff_user, client_identified):
    return create_case(facility=facility, user=staff_user, client=client_identified, title="Zweiter Fall")


@pytest.mark.django_db
class TestZuordnungSchreibtGetilgtesNichtZurueck:
    """Die Tilgung der Ereignisinhalte uebersteht jede Zuordnungsaenderung."""

    def test_assign_schreibt_redigierte_inhalte_nicht_zurueck(self, case_open, sample_event, staff_user):
        alte_instanz = _alte_instanz(sample_event)
        assert alte_instanz.data_json, "Aufbau kaputt: das Ereignis hat keinen Inhalt"
        _redact_live_events(sample_event.client)

        assign_event_to_case(case_open, alte_instanz, staff_user)

        frisch = _zeilen_stand(sample_event.pk)
        assert frisch["data_json"] == {}, f"getilgter Inhalt zurueckgeschrieben: {frisch['data_json']!r}"
        assert frisch["search_text"] == ""
        assert frisch["case_id"] == case_open.pk

    def test_remove_schreibt_redigierte_inhalte_nicht_zurueck(self, case_open, sample_event, staff_user):
        assign_event_to_case(case_open, sample_event, staff_user)
        alte_instanz = _alte_instanz(sample_event)
        _redact_live_events(sample_event.client)

        remove_event_from_case(alte_instanz, staff_user)

        frisch = _zeilen_stand(sample_event.pk)
        assert frisch["data_json"] == {}, f"getilgter Inhalt zurueckgeschrieben: {frisch['data_json']!r}"
        assert frisch["search_text"] == ""
        assert frisch["case_id"] is None


@pytest.mark.django_db
class TestZuordnungAnGetilgterAkteWirdAbgelehnt:
    """Laut Zustandsvertrag gesperrte oder getilgte Akten: kein UPDATE, kein Audit, kein Zeitstrom."""

    def _abgelehnt(self, aufruf, event_pk):
        vorher = _zeilen_stand(event_pk)
        spuren = _spuren()
        with CaptureQueriesContext(connection) as captured, pytest.raises(ValidationError):
            aufruf()
        assert _event_updates(captured) == [], "abgelehnte Zuordnung hat die Ereigniszeile geschrieben"
        assert _zeilen_stand(event_pk) == vorher
        assert _spuren() == spuren, "abgelehnte Zuordnung hat Audit-/Zeitstrom-Zeilen geschrieben"

    def test_assign_nach_anonymisierung_der_person(self, case_open, sample_event, staff_user, admin_user):
        alte_instanz = _alte_instanz(sample_event)
        alter_fall = Case.objects.get(pk=case_open.pk)
        anonymize_client(Client.objects.get(pk=sample_event.client_id), admin_user)

        self._abgelehnt(lambda: assign_event_to_case(alter_fall, alte_instanz, staff_user), sample_event.pk)
        assert _zeilen_stand(sample_event.pk)["data_json"] == {}

    def test_remove_nach_anonymisierung_der_person(self, zweiter_fall, sample_event, staff_user, admin_user):
        """Die Personen-Tilgung loest die Faelle nicht vom Ereignis — die alte
        Instanz haengt also noch an einem Fall, der Vertrag muss greifen."""
        assign_event_to_case(zweiter_fall, sample_event, staff_user)
        alte_instanz = _alte_instanz(sample_event)
        anonymize_client(Client.objects.get(pk=sample_event.client_id), admin_user)

        self._abgelehnt(lambda: remove_event_from_case(alte_instanz, staff_user), sample_event.pk)

    def test_assign_an_person_im_papierkorb(self, case_open, sample_event, staff_user, admin_user):
        from core.services.client import approve_client_deletion, request_client_deletion

        alte_instanz = _alte_instanz(sample_event)
        antrag = request_client_deletion(sample_event.client, staff_user, reason="Widerruf")
        approve_client_deletion(antrag, admin_user)
        assert Client.objects.get(pk=sample_event.client_id).is_deleted, "Aufbau kaputt: Person nicht im Papierkorb"

        self._abgelehnt(lambda: assign_event_to_case(case_open, alte_instanz, staff_user), sample_event.pk)

    def test_assign_eines_ereignisses_im_papierkorb(self, case_open, sample_event, staff_user):
        alte_instanz = _alte_instanz(sample_event)
        soft_delete_event(sample_event, staff_user)
        assert _zeilen_stand(sample_event.pk)["is_deleted"], "Aufbau kaputt: Ereignis nicht im Papierkorb"

        self._abgelehnt(lambda: assign_event_to_case(case_open, alte_instanz, staff_user), sample_event.pk)
        assert _zeilen_stand(sample_event.pk)["data_json"] == {}

    def test_remove_eines_ereignisses_im_papierkorb(self, case_open, sample_event, staff_user):
        assign_event_to_case(case_open, sample_event, staff_user)
        alte_instanz = _alte_instanz(sample_event)
        soft_delete_event(_alte_instanz(sample_event), staff_user)

        self._abgelehnt(lambda: remove_event_from_case(alte_instanz, staff_user), sample_event.pk)

    def test_assign_an_fall_im_papierkorb(self, case_open, sample_event, staff_user):
        alter_fall = Case.objects.get(pk=case_open.pk)
        soft_delete_case(case_open, staff_user)

        self._abgelehnt(lambda: assign_event_to_case(alter_fall, sample_event, staff_user), sample_event.pk)

    def test_umhaengen_aus_fall_im_papierkorb(self, case_open, zweiter_fall, sample_event, staff_user):
        """Auch der ABGEBENDE Fall gehoert zum Vorgang: ein Ereignis aus einer
        Akte im Papierkorb herauszuloesen, aendert diese Akte."""
        assign_event_to_case(case_open, sample_event, staff_user)
        alte_instanz = _alte_instanz(sample_event)
        soft_delete_case(Case.objects.get(pk=case_open.pk), staff_user)

        self._abgelehnt(lambda: assign_event_to_case(zweiter_fall, alte_instanz, staff_user), sample_event.pk)

    def test_remove_aus_fall_im_papierkorb(self, case_open, sample_event, staff_user):
        assign_event_to_case(case_open, sample_event, staff_user)
        alte_instanz = _alte_instanz(sample_event)
        soft_delete_case(Case.objects.get(pk=case_open.pk), staff_user)

        self._abgelehnt(lambda: remove_event_from_case(alte_instanz, staff_user), sample_event.pk)

    def test_assign_an_getilgten_fall(self, case_open, sample_event, staff_user, admin_user):
        from core.services.case import anonymize_case_now

        alter_fall = Case.objects.get(pk=case_open.pk)
        soft_delete_case(case_open, staff_user)
        anonymize_case_now(Case.objects.get(pk=case_open.pk), admin_user)

        self._abgelehnt(lambda: assign_event_to_case(alter_fall, sample_event, staff_user), sample_event.pk)

    def test_assign_bei_eingeschraenkter_verarbeitung(self, case_open, sample_event, staff_user):
        """Art. 18: bestehende Grenze, hier nur fuer die Zustandstabelle mitgefahren."""
        Client.objects.filter(pk=sample_event.client_id).update(processing_restricted=True)

        self._abgelehnt(lambda: assign_event_to_case(case_open, sample_event, staff_user), sample_event.pk)


@pytest.mark.django_db
class TestZuordnungUeberschreibtParalleleInhalteNicht:
    """Eine Zuordnungsaenderung schreibt die Zuordnungsspalte — sonst nichts."""

    def test_assign_ueberschreibt_parallel_bearbeiteten_inhalt_nicht(self, case_open, sample_event, staff_user):
        alte_instanz = _alte_instanz(sample_event)
        update_event(sample_event, staff_user, {"dauer": 45, "notiz": "Parallel ergaenzt"})

        assign_event_to_case(case_open, alte_instanz, staff_user)

        frisch = Event.objects.get(pk=sample_event.pk)
        assert frisch.data_json.get("notiz") == "Parallel ergaenzt"
        assert frisch.data_json.get("dauer") == 45
        assert frisch.case_id == case_open.pk

    def test_remove_ueberschreibt_parallel_bearbeiteten_inhalt_nicht(self, case_open, sample_event, staff_user):
        assign_event_to_case(case_open, sample_event, staff_user)
        alte_instanz = _alte_instanz(sample_event)
        update_event(_alte_instanz(sample_event), staff_user, {"dauer": 45, "notiz": "Parallel ergaenzt"})

        remove_event_from_case(alte_instanz, staff_user)

        frisch = Event.objects.get(pk=sample_event.pk)
        assert frisch.data_json.get("notiz") == "Parallel ergaenzt"
        assert frisch.case_id is None

    def test_assign_schreibt_nur_zuordnung_und_zeitstempel(self, case_open, sample_event, staff_user):
        with CaptureQueriesContext(connection) as captured:
            assign_event_to_case(case_open, _alte_instanz(sample_event), staff_user)

        updates = _event_updates(captured)
        assert len(updates) == 1, updates
        assert _gesetzte_spalten(updates[0]) == ["case_id", "updated_at"]

    def test_remove_schreibt_nur_zuordnung_und_zeitstempel(self, case_open, sample_event, staff_user):
        assign_event_to_case(case_open, sample_event, staff_user)
        with CaptureQueriesContext(connection) as captured:
            remove_event_from_case(_alte_instanz(sample_event), staff_user)

        updates = _event_updates(captured)
        assert len(updates) == 1, updates
        assert _gesetzte_spalten(updates[0]) == ["case_id", "updated_at"]
