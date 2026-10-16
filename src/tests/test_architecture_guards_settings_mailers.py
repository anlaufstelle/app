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
from django import conf as django_conf

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]

# Statischer Fallback fuer kuenftige Django-Versionen, die das interne Attribut
# nicht mehr anbieten. Der Selbsttest unten verhindert Drift gegen die aktuell
# installierte Laufzeitmenge.
STATIC_DEPRECATED_EMAIL_SETTINGS = frozenset(
    {
        "EMAIL_BACKEND",
        "EMAIL_FILE_PATH",
        "EMAIL_HOST",
        "EMAIL_PORT",
        "EMAIL_HOST_USER",
        "EMAIL_HOST_PASSWORD",
        "EMAIL_SSL_CERTFILE",
        "EMAIL_SSL_KEYFILE",
        "EMAIL_USE_TLS",
        "EMAIL_USE_SSL",
        "EMAIL_TIMEOUT",
    }
)
DEPRECATED_EMAIL_SETTINGS = frozenset(
    getattr(django_conf, "DEPRECATED_EMAIL_SETTINGS", STATIC_DEPRECATED_EMAIL_SETTINGS)
)

# Module, die ``MAILERS`` definieren, ohne selbst im Scan-Set zu liegen bzw.
# deren Name die Herkunft eindeutig macht (``from …prod import *``).
_MAILERS_BASE_MODULES = frozenset({"prod", "base"})


def _is_deprecated_email_setting(name: str) -> bool:
    return name in DEPRECATED_EMAIL_SETTINGS


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


