"""Mutations-Pins der Checkpoint-Triage 0.94 — ``core.services.client.main``.

Gepinnt wird das Prio-1-Set der Survivor-Triage des Checkpoint-Delta-Laufs
zum Meilenstein 0.94.
Modul unter Test: ``src/core/services/client/main.py`` — ausschliesslich der
Sperr-Helfer :func:`core.services.client.main._lock_client_event_rows`
(Quellzeilen 1424/1425).

Gepinnt sind zwei Survivors mit identischer Wirkung: die ZWEITE Stufe der
kanonischen Sperr-Ordnung ``core_client`` -> ``core_event`` (Refs #2108/#2122,
ADR-050 D5.5) wird stillschweigend zur No-Op bzw. greift auf der falschen
Zeilenmenge, waehrend das ``SELECT ... FOR UPDATE`` auf ``core_event``
weiterhin abgesetzt wird:

* ``x__lock_client_event_rows__mutmut_4`` — ``.filter(client_id=client.pk)``
  wird zu ``.filter(client_id=None)``: gesperrt werden die Ereignisse OHNE
  Person derselben Einrichtung statt der Ereignisse DIESER Person.
* ``x__lock_client_event_rows__mutmut_5`` — ``Event.objects.for_facility(
  client.facility_id)`` wird zu ``for_facility(None)``: ``for_facility``
  faltet sich auf ``.filter(facility=None)`` auf (``core/models/managers.py``),
  ``Event.facility`` ist ein nicht-nullbarer FK — die Sperrmenge ist damit
  garantiert leer.

Beide ueberlebten den Delta-Lauf, weil die vorhandenen Sperr-Pins in
``test_retention_hold_toctou_rest.py`` per Regex nur die TABELLEN-Namen und
die Reihenfolge der ``FOR UPDATE``-Statements pruefen (nie das WHERE-Praedikat)
und die Renn-Tests in ``test_client_anonymize_hold_race.py`` seit Refs
#2144/#2123 schon an der Personenzeile blockieren. Diese Datei schliesst genau
diese Luecke — einmal ueber die gebundenen Query-Parameter (billig) und einmal
ueber einen deterministischen ``NOWAIT``-Sperrversuch aus einer zweiten
DB-Verbindung (verhaltensecht, kein Zeitfenster).

Konventionen wie ``src/tests/test_mutation_pins_091_cut.py``: eine Testklasse
je Funktion, jeder Test nennt seine Mutanten in der Docstring samt dem Wert,
der beim Mutanten abweicht; Zusicherungen vergleichen EXAKT (``==``), nie per
Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); KEINE Imports aus
``test_rls_functional`` (modulweiter Skip unter ``MUTANT_UNDER_TEST`` wuerde
jeden Pin entwerten); keine Fixture-Faken, ausschliesslich echte
Model-Objekte. Ein Sprachkontext per ``translation.override`` ist hier nicht
noetig: gepinnt wird kein Text, sondern eine Sperrmenge.

Beide Tests brauchen PostgreSQL (``FOR UPDATE``/``NOWAIT``, Refs #1829) und
tragen deshalb ``pytest.mark.postgres``.
"""

import threading
import uuid
from contextlib import contextmanager

import pytest
from django.db import OperationalError, connection, connections, transaction

from core.models import Client, DocumentType, Event, Organization
from core.services.client.main import _lock_client_event_rows

pytestmark = pytest.mark.postgres

#: Obergrenze fuer den Sonden-Thread. Grosszuegig: die Sonde ist wegen
#: ``NOWAIT`` nicht blockierend, ein Ueberschreiten waere ein echter Defekt.
_SONDEN_TIMEOUT = 30


def _als_uuid(wert):
    """Normalisiert einen gebundenen Query-Parameter auf ``uuid.UUID``.

    Django reicht UUID-Werte je nach Backend/Treiber als ``uuid.UUID`` oder als
    Hex-Zeichenkette an den Cursor; der Pin soll an dieser Treiber-Freiheit
    nicht haengen. Nicht-UUID-Werte bleiben unveraendert, damit ein
    unerwarteter Parameter im Vergleich sichtbar wird statt zu maskieren.
    """
    try:
        return uuid.UUID(str(wert))
    except (AttributeError, TypeError, ValueError):
        return wert


