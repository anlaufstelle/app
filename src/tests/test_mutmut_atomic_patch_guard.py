"""Guard fuer den ``@transaction.atomic``-Patch des Mutationslaufs (Refs #2100).

Maintainer-Entscheid 2026-09-05 zu
https://github.com/anlaufstelle/app/issues/2100: Option C — mutmut
ueberspringt dekorierte Funktionen strukturell, und
``scripts/dev/run_mutmut.py:_patch_mutmut_atomic_decorators`` hebt genau diesen
Sperrgrund fuer ``@transaction.atomic`` zur Laufzeit auf. Damit werden die 57
datenveraendernden Dienste des Scopes (Merge, Unmerge, Loeschgenehmigung,
Anonymisierung, Rektifikation, Verarbeitungseinschraenkung, Recovery-Token)
ueberhaupt erst gemessen.

Ein Monkey-Patch gegen fremde Interna ist genau so lange etwas wert, wie er
wirkt. Bricht er still, verliert der Voll-Lauf 2 715 Mutanten und das Gate
sieht trotzdem gruen aus. Dieser Guard haelt deshalb drei Dinge fest:

* **Versions-/Signatur-Pin:** die gepatchte Stelle in
  ``mutmut/mutation/file_mutation.py`` sieht aus wie erwartet, und die
  installierte mutmut-Version ist die, gegen die der Patch geprueft wurde. Ein
  Bump ohne Recheck faellt hier auf →
  https://github.com/anlaufstelle/app/issues/2150.
* **Wirkungsnachweis:** eine ``@transaction.atomic``-Probe erzeugt ohne Patch
  null Mutanten und mit Patch mehr als null — rein in-memory, ohne
  ``mutants/``-Baum und ohne Datenbank.
* **Gleichlauf mit dem Blindstellen-Report:**
  ``scripts/dev/mutation_skipped_functions.py`` zaehlt dieselben Dekoratoren
  nicht mehr als unsichtbar (85 -> 28 dekorierte unsichtbare Funktionen).

Der Delta-/Triage-Nachlauf zu den neu sichtbaren Mutanten haengt an
https://github.com/anlaufstelle/app/issues/2149.

``pytestmark = architecture``: der Test liest ``scripts/dev/`` und importiert
mutmut — beides fehlt im ``mutants/``-Tree bzw. gehoert nicht in den
Mutationslauf (Muster ``test_ops_backup_path_resolution.py``). Die
Modul-Level-Skips davor sind die von
``test_architecture_guards_mutation_isolation.py`` geforderte Kapselung.
"""

from __future__ import annotations

import ast
import importlib.metadata
import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "dev" / "run_mutmut.py"
SKIPPED_REPORT = REPO_ROOT / "scripts" / "dev" / "mutation_skipped_functions.py"

if not RUNNER.exists() or not SKIPPED_REPORT.exists():
    pytest.skip("dev-only scripts/dev/ fehlt (Release-Snapshot)", allow_module_level=True)

file_mutation = pytest.importorskip(
    "mutmut.mutation.file_mutation",
    reason="mutmut ist eine dev-Abhaengigkeit und fehlt im ausgelieferten Baum",
)
libcst = pytest.importorskip("libcst", reason="libcst kommt mit mutmut")

RECHECK = "https://github.com/anlaufstelle/app/issues/2150"

#: (Quelltext-Schnipsel, wird der Rumpf mutiert?) — die Vertragstabelle der
#: Ausnahme. Beide Implementierungen (libcst im Runner, ``ast`` im Report)
#: muessen sie identisch beantworten.
DEKORATOR_FAELLE: tuple[tuple[str, str, bool], ...] = (
    ("attribut", "@transaction.atomic", True),
    ("name", "@atomic", True),
    ("call", '@transaction.atomic(using="default")', True),
    ("call_savepoint", "@transaction.atomic(savepoint=False)", True),
    ("durable", "@transaction.atomic(durable=True)", False),
    ("fremdes_modul", "@db.atomic", False),
    ("property", "@property", False),
    ("contextmanager", "@contextmanager", False),
)


def _lade_modul(pfad: Path, name: str):
    """dev-only Script als Modul laden, ohne seinen ``__main__``-Block zu fahren."""
    spec = importlib.util.spec_from_file_location(name, pfad)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _quelle(dekorator: str) -> str:
    return f"{dekorator}\ndef probe(wert):\n    return wert + 1\n"


