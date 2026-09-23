"""Pins fuer den Kontingent-Backstop beim Import-Upload (``core.services.import_engine``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 3 von 5 Mutanten in
``store_import_upload`` (``x__lock_job_fk_rows__mutmut_13/21`` betrafen die
FK-Ableitung, die inzwischen in ``core.services.fk_locks`` liegt). Gruppen:

* Der Backstop bekommt die tatsaechlich hochladende Person (sie steht in der
  Audit-Zeile des Backstops).
* Das Aufraeumen nach dem Backstop ignoriert eine bereits fehlende Datei —
  sonst verdraengte ein ``FileNotFoundError`` die Backstop-Ausnahme.
"""

from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import ImportJob
from core.services import import_engine
from core.services.file_vault.policy import StorageQuotaBackstopError

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

_BELEGUNG = SimpleNamespace(used_mb=10, limit_mb=10, trash_mb=0)
CSV = "Pseudonym;Kontaktstufe;Altersgruppe;Notizen\nIMP-0993;;;\n"


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _hochladen(facility, user):
    upload = SimpleUploadedFile("import.csv", CSV.encode("utf-8"), content_type="text/csv")
    return import_engine.store_import_upload(facility, user, upload, target_type=ImportJob.TargetType.CLIENT)


class TestStoreImportUpload:
    def test_backstop_bekommt_die_hochladende_person(self, facility, admin_user, media_root, monkeypatch):
        """x_store_import_upload__mutmut_77: ``user=None`` an den Backstop
        nimmt der Audit-Zeile die ausloesende Person."""
        aufrufe = []
        original = import_engine.check_import_storage_quota_at_commit

        def _spy(fac, user, groesse):
            aufrufe.append((fac, user))
            return original(fac, user, groesse)

        monkeypatch.setattr(import_engine, "check_import_storage_quota_at_commit", _spy)
        _hochladen(facility, admin_user)
        assert aufrufe == [(facility, admin_user)]

    def test_backstop_bei_bereits_fehlender_datei_bleibt_backstop(self, facility, admin_user, media_root, monkeypatch):
        """x_store_import_upload__mutmut_82 (``missing_ok=None``) und _83
        (``missing_ok`` fehlt): das Aufraeumen einer schon entfernten Datei
        wirft ``FileNotFoundError`` statt die Backstop-Ausnahme durchzulassen."""

        def _backstop(fac, user, groesse):
            for datei in import_engine._import_dir(fac).glob("*.enc"):
                datei.unlink()
            raise StorageQuotaBackstopError(
                facility=fac, user=user, owner=None, usage=_BELEGUNG, requested_bytes=groesse, target_type="ImportJob"
            )

        monkeypatch.setattr(import_engine, "check_import_storage_quota_at_commit", _backstop)
        with pytest.raises(StorageQuotaBackstopError) as exc_info:
            _hochladen(facility, admin_user)
        assert exc_info.value.user == admin_user
        assert ImportJob.objects.filter(facility=facility).count() == 0
