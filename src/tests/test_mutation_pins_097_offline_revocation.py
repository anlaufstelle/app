"""Mutation-Pins der Welle J (0.97, Refs #2149) — Obergrenze der
Offline-Revoke-Liste (``core.services.system.offline_revocation``).

Quelle: Prio-1-Survivor des atomaren Messlaufs
(``welle-j-0.97/reports/triage-097-sec.json``, Batch ``sec``).

``list_offline_client_revocations`` liest eine Zeile mehr als der Deckel
erlaubt, loggt beim Ueberlauf laut und liefert die auf
``OFFLINE_REVOCATION_MAX_ENTRIES`` gekuerzte Liste. Der Ueberlauf-Zweig war
vollstaendig ungetestet — die Bestandstests bleiben unter dem Deckel.

Gepinnter Mutant (Mutantenbaum ``/work/anlaufstelle/mutants``, Stand
f5272d3e — ``offline_revocation.py`` ist seitdem unveraendert):

* ``x_list_offline_client_revocations__mutmut_32`` —
  ``entries = entries[:OFFLINE_REVOCATION_MAX_ENTRIES]`` -> ``entries = None``.
  Der Endpoint serialisiert dann ``"revocations": null``, JEDES Geraet der
  Einrichtung bekommt ueberhaupt keine Widerrufe mehr — und zwar genau im
  Massen-Anonymisierungsfall, fuer den der Deckel gebaut wurde. Verletzt
  zugleich den deklarierten Rueckgabetyp ``list[str]``.

Als Beifang toetet derselbe Test die beiden Nachbarn des Ueberlauf-Zweigs
(``__mutmut_17``: Slice ohne ``+ 1``, liefert einen Eintrag zu wenig und
loggt nie; ``__mutmut_19``: ``>=`` statt ``>``, Fehlalarm-Log genau am
Deckel) — beide sind in der Triage nicht Prio 1, der Pin nimmt sie mit.

Der Deckel wird per ``monkeypatch`` klein gemacht, statt 5001 Zeilen zu
erzeugen: die Funktion liest das Modul-Global zur Laufzeit, der Mutantenbaum
ebenso.
"""

from __future__ import annotations

import logging

import pytest

from core.models import Client
from core.services.system.offline_revocation import (
    list_offline_client_revocations,
    revoke_offline_client_bundles,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

_MODUL = "core.services.system.offline_revocation"


def _revoke_n(facility, staff_user, count):
    for index in range(count):
        client = Client.objects.create(
            facility=facility,
            pseudonym=f"Pin-097-Revoke-{index}",
            created_by=staff_user,
        )
        revoke_offline_client_bundles(client)


class TestRevocationCap:
    def test_obergrenze_liefert_die_gekuerzte_liste_und_loggt(self, facility, staff_user, monkeypatch, caplog):
        """Pins ``x_list_offline_client_revocations__mutmut_32``: beim
        Ueberschreiten der Obergrenze muss die Funktion die GEKUERZTE Liste
        liefern (``list[str]``), nicht ``None``. Nimmt ``__mutmut_17``
        (Slice ohne ``+ 1`` — zwei statt drei Eintraege, kein Log) mit."""
        monkeypatch.setattr(f"{_MODUL}.OFFLINE_REVOCATION_MAX_ENTRIES", 3)
        _revoke_n(facility, staff_user, 4)

        with caplog.at_level(logging.ERROR, logger=_MODUL):
            entries = list_offline_client_revocations(facility)

        assert isinstance(entries, list)
        assert len(entries) == 3
        assert all(isinstance(entry, str) for entry in entries)
        assert "Obergrenze" in caplog.text

    def test_genau_am_deckel_bleibt_die_liste_vollstaendig_und_still(self, facility, staff_user, monkeypatch, caplog):
        """Gegenprobe zum Ueberlauf-Zweig: exakt am Deckel wird nichts
        abgeschnitten und nichts geloggt. Nimmt ``__mutmut_19`` (``>=`` statt
        ``>`` — Fehlalarm-Log genau am Deckel) mit."""
        monkeypatch.setattr(f"{_MODUL}.OFFLINE_REVOCATION_MAX_ENTRIES", 3)
        _revoke_n(facility, staff_user, 3)

        with caplog.at_level(logging.ERROR, logger=_MODUL):
            entries = list_offline_client_revocations(facility)

        assert isinstance(entries, list)
        assert len(entries) == 3
        assert caplog.records == []
