"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.case.handover``.

Quelle: Vollmutation #2174, Batch ``case-1``
(``welle-l-0.99/reports/m-scratch/batch-case-1.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-case-1/triage-099-case-1.json``). Die Batch hat 18
Survivor-Mutanten: ``_build_shift_metadata`` (5), ``_collect_stats`` (1),
``_collect_highlights`` (12).

Drei Themen:

* **Die Schicht-Zeitspanne.** ``_build_shift_metadata`` formatiert ``shift_range`` als
  ``"HH:MM – HH:MM"``. Ein Mutant setzt sie auf ``None`` (``x__build_shift_metadata__mutmut_2``),
  vier vertauschen ``%H``/``%M`` gegen ``%h``/``%m`` bzw. umschließen das Format mit
  ``XX...XX``-Markern (``…__mutmut_4/5/7/8``) — am installierten Python verifiziert:
  ``datetime.strftime('%h:%m')`` liefert Monatsname:Monatszahl (z. B. ``"Sep:09"``) statt der
  Uhrzeit. Das ist keine Meldungs-Textvariante (kein ``raise``), sondern ein Formatstring-Fehler
  im zurückgegebenen Anzeigewert — die Sonderregel für XX-Marker-Varianten von
  ``raise``-Meldungstexten greift hier nicht, weil hier gar nicht geworfen wird.
* **Die Farbe je Ereignistyp in der Statistik.** ``_collect_stats`` verliert mit
  ``x__collect_stats__mutmut_9`` das Feld ``document_type__color`` aus
  ``.values(...)`` — der Schlüssel fehlt dann komplett in jedem ``events_by_type``-Eintrag.
  Das aktuelle Template rendert die Farbe zwar noch nicht, sie ist aber Teil des
  dokumentierten Rückgabewerts von ``_collect_stats`` und damit ein beobachtbarer
  Datenverlust.
* **Die Vorschau-Fremdschlüssel der Highlights.** Acht Mutanten entfernen je einen
  Namen aus einem ``.select_related(...)``-Aufruf auf ``crisis_events``/``ban_events``/
  ``urgent_tasks`` (``…__mutmut_6/7/8``, ``…26/27/28``, ``…45/46``). Verlorene
  Abfragezahl ist laut Äquivalenzregel ausdrücklich KEIN Äquivalenzbeweis —
  gepinnt per Query-Zählung (``CaptureQueriesContext``): mit vollständigem
  ``select_related`` kostet der Folgezugriff auf die entfernten Felder null zusätzliche
  Queries, ohne eines der drei Felder mindestens eine zusätzliche Query je betroffenem
  Objekt (empirisch am unveränderten Code verifiziert, siehe Testkommentare).

Vier Mutanten sind **equivalent** (kein Pin, siehe Triage-Begründung):

* ``x__collect_highlights__mutmut_104/105`` (Dict-Schlüssel ``"occurred_at"`` in der
  lokalen ``event_highlights``-Liste umbenannt): der Schlüssel wird von
  ``enrich_events_with_preview`` (``core/services/events/feed.py``) nachweislich nie
  gelesen (die Funktion liest ausschließlich ``item["type"]`` und ``item["object"]``) und
  ``event_highlights`` selbst wird nach dem Enrich-Aufruf verworfen — die Umbenennung ist
  für jeden Aufrufer unsichtbar.
* ``x__collect_highlights__mutmut_115/116`` (Filter ``if h["type"] in ("crisis", "ban")``
  auf ``("XXcrisisXX"/"CRISIS", "ban")`` verengt, sodass Krisen-Highlights aus
  ``event_highlights`` herausfallen): ``enrich_events_with_preview`` filtert intern
  selbst noch einmal auf ``item["type"] in ("event", "ban")`` — "crisis" besteht diesen
  zweiten Filter so oder so nie. Ob ein Krisen-Highlight also überhaupt an
  ``enrich_events_with_preview`` übergeben wird, ändert nichts: es bekommt in BEIDEN
  Fällen kein ``preview_fields``/``expanded_fields`` gesetzt.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import DocumentType, Event, WorkItem
from core.services.case.handover import _build_shift_metadata, _collect_highlights, _collect_stats

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestBuildShiftMetadataFormat:
    def test_shift_range_exaktes_uhrzeitformat(self):
        """Pins ``x__build_shift_metadata__mutmut_2/4/5/7/8``: mit gesetztem
        ``time_filter`` muss ``shift_range`` exakt ``"HH:MM – HH:MM"`` liefern. mutmut_2
        liefert ``None``; mutmut_5/8 vertauschen ``%H``/``%M`` gegen ``%h``/``%m``
        (Monatsname/Monatszahl statt Uhrzeit — ``datetime(2026, 9, 16, 6, 0).strftime('%h:%m')
        == 'Sep:09'``, am installierten Python verifiziert); mutmut_4/7 umschließen das
        Format mit ``XX...XX``-Markern (``'XX06:00XX – 14:30'`` bzw. Gegenstück)."""
        time_filter = SimpleNamespace(label="Frühdienst")  # nur .label wird gelesen
        start_dt = timezone.make_aware(datetime(2026, 9, 16, 6, 0))
        end_dt = timezone.make_aware(datetime(2026, 9, 16, 14, 30))

        shift_label, shift_range = _build_shift_metadata(time_filter, start_dt, end_dt)

        assert shift_label == "Frühdienst"
        assert shift_range == "06:00 – 14:30"

    def test_shift_range_ohne_time_filter_ganzer_tag(self):
        """Randfall (bestehendes Verhalten, hier nur als Kontrastfolie zum obigen Pin):
        ohne ``time_filter`` bleibt der ``else``-Zweig unverändert bei "Ganzer Tag" /
        "00:00 – 23:59" — nicht Teil des Batches, dient nur der Lesbarkeit der
        Testklasse."""
        shift_label, shift_range = _build_shift_metadata(None, None, None)
        assert (shift_label, shift_range) == ("Ganzer Tag", "00:00 – 23:59")


