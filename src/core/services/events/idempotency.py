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

* Der Cache bleibt der Fast-Path, ist aber seit Review R5/R6 nicht mehr die
  alleinige Dedup-Garantie: ``Event`` und ``WorkItem`` tragen den Schlüssel
  zusätzlich als Spalte ``idempotency_key`` mit einem partiellen
  Unique-Constraint je ``created_by`` (``event_idem_key_per_user_uniq`` /
  ``workitem_idem_key_per_user_uniq``). Fällt der Cache aus (Eviction,
  Worker-Neustart — R5) oder passieren zwei parallele Replays gleichzeitig das
  get-then-set-Fenster (R6), fängt dieser DB-Backstop das Duplikat ab: der
  View macht nach einem Cache-Miss bzw. einem ``IntegrityError`` einen
  DB-Lookup und leitet auf den Originaldatensatz um. In Produktion ist der
  Cache der ``DatabaseCache`` (persistent, shared über Worker,
  ``settings/prod.py``); im LocMem-Default (dev/test) gilt der Fast-Path pro
  Prozess — für den seriellen Replay-Fall ausreichend, den harten Rand deckt
  der Constraint.
* Das Zeitfenster ist die TTL (72 h, Refs #1329). Sie ist bewusst **länger**
  als die Offline-Bundle-Lease (``BUNDLE_TTL_SECONDS = 48 h`` in
  :mod:`core.services.system.offline`) plus ein Retry-Fenster für einen
  Reconnect kurz vor Lease-Ablauf — Invariante ``TTL ≥ Bundle-Lease``. Der
  ursprüngliche Default (24 h) verletzte diese Invariante: ein Replay kurz
  vor Lease-Ablauf, aber nach TTL-Ablauf des Dedup-Keys, hätte wieder ein
  Duplikat erzeugt. Ein Replay nach Ablauf der (jetzt 72 h langen) TTL würde
  weiterhin ein neues Objekt erzeugen — akzeptiert, weil ein so später
  Replay ohnehin verworfen werden sollte.
"""

from __future__ import annotations

import re

from django.core.cache import cache

# R6/N14: Der Client generiert UUIDs — alles ausserhalb dieses engen Formats
# (ueberlang/binaer/Steuerzeichen) wird verworfen statt in DB-Spalte
# (CharField max_length=64) und Cache-Key zu wandern.
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def normalize_idempotency_key(raw: str | None) -> str | None:
    """Validiert den Client-Schluessel; ungueltige Keys degradieren zum
    Legacy-Verhalten ohne Dedup (kein 500, kein Cache-Muell)."""
    if not raw or not IDEMPOTENCY_KEY_RE.fullmatch(raw):
        return None
    return raw


# Wie lange ein Idempotenz-Schlüssel als „erledigt" gilt. Muss >= der
# Offline-Bundle-Lease sein (``BUNDLE_TTL_SECONDS`` in
# ``core.services.system.offline``, aktuell 48 h) plus ein Retry-Fenster für
# Reconnects kurz vor Lease-Ablauf (Refs #1329) — sonst dedupliziert ein
# später Replay nicht mehr zuverlässig. Modul bleibt generisch: gilt für
# jeden Scope (``event_create``, ``workitem_create``, …), nicht nur Events.
IDEMPOTENCY_TTL_SECONDS = 72 * 3600

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
