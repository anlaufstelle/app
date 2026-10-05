"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.zeitstrom_watch`` (Batch ``zeitstrom_watch-e``, letzter Teil
des Moduls).

Refs #2174.
Refs #2193.

Quelle: Vollmutations-Lauf auf main@54b17ef1 (frischer Mutantenbaum), Batch
``zeitstrom_watch-e`` (63 Survivor-Mutanten) ueber zehn kleine Helfer-/
Fassadenfunktionen:

* ``_json`` — kanonische, kompakte JSON-Kodierung fuer alle HMAC-Quelltexte
  des Watchers (Seitenbindung, Komponenten-/Seiten-ETag).
* ``_tally_counts_projection`` / ``build_zeitstrom_tally_topology_validator``
  — Strichlisten-Baseline: Einrichtungs-/Rollen-/Zeitfenster-Weiterreichung
  an ``build_tally_summary`` im Fallback-Pfad (kein ``summary`` vorgegeben)
  bzw. an ``build_zeitstrom_component_validator``.
* ``build_zeitstrom_watch_snapshot`` — derselbe Weiterreichungs-Vertrag auf
  der aeusseren Schnappschuss-Ebene (bestimmt zusaetzlich, OB ueberhaupt neu
  aggregiert wird).
* ``build_zeitstrom_component_validator`` — Fassade, die facility/user
  unveraendert an die jeweilige Bauteil-Projektion durchreicht.
* ``zeitstrom_page_scope_matches`` — Normalisierung fehlgeformter
  Kandidaten auf den EINEN Konstantzeit-Vergleichspfad (Timing-Haertung).
* ``_dynamic_projection`` — Leer-/Boolean-Filterung der pro Ereignis
  sichtbaren Feldwerte.
* ``_event_projection`` / ``_ban_card_projection`` / ``_related_user`` —
  reine Tupel-Projektionen aus einem ``values()``-Zeilen-Dict.

Mutmut mutiert dabei zwei Achsen:

* **Weiterreichungs-Bruch** (``facility``/``user``/``context`` durch
  ``None`` ersetzt, Positions-/Keyword-Argumente gestrichen, ``and``/``or``
  vertauscht) an den Stellen, die an ``build_tally_summary`` bzw. an die
  Bauteil-Projektionen delegieren — gefangen per Spy auf den importierten
  Namen (``monkeypatch.setattr`` auf das Modul, Muster wie
  ``test_mutation_pins_098_hubs.py``), NIE ueber das Ergebnis der (nicht
  gemockten) Baseline-Abfrage selbst.
* **Tupel-/String-Korruption** (``str(x)`` -> ``str(None)``,
  ``row.get(key)`` -> ``row.get(None)``, Praefix-/Literal-Vertauschung,
  Vergleichs-/Booleschoperator-Tausch) in den reinen Projektionsfunktionen
  — gefangen per Direktaufruf mit einem Zeilen-/Feld-Szenario, dessen Werte
  sich alle exakt unterscheiden lassen (``==``, nie Teilstring).

Zwei Achsen sind bei genauer Pruefung AEQUIVALENT (Begruendung je Mutante im
Test-/Triage-Dokument): ``ensure_ascii=None`` nimmt im json-Encoder denselben
(nicht-eskapierenden) Zweig wie ``ensure_ascii=False`` (Truthy-Check), und
die Schnittmengen-Pruefung in ``build_zeitstrom_watch_snapshot`` ist wegen
Kurzschluss-``and`` und der Tatsache, dass ``tally_topology`` IMMER in
``component_names`` steht, unabhaengig vom konkreten Set-Literal.

