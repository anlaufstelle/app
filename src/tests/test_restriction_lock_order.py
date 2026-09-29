"""Kanonische Sperr-Reihenfolge Client → WorkItem (Cross-Review-Runde 2).

Das Art.-18-Gate nimmt in Schreibpfaden eine Sperre auf die `Client`-Zeile.
Eine fruehere Annahme behauptete, die Reihenfolge sei „Client zuerst, überall
gleich" — das war falsch: die Aufgaben-Pfade sperrten erst das WorkItem und
lasen den Client danach, während der Merge-Pfad umgekehrt vorging. Daraus
entstand ein realer Deadlock-Zyklus:

    Bulk:  hält WorkItem  →  wartet auf Client
    Merge: hält Client    →  wartet auf WorkItem

Diese Tests pinnen die jetzt festgelegte Reihenfolge **am ausgeführten SQL**,
nicht an einer Zusage im Kommentar — dieselbe Disziplin wie bei der
advisory-lock-Ordnung aus Lehre 3 der 0.88-Reihe. Ein Pfad, der die Reihenfolge
wieder umdreht, wird rot.

Ergänzend ein Query-Count-Pin für den heißesten Pfad: das Gate kostet
zusätzliche Abfragen, und der Re-Review hat zu Recht bemängelt, dass der
Bericht diese Kosten unterschätzte, ohne sie zu messen.
"""

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import Client, WorkItem
from core.services.client.restriction import restrict_processing

#: ``FOR UPDATE``-Statements verraten die Sperr-Reihenfolge. Der Tabellenname
#: steht im ``FROM``; die Erkennung bleibt bewusst grob, damit sie nicht an
#: Django-Versionsdetails der SQL-Formatierung hängt.
_LOCK_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)

#: UUIDs erscheinen im ausgefuehrten SQL als Hex ohne Bindestriche.
_UUID_RE = re.compile(r"'([0-9a-f]{32})'::uuid")


def _lock_sequence(captured):
    """Die Reihenfolge der gesperrten Tabellen aus einem Query-Mitschnitt."""
    sequence = []
    for query in captured.captured_queries:
        match = _LOCK_RE.search(query["sql"])
        if match:
            sequence.append(match.group("table"))
    return sequence


def _first_index(sequence, table):
    return sequence.index(table) if table in sequence else None


#: Abfragen eines ``update_workitem``-Laufs ohne ``client``-Kwarg. Der Wert
#: wird vom Test selbst ermittelt und hier festgeschrieben; er ist kein
#: Budget, sondern eine Aussage über den Pfad. Zwei Lesungen unter bereits
#: gehaltener Sperre gehören dazu: der Widerrufszustand der gesperrten Person
#: (Papierkorb, deaktiviert, zusammengeführt) und der Löschzustand der
#: gesperrten Aufgabe.
EXPECTED_UPDATE_WORKITEM_QUERIES = 16


