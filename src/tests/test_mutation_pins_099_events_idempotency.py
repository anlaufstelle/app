"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Batch ``events-2``
(Vollmutation-Survivor-Triage), Modul ``core.services.events.idempotency``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-events-2.json`` (Triage
``triage-099-events-2.json``).

Getoetete Mutanten (voller ``x...__mutmut_N``-Name, Modul
``core.services.events.idempotency``):

* **``x_resolve_idempotency_key__mutmut_1``**: der Default von ``required``
  kippt von ``False`` auf ``True``. Der einzige Produktionsaufrufer
  (``TallyIncrementView.post``, ``src/core/views/events.py``) uebergibt
  ``required=True`` immer explizit — der Default ist trotzdem oeffentliches
  Verhalten der Funktion und direkt beobachtbar: ohne ``required``-Angabe
  darf ein fehlender Schluessel NICHT zum ``ValueError`` fuehren.

* **``x__canonical_payload__mutmut_8/_10/_11/_12/_13/_14/_15/_16/_17/_18``**:
  ``json.dumps(...)`` verliert ``separators=(",", ":")`` bzw.
  ``ensure_ascii=False`` oder die Schluesselnamen ``"post"``/``"files"``
  aendern sich. Die kanonische Form ist die Eingabe des HMAC-Fingerprints
  (N14/#1942) — jede Formaenderung aendert den Hash-Input und muss darum
  EXAKT gepinnt sein (Text, der in den Hash einfliesst,
  wird gepinnt statt geskippt). Bestehende Tests
  (``src/tests/offline/test_offline_sync_idempotency.py::TestPayloadFingerprint``)
  vergleichen nur relative Gleichheit/Ungleichheit zwischen Fingerprints,
  nie die exakte Zeichenkette — deshalb ueberleben diese Mutanten dort.

* **``x__fingerprint_for_pepper__mutmut_18``**: ``.rstrip("=")`` wird zu
  ``.rstrip("XX=XX")`` (mutmut-Marker, tatsaechliche Zeichen ``X``/``=``).
  ``str.rstrip`` behandelt das Argument als Zeichen-MENGE — jedes
  Base64url-Zeichen ``X`` am Ende des Fingerprints wuerde mit-entfernt statt
  nur das Padding ``=``. Das kuerzt/verfaelscht den gespeicherten
  Payload-Fingerprint (Kollisionsschutz N14/#1942) fuer ca. 1/32 aller
  Peppers/Payloads.

* **``x__fingerprint_pepper_id__mutmut_6/_7/_9``**: ``fingerprint.split(":", 2)``
  verliert das ``maxsplit`` bzw. wird zu ``rsplit``. Bei einem (praktisch
  nie vom Server erzeugten, aber ueber die Funktion direkt pruefbaren)
  Fingerprint mit einem DRITTEN Doppelpunkt liefert das Original weiterhin
  die korrekte Pepper-Kennung, die Mutanten verwerfen sie faelschlich als
  ``None`` (Legacy-Fall) — das wuerde den Payload-Vergleich fuer diesen
  Fingerprint abschalten statt ihn durchzufuehren.

* **``x__fingerprint_pepper_id__mutmut_10``**: ``or`` wird zu ``and`` in
  ``len(parts) != 3 or parts[0] != FINGERPRINT_SCHEME``. Ein Fingerprint mit
  FALSCHEM Schema-Praefix aber zufaellig drei Segmenten wird dadurch NICHT
  mehr verworfen, sondern als gueltige Pepper-Kennung interpretiert —
  Schema-Validierung des gespeicherten Hashes faellt faktisch aus.

* **``x_remember_idempotent_result__mutmut_4/_7``**: die TTL
  (``IDEMPOTENCY_TTL_SECONDS`` = 72 h) faellt entweder auf ``None``
  (Cache-Eintrag verfaellt NIE) oder ganz weg (Cache-Backend-Default,
  typischerweise wenige Minuten statt 72 h) — beides verletzt die im
  Modul-Docstring dokumentierte Invariante ``TTL >= Bundle-Lease``.

``_encryption_key`` liegt modulweit an (Lehre 112), auch wenn nicht jeder
Test hier tatsaechlich verschluesselt — Standard-Vorgabe der Welle.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_resolve_idempotency_key_default_ist_kein_pflichtfeld():
    """``xǁresolve_idempotency_keyǁmutmut_1``: ohne ``required``-Angabe ist ein
    fehlender Schluessel gueltig (kein ``ValueError``) — der Default ist
    ``False``, nicht ``True``."""
    from core.services.events.idempotency import resolve_idempotency_key

    assert resolve_idempotency_key(None, None) is None


def test_canonical_payload_hat_das_exakte_wire_format_fuer_den_hash():
    """``xǁ_canonical_payloadǁmutmut_8/_10/_11/_12/_13/_14/_15/_16/_17/_18``:
    die kanonische Form ist EXAKT ``{"post":[[...]],"files":[[...]]}"`` —
    kompakte Separatoren (``","``/``":"``), ``ensure_ascii=False`` (Umlaut
    bleibt Klartext) und die Schluesselnamen ``post``/``files`` sind Teil des
    Hash-Inputs (N14/#1942) und deshalb exakt gepinnt, nicht nur relativ."""
    from core.services.events.idempotency import _canonical_payload

    request = RequestFactory().post(
        "/x",
        {
            "vorname": "Bärbel",
            "csrfmiddlewaretoken": "tok-123",
            "idempotency_key": "transport-key",
            "anhang": SimpleUploadedFile("f.txt", b"data"),
        },
    )
    result = _canonical_payload(request)
    assert result == '{"post":[["vorname","Bärbel"]],"files":[["anhang","f.txt",4]]}'


def test_fingerprint_for_pepper_rstrip_entfernt_nur_das_padding_gleich(monkeypatch):
    """``xǁ_fingerprint_for_pepperǁmutmut_18``: ``.rstrip("=")`` darf NUR das
    Padding-Zeichen ``=`` entfernen, keine Base64url-Nutzzeichen wie ``X`` —
    per Spy auf ``base64.urlsafe_b64encode`` deterministisch erzwungen, ohne
    auf einen zufaelligen Digest mit ``X``-Endung warten zu muessen."""
    from core.services.events.idempotency import _fingerprint_for_pepper

    monkeypatch.setattr(
        "core.services.events.idempotency.base64.urlsafe_b64encode",
        lambda _digest: b"testX==",
    )
    result = _fingerprint_for_pepper(b"irrelevanter-pepper", "irrelevantes-payload")
    assert result.endswith(":testX")


def test_fingerprint_pepper_id_nutzt_maxsplit_zwei_bei_zusatz_doppelpunkten():
    """``xǁ_fingerprint_pepper_idǁmutmut_6/_7/_9``: ``split(":", 2)`` (nicht
    unlimitiert, nicht ``rsplit``, nicht ``maxsplit=3``) liefert bei einem
    Fingerprint mit einem DRITTEN Doppelpunkt weiterhin die korrekte
    Pepper-Kennung im zweiten Segment, statt den Vergleich faelschlich als
    Legacy-Fall (``None``) abzuschalten."""
    from core.services.events.idempotency import _fingerprint_pepper_id

    assert _fingerprint_pepper_id("h1:11111111:22222222:h1") == "11111111"


def test_fingerprint_pepper_id_lehnt_falsches_schema_immer_ab():
    """``xǁ_fingerprint_pepper_idǁmutmut_10``: ``or`` (nicht ``and``) in der
    Verwerfungs-Bedingung — ein Fingerprint mit FALSCHEM Schema-Praefix, aber
    zufaellig drei Segmenten, muss trotzdem ``None`` liefern (Schema-Pruefung
    darf nicht durch die Laengenpruefung ausgehebelt werden)."""
    from core.services.events.idempotency import _fingerprint_pepper_id

    assert _fingerprint_pepper_id("wrongscheme:11111111:xyz") is None


def test_remember_idempotent_result_setzt_die_volle_72h_ttl(monkeypatch):
    """``xǁremember_idempotent_resultǁmutmut_4/_7``: ``cache.set(...)`` bekommt
    IMMER die dritte Positionsangabe ``IDEMPOTENCY_TTL_SECONDS`` (72 h) —
    weder ``None`` (verfaellt nie) noch weggelassen (Cache-Backend-Default,
    typischerweise Minuten statt 72 h). Per Spy auf ``cache.set`` statt
    Warten auf echten Ablauf."""
    from core.services.events.idempotency import (
        IDEMPOTENCY_TTL_SECONDS,
        remember_idempotent_result,
    )

    calls = {}

    def fake_set(key, value, timeout=None):
        calls["timeout"] = timeout

    monkeypatch.setattr("core.services.events.idempotency.cache.set", fake_set)
    remember_idempotent_result("scope-x", 7, "key-1", "pk-1")
    assert calls["timeout"] == IDEMPOTENCY_TTL_SECONDS
    assert calls["timeout"] == 72 * 3600
