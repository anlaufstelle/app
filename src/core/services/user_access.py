"""Einrichtungsgebundene Benutzerverwaltung fuer den Zugaenge-Hub (#1871).

``core_user`` ist absichtlich keine RLS-Tabelle. Deshalb beginnt jeder
oeffentliche Einstieg dieses Moduls mit einem expliziten Facility-Filter und
schliesst ``super_admin`` aus; Views greifen nie direkt per PK auf ``User`` zu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog, User
from core.services.audit import audit_event
from core.services.security import LOCKOUT_THRESHOLD, LOCKOUT_WINDOW, is_locked, send_invite_email, unlock
from core.services.security.credentials import RevocationReason, revoke_credentials

logger = logging.getLogger(__name__)

_ASSIGNABLE_ROLES = frozenset(role for role in User.Role.values if role != User.Role.SUPER_ADMIN)
_EDITABLE_FIELDS = (
    "username",
    "email",
    "display_name",
    "role",
    "is_active",
    "mfa_required",
    "can_confirm_deletion",
)


@dataclass(frozen=True)
class UserAccessRow:
    user: User
    is_locked: bool

    @property
    def can_resend_invite(self) -> bool:
        return bool(self.user.email and self.user.must_change_password)


@dataclass(frozen=True)
class UserInvitationResult:
    user: User
    sent: bool


def _assert_actor(facility, actor) -> None:
    if (
        not getattr(actor, "is_authenticated", False)
        or not getattr(actor, "is_facility_admin", False)
        or actor.facility_id != getattr(facility, "pk", None)
    ):
        raise PermissionDenied("Benutzerverwaltung ist nur in der eigenen Einrichtung erlaubt.")


def manageable_users(facility):
    """Expliziter Scope fuer die nicht durch RLS geschuetzte User-Tabelle."""
    return User.objects.filter(facility=facility).exclude(role=User.Role.SUPER_ADMIN)


def get_manageable_user(facility, pk) -> User:
    return get_object_or_404(manageable_users(facility), pk=pk)


def _locked_user_ids(users: list[User]) -> set[int]:
    """Konservativer Listenstatus wie ``is_locked(user)`` ohne IP, in zwei Queries."""
    user_ids = [user.pk for user in users]
    if not user_ids:
        return set()
    cutoff = timezone.now() - LOCKOUT_WINDOW
    last_unlocks = dict(
        AuditLog.objects.filter(user_id__in=user_ids, action=AuditLog.Action.LOGIN_UNLOCK)
        .values("user_id")
        .annotate(timestamp=Max("timestamp"))
        .values_list("user_id", "timestamp")
    )
    counts: dict[int, int] = {}
    failures = AuditLog.objects.filter(
        user_id__in=user_ids,
        action=AuditLog.Action.LOGIN_FAILED,
        timestamp__gte=cutoff,
    ).values_list("user_id", "timestamp")
    for user_id, timestamp in failures:
        last_unlock: datetime | None = last_unlocks.get(user_id)
        if last_unlock is None or timestamp > last_unlock:
            counts[user_id] = counts.get(user_id, 0) + 1
    return {user_id for user_id, count in counts.items() if count >= LOCKOUT_THRESHOLD}


def list_user_access(facility) -> list[UserAccessRow]:
    users = list(manageable_users(facility).order_by("display_name", "username", "pk"))
    locked = _locked_user_ids(users)
    return [UserAccessRow(user=user, is_locked=user.pk in locked) for user in users]


def _audit_invite(user: User, actor: User, trigger: str, ip_address: str | None) -> None:
    audit_event(
        AuditLog.Action.USER_INVITE_SENT,
        user=user,
        actor=actor,
        facility=user.facility,
        target_obj=user,
        detail={
            "trigger": trigger,
            "username": user.username,
            "actor_id": actor.pk,
            "actor_username": actor.get_username(),
        },
        ip_address=ip_address,
    )


def _deliver_invitation(user: User, actor: User, trigger: str, ip_address: str | None) -> bool:
    try:
        sent = send_invite_email(user)
    except Exception:
        logger.exception("Einladungs-E-Mail konnte nicht versendet werden", extra={"user_pk": user.pk})
        return False
    if sent:
        _audit_invite(user, actor, trigger, ip_address)
    return sent


def create_invited_user(
    *,
    facility,
    actor,
    username: str,
    email: str,
    display_name: str,
    role: str,
    mfa_required: bool,
    can_confirm_deletion: bool,
    ip_address: str | None = None,
) -> UserInvitationResult:
    _assert_actor(facility, actor)
    if role not in _ASSIGNABLE_ROLES:
        raise PermissionDenied("Die Rolle Systemadministration kann hier nicht vergeben werden.")
    if not email:
        raise ValidationError(_("Für den Einladungsversand ist eine E-Mail-Adresse erforderlich."))

    with transaction.atomic():
        user = User(
            facility=facility,
            username=username,
            email=email,
            display_name=display_name,
            role=role,
            mfa_required=mfa_required,
            can_confirm_deletion=can_confirm_deletion,
            must_change_password=True,
        )
        user.set_unusable_password()
        user._audit_actor = actor  # type: ignore[attr-defined]  # transienter Signal-Kontext
        user.save()

    return UserInvitationResult(
        user=user,
        sent=_deliver_invitation(user, actor, "invite_create", ip_address),
    )


def update_user_access(user: User, *, facility, actor, ip_address: str | None = None, **values) -> User:
    _assert_actor(facility, actor)
    if values.get("role") not in _ASSIGNABLE_ROLES:
        raise PermissionDenied("Die Rolle Systemadministration kann hier nicht vergeben werden.")

    with transaction.atomic():
        target = get_object_or_404(manageable_users(facility).select_for_update(), pk=user.pk)
        if target.pk == actor.pk and (values.get("role") != User.Role.FACILITY_ADMIN or not values.get("is_active")):
            raise PermissionDenied("Der eigene Administrationszugang darf nicht entzogen werden.")

        old_is_active = target.is_active
        changed_fields = []
        for name in _EDITABLE_FIELDS:
            value = values[name]
            if getattr(target, name) != value:
                setattr(target, name, value)
                changed_fields.append(name)
        if not changed_fields:
            return target

        target._audit_actor = actor  # transienter Signal-Kontext
        target.save(update_fields=changed_fields)

        # Rolle, Deaktivierung und Loeschbestaetigungsrecht besitzen bereits
        # eigene Signal-Actions. USER_ACCESS_UPDATED schliesst nur die Luecken
        # (MFA-Pflicht, Login-/Anzeige-/Mail-Kennung und Reaktivierung).
        uncovered = set(changed_fields) - {"role", "can_confirm_deletion"}
        if "is_active" in uncovered and old_is_active and not target.is_active:
            uncovered.remove("is_active")
        if uncovered:
            audit_event(
                AuditLog.Action.USER_ACCESS_UPDATED,
                user=target,
                actor=actor,
                facility=facility,
                target_obj=target,
                detail={
                    "changed_fields": sorted(uncovered),
                    "username": target.username,
                    "actor_id": actor.pk,
                    "actor_username": actor.get_username(),
                },
                ip_address=ip_address,
            )
        return target


def resend_user_invitation(user: User, *, facility, actor, ip_address: str | None = None) -> bool:
    _assert_actor(facility, actor)
    target = get_manageable_user(facility, user.pk)
    if not target.email or not target.must_change_password:
        raise ValidationError(_("Ein Setup-Link kann nur für einen noch nicht eingerichteten Zugang versendet werden."))

    # Der E-Mail-lose Legacy-Fallback kann nach dem Nachtragen einer Adresse
    # noch ein nutzbares Initialpasswort tragen. Vor dem neuen Setup-Link wird
    # es samt Offline-/Session-Zugang explizit entwertet.
    if target.has_usable_password():
        target.set_unusable_password()
        target.save(update_fields=["password"])
        revoke_credentials(target, reason=RevocationReason.PRIVILEGE_REVOKED)
    return _deliver_invitation(target, actor, "invite_resend", ip_address)


def resend_invitation_for_any_user(target: User, *, actor, ip_address: str | None = None) -> bool:
    """Einladung installationsweit durch die Systemadministration erneut senden."""
    if not getattr(actor, "is_authenticated", False) or not getattr(actor, "is_super_admin", False):
        raise PermissionDenied("Der installationsweite Einladungsversand ist nur für super_admin erlaubt.")

    if not target.email or not target.must_change_password:
        raise ValidationError(_("Ein Setup-Link kann nur für einen noch nicht eingerichteten Zugang versendet werden."))

    if target.has_usable_password():
        target.set_unusable_password()
        target.save(update_fields=["password"])
        revoke_credentials(target, reason=RevocationReason.PRIVILEGE_REVOKED)
    return _deliver_invitation(target, actor, "invite_resend", ip_address)


def unlock_managed_user(user: User, *, facility, actor, ip_address: str | None = None) -> bool:
    _assert_actor(facility, actor)
    target = get_manageable_user(facility, user.pk)
    if not is_locked(target):
        return False
    unlock(target, unlocked_by=actor, ip_address=ip_address)
    return True
