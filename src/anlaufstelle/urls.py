from django.contrib.auth import views as auth_views
from django.urls import include, path, reverse_lazy

from core.admin_site import anlaufstelle_admin_site
from core.views.auth import (
    CustomLoginView,
    CustomLogoutView,
    CustomPasswordChangeView,
    CustomPasswordResetConfirmView,
    InviteConfirmView,
    OfflineKeySaltView,
    RateLimitedPasswordResetView,
    set_user_language,
)
from core.views.csp_report import CSPReportView
from core.views.demo_scenarios import DemoOfflineKeyReadyView, DemoScenarioSelectView, DemoScenarioStartView
from core.views.health import HealthView
from core.views.legal import DemoImprintView, DemoPrivacyView
from core.views.mfa import (
    MFABackupCodesView,
    MFADeviceChangeView,
    MFADisableView,
    MFAPasskeyDeleteView,
    MFARegenerateBackupCodesView,
    MFASettingsView,
    MFASetupView,
    MFAVerifyView,
)
from core.views.misc import RobotsTxtView
from core.views.pwa import ManifestView, OfflineFallbackView, ServiceWorkerView
from core.views.sudo_mode import SudoModeView

urlpatterns = [
    path("admin-mgmt/", anlaufstelle_admin_site.urls),
    path("i18n/setlang/", set_user_language, name="set_language"),
    path("i18n/", include([path("setlang/", set_user_language, name="set_language")])),
    path("health/", HealthView.as_view(), name="health"),
    path("robots.txt", RobotsTxtView.as_view(), name="robots_txt"),
    path("csp-report/", CSPReportView.as_view(), name="csp_report"),
    path("login/", CustomLoginView.as_view(), name="login"),
    path("logout/", CustomLogoutView.as_view(), name="logout"),
    path("password-change/", CustomPasswordChangeView.as_view(), name="password_change"),
    path("auth/offline-key-salt/", OfflineKeySaltView.as_view(), name="offline_key_salt"),
    # Two-Factor Authentication (TOTP)
    path("mfa/setup/", MFASetupView.as_view(), name="mfa_setup"),
    path("mfa/change-device/", MFADeviceChangeView.as_view(), name="mfa_device_change"),
    path("mfa/verify/", MFAVerifyView.as_view(), name="mfa_verify"),
    path("mfa/settings/", MFASettingsView.as_view(), name="mfa_settings"),
    path("mfa/disable/", MFADisableView.as_view(), name="mfa_disable"),
    path("mfa/backup-codes/", MFABackupCodesView.as_view(), name="mfa_backup_codes"),
    path(
        "mfa/backup-codes/regenerate/",
        MFARegenerateBackupCodesView.as_view(),
        name="mfa_backup_codes_regenerate",
    ),
    # Passkeys/WebAuthn als zweiter Faktor (ADR-032, Refs #1492).
    path(
        "mfa/passkey/<int:pk>/delete/",
        MFAPasskeyDeleteView.as_view(),
        name="mfa_passkey_delete",
    ),
    path("webauthn/", include("core.webauthn_urls")),
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
        CustomPasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    # L4 (Refs #1375): Eigene Setup-Route fuer Einladungen — gleicher
    # Passwort-Set-Flow wie beim Reset, aber ueber den entkoppelten
    # invite_token_generator (eigener, laengerer Ablauf INVITE_TOKEN_TIMEOUT).
    path(
        "invite/<uidb64>/<token>/",
        InviteConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="invite_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
    # Oeffentliche Rechtstexte: unveraenderte Demo-Templates oder konfigurierte
    # Betreiberdateien; ohne beides 404. Refs #1641, #2076.
    path("impressum/", DemoImprintView.as_view(), name="demo_imprint"),
    path("datenschutzerklaerung/", DemoPrivacyView.as_view(), name="demo_privacy"),
    # Demo-Bereichsauswahl + -wechsel (Refs #2285): nur bei DEMO_MODE erreichbar
    # (404 sonst, dieselbe Doktrin wie die beiden Rechtstexte oben).
    path("demo/", DemoScenarioSelectView.as_view(), name="demo_scenario_select"),
    path("demo/offline-key-ready/", DemoOfflineKeyReadyView.as_view(), name="demo_offline_key_ready"),
    path("demo/start/", DemoScenarioStartView.as_view(), name="demo_scenario_start"),
    path("sw.js", ServiceWorkerView.as_view(), name="service_worker"),
    path("manifest.json", ManifestView.as_view(), name="manifest"),
    path("offline/", OfflineFallbackView.as_view(), name="offline_fallback"),
    path("sudo/", SudoModeView.as_view(), name="sudo_mode"),
    path("", include("core.urls")),
]

# Refs #1354: django_ratelimits ``Ratelimited`` (PermissionDenied-Subklasse)
# wird auf 429 gemappt statt als 403 ausgeliefert — der Offline-Client deutet
# 403 als Rechteentzug und purgt lokale Bundles. Echte 403 bleiben unveraendert.
handler403 = "core.views.errors.permission_denied"
# Refs #1904: adds a non-sensitive reference id (also written to the server
# log) and the configured operator contact to the default 500 page.
handler500 = "core.views.errors.server_error"
