"""Pins for the key cache invalidation (``core.services.file_vault.encryption``).

Source: mutation delta of checkpoint release-0.99.5, 2 mutants in
``_reset_fernet_cache``: the setting name ``ENCRYPTION_KEY`` in the watched
pair is misspelt (``XXENCRYPTION_KEYXX``) or lower-cased. Only overrides of
``ENCRYPTION_KEYS`` then clear the process-wide ``get_fernet`` instance; an
override of the single key keeps encrypting with the key that was cached
before. The effect is visible only while the key ring is empty (otherwise the
ring wins over the single key), so the pins clear the ring first, warm the
cache with one key and then switch the single key.
"""

import pytest
from cryptography.fernet import Fernet, InvalidToken
from django.test import override_settings

from core.services.file_vault.encryption import encrypt_field, get_fernet


@pytest.fixture
def fresh_fernet_cache():
    """``get_fernet`` keeps one instance per process — discard it before and after the test."""
    get_fernet.cache_clear()
    yield
    get_fernet.cache_clear()


def _readable_with(key: str, token: str) -> bool:
    try:
        Fernet(key.encode()).decrypt(token.encode())
    except InvalidToken:
        return False
    return True


class TestResetFernetCache:
    """``_reset_fernet_cache``: overriding the single key replaces the cached instance."""

    def test_single_key_override_encrypts_with_new_key(self, fresh_fernet_cache):
        """x__reset_fernet_cache__mutmut_5 and x__reset_fernet_cache__mutmut_6: the
        ``ENCRYPTION_KEY`` override no longer clears the cache, so the value is still
        encrypted with the previously cached key.
        """
        first_key = Fernet.generate_key().decode()
        second_key = Fernet.generate_key().decode()

        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=first_key):
            warm = encrypt_field("warm-up")
            assert _readable_with(first_key, warm["value"])

            with override_settings(ENCRYPTION_KEY=second_key):
                token = encrypt_field("after override")["value"]

        assert _readable_with(second_key, token)
        assert not _readable_with(first_key, token)
