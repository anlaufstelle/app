"""Pins fuer die Schreibformen des Zustandsvertrags (``core.services.case.state_contract``).

Quelle: Mutations-Delta des Checkpoints release-0.99.3, 17 Mutanten.
Gruppen:

* ``_raise_if_clients_revoked`` — die Widerrufslesung verliert die
  Einrichtungsgrenze.
* ``lock_case_for_write`` — das Vorlesen verliert ``pk`` oder die
  Einrichtung; im Wettlauf geht die Schreibmeldung verloren.
* ``lock_goal_for_write`` — die Zielsperre verliert ``pk`` oder den Bezug
  zum gesperrten Fall; im Wettlauf geht die Schreibmeldung verloren.
* ``lock_event_for_case_link`` — Vorlesen, Zielfall-Lesung, Fallsperre und
  Ereignissperre verlieren je eine Bedingung.

Eine entfallene Bedingung faellt im Ergebnis nur auf, wenn eine zweite Zeile
passt. Deshalb halten die Pins je Abfrage die Bedingungen fest, die sie
tragen muss — erkannt an Tabelle, Sperrmarker und den Parametern der
Bedingungen, nicht an Projektion, Reihenfolge oder Literalform; ``= x`` und
``IN (x)`` gelten gleich. Die ``pk``-Varianten sind zusaetzlich am Ergebnis
belegt: die
Standardsortierung der Modelle stellt die falsche Zeile nach vorn, sodass ein
Filter ohne ``pk`` sie liefern wuerde. Wettlaeufe werden deterministisch im
Art.-18-Gate bzw. direkt nach der Fallsperre nachgestellt — zwischen dem
ungesperrten Vorlesen und der Sperre der naechsten Stufe.
"""

import re
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone

from core.models import Case, Client, Event
from core.models.outcome import OutcomeGoal
from core.services.case import state_contract as sc
from core.services.client import restriction
from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE

pytestmark = pytest.mark.django_db

_FROM = re.compile(r'\bFROM\s+"(\w+)"', re.IGNORECASE)
_SPERRE = re.compile(r"\bFOR\s+(?:NO\s+KEY\s+UPDATE|UPDATE|KEY\s+SHARE|SHARE)\b", re.IGNORECASE)
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


class _Abfrage:
    """Eine ausgefuehrte Abfrage: Tabelle, Sperrmarker und ihre Parameter-Bedingungen.

    Gelesen wird die Abfrage VOR der Debug-Formatierung (SQL mit Platzhaltern
    plus Parameter). Die SELECT-Projektion, die Reihenfolge der Bedingungen und
    die Literalform der Werte spielen damit keine Rolle; ``spalte = x`` und
    ``spalte IN (x, ...)`` ergeben gleichermassen die Wertemenge der Spalte.
    """

    def __init__(self, sql, params):
        self.sql = sql
        treffer = _FROM.search(sql)
        self.tabelle = treffer.group(1) if treffer else None
        self.sperrt = bool(_SPERRE.search(sql))
        self.bedingungen = {}
        werte = iter(params or ())
        for match in _PLATZHALTER.finditer(sql):
            tabelle, spalte, liste = match.groups()
            anzahl = liste.count("%s") if liste else 1
            gelesen = [_wert(next(werte)) for _ in range(anzahl)]
            if tabelle:
                self.bedingungen.setdefault(f"{tabelle}.{spalte}", set()).update(gelesen)

    def traegt(self, **erwartet):
        """Traegt die Abfrage je Spalte genau diese Wertemenge (``=`` oder ``IN``)?"""
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
    """Fuehrt ``wechsel`` im Art.-18-Gate des Einzelpfads aus — nach dem Vorlesen, vor der Fallsperre."""
    original = restriction.assert_processing_allowed

    def _gate(client, *, lock=False):
        wechsel()
        return original(client, lock=lock)

    monkeypatch.setattr(restriction, "assert_processing_allowed", _gate)


