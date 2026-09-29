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
zusaetzlich die Bedingungen der Sperrabfragen fest — erkannt an Tabelle,
Sperrart (``FOR UPDATE``) und den Parametern der Filterbedingungen in der
WHERE-Klausel, nicht an Projektion, Reihenfolge oder Literalform. Wettlaeufe
werden deterministisch nachgestellt: im Art.-18-Gate (nach dem Vorlesen, vor
der Fallsperre) oder direkt nach der Fallsperre (vor der Zielsperre).
"""

import re
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone

from core.models import Case, Client
from core.models.outcome import Milestone, OutcomeGoal
from core.services.case import goals
from core.services.case import state_contract as sc
from core.services.client import restriction

pytestmark = pytest.mark.django_db

_FROM = re.compile(r'\bFROM\s+"(\w+)"', re.IGNORECASE)
_SPERRE = re.compile(r"\bFOR\s+(NO\s+KEY\s+UPDATE|UPDATE|KEY\s+SHARE|SHARE)\b", re.IGNORECASE)
_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_WHERE_ENDE = re.compile(
    r"\b(?:ORDER\s+BY|GROUP\s+BY|HAVING|LIMIT|OFFSET|FOR\s+(?:NO\s+KEY\s+UPDATE|UPDATE|KEY\s+SHARE|SHARE))\b",
    re.IGNORECASE,
)
_PLATZHALTER = re.compile(
    r'"(\w+)"\s*\.\s*"(\w+)"\s*(?:=\s*%s|IN\s*\(((?:\s*%s\s*,?)+)\))|%s',
    re.IGNORECASE,
)


def _wert(wert):
    """UUID-Werte normalisiert (Objekt, Text mit oder ohne Bindestriche), sonst ``str``."""
    try:
        return uuid.UUID(str(wert)).hex
    except ValueError:
        return str(wert)


def _tiefen(sql):
    """Klammertiefe je Zeichen; Klammern in Bezeichnern und Literalen zaehlen nicht."""
    tiefen, tiefe, quote = [], 0, None
    for zeichen in sql:
        if quote:
            if zeichen == quote:
                quote = None
        elif zeichen in "\"'":
            quote = zeichen
        elif zeichen == "(":
            tiefe += 1
        elif zeichen == ")":
            tiefe -= 1
        tiefen.append(tiefe)
    return tiefen


def _erster_auf_ebene_null(muster, sql, tiefen, ab=0):
    return next((m for m in muster.finditer(sql, ab) if tiefen[m.start()] == 0), None)


def _where_bereich(sql):
    """``(anfang, ende)`` der WHERE-Klausel der aeusseren Abfrage, sonst ``None``."""
    tiefen = _tiefen(sql)
    where = _erster_auf_ebene_null(_WHERE, sql, tiefen)
    if where is None:
        return None
    ende = _erster_auf_ebene_null(_WHERE_ENDE, sql, tiefen, where.end())
    return where.end(), ende.start() if ende else len(sql)


class _Abfrage:
    """Eine ausgefuehrte Abfrage: Tabelle, Sperrart und ihre Filterbedingungen.

    Gelesen wird das SQL mit Platzhaltern plus Parameter. Als Filterbedingung
    zaehlt nur ein Vergleich ``"t"."spalte" = %s`` bzw. ``IN (%s, ...)`` in der
    WHERE-Klausel der aeusseren Abfrage; Projektion, Reihenfolge der
    Bedingungen und Literalform der Werte spielen keine Rolle.
    """

    def __init__(self, sql, params):
        self.sql = sql
        treffer = _FROM.search(sql)
        self.tabelle = treffer.group(1) if treffer else None
        sperre = _SPERRE.search(sql)
        self.sperrart = " ".join(sperre.group(1).upper().split()) if sperre else None
        self.sperrt = self.sperrart is not None
        self.bedingungen = {}
        bereich = _where_bereich(sql)
        werte = iter(params or ())
        for match in _PLATZHALTER.finditer(sql):
            tabelle, spalte, liste = match.groups()
            anzahl = liste.count("%s") if liste else 1
            gelesen = [_wert(next(werte)) for _ in range(anzahl)]
            if tabelle and bereich and bereich[0] <= match.start() < bereich[1]:
                self.bedingungen.setdefault(f"{tabelle}.{spalte}", set()).update(gelesen)

    def traegt(self, **erwartet):
        """Traegt die WHERE-Klausel je Spalte genau diese Wertemenge (``=`` oder ``IN``)?"""
        return all(
            self.bedingungen.get(f"{self.tabelle}.{spalte}") == {_wert(w) for w in werte}
            for spalte, werte in erwartet.items()
        )


class _Mitschnitt(list):
    """Schneidet jede Abfrage samt Parametern mit (``connection.execute_wrapper``)."""

    def __call__(self, execute, sql, params, many, context):
        if not many:
            self.append(_Abfrage(sql, params))
        return execute(sql, params, many, context)

    def sperren(self, tabelle):
        return [a for a in self if a.tabelle == tabelle and a.sperrt]


@contextmanager
def _mitschnitt():
    mitschnitt = _Mitschnitt()
    with connection.execute_wrapper(mitschnitt):
        yield mitschnitt


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


def _nach_dem_personengate(monkeypatch, wechsel):
    """Fuehrt ``wechsel`` im Art.-18-Gate aus — nach dem Vorlesen, vor der Fallsperre."""
    original = restriction.assert_processing_allowed

    def _gate(client, *, lock=False):
        wechsel()
        return original(client, lock=lock)

    monkeypatch.setattr(restriction, "assert_processing_allowed", _gate)


def _nach_der_fallsperre(monkeypatch, wechsel):
    """Fuehrt ``wechsel`` direkt nach der Fallsperre aus — vor der Zielsperre."""
    original = sc._locked_case

    def _gesperrt(*args, **kwargs):
        fall = original(*args, **kwargs)
        wechsel()
        return fall

    monkeypatch.setattr(sc, "_locked_case", _gesperrt)


@pytest.fixture
def andere_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Andere-Person", created_by=staff_user)


class TestZielsperre:
    """``lock_milestone_for_write``: die Sperre des Ziels zwischen Fall und Meilenstein."""

    def test_zielsperre_traegt_pk_und_fall(self, milestone, outcome_goal, case_open):
        """x_lock_milestone_for_write__mutmut_35 (Zielsperre entfaellt), _40
        (``pk`` faellt weg), _41 (``case_id`` faellt weg)."""
        with _mitschnitt() as abfragen:
            sc.lock_milestone_for_write(milestone)
        sperren = abfragen.sperren("core_outcomegoal")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(a.traegt(id=[outcome_goal.pk], case_id=[case_open.pk]) for a in sperren)

    def test_ziel_wird_nach_der_fallsperre_neben_einem_nachbarziel_umgehaengt(
        self, facility, client_identified, staff_user, case_open, monkeypatch
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
        _nach_der_fallsperre(monkeypatch, lambda: OutcomeGoal.objects.filter(pk=eigenes.pk).update(case=anderer_fall))
        with pytest.raises(ValidationError) as exc:
            sc.lock_milestone_for_write(meilenstein)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]

    def test_ziel_verschwindet_nach_der_fallsperre(self, milestone, outcome_goal, monkeypatch):
        """x_lock_milestone_for_write__mutmut_34 (Meldung ``None``), _36
        (Statuswechsel-Meldung): das Ziel ist nach der Fallsperre geloescht."""
        _nach_der_fallsperre(monkeypatch, lambda: OutcomeGoal.objects.filter(pk=outcome_goal.pk).delete())
        with pytest.raises(ValidationError) as exc:
            sc.lock_milestone_for_write(milestone)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]


class TestMeilensteinsperre:
    """``lock_milestone_for_write``: die Sperre des Meilensteins am gesperrten Ziel."""

    def test_meilensteinsperre_traegt_pk_und_ziel(self, milestone, outcome_goal):
        """x_lock_milestone_for_write__mutmut_50 (``pk`` faellt weg), _51
        (``goal_id`` faellt weg)."""
        with _mitschnitt() as abfragen:
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

    def test_meilenstein_wird_nach_der_fallsperre_an_ein_anderes_ziel_gehaengt(
        self, case_open, staff_user, milestone, monkeypatch
    ):
        """x_lock_milestone_for_write__mutmut_51 (der umgehaengte Meilenstein
        wird ohne Zielbezug ausgeliefert), _45 (Meldung ``None``), _47
        (Statuswechsel-Meldung)."""
        anderes_ziel = _ziel(case_open, staff_user, alter_tage=1, titel="Anderes Ziel")
        _nach_der_fallsperre(monkeypatch, lambda: Milestone.objects.filter(pk=milestone.pk).update(goal=anderes_ziel))
        with pytest.raises(ValidationError) as exc:
            sc.lock_milestone_for_write(milestone)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]

    def test_meilenstein_verschwindet_nach_der_fallsperre(self, milestone, monkeypatch):
        """x_lock_milestone_for_write__mutmut_45 (Meldung ``None``), _47
        (Statuswechsel-Meldung)."""
        _nach_der_fallsperre(monkeypatch, lambda: Milestone.objects.filter(pk=milestone.pk).delete())
        with pytest.raises(ValidationError) as exc:
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

    def test_fall_wechselt_vor_der_fallsperre_die_person(self, milestone, case_open, andere_person, monkeypatch):
        """x_lock_milestone_for_write__mutmut_27 (``gone_message=None``), _32
        (Default -> Statuswechsel-Meldung): der Fall haengt zwischen Vorlesen
        und Fallsperre an einer anderen Person."""
        _nach_dem_personengate(monkeypatch, lambda: Case.objects.filter(pk=case_open.pk).update(client=andere_person))
        with pytest.raises(ValidationError) as exc:
            sc.lock_milestone_for_write(milestone)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]
