"""Pin-Tests der Welle J (0.97) — Prio-1-Survivors in ``core.services.case.workitems``.

Refs #2149.

Quelle: Survivor-Triage des atomaren Mutations-Messlaufs (Batch ``workitems-a``
und ``workitems-b``). Gepinnt werden die 29 als ``testluecke_prio1``
bestaetigten Testluecken des Aufgaben-Service in fuenf Familien:

* **Audit-Attribution** — ``user=None`` bzw. ``facility=None`` im
  ``audit_event``-/``_log_workitem_update``-Aufruf. Der Eintrag entsteht
  weiterhin, er verliert nur die handelnde Person bzw. die
  Einrichtungs-Zuordnung; die Bestandstests zaehlen Eintraege, statt ihre
  Attribution zu pruefen (Threat-Model TB6).
* **Attributions-Durchreichung im Recurrence-Pfad** — ``user`` -> ``None`` auf
  dem Weg ``update_workitem_status``/``bulk_update_workitem_status`` ->
  ``_maybe_duplicate_recurring`` -> ``duplicate_recurring_workitem``. Die
  Folgeaufgabe entsteht weiterhin, aber ohne ``created_by``; der
  ``log_activity``-Eintrag ist dort der EINZIGE Attributionsdatensatz (es gibt
  keinen zusaetzlichen ``audit_event``-Aufruf).
* **Die Zeilensperre des Art.-18-Gates** — ``lock=True`` ->
  ``lock=False``/``None``/weggelassen in ``create_workitem`` und
  ``duplicate_recurring_workitem``. Die Abweisung einer BEREITS
  eingeschraenkten Person bleibt erhalten (Bestandstests bleiben gruen), nur
  das ``FOR UPDATE`` faellt weg — der Unterschied zwischen „war beim Pruefen
  nicht eingeschraenkt" und „kann bis zum Commit nicht eingeschraenkt werden"
  (Docstring ``assert_processing_allowed``). Beobachtbar nur am
  ausgefuehrten SQL.
* **Facility-Scoping** — der gestrichene ``facility_id``-Filter in beiden
  Lookups von ``update_workitem_status`` (Inline-Kommentar dort: „Facility-
  Filter explizit, damit RLS- und Tenant-Isolation greifen") und
  ``User.objects.filter(facility=None)`` statt ``facility=facility`` in
  ``build_inbox_sections``. Letzteres ist besonders scharf, weil ``core_user``
  laut ``core/models/user.py`` KEINE RLS-Policy traegt — der Anwendungsfilter
  ist dort die einzige Schranke.
* **Der No-op-Guard der drei Bulk-Pfade** — ``continue`` -> ``break``. Trifft
  der Stapel auf ein Item, das schon im Zielzustand ist, verarbeitet die
  Mutante KEIN einziges nachfolgendes Item mehr.

Konventionen wie ``test_mutation_pins_096_events_crud.py``: eine Testklasse je
Funktion; jeder Test nennt seine Mutanten in der Docstring samt dem Wert, der
beim Mutanten abweicht; Zusicherungen vergleichen EXAKT (``==``/``is``), nie
per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); keine Fakes — es
laufen echte Model-Objekte durch die echten Services.

``_encryption_key`` liegt modulweit an: der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``; ohne die Fixture waeren die Pins dort aus dem FALSCHEN
Grund rot und der Kill-Nachweis wertlos.

Die Bulk-Pfade laden ihre Items in ``_locked_workitems`` per ``order_by("pk")``
neu. WorkItem-PKs sind UUIDs, die Reihenfolge der Anlage sagt also nichts ueber
die Reihenfolge der Verarbeitung. Die ``continue``-Pins setzen den No-op
deshalb gezielt auf das Item mit der KLEINSTEN PK — nur dann steht er
garantiert am Schleifenanfang, wo ``break`` den Rest des Stapels verschluckt.
"""

import re

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Activity, AuditLog, Client, WorkItem
from core.services.case.workitems import (
    _log_workitem_update,
    _maybe_duplicate_recurring,
    build_inbox_sections,
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    bulk_update_workitem_status,
    create_workitem,
    duplicate_recurring_workitem,
    update_workitem,
    update_workitem_status,
)
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE, restrict_processing

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


