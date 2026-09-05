"""Architektur-Guard fuer jeden ``RunPython``-Zugriff auf RLS-Tabellen.

``src/tests/test_migration_rls_guard.py`` prueft, dass
:func:`core.utils.migration_guards.assert_migration_role_bypasses_rls` **wirkt**.
Was fehlte, war die Frage, ob er ueberall **steht**: Die Konvention „erste Zeile
jeder ``RunPython``-Funktion, die RLS-Tabellen schreibt" lebte bis hierher nur
im Modul-Docstring des Guards und in der Sorgfalt der Autorin. Die
Alt-Migrationen aus der Zeit vor Refs #1746 trugen ihn nicht — liefe eine
Neuinstallation oder ein Restore mit der NOBYPASSRLS-App-Rolle, gingen ihre
Backfills **still auf 0 Zeilen** durch, waehrend die neueren Migrationen
fail-loud abbraechen (Red-Team B-7, Refs #2114).

Dieser Guard leitet die Anforderung **ab** statt sie zu wiederholen:

1. Er findet per AST alle benannten, modul-lokalen Funktionen in
   ``migrations.RunPython(<fn>, <reverse>)``. ``RunPython.noop`` wird
   ignoriert; Lambdas und andere Attribut-Referenzen werden fail-loud als
   nicht pruefbar gemeldet, statt still aus dem Scan zu fallen.
2. Er sammelt je Einstiegsfunktion **transitiv** ueber modul-lokale Helfer
   sowohl ``apps.get_model("<app>", "<Model>")`` als auch literale
   ``core_*``-Tabellennamen in ``execute(...)``-SQL. Das umfasst direkte
   Strings, f-Strings, ``%``-Formatierungen und lokal oder auf Modulebene
   gebundene String-Ausdruecke. Dynamisch aus externen Rueckgabewerten
   erzeugte SQL-Fragmente bleiben absichtlich ausserhalb der AST-Heuristik.
3. Er vereinigt Modell- und Raw-SQL-Tabellen, loest Modelle **zur Laufzeit**
   auf ihre Tabellennamen auf und vergleicht die Menge mit den RLS-Tabellen
   der **Test-Datenbank**
   (``pg_class.relrowsecurity``). Eine gepflegte Konstanten-Liste waere genau
   die zweite Wahrheit, die auseinanderlaeuft — die RLS-Menge wird ueber
   mindestens sechs Migrationen erweitert (``0047``, ``0057``, ``0063``,
   ``0085``, ``0127``, ``0131``) und hat keine importierbare Konstante.
4. Steht der Guard nicht als **erste ausfuehrbare Anweisung** (Docstring zaehlt
   nicht) der Einstiegsfunktion, wird der Test rot und nennt die Fundstellen.

Dazu der Wirkungsnachweis: jede nachgeruestete Einstiegsfunktion wird unter
einer NOSUPERUSER/NOBYPASSRLS-Rolle aufgerufen und muss ``MigrationRoleError``
werfen — der Guard steht vor jedem ``apps.get_model``, deshalb genuegt
``apps=None``.

**Scan-Funktion mit ``root=``-Parameter** (Welle-C-Lehre): der Negativpfad
laeuft gegen ``tmp_path``, nie mit einer Probe im echten Migrationsbaum.

**Import-Falle** (Welle-D-Befund): ``tests.test_rls_functional`` traegt einen
``pytest.skip(allow_module_level=True)`` fuer mutmut-Laeufe. Der Import der
Fixtures propagiert diesen Skip auf dieses Modul — unschaedlich, weil es als
``architecture`` markiert und im Mutations-Lauf ohnehin deselektiert ist
(dieselbe Kopplung besteht seit Refs #1746 in ``test_migration_rls_guard.py``).
"""

from __future__ import annotations

import ast
import importlib
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.db import connection, transaction

from core.utils.migration_guards import MigrationRoleError
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)

pytestmark = pytest.mark.architecture

# Repo-Wurzel-Konvention der uebrigen architecture-Guards: ``src/core`` liegt
# in ``[tool.mutmut] also_copy``, der Import-Zeit-Scan ist im ``mutants/``-Tree
# also unschaedlich (``test_architecture_guards_mutation_isolation.py``).
REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "src" / "core" / "migrations"
GUARD_NAME = "assert_migration_role_bypasses_rls"
CORE_TABLE_RE = re.compile(r"\bcore_\w+\b")

