"""CSV-Import-Engine: Probelauf, Uebernahme, Koerzierung, PII-Disziplin (Refs #1060).

M4 Strang A / P3. Die Engine schreibt ausschliesslich ueber den Service-Layer
(``create_client``/``create_event``) — dadurch kommen Verschluesselung,
Kontaktstufen-Gates, EventHistory, Activity und Audit automatisch mit. Probelauf
und Uebernahme laufen durch **denselben** Codepfad; nur das Schluss-Rollback
unterscheidet sie (kein Validierungs-Drift).
"""

import io
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, IntegrityError
from django.utils import timezone

from core.models import Activity, AuditLog, Client, DocumentTypeField, Event, FieldTemplate, ImportJob
from core.services import import_engine
from core.services.file_vault import decrypt_field, encrypt_file

PSEUDONYM_HEADER = "Pseudonym;Kontaktstufe;Altersgruppe;Notizen\n"


def _upload(content, name="import.csv", encoding="utf-8"):
    """CSV als hochgeladene Datei — Bytes wie sie der Browser schickt."""
    raw = content.encode(encoding) if isinstance(content, str) else content
    return SimpleUploadedFile(name, raw, content_type="text/csv")


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def client_job(facility, admin_user, media_root):
    """Personen-Import mit drei Zeilen, bereits zugeordnet und geprueft-bereit."""
    content = PSEUDONYM_HEADER + "IMP-01;identified;u18;Erste\nIMP-02;qualified;27_plus;Zweite\nIMP-03;;;\n"
    job = import_engine.store_import_upload(
        facility,
        admin_user,
        _upload(content),
        target_type=ImportJob.TargetType.CLIENT,
    )
    import_engine.apply_mapping(
        job,
        {
            "Pseudonym": "pseudonym",
            "Kontaktstufe": "contact_stage",
            "Altersgruppe": "age_cluster",
            "Notizen": "notes",
        },
    )
    return job


def _profile_job(facility, admin_user, doc_type, content, mapping):
    job = import_engine.store_import_upload(
        facility,
        admin_user,
        _upload(content),
        target_type=ImportJob.TargetType.CLIENT,
        document_type=doc_type,
    )
    import_engine.apply_mapping(job, mapping)
    return job


def _single_row_client_job(facility, admin_user, pseudonym="CHAR-PII"):
    job = import_engine.store_import_upload(
        facility,
        admin_user,
        _upload(f"Pseudonym\n{pseudonym}\n"),
        target_type=ImportJob.TargetType.CLIENT,
    )
    import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
    return job


