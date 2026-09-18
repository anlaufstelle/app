"""Architecture-Guard — keine veralteten ``EMAIL_*``-Settings neben ``MAILERS`` (Refs #1662).

Django 6.1 verbietet die Kombination aus ``MAILERS`` und den deprecateten
``EMAIL_*``-*Settings*: ``django.conf`` wirft beim ersten Settings-Zugriff
``ImproperlyConfigured: Deprecated email settings are not allowed when MAILERS
is defined: …``. Ein Settings-Overlay, das ``MAILERS`` erbt (``from …prod
import *``) und danach z. B. ``EMAIL_BACKEND`` setzt, startet also nicht mehr —
und zwar erst beim Deploy, weil kein Test ein Instanz-Overlay importiert.

Der Guard scannt deshalb ALLE Settings-Module des Repos (die ausgelieferten
unter ``src/anlaufstelle/settings/`` und die Instanz-Overlays unter
``dev-ops/deploy/``) per AST — ohne sie zu importieren (die Overlays brauchen
eine vollstaendige Prod-ENV).

Erlaubt bleiben ENV-Variablen-NAMEN: ``os.environ.get("EMAIL_HOST", …)`` ist
eine Zeichenkette, kein Setting. Verboten sind nur Zuweisungen AN ein
veraltetes ``EMAIL_*``-Setting auf Modulebene.

``architecture``-Marker: der Test liest Dateien ausserhalb ``src/`` (Lehre 93)
und scannt den Quellbaum.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]

# Von Django 6.1 deprecatete Mail-Settings (Entfernung in 7.0). Zuweisungen an
# diese Namen sind neben ``MAILERS`` verboten.
DEPRECATED_EMAIL_SETTINGS = frozenset(
    {
        "EMAIL_BACKEND",
        "EMAIL_HOST",
        "EMAIL_PORT",
        "EMAIL_HOST_USER",
        "EMAIL_HOST_PASSWORD",
        "EMAIL_USE_TLS",
        "EMAIL_USE_SSL",
        "EMAIL_TIMEOUT",
    }
)
# ``EMAIL_SSL_CERTFILE``/``EMAIL_SSL_KEYFILE`` und kuenftige Geschwister.
DEPRECATED_EMAIL_PREFIX = "EMAIL_SSL_"

# Module, die ``MAILERS`` definieren, ohne selbst im Scan-Set zu liegen bzw.
# deren Name die Herkunft eindeutig macht (``from …prod import *``).
_MAILERS_BASE_MODULES = frozenset({"prod", "base"})


def _is_deprecated_email_setting(name: str) -> bool:
    return name in DEPRECATED_EMAIL_SETTINGS or name.startswith(DEPRECATED_EMAIL_PREFIX)


def settings_module_paths(root: Path) -> list[Path]:
    """Alle Settings-Module unterhalb ``root`` — per Glob, nicht hart verdrahtet.

    ``dev-ops/`` fehlt im ausgelieferten Baum (``build-release.sh`` schliesst
    es aus); der Glob liefert dort schlicht nichts.
    """
    found = set(root.glob("src/anlaufstelle/settings/*.py"))
    found.update(root.glob("dev-ops/deploy/**/*_settings.py"))
    return sorted(found)


def _star_import_targets(tree: ast.Module) -> set[str]:
    """Modul-Basisnamen, aus denen das Modul per ``import *`` erbt."""
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            module = node.module or ""
            base = module.rsplit(".", 1)[-1]
            if base:
                targets.add(base)
    return targets


def _assigned_names(tree: ast.Module) -> set[str]:
    """Namen, denen auf Modulebene zugewiesen wird."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _deprecated_assignments(tree: ast.Module) -> list[tuple[str, int]]:
    """``(Setting, Zeile)`` je Zuweisung an ein veraltetes ``EMAIL_*``-Setting.

    Auch verschachtelte Zuweisungen (``if``/``try``-Bloecke) zaehlen — Django
    sieht am Ende nur das Modul-Namespace-Dict.
    """
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and _is_deprecated_email_setting(target.id):
                hits.append((target.id, target.lineno))
    return sorted(hits)


