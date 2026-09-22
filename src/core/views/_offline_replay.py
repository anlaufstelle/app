"""Bestätigungsmarker des Offline-Replays (Refs #2289).

Der Offline-Client (``offline-edit.js``/``offline-queue.js``) markiert jeden
Replay-Request mit ``X-Offline-Replay: 1``. Bis 0.99.1 galt für ihn die
Antwort-*Form* als Erfolgsbeleg: ein gefolgter Redirect auf ein beliebiges
Ziel außer ``/login/``. Das ist kein Beleg — ``fetch`` folgt einer
Weiterleitung transparent, und Middleware kann eine Schreibanfrage **vor**
der View auf die Zwei-Faktor-Abfrage (``/mfa/setup/``, ``/mfa/verify/``) oder
den Passwortwechsel umleiten. Die Speicherung fand dann nie statt, die
Antwort sah aber aus wie ein Erfolg, und der Client verwarf die lokale,
serverseitig nirgends existierende Arbeit.

Dieses Modul liefert die *positive* Bestätigung: eine ``200``-Antwort mit dem
Header ``X-Offline-Replay-Result: saved``, die ausschließlich **nach** einem
festgeschriebenen Schreibvorgang (oder bei einem Idempotenz-Treffer auf einen
bereits festgeschriebenen) entsteht. Nur markierte Requests bekommen sie —
jeder andere Aufrufer behält unverändert seinen Redirect samt Flash-Meldung.

Bewusst **statt** des Redirects, nicht zusätzlich: ``fetch`` legt einem
Aufrufer nur die Header der *End*antwort offen, ein Header auf dem ``302``
wäre für den Replay unsichtbar. Die Antwort ist deshalb zugleich die Stelle,
an der der Replay-Pfad keine ``messages``-Flash-Meldung mehr erzeugt — sie
würde sonst erst bei der nächsten echten Seitenansicht der Nutzerin
auftauchen, obwohl dort nie jemand etwas gespeichert hat.

Gegenrichtung (Client neu, Server alt oder umgekehrt) ist fail-safe: ohne
Marker gilt der Replay als *unbestätigt*, der lokale Datensatz bleibt liegen
und wird beim nächsten Kontakt erneut gespielt — der Idempotenz-Schlüssel
(F-09, Refs #1109) verhindert dabei eine Doppelanlage.
"""

from django.http import JsonResponse

#: Marker, mit dem der Offline-Client seine Replay-Requests kennzeichnet
#: (gesetzt in ``offline-edit.js``/``offline-queue.js``, vom Service Worker
#: network-only durchgereicht).
OFFLINE_REPLAY_HEADER = "X-Offline-Replay"

#: Bestätigungs-Header der Schreib-Endpunkte.
OFFLINE_REPLAY_RESULT_HEADER = "X-Offline-Replay-Result"

#: Einziger Wert, den der Client als Erfolg akzeptiert.
OFFLINE_REPLAY_RESULT_SAVED = "saved"


def is_offline_replay(request) -> bool:
    """True, wenn der Request vom Offline-Replay stammt."""
    return bool(request.headers.get(OFFLINE_REPLAY_HEADER))


def saved_confirmation(request, result_pk):
    """Bestätigte Erfolgsantwort für einen Offline-Replay — sonst ``None``.

    Aufrufstelle ist jeweils der Punkt, an dem der Schreibvorgang
    festgeschrieben ist und die View sonst auf die Detail-/Listenseite
    umleiten würde. ``None`` bedeutet „kein Replay" — die View macht dann
    unverändert weiter (Flash-Meldung + Redirect).
    """
    if not is_offline_replay(request):
        return None
    response = JsonResponse(
        {"status": OFFLINE_REPLAY_RESULT_SAVED, "pk": str(result_pk)},
        status=200,
    )
    response[OFFLINE_REPLAY_RESULT_HEADER] = OFFLINE_REPLAY_RESULT_SAVED
    return response
