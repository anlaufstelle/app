"""Offline-Cache-Invalidierung bei Rechteentzug (F-03, Refs #1110).

Security-Review 2026-06-14 (Abschnitt 5.2): Eine Rollen-Herabstufung, ein
Sensitivity-Entzug (= Rollen-Herabstufung, da Notizen nur Staff+ sehen) oder
eine Account-Deaktivierung invalidieren ein bereits offline genommenes Bundle
NICHT. Der client-seitige AES-GCM-Schlüssel bleibt ableitbar, solange der
``offline_key_salt`` des Users unverändert ist, und die laufende Session kann
den Salt-Endpoint weiter bedienen. Damit überdauert verschlüsselter
Klartext-Cache (bis TTL/Idle) den Rechteentzug — online würde der Filter
sofort greifen, offline nicht.

Dieses Modul koppelt zwei server-seitige Mechanismen an die relevante
User-Mutation, sanktioniert durch Audit-Blocker #3:

1. **Salt-Rotation** — ``offline_key_salt`` wird geleert, exakt wie beim
   Passwortwechsel (``core/views/auth.py`` -> ``CustomPasswordChangeView``).
   Der nächste Salt-Fetch generiert lazily einen neuen Salt
   (``User.ensure_offline_key_salt``); der mit dem ALTEN Salt abgeleitete
   Schlüssel kann den IndexedDB-Chiffretext nicht mehr entschlüsseln ->
   Auto-Discard (``src/static/js/offline-store.js``).
2. **Session-Flush** — alle DB-Sessions des Users werden gelöscht. Ohne das
   könnte die laufende, noch gültige Session den (alten) Schlüssel
   weiterverwenden und über ``OfflineKeySaltView`` (``LoginRequiredMixin``)
   den frisch rotierten Salt sofort wieder nachladen — die Rotation liefe
   ins Leere und der Account bliebe trotz Entzug aktiv.

Auslöser (konservativ): jede Rollen**änderung** (jede Änderung kann ein Entzug
sein; eine Hochstufung erfordert ohnehin frisches Laden unter weiterer Sicht)
sowie ``is_active`` True -> False.

Die Diff-Erkennung läuft über eigene, instanz-lokale ``_offline_inval_old_*``-
Attribute (unabhängig von den ``_audit_old_*``-Attributen in
``core/signals/audit.py``), damit beide Receiver entkoppelt bleiben.
"""

from __future__ import annotations

import logging

from django.contrib.sessions.models import Session
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from core.models import User

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=User, dispatch_uid="offline_invalidation_capture_old")
def _capture_old_privilege_state(sender, instance, **kwargs):
    """Snapshot von Rolle und ``is_active`` vor dem Save, für den Diff im
    ``post_save``."""
    if not instance.pk:
        instance._offline_inval_old_role = None
        instance._offline_inval_old_is_active = None
        return
    try:
        old = User.objects.only("role", "is_active").get(pk=instance.pk)
    except User.DoesNotExist:
        instance._offline_inval_old_role = None
        instance._offline_inval_old_is_active = None
        return
    instance._offline_inval_old_role = old.role
    instance._offline_inval_old_is_active = old.is_active


@receiver(post_save, sender=User, dispatch_uid="offline_invalidation_on_revoke")
def _invalidate_offline_cache_on_revoke(sender, instance, created, **kwargs):
    """Rotiert den Offline-Salt und flusht die Sessions des Users, sobald
    seine Rolle wechselt oder er deaktiviert wird."""
    if created:
        return

    old_role = getattr(instance, "_offline_inval_old_role", None)
    old_is_active = getattr(instance, "_offline_inval_old_is_active", None)

    role_changed = old_role is not None and old_role != instance.role
    deactivated = old_is_active is True and instance.is_active is False

    if not (role_changed or deactivated):
        return

    _rotate_offline_salt(instance)
    _flush_user_sessions(instance)


def _rotate_offline_salt(user) -> None:
    """Leere ``offline_key_salt`` per Queryset-Update (feuert KEINE Signale,
    also keine Rekursion). Spiegelt das Verhalten der Passwortwechsel-Rotation:
    der alte client-seitige Schlüssel wird damit wertlos."""
    if not user.offline_key_salt:
        return
    User.objects.filter(pk=user.pk).update(offline_key_salt="")
    # Lokale Instanz konsistent halten, damit ein nachfolgender Zugriff in
    # derselben Transaktion nicht den alten Wert sieht.
    user.offline_key_salt = ""


def _flush_user_sessions(user) -> None:
    """Lösche alle (noch gültigen) DB-Sessions, die auf diesen User zeigen.

    Django-Default-Session-Backend ist DB-backed; eine Session bindet den User
    über ``_auth_user_id`` im dekodierten Payload. Wir scannen nur die noch
    nicht abgelaufenen Sessions (abgelaufene tragen ohnehin keinen lebenden
    Schlüssel mehr und werden vom Clearsessions-Housekeeping getilgt).
    """
    user_id = str(user.pk)
    stale_keys = []
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if session.get_decoded().get("_auth_user_id") == user_id:
            stale_keys.append(session.session_key)
    if stale_keys:
        Session.objects.filter(session_key__in=stale_keys).delete()
        logger.info(
            "Offline-Invalidierung: %d Session(s) für User %s geflusht.",
            len(stale_keys),
            user.pk,
        )
