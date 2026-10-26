"""Mutation regression tests for ``core.services.events.idempotency`` (Refs #2174, #2193).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-events-1n.json`` (Triage
``triage-099-events-1n.json``).

Getoetete Mutante (voller ``x...__mutmut_N``-Name, Modul
``core.services.events.idempotency``):

* **``x__fingerprint_pepper__mutmut_3``**: das ``EncryptionKeyMissing``
  bekommt statt des Diagnosetexts ``None`` als einziges Argument
  (Meldungsverlust). Ohne konfigurierten Schluesselring ``ENCRYPTION_KEYS``
  bricht ``_fingerprint_pepper()`` bewusst ab, statt auf einen ungepfefferten
  Digest zurueckzufallen (Refs #1942) — die Meldung
  benennt genau diesen Grund. Pin: exakte Assertion auf
  ``exc_info.value.args[0]``.

``_encryption_key`` liegt modulweit an (Lehre 112, wie in den bestehenden
099-Pin-Dateien) — fuer diesen einen Pin wird der so gesetzte Schluessel im
Testkoerper GEZIELT wieder geleert (``settings.ENCRYPTION_KEYS`` auf leer), damit ``_get_keys()``
(``core/services/file_vault/encryption.py``) eine leere Liste liefert und der
Fehlerpfad ueberhaupt erreichbar wird.
"""

from __future__ import annotations

import pytest

from core.services.events.idempotency import _fingerprint_pepper
from core.services.file_vault.encryption import EncryptionKeyMissing

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_fingerprint_pepper_ohne_schluessel_wirft_die_volle_diagnosemeldung(settings):
    """``xǁ_fingerprint_pepperǁmutmut_3``: when ``ENCRYPTION_KEYS`` is missing,
    ``EncryptionKeyMissing`` must be raised with the full diagnostic text, not
    with ``None`` as the only argument. The key is cleared on purpose here (the
    module-wide ``_encryption_key`` fixture sets it beforehand) so that
    ``_get_keys()`` really returns ``[]``."""
    settings.ENCRYPTION_KEYS = ""

    with pytest.raises(EncryptionKeyMissing) as exc_info:
        _fingerprint_pepper()

    assert exc_info.value.args[0] == (
        "ENCRYPTION_KEYS is not set — the idempotency fingerprint needs it as pepper (Refs #1942)."
    )