@pytest.fixture
def runner(mutmut_unveraendert):
    """Der Wrapper, frisch geladen — die Klasse wird danach zurueckgesetzt."""
    return _lade_modul(RUNNER, "run_mutmut_guard")


@pytest.fixture
def report_modul():
    return _lade_modul(SKIPPED_REPORT, "mutation_skipped_guard")


@pytest.fixture
def mutmut_unveraendert():
    """mutmuts Sperre und Trampolin-Aufbau vor/nach dem Test im Original halten."""
    original = file_mutation.MutationVisitor._skip_node_and_children
    original_trampolin = file_mutation.function_trampoline_arrangement
    gepatchte_stellen = (
        ("_skip_node_and_children", original),
        ("function_trampoline_arrangement", original_trampolin),
    )
    for stelle, funktion in gepatchte_stellen:
        assert not getattr(funktion, "_anlaufstelle_atomic_patch", False), (
            f"mutmut ist beim Testeintritt an {stelle} bereits gepatcht — ein frueherer Test hat nicht aufgeraeumt."
        )
    try:
        yield original
    finally:
        file_mutation.MutationVisitor._skip_node_and_children = original
        file_mutation.function_trampoline_arrangement = original_trampolin


def _mutanten(dekorator: str) -> list[str]:
    return list(file_mutation.mutate_file_contents("probe.py", _quelle(dekorator)).mutant_names)


# ── (a) Versions- und Signatur-Pin ──────────────────────────────────────────
def test_installierte_mutmut_version_ist_die_geprueffte(report_modul) -> None:
    """Ein mutmut-Bump ohne Recheck des Patches faellt hier auf (Refs #2100)."""
    installiert = importlib.metadata.version("mutmut")
    assert installiert == report_modul.MUTMUT_VERSION, (
        f"mutmut {installiert} installiert, geprueft wurde {report_modul.MUTMUT_VERSION}. "
        f"Der @transaction.atomic-Patch in {RUNNER.name} und die nachgebaute Regel im "
        f"Blindstellen-Report brauchen einen Recheck: {RECHECK}"
    )


def test_gepatchte_mutmut_stelle_sieht_aus_wie_erwartet(runner, mutmut_unveraendert) -> None:
    """Fingerabdruck der Dekorator-Sperre in ``file_mutation.py``."""
    quelltext = inspect.getsource(mutmut_unveraendert)
    fehlend = [zeile for zeile in runner.MUTMUT_DECORATOR_BLOCK_FINGERPRINT if zeile not in quelltext]
    assert not fehlend, (
        "Die von Refs #2100 gepatchte mutmut-Sperre hat sich geaendert — fehlende Zeilen: "
        f"{fehlend}. Patch pruefen statt anpassen: {RECHECK}"
    )


def test_patch_bricht_laut_ab_wenn_die_sperre_nicht_mehr_greift(runner, mutmut_unveraendert) -> None:
    """Fail-loud statt still weiterlaufen: eine wirkungslose Sperre ist ein Abbruch."""

    def _nie_blockieren(self, node) -> bool:
        return False

    file_mutation.MutationVisitor._skip_node_and_children = _nie_blockieren
    with pytest.raises(runner.MutmutAtomicPatchError) as fehler:
        runner._patch_mutmut_atomic_decorators()
    assert RECHECK in str(fehler.value)


# ── (b) Wirkungsnachweis ohne Datenbank ─────────────────────────────────────
def test_ohne_patch_erzeugt_atomic_keine_mutanten(runner, mutmut_unveraendert) -> None:
    """Die Blindstelle aus Refs #2098, an der Probe reproduziert."""
    assert runner.mutmut_atomic_probe_mutant_count() == 0
    assert _mutanten("@transaction.atomic") == []


def test_mit_patch_entstehen_mutanten_fuer_atomic(runner, mutmut_unveraendert) -> None:
    assert runner.mutmut_atomic_probe_mutant_count() == 0
    runner._patch_mutmut_atomic_decorators()
    assert runner.mutmut_atomic_probe_mutant_count() > 0


@pytest.mark.parametrize(("fall", "dekorator", "sichtbar"), DEKORATOR_FAELLE, ids=[f[0] for f in DEKORATOR_FAELLE])
def test_patch_gibt_genau_die_atomic_formen_frei(runner, mutmut_unveraendert, fall, dekorator, sichtbar) -> None:
    assert _mutanten(dekorator) == [], f"{fall}: mutmut sperrt vor dem Patch bereits nicht mehr"
    runner._patch_mutmut_atomic_decorators()
    assert bool(_mutanten(dekorator)) is sichtbar, f"{fall}: unerwartete Sichtbarkeit nach dem Patch"


