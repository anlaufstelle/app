"""Idempotenz-Guard für Offline-Queue-Replays (F-09, Refs #1109).

Problem (Security-Review 2026-06-14, Abschnitt 6.3): Geht ein nicht-idempotenter
POST (Event-Create) auf dem Server durch, bricht aber die Verbindung *vor*
Empfang der Response ab, bleibt die Zeile in der client-seitigen Offline-Queue
und wird beim nächsten ``online``-Event erneut gespielt → Doppel-Submit.

Lösung ohne neues DB-Model: Der Client erzeugt pro Queue-Eintrag eine UUID und
schickt sie als Header ``X-Idempotency-Key``. Der Server merkt sich beim ersten
Erfolg unter diesem Key die erzeugte Ziel-PK in Djangos Cache-Framework (kurze
TTL). Ein Replay mit demselben Key liefert die gemerkte PK zurück, statt ein
zweites Objekt zu erzeugen.

**Grenzen (bewusst, vom Maintainer zu reviewen):**

* Kein DB-Model → die Dedup-Garantie hängt am Cache-Backend. In Produktion ist
  das der ``DatabaseCache`` (persistent, shared über Worker, ``settings/prod.py``),
  also über Prozesse hinweg konsistent. Im LocMem-Default (dev/test) gilt sie
  pro Prozess — für den dokumentierten Replay-Fall (serielles Re-Drainen
  derselben Queue durch denselben Client) ausreichend.
* Das Zeitfenster ist die TTL (Default 24 h, deckt den Offline-Bundle-Lease von
  48 h nicht voll ab, aber jeden realistischen Replay-Zyklus nach
  Verbindungsabbruch). Ein Replay nach Ablauf der TTL würde wieder ein neues
  Objekt erzeugen — akzeptiert, weil ein so später Replay nach Lease-Ablauf
  ohnehin verworfen werden sollte.
"""

from __future__ import annotations

from django.core.cache import cache

# Wie lange ein Idempotenz-Schlüssel als „erledigt" gilt. Großzügig genug für
# jeden Reconnect-/Replay-Zyklus, aber begrenzt, damit der Cache nicht wächst.
IDEMPOTENCY_TTL_SECONDS = 24 * 3600

# Marker für „in Bearbeitung / fehlgeschlagen, aber noch kein Ergebnis" — wird
# bewusst NICHT genutzt: ein fehlgeschlagener Versuch darf den Key nicht
# verbrennen (siehe ``test_failed_create_does_not_poison_key``). Wir cachen
# daher ausschließlich nach erfolgreichem Write.


def _cache_key(scope: str, user_id, idempotency_key: str) -> str:
    """Cache-Key, der pro (Aktion, User, Client-Schlüssel) eindeutig ist.

    Das User-Scoping verhindert, dass ein (vom Client frei wählbarer) Schlüssel
    eines anderen Nutzers ein fremdes Ergebnis zurückliefert.
    """
    return f"idem:{scope}:{user_id}:{idempotency_key}"


def get_idempotent_result(scope: str, user_id, idempotency_key: str | None):
    """Liefert die gemerkte Ziel-PK für einen bereits verarbeiteten Schlüssel.

    Gibt ``None`` zurück, wenn kein Schlüssel mitgeschickt wurde oder der
    Schlüssel noch nicht (erfolgreich) verarbeitet wurde.
    """
    if not idempotency_key:
        return None
    return cache.get(_cache_key(scope, user_id, idempotency_key))


def remember_idempotent_result(scope: str, user_id, idempotency_key: str | None, result_pk) -> None:
    """Merkt sich das Ergebnis eines erfolgreichen Writes unter dem Schlüssel.

    No-op, wenn kein Schlüssel mitgeschickt wurde (Online-Direkt-Submit ohne
    Offline-Queue verhält sich unverändert).
    """
    if not idempotency_key:
        return
    cache.set(_cache_key(scope, user_id, idempotency_key), str(result_pk), IDEMPOTENCY_TTL_SECONDS)