Konventionen wie ``test_mutation_pins_099_zeitstrom_watch_a.py`` /
``_b.py`` / ``_c.py``: Zusicherungen vergleichen EXAKT (``==``), nie per
Teilstring. ``facility``/``user`` sind fuer die reinen Weiterreichungs-Spies
bewusst leichte ``SimpleNamespace``-Platzhalter (nur ``.pk`` noetig) statt
echter DB-Objekte — die Funktionen selbst fassen sie nur an, ohne sie zu
lesen/zu speichern. ``_encryption_key`` liegt modulweit an (Lehre 112) — der
Mutantenbaum hat keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import json as stdlib_json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

import core.services.zeitstrom_watch as zeitstrom_watch_module
from core.services.zeitstrom_filters import ZeitstromState
from core.services.zeitstrom_watch import (
    _ban_card_projection,
    _dynamic_projection,
    _event_projection,
    _json,
    _related_user,
    _tally_counts_projection,
    build_zeitstrom_component_validator,
    build_zeitstrom_page_scope,
    build_zeitstrom_tally_topology_validator,
    build_zeitstrom_watch_snapshot,
    zeitstrom_page_scope_matches,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _recording_stub(calls, retval="stub"):
    """Spy-Fabrik: zeichnet ``(args, kwargs)`` auf und liefert ``retval``."""

    def inner(*args, **kwargs):
        calls.append((args, kwargs))
        return retval

    return inner


def _state(*, target_date, local_date, live_context, time_filter=None, view=""):
    return ZeitstromState(
        target_date=target_date,
        time_filter=time_filter,
        time_filters=(),
        live_context=live_context,
        feed_type="",
        doc_type_id="",
        view=view,
        local_date=local_date,
    )


def _minimal_state():
    """Live-Tally-Kontext (target_date == local_date, kein Zeitfilter)."""

    return _state(target_date=DATE_A, local_date=DATE_A, live_context=SimpleNamespace(time_filter=None))


def _stub_other_components(monkeypatch):
    """Stubbt feed/current_shift/focus_box/bans — nicht Ziel dieser Batch."""

    monkeypatch.setattr(zeitstrom_watch_module, "_feed_projection", lambda *a, **k: "stub-feed")
    monkeypatch.setattr(zeitstrom_watch_module, "_current_shift_projection", lambda *a, **k: "stub-current-shift")
    monkeypatch.setattr(zeitstrom_watch_module, "_focus_box_projection", lambda *a, **k: "stub-focus-box")
    monkeypatch.setattr(zeitstrom_watch_module, "_bans_projection", lambda *a, **k: "stub-bans")


DATE_A = date(2026, 9, 10)
DATE_B = date(2026, 9, 11)


# ---------------------------------------------------------------------------
# _json — kanonische HMAC-Quelltext-Kodierung (9 Survivors)
# ---------------------------------------------------------------------------


def test_json_is_canonical_sorted_compact_and_ascii_transparent():
    """Pins x__json__mutmut_2/4/7/8/9/11/12/13/14 (sort_keys/ensure_ascii/
    separators je entfernt, auf den Gegenwert gesetzt oder mit dem
    mutmut-String-Marker ``XX…XX`` versehen).

    ``x__json__mutmut_3`` (``ensure_ascii=None``) ist AEQUIVALENT: der
    json-Encoder prueft ``ensure_ascii`` nur per ``if self.ensure_ascii:``
    (Truthy-Check) — ``None`` und ``False`` nehmen denselben, nicht
    eskapierenden Zweig, kein Eingabewert unterscheidet beide (siehe
    Triage-Dokument).
    """
    payload = {"b": 1, "a": "ä", "c": [1, 2]}
    expected = stdlib_json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)

    assert expected == '{"a":"ä","b":1,"c":[1,2]}', "Sanity: Referenzwert ist sortiert/kompakt/nicht-eskapiert."
    assert _json(payload) == expected


# ---------------------------------------------------------------------------
# _dynamic_projection — Leer-/Boolean-Filterung sichtbarer Feldwerte (14 Survivors)
# ---------------------------------------------------------------------------