def test_argumente_des_atomic_dekorators_werden_nie_mutiert(runner, mutmut_unveraendert) -> None:
    """mutmut-Grund 2 bleibt sachlich erfuellt (Refs #2100).

    Ohne den Schutz des Dekorator-Teilbaums entstuende
    ``@transaction.atomic(using="XXdefaultXX")`` — der Import der GANZEN
    mutierten Datei braeche, nicht nur dieser eine Mutant.
    """
    runner._patch_mutmut_atomic_decorators()
    code = file_mutation.mutate_file_contents("probe.py", _quelle('@transaction.atomic(using="default")')).code
    assert "XXdefaultXX" not in code
    assert "using=None" not in code
    assert code.count('@transaction.atomic(using="default")') >= 3


def test_patch_ist_idempotent(runner, mutmut_unveraendert) -> None:
    runner._patch_mutmut_atomic_decorators()
    erste = file_mutation.MutationVisitor._skip_node_and_children
    runner._patch_mutmut_atomic_decorators()
    assert file_mutation.MutationVisitor._skip_node_and_children is erste


def test_pragma_no_mutate_bleibt_wirksam(runner, mutmut_unveraendert) -> None:
    """Der Patch hebt genau einen Sperrgrund auf, nicht die anderen."""
    runner._patch_mutmut_atomic_decorators()
    quelle = "@transaction.atomic\ndef probe(wert):  # pragma: no mutate block\n    return wert + 1\n"
    assert list(file_mutation.mutate_file_contents("probe.py", quelle).mutant_names) == []


# ── (b2) Query-Neutralitaet: genau EINE atomic-Ebene pro Aufruf ─────────────
SYNTHETISCHE_QUELLE = (
    "from django.db import transaction\n\n\n@transaction.atomic\ndef dienst(wert):\n    return wert + 1\n"
)


def _erzeuge_mutantenquelle(tmp_path: Path):
    """Die synthetische Probe durch mutmut schicken — Datei liegt in ``tmp_path``.

    Nie im echten Baum ablegen: der Mutations-Scanner laeuft
    ueber ``src/`` und wuerde die Probe sonst mitzaehlen.
    """
    quelle = tmp_path / "probe_atomic_dienst.py"
    quelle.write_text(SYNTHETISCHE_QUELLE, encoding="utf-8")
    return file_mutation.mutate_file_contents(str(quelle), quelle.read_text(encoding="utf-8"))


def test_trampolin_wird_nicht_zusaetzlich_in_atomic_gewickelt(runner, mutmut_unveraendert, tmp_path) -> None:
    """Genau eine ``atomic``-Ebene pro Aufruf (Refs #2100, Checkpoint-Befund 2026-09-06).

    mutmuts ``function_trampoline_arrangement`` legt die Dekoratoren der
    Originalfunktion SOWOHL auf das Trampolin ALS AUCH auf jede Kopie. Bei
    ``@transaction.atomic`` heisst das: pro Aufruf ein zusaetzliches
    ``SAVEPOINT``/``RELEASE`` — die exakten Query-Pins des Projekts
    (``test_restriction_lock_order.py::TestGateQueryCost``) messen das als
    16 statt 14 Queries und reissen den Stats-Lauf.

    Verteilung: Trampolin UNdekoriert, Kopien (``_orig`` +
    Mutanten) behalten ``@transaction.atomic`` — das ist der Code unter Test.
    """
    runner._patch_mutmut_atomic_decorators()
    ergebnis = _erzeuge_mutantenquelle(tmp_path)
    code = ergebnis.code

    trampolin = runner.dekoratoren_ueber(code, "dienst")
    assert trampolin == ["@_mutmut_mutated(mutants_x_dienst__mutmut)"], (
        "Das Trampolin traegt neben dem Dispatcher-Dekorator noch einen weiteren — "
        f"gefunden: {trampolin}. Zwei geschachtelte atomic-Bloecke kosten pro Aufruf "
        "zwei zusaetzliche Queries (SAVEPOINT/RELEASE)."
    )

    kopien = ["x_dienst__mutmut_orig", *ergebnis.mutant_names]
    assert len(kopien) > 1, "Die Probe erzeugt keine Mutanten — der Patch wirkt nicht"
    for name in kopien:
        assert runner.dekoratoren_ueber(code, name) == ["@transaction.atomic"], (
            f"{name}: die Kopie unter Test muss genau eine atomic-Ebene behalten"
        )


