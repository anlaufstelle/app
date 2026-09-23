"""Speicherkontingent je Einrichtung (Refs #2129, Befund N-5).

Bis hierher war die Groessenbegrenzung ausschliesslich **pro Datei**
(``enforce_upload_size``: ``min(Settings.max_file_size_mb, globaler Cap)``).
Eine Summe je Einrichtung wurde nirgends gebildet; betriebsseitig gab es nur
den globalen Disk-Check in ``/health/``, der meldet, aber nichts verhindert und
keine Einrichtung kennt. Im Mehr-Mandanten-Betrieb (eine Instanz, mehrere
Einrichtungen — das RLS-Zielbild) konnte damit **eine** Einrichtung mit
lauter legitimen 10-MB-Uploads den gemeinsamen ``MEDIA_ROOT`` fuellen; die
Folge (Upload- und Backup-Fehler) traf **alle**. Verfuegbarkeits- und
Isolationsthema nach Art. 32 DSGVO, kein Vertraulichkeitsleck.

Diese Suite nagelt fest:

* das wirksame Limit = ``min(Settings.max_storage_mb, ENV-Backstop)``, beide
  optional (``NULL``/``0`` = unbegrenzt an dieser Stelle),
* die fail-closed Durchsetzung in ``prepare_encrypted_upload`` — **vor** dem
  Virenscan (kein Scan fuer etwas, das ohnehin abgelehnt wird),
* dass der Papierkorb mitzaehlt (soft-geloeschte Anhaenge belegen Platz, bis
  sie physisch getilgt sind),
* dass die Ablehnung als ``ATTACHMENT_QUOTA_EXCEEDED`` auditiert wird und
  **nicht** als ``SECURITY_VIOLATION`` (normaler Betriebszustand, kein
  Angriff),
* die Sichtbarkeit fuer Anwendungsbetreuung (Verwaltungs-Hub) und Leitung
  (Leitungs-Arbeitszentrale) samt 90-%-Warnschwelle.
"""

from __future__ import annotations

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

# ``store_encrypted_file`` prueft Magic-Bytes via libmagic (#610).
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffaehig ({_libmagic_exc}) — Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, Settings  # noqa: E402
from core.models.attachment import Attachment  # noqa: E402
from core.services.file_vault import store_encrypted_file  # noqa: E402
from core.services.file_vault.encryption import encrypt_field  # noqa: E402
from core.services.file_vault.quota import facility_storage_usage  # noqa: E402

MB = 1024 * 1024

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


@pytest.fixture
def facility_with_settings(facility):
    Settings.objects.update_or_create(
        facility=facility,
        defaults={"allowed_file_types": "pdf", "max_file_size_mb": 10},
    )
    return facility


