"""Perf-/Nebenlaeufigkeits-Test: der AuditLog-Hashchain-Advisory-Lock darf
NICHT ueber den synchronen ClamAV-Scan + Fernet-Encrypt gehalten werden
(Refs #1345).

Hintergrund: ``EventCreateView.post`` umspannte frueher mit EINEM
``transaction.atomic()`` sowohl ``create_event`` (schreibt eine AuditLog-Zeile;
deren ``save()`` nimmt ``pg_advisory_xact_lock`` je Facility, frei erst beim
Commit der aeusseren Transaktion) als auch den bis zu ``CLAMAV_TIMEOUT``
langen Datei-Scan. In diesem Fenster blockierte jede andere audit-schreibende
Aktion derselben Facility. Der Fix zieht Scan + Verschluesselung VOR den
atomic-Block — waehrend des Scans darf der Lock daher frei sein.

Der Advisory-Lock ist auf SQLite ein No-op; dieser Test braucht PostgreSQL
(wie ``test_rls_functional``).
"""

from __future__ import annotations

import threading

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate

pytestmark = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Advisory-Lock-Serialisierung ist nur auf PostgreSQL beobachtbar",
)


PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _lock_is_free(facility_id) -> bool:
    """True, wenn der Audit-Chain-Advisory-Lock der Facility GERADE frei ist.

    Probt aus einer SEPARATEN DB-Verbindung (eigener Thread -> eigene
    Django-Connection) per ``pg_try_advisory_xact_lock`` gegen exakt den
    Schluessel, den ``assign_chain_fields`` nimmt
    (``hashtext('audit_chain_<fid>')``). Haelt ein anderer offener
    Transaktions-Kontext den Lock, gibt der (nicht blockierende) Try-Lock
    ``False`` zurueck. Kein ``sleep``, damit der Test deterministisch bleibt.
    """
    result: dict[str, bool] = {}

    def worker():
        conn = connections.create_connection("default")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
                    [f"audit_chain_{facility_id}"],
                )
                result["free"] = bool(cur.fetchone()[0])
        finally:
            conn.close()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return result["free"]


def _doc_type_with_file(facility):
    dt = DocumentType.objects.create(
        facility=facility,
        name="Doc mit Anhang",
        category=DocumentType.Category.NOTE,
    )
    ft_file = FieldTemplate.objects.create(
        facility=facility,
        name="Anhang",
        field_type=FieldTemplate.FieldType.FILE,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    return dt


@pytest.mark.django_db(transaction=True)
def test_scan_does_not_hold_audit_chain_lock(client, staff_user, facility):
    """Waehrend des Datei-Scans muss der Audit-Chain-Lock der Facility frei sein.

    Wir haengen die Lock-Probe an den (gemockten) Virenscan: Wird sie
    aufgerufen, ist entweder ``create_event`` bereits gelaufen und haelt den
    Lock im selben ``atomic`` (alter, blockierender Zustand -> Probe False) oder
    der Scan laeuft VOR der Transaktion (Fix -> Probe True).
    """
    doc_type = _doc_type_with_file(facility)
    client.force_login(staff_user)

    captured: dict[str, bool] = {}

    def probing_scan(facility_arg, uploaded_file, event, user):
        captured["lock_free"] = _lock_is_free(facility_arg.pk)
        # Kein Fund -> sauber, kein Raise (Scan-Vertrag: nur bei Treffer werfen).

    from unittest.mock import patch

    upload = SimpleUploadedFile("test.pdf", PDF_MINIMAL, content_type="application/pdf")
    with patch("core.services.file_vault.storage.run_virus_scan", probing_scan):
        resp = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "anhang": upload,
            },
        )

    assert resp.status_code == 302, resp.content
    assert captured.get("lock_free") is True, "Audit-Chain-Advisory-Lock wurde ueber den Datei-Scan gehalten"
    # Sanity: Event + Anhang wurden regulaer angelegt.
    event = Event.objects.get(document_type=doc_type)
    assert event.attachments.count() == 1


@pytest.mark.django_db(transaction=True)
def test_chain_stays_verifiable_after_upload_create(client, staff_user, facility):
    """Nach einem Event-Create mit Anhang bleibt die Facility-Hashkette gruen."""
    from core.services.audit.chain import verify_chain

    doc_type = _doc_type_with_file(facility)
    client.force_login(staff_user)

    upload = SimpleUploadedFile("test.pdf", PDF_MINIMAL, content_type="application/pdf")
    resp = client.post(
        reverse("core:event_create"),
        {
            "document_type": str(doc_type.pk),
            "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            "anhang": upload,
        },
    )
    assert resp.status_code == 302, resp.content

    result = verify_chain(facility)
    assert result.ok, result.reason


@pytest.mark.django_db(transaction=True)
def test_security_violation_survives_policy_reject(client, staff_user, facility):
    """Ein Virus-/Policy-Reject schreibt den ``SECURITY_VIOLATION``-Audit
    dauerhaft — er wird NICHT mehr mit der Event-Transaktion zurueckgerollt
    (bewusste Verhaltensaenderung, Refs #1345).
    """
    from unittest.mock import patch

    from core.services.file_vault import ScanResult

    doc_type = _doc_type_with_file(facility)
    client.force_login(staff_user)

    violations_before = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).count()
    events_before = Event.objects.count()

    upload = SimpleUploadedFile("test.pdf", PDF_MINIMAL, content_type="application/pdf")
    with patch(
        "core.services.file_vault.policy.scan_file",
        return_value=ScanResult(clean=False, infected=True, signature="EICAR-Test-Signature"),
    ):
        resp = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "anhang": upload,
            },
        )

    # Kein Event angelegt (Reject vor der Transaktion), aber der Audit ueberlebt.
    assert Event.objects.count() == events_before
    violations_after = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).count()
    assert violations_after == violations_before + 1, (
        "SECURITY_VIOLATION muss den Reject ueberleben (durable pre-tx Audit)"
    )
    assert resp.status_code in (200, 422)