def _target_names(target: ast.expr) -> list[tuple[str, int]]:
    """Statische Namen eines Zuweisungsziels, inklusive Unpacking."""
    if isinstance(target, ast.Name):
        return [(target.id, target.lineno)]
    if isinstance(target, (ast.List, ast.Tuple)):
        return [name for element in target.elts for name in _target_names(element)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if (
        isinstance(target, ast.Subscript)
        and isinstance(target.value, ast.Call)
        and isinstance(target.value.func, ast.Name)
        and target.value.func.id == "globals"
        and not target.value.args
        and not target.value.keywords
        and isinstance(target.slice, ast.Constant)
        and isinstance(target.slice.value, str)
    ):
        return [(target.slice.value, target.lineno)]
    return []


def _setattr_current_module_name(node: ast.Call, sys_names: set[str]) -> tuple[str, int] | None:
    """Erkenne nur ``setattr(sys.modules[__name__], 'NAME', value)``."""
    if not isinstance(node.func, ast.Name) or node.func.id != "setattr" or len(node.args) != 3 or node.keywords:
        return None
    owner, setting, _value = node.args
    if (
        not isinstance(owner, ast.Subscript)
        or not isinstance(owner.value, ast.Attribute)
        or owner.value.attr != "modules"
        or not isinstance(owner.value.value, ast.Name)
        or owner.value.value.id not in sys_names
        or not isinstance(owner.slice, ast.Name)
        or owner.slice.id != "__name__"
        or not isinstance(setting, ast.Constant)
        or not isinstance(setting.value, str)
    ):
        return None
    return setting.value, node.lineno


def _deprecated_assignments(tree: ast.Module) -> list[tuple[str, int]]:
    """``(Setting, Zeile)`` je Zuweisung an ein veraltetes ``EMAIL_*``-Setting.

    Auch verschachtelte Zuweisungen (``if``/``try``-Bloecke) zaehlen — Django
    sieht am Ende nur das Modul-Namespace-Dict.
    """
    sys_names = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "sys"
    }
    candidates: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif (isinstance(node, ast.AnnAssign) and node.value is not None) or isinstance(
            node, (ast.AugAssign, ast.NamedExpr)
        ):
            targets = [node.target]
        for target in targets:
            candidates.extend(_target_names(target))
        if isinstance(node, ast.Call):
            setattr_name = _setattr_current_module_name(node, sys_names)
            if setattr_name is not None:
                candidates.append(setattr_name)
    return sorted((name, line) for name, line in candidates if _is_deprecated_email_setting(name))


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
    """Selbsttest gegen ``tmp_path`` — nie Proben im echten Baum."""

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

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("EMAIL_HOST, other = ('smtp', 1)\n", [("EMAIL_HOST", 2)]),
            ("[EMAIL_PORT, other] = [25, 1]\n", [("EMAIL_PORT", 2)]),
            (
                "(a, (EMAIL_HOST_USER, b)), *rest = (1, (2, 3)), 4\n",
                [("EMAIL_HOST_USER", 2)],
            ),
            ("other = EMAIL_BACKEND = 'smtp'\n", [("EMAIL_BACKEND", 2)]),
            ("EMAIL_USE_TLS: bool = True\n", [("EMAIL_USE_TLS", 2)]),
            ("EMAIL_TIMEOUT += 1\n", [("EMAIL_TIMEOUT", 2)]),
            ("(EMAIL_HOST := 'smtp')\n", [("EMAIL_HOST", 2)]),
            ("globals()['EMAIL_HOST'] = 'smtp'\n", [("EMAIL_HOST", 2)]),
            (
                "import sys\nsetattr(sys.modules[__name__], 'EMAIL_HOST', 'smtp')\n",
                [("EMAIL_HOST", 3)],
            ),
        ],
        ids=[
            "tuple-target",
            "list-target",
            "nested-starred-target",
            "multiple-assignment",
            "annotated-assignment",
            "augmented-assignment",
            "walrus",
            "globals-subscript",
            "setattr-current-module",
        ],
    )
    def test_flags_all_assignment_forms(
        self,
        tmp_path: Path,
        source: str,
        expected: list[tuple[str, int]],
    ) -> None:
        self._base_tree(tmp_path)
        module = self._write(
            tmp_path,
            "src/anlaufstelle/settings/probe.py",
            "from .prod import *\n" + source,
        )
        assert scan_settings_modules(tmp_path).get(module) == expected

    @pytest.mark.parametrize(
        "source",
        [
            "import os\n_host = os.environ.get('EMAIL_HOST', 'localhost')\n",
            "import os\n_host = os.getenv('EMAIL_HOST', 'localhost')\n",
            "options = {'EMAIL_HOST': 'localhost', 'EMAIL_TIMEOUT': 10}\n",
            "message = 'EMAIL_HOST = localhost'\n# EMAIL_TIMEOUT = 10\n",
            "EMAIL_USE_TLS: bool\n",
        ],
        ids=["os-environ-get", "os-getenv", "dict-keys", "strings-comments", "annotation-without-value"],
    )
    def test_allows_non_assignments(self, tmp_path: Path, source: str) -> None:
        self._base_tree(tmp_path)
        self._write(
            tmp_path,
            "src/anlaufstelle/settings/probe.py",
            "from .prod import *\n" + source,
        )
        assert scan_settings_modules(tmp_path) == {}


def test_static_deprecated_email_settings_cover_django_runtime() -> None:
    """Der Fallback darf bei einer Django-Erweiterung nicht still veralten."""
    runtime_names = frozenset(django_conf.DEPRECATED_EMAIL_SETTINGS)
    assert runtime_names <= STATIC_DEPRECATED_EMAIL_SETTINGS


@pytest.mark.parametrize("email_host", ["", "smtp.example.invalid"])
def test_demo_mail_delivery_stays_on_console_with_smtp_configured(email_host):
    import subprocess
    import sys

    from tests.test_devlive_settings import _devlive_env

    env = _devlive_env(
        {
            "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.demo",
            "EMAIL_HOST": email_host,
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings;"
            "from django.core.mail import get_connection, send_mail;"
            "assert settings.MAILERS['default'] == {'BACKEND': 'django.core.mail.backends.console.EmailBackend'};"
            "assert 'mail.E001' in settings.SILENCED_SYSTEM_CHECKS;"
            "assert get_connection().__class__.__module__ == 'django.core.mail.backends.console';"
            "assert send_mail('Demo mail', 'Console only', 'demo@example.invalid', ['recipient@example.invalid']) == 1",
        ],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "Demo mail" in result.stdout
