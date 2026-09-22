"""Zustandsvertrag der Ziel-/Meilensteinstatus-Wechsel (Refs #2295).

``achieve_goal``, ``unachieve_goal`` und ``toggle_milestone`` bekommen ihre
Instanz von der View und speichern sie anschliessend vollstaendig. Laeuft
dazwischen :func:`~core.services.case.deletion.redact_case_goals` — der
gemeinsame Kaskadenschritt der Fall- UND der Personen-Tilgung —, dann schreibt
der Statuswechsel die bereits getilgten Freitexte (``OutcomeGoal.title``,
``OutcomeGoal.description``, ``Milestone.title``) wieder in die Datenbank. Die
Personensperre des Art.-18-Gates schuetzt sie nicht: sie sperrt die Person und
laedt das Ziel nicht neu.

Zwei Bauformen:

* Die Instanz-Guards stellen das Fenster mit zwei Instanzen derselben Zeile
  nach. Der Fehler ist ein Schreibfehler, keine Renn-Bedingung — er braucht
  keinen Nebenlauf, um einzutreten.
* ``TestRendezvousRedaktionGegenStatuswechsel`` faehrt beide relevanten
  Reihenfolgen auf ZWEI echten PostgreSQL-Verbindungen (Muster
  ``test_client_lock_order``): beobachtend, mit Barrieren, Lock-Zeitgrenze und
  Join-Deadline — kein ``sleep`` ohne Ende und kein ``assert`` im gesperrten
  Fenster.
"""

import threading
import time

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.utils import timezone

from core.models import Case, Client, Facility, Organization, User
from core.models.outcome import Milestone, OutcomeGoal
from core.services.case import (
    achieve_goal,
    anonymize_case_now,
    redact_case_goals,
    soft_delete_case,
    toggle_milestone,
    unachieve_goal,
    update_goal,
)

#: Lock-Zeitgrenze der Renn-Verbindungen: keine Seite wartet unbegrenzt.
_LOCK_TIMEOUT = "20s"
#: Deadline fuer Barrieren und ``join``.
_JOIN_TIMEOUT = 60
#: Deadline der Beobachtung „Faden haengt an einer Sperre".
_WAIT_DEADLINE = 30

#: Die Marker, die ``redact_case_goals`` hinterlaesst.
_ZIEL_MARKER = "Wirkungsziel (anonymisiert)"
_MEILENSTEIN_MARKER = "Meilenstein (anonymisiert)"


def _zweite_instanz(obj):
    """Die ZWEITE Instanz derselben Zeile — der Stand von vor dem Fenster."""
    return type(obj).objects.get(pk=obj.pk)


@pytest.mark.django_db
class TestZielstatusSchreibtRedigiertesNichtZurueck:
    """Nach einer Redaktion darf kein Statuswechsel den Freitext zurueckholen."""

    def test_achieve_goal_schreibt_den_redigierten_titel_nicht_zurueck(self, outcome_goal):
        alte_instanz = _zweite_instanz(outcome_goal)
        alter_titel = alte_instanz.title
        redact_case_goals([outcome_goal.case_id])

        achieve_goal(alte_instanz)

        frisch = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        assert frisch.title == _ZIEL_MARKER, f"alter Titel zurueckgeschrieben: {frisch.title!r}"
        assert frisch.description == ""
        assert alter_titel not in frisch.title
        assert frisch.is_achieved is True
        assert frisch.achieved_at == timezone.localdate()

    def test_unachieve_goal_schreibt_den_redigierten_titel_nicht_zurueck(self, outcome_goal):
        achieve_goal(outcome_goal)
        alte_instanz = _zweite_instanz(outcome_goal)
        redact_case_goals([outcome_goal.case_id])

        unachieve_goal(alte_instanz)

        frisch = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        assert frisch.title == _ZIEL_MARKER, f"alter Titel zurueckgeschrieben: {frisch.title!r}"
        assert frisch.description == ""
        assert frisch.is_achieved is False
        assert frisch.achieved_at is None

    def test_toggle_milestone_schreibt_den_redigierten_titel_nicht_zurueck(self, milestone):
        alte_instanz = _zweite_instanz(milestone)
        redact_case_goals([milestone.goal.case_id])

        toggle_milestone(alte_instanz)

        frisch = Milestone.objects.get(pk=milestone.pk)
        assert frisch.title == _MEILENSTEIN_MARKER, f"alter Titel zurueckgeschrieben: {frisch.title!r}"
        assert frisch.is_completed is True
        assert frisch.completed_at == timezone.localdate()

    def test_toggle_milestone_haelt_auch_den_zieltitel_redigiert(self, milestone):
        """Der Meilenstein-Pfad laeuft ueber ``milestone.goal`` — auch dessen
        Zeile darf er nicht anfassen."""
        alte_instanz = _zweite_instanz(milestone)
        redact_case_goals([milestone.goal.case_id])

        toggle_milestone(alte_instanz)

        assert OutcomeGoal.objects.values_list("title", flat=True).get(pk=milestone.goal_id) == _ZIEL_MARKER


