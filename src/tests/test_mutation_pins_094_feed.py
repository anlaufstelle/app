"""Pin-Tests zum Meilenstein release-0.94 — triagierte Survivors in ``core.services.events.feed``.

Quelle: Survivor-Triage des Moduls ``src/core/services/events/feed.py``
(Mutantenbaum ``mutants/src/core/services/events/feed.py``). Vorgabe ist
die Survivor-Triage release-0.94 (Ledger des Meilensteins) (44 triagierte Mutanten);
gepinnt sind davon 41, drei sind unten begruendet ausgenommen.
Schwerpunkte:

* **Zeitfenster aller Feed-Zweige.** Die Boundary-Tests in
  ``test_mutation_followup_feed.py`` decken bisher nur den Event- und (halb)
  den WorkItem-Zweig ab. Activity-, Ban- und WorkItem-Zweig bekommen hier je
  einen Test, der beide Fenstergrenzen GESCHLOSSEN festschreibt (``__gte`` /
  ``__lte``) und je einen Nachbarn eine Mikrosekunde ausserhalb ausschliesst.
* **N+1-Schutz der vier Builder.** ``select_related``/``prefetch_related``
  waren nur ueber eine relative Drift-Schranke (``<= small + 5``) gesichert —
  ein einzeln entferntes Relationsfeld blieb darunter. Die Pins hier messen
  die Query-Zahl bei 1 vs. 5 Objekten und fassen im Messfenster JEDE der
  vorgeladenen Relationen an; ein fehlendes Feld waechst damit linear mit N.
* **Vorschau-Aufbau.** Ein fuer die Rolle gesperrtes Feld darf die
  Feldschleife nicht ABBRECHEN (Datenverlust in der Anzeige), und ein
  Nicht-Event-Eintrag im Feed darf die Anreicherung nicht abbrechen.
* **``_format_preview_value``.** Label-Fallback der Mehrfachauswahl, die
  ngettext-Pluralform der Datei-Marker und der exakte Platzhaltertext fuer
  nicht entschluesselbare Werte.
* **DST-Semantik (Refs #2057).** ``_is_imaginary``/``_is_ambiguous``/
  ``_resolve_window_end``/``shift_window_covers`` rechnen ausdruecklich in der
  AKTIVEN Zeitzone und ueber die UTC-Achse — nicht ueber die Prozess-Zone und
  nicht ueber Wanduhrarithmetik. Die Pins trennen beides, indem sie die aktive
  Zone per ``timezone.override`` von der Prozess-Zone (Europe/Berlin)
  abweichen lassen bzw. den Rand des ``datetime``-Wertebereichs benutzen, an
  dem eine Umrechnung ueber die Prozess-Zone ueberlaeuft.

Nicht gepinnt (begruendet)
==========================

* ``_feed_activities__mutmut_15`` — ``values_list("pk", flat=True)`` wird zu
  ``values_list(flat=True)``. Unter Django 6.0.8 setzt ``QuerySet.values_list``
  bei ``flat=True`` ohne Feldargument selbst das erste konkrete Feld des
  Modells ein (``django/db/models/query.py``: ``elif not fields: fields =
  [self.model._meta.concrete_fields[0].attname]``); fuer ``Event`` ist das
  ``id`` und damit die PK. ``visible_event_ids`` geht ausschliesslich als
  Subquery in ``target_id__in`` und wird nie in Python iteriert.

  Beweis (nachvollziehbar in ``manage.py shell``)::

      Event.objects.all().values_list(flat=True).query
      # SELECT "core_event"."id" AS "id"  FROM "core_event" ...
      Event.objects.all().values_list("pk", flat=True).query
      # SELECT "core_event"."id" AS "pk" FROM "core_event" ...

  Der einzige Unterschied ist der Spalten-Alias, der innerhalb von ``IN (...)``
  bedeutungslos ist; das SQL des vollstaendigen ``Activity``-Ausschlusses ist
  zeichengleich. Zusaetzlich mit dem echten Mutantenrumpf gegengeprueft: die
  Datei bleibt gruen (Negativkontrolle, kein Pin behauptet ihn).
* ``enrich_events_with_preview__mutmut_33`` (``dt_fields.get(..., None)``) /
  ``__mutmut_35`` (``dt_fields.get(..., )``, impliziter ``None``-Default) —
  der Listen-Default ``[]`` faellt weg. ``dt_ids`` (Mengenbildung) und die
  Anreicherungsschleife laufen ueber DIESELBE Liste ``feed_items`` mit dem
  identischen Praedikat ``item["type"] in ("event", "ban")``, und ``dt_fields``
  ist eine Dict-Comprehension ueber genau ``dt_ids``. Fuer jedes in der
  Schleife bearbeitete Item ist ``event.document_type_id`` also garantiert
  Schluessel von ``dt_fields``; der Default wird nie ausgewertet. Bei einer
  echten Modellinstanz ist ``document_type_id`` innerhalb des synchronen
  Aufrufs stabil (auch ein ``defer``-tes FK-Attribut wird nach dem ersten
  Zugriff gecacht), es gibt keinen Produktionspfad in den Default.
  Beobachtbar waere er nur mit einer Attrappe, deren ``document_type_id``
  zwischen den beiden Lesungen wechselt — ein Fixture-Fake, den die
  Konventionen dieser Datei ausschliessen; ein solcher Pin wuerde eine im
  Produktionscode unerreichbare Semantik festschreiben. Mit beiden echten
  Mutantenruempfen gegengeprueft: die Datei bleibt gruen.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); Sprachkontext
wird bei Texten per ``translation.override`` festgenagelt; ``django_db`` nur
dort, wo wirklich Datenbank noetig ist; KEINE Imports aus
``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py``, alle
uebrigen Objekte sind echte Model-Instanzen (fuer die reinen Formatier-Tests
unsaved, weil dort keine Zeile gebraucht wird). Jeder Test nennt seine
Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.
"""

