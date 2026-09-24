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

Der Guard prüft zwei Dinge:

1. Jeder ``content``-Glob in ``tailwind.config.js`` ist in der Node-Stufe vor
   dem Build-Schritt kopiert.
2. Jede JavaScript-Datei unter ``src/static/js/``, die Klassen für erzeugte
   DOM-Knoten setzt (``className = …``, ``classList.add(…)``), ist von einem ``content``-Glob
   erfasst und in der Node-Stufe kopiert — auch dann, wenn ``content`` den
   Pfad gar nicht nennt.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]

_CONTENT_BLOCK_RE = re.compile(r"\bcontent\s*:\s*\[(?P<body>.*?)\]", re.DOTALL)
_QUOTED_RE = re.compile(r"""["']([^"']+)["']""")
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_DOM_CLASS_RE = re.compile(r"\.className\s*=(?!=)|\.classList\.(?:add|toggle|replace)\(")
_BRACE_RE = re.compile(r"\{([^{}]*)\}")
_GLOB_CHARS = set("*?[{")


def _normalise(path: str) -> str:
    path = path.strip()
    if path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def tailwind_content_globs(root: Path) -> list[str]:
    """Die ``content``-Globs aus ``tailwind.config.js`` (ohne ``./``-Präfix)."""
    source = (root / "tailwind.config.js").read_text(encoding="utf-8")
    source = _LINE_COMMENT_RE.sub("", source)
    match = _CONTENT_BLOCK_RE.search(source)
    if match is None:
        return []
    return [_normalise(glob) for glob in _QUOTED_RE.findall(match.group("body"))]


def node_stage_copy_sources(root: Path) -> list[str]:
    """Quellpfade der ``COPY``-Anweisungen der Node-Stufe vor ``RUN npm ci``.

    Kopien nach dem Build-Schritt zählen nicht — der Build hat sie nicht
    gesehen.
    """
    lines = (root / "Dockerfile").read_text(encoding="utf-8").splitlines()
    sources: list[str] = []
    in_stage = False
    for raw in lines:
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
        if upper.startswith("COPY "):
            parts = [part for part in line.split()[1:] if not part.startswith("--")]
            if len(parts) >= 2:
                sources.extend(_normalise(part) for part in parts[:-1])
    return sources


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


def _is_scanned(path: str, globs: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(candidate.full_match(pattern) for glob in globs for pattern in _expand_braces(glob))


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
    sources = node_stage_copy_sources(root)
    violations: list[str] = []
    for glob in globs:
        for pattern in _expand_braces(glob):
            base = _glob_base(pattern)
            if not _is_copied(base, sources):
                violations.append(f"content-Glob {glob!r}: {base!r} wird in der Node-Stufe nicht kopiert")
    for path in js_files_with_dom_classes(root):
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
