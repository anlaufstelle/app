"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Batch ``zeitstrom_watch-c``
(Vollmutation-Survivor-Triage) in ``core.services.zeitstrom_watch``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-zeitstrom_watch-c.json``
(Triage ``triage-099-zeitstrom_watch-c.json``). Deckt zwei Funktionen ab:
``_feed_projection`` (die Zusammenstellung des Zeitstrom-Feeds aus
Ereignissen/Aktivitaeten/Aufgaben/Hausverboten je Zeitfenster und Typ-Filter)
und ``_visible_field_configs`` (die je Anfrage EINE gebuendelte Metadaten-
Abfrage, die dynamische Felder nach Sichtbarkeits-Rang filtert und in eine
Block-Reihenfolge bringt). Das Modul ist auf fuenf Batches a–e verteilt
(andere Funktionen, andere Lanes) — diese Datei deckt AUSSCHLIESSLICH die
oben genannten zwei Funktionen ab (``pin_suffix`` ``c``).

**Mehrere Mutanten je Test** (Standardblock, "mehrere Mutanten je Test
erlaubt"): wo mehrere Survivors dieselbe beobachtbare Wirkung haben (z. B.
"Zeitfenster-Untergrenze entfaellt" und "Zeitfenster-Obergrenze entfaellt"
zusammen mit "kompletter Zeitfilter faellt auf Ganztag zurueck"), pruefen
die Tests die exakte Ergebnismenge einmal mit mehreren, bewusst grenz-
nahen Zeilen statt je Mutante einen eigenen Test zu schreiben.

**Die fuenf ``order_by``-Spalten-Mutanten** von ``_visible_field_configs``
(``x__visible_field_configs__mutmut_{13,14,15,16,17}``) werden NICHT ueber
die Ergebnisreihenfolge gepinnt, sondern ueber die tatsaechlich aufgerufene
Sortierkette selbst (``_capture_order_by``, Spy auf ``QuerySet.order_by`` —
Muster ``test_mutation_pins_099_facility_export_d.py::_capture_order_by``,
dort wiederum aus ``test_mutation_pins_098_client_export.py`` uebernommen).

**Aequivalenz-Urteile (14 Mutanten, mit Begruendung, kein Pin):**

* ``x__feed_projection__mutmut_{66,68,72}`` — ``visible_event_ids = Event.
  objects.visible_to(user).values_list("pk", flat=True)`` verlor ``flat``
  (68 ganz entfernt, 66 ``flat=None``, 72 ``flat=False``). ``visible_event_ids``
  wird NIE in Python materialisiert — es fliesst unausgewertet in
  ``activities_qs.exclude(Q(...) & ~Q(target_id__in=visible_event_ids))``.
  Django loest eine Queryset-RHS von ``__in=`` ueber ``QuerySet.
  resolve_expression()`` zu ihrer ``Query`` auf und uebernimmt deren
  SELECT-Klausel unveraendert als Subquery (``django.db.models.lookups.In.
  get_prep_lookup``: der ``"pk"``-Fallback greift nur, wenn die Subquery
  KEINE eigenen ``has_select_fields`` traegt — hier hat sie welche, naemlich
  ``"pk"``, unabhaengig von ``flat``). ``flat`` steuert ausschliesslich die
  PYTHON-seitige Rueckgabeform (nackter Wert vs. 1-Tupel) einer direkt
  iterierten Queryset — genau die Situation, die hier NICHT eintritt. Beide
  Varianten erzeugen identisches SQL (``... target_id IN (SELECT U0."id"
  FROM core_event U0 WHERE ...)``). Empirisch bestaetigt: der eigens fuer
  ``x__feed_projection__mutmut_69`` (``visible_to(None)`` statt
  ``visible_to(user)`` — das AENDERT die Subquery, ``.none()`` filtert per
  ``WHERE 1=0``) geschriebene Pin bleibt unter 66/68/72 GRUEN.
* ``x__feed_projection__mutmut_67`` — dieselbe Zeile verlor stattdessen das
  Positionsargument ``"pk"`` selbst. Django fuellt ein fehlendes Feld bei
  ``values_list()`` mit dem ERSTEN konkreten Modellfeld auf
  (``django.db.models.query.QuerySet.values_list``, Zeile ``fields =
  [self.model._meta.concrete_fields[0].attname]``); bei ``Event`` ist das
  ``id`` — dasselbe Feld, das ``"pk"`` bezeichnet. Identisches SQL wie oben.
