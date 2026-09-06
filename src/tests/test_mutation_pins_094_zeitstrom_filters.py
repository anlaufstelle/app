"""Pin-Tests zum Meilenstein release-0.94 — triagierte Survivors in ``core.services.zeitstrom_filters``.

Quelle: Survivor-Triage des Moduls ``src/core/services/zeitstrom_filters.py``
(Mutantenbaum ``mutants/src/core/services/zeitstrom_filters.py``). Vorgabe ist
die Survivor-Triage release-0.94 (Ledger des Meilensteins) (27 triagierte
Mutanten, Herkunft: Delta-Lauf 0.92). Schwerpunkte:

* **Die explizite Schichtwahl der Uebergabe.** ``?view=uebergabe&time_filter=<uuid>``
  hat Vorrang vor dem automatischen Scan der zuletzt abgeschlossenen Schicht.
  Der einzige bestehende Test dazu (``test_zeitstrom.py::TestZeitstromUebergabeMode``)
  arbeitet mit EINEM TimeFilter, sodass Auswahl und Auto-Scan dasselbe Label
  liefern — jede Mutation des Parameterzugriffs ueberlebt das. Die Pins hier
  legen drei Schichten an und waehlen einen Zeitpunkt, an dem der Auto-Scan
  eine ANDERE Schicht UND ein anderes Schichtdatum liefert.
* **Die Sentinel-Semantik von ``all``.** Exakt der kleingeschriebene String
  schaltet die Schichtauswahl ab — in der Uebergabe (Zeile 92/96) wie im
  Normalpfad (Zeile 155, wo er den Fallback auf die laufende Schicht
  unterdrueckt). Gepinnt wird jeweils der exakte Wert samt seiner
  Gross-/Kleinschreibung und der ``XX...XX``-Sentinelform von mutmut.
* **Der Auto-Scan selbst.** Die Grenze am Schichtende (``>`` statt ``>=``),
  das Ueberspringen (``continue``) statt Abbrechen (``break``) eines noch
  laufenden Fensters und der Tie-Break bei gleichem Fensterende.
* **Die UTC-Normalisierung (Refs #2057).** Der Vergleich der Fenster laeuft
  ausdruecklich ueber UTC und nicht ueber die Prozess-Zone; die Pins trennen
  beides ueber den Rand des ``datetime``-Wertebereichs, an dem eine Umrechnung
  in die Prozess-Zone ueberlaeuft (Muster wie ``test_mutation_pins_094_feed.py``).
* **Die Reihenfolge der Schicht-Tabs** (``order_by("sort_order", "pk")``) und
  die Argumente, mit denen ``current_tally_context`` aufgerufen wird
  (Mandant, vorgeladene Filter, Zeitpunkt).
* **``zeitstrom_filter_state``** — der Helfer hatte bisher keinen einzigen
  direkten Unit-Test: Default ``time_filter_id="all"`` und der
  ``view``-Durchreicher sind hier erstmals festgeschrieben.

Nicht gepinnt (begruendet)
==========================

Keiner der 27 Mutanten ist ausgenommen; die Begruendungen der beiden als
„Aequivalenz widerlegt" markierten Eintraege (``_handover_filter__mutmut_23``
und ``__mutmut_37``) stehen bei
:class:`TestHandoverFilterUtcNormalisierung`.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); KEINE Imports
aus ``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py`` bzw.
werden als echte Model-Objekte angelegt (keine Fakes). Jeder Test nennt seine
Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.
"""

import os
import time as time_module
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from uuid import UUID

import pytest
from django.utils import timezone

from core.models import TimeFilter
from core.services import zeitstrom_filters
from core.services.events.tally import current_tally_context
from core.services.zeitstrom_filters import (
    _handover_filter,
    resolve_zeitstrom_state,
    zeitstrom_filter_state,
)

# Referenztag ohne DST-Wechsel (Sonntag, CEST). Der Auto-Scan der Uebergabe
# sieht immer die drei Schichtdaten 13./14./15.06.
TAG = date(2025, 6, 15)


