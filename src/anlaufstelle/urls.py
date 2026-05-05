from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, reverse_lazy

from core.views.auth import (
    CustomLoginView,
    CustomLogoutView,
    CustomPasswordChangeView,
    OfflineKeySaltView,
    RateLimitedPasswordResetView,
    set_user_language,
)
from core.views.csp_report import CSPReportView
from core.views.health import HealthView
from core.views.mfa import (
    MFABackupCodesView,
    MFADisableView,
    MFARegenerateBackupCodesView,
    MFASettingsView,
    MFASetupView,
    MFAVerifyView,
)
from core.views.pwa import ManifestView, OfflineFallbackView, ServiceWorkerView
from core.views.sudo_mode import SudoModeView

urlpatterns = [
    path("admin-mgmt/", admin.site.urls),
    path("i18n/setlang/", set_user_language, name="set_language"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("health/", HealthView.as_view(), name="health"),
    path("csp-report/", CSPReportView.as_view(), name="csp_report"),
    path("login/", CustomLoginView.as_view(), name="login"),
    path("logout/", CustomLogoutView.as_view(), name="logout"),
    path("password-change/", CustomPasswordChangeView.as_view(), name="password_change"),
    path("auth/offline-key-salt/", OfflineKeySaltView.as_view(), name="offline_key_salt"),
    # Two-Factor Authentication (TOTP)
    path("mfa/setup/", MFASetupView.as_view(), name="mfa_setup"),
    path("mfa/verify/", MFAVerifyView.as_view(), name="mfa_verify"),
    path("mfa/settings/", MFASettingsView.as_view(), name="mfa_settings"),
    path("mfa/disable/", MFADisableView.as_view(), name="mfa_disable"),
    path("mfa/backup-codes/", MFABackupCodesView.as_view(), name="mfa_backup_codes"),
    path(
        "mfa/backup-codes/regenerate/",
        MFARegenerateBackupCodesView.as_view(),
        name="mfa_backup_codes_regenerate",
    ),
    # Password Reset
    path(
        "password-reset/",
        RateLimitedPasswordResetView.as_view(
            template_name="registration/password_reset_form.html",
            email_template_name="registration/password_reset_email.html",
            subject_template_name="registration/password_reset_subject.txt",
            success_url=reverse_lazy("password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
    path("sw.js", ServiceWorkerView.as_view(), name="service_worker"),
    path("manifest.json", ManifestView.as_view(), name="manifest"),
    path("offline/", OfflineFallbackView.as_view(), name="offline_fallback"),
    path("sudo/", SudoModeView.as_view(), name="sudo_mode"),
    path("", include("core.urls")),
]
