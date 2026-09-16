"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Batch ``events-1n``
(Vollmutation-Survivor-Triage), Modul ``core.services.events.idempotency``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-events-1n.json`` (Triage
``triage-099-events-1n.json``).

Getoetete Mutante (voller ``x...__mutmut_N``-Name, Modul
``core.services.events.idempotency``):

* **``x__fingerprint_pepper__mutmut_3``**: das ``EncryptionKeyMissing``
  bekommt statt des Diagnosetexts ``None`` als einziges Argument
  (Meldungsverlust). Ohne konfigurierten ``ENCRYPTION_KEY``/
  ``ENCRYPTION_KEYS`` bricht ``_fingerprint_pepper()`` bewusst ab, statt auf
  einen ungepfefferten Digest zurueckzufallen (Refs #1942) — die Meldung
  benennt genau diesen Grund. Pin: exakte Assertion auf
  ``exc_info.value.args[0]``.

``_encryption_key`` liegt modulweit an (Lehre 112, wie in den bestehenden
099-Pin-Dateien) — fuer diesen einen Pin wird der so gesetzte Schluessel im
Testkoerper GEZIELT wieder geleert (``settings.ENCRYPTION_KEY`` /
``settings.ENCRYPTION_KEYS`` auf leer), damit ``_get_keys()``
(``core/services/file_vault/encryption.py``) eine leere Liste liefert und der
Fehlerpfad ueberhaupt erreichbar wird.
"""

from __future__ import annotations

import pytest

from core.services.events.idempotency import _fingerprint_pepper
from core.services.file_vault.encryption import EncryptionKeyMissing

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_fingerprint_pepper_ohne_schluessel_wirft_die_volle_diagnosemeldung(settings):
    """``xǁ_fingerprint_pepperǁmutmut_3``: fehlt ``ENCRYPTION_KEY``/
    ``ENCRYPTION_KEYS``, muss ``EncryptionKeyMissing`` mit dem vollen
    Diagnosetext ausgeloest werden — nicht mit ``None`` als einzigem
    Argument. Der Schluessel wird hier gezielt geleert (die modulweite
    ``_encryption_key``-Fixture setzt ihn zuvor), damit ``_get_keys()``
    tatsaechlich ``[]`` liefert."""
    settings.ENCRYPTION_KEY = ""
    settings.ENCRYPTION_KEYS = ""

    with pytest.raises(EncryptionKeyMissing) as exc_info:
        _fingerprint_pepper()

    assert exc_info.value.args[0] == (
        "ENCRYPTION_KEY / ENCRYPTION_KEYS ist nicht gesetzt — der Idempotenz-Fingerprint "
        "braucht ihn als Pepper (Refs #1942)."
    )
