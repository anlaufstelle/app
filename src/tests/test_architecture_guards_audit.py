"""Architecture-Guards — Audit-Completeness, Settings-Allowlist und E2E-Selektor-Stabilitäts-Guard (Refs Welle 6 #929)."""

import re
from pathlib import Path
from typing import ClassVar

import pytest

pytestmark = pytest.mark.architecture


class TestSettingsAuditCompletenessGuard:
    """Refs #900 (FND-001): jedes Feld auf ``Settings`` muss entweder
    auditiert (``_AUDIT_FIELDS``) oder explizit ausgenommen sein
    (``_AUDIT_EXEMPT``). Verhindert, dass ein neues verhaltensrelevantes
    Setting unauditiert gemerged wird.
    """

    def _settings_fields(self):
        from core.models import Settings

        names = []
        for f in Settings._meta.get_fields():
            # Reverse-Relationen (related_name-Zugriffe) zaehlen nicht — sie
            # haben kein Datenbank-Spaltenpendant auf Settings.
            if not getattr(f, "concrete", False):
                continue
            # M2M-Through und virtuelle Felder ueberspringen — sie haben
            # entweder keine ``column`` oder sind reine Manager-Konstrukte.
            if getattr(f, "column", None) is None:
                continue
            names.append(f.name)
        return set(names)

    def test_no_overlap_between_audit_fields_and_exempt(self):
        from core.services.settings import _AUDIT_EXEMPT, _AUDIT_FIELDS

        overlap = set(_AUDIT_FIELDS) & set(_AUDIT_EXEMPT)
        assert not overlap, (
            f"_AUDIT_FIELDS und _AUDIT_EXEMPT duerfen nicht ueberlappen — "
            f"sonst wird ein 'auditiertes' Feld gleichzeitig als 'irrelevant' "
            f"deklariert. Konflikt: {sorted(overlap)}"
        )

    def test_audit_fields_reference_existing_model_fields(self):
        from core.services.settings import _AUDIT_FIELDS

        existing = self._settings_fields()
        missing = [name for name in _AUDIT_FIELDS if name not in existing]
        assert not missing, (
            f"_AUDIT_FIELDS verweist auf nicht-existierende Settings-Felder: "
            f"{missing}. Wahrscheinlich Tippfehler oder veralteter Eintrag — "
            f"existierende Felder: {sorted(existing)}"
        )

    def test_audit_exempt_references_existing_model_fields(self):
        from core.services.settings import _AUDIT_EXEMPT

        existing = self._settings_fields()
        missing = [name for name in _AUDIT_EXEMPT if name not in existing]
        assert not missing, (
            f"_AUDIT_EXEMPT verweist auf nicht-existierende Settings-Felder: "
            f"{missing}. Existierende Felder: {sorted(existing)}"
        )

    def test_every_settings_field_is_classified(self):
        """Jedes konkrete Feld auf Settings muss explizit klassifiziert sein.

        Failt, wenn jemand ein neues Feld zu Settings hinzufuegt, aber
        weder in ``_AUDIT_FIELDS`` noch in ``_AUDIT_EXEMPT`` deklariert.
        Dann muss bewusst entschieden werden: auditieren oder begruendet
        ausnehmen — beides ist ok, aber stillschweigendes Uebergehen nicht.
        """
        from core.services.settings import _AUDIT_EXEMPT, _AUDIT_FIELDS

        classified = set(_AUDIT_FIELDS) | set(_AUDIT_EXEMPT)
        existing = self._settings_fields()
        unclassified = sorted(existing - classified)
        assert not unclassified, (
            f"Unklassifizierte Settings-Felder: {unclassified}. "
            f"Bitte zu ``_AUDIT_FIELDS`` in src/core/services/settings.py "
            f"hinzufuegen (verhaltensrelevant — DSGVO/MFA/Retention/Suche/"
            f"Datei-Policy) oder zu ``_AUDIT_EXEMPT`` mit Kommentar "
            f"(z.B. PrimaryKey, auto_now). Refs #893 / FND-001."
        )


