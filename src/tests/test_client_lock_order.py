"""PK-Sperrfolge auf Personenzeilen (Refs #2268).

Projektweite Regel: **mehrere Zeilen derselben Tabelle werden immer in
aufsteigender PK-Reihenfolge gesperrt.** Fuer ``core_client`` laeuft das ueber
EINEN gemeinsamen Helfer, der eine PK-Menge in EINER ``select_for_update``-
Abfrage nimmt.

Ohne die Regel sperrten die Pfade in *semantischer* Reihenfolge: die
Fallzuordnung erst die Fallperson, dann die Ereignisperson. Fuer zwei Personen
A < B ergibt das mit einem gleichzeitigen Zusammenfuehren A->B einen Zyklus —

    Fallzuordnung:    haelt B  ->  wartet auf A
    Zusammenfuehren:  haelt A  ->  wartet auf B

— den PostgreSQL mit ``40P01`` (``deadlock detected``) aufloest: eine der
beiden Anfragen endet mit einem Serverfehler. Kein Datenverlust, aber ein
vermeidbarer Fehlerpfad unter Last.

Die Sequenz-Guards pinnen die Reihenfolge **am ausgefuehrten SQL** (Muster
``test_restriction_lock_order``), der Renn-Nachweis am echten Zyklus zweier
Verbindungen. Beide sind beobachtend gebaut: kein ``assert`` im gesperrten
Fenster, endliche Wartezeiten auf beiden Seiten (Barriere, Lock-Zeitgrenze der
Datenbank, Join-Deadline) und die Auswertung erst NACH dem Rennen.
"""

import re
import threading
import time

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Case, Client, DocumentType, Event, Facility, Organization, User