from __future__ import annotations

import datetime as dt
import os
import time as time_module
from contextlib import contextmanager
from zoneinfo import ZoneInfo

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation

from core.models import (
    Activity,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldTemplate,
    TimeFilter,
    WorkItem,
)
from core.services.events import build_feed_items, get_time_range
from core.services.events import feed as feed_module
from core.services.events.feed import (
    _build_event_preview_fields,
    _format_preview_value,
    _is_ambiguous,
    _is_imaginary,
    _resolve_window_end,
    enrich_events_with_preview,
    shift_window_covers,
)

BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
UTC = dt.UTC

MICROSECOND = dt.timedelta(microseconds=1)


# ============================================================================
# Hilfen
# ============================================================================


@pytest.fixture
def normal_doc_type(facility):
    """NORMAL-Dokumentationstyp ohne ``system_type`` — Standard-Ereignisse."""
    return DocumentType.objects.create(facility=facility, name="Pin-Normal")


@pytest.fixture
def ban_doc_type(facility):
    """``system_type=BAN``, ELEVATED — damit ``staff`` es lesen darf."""
    return DocumentType.objects.create(
        facility=facility,
        name="Pin-Hausverbot",
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
    )


def _event(facility, doc_type, user, client, when):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=when,
        data_json={},
        created_by=user,
    )


def _activity(facility, actor, target, when):
    return Activity.objects.create(
        facility=facility,
        actor=actor,
        verb=Activity.Verb.UPDATED,
        target_type=ContentType.objects.get_for_model(type(target)),
        target_id=target.pk,
        context={},
        occurred_at=when,
    )


def _workitem(facility, client, user, title, created_at=None):
    item = WorkItem.objects.create(
        facility=facility,
        client=client,
        created_by=user,
        assigned_to=user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title=title,
    )
    if created_at is not None:
        # ``created_at`` ist ``auto_now_add`` — nur ueber ``update()`` setzbar.
        WorkItem.objects.filter(pk=item.pk).update(created_at=created_at)
    return item


def _count_queries(func):
    """Query-Zahl EINES Aufrufs — inklusive aller darin beruehrten Relationen."""
    with CaptureQueriesContext(connection) as ctx:
        func()
    return len(ctx.captured_queries)


@contextmanager
def _process_timezone(name):
    """Prozess-Zeitzone (``TZ``/``time.tzset``) fuer die Dauer des Blocks fixieren.

    ``datetime.astimezone(None)`` — das Ziel mehrerer Mutanten — rechnet in
    genau dieser Zone, ``astimezone(UTC)`` nicht. Der Test darf sich nicht auf
    die vom Test-Runner geerbte Zone verlassen; Django setzt ``TZ`` zwar aus
    ``settings.TIME_ZONE``, aber das ist keine Zusicherung dieses Moduls.
    """
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time_module.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time_module.tzset()


# ============================================================================
# Zeitfenster der Feed-Zweige — beide Grenzen geschlossen
# ============================================================================


