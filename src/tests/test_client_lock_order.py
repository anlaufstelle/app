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

#: Die Sperrordnung muss ausdruecklich auf der PK liegen und aufsteigen.
_CLIENT_PK_ORDER_RE = re.compile(
    r'ORDER BY\s+"core_client"\."id"\s+ASC(?=\s|,)',
    re.IGNORECASE,
)

#: WHERE-Ausdruck einer Client-Sperre, um die Treffermenge vorab nachzustellen.
_CLIENT_LOCK_WHERE_RE = re.compile(
    r'FROM\s+"core_client"\s+WHERE\s+(?P<where>.*?)(?=\s+ORDER BY|\s+FOR UPDATE)',
    re.IGNORECASE | re.DOTALL,
)

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


class _ClientLockCapture(CaptureQueriesContext):
    """SQL-Mitschnitt plus Treffermenge jeder Client-Sperre vor Ausfuehrung.

    Die ``WHERE``-Bedingung wird mit denselben Parametern als ``SELECT id``
    nachgestellt. Die Probe sortiert nach ``id``; damit prueft der Guard die
    tatsaechlich von der Bedingung erfassten Zeilen statt die Darstellung der
    Werte in einer ``IN``-Liste.
    """

    def __init__(self, dbconnection=None):
        super().__init__(dbconnection or connection)
        self.locked_client_ids: list[list[str]] = []
        self._probing = False
        self._wrapper_cm = None

    def __enter__(self):
        self._wrapper_cm = self.connection.execute_wrapper(self._wrapper)
        self._wrapper_cm.__enter__()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, tb):
        try:
            return super().__exit__(exc_type, exc_value, tb)
        finally:
            self._wrapper_cm.__exit__(exc_type, exc_value, tb)

    def _wrapper(self, execute, sql, params, many, context):
        if self._probing or many or not _CLIENT_LOCK_RE.search(sql):
            return execute(sql, params, many, context)
        match = _CLIENT_LOCK_WHERE_RE.search(sql)
        assert match is not None, f"WHERE der Client-Sperre nicht aufloesbar: {sql}"
        probe_sql = (
            f'SELECT "core_client"."id" FROM "core_client" WHERE {match.group("where")} ORDER BY "core_client"."id" ASC'
        )
        self._probing = True
        try:
            with context["connection"].cursor() as cur:
                cur.execute(probe_sql, params)
                self.locked_client_ids.append([_hex(row[0]) for row in cur.fetchall()])
        finally:
            self._probing = False
        return execute(sql, params, many, context)


