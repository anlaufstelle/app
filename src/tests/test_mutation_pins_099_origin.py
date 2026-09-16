"""Pin-Tests zum Meilenstein release-0.99 (#2174, #2193) — Survivors in
``core.services.security.origin``.

Quelle: Originalmodul ``src/core/services/security/origin.py``,
Mutantenbaum ``mutants/src/core/services/security/origin.py``. Herkunft der
Auswahl: die Batch-Triage ``security-1`` des Vollmutationslaufs #2174 (10
Mutanten). Schwerpunkt: ``get_external_origin()`` ist der Fail-loud-Schutz
gegen Host-Header-Injection in Einladungs-/Reset-/Entsperr-Links (Refs
#1967) — er darf bei fehlender Konfiguration NIEMALS still einen
akzeptierten (aber falschen) Origin-Wert zurueckgeben.

``EXTERNAL_ORIGIN`` ist in JEDER Settings-Umgebung dieses Repos real gesetzt
(``settings/base.py``); der ``getattr``-Default wird nur ueber
``del settings.EXTERNAL_ORIGIN`` beobachtbar (die ``settings``-Fixture stellt
das am Testende wieder her).

Konventionen wie ``test_mutation_pins_094_lockout_recovery.py``: Zusicherungen
vergleichen EXAKT; Fixtures aus ``src/tests/conftest.py``.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from core.services.security.origin import get_external_origin

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestGetExternalOriginFailsLoudWhenUnset:
    def test_missing_setting_raises_improperly_configured_not_a_silent_or_bare_fallback(self, settings):
        """Pins: x_get_external_origin__mutmut_8
        (Default-Argument von ``getattr(settings, "EXTERNAL_ORIGIN", "")`` entfernt),
        x_get_external_origin__mutmut_11 (Default ``""`` -> ``"XXXX"``).

        Original faellt (Attribut geloescht) auf ``""`` zurueck -> ``or ""``
        bleibt leer -> kontrollierter ``ImproperlyConfigured``-Fehler.
        mutmut_8 ruft ``getattr`` zweiargumentig auf und stirbt an einem
        nackten ``AttributeError`` (kein Hinweis mehr auf die fehlende
        Konfiguration). mutmut_11 faellt auf den WAHRHEITSWERTIGEN
        Platzhalter ``"XXXX"`` zurueck -> der ``or ""``-Fallback greift
        NICHT mehr, die Funktion liefert stillschweigend ``"XXXX"`` als
        vermeintliche Basis-Origin statt den fehlenden Betriebswert laut zu
        melden — genau der Fail-loud-Schutz, den dieses Modul garantieren
        soll, waere ausgehebelt.
        """
        del settings.EXTERNAL_ORIGIN

        with pytest.raises(ImproperlyConfigured):
            get_external_origin()
