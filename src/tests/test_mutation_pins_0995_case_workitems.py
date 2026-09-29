"""Pins fuer die Einrichtungs- und Zeilenbindung der Aufgabenbearbeitung (``core.services.case.workitems``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 3 Mutanten.
Gruppen:

* ``update_workitem`` — das ungesperrte Vorlesen des Personenbezugs verliert
  ``pk``; das Art.-18-Gate sperrt dann die Person einer beliebigen Aufgabe.
* ``_locked_client_id`` — die Aufgabensperre verliert ``pk`` und liefert die
  Person der ersten Aufgabe der Einrichtung.
* ``_form_state_unchanged`` — die Fingerabdruck-Lesung verliert ``pk`` und
  vergleicht gegen die Formularfelder einer Nachbaraufgabe.

Alle drei fallen nur auf, wenn eine zweite Aufgabe derselben Einrichtung
existiert. Die Nachbaraufgabe ist deshalb die neuere (Standardsortierung
``-created_at`` stellt sie nach vorn) und gehoert je nach Pin einer anderen
Person oder traegt dieselben Formularwerte. Zusaetzlich halten die Pins die
Bedingungen der Abfragen fest — erkannt an Tabelle, Sperrart und den
Parametern der Filterbedingungen in der WHERE-Klausel, nicht an Projektion,
Reihenfolge oder Literalform.
"""

import re
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from core.models import Client, WorkItem
from core.services.case.workitems import (
    _form_state_unchanged,
    _locked_client_id,
    update_workitem,
    workitem_form_state_token,
)
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

    Als Filterbedingung zaehlt nur ein Vergleich ``"t"."spalte" = %s`` bzw.
    ``IN (%s, ...)`` in der WHERE-Klausel der aeusseren Abfrage; Projektion,
    Reihenfolge der Bedingungen und Literalform der Werte spielen keine Rolle.
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

    def lesungen(self, tabelle):
        return [a for a in self if a.tabelle == tabelle and not a.sperrt]

    def sperren(self, tabelle):
        return [a for a in self if a.tabelle == tabelle and a.sperrt]


@contextmanager
def _mitschnitt():
    mitschnitt = _Mitschnitt()
    with connection.execute_wrapper(mitschnitt):
        yield mitschnitt


def _aufgabe(facility, client, user, *, alter_tage, titel="Aufgabe"):
    aufgabe = WorkItem.objects.create(
        facility=facility,
        client=client,
        created_by=user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title=titel,
    )
    WorkItem.objects.filter(pk=aufgabe.pk).update(created_at=timezone.now() - timedelta(days=alter_tage))
    aufgabe.refresh_from_db()
    return aufgabe


@pytest.fixture
def andere_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Andere-Person", created_by=staff_user)


@pytest.fixture
def eigene_und_nachbar(facility, client_identified, andere_person, staff_user):
    """Eigene (aeltere) Aufgabe und eine neuere Nachbaraufgabe einer anderen Person."""
    eigene = _aufgabe(facility, client_identified, staff_user, alter_tage=5, titel="Eigene")
    nachbar = _aufgabe(facility, andere_person, staff_user, alter_tage=0, titel="Nachbar")
    return eigene, nachbar


class TestUpdateWorkitem:
    """``update_workitem``: das Vorlesen des Personenbezugs vor dem Art.-18-Gate."""

    def test_gate_sperrt_nur_die_person_der_adressierten_aufgabe(
        self, eigene_und_nachbar, staff_user, client_identified, monkeypatch
    ):
        """x_update_workitem__mutmut_9: ohne ``pk`` liest das Vorlesen die
        Person der neueren Nachbaraufgabe, das Gate sperrt die falsche Person
        und die Aufgabensperre weist mit 'Personenzuordnung ... geaendert' ab."""
        eigene, _ = eigene_und_nachbar
        gesehen = []
        original = restriction.assert_processing_allowed

        def _gate(client, *, lock=False):
            gesehen.append(getattr(client, "pk", client))
            return original(client, lock=lock)

        monkeypatch.setattr(restriction, "assert_processing_allowed", _gate)
        update_workitem(eigene, staff_user, title="Neu")
        assert gesehen == [client_identified.pk]
        eigene.refresh_from_db()
        assert eigene.title == "Neu"

    def test_vorlesen_traegt_pk_und_einrichtung(self, sample_workitem, staff_user):
        """x_update_workitem__mutmut_9: das ungesperrte Vorlesen der Aufgabe
        filtert auf ``pk`` und Einrichtung (eine Aufgabe allein — die Abfrage,
        nicht ihr Ergebnis, traegt den Nachweis)."""
        with _mitschnitt() as abfragen:
            update_workitem(sample_workitem, staff_user, title="Neu")
        assert any(
            a.traegt(id=[sample_workitem.pk], facility_id=[sample_workitem.facility_id])
            for a in abfragen.lesungen("core_workitem")
        )