def _wechsel_im_mengengate(monkeypatch, wechsel):
    """Fuehrt ``wechsel`` im Art.-18-Gate der Personenmenge aus — nach dem Vorlesen, vor den Sperren."""
    original = restriction.assert_processing_allowed_for

    def _gate(clients, *, lock=False):
        wechsel()
        return original(clients, lock=lock)

    monkeypatch.setattr(restriction, "assert_processing_allowed_for", _gate)


def _ereignis(facility, client, doc_type, user, *, case=None, alter_tage=0):
    return Event.objects.create(
        facility=facility,
        client=client,
        case=case,
        document_type=doc_type,
        occurred_at=timezone.now() - timedelta(days=alter_tage),
        data_json={},
        created_by=user,
    )


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


@pytest.fixture
def fremde_person(other_facility, staff_user):
    return Client.objects.create(facility=other_facility, pseudonym="Fremde-Person", created_by=staff_user)


class TestRaiseIfClientsRevoked:
    """``_raise_if_clients_revoked``: die Widerrufslesung bleibt in der Einrichtung."""

    def test_person_einer_fremden_einrichtung_gilt_als_widerrufen(self, facility, fremde_person):
        """x__raise_if_clients_revoked__mutmut_13: ohne ``facility_id`` zaehlte
        die aktive Person einer fremden Einrichtung als 'live' — erwartet ist
        die fail-closed-Absage."""
        with pytest.raises(ValidationError) as exc:
            sc._raise_if_clients_revoked({fremde_person.pk}, facility.pk)
        assert _meldungen(exc) == [str(CLIENT_WRITE_REVOKED_MESSAGE)]

    def test_gegenprobe_eigene_aktive_person_ist_live(self, facility, client_identified):
        assert sc._raise_if_clients_revoked({client_identified.pk}, facility.pk) is None


class TestLockCaseForWrite:
    """``lock_case_for_write``: Vorlesen des Personenbezugs und Wettlaufmeldung."""

    def test_vorlesen_traegt_pk_und_einrichtung(self, case_open):
        """x_lock_case_for_write__mutmut_12 (``pk`` faellt weg), _13
        (``facility_id`` faellt weg)."""
        with _mitschnitt() as abfragen:
            sc.lock_case_for_write(case_open)
        assert any(
            a.traegt(id=[case_open.pk], facility_id=[case_open.facility_id]) for a in abfragen.lesungen("core_case")
        )

    def test_aelterer_fall_neben_neuerem_fall_einer_anderen_person(
        self, facility, client_identified, andere_person, staff_user
    ):
        """x_lock_case_for_write__mutmut_12: ohne ``pk`` liest das Vorlesen die
        Person des neuesten Falls der Einrichtung (Sortierung ``-created_at``),
        die Sperre findet den adressierten Fall dann nicht und meldet GONE."""
        alt = _fall(facility, client_identified, staff_user, alter_tage=5)
        _fall(facility, andere_person, staff_user, alter_tage=0)
        assert sc.lock_case_for_write(alt).pk == alt.pk

    def test_fall_mit_fremder_einrichtung_im_speicher_wird_abgewiesen(self, case_open, other_facility):
        """x_lock_case_for_write__mutmut_13: ohne ``facility_id`` im Vorlesen
        uebernimmt die Sperre die Einrichtung aus der Datenbank und liefert den
        Fall aus, obwohl der Aufrufer ihn einer anderen Einrichtung zuschrieb."""
        case_open.facility_id = other_facility.pk
        with pytest.raises(ValidationError) as exc:
            sc.lock_case_for_write(case_open)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]

    def test_wettlauf_meldet_die_schreibform(self, case_open, andere_person, monkeypatch):
        """x_lock_case_for_write__mutmut_22 (``gone_message=None`` -> Meldung
        ``None``) und _27 (Default -> Statuswechsel-Meldung): der Fall wechselt
        zwischen Vorlesen und Sperre die Person."""
        _nach_dem_personengate(monkeypatch, lambda: Case.objects.filter(pk=case_open.pk).update(client=andere_person))
        with pytest.raises(ValidationError) as exc:
            sc.lock_case_for_write(case_open)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]


