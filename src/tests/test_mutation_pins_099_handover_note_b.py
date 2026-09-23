"""Pin-Tests Welle L (release-0.99) — Vollmutation #2174/#2193, Batch **handover_note-b**.

Modul ``core.services.case.handover_note`` (Schreibpfad der manuellen
Schichtnotiz, Refs #2042), Batch-Teil **b** von drei disjunkten Batches
(a/b/c teilen sich das Modul; ``handover_note-a``/``-c`` laufen parallel in
eigenen Worktrees und eigenen Pin-Dateien). Dieser Batch deckt 70 Survivors
in 13 Funktionen: ``next_shift`` (15), ``_build_block`` (9),
``is_shift_editable`` (8), ``assert_shift_identity`` (7), ``_fenster_zu`` (6),
``_window_is_open`` (4), ``_schicht_unbestimmbar`` (4), ``_identitaet_ok``
(4), ``can_read_notes`` (3), ``get_note`` (3), ``build_handover_note_context``
(3), ``_active_time_filters`` (2), ``editable_until`` (2). Liste:
``/work/wellen/welle-l-0.99/reports/m-scratch/batch-handover_note-b.json``.
Quelle der Mutanten-Rümpfe: Mutantenbaum
``mutants/src/core/services/case/handover_note.py``.

Triage-Ergebnis (Details siehe ``reports/m-pins-handover_note-b-report.md``):
alle 70 Mutanten sind entweder ``test`` (62, hier gepinnt) oder ``equivalent``
(8, s. je Testklasse) — kein ``bug``, kein ``skip``, nichts offen. Nachbesserung
(Zweitgutachter): ``x_get_note__mutmut_{4,5}`` waren zunaechst als
``equivalent`` (reine Query-Optimierung) eingestuft; ein Grep-Beleg zeigt,
dass ``_build_block`` ``note.author``/``note.updated_by`` tatsaechlich
liest — beide sind jetzt ``test`` (Prio 2, ``CaptureQueriesContext``,
Klasse ``TestGetNote``). Nur ``x_get_note__mutmut_6`` (``time_filter``,
nirgends gelesen) bleibt ``equivalent``.

Konvention: Zusicherungen vergleichen EXAKT (``==``), nie per Teilstring
(mutmut erzeugt zu jedem Text-Literal zusätzlich die Variante
``"XX<text>XX"``, die ein ``in``-Vergleich überleben würde; ``pytest.raises
(match=...)`` ist ebenfalls eine Teilstring-Suche und wird deshalb
NICHT verwendet). Mehrere Mutanten je Test sind vorgesehen — Argument-
Substitutions-Mutanten (``facility``/``time_filters``/``note`` → ``None``
oder Keyword entfernt) werden per Spy auf die aufgerufene Kollaborator-
Funktion gepinnt (deterministisch, unabhängig von Datenbank-Zufall) statt
über fragile End-zu-Ende-Zeitfenster-Rechnungen.
"""

from datetime import date, datetime, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.formats import date_format