@pytest.mark.django_db
class TestStoreImportUpload:
    def test_creates_job_in_uploaded_state(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility, admin_user, _upload(PSEUDONYM_HEADER + "A;;;\n"), target_type=ImportJob.TargetType.CLIENT
        )
        assert job.status == ImportJob.Status.UPLOADED
        assert job.facility == facility
        assert job.created_by == admin_user

    def test_file_on_disk_is_encrypted(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER + "GEHEIM-PSEUDONYM;;;\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        path = import_engine.import_file_path(job)
        assert path.exists()
        raw = path.read_bytes()
        assert b"GEHEIM-PSEUDONYM" not in raw
        # A4.5-Bindung mitpinnen (Muster test_file_vault.py): ohne das
        # Versionsbyte bliebe ein Downgrade auf das ungebundene v1-Format
        # (file_id aus dem encrypt_file-Aufruf entfernt) unentdeckt — die
        # Klartext-Probe oben wuerde weiterhin gruen sein.
        assert raw[0] == 2, "Import-Upload muss v2-gebunden (file_id) geschrieben werden."

    def test_original_filename_is_only_stored_encrypted(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER, name="Klientenliste Meier.csv"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        assert job.original_filename_encrypted.get("__encrypted__") is True
        assert decrypt_field(job.original_filename_encrypted) == "Klientenliste Meier.csv"
        assert "Meier" not in str(job.original_filename_encrypted["value"])

    def test_writes_audit_entry_without_filename(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER + "A;;;\n", name="Klientenliste Meier.csv"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        entry = AuditLog.objects.filter(action=AuditLog.Action.IMPORT_UPLOADED).latest("timestamp")
        assert entry.target_id == str(job.pk)
        assert "Meier" not in str(entry.detail)
        assert entry.detail["rows"] == 1

    def test_rejects_files_above_the_byte_limit(self, facility, admin_user, media_root):
        oversized = _upload(b"x" * (import_engine.MAX_BYTES + 1))
        with pytest.raises(ValidationError):
            import_engine.store_import_upload(facility, admin_user, oversized, target_type=ImportJob.TargetType.CLIENT)
        assert ImportJob.objects.for_facility(facility).count() == 0

    @pytest.mark.parametrize("fmt", ["BMP", "TIFF", "JPEG", "PNG", "GIF", "WEBP"])
    def test_rejects_binary_image_payloads(self, facility, admin_user, media_root, fmt):
        """Refs #2128 (Fix-Runde 1): Der Import-Wizard verschluesselt Rohbytes
        direkt (``encrypt_file``) und umgeht damit die File-Vault-Pipeline —
        also auch die Metadaten-Bereinigung. Bislang begrenzte ihn serverseitig
        nichts auf CSV: ein BMP oder TIFF laesst sich als CP1252 dekodieren und
        von ``csv.reader`` als Kopfzeile akzeptieren. Damit haette #2128 einen
        Bild-Bypass in einem zweiten Speicherpfad."""
        import io as _io

        from PIL import Image

        img = Image.new("RGB", (4, 2), (7, 8, 9))
        buf = _io.BytesIO()
        (img.convert("P") if fmt == "GIF" else img).save(buf, format=fmt)

        with pytest.raises(ValidationError):
            import_engine.store_import_upload(
                facility,
                admin_user,
                _upload(buf.getvalue(), name="tarnung.csv"),
                target_type=ImportJob.TargetType.CLIENT,
            )
        assert ImportJob.objects.for_facility(facility).count() == 0

    def test_rejects_pdf_and_zip_payloads(self, facility, admin_user, media_root):
        import zipfile

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("word/document.xml", "<document/>")

        for payload in (b"%PDF-1.4\ntrailer<</Size 1>>\n%%EOF\n", zip_buf.getvalue()):
            with pytest.raises(ValidationError):
                import_engine.store_import_upload(
                    facility,
                    admin_user,
                    _upload(payload, name="tarnung.csv"),
                    target_type=ImportJob.TargetType.CLIENT,
                )
        assert ImportJob.objects.for_facility(facility).count() == 0

    def test_real_csv_with_bom_and_cp1252_still_accepted(self, facility, admin_user, media_root):
        """Gegenprobe: die beiden real vorkommenden Excel-Exporte bleiben gueltig."""
        content = PSEUDONYM_HEADER + "IMP-Ü;identified;u18;Straße\n"

        bom_job = import_engine.store_import_upload(
            facility, admin_user, _upload(content, encoding="utf-8-sig"), target_type=ImportJob.TargetType.CLIENT
        )
        cp_job = import_engine.store_import_upload(
            facility, admin_user, _upload(content, encoding="cp1252"), target_type=ImportJob.TargetType.CLIENT
        )

        assert bom_job.pk is not None
        assert cp_job.pk is not None

    def test_rejects_files_above_the_row_limit(self, facility, admin_user, media_root):
        content = PSEUDONYM_HEADER + "".join(f"P-{i};;;\n" for i in range(import_engine.MAX_ROWS + 1))
        with pytest.raises(ValidationError):
            import_engine.store_import_upload(
                facility, admin_user, _upload(content), target_type=ImportJob.TargetType.CLIENT
            )

    def test_rejects_duplicate_column_headers(self, facility, admin_user, media_root):
        with pytest.raises(ValidationError):
            import_engine.store_import_upload(
                facility,
                admin_user,
                _upload("Pseudonym;Pseudonym\nA;B\n"),
                target_type=ImportJob.TargetType.CLIENT,
            )

    def test_rejects_malformed_csv_as_validation_error(self, facility, admin_user, media_root):
        with pytest.raises(ValidationError, match="Fehler in Zeile 2"):
            import_engine.store_import_upload(
                facility,
                admin_user,
                _upload(b"Pseudonym\r\na\rb\r\n"),
                target_type=ImportJob.TargetType.CLIENT,
            )

    def test_rejects_cr_only_csv_as_header_validation_error(self, facility, admin_user, media_root):
        with pytest.raises(ValidationError, match="Fehler in Zeile 1"):
            import_engine.store_import_upload(
                facility,
                admin_user,
                _upload(b"Pseudonym\rA\rB\r"),
                target_type=ImportJob.TargetType.CLIENT,
            )

    def test_event_job_without_document_type_is_rejected(self, facility, admin_user, media_root):
        with pytest.raises(ValidationError):
            import_engine.store_import_upload(
                facility, admin_user, _upload("Datum\n01.01.2026\n"), target_type=ImportJob.TargetType.EVENT
            )


@pytest.mark.django_db
class TestParsing:
    def test_preview_returns_header_and_first_rows(self, facility, admin_user, media_root):
        content = PSEUDONYM_HEADER + "".join(f"P-{i};;;\n" for i in range(10))
        job = import_engine.store_import_upload(
            facility, admin_user, _upload(content), target_type=ImportJob.TargetType.CLIENT
        )
        preview = import_engine.parse_preview(job, limit=5)
        assert preview["columns"] == ["Pseudonym", "Kontaktstufe", "Altersgruppe", "Notizen"]
        assert len(preview["rows"]) == 5
        assert preview["total_rows"] == 10

    def test_cp1252_file_is_decoded_via_fallback(self, facility, admin_user, media_root):
        content = "Pseudonym;Notizen\nP-1;Straße\n"
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(content, encoding="cp1252"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        _header, rows = import_engine.read_rows(job)
        assert rows[0][1] == "Straße"

    def test_comma_delimiter_is_detected(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Pseudonym,Notizen\nP-1,Notiz\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        header, rows = import_engine.read_rows(job)
        assert header == ["Pseudonym", "Notizen"]
        assert rows == [["P-1", "Notiz"]]

    def test_explicit_delimiter_overrides_detection(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Pseudonym\tNotizen\nP-1\tNotiz\n"),
            target_type=ImportJob.TargetType.CLIENT,
            delimiter="\t",
        )
        header, _rows = import_engine.read_rows(job)
        assert header == ["Pseudonym", "Notizen"]

    def test_blank_lines_are_skipped(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER + "P-1;;;\n\n;;;\nP-2;;;\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        _header, rows = import_engine.read_rows(job)
        assert len(rows) == 2

    def test_preview_rejects_malformed_stored_csv_as_validation_error(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Pseudonym\nA\n"),
            target_type=ImportJob.TargetType.CLIENT,
            delimiter=";",
        )
        encrypt_file(
            io.BytesIO(b"Pseudonym\r\na\rb\r\n"),
            import_engine.import_file_path(job),
            file_id=job.storage_filename,
        )

        with pytest.raises(ValidationError, match="Fehler in Zeile 2"):
            import_engine.parse_preview(job)


@pytest.mark.django_db
class TestAvailableTargets:
    def test_client_targets_contain_the_master_data_fields(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility, admin_user, _upload(PSEUDONYM_HEADER), target_type=ImportJob.TargetType.CLIENT
        )
        keys = {t.key: t for t in import_engine.available_targets(job)}
        assert set(keys) == {"pseudonym", "contact_stage", "age_cluster", "notes"}
        assert keys["pseudonym"].required is True
        assert keys["notes"].required is False

    def test_event_targets_require_timestamp_and_pseudonym(self, facility, admin_user, doc_type_contact, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Datum;Dauer\n01.01.2026;5\n"),
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_contact,
        )
        keys = {t.key: t for t in import_engine.available_targets(job)}
        assert keys["occurred_at"].required is True
        assert keys["client_pseudonym"].required is True
        assert "data_json.dauer" in keys

    def test_file_fields_are_never_offered(self, facility, admin_user, doc_type_client_profile, media_root):
        file_template = FieldTemplate.objects.create(
            facility=facility,
            name="Ausweis",
            field_type=FieldTemplate.FieldType.FILE,
        )
        DocumentTypeField.objects.create(
            document_type=doc_type_client_profile, field_template=file_template, sort_order=5
        )
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER),
            target_type=ImportJob.TargetType.CLIENT,
            document_type=doc_type_client_profile,
        )
        keys = {t.key for t in import_engine.available_targets(job)}
        assert "data_json.wohnsituation" in keys
        assert "data_json.ausweis" not in keys

    def test_inactive_field_templates_are_not_offered(self, facility, admin_user, doc_type_client_profile, media_root):
        template = FieldTemplate.objects.get(facility=facility, slug="wohnsituation")
        template.is_active = False
        template.save()
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER),
            target_type=ImportJob.TargetType.CLIENT,
            document_type=doc_type_client_profile,
        )
        keys = {t.key for t in import_engine.available_targets(job)}
        assert "data_json.wohnsituation" not in keys


@pytest.mark.django_db
class TestApplyMapping:
    def _job(self, facility, admin_user):
        return import_engine.store_import_upload(
            facility, admin_user, _upload(PSEUDONYM_HEADER + "A;;;\n"), target_type=ImportJob.TargetType.CLIENT
        )

    def test_valid_mapping_sets_status_mapped(self, facility, admin_user, media_root):
        job = self._job(facility, admin_user)
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym", "Notizen": ""})
        job.refresh_from_db()
        assert job.status == ImportJob.Status.MAPPED
        assert job.mapping_json["columns"] == {"Pseudonym": "pseudonym"}
        assert job.mapping_json["delimiter"] == ";"

    def test_missing_required_target_is_rejected(self, facility, admin_user, media_root):
        job = self._job(facility, admin_user)
        with pytest.raises(ValidationError):
            import_engine.apply_mapping(job, {"Notizen": "notes"})

    def test_duplicate_target_is_rejected(self, facility, admin_user, media_root):
        job = self._job(facility, admin_user)
        with pytest.raises(ValidationError):
            import_engine.apply_mapping(job, {"Pseudonym": "pseudonym", "Notizen": "pseudonym"})

    def test_unknown_target_is_rejected(self, facility, admin_user, media_root):
        job = self._job(facility, admin_user)
        with pytest.raises(ValidationError):
            import_engine.apply_mapping(job, {"Pseudonym": "pseudonym", "Notizen": "facility"})

    def test_unknown_column_is_rejected(self, facility, admin_user, media_root):
        job = self._job(facility, admin_user)
        with pytest.raises(ValidationError):
            import_engine.apply_mapping(job, {"Pseudonym": "pseudonym", "Gibtsnicht": "notes"})

    def test_unknown_column_wins_over_unknown_target_and_missing_required(self, facility, admin_user, media_root):
        job = self._job(facility, admin_user)

        with pytest.raises(ValidationError) as exc_info:
            import_engine.apply_mapping(job, {"Gibtsnicht": "facility"})

        assert exc_info.value.messages == ["Die Spalte „Gibtsnicht“ gibt es in der Datei nicht."]

    def test_final_job_cannot_be_remapped(self, facility, admin_user, media_root):
        job = self._job(facility, admin_user)
        job.status = ImportJob.Status.COMMITTED
        job.save(update_fields=["status"])
        with pytest.raises(ValidationError):
            import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})


