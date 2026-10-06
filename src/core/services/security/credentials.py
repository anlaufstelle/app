"""Zentraler Credential-Lifecycle (Refs #1965).

Drei Wege enden mit einem neuen Passwort — Passwortwechsel
(``CustomPasswordChangeView``), Self-Service-Reset
(``CustomPasswordResetConfirmView``) und Invite-Setup (``InviteConfirmView``) —
und ein vierter (Rechteentzug/Lockout) entwertet den Zugang ohne Passwort.
Bis hierher trug jeder dieser Wege seine eigene Teilmenge der Nebenwirkungen im
Rumpf seiner ``form_valid``: der Wechsel rotierte den ``offline_key_salt``,
leerte das Sudo-Fenster und loeste den Zwangswechsel; Reset und Invite hoben den
Lockout auf und loesten den Zwangswechsel, fassten Salt und Sudo-Fenster aber
nicht an. Welche Wirkung wo fehlte, war nur durch Vergleich der Rumpfe zu
erkennen — eine Auslassung sah aus wie eine Entscheidung.

:func:`revoke_credentials` ist der einzige Einstieg. Die Unterschiede zwischen
den Wegen bleiben — sie sind fachlich begruendet —, stehen jetzt aber als EINE
erklaerte Wirkungs-Matrix (:data:`REVOCATION_EFFECTS`) an einer Stelle statt als
Zufall in vier.

Korrigierte Praemisse: Alt-Sessions sterben ohnehin am Auth-Hash
================================================================

Ein wiederkehrender Review-Befund lautet, Reset und Invite liessen Alt-Sessions
weiterlaufen. Das ist **falsch** und soll hier nicht erneut als Luecke gemeldet
werden: Django prueft in ``django.contrib.auth.get_user()`` bei JEDEM Request
den Session-Auth-Hash (``constant_time_compare`` gegen
``user.get_session_auth_hash()``, abgeleitet aus dem Passwort-Hash). Die dafuer
noetige ``AuthenticationMiddleware`` ist aktiv (``settings/base.py``), und
``User`` erbt von ``AbstractUser``. Ein neues Passwort entwertet damit jede
Alt-Session beim naechsten Request — auch ohne Session-Flush. Der Salt-Endpunkt
``OfflineKeySaltView`` haengt an ``LoginRequiredMixin`` und ist fuer eine tote
Session ebenfalls zu.

Zwei bewusste Nicht-Erweiterungen folgen daraus (#1965, Entscheidungspunkte):

* **Keine harte Salt-Rotation bei Reset/Invite.** Der Offline-Schluessel ist
  ``PBKDF2(passwort, salt)``; ein neues Passwort entwertet ihn unabhaengig vom
  Salt, der alte IndexedDB-Chiffretext wird ohnehin unlesbar und beim naechsten
  Login verworfen. Der Restnutzen einer Rotation ist der Reset auf DASSELBE
  Passwort; die Kosten sind der sofort verlorene Offline-Cache eines noch
  offenen Tabs derselben Nutzerin. Der Passwortwechsel rotiert weiterhin, weil
  dort die eigene Session per ``update_session_auth_hash`` bewusst am Leben
  bleibt.
* **Kein Session-Flush bei Reset/Invite.** Sicherheitsseitig redundant (s.o.),
  kostet aber einen Scan ueber ALLE aktiven Sessions (Djangos DB-Backend hat
  keinen User-Index). Der Flush bleibt dem Rechteentzug vorbehalten, wo die
  Session gerade NICHT am Passwort stirbt.

Warum hier und nicht in ``signals/offline_invalidation``
========================================================

Die Mechanik (Salt-Rotation, Session-Flush samt DoS-Deckel) lag bis #1965 im
Signal-Modul, obwohl drei ihrer vier Ausloeser Views sind. Sie liegt jetzt in
der Service-Schicht; ``core.signals.offline_invalidation.revoke_offline_access``
bleibt als Einstieg des Signal-Pfads bestehen und ruft hierher. Die
Importrichtung ist damit eindeutig ``signals -> services`` (kein Zirkel).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from django.conf import settings
from django.contrib.sessions.models import Session
from django.utils import timezone

from core.models import AuditLog, User
from core.services.audit import audit_event, log_audit_event
from core.services.security.login_lockout import unlock
from core.services.security.sudo_mode import clear_sudo
from core.signals.audit import _set_session_vars, get_client_ip

logger = logging.getLogger(__name__)

# R18 (Refs #1476/#1666): Obergrenze fuer den Session-Scan in
# :func:`flush_user_sessions`. Djangos DB-Session-Backend traegt keinen
# queryable User-Index — der User haengt nur im signierten ``session_data``.
# Der Flush muss daher jede nicht-abgelaufene Session dekodieren. Ohne Deckel
# ist das O(alle aktiven Sessions) je Revocation (DoS-Flaeche, wenn ein
# Angreifer viele anonyme Sessions erzeugt). Der Default liegt weit ueber dem
# realistischen Bestand (Pilot < 30 User); wird er ueberschritten, wird das
# laut geloggt (Salt-Rotation entwertet den Offline-Schluessel ohnehin global;
# der ungeflushte Rest laeuft bei Inaktivitaet ab — aktiv genutzte Sessions
# erneuern sich seit #1858, enden aber spaetestens am absoluten
# Sitzungs-Maximum aus #1868 [Default 12 h ab Login]. Das Zeitfenster des
# Rests ist damit wieder nach oben begrenzt; siehe docs/security-notes.md).
_SESSION_SCAN_CAP_DEFAULT = 10000
_SESSION_SCAN_CHUNK = 2000


class RevocationReason(StrEnum):
    """Ausloeser eines Credential-Widerrufs — zugleich der Slug im Audit-Detail."""

    # Die beiden ``noqa: S105`` sind Fehlalarme des Bandit-Regelsatzes: das
    # sind Ausloeser-Slugs fuer das Audit-Detail, kein Passwort-Material.
    PASSWORD_CHANGE = "password_change"  # noqa: S105
    PASSWORD_RESET = "password_reset"  # noqa: S105
    INVITE_SETUP = "invite_setup"
    ACCOUNT_LOCKOUT = "account_lockout"
    PRIVILEGE_REVOKED = "privilege_revoked"


@dataclass(frozen=True)
class RevocationEffects:
    """Was ein Ausloeser bewirkt. Sechs Achsen, je Ausloeser einmal entschieden."""

    #: ``offline_key_salt`` leeren — entwertet den client-seitig abgeleiteten
    #: AES-GCM-Schluessel der Offline-Ablage global.
    rotate_offline_salt: bool
    #: Alle DB-Sessions des Users loeschen (teuer, s. Scan-Deckel).
    flush_sessions: bool
    #: Sudo-Fenster der HANDELNDEN Session leeren (braucht ``request``).
    clear_sudo_window: bool
    #: ``must_change_password`` aufloesen.
    reset_forced_change: bool
    #: ``LOGIN_UNLOCK`` schreiben, damit ein aktiver Lockout endet.
    lift_lockout: bool
    #: Eigene ``CREDENTIALS_REVOKED``-Zeile schreiben.
    write_audit: bool


#: Die Wirkungs-Matrix — die einzige Wahrheit darueber, was ein Ausloeser tut.
#:
#: Die drei Passwort-Wege teilen alles ausser der Salt-Rotation (Begruendung im
#: Modul-Docstring). Die beiden Entzugs-Wege teilen Rotation und Flush, aber
#: nichts aus dem Passwort-Bereich: dort wechselt kein Passwort, ein
#: aufgeloester Zwangswechsel oder ein aufgehobener Lockout waere schlicht
#: falsch. Ihre Audit-Spur schreiben sie selbst (``USER_ROLE_CHANGED`` /
#: ``USER_DEACTIVATED`` ueber ``signals/audit.py`` bzw. ``LOGIN_FAILED`` mit
#: ``reason=locked`` in ``views/auth.py``) — eine zweite Zeile beschriebe
#: denselben Vorgang doppelt.
REVOCATION_EFFECTS: dict[RevocationReason, RevocationEffects] = {
    RevocationReason.PASSWORD_CHANGE: RevocationEffects(
        rotate_offline_salt=True,
        flush_sessions=False,
        clear_sudo_window=True,
        reset_forced_change=True,
        lift_lockout=False,
        write_audit=True,
    ),
    RevocationReason.PASSWORD_RESET: RevocationEffects(
        rotate_offline_salt=False,
        flush_sessions=False,
        clear_sudo_window=True,
        reset_forced_change=True,
        lift_lockout=True,
        write_audit=True,
    ),
    RevocationReason.INVITE_SETUP: RevocationEffects(
        rotate_offline_salt=False,
        flush_sessions=False,
        clear_sudo_window=True,
        reset_forced_change=True,
        lift_lockout=True,
        write_audit=True,
    ),
    RevocationReason.ACCOUNT_LOCKOUT: RevocationEffects(
        rotate_offline_salt=True,
        flush_sessions=True,
        clear_sudo_window=False,
        reset_forced_change=False,
        lift_lockout=False,
        write_audit=False,
    ),
    RevocationReason.PRIVILEGE_REVOKED: RevocationEffects(
        rotate_offline_salt=True,
        flush_sessions=True,
        clear_sudo_window=False,
        reset_forced_change=False,
        lift_lockout=False,
        write_audit=False,
    ),
}


def revoke_credentials(user, *, reason, request=None) -> None:
    """Widerrufe alles, was am bisherigen Passwort des Users hing.

    ``reason`` ist ein :class:`RevocationReason` (oder dessen Slug) und
    entscheidet ueber :data:`REVOCATION_EFFECTS`, welche Achsen greifen — die
    Aufrufer wissen nur, WARUM sie rufen, nicht WAS das ausloest.

    ``request`` ist optional: er wird fuer das Sudo-Fenster (das in der
    handelnden Session liegt) und fuer die IP der Audit-Zeile gebraucht. Ohne
    Request laeuft der Widerruf durch, das Sudo-Fenster bleibt dann
    unberuehrt — im Service-/CLI-Kontext gibt es keins.

    Ein unbekannter ``reason`` scheitert laut (``ValueError``): eine stille
    Voreinstellung waere die Rueckkehr zu genau dem Zustand, den #1965
    aufgeloest hat.
    """
    grund = RevocationReason(reason)
    effects = REVOCATION_EFFECTS[grund]

    if effects.rotate_offline_salt:
        rotate_offline_salt(user)
    if effects.flush_sessions:
        flush_user_sessions(user)
    if effects.clear_sudo_window and request is not None:
        # I8 (Refs #1376): Ein neues Passwort entwertet die alte Re-Auth. Ohne
        # das bliebe eine bereits erreichte Sudo-Session (z.B. per gestohlenem
        # Session-Cookie vor dem Wechsel) trotz neuem Passwort gueltig.
        clear_sudo(request)
    if effects.reset_forced_change and getattr(user, "must_change_password", False):
        # Refs #1969: Wer sein Passwort gerade selbst gesetzt hat, darf nicht
        # beim ersten Login sofort wieder auf ``/password-change/`` landen.
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
    if effects.lift_lockout:
        # Refs #869: Ein abgeschlossener Reset ist ein starker Vertrauensanker
        # (bewiesene E-Mail-Kontrolle + selbst gewaehltes Passwort) und hebt
        # einen aktiven Lockout auf. Der Slug haelt fest, ueber welchen der
        # beiden Token-Wege das geschah.
        #
        # Refs #2121: Die Quell-IP stand hier fest auf ``None``, obwohl der
        # ``request`` vorliegt — dieselbe Luecke, die #2111 an den
        # Recovery-Token- und Backup-Code-Wegen geschlossen hat. Der
        # LOGIN_UNLOCK-Eintrag verlor damit genau das Merkmal, das die Frage
        # „von wo kam die Entsperrung" beantwortet. ``get_client_ip`` loest sie
        # spoofing-resistent ueber ``TRUSTED_PROXY_HOPS`` auf und liefert fuer
        # den request-losen Service-/CLI-Kontext weiterhin ``None``.
        #
        # Der Unlock schreibt ueber den request-losen ``audit_event``-Helper —
        # fuer den anonymen Reset-/Invite-Request muessen die RLS-GUCs vorher
        # auf den Ziel-Kontext stehen (siehe Helper-Docstring).
        _sync_rls_context_for_anonymous_request(user, request)
        unlock(user, unlocked_by=None, ip_address=get_client_ip(request), trigger=str(grund))
    if effects.write_audit:
        _write_audit(user, grund, request)


def _sync_rls_context_for_anonymous_request(user, request) -> None:
    """Setzt die RLS-Session-GUCs auf den Ziel-User, wenn der Request anonym ist.

    Reset und Invite laufen ohne Login: ``FacilityScopeMiddleware`` hat
    ``app.current_facility_id``/``app.is_super_admin`` fuer den anonymen
    Request auf ``''`` gestellt. ``unlock`` schreibt LOGIN_UNLOCK ueber den
    request-losen Helper ``audit_event``, der — anders als ``log_audit_event``
    (Security R3) — keinen GUC-Sync kennt. Unter der NOBYPASSRLS-App-Rolle
    verletzte der INSERT damit die WITH-CHECK-Policy (Migration 0085), und zwar
    NACH dem bereits gespeicherten Passwort: 500er statt Weiterleitung, weder
    Unlock- noch CREDENTIALS_REVOKED-Zeile, ein aktiver Lockout blieb trotz
    Reset bestehen (Refs #2391). Dasselbe Muster setzt
    ``consume_recovery_token`` vor seinem Unlock (Refs #2111).

    Authentifizierte Requests (Passwortwechsel) bleiben unberuehrt: dort tragen
    die GUCs bereits den Kontext der handelnden Person, und ein Umschalten auf
    den Ziel-User waere fuer eine super_admin-Session ein Kontextverlust. Ohne
    Request (Service-/CLI-Kontext) gibt es keine Middleware-Vorbelegung zu
    korrigieren — dort verantwortet der Aufrufer den DB-Kontext selbst.
    """
    if request is None:
        return
    request_user = getattr(request, "user", None)
    if request_user is not None and getattr(request_user, "is_authenticated", False):
        return
    _set_session_vars(
        getattr(user, "facility", None),
        is_super_admin=bool(getattr(user, "is_super_admin", False)),
    )


def _write_audit(user, reason: RevocationReason, request) -> None:
    """Rechenschaftszeile ueber den Credential-Wechsel (Art. 5 Abs. 2 DSGVO).

    Mit Request laeuft der Write ueber :func:`log_audit_event` — der Helper
    synct fuer die anonymen Reset-/Invite-Requests zusaetzlich die
    RLS-Session-GUCs auf den Ziel-Kontext (Security R3), was der request-lose
    Helper nicht kann. Das ``detail`` traegt nur den festen Grund-Slug: keine
    Kennung, kein Passwort-Material, kein Freitext. Es steht in beiden Zweigen
    literal am Aufruf (statt einmal in einer Variablen), damit der statische
    Inventar-Guard ``TestAuditDetailKeyInventory`` (Refs #1944) den Schluessel
    sieht — ohne Ausführung des Schreibpunkts.
    """
    facility = getattr(user, "facility", None)
    if request is not None:
        log_audit_event(
            request,
            AuditLog.Action.CREDENTIALS_REVOKED,
            target_obj=user,
            detail={"reason": str(reason)},
            user=user,
            facility=facility,
        )
    else:
        audit_event(
            AuditLog.Action.CREDENTIALS_REVOKED,
            user=user,
            facility=facility,
            target_obj=user,
            detail={"reason": str(reason)},
        )


def rotate_offline_salt(user) -> None:
    """Leere ``offline_key_salt`` per Queryset-Update.

    Bewusst ``QuerySet.update()`` statt ``save()``: der Aufruf feuert KEINE
    Signale und kann deshalb aus dem ``post_save``-Receiver des Rechteentzugs
    heraus laufen, ohne sich selbst erneut auszuloesen. Der naechste
    Salt-Fetch erzeugt lazily einen neuen Salt
    (``User.ensure_offline_key_salt``); der mit dem ALTEN Salt abgeleitete
    Schluessel kann den IndexedDB-Chiffretext nicht mehr entschluesseln ->
    Auto-Discard (``src/static/js/offline-store.js``).
    """
    if not user.offline_key_salt:
        return
    User.objects.filter(pk=user.pk).update(offline_key_salt="")
    # Lokale Instanz konsistent halten, damit ein nachfolgender Zugriff in
    # derselben Transaktion nicht den alten Wert sieht.
    user.offline_key_salt = ""


def flush_user_sessions(user, *, salt_rotated: bool = True) -> None:
    """Loesche alle (noch gueltigen) DB-Sessions, die auf diesen User zeigen.

    Django-Default-Session-Backend ist DB-backed; eine Session bindet den User
    ueber ``_auth_user_id`` im dekodierten Payload. Wir scannen nur die noch
    nicht abgelaufenen Sessions (abgelaufene tragen ohnehin keinen lebenden
    Schluessel mehr und werden vom taeglichen ``clearsessions``-Job des
    Cron-Sidecars getilgt).

    R18 (Refs #1476/#1666): Der Scan ist begrenzt — nur die zwei benoetigten
    Spalten (``.only``), gestreamt (``.iterator``) statt materialisiert, und
    hart gedeckelt (``OFFLINE_REVOCATION_SESSION_SCAN_LIMIT``). Wird der Deckel
    erreicht, wird laut geloggt und abgebrochen; die bereits gerotierte Salt
    entwertet den Offline-Schluessel global. Ein etwaiger ungeflushter
    Session-Rest laeuft seit #1858 bei Inaktivitaet ab — aktiv genutzte
    Sessions erneuern sich, enden aber spaetestens am absoluten
    Sitzungs-Maximum (#1868, Default 12 h ab Login).

    ``salt_rotated=False`` is for callers that rotate no offline salt (the
    second-factor reset, Refs #2469): the log lines then describe a plain
    session flush instead of an offline revocation.
    """
    user_id = str(user.pk)
    cap = getattr(settings, "OFFLINE_REVOCATION_SESSION_SCAN_LIMIT", _SESSION_SCAN_CAP_DEFAULT)
    stale_keys = []
    capped = False
    qs = (
        Session.objects.filter(expire_date__gte=timezone.now())
        .only("session_key", "session_data")
        .iterator(chunk_size=_SESSION_SCAN_CHUNK)
    )
    for scanned, session in enumerate(qs):
        if scanned >= cap:
            capped = True
            break
        if session.get_decoded().get("_auth_user_id") == user_id:
            stale_keys.append(session.session_key)
    if stale_keys:
        Session.objects.filter(session_key__in=stale_keys).delete()
        if not salt_rotated:
            logger.info(
                "Session flush without salt rotation: %d session(s) of user %s deleted.", len(stale_keys), user.pk
            )
        else:
            logger.info(
                "Offline-Invalidierung: %d Session(s) für User %s geflusht.",
                len(stale_keys),
                user.pk,
            )
    if capped and not salt_rotated:
        logger.error(
            "Session flush without salt rotation: session scan cap (%d) reached for user %s - "
            "remaining sessions not flushed (they expire on inactivity, at the latest at the absolute "
            "session maximum, Refs #1858/#1868).",
            cap,
            user.pk,
        )
    elif capped:
        logger.error(
            "Offline-Invalidierung: Session-Scan-Obergrenze (%d) für User %s erreicht — "
            "Rest nicht geflusht (Salt ist rotiert; Rest läuft bei Inaktivität ab, spätestens am "
            "absoluten Sitzungs-Maximum, Refs #1858/#1868).",
            cap,
            user.pk,
        )