import core.services.case.handover_note as handover_note
from core.models import ShiftHandoverNote, TimeFilter
from core.services.case.handover_note import (
    Shift,
    ShiftWindow,
    _active_time_filters,
    _build_block,
    _fenster_zu,
    _identitaet_ok,
    _window_is_open,
    assert_shift_date_supported,
    assert_shift_identity,
    build_handover_note_context,
    can_read_notes,
    editable_until,
    get_note,
    is_shift_editable,
    next_shift,
    save_note,
    shift_window,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _berlin(year, month, day, hour, minute=0):
    """Aware Berliner Wanduhrzeit — wie ``test_handover_note.py::_berlin``."""
    naive = datetime(year, month, day, hour, minute)
    return naive.replace(tzinfo=timezone.get_current_timezone())


def _spy(monkeypatch, owner, name):
    """Ersetzt ``owner.<name>`` durch eine aufzeichnende Huelle, die an die
    echte Funktion durchreicht (Verhalten unveraendert, Aufrufargumente
    landen in der zurueckgegebenen Liste)."""
    calls = []
    original = getattr(owner, name)

    def wrapper(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)
    return calls


@pytest.fixture
def tf(facility):
    """Ein einzelnes, aktives Zeitfenster — reicht fuer die meisten Pins."""
    return TimeFilter.objects.create(
        facility=facility, label="Frühdienst", start_time=time(6, 0), end_time=time(14, 0), sort_order=0
    )


class TestNextShift:
    """``next_shift`` — 15 Survivors: ``x_next_shift__mutmut_{12,13,14,15,16,
    17,19,32,39,43,44,45,46,47,48}``."""

    def test_ohne_kandidaten_faellt_naechste_schicht_auf_den_folgetag(self, facility, tf):
        """Toetet mutmut_{12,13,14,15,16,17} — alle sechs mutieren dieselbe
        Zeile (der fruehe Rueckfall bei leeren ``kandidaten``): ``Shift(None,
        None)``/fehlendes zweites Feld/``-`` statt ``+``/``days=None``/
        ``days=2`` weichen alle vom erwarteten ``Shift(datum+1, None)`` ab
        oder brechen mit ``TypeError`` beim Bau der Ruckgabe."""
        schicht = Shift(date(2026, 6, 15), tf)
        assert next_shift(facility, schicht, time_filters=[]) == Shift(date(2026, 6, 16), None)

    def test_ohne_treffer_im_suchfenster_faellt_naechste_schicht_auf_den_folgetag(self, monkeypatch, facility, tf):
        """Toetet mutmut_{43,44,45,46,47,48} — dieselben sechs Varianten wie
        oben, aber am ZWEITEN Vorkommen der Zeile (Rueckfall NACH der Suche,
        wenn kein Kandidat echt spaeter startet als die eigene Schicht).
        ``shift_window`` wird auf einen festen Wert gepatcht, damit JEDER
        Kandidat exakt auf ``start_utc`` faellt und beim ``<=``-Filter
        uebersprungen wird — ``bester`` bleibt garantiert ``None``."""
        schicht = Shift(date(2026, 6, 15), tf)
        fixer_start = _berlin(2026, 6, 15, 8, 0)
        fixer_end = _berlin(2026, 6, 15, 16, 0)
        monkeypatch.setattr(handover_note, "shift_window", lambda s: (fixer_start, fixer_end))
        assert next_shift(facility, schicht, time_filters=[tf]) == Shift(date(2026, 6, 16), None)

    def test_bei_gleichem_beginn_gewinnt_der_zuerst_uebergebene_kandidat(self, facility):
        """Toetet mutmut_39 (``<=`` statt ``<`` beim Bestenvergleich) —
        Sortier-/Tiebreak-Mutante, per deterministischem Aufbau gepinnt (zwei
        Zeitfenster mit IDENTISCHEM ``start_time``, `kandidaten`-Reihenfolge
        explizit vorgegeben). Original behaelt bei einem exakten Gleichstand
        den ZUERST gefundenen Kandidaten (strenges ``<``); die Mutante
        ueberschreibt ihn mit dem letzten Gleichstand."""
        tf_anker = TimeFilter.objects.create(
            facility=facility, label="Anker", start_time=time(0, 0), end_time=time(4, 0), sort_order=0
        )
        tf_a = TimeFilter.objects.create(
            facility=facility, label="A", start_time=time(8, 0), end_time=time(12, 0), sort_order=1
        )
        tf_b = TimeFilter.objects.create(
            facility=facility, label="B", start_time=time(8, 0), end_time=time(16, 0), sort_order=2
        )
        schicht = Shift(date(2026, 6, 15), tf_anker)
        assert next_shift(facility, schicht, time_filters=[tf_a, tf_b]) == Shift(date(2026, 6, 15), tf_a)

    # mutmut_19 (``start_utc = shift_window(shift)[0].astimezone(None)``) und
    # mutmut_32 (dieselbe Ersetzung fuer ``kandidat_start``) sind EQUIVALENT:
    # ``astimezone(None)`` auf einem bereits aware Datetime aendert nur das
    # angehaengte ``tzinfo``-Objekt (System-Lokalzeit statt UTC), NICHT den
    # repraesentierten Instant — und der Rueckgabewert wird ausschliesslich
    # in Instant-Vergleichen (``<=``, ``<``) verwendet, nie angezeigt. Belegt
    # im Bericht per Diff + Testlauf der ``TestSchichtbestimmung``-Klasse aus
    # ``test_handover_note.py`` unter ``MUTANT_UNDER_TEST=...mutmut_19``
    # bzw. ``...mutmut_32`` (bleibt gruen).


class TestBuildBlock:
    """``_build_block`` — 9 Survivors: ``x__build_block__mutmut_{9,11,14,16,
    19,24,25,50,53}``."""

    def test_ohne_gespeicherte_notiz_reicht_facility_und_zeitfilter_unveraendert_an_compute_shift_window_durch(
        self, monkeypatch, facility, tf
    ):
        """Toetet mutmut_9 (``facility`` → ``None``) und mutmut_{11,14}
        (``time_filters`` → ``None``/Keyword entfernt) — alle drei mutieren
        den ``compute_shift_window(...)``-Aufruf im ``note is None``-Zweig.
        Spy auf die Kollaborator-Funktion statt auf eine abgeleitete
        Fensterberechnung: deterministisch, unabhaengig davon, ob die
        vertauschten Argumente an dieser Stelle zufaellig dasselbe Ergebnis
        liefern wuerden. ``compute_shift_window`` wird in diesem Szenario
        ZWEIMAL aufgerufen (einmal direkt in ``_build_block``, einmal
        indirekt ueber ``is_shift_editable``, das dieselbe fehlende Notiz
        ebenfalls neu berechnet) — der ERSTE Aufruf ist der, den dieser
        Batch mutiert."""
        schicht = Shift(date(2026, 6, 15), tf)
        aufrufe = _spy(monkeypatch, handover_note, "compute_shift_window")
        _build_block(facility, schicht, "current", now=_berlin(2026, 6, 15, 9, 0), time_filters=[tf])
        assert aufrufe
        args, kwargs = aufrufe[0]
        assert args[0] == facility
        assert kwargs.get("time_filters") == [tf]

    def test_reicht_facility_zeitfilter_und_die_bereits_geladene_notiz_unveraendert_an_is_shift_editable_durch(
        self, monkeypatch, facility, staff_user, tf
    ):
        """Toetet mutmut_16 (``facility`` → ``None``), mutmut_{19,24}
        (``time_filters`` → ``None``/Keyword entfernt) und mutmut_25
        (``note=note`` entfernt, erzwingt eine ZWEITE, unnoetige
        ``get_note``-Abfrage statt der bereits geladenen Zeile — genau die
        Doppelabfrage, die der Docstring ausdruecklich vermeiden will)."""
        schicht = Shift(date(2026, 6, 15), tf)
        notiz = save_note(facility, schicht, staff_user, "Text", now=_berlin(2026, 6, 15, 7, 0))
        aufrufe = _spy(monkeypatch, handover_note, "is_shift_editable")
        _build_block(facility, schicht, "current", now=_berlin(2026, 6, 15, 9, 0), time_filters=[tf])
        assert len(aufrufe) == 1
        args, kwargs = aufrufe[0]
        assert args[0] == facility
        assert kwargs.get("time_filters") == [tf]
        assert kwargs.get("note") == notiz

    def test_zeigt_den_letzten_bearbeiter_nicht_den_ersterfasser(self, facility, staff_user, lead_user, tf):
        """Toetet mutmut_50 (``and`` statt ``or`` im ``str(note.updated_by or
        note.author)``) — bei GESETZTEM ``updated_by`` liefert ``and`` das
        FALSCHE Feld (``note.author``, die urspruengliche Verfasserin statt
        der zuletzt aendernden Person)."""
        schicht = Shift(date(2026, 6, 15), tf)
        save_note(facility, schicht, staff_user, "Erst", now=_berlin(2026, 6, 15, 7, 0))
        save_note(facility, schicht, lead_user, "Zweit", now=_berlin(2026, 6, 15, 8, 0))
        block = _build_block(facility, schicht, "current", now=_berlin(2026, 6, 15, 9, 0), time_filters=[tf])
        assert block.author == str(lead_user)

    def test_zeigt_den_autor_auch_wenn_der_zuletzt_aendernde_account_geloescht_wurde(self, facility, staff_user, tf):
        """Toetet mutmut_53 (``and`` statt ``or`` in der aeusseren Bedingung
        ``note.updated_by or note.author``) — ist NUR ``author`` gesetzt
        (``updated_by`` per ``SET_NULL`` geloescht), liefert ``or`` weiterhin
        den Autorennamen; ``and`` schaltet die gesamte Anzeige still auf
        ``None``."""
        schicht = Shift(date(2026, 6, 15), tf)
        notiz = save_note(facility, schicht, staff_user, "Text", now=_berlin(2026, 6, 15, 7, 0))
        notiz.updated_by = None
        notiz.save(update_fields=["updated_by"])
        block = _build_block(facility, schicht, "current", now=_berlin(2026, 6, 15, 9, 0), time_filters=[tf])
        assert block.author == str(staff_user)


class TestGetNote:
    """``get_note`` — 3 Survivors: ``x_get_note__mutmut_{4,5,6}``, alle
    mutieren die ``select_related("author", "updated_by", "time_filter")``-
    Liste. Nachbesserung (Zweitgutachter): ``grep -rn`` auf
    ``note\\.author``/``note\\.updated_by``/``note\\.time_filter`` gegen das
    ``get_note``-Ergebnis zeigt, dass ausschliesslich ``_build_block``
    (Zeile 608) ``note.updated_by`` UND ``note.author`` liest (``str(note.
    updated_by or note.author) if ... (note.updated_by or note.author)
    else None``) — ``note.time_filter`` wird an KEINER Stelle auf dem
    ``get_note``-Rueckgabewert gelesen (``is_shift_editable`` beruehrt nur
    ``note_window`` = ``window_start``/``window_end``/``editable_until``,
    eigene Spalten ohne FK-Bezug). ``mutmut_4``/``mutmut_5`` sind damit KEINE
    reinen Optimierungs-Mutanten mehr, sondern erzeugen eine beobachtbare
    zusaetzliche DB-Abfrage — per ``CaptureQueriesContext`` gepinnt.
    ``mutmut_6`` (``time_filter`` entfernt) bleibt ``equivalent``."""

    def test_select_related_spart_die_abfrage_fuer_updated_by(self, facility, staff_user, tf):
        """Toetet mutmut_5 (``"updated_by"`` aus ``select_related`` entfernt).
        ``note.updated_by`` wird in ``_build_block`` bei JEDER bestehenden
        Notiz zuerst gelesen (Ternary-Bedingung) — ohne Eager-Load loest der
        Zugriff eine zusaetzliche Abfrage aus."""
        schicht = Shift(date(2026, 6, 15), tf)
        save_note(facility, schicht, staff_user, "Text", now=_berlin(2026, 6, 15, 7, 0))
        with CaptureQueriesContext(connection) as ctx:
            note = get_note(facility, schicht)
            _ = note.updated_by
        assert len(ctx.captured_queries) == 1

    def test_select_related_spart_die_abfrage_fuer_author_wenn_updated_by_fehlt(self, facility, staff_user, tf):
        """Toetet mutmut_4 (``"author"`` aus ``select_related`` entfernt).
        ``note.author`` wird in ``_build_block`` genau dann gelesen, wenn
        ``note.updated_by`` falsy ist (zuletzt aendernder Account per
        ``SET_NULL`` geloescht, Szenario wie in ``TestBuildBlock``) — auch
        dann darf kein zusaetzlicher Query noetig sein."""
        schicht = Shift(date(2026, 6, 15), tf)
        notiz = save_note(facility, schicht, staff_user, "Text", now=_berlin(2026, 6, 15, 7, 0))
        notiz.updated_by = None
        notiz.save(update_fields=["updated_by"])
        with CaptureQueriesContext(connection) as ctx:
            note = get_note(facility, schicht)
            _ = note.updated_by or note.author
        assert len(ctx.captured_queries) == 1

    # mutmut_6 (``"time_filter"`` aus ``select_related`` entfernt) ist
    # EQUIVALENT: ``note.time_filter`` wird an KEINER Stelle auf dem
    # ``get_note``-Rueckgabewert gelesen (Grep-Beleg im Klassen-Docstring
    # oben; einzige ``.time_filter``-Zugriffe im Modul haengen an ``shift.
    # time_filter``/``Shift.time_filter`` bzw. an ``ShiftHandoverNote.
    # __str__``, das ueber ``get_note`` nie aufgerufen wird) — ohne Zugriff
    # macht select_related weder wert- noch querymaessig einen Unterschied.
    # Belegt per Diff + Testlauf von test_select_related_spart_die_abfrage_
    # fuer_updated_by (oben) unter MUTANT_UNDER_TEST=...mutmut_6 (bleibt
    # gruen, da diese Kollaborator-Kette ``note.time_filter`` ebenfalls nie
    # anfasst).


class TestIsShiftEditable:
    """``is_shift_editable`` — 8 Survivors: ``x_is_shift_editable__mutmut_{4,
    5,12,17,21,24,33,35}``."""

    def test_ohne_explizit_uebergebene_notiz_liest_die_echte_gespeicherte_zeile(self, facility, staff_user, tf):
        """Toetet mutmut_4 (``note = None`` statt ``get_note(...)``) und
        mutmut_5 (``get_note(None, shift)``, findet die reale Zeile nie) —
        beide behandeln eine TATSAECHLICH gespeicherte, laengst geschlossene
        Notiz so, als gaebe es sie nicht, und rechnen faelschlich mit der
        GEGENWAERTIGEN Konfiguration (die hier grosszuegiger waere)."""
        schicht = Shift(date(2026, 6, 15), tf)
        ShiftHandoverNote.objects.create(
            facility=facility,
            shift_date=schicht.shift_date,
            time_filter=tf,
            note=None,
            author=staff_user,
            updated_by=staff_user,
            window_start=_berlin(2026, 6, 15, 6, 0),
            window_end=_berlin(2026, 6, 15, 14, 0),
            editable_until=_berlin(2026, 6, 15, 15, 0),
        )
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 15, 20, 0)) is False

    def test_bei_bestehender_notiz_ignoriert_die_fensteroeffnung_den_schichtbeginn(self, facility, staff_user, tf):
        """Toetet mutmut_17 (``ab_schichtbeginn=True`` statt ``False`` im
        Bestandszeilen-Zweig) — fuer eine bestehende Notiz zaehlt laut
        Docstring AUSSCHLIESSLICH ``editable_until``; ein ``now`` VOR dem
        eingefrorenen ``window_start`` darf die Bearbeitung nicht sperren."""
        schicht = Shift(date(2026, 6, 15), tf)
        notiz = ShiftHandoverNote.objects.create(
            facility=facility,
            shift_date=schicht.shift_date,
            time_filter=tf,
            note=None,
            author=staff_user,
            updated_by=staff_user,
            window_start=_berlin(2026, 6, 15, 6, 0),
            window_end=_berlin(2026, 6, 15, 14, 0),
            editable_until=_berlin(2026, 6, 16, 14, 0),
        )
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 15, 3, 0), note=notiz) is True

    def test_ohne_gespeicherte_notiz_nutzt_das_fenster_den_uebergebenen_zeitfilter_snapshot(self, facility, tf):
        """Toetet mutmut_{21,24} (``time_filters`` → ``None``/Keyword
        entfernt im ``compute_shift_window``-Aufruf) — ein bewusst LEERER
        Snapshot (``time_filters=[]``, „zum Anfragezeitpunkt kein aktives
        Zeitfenster") muss die GROSSZUEGIGE Ganztags-Folgetag-Grenze liefern,
        nicht die engere Grenze der tatsaechlich aktiven Konfiguration."""
        schicht = Shift(date(2026, 6, 15), tf)
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 16, 20, 0), time_filters=[]) is True

    def test_identitaetspruefung_verwendet_die_richtige_facility(self, facility, tf):
        """Toetet mutmut_33 (``facility`` → ``None`` im ``_identitaet_ok``-
        Aufruf) — mit konfigurierten Zeitfenstern muss eine Ganztags-Schicht
        blockiert werden; mit ``facility=None`` findet die Identitaetspruefung
        keine Zeitfenster und laesst faelschlich zu."""
        schicht = Shift(date(2026, 6, 15), None)
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 15, 10, 0), time_filters=None) is False

    def test_identitaetspruefung_nutzt_den_uebergebenen_zeitfilter_snapshot(self, facility, tf):
        """Toetet mutmut_35 (``time_filters`` → ``None`` im
        ``_identitaet_ok``-Aufruf, ignoriert den uebergebenen Snapshot) — ein
        LEERER Snapshot muss die Ganztags-Schicht als identitaetskonform
        durchlassen, auch wenn die Einrichtung ANDERSWO Zeitfenster hat."""
        schicht = Shift(date(2026, 6, 15), None)
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 15, 10, 0), time_filters=[]) is True

    # mutmut_12 (``ab_schichtbeginn=None`` statt ``False`` im
    # Bestandszeilen-Zweig) ist EQUIVALENT: ``None`` und ``False`` sind in
    # der einzigen Verwendung (``if ab_schichtbeginn and ...``) gleich falsy
    # — kein Aufrufer kann die beiden unterscheiden. Belegt per Diff + per
    # Testlauf von
    # ``test_bei_bestehender_notiz_ignoriert_die_fensteroeffnung_den_schichtbeginn``
    # oben unter ``MUTANT_UNDER_TEST=...mutmut_12`` (bleibt gruen).


