"""Architecture-Guard: der Tailwind-Build im Image sieht alle Klassenquellen (Refs #2387).

Tailwind erzeugt nur Klassen, die es in seinen Quellen findet. Im lokalen
Arbeitsbaum erkennt der CLI-Lauf zusätzlich jede Datei unterhalb des
Arbeitsverzeichnisses — im Image existieren in der Node-Stufe des
``Dockerfile`` dagegen nur die ausdrücklich kopierten Pfade. Fehlt dort ein
Verzeichnis, fehlen dessen Klassen in der ausgelieferten ``styles.css``, obwohl
der lokale Build sie enthält.

Genau so waren die per JavaScript erzeugten Hinweis-Toasts (``bottom-4`` steht
nur in ``src/static/js/``) im Image unsichtbar: ``position: fixed`` ohne
``bottom`` legt das Element an seine statische Position unterhalb des
Seitenendes.

Der Guard prüft:

1. Jeder ``content``-Glob der mit Node ausgewerteten ``tailwind.config.js``
   ist in der Node-Stufe vor dem Build-Schritt kopiert.
2. Jede JavaScript-Datei unter ``src/static/js/``, die Klassen für erzeugte
   DOM-Knoten setzt (``className = …``, ``classList.add(…)``), ist von einem
   ``content``-Glob erfasst und in der Node-Stufe kopiert — auch dann, wenn
   ``content`` den Pfad gar nicht nennt.
3. Jede Kopie landet relativ zum ``WORKDIR`` unter ihrem eigenen Pfad (dort
   sucht Tailwind), und ``.dockerignore`` nimmt keinen dieser Pfade aus dem
   Build-Kontext.
"""

from __future__ import annotations

import fnmatch
import json
import posixpath
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]

_DOM_CLASS_RE = re.compile(r"\.className\s*=(?!=)|\.classList\.(?:add|toggle|replace)\(")
_BRACE_RE = re.compile(r"\{([^{}]*)\}")
_GLOB_CHARS = set("*?[{")

# Wertet die Config so aus, wie Tailwind sie lädt (``require``), statt die
# ``content``-Liste aus dem Quelltext zu lesen — Kommentare, Anführungszeichen,
# Template-Strings und Variablen löst so Node selbst auf.
_NODE_READ_CONTENT = "process.stdout.write(JSON.stringify(require(process.argv[1]).content))"
_NODE_TIMEOUT_SECONDS = 30


