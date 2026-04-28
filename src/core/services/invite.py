"""User-Invite-Service: Token-basierter Invite-Flow.

Erzeugt beim Anlegen neuer User-Konten einen Password-Reset-Token (Django
`default_token_generator`) und versendet eine Einladungs-E-Mail mit Setup-Link.
Der Link nutzt die bestehende `password_reset_confirm`-Route — dieselbe
Mechanik wie beim normalen "Passwort vergessen"-Flow.

Gültigkeit: `PASSWORD_RESET_TIMEOUT` (Django-Default 3 Tage, bei Bedarf per
Settings erhöhbar auf 7 Tage, siehe Issue #528).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)


def build_invite_url(user, request=None) -> str:
    """Erzeugt den Setup-Link (uidb64 + token) für den Invite-Flow.

    Wenn `request` angegeben ist, wird eine absolute URL (inkl. Host und Scheme)
    zurückgegeben, andernfalls eine relative URL.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def send_invite_email(user, request=None) -> bool:
    """Versendet die Einladungs-E-Mail an `user.email`.

    Rückgabe: True bei erfolgreichem Versand, False falls `user.email` leer.
    Exceptions des Mail-Backends werden geloggt, aber nicht unterdrückt.
    """
    if not user.email:
        # Keine PII im Log: Nur user-pk, nicht username/email.
        # extra={"user_pk": ...} landet im JsonFormatter-Record separat und
        # wird vom PII-Scrubber nicht aus msg/args gezogen. Refs #637.
        logger.warning("Invite-Mail nicht versendet: User ohne E-Mail", extra={"user_pk": user.pk})
        return False

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    if request is not None:
        protocol = "https" if request.is_secure() else "http"
        domain = request.get_host()
    else:
        protocol = "https"
        domain = getattr(settings, "INVITE_DEFAULT_DOMAIN", "localhost")

    context = {
        "user": user,
        "uid": uid,
        "token": token,
        "protocol": protocol,
        "domain": domain,
    }

    subject = render_to_string("registration/invite_subject.txt", context).strip()
    body = render_to_string("registration/invite_email.html", context)

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@anlaufstelle.app")

    send_mail(
        subject=subject,
        message=body,
        from_email=from_email,
        recipient_list=[user.email],
        fail_silently=False,
    )
    # Keine PII im Log-Message — user_pk in extra, kein username/email.
    # Refs #637.
    logger.info("Invite-Mail versendet", extra={"user_pk": user.pk})
    return True