def test_dynamic_projection_missing_document_type_falls_back_to_empty_tuple():
    """Pins x__dynamic_projection__mutmut_7/9 (Default ``()`` -> ``None``
    bzw. entferntes Default-Argument bei ``field_configs.get(...)``): fehlt
    der ``document_type_id``-Schluessel, muss die Schleife folgenlos
    ausbleiben (``()``) statt ``for ... in None`` zu werfen."""

    row = {"document_type_id": "unbekannter-doctype", "data_json": {"beliebig": "wert"}}

    assert _dynamic_projection(row, {}) == ()


def test_dynamic_projection_filters_blank_and_boolean_values_without_truncating():
    """Pins x__dynamic_projection__mutmut_14/15/18/19/20/21/22/23/24/25/26/27
    (``or``/``and``-Vertauschung der Leerwert-Pruefung, String-Marker
    ``""``-> ``"XXXX"``, ``isinstance(...) and/or not value``-Vertauschung
    fuer Listen, ``field_type == "boolean"``-Vertauschung/-Vertippung,
    ``not value``-> ``value``, sowie ``continue`` -> ``break`` an beiden
    Ausschluss-Pruefungen).

    Die Feldreihenfolge platziert je ein auszuschliessendes Feld VOR
    gueltigen Folgefeldern (``f_none``/``f_empty`` vor ``f_zero`` etc.,
    ``f_bool_false`` vor ``f_bool_true``/``f_text_real``) — ein
    ``break``-Mutant liesse die Folgefelder verschwinden.
    """
    doc_type_id = "doctype-dynproj"
    field_configs = {
        doc_type_id: [
            ("f_none", "Feld None", "text", "opts-none"),
            ("f_empty", "Feld Leer", "text", "opts-empty"),
            ("f_emptylist", "Feld Leere Liste", "multi", "opts-emptylist"),
            ("f_zero", "Feld Null", "number", "opts-zero"),
            ("f_falselist", "Feld Falsy-Liste", "multi", "opts-falselist"),
            ("f_bool_false", "Feld Bool Falsch", "boolean", "opts-bool-false"),
            ("f_bool_true", "Feld Bool Wahr", "boolean", "opts-bool-true"),
            ("f_text_real", "Feld Text", "text", "opts-text"),
        ]
    }
    row = {
        "document_type_id": doc_type_id,
        "data_json": {
            "f_none": None,
            "f_empty": "",
            "f_emptylist": [],
            "f_zero": 0,
            "f_falselist": [False],
            "f_bool_false": False,
            "f_bool_true": True,
            "f_text_real": "hallo",
        },
    }

    result = _dynamic_projection(row, field_configs)

    assert result == (
        ("f_zero", "Feld Null", "number", "opts-zero", 0),
        ("f_falselist", "Feld Falsy-Liste", "multi", "opts-falselist", [False]),
        ("f_bool_true", "Feld Bool Wahr", "boolean", "opts-bool-true", True),
        ("f_text_real", "Feld Text", "text", "opts-text", "hallo"),
    )


# ---------------------------------------------------------------------------
# _related_user / _event_projection / _ban_card_projection — Tupel-Projektionen
# (4 + 5 + 2 = 11 Survivors)
# ---------------------------------------------------------------------------


def test_related_user_projects_all_five_fields_without_swap():
    """Pins x__related_user__mutmut_4/5/7/8 (``str(user_id)`` -> ``str(None)``
    bzw. ``row.get(f"{prefix}__…")`` -> ``row.get(None)`` fuer
    username/first_name/last_name — ``row.get(None)`` trifft nie einen
    echten Schluessel und liefert immer ``None``)."""

    row = {
        "created_by_id": "user-42",
        "created_by__username": "hnovak",
        "created_by__display_name": "Hana Novak",
        "created_by__first_name": "Hana",
        "created_by__last_name": "Novak",
    }

    assert _related_user(row, "created_by") == (
        "user-42",
        "hnovak",
        "Hana Novak",
        "Hana",
        "Novak",
    )


