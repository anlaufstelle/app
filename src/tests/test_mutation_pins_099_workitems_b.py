"""Pin-Tests der Welle L (0.99) — Vollmutation #2174, Batch ``workitems-b``.

Refs #2174,
#2193.

Modul ``core.services.case.workitems`` (zweiter Teilbaum, Batch
``workitems-b``): ``_locked_workitems``, ``update_workitem_status``,
``update_workitem``, ``apply_workitem_filters``,
``bulk_update_workitem_status``, ``duplicate_recurring_workitem``,
``bulk_update_workitem_priority``, ``_capped``, ``_add_months``,
``_maybe_duplicate_recurring``, ``create_workitem``, ``bulk_assign_workitems``.

Wiederkehrende Familien dieses Batches:

* **Diagnose-Log beim Reassignment-waehrend-Sperre** — ``_locked_workitems``
  loggt, wenn die Revalidierung (Re-Review 2) ein Item nach dem Sperren
  wieder herausfiltert. Elf Mutanten verstuemmeln entweder den ``skipped``-
  Zaehler, den Meldungstext oder die Schluessel des ``extra``-Dicts; ein
  Rennen wird per gepatchtem ``assert_processing_allowed`` ausgeloest (Muster
  ``test_restriction_lock_order.py``), ``caplog`` prueft das Ergebnis exakt.
* **Konkurrenz-Meldungstext** — dieselbe deutsche Ausnahme
  ("Die Personenzuordnung der Aufgabe wurde zwischenzeitlich geändert.")
  entsteht an zwei Stellen (``update_workitem_status``, ``update_workitem``);
  ``pytest.raises(match=...)`` waere eine Teilstring-Suche und uebersaehe den
  ``XX…XX``-Mutmut-Marker — die Pins vergleichen ``excinfo.value.messages``
  exakt per ``==``.
* **Der Kurzschluss-Guard fuer den Recurrence-Pfad** — ``or`` -> ``and`` in
  ``_maybe_duplicate_recurring`` ruft ``duplicate_recurring_workitem`` auch
  fuer NICHT wiederkehrende Aufgaben auf; dessen erste Zeile ist das
  Art.-18-Gate, das dann unnoetig (und bei einer eingeschraenkten Person mit
  Ausnahme) feuert. Dieselbe Mutation INNERHALB ``duplicate_recurring_workitem``
  selbst ist dagegen aequivalent (siehe Begruendung dort).
* **Teil-Save-Spalten** — mehrere ``save(update_fields=[…])``-Aufrufe werden zu
  ``save(update_fields=None)`` (Full-Save). Gemessen per ``post_save``-Spy
  (Muster ``test_mutation_pins_094_mfa.py``): Django reicht ``update_fields``
  als ``frozenset`` bzw. ``None`` exakt durch, kein SQL-Text-Parsing noetig.
* **Aktivitaets-Attribution** — ``actor=user`` -> ``actor=None`` in
  ``log_activity``-Aufrufen; die Bestandstests zaehlen nur Eintraege, diese
  Pins pruefen ``activity.actor``.

Konventionen wie ``test_mutation_pins_097_workitems.py``: eine Testklasse je
Funktion/Mutantengruppe; jeder Test nennt seine Mutanten mit vollem
``x…__mutmut_N``-Namen in der Docstring; Zusicherungen vergleichen EXAKT
(``==``/``is``), nie per Teilstring; keine Fakes fuer Model-Objekte.

``_encryption_key`` liegt modulweit an: der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``; ohne die Fixture waeren die Pins dort aus dem FALSCHEN
Grund rot und der Kill-Nachweis wertlos.
"""

import logging
from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.utils import timezone

from core.models import Activity, Client, WorkItem
from core.services.case.workitems import (
    _add_months,
    _capped,
    _locked_workitems,
    _maybe_duplicate_recurring,
    apply_workitem_filters,
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    bulk_update_workitem_status,
    create_workitem,
    duplicate_recurring_workitem,
    update_workitem,
    update_workitem_status,
)
from core.services.client import restriction
from core.services.client.restriction import restrict_processing

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _make_workitem(facility, user, **overrides):
    """Aufgabe mit sprechenden Standardwerten; ``client=None`` sofern nicht überschrieben."""
    defaults = {
        "facility": facility,
        "created_by": user,
        "item_type": WorkItem.ItemType.TASK,
        "status": WorkItem.Status.OPEN,
        "title": "Pin-Aufgabe (workitems-b)",
        "client": None,
    }
    defaults.update(overrides)
    return WorkItem.objects.create(**defaults)