def scan_settings_modules(root: Path) -> dict[Path, list[tuple[str, int]]]:
    """Settings-Module mit ``MAILERS`` UND veralteten ``EMAIL_*``-Zuweisungen.

    Rueckgabe: Pfad → Liste der Verstoesse ``(Setting, Zeile)``. Ein Modul gilt
    als ``MAILERS``-behaftet, wenn es ``MAILERS`` selbst setzt oder (transitiv)
    aus einem Modul erbt, das es tut.
    """
    paths = settings_module_paths(root)
    trees: dict[Path, ast.Module] = {}
    for path in paths:
        trees[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    defines: dict[str, bool] = {}
    parents: dict[str, set[str]] = {}
    for path, tree in trees.items():
        key = path.stem
        defines[key] = "MAILERS" in _assigned_names(tree)
        parents[key] = _star_import_targets(tree)

    def _has_mailers(key: str, seen: frozenset[str] = frozenset()) -> bool:
        if key in seen:
            return False
        if defines.get(key):
            return True
        if key not in parents:
            # Unbekanntes Elternmodul: ``prod``/``base`` tragen die
            # ``MAILERS``-Konfiguration des Projekts.
            return key in _MAILERS_BASE_MODULES
        return any(_has_mailers(parent, seen | {key}) for parent in parents[key])

    violations: dict[Path, list[tuple[str, int]]] = {}
    for path, tree in trees.items():
        if not _has_mailers(path.stem):
            continue
        hits = _deprecated_assignments(tree)
        if hits:
            violations[path] = hits
    return violations


class TestSettingsModuleDiscovery:
    """Der Scan darf nicht still ins Leere laufen."""

    def test_src_settings_are_found(self) -> None:
        paths = settings_module_paths(REPO_ROOT)
        stems = {path.stem for path in paths}
        assert {"base", "prod", "dev", "devlive"} <= stems, (
            f"Settings-Glob findet die ausgelieferten Module nicht (gefunden: {sorted(stems)}) — "
            "ein Null-Treffer-Erfolg wuerde den Guard entwerten (Refs #1662)"
        )

    def test_instance_overlays_are_scanned_when_present(self) -> None:
        """``dev-ops/`` fehlt im Public-Snapshot — dort ist der Teil moot."""
        overlay_dir = REPO_ROOT / "dev-ops" / "deploy"
        if not overlay_dir.exists():
            pytest.skip("dev-ops/ nicht im Public-Snapshot — Overlay-Scan nur im Dev-Repo relevant (Refs #1662)")
        overlays = [path for path in settings_module_paths(REPO_ROOT) if "dev-ops" in path.parts]
        assert overlays, (
            "dev-ops/deploy/ ist vorhanden, der Glob findet aber kein Instanz-Overlay "
            "(*_settings.py) — Scan-Muster pruefen (Refs #1662)"
        )


class TestNoDeprecatedEmailSettingsBesidesMailers:
    """Refs #1662: Ein Overlay mit ``MAILERS`` + ``EMAIL_*`` startet nicht mehr."""

    def test_repo_is_clean(self) -> None:
        violations = scan_settings_modules(REPO_ROOT)
        rendered = "; ".join(
            f"{path.relative_to(REPO_ROOT)}: " + ", ".join(f"{name} (Zeile {line})" for name, line in hits)
            for path, hits in sorted(violations.items())
        )
        assert not violations, (
            "Settings-Module kombinieren MAILERS mit veralteten EMAIL_*-Settings — Django 6.1 wirft beim "
            f"Start ImproperlyConfigured, die Instanz kommt nach dem Deploy nicht mehr hoch: {rendered}. "
            "Konfiguration stattdessen in MAILERS setzen (Refs #1662)"
        )


class TestScannerSelfCheck:
    """Selbsttest gegen ``tmp_path`` — nie Proben im echten Baum (Standardblock 3)."""

    def _write(self, root: Path, relative: str, source: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def _base_tree(self, root: Path) -> None:
        self._write(
            root,
            "src/anlaufstelle/settings/base.py",
            "SECRET_KEY = 'x'\n",
        )
        self._write(
            root,
            "src/anlaufstelle/settings/prod.py",
            "from .base import *\n\nMAILERS = {'default': {'BACKEND': 'smtp'}}\n",
        )

    def test_flags_inherited_mailers_with_email_backend(self, tmp_path: Path) -> None:
        self._base_tree(tmp_path)
        overlay = self._write(
            tmp_path,
            "dev-ops/deploy/instanz/instanz_settings.py",
            "from anlaufstelle.settings.prod import *\n\nEMAIL_BACKEND = 'console'\n",
        )
        violations = scan_settings_modules(tmp_path)
        assert overlay in violations
        assert violations[overlay] == [("EMAIL_BACKEND", 3)]

    def test_flags_own_mailers_with_email_host(self, tmp_path: Path) -> None:
        self._base_tree(tmp_path)
        module = self._write(
            tmp_path,
            "src/anlaufstelle/settings/eigen.py",
            "MAILERS = {}\nEMAIL_HOST = 'localhost'\n",
        )
        assert scan_settings_modules(tmp_path)[module] == [("EMAIL_HOST", 2)]

    def test_flags_transitively_inherited_mailers(self, tmp_path: Path) -> None:
        self._base_tree(tmp_path)
        self._write(
            tmp_path,
            "src/anlaufstelle/settings/zwischen.py",
            "from .prod import *\n",
        )
        module = self._write(
            tmp_path,
            "src/anlaufstelle/settings/kind.py",
            "from .zwischen import *\n\nEMAIL_USE_SSL = True\n",
        )
        assert module in scan_settings_modules(tmp_path)

    def test_env_variable_names_are_allowed(self, tmp_path: Path) -> None:
        self._base_tree(tmp_path)
        self._write(
            tmp_path,
            "dev-ops/deploy/instanz/instanz_settings.py",
            "import os\n\nfrom anlaufstelle.settings.prod import *\n\n"
            "_host = os.environ.get('EMAIL_HOST', 'localhost')\n"
            "MAILERS = {'default': {'OPTIONS': {'host': _host}}}\n",
        )
        assert scan_settings_modules(tmp_path) == {}

    def test_module_without_mailers_is_not_flagged(self, tmp_path: Path) -> None:
        """Ohne ``MAILERS`` ist ``EMAIL_BACKEND`` (noch) zulaessig — Django wirft nicht."""
        self._write(tmp_path, "src/anlaufstelle/settings/base.py", "SECRET_KEY = 'x'\n")
        self._write(
            tmp_path,
            "src/anlaufstelle/settings/alt.py",
            "from .base import *\n\nEMAIL_BACKEND = 'console'\n",
        )
        assert scan_settings_modules(tmp_path) == {}

    def test_deprecated_ssl_prefix_is_covered(self, tmp_path: Path) -> None:
        self._base_tree(tmp_path)
        module = self._write(
            tmp_path,
            "src/anlaufstelle/settings/zert.py",
            "from .prod import *\n\nEMAIL_SSL_CERTFILE = '/tmp/c.pem'\n",
        )
        assert module in scan_settings_modules(tmp_path)