class TestAuditLogCreationAllowlist:
    """Refs #901 / FND-002: direkte ``AuditLog.objects.create(...)``-Aufrufe
    sind nur an dokumentierten Stellen erlaubt. Alles andere muss ueber die
    typed Helper aus ``core.services.audit`` laufen
    (``log_audit_event``, ``audit_event``, ``audit_client_event``,
    ``audit_retention_decision``, ``audit_security_violation``,
    ``audit_system_view``).

    Die Allowlist enthaelt aktuell drei Kategorien:

    1. **Helper-Bodies** in ``services/audit.py`` und ``services/settings.py``
       — die Helper selbst muessen am Ende ``.objects.create(...)`` rufen.
    2. **Signal-Handler** in ``signals/audit.py`` — laufen in Post-Save-
       Signalen ohne Request-Objekt und setzen RLS-Session-Variablen
       manuell. Migration auf Helper waere Schein-Aufwand.
    3. **Migrationsstandorte** waehrend des #901-Refactors — werden in
       atomaren Schritten in 8 Commits abgeraeumt. Endzustand siehe S9
       des Plans.
    """

    _CORE_DIR = Path("src/core")
    _PATTERN = re.compile(r"AuditLog\.objects\.create\(")

    # Allowlist: { relative-to-src/-path: { line_number: "Begruendung" } }.
    # Aenderungen am Code, die die Zeilennummern eines erlaubten Eintrags
    # verschieben, MUESSEN die Allowlist mit aktualisieren — sonst meldet
    # der Test wieder einen "Verstoss".
    _ALLOWED_DIRECT_CALLS: dict[str, dict[int, str]] = {
        # Helper-Bodies — by design.
        "core/services/audit.py": {
            4: "Docstring-Beispiel — kein echter Call",
            85: "log_audit_event-Body — zentraler View-Helper",
            126: "audit_event-Body — generischer Service-/Cron-Helper",
        },
        "core/services/settings.py": {
            85: "log_settings_change-Body — Settings-Diff-Helper",
            105: "log_settings_change-Body — zweite create-Stelle",
        },
        # Signal-Handler: kein Request, RLS-Session-State explizit gesetzt.
        "core/signals/audit.py": {
            93: "on_user_logged_in — auth signal, kein Request",
            112: "on_user_logged_out — auth signal",
            145: "on_user_login_failed — no-facility branch",
            153: "on_user_login_failed — with-facility branch",
            198: "post_save User — role change detection",
            212: "post_save User — deactivation detection",
        },
    }

    def test_only_allowlisted_direct_auditlog_creates(self):
        if not self._CORE_DIR.exists():
            pytest.skip(f"{self._CORE_DIR} nicht vorhanden")

        violations: list[str] = []
        for py_file in self._CORE_DIR.rglob("*.py"):
            rel = str(py_file.relative_to("src"))
            allowed_for_file = self._ALLOWED_DIRECT_CALLS.get(rel, {})
            source = py_file.read_text(errors="ignore")
            for match in self._PATTERN.finditer(source):
                line = source[: match.start()].count("\n") + 1
                if line in allowed_for_file:
                    continue
                violations.append(f"{rel}:{line}")

        assert not violations, (
            "Direkte AuditLog.objects.create()-Calls ausserhalb der Allowlist. "
            "Bitte stattdessen einen typed Helper aus core.services.audit "
            "verwenden (audit_event, audit_client_event, "
            "audit_retention_decision, audit_security_violation, "
            "audit_system_view) oder die Stelle in "
            "_ALLOWED_DIRECT_CALLS mit Begruendung dokumentieren.\n"
            "Refs #901 / FND-002.\n"
            f"Verstoesse: {violations}"
        )

    def test_allowlist_lines_actually_have_creates(self):
        """Schutz vor stale Eintraegen: jede Allowlist-Zeile muss
        tatsaechlich auf eine ``AuditLog.objects.create``-Stelle zeigen.
        Sonst altert die Allowlist still — wenn der Code refactored wird,
        verschiebt sich die Zeile und der alte Eintrag verstummt."""
        stale: list[str] = []
        for rel, lines in self._ALLOWED_DIRECT_CALLS.items():
            path = Path("src") / rel
            if not path.exists():
                stale.append(f"{rel} (Datei existiert nicht)")
                continue
            src_lines = path.read_text(errors="ignore").splitlines()
            for line_no in lines:
                if line_no < 1 or line_no > len(src_lines):
                    stale.append(f"{rel}:{line_no} (Zeile ausserhalb der Datei)")
                    continue
                if "AuditLog.objects.create" not in src_lines[line_no - 1]:
                    stale.append(f"{rel}:{line_no} (kein 'AuditLog.objects.create' an dieser Zeile)")
        assert not stale, (
            "Allowlist-Eintraege zeigen ins Leere — wahrscheinlich verschoben "
            "sich Zeilennummern durch Refactoring. Bitte _ALLOWED_DIRECT_CALLS "
            "auf die aktuellen Zeilen aktualisieren.\n"
            f"Verstoesse: {stale}"
        )


