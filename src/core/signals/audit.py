"""Audit logging via Django auth signals."""

import logging

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db import connection
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.models import AuditLog

logger = logging.getLogger(__name__)


def _set_session_vars(facility, is_super_admin=False):
    """Setzt ``app.current_facility_id`` und ``app.is_super_admin`` fuer die
    laufende Postgres-Session.

    Notwendig fuer Auth-Signal-Handler (login/logout/login_failed): die
    feuern zwischen FacilityScopeMiddleware-Pre und -Post, in einem Request,
    der zu Beginn als ``anonymous`` registriert war (beide Settings='').
    Damit der nachfolgende ``AuditLog``-INSERT die RLS-WITH-CHECK-Policy
    erfuellt, muessen die Settings hier auf den jetzt bekannten User
    aktualisiert werden. Refs #863, #867.

    - ``facility=None`` (System-Event, NULL-AuditLog) -> facility_id=''.
      Der WITH-CHECK-Branch ``facility_id IS NULL`` (Migration 0083)
      greift dann.
    - ``is_super_admin=True`` -> ``app.is_super_admin='true'``. Der
      WITH-CHECK-Branch ``current_setting('app.is_super_admin', true) =
      'true'`` (Migration 0085) greift dann fuer non-NULL facility.
    """
    if connection.vendor != "postgresql":
        return
    facility_id = str(facility.pk) if facility is not None else ""
    is_super = "true" if is_super_admin else ""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_facility_id', %s, false), "
            "       set_config('app.is_super_admin', %s, false)",
            [facility_id, is_super],
        )


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
    """Create an audit entry on successful login.

    Refs #867: super_admin hat kein ``facility`` — AuditLog-Eintrag wird
    trotzdem geschrieben (facility=NULL). Migration 0083 erlaubt INSERT
    mit NULL-facility via WITH CHECK.
    """
    facility = getattr(user, "facility", None)
    is_super = bool(getattr(user, "is_super_admin", False))
    _set_session_vars(facility, is_super_admin=is_super)
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.LOGIN,
        ip_address=get_client_ip(request),
    )


@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    """Create an audit entry on logout.

    Refs #867: super_admin (facility=None) wird ebenfalls geloggt.
    """
    if user is None:
        return
    facility = getattr(user, "facility", None)
    is_super = bool(getattr(user, "is_super_admin", False))
    _set_session_vars(facility, is_super_admin=is_super)
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

    # Try to find the user to determine the facility / super_admin status
    try:
        user = User.objects.select_related("facility").get(username=username)
        facility = user.facility
        is_super = bool(getattr(user, "is_super_admin", False))
    except User.DoesNotExist:
        user = None
        facility = None
        is_super = False

    # facility kann None sein (unknown user / super_admin) — Settings
    # entsprechend setzen, damit WITH-CHECK greift (NULL-Branch oder
    # is_super_admin-Branch).
    _set_session_vars(facility, is_super_admin=is_super)

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


# --- User-Model: Rollenwechsel + Deaktivierung ----------------------------
#
# Django-Admin mutiert User direkt (ohne Service). pre_save erfasst den alten
# Zustand; post_save diffed und schreibt bei echten Änderungen einen Audit-
# Eintrag. Wir setzen temporäre ``_audit_old_*``-Attribute auf der Instanz —
# die sind request-lokal und überleben nicht über den Signal-Fan-Out hinaus.
from core.models import User  # noqa: E402  (nach AppConfig.ready() geladen)


@receiver(pre_save, sender=User)
def _capture_old_user_state(sender, instance, **kwargs):
    """Snapshot des alten User-Zustands, bevor save() den neuen Zustand schreibt."""
    if not instance.pk:
        instance._audit_old_role = None
        instance._audit_old_is_active = None
        return
    try:
        old = User.objects.only("role", "is_active").get(pk=instance.pk)
    except User.DoesNotExist:
        instance._audit_old_role = None
        instance._audit_old_is_active = None
        return
    instance._audit_old_role = old.role
    instance._audit_old_is_active = old.is_active


@receiver(post_save, sender=User)
def _log_user_role_or_deactivation(sender, instance, created, **kwargs):
    """Vergleicht alten und neuen User-Zustand; loggt Rollenwechsel und
    Deaktivierung (True → False) als eigene AuditLog-Einträge."""
    if created:
        return  # Neu-Anlage wird via Admin-Create-Flow separat auditiert.
    old_role = getattr(instance, "_audit_old_role", None)
    old_is_active = getattr(instance, "_audit_old_is_active", None)

    if old_role is not None and old_role != instance.role:
        AuditLog.objects.create(
            facility=getattr(instance, "facility", None),
            user=instance,
            action=AuditLog.Action.USER_ROLE_CHANGED,
            target_type="User",
            target_id=str(instance.pk),
            detail={
                "old_role": old_role,
                "new_role": instance.role,
                "username": instance.username,
            },
        )

    if old_is_active is True and instance.is_active is False:
        AuditLog.objects.create(
            facility=getattr(instance, "facility", None),
            user=instance,
            action=AuditLog.Action.USER_DEACTIVATED,
            target_type="User",
            target_id=str(instance.pk),
            detail={"username": instance.username},
        )
