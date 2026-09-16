"""Pin-Tests der Welle L (release-0.99, Refs #2174 / #2193) — Batch **security-2**.

Quelle: Vollmutation #2174, Batch ``security-2``
(``welle-l-0.99/reports/m-scratch/batch-security-2.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-security-2/triage-099-security-2.json``).
13 Survivor-Mutanten in drei Modulen: ``core.services.security.mfa``
(``_hash_code`` 1, ``_get_or_create_device`` 2, ``verify_backup_code`` 2),
``core.services.security.totp`` (``encrypt_totp_key`` 2, ``decrypt_totp_key``
3) und ``core.services.security.credentials`` (``revoke_credentials`` 1,
``flush_user_sessions`` 2).

**Nicht gepinnt (``equivalent``, 12 Mutanten — jeweils Diff-Beleg UND
Nachweis ohne beobachtbaren Unterschied, siehe Bericht
``m-pins-security-2-report.md`` fuer die vollstaendige Liste):**

* Fuenf Codec-Namens-Case-Varianten (``"utf-8"``/``"ascii"`` -> ``"UTF-8"``/
  ``"ASCII"``) in ``_hash_code``, ``encrypt_totp_key`` (2x) und
  ``decrypt_totp_key`` (2x): Pythons Codec-Registry ist case-insensitiv
  (``codecs.lookup("ascii") is codecs.lookup("ASCII")``), ``encode``/
  ``decode`` liefern byteidentische Ergebnisse.
* Zwei Varianten von ``_get_or_create_device`` (``defaults={"confirmed":
  True}`` -> ``defaults=None`` bzw. das Kwarg komplett entfernt):
  ``django_otp``s ``Device.confirmed`` ist ``BooleanField(default=True)`` —
  beim Neuanlegen greift ohnehin der Modell-Default, beim vorhandenen Device
  liest ``get_or_create`` ``defaults`` gar nicht.
* Zwei Varianten in ``verify_backup_code`` (``throttle_reset``/
  ``set_last_used_timestamp`` mit ``commit=False`` -> ``commit=None``):
  beide Methoden pruefen nur ``if commit: self.save()`` — ``None`` und
  ``False`` sind beide falsy, und der Aufrufer ruft zwei Zeilen weiter
  ohnehin unbedingt ``device.save()``.
* ``revoke_credentials``: ``getattr(user, "must_change_password", False)``
  -> ``..., None)``. Das Feld existiert auf jedem echten ``User`` immer
  (BooleanField), der Default wird nie gezogen; selbst falls doch, wird der
  Wert nur als Wahrheitswert verwendet.
* ``flush_user_sessions``: ``capped = False`` -> ``capped = None``
  (nur als Wahrheitswert in ``if capped:`` gelesen) sowie
  ``.only("session_key", "session_data")`` -> ``.only("session_data")``
  (``session_key`` ist die Primary-Key-Spalte von
  ``django.contrib.sessions.models.Session`` — Djangos SQL-Compiler nimmt
  den Primary Key IMMER in die SELECT-Liste auf, unabhaengig von
  ``only()``; ``qs.query.get_compiler("default").get_select()`` liefert fuer
  beide Varianten dieselbe Spaltenliste).

Alle Exakt-Vergleiche (nie Teilstring) — sonst uebersieht der Test den
mutmut-Marker bzw. Case-Varianten.
"""

from __future__ import annotations

import pytest

from core.services.file_vault import EncryptionError
from core.services.security.totp import decrypt_totp_key

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_decrypt_totp_key_invalid_token_keeps_full_error_message():
    """Pinnt ``x_decrypt_totp_key__mutmut_10``.

    Die Mutante ersetzt ``raise EncryptionError(f"TOTP-Key-Entschluesselung
    fehlgeschlagen: {exc}") from exc`` durch ``raise EncryptionError(None)
    from exc`` — ein Meldungs-Verlust-Mutant am Exception-Argument. Der
    Eingabewert ist kein Hex (``_is_plain_hex`` liefert False) und kein
    gueltiges Fernet-Token, sodass ``get_fernet().decrypt(...)`` ein leeres
    ``InvalidToken()`` ausloest (``str(exc) == ""``, verifiziert gegen die
    installierte ``cryptography``-Version) — die erwartete Meldung ist damit
    deterministisch exakt bekannt.
    """
    with pytest.raises(EncryptionError) as exc_info:
        decrypt_totp_key("not-a-real-token-1234")

    assert exc_info.value.args == ("TOTP-Key-Entschluesselung fehlgeschlagen: ",)