class TestE2ESelectorStabilityGuard:
    """Blockt brüchige Playwright-Selektoren in src/tests/e2e/.

    Refs #922 / #924 (Welle 1): ``page.locator(...).first.click()`` und
    ``.nth(<int>).click()`` auf nicht-deterministischer Reihenfolge führen
    zu Flakes bei Seed-Drift und parallelisierten Runs. Stabile Alternativen
    sind ``data-testid``-basierte Selektoren plus die Helper in
    ``src/tests/e2e/_selectors.py``.

    Bestehende Sünden, die in dieser Welle bewusst noch nicht migriert wurden,
    sind per File-zentrierter Whitelist mit max. erlaubten Vorkommen erfasst.
    Wenn ein File aufräumt, muss der Counter heruntergesetzt werden — das
    erzwingt Bewegung in die richtige Richtung. Wenn die Zahl überschritten
    wird, schlägt der Guard fehl.
    """

    _E2E_DIR = Path("src/tests/e2e")
    _PATTERN = re.compile(r"\.(first|nth\(\d+\))\.click\(\)")

    # File-Whitelist mit max. erlaubten Vorkommen. Beim Migrieren auf 0 setzen
    # und den Eintrag entfernen, sobald der Counter 0 ist.
    #
    # Welle-5-Follow-Up: Die neuen E2E-Files aus Welle 5 (#928) haben einige
    # ``.first.click()``-Selektoren mitgebracht; Werte hier dokumentieren den
    # Ist-Stand, damit Welle 6 (#929) den reinen Refactor ohne behavioural
    # change abschließen kann. Cleanup → eigenes Issue (Welle 1 Nachzügler).
    _WHITELIST_MAX: ClassVar[dict[str, int]] = {
        "test_button_permissions.py": 2,
        "test_cases.py": 3,
        "test_client_deletion_workflow.py": 6,
        "test_episodes.py": 1,
        # Welle 5 / dbdaf3c: TestMultipleGoalsAndMilestones brachte 3 weitere
        # ``.first.click()`` (Milestone-Toggle + Goal-Toggle + Submit innerhalb
        # einer Schleife). Counter zieht den Ist-Stand nach; Stabilisierung
        # auf data-testid läuft separat (Welle-1-Nachzügler).
        "test_goals_htmx.py": 5,
        "test_handover.py": 3,
        "test_i18n_locale.py": 2,
        "test_retention_dashboard.py": 5,
        "test_workflow_complete.py": 1,
        "test_workitem_edit.py": 2,
        "test_zeitstrom_enrichment.py": 1,
        "test_zeitstrom_events.py": 2,
    }

    def _count_in_file(self, path: Path) -> int:
        if not path.exists():
            return 0
        return len(self._PATTERN.findall(path.read_text(errors="ignore")))

    def test_no_new_unstable_selectors(self):
        """Neue Files dürfen kein ``.first.click()``/``.nth(N).click()``."""
        if not self._E2E_DIR.exists():
            pytest.skip(f"{self._E2E_DIR} nicht vorhanden")

        violations: list[str] = []
        for py_file in sorted(self._E2E_DIR.glob("test_*.py")):
            name = py_file.name
            count = self._count_in_file(py_file)
            allowed = self._WHITELIST_MAX.get(name, 0)
            if count > allowed:
                violations.append(f"{name}: {count} unstable Selektor(en), erlaubt sind {allowed}")

        assert not violations, (
            "Brüchige Playwright-Selektoren (.first.click() / .nth(N).click()) "
            "über der Whitelist-Schwelle. Bitte stabile data-testid-basierte "
            "Selektoren nutzen (siehe src/tests/e2e/_selectors.py) oder die "
            "Whitelist im TestE2ESelectorStabilityGuard anpassen, falls eine "
            "Migration den Counter reduziert hat.\n"
            "Verstöße:\n  " + "\n  ".join(violations)
        )

    def test_whitelist_entries_are_still_needed(self):
        """Schutz gegen veraltete Whitelist: wenn ein File migriert ist,
        muss der Eintrag entfernt oder der Counter gesenkt werden."""
        stale: list[str] = []
        for name, allowed in self._WHITELIST_MAX.items():
            path = self._E2E_DIR / name
            if not path.exists():
                stale.append(f"{name}: Datei existiert nicht mehr")
                continue
            count = self._count_in_file(path)
            if count < allowed:
                stale.append(f"{name}: Whitelist erlaubt {allowed}, aktuell nur {count} Vorkommen")
        assert not stale, (
            "Whitelist-Schwellen sind höher als der aktuelle Bestand — bitte "
            "_WHITELIST_MAX auf die tatsächliche Anzahl senken (Ziel: 0 und "
            "Eintrag entfernen).\n"
            "Veraltete Einträge:\n  " + "\n  ".join(stale)
        )
