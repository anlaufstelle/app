"""Pins fuer die Einrichtungsgrenze der Fall-Loeschung (``core.services.case.deletion``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 3 Mutanten.
Gruppen:

* ``soft_delete_case`` — das Vorlesen des Personenbezugs fuer das
  Art.-18-Gate verliert ``pk`` (das Gate prueft dann die Person eines
  beliebigen Falls der Einrichtung); ein Akteur ohne ``facility_id`` wird
  fail-closed mit der Einrichtungsmeldung abgewiesen, nicht mit
  ``AttributeError``.
* ``_move_case_to_trash`` — fehlt die Fallzeile in der Einrichtung, nennt
  die Absage die Einrichtungsgrenze (kein leerer Meldungstext).

Der Nachbarfall einer freien Person ist der neuere (Standardsortierung
``-created_at``), der zu loeschende gehoert einer Person mit
Verarbeitungseinschraenkung. Das Vorlesen ist zusaetzlich an Tabelle und den
Parametern seiner WHERE-Bedingungen festgehalten.
"""

import re
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone

from core.models import Case, Client
from core.services.case.deletion import CASE_OTHER_FACILITY_MESSAGE, _move_case_to_trash, soft_delete_case
from core.services.client import restriction
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE

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


def _fall(facility, client, user, *, alter_tage):
    fall = Case.objects.create(facility=facility, client=client, title="Fall", status=Case.Status.OPEN, created_by=user)
    Case.objects.filter(pk=fall.pk).update(created_at=timezone.now() - timedelta(days=alter_tage))
    fall.refresh_from_db()
    return fall


@pytest.fixture
def eingeschraenkte_person(facility, staff_user):
    person = Client.objects.create(facility=facility, pseudonym="Eingeschraenkt", created_by=staff_user)
    Client.objects.filter(pk=person.pk).update(
        processing_restricted=True,
        processing_restriction_reason=Client.RestrictionReason.ACCURACY_DISPUTED,
        processing_restricted_at=timezone.now(),
    )
    return person


@pytest.fixture
def freie_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Frei", created_by=staff_user)


class TestSoftDeleteCase:
    """``soft_delete_case``: Einrichtungsgrenze und Personenbezug des Art.-18-Gates."""

    def test_fall_einer_eingeschraenkten_person_neben_neuerem_freien_fall(
        self, facility, eingeschraenkte_person, freie_person, staff_user, monkeypatch
    ):
        """x_soft_delete_case__mutmut_17: ohne ``pk`` las das Vorlesen die
        Person des neueren Falls — das Gate pruefte die freie Person und der
        Fall der eingeschraenkten Person landete im Papierkorb."""
        ziel = _fall(facility, eingeschraenkte_person, staff_user, alter_tage=5)
        _fall(facility, freie_person, staff_user, alter_tage=0)
        gesehen = []
        original = restriction.assert_processing_allowed

        def _gate(client, *, lock=False):
            gesehen.append(getattr(client, "pk", client))
            return original(client, lock=lock)

        monkeypatch.setattr(restriction, "assert_processing_allowed", _gate)
        with pytest.raises(ValidationError) as exc:
            soft_delete_case(ziel, staff_user)
        assert exc.value.messages == [str(PROCESSING_RESTRICTED_MESSAGE)]
        assert gesehen == [eingeschraenkte_person.pk]
        assert Case.objects.filter(pk=ziel.pk, is_deleted=False).exists()

    def test_vorlesen_traegt_pk_und_einrichtung(self, case_open, staff_user):
        """x_soft_delete_case__mutmut_17: das Vorlesen der Fallzeile filtert auf
        ``pk`` und die Einrichtung der handelnden Person."""
        with _mitschnitt() as abfragen:
            soft_delete_case(case_open, staff_user)
        assert any(
            a.traegt(id=[case_open.pk], facility_id=[staff_user.facility_id]) for a in abfragen.lesungen("core_case")
        )

    @pytest.mark.parametrize("akteur", [None, AnonymousUser()], ids=["none", "anonym"])
    def test_akteur_ohne_einrichtung_wird_abgewiesen(self, case_open, akteur):
        """x_soft_delete_case__mutmut_6: ohne ``facility_id``-Attribut warf der
        Akteur ``AttributeError`` statt der fail-closed-Absage."""
        with pytest.raises(ValidationError) as exc:
            soft_delete_case(case_open, akteur)
        assert exc.value.messages == [str(CASE_OTHER_FACILITY_MESSAGE)]
        assert Case.objects.filter(pk=case_open.pk, is_deleted=False).exists()


class TestMoveCaseToTrash:
    """``_move_case_to_trash``: die Absage bei fehlender Fallzeile in der Einrichtung."""

    def test_fallzeile_fehlt_in_der_einrichtung(self, case_open, other_facility, staff_user):
        """x__move_case_to_trash__mutmut_8: ohne Meldungstext verlor die
        Absage die Einrichtungsgrenze."""
        case_open.facility_id = other_facility.pk
        with pytest.raises(ValidationError) as exc:
            _move_case_to_trash(case_open, staff_user)
        assert exc.value.messages == [str(CASE_OTHER_FACILITY_MESSAGE)]
        assert Case.objects.filter(pk=case_open.pk, is_deleted=False).exists()