def test_event_projection_keeps_id_client_id_and_creator_distinct():
    """Pins x__event_projection__mutmut_1/10/18/21/22 (``str(row["id"])``
    bzw. ``str(row["client_id"])`` -> ``str(None)``; ``_related_user(row,
    "created_by")`` -> ``_related_user(row, None/"XXcreated_byXX"/
    "CREATED_BY")`` — jeder falsche Praefix trifft keinen echten
    ``…_id``-Schluessel und liefert ``None`` statt des Ersteller-Tupels)."""

    occurred_at = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    row = {
        "id": "event-101",
        "occurred_at": occurred_at,
        "document_type__name": "Beratungsgespraech",
        "document_type_id": "doctype-101",
        "is_anonymous": False,
        "client_id": "client-11",
        "client__pseudonym": "Pseudo-11",
        "created_by_id": "user-21",
        "created_by__username": "tkrause",
        "created_by__display_name": "Tom Krause",
        "created_by__first_name": "Tom",
        "created_by__last_name": "Krause",
        "data_json": {},
    }

    assert _event_projection(row, {}) == (
        "event-101",
        occurred_at,
        "Beratungsgespraech",
        False,
        ("client-11", "Pseudo-11"),
        ("user-21", "tkrause", "Tom Krause", "Tom", "Krause"),
        (),
    )


def test_ban_card_projection_keeps_id_and_client_id_distinct():
    """Pins x__ban_card_projection__mutmut_1/6 (``str(row["id"])`` bzw.
    ``str(row["client_id"])`` -> ``str(None)``)."""

    occurred_at = datetime(2026, 9, 10, 8, 30, tzinfo=UTC)
    row = {
        "id": "event-77",
        "occurred_at": occurred_at,
        "client_id": "client-9",
        "client__pseudonym": "Pseudo-9",
        "created_by_id": "user-5",
        "created_by__username": "abauer",
        "created_by__display_name": "Anna Bauer",
        "created_by__first_name": "Anna",
        "created_by__last_name": "Bauer",
    }

    assert _ban_card_projection(row) == (
        "event-77",
        occurred_at,
        ("client-9", "Pseudo-9"),
        ("user-5", "abauer", "Anna Bauer", "Anna", "Bauer"),
    )


# ---------------------------------------------------------------------------
# _tally_counts_projection — Baseline-Weiterreichung + pk-Unterscheidbarkeit
# (9 Survivors: 6 Weiterreichung, 1 pk-Korruption, 2 skip)
# ---------------------------------------------------------------------------


def test_tally_counts_projection_fallback_forwards_facility_user_and_context(monkeypatch):
    """Pins x__tally_counts_projection__mutmut_6/7/8/9/10/11 (facility/user
    durch ``None`` ersetzt, ``context`` durch ``None`` ersetzt, Positions-
    bzw. Keyword-Argument gestrichen — alles im Fallback-Pfad, der greift,
    wenn ``build_zeitstrom_component_validator``/-``_tally_topology_validator``
    ohne vorberechnetes ``summary`` aufgerufen werden, siehe Docstring von
    ``build_zeitstrom_watch_snapshot`` ('fixed-query'))."""

    calls = []
    monkeypatch.setattr(
        zeitstrom_watch_module,
        "build_tally_summary",
        _recording_stub(calls, retval={"rows": []}),
    )
    facility = SimpleNamespace(pk="facility-tcp")
    user = SimpleNamespace(pk="user-tcp")
    live_context = SimpleNamespace(time_filter=None)
    state = _state(target_date=DATE_A, local_date=DATE_A, live_context=live_context)

    result = _tally_counts_projection(facility, user, state, summary=None)

    assert result == ()
    assert calls == [((facility, user), {"context": live_context})]