@pytest.mark.django_db
class TestZielstatusUeberschreibtRegulaereAenderungenNicht:
    """Zwischenzeitliche REGULAERE Textaenderungen bleiben ebenso erhalten."""

    def test_achieve_goal_ueberschreibt_den_parallel_geaenderten_text_nicht(self, outcome_goal, staff_user):
        alte_instanz = _zweite_instanz(outcome_goal)
        update_goal(outcome_goal, staff_user, title="Parallel geschaerftes Ziel", description="Parallel ergaenzt")

        achieve_goal(alte_instanz)

        frisch = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        assert frisch.title == "Parallel geschaerftes Ziel"
        assert frisch.description == "Parallel ergaenzt"
        assert frisch.is_achieved is True

    def test_toggle_milestone_ueberschreibt_den_parallel_geaenderten_titel_nicht(self, milestone):
        alte_instanz = _zweite_instanz(milestone)
        Milestone.objects.filter(pk=milestone.pk).update(title="Parallel umbenannt")

        toggle_milestone(alte_instanz)

        frisch = Milestone.objects.get(pk=milestone.pk)
        assert frisch.title == "Parallel umbenannt"
        assert frisch.is_completed is True


@pytest.mark.django_db
class TestGetilgteFallakteLehntStatuswechselAb:
    """Ist die Fallakte getilgt, gibt es keinen Statuswechsel mehr."""

    def test_achieve_goal_an_getilgter_fallakte_wird_abgelehnt(self, outcome_goal, case_open, staff_user, admin_user):
        alte_instanz = _zweite_instanz(outcome_goal)
        soft_delete_case(case_open, staff_user)
        anonymize_case_now(case_open, admin_user)

        with pytest.raises(ValidationError):
            achieve_goal(alte_instanz)

        frisch = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        assert frisch.is_achieved is False
        assert frisch.achieved_at is None
        assert frisch.title == _ZIEL_MARKER

    def test_unachieve_goal_an_getilgter_fallakte_wird_abgelehnt(self, outcome_goal, case_open, staff_user, admin_user):
        achieve_goal(outcome_goal)
        alte_instanz = _zweite_instanz(outcome_goal)
        soft_delete_case(case_open, staff_user)
        anonymize_case_now(case_open, admin_user)

        with pytest.raises(ValidationError):
            unachieve_goal(alte_instanz)

        frisch = OutcomeGoal.objects.get(pk=outcome_goal.pk)
        assert frisch.is_achieved is True
        assert frisch.title == _ZIEL_MARKER

    def test_toggle_milestone_an_getilgter_fallakte_wird_abgelehnt(self, milestone, case_open, staff_user, admin_user):
        alte_instanz = _zweite_instanz(milestone)
        soft_delete_case(case_open, staff_user)
        anonymize_case_now(case_open, admin_user)

        with pytest.raises(ValidationError):
            toggle_milestone(alte_instanz)

        frisch = Milestone.objects.get(pk=milestone.pk)
        assert frisch.is_completed is False
        assert frisch.completed_at is None
        assert frisch.title == _MEILENSTEIN_MARKER

    def test_achieve_goal_am_papierkorb_fall_wird_abgelehnt(self, outcome_goal, case_open, staff_user):
        """Papierkorb ohne Tilgung — das Zustandsmerkmal allein reicht."""
        alte_instanz = _zweite_instanz(outcome_goal)
        soft_delete_case(case_open, staff_user)

        with pytest.raises(ValidationError):
            achieve_goal(alte_instanz)

        assert OutcomeGoal.objects.values_list("is_achieved", flat=True).get(pk=outcome_goal.pk) is False