@pytest.mark.django_db
class TestFeedActivitiesWindow:
    """``_feed_activities``: ``occurred_at__gte``/``__lte`` (Refs #2057-Semantik)."""

    def test_activity_window_is_closed_at_both_ends(self, facility, staff_user, client_identified):
        """Pin: ``_feed_activities__mutmut_4`` — ``filter(occurred_at__lte=end_dt)``
        ohne untere Grenze; ``_feed_activities__mutmut_5`` — ``filter(occurred_at__gte=start_dt)``
        ohne obere Grenze.

        Vier Aktivitaeten: eine Mikrosekunde vor ``start_dt``, exakt auf
        ``start_dt``, exakt auf ``end_dt``, eine Mikrosekunde danach. Das
        Original liefert exakt die beiden Grenzfaelle (absteigend sortiert).
        Mutant 4 nimmt die Vortags-Aktivitaet dazu (3 Eintraege), Mutant 5 die
        des Folgetages (3 Eintraege) — die exakte Listengleichheit faellt in
        beiden Faellen.

        NICHT gepinnt wird hier ``_feed_activities__mutmut_15``: der Test
        durchlaeuft zwar den ``visible_event_ids``-Subselect, bleibt beim
        Mutanten aber gruen — er ist unter Django 6.0.8 aequivalent (Begruendung
        im Modul-Docstring unter „Nicht gepinnt").
        """
        today = timezone.localdate()
        start_dt, end_dt = get_time_range(today)
        _activity(facility, staff_user, client_identified, start_dt - MICROSECOND)
        at_start = _activity(facility, staff_user, client_identified, start_dt)
        at_end = _activity(facility, staff_user, client_identified, end_dt)
        _activity(facility, staff_user, client_identified, end_dt + MICROSECOND)

        items = build_feed_items(facility, today, feed_type="activities", user=staff_user)

        assert [i["object"].pk for i in items] == [at_end.pk, at_start.pk]


@pytest.mark.django_db
class TestFeedBansWindow:
    """``_feed_bans``: ``occurred_at__gte``/``__lte``."""

    def test_ban_window_is_closed_at_both_ends(self, facility, staff_user, client_identified, ban_doc_type):
        """Pin: ``_feed_bans__mutmut_17`` (untere Grenze entfernt) und
        ``_feed_bans__mutmut_18`` (obere Grenze entfernt).

        Original: exakt die beiden Grenz-Hausverbote. Mutant 17 zieht das
        Hausverbot des Vortags mit hinein, Mutant 18 das des Folgetags — die
        Liste hat dann 3 statt 2 Eintraege.
        """
        today = timezone.localdate()
        start_dt, end_dt = get_time_range(today)
        _event(facility, ban_doc_type, staff_user, client_identified, start_dt - MICROSECOND)
        at_start = _event(facility, ban_doc_type, staff_user, client_identified, start_dt)
        at_end = _event(facility, ban_doc_type, staff_user, client_identified, end_dt)
        _event(facility, ban_doc_type, staff_user, client_identified, end_dt + MICROSECOND)

        items = build_feed_items(facility, today, feed_type="bans", user=staff_user)

        assert [i["object"].pk for i in items] == [at_end.pk, at_start.pk]


@pytest.mark.django_db
class TestFeedWorkitemsWindow:
    """``_feed_workitems``: ``created_at__gte``/``__lte``."""

    def test_workitem_window_is_closed_at_both_ends(self, facility, staff_user, client_identified):
        """Pin: ``_feed_workitems__mutmut_9`` (``created_at__gte`` entfaellt) und
        ``_feed_workitems__mutmut_10`` (``created_at__lte`` entfaellt).

        Der bestehende Test ``test_workitem_exactly_at_start_included`` prueft
        nur ``len(...) == 1`` mit einem einzigen Datensatz und laesst beide
        Mutanten am Leben. Hier liegen vier Aufgaben im Feld; das Original
        liefert exakt die beiden Grenzfaelle, Mutant 9 zusaetzlich die des
        Vortags, Mutant 10 die des Folgetags.
        """
        today = timezone.localdate()
        start_dt, end_dt = get_time_range(today)
        _workitem(facility, client_identified, staff_user, "Vortag", start_dt - MICROSECOND)
        at_start = _workitem(facility, client_identified, staff_user, "Start", start_dt)
        at_end = _workitem(facility, client_identified, staff_user, "Ende", end_dt)
        _workitem(facility, client_identified, staff_user, "Folgetag", end_dt + MICROSECOND)

        items = build_feed_items(facility, today, feed_type="workitems", user=staff_user)

        assert [i["object"].pk for i in items] == [at_end.pk, at_start.pk]


# ============================================================================
# N+1-Schutz: jede vorgeladene Relation wird im Messfenster angefasst
# ============================================================================


@pytest.mark.django_db
class TestFeedEventsPreloadsAllRenderedRelations:
    """``_feed_events``: ``select_related("document_type", "client", "created_by")``."""

    def test_query_count_stays_constant_when_touching_every_relation(
        self, facility, staff_user, client_identified, normal_doc_type
    ):
        """Pin: ``_feed_events__mutmut_21`` (``document_type`` faellt raus),
        ``_feed_events__mutmut_22`` (``client``), ``_feed_events__mutmut_23``
        (``created_by``).

        Gemessen wird die Query-Zahl EINES Feed-Aufbaus inklusive Zugriff auf
        alle drei Relationen — einmal mit 1, einmal mit 5 Ereignissen. Das
        Original bleibt konstant (alles vorgeladen). Jeder der drei Mutanten
        laedt das fehlende Objekt pro Ereignis einzeln nach: 4 zusaetzliche
        Queries im grossen Lauf, die Gleichheit faellt.

        Die bestehende Drift-Schranke in ``test_zeitstrom_perf.py``
        (``large <= small + 5``) deckt genau diesen Abstand nicht ab.
        """
        when = timezone.localtime()
        today = when.date()

        def touch():
            items = build_feed_items(facility, today, feed_type="events", user=staff_user)
            # Die Relationen werden ausdruecklich materialisiert (nicht nur
            # beruehrt): jede Nachlade-Query eines fehlenden
            # ``select_related``-/``prefetch_related``-Feldes entsteht damit
            # INNERHALB des Messfensters.
            return [
                (item["object"].document_type.pk, item["object"].client.pk, item["object"].created_by.pk)
                for item in items
            ]

        _event(facility, normal_doc_type, staff_user, client_identified, when)
        touch()  # Aufwaermen: ContentType-/Rollen-Caches fuellen
        small = _count_queries(touch)

        for _ in range(4):
            _event(facility, normal_doc_type, staff_user, client_identified, when)
        assert len(touch()) == 5
        large = _count_queries(touch)

        assert large == small