#: Einstiegsfunktionen, die eine RLS-Tabelle anfassen und trotzdem KEINEN Guard
#: tragen duerfen — mit Begruendung. ``0049._create_mv`` liest ``core_event``
#: ausschliesslich, um die Materialized View aufzubauen. Deren spaeterer
#: ``REFRESH`` laeuft bewusst unter der App-Rolle; ein Migrationsrollen-Guard
#: wuerde diesen etablierten View-Laufzeitvertrag faelschlich als einmaligen
#: RLS-Backfill klassifizieren.
#: ``0084``/``0094`` (``core_user``) und ``0102``
#: (``otp_totp_totpdevice``) fallen ohne Ausnahme heraus, weil ihre Tabellen
#: keine RLS tragen.
ACKNOWLEDGED_WITHOUT_GUARD: frozenset[tuple[str, str]] = frozenset({("0049_statistics_event_flat_mv", "_create_mv")})


@dataclass(frozen=True)
class RunPythonEntry:
    """Eine ``RunPython``-Einstiegsfunktion mit dem, was sie anfasst."""

    migration: str
    function: str
    models: tuple[tuple[str, str], ...]
    raw_sql_tables: tuple[str, ...]
    guard_is_first: bool

    def __str__(self) -> str:  # pragma: no cover - nur Fehlermeldung
        return f"{self.migration}.{self.function}"


@dataclass(frozen=True)
class UnsupportedRunPythonEntry:
    """Ein Lambda-/Attribut-Einstieg, dem der Scanner nicht sicher folgen kann."""

    migration: str
    expression: str
    line: int

    def __str__(self) -> str:  # pragma: no cover - nur Fehlermeldung
        return f"{self.migration}:{self.line} ({self.expression})"


@dataclass(frozen=True)
class RunPythonScan:
    """Scannergebnis mit pruefbaren und bewusst nicht pruefbaren Einstiegen."""

    entries: tuple[RunPythonEntry, ...]
    unsupported_entries: tuple[UnsupportedRunPythonEntry, ...]