* ``x__visible_field_configs__mutmut_{34,35,36,37}`` — je ein Schluessel
  (``block_pos``/``block_is_free``/``sort_order``/``id``) entfiel aus der
  ``.values(...)``-Projektion. Alle vier Felder sind reine Sortier-Hilfen
  (Annotation bzw. Modellspalte); ``order_by()`` referenziert sie ueber die
  Query, unabhaengig davon, ob ``.values()`` sie auch AUSGIBT (Standard-
  Django-Verhalten, kein ``.distinct()`` im Spiel). Keine der vier wird im
  Schleifenkoerper aus ``assignment[...]`` gelesen — der Wegfall aendert
  weder Sortierung noch Rueckgabewert.
* ``x__visible_field_configs__mutmut_{53,56}`` — ``output_field=IntegerField()``
  entfiel/wurde ``None``. ``Value(1)``/``Value(0)`` sind beides eindeutig
  als Integer aufloesbare Konstanten; Djangos Ausdrucksaufloesung leitet den
  ``output_field`` in diesem Fall automatisch her (keine Typ-Mehrdeutigkeit).
* ``x__visible_field_configs__mutmut_{58,60,62}`` — der ``then``-Zweig von
  ``When(field_group__isnull=True, then=Value(1))`` wurde NULL. ``block_is_free``
  nimmt in der App NUR die Werte ``0`` (Default-Zweig) oder den ``then``-Wert
  an; Postgres sortiert NULL in ASC IMMER hinter jedem echten Wert (NULLS
  LAST-Default) — genau wie den vorherigen Wert ``1``. Der relative Rang
  gegenueber ``0``-Zeilen UND gegenueber anderen gleich betroffenen Zeilen
  (deren Tiebreak faellt in beiden Faellen identisch auf die naechste
  Sortierspalte zurueck) bleibt unveraendert.
* ``x__visible_field_configs__mutmut_63`` — derselbe ``then``-Zweig wurde
  ``Value(2)`` statt ``Value(1)``. ``2`` ist wie ``1`` schlicht groesser als
  der Default-Zweig-Wert ``0`` — der Rang gegenueber gruppierten Feldern und
  der Tiebreak-Fallback unter gleich betroffenen freien Feldern aendern sich
  nicht.

Alle 14 Urteile sind unten je durch einen tatsaechlichen Testlauf UNTER der
jeweiligen Mutante bestaetigt (Bericht Abschnitt 3, ``killmap.tsv``:
``GRUEN-BESTAETIGT-AEQUIVALENT``) statt nur behauptet.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.models import QuerySet
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import (
    Activity,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldGroup,
    FieldTemplate,
    TimeFilter,
    User,
    WorkItem,
)
from core.services import zeitstrom_watch as zw
from core.services.zeitstrom_filters import ZeitstromState
from tests._factories import make_document_type, make_event

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

# Fester Kalendertag (keine DST-Transition in DE 2026) — bewusst NICHT
# ``timezone.localdate()`` (Lehre: keine Laufzeit-Daten in Testkoerpern
# dieses Musters, macht die Fenstergrenzen reproduzierbar).
_DAY = date(2026, 9, 10)
_BEFORE_WINDOW = timezone.make_aware(datetime(2026, 9, 10, 8, 0))
_IN_WINDOW = timezone.make_aware(datetime(2026, 9, 10, 11, 0))
_AFTER_WINDOW = timezone.make_aware(datetime(2026, 9, 10, 20, 0))
_EARLIER_IN_WINDOW = timezone.make_aware(datetime(2026, 9, 10, 10, 15))
_LATER_IN_WINDOW = timezone.make_aware(datetime(2026, 9, 10, 11, 45))


def _state(*, feed_type="", doc_type_id="", time_filter=None, target_date=_DAY):
    """Minimaler ``ZeitstromState`` fuer Direktaufrufe von ``_feed_projection``.

    ``live_context``/``view``/``local_date`` liest die Funktion nicht — sie
    bekommen unkritische Platzhalter statt einer echten
    ``TallyLiveContext``-Instanz.
    """
    return ZeitstromState(
        target_date=target_date,
        time_filter=time_filter,
        time_filters=(),
        live_context=None,
        feed_type=feed_type,
        doc_type_id=doc_type_id,
        view="",
        local_date=target_date,
    )


