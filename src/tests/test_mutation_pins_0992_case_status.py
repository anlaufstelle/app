"""Pin-Tests der Welle N (0.99.2) — Zustandsvertrag der Statuswechsel.

Quelle: die beiden Sicherheitskorrekturen
#2288 und
#2295. Gepinnt wird
hier, was die Guard-Tests (``test_case_status_redaction_guard``,
``test_goal_status_redaction_guard``) bewusst NICHT festhalten, weil sie das
Verhalten am Ergebnis messen und nicht an seiner Mechanik:

* **Der Wortlaut der Absagen und jeder Zweig fuer sich.** ``is_deleted`` und
  ``anonymized_at``, ``is_deleted`` und ``is_active`` der Person stehen je in
  einem ``or``; ein Mutant, der eine Haelfte streicht, bleibt am
  Gesamtergebnis unsichtbar, solange der Test nur den haeufigen Zustand
  herstellt. Verglichen wird EXAKT gegen das Literal (nie per Teilstring —
  mutmut erzeugt ``"XX<text>XX"``-Varianten) und bewusst nicht gegen die
  Konstante: ein Mutant an der Konstante wuerde die Erwartung mitmutieren.
* **Die Sperrreihenfolge am ausgefuehrten SQL** (Muster
  ``test_restriction_lock_order``): ``core_client`` -> ``core_case`` ->
  ``core_outcomegoal`` -> ``core_milestone``, in dieser Ordnung und
  vollstaendig. Faellt eine Stufe weg, bleibt der Vertrag inhaltlich richtig
  und wird zur Momentaufnahme.
* **Die geschriebenen Spalten** (``update_fields``). Ein Mutant, der die
  Liste weglaesst, schreibt wieder die ganze Zeile — genau der Befund.
* **Das Abfrage-Budget** der fuenf Pfade. Die Sperrkette kostet Abfragen;
  ohne Pin waechst sie beim naechsten Zusatz unbemerkt weiter (R-T18).
"""

import re

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Case, Client
from core.models.outcome import Milestone, OutcomeGoal
from core.services.case.cases import close_case, reopen_case
from core.services.case.goals import achieve_goal, toggle_milestone, unachieve_goal

#: Der ausgelieferte Wortlaut der drei Absagen — als Literal, nicht als Import.
_REDIGIERT = "Diese Fallakte wurde gelöscht oder anonymisiert — ihr Status kann nicht mehr geändert werden."
_VERSCHWUNDEN = "Diese Fallakte steht für eine Statusänderung nicht mehr zur Verfügung — bitte die Seite neu laden."
_PERSON_WIDERRUFEN = (
    "Diese Person wurde gelöscht oder anonymisiert — es können keine Daten mehr zu ihr gespeichert werden."
)

#: ``FOR UPDATE``-Statements verraten die Sperre; der Tabellenname steht im
#: ``FROM`` (gleiche Grob-Erkennung wie ``test_mutation_pins_097_case_rest``).
_LOCK_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)


def _gesperrte_tabellen(captured):
    """Die gesperrten Tabellen in Ausfuehrungsreihenfolge."""
    treffer = []
    for query in captured.captured_queries:
        match = _LOCK_RE.search(query["sql"])
        if match:
            treffer.append(match.group("table"))
    return treffer


def _fachliche_abfragen(captured):
    """Der Mitschnitt ohne die Savepoint-Klammern der Testtransaktion."""
    return [q["sql"] for q in captured.captured_queries if not q["sql"].startswith(("SAVEPOINT", "RELEASE SAVEPOINT"))]


def _meldung(fehler):
    """Die EINE Meldung einer ``ValidationError`` als Zeichenkette."""
    assert len(fehler.value.messages) == 1, fehler.value.messages
    return str(fehler.value.messages[0])