def test_genau_eine_atomic_ebene_pro_aufrufpfad(runner, mutmut_unveraendert, tmp_path) -> None:
    """Zaehlnachweis: je Aufrufpfad (Trampolin -> Kopie) genau ein ``atomic``."""
    runner._patch_mutmut_atomic_decorators()
    ergebnis = _erzeuge_mutantenquelle(tmp_path)
    for name in ["x_dienst__mutmut_orig", *ergebnis.mutant_names]:
        ebenen = runner.dekoratoren_ueber(ergebnis.code, "dienst").count("@transaction.atomic")
        ebenen += runner.dekoratoren_ueber(ergebnis.code, name).count("@transaction.atomic")
        assert ebenen == 1, f"Aufrufpfad dienst -> {name}: {ebenen} atomic-Ebenen statt genau einer"


def test_undekorierte_funktionen_behalten_ihr_trampolin(runner, mutmut_unveraendert, tmp_path) -> None:
    """Der Zusatzpatch fasst nur ``atomic``-Funktionen an."""
    runner._patch_mutmut_atomic_decorators()
    quelle = tmp_path / "probe_schlicht.py"
    quelle.write_text("def dienst(wert):\n    return wert + 1\n", encoding="utf-8")
    code = file_mutation.mutate_file_contents(str(quelle), quelle.read_text(encoding="utf-8")).code
    assert runner.dekoratoren_ueber(code, "dienst") == ["@_mutmut_mutated(mutants_x_dienst__mutmut)"]
    assert runner.dekoratoren_ueber(code, "x_dienst__mutmut_orig") == []


def test_trampolin_stelle_in_mutmut_sieht_aus_wie_erwartet(runner) -> None:
    """Fingerabdruck von ``function_trampoline_arrangement`` (Recheck bei Bump)."""
    quelltext = inspect.getsource(file_mutation.function_trampoline_arrangement)
    fehlend = [zeile for zeile in runner.MUTMUT_TRAMPOLINE_BLOCK_FINGERPRINT if zeile not in quelltext]
    assert not fehlend, (
        "Der von Refs #2100 gepatchte Trampolin-Aufbau hat sich geaendert — fehlende Zeilen: "
        f"{fehlend}. Patch pruefen statt anpassen: {RECHECK}"
    )


# ── (c) Gleichlauf mit dem Blindstellen-Report ──────────────────────────────
@pytest.mark.parametrize(("fall", "dekorator", "sichtbar"), DEKORATOR_FAELLE, ids=[f[0] for f in DEKORATOR_FAELLE])
def test_blindstellen_report_folgt_derselben_regel(report_modul, fall, dekorator, sichtbar) -> None:
    """``make mutation-skipped`` zaehlt genau die Formen nicht mehr, die der Patch freigibt."""
    knoten = ast.parse(_quelle(dekorator)).body[0]
    assert report_modul._is_skipped_decorated_function(knoten) is (not sichtbar), (
        f"{fall}: Blindstellen-Report und Monkey-Patch sind auseinandergelaufen (Refs #2100)"
    )


def test_report_und_runner_erkennen_dieselben_dekoratoren(runner, report_modul, mutmut_unveraendert) -> None:
    """libcst-Fassung (Runner) und ast-Fassung (Report) muessen deckungsgleich sein."""
    for fall, dekorator, _sichtbar in DEKORATOR_FAELLE:
        cst_knoten = libcst.parse_module(_quelle(dekorator)).body[0].decorators[0].decorator
        ast_knoten = ast.parse(_quelle(dekorator)).body[0].decorator_list[0]
        assert runner.ist_atomic_dekorator(cst_knoten) is report_modul._is_atomic_decorator(ast_knoten), (
            f"{fall}: die beiden Fassungen der atomic-Erkennung widersprechen sich"
        )


def test_stapel_aus_zwei_dekoratoren_bleibt_unsichtbar(report_modul, runner, mutmut_unveraendert) -> None:
    """mutmuts ``len(decorators) == 1``-Bedingung bleibt unangetastet."""
    quelle = "@audit\n@transaction.atomic\ndef probe(wert):\n    return wert + 1\n"
    runner._patch_mutmut_atomic_decorators()
    assert list(file_mutation.mutate_file_contents("probe.py", quelle).mutant_names) == []
    assert report_modul._is_skipped_decorated_function(ast.parse(quelle).body[0]) is True