@pytest.mark.django_db(transaction=False)
class TestCanonicalLockOrder:
    def test_update_workitem_locks_client_before_workitem(self, facility, client_identified, staff_user):
        from core.services.case import create_workitem, update_workitem

        workitem = create_workitem(facility, staff_user, client=client_identified, title="Aufgabe")
        with CaptureQueriesContext(connection) as captured:
            update_workitem(workitem, staff_user, title="Umbenannt")
        sequence = _lock_sequence(captured)
        client_at = _first_index(sequence, "core_client")
        workitem_at = _first_index(sequence, "core_workitem")
        assert client_at is not None, f"Client-Zeile wurde nicht gesperrt: {sequence}"
        assert workitem_at is not None, f"WorkItem-Zeile wurde nicht gesperrt: {sequence}"
        assert client_at < workitem_at, f"Sperr-Reihenfolge verdreht: {sequence}"

    def test_update_workitem_status_locks_client_before_workitem(self, facility, client_identified, staff_user):
        from core.services.case import create_workitem, update_workitem_status

        workitem = create_workitem(facility, staff_user, client=client_identified, title="Aufgabe")
        with CaptureQueriesContext(connection) as captured:
            update_workitem_status(workitem, WorkItem.Status.IN_PROGRESS, staff_user)
        sequence = _lock_sequence(captured)
        assert _first_index(sequence, "core_client") < _first_index(sequence, "core_workitem"), sequence

    def test_bulk_locks_clients_before_workitems(self, facility, client_identified, staff_user):
        from core.services.case import bulk_update_workitem_priority, create_workitem

        first = create_workitem(facility, staff_user, client=client_identified, title="A")
        second = create_workitem(facility, staff_user, client=client_identified, title="B")
        with CaptureQueriesContext(connection) as captured:
            bulk_update_workitem_priority([first, second], staff_user, WorkItem.Priority.URGENT)
        sequence = _lock_sequence(captured)
        assert _first_index(sequence, "core_client") < _first_index(sequence, "core_workitem"), sequence

    def test_bulk_locks_each_client_once(self, facility, client_identified, staff_user):
        """Dedupliziert: zwei Aufgaben derselben Person ergeben EINE Sperre.

        Die erste Fassung sperrte je Aufgabe erneut — bei einem Stapel von
        fünfzig Aufgaben einer Person also fünfzig Mal dieselbe Zeile.
        """
        from core.services.case import bulk_update_workitem_priority, create_workitem

        items = [
            create_workitem(facility, staff_user, client=client_identified, title=f"Aufgabe {n}") for n in range(3)
        ]
        with CaptureQueriesContext(connection) as captured:
            bulk_update_workitem_priority(items, staff_user, WorkItem.Priority.URGENT)
        assert _lock_sequence(captured).count("core_client") == 1

    def test_assigning_a_client_locks_client_before_workitem(self, facility, client_identified, staff_user):
        """``None -> Person`` (Re-Review 2).

        Die erste Fassung sperrte hier erst das WorkItem und danach den NEUEN
        Client — genau die Umkehrung, die dieser Umbau schliessen sollte.
        """
        from core.services.case import create_workitem, update_workitem

        workitem = create_workitem(facility, staff_user, title="Ohne Person")
        with CaptureQueriesContext(connection) as captured:
            update_workitem(workitem, staff_user, client=client_identified)
        sequence = _lock_sequence(captured)
        client_at = _first_index(sequence, "core_client")
        workitem_at = _first_index(sequence, "core_workitem")
        assert client_at is not None, f"neuer Client wurde nicht gesperrt: {sequence}"
        assert client_at < workitem_at, f"Sperr-Reihenfolge bei None->Person verdreht: {sequence}"
        workitem.refresh_from_db()
        assert workitem.client_id == client_identified.pk

    def test_bulk_locks_multiple_clients_in_pk_order(self, facility, staff_user):
        """Mehrere Personen im Stapel: die Sperren muessen in PK-Reihenfolge
        fallen, sonst nehmen zwei gleichzeitige Stapel sie ueber Kreuz."""
        from core.services.case import bulk_update_workitem_priority, create_workitem

        clients = sorted(
            (Client.objects.create(facility=facility, pseudonym=f"Bulk-{n}", created_by=staff_user) for n in range(3)),
            key=lambda c: str(c.pk),
        )
        items = [
            create_workitem(facility, staff_user, client=client, title=f"Aufgabe {n}")
            for n, client in enumerate(clients)
        ]
        with CaptureQueriesContext(connection) as captured:
            bulk_update_workitem_priority(items, staff_user, WorkItem.Priority.URGENT)

        # Die Sperr-Statements tragen die UUID als Hex ohne Bindestriche.
        expected = [str(client.pk).replace("-", "") for client in clients]
        locked_ids = []
        for query in captured.captured_queries:
            sql = query["sql"]
            if "core_client" not in sql or "FOR UPDATE" not in sql.upper():
                continue
            found = _UUID_RE.findall(sql)
            locked_ids.extend(uuid for uuid in found if uuid in expected)
        assert len(locked_ids) == len(clients), f"nicht jede Person gesperrt: {locked_ids}"
        assert locked_ids == sorted(locked_ids), f"Client-Sperren nicht in PK-Ordnung: {locked_ids}"

    def test_merge_request_locks_both_clients_in_one_ordered_statement(self, facility, staff_user):
        """Der Merge-ANTRAG sperrte Quelle und Ziel einzeln in semantischer
        Reihenfolge — zwei invertierte Antraege konnten Client<->Client
        deadlocken (Re-Review 2)."""
        from core.services.client import request_client_merge

        source = Client.objects.create(facility=facility, pseudonym="Antrag-Quelle", created_by=staff_user)
        target = Client.objects.create(facility=facility, pseudonym="Antrag-Ziel", created_by=staff_user)
        with CaptureQueriesContext(connection) as captured:
            request_client_merge(source, target, staff_user, "Duplikat")
        client_locks = [
            q["sql"]
            for q in captured.captured_queries
            if "core_client" in q["sql"] and "FOR UPDATE" in q["sql"].upper()
        ]
        assert len(client_locks) == 1, f"erwartet EIN gemeinsames Sperr-Statement, war: {len(client_locks)}"
        assert "ORDER BY" in client_locks[0].upper(), client_locks[0]

    def test_merge_keeps_client_first(self, facility, client_identified, staff_user, lead_user):
        """Der Merge-Pfad war schon richtig herum — er darf es bleiben."""
        from core.services.client import merge_clients

        target = Client.objects.create(facility=facility, pseudonym="Merge-Ziel", created_by=staff_user)
        with CaptureQueriesContext(connection) as captured:
            merge_clients(client_identified, target, lead_user)
        sequence = _lock_sequence(captured)
        assert sequence, "Merge hat keine Sperre genommen"
        assert sequence[0] == "core_client", sequence

    def test_merge_locks_clients_in_pk_order(self, facility, staff_user, lead_user):
        """Ohne deterministische Reihenfolge sperren zwei gleichzeitige Merges
        mit vertauschtem Paar dieselben Zeilen über Kreuz."""
        from core.services.client.merge import _locked_clients

        first = Client.objects.create(facility=facility, pseudonym="Merge-A", created_by=staff_user)
        second = Client.objects.create(facility=facility, pseudonym="Merge-B", created_by=staff_user)
        with CaptureQueriesContext(connection) as captured:
            _locked_clients(first, second)
        locking = [q["sql"] for q in captured.captured_queries if "FOR UPDATE" in q["sql"].upper()]
        assert locking, "keine Sperre genommen"
        assert any("ORDER BY" in sql.upper() for sql in locking), locking