#: ``FOR UPDATE``-Statements verraten die Sperre; der Tabellenname steht im
#: ``FROM``. Gleiche Grob-Erkennung wie ``test_mutation_pins_096_events_crud.py``,
#: damit sie nicht an Django-Versionsdetails der SQL-Formatierung haengt.
_LOCK_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)


def _locking_statements(captured, table):
    """Alle ``FOR UPDATE``-Anweisungen eines Mitschnitts auf *table*."""
    hits = []
    for query in captured.captured_queries:
        match = _LOCK_RE.search(query["sql"])
        if match and match.group("table") == table:
            hits.append(query["sql"])
    return hits


def _selects_on(captured, table):
    """``(ungesperrte, gesperrte)`` SELECTs eines Mitschnitts auf *table*."""
    unlocked, locked = [], []
    for query in captured.captured_queries:
        sql = query["sql"]
        if not sql.lstrip().upper().startswith("SELECT"):
            continue
        if f'FROM "{table}"' not in sql:
            continue
        (locked if "FOR UPDATE" in sql.upper() else unlocked).append(sql)
    return unlocked, locked


def _pk_literal(obj_or_pk):
    """Die PK so, wie Postgres sie im ausgefuehrten SQL zeigt: Hex ohne Bindestriche."""
    pk = getattr(obj_or_pk, "pk", obj_or_pk)
    return str(pk).replace("-", "")


def _make_workitem(facility, user, **overrides):
    """Aufgabe OHNE Personenbezug (kein Art.-18-Gate im Weg), Titel eindeutig."""
    defaults = {
        "facility": facility,
        "created_by": user,
        "item_type": WorkItem.ItemType.TASK,
        "status": WorkItem.Status.OPEN,
        "title": "Pin-Aufgabe",
    }
    defaults.update(overrides)
    return WorkItem.objects.create(**defaults)


def _latest_workitem_audit(workitem, action=AuditLog.Action.WORKITEM_UPDATE):
    return AuditLog.objects.filter(action=action, target_id=str(workitem.pk)).latest("timestamp")


def _by_pk(*workitems):
    """Die Items in genau der Reihenfolge, in der die Bulk-Pfade sie sehen."""
    return sorted(workitems, key=lambda item: str(item.pk))


# ---------------------------------------------------------------------------
# _log_workitem_update — Attribution des Bulk-Audit-Eintrags (2 Mutanten)
# ---------------------------------------------------------------------------


class TestLogWorkitemUpdateAttribution:
    def test_bulk_audit_eintrag_traegt_akteur_und_einrichtung(self, facility, staff_user):
        """Pin: ``x__log_workitem_update__mutmut_2`` (``user=user`` -> ``user=None``)
        und ``x__log_workitem_update__mutmut_3`` (``facility=workitem.facility``
        -> ``facility=None``).

        Der Eintrag entsteht in beiden Faellen — die Bestandstests der
        Bulk-Pfade zaehlen ihn und bleiben gruen. Verloren geht die
        Zuschreibung.

        Abweichung beim Mutanten: ``entry.user is None`` (2) bzw.
        ``entry.facility is None`` (3).
        """
        workitem = _make_workitem(facility, staff_user)

        _log_workitem_update(workitem, staff_user, ["status"])

        entry = _latest_workitem_audit(workitem)
        assert entry.user == staff_user
        assert entry.facility == facility
        assert entry.detail == {"changed_fields": ["status"], "bulk": True}


# ---------------------------------------------------------------------------
# create_workitem — Zeilensperre des Art.-18-Gates + Audit (5 Mutanten)
# ---------------------------------------------------------------------------


class TestCreateWorkitemArt18RowLock:
    def test_create_workitem_sperrt_die_personenzeile(self, facility, staff_user, client_identified):
        """Pin: ``x_create_workitem__mutmut_2`` (``lock=None``),
        ``…__mutmut_4`` (Argument weggelassen, Default ``lock=False``) und
        ``…__mutmut_5`` (``lock=False``).

        ``create_workitem`` nimmt sonst KEINE Client-Sperre; faellt sie weg,
        kann ein paralleles ``restrict_processing`` zwischen Pruefung und
        ``save()`` committen (TOCTOU-Bypass des Art.-18-Gates, Refs #2045).
        Der Bestandstest bleibt gruen, weil eine BEREITS gesetzte
        Einschraenkung auch ungesperrt gelesen wird.

        Abweichung beim Mutanten: der ``core_client``-SELECT des Gates traegt
        kein ``FOR UPDATE`` mehr — die Liste der Sperr-Anweisungen ist leer.
        """
        with CaptureQueriesContext(connection) as captured:
            create_workitem(facility, staff_user, client=client_identified, title="Neue Aufgabe")

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]


