"""Tests für Auth-Middleware und Rollen."""

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
        for url in ["/login/", "/logout/", "/password-change/", "/static/foo.css"]:
            request = rf.get(url)
            request.user = staff_user
            request.path = url
            middleware = ForcePasswordChangeMiddleware(lambda r: r)
            response = middleware(request)
            assert response == request, f"URL {url} sollte exempt sein"

    def test_admin_mgmt_not_exempt_from_password_change(self, rf, staff_user):
        """Admin-UI darf nicht als Bypass für erzwungene Passwort-Änderung
        dienen (Refs #582)."""
        staff_user.must_change_password = True
        request = rf.get("/admin-mgmt/")
        request.user = staff_user
        request.path = "/admin-mgmt/"
        middleware = ForcePasswordChangeMiddleware(lambda r: r)
        response = middleware(request)
        assert response.status_code == 302
        assert "/password-change/" in response.url

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
        """Rate-Limiting explizit aktivieren (in Test-Settings deaktiviert).

        Cache muss pro Test geleert werden — django-ratelimit nutzt den
        Django-Cache (LocMem), der zwischen Tests persistiert und sonst
        Zähler aus vorherigen Tests mitschleppt.
        """
        from django.core.cache import cache

        settings.RATELIMIT_ENABLE = True
        cache.clear()
        yield
        cache.clear()

    def test_rate_limit_blocks_after_threshold(self, client, staff_user):
        """Nach 5 fehlerhaften POST-Requests von derselben IP → 403."""
        for i in range(6):
            response = client.post(
                "/login/",
                {"username": "teststaff", "password": "wrong"},
                REMOTE_ADDR="10.99.99.99",
            )
        assert response.status_code == 403

    def test_password_reset_post_rate_limited(self, client):
        """Nach 5 POST-Requests auf password-reset von derselben IP → 403."""
        for i in range(6):
            response = client.post(
                "/password-reset/",
                {"email": "test@example.com"},
                REMOTE_ADDR="10.88.88.88",
            )
        assert response.status_code == 403

    def test_rate_limit_per_username_blocks_distributed_brute_force(self, client, staff_user):
        """Refs #598 S-3: Nach 10 fehlgeschlagenen Login-Versuchen auf den
        gleichen Usernamen — auch von unterschiedlichen IPs — greift der
        User-basierte Ratelimit (10/h) und Versuch 11 liefert 403.

        IP-Rate-Limit wird durch rotierende REMOTE_ADDR umgangen (simuliert
        Botnet). User-Rate-Limit muss trotzdem greifen.
        """
        # 10 Versuche mit jeweils anderer IP — IP-Limit greift nie.
        for i in range(10):
            response = client.post(
                "/login/",
                {"username": "teststaff", "password": "wrong"},
                REMOTE_ADDR=f"10.77.77.{i + 1}",
            )
            assert response.status_code == 200, (
                f"Versuch {i + 1}: unerwarteter Status {response.status_code} "
                f"(User-Limit sollte erst beim 11. Versuch greifen)."
            )
        # 11. Versuch: User-Limit greift.
        response = client.post(
            "/login/",
            {"username": "teststaff", "password": "wrong"},
            REMOTE_ADDR="10.77.77.250",
        )
        assert response.status_code == 403

    def test_rate_limit_per_username_is_case_insensitive(self, client, staff_user):
        """Die Lambda normalisiert Username (lowercase + strip) — sonst wäre
        ``Alice`` vs. ``alice`` ein trivialer Bypass."""
        for i in range(10):
            response = client.post(
                "/login/",
                {"username": "TESTSTAFF", "password": "wrong"},
                REMOTE_ADDR=f"10.66.66.{i + 1}",
            )
            assert response.status_code == 200
        # Gleicher User, aber andere Schreibweise → muss in denselben Bucket.
        response = client.post(
            "/login/",
            {"username": " teststaff ", "password": "wrong"},
            REMOTE_ADDR="10.66.66.250",
        )
        assert response.status_code == 403


# --- B.8: Account-Lockout (Refs #612) ---