@pytest.mark.django_db
class TestWortlautUndZweigeDesZustandsvertrags:
    """Jeder Ablehnungsgrund einzeln — und mit seinem ausgelieferten Text."""

    def test_papierkorb_fall_nennt_die_redaktions_meldung(self, case_open, staff_user):
        """Pin: ``locked.is_deleted`` -> ``False`` bzw. der Wortlaut der Meldung."""
        Case.objects.filter(pk=case_open.pk).update(is_deleted=True, deleted_at=timezone.now())

        with pytest.raises(ValidationError) as fehler:
            close_case(case_open, staff_user)

        assert _meldung(fehler) == _REDIGIERT

    def test_anonymisierter_fall_ohne_papierkorb_wird_ebenso_abgewiesen(self, case_closed, staff_user):
        """Pin: die zweite Haelfte des ``or`` — ``anonymized_at is not None``.

        Der Zustand ist echt: die Personen-Anonymisierung
        (``_redact_cases_and_episodes``) stempelt die Faelle, OHNE sie in den
        Papierkorb zu legen. Hier per Queryset hergestellt, damit die Person
        dabei unberuehrt bleibt und wirklich nur dieser Zweig traegt.
        """
        Case.objects.filter(pk=case_closed.pk).update(anonymized_at=timezone.now())

        with pytest.raises(ValidationError) as fehler:
            reopen_case(case_closed, staff_user)

        assert _meldung(fehler) == _REDIGIERT

    def test_person_im_papierkorb_nennt_die_widerrufs_meldung(self, case_open, client_identified, staff_user):
        """Pin: ``is_deleted`` der Person -> ``False``."""
        Client.objects.filter(pk=client_identified.pk).update(is_deleted=True)

        with pytest.raises(ValidationError) as fehler:
            close_case(case_open, staff_user)

        assert _meldung(fehler) == _PERSON_WIDERRUFEN

    def test_anonymisierte_person_wird_ebenso_abgewiesen(self, case_open, client_identified, staff_user):
        """Pin: die zweite Haelfte — ``not is_active`` (K-Anon-Bridge laesst
        ``is_deleted=False``)."""
        Client.objects.filter(pk=client_identified.pk).update(is_active=False)

        with pytest.raises(ValidationError) as fehler:
            close_case(case_open, staff_user)

        assert _meldung(fehler) == _PERSON_WIDERRUFEN

    def test_verschwundene_fallzeile_nennt_die_eigene_meldung(self, case_open, staff_user):
        """Pin: die Absage fuer eine Zeile, die es nicht mehr gibt."""
        Case.objects.filter(pk=case_open.pk).delete()

        with pytest.raises(ValidationError) as fehler:
            close_case(case_open, staff_user)

        assert _meldung(fehler) == _VERSCHWUNDEN

    def test_verschwundene_zielzeile_nennt_die_eigene_meldung(self, outcome_goal):
        OutcomeGoal.objects.filter(pk=outcome_goal.pk).delete()

        with pytest.raises(ValidationError) as fehler:
            achieve_goal(outcome_goal)

        assert _meldung(fehler) == _VERSCHWUNDEN

    def test_verschwundene_meilensteinzeile_nennt_die_eigene_meldung(self, milestone):
        Milestone.objects.filter(pk=milestone.pk).delete()

        with pytest.raises(ValidationError) as fehler:
            toggle_milestone(milestone)

        assert _meldung(fehler) == _VERSCHWUNDEN


@pytest.mark.django_db
class TestSperrreihenfolgeDerStatuswechsel:
    """Person -> Fall -> Ziel -> Meilenstein, vollstaendig und in dieser Ordnung."""

    def test_close_case_sperrt_person_vor_fall(self, case_open, staff_user):
        with CaptureQueriesContext(connection) as captured:
            close_case(case_open, staff_user)

        assert _gesperrte_tabellen(captured) == ["core_client", "core_case"]

    def test_reopen_case_sperrt_person_vor_fall(self, case_closed, staff_user):
        with CaptureQueriesContext(connection) as captured:
            reopen_case(case_closed, staff_user)

        assert _gesperrte_tabellen(captured) == ["core_client", "core_case"]

    def test_achieve_goal_sperrt_person_fall_ziel(self, outcome_goal):
        with CaptureQueriesContext(connection) as captured:
            achieve_goal(outcome_goal)

        assert _gesperrte_tabellen(captured) == ["core_client", "core_case", "core_outcomegoal"]

    def test_unachieve_goal_sperrt_person_fall_ziel(self, outcome_goal):
        with CaptureQueriesContext(connection) as captured:
            unachieve_goal(outcome_goal)

        assert _gesperrte_tabellen(captured) == ["core_client", "core_case", "core_outcomegoal"]

    def test_toggle_milestone_sperrt_person_fall_ziel_meilenstein(self, milestone):
        with CaptureQueriesContext(connection) as captured:
            toggle_milestone(milestone)

        assert _gesperrte_tabellen(captured) == [
            "core_client",
            "core_case",
            "core_outcomegoal",
            "core_milestone",
        ]