def _get_model_pairs(node: ast.AST) -> list[tuple[str, str]]:
    """``apps.get_model("core", "Client")``-Aufrufe im Teilbaum."""
    pairs = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "get_model"):
            continue
        args = [a for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if len(args) >= 2:
            pairs.append((args[0].value, args[1].value))
    return pairs


def _local_calls(node: ast.AST) -> set[str]:
    """Namen modul-lokaler Funktionen, die im Teilbaum gerufen werden."""
    names = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
            names.add(call.func.id)
    return names


def _models_transitively(fn: ast.FunctionDef, functions: dict, seen: set) -> list[tuple[str, str]]:
    """Modelle der Funktion **und** der von ihr gerufenen Modul-Helfer.

    ``0115_global_block_sequence.forward`` fasst kein Model direkt an — es ruft
    ``_renumber(apps, ...)``. Ohne diesen Durchgriff bliebe genau die Migration
    unentdeckt, die drei RLS-Tabellen umnummeriert.
    """
    if fn.name in seen:
        return []
    seen.add(fn.name)
    pairs = _get_model_pairs(fn)
    for name in _local_calls(fn):
        helper = functions.get(name)
        if helper is not None:
            pairs.extend(_models_transitively(helper, functions, seen))
    return pairs


def _named_expressions(node: ast.AST, *, recursive: bool = True) -> dict[str, ast.AST]:
    """Einfache ``name = <Ausdruck>``-Bindungen fuer literale SQL-Aufloesung."""
    expressions = {}
    candidates = ast.walk(node) if recursive else getattr(node, "body", ())
    for assignment in candidates:
        if isinstance(assignment, ast.Assign):
            for target in assignment.targets:
                if isinstance(target, ast.Name):
                    expressions[target.id] = assignment.value
        elif isinstance(assignment, ast.AnnAssign) and isinstance(assignment.target, ast.Name):
            if assignment.value is not None:
                expressions[assignment.target.id] = assignment.value
    return expressions


def _literal_strings(node: ast.AST, bindings: dict[str, ast.AST], seen: set[str]) -> list[str]:
    """Literale Teile eines Ausdrucks, inklusive gebundener String-Ausdruecke."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name) and node.id in bindings and node.id not in seen:
        return _literal_strings(bindings[node.id], bindings, seen | {node.id})
    strings = []
    for child in ast.iter_child_nodes(node):
        strings.extend(_literal_strings(child, bindings, seen))
    return strings


def _raw_sql_tables(node: ast.AST, module_bindings: dict[str, ast.AST]) -> list[str]:
    """Literale ``core_*``-Tabellen in ``execute``-Aufrufen eines Teilbaums."""
    bindings = module_bindings | _named_expressions(node)
    tables = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        is_execute = (isinstance(func, ast.Attribute) and func.attr == "execute") or (
            isinstance(func, ast.Name) and func.id == "execute"
        )
        if not is_execute:
            continue
        sql_expressions = list(call.args[:1])
        sql_expressions.extend(kw.value for kw in call.keywords if kw.arg in {"sql", "statement"})
        for expression in sql_expressions:
            for literal in _literal_strings(expression, bindings, set()):
                tables.extend(CORE_TABLE_RE.findall(literal))
    return tables


def _raw_sql_tables_transitively(
    fn: ast.FunctionDef,
    functions: dict,
    module_bindings: dict[str, ast.AST],
    seen: set,
) -> list[str]:
    """Raw-SQL-Tabellen der Funktion und ihrer modul-lokalen Helfer."""
    if fn.name in seen:
        return []
    seen.add(fn.name)
    tables = _raw_sql_tables(fn, module_bindings)
    for name in _local_calls(fn):
        helper = functions.get(name)
        if helper is not None:
            tables.extend(_raw_sql_tables_transitively(helper, functions, module_bindings, seen))
    return tables


def _guard_is_first_statement(fn: ast.FunctionDef) -> bool:
    """Steht der Guard als erste ausfuehrbare Anweisung? (Docstring zaehlt nicht.)

    Bewusst **Position**, nicht blosse Anwesenheit: ein Guard hinter dem ersten
    Schreibzugriff schuetzt nichts mehr, und ein Import darf ihm nicht
    vorausgehen — nach einem fehlgeschlagenen Import waere die Reihenfolge
    wieder unbestimmt.
    """
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if not body:
        return False
    first = body[0]
    if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Call):
        return False
    func = first.value.func
    if isinstance(func, ast.Name):
        return func.id == GUARD_NAME
    return isinstance(func, ast.Attribute) and func.attr == GUARD_NAME


def _attribute_path(node: ast.Attribute) -> tuple[str, ...]:
    """Punktpfad eines Attribut-Ausdrucks, soweit er statisch benannt ist."""
    parts = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return tuple(reversed(parts))


def _is_runpython_noop(node: ast.AST) -> bool:
    """Django-Sentinel ``RunPython.noop`` mit oder ohne Modul-Praefix."""
    return isinstance(node, ast.Attribute) and _attribute_path(node) in {
        ("RunPython", "noop"),
        ("migrations", "RunPython", "noop"),
    }


def scan_runpython_entries(root: Path) -> RunPythonScan:
    """Alle pruefbaren und nicht pruefbaren ``RunPython``-Einstiege unter *root*.

    ``root`` ist Parameter und nicht fest verdrahtet, damit der Negativpfad
    gegen ``tmp_path`` laufen kann statt eine Probe in den echten
    Migrationsbaum zu legen.
    """
    entries: list[RunPythonEntry] = []
    unsupported_entries: list[UnsupportedRunPythonEntry] = []
    for path in sorted(root.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        module_bindings = _named_expressions(tree, recursive=False)
        entry_names: list[str] = []
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            is_runpython = (isinstance(func, ast.Attribute) and func.attr == "RunPython") or (
                isinstance(func, ast.Name) and func.id == "RunPython"
            )
            if not is_runpython:
                continue
            candidates = list(call.args[:2]) + [kw.value for kw in call.keywords if kw.arg in {"code", "reverse_code"}]
            for candidate in candidates:
                if isinstance(candidate, ast.Name) and candidate.id in functions:
                    entry_names.append(candidate.id)
                elif isinstance(candidate, ast.Lambda) or (
                    isinstance(candidate, ast.Attribute) and not _is_runpython_noop(candidate)
                ):
                    unsupported_entries.append(
                        UnsupportedRunPythonEntry(
                            migration=path.stem,
                            expression=ast.unparse(candidate),
                            line=candidate.lineno,
                        )
                    )
        for name in dict.fromkeys(entry_names):
            fn = functions[name]
            entries.append(
                RunPythonEntry(
                    migration=path.stem,
                    function=name,
                    models=tuple(dict.fromkeys(_models_transitively(fn, functions, set()))),
                    raw_sql_tables=tuple(
                        sorted(set(_raw_sql_tables_transitively(fn, functions, module_bindings, set())))
                    ),
                    guard_is_first=_guard_is_first_statement(fn),
                )
            )
    return RunPythonScan(
        entries=tuple(entries),
        unsupported_entries=tuple(dict.fromkeys(unsupported_entries)),
    )


def _rls_tables() -> set[str]:
    """RLS-Tabellen der laufenden Test-Datenbank (Laufzeit, keine Kopie)."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relrowsecurity AND n.nspname = 'public'"
        )
        return {row[0] for row in cur.fetchall()}