@contextmanager
def _record_saves(model):
    """Zeichnet jeden Save von *model* als ``(created, update_fields)`` auf.

    Django reicht ``update_fields`` als ``frozenset`` (bzw. ``None`` bei einem
    Full-Save) an ``post_save`` durch — exakt vergleichbar, ohne SQL-Text zu
    parsen (Muster ``test_mutation_pins_094_mfa.py``).
    """
    records: list[tuple[bool, object]] = []

    def _receiver(sender, instance, created, update_fields, **kwargs):  # noqa: ARG001
        records.append((created, update_fields))

    dispatch_uid = f"pins099-workitems-b-{model.__name__}"
    post_save.connect(_receiver, sender=model, dispatch_uid=dispatch_uid)
    try:
        yield records
    finally:
        post_save.disconnect(sender=model, dispatch_uid=dispatch_uid)


# ---------------------------------------------------------------------------
# _locked_workitems — Diagnose-Log beim Reassignment-waehrend-Sperre (11 Mutanten)
# ---------------------------------------------------------------------------


class TestLockedWorkitemsSkipLogging:
    def test_skip_log_meldet_mutant_und_zaehler_exakt(
        self, monkeypatch, caplog, facility, staff_user, client_identified, client_qualified
    ):
        """Pin: ``x__locked_workitems__mutmut_29`` (``skipped = None`` statt
        ``len(locked) - len(checked)`` — das Log feuert dann NIE), ``…_30``
        (``skipped = len(locked) + len(checked)`` — falscher Zaehler UND
        feuert auch OHNE echten Uebersprung), ``…_31`` (Meldungstext ->
        ``None``), ``…_32``/``…_34`` (``extra``-Dict -> ``None`` bzw. ganz
        entfernt), ``…_35``/``…_36`` (Meldungstext klein-/grossgeschrieben)
        sowie ``…_37``..``…_40`` (Schluessel ``skipped``/``requested`` im
        ``extra``-Dict verstuemmelt).

        Szenario: ``wi_gate`` traegt zu Beginn ``client_identified``.
        ``assert_processing_allowed`` wird funktionslokal aus
        ``core.services.client.restriction`` importiert — ein Patch dort
        greift zur Aufrufzeit (Muster ``test_restriction_lock_order.py``).
        Genau beim Sperren DIESER Person wird eine parallele Umzuordnung auf
        ``client_qualified`` simuliert; die Revalidierung in
        ``_locked_workitems`` uebergeht ``wi_gate`` danach zu Recht.
        ``wi_free`` hat von Anfang an keinen Personenbezug und bleibt immer
        in der gecheckten Menge — macht ``skipped`` (1) von ``requested`` (2)
        unterscheidbar.

        Abweichung beim Mutanten: entweder kein Log-Datensatz mehr (29), ein
        falscher ``skipped``-Wert (30), ein falscher Meldungstext
        (31/35/36) oder ein fehlendes/falsch benanntes ``extra``-Feld
        (32/34/37/38/39/40, jeweils ``AttributeError`` beim Zugriff).
        """
        wi_gate = _make_workitem(facility, staff_user, client=client_identified)
        wi_free = _make_workitem(facility, staff_user, client=None)

        original_gate = restriction.assert_processing_allowed
        fired = {"done": False}

        def _fake_gate(client, *, lock=False):
            if not fired["done"]:
                fired["done"] = True
                WorkItem.objects.filter(pk=wi_gate.pk).update(client=client_qualified)
            return original_gate(client, lock=lock)

        monkeypatch.setattr(restriction, "assert_processing_allowed", _fake_gate)

        logger_name = "core.services.case.workitems"
        with caplog.at_level(logging.WARNING, logger=logger_name):
            checked = _locked_workitems([wi_gate, wi_free], staff_user)

        assert fired["done"], "das Rennen wurde nicht ausgeloest"
        assert [item.pk for item in checked] == [wi_free.pk]

        records = [record for record in caplog.records if record.name == logger_name]
        assert len(records) == 1
        record = records[0]
        assert record.getMessage() == "bulk_workitem_reassigned_during_lock"
        assert record.skipped == 1
        assert record.requested == 2

    def test_kein_uebersprung_bleibt_still(self, facility, staff_user, caplog):
        """Gegenprobe zu ``…_30``: ohne echten Uebersprung darf KEIN Log
        entstehen. Mit ``skipped = len(locked) + len(checked)`` waere der
        Zaehler hier ``2 * len(locked)`` — bei nicht-leerem Stapel immer
        wahr — und das Log feuerte faelschlich.
        """
        wi_a = _make_workitem(facility, staff_user, client=None)
        wi_b = _make_workitem(facility, staff_user, client=None)

        logger_name = "core.services.case.workitems"
        with caplog.at_level(logging.WARNING, logger=logger_name):
            checked = _locked_workitems([wi_a, wi_b], staff_user)

        assert {item.pk for item in checked} == {wi_a.pk, wi_b.pk}
        assert [record for record in caplog.records if record.name == logger_name] == []