class TestCreateWorkitemAuditAttribution:
    def test_create_audit_traegt_akteur_und_einrichtung(self, facility, staff_user):
        """Pin: ``x_create_workitem__mutmut_25`` (``user=user`` -> ``user=None``)
        und ``x_create_workitem__mutmut_26`` (``facility=facility`` ->
        ``facility=None``).

        Abweichung beim Mutanten: der ``WORKITEM_CREATE``-Eintrag hat
        ``user is None`` (25) bzw. ``facility is None`` (26).
        """
        workitem = create_workitem(facility, staff_user, title="Neue Aufgabe")

        entry = _latest_workitem_audit(workitem, AuditLog.Action.WORKITEM_CREATE)
        assert entry.user == staff_user
        assert entry.facility == facility


# ---------------------------------------------------------------------------
# update_workitem — Gate fuer den NEUEN Personenbezug + Audit (2 Mutanten)
# ---------------------------------------------------------------------------


class TestUpdateWorkitemGatesTheNewClient:
    def test_roher_primaerschluessel_als_neuer_personenbezug_wird_gegatet(
        self, facility, staff_user, client_identified
    ):
        """Pin: ``x_update_workitem__mutmut_19`` —
        ``pending_client_ids.add(getattr(fields["client"], "pk", fields["client"]))``
        -> ``…, "pk", None))``.

        Wird ``client`` als roher Primaerschluessel statt als ``Client``-
        Instanz durchgereicht (Offline-Replay, JSON-Clients), greift der
        ``getattr``-Fallback. Im Original landet der rohe Wert in
        ``pending_client_ids`` und damit vor dem Update im Art.-18-Gate; die
        Mutante traegt ``None`` ein, das die Zeile darunter
        (``cid for cid in pending_client_ids if cid is not None``) sofort
        wieder herausfiltert — die Einschraenkungspruefung fuer den NEUEN
        Personenbezug entfaellt vollstaendig.

        Abweichung beim Mutanten: kein ``ValidationError`` aus dem Gate; der
        Aufruf laeuft bis zum ``setattr`` durch und scheitert dort mit einem
        ``ValueError`` von Djangos FK-Deskriptor (roher Wert statt Instanz) —
        die ``pytest.raises(ValidationError)``-Klammer schlaegt fehl.
        """
        workitem = _make_workitem(facility, staff_user)
        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)

        with pytest.raises(ValidationError) as excinfo:
            update_workitem(workitem, staff_user, client=str(client_identified.pk))

        assert excinfo.value.messages == [str(PROCESSING_RESTRICTED_MESSAGE)]
        workitem.refresh_from_db()
        assert workitem.client_id is None

    def test_roher_primaerschluessel_wird_vor_dem_update_gesperrt(self, facility, staff_user, client_identified):
        """Zweiter Pin auf ``x_update_workitem__mutmut_19``, diesmal an der
        Sperre statt am Gate-Ergebnis.

        Der rohe Wert wird von Djangos FK-Deskriptor am Ende ohnehin abgelehnt
        (``ValueError``) — das passiert im Original wie beim Mutanten und ist
        hier nicht der Messpunkt. Gemessen wird, ob die Zeile des NEUEN
        Personenbezugs vorher ueberhaupt gesperrt und gegatet wurde.

        Abweichung beim Mutanten: keine einzige ``core_client``-Sperre im
        Mitschnitt.
        """
        workitem = _make_workitem(facility, staff_user)

        with CaptureQueriesContext(connection) as captured:
            with pytest.raises(ValueError):
                update_workitem(workitem, staff_user, client=str(client_identified.pk))

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]


class TestUpdateWorkitemAuditAttribution:
    def test_update_audit_traegt_akteur(self, facility, staff_user):
        """Pin: ``x_update_workitem__mutmut_106`` — ``user=user`` -> ``user=None``
        im ``WORKITEM_UPDATE``-Eintrag.

        Abweichung beim Mutanten: ``entry.user is None``.
        """
        workitem = _make_workitem(facility, staff_user)

        update_workitem(workitem, staff_user, title="Umbenannt")

        entry = _latest_workitem_audit(workitem)
        assert entry.user == staff_user
        assert entry.detail["changed_fields"] == ["title"]