class TestCollectStatsEventsByType:
    def test_events_by_type_enthaelt_dokumenttyp_farbe(self, facility, client_identified, staff_user, doc_type_contact):
        """Pins ``x__collect_stats__mutmut_9``: ``.values(...)`` verliert
        ``document_type__color`` — die Schicht-Übergabe-Statistik muss die Farbe je
        Dokumenttyp im ``events_by_type``-Eintrag weiterhin liefern (Rückgabe-Vertrag von
        ``_collect_stats``, auch wenn das aktuelle Template sie noch nicht rendert)."""
        doc_type_contact.color = "indigo"
        doc_type_contact.save()
        now = timezone.now()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        time_range = (now - timedelta(hours=1), now + timedelta(hours=1))
        visible_events = Event.objects.visible_to(staff_user).filter(
            facility=facility, is_deleted=False, occurred_at__range=time_range
        )

        stats = _collect_stats(facility, visible_events, time_range)

        assert stats["events_by_type"] == [
            {"document_type__name": "Kontakt", "document_type__color": "indigo", "count": 1}
        ]


class TestCollectHighlightsSelectRelated:
    def test_highlight_objekte_liefern_fremdschluessel_ohne_zusatzquery(self, facility, client_identified, staff_user):
        """Pins acht Mutanten in drei ``.select_related(...)``-Aufrufen von
        ``_collect_highlights`` — verlorene Abfragezahl ist laut Äquivalenzregel
        ausdrücklich kein Äquivalenzbeweis:

        * ``x__collect_highlights__mutmut_6/7/8`` (``crisis_events`` verliert
          ``document_type``/``client``/``created_by``): geprüft per Folgezugriff NACH dem
          Aufruf — ``enrich_events_with_preview`` fasst Krisen-Highlights nicht an (siehe
          Moduldoc), ein Nachladen wäre also ausschließlich auf die fehlende
          ``select_related``-Angabe zurückzuführen.
        * ``x__collect_highlights__mutmut_26/27/28`` (``ban_events`` verliert dieselben
          drei Felder): ``client``/``created_by`` wie oben per Folgezugriff;
          ``document_type`` wird dagegen SCHON WÄHREND ``_collect_highlights`` selbst
          gelesen (``enrich_events_with_preview`` -> ``_build_event_preview_fields`` liest
          ``event.document_type.sensitivity`` für jedes Ban-Highlight) — deshalb pinnt die
          exakte Gesamt-Query-Zahl DES AUFRUFS (empirisch am unveränderten Code ermittelt:
          1× crisis_events + 1× ban_events + 1× urgent_tasks + 1× FieldGroup- + 1×
          DocumentTypeField-Query aus ``iter_fields_in_block_order`` = 5; fehlt
          ``document_type`` im ``select_related`` von ``ban_events``, kommt eine
          zusätzliche freistehende ``core_documenttype``-Query hinzu = 6).
        * ``x__collect_highlights__mutmut_45/46`` (``urgent_tasks`` verliert
          ``client``/``assigned_to``): wie ``crisis_events`` per Folgezugriff — beide
          Felder werden innerhalb von ``_collect_highlights`` selbst nirgends gelesen.
        """
        doc_type_crisis = DocumentType.objects.create(
            facility=facility,
            name="Krise-Pin-Case1",
            sensitivity=DocumentType.Sensitivity.ELEVATED,
            system_type="crisis",
        )
        doc_type_ban = DocumentType.objects.create(
            facility=facility,
            name="Ban-Pin-Case1",
            sensitivity=DocumentType.Sensitivity.ELEVATED,
            system_type="ban",
        )
        now = timezone.now()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_ban,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            assigned_to=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            priority="urgent",
            title="Dringende Aufgabe Pin-Case1",
        )
        time_range = (now - timedelta(hours=1), now + timedelta(hours=1))
        visible_events = Event.objects.visible_to(staff_user).filter(
            facility=facility, is_deleted=False, occurred_at__range=time_range
        )

        with CaptureQueriesContext(connection) as call_ctx:
            highlights = _collect_highlights(facility, visible_events, time_range, staff_user)
        assert len(call_ctx.captured_queries) == 5, [q["sql"] for q in call_ctx.captured_queries]

        crisis = next(h for h in highlights if h["type"] == "crisis")
        ban = next(h for h in highlights if h["type"] == "ban")
        task = next(h for h in highlights if h["type"] == "task")

        with CaptureQueriesContext(connection) as crisis_ctx:
            _ = crisis["object"].document_type.name
            _ = crisis["object"].client.pseudonym
            _ = crisis["object"].created_by.username
        assert len(crisis_ctx.captured_queries) == 0, [q["sql"] for q in crisis_ctx.captured_queries]

        with CaptureQueriesContext(connection) as ban_ctx:
            _ = ban["object"].client.pseudonym
            _ = ban["object"].created_by.username
        assert len(ban_ctx.captured_queries) == 0, [q["sql"] for q in ban_ctx.captured_queries]

        with CaptureQueriesContext(connection) as task_ctx:
            _ = task["object"].client.pseudonym
            _ = task["object"].assigned_to.username
        assert len(task_ctx.captured_queries) == 0, [q["sql"] for q in task_ctx.captured_queries]