# ---------------------------------------------------------------------------
# Rendezvous auf zwei echten Verbindungen
# ---------------------------------------------------------------------------


def _renn_bestand():
    """Committeter Aufbau — die Faeden bringen eigene Verbindungen mit."""
    org = Organization.objects.create(name="Zielstatus-Org")
    facility = Facility.objects.create(organization=org, name="Zielstatus-Stelle")
    staff = User.objects.create_user(
        username="zielstatus-staff", password="x" * 40, facility=facility, role=User.Role.STAFF, is_staff=True
    )
    person = Client.objects.create(facility=facility, pseudonym="Zielstatus-1", created_by=staff)
    case = Case.objects.create(
        facility=facility, client=person, title="Renn-Fall", status=Case.Status.OPEN, created_by=staff
    )
    goal = OutcomeGoal.objects.create(
        case=case, title="Renn-Ziel im Klartext", description="Klartext-Beschreibung", created_by=staff
    )
    return facility, staff, person, case, goal


@pytest.fixture
def renn_bestand():
    """Committeter Aufbau; das Aufraeumen macht der Flush von pytest-django."""
    yield _renn_bestand()
    connections.close_all()


def _backend_pid():
    with connection.cursor() as cur:
        cur.execute("SET lock_timeout = %s", [_LOCK_TIMEOUT])
        cur.execute("SELECT pg_backend_pid()")
        return cur.fetchone()[0]


def _wartet_auf_sperre(pid, deadline):
    """Wartet beobachtend, bis *pid* an einer Sperre haengt (oder die Zeit ab)."""
    ende = time.monotonic() + deadline
    while time.monotonic() < ende:
        with connections["default"].cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE pid = %s AND wait_event_type = 'Lock'",
                [pid],
            )
            if cur.fetchone()[0]:
                return True
        time.sleep(0.05)
    return False