def test_tally_counts_projection_keeps_each_document_type_pk_distinct():
    """Pins x__tally_counts_projection__mutmut_13 (``str(row["document_type"]
    .pk)`` -> ``str(None)``): zwei unterschiedliche Dokumenttypen duerfen im
    Ergebnis nicht auf denselben Identifier kollabieren."""

    state = _state(target_date=DATE_A, local_date=DATE_A, live_context=SimpleNamespace(time_filter=None))
    summary = {
        "rows": [
            {"document_type": SimpleNamespace(pk="doctype-a"), "count": 3},
            {"document_type": SimpleNamespace(pk="doctype-b"), "count": 7},
        ]
    }

    result = _tally_counts_projection(
        SimpleNamespace(pk="facility-tcp2"),
        SimpleNamespace(pk="user-tcp2"),
        state,
        summary=summary,
    )

    assert result == (("doctype-a", 3), ("doctype-b", 7))


def test_tally_counts_projection_hidden_sentinel_is_exact():
    """Pins x__tally_counts_projection__mutmut_2/3 (Sentinel ``"hidden"`` ->
    ``"XXhiddenXX"``/``"HIDDEN"``): ausserhalb des Live-Tally-Kontexts ist
    der Rueckgabewert direkt beobachtbar (Funktionsergebnis UND — darueber —
    der HMAC-Quelltext des Validators) und muss exakt ``("hidden",)``
    lauten, nicht irgendein fester Platzhalter (Nachbesserung nach
    Zweitgutachten: urspruenglich faelschlich als ``skip`` eingestuft)."""

    state = _state(target_date=DATE_A, local_date=DATE_B, live_context=SimpleNamespace(time_filter=None))

    result = _tally_counts_projection(SimpleNamespace(pk="f-hidden"), SimpleNamespace(pk="u-hidden"), state)

    assert result == ("hidden",)


# ---------------------------------------------------------------------------
# build_zeitstrom_watch_snapshot — Weiterreichung + Live-Gate (3 Survivors)
# ---------------------------------------------------------------------------


def test_build_zeitstrom_watch_snapshot_reuses_live_context_for_tally_summary(monkeypatch):
    """Pins x_build_zeitstrom_watch_snapshot__mutmut_18/21 (``context=state.
    live_context`` -> ``context=None`` bzw. Keyword gestrichen): im
    Live-Tally-Kontext muss der bereits aufgeloeste Zeitfenster-Kontext des
    Requests weitergereicht werden, sonst berechnet ``build_tally_summary``
    ein FALSCHES (das aktuelle statt des angeforderten) Zeitfenster.

    ``x_build_zeitstrom_watch_snapshot__mutmut_8`` (Startwert ``None`` ->
    ``""``) sowie ``_11/_12/_13/_14`` (Set-Literal ``"tally_counts"``/
    ``"tally_topology"`` mit dem mutmut-Marker versehen) sind AEQUIVALENT —
    Begruendung im Triage-Dokument (Kurzschluss-``and`` + ``tally_topology``
    steht immer in ``component_names``).
    """
    _stub_other_components(monkeypatch)
    calls = []
    monkeypatch.setattr(
        zeitstrom_watch_module,
        "build_tally_summary",
        _recording_stub(
            calls,
            retval={"rows": [], "time_filter": None, "shift_date": DATE_A, "window_start": None, "window_end": None},
        ),
    )
    facility = SimpleNamespace(pk="facility-snap")
    user = SimpleNamespace(pk="user-snap")
    live_context = SimpleNamespace(time_filter=None)
    state = _state(target_date=DATE_A, local_date=DATE_A, live_context=live_context)

    build_zeitstrom_watch_snapshot(user, facility, state)

    assert calls == [((facility, user), {"context": live_context})]


