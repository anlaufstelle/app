"""TOTP-Geraet mit at-rest Fernet-verschluesseltem Secret (Refs #1362).

:class:`EncryptedTOTPDevice` ist ein **Proxy** ueber django-otps
``TOTPDevice`` — es teilt sich Tabelle (``otp_totp_totpdevice``) und die
komplette, erprobte Verify-/Throttle-/Provisioning-Logik von django-otp und
greift nur an einer einzigen Stelle ein: dem Secret ``key``.

* **Lesen** — :pyattr:`bin_key` entschluesselt das Secret transparent
  (:func:`core.services.security.totp.decrypt_totp_key`). Da ``verify_token``
  und ``config_url`` intern ``self.bin_key`` benutzen, funktionieren sie ohne
  weitere Aenderung. Der Decrypt-Pfad ist format-tolerant: ein noch nicht
  migrierter Klartext-Key wird unveraendert verarbeitet.
* **Schreiben** — :pymeth:`save` verschluesselt ein Klartext-Secret (z.B. der
  ``default_key`` bei ``create()``), bevor es die DB erreicht. Bereits
  verschluesselte Keys bleiben unangetastet (idempotent).

Die gesamte Applikation greift ausschliesslich ueber dieses Modell auf
TOTP-Geraete zu; das nackte ``TOTPDevice`` wird nur noch von der Proxy-
Definition und der Datenmigration referenziert. Siehe ADR-031.
"""

from __future__ import annotations

from binascii import unhexlify

from django_otp.plugins.otp_totp.models import TOTPDevice


class EncryptedTOTPDevice(TOTPDevice):
    """Proxy-``TOTPDevice``, dessen ``key`` at rest Fernet-verschluesselt ist."""

    class Meta:
        proxy = True
        verbose_name = "TOTP-Gerät (verschlüsselt)"
        verbose_name_plural = "TOTP-Geräte (verschlüsselt)"

    @property
    def bin_key(self):
        """Secret als Binaerstring — entschluesselt das gespeicherte Token transparent."""
        # Lazy-Import: ``core.services.security`` zieht ueber sein Paket-``__init__``
        # Modelle nach — ein Top-Level-Import hier wuerde beim App-Boot einen
        # Zirkel erzeugen (``core.models`` ⇄ ``core.services.security``).
        from core.services.security.totp import decrypt_totp_key

        return unhexlify(decrypt_totp_key(self.key).encode("ascii"))

    def save(self, *args, **kwargs):
        """Verschluesselt ein Klartext-Secret vor dem Persistieren (idempotent).

        Rewrap-Race (Refs #1362): ``django-otp.verify_token`` schreibt beim
        Verify ein **Voll**-``save()`` inkl. ``key`` — auf Erfolg nach
        ``throttle_reset(commit=False)``/``set_last_used_timestamp(commit=False)``,
        auf Fehlschlag ueber ``throttle_increment(commit=True)``. Der Verify
        will nur Throttle-/``last_t``-/``drift``-Felder aktualisieren, wuerde
        aber das (aus der DB geladene, evtl. bereits unter einem Alt-Schluessel
        gewickelte) Secret unveraendert mitschreiben. Laeuft parallel gerade
        ``reencrypt_fields`` und wickelt das Secret unter den neuen
        Primaerschluessel um, koennte ein solcher Verify-``save()`` den frischen
        Rewrap **nach** dessen Commit blind ueberschreiben (Lost Update) — das
        Geraet bliebe still unter dem Alt-Key, und ein danach entfernter
        Alt-Schluessel sperrt den MFA-pflichtigen Admin aus. Command-seitig ist
        das nicht schliessbar (der Verify nimmt keinen Row-Lock und schreibt
        einen vor dem Lock gelesenen Wert); die Race wird deshalb hier an der
        Quelle geschlossen: ein Voll-``save()`` auf eine bestehende Zeile mit
        bereits verschluesseltem Secret laesst ``key`` unangetastet.

        Das Secret wird damit nur noch bewusst geschrieben: beim Verschluesseln
        eines Klartext-Keys (``create()``/Provisioning, unten) oder ueber
        ``reencrypt_fields`` (``update(key=...)``) — nie durch einen Verify.
        """
        from core.services.security.totp import encrypt_totp_key, is_encrypted_totp_key

        update_fields = kwargs.get("update_fields")
        if self.key and not is_encrypted_totp_key(self.key):
            self.key = encrypt_totp_key(self.key)
            # Falls ein Aufrufer gezielt Felder speichert, muss ``key`` dabei
            # sein — sonst landet das frisch verschluesselte Secret nicht in der DB.
            if update_fields is not None and "key" not in update_fields:
                kwargs["update_fields"] = list(update_fields) + ["key"]
        elif self.pk is not None and update_fields is None:
            # Voll-``save()`` auf bestehende Zeile, Secret bereits verschluesselt
            # = Verify-/Throttle-Pfad. ``key`` aus dem UPDATE ausschliessen, indem
            # explizit alle uebrigen konkreten Felder gesetzt werden (Proxy: die
            # Felder liegen auf ``otp_totp.TOTPDevice``, daher ``concrete_fields``).
            kwargs["update_fields"] = [
                f.name for f in self._meta.concrete_fields if f.name != "key" and not f.primary_key
            ]
        super().save(*args, **kwargs)
