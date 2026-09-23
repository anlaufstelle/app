"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.audit.chain``
(Batch ``chain-a``).

Quelle: Vollmutation #2174, Batch ``chain-a``
(``welle-l-0.99/reports/m-scratch/batch-chain-a.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-chain-a/triage-099-chain-a.json``).
65 Survivor-Mutanten aus vier Funktionen: ``_canonical_payload`` (25),
``_system_chain_latest_link_raw`` (19), ``verify_chain`` (13),
``_checkpoint_boundaries`` (8). Batch ``chain-b`` (``assign_chain_fields``,
``_canonical_timestamp``, ``_latest_link``, ``compute_entry_hash``,
``advisory_lock_name``/``advisory_lock_id``) ist ein SEPARATER Auftrag und
wird hier NICHT angefasst.

Die HMAC-Kette macht den append-only ``AuditLog`` tamper-evident (Refs #1070):
jede Zeile traegt ``entry_hash = HMAC(key, prev_hash || canonical(row))``. Die
hier gepinnten Survivor-Klassen:

1. **Kanonisierung** (``_canonical_payload``): jede Dict-Schluessel-Umbenennung
   oder Werte-Vertauschung aendert den Hash-Input lautlos — bestehende,
   VOR der Mutation geschriebene Zeilen wuerden beim Verify als manipuliert
   gelten (falscher Tamper-Alarm) bzw. eine Manipulation, die zufaellig den
   NEUEN falschen Kanon trifft, bliebe unentdeckt. Gepinnt per unabhaengiger
   Golden-Rekonstruktion (wie ``test_mutation_pins_099_audit_hwm.py`` fuer
   ``_sign``) — ein in sich konsistenter Schreib/Lese-Roundtrip mit DERSELBEN
   Mutante deckt eine falsche Kanonisierung NICHT auf.
2. **System-Ketten-Read** (``_system_chain_latest_link_raw``): die R15-
   Reentrancy-Flagge und die transiente RLS-Elevation (``app.is_super_admin``)
   sind sicherheitskritisch. Ein GROSS/klein-Unterschied im GUC-*Namen* ist
   Postgres-seitig irrelevant (empirisch verifiziert: ``set_config``/
   ``current_setting`` falten Parameternamen case-insensitiv, unabhaengig von
   der beim Setzen verwendeten Schreibweise) — aber der GUC-*Wert* wird von
   der RLS-Policy (Migration 0085) exakt (``= 'true'``) verglichen, ein
   ``'TRUE'`` bricht die Elevation lautlos.
3. **Verifikation** (``verify_chain``): der ``(timestamp, id)``-Tiebreak ist
   fuer Zeilen mit identischem Zeitstempel NICHT aequivalent zu ``timestamp``
   allein (Sortier-/pk-Tiebreak-Mutanten sind nie aequivalent);
   ``continue``/``break`` bei einer unverketteten Bestandszeile entscheidet,
   ob der Scan nach einer Luecke WEITERLAEUFT (richtig) oder lautlos ABBRICHT
   (Tampering in allen Folgezeilen bliebe unentdeckt).
4. **Checkpoint-Grenzen** (``_checkpoint_boundaries``): dieselbe
   ``continue``/``break``-Unterscheidung bei unauthentifizierten/gefaelschten
   Checkpoints, plus der ``boundary_hash``-Schluessel-Lookup und der
   ``prev_hash``-Fallback fuer den allerersten Checkpoint einer Kette.

``pytest.raises(match=...)`` / ``"x" in text`` sind Teilstring-Suchen und
uebersehen den mutmut-Marker ``XX...XX`` (er UMSCHLIESST den Text, die
gesuchte Teilzeichenkette bleibt drin) ebenso wie Gross-/Kleinschreibung nach
``.lower()``-Normalisierung — deshalb vergleichen die Text-Pins hier exakt.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest
from django.db import connection, transaction
from django.db.models.query import QuerySet
from django.utils import timezone

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.audit import chain as chain_mod
from core.services.audit.chain import compute_entry_hash, verify_chain
from core.services.audit.hwm import HighWaterMarkStore
from core.services.system import bypass_replication_triggers

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

# WICHTIG: kein Modul-Import aus ``tests.test_rls_functional`` — jenes Modul
# deaktiviert sich unter ``MUTANT_UNDER_TEST`` (Refs #1388, DatabaseBlocker-Leak
# im mutmut-Mehrphasenlauf) beim Import selbst komplett (``allow_module_level=
# True``-Selbstabschaltung). Ein Import auf Modulebene wuerde diese
# Selbstabschaltung in DIESE Datei hineinziehen und JEDEN Test hier unter
# JEDEM Mutanten als nicht ausgefuehrt erscheinen lassen (empirisch
# verifiziert: "no tests collected"). Die RLS-durchsetzende Rollen-Fixture
# (``rls_test_role``) bleibt deshalb fuer Mutation-Pins tabu — s.
# ``TestSystemChainLatestLinkRawElevationValueCase`` unten fuer die
# Spy-Alternative.


def _tamper(pk, **fields):
    """Mutiert eine AuditLog-Zeile direkt (umgeht ``save()``-Guard UND den
    DB-Immutability-Trigger) — modelliert den Owner-/``replica``-Vektor
    (wie ``test_audit_chain.py::_tamper``, hier lokal, um Bestandsdateien
    nicht anzufassen)."""
    with transaction.atomic(), bypass_replication_triggers():
        AuditLog.objects.filter(pk=pk).update(**fields)


def _capture_order_by(call) -> list[tuple[str, tuple]]:
    """Erfasst jeden ``QuerySet.order_by``-Aufruf waehrend ``call()`` laeuft,
    als ``(Modellname, args)`` in Aufrufreihenfolge (Muster
    ``test_mutation_pins_099_facility_export_b.py``, hier lokal — kein Import
    aus jener Datei noetig).

    Planunabhaengig: prueft das tatsaechlich an ``order_by()`` uebergebene
    Argument-Tupel, NICHT die von Postgres zurueckgelieferte Zeilenreihenfolge.
    Letztere ist bei Gleichstand ohne entscheidendes Kriterium
    implementierungsabhaengig und — empirisch verifiziert beim Kill-Nachweis
    fuer ``x_verify_chain__mutmut_7`` — NICHT verlaesslich beobachtbar (derselbe
    Mutant lieferte in wiederholten Laeufen sowohl ``ok=True`` als auch
    ``ok=False``)."""
    calls: list[tuple[str, tuple]] = []
    original = QuerySet.order_by

    def _spy(self, *args, **kwargs):
        calls.append((self.model.__name__, args))
        return original(self, *args, **kwargs)

    with mock.patch.object(QuerySet, "order_by", _spy):
        call()
    return calls


def _last_order_by(calls: list[tuple[str, tuple]], model_name: str) -> tuple | None:
    """Letztes ``order_by``-Argument-Tupel fuer ``model_name`` aus ``calls``."""
    matches = [args for name, args in calls if name == model_name]
    return matches[-1] if matches else None


# ---------------------------------------------------------------------------
# _canonical_payload — Golden-Rekonstruktion der Kanonisierung
# ---------------------------------------------------------------------------


class TestCanonicalPayloadGoldenReconstruction:
    def test_full_payload_matches_an_independently_built_json_string(self, facility, staff_user, admin_user):
        """Killt 24 der 25 ``_canonical_payload``-Survivors in EINER Referenzwert-
        Assertion (alles ausser ``__mutmut_26``, s. Klasse darunter):

        Dict-Schluessel-Umbenennungen (``XX...XX``-Marker/GROSS): ``__mutmut_2``/
        ``_3`` (``timestamp``), ``_5``/``_6`` (``action``), ``_8``/``_9``
        (``user_id``), ``_10``/``_11`` (``facility_id``), ``_12``/``_13``
        (``target_type``), ``_14``/``_15`` (``target_id``), ``_16``/``_17``
        (``ip_address``), ``_19``/``_20`` (``detail``), ``_31``/``_32``
        (``actor_id``) — ``sort_keys=True`` sortiert jede umbenannte Schluessel-
        Variante an eine ANDERE alphabetische Position (Grossbuchstaben < 'a'
        in ASCII), die gesamte Zeichenkette weicht also selbst dann ab, wenn nur
        EIN Schluessel betroffen ist.

        Werte-Vertauschungen: ``__mutmut_4`` (``_canonical_timestamp(row.timestamp)``
        -> ``_canonical_timestamp(None)``, liefert immer ``""``), ``__mutmut_7``
        (``str(row.action)`` -> ``str(None)``, liefert immer ``"None"``).

        ``json.dumps``-Parameter: ``__mutmut_35`` (``separators=None`` -> Default
        mit Leerzeichen), ``__mutmut_39`` (``separators`` faellt komplett weg,
        gleicher Effekt), ``__mutmut_42`` (Item-Trenner ``","`` -> ``"XX,XX"``),
        ``__mutmut_43`` (Key-Trenner ``":"`` -> ``"XX:XX"``).

        Der Vergleichswert wird UNABHAENGIG von ``_canonical_payload`` aus der
        dokumentierten Kanonisierung aufgebaut (sortierte Schluessel, kompakte
        Trenner, ``default=str`` fuer UUIDs) — ein in sich konsistenter
        Schreib/Lese-Roundtrip mit DERSELBEN Mutante wuerde jede dieser
        Abweichungen NICHT aufdecken, weil Schreiben und Lesen sich intern
        noch einig waeren.
        """
        ts = datetime(2026, 3, 4, 5, 6, 7, 890123, tzinfo=UTC)
        row = AuditLog(
            facility=facility,
            user=staff_user,
            actor=admin_user,
            action=AuditLog.Action.CLIENT_UPDATE,
            target_type="Client",
            target_id="target-xyz",
            detail={"zeta": 1, "alpha": [3, 2, 1]},
            ip_address="203.0.113.7",
            timestamp=ts,
        )

        expected = json.dumps(
            {
                "timestamp": ts.isoformat(),
                "action": "client_update",
                "user_id": staff_user.pk,
                "facility_id": facility.pk,
                "target_type": "Client",
                "target_id": "target-xyz",
                "ip_address": "203.0.113.7",
                "detail": {"zeta": 1, "alpha": [3, 2, 1]},
                "actor_id": admin_user.pk,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

        assert chain_mod._canonical_payload(row) == expected


class TestCanonicalPayloadActorIdDefault:
    def test_a_row_without_a_loaded_actor_attribute_does_not_raise(self):
        """Killt ``__mutmut_26``: ``getattr(row, "actor_id", None)`` verliert das
        3. Argument -> ``getattr(row, "actor_id")`` (2-Arg-Form). Fuer ein echtes,
        aus der DB geladenes ``AuditLog`` faellt das nie auf (das Feld existiert
        immer) — der Docstring dokumentiert die Funktion aber ausdruecklich als
        „robust gegen In-Memory-Rows ohne geladenes actor-Attribut" (z.B. ein
        handgebautes Duck-Typing-Objekt ohne dieses Feld). Ohne den Default
        wirft die 2-Arg-Form ``AttributeError`` statt ``None`` zurueckzugeben —
        die Kanonisierung (und damit jeder Hash-Write/-Verify ueber einen
        solchen Row-Stellvertreter) wuerde hart abbrechen statt den Actor als
        „nicht gesetzt" zu behandeln."""

        class _RowWithoutActor:
            timestamp = None
            action = "login"
            user_id = None
            facility_id = None
            target_type = ""
            target_id = ""
            ip_address = None
            detail = {}

        row = _RowWithoutActor()
        assert not hasattr(row, "actor_id")

        payload = chain_mod._canonical_payload(row)

        assert "actor_id" not in json.loads(payload)


# ---------------------------------------------------------------------------
# _system_chain_latest_link_raw — R15 Reentrancy-Guard + RLS-Elevation
# ---------------------------------------------------------------------------


class TestSystemChainLatestLinkRawReentrancyDefault:
    def setup_method(self):
        # Harte Assertion statt eines bedingten Auslassens (Pin-Dateien
        # duerfen Tests nicht bedingt uebergehen): die Suite laeuft
        # ausschliesslich auf PostgreSQL (AuditLog-Kette/Trigger/RLS sind
        # Postgres-spezifisch) — ein anderer Vendor waere eine
        # Fehlkonfiguration der Testumgebung und soll den Pin laut
        # abbrechen statt ihn stumm zu deaktivieren.
        assert connection.vendor == "postgresql", "AuditLog-Kette/Trigger/RLS erfordert PostgreSQL"

    def test_a_fresh_thread_local_state_does_not_trip_the_guard(self):
        """Killt ``__mutmut_6`` und ``__mutmut_9`` am Reentrancy-Check
        ``getattr(_system_read_active, "flag", False)``:

        - ``__mutmut_6`` entfernt den Default komplett (2-Arg-``getattr``) —
          fehlt das ``flag``-Attribut (allererster Aufruf in einem frischen
          Thread), wirft das ``AttributeError`` statt ``False`` zu liefern.
        - ``__mutmut_9`` aendert den Default zu ``True`` — derselbe frische
          Aufruf loest dann SOFORT den ``RuntimeError`` der geschachtelten
          Elevation aus, obwohl gar keine Verschachtelung vorliegt.

        ``threading.local()`` traegt das Attribut erst NACH dem ersten Lauf
        (im ``finally`` auf ``False`` gesetzt); ein vorheriger Testlauf im
        selben Thread haette es also bereits gesetzt. ``del`` stellt den
        „frischen" Zustand explizit her, statt sich auf Testreihenfolge zu
        verlassen.
        """
        if hasattr(chain_mod._system_read_active, "flag"):
            del chain_mod._system_read_active.flag
        try:
            # Muss ohne Exception durchlaufen (Rueckgabewert ist fuer diesen
            # Survivor irrelevant — nur die Nicht-Exception zaehlt).
            chain_mod._system_chain_latest_link_raw()
        finally:
            chain_mod._system_read_active.flag = False


class TestSystemChainLatestLinkRawReentrancyMessage:
    def setup_method(self):
        # Harte Assertion statt eines bedingten Auslassens (Pin-Dateien
        # duerfen Tests nicht bedingt uebergehen): die Suite laeuft
        # ausschliesslich auf PostgreSQL (AuditLog-Kette/Trigger/RLS sind
        # Postgres-spezifisch) — ein anderer Vendor waere eine
        # Fehlkonfiguration der Testumgebung und soll den Pin laut
        # abbrechen statt ihn stumm zu deaktivieren.
        assert connection.vendor == "postgresql", "AuditLog-Kette/Trigger/RLS erfordert PostgreSQL"

    def test_the_reentrancy_error_message_is_exact(self):
        """Killt acht Text-Survivors der ``RuntimeError``-Meldung (drei
        konkatenierte String-Literale): ``__mutmut_11``/``_12`` (Segment 1
        XX-umschlossen/klein), ``__mutmut_14``/``_15``/``_16`` (Segment 2
        XX-umschlossen/klein/GROSS), ``__mutmut_17``/``_18``/``_19``
        (Segment 3 XX-umschlossen/klein/GROSS).

        Der bestehende Test ``test_audit_chain.py::test_reentrancy_guard_fails_loud``
        prueft nur ``pytest.raises(match="geschachtelte")`` — eine Teilstring-
        Suche, die den mutmut-``XX...XX``-Marker uebersieht (er umschliesst den
        Text, "geschachtelte" bleibt als Teilstring drin) und durch
        ``re.search`` ohnehin gross-/kleinschreibungsempfindlich, aber nicht
        gegen eine komplette GROSS-Variante gehaertet ist. Hier exakter
        Vergleich der vollstaendigen Meldung.
        """
        chain_mod._system_read_active.flag = True
        try:
            with pytest.raises(RuntimeError) as exc:
                chain_mod._system_chain_latest_link_raw()
        finally:
            chain_mod._system_read_active.flag = False

        assert str(exc.value) == (
            "geschachtelte System-Ketten-Elevation (R15): das angehobene "
            "app.is_super_admin-Fenster darf ausschliesslich den einen "
            "Raw-Cursor-Read umschliessen — keine weitere (ORM-)Query."
        )


class TestSystemChainLatestLinkRawFlagLifecycle:
    def setup_method(self):
        # Harte Assertion statt eines bedingten Auslassens (Pin-Dateien
        # duerfen Tests nicht bedingt uebergehen): die Suite laeuft
        # ausschliesslich auf PostgreSQL (AuditLog-Kette/Trigger/RLS sind
        # Postgres-spezifisch) — ein anderer Vendor waere eine
        # Fehlkonfiguration der Testumgebung und soll den Pin laut
        # abbrechen statt ihn stumm zu deaktivieren.
        assert connection.vendor == "postgresql", "AuditLog-Kette/Trigger/RLS erfordert PostgreSQL"

    def test_a_nested_call_during_the_active_window_is_rejected_and_a_later_call_is_allowed(self, monkeypatch):
        """Killt ``__mutmut_42``/``_43`` (``_system_read_active.flag = True`` ->
        ``None``/``False`` VOR dem Raw-Select).

        Verhaltens-Pin statt Schreibhistorie-Spy (nach Zweitgutachten):
        ``__mutmut_65`` (``flag = False`` -> ``None`` im
        ``finally`` NACHHER) wurde aus diesem Test entfernt und als
        ``equivalent`` eingestuft — die Flagge wird produktiv AUSSCHLIESSLICH
        booleschen in ``if getattr(_system_read_active, "flag", False):``
        gelesen, ``None`` und ``False`` sind dort identisch falsy; ein Spy auf
        den GESCHRIEBENEN Wert pinnt Implementierungstext statt Verhalten und
        blockiert ein kuenftiges Refactoring, das denselben Vertrag anders
        implementiert (s. Abschnitt 3 des Berichts).

        Stattdessen wird NUR die dokumentierte Schutzwirkung selbst geprueft:
        ein Aufruf, der WAEHREND des aktiven Elevation-Fensters (nach dem
        eigenen ``flag = True``, waehrend des eigenen Cursor-Gebrauchs)
        geschachtelt erneut in die Funktion eintritt, MUSS mit
        ``RuntimeError`` abgelehnt werden — und ein GEWOEHNLICHER (nicht
        geschachtelter) Folgeaufruf NACH Abschluss muss wieder gelingen. Der
        geschachtelte Aufruf wird ueber einen Spy auf ``connection.cursor``
        injiziert (feuert exakt einmal, genau BEVOR der aeussere Aufruf
        seinen eigenen Cursor bekommt — also mitten im echten Fenster).

        Original: der innere Aufruf sieht ``flag=True`` (vom aeusseren Aufruf
        gesetzt) und wirft sofort den Reentrancy-``RuntimeError`` — der
        aeussere Aufruf laeuft danach unbeeinflusst weiter. Mit
        ``__mutmut_42``/``_43`` sieht der innere Aufruf stattdessen
        ``flag=None``/``False`` (falsy) und laeuft VOLLSTAENDIG durch (eigene
        Elevation, eigener Read, eigenes Reset) — kein ``RuntimeError``, der
        ``pytest.raises``-Block im Test schlaegt fehl.
        """
        original_cursor = connection.cursor
        triggered = {"once": False}

        def cursor_with_injected_reentrancy(*args, **kwargs):
            if not triggered["once"]:
                triggered["once"] = True
                with pytest.raises(RuntimeError, match="geschachtelte"):
                    chain_mod._system_chain_latest_link_raw()
            return original_cursor(*args, **kwargs)

        monkeypatch.setattr(connection, "cursor", cursor_with_injected_reentrancy)

        # Laeuft trotz des injizierten (abgelehnten) geschachtelten Aufrufs
        # normal durch — die aeussere Elevation bleibt unbeeinflusst.
        chain_mod._system_chain_latest_link_raw()

        # Nach Abschluss (flag wieder falsy, ob False oder None) ist ein
        # GEWOEHNLICHER Folgeaufruf erlaubt — kein RuntimeError.
        monkeypatch.setattr(connection, "cursor", original_cursor)
        chain_mod._system_chain_latest_link_raw()


class TestSystemChainLatestLinkRawElevationValueCase:
    def setup_method(self):
        # Harte Assertion statt eines bedingten Auslassens (Pin-Dateien
        # duerfen Tests nicht bedingt uebergehen): die Suite laeuft
        # ausschliesslich auf PostgreSQL (AuditLog-Kette/Trigger/RLS sind
        # Postgres-spezifisch) — ein anderer Vendor waere eine
        # Fehlkonfiguration der Testumgebung und soll den Pin laut
        # abbrechen statt ihn stumm zu deaktivieren.
        assert connection.vendor == "postgresql", "AuditLog-Kette/Trigger/RLS erfordert PostgreSQL"

    def test_the_guc_statements_have_the_right_shape_and_the_elevation_value_is_exactly_lowercase_true(
        self, monkeypatch, staff_user
    ):
        """Killt ausschliesslich ``__mutmut_55`` (**SICHERHEITSKRITISCH**):
        ``set_config('app.is_super_admin', 'true', true)`` -> ``SET_CONFIG(
        'APP.IS_SUPER_ADMIN', 'TRUE', TRUE)``.

        Semantischer statt Text-Pin (nach Zweitgutachten):
        ``__mutmut_46``/``_47``/``_54``/``_63`` (reine SQL-Schluesselwort-/
        Parameternamen-Gross-Kleinschreibung auf den drei GUC-Anweisungen)
        sind Postgres-seitig FUNKTIONAL aequivalent (empirisch verifiziert:
        ``set_config``/``current_setting`` falten sowohl Funktionsnamen als
        auch benutzerdefinierte GUC-Parameternamen case-insensitiv,
        unabhaengig von der beim Setzen/Lesen verwendeten Schreibweise) und
        werden NICHT mehr gepinnt — ein reiner Text-Vergleich haette
        Implementierungstext statt Verhalten gepinnt und ein kuenftiges
        Refactoring (z. B. andere Schreibweise, Formatierung) unnoetig
        blockiert. Der Schwester-Batch ``chain-b`` fuehrt dieselben Faelle
        bereits korrekt als ``equivalent``.

        Die Pruefung unten ist deshalb zweigeteilt:

        1. **Struktur** (Reihenfolge/Anzahl der drei Anweisungen: Lesen des
           Vorwerts, Elevieren, Wiederherstellen) — auf dem NORMALISIERTEN
           Text (``" ".join(sql.split()).lower()``, kollabiert Whitespace UND
           Gross-/Kleinschreibung), bewusst OHNE Buchstabenfall-Vergleich.
        2. **Semantisch entscheidender Wert** — der von der Elevations-
           Anweisung GESETZTE Wert wird bewusst NICHT normalisiert geprueft:
           die RLS-Policy (Migration 0085) vergleicht ``current_setting(...)
           = 'true'`` als EXAKTEN String; ``'TRUE'`` erfuellt diesen
           Vergleich nachweislich NICHT (empirisch mit einer echten
           NOSUPERUSER/NOBYPASSRLS-Rolle verifiziert: der Vergleich liefert
           dort ``false`` statt ``true``). Eine end-to-end-Pruefung MIT
           echter RLS-Durchsetzung ist in diesem Baum nicht moeglich (die
           einzige RLS-durchsetzende Rollen-Fixture
           ``tests.test_rls_functional.rls_test_role`` schaltet sich unter
           ``MUTANT_UNDER_TEST`` selbst komplett ab, Refs #1388,
           DatabaseBlocker-Leak im mutmut-Mehrphasenlauf — ein Pin, der sie
           importiert, wuerde unter JEDEM Mutanten dieser Datei
           uebersprungen, verifiziert: „no tests collected"; die
           Default-Testverbindung selbst laeuft zudem als Superuser/
           BYPASSRLS und macht RLS dort wirkungslos). Stattdessen wird der
           TATSAECHLICH ausgefuehrte SQL-Text ueber einen Spy auf
           ``connection.cursor().execute`` erfasst.
        """
        executed: list[str] = []
        original_cursor = connection.cursor

        def spying_cursor(*args, **kwargs):
            cur = original_cursor(*args, **kwargs)
            original_execute = cur.execute

            def spy_execute(sql, params=None):
                executed.append(sql)
                if params is None:
                    return original_execute(sql)
                return original_execute(sql, params)

            cur.execute = spy_execute
            return cur

        monkeypatch.setattr(connection, "cursor", spying_cursor)

        audit_event(AuditLog.Action.SYSTEM_VIEW, user=staff_user, facility=None)

        normalized = [" ".join(sql.split()).lower() for sql in executed]

        read_form = "select current_setting('app.is_super_admin', true)"
        restore_form = "select set_config('app.is_super_admin', %s, true)"

        def _is_elevate_form(s: str) -> bool:
            return s.startswith("select set_config('app.is_super_admin',") and s.endswith(", true)") and "%s" not in s

        read_idx = next((i for i, s in enumerate(normalized) if s == read_form), None)
        elevate_idx = next((i for i, s in enumerate(normalized) if _is_elevate_form(s)), None)
        restore_idx = next((i for i, s in enumerate(normalized) if s == restore_form), None)

        assert read_idx is not None, "GUC-Lesen des Vorwerts nicht gefunden"
        assert elevate_idx is not None, "GUC-Elevation nicht gefunden"
        assert restore_idx is not None, "GUC-Wiederherstellung nicht gefunden"
        assert read_idx < elevate_idx < restore_idx, "Reihenfolge Lesen -> Elevieren -> Wiederherstellen verletzt"
        assert normalized.count(read_form) == 1
        assert sum(1 for s in normalized if _is_elevate_form(s)) == 1
        assert normalized.count(restore_form) == 1

        # SICHERHEITSKRITISCH, bewusst NICHT normalisiert (s. Docstring):
        elevate_raw = executed[elevate_idx]
        assert "'true'" in elevate_raw
        assert "'TRUE'" not in elevate_raw


# ---------------------------------------------------------------------------
# verify_chain
# ---------------------------------------------------------------------------


class TestVerifyChainOrderTiebreak:
    def test_the_order_by_call_carries_the_id_tiebreak(self, facility):
        """Killt ``__mutmut_7``: ``.order_by("timestamp", "id")`` ->
        ``.order_by("timestamp", )`` (id-Tiebreak entfernt).

        Planunabhaengige Introspektion (Spy auf ``QuerySet.order_by``,
        Muster ``test_mutation_pins_099_facility_export_b.py``
        ``_capture_order_by``/``_last_order_by``) statt Zeilenreihenfolge:
        eine Zeilenreihenfolge-Probe (zwei Zeilen mit identischem Zeitstempel,
        physisch umgekehrt zur id-Ordnung eingefuegt) erwies sich HIER beim
        eigenen Kill-Nachweis als NICHT verlaesslich — derselbe Mutant lieferte
        in wiederholten Laeufen sowohl ``ok=True`` als auch ``ok=False``, weil
        Postgres bei einem winzigen Zwei-Zeilen-Sequential-Scan ohne
        entscheidendes Sortierkriterium KEINE stabile Reihenfolge garantiert
        (deckungsgleich mit der Nachbesserung in
        ``test_mutation_pins_099_facility_export_b.py``, dort dieselbe
        Falsch-Gruen-Symptomatik). Die Introspektion prueft stattdessen direkt
        das an ``order_by()`` uebergebene Argument-Tupel — deterministisch,
        unabhaengig vom Postgres-Ausfuehrungsplan.
        """
        calls = _capture_order_by(lambda: verify_chain(facility))
        assert _last_order_by(calls, "AuditLog") == ("timestamp", "id")


class TestVerifyChainLastEntryHashInitialisation:
    def setup_method(self):
        # Harte Assertion statt eines bedingten Auslassens (Pin-Dateien
        # duerfen Tests nicht bedingt uebergehen): die Suite laeuft
        # ausschliesslich auf PostgreSQL (AuditLog-Kette/Trigger/RLS sind
        # Postgres-spezifisch) — ein anderer Vendor waere eine
        # Fehlkonfiguration der Testumgebung und soll den Pin laut
        # abbrechen statt ihn stumm zu deaktivieren.
        assert connection.vendor == "postgresql", "AuditLog-Kette/Trigger/RLS erfordert PostgreSQL"

    def test_an_empty_chain_reports_last_entry_hash_none(self, facility):
        """Killt ``__mutmut_16``: ``prev_entry: str | None = None`` ->
        ``= ""``.

        Der Docstring von ``ChainResult.last_entry_hash`` dokumentiert den
        Vertrag ausdruecklich: „``None`` bei leerer Kette". Bei GAR KEINEN
        Zeilen laeuft die Schleife nie, ``prev_entry`` behaelt seinen
        Initialwert und wird unveraendert zurueckgegeben — der einzige Ort,
        an dem der Initialwert ueberhaupt sichtbar wird (in jeder anderen
        Situation ueberschreibt die Schleife ihn). ``is None`` statt eines
        blossen Falsy-Checks haertet den Vertrag exakt (analog zum
        etablierten Muster in ``test_mutation_followup_audit_chain.py`` fuer
        ``ok is False``).
        """
        assert not AuditLog.objects.filter(facility=facility).exists()

        result = verify_chain(facility)

        assert result.rows_checked == 0
        assert result.last_entry_hash is None


class TestVerifyChainLegacyRowResetValue:
    def setup_method(self):
        # Harte Assertion statt eines bedingten Auslassens (Pin-Dateien
        # duerfen Tests nicht bedingt uebergehen): die Suite laeuft
        # ausschliesslich auf PostgreSQL (AuditLog-Kette/Trigger/RLS sind
        # Postgres-spezifisch) — ein anderer Vendor waere eine
        # Fehlkonfiguration der Testumgebung und soll den Pin laut
        # abbrechen statt ihn stumm zu deaktivieren.
        assert connection.vendor == "postgresql", "AuditLog-Kette/Trigger/RLS erfordert PostgreSQL"

    def test_a_chain_ending_on_an_unlinked_legacy_row_reports_last_entry_hash_none(self, facility, staff_user):
        """Killt ``__mutmut_20``: ``prev_entry = None`` (Reset im
        Bestandszeilen-Zweig) -> ``= ""``.

        Eine Kette, deren LETZTE (juengste) Zeile eine unverkettete
        Bestandszeile ist (``entry_hash`` leer, vor ``backfill_audit_chain``),
        endet die Schleife mit dem Reset-Wert als ``prev_entry`` — der einzige
        Fall ausser einer komplett leeren Kette, in dem der Initial-/Reset-Wert
        (statt eines echten Zeilen-Hashes) den Rueckgabewert bestimmt.
        Original liefert ``last_entry_hash=None`` (Vertrag: „None bei leerer
        [restlicher] Kette"), der Mutant ``""``.
        """
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        trailing = audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        _tamper(trailing.pk, prev_hash="", entry_hash="")

        result = verify_chain(facility)

        assert result.ok
        assert result.last_entry_hash is None


class TestVerifyChainLegacyRowDoesNotAbortTheScan:
    def setup_method(self):
        # Harte Assertion statt eines bedingten Auslassens (Pin-Dateien
        # duerfen Tests nicht bedingt uebergehen): die Suite laeuft
        # ausschliesslich auf PostgreSQL (AuditLog-Kette/Trigger/RLS sind
        # Postgres-spezifisch) — ein anderer Vendor waere eine
        # Fehlkonfiguration der Testumgebung und soll den Pin laut
        # abbrechen statt ihn stumm zu deaktivieren.
        assert connection.vendor == "postgresql", "AuditLog-Kette/Trigger/RLS erfordert PostgreSQL"

    def test_tampering_after_a_legacy_gap_is_still_detected(self, facility, staff_user):
        """Killt ``__mutmut_21`` (KRITISCH): ``prev_entry = None; continue`` ->
        ``prev_entry = None; break`` im Bestandszeilen-Zweig.

        Original: eine unverkettete Bestandszeile wird uebersprungen, die
        Pruefung setzt an der NAECHSTEN Zeile neu an ("Kette ab der naechsten
        Zeile neu ansetzen", Docstring). Mutant: der komplette Scan bricht
        SOFORT bei der ersten Bestandszeile ab — jede Manipulation an JEDER
        Zeile DANACH bliebe unentdeckt. Aufbau: legacy (nachtraeglich entkettet)
        -> restart (Kettenneustart, korrekt gebildet) -> victim (danach
        In-Place manipuliert). Original erkennt die Manipulation an ``victim``;
        der Mutant meldet faelschlich ``ok=True``, weil er nach ``legacy`` nie
        mehr weiterliest.
        """
        legacy = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        _tamper(legacy.pk, prev_hash="", entry_hash="")

        restart = audit_event(AuditLog.Action.VIEW_QUALIFIED, user=staff_user, facility=facility)
        victim = audit_event(
            AuditLog.Action.CLIENT_UPDATE,
            user=staff_user,
            facility=facility,
            detail={"changed_fields": ["notes"]},
        )
        assert restart.pk  # nur zur Lesbarkeit der Aufbaureihenfolge

        # Baseline: trotz Luecke intakt (kein echter Bruch vorhanden).
        assert verify_chain(facility).ok

        _tamper(victim.pk, detail={"changed_fields": ["evil"]})

        result = verify_chain(facility)
        assert not result.ok
        assert result.first_break_id == str(victim.pk)


class TestVerifyChainHashMismatchBranch:
    def setup_method(self):
        # Harte Assertion statt eines bedingten Auslassens (Pin-Dateien
        # duerfen Tests nicht bedingt uebergehen): die Suite laeuft
        # ausschliesslich auf PostgreSQL (AuditLog-Kette/Trigger/RLS sind
        # Postgres-spezifisch) — ein anderer Vendor waere eine
        # Fehlkonfiguration der Testumgebung und soll den Pin laut
        # abbrechen statt ihn stumm zu deaktivieren.
        assert connection.vendor == "postgresql", "AuditLog-Kette/Trigger/RLS erfordert PostgreSQL"

    def test_reports_exact_status_count_and_reason(self, facility, staff_user):
        """Killt drei Survivors am In-Place-Tamper-Zweig:

        - ``__mutmut_37``: ``ChainResult(False, ...)`` -> ``ChainResult(None, ...)``
          (``ok`` wird ``None`` statt ``False``; beide sind falsy, ``is False``
          haertet die exakte Semantik — analog zu
          ``test_mutation_followup_audit_chain.py::test_middle_deletion_reports_ok_false``,
          hier fuer den HASH-MISMATCH- statt den Linkage-Zweig).
        - ``__mutmut_38``: der ``checked``-Zaehler wird zu ``None`` statt der
          tatsaechlichen Zeilenzahl.
        - ``__mutmut_47``/``_48``: die ``reason`` wird ``XX...XX``-umschlossen
          bzw. GROSS — ``test_audit_chain.py`` prueft nur
          ``"hash" in result.reason.lower()``, was beide Varianten uebersieht
          (der Teilstring "hash" bleibt in "XXentry_hash..." erhalten;
          ``.lower()`` normalisiert die GROSS-Variante von selbst).
        """
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        victim = audit_event(
            AuditLog.Action.CLIENT_UPDATE,
            user=staff_user,
            facility=facility,
            detail={"changed_fields": ["notes"]},
        )
        _tamper(victim.pk, detail={"changed_fields": ["evil"]})

        result = verify_chain(facility)

        assert result.ok is False
        assert result.rows_checked == 3
        assert result.reason == "entry_hash mismatch (in-place tampering)"
        assert result.first_break_id == str(victim.pk)


class TestVerifyChainLinkageBreakBranch:
    def setup_method(self):
        # Harte Assertion statt eines bedingten Auslassens (Pin-Dateien
        # duerfen Tests nicht bedingt uebergehen): die Suite laeuft
        # ausschliesslich auf PostgreSQL (AuditLog-Kette/Trigger/RLS sind
        # Postgres-spezifisch) — ein anderer Vendor waere eine
        # Fehlkonfiguration der Testumgebung und soll den Pin laut
        # abbrechen statt ihn stumm zu deaktivieren.
        assert connection.vendor == "postgresql", "AuditLog-Kette/Trigger/RLS erfordert PostgreSQL"

    def test_reports_exact_count_and_reason(self, facility, staff_user):
        """Killt zwei Survivors am Verkettungsbruch-Zweig:

        - ``__mutmut_61``: der ``checked``-Zaehler wird zu ``None``.
        - ``__mutmut_70``: die ``reason`` wird ``XX...XX``-umschlossen —
          ``test_audit_chain.py`` prueft nur
          ``"linkage" in result.reason.lower() or "deleted" in ...`` (Teilstrings
          bleiben im umschlossenen Text erhalten).
        """
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        middle = audit_event(AuditLog.Action.VIEW_QUALIFIED, user=staff_user, facility=facility)
        tail = audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)

        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=middle.pk).delete()

        result = verify_chain(facility)

        assert result.ok is False
        assert result.rows_checked == 2
        assert result.reason == "prev_hash linkage break (row deleted/inserted)"
        assert result.first_break_id == str(tail.pk)


class TestVerifyChainHwmBranchResult:
    def setup_method(self):
        # Harte Assertion statt eines bedingten Auslassens (Pin-Dateien
        # duerfen Tests nicht bedingt uebergehen): die Suite laeuft
        # ausschliesslich auf PostgreSQL (AuditLog-Kette/Trigger/RLS sind
        # Postgres-spezifisch) — ein anderer Vendor waere eine
        # Fehlkonfiguration der Testumgebung und soll den Pin laut
        # abbrechen statt ihn stumm zu deaktivieren.
        assert connection.vendor == "postgresql", "AuditLog-Kette/Trigger/RLS erfordert PostgreSQL"

    def test_tail_truncation_reports_ok_false_and_the_surviving_tail_hash(self, facility, staff_user, tmp_path):
        """Killt drei Survivors am HWM-Zweig (``if reason: return ChainResult(...)``):

        - ``__mutmut_79``: ``ok`` wird ``None`` statt ``False`` (``is False``
          haertet die exakte Semantik, wie oben).
        - ``__mutmut_82``: ``last_entry_hash=prev_entry`` -> ``last_entry_hash=None``
          (der Tail der ÜBERLEBENDEN Restkette geht verloren).
        - ``__mutmut_87``: das ``last_entry_hash=``-Keyword-Argument faellt
          komplett weg -> Dataclass-Default ``None`` (gleicher Effekt wie
          ``_82``).

        Aufbau wie ``test_audit_chain.py::TestHighWaterMark::test_hwm_flags_tail_truncation``:
        die juengste Zeile wird abgeschnitten, der HWM-Anker (vor dem
        Abschneiden verankert) erkennt die Truncation. ``last_entry_hash`` MUSS
        dabei weiterhin den Hash der ueberlebenden (vorletzten) Zeile tragen —
        das ist der Anker, auf dem ein Folge-Lauf aufsetzt.
        """
        survivor = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        tail = audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        survivor.refresh_from_db()
        tail.refresh_from_db()

        path = str(tmp_path / "hwm.json")
        baseline = verify_chain(facility)
        store = HighWaterMarkStore(path=path)
        store.record(facility.pk, baseline.rows_checked, baseline.last_entry_hash)
        store.save()

        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=tail.pk).delete()

        reloaded = HighWaterMarkStore.load(path)
        result = verify_chain(facility, hwm=reloaded)

        assert result.ok is False
        assert result.last_entry_hash == survivor.entry_hash