# ---------------------------------------------------------------------------
# update_workitem_status — Konkurrenz-Meldungstext (5 Mutanten)
# ---------------------------------------------------------------------------


class TestUpdateWorkitemStatusConcurrencyMessage:
    def test_zwischenzeitliche_umzuordnung_meldet_den_text_exakt(
        self, monkeypatch, facility, staff_user, client_identified
    ):
        """Pin: ``x_update_workitem_status__mutmut_25`` (``ValidationError(None)``),
        ``…_26`` (``ValidationError(_(None))``), ``…_27`` (XX-Sentinel-Text),
        ``…_28`` (kleingeschrieben), ``…_29`` (grossgeschrieben) — statt des
        Original-Satzes ``Die Personenzuordnung der Aufgabe wurde
        zwischenzeitlich geändert.``.

        Rennen wie oben: ``assert_processing_allowed`` wird beim Sperren des
        (hier ``None``) Personenbezugs gepatcht und traegt ``client_identified``
        erst WAEHREND des Aufrufs ein — der gesperrte Stand weicht danach vom
        ungesperrt gelesenen ab.
        """
        workitem = _make_workitem(facility, staff_user, client=None)
        original_gate = restriction.assert_processing_allowed
        fired = {"done": False}

        def _fake_gate(client, *, lock=False):
            if not fired["done"]:
                fired["done"] = True
                WorkItem.objects.filter(pk=workitem.pk).update(client=client_identified)
            return original_gate(client, lock=lock)

        monkeypatch.setattr(restriction, "assert_processing_allowed", _fake_gate)

        with pytest.raises(ValidationError) as excinfo:
            update_workitem_status(workitem, WorkItem.Status.IN_PROGRESS, staff_user)

        assert excinfo.value.messages == ["Die Personenzuordnung der Aufgabe wurde zwischenzeitlich geändert."]