@pytest.mark.django_db(transaction=True)
class TestRendezvousRedaktionGegenStatuswechsel:
    """Beide Reihenfolgen, zwei Verbindungen, ein Ergebnis: der Text bleibt getilgt.

    Reihenfolge A — die Redaktion committet, WAEHREND die Statusanfrage ihre
    Instanz schon gelesen hat und noch nicht geschrieben hat. Das ist der Fall
    aus dem Ticket.

    Reihenfolge B — die Statusanfrage schreibt zuerst und haelt ihre Sperren;
    die Redaktion laeuft ihr hinterher. Sie muss messbar warten (sonst haette
    der Statuswechsel die Zeile nie gesperrt) und danach durchkommen, ohne dass
    eine der beiden Seiten in einen Sperrzyklus (``40P01``) laeuft.
    """

    def test_a_redaktion_vor_dem_statuswechsel(self, renn_bestand):
        _facility, _staff, _person, case, goal = renn_bestand
        geladen = threading.Event()
        redigiert = threading.Event()
        ergebnis: dict[str, str] = {}

        def _statuswechsel():
            try:
                alte_instanz = OutcomeGoal.objects.get(pk=goal.pk)
                geladen.set()
                if not redigiert.wait(timeout=_JOIN_TIMEOUT):
                    raise AssertionError("Redaktions-Faden meldete seinen Commit nicht")
                achieve_goal(alte_instanz)
                ergebnis["status"] = "gewechselt"
            except ValidationError:
                ergebnis["status"] = "abgelehnt"
            except Exception as exc:  # noqa: BLE001
                ergebnis["status"] = f"{type(exc).__name__}: {exc}"
            finally:
                connections.close_all()

        def _redaktion():
            try:
                if not geladen.wait(timeout=_JOIN_TIMEOUT):
                    raise AssertionError("Status-Faden las seine Instanz nicht")
                with transaction.atomic():
                    redact_case_goals([case.pk])
                ergebnis["redaktion"] = "getilgt"
            except Exception as exc:  # noqa: BLE001
                ergebnis["redaktion"] = f"{type(exc).__name__}: {exc}"
            finally:
                redigiert.set()
                connections.close_all()

        faden_status = threading.Thread(target=_statuswechsel, name="zielstatus-status")
        faden_redaktion = threading.Thread(target=_redaktion, name="zielstatus-redaktion")
        faden_status.start()
        faden_redaktion.start()
        faden_status.join(timeout=_JOIN_TIMEOUT)
        faden_redaktion.join(timeout=_JOIN_TIMEOUT)

        assert not faden_status.is_alive() and not faden_redaktion.is_alive(), f"Verklemmung: {ergebnis}"
        assert ergebnis.get("redaktion") == "getilgt", ergebnis
        assert ergebnis.get("status") == "gewechselt", ergebnis

        frisch = OutcomeGoal.objects.get(pk=goal.pk)
        assert frisch.title == _ZIEL_MARKER, f"alter Titel zurueckgeschrieben: {frisch.title!r}"
        assert frisch.description == ""
        assert frisch.is_achieved is True

    def test_b_statuswechsel_haelt_die_sperre_vor_der_redaktion(self, renn_bestand):
        _facility, _staff, _person, case, goal = renn_bestand
        pid_box: dict[str, int] = {}
        status_geschrieben = threading.Event()
        redaktion_bereit = threading.Event()
        redaktion_wartet = threading.Event()
        beobachtet: dict[str, bool] = {}
        ergebnis: dict[str, str] = {}

        def _merke(name, exc):
            sqlstate = getattr(getattr(exc, "__cause__", None), "sqlstate", None)
            ergebnis[name] = f"deadlock:{sqlstate}" if sqlstate == "40P01" else f"{type(exc).__name__}: {exc}"

        def _statuswechsel():
            try:
                with transaction.atomic():
                    _backend_pid()
                    achieve_goal(OutcomeGoal.objects.get(pk=goal.pk))
                    status_geschrieben.set()
                    # Erst freigeben, wenn die Redaktion messbar wartet — ohne
                    # diesen Punkt entschiede die Maschinenlaune die Ordnung.
                    if not redaktion_wartet.wait(timeout=_JOIN_TIMEOUT):
                        raise AssertionError("Redaktions-Faden wartete nicht messbar auf die Zeilensperre")
                ergebnis["status"] = "gewechselt"
            except Exception as exc:  # noqa: BLE001
                _merke("status", exc)
            finally:
                connections.close_all()

        def _redaktion():
            try:
                pid_box["redaktion"] = _backend_pid()
                redaktion_bereit.set()
                if not status_geschrieben.wait(timeout=_JOIN_TIMEOUT):
                    raise AssertionError("Status-Faden schrieb nicht")
                with transaction.atomic():
                    redact_case_goals([case.pk])
                ergebnis["redaktion"] = "getilgt"
            except Exception as exc:  # noqa: BLE001
                _merke("redaktion", exc)
            finally:
                connections.close_all()

        faden_status = threading.Thread(target=_statuswechsel, name="zielstatus-status")
        faden_redaktion = threading.Thread(target=_redaktion, name="zielstatus-redaktion")
        faden_status.start()
        faden_redaktion.start()
        beobachtet["bereit"] = redaktion_bereit.wait(timeout=_JOIN_TIMEOUT)
        beobachtet["wartet"] = status_geschrieben.wait(timeout=_JOIN_TIMEOUT) and _wartet_auf_sperre(
            pid_box.get("redaktion", -1), _WAIT_DEADLINE
        )
        redaktion_wartet.set()
        faden_status.join(timeout=_JOIN_TIMEOUT)
        faden_redaktion.join(timeout=_JOIN_TIMEOUT)

        assert not faden_status.is_alive() and not faden_redaktion.is_alive(), f"Verklemmung: {ergebnis}"
        assert beobachtet == {"bereit": True, "wartet": True}, f"Rendezvous nicht erreicht: {beobachtet} / {ergebnis}"
        deadlocks = [name for name, wert in ergebnis.items() if wert.startswith("deadlock")]
        assert not deadlocks, f"Sperrzyklus zwischen Statuswechsel und Redaktion: {ergebnis}"
        assert ergebnis.get("status") == "gewechselt", ergebnis
        assert ergebnis.get("redaktion") == "getilgt", ergebnis

        frisch = OutcomeGoal.objects.get(pk=goal.pk)
        assert frisch.title == _ZIEL_MARKER, f"Redaktion verloren: {frisch.title!r}"
        assert frisch.is_achieved is True, "Statuswechsel verloren"