@pytest.mark.django_db
class TestRunImportCharacterization:
    def test_final_guard_runs_before_reading_the_source(self, client_job, admin_user, monkeypatch):
        client_job.status = ImportJob.Status.COMMITTED

        def _unexpected_read(_job):
            pytest.fail("run_import darf für einen finalen Lauf die Quelldatei nicht lesen")

        monkeypatch.setattr(import_engine, "read_rows", _unexpected_read)

        with pytest.raises(ValidationError) as exc_info:
            import_engine.run_import(client_job, admin_user, commit=False)

        assert exc_info.value.messages == ["Der Importlauf ist bereits abgeschlossen."]

    def test_empty_columns_map_guard_runs_before_row_processing(self, client_job, admin_user, monkeypatch):
        client_job.mapping_json = {"columns": {}}

        def _unexpected_row_values(*args, **kwargs):
            pytest.fail("run_import darf ohne Spaltenzuordnung keine Zeile verarbeiten")

        monkeypatch.setattr(import_engine, "_row_values", _unexpected_row_values)

        with pytest.raises(ValidationError) as exc_info:
            import_engine.run_import(client_job, admin_user, commit=False)

        assert exc_info.value.messages == ["Für diesen Importlauf ist noch keine Spaltenzuordnung hinterlegt."]

    @pytest.mark.parametrize(
        ("exception_type", "source_message", "expected_code", "expected_message"),
        [
            pytest.param(
                ValidationError,
                ["Die fachliche Prüfung ist fehlgeschlagen.", "Bitte Eingabe prüfen."],
                "validation",
                "Die fachliche Prüfung ist fehlgeschlagen. Bitte Eingabe prüfen.",
                id="validation-safe-message",
            ),
            pytest.param(
                PermissionDenied,
                "Die Rolle ist für diesen Vorgang gesperrt.",
                "permission",
                "Die Rolle ist für diesen Vorgang gesperrt.",
                id="permission-safe-message",
            ),
            pytest.param(
                ValueError,
                "Der Wert kann nicht konvertiert werden.",
                "invalid_value",
                "Der Wert kann nicht konvertiert werden.",
                id="value-safe-message",
            ),
            pytest.param(
                ValidationError,
                "Die fachliche Prüfung lehnt CHAR-PII ab.",
                "validation",
                "Die Zeile konnte nicht übernommen werden.",
                id="validation-pii-message",
            ),
            pytest.param(
                PermissionDenied,
                "Für CHAR-PII fehlt die Berechtigung.",
                "permission",
                "Die Zeile konnte nicht übernommen werden.",
                id="permission-pii-message",
            ),
            pytest.param(
                ValueError,
                "CHAR-PII kann nicht konvertiert werden.",
                "invalid_value",
                "Die Zeile konnte nicht übernommen werden.",
                id="value-pii-message",
            ),
        ],
    )
    def test_service_exception_is_classified_and_its_message_is_filtered(
        self,
        facility,
        admin_user,
        media_root,
        monkeypatch,
        exception_type,
        source_message,
        expected_code,
        expected_message,
    ):
        job = _single_row_client_job(facility, admin_user)

        def _raise_service_exception(*args, **kwargs):
            raise exception_type(source_message)

        monkeypatch.setattr(import_engine, "create_client", _raise_service_exception)

        result = import_engine.run_import(job, admin_user, commit=False)

        assert result == {
            "summary": {"rows": 1, "created": 0, "errors": 1, "committed": False},
            "errors": [
                {
                    "row": 2,
                    "column": "",
                    "code": expected_code,
                    "message": expected_message,
                }
            ],
        }
        job.refresh_from_db()
        assert job.status == ImportJob.Status.VALIDATED
        assert job.result_json == result

    def test_integrity_error_is_classified_before_database_error(self, facility, admin_user, media_root, monkeypatch):
        assert issubclass(IntegrityError, DatabaseError)
        job = _single_row_client_job(facility, admin_user)

        def _raise_integrity_error(*args, **kwargs):
            raise IntegrityError("duplicate key DETAIL: CHAR-PII")

        monkeypatch.setattr(import_engine, "create_client", _raise_integrity_error)

        result = import_engine.run_import(job, admin_user, commit=False)

        assert result["errors"] == [
            {
                "row": 2,
                "column": "",
                "code": "duplicate",
                "message": "Die Zeile verletzt eine Eindeutigkeitsregel (z. B. bereits vergebenes Pseudonym).",
            }
        ]
        assert "CHAR-PII" not in str(result)


@pytest.mark.django_db
class TestDryRunLeavesNothingBehind:
    def test_dry_run_creates_no_rows_at_all(self, client_job, admin_user):
        before = (
            Client.objects.count(),
            AuditLog.objects.count(),
            Activity.objects.count(),
        )
        result = import_engine.run_import(client_job, admin_user, commit=False)
        assert result["summary"]["created"] == 3
        assert (Client.objects.count(), AuditLog.objects.count(), Activity.objects.count()) == before

    def test_dry_run_sets_validated_and_persists_the_result(self, client_job, admin_user):
        import_engine.run_import(client_job, admin_user, commit=False)
        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.VALIDATED
        assert client_job.result_json["summary"]["rows"] == 3
        assert client_job.result_json["errors"] == []


