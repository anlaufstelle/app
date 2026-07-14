"""L5 (Refs #1375) — soft-gelöschte Anhänge dürfen nicht mehr ausgeliefert werden.

``EventAttachment`` trägt ein ``deleted_at`` (User-seitiges Soft-Delete), aber
KEINEN Soft-Delete-Manager. ``get_visible_attachment_or_404`` filterte nur nach
``pk`` + ``event`` — ein soft-gelöschter Anhang blieb also per Direkt-URL
(Download-View) abrufbar (innerhalb Facility+Sensitivität, kein Cross-Tenant,
aber ein logisch gelöschtes Objekt, das nicht mehr erreichbar sein darf). Der
Loader schließt ``deleted_at__isnull=True`` jetzt ein -> 404.
"""

from __future__ import annotations

import pytest
from django.http import Http404
from django.utils import timezone

from core.services.compliance.sensitivity import get_visible_attachment_or_404

pytestmark = pytest.mark.django_db


class TestSoftDeletedAttachmentHidden:
    def test_active_attachment_is_returned(self, facility, sample_event, lead_user, authz_attachment):
        event, attachment = get_visible_attachment_or_404(
            lead_user, facility, sample_event.pk, authz_attachment.pk
        )
        assert attachment.pk == authz_attachment.pk

    def test_soft_deleted_attachment_raises_404(self, facility, sample_event, lead_user, authz_attachment):
        authz_attachment.deleted_at = timezone.now()
        authz_attachment.save(update_fields=["deleted_at"])
        with pytest.raises(Http404):
            get_visible_attachment_or_404(lead_user, facility, sample_event.pk, authz_attachment.pk)
