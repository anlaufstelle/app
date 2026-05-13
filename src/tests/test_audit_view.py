"""Unit tests for the AuditLog list view."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog
from core.signals.audit import get_client_ip


def _make_audit_log(facility, user, action=AuditLog.Action.LOGIN, delta_days=0):
    """Helper: create an AuditLog entry, optionally with a backdated timestamp.

    Uses raw SQL INSERT to set the timestamp directly, bypassing the
    immutable trigger (INSERT is allowed, only UPDATE/DELETE are blocked).
    """
    import uuid

    entry_id = uuid.uuid4()
    ts = timezone.now() - timedelta(days=delta_days)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core_auditlog"
            " (id, facility_id, user_id, action, target_type,"
            " target_id, detail, ip_address, timestamp)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [entry_id, facility.pk, user.pk, action, "", "", "{}", None, ts],
        )
    return AuditLog.objects.get(pk=entry_id)


@pytest.mark.django_db
def test_audit_log_list_renders(client, admin_user, facility):
    """Admin can access the audit log and receives HTTP 200."""
    client.force_login(admin_user)
    response = client.get(reverse("core:audit_log"))
    assert response.status_code == 200
    assert "page_obj" in response.context
    assert "action_choices" in response.context
    assert "facility_users" in response.context


@pytest.mark.django_db
def test_audit_log_pagination(client, admin_user, facility):
    """Creating >50 entries results in a paginated response."""
    for _ in range(55):
        AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.LOGIN)

    client.force_login(admin_user)
    response = client.get(reverse("core:audit_log"))
    assert response.status_code == 200

    page_obj = response.context["page_obj"]
    assert page_obj.paginator.num_pages > 1
    assert len(page_obj.object_list) == 50


@pytest.mark.django_db
def test_audit_log_filter_by_action(client, admin_user, facility):
    """Filter ?action=login returns only login entries."""
    AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.LOGIN)
    AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.LOGOUT)
    AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.EXPORT)

    client.force_login(admin_user)
    response = client.get(reverse("core:audit_log") + "?action=login")
    assert response.status_code == 200

    page_obj = response.context["page_obj"]
    actions = [entry.action for entry in page_obj.object_list]
    assert all(a == AuditLog.Action.LOGIN for a in actions)
    assert response.context["filter_action"] == "login"


@pytest.mark.django_db
def test_audit_log_filter_by_date(client, admin_user, facility):
    """Filter by date_from/date_to restricts the result set."""
    # Entry 10 days ago
    old_entry = _make_audit_log(facility, admin_user, delta_days=10)
    # Entry today
    new_entry = _make_audit_log(facility, admin_user, delta_days=0)

    client.force_login(admin_user)

    # Filter: only entries from 5 days ago onwards
    date_from = (timezone.now() - timedelta(days=5)).date().isoformat()
    response = client.get(reverse("core:audit_log") + f"?date_from={date_from}")
    assert response.status_code == 200

    ids = [str(entry.pk) for entry in response.context["page_obj"].object_list]
    assert str(new_entry.pk) in ids
    assert str(old_entry.pk) not in ids


@pytest.mark.django_db
def test_facility_admin_audit_view_excludes_null_facility_entries(client, admin_user, facility):
    """Refs #867: Die facility-scoped Audit-View (``core:audit_log``)
    nutzt ``AuditLog.objects.for_facility(facility)`` — diese Filterung
    auf ``facility=<concrete>`` schliesst NULL-Facility-Eintraege per
    Definition aus.

    Komplement zum ``/system/audit/``-Test: der facility-Admin sieht
    NUR seine eigenen Eintraege; System-Events (NULL-Facility, z.B.
    Pre-Auth-LOGIN_FAILED, SYSTEM_VIEW) bleiben in der Cross-Facility-
    Sicht des super_admin reserviert.
    """
    import uuid

    # Eigener Facility-Eintrag.
    own_entry = AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.LOGIN)

    # NULL-Facility-Eintrag (z.B. Pre-Auth-Loggen). Raw-SQL, weil der
    # Manager-Default-Pfad ``facility`` NICHT explizit auf NULL setzt
    # und WITH-CHECK-Policies in Produktion sonst greifen.
    null_marker = "fac-admin-null-" + uuid.uuid4().hex[:8]
    from django.db import connection as conn

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO core_auditlog (id, facility_id, user_id, action, "
            "target_type, target_id, detail, ip_address, timestamp) "
            "VALUES (%s, NULL, %s, %s, '', %s, '{}', NULL, NOW())",
            [uuid.uuid4(), admin_user.pk, "login_failed", null_marker],
        )

    client.force_login(admin_user)
    response = client.get(reverse("core:audit_log"))
    assert response.status_code == 200

    target_ids = [entry.target_id for entry in response.context["page_obj"].object_list]
    ids = [str(entry.pk) for entry in response.context["page_obj"].object_list]
    assert str(own_entry.pk) in ids, "Facility-Admin sieht eigenen Audit-Eintrag nicht."
    assert null_marker not in target_ids, (
        f"Facility-Admin sieht NULL-Facility-Audit (target_id={null_marker!r}). "
        "Erwartet: NULL-Eintraege sind ausschliesslich in /system/audit/ sichtbar."
    )


class TestGetClientIp:
    """Tests für die konfigurierbare Client-IP-Ermittlung via TRUSTED_PROXY_HOPS."""

    def test_none_request_returns_none(self):
        assert get_client_ip(None) is None

    def test_zero_hops_uses_remote_addr(self, settings):
        """TRUSTED_PROXY_HOPS=0 ignoriert X-Forwarded-For (spoofing-sicher)."""
        settings.TRUSTED_PROXY_HOPS = 0
        rf = RequestFactory()
        request = rf.get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = "1.2.3.4, 5.6.7.8"
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        assert get_client_ip(request) == "10.0.0.1"

    def test_zero_hops_without_forwarded_for(self, settings):
        settings.TRUSTED_PROXY_HOPS = 0
        rf = RequestFactory()
        request = rf.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        assert get_client_ip(request) == "10.0.0.1"

    def test_one_hop_caddy_only(self, settings):
        """TRUSTED_PROXY_HOPS=1 (Default): letzter X-Forwarded-For-Eintrag = Client-IP."""
        settings.TRUSTED_PROXY_HOPS = 1
        rf = RequestFactory()
        request = rf.get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.50, 70.41.3.18"
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        assert get_client_ip(request) == "70.41.3.18"

    def test_one_hop_single_entry(self, settings):
        settings.TRUSTED_PROXY_HOPS = 1
        rf = RequestFactory()
        request = rf.get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.50"
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        assert get_client_ip(request) == "203.0.113.50"

    def test_two_hops_cdn_plus_caddy(self, settings):
        """TRUSTED_PROXY_HOPS=2 (z.B. Cloudflare → Caddy): vorletzter Eintrag = Client-IP."""
        settings.TRUSTED_PROXY_HOPS = 2
        rf = RequestFactory()
        request = rf.get("/")
        # Client: 203.0.113.50, CDN: 70.41.3.18, Caddy: 10.0.0.2
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.50, 70.41.3.18, 10.0.0.2"
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        assert get_client_ip(request) == "70.41.3.18"

    def test_three_hops(self, settings):
        settings.TRUSTED_PROXY_HOPS = 3
        rf = RequestFactory()
        request = rf.get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = "1.1.1.1, 2.2.2.2, 3.3.3.3, 4.4.4.4"
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        assert get_client_ip(request) == "2.2.2.2"

    def test_empty_forwarded_for_falls_back_to_remote_addr(self, settings):
        settings.TRUSTED_PROXY_HOPS = 1
        rf = RequestFactory()
        request = rf.get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = ""
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        assert get_client_ip(request) == "10.0.0.1"

    def test_missing_forwarded_for_falls_back_to_remote_addr(self, settings):
        settings.TRUSTED_PROXY_HOPS = 1
        rf = RequestFactory()
        request = rf.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        assert get_client_ip(request) == "10.0.0.1"

    def test_fewer_hops_than_trusted_falls_back(self, settings):
        """Weniger X-Forwarded-For-Einträge als TRUSTED_PROXY_HOPS → REMOTE_ADDR."""
        settings.TRUSTED_PROXY_HOPS = 3
        rf = RequestFactory()
        request = rf.get("/")
        # Nur zwei Einträge, aber 3 Hops erwartet — Header könnte manipuliert sein.
        request.META["HTTP_X_FORWARDED_FOR"] = "1.1.1.1, 2.2.2.2"
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        assert get_client_ip(request) == "10.0.0.1"

    def test_whitespace_only_entries_ignored(self, settings):
        settings.TRUSTED_PROXY_HOPS = 1
        rf = RequestFactory()
        request = rf.get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = "1.1.1.1,   , 2.2.2.2"
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        assert get_client_ip(request) == "2.2.2.2"


@pytest.mark.django_db
def test_audit_log_htmx_returns_partial(client, admin_user, facility):
    """An HX-Request header causes the view to return the partial table template."""
    AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.LOGIN)

    client.force_login(admin_user)
    response = client.get(
        reverse("core:audit_log"),
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    # The partial template is used, not the full list template
    templates_used = [t.name for t in response.templates]
    assert "core/audit/partials/table.html" in templates_used
    assert "core/audit/list.html" not in templates_used
