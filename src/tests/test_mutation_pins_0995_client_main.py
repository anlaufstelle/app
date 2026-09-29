"""Pins fuer den Personen-Lookup der Vorauswahl (``core.services.client.main``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 1 Mutant in
``get_client_or_none``: der Lookup verliert die angefragte ``client_id``.
Bei genau einer Person der Einrichtung waere sie dann fuer jede — auch eine
unbekannte — ID vorgewaehlt, bei mehreren liefe ``get`` in
``MultipleObjectsReturned``. Die Pins legen deshalb je Fall die passende
Personenzahl an.
"""

import uuid

import pytest

from core.models import Client
from core.services.client.main import get_client_or_none

pytestmark = pytest.mark.django_db


class TestGetClientOrNone:
    """``get_client_or_none``: Lookup nach ``client_id`` in der Einrichtung."""

    def test_liefert_die_angefragte_von_zwei_personen(self, facility, staff_user):
        """x_get_client_or_none__mutmut_4: ohne ``pk`` wirft ``get`` bei zwei
        Personen ``MultipleObjectsReturned`` (unbehandelt, Serverfehler)."""
        erste = Client.objects.create(facility=facility, pseudonym="Alpha-Erste", created_by=staff_user)
        zweite = Client.objects.create(facility=facility, pseudonym="Zulu-Zweite", created_by=staff_user)
        assert get_client_or_none(facility, zweite.pk) == zweite
        assert get_client_or_none(facility, erste.pk) == erste
        assert get_client_or_none(facility, uuid.uuid4()) is None

    def test_unbekannte_id_waehlt_die_einzige_person_nicht_vor(self, facility, client_identified):
        """x_get_client_or_none__mutmut_4: ohne ``pk`` kaeme die einzige
        Person der Einrichtung fuer jede unbekannte ID zurueck."""
        assert get_client_or_none(facility, uuid.uuid4()) is None
        assert get_client_or_none(facility, client_identified.pk) == client_identified
