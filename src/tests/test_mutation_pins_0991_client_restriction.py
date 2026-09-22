"""Pin-Tests des Mutations-Delta-Checkpoints der Welle M (release-0.99.1) zu
``core.services.client.restriction``.

Quelle: Ticket #2322 (https://github.com/anlaufstelle/app/issues/2322),
Survivor-Triage des Checkpoint-Deltas ``6435c18e`` (BASE v0.99.0). Gepinnt
werden die vier Prio-1-Survivor dieses Moduls: ``assert_processing_allowed_for``
— das Mehrfach-Personen-Gate (Refs #2268) — hat einen ``lock``-Default, der
den standardmaessigen (ungesperrten) Aufrufpfad bestimmt, und eine
``restricted``-Zusicherung im ``lock=False``-Zweig, deren Wegfall den
Verarbeitungsschutz OHNE Fehlermeldung umgeht.

**S-B-Ueberschneidung (Nachlauf noetig):** Lane S-B liest Bausteine aus
``client/locking.py`` (``lock_clients``), das dieses Modul importiert. Der
Kill-Nachweis dieser Datei gilt fuer den Stand VOR dem S-B-Merge — der
Orchestrator wiederholt ihn danach.

Konvention wie ``src/tests/test_mutation_pins_094_attachments_crud.py``:
echte Model-Objekte, Zusicherungen exakt.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import Client
from core.services.client import restriction as restriction_module
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE, assert_processing_allowed_for

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _client(facility, *, restricted, pseudonym):
    return Client.objects.create(facility=facility, pseudonym=pseudonym, processing_restricted=restricted)


class TestAssertProcessingAllowedForLockDefault:
    def test_ohne_lock_argument_liest_ungesperrt(self, monkeypatch, facility):
        """Pin: ``x_assert_processing_allowed_for__mutmut_1`` — der Default
        von ``lock`` wechselt von ``False`` auf ``True``.

        Aufruf gemaess oeffentlicher Standardsignatur (kein ``lock``-Argument)
        MUSS ueber ``_restricted_flag`` (ungesperrtes Lesen je Person) laufen,
        NICHT ueber ``lock_clients`` (Zeilensperre, braucht eine offene
        Transaktion — der Vertrag der oeffentlichen Signatur ist gerade, dass
        das NICHT vorausgesetzt wird).
        """
        client = _client(facility, restricted=False, pseudonym="Pins-0991-Restriction-Lock")

        restricted_flag_calls = []
        lock_clients_calls = []

        def _restricted_flag_spy(client_arg, **kwargs):
            restricted_flag_calls.append(client_arg)
            return False

        def _lock_clients_spy(clients_arg, **kwargs):
            lock_clients_calls.append(list(clients_arg))
            return {}

        monkeypatch.setattr(restriction_module, "_restricted_flag", _restricted_flag_spy)
        monkeypatch.setattr(restriction_module, "lock_clients", _lock_clients_spy)

        assert_processing_allowed_for([client.pk])

        assert restricted_flag_calls == [client.pk]
        assert lock_clients_calls == []


class TestAssertProcessingAllowedForLockFalseBranch:
    def test_lock_false_zweig_erkennt_eingeschraenkte_und_freie_personen(self, facility):
        """Pin: ``x_assert_processing_allowed_for__mutmut_6`` (``restricted``
        wird im ``lock=False``-Zweig auf ``None`` gesetzt), ``…__mutmut_7``
        (``any(generator)`` wird zu ``any(None)``) und ``…__mutmut_8`` (jedes
        ``client``-Argument im Generator wird durch ``None`` ersetzt).

        Original: eine EINGESCHRAENKTE Person loest ``ValidationError`` mit
        ``PROCESSING_RESTRICTED_MESSAGE`` aus, eine FREIE Person laesst die
        Funktion regulaer zurueckkehren. Mutant 6/8: die eingeschraenkte
        Person wird NIE erkannt (kein Fehler, stiller Bypass). Mutant 7: JEDE
        Eingabe (frei oder eingeschraenkt) wirft ``TypeError`` statt der
        fachlichen Unterscheidung.
        """
        frei = _client(facility, restricted=False, pseudonym="Pins-0991-Frei")
        eingeschraenkt = _client(facility, restricted=True, pseudonym="Pins-0991-Eingeschraenkt")

        # Freie Person: keine Ausnahme.
        assert_processing_allowed_for([frei.pk])

        # Eingeschraenkte Person: exakt die ValidationError mit der
        # Verarbeitungsschutz-Meldung — kein TypeError, kein stilles Passieren.
        with pytest.raises(ValidationError) as exc_info:
            assert_processing_allowed_for([eingeschraenkt.pk])
        assert exc_info.value.messages == [str(PROCESSING_RESTRICTED_MESSAGE)]
