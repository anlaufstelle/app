"""Pins fuer die Sperrabfragen des Statuswechsel-Vertrags (``core.services.case.state_contract``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 13 Mutanten.
Gruppen:

* ``_locked_case`` — Widerrufslesung der Person und Fallsperre verlieren je
  eine ihrer Bedingungen (``pk``/``client_id``/``facility_id``).
* ``lock_case_for_status_change`` — das Vorlesen des Personenbezugs verliert
  ``pk`` oder ``facility_id``.
* ``lock_goal_for_status_change`` / ``lock_milestone_for_status_change`` —
  die Stufen Ziel und Meilenstein verlieren ``pk`` oder den Bezug zur
  uebergeordneten Stufe.

Eine entfallene Bedingung faellt im Ergebnis nur auf, wenn eine zweite Zeile
passt. Deshalb halten die Pins die WHERE-Bedingungen jeder Sperrabfrage exakt
fest (Spalte und Wert) und belegen die ``pk``-Varianten zusaetzlich am
Ergebnis: die Standardsortierung der Modelle stellt die falsche Zeile nach
vorn, sodass ein Filter ohne ``pk`` sie liefern wuerde.
"""

import re
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Case, Client
from core.models.outcome import Milestone, OutcomeGoal
from core.services.case import state_contract as sc

pytestmark = pytest.mark.django_db

_BEDINGUNG = re.compile(r'"(\w+)"\."(\w+)" = \'([0-9a-f]{32})\'::uuid')


def _bedingungen(sql):
    """Die ``Spalte = UUID``-Bedingungen der WHERE-Klausel als Menge."""
    where = sql.split(" WHERE ", 1)[1]
    return {(f"{t}.{c}", v) for t, c, v in _BEDINGUNG.findall(where)}


def _abfragen(ctx, praefix):
    return [q["sql"] for q in ctx.captured_queries if q["sql"].startswith(praefix)]


_WIDERRUF = 'SELECT "core_client"."is_deleted" AS "is_deleted", "core_client"."is_active" AS "is_active" FROM'
_FALLSPERRE = 'SELECT "core_case"."is_deleted", '
_ZIELSPERRE = 'SELECT "core_outcomegoal".'
_MEILENSTEINSPERRE = 'SELECT "core_milestone"."id", '
_VORLESEN_FALL = 'SELECT "core_case"."client_id" AS "client_id", "core_case"."facility_id" AS "facility_id" FROM'


def _fall(facility, client, user, *, alter_tage):
    fall = Case.objects.create(facility=facility, client=client, title="Fall", status=Case.Status.OPEN, created_by=user)
    Case.objects.filter(pk=fall.pk).update(created_at=timezone.now() - timedelta(days=alter_tage))
    return fall


class TestLockedCaseAbfragen:
    """``_locked_case``: Widerrufslesung und Fallsperre mit allen drei Bedingungen."""

    def test_widerrufslesung_und_fallsperre_tragen_alle_bedingungen(self, case_open):
        """x__locked_case__mutmut_13 (pk der Person faellt weg), _14 (facility_id
        der Person faellt weg), _31 (pk des Falls), _32 (client_id des Falls),
        _33 (facility_id des Falls): jede fehlende Bedingung aendert die Menge."""
        with CaptureQueriesContext(connection) as ctx:
            sc.lock_case_for_status_change(case_open)
        (widerruf,) = _abfragen(ctx, _WIDERRUF)
        assert _bedingungen(widerruf) == {
            ("core_client.id", case_open.client_id.hex),
            ("core_client.facility_id", case_open.facility_id.hex),
        }
        (sperre,) = _abfragen(ctx, _FALLSPERRE)
        assert sperre.endswith("FOR UPDATE")
        assert _bedingungen(sperre) == {
            ("core_case.id", case_open.pk.hex),
            ("core_case.client_id", case_open.client_id.hex),
            ("core_case.facility_id", case_open.facility_id.hex),
        }

    def test_widerrufene_person_wird_trotz_aktiver_nachbarin_abgelehnt(self, facility, staff_user):
        """x__locked_case__mutmut_13: ohne ``pk`` liest die Pruefung die nach
        Pseudonym erste Person der Einrichtung (hier die aktive) und laesst
        den Statuswechsel an der widerrufenen Person durch."""
        Client.objects.create(facility=facility, pseudonym="AAA-aktiv", created_by=staff_user)
        widerrufen = Client.objects.create(facility=facility, pseudonym="ZZZ-widerrufen", created_by=staff_user)
        fall = _fall(facility, widerrufen, staff_user, alter_tage=0)
        Client.objects.filter(pk=widerrufen.pk).update(is_active=False)
        with pytest.raises(ValidationError):
            sc.lock_case_for_status_change(fall)

    def test_liefert_den_adressierten_fall_nicht_den_neuesten(self, facility, client_identified, staff_user):
        """x__locked_case__mutmut_31: ohne ``pk`` kaeme der neueste Fall der
        Person zurueck (Sortierung ``-created_at``), nicht der adressierte."""
        alt = _fall(facility, client_identified, staff_user, alter_tage=5)
        _fall(facility, client_identified, staff_user, alter_tage=0)
        assert sc.lock_case_for_status_change(alt).pk == alt.pk