class TestUpdateWorkitemStatusActivityAttribution:
    def test_abschluss_aktivitaet_traegt_den_akteur(self, facility, staff_user):
        """Pin: ``x_update_workitem_status__mutmut_67`` — ``actor=user`` ->
        ``actor=None`` im COMPLETED-``log_activity``-Aufruf.
        """
        workitem = _make_workitem(facility, staff_user, status=WorkItem.Status.OPEN)

        update_workitem_status(workitem, WorkItem.Status.DONE, staff_user)

        activity = Activity.objects.filter(
            target_id=workitem.pk, verb=Activity.Verb.COMPLETED, facility=facility
        ).latest("created_at")
        assert activity.actor == staff_user

    def test_wiedereroeffnung_nur_von_erledigt_nach_offen_und_mit_akteur(self, facility, staff_user):
        """Pin: ``x_update_workitem_status__mutmut_76`` (``and`` -> ``or``
        in der REOPENED-Bedingung), ``…_77`` (``==`` -> ``!=`` auf
        ``new_status``), ``…_78`` (``==`` -> ``!=`` auf ``old_status``) und
        ``…_80`` (``actor=user`` -> ``actor=None``).

        Der REOPENED-Log gilt NUR fuer den Uebergang Erledigt -> Offen. Ein
        Uebergang In-Bearbeitung -> Offen ist KEINE Wiedereroeffnung und darf
        keinen REOPENED-Eintrag erzeugen — genau das unterscheidet ``…_76``
        (``or`` feuert auch hier, weil ``new_status == OPEN`` allein reicht).
        """
        done_item = _make_workitem(facility, staff_user, status=WorkItem.Status.DONE, title="Von Erledigt")
        update_workitem_status(done_item, WorkItem.Status.OPEN, staff_user)

        activity = Activity.objects.filter(
            target_id=done_item.pk, verb=Activity.Verb.REOPENED, facility=facility
        ).latest("created_at")
        assert activity.actor == staff_user

        in_progress_item = _make_workitem(
            facility, staff_user, status=WorkItem.Status.IN_PROGRESS, title="Von In-Bearbeitung"
        )
        update_workitem_status(in_progress_item, WorkItem.Status.OPEN, staff_user)

        assert not Activity.objects.filter(target_id=in_progress_item.pk, verb=Activity.Verb.REOPENED).exists()


# ---------------------------------------------------------------------------
# update_workitem — Konkurrenz-Meldungstext + Rohes-Pk-Gate + Audit (9 Mutanten)
# ---------------------------------------------------------------------------


class TestUpdateWorkitemConcurrencyMessage:
    def test_zwischenzeitliche_umzuordnung_meldet_den_text_exakt(
        self, monkeypatch, facility, staff_user, client_identified, client_qualified
    ):
        """Pin: ``x_update_workitem__mutmut_39`` (``ValidationError(None)``),
        ``…_40`` (``ValidationError(_(None))``), ``…_41`` (XX-Sentinel-Text),
        ``…_42`` (kleingeschrieben), ``…_43`` (grossgeschrieben) — statt des
        Original-Satzes ``Die Personenzuordnung der Aufgabe wurde
        zwischenzeitlich geändert.``.

        ``current_client_id`` ist hier ``client_identified.pk`` (nicht
        ``None``) — nur dann durchlaeuft die Schleife
        ``for client_id in sorted(cid for cid in pending_client_ids …)``
        ueberhaupt einen Eintrag und ruft ``assert_processing_allowed`` auf,
        an dem das Rennen haengt.
        """
        workitem = _make_workitem(facility, staff_user, client=client_identified)
        original_gate = restriction.assert_processing_allowed
        fired = {"done": False}

        def _fake_gate(client, *, lock=False):
            if not fired["done"]:
                fired["done"] = True
                WorkItem.objects.filter(pk=workitem.pk).update(client=client_qualified)
            return original_gate(client, lock=lock)

        monkeypatch.setattr(restriction, "assert_processing_allowed", _fake_gate)

        with pytest.raises(ValidationError) as excinfo:
            update_workitem(workitem, staff_user, title="Neuer Titel")

        assert excinfo.value.messages == ["Die Personenzuordnung der Aufgabe wurde zwischenzeitlich geändert."]


class TestUpdateWorkitemRawPkGate:
    def test_rohe_pk_desselben_personenbezugs_bleibt_ein_no_op(self, facility, staff_user, client_identified):
        """Pin: ``x_update_workitem__mutmut_82`` — ``getattr(new_client, "pk",
        new_client)`` -> ``getattr(new_client, "pk", None)``.

        Reicht ein Aufrufer (Offline-Replay, JSON-Client) den Personenbezug
        als ROHEN Primaerschluessel statt als ``Client``-Instanz durch, hat
        dieser Wert kein ``.pk``-Attribut — der ``getattr``-Fallback greift.
        Original: der Fallback ist der Wert selbst, ``new_pk`` stimmt mit dem
        bereits gesetzten ``db_client_id`` ueberein, No-op. Mutant: der
        Fallback ist ``None``, ``new_pk != db_client_id`` wird faelschlich
        wahr und die Funktion weist den UNVERAENDERTEN Personenbezug als
        „geaendert" zurueck.

        Abweichung beim Mutanten: ``ValueError`` statt eines ruhigen No-op.
        """
        workitem = _make_workitem(facility, staff_user, client=client_identified)

        result = update_workitem(workitem, staff_user, client=client_identified.pk)

        assert result.client_id == client_identified.pk