@pytest.mark.django_db
class TestGeschriebeneSpalten:
    """``update_fields`` ist der Unterschied zwischen Statuswechsel und Vollsave."""

    def _spion(self, monkeypatch, modell):
        gesehen = []
        original = modell.save

        def _save(self, *args, **kwargs):
            gesehen.append(kwargs.get("update_fields"))
            return original(self, *args, **kwargs)

        monkeypatch.setattr(modell, "save", _save)
        return gesehen

    def test_close_case_schreibt_status_und_schliesszeitpunkt(self, case_open, staff_user, monkeypatch):
        gesehen = self._spion(monkeypatch, Case)
        close_case(case_open, staff_user)
        assert gesehen == [["status", "closed_at", "updated_at"]]

    def test_reopen_case_schreibt_status_und_schliesszeitpunkt(self, case_closed, staff_user, monkeypatch):
        gesehen = self._spion(monkeypatch, Case)
        reopen_case(case_closed, staff_user)
        assert gesehen == [["status", "closed_at", "updated_at"]]

    def test_achieve_goal_schreibt_nur_die_beiden_statusspalten(self, outcome_goal, monkeypatch):
        gesehen = self._spion(monkeypatch, OutcomeGoal)
        achieve_goal(outcome_goal)
        assert gesehen == [["is_achieved", "achieved_at"]]

    def test_unachieve_goal_schreibt_nur_die_beiden_statusspalten(self, outcome_goal, monkeypatch):
        gesehen = self._spion(monkeypatch, OutcomeGoal)
        unachieve_goal(outcome_goal)
        assert gesehen == [["is_achieved", "achieved_at"]]

    def test_toggle_milestone_schreibt_nur_die_beiden_statusspalten(self, milestone, monkeypatch):
        gesehen = self._spion(monkeypatch, Milestone)
        toggle_milestone(milestone)
        assert gesehen == [["is_completed", "completed_at"]]

    def test_achieve_goal_schreibt_beim_zweiten_aufruf_gar_nicht(self, outcome_goal, monkeypatch):
        """Pin: der Idempotenz-Ausstieg entscheidet am FRISCHEN Stand."""
        achieve_goal(outcome_goal)
        gesehen = self._spion(monkeypatch, OutcomeGoal)
        achieve_goal(OutcomeGoal.objects.get(pk=outcome_goal.pk))
        assert gesehen == []


@pytest.mark.django_db
class TestUebergebeneInstanzBleibtDerRueckgabewert:
    """Der Aufrufer arbeitet mit SEINER Instanz weiter — sie traegt den neuen Stand."""

    def test_close_case_liefert_die_uebergebene_instanz_frisch_zurueck(self, case_open, staff_user):
        Case.objects.filter(pk=case_open.pk).update(title="Parallel umbenannt")

        ergebnis = close_case(case_open, staff_user)

        assert ergebnis is case_open
        assert case_open.status == Case.Status.CLOSED
        assert case_open.title == "Parallel umbenannt"

    def test_toggle_milestone_liefert_die_uebergebene_instanz_frisch_zurueck(self, milestone):
        Milestone.objects.filter(pk=milestone.pk).update(title="Parallel umbenannt")

        ergebnis = toggle_milestone(milestone)

        assert ergebnis is milestone
        assert milestone.is_completed is True
        assert milestone.title == "Parallel umbenannt"


@pytest.mark.django_db
class TestAbfrageBudgetDerStatuswechsel:
    """R-T18: die Sperrkette kostet Abfragen — die Zahl steht fest.

    Fallpfad (12): Personenbezug vorlesen · Personensperre (Art.-18-Gate) ·
    Widerrufsspalten der Person · Fallzeile sperren · ``UPDATE core_case`` ·
    Einrichtung fuer Audit und Zeitstrom · drei Abfragen der Audit-Kette
    (Existenz, ``pg_advisory_xact_lock``, Vorgaengerzeile) · ``INSERT`` der
    Audit-Zeile · ``INSERT`` der Aktivitaet · ``refresh_from_db`` der
    uebergebenen Instanz.

    Zielpfad (7): Kette vorlesen · Personensperre · Widerrufsspalten ·
    Fallzeile sperren · Zielzeile sperren · ``UPDATE`` · ``refresh_from_db``.
    Der Meilenstein (8) sperrt die Zielzeile zusaetzlich.

    Die ``ContentType``-Abfrage des Zeitstroms wird vorher aufgewaermt: sie
    faellt je Prozess nur EINMAL an und gehoert nicht zum Budget des Pfades.
    """

    @pytest.fixture(autouse=True)
    def _content_type_cache(self, db):
        from django.contrib.contenttypes.models import ContentType

        ContentType.objects.get_for_model(Case)

    def test_close_case_budget(self, case_open, staff_user):
        with CaptureQueriesContext(connection) as captured:
            close_case(case_open, staff_user)
        assert len(_fachliche_abfragen(captured)) == 12

    def test_reopen_case_budget(self, case_closed, staff_user):
        with CaptureQueriesContext(connection) as captured:
            reopen_case(case_closed, staff_user)
        assert len(_fachliche_abfragen(captured)) == 12

    def test_achieve_goal_budget(self, outcome_goal):
        with CaptureQueriesContext(connection) as captured:
            achieve_goal(outcome_goal)
        assert len(_fachliche_abfragen(captured)) == 7

    def test_unachieve_goal_budget(self, outcome_goal):
        with CaptureQueriesContext(connection) as captured:
            unachieve_goal(outcome_goal)
        assert len(_fachliche_abfragen(captured)) == 7

    def test_toggle_milestone_budget(self, milestone):
        with CaptureQueriesContext(connection) as captured:
            toggle_milestone(milestone)
        assert len(_fachliche_abfragen(captured)) == 8
