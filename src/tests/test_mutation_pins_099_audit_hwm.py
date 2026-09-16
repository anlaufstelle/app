"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.audit.hwm``.

Quelle: Vollmutation #2174, Batch ``audit-2``
(``welle-l-0.99/reports/m-scratch/batch-audit-2.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-audit-2/triage-099-audit-2.json``).

Der externe High-Water-Mark (HWM, R16, Refs #1476/#1665) schuetzt die
Audit-HMAC-Kette gegen Whole-Chain-TRUNCATE und Tail-Truncation — beides
Angriffe, die ``verify_chain`` allein NICHT erkennt (Moduldocstring von
``hwm.py``). Zwei Bausteine tragen diesen Schutz: der stabile Store-Schluessel
je Kette (``_chain_key``) und die HMAC-Signatur der gesamten Store-Datei
(``_sign``). Beide werden hier DIREKT (nicht nur ueber einen Datei-Roundtrip)
gepinnt: ein in sich konsistenter Roundtrip (schreiben UND lesen mit
DERSELBEN Mutante) deckt eine falsche Kanonisierung von ``_sign`` haeufig
NICHT auf, weil Schreiben und Lesen dieselbe (mutierte) Funktion benutzen und
sich intern noch einig waeren. Erst der Vergleich gegen einen UNABHAENGIG
(mit der dokumentierten Kanonisierung: sortierte Schluessel, kompakte
Separatoren, UTF-8, SHA-256) berechneten Referenzwert macht die Abweichung
sichtbar.

Killt: ``x__chain_key__mutmut_1``/``__mutmut_2`` (Test 1) sowie
``x__sign__mutmut_4``/``__mutmut_5``/``__mutmut_7``/``__mutmut_8``/
``__mutmut_9``/``__mutmut_10``/``__mutmut_11`` (Test 2).

NICHT gepinnt (Triage-JSON, Klasse ``equivalent``): ``x__sign__mutmut_13``
(``encode("UTF-8")`` statt ``"utf-8"``) und ``x__sign__mutmut_21``
(``digestmod="SHA256"`` statt ``"sha256"``) — Pythons Codec- bzw.
Hash-Algorithmus-Namensauflösung ist gross-/kleinschreibungsunabhaengig
(``"x".encode("UTF-8") == "x".encode("utf-8")``;
``hmac.new(k, m, "SHA256").hexdigest() == hmac.new(k, m, "sha256").hexdigest()``
— verifiziert), beide Mutanten liefern fuer JEDEN Eingabewert byteidentische
Ausgaben.
"""

from __future__ import annotations

import hmac
import json

import pytest

from core.services.audit.hash import _get_audit_hash_key
from core.services.audit.hwm import _chain_key, _sign

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_chain_key_fuer_systemweite_kette_ist_literal_system():
    """Pins ``x__chain_key__mutmut_1`` (Literal wird zum mutmut-Marker
    ``"XXsystemXX"``) UND ``x__chain_key__mutmut_2`` (Literal wird zu
    ``"SYSTEM"``): der Store-Schluessel der systemweiten Kette
    (``facility_id=None``) MUSS exakt ``"system"`` lauten. Persistierte
    HWM-Dateien binden den Anker unter diesem Literal; ein abweichender
    Schluessel macht ``HighWaterMarkStore.get(None)`` nach einem Reload
    blind fuer einen zuvor verankerten Tail — eine Tail-Truncation der
    systemweiten Kette (z. B. system-/cron-ausgeloeste Audit-Zeilen ohne
    Facility) bliebe unerkannt."""
    assert _chain_key(None) == "system"


def test_sign_kanonisierung_ist_sortiert_kompakt_utf8_sha256():
    """Pins sieben Kanonisierungs-Mutanten von ``x__sign`` in EINER
    Referenzwert-Assertion: ``__mutmut_4`` (``sort_keys=None``),
    ``__mutmut_7`` (``sort_keys`` faellt ganz weg -> Default ``False``),
    ``__mutmut_9`` (``sort_keys=False`` explizit), ``__mutmut_5``
    (``separators=None`` -> Default-Leerzeichen statt ``(",", ":")``),
    ``__mutmut_8`` (``separators`` faellt ganz weg), ``__mutmut_10``
    (Item-Separator ``","`` -> ``"XX,XX"``) und ``__mutmut_11``
    (Key-Separator ``":"`` -> ``"XX:XX"``).

    Der Vergleichswert wird UNABHAENGIG von ``_sign`` aus der dokumentierten
    Kanonisierung berechnet. Der Beispiel-``chains``-Dict traegt bewusst
    ZWEI Ketten in NICHT-alphabetischer Einfuegereihenfolge (``zzz`` vor
    ``aaa``), damit ``sort_keys``-Mutanten ueberhaupt eine andere
    Byte-Reihenfolge erzeugen — bei nur einer Kette waere die Reihenfolge
    ohnehin trivial gleich."""
    chains = {
        "zzz-facility": {"count": 2, "last_entry_hash": "b" * 64},
        "aaa-facility": {"count": 1, "last_entry_hash": "a" * 64},
    }
    canonical = json.dumps(chains, sort_keys=True, separators=(",", ":")).encode("utf-8")
    erwartet = hmac.new(_get_audit_hash_key(), canonical, "sha256").hexdigest()

    assert _sign(chains) == erwartet