class TestUpdateWorkitemClientReassignmentMessage:
    def test_wechsel_des_personenbezugs_meldet_den_text_exakt(
        self, facility, staff_user, client_identified, client_qualified
    ):
        """Pin: ``x_update_workitem__mutmut_91`` — XX-Sentinel-Text statt des
        Original-Satzes ``Personenzuordnung einer Aufgabe kann nicht
        geändert oder entfernt werden.``.
        """
        workitem = _make_workitem(facility, staff_user, client=client_identified)

        with pytest.raises(ValueError) as excinfo:
            update_workitem(workitem, staff_user, client=client_qualified)

        assert str(excinfo.value) == "Personenzuordnung einer Aufgabe kann nicht geändert oder entfernt werden."


class TestUpdateWorkitemNoopClientSync:
    def test_identischer_personenbezug_bleibt_auf_dem_db_stand(self, facility, staff_user, client_identified):
        """Pin: ``x_update_workitem__mutmut_97`` — ``workitem.client_id =
        db_client_id`` -> ``= None`` im No-op-Zweig (identischer Wert, kein
        Feld-Update).

        ``client`` wird aus ``fields`` entfernt, bevor
        ``apply_allowlisted_update`` laeuft — der DB-Stand bleibt so oder so
        unveraendert. Beobachtbar ist nur das IN-MEMORY-Attribut der
        zurueckgegebenen Instanz.
        """
        workitem = _make_workitem(facility, staff_user, client=client_identified)

        result = update_workitem(workitem, staff_user, client=client_identified)

        assert result.client_id == client_identified.pk


class TestUpdateWorkitemActivityAttribution:
    def test_aktualisierungs_aktivitaet_traegt_den_akteur(self, facility, staff_user):
        """Pin: ``x_update_workitem__mutmut_118`` — ``actor=user`` ->
        ``actor=None`` im UPDATED-``log_activity``-Aufruf.
        """
        workitem = _make_workitem(facility, staff_user, title="Alter Titel")

        update_workitem(workitem, staff_user, title="Neuer Titel")

        activity = Activity.objects.filter(target_id=workitem.pk, verb=Activity.Verb.UPDATED, facility=facility).latest(
            "created_at"
        )
        assert activity.actor == staff_user


# ---------------------------------------------------------------------------
# apply_workitem_filters — Default-Werte + and/or + overdue-Scope (4 Testmutanten)
# ---------------------------------------------------------------------------


class TestApplyWorkitemFiltersDefaults:
    def test_weggelassener_assigned_to_filtert_nicht(self, facility, staff_user):
        """Pin: ``x_apply_workitem_filters__mutmut_3`` — Default
        ``assigned_to=""`` -> ``assigned_to="XXXX"``.

        Wird das Keyword ausgelassen, bleibt der Default im Original leer und
        die Zuweisungs-Filterzeile aus. Der Mutant traegt stattdessen den
        Platzhalter ``"XXXX"`` in ``qs.filter(assigned_to_id=…)`` ein — ein
        ungueltiger Wert fuer die UUID-Spalte.

        Abweichung beim Mutanten: die Queryset-Auswertung wirft, statt die
        Aufgabe unveraendert zurueckzugeben.
        """
        workitem = _make_workitem(facility, staff_user)

        result = list(apply_workitem_filters(WorkItem.objects.filter(pk=workitem.pk), user=staff_user))

        assert result == [workitem]


