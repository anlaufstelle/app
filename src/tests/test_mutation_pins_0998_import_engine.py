"""Pins for CSV structure, imported numbers, diagnostics and cleanup.

Source: mutation delta of the release-0.99.8 checkpoint, 24 mutants.
Groups: CSV reader (3), quote positions (16), numeric representation (1),
abort and retention (3), validation fallback (1).
All behavior is exercised through upload, import or cleanup service APIs.
"""

import csv
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.utils.translation import gettext

from core.models import AuditLog, Client, DocumentType, DocumentTypeField, FieldTemplate, ImportJob
from core.services import import_engine

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def import_storage(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)


@pytest.fixture
def upload(facility, admin_user):
    def store(content, *, delimiter=";", document_type=None):
        return import_engine.store_import_upload(
            facility,
            admin_user,
            SimpleUploadedFile("example.csv", content.encode(), content_type="text/csv"),
            target_type=ImportJob.TargetType.CLIENT,
            delimiter=delimiter,
            document_type=document_type,
        )

    return store


def _structural_message(line, record):
    return gettext(
        "Die Datei konnte nicht als CSV gelesen werden (Fehler in Zeile %(line)s, Datensatz %(record)s). "
        "Bitte Anführungszeichen und Zeilenumbrüche innerhalb von Feldern prüfen."
    ) % {"line": line, "record": record}


@pytest.mark.parametrize(
    ("content", "delimiter", "line", "record"),
    [
        ('Name\rprefix"bad', "\r", 2, 1),
        ('Name\rprefix"bad', ";", 2, 2),
        ('Name\n\nprefix"bad', ";", 3, 3),
        ('Name\r\nprefix"bad', ";", 2, 2),
        ('Name\nX"bad"', ";", 2, 2),
        ('Name\n"closed"suffix\nlater"bad', ";", 2, 2),
        ('Name\nfirst\n"unfinished', ";", 3, 3),
        ('Name\n"first\rsecond', ";", 3, 2),
    ],
    ids=[
        "cr-delimiter",
        "lone-cr",
        "blank-line",
        "crlf",
        "literal-x",
        "first-error",
        "unterminated-record",
        "unterminated-cr",
    ],
)
def test_upload_reports_exact_csv_positions(upload, content, delimiter, line, record):
    """Upload diagnoses the first invalid position without exposing cell values.

    x__validate_csv_quote_positions__mutmut_13, x__validate_csv_quote_positions__mutmut_15,
    x__validate_csv_quote_positions__mutmut_19, x__validate_csv_quote_positions__mutmut_21,
    x__validate_csv_quote_positions__mutmut_22, x__validate_csv_quote_positions__mutmut_26,
    x__validate_csv_quote_positions__mutmut_28, x__validate_csv_quote_positions__mutmut_52,
    x__validate_csv_quote_positions__mutmut_72, x__validate_csv_quote_positions__mutmut_73,
    x__validate_csv_quote_positions__mutmut_82, x__validate_csv_quote_positions__mutmut_83,
    x__validate_csv_quote_positions__mutmut_85.
    """
    with pytest.raises(ValidationError) as error:
        upload(content, delimiter=delimiter)
    assert error.value.messages == [_structural_message(line, record)]


def test_upload_identifies_an_oversized_header_as_record_one(upload):
    """Header reader failures identify record one: x__parse_csv__mutmut_17, x__parse_csv__mutmut_20."""
    original_limit = csv.field_size_limit()
    try:
        csv.field_size_limit(16)
        with pytest.raises(ValidationError) as error:
            upload("H" * 17 + "\nvalue\n")
    finally:
        csv.field_size_limit(original_limit)
    assert error.value.messages == [_structural_message(1, 1)]


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_quoted_first_data_field_can_be_committed(upload, admin_user, newline):
    """Quoted first fields remain valid after a record separator.

    x__validate_csv_quote_positions__mutmut_53, x__validate_csv_quote_positions__mutmut_54,
    x__validate_csv_quote_positions__mutmut_55.
    """
    job = upload(f'Pseudonym{newline}"Quoted-01"{newline}')
    import_engine.probe_import(job, admin_user, {"Pseudonym": "pseudonym"})
    import_engine.commit_import(job, admin_user)
    assert Client.objects.for_facility(job.facility).get(pseudonym="Quoted-01")


