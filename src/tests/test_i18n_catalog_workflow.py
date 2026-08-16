"""Fixtures for the reorder-safe PO merge workflow (Refs #1915)."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest


def _workflow_module():
    path = Path(__file__).parents[2] / "scripts" / "i18n_catalog.py"
    spec = importlib.util.spec_from_file_location("i18n_catalog", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_new_and_removed_entries_do_not_reorder_existing_blocks():
    workflow = _workflow_module()
    existing = """msgid ""
msgstr ""

msgid "First"
msgstr "Erste"

msgid "Removed"
msgstr "Entfernt"

msgid "Last"
msgstr "Letzte"
"""
    generated = """msgid ""
msgstr ""

msgid "Last"
msgstr "Letzte"

msgid "First"
msgstr "Erste"

msgid "New"
msgstr "Neu"
"""

    merged = workflow.merge_preserving_order(existing, generated)
    assert merged.index('msgid "First"') < merged.index('msgid "Last"')
    assert 'msgid "Removed"' not in merged
    assert merged.index('msgid "Last"') < merged.index('msgid "New"')


def test_context_and_plural_are_part_of_the_stable_key():
    workflow = _workflow_module()
    text = """msgid ""
msgstr ""

msgctxt "menu"
msgid "Open"
msgstr "Öffnen"

msgid "Open"
msgid_plural "Opens"
msgstr[0] "Öffnen"
msgstr[1] "Öffnungen"
"""

    keys = workflow.catalogue_keys(text)
    assert ("menu", "Open", "") in keys
    assert ("", "Open", "Opens") in keys


def _catalogue(*entries: str) -> str:
    return (
        "\n\n".join(
            (
                'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"',
                *entries,
            )
        )
        + "\n"
    )


def _write_catalogue(root: Path, language: str, content: str) -> Path:
    path = root / "src" / "locale" / language / "LC_MESSAGES" / "django.po"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _configure_catalogues(monkeypatch, workflow, tmp_path, *, existing: str, generated: str):
    root = tmp_path / "root"
    mirror = tmp_path / "mirror"
    for language in workflow.LOCALES:
        _write_catalogue(root, language, existing)
        _write_catalogue(mirror, language, generated)

    temporary = tempfile.TemporaryDirectory(dir=tmp_path)
    monkeypatch.setattr(workflow, "ROOT", root)
    monkeypatch.setattr(workflow, "_generated_catalogues", lambda: (temporary, mirror))
    return root


def test_ignore_copy_excludes_nested_worktrees_runtime_outputs_secrets_and_env_files():
    workflow = _workflow_module()

    root_ignored = workflow._ignore_copy(
        str(workflow.ROOT),
        [".worktrees", ".env", ".env.deploy", ".venv", "node_modules", "secrets", "src"],
    )
    source_ignored = workflow._ignore_copy(str(workflow.ROOT / "src"), ["media", "staticfiles", "core"])
    nested_ignored = workflow._ignore_copy(
        str(workflow.ROOT / "src" / "core"),
        [".env.production", "__pycache__", "secrets", "views"],
    )

    assert root_ignored == {".worktrees", ".env", ".env.deploy", ".venv", "node_modules", "secrets"}
    assert source_ignored == {"media", "staticfiles"}
    assert nested_ignored == {".env.production", "__pycache__", "secrets"}


def test_check_returns_nonzero_for_semantic_drift(monkeypatch, tmp_path):
    workflow = _workflow_module()
    existing = _catalogue('msgid "Existing"\nmsgstr "Existing"')
    generated = _catalogue('msgid "New"\nmsgstr "New"')
    _configure_catalogues(monkeypatch, workflow, tmp_path, existing=existing, generated=generated)

    assert workflow.main(["check"]) == 1


def test_check_keeps_reference_only_drift_informational(monkeypatch, tmp_path):
    workflow = _workflow_module()
    existing = _catalogue('#: old.py:1\nmsgid "Existing"\nmsgstr "Existing"')
    generated = _catalogue('#: new.py:1\nmsgid "Existing"\nmsgstr "Existing"')
    _configure_catalogues(monkeypatch, workflow, tmp_path, existing=existing, generated=generated)

    assert workflow.main(["check"]) == 0


def test_report_lists_changed_msgids_separately_from_reference_noise(capsys):
    workflow = _workflow_module()
    existing = _catalogue('#: old.py:1\nmsgid "Existing"\nmsgstr "Old translation"')
    generated = _catalogue('#: new.py:1\nmsgid "Existing"\nmsgstr "New translation"')

    assert workflow._report(existing, generated, "en") is True

    assert "  ~ Existing" in capsys.readouterr().out


def test_update_rejects_empty_entries_before_writing_any_live_catalogue(monkeypatch, tmp_path):
    workflow = _workflow_module()
    existing = _catalogue('msgid "Existing"\nmsgstr "Existing"')
    generated = _catalogue('msgid "New"\nmsgstr ""')
    root = _configure_catalogues(monkeypatch, workflow, tmp_path, existing=existing, generated=generated)
    before = {language: _write_catalogue(root, language, existing).read_bytes() for language in workflow.LOCALES}

    with pytest.raises(RuntimeError, match="untranslated"):
        workflow.main(["update"])

    assert {
        language: _write_catalogue(root, language, existing).read_bytes() for language in workflow.LOCALES
    } == before


@pytest.mark.parametrize(
    ("language", "entry", "error"),
    [
        ("en", '#, fuzzy\nmsgid "New"\nmsgstr "New"', "fuzzy"),
        ("de", 'msgid "Neu"\nmsgstr "Different"', "non-identity German"),
        (
            "de",
            'msgid "Eintrag"\nmsgid_plural "Einträge"\nmsgstr[0] "Falsch"\nmsgstr[1] "Einträge"',
            "plural form 0",
        ),
        (
            "de",
            'msgid "Eintrag"\nmsgid_plural "Einträge"\nmsgstr[0] "Eintrag"\nmsgstr[1] "Falsch"',
            "plural form 1",
        ),
    ],
)
def test_candidate_validation_keeps_fuzzy_and_german_identity_gates(language, entry, error):
    workflow = _workflow_module()

    with pytest.raises(RuntimeError, match=error):
        workflow._validate_catalogue(_catalogue(entry), language)


def test_candidate_validation_accepts_both_identity_plural_forms_in_german():
    workflow = _workflow_module()
    plural = 'msgid "Eintrag"\nmsgid_plural "Einträge"\nmsgstr[0] "Eintrag"\nmsgstr[1] "Einträge"'

    workflow._validate_catalogue(_catalogue(plural), "de")


def test_update_rolls_back_already_replaced_files_when_a_later_replace_fails(monkeypatch, tmp_path):
    workflow = _workflow_module()
    existing = _catalogue('msgid "Existing"\nmsgstr "Existing"')
    generated = _catalogue('msgid "New"\nmsgstr "New"')
    root = _configure_catalogues(monkeypatch, workflow, tmp_path, existing=existing, generated=generated)
    for language in workflow.LOCALES:
        po, mo = workflow._paths(root, language)
        workflow._compile(po, mo)
    before = {path: path.read_bytes() for language in workflow.LOCALES for path in workflow._paths(root, language)}

    original_copy = workflow._atomic_copy
    calls = 0

    def fail_second_copy(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated replacement failure")
        original_copy(source, destination)

    monkeypatch.setattr(workflow, "_atomic_copy", fail_second_copy)

    with pytest.raises(RuntimeError, match="rolled back"):
        workflow.main(["update"])

    assert {path: path.read_bytes() for path in before} == before