@pytest.mark.django_db
class TestSavepointPerRow:
    def test_dry_run_reports_every_broken_row_not_only_the_first(self, facility, admin_user, media_root):
        """Ohne Savepoint je Zeile vergiftet der erste IntegrityError die
        aeussere Transaktion — der Probelauf lieferte dann nur einen Fehler."""
        Client.objects.create(facility=facility, pseudonym="DUP-A")
        Client.objects.create(facility=facility, pseudonym="DUP-B")
        content = PSEUDONYM_HEADER + "DUP-A;;;\nNEU-01;;;\nDUP-B;;;\n"
        job = import_engine.store_import_upload(
            facility, admin_user, _upload(content), target_type=ImportJob.TargetType.CLIENT
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})

        result = import_engine.run_import(job, admin_user, commit=False)

        assert [error["row"] for error in result["errors"]] == [2, 4]
        assert {error["code"] for error in result["errors"]} == {"duplicate"}
        assert result["summary"]["created"] == 1

    def test_commit_with_errors_stays_all_or_nothing(self, facility, admin_user, media_root):
        Client.objects.create(facility=facility, pseudonym="DUP-A")
        content = PSEUDONYM_HEADER + "DUP-A;;;\nNEU-01;;;\n"
        job = import_engine.store_import_upload(
            facility, admin_user, _upload(content), target_type=ImportJob.TargetType.CLIENT
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})

        import_engine.run_import(job, admin_user, commit=True)

        job.refresh_from_db()
        assert job.status == ImportJob.Status.VALIDATED
        assert not Client.objects.filter(pseudonym="NEU-01").exists()


@pytest.mark.django_db
class TestCommitRun:
    def test_commit_creates_clients_with_service_side_effects(self, client_job, admin_user, facility):
        result = import_engine.run_import(client_job, admin_user, commit=True)

        assert result["summary"]["created"] == 3
        created = Client.objects.for_facility(facility).filter(pseudonym__startswith="IMP-")
        assert created.count() == 3
        first = created.get(pseudonym="IMP-01")
        assert first.contact_stage == Client.ContactStage.IDENTIFIED
        assert first.age_cluster == Client.AgeCluster.U18
        assert first.notes == "Erste"
        assert AuditLog.objects.filter(action=AuditLog.Action.CLIENT_CREATE, target_id=str(first.pk)).exists()
        activity = Activity.objects.get(verb=Activity.Verb.CREATED, target_id=first.pk)
        assert activity.context["action"] == Activity.Action.CLIENT_CREATED

    def test_commit_sets_status_committed(self, client_job, admin_user):
        import_engine.run_import(client_job, admin_user, commit=True)
        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.COMMITTED

    def test_encrypted_dynamic_field_is_stored_as_marker(
        self, facility, admin_user, doc_type_client_profile, media_root
    ):
        job = _profile_job(
            facility,
            admin_user,
            doc_type_client_profile,
            "Pseudonym;Wohnsituation;Diagnose\nIMP-ENC;WG;Sehr-vertraulich\n",
            {"Pseudonym": "pseudonym", "Wohnsituation": "data_json.wohnsituation", "Diagnose": "data_json.diagnose"},
        )
        import_engine.run_import(job, admin_user, commit=True)

        client = Client.objects.get(pseudonym="IMP-ENC")
        assert client.data_json["wohnsituation"] == "WG"
        assert client.data_json["diagnose"].get("__encrypted__") is True

    def test_event_import_uses_create_event(
        self, facility, admin_user, doc_type_contact, client_identified, media_root
    ):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Datum;Person;Dauer\n01.03.2026 09:30;Test-ID-01;45\n"),
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_contact,
        )
        import_engine.apply_mapping(
            job,
            {"Datum": "occurred_at", "Person": "client_pseudonym", "Dauer": "data_json.dauer"},
        )
        import_engine.run_import(job, admin_user, commit=True)

        event = Event.objects.get(client=client_identified, document_type=doc_type_contact)
        assert event.data_json["dauer"] == 45
        assert event.occurred_at.year == 2026
        assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE, target_id=str(event.pk)).exists()

    def test_unknown_pseudonym_is_a_row_error(self, facility, admin_user, doc_type_contact, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Datum;Person\n01.03.2026;GIBTSNICHT\n"),
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_contact,
        )
        import_engine.apply_mapping(job, {"Datum": "occurred_at", "Person": "client_pseudonym"})
        result = import_engine.run_import(job, admin_user, commit=False)
        assert result["errors"][0]["code"] == "unknown_client"
        assert result["errors"][0]["column"] == "Person"

    def test_anonymized_client_is_a_row_error(self, facility, admin_user, doc_type_contact, media_root):
        """Refs #2014: kein frischer Klartext an einen redigierten Menschen.

        Die K-Anon-Bridge anonymisiert gezielt OHNE Papierkorb
        (``is_active=False, is_deleted=False``). ``active()`` filtert nur
        ``is_deleted`` — ohne die zweite Bedingung haengt ein Import-Commit
        neue Ereignisse an eine bereits redigierte Person, deterministisch und
        ohne Race. Der Zeilenfehler ist derselbe wie bei einem unbekannten
        Pseudonym: fuer einen Schreibzugriff ist eine widerrufene Person nicht
        adressierbar.
        """
        redacted = Client.objects.create(
            facility=facility,
            pseudonym="anon-abc123def456",
            is_active=False,
            is_deleted=False,
        )
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(f"Datum;Person\n01.03.2026;{redacted.pseudonym}\n"),
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_contact,
        )
        import_engine.apply_mapping(job, {"Datum": "occurred_at", "Person": "client_pseudonym"})
        result = import_engine.run_import(job, admin_user, commit=True)

        assert result["errors"][0]["code"] == "unknown_client"
        assert not Event.objects.filter(client=redacted).exists()

    def test_trashed_client_is_a_row_error(self, facility, admin_user, doc_type_contact, staff_user, media_root):
        """Refs #2014: Gegenprobe — der Papierkorb blockte schon vorher."""
        trashed = Client.objects.create(facility=facility, pseudonym="IMP-TRASH")
        trashed.soft_delete(user=staff_user)
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Datum;Person\n01.03.2026;IMP-TRASH\n"),
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_contact,
        )
        import_engine.apply_mapping(job, {"Datum": "occurred_at", "Person": "client_pseudonym"})
        result = import_engine.run_import(job, admin_user, commit=True)

        assert result["errors"][0]["code"] == "unknown_client"
        assert not Event.objects.filter(client=trashed).exists()


