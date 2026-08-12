"""Middleware: erneuert das Session-Fenster bei Nutzeraktivitaet (Refs #1858).

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

from django.utils import timezone

# Session-Key mit Unix-Timestamp der letzten Fenster-Erneuerung. Wird beim
# Login mitgestempelt (views/auth.py), damit der erste Request nach dem Login
# keinen zusaetzlichen Write ausloest.
LAST_RENEWAL_KEY = "_idle_window_renewed_at"

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
    """Verlaengert authentifizierte Sessions bei Aktivitaet (Sliding Window).

    Muss NACH ``AuthenticationMiddleware`` liegen (braucht ``request.user``).
    Die Mutation passiert in der Request-Phase; den Save erledigt die
    ``SessionMiddleware`` ausgangsseitig wie bei jedem anderen Session-Write.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            # Offline-Queue-Replays sind Hintergrund-Sync, keine
            # Nutzeraktivitaet (vgl. htmx_session.py, Refs #1419) — sie
            # duerfen das Fenster nicht offen halten.
            and not request.headers.get("X-Offline-Replay")
            # ... ebenso die restliche Sync-Sequenz (laeuft ohne den Marker).
            and not request.path.startswith(BACKGROUND_SYNC_PATH_PREFIX)
        ):
            session = request.session
            window = session.get_expiry_age()
            now = timezone.now().timestamp()
            renewed_at = session.get(LAST_RENEWAL_KEY)
            if not isinstance(renewed_at, (int, float)) or now - renewed_at >= window * RENEWAL_FRACTION:
                session[LAST_RENEWAL_KEY] = now
        return self.get_response(request)
