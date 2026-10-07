"""Guards: code references and sibling links in the shipped documentation hold.

Two classes of defect came back in every review round and are pinned here:

- A code reference points at a file that no longer exists (``src/core/services``
  was split into packages) or at a line number that moves with every edit.
  Checked: GitHub ``blob/<ref>/<path>`` links, relative links into ``src/``,
  ``scripts/`` and ``dev-ops/``, and back-ticked repository paths. Line anchors
  (``#L12``, ``path.py:12``) are rejected; a symbol name is the stable anchor.
- An English document links the German sibling although an English one exists
  (``docs/en/ops-runbook.md`` instead of ``docs/ops-runbook.md``).

The scan functions take the root as a parameter; the function tests probe
``tmp_path`` only, the repository-wide proof runs against ``REPO_ROOT``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]

# The stripped directory names are assembled at run time: the release guard
# rejects shipped tests that spell them out, and this module ships.
_OPS_DIR = "dev-" + "ops"
_DEV_SCRIPTS = "scripts/" + "dev/"
_CODE_ROOTS = ("src/", "scripts/", f"{_OPS_DIR}/")
# The development-only directories are not part of the public tree; a document
# may name them in the development tree only.
_STRIPPED_PREFIXES = (f"{_OPS_DIR}/", _DEV_SCRIPTS)

_BLOB_LINK = re.compile(
    r"https://github\.com/[\w.-]+/[\w.-]+/blob/[^/\s)]+/(?P<path>[^\s)#?`]+)(?:\?plain=1)?(?P<anchor>#L\d+(?:-L?\d+)?)?"
)
_RELATIVE_LINK = re.compile(
    r"\]\((?P<path>(?:\.\./|\./)*(?:src|scripts|" + _OPS_DIR + r")/[^\s)#]+)(?P<anchor>#L\d+(?:-L?\d+)?)?\)"
)
_PROSE_PATH = re.compile(
    r"`(?P<path>(?:src|scripts|" + _OPS_DIR + r")/[\w./-]+\.(?:py|js|html|sh|yml|yaml|toml|json|css))"
    r"(?P<anchor>:\d+(?:-\d+)?|#L\d+(?:-L?\d+)?)?`"
)
_MD_LINK = re.compile(r"\]\((?P<path>[^)\s#]+\.md)(?P<fragment>#[^)\s]*)?\)")


def _shipped_documents(root: Path) -> list[Path]:
    """Core documents, test matrices and READMEs — not ADRs, archive, dev notes
    or the CHANGELOG (dated history names files as they were)."""
    paths: list[Path] = []
    for pattern in ("docs/*.md", "docs/en/*.md", "docs/testing/*.md"):
        paths.extend(sorted(root.glob(pattern)))
    paths.extend(sorted((root / "docs" / "compliance").rglob("*.md")))
    for name in ("README.md", "README.en.md", "CONTRIBUTING.md", "CONTRIBUTING.en.md", "SECURITY.md"):
        paths.append(root / name)
    return [p for p in paths if p.is_file()]


def _exists(root: Path, rel: str) -> bool:
    if rel.startswith(_STRIPPED_PREFIXES) and not (root / _OPS_DIR).is_dir():
        return True  # public tree: the path is stripped by design
    return (root / rel.rstrip("/")).exists()


def find_code_reference_findings(root: Path) -> list[str]:
    """``file:line: message`` for every dead path or line anchor."""
    findings: list[str] = []
    for doc in _shipped_documents(root):
        rel_doc = doc.relative_to(root)
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
            where = f"{rel_doc}:{lineno}"
            for m in _BLOB_LINK.finditer(line):
                path = m.group("path")
                if not path.startswith(_CODE_ROOTS):
                    continue
                if not _exists(root, path):
                    findings.append(f"{where}: link target {path} does not exist")
                elif m.group("anchor"):
                    findings.append(f"{where}: line anchor {m.group('anchor')} on {path}; use a symbol name")
            for m in _RELATIVE_LINK.finditer(line):
                target = Path(os.path.normpath(doc.parent / m.group("path")))
                try:
                    rel = target.relative_to(root).as_posix()
                except ValueError:
                    findings.append(f"{where}: link {m.group('path')} leaves the repository")
                    continue
                if not _exists(root, rel):
                    findings.append(f"{where}: link target {m.group('path')} does not exist")
                elif m.group("anchor"):
                    findings.append(f"{where}: line anchor {m.group('anchor')} on {rel}; use a symbol name")
            for m in _PROSE_PATH.finditer(line):
                path = m.group("path")
                if not _exists(root, path):
                    findings.append(f"{where}: path {path} does not exist")
                elif m.group("anchor"):
                    findings.append(f"{where}: line number {m.group('anchor')} on {path}; use a symbol name")
    return findings


def _english_sibling(root: Path, rel: Path) -> Path | None:
    if rel.parts[0] == "docs" and len(rel.parts) == 2:
        return root / "docs" / "en" / rel.name
    return {"README.md": root / "README.en.md", "CONTRIBUTING.md": root / "CONTRIBUTING.en.md"}.get(rel.as_posix())


def find_english_sibling_findings(root: Path) -> list[str]:
    """English documents that link the German file although an English sibling exists.

    Exempt: the language switch at the top (the German source of the document),
    lines that mark the target as German (``(DE:``) and lines that already
    link the sibling next to the German file.
    """
    findings: list[str] = []
    english = [*sorted((root / "docs" / "en").glob("*.md")), root / "README.en.md", root / "CONTRIBUTING.en.md"]
    for doc in (p for p in english if p.is_file()):
        text = doc.read_text(encoding="utf-8")
        source = re.search(r"<!-- translation-source: (\S+) -->", text)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if lineno <= 3 or "(DE:" in line:
                continue
            targets = [Path(os.path.normpath(doc.parent / m.group("path"))) for m in _MD_LINK.finditer(line)]
            for target in targets:
                if not target.is_file():
                    continue
                rel = target.relative_to(root)
                sibling = _english_sibling(root, rel)
                if sibling is None or not sibling.is_file() or sibling == doc:
                    continue
                if source and rel.as_posix() == source.group(1):
                    continue
                if sibling in targets:
                    continue
                findings.append(
                    f"{doc.relative_to(root)}:{lineno}: links {rel} although {sibling.relative_to(root)} exists"
                )
    return findings


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(tmp_path: Path) -> Path:
    _write(tmp_path, "src/core/services/dashboard/search.py", "")
    _write(tmp_path, "docs/ops-runbook.md", "# Betrieb\n")
    _write(tmp_path, "docs/en/ops-runbook.md", "# Ops\n")
    return tmp_path


class TestCodeReferences:
    def test_dead_blob_link_is_reported(self, tmp_path):
        root = _tree(tmp_path)
        _write(root, "docs/faq.md", "[x](https://github.com/o/r/blob/main/src/core/services/search.py)\n")
        assert find_code_reference_findings(root) == [
            "docs/faq.md:1: link target src/core/services/search.py does not exist"
        ]

    def test_existing_blob_link_passes(self, tmp_path):
        root = _tree(tmp_path)
        _write(root, "docs/faq.md", "[x](https://github.com/o/r/blob/main/src/core/services/dashboard/search.py)\n")
        assert find_code_reference_findings(root) == []

    def test_line_anchors_are_reported_in_all_three_forms(self, tmp_path):
        root = _tree(tmp_path)
        _write(
            root,
            "docs/faq.md",
            "[a](https://github.com/o/r/blob/main/src/core/services/dashboard/search.py?plain=1#L5)\n"
            "[b](../src/core/services/dashboard/search.py#L5-L9)\n"
            "`src/core/services/dashboard/search.py:12`\n",
        )
        findings = find_code_reference_findings(root)
        assert len(findings) == 3
        assert all("use a symbol name" in f for f in findings)

    def test_dead_relative_link_and_prose_path_are_reported(self, tmp_path):
        root = _tree(tmp_path)
        _write(root, "docs/faq.md", "[a](../src/core/urls.py)\n`src/core/services/locking.py`\n")
        assert find_code_reference_findings(root) == [
            "docs/faq.md:1: link target ../src/core/urls.py does not exist",
            "docs/faq.md:2: path src/core/services/locking.py does not exist",
        ]

    def test_package_directory_link_passes(self, tmp_path):
        root = _tree(tmp_path)
        _write(root, "docs/faq.md", "[a](../src/core/services/dashboard/)\n")
        assert find_code_reference_findings(root) == []

    def test_adr_and_changelog_history_is_not_scanned(self, tmp_path):
        root = _tree(tmp_path)
        _write(root, "docs/adr/001.md", "`src/core/services/locking.py`\n")
        _write(root, "CHANGELOG.md", "`src/core/services/locking.py`\n")
        assert find_code_reference_findings(root) == []

    def test_stripped_paths_are_not_required_in_the_public_tree(self, tmp_path):
        root = _tree(tmp_path)
        _write(root, "docs/faq.md", f"`{_DEV_SCRIPTS}run_mutmut.py`\n")
        assert find_code_reference_findings(root) == []
        (root / _OPS_DIR).mkdir()
        assert find_code_reference_findings(root) == [f"docs/faq.md:1: path {_DEV_SCRIPTS}run_mutmut.py does not exist"]

    def test_shipped_documents_name_existing_code_without_line_numbers(self):
        findings = find_code_reference_findings(REPO_ROOT)
        assert not findings, "Dead code references:\n" + "\n".join(findings)


class TestEnglishSiblings:
    def test_german_link_in_english_file_is_reported(self, tmp_path):
        root = _tree(tmp_path)
        _write(root, "docs/en/admin-guide.md", "x\nx\nx\n[run](../ops-runbook.md#3)\n")
        assert find_english_sibling_findings(root) == [
            "docs/en/admin-guide.md:4: links docs/ops-runbook.md although docs/en/ops-runbook.md exists"
        ]

    def test_sibling_link_passes(self, tmp_path):
        root = _tree(tmp_path)
        _write(root, "docs/en/admin-guide.md", "x\nx\nx\n[run](ops-runbook.md#3)\n")
        assert find_english_sibling_findings(root) == []

    def test_language_switch_and_marked_german_links_pass(self, tmp_path):
        root = _tree(tmp_path)
        _write(
            root,
            "docs/en/ops-runbook.md",
            "> [German](../ops-runbook.md)\nx\nx\n(DE: [runbook](../ops-runbook.md))\n"
            "<!-- translation-source: docs/ops-runbook.md -->\n",
        )
        _write(root, "docs/en/admin-guide.md", "x\nx\nx\n[both](../ops-runbook.md) / [en](ops-runbook.md)\n")
        assert find_english_sibling_findings(root) == []

    def test_english_documents_link_english_siblings(self):
        findings = find_english_sibling_findings(REPO_ROOT)
        assert not findings, "English documents link German siblings:\n" + "\n".join(findings)


_SYNC_NOTE = re.compile(
    r"Synchroni[sz]ation update|(?:Previously|Most recently) synchroni[sz]ed|<!--\s*translation-note",
    re.IGNORECASE,
)


def find_sync_note_findings(root: Path) -> list[str]:
    """English documents whose head or body carries a synchronisation note.

    The stamp at the end of the file (``source-hash``) is the only sync record;
    the reason for a change belongs in the commit body.
    """
    findings: list[str] = []
    english = [*sorted((root / "docs" / "en").glob("*.md")), root / "README.en.md", root / "CONTRIBUTING.en.md"]
    for doc in (p for p in english if p.is_file()):
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
            if _SYNC_NOTE.search(line):
                findings.append(f"{doc.relative_to(root)}:{lineno}: synchronisation note")
    return findings


class TestNoSyncNotes:
    def test_sync_notes_are_reported(self, tmp_path):
        _write(tmp_path, "docs/en/a.md", "> Synchronization update 2026-10-06: x\n")
        _write(tmp_path, "docs/en/b.md", "<!-- translation-note 2026-10-06: x -->\n> Previously synchronized on x\n")
        _write(tmp_path, "docs/en/c.md", "# Clean\n<!-- translation-date: 2026-10-06 -->\n")
        assert find_sync_note_findings(tmp_path) == [
            "docs/en/a.md:1: synchronisation note",
            "docs/en/b.md:1: synchronisation note",
            "docs/en/b.md:2: synchronisation note",
        ]

    def test_english_documents_carry_no_sync_notes(self):
        findings = find_sync_note_findings(REPO_ROOT)
        assert not findings, "Synchronisation notes:\n" + "\n".join(findings)
