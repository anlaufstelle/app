"""Tests für die Audit-Spur des Offline-Notiz-Exports (Refs #1476, R10).

Der Export einer nicht übertragbaren Offline-Notiz (``conflict-list.js``
``exportNote``) schreibt entschlüsselte Art.-9-PII als Klartext-Datei auf die
Platte. Der Client meldet diese bewusste Nutzeraktion an
``POST /api/v1/offline/audit/note-export/`` (online direkt, offline über die
Replay-Queue). Der Endpoint schreibt genau EINEN ``OFFLINE_NOTE_EXPORT``-Eintrag
und trägt dabei bewusst nur die (client-seitige) Event-``pk`` als Bezug — keine
Notiz-Inhalte, kein Pseudonym (Refs #1476/R20).
"""

import json

import pytest
from django.urls import reverse

from core.models import AuditLog


def _url():
    return reverse("core:offline_note_export_audit")


@pytest.mark.django_db
class TestOfflineNoteExportAuditView:
    def test_requires_login(self, client):
        response = client.post(_url(), data="{}", content_type="application/json")
        assert response.status_code in (302, 403)

    def test_get_not_allowed(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(_url())
        assert response.status_code == 405

    def test_post_writes_single_audit_entry(self, client, assistant_user):
        client.force_login(assistant_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.OFFLINE_NOTE_EXPORT).count()
        export_before = AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count()

        response = client.post(
            _url(),
            data=json.dumps({"event_pk": "ev-123"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

        entries = AuditLog.objects.filter(action=AuditLog.Action.OFFLINE_NOTE_EXPORT)
        assert entries.count() == before + 1
        entry = entries.latest("timestamp")
        assert entry.target_type == "Event-OfflineNoteExport"
        assert entry.target_id == "ev-123"
        assert entry.detail.get("event_pk") == "ev-123"
        # Bewusst NICHT unter EXPORT (Massen-Export-Breach-Heuristik sauber).
        assert AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count() == export_before

    def test_no_pii_freetext_leaks_into_detail(self, client, assistant_user):
        """Unbekannte/überlange Felder aus dem Body dürfen NICHT ins
        append-only Audit-Log wandern; nur eine gekappte event_pk wird
        übernommen."""
        client.force_login(assistant_user)
        response = client.post(
            _url(),
            data=json.dumps({"event_pk": "x" * 200, "note": "geheime PII", "pseudonym": "Falke"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        entry = AuditLog.objects.filter(action=AuditLog.Action.OFFLINE_NOTE_EXPORT).latest("timestamp")
        assert len(entry.target_id) <= 64
        assert "note" not in entry.detail
        assert "pseudonym" not in entry.detail
        assert "geheime PII" not in json.dumps(entry.detail)

    def test_tolerates_empty_and_garbage_body(self, client, assistant_user):
        client.force_login(assistant_user)
        for body in ["", "not json", "[]", "null"]:
            response = client.post(_url(), data=body, content_type="application/json")
            assert response.status_code == 200
            entry = AuditLog.objects.filter(action=AuditLog.Action.OFFLINE_NOTE_EXPORT).latest("timestamp")
            assert entry.detail.get("event_pk") == ""
