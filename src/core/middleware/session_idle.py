"""Middleware: Session-Fenster bei Aktivitaet erneuern, absolut deckeln.

Zwei Mechanismen in einer Middleware, in dieser Reihenfolge:

1. **Absolutes Maximum ab Login** (Refs #1868) — beendet die Sitzung, sobald
   die beim Login eingefrorene Deadline (``ABSOLUTE_DEADLINE_KEY``) erreicht
   ist. Das gilt fuer JEDEN authentifizierten Request, auch fuer die unten
   ausgenommenen Hintergrund-Sync-Pfade: das Cap ist eine absolute Grenze,
   keine Aktivitaets-Frage. Ein dauersyncendes Geraet haette sonst ein
   Schlupfloch daran vorbei.
2. **Inaktivitaets-Fenster** (Refs #1858, unten beschrieben) — laeuft nur,
   wenn (1) die Sitzung nicht beendet hat.

Zu (1): Die Middleware antwortet bewusst **nicht selbst** mit einem Redirect.
Nach ``logout()`` laeuft der Request anonym weiter und die getroffene View
liefert ihren eigenen ``LoginRequiredMixin``-302. Nur so kommt der
Standard-Pfad zum Zug: ``HtmxSessionMiddleware`` uebersetzt ihn fuer HTMX in
``200 + HX-Redirect`` und laesst ihn fuer ein Offline-Replay
(``X-Offline-Replay``) als rohen 302 durch — daran haelt der
Queue-Klassifikator an, ohne die Zeile zu loeschen (ADR-030 §3, Refs #1419).
Ein eigener Redirect aus dieser Middleware wuerde beides umgehen.

---

Erneuerung des Session-Fensters bei Nutzeraktivitaet (Refs #1858).

Mit ``SESSION_SAVE_EVERY_REQUEST = False`` (Refs #733) verlaengert Django das
Session-Fenster nur bei tatsaechlichen Session-Writes — die gab es im normalen
Request-Pfad nirgends, der Timeout wirkte deshalb ABSOLUT ab Login statt nach
Inaktivitaet. Diese Middleware liefert das dokumentierte Inaktivitaets-Fenster,
ohne die Write-Amplifikation pro Request zurueckzuholen: hoechstens ein
Session-Write je ``RENEWAL_FRACTION``-Anteil der konfigurierten Laufzeit.

Mechanik: ``session.get_expiry_age()`` liefert bei int-basiertem
``_session_expiry`` (der Login setzt ``set_expiry(session_timeout_minutes * 60)``)
die KONFIGURIERTE Laufzeit, nicht die Restlaufzeit — die steht nur in
``django_session.expire_date`` und ist am Session-Objekt nicht verfuegbar.
Deshalb fuehrt die Middleware einen eigenen Erneuerungs-Zeitstempel
(``LAST_RENEWAL_KEY``) in der Session: Liegt er laenger als
``RENEWAL_FRACTION * Laufzeit`` zurueck (oder fehlt er, Alt-Session), wird er
neu gestempelt. Der Write markiert die Session als modified; die
``SessionMiddleware`` speichert sie ausgangsseitig und berechnet dabei
``expire_date = jetzt + konfigurierte Laufzeit`` neu — die facility-spezifische
Laufzeit bleibt automatisch wirksam.
"""

from __future__ import annotations

from django.contrib.auth import logout
from django.utils import timezone

from core.models.settings import DEFAULT_SESSION_ABSOLUTE_HOURS

# Session-Key mit Unix-Timestamp der letzten Fenster-Erneuerung. Wird beim
# Login mitgestempelt (views/auth.py), damit der erste Request nach dem Login
# keinen zusaetzlichen Write ausloest.
LAST_RENEWAL_KEY = "_idle_window_renewed_at"

# Session-Key mit Unix-Timestamp des absoluten Sitzungs-Endes (Refs #1868).
# Wird beim Login EINMAL gestempelt (views/auth.py) und danach von nichts mehr
# verschoben — insbesondere nicht von der Erneuerungs-Logik unten. Wuerde er
# mitwandern, waere das Maximum nur ein zweites Sliding Window.
ABSOLUTE_DEADLINE_KEY = "_session_absolute_deadline"

# Anteil der konfigurierten Laufzeit, nach dem die naechste authentifizierte
# Anfrage das Fenster erneuert. 0.1 = beim 30-Minuten-Default schreibt eine
# aktive Session hoechstens alle 3 Minuten (statt pro Request, Refs #733) —
# und der Logout kommt garantiert erst nach 90–100 % der konfigurierten
# Inaktivitaetszeit, wie von Doku/FAQ zugesagt. Das grosszuegigere 50 %-Fenster
# aus dem Issue-Vorschlag wuerde Logouts schon nach der halben zugesagten
# Inaktivitaetszeit erlauben.
RENEWAL_FRACTION = 0.1