# ---------------------------------------------------------------------------
# update_workitem_status — Facility-Scoping, Audit, Recurrence (5 Mutanten)
# ---------------------------------------------------------------------------


class TestUpdateWorkitemStatusFacilityScope:
    def test_beide_lookups_sind_einrichtungsgebunden(self, facility, staff_user):
        """Pin: ``x_update_workitem_status__mutmut_10`` (``facility_id`` faellt aus
        dem UNGESPERRTEN Vorab-Lesen) und ``…__mutmut_23`` (``facility_id``
        faellt aus dem GESPERRTEN ``select_for_update().get()``).

        Der Inline-Kommentar zieht die Linie ausdruecklich: „Facility-Filter
        explizit, damit RLS- und Tenant-Isolation greifen". Die
        Bestandstests sehen den Unterschied nicht, weil in ihrem
        Einzel-Facility-Aufbau dieselbe Zeile herauskommt — sichtbar ist er
        nur am ausgefuehrten SQL.

        Abweichung beim Mutanten: dem ungesperrten SELECT (10) bzw. dem
        ``FOR UPDATE``-SELECT (23) fehlt die Facility-PK.
        """
        workitem = _make_workitem(facility, staff_user)

        with CaptureQueriesContext(connection) as captured:
            update_workitem_status(workitem, WorkItem.Status.IN_PROGRESS, staff_user)

        unlocked, locked = _selects_on(captured, "core_workitem")
        # Business-activity tracking adds a second scoped read (Refs #2524).
        assert len(unlocked) == 2, f"Expected exactly two unlocked reads, got: {unlocked}"
        assert len(locked) == 1, f"erwartet GENAU eine WorkItem-Sperre, war: {locked}"
        facility_literal = _pk_literal(facility)
        for query in unlocked:
            assert facility_literal in query, query
        assert facility_literal in locked[0], locked[0]


class TestUpdateWorkitemStatusAuditAttribution:
    def test_status_audit_traegt_akteur_und_einrichtung(self, facility, staff_user):
        """Pin: ``x_update_workitem_status__mutmut_48`` (``user=user`` ->
        ``user=None``) und ``…__mutmut_49`` (``facility=locked.facility`` ->
        ``facility=None``).

        Der Eintrag ist die unveraenderliche Spur JEDES Statuswechsels
        (Refs #1467, Threat-Model TB6); ohne Akteur bzw. Einrichtung ist er
        als Rechenschaftsnachweis wertlos.

        Abweichung beim Mutanten: ``entry.user is None`` (48) bzw.
        ``entry.facility is None`` (49).
        """
        workitem = _make_workitem(facility, staff_user)

        update_workitem_status(workitem, WorkItem.Status.IN_PROGRESS, staff_user)

        entry = _latest_workitem_audit(workitem)
        assert entry.user == staff_user
        assert entry.facility == facility
        assert entry.detail["to"] == WorkItem.Status.IN_PROGRESS


class TestUpdateWorkitemStatusRecurrenceAttribution:
    def test_folgeaufgabe_traegt_den_ausloesenden_akteur(self, facility, staff_user):
        """Pin: ``x_update_workitem_status__mutmut_90`` —
        ``_maybe_duplicate_recurring(locked, user, new_status)`` ->
        ``…, None, new_status``.

        Der ``None``-Wert fliesst bis in ``duplicate_recurring_workitem``
        durch: die Folgeaufgabe verliert ``created_by``, der einzige
        Activity-Eintrag seinen ``actor``.

        Abweichung beim Mutanten: ``folgeaufgabe.created_by is None``.
        """
        workitem = _make_workitem(
            facility,
            staff_user,
            recurrence=WorkItem.Recurrence.WEEKLY,
            due_date=timezone.localdate(),
        )

        update_workitem_status(workitem, WorkItem.Status.DONE, staff_user)

        folgeaufgabe = WorkItem.objects.exclude(pk=workitem.pk).get()
        assert folgeaufgabe.created_by == staff_user