class TestLockGoalForWrite:
    """``lock_goal_for_write``: die Zielsperre und ihre Wettlaufmeldung."""

    def test_zielsperre_traegt_pk_und_fall(self, outcome_goal):
        """x_lock_goal_for_write__mutmut_35 (``pk`` faellt weg), _36
        (``case_id`` faellt weg)."""
        with _mitschnitt() as abfragen:
            sc.lock_goal_for_write(outcome_goal)
        sperren = abfragen.sperren("core_outcomegoal")
        assert sperren
        assert all(a.traegt(id=[outcome_goal.pk], case_id=[outcome_goal.case_id]) for a in sperren)

    def test_liefert_das_adressierte_ziel(self, case_open, staff_user):
        """x_lock_goal_for_write__mutmut_35: ohne ``pk`` kaeme das neueste
        Ziel des Falls zurueck (Sortierung ``-created_at``)."""
        alt = _ziel(case_open, staff_user, alter_tage=3, titel="Ziel A")
        _ziel(case_open, staff_user, alter_tage=0, titel="Ziel B")
        assert sc.lock_goal_for_write(alt).pk == alt.pk

    def test_ziel_wird_nach_der_fallsperre_umgehaengt(
        self, facility, client_identified, staff_user, outcome_goal, monkeypatch
    ):
        """x_lock_goal_for_write__mutmut_36 (liefert das umgehaengte Ziel aus),
        _30 (Meldung ``None``), _32 (Statuswechsel-Meldung): das Ziel haengt
        nach der Fallsperre an einem anderen, ungesperrten Fall."""
        anderer_fall = _fall(facility, client_identified, staff_user, alter_tage=1)
        _nach_der_fallsperre(
            monkeypatch, lambda: OutcomeGoal.objects.filter(pk=outcome_goal.pk).update(case=anderer_fall)
        )
        with pytest.raises(ValidationError) as exc:
            sc.lock_goal_for_write(outcome_goal)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]

    def test_ziel_verschwindet_nach_der_fallsperre(self, outcome_goal, monkeypatch):
        """x_lock_goal_for_write__mutmut_30 (Meldung ``None``), _32
        (Statuswechsel-Meldung): das Ziel ist nach der Fallsperre geloescht."""
        _nach_der_fallsperre(monkeypatch, lambda: OutcomeGoal.objects.filter(pk=outcome_goal.pk).delete())
        with pytest.raises(ValidationError) as exc:
            sc.lock_goal_for_write(outcome_goal)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]