# ---------------------------------------------------------------------------
# _checkpoint_boundaries
# ---------------------------------------------------------------------------


class TestCheckpointBoundariesUnauthenticatedRowsDoNotAbortTheScan:
    def test_an_unhashed_and_a_forged_checkpoint_do_not_hide_a_later_legitimate_one(self, facility):
        """Killt ``__mutmut_5`` (``if not row.entry_hash: continue`` -> ``break``)
        und ``__mutmut_18`` (``if not hmac.compare_digest(...): continue`` ->
        ``break``) in EINEM Test: drei Checkpoint-Zeilen in chronologischer
        Reihenfolge — (a) ohne ``entry_hash`` (unverkettete Bestandszeile,
        triggert Zeile ``_5``), (b) mit FALSCHEM ``entry_hash`` (gefaelscht,
        triggert Zeile ``_18``), (c) LEGITIM (authentischer Hash). Original:
        beide vorderen Zeilen werden uebersprungen, (c)'s Grenze landet im
        Ergebnis. Unter JEDEM der beiden Mutanten bricht die Schleife VOR (c)
        ab -> die legitime Grenze fehlt -> ``verify_chain`` wuerde einen
        echten, durch (c) gedeckten Pruning-Sprung faelschlich als Bruch
        melden (Checkpoint-Grenzen-Abschwaechung im Sinne des Standardblocks).
        """
        now = timezone.now()
        unhashed = AuditLog(
            facility=facility,
            action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT,
            target_type="AuditLog",
            detail={"boundary_hash": "should-be-ignored-unhashed"},
            timestamp=now,
            prev_hash="",
            entry_hash="",
        )
        forged = AuditLog(
            facility=facility,
            action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT,
            target_type="AuditLog",
            detail={"boundary_hash": "should-be-ignored-forged"},
            timestamp=now + timedelta(seconds=1),
            prev_hash="deadbeef",
            entry_hash="f" * 64,
        )
        legit_prev = "cafebabe"
        legit = AuditLog(
            facility=facility,
            action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT,
            target_type="AuditLog",
            detail={"boundary_hash": "legit-boundary"},
            timestamp=now + timedelta(seconds=2),
            prev_hash=legit_prev,
        )
        legit.entry_hash = compute_entry_hash(legit, legit_prev)

        boundaries = chain_mod._checkpoint_boundaries([unhashed, forged, legit])

        assert boundaries == {"legit-boundary"}


