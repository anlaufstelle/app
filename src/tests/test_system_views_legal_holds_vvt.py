"""Tests fuer Cross-Facility-Legal-Holds und VVT im ``/system/``-Areal.

Enthaelt die Cluster:

* ``TestSystemLegalHoldAccess`` — Zugriffsschutz ``/system/legal-holds/`` (Refs #877).
* ``TestSystemLegalHoldList`` — Listing- und Filter-Logik fuer Legal-Holds.
* ``TestSystemVVTAccess`` — VVT-View Smoke-Test (Refs #876).
"""

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog
from core.models.retention import LegalHold

# ---------------------------------------------------------------------------
# Tier 2: Cross-Facility-Legal-Holds (Refs #877)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemLegalHoldAccess:
    """``GET /system/legal-holds/`` — Zugriffsschutz."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_legal_hold_list"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_legal_hold_list"))
        assert response.status_code == 403

    def test_super_admin_ok(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"))
        assert response.status_code == 200
        content = response.content.decode("utf-8", errors="replace")
        assert "facility-übergreifend" in content


@pytest.mark.django_db
class TestSystemLegalHoldList:
    """Listing- und Filter-Logik."""

    def test_lists_holds_cross_facility(
        self,
        client,
        super_admin_user,
        facility,
        second_facility,
        lead_user,
        second_facility_user,
    ):
        """Holds beider Facilities werden gelistet, sortiert nach created_at DESC."""
        h1 = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Erste Begruendung",
            created_by=lead_user,
        )
        h2 = LegalHold.objects.create(
            facility=second_facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Zweite Begruendung",
            created_by=second_facility_user,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"))
        assert response.status_code == 200

        page_obj = response.context["page_obj"]
        ids = [h.pk for h in page_obj.object_list]
        assert h1.pk in ids
        assert h2.pk in ids
        # Sortierung created_at DESC: h2 (spaeter erstellt) zuerst.
        assert ids.index(h2.pk) < ids.index(h1.pk)

    def test_filter_by_facility(
        self,
        client,
        super_admin_user,
        facility,
        second_facility,
        lead_user,
        second_facility_user,
    ):
        h1 = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Begruendung A",
            created_by=lead_user,
        )
        LegalHold.objects.create(
            facility=second_facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Begruendung B",
            created_by=second_facility_user,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"), {"facility": facility.pk})
        assert response.status_code == 200
        page_obj = response.context["page_obj"]
        ids = [h.pk for h in page_obj.object_list]
        assert ids == [h1.pk]

    def test_filter_status_active(self, client, super_admin_user, facility, lead_user):
        active_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Aktiv",
            created_by=lead_user,
        )
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Aufgehoben",
            created_by=lead_user,
            dismissed_at=timezone.now(),
            dismissed_by=lead_user,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"), {"status": "active"})
        assert response.status_code == 200
        page_obj = response.context["page_obj"]
        ids = [h.pk for h in page_obj.object_list]
        assert ids == [active_hold.pk]

    def test_filter_status_dismissed(self, client, super_admin_user, facility, lead_user):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Aktiv",
            created_by=lead_user,
        )
        dismissed_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Aufgehoben",
            created_by=lead_user,
            dismissed_at=timezone.now(),
            dismissed_by=lead_user,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"), {"status": "dismissed"})
        assert response.status_code == 200
        page_obj = response.context["page_obj"]
        ids = [h.pk for h in page_obj.object_list]
        assert ids == [dismissed_hold.pk]

    def test_writes_system_view_audit(self, client, super_admin_user, facility, lead_user):
        """Auch die Legal-Hold-View loggt SYSTEM_VIEW pro Aufruf."""
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"))
        assert response.status_code == 200

        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before + 1
        latest = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).order_by("-timestamp").first()
        assert latest.target_type == "SystemLegalHoldListView"


# ---------------------------------------------------------------------------
# Tier 2: VVT View (Refs #876) — Schmaler Smoke-Test (Konstante in test_vvt.py)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemVVTAccess:
    """``GET /system/vvt/`` — Zugriffsschutz und Smoke-Test."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_vvt"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_vvt"))
        assert response.status_code == 403

    def test_super_admin_ok(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_vvt"))
        assert response.status_code == 200

        # Mindestens 6 Verarbeitungstaetigkeiten im Context.
        activities = response.context["activities"]
        assert len(activities) >= 6

        content = response.content.decode("utf-8", errors="replace")
        # Banner sichtbar.
        assert "facility-übergreifend" in content
        # Druck-Button vorhanden (Print-CSS-MVP).
        assert "system-vvt-print-button" in content

    def test_writes_system_view_audit(self, client, super_admin_user):
        """SYSTEM_VIEW-Audit auch fuer die VVT-View."""
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_vvt"))
        assert response.status_code == 200
        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before + 1
        latest = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).order_by("-timestamp").first()
        assert latest.target_type == "SystemVVTView"