@pytest.mark.django_db
class TestValueCoercion:
    def _run(self, facility, admin_user, doc_type, header, row, mapping):
        job = _profile_job(facility, admin_user, doc_type, f"{header}\n{row}\n", mapping)
        return import_engine.run_import(job, admin_user, commit=True)

    @pytest.fixture
    def numeric_type(self, facility, doc_type_client_profile):
        template = FieldTemplate.objects.create(
            facility=facility, name="Betrag", field_type=FieldTemplate.FieldType.NUMBER
        )
        DocumentTypeField.objects.create(document_type=doc_type_client_profile, field_template=template, sort_order=9)
        return doc_type_client_profile

    def test_german_decimal_comma_is_accepted(self, facility, admin_user, numeric_type, media_root):
        self._run(
            facility,
            admin_user,
            numeric_type,
            "Pseudonym;Betrag",
            "NUM-01;12,50",
            {"Pseudonym": "pseudonym", "Betrag": "data_json.betrag"},
        )
        assert Client.objects.get(pseudonym="NUM-01").data_json["betrag"] == 12.5

    def test_number_range_is_checked_without_losing_decimal_csv_contract(
        self, facility, admin_user, numeric_type, media_root
    ):
        template = FieldTemplate.objects.get(facility=facility, slug="betrag")
        template.min_value = 0
        template.max_value = 10
        template.save(update_fields=["min_value", "max_value"])

        result = self._run(
            facility,
            admin_user,
            numeric_type,
            "Pseudonym;Betrag",
            "NUM-RANGE;12,50",
            {"Pseudonym": "pseudonym", "Betrag": "data_json.betrag"},
        )

        assert result["errors"][0]["code"] == "validation"
        assert not Client.objects.filter(pseudonym="NUM-RANGE").exists()

    def test_german_thousands_separator_without_decimals_is_accepted(
        self, facility, admin_user, numeric_type, media_root
    ):
        """``1.500`` ist in einem deutschen Export Tausendertrennung, nicht 1,5.

        Vor dem Fix griff die Umrechnung nur bei vorhandenem Komma; ``float()``
        las den Punkt als Dezimalpunkt und der Wert landete um Faktor 1000
        verfaelscht in ``data_json`` — ohne Fehler, ohne Hinweis.
        """
        self._run(
            facility,
            admin_user,
            numeric_type,
            "Pseudonym;Betrag",
            "NUM-TSD;1.500",
            {"Pseudonym": "pseudonym", "Betrag": "data_json.betrag"},
        )
        assert Client.objects.get(pseudonym="NUM-TSD").data_json["betrag"] == 1500

    def test_german_thousands_separator_with_decimals_is_accepted(self, facility, admin_user, numeric_type, media_root):
        self._run(
            facility,
            admin_user,
            numeric_type,
            "Pseudonym;Betrag",
            "NUM-TSD2;1.500,25",
            {"Pseudonym": "pseudonym", "Betrag": "data_json.betrag"},
        )
        assert Client.objects.get(pseudonym="NUM-TSD2").data_json["betrag"] == 1500.25

    def test_a_point_that_cannot_be_a_thousands_group_stays_a_decimal_point(
        self, facility, admin_user, numeric_type, media_root
    ):
        """Festgelegte Konvention (Refs #1060): nur die exakte Gruppierungsform
        ``\\d{1,3}(\\.\\d{3})+`` gilt als Tausendertrennung. ``1.5`` kann in
        keinem deutschen Export eine Gruppierung sein und bleibt daher 1,5 —
        so bricht der Fix keine Datei aus einem englischsprachigen Werkzeug."""
        self._run(
            facility,
            admin_user,
            numeric_type,
            "Pseudonym;Betrag",
            "NUM-DEC;1.5",
            {"Pseudonym": "pseudonym", "Betrag": "data_json.betrag"},
        )
        assert Client.objects.get(pseudonym="NUM-DEC").data_json["betrag"] == 1.5

    def test_a_leading_zero_group_stays_a_decimal_fraction(self, facility, admin_user, numeric_type, media_root):
        """S5-Restluecke N4 (Refs #1060): ``0.500`` ist nie eine deutsche
        Gruppierung (die erste Gruppe beginnt nie mit 0) — der Wert muss 0,5
        bleiben statt still zu 500 zu werden (Faktor-1000-Fehler in
        Gegenrichtung)."""
        self._run(
            facility,
            admin_user,
            numeric_type,
            "Pseudonym;Betrag",
            "NUM-ZERO;0.500",
            {"Pseudonym": "pseudonym", "Betrag": "data_json.betrag"},
        )
        assert Client.objects.get(pseudonym="NUM-ZERO").data_json["betrag"] == 0.5

    @pytest.mark.parametrize("raw", ["inf", "-inf", "Infinity", "nan", "NaN"])
    def test_non_finite_numbers_are_a_row_error(self, facility, admin_user, numeric_type, media_root, raw):
        """``float()`` akzeptiert ``inf``/``nan`` klaglos; in ``jsonb`` sind sie
        nicht darstellbar und wuerden den ganzen Lauf mit einem DB-Fehler
        killen. Sie gehoeren in den Zeilen-Fehlerkatalog."""
        result = self._run(
            facility,
            admin_user,
            numeric_type,
            "Pseudonym;Betrag",
            f"NUM-INF;{raw}",
            {"Pseudonym": "pseudonym", "Betrag": "data_json.betrag"},
        )
        assert result["errors"][0]["code"] == "invalid_number"
        assert result["errors"][0]["column"] == "Betrag"

    def test_invalid_number_is_a_row_error(self, facility, admin_user, numeric_type, media_root):
        result = self._run(
            facility,
            admin_user,
            numeric_type,
            "Pseudonym;Betrag",
            "NUM-02;keine-zahl",
            {"Pseudonym": "pseudonym", "Betrag": "data_json.betrag"},
        )
        assert result["errors"][0]["code"] == "invalid_number"
        assert result["errors"][0]["column"] == "Betrag"

    def test_german_date_format_is_accepted(self, facility, admin_user, doc_type_client_profile, media_root):
        template = FieldTemplate.objects.create(
            facility=facility, name="Aufnahme", field_type=FieldTemplate.FieldType.DATE
        )
        DocumentTypeField.objects.create(document_type=doc_type_client_profile, field_template=template, sort_order=8)
        self._run(
            facility,
            admin_user,
            doc_type_client_profile,
            "Pseudonym;Aufnahme",
            "DAT-01;24.12.2026",
            {"Pseudonym": "pseudonym", "Aufnahme": "data_json.aufnahme"},
        )
        assert Client.objects.get(pseudonym="DAT-01").data_json["aufnahme"] == "2026-12-24"

    def test_yes_no_becomes_boolean(self, facility, admin_user, doc_type_client_profile, media_root):
        template = FieldTemplate.objects.create(
            facility=facility, name="Einwilligung", field_type=FieldTemplate.FieldType.BOOLEAN
        )
        DocumentTypeField.objects.create(document_type=doc_type_client_profile, field_template=template, sort_order=7)
        self._run(
            facility,
            admin_user,
            doc_type_client_profile,
            "Pseudonym;Einwilligung",
            "BOOL-01;Ja",
            {"Pseudonym": "pseudonym", "Einwilligung": "data_json.einwilligung"},
        )
        assert Client.objects.get(pseudonym="BOOL-01").data_json["einwilligung"] is True

    def test_select_value_is_validated_against_options(self, facility, admin_user, doc_type_client_profile, media_root):
        template = FieldTemplate.objects.create(
            facility=facility,
            name="Zugang",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "strasse", "label": "Straße", "is_active": True}],
        )
        DocumentTypeField.objects.create(document_type=doc_type_client_profile, field_template=template, sort_order=6)
        mapping = {"Pseudonym": "pseudonym", "Zugang": "data_json.zugang"}

        self._run(facility, admin_user, doc_type_client_profile, "Pseudonym;Zugang", "SEL-01;Straße", mapping)
        assert Client.objects.get(pseudonym="SEL-01").data_json["zugang"] == "strasse"

        result = self._run(facility, admin_user, doc_type_client_profile, "Pseudonym;Zugang", "SEL-02;Wald", mapping)
        assert result["errors"][0]["code"] == "invalid_option"

    @pytest.mark.parametrize(
        ("cell", "expected"),
        [
            # Aktuelle Labels (Refs #1805).
            ("Nur Pseudonym", Client.ContactStage.IDENTIFIED),
            ("Mit Zusatzangaben", Client.ContactStage.QUALIFIED),
            # Abgeloeste Labels: jede vor #1805 exportierte CSV traegt sie,
            # weil services/system/export.py das Anzeige-Label schreibt.
            ("Identifiziert", Client.ContactStage.IDENTIFIED),
            ("qualifiziert", Client.ContactStage.QUALIFIED),
            # Rohwerte bleiben unabhaengig vom Label gueltig.
            ("identified", Client.ContactStage.IDENTIFIED),
        ],
    )
    def test_contact_stage_accepts_current_and_legacy_labels(self, facility, admin_user, media_root, cell, expected):
        """CSV ist ein Datenvertrag mit Bestandsdateien — die Label-Umbenennung
        aus #1805 darf keine bereits exportierte Datei unlesbar machen."""
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(f"Pseudonym;Kontaktstufe\nSTG-ALIAS;{cell}\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym", "Kontaktstufe": "contact_stage"})
        result = import_engine.run_import(job, admin_user, commit=True)

        assert result["errors"] == []
        assert Client.objects.for_facility(facility).get(pseudonym="STG-ALIAS").contact_stage == expected

    def test_unknown_contact_stage_is_a_row_error(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Pseudonym;Kontaktstufe\nSTG-01;voellig-neu\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym", "Kontaktstufe": "contact_stage"})
        result = import_engine.run_import(job, admin_user, commit=False)
        assert result["errors"][0]["code"] == "invalid_option"

    def test_missing_required_cell_is_a_row_error(self, facility, admin_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER + ";identified;;Ohne Pseudonym\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym", "Notizen": "notes"})
        result = import_engine.run_import(job, admin_user, commit=False)
        assert result["errors"][0]["code"] == "required_missing"


