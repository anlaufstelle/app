"""WebAuthn-/Passkey-Ceremony-URLs unter dem Namespace ``otp_webauthn`` (ADR-032, Refs #1492).

Bewusst NICHT ``include("django_otp_webauthn.urls")``: wir mounten dieselben
URL-Namen (die von den Template-Tags ``render_otp_webauthn_*_scripts`` per
``reverse("otp_webauthn:…")`` aufgeloest werden), ersetzen aber die Complete-/
Begin-Views durch unsere Subklassen (``core.views.mfa_webauthn``) — dort sitzt
der ``mfa_verified``-Glue und der „nur neben TOTP"-Guard. Der Namespace bleibt
``otp_webauthn``, damit das mitgelieferte JS-Bundle unveraendert funktioniert.
"""

from django.urls import path
from django.views.i18n import JavaScriptCatalog
from django_otp_webauthn.views import BeginCredentialAuthenticationView

from core.views.mfa_webauthn import (
    WebAuthnAuthenticationCompleteView,
    WebAuthnRegistrationBeginView,
    WebAuthnRegistrationCompleteView,
)

app_name = "otp_webauthn"

urlpatterns = [
    path(
        "registration/begin/",
        WebAuthnRegistrationBeginView.as_view(),
        name="credential-registration-begin",
    ),
    path(
        "registration/complete/",
        WebAuthnRegistrationCompleteView.as_view(),
        name="credential-registration-complete",
    ),
    path(
        "authentication/begin/",
        BeginCredentialAuthenticationView.as_view(),
        name="credential-authentication-begin",
    ),
    path(
        "authentication/complete/",
        WebAuthnAuthenticationCompleteView.as_view(),
        name="credential-authentication-complete",
    ),
    path(
        "jsi18n/",
        JavaScriptCatalog.as_view(packages=["django_otp_webauthn"]),
        name="js-i18n-catalog",
    ),
]
