"""Pins fuer die Sperrfolge der Ereignis-Bearbeitung (``core.services.events.crud``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 9 Mutanten.
Gruppen:

* ``lock_event_for_edit`` — Vorlesen der Personenbindung (verliert ``pk``
  oder die Einrichtung), Ereignissperre (verliert ``pk`` oder die Person),
  Abgleich der Instanz an die gesperrte Person (entfaellt oder nullt) und
  der Meldungstext beider Absagen.
* ``_event_form_state_unchanged`` — ein leerer oder nicht-textueller
  Fingerabdruck steigt ohne Abfrage aus.

Eine entfallene Bedingung faellt nur auf, wenn eine zweite Zeile passt. Die
Pins legen deshalb ein zweites Ereignis an, das in der Standardsortierung
(``-occurred_at``) vor dem bearbeiteten steht — derselben oder einer anderen
Person —, und halten die Bedingungen der Abfragen zusaetzlich an Tabelle,
Sperrart (``FOR UPDATE``) und den Parametern der WHERE-Bedingungen fest.
Wettlaeufe werden deterministisch im Art.-18-Gate nachgestellt: das Gate
laeuft zwischen dem ungesperrten Vorlesen und der Ereignissperre.
"""

import re
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone

from core.models import Client, Event
from core.services.case.state_contract import EVENT_WRITE_GONE_MESSAGE
from core.services.client import restriction
from core.services.events.crud import (
    EVENT_EDIT_DELETED_MESSAGE,
    _event_form_state_unchanged,
    lock_event_for_edit,
)

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


def _ereignis(facility, client, doc_type, user, *, vor_tagen):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now() - timedelta(days=vor_tagen),
        data_json={},
        created_by=user,
    )


def _in_den_papierkorb(event):
    Event._base_manager.filter(pk=event.pk).update(is_deleted=True)


def _gate_spion(monkeypatch, nach_der_sperre=None):
    """Zeichnet die Personen des Art.-18-Gates auf; ``nach_der_sperre`` laeuft direkt nach dem Gate."""
    gesehen = []
    original = restriction.assert_processing_allowed

    def _gate(client, *, lock=False):
        gesehen.append(getattr(client, "pk", client))
        ergebnis = original(client, lock=lock)
        if nach_der_sperre is not None:
            nach_der_sperre()
        return ergebnis

    monkeypatch.setattr(restriction, "assert_processing_allowed", _gate)
    return gesehen


@pytest.fixture
def andere_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Andere-Person", created_by=staff_user)


class TestLockEventForEditVorlesen:
    """``lock_event_for_edit``: das ungesperrte Vorlesen der Personenbindung."""

    def test_neueres_ereignis_einer_anderen_person_bestimmt_die_sperre_nicht(
        self, facility, client_identified, andere_person, doc_type_contact, staff_user, monkeypatch
    ):
        """x_lock_event_for_edit__mutmut_5: ohne ``pk`` las das Vorlesen die
        Person des neuesten Ereignisses der Einrichtung, das Gate sperrte die
        falsche Person und die Ereignissperre meldete GONE."""
        eigenes = _ereignis(facility, client_identified, doc_type_contact, staff_user, vor_tagen=3)
        _ereignis(facility, andere_person, doc_type_contact, staff_user, vor_tagen=0)
        gesehen = _gate_spion(monkeypatch)
        lock_event_for_edit(eigenes)
        assert gesehen == [client_identified.pk]

    def test_fremde_einrichtung_im_speicher_bricht_vor_dem_gate_ab(self, sample_event, other_facility, monkeypatch):
        """x_lock_event_for_edit__mutmut_6 (ohne Einrichtung las und sperrte
        das Vorlesen die Person der Zeile, bevor die Sperre abwies), _10
        (Meldung ``None``)."""
        gesehen = _gate_spion(monkeypatch)
        sample_event.facility_id = other_facility.pk
        with pytest.raises(ValidationError) as exc:
            lock_event_for_edit(sample_event)
        assert exc.value.messages == [str(EVENT_WRITE_GONE_MESSAGE)]
        assert gesehen == []

    def test_vorlesen_traegt_pk_und_einrichtung(self, sample_event):
        """x_lock_event_for_edit__mutmut_5 (``pk`` faellt weg), _6
        (``facility_id`` faellt weg)."""
        with _mitschnitt() as abfragen:
            lock_event_for_edit(sample_event)
        assert any(
            a.traegt(id=[sample_event.pk], facility_id=[sample_event.facility_id])
            for a in abfragen.lesungen("core_event")
        )