class TestLockedClientId:
    """``_locked_client_id``: die Aufgabensperre trifft die adressierte Zeile."""

    def test_liefert_die_person_der_adressierten_aufgabe(self, eigene_und_nachbar, client_identified):
        """x__locked_client_id__mutmut_8: ohne ``pk`` kaeme die Person der
        neueren Nachbaraufgabe zurueck."""
        eigene, _ = eigene_und_nachbar
        assert _locked_client_id(eigene) == client_identified.pk

    def test_aufgabensperre_traegt_pk_und_einrichtung(self, eigene_und_nachbar):
        """x__locked_client_id__mutmut_8: ``FOR UPDATE`` auf genau die
        adressierte Aufgabe in ihrer Einrichtung."""
        eigene, _ = eigene_und_nachbar
        with _mitschnitt() as abfragen:
            _locked_client_id(eigene)
        sperren = abfragen.sperren("core_workitem")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(a.traegt(id=[eigene.pk], facility_id=[eigene.facility_id]) for a in sperren)


class TestFormStateUnchanged:
    """``_form_state_unchanged``: der Fingerabdruck wird an der eigenen Zeile gemessen."""

    def test_reiner_versionskonflikt_wird_neben_einer_nachbaraufgabe_gerettet(self, eigene_und_nachbar, staff_user):
        """x__form_state_unchanged__mutmut_8: parallel aenderte sich nur
        ``updated_at`` der eigenen Aufgabe; ohne ``pk`` verglich die Rettung
        gegen die Formularwerte der neueren Nachbaraufgabe und wies ab."""
        eigene, _ = eigene_und_nachbar
        stand = eigene.updated_at.isoformat()
        token = workitem_form_state_token(eigene)
        WorkItem.objects.filter(pk=eigene.pk).update(updated_at=timezone.now() + timedelta(minutes=1))
        assert _form_state_unchanged(eigene, token) is True
        update_workitem(eigene, staff_user, expected_updated_at=stand, expected_form_state=token, title="Neu")
        eigene.refresh_from_db()
        assert eigene.title == "Neu"

    def test_parallele_feldaenderung_bleibt_konflikt_neben_gleichlautender_nachbaraufgabe(
        self, facility, client_identified, staff_user
    ):
        """x__form_state_unchanged__mutmut_8: die neuere Nachbaraufgabe traegt
        genau die gerenderten Formularwerte, die eigene wurde parallel
        umbenannt — ohne ``pk`` galt das als unveraendert und die parallele
        Aenderung waere still ueberschrieben worden."""
        eigene = _aufgabe(facility, client_identified, staff_user, alter_tage=5, titel="Gleich")
        _aufgabe(facility, client_identified, staff_user, alter_tage=0, titel="Gleich")
        token = workitem_form_state_token(eigene)
        WorkItem.objects.filter(pk=eigene.pk).update(title="Parallel geaendert")
        assert _form_state_unchanged(eigene, token) is False

    def test_fingerabdruck_lesung_traegt_pk_und_einrichtung(self, eigene_und_nachbar):
        """x__form_state_unchanged__mutmut_8: ``FOR UPDATE`` auf genau die
        adressierte Aufgabe in ihrer Einrichtung."""
        eigene, _ = eigene_und_nachbar
        with _mitschnitt() as abfragen:
            _form_state_unchanged(eigene, workitem_form_state_token(eigene))
        sperren = abfragen.sperren("core_workitem")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(a.traegt(id=[eigene.pk], facility_id=[eigene.facility_id]) for a in sperren)
