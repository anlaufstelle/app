"""Account-Lockout nach gehäuften Fehlanmeldungen (Refs #612, #737).

Tracking erfolgt über den bestehenden AuditLog (Option A aus dem Design):
kein zusätzliches Modell, dafür eine Composite-Query je Login. Der Audit-
Log wird ohnehin auf jeden `user_login_failed`-Signal geschrieben — wir
zählen einfach die relevanten Einträge.

Concurrency (Refs #737): `is_locked()` laeuft unter
``transaction.atomic()`` + ``User.objects.select_for_update()``, damit
zwei zeitgleiche Pruefungen fuer denselben User serialisiert werden.
**Residuelle Luecke:** zwischen einer ``is_locked()=False``-Antwort und
dem AuditLog-Write der naechsten ``user_login_failed``-Signal-Instanz
kann eine zweite parallele Anmeldung mit korrektem Passwort den
Lockout-Zustand noch nicht sehen — Threshold + 1 Versuch in seltenen
Fenstern. Vollstaendig atomar waere ein dedizierter Lockout-State-Row
(eigenes Modell) oder Redis ``INCR`` mit TTL; beides ist hier overkill
fuer einen Pre-Release-Pilot mit <30 Usern und MFA-Pflicht-Option.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, User
from core.services.audit import audit_event

# Schwelle und Zeitfenster (Issue #612): 10 Fehlversuche in 15 Minuten
# lösen die Sperre aus; sie wirkt, bis das Fenster leer ist — also 15 Min
# nach dem letzten Fehlversuch.  Einträge vor dem letzten LOGIN_UNLOCK zählen
# nicht mit, damit Admin-Unlock wirkt.
#
# Lockout-Semantik (A3.3, Refs #1024 — bewusste Maintainer-Entscheidung):
# Login-Versuche WÄHREND einer aktiven Sperre werden über ``views/auth.py``
# weiterhin als ``LOGIN_FAILED`` protokolliert und zählen ins gleitende
# 15-Minuten-Fenster. Wer also gegen die Sperre weiter anmeldet, hält sie
# selbst aufrecht ("self-sustaining"). Das ist GEWOLLT (stärkerer Anti-
# Bruteforce-Effekt für einen Pre-Release-Pilot mit MFA-Pflicht-Option) —
# es wird bewusst KEINE separate, nicht-gezählte ``LOGIN_BLOCKED``-Action
# eingeführt. Falls sich die Anforderung ändert (saubere Forensik-Trennung
# „trug zur Sperre bei" vs. „kam gegen die Sperre"), wäre das der Ansatzpunkt.
#
# Lockout-Achse je Quell-IP (N9, Refs #1446): Der Lockout zählt Fehlversuche
# je (User, Quell-IP) statt global je Username. Ein Unauthentifizierter, der
# einen (semi-öffentlichen) Usernamen kennt, sperrte sonst mit 10 Falsch-
# passwörtern gezielt den Login des Opfers — auch von dessen eigener IP —
# und hielt die Sperre durch Weiteranfeuern self-sustaining. Mit IP-Bindung
# sperrt der Angreifer nur das Paar (Opfer-Username, Angreifer-IP); das Opfer
# loggt sich von der eigenen IP normal ein. Das Self-Sustaining bleibt je IP
# erhalten (der Block-Audit trägt die Angreifer-IP mit). Verteiltes Cross-IP-
# Raten bleibt durch den Username-Ratelimit in ``views/auth.py`` (10/h GESAMT,
# Refs #598) gedeckelt — dieser bleibt bewusst rein username-basiert, damit
# rotierende IPs ihn nicht umgehen. Alt-Aufrufer ohne Request-Kontext (CLI /
# Admin-Anzeige) rufen ``is_locked(user)`` ohne IP → konservativ zählen dann
# weiterhin ALLE Fehlversuche (Frage „ist der Account überhaupt gesperrt?").
LOCKOUT_THRESHOLD = 10
LOCKOUT_WINDOW = timedelta(minutes=15)


def is_locked(user, ip_address=None) -> bool:
    """Return True when the user has reached the lockout threshold.

    ``ip_address`` (N9, Refs #1446): echte Client-IP des AKTUELLEN Versuchs
    (N1-Resolver ``get_client_ip``). Ist sie gesetzt, zählt nur die Sperre
    dieser Quell-IP — fremde Falschpasswörter sperren den Login des Opfers von
    dessen eigener IP nicht mehr. Ist sie ``None`` (Alt-Aufrufer ohne Request:
    CLI / Admin-Anzeige), bleibt das konservative Legacy-Verhalten (alle
    Fehlversuche des Users zählen, quer über alle IPs).

    Refs #737: ``transaction.atomic`` + ``select_for_update`` auf der
    User-Zeile serialisieren parallele ``is_locked``-Aufrufe fuer
    denselben User — verhindert, dass zwei zeitgleiche Anmeldungen
    beide ``count=9`` lesen und beide den Threshold-Schutz unterlaufen.
    """
    if user is None or not getattr(user, "pk", None):
        return False

    with transaction.atomic():
        # User-Zeile sperren — nachfolgende ``is_locked``-Aufrufe fuer
        # denselben User warten, bis diese Transaktion committet.
        User.objects.select_for_update().filter(pk=user.pk).first()

        cutoff = timezone.now() - LOCKOUT_WINDOW
        last_unlock = (
            AuditLog.objects.filter(user=user, action=AuditLog.Action.LOGIN_UNLOCK).order_by("-timestamp").first()
        )

        qs = AuditLog.objects.filter(
            user=user,
            action=AuditLog.Action.LOGIN_FAILED,
            timestamp__gte=cutoff,
        )
        if ip_address is not None:
            # N9 (Refs #1446): Sperre nur je (User, Quell-IP).
            qs = qs.filter(ip_address=ip_address)
        if last_unlock is not None:
            qs = qs.filter(timestamp__gt=last_unlock.timestamp)
        return qs.count() >= LOCKOUT_THRESHOLD


def unlock(user, unlocked_by, ip_address=None, trigger: str = "admin") -> AuditLog:
    """Record a LOGIN_UNLOCK audit entry for the user.

    Subsequent `is_locked(user)` calls ignore LOGIN_FAILED entries with
    `timestamp <= this_entry.timestamp`.

    ``trigger`` documents the recovery path (Refs #869): ``"admin"`` (Admin-
    Action), ``"cli"`` (manage.py unlock), ``"password_reset"``,
    ``"recovery_token"`` (dedizierter Token-Flow), ``"backup_code"``
    (MFA-Recovery).
    """
    return audit_event(
        AuditLog.Action.LOGIN_UNLOCK,
        user=user,
        facility=getattr(user, "facility", None),
        target_type="User",
        target_id=str(user.pk),
        detail={
            "unlocked_by": str(unlocked_by.pk) if unlocked_by else None,
            "trigger": trigger,
        },
        ip_address=ip_address,
    )
