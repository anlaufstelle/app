"""Pins fuer den Schluesselring-Rueckfall (``core.services.file_vault.encryption``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 4 Mutanten, alle in
``_get_keys``: die ``getattr``-Defaults fuer ``ENCRYPTION_KEYS`` und
``ENCRYPTION_KEY`` entfallen (``AttributeError``) oder werden zu einem
Platzhalter-Schluessel. Beides ist nur bei einer ganz entfernten Einstellung
beobachtbar — die Basis-Settings setzen beide sonst immer. Die Pins entfernen
die Einstellungen deshalb ueber die Settings-Fixture: fehlt nur der
Schluesselring, gilt der Einzelschluessel; fehlen beide, ist die Liste leer
und ``get_fernet`` meldet fail-closed ``EncryptionKeyMissing``.
"""

import pytest
from cryptography.fernet import Fernet

from core.services.file_vault.encryption import EncryptionKeyMissing, _get_keys, get_fernet


@pytest.fixture
def frischer_fernet_cache():
    """``get_fernet`` haelt eine Instanz je Prozess — vor und nach dem Test verwerfen."""
    get_fernet.cache_clear()
    yield
    get_fernet.cache_clear()


class TestGetKeys:
    """``_get_keys``: Rueckfall auf den Einzelschluessel und leerer Ring."""

    def test_ohne_schluesselring_gilt_der_einzelschluessel(self, settings, frischer_fernet_cache):
        """x__get_keys__mutmut_8 (``AttributeError`` ohne Default), _11
        (Platzhalter-Schluessel statt Rueckfall)."""
        schluessel = Fernet.generate_key().decode("utf-8")
        del settings.ENCRYPTION_KEYS
        settings.ENCRYPTION_KEY = schluessel
        assert _get_keys() == [schluessel]
        assert get_fernet().decrypt(Fernet(schluessel).encrypt(b"probe")) == b"probe"

    def test_ohne_beide_einstellungen_ist_der_ring_leer(self, settings, frischer_fernet_cache):
        """x__get_keys__mutmut_20 (``AttributeError`` ohne Default), _23
        (Platzhalter-Schluessel statt leerer Liste)."""
        del settings.ENCRYPTION_KEYS
        del settings.ENCRYPTION_KEY
        assert _get_keys() == []
        with pytest.raises(EncryptionKeyMissing):
            get_fernet()