class TestLockCaseForStatusChange:
    """``lock_case_for_status_change``: das Vorlesen des Personenbezugs."""

    def test_vorlesen_traegt_pk_und_einrichtung(self, case_open):
        """x_lock_case_for_status_change__mutmut_9 (pk faellt weg), _10
        (facility_id faellt weg)."""
        with CaptureQueriesContext(connection) as ctx:
            sc.lock_case_for_status_change(case_open)
        (vorlesen,) = _abfragen(ctx, _VORLESEN_FALL)
        assert _bedingungen(vorlesen) == {
            ("core_case.id", case_open.pk.hex),
            ("core_case.facility_id", case_open.facility_id.hex),
        }

    def test_aelterer_fall_neben_fremder_person_bleibt_sperrbar(self, facility, client_identified, staff_user):
        """x_lock_case_for_status_change__mutmut_9: ohne ``pk`` liest das
        Vorlesen die Person des neuesten Falls der Einrichtung; die Sperre
        findet den adressierten Fall dann nicht und meldet GONE."""
        andere = Client.objects.create(facility=facility, pseudonym="Andere-Person", created_by=staff_user)
        alt = _fall(facility, client_identified, staff_user, alter_tage=5)
        _fall(facility, andere, staff_user, alter_tage=0)
        assert sc.lock_case_for_status_change(alt).pk == alt.pk


class TestLockGoalForStatusChange:
    """``lock_goal_for_status_change``: die Zielsperre."""

    def test_zielsperre_traegt_pk_und_fall(self, outcome_goal):
        """x_lock_goal_for_status_change__mutmut_25 (pk faellt weg), _26
        (case_id faellt weg)."""
        with CaptureQueriesContext(connection) as ctx:
            sc.lock_goal_for_status_change(outcome_goal)
        sperren = [q for q in _abfragen(ctx, _ZIELSPERRE) if q.endswith("FOR UPDATE")]
        assert len(sperren) == 1
        assert _bedingungen(sperren[0]) == {
            ("core_outcomegoal.id", outcome_goal.pk.hex),
            ("core_outcomegoal.case_id", outcome_goal.case_id.hex),
        }

    def test_liefert_das_adressierte_ziel(self, case_open, staff_user):
        """x_lock_goal_for_status_change__mutmut_25: ohne ``pk`` kaeme das
        neueste Ziel des Falls zurueck."""
        alt = OutcomeGoal.objects.create(case=case_open, title="Ziel A", created_by=staff_user)
        OutcomeGoal.objects.filter(pk=alt.pk).update(created_at=timezone.now() - timedelta(days=3))
        OutcomeGoal.objects.create(case=case_open, title="Ziel B", created_by=staff_user)
        assert sc.lock_goal_for_status_change(alt).pk == alt.pk


class TestLockMilestoneForStatusChange:
    """``lock_milestone_for_status_change``: Ziel- und Meilensteinsperre."""

    def test_ziel_und_meilensteinsperre_tragen_ihre_bedingungen(self, milestone):
        """x_lock_milestone_for_status_change__mutmut_30 (pk des Ziels), _31
        (case_id des Ziels), _37 (pk des Meilensteins), _38 (goal_id des
        Meilensteins)."""
        goal = milestone.goal
        with CaptureQueriesContext(connection) as ctx:
            sc.lock_milestone_for_status_change(milestone)
        (zielsperre,) = _abfragen(ctx, 'SELECT "core_outcomegoal"."id" FROM')
        assert zielsperre.endswith("FOR UPDATE")
        assert _bedingungen(zielsperre) == {
            ("core_outcomegoal.id", goal.pk.hex),
            ("core_outcomegoal.case_id", goal.case_id.hex),
        }
        (meilenstein,) = _abfragen(ctx, _MEILENSTEINSPERRE)
        assert meilenstein.endswith("FOR UPDATE")
        assert _bedingungen(meilenstein) == {
            ("core_milestone.id", milestone.pk.hex),
            ("core_milestone.goal_id", goal.pk.hex),
        }

    def test_liefert_den_adressierten_meilenstein(self, outcome_goal):
        """x_lock_milestone_for_status_change__mutmut_37: ohne ``pk`` kaeme
        der Meilenstein mit der kleinsten ``sort_order`` zurueck."""
        adressiert = Milestone.objects.create(goal=outcome_goal, title="A", sort_order=5)
        Milestone.objects.create(goal=outcome_goal, title="B", sort_order=0)
        assert sc.lock_milestone_for_status_change(adressiert).pk == adressiert.pk