def test_build_zeitstrom_watch_snapshot_skips_tally_summary_outside_live_context(monkeypatch):
    """Pins x_build_zeitstrom_watch_snapshot__mutmut_9 (``and`` -> ``or``
    zwischen ``state.is_live_tally_context`` und der Schnittmengen-Pruefung):
    ausserhalb des Live-Tally-Kontexts darf ``build_tally_summary`` GAR
    NICHT aufgerufen werden — ``tally_topology`` steht unabhaengig vom
    Live-Status immer in ``component_names``, die Schnittmenge ist also
    (faelschlich) nie leer, wenn die Bedingung ``or`` statt ``and`` waere."""

    _stub_other_components(monkeypatch)
    calls = []
    monkeypatch.setattr(
        zeitstrom_watch_module,
        "build_tally_summary",
        _recording_stub(calls, retval={"rows": []}),
    )
    facility = SimpleNamespace(pk="facility-snap2")
    user = SimpleNamespace(pk="user-snap2")
    live_context = SimpleNamespace(time_filter=None)
    state = _state(target_date=DATE_A, local_date=DATE_B, live_context=live_context)

    build_zeitstrom_watch_snapshot(user, facility, state)

    assert calls == []


# ---------------------------------------------------------------------------
# build_zeitstrom_component_validator — facility/user-Weiterreichung (4 Survivors)
# ---------------------------------------------------------------------------


def test_build_zeitstrom_component_validator_tally_counts_forwards_facility_and_user(monkeypatch):
    """Pins x_build_zeitstrom_component_validator__mutmut_14/15 (facility
    bzw. user durch ``None`` ersetzt im Aufruf von
    ``_tally_counts_projection``)."""

    calls = []
    monkeypatch.setattr(zeitstrom_watch_module, "_tally_counts_projection", _recording_stub(calls))
    facility = SimpleNamespace(pk="f-cv")
    user = SimpleNamespace(pk="u-cv")
    state = _minimal_state()

    build_zeitstrom_component_validator(user, facility, state, "tally_counts")

    assert calls == [((facility, user, state), {"summary": None})]


def test_build_zeitstrom_component_validator_tally_topology_forwards_facility(monkeypatch):
    """Pins x_build_zeitstrom_component_validator__mutmut_25 (facility durch
    ``None`` ersetzt im Aufruf von ``_tally_topology_projection``)."""

    calls = []
    monkeypatch.setattr(zeitstrom_watch_module, "_tally_topology_projection", _recording_stub(calls))
    facility = SimpleNamespace(pk="f-cv2")
    user = SimpleNamespace(pk="u-cv2")
    state = _minimal_state()

    build_zeitstrom_component_validator(user, facility, state, "tally_topology")

    assert calls == [((facility, user, state), {"summary": None})]


def test_build_zeitstrom_component_validator_current_shift_forwards_user(monkeypatch):
    """Pins x_build_zeitstrom_component_validator__mutmut_37 (user durch
    ``None`` ersetzt im Aufruf von ``_current_shift_projection``)."""

    calls = []
    monkeypatch.setattr(zeitstrom_watch_module, "_current_shift_projection", _recording_stub(calls))
    facility = SimpleNamespace(pk="f-cv3")
    user = SimpleNamespace(pk="u-cv3")
    state = _minimal_state()

    build_zeitstrom_component_validator(user, facility, state, "current_shift")

    assert calls == [((facility, user, state), {})]


def test_build_zeitstrom_component_validator_unknown_component_message_is_exact():
    """Pins x_build_zeitstrom_component_validator__mutmut_54 (``raise
    ValueError(f"Unknown Zeitstrom component: {component}")`` -> ``raise
    ValueError(None)``): ``component`` ist zwar an jeder echten Aufrufstelle
    ein Literal, aber die Funktion nimmt IRGENDEINEN String entgegen — ein
    Aufrufer (hier: dieser Test) kann die Fehlermeldung direkt beobachten
    (``exc.args``), sie wird deshalb EXAKT verglichen statt per
    ``pytest.raises(match=...)`` (Teilstring-Suche uebersieht den
    mutmut-Marker; Nachbesserung nach Zweitgutachten: urspruenglich
    faelschlich als ``skip`` eingestuft)."""

    user = SimpleNamespace(pk="u-badcomp")
    facility = SimpleNamespace(pk="f-badcomp")
    state = _minimal_state()

    with pytest.raises(ValueError) as excinfo:
        build_zeitstrom_component_validator(user, facility, state, "unbekannte-komponente")

    assert excinfo.value.args == ("Unknown Zeitstrom component: unbekannte-komponente",)