# ---------------------------------------------------------------------------
# _maybe_duplicate_recurring — Durchreichen des Akteurs (1 Mutante)
# ---------------------------------------------------------------------------


class TestMaybeDuplicateRecurring:
    def test_reicht_den_akteur_an_die_neuanlage_durch(self, facility, staff_user):
        """Pin: ``x__maybe_duplicate_recurring__mutmut_6`` —
        ``duplicate_recurring_workitem(workitem, user)`` ->
        ``duplicate_recurring_workitem(workitem, None)``.

        Gepinnt wird der Helper direkt: die Aufruf-Mutanten seiner beiden
        Caller (Single-/Bulk-Pfad) sind eigene Mutanten und haben eigene Pins.

        Abweichung beim Mutanten: ``folgeaufgabe.created_by is None``.
        """
        workitem = _make_workitem(
            facility,
            staff_user,
            status=WorkItem.Status.DONE,
            recurrence=WorkItem.Recurrence.WEEKLY,
            due_date=timezone.localdate(),
        )

        _maybe_duplicate_recurring(workitem, staff_user, WorkItem.Status.DONE)

        folgeaufgabe = WorkItem.objects.exclude(pk=workitem.pk).get()
        assert folgeaufgabe.created_by == staff_user


# ---------------------------------------------------------------------------
# duplicate_recurring_workitem — Sperre, created_by, Activity (6 Mutanten)
# ---------------------------------------------------------------------------