def _assert_ordered_client_lock(captured, among):
    """Pinnt Anweisung, PK-Sortierung und tatsaechliche Treffermenge."""
    statements = _client_lock_statements(captured)
    assert len(statements) == 1, f"erwartet EIN gemeinsames Sperr-Statement, war: {len(statements)}"
    statement = statements[0]
    assert "FOR UPDATE" in statement.upper(), statement
    assert _CLIENT_PK_ORDER_RE.search(statement), f"Client-Sperre nicht aufsteigend nach PK: {statement}"

    assert len(captured.locked_client_ids) == 1, captured.locked_client_ids
    expected = sorted(_hex(pk) for pk in among)
    locked = captured.locked_client_ids[0]
    assert locked == expected, f"tatsaechlich gesperrte Personen nicht PK-geordnet: {locked}"
    return statement


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

        with _ClientLockCapture() as captured:
            with pytest.raises(ValidationError, match="Person des Ereignisses"):
                assign_event_to_case(case, event, staff_user)

        _assert_ordered_client_lock(captured, [kleiner.pk, groesser.pk])

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

        with _ClientLockCapture() as captured:
            with pytest.raises(ValidationError):
                assign_event_to_case(case, event, staff_user)

        _assert_ordered_client_lock(captured, [kleiner.pk, groesser.pk])

    def test_zusammenfuehren_sperrt_beide_personen_in_pk_ordnung(self, facility, staff_user, lead_user):
        """Quelle(B) -> Ziel(A): auch hier faellt A zuerst."""
        from core.services.client import merge_clients

        kleiner, groesser = _zwei_personen(facility, staff_user)
        with _ClientLockCapture() as captured:
            merge_clients(groesser, kleiner, lead_user)

        _assert_ordered_client_lock(captured, [kleiner.pk, groesser.pk])

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
        with _ClientLockCapture() as captured:
            with transaction.atomic():
                restriction_module.assert_processing_allowed(kleiner, lock=True)

        assert gesehen == [[str(kleiner.pk)]], f"Gate sperrt am Helfer vorbei: {gesehen}"
        _assert_ordered_client_lock(captured, [kleiner.pk])

    def test_einschraenkungs_gate_sperrt_mehrere_personen_geordnet(self, facility, staff_user):
        """Der Mehrfach-Einstieg des Gates: EIN Statement, PK-Ordnung."""
        from core.services.client.restriction import assert_processing_allowed_for

        kleiner, groesser = _zwei_personen(facility, staff_user)
        with _ClientLockCapture() as captured:
            with transaction.atomic():
                assert_processing_allowed_for([groesser, kleiner], lock=True)

        _assert_ordered_client_lock(captured, [kleiner.pk, groesser.pk])

    def test_guard_verwirft_absteigend_sortierten_helfer(self, facility, staff_user, monkeypatch):
        """Negativbeleg: eine falsche Helfer-Sortierung darf nicht gruen sein."""
        from core.services.client import restriction as restriction_module

        kleiner, groesser = _zwei_personen(facility, staff_user)

        def _falsch_sortiert(refs, **kwargs):
            pks = [getattr(ref, "pk", ref) for ref in refs]
            rows = Client.objects.filter(pk__in=pks).select_for_update().order_by("-pk")
            return {row.pk: row for row in rows}

        monkeypatch.setattr(restriction_module, "lock_clients", _falsch_sortiert)
        with _ClientLockCapture() as captured:
            with transaction.atomic():
                restriction_module.assert_processing_allowed_for([kleiner, groesser], lock=True)

        with pytest.raises(AssertionError, match="nicht aufsteigend nach PK"):
            _assert_ordered_client_lock(captured, [kleiner.pk, groesser.pk])

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
        with _ClientLockCapture() as captured:
            with transaction.atomic():
                locked = lock_clients([groesser, kleiner.pk, groesser.pk, None])

        assert set(locked) == {kleiner.pk, groesser.pk}
        _assert_ordered_client_lock(captured, [kleiner.pk, groesser.pk])

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


