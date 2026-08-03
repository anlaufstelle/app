"""Architecture-Guards — Service-Layer-Direction- und Encryption-Bypass-Guards (Refs #929)."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture


# Modelle mit ``document_type``-gesteuertem, feldverschluesseltem ``data_json``
# (:class:`core.models.mixins.DynamicDataEncryptionMixin`). Die Bypass-Patterns
# werden pro Eintrag generiert — ein neues Modell traegt sich hier mit EINER
# Zeile ein und ist damit vollstaendig abgedeckt.
#
#   Event  — Refs #736 / #713
#   Client — M2 P4b (Refs #1058)
#   Case   — folgt in M2 P5 (Refs #1058), dann hier ergaenzen
ENCRYPTED_DATA_JSON_MODELS = ("Event", "Client", "Case")


def _bypass_patterns(model: str) -> tuple[tuple[str, re.Pattern], ...]:
    """Die vier verbotenen Schreibpfade fuer ein verschluesseltes ``data_json``.

    Alle umgehen ``Model.save()`` — und damit
    ``_encrypt_sensitive_fields()`` — und schreiben Klartext direkt ins JSONB:

    1. ``<M>.objects.bulk_create(...)`` — kein ``save()``-Hook, daher
       **unbedingt** verboten (unabhaengig von den gesetzten Feldern).
    2. ``<M>.objects[.filter(...)…].update(data_json=...)`` — Raw-Update.
    3. ``<M>.objects.update_or_create(..., data_json=...)`` — ebenfalls
       ohne ``save()``-Hook auf dem Update-Zweig.
    4. ``<M>.objects[.filter(...)…].bulk_update(..., ["data_json"])`` — der
       Bulk-Schreibpfad, der ``save()`` genauso umgeht wie ``bulk_create``,
       hier aber nur bei ``data_json``-Beteiligung anschlaegt (ein
       ``bulk_update`` auf Statusfelder ist legitim).

    Die ``\\.update\\(``-Klausel matcht ``update`` exakt (nicht
    ``update_or_create``, weil dort ``_or_create`` zwischen ``update`` und
    ``(`` steht). ``(?:\\.[a-zA-Z_]+\\([^)]*\\))*`` erlaubt gechainte
    QuerySet-Methoden zwischen ``objects`` und dem Schreib-Aufruf und
    limitiert das Matching zugleich auf eine sinnvolle Distanz.
    """
    chain = r"(?:\.[a-zA-Z_]+\([^)]*\))*"
    return (
        (f"{model}.objects.bulk_create", re.compile(rf"{model}\.objects\.bulk_create\b")),
        (f"{model}.objects.update(data_json=...)", re.compile(rf"{model}\.objects{chain}\.update\([^)]*\bdata_json\b")),
        (
            f"{model}.objects.update_or_create(data_json=...)",
            re.compile(rf"{model}\.objects\.update_or_create\([^)]*\bdata_json\b"),
        ),
        (
            f"{model}.objects.bulk_update(data_json)",
            re.compile(rf"{model}\.objects{chain}\.bulk_update\([^)]*\bdata_json\b"),
        ),
    )


BYPASS_PATTERNS = tuple(pattern for model in ENCRYPTED_DATA_JSON_MODELS for pattern in _bypass_patterns(model))


def find_bypasses(source: str, origin: str = "<inline>") -> list[str]:
    """Alle Encryption-Bypaesse in einem Quelltext (Datei-unabhaengig).

    Als eigenstaendige Funktion, damit die Selbsttests des Guards synthetische
    Verstoesse einspeisen koennen, ohne eine Datei im Baum anzulegen — ein
    Guard, der seinen eigenen Trefferfall nie beweist, kann still verrotten.
    """
    hits = []
    for label, pattern in BYPASS_PATTERNS:
        for match in pattern.finditer(source):
            line = source[: match.start()].count("\n") + 1
            hits.append(f"{origin}:{line} [{label}] — {match.group(0)[:120]}")
    return hits


class TestDynamicDataEncryptionBypassGuard:
    """Refs #736 / #713 (Event), M2 P4b Refs #1058 (Client): Verhindert, dass
    irgendein Code-Pfad die Feldverschluesselungs-Pipeline umgeht.

    Die Verschluesselung haengt an ``Model.save()``
    (``DynamicDataEncryptionMixin._encrypt_sensitive_fields``) — jeder
    Schreibpfad, der ``save()`` umgeht, schreibt Klartext direkt ins JSONB.
    Seit P4b ist ``Client`` der zweite solche Store; die Modellliste
    (:data:`ENCRYPTED_DATA_JSON_MODELS`) ist der einzige Ort, an dem ein
    weiteres Modell (P5: ``Case``) eingetragen werden muss.

    Allowlist (legitime Bypaesse):
    - ``src/core/services/encryption.py`` — die Encryption-Pipeline selbst
    - ``src/core/seed/`` — Seed-Daten sind deterministische Test-Fixtures
      ohne reale Art-9-Inhalte (nutzen ``encrypt_dynamic_data`` vor dem Bulk)
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

    def _is_allowlisted(self, path: Path) -> bool:
        return any(path == allow or allow in path.parents for allow in self._ALLOWLIST)

    def test_no_dynamic_data_encryption_bypass(self):
        if not self._CORE_DIR.exists():
            pytest.skip(f"{self._CORE_DIR} nicht vorhanden")
        violations = []
        for py_file in self._CORE_DIR.rglob("*.py"):
            if self._is_allowlisted(py_file):
                continue
            violations.extend(find_bypasses(py_file.read_text(errors="ignore"), origin=str(py_file)))
        assert not violations, (
            "Folgende Stellen umgehen die Feldverschluesselungs-Pipeline. Die "
            "Verschluesselung lebt in services/file_vault/encryption.py und wird "
            "per <Model>.save() angewendet — bulk_create / .update(data_json=...) / "
            ".update_or_create(data_json=...) / .bulk_update(..., data_json) "
            "schreiben Klartext direkt in JSONB.\n"
            "Refs #736 / #713 (Event), Refs #1058 (Client).\n"
            f"Verstoesse: {violations}"
        )

    @pytest.mark.parametrize("model", ENCRYPTED_DATA_JSON_MODELS)
    @pytest.mark.parametrize(
        "snippet",
        [
            "{model}.objects.bulk_create([obj])",
            '{model}.objects.filter(facility=f).update(data_json={{"a": 1}})',
            '{model}.objects.update_or_create(pk=p, defaults={{"data_json": {{}}}})',
            '{model}.objects.bulk_update(objs, ["data_json"])',
        ],
    )
    def test_synthetic_violation_is_detected(self, model, snippet):
        """Trefferbeweis: jeder Bypass-Pfad schlaegt fuer JEDES gelistete Modell an.

        Ohne diesen Selbsttest waere ein neu eingetragenes Modell (oder ein
        kaputtes Regex) nicht von „Baum ist sauber" zu unterscheiden.
        """
        assert find_bypasses(snippet.format(model=model)), f"Guard uebersieht Bypass: {snippet.format(model=model)}"

    @pytest.mark.parametrize("model", ENCRYPTED_DATA_JSON_MODELS)
    def test_legitimate_writes_are_not_flagged(self, model):
        """Gegenprobe: Schreibpfade ohne ``data_json`` bleiben erlaubt.

        Sonst wuerde der Guard legitime Massen-Updates (Soft-Delete,
        Status-Flags) blockieren und zur Rauschquelle werden.
        """
        legit = "\n".join(
            [
                f"{model}.objects.filter(facility=f).update(is_deleted=True)",
                f'{model}.objects.bulk_update(objs, ["is_deleted"])',
                f"{model}.objects.update_or_create(pk=p, defaults={{'is_deleted': True}})",
                "obj.data_json = {}\nobj.save(update_fields=['data_json'])",
            ]
        )
        assert not find_bypasses(legit)


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

    Refs #743 (``Client.anonymize`` durchbrach Aggregat-Grenzen).
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
