"""Pin-Tests zum Meilenstein release-0.98 (#2193) — Modul-Batch ``file_vault.audit``.

Quelle: ``src/core/services/file_vault/audit.py``. Vorgabe ist die
Prio-2-Survivor-Triage aus ``reports/c-scratch/batch-vault.json`` (Quelle
``triage-096-audit-settings-forms.json``): die drei betroffenen Funktionen
bleiben nutzbar, verlieren aber Rechenschaftsinformation (Dateiendung als
Typmarker, Owner-Zuordnung/Zieltyp der Kontingent-Zeile, deren MB-Werte,
verifizierter MIME-Typ beim Upload) — kein PII-Leak, keine fehlende Zeile.

Konventionen wie ``test_mutation_pins_096_vault_audit.py``: echte
Model-Objekte, Fixtures aus ``src/tests/conftest.py``, exakte Vergleiche.
Jeder Test nennt seine gepinnten Mutanten in der Docstring.

Hinweis zur Triage: mehrere ``diff_kurz``-Eintraege des Batches fuer
``audit_attachment_quota_exceeded`` beschreiben NICHT exakt die Zeile, die im
Mutantenbaum tatsaechlich veraendert ist (u.a. mutmut_2 aendert den
``target_type``-Funktionsdefault statt ``_owner_reference``; mutmut_4 zerlegt
``owner_type, owner_id = _owner_reference(owner)`` in
``owner_type, owner_id = None`` statt ``user=None`` zu setzen; mutmut_6/12/13
entfernen Pflichtargumente von ``audit_event`` (``action``/``user``) statt
``target_type`` zu veraendern). Massgeblich war in jedem Fall der reale
Quelltext unter ``/work/anlaufstelle/mutants`` (Funktionen
``x_audit_attachment_quota_exceeded__mutmut_<n>``), nicht der Batch-Text.
Der EINE direkte Aufruf unten deckt alle diese Ueberlebenden trotzdem ab:
fehlende Pflichtargumente lassen den Aufruf crashen (rot), falsche Werte
lassen die Assertions fehlschlagen (rot).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.models import AuditLog, FieldTemplate
from core.services.file_vault.audit import audit_attachment_quota_exceeded, filename_fingerprint
from core.services.file_vault.storage import StagedUpload, commit_staged_upload

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_extension_uses_the_last_dot_not_the_first():
    """Pins x__extension__mutmut_12 (``rsplit(".", 1)`` -> ``split(".", 1)``).

    Ein Dateiname mit ZWEI Punkten (``"Attest Dr.Meier.pdf"``): der
    Original-Code nimmt die Endung nach dem LETZTEN Punkt (``"pdf"``); der
    Mutant nimmt alles nach dem ERSTEN Punkt (``"Meier.pdf"``, enthaelt
    selbst einen Punkt und ist damit nicht ``isalnum``) und verwirft die
    Endung ganz (``""``). Der Bestand deckt nur einpunktige Dateinamen ab.
    """
    result = filename_fingerprint("Attest Dr.Meier.pdf")

    assert result["filename_ext"] == "pdf"


@pytest.fixture
def file_template_098(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Pin-098-Anhang",
        field_type=FieldTemplate.FieldType.FILE,
    )


def _staged(tmp_path, *, name="pin-098.pdf", content_type="application/pdf", detected_mime="application/pdf"):
    output_path = tmp_path / "staged.enc"
    output_path.write_bytes(b"encrypted")
    return StagedUpload(
        storage_name="staged.enc",
        output_path=output_path,
        detected_mime=detected_mime,
        file_size=9,
        original_name=name,
        content_type=content_type,
    )


def test_upload_audit_mime_is_the_verified_type_not_the_reported_content_type(
    tmp_path, sample_event, file_template_098, staff_user
):
    """Pins x_audit_attachment_upload__mutmut_39 (``detected_mime or content_type or
    fallback`` -> ``detected_mime and content_type or fallback``).

    libmagic erkennt ``application/pdf`` (``detected_mime``), der Browser
    meldet bewusst falsch ``text/plain`` (``content_type``): der
    Original-Code traegt den VERIFIZIERTEN Typ ins Audit-``detail``; der
    Mutant traegt im Normalfall (``detected_mime`` gesetzt, also wahr) ueber
    das kurzschliessende ``and`` den ungeprueften ``content_type`` ein.
    """
    commit_staged_upload(
        sample_event,
        file_template_098,
        _staged(tmp_path, content_type="text/plain"),
        staff_user,
    )

    entry = AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_UPLOAD)
    assert entry.detail["mime"] == "application/pdf"


def test_quota_exceeded_records_target_owner_and_mb_values_precisely(facility, sample_event, staff_user):
    """Pins x_audit_attachment_quota_exceeded__mutmut_2/4/6/7/12/13/15/16/17/18/19/24/25/
    38/40/41/51/53/54 (19 Ueberlebende dieser einen Funktion).

    Ein direkter Aufruf mit bekannten ``usage``-/``requested_bytes``-Werten
    deckt alle 19 ab: falscher/fehlender ``target_type``-Default oder
    -Uebergabe (2/6/15/16/17), fehlende Pflichtargumente an ``audit_event``
    die zum Crash fuehren (6/12/13 — ``action``/``user`` entfernt), ``user``
    hart auf ``None`` (7), ``target_id`` verfaelscht ueber
    ``str(None)``/eine ``getattr``-Falle (18/19/24/25) sowie ``used_mb``/
    ``requested_mb`` als Konstante, Produkt oder falscher Kehrwert statt
    echter Division durch 1 048 576 (38/40/41/51/53/54). Der Bestandstest
    (``test_file_vault_quota.py``) prueft nur ``"used_mb" in entry.detail``
    und nie ``target_id``/``user_id``/exakte MB-Werte.
    """
    usage = SimpleNamespace(used_bytes=1_048_576, limit_mb=1)

    entry = audit_attachment_quota_exceeded(
        facility,
        staff_user,
        sample_event,
        usage=usage,
        requested_bytes=2_097_152,
    )

    assert entry.target_type == "Attachment"
    assert entry.target_id == str(sample_event.pk)
    assert entry.user_id == staff_user.pk
    assert entry.detail["owner_type"] == "event"
    assert entry.detail["owner_id"] == str(sample_event.pk)
    assert entry.detail["used_mb"] == 1.0
    assert isinstance(entry.detail["used_mb"], float)
    assert entry.detail["requested_mb"] == 2.0
    assert isinstance(entry.detail["requested_mb"], float)