class TestLockEventForCaseLink:
    """``lock_event_for_case_link``: Vorlesen, Zielfall-Lesung, Fall- und Ereignissperre."""

    def test_abfragen_tragen_ihre_bedingungen(self, facility, client_identified, staff_user, case_open, sample_event):
        """x_lock_event_for_case_link__mutmut_18 (``pk`` im Vorlesen), _19
        (Einrichtung im Vorlesen), _45 (``pk`` der Zielfall-Lesung), _46
        (Einrichtung der Zielfall-Lesung), _80 (``pk IN`` der Fallsperre), _81
        (Einrichtung der Fallsperre), _103 (``pk`` der Ereignissperre), _105
        (Person in der Ereignissperre). Umhaengen von einem Fall an einen
        zweiten, damit die Fallsperre zwei Zeilen traegt.

        Fuer _81 ist dieser Pin ein ausdruecklicher Scoping-Vertrag
        (Tiefenverteidigung), kein Nachweis eines ausgefuehrten fremden
        Zugriffs: der Zielfall ist bereits in der Zielfall-Lesung
        einrichtungsgeprueft, und der zusammengesetzte Fremdschluessel
        ``(case_id, facility_id)`` des Ereignisses schliesst einen
        einrichtungsfremden bisherigen Fall fuer jeden committeten Zustand
        aus. Er ist ``DEFERRABLE INITIALLY DEFERRED`` — innerhalb einer
        offenen Transaktion ist ein solcher Bezug voruebergehend moeglich;
        die Fallsperre bleibt deshalb selbst auf die Einrichtung begrenzt."""
        ziel = _fall(facility, client_identified, staff_user, alter_tage=1)
        Event.objects.filter(pk=sample_event.pk).update(case=case_open)
        with _mitschnitt() as abfragen:
            sc.lock_event_for_case_link(sample_event, ziel)
        # Vorlesen des Ereignisses (_18, _19)
        assert any(a.traegt(id=[sample_event.pk], facility_id=[facility.pk]) for a in abfragen.lesungen("core_event"))
        # Zielfall-Lesung (_45, _46)
        assert any(a.traegt(id=[ziel.pk], facility_id=[facility.pk]) for a in abfragen.lesungen("core_case"))
        # Fallsperre (_80, _81): jede Sperre auf core_case genau die beteiligten Faelle in der Einrichtung
        fallsperren = abfragen.sperren("core_case")
        assert fallsperren
        assert all(a.traegt(id=[case_open.pk, ziel.pk], facility_id=[facility.pk]) for a in fallsperren)
        # Ereignissperre (_103, _105)
        ereignissperren = abfragen.sperren("core_event")
        assert ereignissperren
        assert all(
            a.traegt(
                id=[sample_event.pk],
                facility_id=[facility.pk],
                client_id=[client_identified.pk],
                case_id=[case_open.pk],
            )
            for a in ereignissperren
        )

    def test_aelteres_ereignis_neben_neuerem_einer_anderen_person(
        self, facility, client_identified, andere_person, staff_user, doc_type_contact
    ):
        """x_lock_event_for_case_link__mutmut_18: ohne ``pk`` liest das
        Vorlesen Person und Fall des neuesten Ereignisses der Einrichtung
        (Sortierung ``-occurred_at``) und lehnt das adressierte mit GONE ab."""
        eigener_fall = _fall(facility, client_identified, staff_user, alter_tage=3)
        adressiert = _ereignis(
            facility, client_identified, doc_type_contact, staff_user, case=eigener_fall, alter_tage=2
        )
        fremder_fall = _fall(facility, andere_person, staff_user, alter_tage=0)
        _ereignis(facility, andere_person, doc_type_contact, staff_user, case=fremder_fall, alter_tage=0)
        locked, target, previous = sc.lock_event_for_case_link(adressiert)
        assert locked.pk == adressiert.pk
        assert target is None
        assert previous.pk == eigener_fall.pk

    def test_ereignis_mit_fremder_einrichtung_im_speicher_wird_abgewiesen(self, sample_event, other_facility):
        """x_lock_event_for_case_link__mutmut_19: ohne ``facility_id`` im
        Vorlesen uebernimmt der Vorgang die Einrichtung aus der Datenbank und
        liefert das Ereignis aus, obwohl der Aufrufer es einer anderen
        Einrichtung zuschrieb."""
        sample_event.facility_id = other_facility.pk
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event)
        assert _meldungen(exc) == [str(sc.EVENT_WRITE_GONE_MESSAGE)]

    def test_aelterer_zielfall_neben_neuerem_fall_einer_anderen_person(
        self, facility, client_identified, andere_person, staff_user, sample_event
    ):
        """x_lock_event_for_case_link__mutmut_45: ohne ``pk`` liest die
        Zielfall-Lesung die Person des neuesten Falls der Einrichtung und
        meldet 'Person passt nicht' fuer den richtigen Zielfall."""
        ziel = _fall(facility, client_identified, staff_user, alter_tage=5)
        _fall(facility, andere_person, staff_user, alter_tage=0)
        locked, target, previous = sc.lock_event_for_case_link(sample_event, ziel)
        assert locked.pk == sample_event.pk
        assert target.pk == ziel.pk
        assert previous is None

    def test_zielfall_einer_fremden_einrichtung_wird_ohne_sperre_abgewiesen(
        self, other_facility, fremde_person, staff_user, sample_event
    ):
        """x_lock_event_for_case_link__mutmut_46: ohne ``facility_id`` liest
        die Zielfall-Lesung die fremde Person, sperrt sie und meldet 'Person
        passt nicht' — erwartet ist GONE vor jeder Sperre."""
        fremder_fall = _fall(other_facility, fremde_person, staff_user, alter_tage=0)
        with _mitschnitt() as abfragen, pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event, fremder_fall)
        assert _meldungen(exc) == [str(sc.CASE_WRITE_GONE_MESSAGE)]
        assert [a.sql for a in abfragen if a.sperrt] == []

    def test_fallsperre_umfasst_nur_die_beteiligten_faelle(
        self, facility, client_identified, andere_person, staff_user, case_open, sample_event
    ):
        """x_lock_event_for_case_link__mutmut_80: ohne ``pk IN`` sperrte die
        Fallsperre jeden Fall der Einrichtung — hier auch den unbeteiligten
        Fall der anderen Person."""
        unbeteiligt = _fall(facility, andere_person, staff_user, alter_tage=0)
        with _mitschnitt() as abfragen:
            sc.lock_event_for_case_link(sample_event, case_open)
        fallsperren = abfragen.sperren("core_case")
        assert fallsperren
        assert all(a.traegt(id=[case_open.pk]) for a in fallsperren)
        assert all(unbeteiligt.pk.hex not in a.bedingungen.get("core_case.id", set()) for a in fallsperren)

    def test_liefert_das_adressierte_ereignis(
        self, facility, client_identified, staff_user, doc_type_contact, case_open
    ):
        """x_lock_event_for_case_link__mutmut_103: ohne ``pk`` sperrte und
        lieferte die Ereignissperre das neueste Ereignis derselben Person am
        selben Fall."""
        adressiert = _ereignis(facility, client_identified, doc_type_contact, staff_user, case=case_open, alter_tage=2)
        _ereignis(facility, client_identified, doc_type_contact, staff_user, case=case_open, alter_tage=0)
        locked, _target, previous = sc.lock_event_for_case_link(adressiert)
        assert locked.pk == adressiert.pk
        assert previous.pk == case_open.pk

    def test_ereignis_wechselt_waehrend_der_sperrwartezeit_die_person(
        self, case_open, sample_event, andere_person, monkeypatch
    ):
        """x_lock_event_for_case_link__mutmut_105: ohne ``client_id`` in der
        Ereignissperre ginge ein Ereignis durch, das ein paralleles
        Zusammenfuehren an eine nie gesperrte Person gehaengt hat; der Fall
        bleibt dabei unveraendert."""
        Event.objects.filter(pk=sample_event.pk).update(case=case_open)
        _wechsel_im_mengengate(
            monkeypatch, lambda: Event.objects.filter(pk=sample_event.pk).update(client=andere_person)
        )
        with pytest.raises(ValidationError) as exc:
            sc.lock_event_for_case_link(sample_event)
        assert _meldungen(exc) == [str(sc.EVENT_WRITE_GONE_MESSAGE)]