@pytest.mark.django_db
class TestFeedBansPreloadsAllRenderedRelations:
    """``_feed_bans``: ``select_related("document_type", "client", "created_by")``."""

    def test_query_count_stays_constant_when_touching_every_relation(
        self, facility, staff_user, client_identified, ban_doc_type
    ):
        """Pin: ``_feed_bans__mutmut_6`` (``document_type`` faellt raus),
        ``_feed_bans__mutmut_7`` (``client``), ``_feed_bans__mutmut_8``
        (``created_by``).

        Aufbau wie im Event-Zweig: bei 5 statt 1 Hausverbot bleibt die
        Query-Zahl des Originals gleich, jeder Mutant fuegt 4 Nachlade-Queries
        hinzu. Fuer den Ban-Zweig gab es bisher gar keinen Query-Count-Test.
        """
        when = timezone.localtime()
        today = when.date()

        def touch():
            items = build_feed_items(facility, today, feed_type="bans", user=staff_user)
            # Die Relationen werden ausdruecklich materialisiert (nicht nur
            # beruehrt): jede Nachlade-Query eines fehlenden
            # ``select_related``-/``prefetch_related``-Feldes entsteht damit
            # INNERHALB des Messfensters.
            return [
                (item["object"].document_type.pk, item["object"].client.pk, item["object"].created_by.pk)
                for item in items
            ]

        _event(facility, ban_doc_type, staff_user, client_identified, when)
        touch()
        small = _count_queries(touch)

        for _ in range(4):
            _event(facility, ban_doc_type, staff_user, client_identified, when)
        assert len(touch()) == 5
        large = _count_queries(touch)

        assert large == small


@pytest.mark.django_db
class TestFeedWorkitemsPreloadsAllRenderedRelations:
    """``_feed_workitems``: ``select_related("client", "assigned_to")``."""

    def test_query_count_stays_constant_when_touching_every_relation(self, facility, staff_user, client_identified):
        """Pin: ``_feed_workitems__mutmut_5`` — ``select_related("assigned_to")``,
        ``client`` faellt raus.

        Mit 5 Aufgaben laedt der Mutant den Klienten jeder Aufgabe einzeln
        nach (4 Queries mehr als im Original, das konstant bleibt).
        """
        when = timezone.localtime()
        today = when.date()

        def touch():
            items = build_feed_items(facility, today, feed_type="workitems", user=staff_user)
            # Die Relationen werden ausdruecklich materialisiert (nicht nur
            # beruehrt): jede Nachlade-Query eines fehlenden
            # ``select_related``-/``prefetch_related``-Feldes entsteht damit
            # INNERHALB des Messfensters.
            return [(item["object"].client.pk, item["object"].assigned_to.pk) for item in items]

        _workitem(facility, client_identified, staff_user, "Aufgabe 0")
        touch()
        small = _count_queries(touch)

        for i in range(1, 5):
            _workitem(facility, client_identified, staff_user, f"Aufgabe {i}")
        assert len(touch()) == 5
        large = _count_queries(touch)

        assert large == small


