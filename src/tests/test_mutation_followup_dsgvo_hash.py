"""Follow-Up-Tests für Mutation-Survivors in ``core.services.client.dsgvo_package``.

Refs #930, erweitert Refs #1762. Ziel: Mutationen in ``_settings_hash``
killen — die Funktion erzeugt einen 8-stelligen, deterministischen SHA-256-
Hash über einen JSON-Snapshot retention-relevanter Settings-Felder **plus**
der abgeleiteten Dokumentationstyp-Ausnahmen. Refs #1762 hat dsfa.md § 2.3 von
"nur drei echte Platzhalter, vier Standardwerte mit Prüfauftrag" auf "alle
sieben Fristen als Ist-Wert" umgestellt — ohne die Hash-Erweiterung würde eine
inhaltlich geänderte DSFA (z. B. andere ``retention_activities_days``) denselben
Settings-Stempel tragen wie zuvor, und der Aktualitätsnachweis für
Aufsichtsprüfungen wäre unwahr.

Die Mutmut-Survivors fallen in folgende Kategorien:

1. **Field-Selection** — jedes der sieben skalaren Payload-Felder
   (``retention_anonymous_days``, ``retention_identified_days``,
   ``retention_qualified_days``, ``retention_activities_days``,
   ``client_trash_days``, ``auditlog_retention_months``,
   ``facility_full_name``) sowie die abgeleiteten
   ``document_type_overrides`` müssen in den Hash einfließen. Mutationen,
   die ein Feld auslassen oder durch eine Konstante ersetzen, werden durch
   "ändern → Hash ändert sich"-Tests erkannt.
2. **Default-Konstanten** — ``getattr(.., "field", DEFAULT)``-Defaults
   (90 / 365 / 3650 / 365 / 30 / 24 / "") müssen exakt sein. Mutation der
   Default-Konstanten wird durch Hash-Vergleich mit echtem Settings-Objekt
   getroffen, wenn das Settings-Objekt die jeweilige Default-Beschreibung
   spiegelt.
3. **Sort-Keys** — ``json.dumps(payload, sort_keys=True)``: ohne
   ``sort_keys`` würde die dict-Reihenfolge variieren. Wir verifizieren
   Determinismus über mehrere Aufrufe.
4. **Encoding** — ``.encode("utf-8")``-Aufruf: Unicode-Inhalte
   (``facility_full_name`` mit Umlauten) müssen reproduzierbar gehasht
   werden.
5. **Hash-Algorithmus** — ``hashlib.sha256(...).hexdigest()[:8]``:
   Output-Format-Test (genau 8 Hex-Zeichen) und Algorithmus-Identität
   (Vergleich gegen bekannten SHA-256-Prefix bei kontrollierter
   Eingabe).
6. **None/Empty-Handling** — ``getattr(..., "", "") or ""``: ein
   ``facility_full_name=None`` muss zum gleichen Hash führen wie
   ``facility_full_name=""``.
7. **Document-Type-Overrides** (Refs #1762) — ``DocumentType.retention_days``
   ist kein einzelnes Settings-Feld, sondern pro Dokumentationstyp gesetzt
   (Zeile 4 in dsfa.md § 2.3, "Abweichende Frist einzelner
   Dokumentationstypen"). ``_settings_hash`` nimmt die dafür abgeleitete
   Liste als zweites Argument entgegen; ändert sie sich, muss sich der Hash
   ändern.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from core.models import Settings
from core.services.client import _settings_hash


def _make_stub(**overrides):
    """Erzeugt ein Settings-ähnliches Stub-Objekt mit allen sieben Feldern.

    Defaults entsprechen den Defaults aus dem Settings-Modell, sodass das
    Stub-Ergebnis mit einem frisch angelegten ``Settings``-Datensatz
    übereinstimmt.
    """
    base = {
        "retention_anonymous_days": 90,
        "retention_identified_days": 365,
        "retention_qualified_days": 3650,
        "retention_activities_days": 365,
        "client_trash_days": 30,
        "auditlog_retention_months": 24,
        "facility_full_name": "Anlaufstelle Musterstadt",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.django_db
class TestSettingsHash:
    """Refs `_settings_hash`-Mutation-Killer."""

    # ------------------------------------------------------------------
    # Output-Format & Determinismus
    # ------------------------------------------------------------------

    def test_hash_is_eight_hex_characters(self):
        """Format-Boundary: Hash hat exakt 8 hex-Zeichen.

        Killt Mutationen am Slicing (``[:8]`` → ``[:7]``/``[:9]``/
        ``[8:]``) und am ``.hexdigest()``-Aufruf.
        """
        h = _settings_hash(_make_stub())
        assert len(h) == 8, f"Erwarte 8 Zeichen, bekomme {len(h)}: {h!r}"
        assert all(c in "0123456789abcdef" for c in h), f"Hash enthält Nicht-Hex-Zeichen: {h!r}"

    def test_identical_settings_yield_identical_hash(self):
        """Determinismus: gleiche Eingabe → gleicher Hash, wiederholt.

        Killt Mutationen, die Nicht-Determinismus einführen (z.B.
        ``sort_keys=True`` → ``False`` würde nur intermittierend
        gleiche Ergebnisse liefern, dieser Test wiederholt mehrfach).
        """
        stub_a = _make_stub()
        stub_b = _make_stub()
        results = [_settings_hash(stub_a) for _ in range(5)]
        assert len(set(results)) == 1, f"Hash nicht deterministisch: {results}"
        assert _settings_hash(stub_a) == _settings_hash(stub_b)

    def test_hash_matches_explicit_sha256_of_payload(self):
        """Algorithmus-Identität: Reproduktion mit explizitem SHA-256.

        Killt Mutationen am Hash-Algorithmus (sha256 → md5/sha1) und
        an der JSON-Serialisierung (sort_keys, encoding).
        """
        stub = _make_stub(
            retention_anonymous_days=90,
            retention_identified_days=365,
            retention_qualified_days=3650,
            retention_activities_days=365,
            client_trash_days=30,
            auditlog_retention_months=24,
            facility_full_name="Anlaufstelle Musterstadt",
        )
        expected_payload = {
            "retention_anonymous_days": 90,
            "retention_identified_days": 365,
            "retention_qualified_days": 3650,
            "retention_activities_days": 365,
            "client_trash_days": 30,
            "auditlog_retention_months": 24,
            "facility_full_name": "Anlaufstelle Musterstadt",
            "document_type_overrides": [],
        }
        expected = hashlib.sha256(json.dumps(expected_payload, sort_keys=True).encode("utf-8")).hexdigest()[:8]
        assert _settings_hash(stub) == expected

    # ------------------------------------------------------------------
    # Field-Selection: jedes Feld muss in den Hash einfließen
    # ------------------------------------------------------------------

    def test_changing_retention_anonymous_days_changes_hash(self):
        """Mutation, die ``retention_anonymous_days`` aus Payload entfernt,
        ergibt für 90 vs 91 denselben Hash → dieser Test failt sie."""
        h_baseline = _settings_hash(_make_stub(retention_anonymous_days=90))
        h_changed = _settings_hash(_make_stub(retention_anonymous_days=91))
        assert h_baseline != h_changed

    def test_changing_retention_identified_days_changes_hash(self):
        h_baseline = _settings_hash(_make_stub(retention_identified_days=365))
        h_changed = _settings_hash(_make_stub(retention_identified_days=366))
        assert h_baseline != h_changed

    def test_changing_retention_qualified_days_changes_hash(self):
        h_baseline = _settings_hash(_make_stub(retention_qualified_days=3650))
        h_changed = _settings_hash(_make_stub(retention_qualified_days=3651))
        assert h_baseline != h_changed

    def test_changing_facility_full_name_changes_hash(self):
        h_baseline = _settings_hash(_make_stub(facility_full_name="A"))
        h_changed = _settings_hash(_make_stub(facility_full_name="B"))
        assert h_baseline != h_changed

    def test_changing_retention_activities_days_changes_hash(self):
        """Refs #1762: vormals Standardwert-Prosa, jetzt echter Ist-Wert —
        muss den Hash beeinflussen wie die drei Ur-Felder."""
        h_baseline = _settings_hash(_make_stub(retention_activities_days=365))
        h_changed = _settings_hash(_make_stub(retention_activities_days=366))
        assert h_baseline != h_changed

    def test_changing_client_trash_days_changes_hash(self):
        h_baseline = _settings_hash(_make_stub(client_trash_days=30))
        h_changed = _settings_hash(_make_stub(client_trash_days=31))
        assert h_baseline != h_changed

    def test_changing_auditlog_retention_months_changes_hash(self):
        h_baseline = _settings_hash(_make_stub(auditlog_retention_months=24))
        h_changed = _settings_hash(_make_stub(auditlog_retention_months=25))
        assert h_baseline != h_changed

    def test_changing_document_type_overrides_changes_hash(self):
        """Refs #1762: die abgeleitete vierte Frist (Zeile 4, dsfa.md § 2.3)
        ist kein Settings-Feld, sondern wird als zweites Argument übergeben —
        muss trotzdem den Hash beeinflussen."""
        stub = _make_stub()
        h_none = _settings_hash(stub)
        h_one = _settings_hash(stub, document_type_overrides=[("Vermittlung", 180)])
        h_two = _settings_hash(stub, document_type_overrides=[("Vermittlung", 180), ("Notiz", 30)])
        assert h_none != h_one
        assert h_one != h_two
        assert h_none != h_two

    def test_document_type_overrides_default_to_empty(self):
        """Ohne zweites Argument entspricht der Hash einer leeren Liste."""
        stub = _make_stub()
        assert _settings_hash(stub) == _settings_hash(stub, document_type_overrides=[])

    # ------------------------------------------------------------------
    # Default-Konstanten: getattr-Defaults müssen exakt sein
    # ------------------------------------------------------------------

    def test_missing_anonymous_field_defaults_to_ninety(self):
        """Stub ohne ``retention_anonymous_days`` muss Hash wie 90 ergeben.

        Killt Mutationen am Default 90 (z.B. 90 → 0 / 91 / None).
        """
        stub_full = _make_stub(retention_anonymous_days=90)
        stub_missing = SimpleNamespace(
            retention_identified_days=365,
            retention_qualified_days=3650,
            facility_full_name="Anlaufstelle Musterstadt",
        )
        assert _settings_hash(stub_full) == _settings_hash(stub_missing)

    def test_missing_identified_field_defaults_to_threesixfive(self):
        """Default-Konstante 365 (retention_identified_days)."""
        stub_full = _make_stub(retention_identified_days=365)
        stub_missing = SimpleNamespace(
            retention_anonymous_days=90,
            retention_qualified_days=3650,
            facility_full_name="Anlaufstelle Musterstadt",
        )
        assert _settings_hash(stub_full) == _settings_hash(stub_missing)

    def test_missing_qualified_field_defaults_to_threethousandsixfifty(self):
        """Default-Konstante 3650 (retention_qualified_days)."""
        stub_full = _make_stub(retention_qualified_days=3650)
        stub_missing = SimpleNamespace(
            retention_anonymous_days=90,
            retention_identified_days=365,
            facility_full_name="Anlaufstelle Musterstadt",
        )
        assert _settings_hash(stub_full) == _settings_hash(stub_missing)

    def test_missing_facility_full_name_defaults_to_empty_string(self):
        """Default ``""`` für ``facility_full_name``.

        Killt Mutationen am String-Default (z.B. ``""`` → ``"n/a"``).
        """
        stub_empty = _make_stub(facility_full_name="")
        stub_missing = SimpleNamespace(
            retention_anonymous_days=90,
            retention_identified_days=365,
            retention_qualified_days=3650,
        )
        assert _settings_hash(stub_empty) == _settings_hash(stub_missing)

    def test_missing_activities_field_defaults_to_threesixfive(self):
        """Default-Konstante 365 (retention_activities_days, Refs #1762)."""
        stub_full = _make_stub(retention_activities_days=365)
        stub_missing = SimpleNamespace(
            retention_anonymous_days=90,
            retention_identified_days=365,
            retention_qualified_days=3650,
            client_trash_days=30,
            auditlog_retention_months=24,
            facility_full_name="Anlaufstelle Musterstadt",
        )
        assert _settings_hash(stub_full) == _settings_hash(stub_missing)

    def test_missing_trash_field_defaults_to_thirty(self):
        """Default-Konstante 30 (client_trash_days, Refs #1762)."""
        stub_full = _make_stub(client_trash_days=30)
        stub_missing = SimpleNamespace(
            retention_anonymous_days=90,
            retention_identified_days=365,
            retention_qualified_days=3650,
            retention_activities_days=365,
            auditlog_retention_months=24,
            facility_full_name="Anlaufstelle Musterstadt",
        )
        assert _settings_hash(stub_full) == _settings_hash(stub_missing)

    def test_missing_auditlog_field_defaults_to_twentyfour(self):
        """Default-Konstante 24 (auditlog_retention_months, Refs #1762)."""
        stub_full = _make_stub(auditlog_retention_months=24)
        stub_missing = SimpleNamespace(
            retention_anonymous_days=90,
            retention_identified_days=365,
            retention_qualified_days=3650,
            retention_activities_days=365,
            client_trash_days=30,
            facility_full_name="Anlaufstelle Musterstadt",
        )
        assert _settings_hash(stub_full) == _settings_hash(stub_missing)

    # ------------------------------------------------------------------
    # None / "or"-Branch
    # ------------------------------------------------------------------

    def test_none_facility_full_name_treated_as_empty(self):
        """``None`` und ``""`` müssen identischen Hash ergeben.

        Killt Mutationen am ``getattr(...) or ""``-Branch (z.B. ``or``
        → ``and``: ``None and ""`` ist ``None`` → JSON null → anderer
        Hash).
        """
        h_none = _settings_hash(_make_stub(facility_full_name=None))
        h_empty = _settings_hash(_make_stub(facility_full_name=""))
        assert h_none == h_empty, f"None und Leerstring müssen identisch hashen, sind aber {h_none} vs {h_empty}"

    def test_none_facility_full_name_different_from_nonempty(self):
        """``None`` muss anderen Hash ergeben als ein realer Name —
        sonst hätte der ``or ""``-Default das eigentliche Feld verworfen."""
        h_none = _settings_hash(_make_stub(facility_full_name=None))
        h_real = _settings_hash(_make_stub(facility_full_name="Echt"))
        assert h_none != h_real

    # ------------------------------------------------------------------
    # Encoding: Unicode-Reproduzierbarkeit
    # ------------------------------------------------------------------

    def test_unicode_facility_name_is_deterministic(self):
        """UTF-8-Encoding-Test: Umlaute werden stabil gehasht.

        Killt Mutationen am Encoding-Argument (``"utf-8"`` → ``"ascii"``
        würde bei Umlauten werfen).
        """
        name = "Anlaufstelle Köln-Ümlautstraße"
        h1 = _settings_hash(_make_stub(facility_full_name=name))
        h2 = _settings_hash(_make_stub(facility_full_name=name))
        assert h1 == h2
        # Und unterscheidet sich von ASCII-Variante:
        h_ascii = _settings_hash(_make_stub(facility_full_name="Anlaufstelle Koeln"))
        assert h1 != h_ascii

    # ------------------------------------------------------------------
    # Integration mit echtem Settings-Modell
    # ------------------------------------------------------------------

    def test_real_settings_object_matches_stub_with_same_values(self, facility):
        """Realer ``Settings``-Datensatz und SimpleNamespace mit gleichen
        Feldern müssen denselben Hash ergeben — der ``getattr``-Pfad
        ist also egal."""
        real = Settings.objects.create(
            facility=facility,
            facility_full_name="Anlaufstelle Musterstadt",
            retention_anonymous_days=90,
            retention_identified_days=365,
            retention_qualified_days=3650,
        )
        stub = _make_stub()
        assert _settings_hash(real) == _settings_hash(stub)

    def test_unrelated_settings_field_does_not_affect_hash(self, facility):
        """Felder ausserhalb des Payloads (``session_timeout_minutes``)
        dürfen Hash nicht beeinflussen — verifiziert die Field-
        Selection auf der Inklusions-Seite (nur die dokumentierten sieben
        skalaren Felder plus ``document_type_overrides`` sind drin)."""
        s = Settings.objects.create(
            facility=facility,
            facility_full_name="Anlaufstelle Musterstadt",
            retention_anonymous_days=90,
            retention_identified_days=365,
            retention_qualified_days=3650,
            session_timeout_minutes=30,
        )
        h_before = _settings_hash(s)
        s.session_timeout_minutes = 999
        s.save()
        s.refresh_from_db()
        h_after = _settings_hash(s)
        assert h_before == h_after, (
            f"session_timeout_minutes darf den Hash nicht beeinflussen, bekam aber {h_before} → {h_after}"
        )

    def test_real_document_type_retention_days_changes_hash(self, facility):
        """Refs #1762: eine echte DocumentType-Ausnahme (Zeile 4, dsfa.md
        § 2.3) muss über das zweite Argument den Hash ändern.

        Refs #1775: Die Liste kommt jetzt aus demselben DB-Roundtrip, den auch
        ``render_document`` fährt (``_document_type_retention_overrides``) —
        vorher baute der Test sie von Hand und der Testname versprach damit
        mehr, als er prüfte: ein ausgefallener Ableitungspfad (falscher Filter,
        leere Liste) wäre unbemerkt geblieben.
        """
        from core.models import DocumentType
        from core.services.client import _document_type_retention_overrides

        real = Settings.objects.create(facility=facility, facility_full_name="Anlaufstelle Musterstadt")
        h_without = _settings_hash(real, document_type_overrides=_document_type_retention_overrides(facility))

        DocumentType.objects.create(facility=facility, name="Vermittlung", retention_days=180)
        overrides = _document_type_retention_overrides(facility)

        assert overrides == [("Vermittlung", 180)], f"DB-Ableitung liefert nicht die erwartete Ausnahme: {overrides}"
        h_with = _settings_hash(real, document_type_overrides=overrides)

        assert h_without != h_with
