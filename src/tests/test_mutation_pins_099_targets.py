"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.targets``.

Quelle: Vollmutation #2174, Batch ``targets-1``
(``welle-l-0.99/reports/m-scratch/batch-targets-1.json``,
Triage ``welle-l-0.99/reports/m-scratch/pins-targets-1/triage-099-targets-1.json``). Die Batch
hat 6 Survivor-Mutanten in ``target_q`` — alle drei Fehlerzweige der Funktion tragen
wortgleich den Meldungstext ``"target_ids muss ausschließlich UUID-Werte enthalten."``, den
Bestandstest ``test_targets.py::test_target_q_rejects_non_uuid_target_ids`` prüft aber nur per
``pytest.raises(match=...)``. ``match`` ist eine Teilstring-/Regex-Suche und übersieht daher den
mutmut-String-Marker ``XX…XX`` (Standardblock 72) — die drei so mutierten Meldungen (mutmut_4,
mutmut_10, mutmut_16) überleben den Bestandstest unbemerkt. Der dritte Fehlerzweig (``except
TypeError`` beim ``tuple(target_ids)``-Aufruf mit nicht-iterierbaren ``target_ids``, z. B. einem
``int``) wird von KEINEM Bestandstest ausgelöst — dort überleben zusätzlich eine Ersetzung durch
``None`` (mutmut_9, ``str(exc.value) == "None"``) sowie eine Klein- (mutmut_11) und eine
Großschreibungs-Variante (mutmut_12) der Meldung. ``target_q`` koppelt Zieltyp und UUID-Ziel-IDs
für die DSGVO-Lösch-/Retention-Filter (Refs #2156, siehe ``client/main.py``,
``client/merge.py``) untrennbar — eine verstümmelte oder inhaltslose Fehlermeldung bei falscher
Eingabe verschleiert dort eine Fehlbedienung.

Drei Tests, je einer pro Fehlerzweig, mit exaktem String-Vergleich (``str(exc_info.value) ==
...``) statt Teilstring-Match — nur so werden alle sechs Mutanten sichtbar.
"""

from __future__ import annotations

import uuid

import pytest

from core.services.targets import TARGET_EVENT, target_q

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

_EXPECTED_MESSAGE = "target_ids muss ausschließlich UUID-Werte enthalten."


def test_target_q_rejects_str_target_ids_with_exact_message():
    """Pins ``xǁtarget_qǁ__mutmut_4``: der ``isinstance(target_ids, (str, bytes))``-Zweig
    liefert bei einem einzelnen String als ``target_ids`` exakt die deutsche Meldung ohne
    mutmut-Marker — ``pytest.raises(match=...)`` im Bestandstest würde die XX-umschlossene
    Variante als Teilstring-Treffer durchwinken.
    """
    with pytest.raises(ValueError) as exc_info:
        target_q(TARGET_EVENT, "not-an-iterable-of-uuids")

    assert str(exc_info.value) == _EXPECTED_MESSAGE


def test_target_q_rejects_non_iterable_target_ids_with_exact_message():
    """Pins ``xǁtarget_qǁ__mutmut_9``, ``_10``, ``_11``, ``_12``: ein nicht-iterierbares
    ``target_ids`` (hier ein ``int``) lässt ``tuple(target_ids)`` mit ``TypeError`` scheitern;
    der ``except``-Zweig muss exakt die deutsche Meldung weiterreichen — nicht ``"None"``
    (mutmut_9, ``ValueError(None)``), nicht die XX-markierte Variante (mutmut_10) und nicht
    klein- (mutmut_11) oder großgeschrieben (mutmut_12). Dieser Zweig wird von keinem
    Bestandstest ausgelöst, da alle dortigen Parametrisierungen bereits Iterables sind.
    """
    with pytest.raises(ValueError) as exc_info:
        target_q(TARGET_EVENT, 42)

    assert str(exc_info.value) == _EXPECTED_MESSAGE


def test_target_q_rejects_non_uuid_elements_with_exact_message():
    """Pins ``xǁtarget_qǁ__mutmut_16``: enthält ``target_ids`` ein Element, das keine
    ``uuid.UUID`` ist, liefert der letzte Prüfzweig exakt die deutsche Meldung ohne
    mutmut-Marker — wie bei mutmut_4 übersieht der Teilstring-Match des Bestandstests die
    XX-umschlossene Variante.
    """
    with pytest.raises(ValueError) as exc_info:
        target_q(TARGET_EVENT, [uuid.uuid4(), "not-a-uuid"])

    assert str(exc_info.value) == _EXPECTED_MESSAGE