def _haelt_clientzeilensperre(pid):
    """Belegt PID-gebunden eine ausgefuehrte Zeilensperre auf ``core_client``.

    PostgreSQL fuehrt unbestrittene Tupelsperren nicht einzeln in ``pg_locks``.
    Die Kombination aus ``RowShareLock`` auf der konkreten Relation und dem
    exklusiven Transaktions-ID-Lock belegt jedoch die sperrende Transaktion.
    """
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM pg_locks AS relation_lock
                    JOIN pg_class ON pg_class.oid = relation_lock.relation
                    WHERE relation_lock.pid = %s
                      AND relation_lock.locktype = 'relation'
                      AND relation_lock.mode = 'RowShareLock'
                      AND relation_lock.granted
                      AND pg_class.relname = 'core_client'
                )
                AND EXISTS (
                    SELECT 1
                    FROM pg_locks AS transaction_lock
                    WHERE transaction_lock.pid = %s
                      AND transaction_lock.locktype = 'transactionid'
                      AND transaction_lock.mode = 'ExclusiveLock'
                      AND transaction_lock.granted
                )
            """,
            [pid, pid],
        )
        return cur.fetchone()[0]


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
        probe_eins = Client.objects.create(facility=facility, pseudonym="Rendezvous-1", created_by=staff)
        probe_zwei = Client.objects.create(facility=facility, pseudonym="Rendezvous-2", created_by=staff)
        pid_box: dict[str, int] = {}
        start_barriere = threading.Barrier(2)
        barriere_passiert: set[str] = set()
        faden_1_bereit = threading.Event()
        faden_2_haelt_kleiner = threading.Event()
        begegnung = threading.Event()
        sperren_belegt: dict[str, bool] = {}
        ergebnis: dict[str, str] = {}

        def _merke(name, exc):
            sqlstate = getattr(getattr(exc, "__cause__", None), "sqlstate", None)
            ergebnis[name] = f"deadlock:{sqlstate}" if sqlstate == "40P01" else type(exc).__name__

        def _fallzuordnung():
            try:
                with transaction.atomic():
                    pid_box["eins"] = _backend_pid()
                    list(Client.objects.select_for_update().filter(pk=probe_eins.pk).only("pk"))
                    sperren_belegt["eins"] = _haelt_clientzeilensperre(pid_box["eins"])
                    start_barriere.wait(timeout=_JOIN_TIMEOUT)
                    barriere_passiert.add("eins")
                    faden_1_bereit.set()
                    # Faden 2 haelt bereits die kleinere Person; erst danach
                    # darf die Zuordnung ihre Sperren nehmen.
                    if not faden_2_haelt_kleiner.wait(timeout=_JOIN_TIMEOUT):
                        raise AssertionError("Merge-Faden meldete seine erste fachliche Personensperre nicht")
                    assign_event_to_case(case, event, staff)
                ergebnis["eins"] = "zugeordnet"
            except ValidationError as exc:
                # Die fachliche Zurueckweisung („unterschiedliche Personen") —
                # sie faellt NACH den Sperren und ist der Normalfall.
                if "Person des Ereignisses" in str(exc):
                    ergebnis["eins"] = "personen-verschieden"
                else:
                    _merke("eins", exc)
            except Exception as exc:  # noqa: BLE001
                _merke("eins", exc)
            finally:
                connections.close_all()

        def _zusammenfuehren():
            try:
                with transaction.atomic():
                    pid_box["zwei"] = _backend_pid()
                    list(Client.objects.select_for_update().filter(pk=probe_zwei.pk).only("pk"))
                    sperren_belegt["zwei"] = _haelt_clientzeilensperre(pid_box["zwei"])
                    start_barriere.wait(timeout=_JOIN_TIMEOUT)
                    barriere_passiert.add("zwei")
                    # Die erste Sperre des Zusammenfuehrens in PK-Ordnung.
                    list(Client.objects.select_for_update().filter(pk=kleiner.pk).only("pk"))
                    faden_2_haelt_kleiner.set()
                    # Beobachtend abwarten, bis Faden 1 an einer Sperre haengt
                    # — ohne diesen Punkt entschiede die Maschinenlaune, wer
                    # zuerst kommt, und das Rennen waere kein Nachweis.
                    if not faden_1_bereit.wait(timeout=_JOIN_TIMEOUT):
                        raise AssertionError("Zuordnungs-Faden erreichte den Rennabschnitt nicht")
                    if not _wartet_auf_sperre(pid_box.get("eins", -1), _WAIT_DEADLINE):
                        raise AssertionError("Zuordnungs-Faden wartete nicht messbar auf die Personensperre")
                    begegnung.set()
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
        assert barriere_passiert == {"eins", "zwei"}, f"Rendezvous nicht beidseitig passiert: {barriere_passiert}"
        assert sperren_belegt == {"eins": True, "zwei": True}, f"Personensperren am Rendezvous fehlen: {sperren_belegt}"
        assert begegnung.is_set(), f"Ablaufe sind einander nicht an der Personensperre begegnet: {ergebnis}"
        deadlocks = [name for name, wert in ergebnis.items() if wert.startswith("deadlock")]
        assert not deadlocks, f"Sperrzyklus zwischen Fallzuordnung und Zusammenfuehren: {ergebnis}"
        assert ergebnis.get("eins") == "personen-verschieden", ergebnis
        assert ergebnis.get("zwei") == "zusammengefuehrt", ergebnis
