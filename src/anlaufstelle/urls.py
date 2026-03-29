from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, reverse_lazy

from core.views.auth import CustomLoginView, CustomLogoutView, CustomPasswordChangeView
from core.views.health import HealthView
from core.views.pwa import ManifestView, ServiceWorkerView

urlpatterns = [
    path("admin-mgmt/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("health/", HealthView.as_view(), name="health"),
    path("login/", CustomLoginView.as_view(), name="login"),
    path("logout/", CustomLogoutView.as_view(), name="logout"),
    path("password-change/", CustomPasswordChangeView.as_view(), name="password_change"),
    # Password Reset
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
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
    path("", include("core.urls")),
]