class TestAssertShiftIdentity:
    """``assert_shift_identity`` — 7 Survivors: ``x_assert_shift_identity__
    mutmut_{5,7,8,9,10,11,12}`` — ausschliesslich Mutationen des
    Fehlertexts (kein Zweig der ``if kandidaten:``-Bedingung ist in diesem
    Batch mutiert). Ein Test mit EXAKTEM Textvergleich toetet alle sieben in
    einem Lauf: ``None`` statt Text (5), ``XX...XX``-Marker (7/10),
    Klein-/Grossschreibung (8/11, 9/12)."""

    def test_meldet_den_exakten_text_wenn_schichten_konfiguriert_sind(self, facility, tf):
        with pytest.raises(ValidationError) as exc_info:
            assert_shift_identity(facility, Shift(date(2026, 6, 15), None))
        assert exc_info.value.messages == [
            "Für diese Einrichtung sind Schichten konfiguriert — eine Übergabenotiz "
            "gehört zu einer Schicht, nicht zum ganzen Tag."
        ]


class TestFensterZu:
    """``_fenster_zu`` — 6 Survivors: ``x__fenster_zu__mutmut_{4,10,12,13,
    14,15}``. Ein Test mit vollstaendig exaktem Textvergleich toetet alle
    sechs: der umgebende Text (4), das Format-Argument (10/12/14/15 — ``None``
    /entfernt/verstuemmelt/kleingeschrieben, jede Variante veraendert das
    tatsaechliche Ausgabeformat von ``date_format`` nachweislich) UND der
    WERT selbst (13 — ``timezone.localtime(None)`` liefert ``jetzt`` statt
    der eingefrorenen ``editable_until``-Grenze; mit einer Grenze weit in der
    Vergangenheit ist das im Text sofort sichtbar)."""

    def test_meldet_die_eingefrorene_editable_until_grenze_exakt_formatiert(self):
        grenze = _berlin(2020, 3, 4, 10, 30)
        fenster = ShiftWindow(
            window_start=grenze - timedelta(hours=8),
            window_end=grenze - timedelta(hours=1),
            editable_until=grenze,
        )
        exc = _fenster_zu(fenster)
        erwartete_grenze = date_format(timezone.localtime(grenze), "SHORT_DATETIME_FORMAT")
        assert exc.messages == [f"Bearbeitung nur bis {erwartete_grenze} möglich — diese Schicht ist abgeschlossen."]