class TestCheckpointBoundariesPrevHashFallback:
    def test_the_very_first_checkpoint_of_a_chain_authenticates_against_the_real_fallback(self, facility):
        """Killt ``__mutmut_12``: ``compute_entry_hash(row, row.prev_hash or "")``
        -> ``compute_entry_hash(row, row.prev_hash or "XXXX")``.

        Nur beobachtbar, wenn ``row.prev_hash`` selbst falsy ist (der allererste
        Checkpoint einer Kette, ``prev_hash=""``) — bei einem nicht-leeren
        ``prev_hash`` greift der ``or``-Fallback ohnehin nie, unabhaengig von
        der Mutation. Der ECHTE, gespeicherte ``entry_hash`` wurde mit
        ``compute_entry_hash(row, "")`` gebildet (korrekter Fallback);
        rekonstruiert die Authentizitaetspruefung stattdessen mit
        ``compute_entry_hash(row, "XXXX")``, weicht das Ergebnis vom
        gespeicherten Hash ab -> der Checkpoint gilt faelschlich als
        unauthentifiziert -> seine Grenze fehlt im Ergebnis.
        """
        now = timezone.now()
        first_ever = AuditLog(
            facility=facility,
            action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT,
            target_type="AuditLog",
            detail={"boundary_hash": "genesis-boundary"},
            timestamp=now,
            prev_hash="",
        )
        first_ever.entry_hash = compute_entry_hash(first_ever, "")

        boundaries = chain_mod._checkpoint_boundaries([first_ever])

        assert boundaries == {"genesis-boundary"}


