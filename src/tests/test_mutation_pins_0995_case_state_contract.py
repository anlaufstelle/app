"""Pins fuer die Loeschform des Meilenstein-Vertrags (``core.services.case.state_contract``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 13 Mutanten, alle in
``lock_milestone_for_write`` (Sperrfolge Person -> Fall -> Ziel -> Meilenstein).
Gruppen:

* Zielsperre — die Sperre des Ziels entfaellt ganz oder verliert ``pk``
  bzw. den Bezug zum gesperrten Fall.
* Meilensteinsperre — die Sperre des Meilensteins verliert ``pk`` bzw. den
  Bezug zum gesperrten Ziel.
* Meldungen — eine vor oder waehrend der Sperrwartezeit verschwundene oder
  umgehaengte Zeile meldet die Schreibform ``CASE_WRITE_GONE_MESSAGE``,
  weder eine leere Meldung noch die Statuswechsel-Meldung.

Eine entfallene Bedingung faellt im Ergebnis nur auf, wenn eine zweite Zeile
passt. Die Pins legen deshalb je Stufe eine Nachbarzeile an (zweites Ziel am
Fall, zweiter Meilenstein am Ziel mit kleinerer ``sort_order``) und halten
zusaetzlich fest, welche Zeilen die Sperrabfragen sperren
(``tests._row_lock_helpers``: PostgreSQL plant die mitgeschnittene Anweisung;
massgeblich sind gesperrte Tabelle, Sperrart ``FOR UPDATE``, die Filterwerte
je Spalte und die Sperrfolge Person -> Fall -> Ziel -> Meilenstein, nicht
Alias, Quoting, Projektion, Reihenfolge oder Literalform). Wettlaeufe werden
deterministisch an der beobachteten Sperre nachgestellt: direkt nach der
Sperre der Personenzeile (vor der Fallsperre) oder direkt nach der Sperre der
Fallzeile (vor der Zielsperre) — unabhaengig davon, ueber welchen Helfer die
Produktion die Sperre nimmt.
"""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Case, Client
from core.models.outcome import Milestone, OutcomeGoal
from core.services.case import goals
from core.services.case import state_contract as sc
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

SPERRFOLGE = ["core_client", "core_case", "core_outcomegoal", "core_milestone"]


def _meldungen(exc_info):
    return exc_info.value.messages


def _fall(facility, client, user, *, alter_tage):
    fall = Case.objects.create(facility=facility, client=client, title="Fall", status=Case.Status.OPEN, created_by=user)
    Case.objects.filter(pk=fall.pk).update(created_at=timezone.now() - timedelta(days=alter_tage))
    return fall


def _ziel(case, user, *, alter_tage, titel="Ziel"):
    ziel = OutcomeGoal.objects.create(case=case, title=titel, created_by=user)
    OutcomeGoal.objects.filter(pk=ziel.pk).update(created_at=timezone.now() - timedelta(days=alter_tage))
    return ziel


@contextmanager
def _nach_der_sperre(tabelle, wechsel):
    """Fuehrt ``wechsel`` direkt nach der ersten beobachteten Zeilensperre auf ``tabelle`` aus."""
    with mitschnitt() as abfragen:
        abfragen.nach_sperre(tabelle, wechsel)
        yield abfragen
    assert abfragen.offene_schranken() == []


@pytest.fixture
def andere_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Andere-Person", created_by=staff_user)


class TestZielsperre:
    """``lock_milestone_for_write``: die Sperre des Ziels zwischen Fall und Meilenstein."""

    def test_zielsperre_traegt_pk_und_fall(self, milestone, outcome_goal, case_open):
        """x_lock_milestone_for_write__mutmut_35 (Zielsperre entfaellt), _40
        (``pk`` faellt weg), _41 (``case_id`` faellt weg): das Ziel wird als
        eigene Stufe zwischen Fall und Meilenstein gesperrt, gebunden an
        seine Zeile und den gesperrten Fall."""
        with mitschnitt() as abfragen:
            sc.lock_milestone_for_write(milestone)
        sperren = abfragen.sperren("core_outcomegoal")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(a.traegt(id=[outcome_goal.pk], case_id=[case_open.pk]) for a in sperren)
        assert [t for t in abfragen.sperrfolge() if t in SPERRFOLGE] == SPERRFOLGE

    def test_ziel_wird_nach_der_fallsperre_neben_einem_nachbarziel_umgehaengt(
        self, facility, client_identified, staff_user, case_open
    ):
        """x_lock_milestone_for_write__mutmut_35 (keine Zielpruefung), _40
        (das Nachbarziel des Falls besteht die Pruefung), _41 (das umgehaengte
        Ziel besteht sie ohne Fallbezug), _34 (Meldung ``None``), _36
        (Statuswechsel-Meldung): das Ziel des Meilensteins haengt nach der
        Fallsperre an einem anderen, ungesperrten Fall; am gesperrten Fall
        bleibt ein neueres Nachbarziel zurueck."""
        eigenes = _ziel(case_open, staff_user, alter_tage=3, titel="Ziel A")
        _ziel(case_open, staff_user, alter_tage=0, titel="Ziel B")
        meilenstein = Milestone.objects.create(goal=eigenes, title="Schritt")
        anderer_fall = _fall(facility, client_identified, staff_user, alter_tage=1)
        with (
            _nach_der_sperre("core_case", lambda: OutcomeGoal.objects.filter(pk=eigenes.pk).update(case=anderer_fall)),
            pytest.raises(ValidationError) as exc,
        ):
            sc.lock_milestone_for_write(meilenstein)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]

    def test_ziel_verschwindet_nach_der_fallsperre(self, milestone, outcome_goal):
        """x_lock_milestone_for_write__mutmut_34 (Meldung ``None``), _36
        (Statuswechsel-Meldung): das Ziel ist nach der Fallsperre geloescht."""
        with (
            _nach_der_sperre("core_case", lambda: OutcomeGoal.objects.filter(pk=outcome_goal.pk).delete()),
            pytest.raises(ValidationError) as exc,
        ):
            sc.lock_milestone_for_write(milestone)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]