class TestApplyWorkitemFiltersInvalidValueGuard:
    def test_ungueltiger_item_type_und_priority_wird_ignoriert(self, facility, staff_user):
        """Pin: ``x_apply_workitem_filters__mutmut_5`` (``and`` -> ``or`` bei
        ``item_type``) und ``…_mutmut_10`` (dieselbe Mutation bei
        ``priority``).

        Beide Filter pruefen ZWEI Bedingungen: der Wert ist gesetzt UND er ist
        eine bekannte Choice. Mit ``or`` reicht die erste Bedingung allein —
        ein unbekannter Wert wird dann als Filterwert durchgereicht statt
        ignoriert, und die Abfrage liefert faelschlich nichts.
        """
        workitem = _make_workitem(facility, staff_user)

        by_item_type = list(
            apply_workitem_filters(
                WorkItem.objects.filter(pk=workitem.pk), item_type="kein_gueltiger_typ", user=staff_user
            )
        )
        by_priority = list(
            apply_workitem_filters(
                WorkItem.objects.filter(pk=workitem.pk), priority="keine_gueltige_prioritaet", user=staff_user
            )
        )

        assert by_item_type == [workitem]
        assert by_priority == [workitem]


class TestApplyWorkitemFiltersOverdueStatusScope:
    def test_ueberfaellig_zeigt_nur_offene_und_laufende_aufgaben(self, facility, staff_user):
        """Pin: ``x_apply_workitem_filters__mutmut_37`` — der
        ``status__in=[OPEN, IN_PROGRESS]``-Zusatz im ``overdue``-Zweig faellt
        weg.

        Eine laengst erledigte Aufgabe mit Faelligkeitsdatum in der
        Vergangenheit ist fachlich NICHT ueberfaellig. Ohne den Status-Zusatz
        zeigt der Filter sie trotzdem.
        """
        past = timezone.localdate() - timedelta(days=3)
        open_item = _make_workitem(facility, staff_user, due_date=past, status=WorkItem.Status.OPEN)
        done_item = _make_workitem(
            facility, staff_user, due_date=past, status=WorkItem.Status.DONE, title="Erledigt-Ueberfaellig"
        )

        result = apply_workitem_filters(
            WorkItem.objects.filter(pk__in=[open_item.pk, done_item.pk]), due="overdue", user=staff_user
        )

        assert list(result) == [open_item]


# ---------------------------------------------------------------------------
# bulk_update_workitem_status — Meldungstext + Teil-Save (2 Testmutanten)
# ---------------------------------------------------------------------------


class TestBulkUpdateWorkitemStatusInvalidStatusMessage:
    def test_ungueltiger_status_meldet_den_wert_exakt(self, staff_user):
        """Pin: ``x_bulk_update_workitem_status__mutmut_3`` —
        ``ValueError(f"Invalid status: {status}")`` -> ``ValueError(None)``.
        """
        with pytest.raises(ValueError) as excinfo:
            bulk_update_workitem_status([], staff_user, "kein_status")

        assert str(excinfo.value) == "Invalid status: kein_status"


class TestBulkUpdateWorkitemStatusUpdateFields:
    def test_bulk_status_save_beschraenkt_sich_auf_status_spalten(self, facility, staff_user):
        """Pin: ``x_bulk_update_workitem_status__mutmut_18`` —
        ``save(update_fields=["status", "completed_at", "updated_at"])`` ->
        ``save(update_fields=None)``.
        """
        workitem = _make_workitem(facility, staff_user, status=WorkItem.Status.OPEN)

        with _record_saves(WorkItem) as saves:
            bulk_update_workitem_status([workitem], staff_user, WorkItem.Status.DONE)

        updates = [update_fields for created, update_fields in saves if not created]
        assert updates == [frozenset({"status", "completed_at", "updated_at"})]


# ---------------------------------------------------------------------------
# bulk_update_workitem_priority — Meldungstext + Teil-Save (2 Mutanten)
# ---------------------------------------------------------------------------


class TestBulkUpdateWorkitemPriorityInvalidPriorityMessage:
    def test_ungueltige_prioritaet_meldet_den_wert_exakt(self, staff_user):
        """Pin: ``x_bulk_update_workitem_priority__mutmut_3`` —
        ``ValueError(f"Invalid priority: {priority}")`` -> ``ValueError(None)``.
        """
        with pytest.raises(ValueError) as excinfo:
            bulk_update_workitem_priority([], staff_user, "keine_prioritaet")

        assert str(excinfo.value) == "Invalid priority: keine_prioritaet"