@pytest.mark.django_db
class TestLoginLockoutService:
    def _failed(self, user):
        return AuditLog.objects.create(
            facility=user.facility,
            user=user,
            action=AuditLog.Action.LOGIN_FAILED,
            detail={"username": user.username},
        )

    def test_not_locked_without_failed_attempts(self, staff_user):
        from core.services.login_lockout import is_locked

        assert is_locked(staff_user) is False

    def test_locked_after_threshold(self, staff_user):
        from core.services.login_lockout import LOCKOUT_THRESHOLD, is_locked

        for _ in range(LOCKOUT_THRESHOLD):
            self._failed(staff_user)
        assert is_locked(staff_user) is True

    def test_not_locked_after_unlock(self, staff_user, admin_user):
        from core.services.login_lockout import LOCKOUT_THRESHOLD, is_locked, unlock

        for _ in range(LOCKOUT_THRESHOLD):
            self._failed(staff_user)
        assert is_locked(staff_user) is True
        unlock(staff_user, unlocked_by=admin_user)
        assert is_locked(staff_user) is False

    def test_unlock_only_affects_prior_failures(self, staff_user, admin_user):
        from core.services.login_lockout import LOCKOUT_THRESHOLD, is_locked, unlock

        for _ in range(LOCKOUT_THRESHOLD):
            self._failed(staff_user)
        unlock(staff_user, unlocked_by=admin_user)
        # Neue Fehlversuche nach Unlock zählen wieder bis zum Threshold.
        for _ in range(LOCKOUT_THRESHOLD - 1):
            self._failed(staff_user)
        assert is_locked(staff_user) is False
        self._failed(staff_user)
        assert is_locked(staff_user) is True

    def test_old_failures_outside_window_ignored(self, staff_user):
        from datetime import timedelta
        from unittest.mock import patch

        from django.utils import timezone

        from core.services import login_lockout
        from core.services.login_lockout import LOCKOUT_THRESHOLD, LOCKOUT_WINDOW, is_locked

        for _ in range(LOCKOUT_THRESHOLD + 2):
            self._failed(staff_user)
        # Fenster in die Zukunft verschieben → alle bisherigen Fehlversuche
        # fallen aus dem Tracking-Fenster. AuditLog-Timestamps sind append-only
        # (DB-Trigger), daher verschieben wir NOW() statt der Entries.
        future = timezone.now() + LOCKOUT_WINDOW + timedelta(minutes=5)
        with patch.object(login_lockout, "timezone") as mock_tz:
            mock_tz.now.return_value = future
            assert is_locked(staff_user) is False

    def test_none_user_is_not_locked(self, db):
        from core.services.login_lockout import is_locked

        assert is_locked(None) is False

    def test_lockout_is_per_user(self, staff_user, lead_user):
        from core.services.login_lockout import LOCKOUT_THRESHOLD, is_locked

        for _ in range(LOCKOUT_THRESHOLD):
            self._failed(staff_user)
        assert is_locked(staff_user) is True
        assert is_locked(lead_user) is False


@pytest.mark.django_db
class TestLoginLockoutIntegration:
    """Korrektes Passwort wird bei gesperrtem Account abgewiesen."""

    def test_correct_password_blocked_when_locked(self, client, staff_user):
        from core.services.login_lockout import LOCKOUT_THRESHOLD

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=staff_user.facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={"username": staff_user.username},
            )
        response = client.post(
            "/login/",
            {"username": "teststaff", "password": "testpass123"},
        )
        # Kein Redirect → Form wird mit Fehler erneut gerendert.
        assert response.status_code == 200
        assert response.wsgi_request.user.is_anonymous, "User darf nicht eingeloggt sein"
        # Eigener AuditLog-Eintrag mit reason=locked.
        locked_entry = AuditLog.objects.filter(
            user=staff_user,
            action=AuditLog.Action.LOGIN_FAILED,
            detail__reason="locked",
        ).first()
        assert locked_entry is not None

    def test_correct_password_works_after_unlock(self, client, staff_user, admin_user):
        from core.services.login_lockout import LOCKOUT_THRESHOLD, unlock

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=staff_user.facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={"username": staff_user.username},
            )
        unlock(staff_user, unlocked_by=admin_user)
        response = client.post(
            "/login/",
            {"username": "teststaff", "password": "testpass123"},
        )
        assert response.status_code == 302
        assert response.url == "/"