class TestWindowIsOpen:
    """``_window_is_open`` — 4 Survivors: ``x__window_is_open__mutmut_{2,4,
    5,8}``."""

    def test_schliesst_die_neuanlage_exakt_am_eingefrorenen_schichtbeginn(self):
        """Toetet mutmut_4 (``<=`` statt ``<`` beim Schichtbeginn-Vergleich)
        — Grenzfall-Mutante (Zeitfenster-Logik mit festen ``now``-Werten). Am
        Instant ``now == window_start`` ist die Schicht
        laut Original bereits begonnen (strenges ``<``); die Mutante sperrt
        genau diesen Instant faelschlich."""
        beginn = _berlin(2026, 6, 15, 6, 0)
        fenster = ShiftWindow(
            window_start=beginn,
            window_end=beginn + timedelta(hours=8),
            editable_until=beginn + timedelta(days=1),
        )
        assert _window_is_open(fenster, beginn, ab_schichtbeginn=True) is True

    # mutmut_2 (``jetzt = now.astimezone(None)``), mutmut_5
    # (``fenster.window_start.astimezone(None)``) und mutmut_8
    # (``fenster.editable_until.astimezone(None)``) sind EQUIVALENT:
    # ``astimezone(None)`` auf einem aware Datetime aendert nur das
    # angehaengte ``tzinfo``, nicht den repraesentierten Instant, und alle
    # drei Werte werden ausschliesslich in ``<``-Instant-Vergleichen
    # verwendet. Belegt per Diff + Testlauf von
    # ``TestEditierfenster``/``TestEditierfensterDST`` aus
    # ``test_handover_note.py`` unter ``MUTANT_UNDER_TEST=...mutmut_2`` bzw.
    # ``...mutmut_5``/``...mutmut_8`` (bleiben gruen).