@pytest.fixture
def doc_type_with_file(facility_with_settings):
    dt = DocumentType.objects.create(
        facility=facility_with_settings,
        name="DocWithFile",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    ft_file = FieldTemplate.objects.create(
        facility=facility_with_settings,
        name="Scan",
        field_type=FieldTemplate.FieldType.FILE,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    return dt, ft_file


@pytest.fixture
def event(facility_with_settings, staff_user, doc_type_with_file):
    dt, _ = doc_type_with_file
    return Event.objects.create(
        facility=facility_with_settings,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )


@pytest.fixture
def existing_bytes(facility_with_settings, doc_type_with_file, event, staff_user):
    """Belege der Einrichtung ``size`` Bytes, ohne echte Dateien anzulegen.

    Der Verbrauch ist eine reine ``SUM(file_size)`` — fuer die Kontingent-Frage
    ist die DB-Zeile massgeblich, nicht die ``.enc`` auf der Platte.
    """
    _, ft_file = doc_type_with_file

    def _occupy(size: int, *, deleted: bool = False):
        return Attachment.objects.create(
            facility=facility_with_settings,
            event=event,
            field_template=ft_file,
            storage_filename=f"{uuid.uuid4()}.enc",
            original_filename_encrypted=encrypt_field("bestand.pdf"),
            file_size=size,
            mime_type="application/pdf",
            detected_mime="application/pdf",
            created_by=staff_user,
            is_current=True,
            entry_id=uuid.uuid4(),
            deleted_at=timezone.now() if deleted else None,
        )

    return _occupy


@pytest.fixture
def upload(facility_with_settings, staff_user, doc_type_with_file, event):
    _, ft_file = doc_type_with_file

    def _upload(payload=PDF_BYTES, name="neu.pdf"):
        uploaded = SimpleUploadedFile(name, payload, content_type="application/pdf")
        return store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)

    return _upload


@pytest.mark.django_db
class TestFacilityStorageUsage:
    """Der geteilte Rechner hinter Durchsetzung UND Anzeige."""

    def test_leere_einrichtung_hat_null_verbrauch(self, facility_with_settings, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0

        usage = facility_storage_usage(facility_with_settings)

        assert usage.used_bytes == 0
        assert usage.trash_bytes == 0
        assert usage.limit_bytes is None

    def test_papierkorb_zaehlt_zum_verbrauch(self, facility_with_settings, existing_bytes, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        existing_bytes(3 * MB)
        existing_bytes(2 * MB, deleted=True)

        usage = facility_storage_usage(facility_with_settings)

        assert usage.used_bytes == 5 * MB
        assert usage.trash_bytes == 2 * MB

    def test_setting_greift_wenn_es_strenger_ist_als_der_backstop(self, facility_with_settings, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 2048
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=100)

        assert facility_storage_usage(facility_with_settings).limit_bytes == 100 * MB

    def test_backstop_greift_wenn_er_strenger_ist_als_das_setting(self, facility_with_settings, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 50
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=100)

        assert facility_storage_usage(facility_with_settings).limit_bytes == 50 * MB

    def test_null_setting_faellt_auf_den_backstop(self, facility_with_settings, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 64
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=None)

        assert facility_storage_usage(facility_with_settings).limit_bytes == 64 * MB

    def test_backstop_null_und_setting_null_ist_unbegrenzt(self, facility_with_settings, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=None)

        assert facility_storage_usage(facility_with_settings).limit_bytes is None

    def test_fehlende_settings_row_faellt_auf_den_backstop(self, facility, settings):
        """Fail-closed wie ``enforce_allowed_file_types``: ohne Settings-Row
        gilt der Betreiber-Backstop, nicht ‚unbegrenzt'."""
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 8
        Settings.objects.filter(facility=facility).delete()

        assert facility_storage_usage(facility).limit_bytes == 8 * MB

    def test_prozent_und_warnschwelle(self, facility_with_settings, existing_bytes, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=10)
        existing_bytes(9 * MB)

        usage = facility_storage_usage(facility_with_settings)

        assert usage.percent_used == 90
        assert usage.is_near_limit is True

    def test_schwelle_vergleicht_ungerundet(self, facility_with_settings, existing_bytes, settings):
        """Refs #2129: Die Schwelle wurde bisher auf der
        **gerundeten** Prozentzahl geprueft — 89,6 % rundeten auf 90 und
        loesten die Warnung aus, obwohl sie noch nicht faellig war. Der
        Vergleich laeuft jetzt auf dem Byte-Verhaeltnis."""
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1000)
        existing_bytes(896 * MB)  # 89,6 %

        usage = facility_storage_usage(facility_with_settings)

        assert usage.percent_used == 90, "die ANZEIGE darf weiter runden"
        assert usage.is_near_limit is False, "die SCHWELLE darf nicht runden"

    def test_schwelle_greift_ab_exakt_neunzig_prozent(self, facility_with_settings, existing_bytes, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1000)
        existing_bytes(900 * MB)

        assert facility_storage_usage(facility_with_settings).is_near_limit is True

    def test_unter_der_schwelle_keine_warnung(self, facility_with_settings, existing_bytes, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=10)
        existing_bytes(8 * MB)

        usage = facility_storage_usage(facility_with_settings)

        assert usage.percent_used == 80
        assert usage.is_near_limit is False

    def test_unbegrenzt_kennt_weder_prozent_noch_warnung(self, facility_with_settings, existing_bytes, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=None)
        existing_bytes(500 * MB)

        usage = facility_storage_usage(facility_with_settings)

        assert usage.percent_used is None
        assert usage.is_near_limit is False

    def test_summe_ueber_zwei_gigabyte_laeuft_nicht_ueber(self, facility_with_settings, existing_bytes, settings):
        """``Attachment.file_size`` ist ein ``PositiveIntegerField`` (32 Bit) —
        die SUMME darueber ist es nicht. Eine Einrichtung mit mehr als 2 GiB
        Anhaengen darf die Kontingent-Rechnung nicht sprengen (Postgres liefert
        ``bigint``, Python rechnet unbegrenzt weiter)."""
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        existing_bytes(1500 * MB)
        existing_bytes(1500 * MB)

        assert facility_storage_usage(facility_with_settings).used_bytes == 3000 * MB

    def test_fremde_einrichtung_zaehlt_nicht_mit(self, facility_with_settings, existing_bytes, organization, settings):
        from core.models import Facility

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        existing_bytes(7 * MB)
        other = Facility.objects.create(organization=organization, name="Nachbarstelle")

        assert facility_storage_usage(other).used_bytes == 0
        assert facility_storage_usage(facility_with_settings).used_bytes == 7 * MB


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestQuotaDurchsetzung:
    def test_upload_ueber_dem_kontingent_wird_abgelehnt(
        self, facility_with_settings, existing_bytes, upload, event, settings
    ):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB)

        with pytest.raises(ValidationError) as exc:
            upload()

        assert "Speicherkontingent" in str(exc.value)
        assert Attachment.objects.filter(event=event, deleted_at__isnull=True).count() == 1

    def test_upload_unter_dem_kontingent_wird_angenommen(
        self, facility_with_settings, existing_bytes, upload, settings
    ):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB - 10_000)

        assert upload().pk is not None

    def test_grenzwert_ist_inklusiv(self, facility_with_settings, existing_bytes, upload, settings):
        """Genau aufgehen darf noch — ``used + neu <= limit``."""
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB - len(PDF_BYTES))

        assert upload().pk is not None

    def test_ein_byte_darueber_wird_abgelehnt(self, facility_with_settings, existing_bytes, upload, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB - len(PDF_BYTES) + 1)

        with pytest.raises(ValidationError):
            upload()

    def test_papierkorb_belegt_das_kontingent_weiter(self, facility_with_settings, existing_bytes, upload, settings):
        """Soft-geloeschte Anhaenge liegen physisch weiter auf der Platte —
        sie geben das Kontingent erst mit der Tilgung frei."""
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB, deleted=True)

        with pytest.raises(ValidationError):
            upload()

    def test_ohne_kontingent_und_ohne_backstop_bleibt_alles_erlaubt(
        self, facility_with_settings, existing_bytes, upload, settings
    ):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=None)
        existing_bytes(2000 * MB)

        assert upload().pk is not None

    def test_backstop_greift_auch_ohne_eigenes_kontingent(
        self, facility_with_settings, existing_bytes, upload, settings
    ):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 1
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=None)
        existing_bytes(1 * MB)

        with pytest.raises(ValidationError):
            upload()

    def test_kein_virenscan_fuer_einen_ohnehin_abgelehnten_upload(
        self, facility_with_settings, existing_bytes, upload, settings
    ):
        """Die Kontingent-Pruefung sitzt VOR ``run_virus_scan`` — ein Upload,
        der ohnehin abgewiesen wird, darf den Scanner nicht beschaeftigen."""
        from unittest.mock import patch

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB)

        with patch("core.services.file_vault.storage.run_virus_scan") as scan:
            with pytest.raises(ValidationError):
                upload()

        scan.assert_not_called()

    def test_keine_enc_datei_bleibt_liegen(self, facility_with_settings, existing_bytes, upload, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path)
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB)

        with pytest.raises(ValidationError):
            upload()

        assert list(tmp_path.rglob("*.enc")) == []


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestQuotaImSelbenRequest:
    """Refs #2129 (R-L7a-5 / Review-Befund B2).

    ``stage_uploads``/``stage_attachment_uploads`` stagen alle Dateien eines
    Formular-Submits, BEVOR irgendeine davon als ``Attachment``-Zeile
    existiert. Jede Einzelpruefung sah deshalb denselben alten DB-Verbrauch —
    ein einzelner Submit konnte das Kontingent mit bis zu
    ``FILE_VAULT_MAX_UPLOAD_FILES`` Dateien deterministisch ueberschreiten. Das
    ist keine tolerierte Parallel-Race (die betrifft zwei Requests), sondern
    der normale Batch-Pfad.
    """

    def test_drei_dateien_im_batch_reissen_das_kontingent_nicht(
        self, facility_with_settings, staff_user, doc_type_with_file, existing_bytes, settings
    ):
        from core.services.attachments.crud import stage_uploads

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        # Rest: 2x PDF passt, das dritte nicht mehr.
        existing_bytes(1 * MB - 2 * len(PDF_BYTES) - 1)
        _, ft_file = doc_type_with_file
        files = {
            ft_file.name.lower(): [
                SimpleUploadedFile(f"datei{i}.pdf", PDF_BYTES, content_type="application/pdf") for i in range(3)
            ]
        }

        with pytest.raises(ValidationError) as exc:
            with stage_uploads(facility_with_settings, staff_user, files):
                pass

        assert "Speicherkontingent" in str(exc.value)

    def test_batch_unter_dem_kontingent_geht_vollstaendig_durch(
        self, facility_with_settings, staff_user, doc_type_with_file, existing_bytes, settings
    ):
        from core.services.attachments.crud import stage_uploads

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB - 4 * len(PDF_BYTES))
        _, ft_file = doc_type_with_file
        files = {
            ft_file.name.lower(): [
                SimpleUploadedFile(f"datei{i}.pdf", PDF_BYTES, content_type="application/pdf") for i in range(3)
            ]
        }

        with stage_uploads(facility_with_settings, staff_user, files) as batch:
            staged = [item for entries in batch.by_slug.values() for item in entries]
            batch.commit()

        assert len(staged) == 3
        for item in staged:
            item.output_path.unlink(missing_ok=True)

    def test_replace_pfad_zaehlt_ebenfalls_kumulativ(
        self, facility_with_settings, staff_user, doc_type_with_file, event, existing_bytes, settings
    ):
        """Derselbe Fehler lag im Update-/Replace-Pfad."""
        from core.services.attachments.crud import stage_attachment_uploads

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB - 2 * len(PDF_BYTES) - 1)
        dt, ft_file = doc_type_with_file
        slug = ft_file.slug
        file_fields = {
            slug: [SimpleUploadedFile(f"neu{i}.pdf", PDF_BYTES, content_type="application/pdf") for i in range(3)]
        }

        with pytest.raises(ValidationError) as exc:
            stage_attachment_uploads(event, staff_user, {}, file_fields, dt, data_json={})

        assert "Speicherkontingent" in str(exc.value)

    def test_pending_bytes_wirkt_direkt_am_enforcer(self, facility_with_settings, staff_user, existing_bytes, settings):
        """Direkte Sonde auf den neuen Parameter: dieselbe Datei passt ohne
        Offset und passt mit Offset nicht mehr."""
        from core.services.file_vault.policy import enforce_storage_quota

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB - 1000)
        uploaded = SimpleUploadedFile("x.pdf", PDF_BYTES, content_type="application/pdf")

        enforce_storage_quota(facility_with_settings, uploaded, None, staff_user)

        with pytest.raises(ValidationError):
            enforce_storage_quota(facility_with_settings, uploaded, None, staff_user, pending_bytes=900)


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestQuotaAudit:
    def test_ablehnung_schreibt_quota_eintrag_statt_security_violation(
        self, facility_with_settings, existing_bytes, upload, settings
    ):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB)

        with pytest.raises(ValidationError):
            upload()

        assert not AuditLog.objects.filter(
            facility=facility_with_settings, action=AuditLog.Action.SECURITY_VIOLATION
        ).exists()
        entry = AuditLog.objects.get(facility=facility_with_settings, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED)
        assert entry.target_type == "Attachment"
        assert entry.detail["reason"] == "quota_exceeded"
        assert entry.detail["limit_mb"] == 1
        assert "used_mb" in entry.detail
        assert "requested_mb" in entry.detail

    def test_audit_detail_traegt_keinen_dateinamen(self, facility_with_settings, existing_bytes, upload, settings):
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB)

        with pytest.raises(ValidationError):
            upload(name="Bericht-Max-Mustermann.pdf")

        entry = AuditLog.objects.get(facility=facility_with_settings, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED)
        assert "Mustermann" not in str(entry.detail)

    def test_target_type_can_identify_an_import_job(self, facility_with_settings, staff_user, existing_bytes, settings):
        from core.services.file_vault.policy import enforce_storage_quota

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=1)
        existing_bytes(1 * MB)
        uploaded = SimpleUploadedFile("import.csv", b"Pseudonym\nQ\n", content_type="text/csv")

        with pytest.raises(ValidationError):
            enforce_storage_quota(
                facility_with_settings,
                uploaded,
                None,
                staff_user,
                target_type="ImportJob",
            )

        entry = AuditLog.objects.get(
            facility=facility_with_settings,
            action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED,
        )
        assert entry.target_type == "ImportJob"