def test_import_preserves_integer_and_fractional_number_types(upload, facility, admin_user):
    """Integral imported values remain integers: x__coerce_number__mutmut_47."""
    doc = DocumentType.objects.create(
        facility=facility, name="Numeric profile", entity_type=DocumentType.EntityType.CLIENT
    )
    field = FieldTemplate.objects.create(
        facility=facility, name="Amount", slug="amount", field_type=FieldTemplate.FieldType.NUMBER
    )
    DocumentTypeField.objects.create(document_type=doc, field_template=field)
    job = upload("Pseudonym;Amount\nInteger-01;12\nFraction-01;12,5\n", document_type=doc)
    result = import_engine.probe_import(job, admin_user, {"Pseudonym": "pseudonym", "Amount": "data_json.amount"})
    assert result["summary"]["errors"] == 0
    import_engine.commit_import(job, admin_user)
    integer = Client.objects.for_facility(facility).get(pseudonym="Integer-01").data_json["amount"]
    fraction = Client.objects.for_facility(facility).get(pseudonym="Fraction-01").data_json["amount"]
    assert type(integer) is int
    assert integer == 12
    assert type(fraction) is float
    assert fraction == 12.5


def test_default_abort_tolerates_file_deletion_failure(upload, admin_user):
    """Default abort still clears references and audits after an OSError: x_abort_import__mutmut_3."""
    job = upload("Pseudonym\nExample-01\n")
    with patch.object(Path, "unlink", side_effect=OSError("Deletion unavailable")):
        import_engine.abort_import(job, admin_user)
    job.refresh_from_db()
    assert job.status == ImportJob.Status.ABORTED
    assert job.storage_filename == ""
    assert job.original_filename_encrypted == {}
    assert AuditLog.objects.filter(action=AuditLog.Action.IMPORT_ABORTED, target_id=str(job.pk)).exists()


def test_cleanup_collects_failed_ids_and_continues_with_later_jobs(upload, facility):
    """Cleanup reports both failed jobs and processes a later successful job.

    x_cleanup_stale_import_jobs__mutmut_29, x_cleanup_stale_import_jobs__mutmut_37.
    """
    jobs = [upload(f"Pseudonym\nExample-{index}\n") for index in range(3)]
    now = timezone.now()
    for index, job in enumerate(jobs):
        ImportJob.objects.filter(pk=job.pk).update(
            created_at=now - timedelta(days=import_engine.STALE_DAYS + index + 1)
        )
    failed_paths = {import_engine.import_file_path(job) for job in jobs[:2]}
    successful_path = import_engine.import_file_path(jobs[2])
    original_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path in failed_paths:
            raise OSError("Deletion unavailable")
        return original_unlink(path, *args, **kwargs)

    failed_ids = set()
    with patch.object(Path, "unlink", unlink):
        cleaned = import_engine.cleanup_stale_import_jobs(facility, now=now, failed_ids=failed_ids)
    assert failed_ids == {job.pk for job in jobs[:2]}
    assert cleaned == 1
    for job in jobs[:2]:
        job.refresh_from_db()
        assert job.status == ImportJob.Status.UPLOADED
        assert job.storage_filename
        assert import_engine.import_file_path(job).exists()
    jobs[2].refresh_from_db()
    assert jobs[2].status == ImportJob.Status.ABORTED
    assert jobs[2].storage_filename == ""
    assert not successful_path.exists()


def test_empty_service_validation_error_uses_catalog_fallback(upload, admin_user):
    """Empty validation errors retain the catalog message: x__classify_validation_error__mutmut_8."""
    job = upload("Pseudonym\nExample-01\n")
    with patch.object(import_engine, "create_client", side_effect=ValidationError([])):
        result = import_engine.probe_import(job, admin_user, {"Pseudonym": "pseudonym"})
    assert result["errors"] == [
        {"row": 2, "column": "", "code": "validation", "message": str(import_engine.ERROR_MESSAGES["validation"])}
    ]
    job.refresh_from_db()
    assert job.result_json == result


@pytest.mark.timeout(10, method="signal", func_only=True)
def test_header_only_upload_finishes_without_data_rows(upload):
    """A header-only upload terminates with no data: x__parse_csv_rows__mutmut_8."""
    job = upload("Pseudonym\n")
    assert import_engine.read_rows(job) == (["Pseudonym"], [])