class TestSchichtUnbestimmbar:
    """``_schicht_unbestimmbar`` (ueber ``assert_shift_date_supported``) — 4
    Survivors: ``x__schicht_unbestimmbar__mutmut_{1,3,4,5}`` — ausschliesslich
    Mutationen des Fehlertexts. Ein Test mit EXAKTEM Textvergleich toetet
    alle vier: ``None`` statt Text (1), ``XX...XX``-Marker (3),
    Klein-/Grossschreibung (4/5)."""

    def test_meldet_den_exakten_text_bei_nicht_unterstuetztem_schichtdatum(self):
        with pytest.raises(ValidationError) as exc_info:
            assert_shift_date_supported(Shift(date(1999, 12, 31), None), today=date(2026, 9, 16))
        assert exc_info.value.messages == ["Die Schicht konnte nicht bestimmt werden. Bitte die Seite neu laden."]


class TestIdentitaetOk:
    """``_identitaet_ok`` — 4 Survivors: ``x__identitaet_ok__mutmut_{1,3,6,
    7}``."""

    def test_meldet_verletzte_identitaet_mit_der_richtigen_facility_als_false(self, facility, tf):
        """Toetet mutmut_1 (``facility`` → ``None`` im ``assert_shift_
        identity``-Aufruf, findet keine Zeitfenster mehr → False-Positiv)
        UND mutmut_7 (``except ValidationError: return True`` statt
        ``False`` — invertiert das Ergebnis einer korrekt ausgeloesten
        Ausnahme). Beide liefern fuer dasselbe Szenario ``True`` statt
        ``False``."""
        schicht = Shift(date(2026, 6, 15), None)
        assert _identitaet_ok(facility, schicht, time_filters=None) is False

    def test_erlaubt_die_ganztags_identitaet_mit_dem_uebergebenen_leeren_zeitfilter_snapshot(self, facility, tf):
        """Toetet mutmut_{3,6} (``time_filters`` → ``None``/Keyword entfernt
        im ``assert_shift_identity``-Aufruf, ignoriert den leeren Snapshot
        und findet stattdessen die echten aktiven Zeitfenster)."""
        schicht = Shift(date(2026, 6, 15), None)
        assert _identitaet_ok(facility, schicht, time_filters=[]) is True