#: ``FOR UPDATE``-Statements auf der Personentabelle. Bewusst grob, damit die
#: Erkennung nicht an Django-Versionsdetails der SQL-Formatierung haengt.
_CLIENT_LOCK_RE = re.compile(r'FROM "core_client"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)

#: UUIDs erscheinen im ausgefuehrten SQL als Hex ohne Bindestriche.
_UUID_RE = re.compile(r"'([0-9a-f]{32})'::uuid")

#: Lock-Zeitgrenze der Renn-Verbindungen: keine Seite wartet unbegrenzt.
_LOCK_TIMEOUT = "20s"
#: Deadline fuer Barrieren und ``join``.
_JOIN_TIMEOUT = 60
#: Deadline der Beobachtung „Faden haengt an einer Sperre".
_WAIT_DEADLINE = 30


def _hex(pk):
    return str(pk).replace("-", "")


def _client_lock_statements(captured):
    """Alle Sperr-Statements auf ``core_client`` in Ausfuehrungsreihenfolge."""
    return [q["sql"] for q in captured.captured_queries if _CLIENT_LOCK_RE.search(q["sql"])]


def _locked_client_ids(captured, among):
    """Die gesperrten Personen-PKs (Hex) in Sperr-Reihenfolge.

    Innerhalb eines Statements ist die Reihenfolge die der ``IN``-Liste; die
    Datenbank sperrt sie in der ``ORDER BY``-Ordnung, die derselbe Helfer
    setzt (beides wird gepinnt).
    """
    wanted = {_hex(pk) for pk in among}
    ids = []
    for sql in _client_lock_statements(captured):
        ids.extend(found for found in _UUID_RE.findall(sql) if found in wanted)
    return ids


def _zwei_personen(facility, user):
    """Zwei Personen, zurueckgegeben als ``(kleinere_pk, groessere_pk)``."""
    first = Client.objects.create(facility=facility, pseudonym="Sperrfolge-1", created_by=user)
    second = Client.objects.create(facility=facility, pseudonym="Sperrfolge-2", created_by=user)
    return tuple(sorted((first, second), key=lambda c: str(c.pk)))


@pytest.mark.django_db
class TestSequenzDerPersonensperren:
    """Sequenz-Guard je Pfad — deterministisch am Query-Mitschnitt."""

    def test_fallzuordnung_sperrt_beide_personen_in_pk_ordnung(self, facility, staff_user, doc_type_contact):
        """Fall(B) x Ereignis(A): die Sperren muessen trotzdem A vor B fallen.

        Der Fall traegt die groessere PK, das Ereignis die kleinere — die
        semantische Reihenfolge ist also genau die verkehrte. Die fachliche
        Zurueckweisung („unterschiedliche Personen") folgt NACH den Sperren
        und bleibt unveraendert.
        """
        from core.services.case import assign_event_to_case

        kleiner, groesser = _zwei_personen(facility, staff_user)
        case = Case.objects.create(
            facility=facility, client=groesser, title="Fall B", status=Case.Status.OPEN, created_by=staff_user
        )
        event = Event.objects.create(
            facility=facility,
            client=kleiner,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        with CaptureQueriesContext(connection) as captured:
            with pytest.raises(ValidationError, match="Person des Ereignisses"):
                assign_event_to_case(case, event, staff_user)

        gesperrt = _locked_client_ids(captured, [kleiner.pk, groesser.pk])
        assert set(gesperrt) == {_hex(kleiner.pk), _hex(groesser.pk)}, f"nicht beide Personen gesperrt: {gesperrt}"
        assert gesperrt == sorted(gesperrt), f"Personensperren nicht in PK-Ordnung: {gesperrt}"

    def test_fallzuordnung_nimmt_beide_sperren_in_einer_abfrage(self, facility, staff_user, doc_type_contact):
        """EIN Statement — zwei Einzelsperren lassen ein Fenster dazwischen."""
        from core.services.case import assign_event_to_case

        kleiner, groesser = _zwei_personen(facility, staff_user)
        case = Case.objects.create(
            facility=facility, client=groesser, title="Fall B", status=Case.Status.OPEN, created_by=staff_user
        )
        event = Event.objects.create(
            facility=facility,
            client=kleiner,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        with CaptureQueriesContext(connection) as captured:
            with pytest.raises(ValidationError):
                assign_event_to_case(case, event, staff_user)

        statements = _client_lock_statements(captured)
        assert len(statements) == 1, f"erwartet EIN gemeinsames Sperr-Statement, war: {len(statements)}"
        assert "ORDER BY" in statements[0].upper(), statements[0]

    def test_zusammenfuehren_sperrt_beide_personen_in_pk_ordnung(self, facility, staff_user, lead_user):
        """Quelle(B) -> Ziel(A): auch hier faellt A zuerst."""
        from core.services.client import merge_clients

        kleiner, groesser = _zwei_personen(facility, staff_user)
        with CaptureQueriesContext(connection) as captured:
            merge_clients(groesser, kleiner, lead_user)

        gesperrt = _locked_client_ids(captured, [kleiner.pk, groesser.pk])
        assert gesperrt[:2] == [_hex(kleiner.pk), _hex(groesser.pk)], f"Merge-Sperren nicht PK-geordnet: {gesperrt}"
        statements = _client_lock_statements(captured)
        assert "ORDER BY" in statements[0].upper(), statements[0]

    def test_einschraenkungs_gate_sperrt_ueber_den_gemeinsamen_helfer(self, facility, staff_user, monkeypatch):
        """Auch die EINZELNE Sperre laeuft ueber den geordneten Helfer.

        Sonst gibt es zwei Formen desselben Zugriffs — und die zweite faellt
        beim naechsten Pfad, der sie kopiert, wieder aus der Ordnung.
        """
        from core.services.client import restriction as restriction_module

        kleiner, _groesser = _zwei_personen(facility, staff_user)
        original = restriction_module.lock_clients
        gesehen = []

        def _mitschnitt(refs, **kwargs):
            gesehen.append([str(getattr(ref, "pk", ref)) for ref in refs])
            return original(refs, **kwargs)

        monkeypatch.setattr(restriction_module, "lock_clients", _mitschnitt)
        with CaptureQueriesContext(connection) as captured:
            with transaction.atomic():
                restriction_module.assert_processing_allowed(kleiner, lock=True)

        assert gesehen == [[str(kleiner.pk)]], f"Gate sperrt am Helfer vorbei: {gesehen}"
        statements = _client_lock_statements(captured)
        assert len(statements) == 1, f"erwartet genau eine Sperre, war: {len(statements)}"
        assert "ORDER BY" in statements[0].upper(), statements[0]

    def test_einschraenkungs_gate_sperrt_mehrere_personen_geordnet(self, facility, staff_user):
        """Der Mehrfach-Einstieg des Gates: EIN Statement, PK-Ordnung."""
        from core.services.client.restriction import assert_processing_allowed_for

        kleiner, groesser = _zwei_personen(facility, staff_user)
        with CaptureQueriesContext(connection) as captured:
            with transaction.atomic():
                assert_processing_allowed_for([groesser, kleiner], lock=True)

        statements = _client_lock_statements(captured)
        assert len(statements) == 1, f"erwartet EIN gemeinsames Sperr-Statement, war: {len(statements)}"
        gesperrt = _locked_client_ids(captured, [kleiner.pk, groesser.pk])
        assert gesperrt == [_hex(kleiner.pk), _hex(groesser.pk)], f"nicht PK-geordnet: {gesperrt}"

    def test_gate_bleibt_ohne_personenbezug_abfragefrei(self, facility, staff_user):
        """``None`` ist kein Personenbezug — und darf nichts kosten."""
        from core.services.client.restriction import assert_processing_allowed_for

        with CaptureQueriesContext(connection) as captured:
            with transaction.atomic():
                assert_processing_allowed_for([None, None], lock=True)
        assert _client_lock_statements(captured) == []

    def test_gate_blockt_weiterhin_jede_der_beiden_personen(self, facility, staff_user):
        """Die Umstellung darf das Art.-18-Gate nicht entschaerfen."""
        from core.services.client.restriction import (
            PROCESSING_RESTRICTED_MESSAGE,
            assert_processing_allowed_for,
            restrict_processing,
        )

        kleiner, groesser = _zwei_personen(facility, staff_user)
        restrict_processing(groesser, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        with transaction.atomic():
            with pytest.raises(ValidationError) as fehler:
                assert_processing_allowed_for([kleiner, groesser], lock=True)
        assert str(PROCESSING_RESTRICTED_MESSAGE) in str(fehler.value)


@pytest.mark.django_db
class TestPersonenSperrHelfer:
    """Der gemeinsame Helfer selbst: idempotent, geordnet, gescopt."""

    def test_helfer_dedupliziert_und_ignoriert_none(self, facility, staff_user):
        from core.services.client.locking import lock_clients

        kleiner, groesser = _zwei_personen(facility, staff_user)
        with CaptureQueriesContext(connection) as captured:
            with transaction.atomic():
                locked = lock_clients([groesser, kleiner.pk, groesser.pk, None])

        assert set(locked) == {kleiner.pk, groesser.pk}
        assert len(_client_lock_statements(captured)) == 1
        gesperrt = _locked_client_ids(captured, [kleiner.pk, groesser.pk])
        assert gesperrt == [_hex(kleiner.pk), _hex(groesser.pk)], gesperrt

    def test_helfer_ohne_pks_fragt_nicht(self, facility, staff_user):
        from core.services.client.locking import lock_clients

        # Die Klammer liegt AUSSEN: der Savepoint des ``atomic`` gehoert nicht
        # zur Rechnung, die Aussage ist „der Helfer selbst fragt nichts".
        with transaction.atomic():
            with CaptureQueriesContext(connection) as captured:
                assert lock_clients([None]) == {}
        assert captured.captured_queries == []

    def test_helfer_bleibt_in_der_einrichtung(self, organization, facility, staff_user):
        """Mit ``facility`` sperrt der Helfer nur Zeilen dieser Einrichtung."""
        from core.services.client.locking import lock_clients

        eigene = Client.objects.create(facility=facility, pseudonym="Eigene", created_by=staff_user)
        fremde_stelle = Facility.objects.create(organization=organization, name="Fremde Stelle")
        fremde = Client.objects.create(facility=fremde_stelle, pseudonym="Fremde")

        with transaction.atomic():
            locked = lock_clients([eigene, fremde], facility=facility)
        assert set(locked) == {eigene.pk}

    def test_helfer_liefert_frisch_gelesene_zeilen(self, facility, staff_user):
        from core.services.client.locking import lock_clients

        person = Client.objects.create(facility=facility, pseudonym="Frisch", created_by=staff_user)
        Client.objects.filter(pk=person.pk).update(pseudonym="Geaendert")
        with transaction.atomic():
            locked = lock_clients([person])
        assert locked[person.pk].pseudonym == "Geaendert"


def _renn_bestand():
    """Committeter Aufbau — die Faeden bringen eigene Verbindungen mit."""
    org = Organization.objects.create(name="Sperrfolge-Org")
    facility = Facility.objects.create(organization=org, name="Sperrfolge-Stelle")
    lead = User.objects.create_user(
        username="sperrfolge-lead", password="x" * 40, facility=facility, role=User.Role.LEAD, is_staff=True
    )
    staff = User.objects.create_user(
        username="sperrfolge-staff", password="x" * 40, facility=facility, role=User.Role.STAFF, is_staff=True
    )
    doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
    kleiner, groesser = _zwei_personen(facility, staff)
    case = Case.objects.create(
        facility=facility, client=groesser, title="Renn-Fall", status=Case.Status.OPEN, created_by=staff
    )
    event = Event.objects.create(
        facility=facility,
        client=kleiner,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff,
    )
    return facility, lead, staff, kleiner, groesser, case, event


@pytest.fixture
def renn_bestand(transactional_db):
    """Committeter Aufbau; das Aufraeumen macht der Flush von pytest-django."""
    yield _renn_bestand()
    connections.close_all()


def _backend_pid():
    with connection.cursor() as cur:
        cur.execute("SET lock_timeout = %s", [_LOCK_TIMEOUT])
        cur.execute("SELECT pg_backend_pid()")
        return cur.fetchone()[0]


def _wartet_auf_sperre(pid, deadline):
    """Wartet beobachtend, bis *pid* an einer Sperre haengt (oder die Zeit ab).

    Der Mitschnitt kommt aus ``pg_stat_activity`` — dieselbe Rolle sieht die
    Details ihrer eigenen Verbindungen. Kein ``sleep`` ohne Deadline.
    """
    ende = time.monotonic() + deadline
    while time.monotonic() < ende:
        with connections["default"].cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE pid = %s AND wait_event_type = 'Lock'",
                [pid],
            )
            if cur.fetchone()[0]:
                return True
        time.sleep(0.05)
    return False


@pytest.mark.django_db(transaction=True)
class TestRendezvousFallzuordnungGegenZusammenfuehren:
    """Der Zyklus aus dem Ticket — an zwei echten Verbindungen.

    Faden 1 ordnet das Ereignis der kleineren Person dem Fall der groesseren
    zu. Faden 2 haelt die kleinere Person (die Sperre, die das
    Zusammenfuehren in PK-Ordnung ohnehin als ERSTE nimmt — sie wird nur
    vorgezogen, damit der Mitspieler messbar ist) und verlangt danach die
    groessere.

    Ohne die PK-Ordnung haelt Faden 1 die groessere und wartet auf die
    kleinere: der Zyklus ist geschlossen, PostgreSQL meldet ``40P01``. Mit ihr
    wartet Faden 1, ohne selbst etwas zu halten — Faden 2 kommt durch, gibt
    frei, Faden 1 laeuft weiter.
    """

    def test_kein_sperrzyklus_zwischen_fallzuordnung_und_zusammenfuehren(self, renn_bestand):
        from core.services.case import assign_event_to_case
        from core.services.client import merge_clients

        facility, lead, staff, kleiner, groesser, case, event = renn_bestand
        pid_box: dict[str, int] = {}
        faden_1_bereit = threading.Event()
        faden_2_haelt_kleiner = threading.Event()
        ergebnis: dict[str, str] = {}

        def _merke(name, exc):
            sqlstate = getattr(getattr(exc, "__cause__", None), "sqlstate", None)
            ergebnis[name] = f"deadlock:{sqlstate}" if sqlstate == "40P01" else type(exc).__name__

        def _fallzuordnung():
            try:
                with transaction.atomic():
                    pid_box["eins"] = _backend_pid()
                    faden_1_bereit.set()
                    # Faden 2 haelt bereits die kleinere Person; erst danach
                    # darf die Zuordnung ihre Sperren nehmen.
                    faden_2_haelt_kleiner.wait(timeout=_JOIN_TIMEOUT)
                    assign_event_to_case(case, event, staff)
                ergebnis["eins"] = "zugeordnet"
            except ValidationError:
                # Die fachliche Zurueckweisung („unterschiedliche Personen") —
                # sie faellt NACH den Sperren und ist der Normalfall.
                ergebnis["eins"] = "personen-verschieden"
            except Exception as exc:  # noqa: BLE001
                _merke("eins", exc)
            finally:
                connections.close_all()

        def _zusammenfuehren():
            try:
                with transaction.atomic():
                    _backend_pid()
                    # Die erste Sperre des Zusammenfuehrens in PK-Ordnung.
                    list(Client.objects.select_for_update().filter(pk=kleiner.pk).only("pk"))
                    faden_2_haelt_kleiner.set()
                    # Beobachtend abwarten, bis Faden 1 an einer Sperre haengt
                    # — ohne diesen Punkt entschiede die Maschinenlaune, wer
                    # zuerst kommt, und das Rennen waere kein Nachweis.
                    faden_1_bereit.wait(timeout=_JOIN_TIMEOUT)
                    _wartet_auf_sperre(pid_box.get("eins", -1), _WAIT_DEADLINE)
                    merge_clients(kleiner, groesser, lead)
                ergebnis["zwei"] = "zusammengefuehrt"
            except ValidationError:
                ergebnis["zwei"] = "validierung"
            except Exception as exc:  # noqa: BLE001
                _merke("zwei", exc)
            finally:
                connections.close_all()

        faden_1 = threading.Thread(target=_fallzuordnung, name="sperrfolge-zuordnung")
        faden_2 = threading.Thread(target=_zusammenfuehren, name="sperrfolge-merge")
        faden_1.start()
        faden_2.start()
        faden_1.join(timeout=_JOIN_TIMEOUT)
        faden_2.join(timeout=_JOIN_TIMEOUT)

        assert not faden_1.is_alive() and not faden_2.is_alive(), f"Verklemmung: {ergebnis}"
        deadlocks = [name for name, wert in ergebnis.items() if wert.startswith("deadlock")]
        assert not deadlocks, f"Sperrzyklus zwischen Fallzuordnung und Zusammenfuehren: {ergebnis}"
        assert ergebnis.get("eins") == "personen-verschieden", ergebnis
        assert ergebnis.get("zwei") == "zusammengefuehrt", ergebnis