def _normalise(path: str) -> str:
    path = path.strip()
    if path.startswith("!"):
        return "!" + _normalise(path[1:])
    if path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def tailwind_content_globs(root: Path) -> list[str]:
    """Die ``content``-Globs der ausgewerteten ``tailwind.config.js`` (ohne ``./``-Präfix)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node nicht gefunden — die Tailwind-Config lässt sich nur mit Node auswerten")
    result = subprocess.run(
        [node, "-e", _NODE_READ_CONTENT, str(root / "tailwind.config.js")],
        capture_output=True,
        text=True,
        timeout=_NODE_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0 and "Cannot find module 'tailwindcss" in result.stderr:
        pytest.skip("node_modules ohne tailwindcss — vorher npm ci ausführen")
    assert result.returncode == 0, f"tailwind.config.js ließ sich nicht auswerten:\n{result.stderr}"
    content = json.loads(result.stdout or "null")
    assert isinstance(content, list) and all(isinstance(entry, str) for entry in content), (
        f"content muss eine Liste von Pfad-Strings sein, ist aber {content!r}"
    )
    return [_normalise(glob) for glob in content]


def _node_stage(root: Path) -> list[str]:
    """Die Zeilen der Node-Stufe bis vor den Build-Schritt (``RUN npm ci``).

    Kopien nach dem Build-Schritt zählen nicht — der Build hat sie nicht
    gesehen.
    """
    stage: list[str] = []
    in_stage = False
    for raw in (root / "Dockerfile").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        upper = line.upper()
        if upper.startswith("FROM "):
            if in_stage:
                break
            in_stage = line.split()[-1].lower() == "node" and " AS " in upper
            continue
        if not in_stage:
            continue
        if upper.startswith("RUN ") and "npm ci" in line:
            break
        stage.append(line)
    return stage


def node_stage_copies(root: Path) -> list[tuple[str, str, str]]:
    """``(Quelle, tatsächliches Ziel, erwartetes Ziel)`` je kopiertem Pfad der Node-Stufe.

    Tailwind sucht die ``content``-Globs relativ zum ``WORKDIR``; eine Quelle
    ist nur dann sichtbar, wenn sie dort unter ihrem eigenen Pfad liegt.
    """
    workdir = "/"
    copies: list[tuple[str, str, str]] = []
    for line in _node_stage(root):
        upper = line.upper()
        if upper.startswith("WORKDIR "):
            workdir = posixpath.normpath(posixpath.join(workdir, line.split(None, 1)[1].strip()))
            continue
        if not upper.startswith("COPY "):
            continue
        parts = [part for part in line.split()[1:] if not part.startswith("--")]
        if len(parts) < 2:
            continue
        *raw_sources, raw_dest = parts
        dest = posixpath.normpath(posixpath.join(workdir, raw_dest))
        dest_is_dir = raw_dest.endswith("/") or raw_dest in (".", "./") or len(raw_sources) > 1
        for raw_source in raw_sources:
            source = _normalise(raw_source)
            source_is_dir = raw_source.endswith("/") or (root / source).is_dir()
            if source_is_dir or not dest_is_dir:
                target = dest
            else:
                target = posixpath.join(dest, posixpath.basename(source))
            copies.append((source, target, posixpath.join(workdir, source)))
    return copies


def node_stage_copy_sources(root: Path) -> list[str]:
    """Quellpfade, die in der Node-Stufe vor dem Build an ihrem eigenen Pfad liegen."""
    return [source for source, target, expected in node_stage_copies(root) if target == expected]


def dockerignore_patterns(root: Path) -> list[str]:
    """Ausschlussmuster der ``.dockerignore`` (Kommentare und Ausnahmen ``!`` übergangen)."""
    path = root / ".dockerignore"
    if not path.exists():
        return []
    patterns = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        patterns.append(line.strip("/"))
    return patterns


def _ignored_by(path: str, pattern: str) -> bool:
    """Trifft das Muster den Pfad, eines seiner Elternverzeichnisse oder etwas darin?"""
    segments = path.split("/")
    for depth in range(1, len(segments) + 1):
        if fnmatch.fnmatchcase("/".join(segments[:depth]), pattern):
            return True
    return pattern.startswith(path + "/")


def _glob_base(glob: str) -> str:
    """Das Verzeichnis vor dem ersten Platzhalter-Segment eines Globs."""
    base: list[str] = []
    for segment in glob.split("/"):
        if _GLOB_CHARS & set(segment):
            break
        base.append(segment)
    return "/".join(base)


def _expand_braces(glob: str) -> list[str]:
    match = _BRACE_RE.search(glob)
    if match is None:
        return [glob]
    expanded: list[str] = []
    for option in match.group(1).split(","):
        expanded.extend(_expand_braces(glob[: match.start()] + option + glob[match.end() :]))
    return expanded


def _is_copied(path: str, sources: list[str]) -> bool:
    return any(path == source or path.startswith(source + "/") for source in sources)


def _matches(path: str, globs: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(candidate.full_match(pattern) for glob in globs for pattern in _expand_braces(glob))


def _is_scanned(path: str, globs: list[str]) -> bool:
    """Von einem Glob erfasst und von keinem ``!``-Ausschluss wieder entfernt."""
    included = [glob for glob in globs if not glob.startswith("!")]
    excluded = [glob[1:] for glob in globs if glob.startswith("!")]
    return _matches(path, included) and not _matches(path, excluded)


def js_files_with_dom_classes(root: Path) -> list[str]:
    """JavaScript-Dateien unter ``src/static/js``, die Klassen an DOM-Knoten setzen.

    Minifizierte Fremdbibliotheken bringen keine eigenen Tailwind-Klassen mit.
    """
    js_root = root / "src" / "static" / "js"
    if not js_root.is_dir():
        return []
    found = []
    for path in sorted(js_root.rglob("*.js")):
        if path.name.endswith(".min.js"):
            continue
        if _DOM_CLASS_RE.search(path.read_text(encoding="utf-8")):
            found.append(path.relative_to(root).as_posix())
    return found


def tailwind_source_violations(root: Path) -> list[str]:
    """Alle Klassenquellen, die der Tailwind-Build im Image nicht sieht."""
    globs = tailwind_content_globs(root)
    included = [glob for glob in globs if not glob.startswith("!")]
    violations = [
        f"COPY {source!r} landet in {target!r} statt in {expected!r}"
        for source, target, expected in node_stage_copies(root)
        if target != expected
    ]
    sources = node_stage_copy_sources(root)
    bases = [_glob_base(pattern) for glob in included for pattern in _expand_braces(glob)]
    class_files = js_files_with_dom_classes(root)
    for pattern in dockerignore_patterns(root):
        for path in dict.fromkeys([*bases, *class_files]):
            if _ignored_by(path, pattern):
                violations.append(f".dockerignore-Muster {pattern!r} entfernt {path!r} aus dem Build-Kontext")
    for glob in included:
        for pattern in _expand_braces(glob):
            base = _glob_base(pattern)
            if not _is_copied(base, sources):
                violations.append(f"content-Glob {glob!r}: {base!r} wird in der Node-Stufe nicht kopiert")
    for path in class_files:
        if not _is_scanned(path, globs):
            violations.append(f"{path}: setzt Klassen, steht aber in keinem content-Glob")
        if not _is_copied(path, sources):
            violations.append(f"{path}: setzt Klassen, wird in der Node-Stufe aber nicht kopiert")
    return violations


# -- Proben gegen tmp_path ---------------------------------------------------

_CONFIG_WITH_JS = """module.exports = {
  content: [
    "./src/templates/**/*.html",
    // "./src/auskommentiert/**/*.html",
    "./src/static/js/**/*.js",
  ],
  theme: {},
};
"""

_CONFIG_WITHOUT_JS = """module.exports = {
  content: [
    "./src/templates/**/*.html",
  ],
};
"""

_DOCKERFILE_WITH_JS = """FROM python:3 AS builder
COPY src/static/js/ /nicht/die/node-stufe/
FROM node:26-alpine AS node
WORKDIR /build
COPY package.json package-lock.json tailwind.config.js ./
COPY src/templates/ src/templates/
COPY src/static/js/ src/static/js/
RUN npm ci && npx @tailwindcss/cli -i in.css -o out.css
FROM python:3 AS final
"""

_DOCKERFILE_WITHOUT_JS = """FROM node:26-alpine AS node
WORKDIR /build
COPY package.json package-lock.json tailwind.config.js ./
COPY src/templates/ src/templates/
RUN npm ci && npx @tailwindcss/cli -i in.css -o out.css
FROM python:3 AS final
COPY --from=node /build/src/static/css/styles.css src/static/css/styles.css
"""

_DOCKERFILE_JS_AFTER_BUILD = """FROM node:26-alpine AS node
WORKDIR /build
COPY package.json package-lock.json tailwind.config.js ./
COPY src/templates/ src/templates/
RUN npm ci && npx @tailwindcss/cli -i in.css -o out.css
COPY src/static/js/ src/static/js/
"""

_TOAST_JS = 'var t = document.createElement("div");\nt.className = "fixed bottom-4 right-4";\n'


def _probe(tmp_path: Path, config: str, dockerfile: str) -> Path:
    (tmp_path / "tailwind.config.js").write_text(config, encoding="utf-8")
    (tmp_path / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    js_dir = tmp_path / "src" / "static" / "js"
    (js_dir / "vendor").mkdir(parents=True)
    (js_dir / "toast.js").write_text(_TOAST_JS, encoding="utf-8")
    (js_dir / "pure.js").write_text("export const x = 1;\n", encoding="utf-8")
    (js_dir / "vendor" / "lib.min.js").write_text('a.className="x";', encoding="utf-8")
    return tmp_path


class TestTailwindSourcesProbes:
    def test_complete_stage_has_no_violations(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_WITH_JS)
        assert tailwind_source_violations(root) == []

    def test_reads_only_the_node_stage_before_the_build(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_WITH_JS)
        assert node_stage_copy_sources(root) == [
            "package.json",
            "package-lock.json",
            "tailwind.config.js",
            "src/templates",
            "src/static/js",
        ]

    def test_ignores_commented_content_globs(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_WITH_JS)
        assert tailwind_content_globs(root) == ["src/templates/**/*.html", "src/static/js/**/*.js"]

    def test_missing_copy_for_content_glob_is_reported(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_WITHOUT_JS)
        violations = tailwind_source_violations(root)
        assert "content-Glob 'src/static/js/**/*.js': 'src/static/js' wird in der Node-Stufe nicht kopiert" in (
            violations
        )
        assert "src/static/js/toast.js: setzt Klassen, wird in der Node-Stufe aber nicht kopiert" in violations

    def test_js_classes_are_reported_even_without_content_glob(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITHOUT_JS, _DOCKERFILE_WITHOUT_JS)
        assert tailwind_source_violations(root) == [
            "src/static/js/toast.js: setzt Klassen, steht aber in keinem content-Glob",
            "src/static/js/toast.js: setzt Klassen, wird in der Node-Stufe aber nicht kopiert",
        ]

    def test_copy_after_the_build_does_not_count(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_JS_AFTER_BUILD)
        assert "src/static/js" not in node_stage_copy_sources(root)
        assert tailwind_source_violations(root)

    def test_brace_globs_are_expanded(self, tmp_path: Path) -> None:
        config = _CONFIG_WITH_JS.replace("./src/static/js/**/*.js", "./src/static/js/**/*.{mjs,js}")
        root = _probe(tmp_path, config, _DOCKERFILE_WITH_JS)
        assert tailwind_source_violations(root) == []

    def test_each_brace_alternative_must_be_copied(self, tmp_path: Path) -> None:
        config = _CONFIG_WITH_JS.replace("./src/static/js/**/*.js", "./src/static/{js,css}/**/*.js")
        root = _probe(tmp_path, config, _DOCKERFILE_WITH_JS)
        assert tailwind_source_violations(root) == [
            "content-Glob 'src/static/{js,css}/**/*.js': 'src/static/css' wird in der Node-Stufe nicht kopiert"
        ]

    def test_only_dom_class_files_are_collected(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_WITH_JS)
        js_dir = root / "src" / "static" / "js"
        (js_dir / "tabs.js").write_text('el.classList.add("bg-red-600");\n', encoding="utf-8")
        (js_dir / "compare.js").write_text('if (el.className === "x") {}\n', encoding="utf-8")
        assert js_files_with_dom_classes(root) == ["src/static/js/tabs.js", "src/static/js/toast.js"]


_CONFIG_BRACKET_COMMENT = """module.exports = {
  content: [
    "./src/templates/**/*.html",
    "./src/static/js/**/*.js",
    /* widgets [HTML] */
    "./src/widgets/**/*.html",
  ],
};
"""

_CONFIG_QUOTING = """/* content: ["./src/falsch/seite.html"] */
const extra = `./src/static/js/**/*.js`;
module.exports = {
  // content: ["./src/auch-falsch/**/*.html"],
  content: [
    "./src/templates/**/*.html", // Kommentar mit ] und "Anfuehrung"
    './src/templates/**/*.txt',
    extra,
  ],
};
"""


class TestTailwindSourcesConfigParsing:
    def test_bracket_in_block_comment_does_not_hide_later_globs(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_BRACKET_COMMENT, _DOCKERFILE_WITH_JS)
        assert tailwind_content_globs(root) == [
            "src/templates/**/*.html",
            "src/static/js/**/*.js",
            "src/widgets/**/*.html",
        ]
        assert tailwind_source_violations(root) == [
            "content-Glob 'src/widgets/**/*.html': 'src/widgets' wird in der Node-Stufe nicht kopiert"
        ]

    def test_comments_quotes_and_template_strings(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_QUOTING, _DOCKERFILE_WITH_JS)
        assert tailwind_content_globs(root) == [
            "src/templates/**/*.html",
            "src/templates/**/*.txt",
            "src/static/js/**/*.js",
        ]
        assert tailwind_source_violations(root) == []

    def test_skips_without_node(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_WITH_JS)
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        with pytest.raises(pytest.skip.Exception, match="node nicht gefunden"):
            tailwind_content_globs(root)

    def test_skips_without_tailwind_package(self, tmp_path: Path) -> None:
        config = "const t = require('tailwindcss/defaultTheme');\n" + _CONFIG_WITH_JS
        root = _probe(tmp_path, config, _DOCKERFILE_WITH_JS)
        with pytest.raises(pytest.skip.Exception, match="npm ci"):
            tailwind_content_globs(root)

    def test_broken_config_fails_loudly(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, "module.exports = {content: [", _DOCKERFILE_WITH_JS)
        with pytest.raises(AssertionError, match="nicht auswerten"):
            tailwind_content_globs(root)

    def test_non_list_content_fails_loudly(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, "module.exports = {content: {files: []}};", _DOCKERFILE_WITH_JS)
        with pytest.raises(AssertionError, match="Liste von Pfad-Strings"):
            tailwind_content_globs(root)

    def test_negated_globs_are_exclusions(self, tmp_path: Path) -> None:
        config = _CONFIG_WITH_JS.replace(
            '"./src/static/js/**/*.js",', '"./src/static/js/**/*.js",\n    "!./src/static/js/**/*.min.js",'
        )
        root = _probe(tmp_path, config, _DOCKERFILE_WITH_JS)
        assert tailwind_content_globs(root)[-1] == "!src/static/js/**/*.min.js"
        assert tailwind_source_violations(root) == []

    def test_negation_hiding_a_class_file_is_reported(self, tmp_path: Path) -> None:
        config = _CONFIG_WITH_JS.replace(
            '"./src/static/js/**/*.js",', '"./src/static/js/**/*.js",\n    "!./src/static/js/toast.js",'
        )
        root = _probe(tmp_path, config, _DOCKERFILE_WITH_JS)
        assert tailwind_source_violations(root) == [
            "src/static/js/toast.js: setzt Klassen, steht aber in keinem content-Glob"
        ]


class TestTailwindSourcesBuildContext:
    def test_dockerignored_class_file_is_reported(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_WITH_JS)
        (root / ".dockerignore").write_text("node_modules\nsrc/static/js/toast.js\n", encoding="utf-8")
        assert tailwind_source_violations(root) == [
            ".dockerignore-Muster 'src/static/js/toast.js' entfernt 'src/static/js' aus dem Build-Kontext",
            ".dockerignore-Muster 'src/static/js/toast.js' entfernt 'src/static/js/toast.js' aus dem Build-Kontext",
        ]

    def test_dockerignored_content_directory_is_reported(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_WITH_JS)
        (root / ".dockerignore").write_text("# Kommentar\n/src/templates/\n", encoding="utf-8")
        assert tailwind_source_violations(root) == [
            ".dockerignore-Muster 'src/templates' entfernt 'src/templates' aus dem Build-Kontext"
        ]

    def test_dockerignore_glob_inside_content_directory_is_reported(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_WITH_JS)
        (root / ".dockerignore").write_text("src/static/js/*.js\n", encoding="utf-8")
        assert ".dockerignore-Muster 'src/static/js/*.js' entfernt 'src/static/js/toast.js' aus dem Build-Kontext" in (
            tailwind_source_violations(root)
        )

    def test_unrelated_dockerignore_entries_are_fine(self, tmp_path: Path) -> None:
        root = _probe(tmp_path, _CONFIG_WITH_JS, _DOCKERFILE_WITH_JS)
        (root / ".dockerignore").write_text(
            ".venv\nnode_modules\n__pycache__\n*.pyc\nsrc/staticfiles/\nsrc/static/css/styles.css\ndocs/\n",
            encoding="utf-8",
        )
        assert tailwind_source_violations(root) == []

    def test_copy_to_other_destination_is_reported(self, tmp_path: Path) -> None:
        dockerfile = _DOCKERFILE_WITH_JS.replace(
            "COPY src/static/js/ src/static/js/", "COPY src/static/js/ /outside-build/js/"
        )
        root = _probe(tmp_path, _CONFIG_WITH_JS, dockerfile)
        violations = tailwind_source_violations(root)
        assert "COPY 'src/static/js' landet in '/outside-build/js' statt in '/build/src/static/js'" in violations
        assert "src/static/js/toast.js: setzt Klassen, wird in der Node-Stufe aber nicht kopiert" in violations

    def test_absolute_destination_matching_the_workdir_is_fine(self, tmp_path: Path) -> None:
        dockerfile = _DOCKERFILE_WITH_JS.replace(
            "COPY src/static/js/ src/static/js/", "COPY src/static/js /build/src/static/js"
        )
        root = _probe(tmp_path, _CONFIG_WITH_JS, dockerfile)
        assert tailwind_source_violations(root) == []

    def test_file_into_wrong_directory_is_reported(self, tmp_path: Path) -> None:
        dockerfile = _DOCKERFILE_WITH_JS.replace(
            "COPY package.json package-lock.json tailwind.config.js ./",
            "COPY package.json package-lock.json tailwind.config.js ./\nCOPY src/static/js/toast.js src/",
        ).replace("COPY src/static/js/ src/static/js/\n", "")
        root = _probe(tmp_path, _CONFIG_WITH_JS, dockerfile)
        assert (
            "COPY 'src/static/js/toast.js' landet in '/build/src/toast.js' statt in '/build/src/static/js/toast.js'"
            in (tailwind_source_violations(root))
        )


@pytest.mark.skipif(not (REPO_ROOT / "Dockerfile").exists(), reason="Baum ohne Dockerfile")
class TestTailwindSourcesRepo:
    def test_image_build_sees_every_class_source(self) -> None:
        assert tailwind_source_violations(REPO_ROOT) == []

    def test_repo_has_js_class_sources(self) -> None:
        """Gegenprobe: der Scan greift im echten Baum (sonst wäre der Guard leer)."""
        files = js_files_with_dom_classes(REPO_ROOT)
        assert "src/static/js/htmx-errors.js" in files
        assert "src/static/js/offline-db.js" in files
        assert "src/static/js/offline-home.js" in files
        assert "src/static/js/time-filter-tabs.js" in files
