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

Payload-Hash (N14, Refs #1424): Der Cache-Eintrag merkt sich neben der Ziel-PK
einen ``payload_hash`` (:func:`payload_fingerprint`) des erfolgreichen
Requests. Ein Replay mit **demselben Key, aber abweichendem Body** ist ein
Key-Kollisions-Kurzschluss (der Client-Schlüssel ist frei wählbar) — statt den
gecachten Erfolg zurückzugeben, liefert der View dann einen Fehler (422), damit
die abweichende Anlage nicht still unter dem falschen Datensatz verschwindet.

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
* Der **DB-Backstop trägt bewusst KEINEN Payload-Hash**: die Spalte
  ``idempotency_key`` bekommt keine Hash-Erweiterung (keine Migration — N14 ist
  Info-Schwere). Fällt der Cache aus (Eviction/Neustart) und greift nur noch
  der DB-Lookup, wird ein Key-Treffer daher OHNE Payload-Prüfung als Duplikat
  behandelt (Redirect aufs Original) — ein abweichender Body im seltenen
  Cache-Verlust-Fenster bleibt unentdeckt. Ebenso gehen **Legacy-Cache-Einträge**
  (nackter PK-String aus der Zeit vor N14, TTL bis 72 h nach Deploy) als
  Treffer ohne Hash-Prüfung durch (Abwärtskompatibilität, kein False-Positive-
  422). Beides ist der akzeptierte Rand des Payload-Vergleichs.
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

import hashlib
import json
import re
from typing import NamedTuple

from django.core.cache import cache

# Feld, das aus dem Payload-Fingerprint ausgeschlossen wird: Der CSRF-Token
# wird beim legitimen Stale-CSRF-Retry (Refs #1331/#1333) nach einem 403 neu
# geholt. Fliesst er in den Hash ein, schlaegt der konforme Replay
# faelschlich als Payload-Mismatch (422) fehl — deshalb hart ausgeklammert.
CSRF_FIELD = "csrfmiddlewaretoken"

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


class IdempotentHit(NamedTuple):
    """Cache-Treffer für einen bereits erfolgreich verarbeiteten Schlüssel.

    ``payload_hash`` ist ``None`` bei einem Legacy-Eintrag (nackter PK-String
    aus der Zeit vor N14) — der View MUSS diesen Fall als Treffer OHNE
    Payload-Prüfung behandeln (Abwärtskompatibilität, siehe Modul-Docstring).
    """

    pk: str
    payload_hash: str | None


def payload_fingerprint(request) -> str:
    """SHA-256-Fingerprint über die kanonische Form des Request-Payloads (N14).

    Kanonisierung: alle ``request.POST.lists()``-Paare **außer**
    :data:`CSRF_FIELD` (der Stale-CSRF-Retry #1331/#1333 erneuert den Token —
    er darf den Hash nicht beeinflussen), als sortierte ``(name, wert)``-Paare;
    plus je Upload in ``request.FILES`` das Tripel ``(feldname, dateiname,
    größe)``, ebenfalls sortiert. Beides eindeutig via JSON serialisiert
    (kompakte Separatoren, feste Struktur), dann gehasht — die Feld-Reihenfolge
    im Formular ist damit irrelevant, jede Wert-/Datei-Änderung nicht.
    """
    post_pairs = sorted(
        (name, value) for name, values in request.POST.lists() if name != CSRF_FIELD for value in values
    )
    file_triples = sorted((name, f.name, f.size) for name, files in request.FILES.lists() for f in files)
    canonical = json.dumps({"post": post_pairs, "files": file_triples}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_idempotent_result(scope: str, user_id, idempotency_key: str | None) -> IdempotentHit | None:
    """Liefert den gemerkten Treffer (Ziel-PK + Payload-Hash) für einen Schlüssel.

    Gibt ``None`` zurück, wenn kein Schlüssel mitgeschickt wurde oder der
    Schlüssel noch nicht (erfolgreich) verarbeitet wurde. Legacy-Cache-Werte
    (nackter PK-String aus der Zeit vor N14) kommen als Treffer mit
    ``payload_hash=None`` zurück — der Aufrufer verzichtet dann auf die
    Payload-Prüfung (siehe Modul-Docstring „Grenzen").
    """
    if not idempotency_key:
        return None
    cached = cache.get(_cache_key(scope, user_id, idempotency_key))
    if cached is None:
        return None
    if isinstance(cached, dict):
        return IdempotentHit(pk=cached["pk"], payload_hash=cached.get("payload_hash"))
    # Legacy-Format (nackter PK-String, vor N14): Treffer ohne Hash-Prüfung.
    return IdempotentHit(pk=str(cached), payload_hash=None)


def remember_idempotent_result(
    scope: str, user_id, idempotency_key: str | None, result_pk, payload_hash: str | None = None
) -> None:
    """Merkt sich das Ergebnis eines erfolgreichen Writes unter dem Schlüssel.

    ``payload_hash`` (N14, :func:`payload_fingerprint`) erlaubt beim nächsten
    Treffer den Payload-Vergleich — eine Key-Kollision mit anderem Body wird
    so erkennbar statt still den gecachten Erfolg zu liefern. No-op, wenn kein
    Schlüssel mitgeschickt wurde (Online-Direkt-Submit ohne Offline-Queue
    verhält sich unverändert).
    """
    if not idempotency_key:
        return
    cache.set(
        _cache_key(scope, user_id, idempotency_key),
        {"pk": str(result_pk), "payload_hash": payload_hash},
        IDEMPOTENCY_TTL_SECONDS,
    )