@pytest.mark.django_db
class TestSichtbarkeit:
    def test_verwaltungs_hub_zeigt_den_speicherstand(self, facility_with_settings, existing_bytes, settings):
        from core.services.hubs import verwaltung_metrics

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=10)
        existing_bytes(4 * MB)
        existing_bytes(1 * MB, deleted=True)

        metric = next(m for m in verwaltung_metrics(facility_with_settings) if m.key == "storage")

        assert metric.value == 5
        assert "10" in metric.detail
        assert "1" in metric.detail

    def test_leitungs_dashboard_zeigt_den_speicherstand(
        self, facility_with_settings, lead_user, existing_bytes, settings
    ):
        from core.services.dashboard import lead_dashboard_context

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=10)
        existing_bytes(4 * MB)

        ctx = lead_dashboard_context(lead_user, facility_with_settings)

        assert ctx["storage_usage"].used_mb == 4
        assert ctx["storage_usage"].limit_mb == 10

    def test_leitungs_dashboard_zeigt_einen_warntext_nicht_nur_farbe(
        self, facility_with_settings, lead_user, existing_bytes, settings
    ):
        """Refs #2129: Bei Erreichen der Schwelle wechselte im
        Lead-Dashboard nur die Textfarbe — der Warnzustand war rein farblich
        codiert (WCAG 1.4.1) und der geforderte Hinweistext fehlte."""
        from core.services.dashboard import lead_dashboard_context

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=10)
        existing_bytes(9 * MB)

        ctx = lead_dashboard_context(lead_user, facility_with_settings)

        assert ctx["storage_warning"], "Leitung bekommt keinen Warntext"
        assert "Speicherkontingent" in ctx["storage_warning"]

    def test_leitungs_dashboard_ohne_warnung_unterhalb_der_schwelle(
        self, facility_with_settings, lead_user, existing_bytes, settings
    ):
        from core.services.dashboard import lead_dashboard_context

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=10)
        existing_bytes(5 * MB)

        assert lead_dashboard_context(lead_user, facility_with_settings)["storage_warning"] is None

    def test_admin_und_leitung_teilen_denselben_warntext(
        self, facility_with_settings, admin_user, lead_user, existing_bytes, settings
    ):
        """Ein msgid fuer beide Rollen — sonst driften die Formulierungen."""
        from core.services.dashboard import facility_admin_dashboard_context, lead_dashboard_context

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=10)
        existing_bytes(9 * MB)

        admin_warnings = facility_admin_dashboard_context(admin_user, facility_with_settings)["settings_warnings"]
        lead_warning = lead_dashboard_context(lead_user, facility_with_settings)["storage_warning"]

        assert lead_warning in admin_warnings

    def test_warnung_ab_neunzig_prozent(self, facility_with_settings, admin_user, existing_bytes, settings):
        from core.services.dashboard import facility_admin_dashboard_context

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=10)
        existing_bytes(9 * MB)

        warnings = facility_admin_dashboard_context(admin_user, facility_with_settings)["settings_warnings"]

        assert any("Speicherkontingent" in warning for warning in warnings)

    def test_keine_warnung_unterhalb_der_schwelle(self, facility_with_settings, admin_user, existing_bytes, settings):
        from core.services.dashboard import facility_admin_dashboard_context

        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.filter(facility=facility_with_settings).update(max_storage_mb=10)
        existing_bytes(5 * MB)

        warnings = facility_admin_dashboard_context(admin_user, facility_with_settings)["settings_warnings"]

        assert not any("Speicherkontingent" in warning for warning in warnings)