@pytest.mark.django_db
class TestErrorReportIsPiiFree:
    """``result_json`` ist als ``non_pii`` klassifiziert — das gilt nur, solange
    keine Zellwerte in die Meldungen sickern (Negativ-Test, Refs #1060)."""

    def test_no_cell_value_appears_in_the_error_report(self, facility, admin_user, media_root):
        Client.objects.create(facility=facility, pseudonym="RESIDUEPROBE-DUP")
        content = (
            "Pseudonym;Kontaktstufe;Notizen\n"
            "RESIDUEPROBE-DUP;identified;RESIDUEPROBE-NOTIZ\n"
            "RESIDUEPROBE-NEU;RESIDUEPROBE-STUFE;RESIDUEPROBE-NOTIZ2\n"
        )
        job = import_engine.store_import_upload(
            facility, admin_user, _upload(content), target_type=ImportJob.TargetType.CLIENT
        )
        import_engine.apply_mapping(
            job, {"Pseudonym": "pseudonym", "Kontaktstufe": "contact_stage", "Notizen": "notes"}
        )

        import_engine.run_import(job, admin_user, commit=False)
        job.refresh_from_db()

        assert len(job.result_json["errors"]) == 2
        assert "RESIDUEPROBE-" not in str(job.result_json)

    def test_sanitizer_replaces_a_message_that_carries_a_cell_value(self):
        row = ["RESIDUEPROBE-WERT", ""]
        leaky = "Ungueltig: RESIDUEPROBE-WERT"
        assert import_engine._pii_safe_message(leaky, row) != leaky
        assert import_engine._pii_safe_message("Ungueltiger Wert.", row) == "Ungueltiger Wert."


@pytest.mark.django_db
class TestDatabaseErrorsBecomeRowErrors:
    """Kein ``DatabaseError`` darf aus der Zeilen-Schleife entkommen (Refs #1060).

    Der Schreibpfad ruft ``create_client``/``create_event`` ohne
    ``full_clean()``; ein zu langer Zellwert im ``varchar``-Ziel erzeugt daher
    einen ``DataError`` — ein Geschwister von ``IntegrityError`` unter
    ``DatabaseError``. Ohne Auffangnetz waere das ein 500er im Wizard und der
    ganze Lauf braeche ab, statt die eine Zeile zu melden.
    """

    def _client_job(self, facility, admin_user, pseudonym):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER + f"{pseudonym};;;\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
        return job

    def test_overlong_pseudonym_is_a_row_error(self, facility, admin_user, media_root):
        """Freitext-Spalte versehentlich auf „Pseudonym" gemappt: 150 Zeichen
        gegen ``varchar(100)``."""
        too_long = "X" * 150
        job = self._client_job(facility, admin_user, too_long)

        result = import_engine.run_import(job, admin_user, commit=False)

        assert result["summary"]["errors"] == 1
        assert result["summary"]["created"] == 0
        assert result["errors"][0]["code"] == "invalid_value"
        assert too_long not in str(result)

    def test_a_later_row_still_runs_after_a_database_error(self, facility, admin_user, media_root):
        """Der Zeilen-Savepoint faengt den ``DataError`` ab — die Folgezeile
        laeuft normal weiter (sonst waere der Lauf nach dem ersten Treffer tot)."""
        content = PSEUDONYM_HEADER + ("Y" * 150) + ";;;\nDBE-OK;;;\n"
        job = import_engine.store_import_upload(
            facility, admin_user, _upload(content), target_type=ImportJob.TargetType.CLIENT
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})

        result = import_engine.run_import(job, admin_user, commit=True)

        assert result["summary"]["errors"] == 1
        assert result["summary"]["created"] == 1
        assert result["summary"]["committed"] is False
        assert not Client.objects.filter(pseudonym="DBE-OK").exists()

    def test_database_error_detail_never_reaches_the_report(self, facility, admin_user, media_root, monkeypatch):
        """``str(exc)`` traegt bei Postgres die DETAIL-Zeile inklusive Zellwert —
        der Bericht nimmt ausschliesslich den festen Katalogtext."""
        from django.db import DataError

        def _boom(*args, **kwargs):
            raise DataError("value too long for type character varying(100)\nDETAIL: DBDETAIL-LECK (DBE-PII)")

        monkeypatch.setattr(import_engine, "create_client", _boom)
        job = self._client_job(facility, admin_user, "DBE-PII")

        result = import_engine.run_import(job, admin_user, commit=False)

        assert result["errors"][0]["code"] == "invalid_value"
        assert "DBDETAIL-LECK" not in str(result)
        assert "DBE-PII" not in str(result)


