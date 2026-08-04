"""Architecture-Guards — Service-Layer-Direction- und Encryption-Bypass-Guards (Refs #929)."""

import ast
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
#   Case   — M2 P5 (Refs #1058)
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
    """Refs #736 / #713 (Event), M2 P4b Refs #1058 (Client), M2 P5 (Case):
    Verhindert, dass irgendein Code-Pfad die Feldverschluesselungs-Pipeline
    umgeht.

    Die Verschluesselung haengt an ``Model.save()``
    (``DynamicDataEncryptionMixin._encrypt_sensitive_fields``) — jeder
    Schreibpfad, der ``save()`` umgeht, schreibt Klartext direkt ins JSONB.
    Die Modellliste (:data:`ENCRYPTED_DATA_JSON_MODELS`) ist der einzige Ort,
    an dem ein weiteres Modell eingetragen werden muss.

    Allowlist (legitime Bypaesse):
    - ``src/core/seed/`` — Seed-Daten sind deterministische Test-Fixtures
      ohne reale Art-9-Inhalte (nutzen ``encrypt_dynamic_data`` vor dem Bulk)
    - ``src/core/migrations/`` — Schema-Migrationen
    - ``src/tests/`` — Testfixtures duerfen direkt schreiben

    Erweiterung der Allowlist erfordert separaten Commit + Begruendung.
    """

    _CORE_DIR = Path("src/core")
    _ALLOWLIST = (
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


class TestKAnonBridgeRedactionParity:
    """Der K-Anon-Retention-Pfad muss dieselbe Redaktionskaskade fahren wie der Hard-Pfad.

    Root Cause von #1750 war **Parity-Drift**: [#1094] holte die Case-/Episoden-/
    WorkItem-Kaskade in die Bridge (``core/retention/anonymization.py``), die
    danach in ``anonymize_client`` aufgenommenen Helfer ([#1067], [#1089],
    [#1090], [#1091]) wurden dort aber nie nachgezogen. Ein per K-Anonymisierung
    „anonymisierter" Klient (``k_anonymized=True``) trug deshalb weiter Klartext
    in ``EventHistory``/``DeletionRequest``/``Activity`` — Art.-17-relevant, weil
    die Anwendung die Tilgung selbst zusichert.

    Dieser Guard ist bewusst **ableitungsbasiert** statt einer gepflegten Liste:
    er liest die Helfer-Menge direkt aus ``anonymize_client`` (Single Source of
    Truth) und verlangt, dass die Bridge jeden davon aufruft. Ein NEUER Helfer im
    Hard-Pfad reisst den Test, bis er entweder in der Bridge landet oder hier mit
    einer am Code verifizierbaren Begruendung ausgenommen wird — genau die
    Drift, die #1750 verursacht hat, kann so nicht unbemerkt wiederkehren.
    """

    _HARD_PATH_MODULE = Path("src/core/services/client/main.py")
    _HARD_PATH_FUNC = "anonymize_client"
    _BRIDGE_MODULE = Path("src/core/retention/anonymization.py")

    # Helfer des Hard-Pfads, die in der Bridge BEWUSST fehlen. Jeder Eintrag
    # traegt eine Begruendung, die an der Eintrittsbedingung der Bridge haengt
    # (``total_events > 0`` UND ``active_events == 0``) — aendert sich die,
    # muessen diese Ausnahmen neu bewertet werden.
    _BRIDGE_EXEMPT = {
        "_redact_client_identity": (
            "``k_anonymize_client`` redigiert die Client-Zeile selbst "
            "(pseudonym/notes/data_json/document_type/is_active). "
            "``_redact_client_identity`` wuerde zusaetzlich ``age_cluster`` auf "
            "UNKNOWN setzen und damit den Quasi-Identifikator zerstoeren, auf dem "
            "die k-Anonymitaets-Zusicherung beruht."
        ),
        "_redact_live_events": (
            "Die Bridge verarbeitet nur Klienten mit ``active_events == 0`` — es "
            "gibt per Eintrittsbedingung keine Live-Events; der Event-Soft-Delete "
            "hat ``data_json``/``search_text`` bereits geleert."
        ),
        "_delete_event_attachments_for_client": (
            "Nur der EREIGNIS-Zweig ist entbehrlich: Ereignis-Anhaenge sind zum "
            "Bridge-Zeitpunkt bereits geloescht, weil beide Soft-Delete-Pfade "
            "(``soft_delete_event`` und ``retention.enforcement``) "
            "``delete_attachments_for`` rufen und die Eintrittsbedingung "
            "``active_events == 0`` genau das erzwingt. Seit M24 (Refs #1738, "
            "ADR-039) traegt die Bridge die personen-/falleigenen Anhaenge "
            "dagegen selbst ab — ueber ``_delete_owned_attachments_for_client``, "
            "denselben Helfer wie der Hard-Pfad (fuer sie gibt es keinen "
            "vorgelagerten Loeschpfad). Beide Haelften sind in "
            "``test_pii_residue_sweep.py`` "
            "(``test_attachments_gone_after_retention_k_anonymization``) gepinnt, "
            "damit eine geaenderte Eintrittsbedingung nicht still zur Luecke wird."
        ),
    }

    @staticmethod
    def _private_calls(node: ast.AST) -> set[str]:
        """Namen aller aufgerufenen Modul-privaten Helfer (``_foo(...)``) unter *node*."""
        return {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id.startswith("_")
        }

    @classmethod
    def _hard_path_helpers(cls) -> set[str]:
        tree = ast.parse(cls._HARD_PATH_MODULE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == cls._HARD_PATH_FUNC:
                return cls._private_calls(node)
        raise AssertionError(f"{cls._HARD_PATH_FUNC} nicht in {cls._HARD_PATH_MODULE} gefunden")

    @classmethod
    def _bridge_helpers(cls) -> set[str]:
        # Ganzes Modul statt nur ``anonymize_clients``: ein spaeterer Refactor
        # darf die Kaskade in einen eigenen Bridge-Helfer ziehen, ohne dass der
        # Guard falsch rot wird.
        return cls._private_calls(ast.parse(cls._BRIDGE_MODULE.read_text(encoding="utf-8")))

    def test_extraction_finds_the_hard_path_cascade(self):
        """Positiv-Kontrolle: die AST-Ableitung liefert die bekannten Helfer.

        Ohne diesen Anker waere der Paritaets-Test still gruen, sobald die
        Extraktion (z.B. nach einem Refactor der Aufrufform) leer zurueckkaeme.
        """
        helpers = self._hard_path_helpers()
        assert {
            "_redact_client_identity",
            "_redact_cases_and_episodes",
            "_redact_workitems",
            "_redact_event_history",
            "_redact_deletion_requests",
            "_redact_client_deletion_request",
            "_redact_activities",
        } <= helpers, f"AST-Ableitung unvollstaendig: {sorted(helpers)}"

    def test_bridge_calls_every_hard_path_redactor(self):
        missing = self._hard_path_helpers() - self._bridge_helpers() - set(self._BRIDGE_EXEMPT)
        assert not missing, (
            "Der K-Anon-Retention-Pfad (core/retention/anonymization.py) ruft diese "
            "Redaktions-Helfer des Hard-Pfads NICHT auf — ein als anonymisiert "
            "markierter Klient behaelt damit Klartext-PII (Art. 17, Refs #1750):\n"
            f"{sorted(missing)}\n"
            "Entweder in der Bridge ergaenzen oder in _BRIDGE_EXEMPT mit "
            "nachpruefbarer Begruendung eintragen."
        )

    def test_no_stale_exemptions(self):
        stale = set(self._BRIDGE_EXEMPT) - self._hard_path_helpers()
        assert not stale, (
            f"_BRIDGE_EXEMPT nennt Helfer, die ``{self._HARD_PATH_FUNC}`` gar nicht (mehr) aufruft: {sorted(stale)}"
        )


class TestZentraleSuppressionsfunktion:
    """Jedes Berichts-Artefakt mit Randsumme + Aufschluesselung benutzt DIESELBE
    Suppressionsmaschinerie (K1/K2-Regressionsschutz, Refs #1770).

    Die Disclosure-Analyse zu #1742
    hat drei Fehlerklassen belegt, die ein statischer Guard und nur ein
    statischer Guard zuverlaessig ausschliesst:

    1. **Eigenbau-Suppression.** ``ExampleBackend`` trug eine zweite,
       pauschalere Regel („faellt eine Zelle, faellt auch ``total``") neben der
       zentralen. Solange jede Kopie fuer sich richtig ist, faellt das nie auf —
       bis eine Haertung nur in einer Kopie landet (genau die Parity-Drift, die
       #1750 erzeugt hat).
    2. **Fail-open bei neuer Zeilenform (K5).** Die kanonische Ordnung wurde
       frueher an der Zeilen*form* erkannt. Eine kuenftige count-sortierte
       Tabelle mit anderer Form waere still unkanonisiert geblieben — kein Test
       waere rot geworden, weil kein Test die neue Tabelle kannte. Deshalb ist
       ``order`` heute ein Pflicht-Keyword, und dieser Guard prueft, dass jede
       Aufrufstelle es setzt.
    3. **Fehlende Randsummen-Deklaration (K2).** Ohne ``margin`` weiss das
       Machbarkeits-Audit nicht, ob es eine Gleichung gibt, an der es rechnen
       kann. Ein Default waere in beide Richtungen falsch: ``None`` liesse eine
       echte Randsumme ungeschuetzt, ein geratener Wert wuerde ohne Not
       unterdruecken.

    Der Guard ist AST-basiert und deshalb unabhaengig von Formatierung und
    Argument-Reihenfolge; er nennt bei jedem Verstoss Datei und Zeile.
    """

    _CENTRAL_MODULE = Path("src/core/services/dashboard/external_report.py")
    _BACKEND_DIR = Path("src/core/reporting/backends")
    # Fuer die Eigenbau-Suche: der ganze Anwendungsbaum, nicht nur die
    # Backends. Ein neues Artefakt entsteht genauso gut in ``views/`` oder
    # ``services/`` — die Fehlerklasse haengt am Marker, nicht am Verzeichnis.
    _APP_DIR = Path("src/core")
    # Aufrufstellen ausserhalb des zentralen Moduls, die suppressieren duerfen.
    _SUPPRESSION_CALLS = ("_suppress_small", "_suppress_stage_dict")
    # Pflicht-Keywords je Suppressionsfunktion.
    _REQUIRED_KEYWORDS = {"_suppress_small": {"margin", "order"}, "_suppress_stage_dict": {"margin", "persons"}}

    @property
    def _KNOWN_ORDERS(self) -> set[str]:  # noqa: N802 — Konstantenname bleibt, Herkunft wird abgeleitet
        """Erlaubte Werte fuer ``order`` — **abgeleitet** aus ``_CANONICAL_ORDERS``.

        Eine neue Ordnung muss im zentralen Modul definiert **und** dort
        registriert werden, nicht an der Aufrufstelle erfunden. Die Ableitung
        (statt einer zweiten Liste hier) verhindert genau die Drift, die eine
        handgepflegte Kopie erzeugt: eine registrierte Ordnung, die der Guard
        nicht kennt, oder umgekehrt.
        """
        from core.services.dashboard import external_report

        registriert = set(external_report._CANONICAL_ORDERS)
        return {
            name
            for name in dir(external_report)
            if name.startswith("ORDER_") and getattr(external_report, name) in registriert
        }

    @staticmethod
    def _calls(path: Path, name: str) -> list[ast.Call]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
        ]

    def _suppression_modules(self) -> list[Path]:
        """Zentrales Modul + alle Reporting-Backends (die Artefakt-Erzeuger)."""
        return [self._CENTRAL_MODULE, *sorted(self._BACKEND_DIR.glob("*.py"))]

    # --- Guard-Logik, von den Guards UND der Positiv-Kontrolle aufgerufen ---
    # Refs #1775: Vorher stand die Erkennung inline in den Testkoerpern und die
    # "Positiv-Kontrolle" parste einen eigenen AST-Schnipsel — sie belegte damit
    # nur, dass ``ast`` funktioniert, nie dass der Guard trifft. Als gemeinsame
    # Methode laeuft in der Kontrolle dieselbe Logik wie im Guard.

    @classmethod
    def _fehlende_pflicht_keywords(cls, modules) -> list[str]:
        violations = []
        for module in modules:
            for function, required in cls._REQUIRED_KEYWORDS.items():
                for call in cls._calls(module, function):
                    given = {keyword.arg for keyword in call.keywords if keyword.arg}
                    missing = required - given
                    if missing:
                        violations.append(f"{module}:{call.lineno} {function}() ohne {sorted(missing)}")
        return violations

    def _fremde_ordnungen(self, modules) -> list[str]:
        violations = []
        for module in modules:
            for call in self._calls(module, "_suppress_small"):
                for keyword in call.keywords:
                    if keyword.arg != "order":
                        continue
                    if not isinstance(keyword.value, ast.Name) or keyword.value.id not in self._KNOWN_ORDERS:
                        violations.append(f"{module}:{call.lineno} order={ast.unparse(keyword.value)}")
        return violations

    @staticmethod
    def _eigenbau_unterdrueckungen(modules) -> list[str]:
        violations = []
        for module in modules:
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value == "suppressed"
                        ):
                            violations.append(f"{module}:{node.lineno} setzt den Unterdrueckungs-Marker selbst")
                if isinstance(node, ast.Dict):
                    for key in node.keys:
                        if isinstance(key, ast.Constant) and key.value == "suppressed":
                            violations.append(f"{module}:{node.lineno} baut eine Zeile mit eigenem ``suppressed``")
        return violations

    def test_jede_aufrufstelle_deklariert_randsumme_und_ordnung(self):
        violations = self._fehlende_pflicht_keywords(self._suppression_modules())
        assert not violations, (
            "Diese Aufrufstellen der zentralen Suppression lassen ein Pflicht-Keyword weg. "
            "``margin`` sagt, ob es eine publizierte Randsumme gibt, an der das "
            "Machbarkeits-Audit rechnen kann (K2); ``order`` benennt die kanonische "
            "Ausgabeordnung unterdrueckter Zeilen (K5). Beides fail-closed, damit eine "
            "neue Tabelle nicht still ungeschuetzt bleibt:\n" + "\n".join(violations)
        )

    def test_order_kommt_aus_der_zentralen_liste(self):
        violations = self._fremde_ordnungen(self._suppression_modules())
        assert not violations, (
            "``order`` muss eine der zentral definierten Ordnungen sein "
            f"({sorted(self._KNOWN_ORDERS)}) — eine an der Aufrufstelle erfundene "
            "Ordnung entzieht sich dem K5-Schutz:\n" + "\n".join(violations)
        )

    def test_kein_artefakt_baut_eigene_unterdrueckung(self):
        """Ausserhalb des zentralen Moduls darf niemand ``suppressed`` setzen.

        Geprueft wird das Setzen des Markers (``row["suppressed"] = ...`` bzw.
        ``suppressed=...`` in einem Dict-Literal) — Lesen ist erlaubt und
        ueblich (Templates, Rueckprojektion der Records).
        """
        kandidaten = [
            module
            for module in sorted(self._APP_DIR.rglob("*.py"))
            if module != self._CENTRAL_MODULE and "migrations" not in module.parts
        ]
        violations = self._eigenbau_unterdrueckungen(kandidaten)
        assert not violations, (
            "Nur core/services/dashboard/external_report.py darf ueber Unterdrueckung "
            "entscheiden. Eine zweite Kopie der Regel driftet garantiert — genau das "
            "war der Befund hinter K2 (``_suppress_stage_dict`` trug eine eigene "
            "Sekundaer-Heuristik):\n" + "\n".join(violations)
        )

    def test_jedes_backend_mit_randsumme_ruft_den_residual_guard(self):
        """Ein ``prepare_export``, das eine Randsumme publiziert, muss den
        Fixpunkt-Guard aufrufen — sonst bleibt die Rueckrechnung offen.

        Erkannt wird die Randsumme daran, dass ``prepare_export`` ein
        ``summary`` schreibt; die Ausnahme fuer Backends ohne Randsumme
        (``summary`` unveraendert durchgereicht) faellt damit automatisch heraus.
        """
        violations = []
        for module in sorted(self._BACKEND_DIR.glob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.FunctionDef) and node.name == "prepare_export"):
                    continue
                source = ast.unparse(node)
                if '"summary"' not in source and "summary=" not in source and "summary[" not in source:
                    continue
                calls = {
                    child.func.id
                    for child in ast.walk(node)
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                }
                if "apply_residual_guard" in calls:
                    continue
                # Wrapper, die die zentrale Suppression komplett delegieren
                # (``suppress_sample_report_1_stats`` ruft den Guard intern),
                # sind erfuellt.
                if calls & {"suppress_sample_report_1_stats", "suppress_report_stats", "build_external_report"}:
                    continue
                violations.append(f"{module}:{node.lineno} prepare_export ohne Residual-Guard")
        assert not violations, (
            "Diese Backends publizieren eine Randsumme neben ihrer Aufschluesselung, "
            "ohne den zentralen Fixpunkt-Guard (K1) zu durchlaufen:\n" + "\n".join(violations)
        )

    def test_synthetischer_verstoss_wird_erkannt(self, tmp_path):
        """Positiv-Kontrolle: die drei GUARDS muessen ihren Trefferfall zeigen.

        Refs #1775: Vorher parste dieser Test einen eigenen AST-Schnipsel und
        pruefte damit nur, dass ``ast`` noch funktioniert — die Guard-Logik
        selbst lief nie mit. Belegt per Mutationsprobe: ``missing = required -
        given`` zu ``given - required`` zu entkernen liess ALLE Tests dieser
        Klasse gruen. Jetzt bekommen die Guards ein synthetisches Modul mit
        genau den Verstoessen zu sehen, gegen die sie schuetzen sollen.
        """
        modul = tmp_path / "verstoss_backend.py"
        modul.write_text(
            "ORDER_ERFUNDEN = 'an der Aufrufstelle erfunden'\n"
            "rows = _suppress_small(rows, threshold)\n"
            "stage = _suppress_stage_dict(stage, margin=total)\n"
            "mehr = _suppress_small(rows, margin=total, order=ORDER_ERFUNDEN)\n"
            'zeile["suppressed"] = True\n'
            'eigenbau = {"suppressed": False}\n',
            encoding="utf-8",
        )

        fehlende = self._fehlende_pflicht_keywords([modul])
        assert len(fehlende) == 2, fehlende
        assert "_suppress_small() ohne ['margin', 'order']" in fehlende[0]
        assert "_suppress_stage_dict() ohne ['persons']" in fehlende[1]

        fremde = self._fremde_ordnungen([modul])
        assert len(fremde) == 1, fremde
        assert "order=ORDER_ERFUNDEN" in fremde[0]

        eigenbau = self._eigenbau_unterdrueckungen([modul])
        assert len(eigenbau) == 2, eigenbau
        assert "setzt den Unterdrueckungs-Marker selbst" in eigenbau[0]
        assert "baut eine Zeile mit eigenem ``suppressed``" in eigenbau[1]

    def test_die_suche_findet_die_bekannten_aufrufstellen(self):
        """Zweiter Anker: die Menge der gefundenen Aufrufstellen darf nicht
        stillschweigend auf null schrumpfen."""
        total = sum(
            len(self._calls(module, function))
            for module in self._suppression_modules()
            for function in self._SUPPRESSION_CALLS
        )
        assert total >= 6, f"nur {total} Aufrufstellen gefunden — AST-Suche pruefen"