def _table_for(app_label: str, model_name: str) -> str:
    """Tabellenname eines Models — mit Fallback fuer inzwischen entfernte Models."""
    try:
        return django_apps.get_model(app_label, model_name)._meta.db_table
    except LookupError:
        return f"{app_label}_{model_name.lower()}"


def _touched_tables(entry: RunPythonEntry) -> set[str]:
    """Alle per historischem Model und literaler SQL beruehrten Tabellen."""
    return {_table_for(app, model) for app, model in entry.models} | set(entry.raw_sql_tables)


def _touches_rls(entry: RunPythonEntry, rls_tables: set[str]) -> bool:
    """Injizierbare Entscheidung fuer echte DB-Menge und ``tmp_path``-Proben."""
    return bool(_touched_tables(entry) & rls_tables)


REPO_SCAN = scan_runpython_entries(MIGRATIONS_DIR)
REPO_ENTRIES = REPO_SCAN.entries
REPO_UNSUPPORTED_ENTRIES = REPO_SCAN.unsupported_entries


def test_no_unsupported_runpython_entry_forms():
    assert not REPO_UNSUPPORTED_ENTRIES, (
        "Nicht pruefbare RunPython-Lambda-/Attribut-Einstiege gefunden. "
        "Eine benannte Modul-Funktion verwenden, damit der RLS-Guard pruefbar ist; "
        f"RunPython.noop bleibt erlaubt:\n  {'\n  '.join(map(str, REPO_UNSUPPORTED_ENTRIES))}"
    )


@pytest.mark.postgres
@pytest.mark.django_db
class TestGuardCoverage:
    def test_scan_finds_the_known_runpython_entries(self):
        """Aufbau-Kontrolle: der Scanner sieht ueberhaupt etwas, und zwar das Richtige."""
        by_migration = {e.migration: e for e in REPO_ENTRIES}
        assert len(REPO_ENTRIES) > 30, f"Scanner findet zu wenig — kaputt? ({len(REPO_ENTRIES)})"
        # Der Durchgriff auf Modul-Helfer (``0115.forward`` -> ``_renumber``).
        forward = next(e for e in REPO_ENTRIES if e.migration.startswith("0115") and e.function == "forward")
        assert ("core", "DocumentTypeField") in forward.models, forward.models
        # ``RunPython.noop`` darf nie als Einstieg gezaehlt werden.
        assert "noop" not in {e.function for e in REPO_ENTRIES}
        assert by_migration, "keine Migration gefunden"

    def test_every_runpython_on_an_rls_table_calls_the_guard_first(self):
        rls_tables = _rls_tables()
        assert "core_client" in rls_tables, "RLS-Menge leer/kaputt — der Guard waere sonst trivial gruen"

        missing = []
        for entry in REPO_ENTRIES:
            if (entry.migration, entry.function) in ACKNOWLEDGED_WITHOUT_GUARD:
                continue
            touched = _touched_tables(entry)
            if not _touches_rls(entry, rls_tables):
                continue
            if not entry.guard_is_first:
                missing.append(f"{entry} (RLS-Tabellen: {sorted(touched & rls_tables)})")

        assert not missing, (
            "RunPython-Funktionen schreiben RLS-Tabellen ohne "
            f"{GUARD_NAME} als erste Zeile — unter falscher Migrationsrolle laufen sie "
            "still auf 0 Zeilen (Refs #2114):\n  " + "\n  ".join(missing)
        )

    def test_raw_sql_only_inventory_is_complete(self):
        """Der Bestands-Blindfleck umfasst fuenf Dateien und sechs Funktionen."""
        actual = {
            (entry.migration, entry.function)
            for entry in REPO_ENTRIES
            if not entry.models
            and entry.raw_sql_tables
            and not entry.migration.startswith("0049_statistics_event_flat_mv")
        }
        assert actual == {
            ("0130_attachment_owner_facility", "backfill_facility"),
            ("0130_attachment_owner_facility", "guard_no_owner_rows"),
            ("0131_rls_attachment", "guard_no_owner_rows"),
            ("0133_attachment_composite_fk", "guard_no_divergent_owner_facility"),
            ("0166_event_composite_fk", "guard_no_cross_facility_event_links"),
            ("0167_schema_hygiene", "guard_no_duplicate_facility_names"),
        }

    def test_raw_sql_only_rls_entries_are_guarded(self):
        """Vier Dateien/fuenf Funktionen sind davon laut DB wirklich RLS-relevant."""
        rls_tables = _rls_tables()
        actual = {
            (entry.migration, entry.function)
            for entry in REPO_ENTRIES
            if not entry.models
            and _touches_rls(entry, rls_tables)
            and (entry.migration, entry.function) not in ACKNOWLEDGED_WITHOUT_GUARD
        }
        assert actual == {
            ("0130_attachment_owner_facility", "backfill_facility"),
            ("0130_attachment_owner_facility", "guard_no_owner_rows"),
            ("0131_rls_attachment", "guard_no_owner_rows"),
            ("0133_attachment_composite_fk", "guard_no_divergent_owner_facility"),
            ("0166_event_composite_fk", "guard_no_cross_facility_event_links"),
        }
        by_key = {(entry.migration, entry.function): entry for entry in REPO_ENTRIES}
        assert all(by_key[key].guard_is_first for key in actual)

    def test_acknowledged_exceptions_still_exist(self):
        """Ausnahmen muessen existieren, RLS beruehren und wirklich guard-los sein."""
        rls_tables = _rls_tables()
        known = {(e.migration, e.function): e for e in REPO_ENTRIES}
        verwaist = sorted(ACKNOWLEDGED_WITHOUT_GUARD - set(known))
        assert not verwaist, f"Ausnahmen ohne Fundstelle: {verwaist}"
        unnoetig = sorted(
            key
            for key in ACKNOWLEDGED_WITHOUT_GUARD
            if known[key].guard_is_first or not _touches_rls(known[key], rls_tables)
        )
        assert not unnoetig, f"Ausnahmen ohne aktuelle Guard-Abweichung: {unnoetig}"