class TestBulkUpdateWorkitemPriorityUpdateFields:
    def test_bulk_priority_save_beschraenkt_sich_auf_priority_spalten(self, facility, staff_user):
        """Pin: ``x_bulk_update_workitem_priority__mutmut_10`` —
        ``save(update_fields=["priority", "updated_at"])`` ->
        ``save(update_fields=None)``.
        """
        workitem = _make_workitem(facility, staff_user, priority=WorkItem.Priority.NORMAL)

        with _record_saves(WorkItem) as saves:
            bulk_update_workitem_priority([workitem], staff_user, WorkItem.Priority.URGENT)

        updates = [update_fields for created, update_fields in saves if not created]
        assert updates == [frozenset({"priority", "updated_at"})]


# ---------------------------------------------------------------------------
# bulk_assign_workitems — Teil-Save (1 Mutant)
# ---------------------------------------------------------------------------


class TestBulkAssignWorkitemsUpdateFields:
    def test_bulk_assign_save_beschraenkt_sich_auf_assigned_to_spalten(self, facility, staff_user, lead_user):
        """Pin: ``x_bulk_assign_workitems__mutmut_7`` —
        ``save(update_fields=["assigned_to", "updated_at"])`` ->
        ``save(update_fields=None)``.
        """
        workitem = _make_workitem(facility, staff_user, assigned_to=None)

        with _record_saves(WorkItem) as saves:
            bulk_assign_workitems([workitem], staff_user, lead_user)

        updates = [update_fields for created, update_fields in saves if not created]
        assert updates == [frozenset({"assigned_to", "updated_at"})]


# ---------------------------------------------------------------------------
# duplicate_recurring_workitem — item_type-Kopie + Teil-Save (2 Testmutanten)
# ---------------------------------------------------------------------------


class TestDuplicateRecurringWorkitemCopiesItemType:
    def test_dupliziertes_item_behaelt_den_item_type(self, facility, staff_user):
        """Pin: ``x_duplicate_recurring_workitem__mutmut_41`` —
        ``item_type=workitem.item_type`` faellt aus dem ``WorkItem.objects.create(…)``-
        Aufruf. Der Feld-Default (``ItemType.TASK``) wuerde einen
        ``ItemType.HINT``-Hinweis stillschweigend zur Aufgabe machen.
        """
        source = _make_workitem(
            facility,
            staff_user,
            item_type=WorkItem.ItemType.HINT,
            recurrence=WorkItem.Recurrence.WEEKLY,
            due_date=timezone.localdate(),
        )

        new_workitem = duplicate_recurring_workitem(source, staff_user)

        assert new_workitem is not None
        assert new_workitem.item_type == WorkItem.ItemType.HINT


class TestDuplicateRecurringWorkitemMarkerSave:
    def test_markierungs_save_beschraenkt_sich_auf_recurrence_duplicated_at(self, facility, staff_user):
        """Pin: ``x_duplicate_recurring_workitem__mutmut_60`` —
        ``workitem.save(update_fields=["recurrence_duplicated_at"])`` ->
        ``save(update_fields=None)`` auf dem QUELL-Item (nicht der neuen
        Kopie — deren ``create()`` ist immer ``created=True``).
        """
        source = _make_workitem(
            facility, staff_user, recurrence=WorkItem.Recurrence.WEEKLY, due_date=timezone.localdate()
        )

        with _record_saves(WorkItem) as saves:
            duplicate_recurring_workitem(source, staff_user)

        updates = [update_fields for created, update_fields in saves if not created]
        assert updates == [frozenset({"recurrence_duplicated_at"})]


# ---------------------------------------------------------------------------
# _maybe_duplicate_recurring — Kurzschluss-Guard fuer den Recurrence-Pfad (1 Mutant)
# ---------------------------------------------------------------------------