class TestCanReadNotes:
    """``can_read_notes`` — 3 Survivors: ``x_can_read_notes__mutmut_{4,7,
    10}``."""

    def test_verweigert_ohne_das_attribut_sicher(self):
        """Toetet mutmut_7 (Default-Wert komplett entfernt → ``AttributeError``
        bei einem Objekt ohne ``is_staff_or_above``) und mutmut_10 (Default
        ``True`` statt ``False`` → oeffnet blind, „fail open" statt „fail
        closed" — ein Berechtigungs-Bug). Dasselbe Objekt belegt zugleich
        mutmut_4 (Default ``None`` statt ``False``) als EQUIVALENT: ``bool
        (None) == bool(False) == False``, kein Aufrufer sieht einen
        Unterschied — dieser Test bleibt unter ``MUTANT_UNDER_TEST=...
        mutmut_4`` gruen."""
        assert can_read_notes(object()) is False


class TestBuildHandoverNoteContext:
    """``build_handover_note_context`` — 3 Survivors: ``x_build_handover_
    note_context__mutmut_{29,30,43}``."""

    def test_uebergibt_now_und_zeitfilter_snapshot_unveraendert_an_beide_bloecke(
        self, monkeypatch, facility, staff_user
    ):
        """Toetet mutmut_29 (``now=None`` im ``previous``-Aufruf), mutmut_30
        (``time_filters=None`` im ``previous``-Aufruf) und mutmut_43
        (``time_filters=None`` im ``current``-Aufruf) — Spy auf
        ``_build_block`` statt auf die durchgereichten Werte: erfasst
        BEIDE Aufrufe (Kalenderfall, keine aktiven Zeitfenster ⇒ ``previous``
        = Vortag, ``current`` = angezeigter Tag) und prueft je Aufruf, dass
        ``now``/``time_filters`` unveraendert ankommen."""
        aufrufe = _spy(monkeypatch, handover_note, "_build_block")
        jetzt = _berlin(2026, 6, 15, 15, 0)
        angezeigt = Shift(date(2026, 6, 15), None)
        build_handover_note_context(facility, staff_user, angezeigt, now=jetzt)
        assert len(aufrufe) == 2
        for args, kwargs in aufrufe:
            assert kwargs.get("now") == jetzt
            assert kwargs.get("time_filters") == []