@pytest.mark.django_db
class TestFeedActivitiesPreloadsActorTypeAndTarget:
    """``_feed_activities``: ``select_related("actor", "target_type")`` + ``activity_target_prefetch()``."""

    def test_query_count_stays_constant_when_touching_actor_type_and_target(
        self, facility, staff_user, client_identified
    ):
        """Pin: ``_feed_activities__mutmut_51`` (``actor`` faellt aus
        ``select_related``), ``_feed_activities__mutmut_52`` (``target_type``
        faellt raus), ``_feed_activities__mutmut_48``
        (``prefetch_related(None)`` statt ``activity_target_prefetch()``).

        Der Zeitstrom rendert je Aktivitaet Handelnde:n, Zieltyp und Ziel —
        genau diese drei Zugriffe stehen im Messfenster. Das Original bleibt
        bei 1 und bei 5 Aktivitaeten gleich teuer; jeder der drei Mutanten
        laedt sein Stueck pro Aktivitaet nach (4 Queries mehr).
        """
        when = timezone.localtime()
        today = when.date()

        def touch():
            items = build_feed_items(facility, today, feed_type="activities", user=staff_user)
            # Die Relationen werden ausdruecklich materialisiert (nicht nur
            # beruehrt): jede Nachlade-Query eines fehlenden
            # ``select_related``-/``prefetch_related``-Feldes entsteht damit
            # INNERHALB des Messfensters.
            return [
                (item["object"].actor.pk, item["object"].target_type.model, item["object"].target.pk) for item in items
            ]

        _activity(facility, staff_user, client_identified, when)
        touch()
        small = _count_queries(touch)

        for _ in range(4):
            _activity(facility, staff_user, client_identified, when)
        assert len(touch()) == 5
        large = _count_queries(touch)

        assert large == small


# ============================================================================
# Vorschau-Aufbau — Schleifen duerfen nicht abbrechen
# ============================================================================


@pytest.mark.django_db
class TestBuildEventPreviewFieldsSkipsHiddenFields:
    """``_build_event_preview_fields``: gesperrtes Feld ueberspringen, nicht abbrechen."""

    def test_hidden_field_does_not_end_the_field_loop(self, facility, assistant_user, client_identified):
        """Pin: ``_build_event_preview_fields__mutmut_13`` — ``continue`` wird zu
        ``break`` im Zweig „Feld fuer User nicht sichtbar".

        Drei Felder in Blockreihenfolge: Alpha (sichtbar), Beta (ELEVATED,
        fuer die Rolle ``assistant`` gesperrt), Gamma (sichtbar). Das Original
        liefert ``["Alpha", "Gamma"]``; der Mutant bricht beim gesperrten Feld
        ab und liefert nur ``["Alpha"]`` — die dokumentierten Felder NACH
        einem gesperrten Feld verschwinden aus der Anzeige.

        Die beiden bestehenden Sichtbarkeitstests
        (``test_feed_enrichment.py``) haengen nur EIN Feld an den
        Dokumentationstyp; dort sind ``continue`` und ``break`` wirkungsgleich.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Pin-Sichtbarkeit")
        ft_alpha = FieldTemplate.objects.create(
            facility=facility, name="Alpha", field_type=FieldTemplate.FieldType.TEXT
        )
        ft_beta = FieldTemplate.objects.create(
            facility=facility,
            name="Beta",
            field_type=FieldTemplate.FieldType.TEXT,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        ft_gamma = FieldTemplate.objects.create(
            facility=facility, name="Gamma", field_type=FieldTemplate.FieldType.TEXT
        )
        for order, ft in enumerate((ft_alpha, ft_beta, ft_gamma)):
            DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=order)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={ft_alpha.slug: "a", ft_beta.slug: "b", ft_gamma.slug: "c"},
            created_by=assistant_user,
        )

        preview, expanded = _build_event_preview_fields(event, [ft_alpha, ft_beta, ft_gamma], assistant_user)

        assert [entry["label"] for entry in expanded] == ["Alpha", "Gamma"]
        assert [entry["label"] for entry in preview] == ["Alpha", "Gamma"]


@pytest.mark.django_db
class TestEnrichEventsWithPreviewVisitsEveryItem:
    """``enrich_events_with_preview``: Nicht-Event-Eintraege ueberspringen, nicht abbrechen."""

    def test_leading_workitem_does_not_end_the_enrichment_loop(self, facility, staff_user, client_identified):
        """Pin: ``enrich_events_with_preview__mutmut_21`` — ``continue`` wird zu
        ``break`` im Zweig „Eintrag ist kein Ereignis/Hausverbot".

        Im gemischten Feed (``feed_type="all"``) steht regelmaessig eine
        Aufgabe oder Aktivitaet VOR einem Ereignis. Das Original reichert das
        Ereignis trotzdem an; der Mutant verlaesst die Schleife beim ersten
        Nicht-Ereignis, ``event.preview_fields`` existiert dann gar nicht
        (``AttributeError``) — im Zeitstrom faellt die Vorschau still weg.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Pin-NachAufgabe")
        ft_note = FieldTemplate.objects.create(facility=facility, name="Notiz", field_type=FieldTemplate.FieldType.TEXT)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_note, sort_order=0)
        workitem = _workitem(facility, client_identified, staff_user, "Vorne stehende Aufgabe")
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={ft_note.slug: "Wert"},
            created_by=staff_user,
        )
        feed_items = [
            {"type": "workitem", "occurred_at": workitem.created_at, "object": workitem},
            {"type": "event", "occurred_at": event.occurred_at, "object": event},
        ]

        enrich_events_with_preview(feed_items, staff_user)

        expected = [{"label": "Notiz", "value": "Wert", "is_textarea": False}]
        assert event.preview_fields == expected
        assert event.expanded_fields == expected