# ---------------------------------------------------------------------------
# zeitstrom_page_scope_matches — Konstantzeit-Normalisierung (4 Survivors)
# ---------------------------------------------------------------------------


def test_zeitstrom_page_scope_matches_normalizes_malformed_candidates_before_compare(monkeypatch):
    """Pins x_zeitstrom_page_scope_matches__mutmut_1/5/7/8 (``or`` -> ``and``
    zwischen Typ- und Laengenpruefung; Normalisierungswert ``"0" * 64`` ->
    ``None``/``"XX0XX" * 64``/``"0" * 65``): ein fehlgeformter (falsch
    langer, aber typkorrekter) Kandidat MUSS immer auf denselben festen
    64-Zeichen-Wert normalisiert werden, bevor er in
    ``constant_time_compare`` einfliesst — sonst verlaesst ein Aufrufer den
    dokumentierten Konstantzeit-Vergleichspfad (Timing-Seitenkanal-Haertung,
    siehe Docstring der Funktion)."""

    calls = []
    monkeypatch.setattr(
        zeitstrom_watch_module,
        "constant_time_compare",
        _recording_stub(calls, retval=False),
    )
    user = SimpleNamespace(pk="u-scope", role="assistant", is_active=True)
    facility = SimpleNamespace(pk="f-scope")

    result = zeitstrom_page_scope_matches("zu-kurz", user, facility)

    assert result is False
    assert len(calls) == 1
    (candidate_arg, _real_scope_arg), kwargs = calls[0]
    assert kwargs == {}
    assert candidate_arg == "0" * 64

    calls.clear()
    real_scope = build_zeitstrom_page_scope(user, facility)

    zeitstrom_page_scope_matches(real_scope, user, facility)

    (candidate_arg2, _), _ = calls[0]
    assert candidate_arg2 == real_scope, "Sanity: wohlgeformte Kandidaten bleiben unveraendert."


# ---------------------------------------------------------------------------
# build_zeitstrom_tally_topology_validator — tally_summary-Weiterreichung (2 Survivors)
# ---------------------------------------------------------------------------


def test_build_zeitstrom_tally_topology_validator_forwards_tally_summary(monkeypatch):
    """Pins x_build_zeitstrom_tally_topology_validator__mutmut_5/10
    (``tally_summary=tally_summary`` -> ``tally_summary=None`` bzw. Keyword
    gestrichen): der vom Aufrufer bereits berechnete Summary-Wert (z. B.
    derselbe, mit dem das Partial-HTML gerendert wurde) muss unveraendert an
    ``build_zeitstrom_component_validator`` weitergereicht werden, sonst
    kann der ETag vom tatsaechlich gerenderten Inhalt abweichen."""

    calls = []
    monkeypatch.setattr(
        zeitstrom_watch_module,
        "build_zeitstrom_component_validator",
        _recording_stub(calls, retval='"stub-etag"'),
    )
    user = SimpleNamespace(pk="u-ttv")
    facility = SimpleNamespace(pk="f-ttv")
    state = _minimal_state()
    sentinel_summary = object()

    result = build_zeitstrom_tally_topology_validator(user, facility, state, tally_summary=sentinel_summary)

    assert result == '"stub-etag"'
    assert calls == [((user, facility, state, "tally_topology"), {"tally_summary": sentinel_summary})]