class TestDuplicateRecurringWorkitemArt18RowLock:
    def test_sperrt_die_personenzeile(self, facility, staff_user, client_identified):
        """Pin: ``x_duplicate_recurring_workitem__mutmut_2`` (``lock=None``),
        ``…__mutmut_4`` (Argument weggelassen, Default ``lock=False``) und
        ``…__mutmut_5`` (``lock=False``).

        Die Folgeaufgabe ist eine Neuanlage mit demselben Personenbezug — das
        Gate muss die Zeile bis zum Commit halten, sonst kann ein paralleles
        ``restrict_processing`` dazwischenfunken.

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        workitem = _make_workitem(
            facility,
            staff_user,
            client=client_identified,
            recurrence=WorkItem.Recurrence.WEEKLY,
            due_date=timezone.localdate(),
        )

        with CaptureQueriesContext(connection) as captured:
            duplicate_recurring_workitem(workitem, staff_user)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]


class TestDuplicateRecurringWorkitemAttribution:
    def test_folgeaufgabe_traegt_created_by(self, facility, staff_user):
        """Pin: ``x_duplicate_recurring_workitem__mutmut_26`` (``created_by=user``
        -> ``created_by=None``) und ``…__mutmut_38`` (die ``created_by``-Zeile
        faellt ganz aus dem ``create()``-Aufruf; das Feld ist
        ``null=True`` ohne eigenen Default und bleibt damit ``None``).

        Abweichung beim Mutanten: ``folgeaufgabe.created_by is None``.
        """
        workitem = _make_workitem(
            facility,
            staff_user,
            recurrence=WorkItem.Recurrence.WEEKLY,
            due_date=timezone.localdate(),
        )

        folgeaufgabe = duplicate_recurring_workitem(workitem, staff_user)

        assert folgeaufgabe is not None
        assert folgeaufgabe.created_by == staff_user
        folgeaufgabe.refresh_from_db()
        assert folgeaufgabe.created_by == staff_user

    def test_activity_traegt_den_akteur(self, facility, staff_user):
        """Pin: ``x_duplicate_recurring_workitem__mutmut_50`` —
        ``log_activity(actor=user, …)`` -> ``actor=None``.

        Anders als ``create_workitem``/``update_workitem`` schreibt
        ``duplicate_recurring_workitem`` KEINEN zusaetzlichen
        ``audit_event``-Eintrag: dieser Activity-Datensatz ist die einzige
        „wer hat das ausgeloest"-Spur der automatischen Neuanlage.

        Abweichung beim Mutanten: ``eintrag.actor is None``.
        """
        workitem = _make_workitem(
            facility,
            staff_user,
            recurrence=WorkItem.Recurrence.WEEKLY,
            due_date=timezone.localdate(),
        )

        folgeaufgabe = duplicate_recurring_workitem(workitem, staff_user)

        eintrag = Activity.objects.get(
            target_id=folgeaufgabe.pk,
            context__action=Activity.Action.WORKITEM_RECURRENCE_CREATED,
        )
        assert eintrag.actor == staff_user
        assert eintrag.verb == Activity.Verb.CREATED


# ---------------------------------------------------------------------------
# bulk_update_workitem_status — No-op-Guard, Audit, Recurrence (3 Mutanten)
# ---------------------------------------------------------------------------


class TestBulkUpdateWorkitemStatus:
    def test_ueberspringt_no_op_und_arbeitet_den_stapel_weiter_ab(self, facility, staff_user):
        """Pin: ``x_bulk_update_workitem_status__mutmut_17`` — ``continue`` ->
        ``break`` im No-op-Guard.

        Steht das erste Item der (nach PK geordneten) Schleife bereits im
        Zielstatus, verschluckt die Mutante den GESAMTEN Rest des Stapels:
        kein ``save()``, kein Audit, kein Recurrence-Duplikat — und der
        Rueckgabewert behauptet es nicht einmal falsch, er ist schlicht 0.

        Abweichung beim Mutanten: ``verarbeitet == 0``, ``zweites``/``drittes``
        bleiben auf ``OPEN``.
        """
        erstes, zweites, drittes = _by_pk(
            _make_workitem(facility, staff_user, title="A"),
            _make_workitem(facility, staff_user, title="B"),
            _make_workitem(facility, staff_user, title="C"),
        )
        erstes.status = WorkItem.Status.IN_PROGRESS
        erstes.save(update_fields=["status"])

        verarbeitet = bulk_update_workitem_status([erstes, zweites, drittes], staff_user, WorkItem.Status.IN_PROGRESS)

        assert verarbeitet == 2
        zweites.refresh_from_db()
        drittes.refresh_from_db()
        assert zweites.status == WorkItem.Status.IN_PROGRESS
        assert drittes.status == WorkItem.Status.IN_PROGRESS

    def test_audit_traegt_akteur(self, facility, staff_user):
        """Pin: ``x_bulk_update_workitem_status__mutmut_26`` —
        ``_log_workitem_update(workitem, user, ["status"])`` -> ``…, None, …``.

        Der Pin auf ``_log_workitem_update`` selbst deckt diese Aufrufstelle
        NICHT ab: dort stimmt die Weitergabe, hier wird schon ``None``
        uebergeben.

        Abweichung beim Mutanten: ``entry.user is None``.
        """
        workitem = _make_workitem(facility, staff_user)

        bulk_update_workitem_status([workitem], staff_user, WorkItem.Status.IN_PROGRESS)

        entry = _latest_workitem_audit(workitem)
        assert entry.user == staff_user
        assert entry.detail == {"changed_fields": ["status"], "bulk": True}

    def test_recurrence_folgeaufgabe_traegt_akteur(self, facility, staff_user):
        """Pin: ``x_bulk_update_workitem_status__mutmut_34`` —
        ``_maybe_duplicate_recurring(workitem, user, status)`` ->
        ``…, None, status``.

        Abweichung beim Mutanten: ``folgeaufgabe.created_by is None``.
        """
        workitem = _make_workitem(
            facility,
            staff_user,
            recurrence=WorkItem.Recurrence.WEEKLY,
            due_date=timezone.localdate(),
        )

        bulk_update_workitem_status([workitem], staff_user, WorkItem.Status.DONE)

        folgeaufgabe = WorkItem.objects.exclude(pk=workitem.pk).get()
        assert folgeaufgabe.created_by == staff_user


# ---------------------------------------------------------------------------
# bulk_update_workitem_priority — No-op-Guard und Audit (2 Mutanten)
# ---------------------------------------------------------------------------


class TestBulkUpdateWorkitemPriority:
    def test_ueberspringt_no_op_und_arbeitet_den_stapel_weiter_ab(self, facility, staff_user):
        """Pin: ``x_bulk_update_workitem_priority__mutmut_8`` — ``continue`` ->
        ``break`` im No-op-Guard.

        Abweichung beim Mutanten: ``verarbeitet == 0``, ``zweites``/``drittes``
        behalten ``NORMAL``.
        """
        erstes, zweites, drittes = _by_pk(
            _make_workitem(facility, staff_user, title="A"),
            _make_workitem(facility, staff_user, title="B"),
            _make_workitem(facility, staff_user, title="C"),
        )
        erstes.priority = WorkItem.Priority.URGENT
        erstes.save(update_fields=["priority"])

        verarbeitet = bulk_update_workitem_priority([erstes, zweites, drittes], staff_user, WorkItem.Priority.URGENT)

        assert verarbeitet == 2
        zweites.refresh_from_db()
        drittes.refresh_from_db()
        assert zweites.priority == WorkItem.Priority.URGENT
        assert drittes.priority == WorkItem.Priority.URGENT

    def test_audit_traegt_akteur(self, facility, staff_user):
        """Pin: ``x_bulk_update_workitem_priority__mutmut_16`` —
        ``_log_workitem_update(workitem, user, ["priority"])`` -> ``…, None, …``.

        Abweichung beim Mutanten: ``entry.user is None``.
        """
        workitem = _make_workitem(facility, staff_user)

        bulk_update_workitem_priority([workitem], staff_user, WorkItem.Priority.URGENT)

        entry = _latest_workitem_audit(workitem)
        assert entry.user == staff_user
        assert entry.detail == {"changed_fields": ["priority"], "bulk": True}


# ---------------------------------------------------------------------------
# bulk_assign_workitems — No-op-Guard und Audit (2 Mutanten)
# ---------------------------------------------------------------------------


class TestBulkAssignWorkitems:
    def test_ueberspringt_no_op_und_arbeitet_den_stapel_weiter_ab(self, facility, staff_user, lead_user):
        """Pin: ``x_bulk_assign_workitems__mutmut_5`` — ``continue`` -> ``break``
        im No-op-Guard.

        Abweichung beim Mutanten: ``verarbeitet == 0``, ``zweites``/``drittes``
        bleiben ohne Zuweisung.
        """
        erstes, zweites, drittes = _by_pk(
            _make_workitem(facility, staff_user, title="A"),
            _make_workitem(facility, staff_user, title="B"),
            _make_workitem(facility, staff_user, title="C"),
        )
        erstes.assigned_to = lead_user
        erstes.save(update_fields=["assigned_to"])

        verarbeitet = bulk_assign_workitems([erstes, zweites, drittes], staff_user, lead_user)

        assert verarbeitet == 2
        zweites.refresh_from_db()
        drittes.refresh_from_db()
        assert zweites.assigned_to == lead_user
        assert drittes.assigned_to == lead_user

    def test_audit_traegt_akteur(self, facility, staff_user, lead_user):
        """Pin: ``x_bulk_assign_workitems__mutmut_13`` —
        ``_log_workitem_update(workitem, user, ["assigned_to"])`` -> ``…, None, …``.

        Abweichung beim Mutanten: ``entry.user is None``.
        """
        workitem = _make_workitem(facility, staff_user)

        bulk_assign_workitems([workitem], staff_user, lead_user)

        entry = _latest_workitem_audit(workitem)
        assert entry.user == staff_user
        assert entry.detail == {"changed_fields": ["assigned_to"], "bulk": True}


# ---------------------------------------------------------------------------
# build_inbox_sections — Facility-Scoping der Zuweisen-Liste (1 Mutante)
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsFacilityScope:
    def test_zuweisen_liste_enthaelt_nur_nutzer_dieser_einrichtung(
        self,
        facility,
        staff_user,
        lead_user,
        second_facility_user,
        super_admin_user,
    ):
        """Pin: ``x_build_inbox_sections__mutmut_293`` —
        ``User.objects.filter(facility=facility)`` -> ``…filter(facility=None)``.

        Ein echter Facility-Scoping-Fehler: die Zuweisen-Auswahl zeigte die
        Konten OHNE Einrichtung (``super_admin``) statt der Fachkraefte dieser
        Stelle. ``core_user`` traegt laut ``core/models/user.py`` KEINE
        RLS-Policy — hier gibt es kein DB-seitiges Sicherheitsnetz, der
        Anwendungsfilter ist die einzige Schranke.

        Abweichung beim Mutanten: ``facility_users == [super_admin_user]`` —
        weder ``staff_user`` noch ``lead_user`` sind enthalten.
        """
        kontext = build_inbox_sections(facility, staff_user, QueryDict(""))

        assert list(kontext["facility_users"]) == [lead_user, staff_user]
