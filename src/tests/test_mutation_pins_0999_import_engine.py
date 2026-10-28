"""Pin selected import profiles; document equivalent strict-reader changes.

Source: mutation delta of the release-0.99.9 checkpoint, 4 mutants.
Groups: selected profile (1), strict CSV reader (3, equivalent for the proposed input).
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Client, DocumentType, ImportJob
from core.services import import_engine

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def import_storage(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)


def test_import_preserves_selected_profile_among_multiple_active_types(facility, admin_user):
    """An explicit profile disambiguates multiple active import targets.

    x__import_client_row__mutmut_58.
    """
    DocumentType.objects.create(facility=facility, name="Other profile", entity_type="client")
    selected = DocumentType.objects.create(facility=facility, name="Selected profile", entity_type="client")
    job = import_engine.store_import_upload(
        facility,
        admin_user,
        SimpleUploadedFile("people.csv", b"Pseudonym\nImported-01\n", content_type="text/csv"),
        target_type=ImportJob.TargetType.CLIENT,
        document_type=selected,
    )
    result = import_engine.probe_import(job, admin_user, {"Pseudonym": "pseudonym"})
    assert result["summary"]["errors"] == 0
    import_engine.commit_import(job, admin_user)
    assert Client.objects.for_facility(facility).get(pseudonym="Imported-01").document_type == selected