def _window_time_filter(facility):
    # ``time.min``/``time`` statt Uhrzeit-Strings: ``.create()`` liest den
    # Wert nicht aus der DB zurueck, ``TimeField.to_python`` laeuft also
    # nicht — ein String bliebe bis zum naechsten ``refresh_from_db()`` ein
    # String und ``get_time_range`` bekaeme kein ``datetime.time``.
    return TimeFilter.objects.create(
        facility=facility,
        label="Fenster 10-12",
        start_time=time(10, 0),
        end_time=time(12, 0),
    )


def _capture_order_by(monkeypatch):
    """Zeichnet jeden ``QuerySet.order_by()``-Aufruf als ``(Modell, Argumente)`` auf.

    Muster ``test_mutation_pins_099_facility_export_d.py::_capture_order_by``
    (dort begruendet: die fuenf ``order_by``-Spalten-Mutanten werden ueber
    die tatsaechlich aufgerufene Sortierkette gepinnt statt ueber die
    Ergebnisreihenfolge, weil ``_visible_field_configs`` die Queryset intern
    baut UND sofort konsumiert).
    """
    calls: list[tuple[type, tuple]] = []
    original_order_by = QuerySet.order_by

    def _spy(self, *args, **kwargs):
        calls.append((self.model, args))
        return original_order_by(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "order_by", _spy)
    return calls


def _event_row(doc_type):
    return {"document_type_id": doc_type.id, "document_type__sensitivity": doc_type.sensitivity}


# ---------------------------------------------------------------------------
# _feed_projection — Zeitfenster (Events, Aktivitaeten, Aufgaben, Hausverbote)
# ---------------------------------------------------------------------------


