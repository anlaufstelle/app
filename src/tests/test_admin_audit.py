"""Admin: Fachobjekte read-only (AUTHZ-1) + Cross-Facility-Read-Audit (AUTHZ-2).

Refs #1341 (Tracker):

- **AUTHZ-1** — Client/Case/Event/WorkItem sind im Django-Admin strikt
  read-only. Direktes Schreiben umginge die Service-Invarianten (Feld-
  Verschlüsselung, EventHistory-Diff, Vier-Augen-Löschung, Legal-Hold/
  Retention) und das Domänen-AuditLog. ``has_add/change/delete_permission``
  liefern ``False``; ein POST persistiert nichts.
- **AUTHZ-2** — Öffnet ein super_admin eine facility-übergreifende Client-/
  Fall-/Ereignis-Ansicht (Change-View oder Changelist), wird genau **ein**
  ``SYSTEM_VIEW``-AuditLog geschrieben — analog zu ``/system/``
  (``SystemAuditMixin``). Ein same-facility-Read durch einen facility_admin
  erzeugt **keinen** Audit-Spam (sein Queryset ist bereits facility-gescopt).
"""

from __future__ import annotations

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from core.admin.clients import CaseAdmin, ClientAdmin
from core.admin.events import EventAdmin
from core.admin.workflow import WorkItemAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import AuditLog, Case, Client, Event, WorkItem

pytestmark = pytest.mark.django_db

SYSTEM_VIEW = AuditLog.Action.SYSTEM_VIEW


@pytest.fixture
def rf():
    return RequestFactory()


def _request(rf, user, method="get"):
    request = getattr(rf, method)("/")
    request.user = user
    request.current_facility = getattr(user, "facility", None)
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.fixture
def client_in_other_facility(other_facility, staff_user):
    return Client.objects.create(
        facility=other_facility,
        contact_stage=Client.ContactStage.QUALIFIED,
        pseudonym="Other-QU-1341",
        created_by=staff_user,
    )


# ---------------------------------------------------------------------------
# (a) AUTHZ-1 — Fachobjekte strikt read-only
# ---------------------------------------------------------------------------


class TestDomainAdminsReadOnly:
    """Add/Change/Delete auf Fachobjekten ist im Admin gesperrt, View bleibt."""

    _CASES = [
        (ClientAdmin, Client),
        (CaseAdmin, Case),
        (EventAdmin, Event),
        (WorkItemAdmin, WorkItem),
    ]

    @pytest.mark.parametrize("admin_cls,model", _CASES)
    def test_super_admin_cannot_write(self, rf, super_admin_user, admin_cls, model):
        admin = admin_cls(model, anlaufstelle_admin_site)
        req = _request(rf, super_admin_user)
        assert admin.has_add_permission(req) is False
        assert admin.has_change_permission(req) is False
        assert admin.has_delete_permission(req) is False
        # View bleibt erlaubt (Read-Only-Sicht).
        assert admin.has_view_permission(req) is True

    @pytest.mark.parametrize("admin_cls,model", _CASES)
    def test_facility_admin_cannot_write(self, rf, admin_user, admin_cls, model):
        admin = admin_cls(model, anlaufstelle_admin_site)
        req = _request(rf, admin_user)
        assert admin.has_add_permission(req) is False
        assert admin.has_change_permission(req) is False
        assert admin.has_delete_permission(req) is False
        assert admin.has_view_permission(req) is True

    def test_post_change_does_not_persist(self, client, super_admin_user, client_identified):
        """POST auf die Client-Change-View ändert nichts (403/302, keine Persistenz)."""
        client.force_login(super_admin_user)
        url = f"/admin-mgmt/core/client/{client_identified.pk}/change/"
        response = client.post(url, {"pseudonym": "MANIPULIERT", "contact_stage": "identified"})
        assert response.status_code in (403, 302)
        client_identified.refresh_from_db()
        assert client_identified.pseudonym == "Test-ID-01"

    def test_add_view_is_forbidden(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get("/admin-mgmt/core/client/add/", follow=False)
        # Read-Only-Admin -> Add ist verboten (403) bzw. Redirect.
        assert response.status_code in (403, 302)


# ---------------------------------------------------------------------------
# (b) AUTHZ-2 — Cross-Facility-Read als super_admin erzeugt genau einen Audit
# ---------------------------------------------------------------------------


class TestSuperAdminReadAudit:
    def test_change_view_creates_single_system_view_audit(self, client, super_admin_user, client_in_other_facility):
        client.force_login(super_admin_user)
        pk = str(client_in_other_facility.pk)
        response = client.get(f"/admin-mgmt/core/client/{pk}/change/", follow=False)
        assert response.status_code == 200
        logs = AuditLog.objects.filter(action=SYSTEM_VIEW, target_type="Client", target_id=pk)
        assert logs.count() == 1
        entry = logs.get()
        assert entry.facility is None
        assert entry.user_id == super_admin_user.pk

    def test_changelist_creates_single_collective_audit(
        self, client, super_admin_user, client_qualified, client_in_other_facility
    ):
        client.force_login(super_admin_user)
        response = client.get("/admin-mgmt/core/client/", follow=False)
        assert response.status_code == 200
        # Sammel-Audit: target_type="Client", ohne target_id (nicht pro Zeile).
        collective = AuditLog.objects.filter(action=SYSTEM_VIEW, target_type="Client", target_id="")
        assert collective.count() == 1


# ---------------------------------------------------------------------------
# (c) AUTHZ-2 — same-facility facility_admin erzeugt keinen Audit-Spam
# ---------------------------------------------------------------------------


class TestFacilityAdminReadNoSpam:
    def test_same_facility_reads_create_no_audit(self, client, admin_user, client_qualified):
        client.force_login(admin_user)
        list_resp = client.get("/admin-mgmt/core/client/", follow=False)
        change_resp = client.get(f"/admin-mgmt/core/client/{client_qualified.pk}/change/", follow=False)
        assert list_resp.status_code == 200
        assert change_resp.status_code == 200
        assert AuditLog.objects.filter(action=SYSTEM_VIEW).count() == 0