@pytest.mark.django_db
class TestBrokenTransactionIsNotMaskedAsRowError:
    """Eine kaputte Transaktion ist Infrastruktur, kein Zeilenfehler (Refs #1793).

    Scheitert der Savepoint-Rollback einer Zeile (Verbindungsabriss), setzt
    Djangos ``Atomic.__exit__`` ``connection.needs_rollback = True``. Ab dann
    lehnt Django JEDE weitere Query mit ``TransactionManagementError`` ab — und
    der ist als ``ProgrammingError`` ein Enkel von ``DatabaseError``. Ohne
    Sonderbehandlung landete deshalb jede Folgezeile als ``invalid_value`` im
    Bericht, und der Lauf endete mit einem VALIDATED-Verdikt, das nichts mehr
    geprueft hat.
    """

    def _two_row_job(self, facility, admin_user):
        content = PSEUDONYM_HEADER + "TXM-BROKEN;;;\nTXM-FOLGE;;;\n"
        job = import_engine.store_import_upload(
            facility, admin_user, _upload(content), target_type=ImportJob.TargetType.CLIENT
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
        return job

    def test_transaction_management_error_aborts_the_run(self, facility, admin_user, media_root, monkeypatch):
        from django.db import DatabaseError, IntegrityError, connection
        from django.db.transaction import TransactionManagementError

        real_create_client = import_engine.create_client
        real_savepoint_rollback = connection.savepoint_rollback

        def _first_row_conflicts(*args, **kwargs):
            """Zeile 1 scheitert an einer Eindeutigkeitsregel (ohne DB-Kontakt)."""
            if kwargs.get("pseudonym") == "TXM-BROKEN":
                raise IntegrityError("duplicate key value violates unique constraint")
            return real_create_client(*args, **kwargs)

        def _savepoint_rollback_fails_once(sid):
            # Setzt sich SOFORT selbst zurueck: bliebe der Patch stehen, waere
            # auch das pytest-django-Teardown vergiftet.
            connection.savepoint_rollback = real_savepoint_rollback
            raise DatabaseError("server closed the connection unexpectedly")

        job = self._two_row_job(facility, admin_user)
        monkeypatch.setattr(import_engine, "create_client", _first_row_conflicts)
        monkeypatch.setattr(connection, "savepoint_rollback", _savepoint_rollback_fails_once)

        with pytest.raises(TransactionManagementError):
            import_engine.run_import(job, admin_user, commit=False)

        job.refresh_from_db()
        # Kein verfaelschter Bericht, kein VALIDATED-Verdikt: der Lauf bleibt
        # da stehen, wo er war.
        assert job.status == ImportJob.Status.MAPPED
        assert not job.result_json
        assert not Client.objects.filter(pseudonym__startswith="TXM-").exists()


@pytest.mark.django_db
class TestCommitImport:
    def test_commit_deletes_the_upload_and_audits(self, client_job, admin_user):
        import_engine.run_import(client_job, admin_user, commit=False)
        path = import_engine.import_file_path(client_job)
        assert path.exists()

        import_engine.commit_import(client_job, admin_user)

        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.COMMITTED
        assert not path.exists()
        assert client_job.storage_filename == ""
        assert client_job.original_filename_encrypted == {}
        entry = AuditLog.objects.filter(action=AuditLog.Action.IMPORT_COMMITTED).latest("timestamp")
        assert entry.detail["created"] == 3
        assert entry.detail["rows"] == 3

    def test_commit_requires_a_clean_dry_run(self, facility, admin_user, media_root):
        Client.objects.create(facility=facility, pseudonym="DUP-A")
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER + "DUP-A;;;\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
        import_engine.run_import(job, admin_user, commit=False)
        with pytest.raises(ValidationError):
            import_engine.commit_import(job, admin_user)

    def test_commit_requires_a_validated_job(self, client_job, admin_user):
        with pytest.raises(ValidationError):
            import_engine.commit_import(client_job, admin_user)

    def test_a_conflict_after_the_dry_run_rolls_back_but_keeps_the_report(self, client_job, admin_user, facility):
        """Sauberer Probelauf, danach vergibt jemand das Pseudonym: die
        Uebernahme faellt all-or-nothing zurueck (Refs #1060).

        Der frische Fehlerbericht muss die Rueckabwicklung ueberleben — die
        Detailseite, auf die der Fehler-Redirect zeigt, liest ihn aus der DB.
        Deshalb liegt der ValidationError bewusst ausserhalb der Lock-Klammer.
        """
        import_engine.run_import(client_job, admin_user, commit=False)
        Client.objects.create(facility=facility, pseudonym="IMP-02")

        with pytest.raises(ValidationError, match="nichts gespeichert"):
            import_engine.commit_import(client_job, admin_user)

        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.VALIDATED
        assert client_job.result_json["summary"]["errors"] == 1
        assert client_job.result_json["errors"][0]["code"] == "duplicate"
        assert not Client.objects.filter(pseudonym="IMP-01").exists()
        assert client_job.storage_filename != ""


@pytest.mark.django_db
class TestAbortImport:
    def test_abort_deletes_the_file_and_audits(self, client_job, admin_user):
        path = import_engine.import_file_path(client_job)
        import_engine.abort_import(client_job, admin_user)

        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.ABORTED
        assert not path.exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.IMPORT_ABORTED).exists()

    def test_final_job_cannot_be_aborted_twice(self, client_job, admin_user):
        import_engine.abort_import(client_job, admin_user)
        with pytest.raises(ValidationError):
            import_engine.abort_import(client_job, admin_user)


