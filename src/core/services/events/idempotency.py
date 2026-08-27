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

Pepper (Refs #1942, aus dem Review zu #1935): Der Fingerprint ist seither ein
**HMAC-SHA-256 mit serverseitigem Pepper**, nicht mehr ein nackter Digest des
Klartext-Payloads. Ungesalzen war er bei Dokumentationstypen ohne Freitextfeld
(z. B. rein numerische Pflichtfelder) ein Enumerations-/Rekonstruktions-Orakel
für redigierte ``data_json``-Inhalte — und zwar an der At-Rest-Verschlüsselung
(``FieldTemplate.is_encrypted``) vorbei, weil der Digest VOR der
Verschlüsselung entsteht und unverschlüsselt in derselben Tabelle landet. Wer
den Kandidatenraum durchprobiert, vergleicht Digests und liest den Klartext ab.
Der Idempotenz-Zweck bleibt vollständig erhalten: verglichen wird ohnehin nur
innerhalb derselben (scope, user, key)-Kette.

Der Pepper wird aus dem vorhandenen ``ENCRYPTION_KEY``/``ENCRYPTION_KEYS``
abgeleitet (Domain-Separation, :func:`_fingerprint_pepper`) — **kein neues
Pflicht-ENV**: diese Anwendung betreiben Einrichtungen selbst, jede weitere
Pflichtvariable ist ein zusätzlicher Weg, eine Installation fail-closed
lahmzulegen. Der Fingerprint trägt die Pepper-Generation als Kennung mit
(``h1:<pepper-id>:<hmac>``).

**Schlüsselrotation erhält den Schutz** (Refs #1942, Cross-Review):
:func:`has_idempotency_payload_mismatch` bekommt den *Request*, nicht einen
vorberechneten Hash, und rechnet einen gespeicherten Fingerprint unter genau
der Generation nach, unter der er entstand — solange deren Schlüssel noch in
``ENCRYPTION_KEYS`` steht. Während einer Rotation stehen Alt- und
Neuschlüssel dort ohnehin nebeneinander (``MultiFernet``-Muster), der
N14-Kollisionsschutz bleibt also über die gesamte Rotation hinweg
vollständig.

Ohne Vergleichsgrundlage bleiben genau zwei Fälle; beide gelten wie das seit
N14 dokumentierte ``NULL`` als Treffer **ohne** Payload-Prüfung (sonst liefe
jeder Replay eines Alt-Events in einen falschen Kollisions-422):

* **Legacy-Bestandszeilen** (nackter SHA-256 ohne Schema-Präfix),
* **ausgelaufene Generationen** — der Schlüssel wurde aus ``ENCRYPTION_KEYS``
  entfernt. Das ist ein **dauerhafter** Verlust für diese Zeilen, denn
  ``Event``-Zeilen werden nie hart gelöscht; er ist aber eine bewusste
  Betreiber-Entscheidung (Altschlüssel entfernen) und nicht die Folge einer
  Rotation an sich. Ausführlich, mit Handlungsempfehlung, in
  ``docs/security-notes.md`` § Härtung per HMAC-Pepper.

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
* ``Event`` trägt den Payload-Hash seit #1909 zusätzlich persistent. Damit
  erkennen EventCreate und Tally eine Kollision auch nach Cache-Verlust und im
  parallelen Constraint-Race. Bestehende Event-Zeilen mit ``NULL`` sowie
  **Legacy-Cache-Einträge** (nackter PK-String aus der Zeit vor N14) gelten aus
  Abwärtskompatibilität weiter als Treffer ohne Hash-Prüfung. ``WorkItem`` hat
  unverändert keinen persistenten Payload-Hash; dort bleibt diese Grenze für
  den DB-Backstop bestehen.
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

import base64
import hashlib
import hmac
import json
import re
from typing import NamedTuple

from django.core.cache import cache

from core.services.file_vault.encryption import EncryptionKeyMissing, _get_keys

CSRF_FIELD = "csrfmiddlewaretoken"
IDEMPOTENCY_FORM_FIELD = "idempotency_key"

# Transport-Metadaten sind kein fachlicher Payload: CSRF rotiert bei einem
# Stale-CSRF-Retry; der Tally-Key kann kanonisch im Header und für No-JS als
# Formular-Fallback ankommen. Beides darf keinen False-Positive-422 erzeugen.
PAYLOAD_FINGERPRINT_EXCLUDED_FIELDS = frozenset({CSRF_FIELD, IDEMPOTENCY_FORM_FIELD})

# R6/N14: Der Client generiert UUIDs — alles ausserhalb dieses engen Formats
# (ueberlang/binaer/Steuerzeichen) wird verworfen statt in DB-Spalte
# (CharField max_length=64) und Cache-Key zu wandern.
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Refs #1942: Schema-Praefix des gepfefferten Fingerprints. Es ist der einzige
# Unterschied zu einem Legacy-Wert (nackter SHA-256-Hex aus der Zeit davor) —
# beide waeren sonst 64 Zeichen und ununterscheidbar.
FINGERPRINT_SCHEME = "h1"

# Domain-Separation der Pepper-Ableitung: derselbe ENCRYPTION_KEY dient auch
# der Feld-/Datei-Verschluesselung; der Pepper darf mit keinem anderen
# Gebrauch zusammenfallen.
_PEPPER_DOMAIN = b"anlaufstelle:idempotency-fingerprint:v1:"

# Laenge der Pepper-Kennung im Fingerprint. Sie unterscheidet
# Schluessel-Generationen und traegt kein Schluesselmaterial (Hash des
# Pepper-Hashes); 8 Hex-Zeichen reichen dafuer weit.
_PEPPER_ID_LENGTH = 8
_PEPPER_ID_RE = re.compile(rf"^[0-9a-f]{{{_PEPPER_ID_LENGTH}}}$")


def normalize_idempotency_key(raw: str | None) -> str | None:
    """Validiert den Client-Schluessel; ungueltige Keys degradieren zum
    Legacy-Verhalten ohne Dedup (kein 500, kein Cache-Muell)."""
    if not raw or not IDEMPOTENCY_KEY_RE.fullmatch(raw):
        return None
    return raw


def resolve_idempotency_key(
    header_value: str | None,
    fallback_value: str | None,
    *,
    required: bool = False,
) -> str | None:
    """Löse kanonischen Header und Formular-Fallback widerspruchsfrei auf.

    Der Header ist der kanonische Transport für Service-Worker-Replays. Ein
    gleichlautendes Formularfeld hält klassische No-JS-POSTs idempotent. Sind
    beide vorhanden, müssen beide einzeln gültig und identisch sein; andernfalls
    darf keine der beiden Angaben still bevorzugt werden.
    """
    header_key = normalize_idempotency_key(header_value)
    fallback_key = normalize_idempotency_key(fallback_value)
    if header_value is not None and header_key is None:
        raise ValueError("invalid idempotency header")
    if fallback_value is not None and fallback_key is None:
        raise ValueError("invalid idempotency fallback")
    if header_key is not None and fallback_key is not None and header_key != fallback_key:
        raise ValueError("conflicting idempotency keys")
    key = header_key or fallback_key
    if required and key is None:
        raise ValueError("missing idempotency key")
    return key


def has_idempotency_payload_mismatch(stored_hash: str | None, request) -> bool:
    """Widerspricht der gespeicherte Fingerprint dem aktuellen Request?

    Zweiter Parameter ist der **Request**, nicht ein vorberechneter Hash
    (geaendert in #1942/Cross-Review): Nur so laesst sich ein gespeicherter
    Fingerprint auch dann pruefen, wenn er unter einer **frueheren
    Schluessel-Generation** entstanden ist — die Funktion rechnet ihn dann
    unter genau jenem Pepper nach, statt den Schutz aufzugeben. Waehrend einer
    ``ENCRYPTION_KEYS``-Rotation stehen beide Schluessel nebeneinander
    (``MultiFernet``-Muster), der Kollisionsschutz bleibt also ueber die
    gesamte Rotation hinweg vollstaendig erhalten.

    Es bleiben genau zwei Faelle ohne Vergleichsgrundlage. Beide sind kein
    Kollisions-*Beweis* und werden deshalb wie das seit N14 dokumentierte
    ``NULL`` behandelt — Treffer OHNE Payload-Pruefung statt 422, sonst liefe
    jeder Replay eines Alt-Events in einen falschen Kollisions-422 und landete
    als Dead Letter in der Offline-UI:

    * **Legacy-Bestand** — Zeilen und Cache-Eintraege aus der Zeit vor #1942
      tragen einen nackten SHA-256-Digest ohne Schema-Praefix. Der Klartext-
      Payload existiert nicht mehr, sie sind nicht nachrechenbar.
    * **Ausgelaufene Generation** — der Schluessel, unter dem der Fingerprint
      entstand, steht nicht mehr in ``ENCRYPTION_KEYS``. Das ist der einzige
      dauerhafte Restfall, und er liegt in der Hand der Betreiberin: solange
      der Altschluessel konfiguriert bleibt (was er fuer Alt-Chiffrate ohnehin
      muss, bis ``reencrypt_fields`` gelaufen ist), greift der Schutz.
      Ausfuehrlich in ``docs/security-notes.md`` § Haertung per HMAC-Pepper.
    """
    if stored_hash is None:
        return False
    stored_pepper_id = _fingerprint_pepper_id(stored_hash)
    if stored_pepper_id is None:
        return False  # Legacy-Digest ohne Schema-Praefix
    pepper = _pepper_for_id(stored_pepper_id)
    if pepper is None:
        return False  # Generation ausgelaufen (Schluessel nicht mehr konfiguriert)
    expected = _fingerprint_for_pepper(pepper, _canonical_payload(request))
    # Timing-sicherer Vergleich: es ist ein MAC-Vergleich, auch wenn der
    # primaere Threat der Offline-Dump ist und die Antwort (Erfolg vs. 422)
    # das Ergebnis ohnehin offenlegt. Ueber Bytes, weil ``stored_hash`` aus
    # der DB kommt und ``compare_digest`` auf str nur ASCII akzeptiert.
    return not hmac.compare_digest(stored_hash.encode("utf-8"), expected.encode("utf-8"))


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


def _pepper_from_key(key) -> bytes:
    """Pepper einer einzelnen Schluessel-Generation."""
    return hashlib.sha256(_PEPPER_DOMAIN + (key.encode("utf-8") if isinstance(key, str) else key)).digest()


def _pepper_id(pepper: bytes) -> str:
    """Kennung einer Pepper-Generation — Hash des Peppers, kein Schluesselmaterial."""
    return hashlib.sha256(pepper).hexdigest()[:_PEPPER_ID_LENGTH]


def _pepper_for_id(pepper_id: str) -> bytes | None:
    """Der Pepper zu einer Generations-Kennung — ``None``, wenn deren
    Schluessel nicht mehr konfiguriert ist (Refs #1942/Cross-Review).

    Durchsucht ALLE konfigurierten Schluessel, nicht nur den ersten: waehrend
    einer Rotation stehen Alt- und Neuschluessel gemeinsam in
    ``ENCRYPTION_KEYS`` (``MultiFernet``-Muster), und genau dann muessen auch
    Fingerprints der Vorgaenger-Generation noch pruefbar sein.
    """
    for key in _get_keys():
        pepper = _pepper_from_key(key)
        if hmac.compare_digest(_pepper_id(pepper), pepper_id):
            return pepper
    return None


def _canonical_payload(request) -> str:
    """Kanonische, eindeutige Form des fachlichen Request-Payloads (N14).

    Alle ``request.POST.lists()``-Paare **außer** den
    :data:`PAYLOAD_FINGERPRINT_EXCLUDED_FIELDS`, als sortierte
    ``(name, wert)``-Paare; plus je Upload in ``request.FILES`` das Tripel
    ``(feldname, dateiname, größe)``, ebenfalls sortiert. Beides via JSON
    serialisiert (kompakte Separatoren, feste Struktur) — die Feld-Reihenfolge
    im Formular ist damit irrelevant, jede Wert-/Datei-Änderung nicht.
    """
    post_pairs = sorted(
        (name, value)
        for name, values in request.POST.lists()
        if name not in PAYLOAD_FINGERPRINT_EXCLUDED_FIELDS
        for value in values
    )
    file_triples = sorted((name, f.name, f.size) for name, files in request.FILES.lists() for f in files)
    return json.dumps({"post": post_pairs, "files": file_triples}, ensure_ascii=False, separators=(",", ":"))


def _fingerprint_for_pepper(pepper: bytes, canonical: str) -> str:
    """``<schema>:<pepper-id>:<hmac>`` — base64url ohne Padding.

    Die Pepper-Kennung macht die Generation erkennbar (und damit pruefbar,
    solange ihr Schluessel konfiguriert ist); base64url haelt die vollen
    256 Bit in ``CharField(max_length=64)`` — 55 Zeichen statt 76 in Hex.
    """
    digest = hmac.new(pepper, canonical.encode("utf-8"), hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{FINGERPRINT_SCHEME}:{_pepper_id(pepper)}:{encoded}"


def _fingerprint_pepper() -> bytes:
    """Serverseitiger Pepper des Payload-Fingerprints (Refs #1942).

    Abgeleitet aus dem vorhandenen ``ENCRYPTION_KEY``/``ENCRYPTION_KEYS`` —
    **kein** neues Pflicht-ENV, denn diese Anwendung betreiben Einrichtungen
    selbst, und jede zusaetzliche Pflichtvariable ist ein weiterer Weg, eine
    Installation fail-closed lahmzulegen. Die Domain-Separation
    (:data:`_PEPPER_DOMAIN`) stellt sicher, dass der Pepper mit keinem anderen
    Gebrauch desselben Schluessels zusammenfaellt; aus dem Pepper laesst sich
    der Fernet-Schluessel nicht zurueckrechnen.

    Kanonisch ist der ERSTE konfigurierte Schluessel — derselbe, mit dem
    ``MultiFernet`` verschluesselt. ``_get_keys`` ist bewusst die gemeinsame
    Quelle: eine zweite Parser-Kopie wuerde bei kuenftigen Aenderungen driften.

    Fehlt der Schluessel, bricht die Funktion ab statt auf einen ungepfefferten
    Digest zurueckzufallen — das waere genau die Luecke, die #1942 schliesst.
    Eine Installation ohne ``ENCRYPTION_KEY`` kann ohnehin kein
    verschluesseltes Feld schreiben (:func:`get_fernet`).

    Bewusst NICHT gecacht: die Ableitung ist ein einzelner SHA-256 ueber
    wenige Bytes, und ein Prozess-Cache braeuchte dieselbe
    ``setting_changed``-Invalidierung wie ``get_fernet`` — eine Fehlerquelle
    mehr fuer einen Gewinn, den niemand misst.
    """
    keys = _get_keys()
    if not keys:
        raise EncryptionKeyMissing(
            "ENCRYPTION_KEY / ENCRYPTION_KEYS ist nicht gesetzt — der Idempotenz-Fingerprint "
            "braucht ihn als Pepper (Refs #1942)."
        )
    return _pepper_from_key(keys[0])


def _fingerprint_pepper_id(fingerprint: str | None) -> str | None:
    """Pepper-Generation eines Fingerprints — ``None`` bei Legacy-Werten.

    Traegt der Wert nicht das Schema-Praefix (nackter SHA-256 aus der Zeit vor
    #1942), ist er keiner Generation zuzuordnen und damit nicht vergleichbar.
    """
    if not fingerprint:
        return None
    parts = fingerprint.split(":", 2)
    if len(parts) != 3 or parts[0] != FINGERPRINT_SCHEME:
        return None
    # Die Kennung wird streng validiert, nicht bloss abgeschnitten: der Wert
    # kommt aus der DB bzw. dem Cache, und ``hmac.compare_digest`` wirft auf
    # Nicht-ASCII einen ``TypeError``. Ein beschaedigter Wert soll wie ein
    # Legacy-Wert behandelt werden, nicht den Request mit einem 500 abbrechen.
    if not _PEPPER_ID_RE.fullmatch(parts[1]):
        return None
    return parts[1]


def payload_fingerprint(request) -> str:
    """Gepfefferter Fingerprint über die kanonische Form des Request-Payloads.

    Der Rückgabewert ist der Fingerprint der **aktuellen** (ersten
    konfigurierten) Schlüssel-Generation — das ist der Wert, der gespeichert
    wird. Geprüft wird über :func:`has_idempotency_payload_mismatch`, die auch
    ältere Generationen nachrechnen kann.

    Kanonisierung: siehe :func:`_canonical_payload` (N14, Refs #1424) —
    unverändert gegenüber dem ungesalzenen Vorgänger.

    Refs #1942: Über diese kanonische Form läuft seither **HMAC-SHA-256 mit
    serverseitigem Pepper** statt eines nackten Digests. Der nackte Digest war
    bei Dokumentationstypen ohne Freitextfeld (z. B. rein numerische
    Pflichtfelder) ein Enumerations-/Rekonstruktions-Orakel für redigierte
    ``data_json``-Inhalte — an der At-Rest-Verschlüsselung vorbei, weil der
    Fingerprint vor der Verschlüsselung entsteht und unverschlüsselt in
    derselben Tabelle liegt. Der Idempotenz-Zweck bleibt vollständig erhalten:
    verglichen wird ohnehin nur innerhalb derselben (scope, user, key)-Kette.
    """
    return _fingerprint_for_pepper(_fingerprint_pepper(), _canonical_payload(request))


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