@contextmanager
def _erfasse_event_sperr_statements():
    """Sammelt ``(sql, params)`` aller ``FOR UPDATE``-Statements auf ``core_event``.

    ``connection.execute_wrapper`` sieht das Statement mit den noch getrennten
    Bind-Parametern — genau das, was die beiden Mutanten veraendern. Die
    Auswahl der Statements ist bewusst grob (Tabellen- und Klausel-Name), die
    ZUSICHERUNG darueber dann exakt.
    """
    erfasst: list[tuple[str, tuple]] = []

    def wrapper(execute, sql, params, many, context):
        if "core_event" in sql and "FOR UPDATE" in sql:
            erfasst.append((sql, tuple(params or ())))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(wrapper):
        yield erfasst


def _sperrstatus(event_pk) -> str:
    """Prueft aus einer SEPARATEN DB-Verbindung, ob eine Ereigniszeile gesperrt ist.

    Eigener Thread -> eigene Django-Connection (Muster aus
    ``test_audit_lock_scan_release.py``). ``FOR UPDATE NOWAIT`` bricht sofort
    mit ``lock_not_available`` (SQLSTATE 55P03 ->
    ``django.db.OperationalError``) ab, wenn die Zeile fremd gesperrt ist —
    kein ``sleep``, kein Zeitfenster, deterministisch.

    Rueckgabe: ``"gesperrt"``, ``"frei"`` (Zeile sichtbar und sperrbar) oder
    ``"unsichtbar"`` (kein Treffer — traete etwa bei greifender RLS auf und
    soll den Pin scheitern lassen, statt still als ``"frei"`` durchzugehen).
    """
    ergebnis: dict[str, str] = {}
    fehler: list[BaseException] = []

    def worker():
        conn = connections.create_connection("default")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT "id" FROM "core_event" WHERE "id" = %s::uuid FOR UPDATE NOWAIT',
                    [str(event_pk)],
                )
                zeilen = cur.fetchall()
            ergebnis["status"] = "frei" if len(zeilen) == 1 else "unsichtbar"
        except OperationalError:
            ergebnis["status"] = "gesperrt"
        except BaseException as exc:  # pragma: no cover - Diagnose im Hauptthread
            fehler.append(exc)
        finally:
            conn.close()

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=_SONDEN_TIMEOUT)
    if fehler:
        raise fehler[0]
    return ergebnis.get("status", "zeitueberschreitung")