@pytest.mark.django_db
class TestGateQueryCost:
    def test_update_workitem_query_count_is_pinned(self, facility, client_identified, staff_user):
        """Der heißeste der neu gegateten Pfade.

        Kein Budget-Test um seiner selbst willen: der urspruengliche Code lud den
        Personenbezug dreimal (ungesperrter Guard-Read, Client-Abfrage des
        Gates, zweiter gesperrter Read im ``client``-Zweig). Diese Schranke
        hält die Bereinigung fest — steigt sie wieder, ist die Redundanz zurück.
        """
        from core.services.case import create_workitem, update_workitem

        workitem = create_workitem(facility, staff_user, client=client_identified, title="Aufgabe")
        with CaptureQueriesContext(connection) as captured:
            update_workitem(workitem, staff_user, title="Umbenannt")
        # EXAKTER Pin (Re-Review 2), nicht nur eine Obergrenze: eine Schranke
        # hätte eine zurückkehrende Redundanz erst gemeldet, wenn sie das
        # Budget sprengt. Ändert sich die Zahl, ist das eine bewusste
        # Entscheidung — nach oben wie nach unten.
        assert len(captured.captured_queries) == EXPECTED_UPDATE_WORKITEM_QUERIES, [
            q["sql"] for q in captured.captured_queries
        ]

    def test_gate_costs_no_query_without_a_client(self, facility, staff_user):
        """Eine personenlose Aufgabe darf das Gate nichts kosten — ``None`` ist
        kein Personenbezug und wird ohne Datenbankzugriff durchgelassen."""
        from core.services.client.restriction import assert_processing_allowed

        with CaptureQueriesContext(connection) as captured:
            assert_processing_allowed(None)
        assert len(captured.captured_queries) == 0


@pytest.mark.django_db
class TestGateStillBlocksAfterReordering:
    """Die Umstellung der Reihenfolge darf das Gate nicht entschärfen."""

    def test_update_workitem_still_blocked(self, facility, client_identified, staff_user):
        from django.core.exceptions import ValidationError

        from core.services.case import create_workitem, update_workitem

        workitem = create_workitem(facility, staff_user, client=client_identified, title="Aufgabe")
        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        with pytest.raises(ValidationError):
            update_workitem(workitem, staff_user, title="Umbenannt")

    def test_update_workitem_status_still_blocked(self, facility, client_identified, staff_user):
        from django.core.exceptions import ValidationError

        from core.services.case import create_workitem, update_workitem_status

        workitem = create_workitem(facility, staff_user, client=client_identified, title="Aufgabe")
        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        with pytest.raises(ValidationError):
            update_workitem_status(workitem, WorkItem.Status.DONE, staff_user)