class TestMaybeDuplicateRecurringSkipsGateForNonRecurring:
    def test_nicht_wiederkehrende_aufgabe_ruft_das_gate_nicht_auf(self, facility, staff_user, client_identified):
        """Pin: ``x__maybe_duplicate_recurring__mutmut_2`` — ``or`` -> ``and``
        in der Kurzschluss-Pruefung.

        Fuer eine NICHT wiederkehrende Aufgabe (Standard ``Recurrence.NONE``)
        darf ``duplicate_recurring_workitem`` gar nicht erst gerufen werden —
        dessen erste Zeile ist das Art.-18-Gate. Mit ``and`` greift der
        Kurzschluss fuer ``Recurrence.NONE`` NICHT (der erste Operand ist fuer
        einen nichtleeren String falsch, ``and`` braucht aber BEIDE wahr) und
        die Funktion ruft ``duplicate_recurring_workitem`` unnoetig auf — bei
        einer eingeschraenkten Person wirft DAS Gate dann eine Ausnahme,
        obwohl gar nichts dupliziert werden sollte.

        Abweichung beim Mutanten: ``ValidationError`` aus dem Art.-18-Gate,
        obwohl die Aufgabe gar keine Wiederholung traegt.
        """
        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        workitem = _make_workitem(facility, staff_user, client=client_identified, recurrence=WorkItem.Recurrence.NONE)

        _maybe_duplicate_recurring(workitem, staff_user, WorkItem.Status.DONE)

        assert WorkItem.objects.filter(facility=facility).count() == 1


# ---------------------------------------------------------------------------
# create_workitem — Audit-Attribution (1 Mutant)
# ---------------------------------------------------------------------------


class TestCreateWorkitemActivityAttribution:
    def test_erstellungs_aktivitaet_traegt_den_akteur(self, facility, staff_user):
        """Pin: ``x_create_workitem__mutmut_15`` — ``actor=user`` ->
        ``actor=None`` im CREATED-``log_activity``-Aufruf.
        """
        workitem = create_workitem(facility, staff_user, title="Neue Aufgabe")

        activity = Activity.objects.filter(target_id=workitem.pk, verb=Activity.Verb.CREATED, facility=facility).latest(
            "created_at"
        )
        assert activity.actor == staff_user


# ---------------------------------------------------------------------------
# _capped — Abfrage-Obergrenze (1 Mutant)
# ---------------------------------------------------------------------------


class _SpyQuerySet:
    """Minimaler Spy: zeichnet den an ``__getitem__`` uebergebenen Slice auf."""

    def __init__(self, items):
        self._items = items
        self.captured_slice = None

    def __getitem__(self, item):
        self.captured_slice = item
        return self._items[item]


class TestCappedLimitBoundary:
    def test_capped_liest_hoechstens_cap_plus_eins(self):
        """Pin: ``x__capped__mutmut_4`` — ``qs[: cap + 1]`` -> ``qs[: cap + 2]``.

        Der Rueckgabewert (``items[:cap]``/``has_more``) ist bei beiden
        Varianten identisch — der Unterschied ist ausschliesslich die an die
        Queryset uebergebene Slice-Obergrenze (eine Zeile mehr geholt als
        dokumentiert). Per Spy-Objekt statt echter Queryset direkt am
        ``__getitem__``-Aufruf gemessen (Muster: Sortier-/Grenzwert-Pins per
        Spy).
        """
        items = list(range(10))
        spy = _SpyQuerySet(items)

        capped_items, has_more = _capped(spy, cap=3)

        assert spy.captured_slice == slice(None, 4)
        assert has_more is True
        assert capped_items == items[:3]


# ---------------------------------------------------------------------------
# _add_months — Jahresuebertrag der Monatsarithmetik (1 Mutant)
# ---------------------------------------------------------------------------


class TestAddMonthsYearBoundary:
    def test_jahreswechsel_wird_korrekt_hochgezaehlt(self):
        """Pin: ``x__add_months__mutmut_8`` — ``month_index // 12`` ->
        ``month_index // 13``.

        Bei ``month_index >= 12`` (Uebertrag ueber ein Kalenderjahr) traegt
        die Division ueber den Jahresindex; ``// 13`` statt ``// 12`` laesst
        das Jahr an genau dieser Grenze unveraendert — die Faelligkeit
        wiederkehrender MONATLICH/VIERTELJAEHRLICH/JAEHRLICH-Aufgaben landet
        dann ein Jahr in der Vergangenheit.
        """
        assert _add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
        assert _add_months(date(2026, 1, 15), 12) == date(2027, 1, 15)