class TestLockClientEventRows:
    """``_lock_client_event_rows`` sperrt genau die Ereigniszeilen EINER Person.

    Der Helfer zieht die Ereigniszeilen der Person unter dieselbe Transaktion
    wie die zuvor gesperrte Personenzeile (Aufrufer: ``anonymize_client_now``,
    Quellzeile 1501, und ``anonymize_eligible_soft_deleted_clients``,
    Quellzeile 1594). Faellt diese zweite Stufe aus, committet ein parallel
    angeordneter EREIGNIS-Hold neben der Anonymisierung her und die Ereignisse
    werden trotz Hold redigiert (Spoliation).

    Beide gepinnten Mutanten lassen das ``FOR UPDATE`` auf ``core_event``
    stehen und aendern nur das Praedikat — sie sind daher weder durch die
    Tabellen-/Reihenfolge-Pins noch durch die vorhandenen Renn-Tests zu
    erkennen. Nicht aequivalent: die gesperrte Zeilenmenge ist sowohl an den
    Bind-Parametern als auch per ``NOWAIT``-Sperrversuch beobachtbar.
    """

    @pytest.mark.django_db
    def test_sperrpraedikat_bindet_einrichtung_und_person(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Pin: ``x__lock_client_event_rows__mutmut_4`` — ``.filter(client_id=client.pk)``
        -> ``.filter(client_id=None)``; und Pin:
        ``x__lock_client_event_rows__mutmut_5`` — ``for_facility(client.facility_id)``
        -> ``for_facility(None)``.

        Gepinnt werden die gebundenen Parameter des einen ``FOR UPDATE``-
        Statements auf ``core_event``. Im Original sind es exakt zwei:
        ``(facility_id, client_id)``.

        Abweichung beim Mutanten 4: ``client_id=None`` wird von Django zu
        ``client_id IS NULL`` aufgeloest — der zweite Parameter entfaellt, die
        erfasste Parameterliste ist ``(facility_id,)``.
        Abweichung beim Mutanten 5: ``facility=None`` wird zu
        ``facility_id IS NULL`` — die erfasste Parameterliste ist
        ``(client_id,)``.
        In beiden Faellen scheitert der Gleichheitsvergleich.
        """
        # Bestand, der einen Zufallstreffer ausschliesst: ein Ereignis OHNE
        # Person (Ziel des Mutanten 4) in derselben Einrichtung.
        Event.objects.create(
            facility=facility,
            client=None,
            document_type=doc_type_contact,
            occurred_at=client_identified.created_at,
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=client_identified.created_at,
            created_by=staff_user,
        )

        with _erfasse_event_sperr_statements() as erfasst, transaction.atomic():
            _lock_client_event_rows(client_identified)

        # Genau EIN Sperr-Statement auf ``core_event`` — nicht mehr (doppelte
        # Sperre) und nicht weniger (Stufe faellt ganz weg).
        assert len(erfasst) == 1

        _sql, params = erfasst[0]
        assert tuple(_als_uuid(p) for p in params) == (
            client_identified.facility_id,
            client_identified.pk,
        )

    @pytest.mark.django_db(transaction=True)
    def test_nur_die_ereigniszeilen_der_person_sind_wirklich_gesperrt(self):
        """Pin: ``x__lock_client_event_rows__mutmut_4`` — ``.filter(client_id=client.pk)``
        -> ``.filter(client_id=None)``; und Pin:
        ``x__lock_client_event_rows__mutmut_5`` — ``for_facility(client.facility_id)``
        -> ``for_facility(None)``.

        Verhaltensechter Gegenbeweis zum Parameter-Pin: waehrend der Helfer die
        Sperre haelt, probt eine zweite DB-Verbindung drei Ereigniszeilen per
        ``FOR UPDATE NOWAIT``. Im Original ist genau die Zeile der Person
        gesperrt, die personenlose Zeile derselben Einrichtung und die Zeile
        der zweiten Einrichtung sind frei.

        Abweichung beim Mutanten 4: gesperrt wird die personenlose Zeile — die
        Sonde liefert ``("frei", "gesperrt", "frei")``.
        Abweichung beim Mutanten 5: ``facility_id IS NULL`` trifft keine Zeile,
        die Sperrmenge ist leer — die Sonde liefert ``("frei", "frei",
        "frei")``.
        """
        org = Organization.objects.create(name="Pin-Org 0.94")
        stelle = org.facilities.create(name="Pin-Stelle A")
        fremde_stelle = org.facilities.create(name="Pin-Stelle B")
        typ = DocumentType.objects.create(
            facility=stelle,
            name="Kontakt (Pin)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        fremder_typ = DocumentType.objects.create(
            facility=fremde_stelle,
            name="Kontakt (Pin, fremd)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        person = Client.objects.create(
            facility=stelle,
            pseudonym="Pin-Person",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        fremde_person = Client.objects.create(
            facility=fremde_stelle,
            pseudonym="Pin-Person (fremd)",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        ereignis_der_person = Event.objects.create(
            facility=stelle,
            client=person,
            document_type=typ,
            occurred_at=person.created_at,
        )
        ereignis_ohne_person = Event.objects.create(
            facility=stelle,
            client=None,
            document_type=typ,
            occurred_at=person.created_at,
        )
        ereignis_fremde_stelle = Event.objects.create(
            facility=fremde_stelle,
            client=fremde_person,
            document_type=fremder_typ,
            occurred_at=person.created_at,
        )

        try:
            with transaction.atomic():
                _lock_client_event_rows(person)
                status = (
                    _sperrstatus(ereignis_der_person.pk),
                    _sperrstatus(ereignis_ohne_person.pk),
                    _sperrstatus(ereignis_fremde_stelle.pk),
                )
        finally:
            # ``transaction=True`` laesst committete Zeilen stehen, bis
            # pytest-django die Tabellen leert — die Sonden-Verbindungen
            # muessen aber in jedem Fall zu sein.
            connections.close_all()

        assert status == ("gesperrt", "frei", "frei")