class TestScannerOnSyntheticTree:
    """Negativ- und Positivpfad gegen ``tmp_path`` — nie im echten Baum."""

    _RAW_SQL_OHNE_GUARD = """
from django.db import migrations


def _write_attachment(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"UPDATE core_attachment SET deleted_at = {marker}")


def backfill(apps, schema_editor):
    _write_attachment(schema_editor)
    schema_editor.execute("SELECT count(*) FROM core_event WHERE id = %s" % event_id)
    execute("SELECT count(*) FROM core_case")


class Migration(migrations.Migration):
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
"""

    _RAW_SQL_MIT_GUARD = """
from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def _write_attachment(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"UPDATE core_attachment SET deleted_at = {marker}")


def backfill(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    _write_attachment(schema_editor)
    schema_editor.execute("SELECT count(*) FROM core_event WHERE id = %s" % event_id)
    execute("SELECT count(*) FROM core_case")


class Migration(migrations.Migration):
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
"""

    _RAW_SQL_NUR_FACILITY = """
from django.db import migrations


def inspect_facilities(apps, schema_editor):
    schema_editor.execute("SELECT name FROM core_facility")


class Migration(migrations.Migration):
    operations = [migrations.RunPython(inspect_facilities)]
"""

    _LAMBDA = """
from django.db import migrations


class Migration(migrations.Migration):
    operations = [migrations.RunPython(lambda apps, schema_editor: None)]
"""

    _ATTRIBUT_ENTRY = """
from django.db import migrations

import other_module


class Migration(migrations.Migration):
    operations = [migrations.RunPython(other_module.backfill)]
"""

    _NUR_NOOP = """
from django.db import migrations


class Migration(migrations.Migration):
    operations = [migrations.RunPython(migrations.RunPython.noop)]
"""

    _OHNE_GUARD = '''
from django.db import migrations


def backfill(apps, schema_editor):
    """Ohne Guard."""
    Client = apps.get_model("core", "Client")
    Client.objects.all().update(notes="")


class Migration(migrations.Migration):
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
'''

    _MIT_GUARD = '''
from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def backfill(apps, schema_editor):
    """Mit Guard."""
    assert_migration_role_bypasses_rls(schema_editor)
    Client = apps.get_model("core", "Client")
    Client.objects.all().update(notes="")


class Migration(migrations.Migration):
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
'''

    _GUARD_ZU_SPAET = """
from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def backfill(apps, schema_editor):
    Client = apps.get_model("core", "Client")
    assert_migration_role_bypasses_rls(schema_editor)
    Client.objects.all().update(notes="")


class Migration(migrations.Migration):
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
"""

    def _scan_one(self, tmp_path, quelle):
        (tmp_path / "0001_probe.py").write_text(quelle, encoding="utf-8")
        scan = scan_runpython_entries(tmp_path)
        assert len(scan.entries) == 1, scan
        assert not scan.unsupported_entries, scan
        return scan.entries[0]

    def test_missing_guard_is_detected(self, tmp_path):
        entry = self._scan_one(tmp_path, self._OHNE_GUARD)
        assert entry.function == "backfill"
        assert entry.models == (("core", "Client"),)
        assert entry.guard_is_first is False

    def test_raw_sql_without_guard_is_detected(self, tmp_path):
        entry = self._scan_one(tmp_path, self._RAW_SQL_OHNE_GUARD)
        assert entry.raw_sql_tables == ("core_attachment", "core_case", "core_event")
        assert _touches_rls(entry, {"core_attachment"})
        assert entry.guard_is_first is False

    def test_raw_sql_with_guard_is_accepted(self, tmp_path):
        entry = self._scan_one(tmp_path, self._RAW_SQL_MIT_GUARD)
        assert entry.raw_sql_tables == ("core_attachment", "core_case", "core_event")
        assert _touches_rls(entry, {"core_attachment"})
        assert entry.guard_is_first is True

    def test_raw_sql_on_non_rls_table_needs_no_guard(self, tmp_path):
        entry = self._scan_one(tmp_path, self._RAW_SQL_NUR_FACILITY)
        assert entry.raw_sql_tables == ("core_facility",)
        assert not _touches_rls(entry, {"core_attachment"})

    def test_lambda_runpython_is_reported_as_unsupported(self, tmp_path):
        (tmp_path / "0001_probe.py").write_text(self._LAMBDA, encoding="utf-8")

        scan = scan_runpython_entries(tmp_path)

        assert scan.entries == ()
        assert len(scan.unsupported_entries) == 1
        assert scan.unsupported_entries[0].expression.startswith("lambda ")

    def test_attribute_runpython_is_reported_as_unsupported(self, tmp_path):
        (tmp_path / "0001_probe.py").write_text(self._ATTRIBUT_ENTRY, encoding="utf-8")

        scan = scan_runpython_entries(tmp_path)

        assert scan.entries == ()
        assert len(scan.unsupported_entries) == 1
        assert scan.unsupported_entries[0].expression == "other_module.backfill"

    def test_runpython_noop_remains_ignored(self, tmp_path):
        (tmp_path / "0001_probe.py").write_text(self._NUR_NOOP, encoding="utf-8")

        scan = scan_runpython_entries(tmp_path)

        assert scan.entries == ()
        assert scan.unsupported_entries == ()

    def test_guard_as_first_statement_is_accepted(self, tmp_path):
        assert self._scan_one(tmp_path, self._MIT_GUARD).guard_is_first is True

    def test_guard_after_the_first_write_is_not_enough(self, tmp_path):
        """Position zaehlt: ein Guard hinter dem ersten Modellzugriff schuetzt nichts."""
        assert self._scan_one(tmp_path, self._GUARD_ZU_SPAET).guard_is_first is False

    def test_empty_tree_yields_nothing(self, tmp_path):
        assert scan_runpython_entries(tmp_path) == RunPythonScan(entries=(), unsupported_entries=())


def _guarded_entries():
    """Alle Einstiege mit Guard — Parameter des Wirkungsnachweises."""
    return [e for e in REPO_ENTRIES if e.guard_is_first]


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("entry", _guarded_entries(), ids=str)
def test_guarded_migration_aborts_under_non_bypass_role(entry, rls_test_role):  # noqa: F811
    """Fail-loud-Nachweis je Einstiegsfunktion (Refs #2114).

    Der Guard steht VOR jedem ``apps.get_model`` — deshalb genuegt ``apps=None``:
    kaeme die Funktion bis zur ersten Modellauflösung, schluege sie mit
    ``AttributeError`` statt ``MigrationRoleError`` fehl und der Test waere rot.
    Das ist die Absicht: „Guard steht vorne" wird hier ausgefuehrt, nicht nur
    gelesen.
    """
    module = importlib.import_module(f"core.migrations.{entry.migration}")
    fn = getattr(module, entry.function)

    with transaction.atomic(), as_rls_role(rls_test_role, facility_id=""):
        with connection.schema_editor() as schema_editor:
            with pytest.raises(MigrationRoleError):
                fn(None, schema_editor)
