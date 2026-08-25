"""Unit-Tests fuer den ``stage_uploads()``-Context-Manager (Refs #1831).

Der CM kapselt den 3x kopierten Pre-Transaction-Staging-Block der Create-Views
(Event/Client/Case): ``prepare_encrypted_upload`` je Upload, Gruppierung je
Slug, und die Cleanup-Garantie — JEDE Nicht-Commit-Ausgang (Rollback,
Duplikat-Replay-Redirect, unerwartete Exception, Staging-ValidationError)
entfernt die bereits verschluesselten ``.enc``-Dateien restlos (Refs #1345).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from core.services.attachments import stage_uploads
from core.services.file_vault.storage import StagedUpload


class _FakeStaged:
    """StagedUpload-Double mit Datei in einem tmp-Verzeichnis."""

    def __init__(self, tmp_dir: Path, name: str):
        self.output_path = tmp_dir / f"{name}.enc"
        self.output_path.write_bytes(b"enc")
        self.storage_name = f"{name}.enc"


@pytest.fixture
def facility_stub():
    return SimpleNamespace(pk="fac-1")


@pytest.fixture
def patch_prepare(monkeypatch):
    """``prepare_encrypted_upload`` durch ein Fake ersetzen, das Dateien in
    ``tmp_path`` schreibt und optional bei der n-ten Datei wirft."""

    def _install(tmp_path: Path, fail_at: str | None = None, exc: Exception | None = None):
        created: list[_FakeStaged] = []

        def _fake(facility, uploaded_file, user, *, owner=None):
            if uploaded_file == fail_at:
                raise exc
            staged = _FakeStaged(tmp_path, f"staged-{uploaded_file}")
            created.append(staged)
            return staged

        monkeypatch.setattr("core.services.file_vault.prepare_encrypted_upload", _fake)
        return created

    return _install


def _file_fields(*slugs: str):
    return {slug: [slug] for slug in slugs}


class TestStageUploadsHappyPath:
    def test_yields_by_slug_and_keeps_files_until_commit(self, tmp_path, facility_stub, patch_prepare):
        created = patch_prepare(tmp_path)
        with stage_uploads(facility_stub, None, _file_fields("anhang", "bericht")) as staged:
            assert set(staged.by_slug) == {"anhang", "bericht"}
            assert [s.output_path for s in staged.by_slug["anhang"]] == [created[0].output_path]
            # Noch nicht committet: Cleanup-Garantie steht bei Verlassen des
            # with-Blocks ohne commit() — Dateien existieren hier aber schon.
            assert all(s.output_path.exists() for s in created)
            staged.commit()
        assert all(s.output_path.exists() for s in created), "commit() muss die .enc-Dateien erhalten"

    def test_empty_file_fields_is_noop(self, tmp_path, facility_stub, patch_prepare):
        patch_prepare(tmp_path)
        with stage_uploads(facility_stub, None, {}) as staged:
            assert staged.by_slug == {}
            staged.commit()

    def test_none_upload_list_skips_slug(self, tmp_path, facility_stub, patch_prepare):
        created = patch_prepare(tmp_path)
        with stage_uploads(facility_stub, None, {"leer": None}) as staged:
            assert staged.by_slug == {}
            staged.commit()
        assert created == []


class TestStageUploadsCleanupGuarantee:
    def test_rollback_leaves_no_enc_file(self, tmp_path, facility_stub, patch_prepare):
        """DoD (Refs #1831): Exception im Body (z.B. Rollback des DB-Write)
        → alle pre-tx verschluesselten ``.enc`` werden restlos entfernt."""
        created = patch_prepare(tmp_path)
        with pytest.raises(RuntimeError, match="boom"):
            with stage_uploads(facility_stub, None, _file_fields("a", "b")):
                raise RuntimeError("boom")
        assert all(not s.output_path.exists() for s in created)

    def test_return_without_commit_cleans_up(self, tmp_path, facility_stub, patch_prepare):
        """Duplikat-Replay-Redirect: sauberes Verlassen ohne commit() ist ein
        Nicht-Commit-Ausgang — die ``.enc`` werden entfernt (#1345)."""
        created = patch_prepare(tmp_path)

        class _LeaveEarly(Exception):
            pass

        with pytest.raises(_LeaveEarly), stage_uploads(facility_stub, None, _file_fields("a")):
            raise _LeaveEarly
        assert all(not s.output_path.exists() for s in created)

    def test_staging_validation_error_cleans_partial_batch(self, tmp_path, facility_stub, patch_prepare):
        """Policy-/Virus-Reject beim ZWEITEN Upload: die bereits gestagte
        erste ``.enc`` wird entfernt, die ValidationError propagiert zum
        Aufrufer (die View formuliert ihre eigene Antwort)."""
        created = patch_prepare(
            tmp_path,
            fail_at="zweit",
            exc=ValidationError("Virus gefunden"),
        )
        with pytest.raises(ValidationError):
            with stage_uploads(facility_stub, None, {"erst": ["erst"], "zweit": ["zweit"]}):
                pass
        assert len(created) == 1, "nur der erste Upload sollte gestagt werden"
        assert not created[0].output_path.exists(), "Teil-Batch muss aufgeräumt werden"

    def test_real_staged_upload_type_accepted(self, tmp_path, facility_stub, patch_prepare, monkeypatch):
        """Der CM ist gegen den echten :class:`StagedUpload`-Datentyp getestet
        (Attribut ``output_path``), nicht nur gegen das Test-Double."""
        real = StagedUpload(
            storage_name="x.enc",
            output_path=tmp_path / "x.enc",
            detected_mime="application/pdf",
            file_size=3,
            original_name="x.pdf",
            content_type="application/pdf",
        )
        real.output_path.write_bytes(b"enc")
        monkeypatch.setattr("core.services.file_vault.prepare_encrypted_upload", lambda *a, **k: real)
        with pytest.raises(RuntimeError, match="x"):
            with stage_uploads(facility_stub, None, {"feld": ["u"]}):
                raise RuntimeError("x")
        assert not real.output_path.exists()
