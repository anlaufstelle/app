"""Audit logging via Django auth signals."""

import logging

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from core.models import AuditLog

logger = logging.getLogger(__name__)


def get_client_ip(request):
    """Extract the client IP from the request.

    Nutzt ``settings.TRUSTED_PROXY_HOPS`` für die Interpretation von
    ``X-Forwarded-For``:

    * ``0`` → ``REMOTE_ADDR`` direkt verwenden (spoofing-sicher).
    * ``N >= 1`` → den N-ten Eintrag von rechts aus ``X-Forwarded-For`` nehmen.
      Bei N=1 (Caddy-only) entspricht das ``split(",")[-1]``; bei N=2
      (z.B. CDN → Caddy) ``split(",")[-2]`` usw.

    Fallback: Sind weniger Einträge als ``TRUSTED_PROXY_HOPS`` vorhanden oder ist
    der Header leer/fehlt, wird ``REMOTE_ADDR`` zurückgegeben.
    """
    if request is None:
        return None

    trusted_hops = getattr(settings, "TRUSTED_PROXY_HOPS", 1)
    remote_addr = request.META.get("REMOTE_ADDR")

    if trusted_hops <= 0:
        return remote_addr

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if not forwarded_for:
        return remote_addr

    parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    if len(parts) < trusted_hops:
        # Weniger Hops als erwartet — Header könnte unvollständig oder
        # manipuliert sein. Fallback auf REMOTE_ADDR.
        return remote_addr

    return parts[-trusted_hops]


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    """Create an audit entry on successful login."""
    facility = getattr(user, "facility", None)
    if facility is None:
        logger.warning("Login ohne Facility: user=%s", user.username)
        return
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.LOGIN,
        ip_address=get_client_ip(request),
    )


@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    """Create an audit entry on logout."""
    if user is None:
        return
    facility = getattr(user, "facility", None)
    if facility is None:
        return
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.LOGOUT,
        ip_address=get_client_ip(request),
    )


@receiver(user_login_failed)
def on_user_login_failed(sender, credentials, request, **kwargs):
    """Create an audit entry on failed login."""
    from core.models import User

    username = credentials.get("username", "")
    ip_address = get_client_ip(request)

    # Try to find the user to determine the facility
    try:
        user = User.objects.select_related("facility").get(username=username)
        facility = user.facility
    except User.DoesNotExist:
        user = None
        facility = None

    if facility is None:
        logger.info("Login fehlgeschlagen ohne Facility: username=%s", username)
        AuditLog.objects.create(
            user=user,
            action=AuditLog.Action.LOGIN_FAILED,
            detail={"message": "Fehlgeschlagener Login-Versuch", "username": username},
            ip_address=ip_address,
        )
        return

    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.LOGIN_FAILED,
        detail={"message": "Fehlgeschlagener Login-Versuch", "username": username},
        ip_address=ip_address,
    )