# ============================================================================
# _format_preview_value — reine Formatierung (keine Datenbank noetig)
# ============================================================================


def _unsaved_field_template(field_type, options_json=None):
    """Echte, nicht gespeicherte ``FieldTemplate`` — ``_format_preview_value``
    liest nur ``field_type`` und ``options_json``."""
    return FieldTemplate(field_type=field_type, options_json=options_json if options_json is not None else [])


class TestFormatPreviewValueMultiSelectLabels:
    """``_format_preview_value``: Mehrfachauswahl, Label-Map mit Roh-Fallback."""

    def test_unknown_entry_falls_back_to_the_raw_value(self):
        """Pin: ``_format_preview_value__mutmut_30`` (``label_map.get(v, None)``),
        ``__mutmut_32`` (Default gestrichen) und ``__mutmut_33``
        (``label_map.get(v, str(None))``).

        Original: ``"Alpha, weg"``. Mutant 30 und 32 geben fuer den
        unbekannten Slug ``None`` in den ``", ".join`` (``TypeError``), Mutant
        33 schreibt den Text ``"Alpha, None"`` in die Vorschau.
        """
        ft = _unsaved_field_template(
            FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[{"slug": "a", "label": "Alpha"}],
        )

        assert _format_preview_value(["a", "weg"], ft) == "Alpha, weg"

    def test_option_keys_are_looked_up_unstringified(self):
        """Pin: ``_format_preview_value__mutmut_31`` — ``label_map.get(str(v))``
        (Default entfaellt, der Schluessel wird zusaetzlich gestringt).

        ``option_label_map`` uebernimmt den Slug unveraendert, hier also die
        Zahl ``1``. Original: ``"Eins"``. Der Mutant schlaegt mit ``"1"`` nach,
        findet nichts und gibt ``None`` in den ``join`` (``TypeError``); er
        stirbt zusaetzlich schon am Test darueber.
        """
        ft = _unsaved_field_template(
            FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[{"slug": 1, "label": "Eins"}],
        )

        assert _format_preview_value([1], ft) == "Eins"


class TestFormatPreviewValueFileCount:
    """``_format_preview_value``: Datei-Marker-Zaehler (``ngettext``)."""

    def test_two_files_render_the_exact_plural_form(self):
        """Pin: ``_format_preview_value__mutmut_47`` (``msgid_plural`` wird
        ``None``), ``__mutmut_55`` (``"XX[%(n)d Dateien]XX"``), ``__mutmut_56``
        (``"[%(n)d dateien]"``) und ``__mutmut_57`` (``"[%(N)D DATEIEN]"``).

        Erste Zusicherung im Null-Katalog (``translation.override(None)``):
        ``ngettext`` gibt die Plural-``msgid`` roh zurueck. Original:
        ``"[2 Dateien]"``. Mutant 47 gibt ``None`` zurueck und scheitert am
        ``%``-Formatieren (``TypeError``), Mutant 55/56/57 liefern ihren
        veraenderten Text (``"XX[2 Dateien]XX"``, ``"[2 dateien]"``,
        ``"[%(N)D DATEIEN]"`` — letzterer formatiert den Platzhalter nicht
        einmal mehr).

        Zweite Zusicherung mit aktivem deutschen Katalog: dieselbe Ausgabe,
        weil die ``de``-Uebersetzung wortgleich ist — der veraenderte
        ``msgid`` faellt dort aus dem Katalog und wird roh durchgereicht.
        """
        ft = _unsaved_field_template(FieldTemplate.FieldType.TEXT)
        value = {"__files__": True, "entries": [{"id": 1}, {"id": 2}]}

        with translation.override(None):
            assert _format_preview_value(value, ft) == "[2 Dateien]"
        with translation.override("de"):
            assert _format_preview_value(value, ft) == "[2 Dateien]"