@pytest.mark.django_db
class TestConcurrentTransitionsAreSerialized:
    """Der Statusuebergang wird UNTER Zeilen-Lock entschieden (Refs #1060).

    Der Wizard laeuft ueber mehrere Requests; jeder POST haelt eine eigene, in
    der View geladene ``ImportJob``-Instanz. Entschiede der Uebergang auf dem
    In-Memory-Status, importierte ein Doppelklick die Datei zweimal — bzw. ein
    ``abort_import`` schriebe einen bereits uebernommenen Lauf auf ABORTED um.
    Der zweite Aufruf steht hier stellvertretend fuer den zweiten Request: er
    haelt einen Snapshot von VOR der ersten Uebernahme.
    """

    #: Zuordnung der ``client_job``-Fixture — der Mapping-POST schickt sie bei
    #: jedem Aufruf erneut mit.
    MAPPING = {
        "Pseudonym": "pseudonym",
        "Kontaktstufe": "contact_stage",
        "Altersgruppe": "age_cluster",
        "Notizen": "notes",
    }

    def _validated_job_and_stale_copy(self, client_job, admin_user):
        import_engine.run_import(client_job, admin_user, commit=False)
        return ImportJob.objects.get(pk=client_job.pk)

    def test_second_commit_of_the_same_job_is_rejected(self, client_job, admin_user):
        stale = self._validated_job_and_stale_copy(client_job, admin_user)
        import_engine.commit_import(client_job, admin_user)

        with pytest.raises(ValidationError, match="fehlerfrei geprüft"):
            import_engine.commit_import(stale, admin_user)

        assert Client.objects.filter(pseudonym__startswith="IMP-").count() == 3

    def test_abort_after_a_commit_does_not_overwrite_the_final_status(self, client_job, admin_user):
        stale = self._validated_job_and_stale_copy(client_job, admin_user)
        import_engine.commit_import(client_job, admin_user)

        with pytest.raises(ValidationError, match="bereits abgeschlossen"):
            import_engine.abort_import(stale, admin_user)

        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.COMMITTED
        assert not AuditLog.objects.filter(action=AuditLog.Action.IMPORT_ABORTED).exists()

    def test_commit_after_an_abort_is_rejected(self, client_job, admin_user):
        stale = self._validated_job_and_stale_copy(client_job, admin_user)
        import_engine.abort_import(client_job, admin_user)

        with pytest.raises(ValidationError, match="fehlerfrei geprüft"):
            import_engine.commit_import(stale, admin_user)

        assert not Client.objects.filter(pseudonym__startswith="IMP-").exists()

    def test_probe_after_a_commit_is_rejected(self, client_job, admin_user):
        """Der Mapping-/Probelauf-Pfad steht unter DEMSELBEN Lock (Refs #1793).

        Ohne ihn entschiede der Mapping-POST auf seinem In-Memory-Snapshot und
        setzte den Lauf per ``job.save()`` auf MAPPED/VALIDATED zurueck.
        """
        stale = self._validated_job_and_stale_copy(client_job, admin_user)
        import_engine.commit_import(client_job, admin_user)

        with pytest.raises(ValidationError, match="bereits abgeschlossen"):
            import_engine.probe_import(stale, admin_user, self.MAPPING)

        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.COMMITTED

    def test_probe_does_not_reopen_a_job_committed_in_the_meantime(self, client_job, admin_user):
        """Das echte Rennfenster liegt INNERHALB von ``commit_import``.

        Dort steht der Status bereits auf COMMITTED, waehrend die Quelldatei
        noch auf der Disk liegt (sie wird erst nach der Lock-Klammer geloescht).
        Genau hier hat der ungesicherte Mapping-POST alles, was er zum Weiter-
        laufen braucht — und importierte die Datei ein zweites Mal.
        """
        stale = self._validated_job_and_stale_copy(client_job, admin_user)
        import_engine.run_import(client_job, admin_user, commit=True)
        assert client_job.status == ImportJob.Status.COMMITTED
        assert import_engine.import_file_path(client_job).exists()

        with pytest.raises(ValidationError, match="bereits abgeschlossen"):
            import_engine.probe_import(stale, admin_user, self.MAPPING)

        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.COMMITTED
        assert Client.objects.filter(pseudonym__startswith="IMP-").count() == 3


@pytest.mark.django_db
class TestCleanupStaleImportJobs:
    def _age(self, job, days):
        ImportJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(days=days))
        job.refresh_from_db()

    def test_old_unfinished_job_is_aborted_and_its_file_removed(self, client_job, facility):
        path = import_engine.import_file_path(client_job)
        self._age(client_job, import_engine.STALE_DAYS + 1)

        assert import_engine.cleanup_stale_import_jobs(facility) == 1

        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.ABORTED
        assert not path.exists()
        entry = AuditLog.objects.filter(action=AuditLog.Action.IMPORT_ABORTED).latest("timestamp")
        assert entry.detail["reason"] == "stale"

    def test_fresh_job_is_untouched(self, client_job, facility):
        assert import_engine.cleanup_stale_import_jobs(facility) == 0
        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.MAPPED

    def test_finished_jobs_are_untouched(self, client_job, admin_user, facility):
        import_engine.abort_import(client_job, admin_user)
        self._age(client_job, import_engine.STALE_DAYS + 1)
        assert import_engine.cleanup_stale_import_jobs(facility) == 0

    def test_cleanup_is_scoped_to_the_given_facility(self, client_job, second_facility):
        self._age(client_job, import_engine.STALE_DAYS + 1)
        assert import_engine.cleanup_stale_import_jobs(second_facility) == 0
        client_job.refresh_from_db()
        assert client_job.status == ImportJob.Status.MAPPED


@pytest.mark.django_db
class TestMissingSourceFile:
    def test_reading_a_deleted_upload_fails_loudly(self, client_job, admin_user):
        import_engine.import_file_path(client_job).unlink()
        with pytest.raises(ValidationError):
            import_engine.read_rows(client_job)


class TestPureHelpers:
    def test_decode_prefers_utf8_and_falls_back_to_cp1252(self):
        assert import_engine._decode("Straße".encode(), "") == ("Straße", "utf-8-sig")
        assert import_engine._decode("Straße".encode("cp1252"), "") == ("Straße", "cp1252")

    def test_sniffer_falls_back_to_semicolon(self):
        assert import_engine._sniff_delimiter("nur-eine-spalte\nwert\n") == ";"

    @pytest.mark.parametrize("text", ["", "\n", " ; ; \n"])
    def test_parse_csv_rejects_a_file_without_header(self, text):
        with pytest.raises(ValidationError) as exc_info:
            import_engine._parse_csv(text, ";")

        assert exc_info.value.messages == ["Die Datei enthält keine Kopfzeile."]

    def test_parse_csv_rejects_empty_column_names(self):
        with pytest.raises(ValidationError) as exc_info:
            import_engine._parse_csv("Pseudonym;;Notizen\nA;B;C\n", ";")

        assert exc_info.value.messages == ["Jede Spalte der Kopfzeile braucht eine Überschrift."]

    def test_parse_csv_strips_before_rejecting_duplicate_headers(self):
        with pytest.raises(ValidationError) as exc_info:
            import_engine._parse_csv("Pseudonym; Pseudonym \nA;B\n", ";")

        assert exc_info.value.messages == ["Die Kopfzeile enthält doppelte Spaltenüberschriften."]

    def test_read_upload_enforces_the_byte_limit(self):
        stream = io.BytesIO(b"x" * (import_engine.MAX_BYTES + 1))
        stream.size = import_engine.MAX_BYTES + 1
        with pytest.raises(ValidationError):
            import_engine._read_upload(stream)
