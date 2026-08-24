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

Refs #1965: Beide Mechanismen selbst liegen seit dem Zusammenziehen des
Credential-Lifecycles in :mod:`core.services.security.credentials` — dieses
Modul haelt nur noch die Signal-Verdrahtung und den benannten Einstieg
:func:`revoke_offline_access`.
"""

from __future__ import annotations

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from core.models import User
from core.services.security.credentials import RevocationReason, revoke_credentials


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

    revoke_offline_access(instance)


def revoke_offline_access(user) -> None:
    """Entwerte den Offline-Zugang eines Users: Salt rotieren + Sessions flushen.

    Einstieg des Signal-Pfads in den zentralen Credential-Lifecycle
    (:func:`core.services.security.credentials.revoke_credentials`, Refs #1965).
    Die Mechanik selbst — Salt-Rotation, Session-Flush samt DoS-Deckel — liegt
    seit #1965 dort, weil drei ihrer vier Ausloeser Views sind und eine zweite
    Wahrheit im Signal-Modul genau die Teilmengen-Drift erzeugte, die #1965
    aufgeloest hat. Beide Schritte gehoeren weiterhin zusammen: die
    Salt-Rotation allein liefe ins Leere, solange eine noch gueltige Session
    ueber ``OfflineKeySaltView`` den frisch rotierten Salt sofort nachladen
    koennte.

    Bewusst OHNE eigene Audit-Zeile: Rollenwechsel und Deaktivierung schreiben
    ueber ``core/signals/audit.py`` bereits ``USER_ROLE_CHANGED`` bzw.
    ``USER_DEACTIVATED``.
    """
    revoke_credentials(user, reason=RevocationReason.PRIVILEGE_REVOKED)