class TestFormatPreviewValueEncryptedFallback:
    """``_format_preview_value``: ``safe_decrypt(value, default="[verschlüsselt]")``."""

    def test_plain_dict_is_passed_through_unchanged(self):
        """Pin: ``_format_preview_value__mutmut_60`` — ``safe_decrypt(None, ...)``.

        Ein Dict ohne Verschluesselungs-Marker reicht ``safe_decrypt``
        unveraendert durch. Original: ``{"a": 1}``. Der Mutant reicht ``None``
        hinein und liefert ``None`` — die Vorschau verloere den Wert.
        """
        ft = _unsaved_field_template(FieldTemplate.FieldType.TEXT)

        assert _format_preview_value({"a": 1}, ft) == {"a": 1}

    def test_corrupt_token_renders_the_exact_placeholder(self, _encryption_key):
        """Pin: ``_format_preview_value__mutmut_61`` (``default=None``),
        ``__mutmut_64`` (``"XX[verschlüsselt]XX"``) und ``__mutmut_65``
        (``"[VERSCHLÜSSELT]"``).

        Ein kaputtes Fernet-Token laeuft in den ``EncryptionError``-Zweig von
        ``safe_decrypt`` und wird zum Platzhalter. Original: exakt
        ``"[verschlüsselt]"``. Mutant 61 setzt ``None`` in die Vorschau,
        Mutant 64/65 ihre veraenderten Texte. Der Vergleich ist bewusst
        exakt — ein Teilstring-Check wuerde 64 ueberleben lassen.
        """
        ft = _unsaved_field_template(FieldTemplate.FieldType.TEXT)
        value = {"__encrypted__": True, "value": "gAAAAABkaputt"}

        assert _format_preview_value(value, ft) == "[verschlüsselt]"

    def test_placeholder_is_passed_explicitly_to_safe_decrypt(self, monkeypatch):
        """Pin: ``_format_preview_value__mutmut_63`` — ``safe_decrypt(value, )``,
        das ``default``-Argument entfaellt an der Aufrufstelle.

        Solange ``safe_decrypt`` denselben Default in der Signatur traegt, ist
        der Mutant verhaltensgleich; beobachtbar ist er nur am Aufruf selbst.
        Genau das pinnt dieser Test — und er haelt zugleich die Mutanten 60,
        61, 64 und 65 fest: die Aufrufliste weicht bei jedem von ihnen ab
        (``value=None`` bzw. ``default=None``/veraenderter Text). Der Ersatz
        traegt einen anderen Default als das Original, sodass ein Aufruf ohne
        ``default=`` sofort auffaellt.
        """
        calls = []

        def _record(value, default="<ohne default aufgerufen>"):
            calls.append((value, default))
            return "PLATZHALTER-SENTINEL"

        monkeypatch.setattr(feed_module, "safe_decrypt", _record)
        ft = _unsaved_field_template(FieldTemplate.FieldType.TEXT)
        value = {"__encrypted__": True, "value": "egal"}

        assert _format_preview_value(value, ft) == "PLATZHALTER-SENTINEL"
        assert calls == [(value, "[verschlüsselt]")]


# ============================================================================
# DST-Semantik (Refs #2057): aktive Zone und UTC-Achse
# ============================================================================


class TestIsAmbiguousIgnoresIncomingFold:
    """``_is_ambiguous``: der Vergleich setzt BEIDE ``fold``-Werte selbst."""

    def test_ambiguity_does_not_depend_on_the_incoming_fold(self):
        """Pin: ``_is_ambiguous__mutmut_4`` — ``replace(tzinfo=tzinfo, fold=0)``
        wird zu ``replace(tzinfo=tzinfo)``, ``fold`` bleibt also stehen.

        Traegt die Eingabe bereits ``fold=1``, vergleicht der Mutant zweimal
        dieselbe Okkurrenz: ``False`` statt ``True``. Am Rueckfalltag
        (2026-10-25, Europe/Berlin) faellt damit die Erkennung der doppelten
        Stunde aus, und ``_resolve_window_end`` waehlt fuer ein Schichtende in
        dieser Stunde die erste statt der zweiten Okkurrenz.
        """
        assert _is_ambiguous(dt.datetime(2026, 10, 25, 2, 30, fold=1), BERLIN) is True
        assert _is_ambiguous(dt.datetime(2026, 6, 14, 2, 30, fold=1), BERLIN) is False