@pytest.mark.django_db
class TestBulkRevalidation:
    """Die WI→Client-Zuordnung wird nach dem Sperren gegengeprüft.

    Zwischen dem ungesperrten Vorab-Lesen und dem Erhalt der WorkItem-Sperre
    kann eine Zuordnung wechseln — die Sperre kann Wartezeit bedeuten. Eine
    Aufgabe, die danach auf eine Person zeigt, die NICHT in der gesperrten und
    gegateten Menge liegt, ist ungeprüft und wird übersprungen (Re-Review 2).
    """

    def test_reassigned_workitem_is_skipped(self, facility, client_identified, staff_user, monkeypatch):
        from core.services.case import create_workitem
        from core.services.case.workitems import _locked_workitems
        from core.services.client import restriction as restriction_module

        other = Client.objects.create(facility=facility, pseudonym="Umgehängt", created_by=staff_user)
        item = create_workitem(facility, staff_user, client=client_identified, title="A")

        # Das Rennen wird GENAU im offenen Fenster ausgelöst: nach dem
        # ungesperrten Vorab-Lesen der Zuordnung, vor dem Sperren der
        # WorkItem-Zeile. Der Guard wird funktionslokal aus ``restriction``
        # importiert, ein Patch dort greift also zur Aufrufzeit.
        original = restriction_module.assert_processing_allowed
        fired = {"done": False}

        def _reassign_then_check(client, **kwargs):
            if not fired["done"]:
                fired["done"] = True
                WorkItem.objects.filter(pk=item.pk).update(client=other)
            return original(client, **kwargs)

        monkeypatch.setattr(restriction_module, "assert_processing_allowed", _reassign_then_check)

        checked = _locked_workitems([item], staff_user)
        assert fired["done"], "das Rennen wurde nicht ausgelöst"
        assert checked == [], "umgehängte Aufgabe wurde nicht übersprungen"

    def test_unassigned_workitem_still_passes(self, facility, staff_user):
        """Ohne Personenbezug gibt es nichts einzuschränken — die Aufgabe
        bleibt im Stapel."""
        from core.services.case import create_workitem
        from core.services.case.workitems import _locked_workitems

        item = create_workitem(facility, staff_user, title="Ohne Person")
        assert [w.pk for w in _locked_workitems([item], staff_user)] == [item.pk]

    def test_untouched_workitem_still_passes(self, facility, client_identified, staff_user):
        from core.services.case import create_workitem
        from core.services.case.workitems import _locked_workitems

        item = create_workitem(facility, staff_user, client=client_identified, title="A")
        assert [w.pk for w in _locked_workitems([item], staff_user)] == [item.pk]


@pytest.mark.django_db(transaction=True)
class TestAutocommitEntryPoints:
    """Direktaufrufe OHNE äußere Transaktion (Cross-Review-Runde 4).

    ``django_db(transaction=False)`` — der Default — wickelt jeden Test in eine
    Transaktion. Ein Test, der dort „läuft ohne äußere Transaktion" behauptet,
    beweist genau das NICHT: die Testtransaktion ist die äußere. Diese Klasse
    läuft deshalb mit ``transaction=True`` im echten Autocommit-Modus.

    Bewusst langsamer (die Tabellen werden zwischen den Tests geleert statt
    zurückgerollt) — dafür beweiskräftig: ``select_for_update`` scheitert im
    Autocommit ohne eigene Klammer, und ``on_commit`` feuert hier wirklich.
    """

    def test_merge_request_brings_its_own_transaction(self, facility, staff_user):
        from core.services.client import request_client_merge

        source = Client.objects.create(facility=facility, pseudonym="Direkt-Quelle", created_by=staff_user)
        target = Client.objects.create(facility=facility, pseudonym="Direkt-Ziel", created_by=staff_user)
        request = request_client_merge(source, target, staff_user, "Duplikat")
        assert request.pk is not None

    def test_merge_request_is_idempotent_on_a_second_call(self, facility, staff_user):
        """Zweiter Antrag auf dasselbe Ziel: ``created=False``, kein
        ``IntegrityError`` und keine zweite Audit-Zeile."""
        from core.models import AuditLog
        from core.services.client import create_or_get_client_merge_request

        source = Client.objects.create(facility=facility, pseudonym="Idem-Quelle", created_by=staff_user)
        target = Client.objects.create(facility=facility, pseudonym="Idem-Ziel", created_by=staff_user)

        first, created_first = create_or_get_client_merge_request(source, target, staff_user, "Duplikat")
        second, created_second = create_or_get_client_merge_request(source, target, staff_user, "Duplikat")

        assert created_first is True
        assert created_second is False
        assert second.pk == first.pk
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.DELETION_REQUESTED,
                target_id=str(first.pk),
            ).count()
            == 1
        )
