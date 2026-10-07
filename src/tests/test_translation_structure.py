"""Translation structure checks operate on disposable Markdown trees."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


def check(root):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_translation_structure.py"), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def pair(root, de, en):
    (root / "docs/en").mkdir(parents=True)
    (root / "docs/example.md").write_text(de)
    (root / "docs/en/example.md").write_text(en + "\n<!-- translation-source: docs/example.md -->\n")


def test_accepts_translated_prose_and_placeholders(tmp_path):
    pair(
        tmp_path,
        "# Deutsch\n```bash\nmake test BASE=<ref>\n```\n",
        "# English\n```bash\nmake test BASE=<reference>\n```\n",
    )
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("de", "en", "metric"),
    [
        ("# Titel\n## Teil\n", "# Title\n", "headings"),
        ("# Titel\n", "## Title\n", "headings"),
        ("```bash\ntrue\n```\n", "", "code_blocks"),
        ("| A |\n|---|\n| B |\n", "| A |\n|---|\n", "tables"),
        ("```bash\nmake test\n```\n", "```bash\nmake lint\n```\n", "make_targets"),
        ("```bash\nexport CI_LOCK=<pfad>\n```\n", "```bash\nexport DB_NAME=<path>\n```\n", "env_variables"),
        ("```bash\npython manage.py migrate\n```\n", "```bash\npython manage.py seed\n```\n", "commands"),
        ("```text\n## not a heading\n| x |\n```\n", "```text\n## translated literal\n| y |\n```\n", None),
    ],
)
def test_detects_structural_drift(tmp_path, de, en, metric):
    pair(tmp_path, de, en)
    result = check(tmp_path)
    if metric is None:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode == 1
        assert metric in result.stdout


def test_rejects_missing_source(tmp_path):
    pair(tmp_path, "# DE\n", "# EN\n")
    (tmp_path / "docs/example.md").unlink()
    result = check(tmp_path)
    assert result.returncode == 1
    assert "missing source" in result.stdout


def test_glossary_column_exception_does_not_hide_missing_rows(tmp_path):
    pair(tmp_path, "| A | B |\n|---|---|\n| x | y |\n", "| A | B | C |\n|---|---|---|\n| x | y | z |\n")
    en = tmp_path / "docs/en/example.md"
    en.rename(en.with_name("glossary.md"))
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALLOW docs/en/glossary.md: table_columns" in result.stdout
    (tmp_path / "docs/en/glossary.md").write_text(
        "| A | B | C |\n|---|---|---|\n<!-- translation-source: docs/example.md -->\n"
    )
    result = check(tmp_path)
    assert result.returncode == 1
    assert ": tables " in result.stdout


@pytest.mark.parametrize("relative", ["CONTRIBUTING.en.md", "README.en.md", "docs/screenshots.en.md"])
def test_detects_drift_in_mirrors_outside_docs_en(tmp_path, relative):
    translated = tmp_path / relative
    translated.parent.mkdir(parents=True, exist_ok=True)
    source = relative.replace(".en.md", ".md")
    (tmp_path / source).write_text("# Source\n```bash\nmake test\n```\n")
    translated.write_text(f"# Translation\n```bash\nmake lint\n```\n<!-- translation-source: {source} -->\n")
    result = check(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert relative in result.stdout
    assert "make_targets" in result.stdout


def test_discovery_covers_every_registered_translation():
    import runpy

    versions = runpy.run_path(str(ROOT / "scripts/check_translation_versions.py"))
    checker = runpy.run_path(str(ROOT / "scripts/check_translation_structure.py"))
    discovered = {path.relative_to(ROOT).as_posix() for path in checker["translation_paths"](ROOT)}
    assert set(versions["TRANSLATED_FILES"]) <= discovered
