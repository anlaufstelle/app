"""Pins for the budgeted media-tree sweep (``core.services.file_vault.cleanup``).

Source: mutation delta of the release-0.99.5 checkpoint, 34 mutants.
Groups:

* ``_read_cleanup_cursor`` (5) — an unusable state file (wrong shape, a
  non-string or empty cursor, a ``..`` component, an absolute path) must be
  discarded with a notice and the sweep must start at the first file. The
  mutants accept such a cursor silently (and skip files sorted below it) or
  crash the sweep with ``KeyError``/``TypeError``.
* ``_write_cleanup_cursor`` (6) — the cursor must be written for any file
  name the tree can yield (non-UTF-8 names arrive surrogate-escaped), a failed
  write must surface as the original ``OSError`` even when temp creation or
  temp cleanup fails, and a hard abort may only leave a hidden temp file that
  belongs to the state file.
* ``CleanupCursorWriteError.__init__`` (2) — the error message is the message
  of the underlying ``OSError``.
* ``cleanup_orphan_storage_files`` (21) — the report of a run: every counter
  counts every file, a dry run checks its whole budget, a budget-limited run
  names its next start, and a missing media root reports an empty, complete
  run with zero counters.

The sweep pins keep the state file under ``tmp_path`` and usually run the
sweep twice, so the persisted continuation point is observed through the
second run instead of by reading implementation details. Reference lookups
are stubbed exactly like in ``file_vault/test_file_vault_cleanup_budget.py``;
no database access happens.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.services.file_vault import cleanup as cleanup_module

STATE_FILENAME = ".cleanup-orphan-storage-files-state.json"

# Operator-facing notices of the sweep, kept verbatim (product output).
FORMAT_NOTICE = "Fortsetzungspunkt hat ein ungueltiges Format; Start am Anfang."
PATH_NOTICE = "Fortsetzungspunkt hat einen ungueltigen Pfad; Start am Anfang."

OLD = 7200
YOUNG = 0


class _HardAbort(BaseException):
    """Stands in for a process abort that skips the ``except OSError`` cleanup."""


def _write_enc(root: Path, relative_path: str, *, age_seconds: int) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ciphertext")
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))
    return path


def _sweep(*, budget: int = 10_000, dry_run: bool = False):
    return cleanup_module.cleanup_orphan_storage_files(min_age_seconds=3600, budget=budget, dry_run=dry_run)


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


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


class TestReadCleanupCursor:
    @pytest.mark.parametrize(
        "raw_state",
        [
            pytest.param("{}", id="empty-object"),
            pytest.param("null", id="null"),
            pytest.param("5", id="number"),
            pytest.param('["after"]', id="list"),
            pytest.param('{"after": "b.enc", "extra": 1}', id="extra-key"),
        ],
    )
    def test_state_with_invalid_shape_restarts_at_first_file(self, media_root, raw_state):
        """``_read_cleanup_cursor__mutmut_10`` (shape check ``or`` -> ``and``).

        The mutant crashes on ``{}`` (``KeyError``), on ``null``/numbers/lists
        (``TypeError``) and silently resumes behind ``b.enc`` for a state with
        an extra key. The existing corrupted-state test only covers text that
        is not JSON at all, which never reaches the shape check.
        """
        for name in ("a.enc", "b.enc", "c.enc"):
            _write_enc(media_root, name, age_seconds=YOUNG)
        (media_root / STATE_FILENAME).write_text(raw_state, encoding="utf-8")

        first = _sweep(budget=1)
        second = _sweep(budget=1)

        assert first.state_warning == FORMAT_NOTICE
        assert first.checked_paths == ("a.enc",)
        assert first.continued_at == "a.enc"
        assert second.state_warning is None
        assert second.checked_paths == ("b.enc",)

    @pytest.mark.parametrize(
        "raw_state",
        [
            pytest.param('{"after": 5}', id="number-cursor"),
            pytest.param('{"after": ""}', id="empty-cursor"),
            pytest.param('{"after": "../z.enc"}', id="parent-component"),
            pytest.param('{"after": "/abs.enc"}', id="absolute-path"),
        ],
    )
    def test_invalid_cursor_restarts_at_first_file(self, media_root, raw_state):
        """``_read_cleanup_cursor__mutmut_23`` / ``_31`` / ``_32`` / ``_35``.

        ``_23`` (``or`` -> ``and`` in the string check) crashes on a numeric
        cursor with ``TypeError`` and accepts an empty cursor without notice.
        ``_31``/``_35`` drop the ``..`` check and ``_32`` drops the absolute
        path check: the cursor is accepted silently, and because ``-`` sorts
        below both ``.`` and ``/`` the first file ``-a.enc`` is skipped.
        """
        for name in ("-a.enc", "a.enc", "b.enc"):
            _write_enc(media_root, name, age_seconds=YOUNG)
        (media_root / STATE_FILENAME).write_text(raw_state, encoding="utf-8")

        first = _sweep(budget=1)
        second = _sweep(budget=1)

        assert first.state_warning == PATH_NOTICE
        assert first.checked_paths == ("-a.enc",)
        assert first.continued_at == "-a.enc"
        assert second.state_warning is None
        assert second.checked_paths == ("a.enc",)


class TestWriteCleanupCursor:
    def test_surrogate_escaped_cursor_round_trips(self, tmp_path):
        """``_write_cleanup_cursor__mutmut_25`` / ``_31`` (``ensure_ascii`` falsy).

        Without ASCII escaping the lone surrogate of a non-UTF-8 file name
        cannot be encoded and the write fails with ``UnicodeEncodeError``.
        """
        state_path = tmp_path / STATE_FILENAME

        cleanup_module._write_cleanup_cursor(state_path, "facility/\udcff.enc")

        assert cleanup_module._read_cleanup_cursor(state_path) == ("facility/\udcff.enc", None)
        assert sorted(path.name for path in tmp_path.iterdir()) == [STATE_FILENAME]

    def test_sweep_resumes_behind_non_utf8_file_name(self, media_root):
        """``_write_cleanup_cursor__mutmut_25`` / ``_31`` through the sweep.

        ``os.scandir`` yields the name ``b"\\xff.enc"`` as ``"\\udcff.enc"``.
        When that file is the last one checked, the mutants crash the run with
        ``UnicodeEncodeError`` instead of persisting it as the cursor.
        """
        fd = os.open(os.fsencode(media_root) + b"/\xff.enc", os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        _write_enc(media_root, "ｚ.enc", age_seconds=YOUNG)

        first = _sweep(budget=1)
        second = _sweep(budget=1)

        assert first.checked_paths == ("\udcff.enc",)
        assert first.next_start == "ｚ.enc"
        assert second.checked_paths == ("ｚ.enc",)
        assert second.continued_at == "ｚ.enc"
        assert second.tree_complete is True

    def test_temp_file_creation_failure_surfaces_as_cursor_write_error(self, media_root, monkeypatch):
        """``_write_cleanup_cursor__mutmut_1`` (temp path default ``None`` -> ``""``).

        When the temp file cannot even be created, the cleanup handler of the
        mutant calls ``"".unlink`` and raises ``AttributeError`` instead of the
        original ``OSError``; the service then cannot report the completed run.
        The existing write-error test only lets ``os.replace`` fail.
        """
        for name in ("a.enc", "b.enc"):
            _write_enc(media_root, name, age_seconds=YOUNG)
        failure = OSError(28, "No space left on device")

        def refuse_temp_file(*args, **kwargs):
            raise failure

        with monkeypatch.context() as patch:
            patch.setattr(cleanup_module.tempfile, "NamedTemporaryFile", refuse_temp_file)
            with pytest.raises(cleanup_module.CleanupCursorWriteError) as exc_info:
                _sweep(budget=1)

        assert exc_info.value.cause is failure
        assert exc_info.value.result.checked_paths == ("a.enc",)
        assert not (media_root / STATE_FILENAME).exists()

        second = _sweep(budget=1)

        assert second.checked_paths == ("a.enc",)

    def test_failed_temp_cleanup_keeps_the_original_write_error(self, media_root, monkeypatch):
        """``_write_cleanup_cursor__mutmut_40`` (``suppress(OSError)`` -> ``suppress(None)``).

        If ``os.replace`` fails and removing the temp file fails as well, the
        mutant raises ``TypeError`` from ``suppress`` and the replace error is
        lost; the service must still raise ``CleanupCursorWriteError`` carrying
        the replace error, and the old state must stay in effect.
        """
        for name in ("a.enc", "b.enc"):
            _write_enc(media_root, name, age_seconds=YOUNG)
        replace_failure = OSError("disk full")
        original_unlink = Path.unlink

        def fail_replace(_source, _target):
            raise replace_failure

        def refuse_temp_unlink(self, missing_ok=False):
            if self.name.endswith(".tmp"):
                raise PermissionError(13, "Permission denied")
            return original_unlink(self, missing_ok=missing_ok)

        with monkeypatch.context() as patch:
            patch.setattr(cleanup_module.os, "replace", fail_replace)
            patch.setattr(cleanup_module.Path, "unlink", refuse_temp_unlink)
            with pytest.raises(cleanup_module.CleanupCursorWriteError) as exc_info:
                _sweep(budget=1)

        assert exc_info.value.cause is replace_failure
        assert exc_info.value.result.checked_paths == ("a.enc",)
        assert not (media_root / STATE_FILENAME).exists()

        second = _sweep(budget=1)

        assert second.checked_paths == ("a.enc",)

    def test_hard_abort_leaves_only_a_hidden_state_temp_file(self, media_root, monkeypatch):
        """``_write_cleanup_cursor__mutmut_5`` / ``_11`` (temp ``prefix`` dropped).

        A hard abort between writing and activating the cursor skips the
        cleanup; the leftover must be hidden and named after the state file,
        not a generic ``tmp*.tmp`` next to the media files. The existing
        atomic-replace test only checks the directory and the ``.tmp`` suffix.
        """
        for name in ("a.enc", "b.enc"):
            _write_enc(media_root, name, age_seconds=YOUNG)

        def abort_replace(_source, _target):
            raise _HardAbort

        with monkeypatch.context() as patch:
            patch.setattr(cleanup_module.os, "replace", abort_replace)
            with pytest.raises(_HardAbort):
                _sweep(budget=1)

        leftovers = sorted(path.name for path in media_root.iterdir() if not path.name.endswith(".enc"))
        assert len(leftovers) == 1
        assert leftovers[0].startswith(f".{STATE_FILENAME}.")
        assert leftovers[0].endswith(".tmp")
        assert json.loads((media_root / leftovers[0]).read_text(encoding="utf-8")) == {"after": "a.enc"}

        second = _sweep(budget=1)

        assert second.checked_paths == ("a.enc",)


class TestCleanupCursorWriteErrorInit:
    @pytest.mark.parametrize(
        "cause",
        [
            pytest.param(OSError("disk full"), id="message"),
            pytest.param(OSError(28, "No space left on device"), id="errno"),
        ],
    )
    def test_message_is_the_cause_message(self, tmp_path, cause):
        """``CleanupCursorWriteError.__init____mutmut_1`` / ``_2`` (message -> ``None``/``"None"``)."""
        result = cleanup_module.OrphanCleanupResult(
            checked_paths=("a.enc",),
            deleted=0,
            deletion_candidates=0,
            skipped_referenced=0,
            skipped_young=1,
            errors=0,
            continued_at="a.enc",
            next_start=None,
            tree_complete=True,
            state_warning=None,
            dry_run=False,
        )

        error = cleanup_module.CleanupCursorWriteError(tmp_path / STATE_FILENAME, result, cause)

        assert str(error) == str(cause)
        assert error.args == (str(cause),)
        assert error.cause is cause
        assert error.result is result


class TestCleanupOrphanStorageFiles:
    @pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "real-run"])
    def test_missing_media_root_reports_an_empty_complete_run(self, settings, tmp_path, dry_run):
        """Early exit without media root: ``cleanup_orphan_storage_files__mutmut_11``,
        ``_13``, ``_14``, ``_15``, ``_16``, ``_17``, ``_18``, ``_31``, ``_32``,
        ``_33``, ``_34``, ``_35``.

        Every field of the early-exit report is pinned: no checked paths,
        zero counters (not ``None``/``1``), a complete tree and the caller's
        dry-run flag. The existing missing-root test only compares the
        integer value of the result.
        """
        missing = tmp_path / "missing"
        settings.MEDIA_ROOT = str(missing)

        runs = [_sweep(dry_run=dry_run), _sweep(dry_run=dry_run)]

        for result in runs:
            assert result.checked_paths == ()
            assert result.checked == 0
            counters = {
                "deleted": result.deleted,
                "deletion_candidates": result.deletion_candidates,
                "skipped_referenced": result.skipped_referenced,
                "skipped_young": result.skipped_young,
                "errors": result.errors,
            }
            assert counters == dict.fromkeys(counters, 0)
            assert all(type(value) is int for value in counters.values())
            assert result.continued_at is None
            assert result.next_start is None
            assert result.tree_complete is True
            assert result.state_warning is None
            assert result.dry_run is dry_run
        assert not missing.exists()

    def test_each_counter_counts_every_file_of_its_kind(self, media_root, reference_names):
        """``cleanup_orphan_storage_files__mutmut_89`` / ``_94`` / ``_98`` (``+= 1`` -> ``= 1``).

        Two files per kind; the existing tests have exactly one referenced,
        one young and one deletion candidate per run, where ``= 1`` and
        ``+= 1`` agree.
        """
        reference_names.update({"ref-1.enc", "ref-2.enc"})
        for name in ("ref-1.enc", "ref-2.enc"):
            _write_enc(media_root, name, age_seconds=OLD)
        young = [_write_enc(media_root, name, age_seconds=YOUNG) for name in ("young-1.enc", "young-2.enc")]
        orphans = [_write_enc(media_root, name, age_seconds=OLD) for name in ("orphan-1.enc", "orphan-2.enc")]

        first = _sweep()
        second = _sweep()

        assert first.checked == 6
        assert first.skipped_referenced == 2
        assert first.skipped_young == 2
        assert first.deletion_candidates == 2
        assert first.deleted == 2
        assert first.errors == 0
        assert not any(path.exists() for path in orphans)
        assert all(path.exists() for path in young)
        assert second.checked_paths == ("ref-1.enc", "ref-2.enc", "young-1.enc", "young-2.enc")
        assert second.skipped_referenced == 2
        assert second.skipped_young == 2
        assert second.deletion_candidates == 0

    def test_every_failed_deletion_is_counted_once(self, media_root, monkeypatch):
        """``cleanup_orphan_storage_files__mutmut_111`` / ``_112`` / ``_113``
        (``errors += 1`` -> ``= 1`` / ``-= 1`` / ``+= 2``).

        No existing service test reaches the ``except OSError`` branch with a
        checked error count.
        """
        orphans = [_write_enc(media_root, name, age_seconds=OLD) for name in ("a.enc", "b.enc")]
        original_unlink = Path.unlink

        def refuse_enc_unlink(self, missing_ok=False):
            if self.name.endswith(".enc"):
                raise PermissionError(13, "Permission denied")
            return original_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(cleanup_module.Path, "unlink", refuse_enc_unlink)

        first = _sweep()
        second = _sweep()

        for result in (first, second):
            assert result.checked_paths == ("a.enc", "b.enc")
            assert result.deletion_candidates == 2
            assert result.deleted == 0
            assert result.errors == 2
        assert all(path.exists() for path in orphans)

    def test_dry_run_checks_its_whole_budget(self, media_root):
        """``cleanup_orphan_storage_files__mutmut_101`` (``continue`` -> ``break``)
        and ``_135`` (reported ``dry_run`` -> ``None``).

        A dry run must preview every file of its budget and name the next
        start; the mutant stops at the first candidate and reports a complete
        tree. The existing dry-run test uses a budget of one file. A dry run
        persists nothing, so the second dry run starts at the beginning again.
        """
        orphans = [_write_enc(media_root, name, age_seconds=OLD) for name in ("a.enc", "b.enc", "c.enc")]

        first = _sweep(budget=2, dry_run=True)
        second = _sweep(budget=2, dry_run=True)

        for result in (first, second):
            assert result.checked_paths == ("a.enc", "b.enc")
            assert result.deletion_candidates == 2
            assert result.deleted == 0
            assert result.next_start == "c.enc"
            assert result.tree_complete is False
            assert result.dry_run is True
        assert all(path.exists() for path in orphans)
        assert not (media_root / STATE_FILENAME).exists()

    def test_budget_limited_run_reports_its_next_start(self, media_root):
        """``cleanup_orphan_storage_files__mutmut_132`` (reported ``next_start`` -> ``None``).

        The existing tests read ``next_start`` only after complete runs, where
        it is ``None`` anyway.
        """
        for name in ("a.enc", "b.enc"):
            _write_enc(media_root, name, age_seconds=YOUNG)

        first = _sweep(budget=1)
        second = _sweep(budget=1)

        assert first.checked_paths == ("a.enc",)
        assert first.next_start == "b.enc"
        assert first.tree_complete is False
        assert second.continued_at == "b.enc"
        assert second.next_start is None
        assert second.tree_complete is True