class TestMeilensteinsperre:
    """``lock_milestone_for_write``: die Sperre des Meilensteins am gesperrten Ziel."""

    def test_meilensteinsperre_traegt_pk_und_ziel(self, milestone, outcome_goal):
        """x_lock_milestone_for_write__mutmut_50 (``pk`` faellt weg), _51
        (``goal_id`` faellt weg)."""
        with mitschnitt() as abfragen:
            sc.lock_milestone_for_write(milestone)
        sperren = abfragen.sperren("core_milestone")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(a.traegt(id=[milestone.pk], goal_id=[outcome_goal.pk]) for a in sperren)

    def test_liefert_den_adressierten_meilenstein(self, outcome_goal):
        """x_lock_milestone_for_write__mutmut_50: ohne ``pk`` kaeme der erste
        Meilenstein des Ziels zurueck (Sortierung ``sort_order``)."""
        Milestone.objects.create(goal=outcome_goal, title="Erster", sort_order=0)
        zweiter = Milestone.objects.create(goal=outcome_goal, title="Zweiter", sort_order=1)
        assert sc.lock_milestone_for_write(zweiter).pk == zweiter.pk

    def test_loeschung_trifft_nur_den_adressierten_meilenstein(self, outcome_goal, staff_user):
        """x_lock_milestone_for_write__mutmut_50: ``delete_milestone`` loescht
        die gesperrte Zeile — ohne ``pk`` waere das der erste Meilenstein des
        Ziels statt des adressierten."""
        erster = Milestone.objects.create(goal=outcome_goal, title="Erster", sort_order=0)
        zweiter = Milestone.objects.create(goal=outcome_goal, title="Zweiter", sort_order=1)
        goals.delete_milestone(zweiter, staff_user)
        assert set(Milestone.objects.filter(goal=outcome_goal).values_list("pk", flat=True)) == {erster.pk}

    def test_meilenstein_wird_nach_der_fallsperre_an_ein_anderes_ziel_gehaengt(self, case_open, staff_user, milestone):
        """x_lock_milestone_for_write__mutmut_51 (der umgehaengte Meilenstein
        wird ohne Zielbezug ausgeliefert), _45 (Meldung ``None``), _47
        (Statuswechsel-Meldung)."""
        anderes_ziel = _ziel(case_open, staff_user, alter_tage=1, titel="Anderes Ziel")
        with (
            _nach_der_sperre("core_case", lambda: Milestone.objects.filter(pk=milestone.pk).update(goal=anderes_ziel)),
            pytest.raises(ValidationError) as exc,
        ):
            sc.lock_milestone_for_write(milestone)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]

    def test_meilenstein_verschwindet_nach_der_fallsperre(self, milestone):
        """x_lock_milestone_for_write__mutmut_45 (Meldung ``None``), _47
        (Statuswechsel-Meldung)."""
        with (
            _nach_der_sperre("core_case", lambda: Milestone.objects.filter(pk=milestone.pk).delete()),
            pytest.raises(ValidationError) as exc,
        ):
            sc.lock_milestone_for_write(milestone)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]


class TestMeldungenVorDerZielsperre:
    """``lock_milestone_for_write``: Vorlesen und Fallsperre melden die Schreibform."""

    def test_meilenstein_vor_der_sperre_verschwunden(self, milestone):
        """x_lock_milestone_for_write__mutmut_3 (Meldung ``None``), _5
        (Statuswechsel-Meldung): die Instanz traegt noch ihren ``pk``, die
        Zeile gibt es nicht mehr."""
        Milestone.objects.filter(pk=milestone.pk).delete()
        with pytest.raises(ValidationError) as exc:
            sc.lock_milestone_for_write(milestone)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]

    def test_fall_wechselt_vor_der_fallsperre_die_person(self, milestone, case_open, andere_person):
        """x_lock_milestone_for_write__mutmut_27 (``gone_message=None``), _32
        (Default -> Statuswechsel-Meldung): der Fall haengt zwischen Vorlesen
        und Fallsperre an einer anderen Person."""
        with (
            _nach_der_sperre("core_client", lambda: Case.objects.filter(pk=case_open.pk).update(client=andere_person)),
            pytest.raises(ValidationError) as exc,
        ):
            sc.lock_milestone_for_write(milestone)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]
