"""Tests für Stream B: Auth-Infrastruktur."""

import string

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.test import RequestFactory

from core.middleware.facility_scope import FacilityScopeMiddleware
from core.middleware.password_change import ForcePasswordChangeMiddleware
from core.models import AuditLog, User
from core.services.password import generate_initial_password
from core.views.mixins import AdminRequiredMixin, LeadOrAdminRequiredMixin, StaffRequiredMixin

# --- Fixtures ---


@pytest.fixture
def rf():
    return RequestFactory()


# --- B.1: FacilityScopeMiddleware ---


class TestFacilityScopeMiddleware:
    def test_authenticated_user_gets_facility(self, rf, staff_user):
        request = rf.get("/")
        request.user = staff_user
        middleware = FacilityScopeMiddleware(lambda r: r)
        middleware(request)
        assert request.current_facility == staff_user.facility

    def test_anonymous_user_gets_none(self, rf, db):
        request = rf.get("/")
        request.user = AnonymousUser()
        middleware = FacilityScopeMiddleware(lambda r: r)
        middleware(request)
        assert request.current_facility is None

    def test_user_without_facility_gets_none(self, rf, db):
        user = User.objects.create_user(username="nofacility", role=User.Role.STAFF)
        request = rf.get("/")
        request.user = user
        middleware = FacilityScopeMiddleware(lambda r: r)
        middleware(request)
        assert request.current_facility is None


# --- B.2: Audit-Signals ---


class TestAuditSignals:
    def test_login_creates_audit_entry(self, rf, staff_user):
        request = rf.get("/")
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        user_logged_in.send(sender=User, request=request, user=staff_user)
        entry = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN).first()
        assert entry is not None
        assert entry.facility == staff_user.facility
        assert entry.ip_address == "127.0.0.1"

    def test_logout_creates_audit_entry(self, rf, staff_user):
        request = rf.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        user_logged_out.send(sender=User, request=request, user=staff_user)
        entry = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGOUT).first()
        assert entry is not None
        assert entry.ip_address == "10.0.0.1"

    def test_login_failed_creates_audit_entry(self, rf, staff_user):
        request = rf.get("/")
        request.META["REMOTE_ADDR"] = "192.168.1.1"
        user_login_failed.send(
            sender=User,
            credentials={"username": staff_user.username},
            request=request,
        )
        entry = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED).first()
        assert entry is not None
        assert entry.detail["username"] == staff_user.username

    def test_login_failed_unknown_user_creates_entry(self, rf, db):
        """AuditLog wird auch bei unbekanntem User erstellt (ohne Facility)."""
        request = rf.get("/")
        request.META["REMOTE_ADDR"] = "1.2.3.4"
        user_login_failed.send(
            sender=User,
            credentials={"username": "nonexistent"},
            request=request,
        )
        entry = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED).first()
        assert entry is not None
        assert entry.facility is None
        assert entry.detail["username"] == "nonexistent"

    def test_logout_none_user_no_entry(self, rf, db):
        """Kein AuditLog bei Logout ohne User."""
        request = rf.get("/")
        user_logged_out.send(sender=User, request=request, user=None)
        assert AuditLog.objects.filter(action=AuditLog.Action.LOGOUT).count() == 0

    def test_x_forwarded_for_header(self, rf, staff_user):
        request = rf.get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.50, 70.41.3.18"
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        user_logged_in.send(sender=User, request=request, user=staff_user)
        entry = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN).first()
        assert entry.ip_address == "70.41.3.18"


# --- B.4: Rollen-Mixins ---


class TestRoleMixins:
    """Testet die test_func()-Methoden der Mixins direkt."""

    def _make_view(self, mixin_class, user, rf):
        class DummyView(mixin_class):
            pass

        view = DummyView()
        request = rf.get("/")
        request.user = user
        view.request = request
        return view

    def test_staff_mixin_allows_staff(self, rf, staff_user):
        view = self._make_view(StaffRequiredMixin, staff_user, rf)
        assert view.test_func() is True

    def test_staff_mixin_allows_lead(self, rf, lead_user):
        view = self._make_view(StaffRequiredMixin, lead_user, rf)
        assert view.test_func() is True

    def test_staff_mixin_allows_admin(self, rf, admin_user):
        view = self._make_view(StaffRequiredMixin, admin_user, rf)
        assert view.test_func() is True

    def test_staff_mixin_denies_assistant(self, rf, assistant_user):
        view = self._make_view(StaffRequiredMixin, assistant_user, rf)
        assert view.test_func() is False

    def test_lead_mixin_allows_lead(self, rf, lead_user):
        view = self._make_view(LeadOrAdminRequiredMixin, lead_user, rf)
        assert view.test_func() is True

    def test_lead_mixin_allows_admin(self, rf, admin_user):
        view = self._make_view(LeadOrAdminRequiredMixin, admin_user, rf)
        assert view.test_func() is True

    def test_lead_mixin_denies_staff(self, rf, staff_user):
        view = self._make_view(LeadOrAdminRequiredMixin, staff_user, rf)
        assert view.test_func() is False

    def test_admin_mixin_allows_admin(self, rf, admin_user):
        view = self._make_view(AdminRequiredMixin, admin_user, rf)
        assert view.test_func() is True

    def test_admin_mixin_denies_lead(self, rf, lead_user):
        view = self._make_view(AdminRequiredMixin, lead_user, rf)
        assert view.test_func() is False

    def test_admin_mixin_denies_staff(self, rf, staff_user):
        view = self._make_view(AdminRequiredMixin, staff_user, rf)
        assert view.test_func() is False