class TestLockEventForEditSperre:
    """``lock_event_for_edit``: die Ereignissperre mit Person in der Bedingung."""

    def test_papierkorb_eines_neueren_ereignisses_derselben_person_sperrt_nicht(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """x_lock_event_for_edit__mutmut_31: ohne ``pk`` pruefte die Sperre
        das neueste Ereignis der Person und wies wegen dessen Papierkorb ab."""
        eigenes = _ereignis(facility, client_identified, doc_type_contact, staff_user, vor_tagen=3)
        _in_den_papierkorb(_ereignis(facility, client_identified, doc_type_contact, staff_user, vor_tagen=0))
        lock_event_for_edit(eigenes)

    def test_eigener_papierkorb_neben_lebendem_neuerem_ereignis_wird_abgewiesen(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """x_lock_event_for_edit__mutmut_31: ohne ``pk`` pruefte die Sperre
        das neuere, lebende Ereignis — das geloeschte waere bearbeitbar."""
        eigenes = _ereignis(facility, client_identified, doc_type_contact, staff_user, vor_tagen=3)
        _ereignis(facility, client_identified, doc_type_contact, staff_user, vor_tagen=0)
        _in_den_papierkorb(eigenes)
        with pytest.raises(ValidationError) as exc:
            lock_event_for_edit(eigenes)
        assert exc.value.messages == [str(EVENT_EDIT_DELETED_MESSAGE)]

    def test_ereignis_wechselt_nach_der_personensperre_die_person(self, sample_event, andere_person, monkeypatch):
        """x_lock_event_for_edit__mutmut_33 (ohne Person in der Bedingung
        wird das umgehaengte Ereignis trotzdem gesperrt), _38 (Meldung
        ``None``): ein paralleles Zusammenfuehren haengt das Ereignis nach der
        Personensperre an eine andere, ungesperrte Person."""
        _gate_spion(
            monkeypatch,
            nach_der_sperre=lambda: Event._base_manager.filter(pk=sample_event.pk).update(client=andere_person),
        )
        with pytest.raises(ValidationError) as exc:
            lock_event_for_edit(sample_event)
        assert exc.value.messages == [str(EVENT_WRITE_GONE_MESSAGE)]

    def test_sperre_traegt_pk_einrichtung_und_person(self, sample_event):
        """x_lock_event_for_edit__mutmut_31 (``pk`` faellt weg), _33
        (``client_id`` faellt weg)."""
        with _mitschnitt() as abfragen:
            lock_event_for_edit(sample_event)
        sperren = abfragen.sperren("core_event")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(
            a.traegt(
                id=[sample_event.pk],
                facility_id=[sample_event.facility_id],
                client_id=[sample_event.client_id],
            )
            for a in sperren
        )


class TestLockEventForEditAbgleich:
    """``lock_event_for_edit``: die Instanz uebernimmt die gesperrte Person."""

    def test_veraltete_instanz_traegt_danach_die_gesperrte_person(self, sample_event, andere_person):
        """x_lock_event_for_edit__mutmut_40 (Abgleich entfaellt, die Instanz
        behaelt die alte Person), _41 (die Instanz verliert die Person): die
        Anhangs-Vorsperre leitet ihr Ziel aus ``event.client_id`` ab."""
        Event._base_manager.filter(pk=sample_event.pk).update(client=andere_person)
        lock_event_for_edit(sample_event)
        assert sample_event.client_id == andere_person.pk


class TestEventFormStateUnchanged:
    """``_event_form_state_unchanged``: Frueh-Ausstieg ohne Abfrage."""

    @pytest.mark.parametrize("fingerabdruck", ["", 123, ["x"]], ids=["leer", "zahl", "liste"])
    def test_leerer_oder_nicht_textueller_fingerabdruck_fragt_nicht_ab(
        self, sample_event, fingerabdruck, django_assert_num_queries
    ):
        """x__event_form_state_unchanged__mutmut_1: ``and`` statt ``or`` —
        das Ergebnis bleibt ``False``, aber ohne Frueh-Ausstieg liefe die
        gesperrte Fingerabdruck-Lesung."""
        with django_assert_num_queries(0):
            assert _event_form_state_unchanged(sample_event, fingerabdruck) is False
