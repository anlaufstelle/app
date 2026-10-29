"""Pins for strict UTF-8 list encryption.

Source: mutation delta of the release-0.99.9 checkpoint, 2 mutants.
Groups: rejection of lone Unicode surrogates (2).
"""

import pytest

from core.services.file_vault import EncryptionError, encrypt_field


@pytest.mark.usefixtures("_encryption_key")
def test_list_with_lone_surrogate_is_rejected():
    """List encryption rejects non-UTF-8 text: x_encrypt_field__mutmut_17, x_encrypt_field__mutmut_19."""
    with pytest.raises(EncryptionError) as error:
        encrypt_field(["\ud800"])
    assert isinstance(error.value.__cause__, UnicodeEncodeError)
