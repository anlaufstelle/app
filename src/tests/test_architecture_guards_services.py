"""Architecture-Guards — Service-Layer-Direction- und Encryption-Bypass-Guards (Refs Welle 6 #929)."""

import re
from pathlib import Path
from typing import ClassVar

import pytest

pytestmark = pytest.mark.architecture


class TestEventEncryptionBypassGuard:
    """Refs #736 / #713 (Audit-Massnahme #11): Verhindert, dass irgendein
    Code-Pfad die Encryption-Pipeline in ``Event.save()``/``encryption.py``
    umgeht.

    Drei Patterns sind verboten:
    - ``Event.objects.bulk_create(...)`` — kein ``save()``-Hook
    - ``Event.objects.filter(...).update(data_json=...)`` — Raw-Update
    - ``Event.objects.update_or_create(defaults={"data_json": ...})`` — auch ohne ``save()``-Hook

    Allowlist (legitime Bypaesse):
    - ``src/core/services/encryption.py`` — die Encryption-Pipeline selbst
    - ``src/core/seed/`` — Seed-Daten sind deterministische Test-Fixtures
      ohne reale Art-9-Inhalte
    - ``src/core/migrations/`` — Schema-Migrationen
    - ``src/tests/`` — Testfixtures duerfen direkt schreiben

    Erweiterung der Allowlist erfordert separaten Commit + Begruendung.
    """

    _CORE_DIR = Path("src/core")
    _ALLOWLIST = (
        Path("src/core/services/encryption.py"),
        Path("src/core/seed"),
        Path("src/core/migrations"),
    )
    # Drei Bypass-Patterns:
    # 1) Event.objects.bulk_create(...) — direkt
    # 2) Event.objects[.filter(...)|.exclude(...)|...].update(data_json=...)
    # 3) Event.objects.update_or_create(..., data_json=...)
    # Pattern 2 erlaubt chained QuerySet-Methoden zwischen ``objects`` und
    # ``update(``; ``[^=\n]{0,200}?`` schliesst Multi-line-Statements aus
    # und limitiert das Matching auf eine sinnvolle Distanz.
    _BYPASS_BULK_CREATE = re.compile(r"Event\.objects\.bulk_create\b")
    # Erlaubt chained QuerySet-Methoden zwischen ``objects`` und ``update(`` —
    # ``Event.objects.filter(...).update(data_json=...)`` ist der haeufigste
    # Bypass-Pfad. Die ``\.update\(``-Klausel matcht ``update`` exakt (nicht
    # ``update_or_create``, weil dort ``_or_create`` zwischen ``update`` und
    # ``(`` steht).
    _BYPASS_UPDATE = re.compile(
        r"Event\.objects(?:\.[a-zA-Z_]+\([^)]*\))*\.update\([^)]*\bdata_json\b",
    )
    _BYPASS_UPDATE_OR_CREATE = re.compile(
        r"Event\.objects\.update_or_create\([^)]*\bdata_json\b",
    )

    def _is_allowlisted(self, path: Path) -> bool:
        return any(path == allow or allow in path.parents for allow in self._ALLOWLIST)

    _BYPASS_PATTERNS = (
        ("bulk_create", _BYPASS_BULK_CREATE),
        ("update(data_json=...)", _BYPASS_UPDATE),
        ("update_or_create(data_json=...)", _BYPASS_UPDATE_OR_CREATE),
    )

    def test_no_event_encryption_bypass(self):
        if not self._CORE_DIR.exists():
            pytest.skip(f"{self._CORE_DIR} nicht vorhanden")
        violations = []
        for py_file in self._CORE_DIR.rglob("*.py"):
            if self._is_allowlisted(py_file):
                continue
            source = py_file.read_text(errors="ignore")
            for label, pattern in self._BYPASS_PATTERNS:
                for match in pattern.finditer(source):
                    line = source[: match.start()].count("\n") + 1
                    violations.append(f"{py_file}:{line} [{label}] — {match.group(0)[:120]}")
        assert not violations, (
            "Folgende Stellen umgehen die Event-Encryption-Pipeline. Encryption "
            "lebt in services/encryption.py und wird per Event.save() angewendet — "
            "bulk_create / .update(data_json=...) / .update_or_create(data_json=...) "
            "schreiben Klartext direkt in JSONB.\n"
            "Refs #736 / #713 (Audit-Massnahme #11).\n"
            f"Verstoesse: {violations}"
        )


class TestRetentionSubmoduleDirectionGuard:
    """``core/retention/`` darf nicht aus ``core/views/`` importieren.

    Refs #744 — Submodul-Schnitt fuer Retention-Logik. Das Submodul ist
    Service-Layer; Views duerfen es benutzen, aber nicht andersrum.
    """

    _RETENTION_DIR = Path("src/core/retention")
    _VIEW_IMPORT = re.compile(r"^\s*(from core\.views|import core\.views)", re.MULTILINE)

    def test_no_view_imports_in_retention_submodule(self):
        if not self._RETENTION_DIR.exists():
            pytest.skip(f"{self._RETENTION_DIR} nicht vorhanden")
        violations = []
        for py_file in self._RETENTION_DIR.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            source = py_file.read_text()
            if self._VIEW_IMPORT.search(source):
                violations.append(py_file.name)
        assert not violations, (
            "Diese core/retention/-Module importieren aus core/views/. "
            "Service-Layer darf keine View-Abhaengigkeiten haben.\n"
            f"Betroffen: {violations}"
        )


class TestServiceLayerDirectionGuard:
    """Models dürfen nicht modul-weit aus ``core.services`` importieren.

    Schichtregel aus der Projekt-Architektur (siehe CONTRIBUTING.md):
    Business-Logik gehört in ``services/``, nicht in Models. Modul-Level-
    Imports von Services in Models drehen die Schicht-Richtung um und
    schaffen zirkuläre Import-Risiken.

    Function-local Imports (innerhalb von Methoden) sind erlaubt und
    notwendig, um Zirkular-Imports zu vermeiden — z. B.
    ``Client.anonymize()`` (``src/core/models/client.py``)
    delegiert an ``services/clients.py:anonymize_client``.

    Refs #743 (Audit-Befund: ``Client.anonymize`` durchbrach Aggregat-Grenzen).
    """

    _MODELS_DIR = Path("src/core/models")
    # Top-of-file region: alles bis zur ersten ``class ``/``def `` Zeile.
    _SERVICE_IMPORT = re.compile(r"^\s*(from core\.services|import core\.services)", re.MULTILINE)

    def test_no_module_level_service_imports_in_models(self):
        violations = []
        for py_file in self._MODELS_DIR.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            source = py_file.read_text()
            # Truncate at first top-level class/def to ignore function-local imports.
            top_match = re.search(r"^(class|def) ", source, re.MULTILINE)
            top_region = source[: top_match.start()] if top_match else source
            if self._SERVICE_IMPORT.search(top_region):
                violations.append(py_file.name)
        assert not violations, (
            "Diese Model-Dateien importieren ``core.services`` auf Modul-Ebene. "
            "Das verstößt gegen die Schichtregel (Models ⟵ Services, nicht "
            "umgekehrt). Imports in Methoden verschieben oder Logik in den "
            "Service-Layer ziehen.\n"
            f"Betroffen: {violations}"
        )
