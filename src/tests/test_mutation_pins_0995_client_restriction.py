"""Pins fuer die Einrichtungsgrenze der Verarbeitungseinschraenkung (``core.services.client.restriction``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 2 Mutanten, beide in
``_lock_client_of_actor_facility`` (Sperre der Personenzeile fuer
``restrict_processing`` und ``lift_processing_restriction``).
Gruppen:

* Sperr-Lookup — verliert ``pk`` und sperrt die erste Person der
  Einrichtung; der Art.-18-Vermerk landete an der falschen Person.
* Akteur ohne Einrichtung — ein Nutzerobjekt ohne ``facility_id`` wird
  fail-closed mit der Einrichtungsmeldung abgewiesen, nicht mit
  ``AttributeError``, und nichts wird gesperrt.

Die falsche Person steht in der Standardsortierung (``pseudonym``) vor der
Zielperson; die Sperrabfrage ist zusaetzlich an Tabelle, Sperrart
(``FOR UPDATE``) und den Parametern ihrer WHERE-Bedingungen festgehalten.
"""

import re
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from core.models import Client
from core.services.client.restriction import _lock_client_of_actor_facility, restrict_processing

pytestmark = pytest.mark.django_db

EINRICHTUNGSMELDUNG = "Person gehört zu einer anderen Einrichtung."

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


def _person(facility, user, pseudonym):
    return Client.objects.create(facility=facility, pseudonym=pseudonym, created_by=user)


class TestLockClientOfActorFacility:
    """``_lock_client_of_actor_facility``: Sperre der Zielperson in der Einrichtung des Akteurs."""

    def test_vermerk_landet_nur_an_der_zielperson(self, facility, staff_user):
        """x__lock_client_of_actor_facility__mutmut_14: ohne ``pk`` sperrte
        der Lookup die erste Person der Einrichtung (Sortierung
        ``pseudonym``) und setzte den Art.-18-Vermerk dort."""
        nachbar = _person(facility, staff_user, "Alpha-Nachbar")
        ziel = _person(facility, staff_user, "Zulu-Ziel")
        ergebnis = restrict_processing(ziel, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        assert ergebnis.pk == ziel.pk
        ziel.refresh_from_db()
        nachbar.refresh_from_db()
        assert ziel.processing_restricted is True
        assert nachbar.processing_restricted is False

    def test_sperre_traegt_pk_und_einrichtung(self, facility, staff_user):
        """x__lock_client_of_actor_facility__mutmut_14: ``FOR UPDATE`` auf
        genau die Zielperson in der Einrichtung des Akteurs."""
        ziel = _person(facility, staff_user, "Zulu-Ziel")
        with _mitschnitt() as abfragen:
            assert _lock_client_of_actor_facility(ziel, staff_user).pk == ziel.pk
        sperren = abfragen.sperren("core_client")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(a.traegt(id=[ziel.pk], facility_id=[facility.pk]) for a in sperren)

    def test_akteur_ohne_einrichtung_wird_ohne_sperre_abgewiesen(self, facility, staff_user):
        """x__lock_client_of_actor_facility__mutmut_6: ein Nutzerobjekt ohne
        ``facility_id``-Attribut warf ``AttributeError`` statt der
        fail-closed-Absage mit der Einrichtungsmeldung."""
        ziel = _person(facility, staff_user, "Zulu-Ziel")
        with _mitschnitt() as abfragen:
            with pytest.raises(ValidationError) as exc:
                _lock_client_of_actor_facility(ziel, SimpleNamespace())
        assert exc.value.messages == [EINRICHTUNGSMELDUNG]
        assert abfragen.sperren("core_client") == []