class TestMitschnitt:
    """Positivproben des Mitschnitts: gleichwertige Formulierungen gelten gleich."""

    def test_gleichheit_und_einelementige_in_liste_sind_gleichwertig(self):
        pk, einrichtung = uuid.uuid4(), uuid.uuid4()
        gleichheit = _Abfrage(
            'SELECT "core_case"."client_id" FROM "core_case" '
            'WHERE ("core_case"."id" = %s AND "core_case"."facility_id" = %s)',
            [pk, str(einrichtung)],
        )
        in_liste = _Abfrage(
            'SELECT "core_case"."facility_id", "core_case"."client_id" AS "c" FROM "core_case" '
            'WHERE ("core_case"."facility_id" IN (%s) AND "core_case"."id"=%s)',
            [str(einrichtung), pk.hex],
        )
        for abfrage in (gleichheit, in_liste):
            assert abfrage.tabelle == "core_case"
            assert not abfrage.sperrt
            assert abfrage.traegt(id=[pk], facility_id=[einrichtung])

    def test_sperrmarker_und_in_liste_mehrerer_werte(self):
        a, b, einrichtung = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        abfrage = _Abfrage(
            'SELECT * FROM "core_case" WHERE "core_case"."facility_id" = %s AND "core_case"."id" IN (%s, %s) '
            "ORDER BY 1 FOR UPDATE",
            [einrichtung, b, a],
        )
        assert abfrage.sperrt
        assert abfrage.traegt(id=[a, b], facility_id=[einrichtung])
        assert not abfrage.traegt(id=[a])
