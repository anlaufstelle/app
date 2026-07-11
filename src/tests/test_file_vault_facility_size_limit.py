"""Per-Facility-Groessenlimit als Service-Layer-SSOT (Refs #1363, N10).

``DynamicEventDataForm.clean()`` (``forms/events.py``) prueft
``Settings.max_file_size_mb`` bislang nur fuer regulaere Form-Felder.
Replace-Uploads laufen ueber den dynamischen Key
``<slug>__replace__<entry_id>`` direkt in ``_apply_replace``
(``services/events/crud.py``) an ``store_encrypted_file`` vorbei am Formular
— bis Issue #1268 griff dort nur die globale 50-MB-Obergrenze
(``FILE_VAULT_MAX_UPLOAD_BYTES``), das (typischerweise kleinere)
Facility-Limit war umgehbar.

Diese Tests verankern, dass ``enforce_upload_size`` (``file_vault/policy.py``)
das striktere von Facility-Limit und globaler Obergrenze erzwingt — fuer
JEDEN Aufrufer von ``store_encrypted_file``, nicht nur den Formular-Pfad.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
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
from core.models.attachment import EventAttachment  # noqa: E402
from core.services.file_vault import store_encrypted_file  # noqa: E402


def _pdf_bytes(marker=b""):
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        b"xref\n0 3\n0000000000 65535 f\n"
        b"trailer<</Size 3/Root 1 0 R>>\n"
        b"startxref\n9\n%%EOF\n" + marker
    )


@pytest.fixture
def _encryption_key(settings):
    settings.ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")
    settings.ENCRYPTION_KEYS = ""


@pytest.fixture
def doc_type_with_file(facility):
    dt = DocumentType.objects.create(
        facility=facility,
        name="DocWithFile",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    ft_file = FieldTemplate.objects.create(
        facility=facility,
        name="Anhang",
        field_type=FieldTemplate.FieldType.FILE,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    return dt, ft_file


@pytest.fixture
def event(facility, staff_user, doc_type_with_file):
    dt, _ = doc_type_with_file
    return Event.objects.create(
        facility=facility,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestServiceLayerFacilySizeLimit:
    """``enforce_upload_size`` muss ``min(Facility-Limit, globaler Cap)``
    erzwingen — direkte Service-Aufrufer eingeschlossen (Replace-Pfad)."""

    def test_add_upload_exceeding_facility_limit_rejected(self, facility, staff_user, doc_type_with_file, event):
        """Direkter ADD-Aufruf (kein supersedes) jenseits des Facility-Limits
        von 1 MB wird abgelehnt, obwohl er weit unter der globalen 50-MB-
        Obergrenze liegt."""
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": "pdf", "max_file_size_mb": 1},
        )
        _, ft_file = doc_type_with_file
        oversized = SimpleUploadedFile("big.pdf", _pdf_bytes(b"X" * (2 * 1024 * 1024)), content_type="application/pdf")

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, oversized, ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "file_too_large"

    def test_replace_upload_exceeding_facility_limit_rejected(self, facility, staff_user, doc_type_with_file, event):
        """Kernbefund N10: Ein Replace-Upload (``supersedes=``) unter dem
        Facility-Limit von 1 MB, aber weit unter der globalen 50-MB-Grenze,
        umging das Limit vor dem Fix vollstaendig."""
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": "pdf", "max_file_size_mb": 1},
        )
        _, ft_file = doc_type_with_file
        original = SimpleUploadedFile("small.pdf", _pdf_bytes(b"v1"), content_type="application/pdf")
        original_attachment = store_encrypted_file(facility, original, ft_file, event, staff_user)

        # 2 MB — unter der globalen 50-MB-Obergrenze, aber ueber dem
        # 1-MB-Facility-Limit.
        replacement = SimpleUploadedFile(
            "big.pdf", _pdf_bytes(b"X" * (2 * 1024 * 1024)), content_type="application/pdf"
        )

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, replacement, ft_file, event, staff_user, supersedes=original_attachment)

        # Keine neue Version gespeichert, alte Version unangetastet.
        assert EventAttachment.objects.filter(event=event).count() == 1
        original_attachment.refresh_from_db()
        assert original_attachment.is_current is True
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "file_too_large"

    def test_facility_limit_above_global_cap_still_bounded_by_global_cap(
        self, facility, staff_user, doc_type_with_file, event, settings
    ):
        """Ist das Facility-Limit hoeher als der globale Cap, bleibt der
        globale Cap (``FILE_VAULT_MAX_UPLOAD_BYTES``) die harte Grenze —
        ``min(facility, global)`` in beide Richtungen."""
        settings.FILE_VAULT_MAX_UPLOAD_BYTES = 1 * 1024 * 1024
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": "pdf", "max_file_size_mb": 100},
        )
        _, ft_file = doc_type_with_file
        oversized = SimpleUploadedFile("big.pdf", _pdf_bytes(b"X" * (2 * 1024 * 1024)), content_type="application/pdf")

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, oversized, ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0

    def test_no_settings_row_falls_back_to_default_max_mb(self, facility, staff_user, doc_type_with_file, event):
        """Fail-closed (Refs #771-Muster): fehlt die Settings-Row, greift
        ``DEFAULT_MAX_FILE_SIZE_MB`` (10 MB) statt eines unbegrenzten
        Facility-Limits."""
        Settings.objects.filter(facility=facility).delete()
        _, ft_file = doc_type_with_file
        oversized = SimpleUploadedFile("big.pdf", _pdf_bytes(b"X" * (11 * 1024 * 1024)), content_type="application/pdf")

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, oversized, ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0

    def test_replace_within_facility_limit_still_succeeds(self, facility, staff_user, doc_type_with_file, event):
        """Regressionsschutz: legitime Replace-Uploads unterhalb des
        Facility-Limits duerfen weiterhin durchlaufen."""
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": "pdf", "max_file_size_mb": 1},
        )
        _, ft_file = doc_type_with_file
        original = SimpleUploadedFile("small.pdf", _pdf_bytes(b"v1"), content_type="application/pdf")
        original_attachment = store_encrypted_file(facility, original, ft_file, event, staff_user)

        replacement = SimpleUploadedFile("still-small.pdf", _pdf_bytes(b"v2"), content_type="application/pdf")
        new_attachment = store_encrypted_file(
            facility, replacement, ft_file, event, staff_user, supersedes=original_attachment
        )

        assert new_attachment.pk is not None
        assert EventAttachment.objects.filter(event=event).count() == 2


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestReplaceViewFacilySizeLimit:
    """View-Integrationstest: Der HTML-Replace-Pfad (``event_update``) muss
    das Facility-Limit ebenfalls durchsetzen, ohne mit 500 zu enden."""

    def test_replace_exceeding_facility_limit_redirects_without_new_version(
        self, client, staff_user, facility, doc_type_with_file
    ):
        from django.contrib.messages import get_messages

        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": "pdf", "max_file_size_mb": 1},
        )
        doc_type, _ft = doc_type_with_file
        client.force_login(staff_user)

        first = SimpleUploadedFile("original.pdf", _pdf_bytes(b"v1"), content_type="application/pdf")
        resp = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "anhang": first,
            },
        )
        assert resp.status_code == 302
        event = Event.objects.get(document_type=doc_type)
        entry_id = event.attachments.get().entry_id
        count_before = EventAttachment.objects.filter(event=event).count()

        replacement = SimpleUploadedFile(
            "big.pdf", _pdf_bytes(b"X" * (2 * 1024 * 1024)), content_type="application/pdf"
        )
        resp = client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                f"anhang__replace__{entry_id}": replacement,
            },
        )

        assert resp.status_code == 302  # graceful redirect, KEIN 500
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        assert any("groß" in m or "gross" in m for m in msgs), msgs
        # Keine neue Attachment-Version gespeichert (Facility-Limit hielt).
        assert EventAttachment.objects.filter(event=event).count() == count_before


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFormPathNoDoubleError:
    """Regressionsschutz: Der normale ADD-Formularpfad bleibt unveraendert
    — genau EIN Fehler (Form-Layer), kein zusaetzlicher Service-Layer-Reject
    (der Service wird bei ungueltigem Formular gar nicht erst aufgerufen)."""

    def test_add_via_form_produces_single_error_no_service_layer_audit(
        self, client, staff_user, facility, doc_type_with_file
    ):
        doc_type, _ft = doc_type_with_file
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": "pdf", "max_file_size_mb": 1},
        )
        client.force_login(staff_user)
        oversized = SimpleUploadedFile("big.pdf", _pdf_bytes(b"X" * (2 * 1024 * 1024)), content_type="application/pdf")

        resp = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "anhang": oversized,
            },
        )

        assert resp.status_code == 200  # Formular-Re-Render, kein Redirect
        assert EventAttachment.objects.count() == 0
        data_form = resp.context["data_form"]
        assert len(data_form.errors.get("anhang", [])) == 1
        # store_encrypted_file wurde nie aufgerufen -> kein service-layer
        # SECURITY_VIOLATION-Log fuer diesen Upload (sonst waer's doppelt).
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.SECURITY_VIOLATION, detail__reason="file_too_large"
        ).exists()