class TestCheckpointBoundariesSingleKeyLookup:
    def test_the_single_boundary_hash_key_is_collected_verbatim(self, facility):
        """Killt fuenf Survivors am ``single = detail.get("boundary_hash")``-
        Lookup und der anschliessenden ``boundaries.add(single)``-Aufnahme:

        - ``__mutmut_21``: ``single = None`` (Lookup komplett ersetzt).
        - ``__mutmut_22``: ``detail.get(None)`` (sucht einen Schluessel, den ein
          aus JSON geladenes ``detail`` nie tragen kann — JSON-Objektschluessel
          sind immer Strings).
        - ``__mutmut_23``/``_24``: ``detail.get("XXboundary_hashXX")`` /
          ``detail.get("BOUNDARY_HASH")`` (falscher Schluessel-Literal).
        - ``__mutmut_25``: ``boundaries.add(single)`` -> ``boundaries.add(None)``
          (der echte Wert wird ermittelt, aber nicht mehr aufgenommen).

        Alle fuenf liefern fuer eine Checkpoint-Zeile mit
        ``detail={"boundary_hash": "abc123", ...}`` ein von ``{"abc123"}``
        abweichendes Ergebnis (leere Menge bzw. ``{None}``).
        """
        now = timezone.now()
        cp = AuditLog(
            facility=facility,
            action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT,
            target_type="AuditLog",
            detail={"boundary_hash": "abc123", "boundary_hashes": []},
            timestamp=now,
            prev_hash="seed",
        )
        cp.entry_hash = compute_entry_hash(cp, "seed")

        boundaries = chain_mod._checkpoint_boundaries([cp])

        assert boundaries == {"abc123"}