class TestActiveTimeFilters:
    """``_active_time_filters`` — 2 Survivors: ``x__active_time_filters__
    mutmut_{4,5}``."""

    def test_ordnet_nach_sort_order_und_dann_nach_pk(self, monkeypatch, facility):
        """Toetet mutmut_4 (``order_by("pk")``, verliert die primaere
        Sortierung) und mutmut_5 (``order_by("sort_order", )``, verliert den
        ``pk``-Tiebreak) — Spy auf ``QuerySet.order_by`` statt auf
        tatsaechliches Gleichstands-Verhalten in Postgres: ``pk`` ist ein
        UUID-Feld, ein realer Gleichstand waere ohne SQL-``ORDER BY``-Garantie
        nicht deterministisch pruefbar. Der Spy reicht an die echte
        Implementierung durch (Verhalten unveraendert) und zeichnet nur die
        tatsaechlich uebergebenen Argumente auf."""
        aufrufe = []
        original_order_by = QuerySet.order_by

        def spy_order_by(self, *args, **kwargs):
            if self.model is TimeFilter:
                aufrufe.append(args)
            return original_order_by(self, *args, **kwargs)

        monkeypatch.setattr(QuerySet, "order_by", spy_order_by)
        _active_time_filters(facility)
        assert aufrufe
        assert aufrufe[-1] == ("sort_order", "pk")


class TestEditableUntil:
    """``editable_until`` — 2 Survivors: ``x_editable_until__mutmut_{4,7}``."""

    def test_nutzt_den_uebergebenen_zeitfilter_snapshot_fuer_die_naechste_schicht(self, facility, tf):
        """Toetet mutmut_4 (``time_filters=None`` im ``next_shift``-Aufruf)
        und mutmut_7 (dasselbe, Keyword entfernt statt explizit ``None`` —
        identisches Verhalten, da der Funktionsdefault ebenfalls ``None``
        ist). Ein LEERER Snapshot (``time_filters=[]``) muss die
        grosszuegige Ganztags-Folgetag-Grenze liefern, nicht die engere
        Grenze der echten (einzigen) aktiven Konfiguration."""
        schicht = Shift(date(2026, 6, 15), tf)
        grenze = editable_until(facility, schicht, time_filters=[])
        assert grenze == shift_window(Shift(date(2026, 6, 16), None))[1]