# --- B.3: Login/Logout Views ---


@pytest.mark.django_db
class TestLoginLogout:
    def test_login_page_loads(self, client):
        response = client.get("/login/")
        assert response.status_code == 200

    def test_login_success_redirects(self, client, staff_user):
        response = client.post("/login/", {"username": "teststaff", "password": "testpass123"})
        assert response.status_code == 302
        assert response.url == "/"

    def test_login_failure_shows_form(self, client, staff_user):
        response = client.post("/login/", {"username": "teststaff", "password": "wrong"})
        assert response.status_code == 200

    def test_logout_redirects_to_login(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        response = client.post("/logout/")
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_password_change_page_loads(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        response = client.get("/password-change/")
        assert response.status_code == 200

    def test_password_change_success(self, client, staff_user):
        staff_user.must_change_password = True
        staff_user.save()
        client.login(username="teststaff", password="testpass123")
        response = client.post(
            "/password-change/",
            {
                "old_password": "testpass123",
                "new_password1": "newSecurePass42",
                "new_password2": "newSecurePass42",
            },
        )
        assert response.status_code == 302
        assert response.url == "/"
        staff_user.refresh_from_db()
        assert staff_user.must_change_password is False


# --- B.5: ForcePasswordChange Middleware ---


class TestForcePasswordChangeMiddleware:
    def test_redirects_when_must_change(self, rf, staff_user):
        staff_user.must_change_password = True
        request = rf.get("/")
        request.user = staff_user
        middleware = ForcePasswordChangeMiddleware(lambda r: r)
        response = middleware(request)
        assert response.status_code == 302
        assert "/password-change/" in response.url

    def test_no_redirect_when_not_required(self, rf, staff_user):
        staff_user.must_change_password = False
        request = rf.get("/")
        request.user = staff_user
        middleware = ForcePasswordChangeMiddleware(lambda r: r)
        response = middleware(request)
        assert response == request

    def test_exempt_urls_not_redirected(self, rf, staff_user):
        staff_user.must_change_password = True
        for url in ["/login/", "/logout/", "/password-change/", "/static/foo.css", "/admin/"]:
            request = rf.get(url)
            request.user = staff_user
            request.path = url
            middleware = ForcePasswordChangeMiddleware(lambda r: r)
            response = middleware(request)
            assert response == request, f"URL {url} sollte exempt sein"

    def test_anonymous_user_not_redirected(self, rf, db):
        request = rf.get("/")
        request.user = AnonymousUser()
        middleware = ForcePasswordChangeMiddleware(lambda r: r)
        response = middleware(request)
        assert response == request


# --- B.6: Auto-Initialpasswort ---


class TestGenerateInitialPassword:
    def test_default_length(self):
        password = generate_initial_password()
        assert len(password) == 12

    def test_custom_length(self):
        password = generate_initial_password(length=20)
        assert len(password) == 20

    def test_contains_only_alphanumeric(self):
        password = generate_initial_password()
        valid_chars = string.ascii_letters + string.digits
        assert all(c in valid_chars for c in password)

    def test_generates_unique_passwords(self):
        passwords = {generate_initial_password() for _ in range(50)}
        assert len(passwords) == 50


# --- B.7: Rate-Limiting ---


@pytest.mark.django_db
class TestRateLimiting:
    @pytest.fixture(autouse=True)
    def _enable_ratelimit(self, settings):
        """Rate-Limiting explizit aktivieren (in Test-Settings deaktiviert)."""
        settings.RATELIMIT_ENABLE = True

    def test_rate_limit_blocks_after_threshold(self, client, staff_user):
        """Nach 5 fehlerhaften POST-Requests von derselben IP → 403."""
        for i in range(6):
            response = client.post(
                "/login/",
                {"username": "teststaff", "password": "wrong"},
                REMOTE_ADDR="10.99.99.99",
            )
        assert response.status_code == 403
