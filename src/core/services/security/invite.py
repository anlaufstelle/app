"""User-Invite-Service: Token-basierter Invite-Flow.

Erzeugt beim Anlegen neuer User-Konten einen Setup-Token und versendet eine
Einladungs-E-Mail mit Setup-Link. Der Link nutzt die eigene
`invite_confirm`-Route (dieselbe Passwort-Set-Mechanik wie „Passwort vergessen",
aber ein eigener Token-Generator + eigene Gültigkeit).

Gültigkeit (L4, Refs #1375): Ein Invite-Token läuft nach `INVITE_TOKEN_TIMEOUT`
ab (Default 3 Tage) — bewusst LÄNGER als `PASSWORD_RESET_TIMEOUT` (kurzlebig,
Stunden). Ein neu eingeladener Mitarbeitender braucht realistisch Tage, um den
Link zu öffnen; ein Passwort-Reset-Token soll dagegen kurzlebig sein. Beide
Gültigkeiten sind über getrennte Token-Generatoren entkoppelt — früher teilten
sich Invite und Reset `default_token_generator` + `PASSWORD_RESET_TIMEOUT`,
sodass ein kurzer Reset-Timeout die Invites mitverkürzt hätte.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.utils.encoding import force_bytes
from django.utils.http import base36_to_int, urlsafe_base64_encode

logger = logging.getLogger(__name__)


class InviteTokenGenerator(PasswordResetTokenGenerator):
    """Setup-Token für Einladungen mit eigenem, längerem Ablauf (L4, Refs #1375).

    Identisch zum Passwort-Reset-Token (HMAC über pk + Passwort-Hash +
    last_login → automatisch einmalig, invalidiert nach dem ersten Setzen des
    Passworts), aber mit zwei Unterschieden:

    * eigenes ``key_salt`` — ein Invite-Token ist NICHT auf der
      Passwort-Reset-Route verwendbar und umgekehrt (saubere Trennung);
    * eigener Ablauf ``INVITE_TOKEN_TIMEOUT`` statt ``PASSWORD_RESET_TIMEOUT``.

    ``check_token`` ist bewusst eine schlanke Kopie der Basis-Implementierung
    (Django 6, `contrib/auth/tokens.py`) — nur der Timeout-Wert weicht ab. Das
    Token-Format der Basisklasse ist seit Jahren stabil.
    """

    key_salt = "core.services.security.invite.InviteTokenGenerator"

    def check_token(self, user, token):
        if not (user and token):
            return False
        try:
            ts_b36, _ = token.split("-")
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False

        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
                break
        else:
            return False

        timeout = getattr(settings, "INVITE_TOKEN_TIMEOUT", settings.PASSWORD_RESET_TIMEOUT)
        return (self._num_seconds(self._now()) - ts) <= timeout


# Modul-Singleton (analog Djangos ``default_token_generator``).
invite_token_generator = InviteTokenGenerator()


def build_invite_url(user, request=None) -> str:
    """Erzeugt den Setup-Link (uidb64 + token) für den Invite-Flow.

    Wenn `request` angegeben ist, wird eine absolute URL (inkl. Host und Scheme)
    zurückgegeben, andernfalls eine relative URL.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = invite_token_generator.make_token(user)
    path = reverse("invite_confirm", kwargs={"uidb64": uid, "token": token})
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
    token = invite_token_generator.make_token(user)

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