# Die Offline-Sync-API (core/urls/_offline.py) zaehlt komplett NICHT als
# Nutzeraktivitaet: Die Sync-Sequenz (sync-orchestrator.js) feuert beim
# ``online``-Event ohne jede Nutzerinteraktion Widerrufslisten-, CSRF- und
# Bundle-Requests OHNE ``X-Offline-Replay``-Marker — ein Geraet mit
# flappender Verbindung hielte seine Sitzung sonst unbegrenzt offen. Dass
# damit auch der nutzerausgeloeste Bundle-Download nicht erneuert, ist
# bewusst in Kauf genommen (die Seite, von der er ausgeht, hat erneuert).
BACKGROUND_SYNC_PATH_PREFIX = "/api/v1/offline/"


class SessionIdleRefreshMiddleware:
    """Deckelt Sessions absolut und verlaengert sie bei Aktivitaet.

    Muss NACH ``AuthenticationMiddleware`` liegen (braucht ``request.user``).
    Die Mutation passiert in der Request-Phase; den Save erledigt die
    ``SessionMiddleware`` ausgangsseitig wie bei jedem anderen Session-Write.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            if self._absolute_cap_expired(request):
                # Sitzung ist beendet — keine Erneuerung mehr, der Request
                # laeuft anonym weiter und laeuft in den View-eigenen
                # Login-Redirect (siehe Modul-Docstring).
                return self.get_response(request)
            if (
                # Offline-Queue-Replays sind Hintergrund-Sync, keine
                # Nutzeraktivitaet (vgl. htmx_session.py, Refs #1419) — sie
                # duerfen das Fenster nicht offen halten.
                not request.headers.get("X-Offline-Replay")
                # ... ebenso die restliche Sync-Sequenz (laeuft ohne den Marker).
                and not request.path.startswith(BACKGROUND_SYNC_PATH_PREFIX)
            ):
                self._renew_idle_window(request.session)
        return self.get_response(request)

    @staticmethod
    def _absolute_cap_expired(request) -> bool:
        """Prueft das absolute Sitzungs-Maximum (Refs #1868).

        Gibt ``True`` zurueck, wenn die Sitzung dabei beendet wurde. Der
        ``logout()`` flusht die Session-Zeile und loest ueber
        ``user_logged_out`` den LOGOUT-AuditLog aus (``core/signals/audit.py``)
        — dieselbe Spur wie bei einem Abmelde-Klick.

        Fehlt der Schluessel, ist die Sitzung aelter als dieses Feature
        (Bestands-Session) oder per ``force_login`` erzeugt: dann wird das
        Maximum EINMALIG ab jetzt gestempelt, statt sofort auszuloggen. Der
        Default-Wert statt des Facility-Werts ist Absicht — hier ist kein
        Facility-Kontext geladen, und ein DB-Roundtrip pro Request waere fuer
        einen einmaligen Uebergangsfall zu teuer.

        Der Grace-Stempel laeuft bewusst auch auf den Hintergrund-Sync-Pfaden,
        die vom Inaktivitaets-Fenster ausgenommen sind. Nebenwirkung: dieser
        eine Session-Write verlaengert dort einmalig auch das
        Inaktivitaets-Fenster. Das ist der kleinere Preis — wuerde nur auf
        Nicht-Sync-Requests gestempelt, bekaeme eine Session, die
        ausschliesslich synct, nie eine Deadline und liefe damit dauerhaft am
        Cap vorbei. Einmalig, weil der Schluessel danach steht.
        """
        session = request.session
        deadline = session.get(ABSOLUTE_DEADLINE_KEY)
        now = timezone.now().timestamp()
        if not isinstance(deadline, (int, float)):
            session[ABSOLUTE_DEADLINE_KEY] = now + DEFAULT_SESSION_ABSOLUTE_HOURS * 3600
            return False
        if now < deadline:
            return False
        logout(request)
        return True

    @staticmethod
    def _renew_idle_window(session) -> None:
        """Erneuert das Inaktivitaets-Fenster, gedrosselt (Refs #1858)."""
        window = session.get_expiry_age()
        now = timezone.now().timestamp()
        renewed_at = session.get(LAST_RENEWAL_KEY)
        if not isinstance(renewed_at, (int, float)) or now - renewed_at >= window * RENEWAL_FRACTION:
            session[LAST_RENEWAL_KEY] = now
