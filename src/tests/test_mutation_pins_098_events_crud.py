"""Pin-Tests der Welle K (0.98) — Prio-2-Survivors in ``core.services.events.crud``.

Quelle: Füllauftrag #2193,
Batch ``merge-crud`` (``triage-096-events-import.json``). Acht Mutanten in vier
Funktionen, alle inhaltlich unveraendert gegenueber dem Batch-Text (die Zeilen
62/209/340 stimmen exakt; ``soft_delete_event`` ist seit der 0.96-Triage um
Refs-#2197-Haertung gewachsen — Zeile 500 ist inzwischen 547, der TEXT der
``lock``-Mutanten (``_2``/``_4``/``_5``) blieb aber deckungsgleich; einzig
``_74`` mutiert tatsaechlich das ``action``-Argument von ``audit_event`` statt
``actor`` — siehe Klassifikation im Bericht):

* **Alias-Durchreichung** (``apply_attachment_changes``, 2 Mutanten) — der
  duenne Event-Alias verliert ``staged_by_key`` auf dem Weg zu
  ``attachments_crud.apply_attachment_changes`` (Refs #1956): doppeltes
  Staging, verwaiste ``.enc``-Dateien.
* **Aktivitaets-Attribution** (``create_event``, 1 Mutant) — der Dashboard-Feed
  zeigt das Ereignis ohne handelnde Person; das AuditLog bleibt korrekt
  (Anzeige-, kein Nachweisproblem).
* **Marker-Scan-Reihenfolge** (``decrypt_event_text_data``, 1 Mutant) —
  ``continue`` -> ``break`` bricht beim ERSTEN File-Marker-Feld ab; die
  Bestandstests legen den Marker immer als letzten Dict-Schluessel an.
* **Zeilensperre des Loeschpfads** (``soft_delete_event``, 3 Mutanten) —
  ``lock=True`` -> ``None``/entfernt/``False`` am Art.-18-Gate. Anders als bei
  ``create_event``/``update_event`` bleibt die Abweisung selbst gruen (die
  Funktion sperrt danach ohnehin per ``_lock_client_row`` erneut) — der
  Unterschied ist ausschliesslich in der ANZAHL der ``FOR UPDATE``-Anweisungen
  auf ``core_client`` sichtbar (zwei statt einer).

Konventionen wie ``test_mutation_pins_096_events_crud.py``: Zusicherungen
vergleichen EXAKT, keine Fakes fuer die Services selbst; KEIN Import aus
``test_rls_functional``.
"""

import re
import types

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Activity
from core.services.attachments import crud as attachments_crud_module
from core.services.events import crud as events_crud_module
from core.services.events.crud import create_event, decrypt_event_text_data, soft_delete_event

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

#: Gleiche Grob-Erkennung wie ``test_mutation_pins_096_events_crud.py``.
_LOCK_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)


def _locking_statements(captured, table):
    hits = []
    for query in captured.captured_queries:
        match = _LOCK_RE.search(query["sql"])
        if match and match.group("table") == table:
            hits.append(query["sql"])
    return hits


def _pk_literal(obj_or_pk):
    pk = getattr(obj_or_pk, "pk", obj_or_pk)
    return str(pk).replace("-", "")


