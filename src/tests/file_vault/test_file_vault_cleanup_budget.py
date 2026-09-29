"""Budget- und Fortsetzungs-Guards fuer den Medienbaum-Cleanup."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.services.file_vault import cleanup as cleanup_module

STATE_FILENAME = ".cleanup-orphan-storage-files-state.json"


def _write_enc(root: Path, relative_path: str, *, age_seconds: int = 7200) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ciphertext")
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))
    return path


def _run_cleanup(*, min_age_seconds: int = 3600, budget: int = 10_000, dry_run: bool = False):
    return cleanup_module.cleanup_orphan_storage_files(
        min_age_seconds=min_age_seconds,
        budget=budget,
        dry_run=dry_run,
    )


@pytest.fixture(autouse=True)
def reference_names(monkeypatch):
    names: set[str] = set()
    monkeypatch.setattr(
        cleanup_module.Attachment.objects,
        "values_list",
        lambda *args, **kwargs: tuple(names),
    )
    import_jobs = MagicMock()
    import_jobs.exclude.return_value.exclude.return_value.values_list.return_value = ()
    monkeypatch.setattr(cleanup_module.ImportJob, "objects", import_jobs)
    return names


def test_budgetierte_laeufe_decken_grossen_baum_fair_ab(reference_names, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    all_paths: set[str] = set()
    referenced_paths: set[str] = set()
    young_paths: set[str] = set()
    old_orphan_paths: set[str] = set()
    known_names: set[str] = set()

    for facility in range(3):
        for bucket in range(3):
            for item in range(20):
                relative = f"facility-{facility}/bucket-{bucket}/file-{facility}-{bucket}-{item:02d}.enc"
                age = 0 if item % 3 == 1 else 7200
                _write_enc(tmp_path, relative, age_seconds=age)
                all_paths.add(relative)
                if item % 3 == 0:
                    referenced_paths.add(relative)
                    known_names.add(Path(relative).name)
                elif item % 3 == 1:
                    young_paths.add(relative)
                else:
                    old_orphan_paths.add(relative)

    reference_names.update(known_names)
    budget = (len(all_paths) + 1) // 2

    first = _run_cleanup(budget=budget)
    second = _run_cleanup(budget=budget)

    assert first.checked == budget
    assert second.checked == len(all_paths) - budget
    assert set(first.checked_paths).isdisjoint(second.checked_paths)
    assert set(first.checked_paths) | set(second.checked_paths) == all_paths
    assert second.tree_complete is True
    assert second.next_start is None
    assert all(not (tmp_path / relative).exists() for relative in old_orphan_paths)
    assert all((tmp_path / relative).exists() for relative in referenced_paths | young_paths)

    third = _run_cleanup(budget=budget)

    expected_first_remaining = min(referenced_paths | young_paths, key=lambda path: tuple(Path(path).parts))
    assert third.continued_at == expected_first_remaining


def test_kleiner_baum_wird_in_einem_lauf_vollstaendig_bereinigt(reference_names, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    old_orphan = _write_enc(tmp_path, "a/old-orphan.enc", age_seconds=7200)
    young_orphan = _write_enc(tmp_path, "b/young-orphan.enc", age_seconds=0)
    referenced = _write_enc(tmp_path, "c/referenced.enc", age_seconds=7200)
    reference_names.add(referenced.name)

    result = _run_cleanup(budget=10)

    assert result.checked == 3
    assert result.deleted == 1
    assert result.deletion_candidates == 1
    assert result.skipped_referenced == 1
    assert result.skipped_young == 1
    assert result.errors == 0
    assert result.tree_complete is True
    assert result.next_start is None
    assert not old_orphan.exists()
    assert young_orphan.exists()
    assert referenced.exists()
    assert json.loads((tmp_path / STATE_FILENAME).read_text(encoding="utf-8")) == {"after": None}


@pytest.mark.parametrize("raw_state", ["", "kein json"])
def test_beschaedigter_fortsetzungspunkt_startet_mit_hinweis_von_vorn(settings, tmp_path, raw_state):
    settings.MEDIA_ROOT = str(tmp_path)
    _write_enc(tmp_path, "a.enc")
    _write_enc(tmp_path, "b.enc")
    (tmp_path / STATE_FILENAME).write_text(raw_state, encoding="utf-8")

    result = _run_cleanup(budget=1, dry_run=True)

    assert result.checked_paths == ("a.enc",)
    assert result.continued_at == "a.enc"
    assert result.state_warning is not None


def test_geloeschte_cursor_datei_setzt_beim_naechsten_pfad_fort(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    first_path = _write_enc(tmp_path, "a.enc")
    _write_enc(tmp_path, "b.enc")
    _write_enc(tmp_path, "c.enc")

    first = _run_cleanup(budget=1)
    second = _run_cleanup(budget=1)

    assert first.checked_paths == ("a.enc",)
    assert not first_path.exists()
    assert second.checked_paths == ("b.enc",)
    assert second.continued_at == "b.enc"


def test_neue_datei_vor_cursor_wird_im_naechsten_umlauf_erreicht(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    _write_enc(tmp_path, "b.enc", age_seconds=0)
    _write_enc(tmp_path, "c.enc", age_seconds=0)

    first = _run_cleanup(budget=1)
    new_path = _write_enc(tmp_path, "a.enc", age_seconds=0)
    second = _run_cleanup(budget=1)
    third = _run_cleanup(budget=1)

    assert first.checked_paths == ("b.enc",)
    assert second.checked_paths == ("c.enc",)
    assert second.tree_complete is True
    assert third.checked_paths == ("a.enc",)
    assert new_path.exists()


def test_trockenlauf_loescht_nichts_und_aendert_den_cursor_nicht(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    _write_enc(tmp_path, "a.enc")
    second_path = _write_enc(tmp_path, "b.enc")
    _write_enc(tmp_path, "c.enc")
    first = _run_cleanup(budget=1)
    state_path = tmp_path / STATE_FILENAME
    state_before = state_path.read_bytes()

    dry_run = _run_cleanup(budget=1, dry_run=True)

    assert first.checked_paths == ("a.enc",)
    assert dry_run.checked_paths == ("b.enc",)
    assert dry_run.deletion_candidates == 1
    assert dry_run.deleted == 0
    assert second_path.exists()
    assert state_path.read_bytes() == state_before