class TestGapDetectionUsesTheActiveTimezone:
    """``_is_imaginary``/``_resolve_window_end``: aktive Zone statt Prozess-Zone."""

    def test_full_day_in_an_overridden_zone_spans_its_own_fall_back(self):
        """Pin: ``_is_imaginary__mutmut_2`` (``naive_dt.replace(tzinfo=None)``)
        und ``_is_imaginary__mutmut_3`` (``.astimezone(None)`` statt
        ``.astimezone(tzinfo)``).

        Aktive Zone ``America/New_York``, Prozess-Zone Europe/Berlin: der
        4. Rueckfalltag der US-Zone (2026-11-01) hat 25 Stunden. Beide
        Mutanten rechnen die Rundreise ueber die Prozess-Zone und halten damit
        JEDE Wanduhrzeit fuer eine Luecke; ``_resolve_window_end`` nimmt dann
        den Zweig „Dauer-ab-Start" und liefert ein Fenster von 24 h - 1 µs.
        Ergebnis: die im 25. Stundenlauf dokumentierten Ereignisse fielen aus
        Feed, Uebergabe und Strichliste.
        """
        with _process_timezone("Europe/Berlin"), timezone.override(NEW_YORK):
            start_dt, end_dt = get_time_range(dt.date(2026, 11, 1))

        real_duration = end_dt.astimezone(UTC) - start_dt.astimezone(UTC)
        assert real_duration == dt.timedelta(hours=25) - MICROSECOND

    def test_gap_lookup_survives_the_end_of_the_datetime_range(self):
        """Pin: ``_is_imaginary__mutmut_4`` — ``aware.astimezone(None)`` statt
        ``aware.astimezone(UTC)`` auf der ersten Etappe der Rundreise.

        Die Rundreise laeuft ausdruecklich ueber UTC. Der Mutant nimmt
        stattdessen die Prozess-Zone (Europe/Berlin, +01:00) und laeuft an
        ``datetime.max`` ueber das Jahr 9999 hinaus — das Original antwortet
        dort ruhig mit ``False``.
        """
        with _process_timezone("Europe/Berlin"):
            assert _is_imaginary(dt.datetime(9999, 12, 31, 23, 59, 59, 999999), UTC) is False

    def test_shift_end_in_a_gap_is_resolved_in_the_active_zone(self, facility):
        """Pin: ``_resolve_window_end__mutmut_3`` (``_is_imaginary(naive_end, None)``)
        und ``_resolve_window_end__mutmut_6`` (Rueckgabe ``.astimezone(None)``).

        Schichtfilter 00:00-02:30 am 2026-03-08 in ``America/New_York`` — die
        Wanduhrzeit 02:30 gibt es dort an diesem Tag nicht. Original:
        Dauer-ab-Start, also 07:30 UTC = ``2026-03-08T03:30:00-04:00``.

        Mutant 3 erkennt die Luecke nicht (er prueft gegen die Prozess-Zone),
        haelt die Zeit fuer mehrdeutig und nimmt ``fold=1`` — 06:30 UTC, eine
        Stunde zu frueh. Mutant 6 trifft denselben Instant, gibt ihn aber in
        der Prozess-Zone zurueck (``+01:00`` statt ``-04:00``); ``case.handover``
        rendert ``end_dt`` direkt per ``strftime`` und zeigte damit eine
        Berliner Uhrzeit.
        """
        time_filter = TimeFilter(
            facility=facility,
            label="Pin-Nacht",
            start_time=dt.time(0, 0),
            end_time=dt.time(2, 30),
        )

        with _process_timezone("Europe/Berlin"), timezone.override(NEW_YORK):
            _start_dt, end_dt = get_time_range(dt.date(2026, 3, 8), time_filter)

        assert end_dt.astimezone(UTC) == dt.datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
        assert end_dt.utcoffset() == dt.timedelta(hours=-4)

    def test_duration_from_start_is_added_on_the_utc_axis(self):
        """Pin: ``_resolve_window_end__mutmut_8`` — ``start_dt.astimezone(None)``
        statt ``start_dt.astimezone(UTC)`` vor der Addition.

        Der Kommentar an der Stelle ist ausdruecklich: addiert wird auf der
        UTC-Achse. Der Mutant rechnet stattdessen ueber die Prozess-Zone
        (Europe/Berlin, +01:00) — am Rand des ``datetime``-Wertebereichs
        laeuft diese Umrechnung ueber das Jahr 9999 hinaus, waehrend das
        Original ruhig ``9999-12-31T23:00:00+00:00`` liefert.

        Aktive Zone ist ``America/New_York``, damit ``naive_end`` (die
        NY-Luecke am 2026-03-08) ueberhaupt in den Dauer-ab-Start-Zweig faellt.
        """
        with _process_timezone("Europe/Berlin"), timezone.override(NEW_YORK):
            resolved = _resolve_window_end(
                dt.datetime(2026, 3, 8, 2, 0),
                dt.datetime(2026, 3, 8, 2, 30),
                dt.datetime(9999, 12, 31, 22, 30, tzinfo=UTC),
            )

        assert resolved.astimezone(UTC) == dt.datetime(9999, 12, 31, 23, 0, tzinfo=UTC)


class TestShiftWindowCoversComparesOverUtc:
    """``shift_window_covers``: alle drei Instants werden nach UTC gedreht."""

    def test_comparison_survives_the_end_of_the_datetime_range(self):
        """Pin: ``shift_window_covers__mutmut_2`` (``moment.astimezone(None)``),
        ``__mutmut_3`` (``start_dt.astimezone(None)``) und ``__mutmut_6``
        (``end_dt.astimezone(None)``).

        Die Funktion sichert ausdruecklich zu, ueber UTC zu vergleichen — nicht
        ueber irgendeine lokale Zone. Am oberen Rand des Wertebereichs ist der
        Unterschied beobachtbar: das Original antwortet ``True``, jeder der
        drei Mutanten rechnet den jeweiligen Instant in die Prozess-Zone
        (Europe/Berlin, +01:00) und laeuft dabei ueber das Jahr 9999 hinaus.
        """
        start_dt = dt.datetime(9999, 12, 31, 23, 0, tzinfo=UTC)
        end_dt = dt.datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
        moment = dt.datetime(9999, 12, 31, 23, 30, tzinfo=UTC)

        with _process_timezone("Europe/Berlin"):
            assert shift_window_covers(start_dt, end_dt, moment) is True