class TestFeedProjectionEventsTimeWindow:
    def test_events_respect_time_filter_soft_delete_and_window_bounds(self, facility, staff_user):
        """Pins ``x__feed_projection__mutmut_{3,5,20,21,22}``: ``get_time_range``
        bekam ``None``/gar keinen ``time_filter`` (3/5, faellt auf Ganztag
        zurueck statt das Schichtfenster zu nutzen), ``is_deleted=False``
        entfiel (20, geloeschte Ereignisse erscheinen wieder),
        ``occurred_at__gte``/``__lte`` entfielen einzeln (21/22, Fenster ohne
        Unter-/Obergrenze). Vier Ereignisse decken alle fuenf ab: eines VOR
        dem Fenster, eines IM Fenster, eines NACH dem Fenster und ein im
        Fenster liegendes, aber weich geloeschtes."""
        doc_type = make_document_type(facility=facility, name="Feed-Zeitfenster")
        time_filter = _window_time_filter(facility)

        event_before = make_event(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=_BEFORE_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        event_in = make_event(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        event_after = make_event(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=_AFTER_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        event_deleted = make_event(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
            is_deleted=True,
        )
        assert {event_before.id, event_after.id, event_deleted.id}  # nur zur Lesbarkeit referenziert

        state = _state(feed_type="events", time_filter=time_filter)
        result = zw._feed_projection(facility, staff_user, state)

        ids = {(kind, value[0]) for kind, value in result}
        assert ids == {("event", str(event_in.id))}


class TestFeedProjectionActivitiesTimeWindow:
    def test_activities_respect_window_bounds(self, facility, staff_user):
        """Pins ``x__feed_projection__mutmut_{57,58}``: ``occurred_at__gte``
        bzw. ``__lte`` entfielen einzeln aus dem Aktivitaeten-Filter."""
        doc_type = make_document_type(facility=facility, name="Feed-Aktivitaeten-Fenster")
        target_event = make_event(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        event_ct = ContentType.objects.get_for_model(Event)
        time_filter = _window_time_filter(facility)

        for when in (_BEFORE_WINDOW, _IN_WINDOW, _AFTER_WINDOW):
            Activity.objects.create(
                facility=facility,
                actor=staff_user,
                verb=Activity.Verb.UPDATED,
                target_type=event_ct,
                target_id=target_event.id,
                occurred_at=when,
            )
        in_window_activity = Activity.objects.get(occurred_at=_IN_WINDOW)

        state = _state(feed_type="activities", time_filter=time_filter)
        result = zw._feed_projection(facility, staff_user, state)

        ids = {(kind, value[0]) for kind, value in result}
        assert ids == {("activity", str(in_window_activity.id))}


class TestFeedProjectionWorkitemsTimeWindow:
    def test_workitems_respect_window_bounds(self, facility, staff_user):
        """Pins ``x__feed_projection__mutmut_{137,138}``: ``created_at__gte``
        bzw. ``__lte`` entfielen einzeln aus dem Aufgaben-Filter.
        ``WorkItem.created_at`` ist ``auto_now_add`` — die Testzeiten werden
        nach dem Anlegen per ``.update()`` gesetzt (Muster
        ``test_mutation_followup_feed.py::TestBuildFeedItemsWorkItemBoundary``)."""
        time_filter = _window_time_filter(facility)
        items = {}
        for label, when in (("before", _BEFORE_WINDOW), ("in", _IN_WINDOW), ("after", _AFTER_WINDOW)):
            wi = WorkItem.objects.create(
                facility=facility,
                created_by=staff_user,
                item_type=WorkItem.ItemType.TASK,
                status=WorkItem.Status.OPEN,
                title=f"Aufgabe-{label}",
            )
            WorkItem.objects.filter(pk=wi.pk).update(created_at=when)
            items[label] = wi

        state = _state(feed_type="workitems", time_filter=time_filter)
        result = zw._feed_projection(facility, staff_user, state)

        ids = {(kind, value[0]) for kind, value in result}
        assert ids == {("workitem", items["in"].id)}


class TestFeedProjectionBansFilters:
    def test_bans_are_scoped_by_facility_type_delete_state_and_window(self, facility, other_facility, staff_user):
        """Pins ``x__feed_projection__mutmut_{164,165,166,167,168}``: der
        Hausverbots-Zweig verlor je EINEN seiner fuenf Filter — ``facility``
        (164, KRITISCH: Ereignisse fremder Einrichtungen sichtbar),
        ``is_deleted=False`` (165, weich geloeschte Hausverbote sichtbar),
        ``document_type__system_type="ban"`` (166, beliebige Ereignistypen im
        Hausverbots-Reiter), ``occurred_at__gte``/``__lte`` (167/168, Fenster
        ohne Unter-/Obergrenze). Sechs Zeilen decken je einen Filterausfall
        ab, nur die korrekt gescopte bleibt sichtbar."""
        doc_type_ban = make_document_type(facility=facility, name="Hausverbot", system_type=DocumentType.SystemType.BAN)
        doc_type_other = make_document_type(facility=facility, name="Kein-Hausverbot")
        doc_type_ban_foreign = make_document_type(
            facility=other_facility, name="Hausverbot-Fremd", system_type=DocumentType.SystemType.BAN
        )
        time_filter = _window_time_filter(facility)

        event_ok = make_event(
            facility=facility,
            client=None,
            document_type=doc_type_ban,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        make_event(
            facility=other_facility,
            client=None,
            document_type=doc_type_ban_foreign,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        make_event(
            facility=facility,
            client=None,
            document_type=doc_type_ban,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
            is_deleted=True,
        )
        make_event(
            facility=facility,
            client=None,
            document_type=doc_type_other,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        make_event(
            facility=facility,
            client=None,
            document_type=doc_type_ban,
            occurred_at=_BEFORE_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        make_event(
            facility=facility,
            client=None,
            document_type=doc_type_ban,
            occurred_at=_AFTER_WINDOW,
            data_json={},
            created_by=staff_user,
        )

        state = _state(feed_type="bans", time_filter=time_filter)
        result = zw._feed_projection(facility, staff_user, state)

        ids = {(kind, value[0]) for kind, value in result}
        assert ids == {("ban", str(event_ok.id))}


class TestFeedProjectionDocTypeFilter:
    def test_doc_type_id_filter_keeps_exactly_the_matching_event(self, facility, staff_user):
        """Pins ``x__feed_projection__mutmut_{34,35,36,37,38}``: die
        ``doc_type``-Nachfilterung der Ereignisliste wurde ``None`` (34,
        Absturz), verglich ``str(None)`` statt der echten Spalte (35, filtert
        alles heraus), griff auf einen falsch geschriebenen Schluessel zu
        (36/37, ``KeyError``) oder invertierte den Vergleich (38, zeigt das
        FALSCHE Ereignis)."""
        doc_type_a = make_document_type(facility=facility, name="Filter-A")
        doc_type_b = make_document_type(facility=facility, name="Filter-B")
        event_a = make_event(
            facility=facility,
            client=None,
            document_type=doc_type_a,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        make_event(
            facility=facility,
            client=None,
            document_type=doc_type_b,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )

        state = _state(feed_type="events", doc_type_id=str(doc_type_a.id))
        result = zw._feed_projection(facility, staff_user, state)

        assert len(result) == 1
        kind, value = result[0]
        assert kind == "event"
        assert value[0] == str(event_a.id)


class TestFeedProjectionActivityVerbExclude:
    def test_created_verb_excluded_only_in_the_merged_all_types_view(self, facility, staff_user):
        """Pins ``x__feed_projection__mutmut_61``: ``exclude(verb=Activity.
        Verb.CREATED)`` wurde ``exclude(verb=None)`` — schliesst nichts mehr
        aus, ``created``-Aktivitaeten (redundant zur Ereigniskarte) tauchen
        in der zusammengefuehrten ``include_all``-Ansicht doppelt auf."""
        doc_type = make_document_type(facility=facility, name="Verb-Exclude")
        target_event = make_event(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        event_ct = ContentType.objects.get_for_model(Event)
        activity_created = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target_type=event_ct,
            target_id=target_event.id,
            occurred_at=_IN_WINDOW,
        )
        activity_updated = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target_type=event_ct,
            target_id=target_event.id,
            occurred_at=_EARLIER_IN_WINDOW,
        )

        state = _state(feed_type="")  # include_all
        result = zw._feed_projection(facility, staff_user, state)

        activity_ids = {value[0] for kind, value in result if kind == "activity"}
        assert activity_ids == {str(activity_updated.id)}
        assert str(activity_created.id) not in activity_ids


class TestFeedProjectionVisibleEventIdsForActivityTarget:
    def test_activity_targeting_a_visible_event_stays_in_the_feed(self, facility, staff_user):
        """Pins ``x__feed_projection__mutmut_69``: ``Event.objects.visible_to
        (user)`` bekam ``user=None`` statt des echten Nutzers —
        ``EventQuerySet.visible_to`` beantwortet ``user is None`` mit
        ``self.none()`` (WHERE 1=0). ``visible_event_ids`` wird dadurch zur
        LEEREN Subquery und eine Aktivitaet, deren Ziel-Ereignis fuer den
        Nutzer eigentlich sichtbar ist, verschwindet zu Unrecht aus dem Feed.
        (``x__feed_projection__mutmut_{66,68,72}`` sind die Nachbar-Mutationen
        an derselben Zeile — ``flat=None``/entfernt/``False`` statt
        ``flat=True`` — und AEQUIVALENT, siehe Kopf-Docstring: ``flat``
        wirkt nur bei direkter Python-Iteration, nicht wenn die Queryset
        unausgewertet als ``__in=``-Subquery verwendet wird.)"""
        doc_type = make_document_type(facility=facility, name="Visible-Event-Ids")
        target_event = make_event(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        event_ct = ContentType.objects.get_for_model(Event)
        activity = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target_type=event_ct,
            target_id=target_event.id,
            occurred_at=_IN_WINDOW,
        )

        state = _state(feed_type="activities")
        result = zw._feed_projection(facility, staff_user, state)

        assert len(result) == 1
        kind, value = result[0]
        assert kind == "activity"
        assert value[0] == str(activity.id)


class TestFeedProjectionActivityQueryBudget:
    def test_activity_batch_stays_at_a_constant_query_count(self, facility, staff_user):
        """Pins ``x__feed_projection__mutmut_{81,84,85}``: ``.prefetch_related
        (None)`` (81, loescht die GenericFK-Vorabladung des Aktivitaetsziels)
        sowie ``.select_related("actor", "target_type")`` verlor je ein
        Argument (84 ``actor``, 85 ``target_type``). Keine der drei
        Mutationen aendert den RUECKGABEWERT — nur die Query-Anzahl waechst
        dann mit der Zeilenzahl (N+1) statt konstant zu bleiben. Der
        ``context={"action": "event_created"}`` ist noetig, damit
        ``render_activity_summary`` ueberhaupt den ``EVENT_CREATED``-Zweig
        nimmt und ``activity.target`` (die GenericFK, die ``prefetch_related``
        vorlaedt) UND ``target.document_type``/``target.client`` liest — mit
        leerem ``context`` faellt die Funktion auf ``_generic_summary``
        zurueck, die nur ``target_type.model`` braucht und ``.target`` nie
        anfasst (81 waere dann unbeobachtbar). Nachweis per Differenzmessung
        (1 vs. 3 Aktivitaeten, je eigenes Ziel-Ereignis und eigener Akteur)
        statt absoluter Zahl — robust gegen Setup-Queries."""
        event_ct = ContentType.objects.get_for_model(Event)

        def _make_activity(n):
            doc_type = make_document_type(facility=facility, name=f"QB-DocType-{n}")
            target = make_event(
                facility=facility,
                client=None,
                document_type=doc_type,
                occurred_at=_IN_WINDOW,
                data_json={},
                created_by=staff_user,
            )
            actor = User.objects.create_user(
                username=f"qb-actor-{n}", role=User.Role.STAFF, facility=facility, first_name=f"Akteur{n}"
            )
            return Activity.objects.create(
                facility=facility,
                actor=actor,
                verb=Activity.Verb.UPDATED,
                target_type=event_ct,
                target_id=target.id,
                occurred_at=_IN_WINDOW,
                context={"action": Activity.Action.EVENT_CREATED.value},
            )

        _make_activity(1)
        state = _state(feed_type="activities")
        with CaptureQueriesContext(connection) as ctx_one:
            zw._feed_projection(facility, staff_user, state)
        n_one = len(ctx_one.captured_queries)

        _make_activity(2)
        _make_activity(3)
        with CaptureQueriesContext(connection) as ctx_three:
            zw._feed_projection(facility, staff_user, state)
        n_three = len(ctx_three.captured_queries)

        assert n_three == n_one, (
            f"Aktivitaeten-Query-Anzahl waechst mit der Zeilenzahl ({n_one} -> {n_three}): "
            "select_related/prefetch_related der Aktivitaeten-Ladestelle fehlen (N+1)."
        )


class TestFeedProjectionActivityProjectionTuple:
    def test_activity_tuple_carries_the_real_actor_and_target_fields(self, facility, staff_user):
        """Pins ``x__feed_projection__mutmut_{104,105,108,109,110,111,235,254}``:
        sechs Mutanten benannten je einen Schluessel des handgebauten
        Aktivitaeten-``row``-Dicts um (``actor__username``/``actor__first_name``/
        ``actor__last_name`` → ``XX...XX``/``GROSSBUCHSTABEN``) — ``_related_user``
        liest ueber ``row.get(...)`` und bekommt dann ``None`` statt des echten
        Werts. Zwei weitere ersetzten ``str(row["id"])``/``str(row["target_id"])``
        in der Ausgabe-Tupel-Konstruktion durch ``str(None)``. EIN Test auf die
        VOLLSTAENDIGE Ausgabe-Tupel deckt alle acht ab."""
        doc_type = make_document_type(facility=facility, name="Aktivitaeten-Tupel")
        target_event = make_event(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        actor = User.objects.create_user(
            username="tupel-actor",
            role=User.Role.STAFF,
            facility=facility,
            first_name="Annika",
            last_name="Testperson",
            display_name="Annika T.",
        )
        event_ct = ContentType.objects.get_for_model(Event)
        activity = Activity.objects.create(
            facility=facility,
            actor=actor,
            verb=Activity.Verb.UPDATED,
            target_type=event_ct,
            target_id=target_event.id,
            occurred_at=_IN_WINDOW,
        )

        state = _state(feed_type="activities")
        result = zw._feed_projection(facility, staff_user, state)

        assert len(result) == 1
        kind, value = result[0]
        assert kind == "activity"
        assert value[0] == str(activity.id)
        assert value[1] == activity.occurred_at
        assert value[2] == Activity.Verb.UPDATED
        assert isinstance(value[3], str) and value[3]
        assert value[4] == (str(actor.id), "tupel-actor", "Annika T.", "Annika", "Testperson")
        assert value[5] == event_ct.id
        assert value[6] == "event"
        assert value[7] == str(target_event.id)


class TestFeedProjectionWorkitemTypeLabel:
    def test_workitem_items_keep_the_literal_type_label(self, facility, staff_user):
        """Pins ``x__feed_projection__mutmut_{145,146}``: das Typ-Label des
        Aufgaben-Eintrags wurde ``"XXworkitemXX"``/``"WORKITEM"`` statt
        ``"workitem"`` — die Ausgabe-Tupel ``(item["type"], value)`` traegt
        das Label direkt nach aussen."""
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Label-Test",
        )
        WorkItem.objects.filter(pk=wi.pk).update(created_at=_IN_WINDOW)

        state = _state(feed_type="workitems")
        result = zw._feed_projection(facility, staff_user, state)

        assert len(result) == 1
        kind, value = result[0]
        assert kind == "workitem"
        assert value[0] == wi.id


class TestFeedProjectionSortOrder:
    def test_items_sort_newest_first(self, facility, staff_user):
        """Pins ``x__feed_projection__mutmut_{187,189,193}``: ``items.sort(key=
        ..., reverse=True)`` verlor ``reverse`` komplett (189), bekam
        ``reverse=None`` (187) oder ``reverse=False`` (193) — alle drei
        sortieren aufsteigend (aeltestes zuerst) statt absteigend."""
        doc_type = make_document_type(facility=facility, name="Sortier-Test")
        event_early = make_event(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=_EARLIER_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )
        event_late = make_event(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=_LATER_IN_WINDOW,
            data_json={},
            created_by=staff_user,
        )

        state = _state(feed_type="events")
        result = zw._feed_projection(facility, staff_user, state)

        ordered_ids = [value[0] for kind, value in result]
        assert ordered_ids == [str(event_late.id), str(event_early.id)]


# ---------------------------------------------------------------------------
# _visible_field_configs
# ---------------------------------------------------------------------------


class TestVisibleFieldConfigsOrderByColumns:
    def test_order_by_keeps_all_five_columns(self, facility, staff_user, monkeypatch):
        """Pins ``x__visible_field_configs__mutmut_{13,14,15,16,17}``: je eine
        der fuenf ``order_by``-Spalten (``document_type_id``, ``block_pos``,
        ``block_is_free``, ``sort_order``, ``id``) fehlte einzeln. Spy auf
        ``QuerySet.order_by`` (Standardblock 72) statt Ergebnisreihenfolge."""
        doc_type = make_document_type(facility=facility, name="OrderBy-Spalten")
        calls = _capture_order_by(monkeypatch)

        zw._visible_field_configs([_event_row(doc_type)], staff_user)

        matching = [args for model, args in calls if model is DocumentTypeField]
        assert matching == [("document_type_id", "block_pos", "block_is_free", "sort_order", "id")]


class TestVisibleFieldConfigsBlockPosition:
    def test_group_sort_order_wins_block_position_over_the_fields_own_sort_order(self, facility, staff_user):
        """Pins ``x__visible_field_configs__mutmut_43``: das ERSTE
        ``Coalesce``-Argument (``"field_group__sort_order"``) wurde ``None``
        — ein gruppiertes Feld ordnete sich dann ueber seine EIGENE (nur
        gruppenintern gueltige) ``sort_order`` ein statt ueber die
        Block-Position seiner Gruppe."""
        doc_type = make_document_type(facility=facility, name="BlockPos-Gruppe")
        group = FieldGroup.objects.create(document_type=doc_type, name="Fruehe Gruppe", sort_order=1)
        ft_grouped = FieldTemplate.objects.create(
            facility=facility, name="Gruppenfeld-Hohe-Eigene-Sortierung", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(
            document_type=doc_type, field_template=ft_grouped, field_group=group, sort_order=99
        )
        ft_free = FieldTemplate.objects.create(
            facility=facility, name="Spaetes-Freifeld", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_free, field_group=None, sort_order=5)

        result = zw._visible_field_configs([_event_row(doc_type)], staff_user)

        slugs = [entry[0] for entry in result[doc_type.id]]
        assert slugs == [ft_grouped.slug, ft_free.slug]

    def test_free_field_falls_back_to_its_own_sort_order_not_null(self, facility, staff_user):
        """Pins ``x__visible_field_configs__mutmut_44``: das ZWEITE
        ``Coalesce``-Argument (``"sort_order"``) wurde ``None`` — ein freies
        (nicht gruppiertes) Feld bekam dann IMMER ``block_pos=NULL``
        (sortiert in Postgres ans Ende) statt seiner eigenen ``sort_order``."""
        doc_type = make_document_type(facility=facility, name="BlockPos-Fallback")
        ft_free = FieldTemplate.objects.create(
            facility=facility, name="Fruehes-Freifeld", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_free, field_group=None, sort_order=0)
        group = FieldGroup.objects.create(document_type=doc_type, name="Spaete Gruppe", sort_order=5)
        ft_grouped = FieldTemplate.objects.create(
            facility=facility, name="Spaet-Gruppiertes-Feld", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(
            document_type=doc_type, field_template=ft_grouped, field_group=group, sort_order=0
        )

        result = zw._visible_field_configs([_event_row(doc_type)], staff_user)

        slugs = [entry[0] for entry in result[doc_type.id]]
        assert slugs == [ft_free.slug, ft_grouped.slug]


class TestVisibleFieldConfigsBlockIsFreeTiebreak:
    def test_free_field_defers_to_a_grouped_field_at_the_same_block_position(self, facility, staff_user):
        """Pins ``x__visible_field_configs__mutmut_{52,54,55,61,64,65}``: die
        ``block_is_free``-Unterscheidung (``When(field_group__isnull=True,
        then=Value(1)), default=Value(0)``) haelt bei GLEICHEM ``block_pos``
        ein freies Feld hinter einem gruppierten — UNABHAENGIG von deren
        jeweils EIGENER ``sort_order``. Bedingung invertiert (61), ``then``
        oder ``default`` auf NULL (55/52 bzw. via fehlendem ``When`` 54) oder
        ``default`` gleich ``then`` gesetzt (65) heben den Vorrang je auf;
        der Tiebreak faellt dann auf die eigene ``sort_order`` zurueck und
        das (hier bewusst NIEDRIGER sortierte) freie Feld ruckt vor."""
        doc_type = make_document_type(facility=facility, name="BlockIsFree-Tiebreak")
        group = FieldGroup.objects.create(document_type=doc_type, name="Gleichrang-Gruppe", sort_order=7)
        ft_grouped = FieldTemplate.objects.create(
            facility=facility, name="Gruppiert-Hohe-Eigene-Sortierung", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(
            document_type=doc_type, field_template=ft_grouped, field_group=group, sort_order=9
        )
        ft_free = FieldTemplate.objects.create(
            facility=facility, name="Frei-Gleicher-Blockrang", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_free, field_group=None, sort_order=7)

        result = zw._visible_field_configs([_event_row(doc_type)], staff_user)

        slugs = [entry[0] for entry in result[doc_type.id]]
        assert slugs == [ft_grouped.slug, ft_free.slug]


class TestVisibleFieldConfigsContinueNotBreak:
    def test_hidden_field_is_skipped_not_a_loop_stop(self, facility, assistant_user):
        """Pins ``x__visible_field_configs__mutmut_109``: die Sichtbarkeits-
        pruefung sprang von ``continue`` auf ``break`` — ein fuer die Rolle
        NICHT sichtbares Feld beendete dann die GESAMTE Sortierschleife
        statt nur dieses eine Feld zu ueberspringen. Ein danach folgendes,
        eigentlich sichtbares Feld fehlte dadurch komplett im Ergebnis."""
        doc_type = make_document_type(
            facility=facility, name="Sichtbarkeits-Schleife", sensitivity=DocumentType.Sensitivity.NORMAL
        )
        ft_visible_1 = FieldTemplate.objects.create(
            facility=facility, name="Sichtbar-1", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_visible_1, sort_order=0)
        ft_hidden = FieldTemplate.objects.create(
            facility=facility,
            name="Verdeckt-Fuer-Assistant",
            field_type=FieldTemplate.FieldType.TEXT,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_hidden, sort_order=1)
        ft_visible_2 = FieldTemplate.objects.create(
            facility=facility, name="Sichtbar-2", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_visible_2, sort_order=2)

        result = zw._visible_field_configs([_event_row(doc_type)], assistant_user)

        slugs = [entry[0] for entry in result[doc_type.id]]
        assert slugs == [ft_visible_1.slug, ft_visible_2.slug]
