"""Architektur-Guard: JEDE ``RunPython``-Funktion auf einer RLS-Tabelle ruft den Rollen-Guard (Refs #2114).

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

1. Er findet per AST alle ``migrations.RunPython(<fn>, <reverse>)``-Argumente —
   ``RunPython.noop`` wird ignoriert.
2. Er sammelt je Einstiegsfunktion **transitiv** ueber modul-lokale
   Hilfsfunktionen die ``apps.get_model("<app>", "<Model>")``-Aufrufe
   (``0115_global_block_sequence`` ruft seine Models erst in ``_renumber``).
3. Er loest die Modelle **zur Laufzeit** auf ihre Tabellennamen auf und
   vergleicht sie mit der RLS-Menge der **Test-Datenbank**
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

#: Einstiegsfunktionen, die eine RLS-Tabelle anfassen und trotzdem KEINEN Guard
#: tragen duerfen — mit Begruendung. Leer: es gibt heute keine solche Ausnahme.
#: Bewusst **nicht** in dieser Menge (und deshalb auch nicht guard-pflichtig):
#: ``0049`` (Materialized View, kein Model), ``0084``/``0094`` (``core_user`` —
#: Auth-Grenze, bewusst ohne RLS) und ``0102`` (``otp_totp_totpdevice``). Sie
#: fallen von selbst heraus, weil ihre Tabellen keine RLS tragen; eine
#: Hand-Ausnahme waere dort eine Behauptung statt einer Ableitung.
ACKNOWLEDGED_WITHOUT_GUARD: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class RunPythonEntry:
    """Eine ``RunPython``-Einstiegsfunktion mit dem, was sie anfasst."""

    migration: str
    function: str
    models: tuple[tuple[str, str], ...]
    guard_is_first: bool

    def __str__(self) -> str:  # pragma: no cover - nur Fehlermeldung
        return f"{self.migration}.{self.function}"


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


def scan_runpython_entries(root: Path) -> list[RunPythonEntry]:
    """Alle ``RunPython``-Einstiegsfunktionen unter *root*.

    ``root`` ist Parameter und nicht fest verdrahtet, damit der Negativpfad
    gegen ``tmp_path`` laufen kann statt eine Probe in den echten
    Migrationsbaum zu legen.
    """
    entries: list[RunPythonEntry] = []
    for path in sorted(root.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
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
            candidates = list(call.args) + [kw.value for kw in call.keywords]
            for candidate in candidates:
                # ``migrations.RunPython.noop`` ist ein Attribute-Ausdruck und
                # damit automatisch draussen — nur benannte Funktionen zaehlen.
                if isinstance(candidate, ast.Name) and candidate.id in functions:
                    entry_names.append(candidate.id)
        for name in dict.fromkeys(entry_names):
            fn = functions[name]
            entries.append(
                RunPythonEntry(
                    migration=path.stem,
                    function=name,
                    models=tuple(dict.fromkeys(_models_transitively(fn, functions, set()))),
                    guard_is_first=_guard_is_first_statement(fn),
                )
            )
    return entries


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


REPO_ENTRIES = scan_runpython_entries(MIGRATIONS_DIR)


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
            touched = {_table_for(app, model) for app, model in entry.models}
            if not touched & rls_tables:
                continue
            if not entry.guard_is_first:
                missing.append(f"{entry} (RLS-Tabellen: {sorted(touched & rls_tables)})")

        assert not missing, (
            "RunPython-Funktionen schreiben RLS-Tabellen ohne "
            f"{GUARD_NAME} als erste Zeile — unter falscher Migrationsrolle laufen sie "
            "still auf 0 Zeilen (Refs #2114):\n  " + "\n  ".join(missing)
        )

    def test_acknowledged_exceptions_still_exist(self):
        """Eine Ausnahme fuer eine geloeschte Funktion waere eine tote Zusage."""
        bekannt = {(e.migration, e.function) for e in REPO_ENTRIES}
        verwaist = sorted(ACKNOWLEDGED_WITHOUT_GUARD - bekannt)
        assert not verwaist, f"Ausnahmen ohne Fundstelle: {verwaist}"


class TestScannerOnSyntheticTree:
    """Negativ- und Positivpfad gegen ``tmp_path`` — nie im echten Baum."""

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
        entries = scan_runpython_entries(tmp_path)
        assert len(entries) == 1, entries
        return entries[0]

    def test_missing_guard_is_detected(self, tmp_path):
        entry = self._scan_one(tmp_path, self._OHNE_GUARD)
        assert entry.function == "backfill"
        assert entry.models == (("core", "Client"),)
        assert entry.guard_is_first is False

    def test_guard_as_first_statement_is_accepted(self, tmp_path):
        assert self._scan_one(tmp_path, self._MIT_GUARD).guard_is_first is True

    def test_guard_after_the_first_write_is_not_enough(self, tmp_path):
        """Position zaehlt: ein Guard hinter dem ersten Modellzugriff schuetzt nichts."""
        assert self._scan_one(tmp_path, self._GUARD_ZU_SPAET).guard_is_first is False

    def test_empty_tree_yields_nothing(self, tmp_path):
        assert scan_runpython_entries(tmp_path) == []


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