class TestApplyAttachmentChangesAliasPassthrough:
    """Der duenne Event-Alias reicht ALLE sieben Argumente unveraendert durch."""

    def test_alias_reicht_staged_by_key_unveraendert_durch(self, sample_event, staff_user, monkeypatch):
        """Pin: ``x_apply_attachment_changes__mutmut_14`` (Kwarg
        ``staged_by_key`` ganz entfernt) und ``_7`` (``staged_by_key`` ->
        ``None``).

        Original: der Alias reicht ``staged_by_key`` 1:1 an
        ``attachments_crud.apply_attachment_changes`` weiter — genau dieses
        Argument schaltet dort zwischen „bereits gestaged" (Aufrufer traegt
        Transaktion) und „selbst staged" (#1956) um. Abweichung beim Mutanten:
        der Recorder sieht ``None`` statt des uebergebenen Schluessels.
        """
        captured = {}

        def recorder(event, user, request_post, request_files, file_fields, document_type, staged_by_key=None):
            captured["args"] = (event, user, request_post, request_files, file_fields, document_type, staged_by_key)
            return {}

        monkeypatch.setattr(attachments_crud_module, "apply_attachment_changes", recorder)

        sentinel_post = {"post": "wert"}
        sentinel_files = {"files": "wert"}
        sentinel_fields = ["feld-a"]
        sentinel_key = "pin098-staged-schluessel"

        events_crud_module.apply_attachment_changes(
            sample_event,
            staff_user,
            sentinel_post,
            sentinel_files,
            sentinel_fields,
            sample_event.document_type,
            sentinel_key,
        )

        assert captured["args"] == (
            sample_event,
            staff_user,
            sentinel_post,
            sentinel_files,
            sentinel_fields,
            sample_event.document_type,
            sentinel_key,
        )


class TestCreateEventActivityAttribution:
    """Der Dashboard-Feed nennt die Person, die das Ereignis anlegte."""

    def test_creation_activity_names_the_acting_user(self, facility, staff_user, doc_type_contact, client_identified):
        """Pin: ``x_create_event__mutmut_119`` — ``log_activity(actor=user)``
        -> ``actor=None``.

        Das AuditLog (``EVENT_CREATE``) traegt den Akteur unabhaengig davon
        weiterhin korrekt — hier wird ausschliesslich der Activity-Eintrag
        (Dashboard-Feed) gepinnt, den kein Bestandstest fuer ``create_event``
        prueft.
        """
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )

        activity = Activity.objects.get(context__action=Activity.Action.EVENT_CREATED.value, target_id=event.pk)
        assert activity.actor_id == staff_user.pk


class TestDecryptEventTextDataMarkerNotLast:
    """Der File-Marker kann an JEDER Position im Dict stehen, nicht nur zuletzt."""

    def test_marker_am_anfang_ueberspringt_trotzdem_nur_sich_selbst(self):
        """Pin: ``x_decrypt_event_text_data__mutmut_11`` — ``continue`` ->
        ``break``.

        Die Bestandstests in ``TestDecryptEventTextData`` legen den Marker
        immer als LETZTEN Schluessel an, weshalb ``break`` dort dasselbe
        Ergebnis liefert wie ``continue``. Mit dem Marker VORNE bricht der
        Mutant die Schleife vollstaendig ab: alle folgenden Slugs fehlen im
        Ergebnis, ``EventUpdateView.get`` zeigt sie leer und ``post`` leert
        sie beim Speichern.
        """
        event = types.SimpleNamespace(
            data_json={
                "anhang": {"__files__": True, "entries": []},
                "notiz": "x",
                "dauer": 15,
            }
        )

        assert decrypt_event_text_data(event) == {"notiz": "x", "dauer": 15}


class TestSoftDeleteEventGateTakesTheClientRowLock:
    """``lock=True`` ist eine EIGENSTAENDIGE Sperre neben ``_lock_client_row``.

    Beide Sperren zielen auf dieselbe Zeile; die Abweisung einer bereits
    eingeschraenkten Person bleibt beim Mutanten gruen (``_lock_client_row``
    sperrt ohnehin), nur die ANZAHL der ``FOR UPDATE``-Anweisungen auf
    ``core_client`` sinkt von zwei auf eine.
    """

    def test_soft_delete_sperrt_die_personenzeile_zweifach(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """Pin: ``x_soft_delete_event__mutmut_2`` (``lock=None``), ``_4``
        (Argument entfernt, Default ``False``) und ``_5`` (``lock=False``).

        Abweichung beim Mutanten: nur EINE ``core_client``-Sperre im
        Mitschnitt (aus ``_lock_client_row``) statt zwei (Gate + Zeilensperre).
        """
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )

        with CaptureQueriesContext(connection) as captured:
            soft_delete_event(event, staff_user)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 2, f"erwartet zwei Client-Sperren (Gate + _lock_client_row), war: {locks}"
        for lock in locks:
            assert _pk_literal(client_identified) in lock, lock