def _berlin(hour, minute=0):
    """Aware Zeitpunkt am Referenztag in der aktiven Zone (Europe/Berlin)."""
    return timezone.make_aware(datetime(2025, 6, 15, hour, minute))


def _shift(facility, label, start, end, sort_order, **kwargs):
    """Echte Schicht — ``time``-Objekte, damit ``get_time_range`` direkt rechnet."""
    return TimeFilter.objects.create(
        facility=facility,
        label=label,
        start_time=start,
        end_time=end,
        sort_order=sort_order,
        **kwargs,
    )


@contextmanager
def _process_timezone(name):
    """Prozess-Zeitzone (``TZ``/``time.tzset``) fuer die Dauer des Blocks fixieren.

    ``datetime.astimezone(None)`` — das Ziel zweier Mutanten — rechnet in genau
    dieser Zone, ``astimezone(UTC)`` nicht. Der Test darf sich nicht auf die
    vom Test-Runner geerbte Zone verlassen (im Container ist das UTC, dort
    waere der Unterschied unsichtbar).
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


@pytest.fixture
def tf_frueh(facility):
    return _shift(facility, "Frühdienst", time(8, 0), time(16, 0), 0)


@pytest.fixture
def tf_spaet(facility):
    return _shift(facility, "Spätdienst", time(16, 0), time(22, 0), 1)


@pytest.fixture
def tf_nacht(facility):
    return _shift(facility, "Nachtdienst", time(22, 0), time(8, 0), 2)


# ============================================================================
# core.services.zeitstrom_filters._handover_filter (15 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestHandoverFilterExpliziteSchicht:
    """``_handover_filter``: die gewaehlte Schicht schlaegt den Auto-Scan."""

    def test_explicit_shift_wins_over_the_automatic_scan(self, facility, tf_frueh, tf_spaet, tf_nacht):
        """Pin: ``_handover_filter__mutmut_1`` (``raw = params.get("time_filter")``
        → ``raw = None``), ``__mutmut_2`` (``params.get(None)``), ``__mutmut_3``
        (``params.get("XXtime_filterXX")``), ``__mutmut_4``
        (``params.get("TIME_FILTER")``) und ``__mutmut_6``
        (``if raw and raw != "all"`` → ``raw == "all"``).

        Alle fuenf Mutanten verlieren den ``time_filter``-Parameter der
        Uebergabe und fallen in den Auto-Scan der zuletzt abgeschlossenen
        Schicht. Um 09:00 des 15.06. ist das der Nachtdienst mit Schichtdatum
        14.06.: der Mutant antwortet ``(Nachtdienst, 2025-06-14)``, das
        Original ``(Spätdienst, 2025-06-15)`` — beide Felder weichen ab.
        """
        state = resolve_zeitstrom_state(
            {"view": "uebergabe", "time_filter": str(tf_spaet.pk), "date": "2025-06-15"},
            facility,
            today=TAG,
            now=_berlin(9),
        )

        assert state.time_filter == tf_spaet
        assert state.target_date == TAG
        assert state.time_filter_id == str(tf_spaet.pk)

    @pytest.mark.parametrize(
        "raw",
        ["all", "ALL", "XXallXX", "keine-uuid", "00000000-0000-0000-0000-000000000000"],
    )
    def test_no_shift_for_any_non_matching_raw_value(self, facility, tf_frueh, tf_spaet, tf_nacht, raw):
        """Pin: ``_handover_filter__mutmut_7`` (``raw != "XXallXX"``, Zeile 92),
        ``__mutmut_8`` (``raw != "ALL"``, Zeile 92), ``__mutmut_19``
        (``raw == "XXallXX"``, Zeile 96), ``__mutmut_20`` (``raw == "ALL"``,
        Zeile 96) und zusaetzlich ``__mutmut_17`` fuer den Fall ``all``.

        Das Original liefert fuer JEDEN dieser Rohwerte ``(None, 2025-06-15)``:
        ``all`` faengt Zeile 96 ab, alles andere laeuft durch die
        UUID-Aufloesung ins Leere. Jeder Mutant faellt fuer genau einen der
        Werte stattdessen in den Auto-Scan und antwortet ``(Nachtdienst,
        2025-06-14)`` — mutmut_7 fuer ``XXallXX``, mutmut_8 fuer ``ALL``,
        mutmut_17/19/20 fuer ``all``.
        """
        state = resolve_zeitstrom_state(
            {"view": "uebergabe", "time_filter": raw, "date": "2025-06-15"},
            facility,
            today=TAG,
            now=_berlin(9),
        )

        assert state.time_filter is None
        assert state.target_date == TAG
        assert state.time_filter_id == "all"

    def test_past_date_keeps_the_requested_day_without_a_shift(self, facility, tf_frueh, tf_spaet, tf_nacht):
        """Pin: ``_handover_filter__mutmut_17`` (``raw == "all" or target_date
        != today`` → ``and``).

        Eine Uebergabe fuer ein vergangenes Datum ohne ``time_filter``-Parameter
        bricht im Original vor dem Auto-Scan ab und behaelt den angefragten Tag:
        ``(None, 2025-06-10)``. Der Mutant verliert diese Abbruchbedingung,
        scannt und antwortet ``(Nachtdienst, 2025-06-14)``.
        """
        state = resolve_zeitstrom_state(
            {"view": "uebergabe", "date": "2025-06-10"},
            facility,
            today=TAG,
            now=_berlin(9),
        )

        assert state.time_filter is None
        assert state.target_date == date(2025, 6, 10)


@pytest.mark.django_db
class TestHandoverFilterAutoScan:
    """``_handover_filter``: der Scan der zuletzt abgeschlossenen Schicht."""

    def test_a_shift_ending_exactly_now_is_the_handover_shift(self, facility, tf_frueh, tf_spaet, tf_nacht):
        """Pin: ``_handover_filter__mutmut_38`` (``window_end_utc > now_utc``
        → ``>=``).

        Punkt 16:00 endet der Fruehdienst. Das Original wertet ihn als
        abgeschlossen und zeigt ihn als Uebergabe-Schicht: ``(Frühdienst,
        2025-06-15)``. Der Mutant ueberspringt das gerade endende Fenster und
        faellt auf die Nachtschicht des Vortags zurueck: ``(Nachtdienst,
        2025-06-14)``.
        """
        state = resolve_zeitstrom_state(
            {"view": "uebergabe", "date": "2025-06-15"},
            facility,
            today=TAG,
            now=_berlin(16),
        )

        assert state.time_filter == tf_frueh
        assert state.target_date == TAG

    def test_a_running_window_only_skips_its_own_entry(self, facility):
        """Pin: ``_handover_filter__mutmut_39`` (``continue`` → ``break``).

        Die Sortierung der Schichten ist frei waehlbar: hier steht der
        Nachtdienst (Fenster 15.06. 22:00 → 16.06. 08:00, um 17:00 also noch in
        der Zukunft) VOR dem bereits abgeschlossenen Fruehdienst. Das Original
        ueberspringt nur das laufende Fenster und findet den Fruehdienst des
        heutigen Tages: ``(Frühdienst, 2025-06-15)``. Der Mutant bricht die
        Schleife ueber die Schichten desselben Schichtdatums ab und behaelt den
        Stand des Vortags: ``(Nachtdienst, 2025-06-14)``.
        """
        nacht = _shift(facility, "Nachtdienst", time(22, 0), time(8, 0), 0)
        frueh = _shift(facility, "Frühdienst", time(8, 0), time(16, 0), 1)

        state = resolve_zeitstrom_state(
            {"view": "uebergabe", "date": "2025-06-15"},
            facility,
            today=TAG,
            now=_berlin(17),
        )

        assert state.time_filter == frueh
        assert state.target_date == TAG
        assert state.time_filter != nacht

    def test_equal_window_ends_keep_the_first_checked_shift(self, facility):
        """Pin: ``_handover_filter__mutmut_42`` (``window_end_utc >
        latest_completed[0]`` → ``>=``).

        Zwei aktive Schichten enden zur selben Zeit (16:00). Das Original
        behaelt bei Gleichstand den ZUERST geprueften Eintrag, also den
        Fruehdienst (``sort_order=0``); der Mutant ersetzt ihn durch den
        Zwischendienst. Antwort des Mutanten: ``(Zwischendienst, 2025-06-15)``.
        """
        frueh = _shift(facility, "Frühdienst", time(8, 0), time(16, 0), 0)
        zwischen = _shift(facility, "Zwischendienst", time(12, 0), time(16, 0), 1)

        state = resolve_zeitstrom_state(
            {"view": "uebergabe", "date": "2025-06-15"},
            facility,
            today=TAG,
            now=_berlin(17),
        )

        assert state.time_filter == frueh
        assert state.target_date == TAG
        assert state.time_filter != zwischen


@pytest.mark.django_db
class TestHandoverFilterUtcNormalisierung:
    """``_handover_filter``: der Fenstervergleich laeuft ueber UTC (Refs #2057)."""

    def test_window_comparison_uses_utc_not_the_process_timezone(self, facility):
        """Pin: ``_handover_filter__mutmut_23`` (``now.astimezone(UTC)`` →
        ``now.astimezone(None)``) und ``__mutmut_37``
        (``window_end.astimezone(UTC)`` → ``window_end.astimezone(None)``).

        Beide Mutanten sind im Normalbetrieb instant-erhaltend (die Triage hat
        das als Aequivalenz-Idee notiert und wieder widerlegt): ``astimezone(None)``
        rechnet in die PROZESS-Zone. Am Rand des ``datetime``-Wertebereichs
        laeuft genau diese Umrechnung ueber — Prozess-Zone Europe/Berlin
        (+01:00), aktive Zone UTC. Das Original antwortet ruhig
        ``(Spätdienst, 9999-12-31)``; mutmut_23 (Zeile 108) und mutmut_37
        (Zeile 113) werfen ``OverflowError: date value out of range``.
        """
        spaet = _shift(facility, "Spätdienst", time(8, 0), time(23, 30), 0)
        letzter_tag = date(9999, 12, 31)

        with _process_timezone("Europe/Berlin"), timezone.override("UTC"):
            selected, shift_date = _handover_filter(
                {},
                (spaet,),
                letzter_tag,
                letzter_tag,
                datetime(9999, 12, 31, 23, 45, tzinfo=UTC),
            )

        assert selected == spaet
        assert shift_date == letzter_tag


# ============================================================================
# core.services.zeitstrom_filters.resolve_zeitstrom_state (7 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestResolveZeitstromStateSchichtreihenfolge:
    """``resolve_zeitstrom_state``: Reihenfolge und Herkunft der Schichtliste."""

    def test_shift_tabs_are_ordered_by_sort_order_then_pk(self, facility):
        """Pin: ``resolve_zeitstrom_state__mutmut_9``
        (``order_by("sort_order", "pk")`` → ``order_by("pk")``) und
        ``__mutmut_10`` (→ ``order_by("sort_order")``).

        Die drei Schichten sind so angelegt, dass pk-Reihenfolge,
        Anlage-Reihenfolge und ``sort_order``-Reihenfolge auseinanderfallen:
        die ``sort_order=0``-Schicht traegt die groesste pk, und die beiden
        Schichten mit gleichem ``sort_order`` werden in umgekehrter
        pk-Reihenfolge angelegt. Das Original liefert
        ``[Frühdienst, Spätdienst, Nachtdienst]``; mutmut_9 sortiert rein nach
        pk (``[Spätdienst, Nachtdienst, Frühdienst]``), mutmut_10 verliert den
        deterministischen Tie-Break: ohne ``pk`` ist die Reihenfolge des
        gleichrangigen Paars nicht garantiert (im geprueften PostgreSQL-Lauf
        kam die Anlage-Reihenfolge ``[Frühdienst, Nachtdienst, Spätdienst]``).

        Die Reihenfolge ist doppelt wirksam: sie bestimmt die Schicht-Tabs im
        Template und den ersten Treffer bei Gleichstand im Uebergabe-Scan.
        """
        frueh = _shift(
            facility,
            "Frühdienst",
            time(8, 0),
            time(16, 0),
            0,
            id=UUID("ffffffff-ffff-4fff-8fff-fffffffffff1"),
        )
        nacht = _shift(
            facility,
            "Nachtdienst",
            time(22, 0),
            time(8, 0),
            5,
            id=UUID("00000000-0000-4000-8000-0000000000b2"),
        )
        spaet = _shift(
            facility,
            "Spätdienst",
            time(16, 0),
            time(22, 0),
            5,
            id=UUID("00000000-0000-4000-8000-0000000000a1"),
        )

        state = resolve_zeitstrom_state({}, facility, today=TAG, now=_berlin(10))

        assert [item.pk for item in state.time_filters] == [frueh.pk, spaet.pk, nacht.pk]

    def test_live_context_is_built_from_facility_filters_and_now(self, facility, monkeypatch):
        """Pin: ``resolve_zeitstrom_state__mutmut_20`` (``current_tally_context(
        facility, ...)`` → ``current_tally_context(None, ...)``), ``__mutmut_21``
        (``time_filters=filters`` → ``time_filters=None``) und ``__mutmut_24``
        (``time_filters``-Argument entfaellt).

        Der Live-Kontext wird mit dem MANDANTEN, den bereits geladenen
        Schichten und dem aufgeloesten Zeitpunkt gebaut — die Weitergabe der
        Einrichtung ist die Mandantenklammer, die Weitergabe der Schichten
        spart die identische zweite Abfrage. Aufgezeichnet wird der echte
        Aufruf: mutmut_20 uebergibt ``(None,)`` statt ``(facility,)``,
        mutmut_21 ``time_filters=None`` und mutmut_24 gar kein
        ``time_filters``-Argument.
        """
        frueh = _shift(facility, "Frühdienst", time(8, 0), time(16, 0), 0)
        aufrufe = []

        def spy(*args, **kwargs):
            aufrufe.append((args, kwargs))
            return current_tally_context(*args, **kwargs)

        monkeypatch.setattr(zeitstrom_filters, "current_tally_context", spy)
        now = _berlin(10)

        resolve_zeitstrom_state({}, facility, today=TAG, now=now)

        assert aufrufe == [((facility,), {"time_filters": (frueh,), "now": now})]


@pytest.mark.django_db
class TestResolveZeitstromStateAlleSchichten:
    """``resolve_zeitstrom_state``: ``time_filter=all`` unterdrueckt die Live-Schicht."""

    def test_all_sentinel_suppresses_the_live_shift_fallback(self, facility):
        """Pin: ``resolve_zeitstrom_state__mutmut_81``
        (``raw_filter != "all"`` → ``!= "XXallXX"``, Zeile 155) und
        ``__mutmut_82`` (→ ``!= "ALL"``).

        Waehlt die Nutzerin ausdruecklich „Alle Schichten", darf der Fallback
        auf die laufende Schicht nicht greifen. Um 10:00 laeuft der
        Fruehdienst; das Original antwortet trotzdem mit ``time_filter=None``
        und ``time_filter_id="all"``. Beide Mutanten erkennen den Sentinel
        nicht mehr und setzen still den Fruehdienst ein — ``state.time_filter``
        waere dann ``Frühdienst`` und ``state.params["time_filter"]`` dessen
        UUID.
        """
        _shift(facility, "Frühdienst", time(8, 0), time(16, 0), 0)

        state = resolve_zeitstrom_state(
            {"time_filter": "all", "date": "2025-06-15"},
            facility,
            today=TAG,
            now=_berlin(10),
        )

        assert state.time_filter is None
        assert state.time_filter_id == "all"
        assert state.target_date == TAG
        assert state.params == {
            "date": "2025-06-15",
            "time_filter": "all",
            "type": "",
            "doc_type": "",
            "view": "",
        }

    def test_only_the_lowercase_sentinel_switches_the_fallback_off(self, facility):
        """Pin: ``resolve_zeitstrom_state__mutmut_82``
        (``raw_filter != "all"`` → ``!= "ALL"``, Zeile 155) — Gegenprobe zum
        vorigen Test.

        ``ALL`` ist KEIN Sentinel, sondern ein unbekannter Wert: das Original
        loest ihn nicht auf und faellt deshalb auf die laufende Schicht zurueck
        (``Frühdienst``, Schichtdatum 2025-06-15). Der Mutant behandelt gerade
        diesen Wert als Sentinel und antwortet mit ``time_filter=None``.
        """
        frueh = _shift(facility, "Frühdienst", time(8, 0), time(16, 0), 0)

        state = resolve_zeitstrom_state(
            {"time_filter": "ALL", "date": "2025-06-15"},
            facility,
            today=TAG,
            now=_berlin(10),
        )

        assert state.time_filter == frueh
        assert state.time_filter_id == str(frueh.pk)
        assert state.target_date == TAG


# ============================================================================
# core.services.zeitstrom_filters.zeitstrom_filter_state (5 Mutanten)
# ============================================================================


class TestZeitstromFilterState:
    """``zeitstrom_filter_state``: der server-gerenderte Filter-Kontrakt.

    Der Helfer hatte bisher keinen direkten Unit-Test — der einzige
    Produktions-Caller (``core/views/events.py``) uebergibt ``time_filter_id``
    stets explizit und schickt nie eine nicht-leere Ansicht durch diesen Pfad.
    """

    def test_default_shift_id_is_the_lowercase_sentinel(self):
        """Pin: ``zeitstrom_filter_state__mutmut_1`` (Default ``time_filter_id
        = "all"`` → ``"XXallXX"``) und ``__mutmut_2`` (→ ``"ALL"``).

        Ohne ``time_filter_id`` steht im Kontrakt exakt ``"all"`` — genau der
        Wert, den ``resolve_zeitstrom_state`` als „Alle Schichten" erkennt.
        Die Mutanten liefern ``"XXallXX"`` bzw. ``"ALL"``; beides ist fuer den
        naechsten Request ein unbekannter Filterwert. Die Zusicherung pinnt
        zugleich alle fuenf Schluessel des Kontrakts.
        """
        assert zeitstrom_filter_state({}, target_date=TAG) == {
            "date": "2025-06-15",
            "time_filter": "all",
            "type": "",
            "doc_type": "",
            "view": "",
        }

    def test_view_is_passed_through_from_the_request(self):
        """Pin: ``zeitstrom_filter_state__mutmut_27`` (``params.get("view", "")``
        → ``params.get(None, "")``), ``__mutmut_31`` (→ ``params.get("XXviewXX",
        "")``) und ``__mutmut_32`` (→ ``params.get("VIEW", "")``).

        Alle drei Mutanten greifen einen Schluessel ab, den kein Request traegt;
        ``dict.get`` wirft dabei nicht, sondern liefert den Default. Das
        ``view``-Feld waere damit konstant ``""`` — ein aus der Uebergabe
        ausgeloester Strich-Request verloere seine Ansicht und faende sich in
        der Standardansicht wieder. Das Original reicht ``"uebergabe"`` durch.
        """
        assert zeitstrom_filter_state(
            {"view": "uebergabe", "type": "events", "doc_type": "42"},
            target_date=TAG,
            time_filter_id="all",
        ) == {
            "date": "2025-06-15",
            "time_filter": "all",
            "type": "events",
            "doc_type": "42",
            "view": "uebergabe",
        }
