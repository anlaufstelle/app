"""Re-encrypt all encrypted Event/EventHistory/EventAttachment fields with the current primary key.

A4.4 (Refs #1024): Für ``Event.data_json`` werden zusätzlich Plaintext-PII-Felder
(laut document_type ``is_encrypted=True``) nachverschlüsselt — Heal von Altbestand
vor Einführung der Feldverschlüsselung.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Event, EventHistory
from core.models.attachment import EventAttachment
from core.services.file_vault import encrypt_event_data, encrypt_field, is_encrypted_value, safe_decrypt
from core.services.system import bypass_replication_triggers


def _reencrypt_data_json(payload):
    """Re-encrypt every encrypted value in a ``data_json``-style dict.

    Returns ``(new_payload_or_None_if_unchanged, changed_flag)``. Operates on
    a copy, so the caller decides whether to persist.
    """
    if not payload or not isinstance(payload, dict):
        return payload, False
    changed = False
    new_payload = dict(payload)
    for key, value in payload.items():
        if is_encrypted_value(value):
            decrypted = safe_decrypt(value)
            if decrypted != "[verschlüsselt]":
                new_payload[key] = encrypt_field(decrypted)
                changed = True
    return new_payload, changed


class Command(BaseCommand):
    help = (
        "Re-encrypt all encrypted fields with the current primary encryption key — "
        "Event.data_json, EventHistory.data_before/data_after, "
        "EventAttachment.original_filename_encrypted (Refs #783), TOTP-Secrets "
        "(Refs #1362). Heilt zusätzlich Plaintext-PII in Event.data_json (Refs #1024 A4.4)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be re-encrypted without saving.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        events_updated = self._reencrypt_events(dry_run)
        history_updated = self._reencrypt_event_history(dry_run)
        attachments_updated = self._reencrypt_attachments(dry_run)
        totp_updated = self._reencrypt_totp_devices(dry_run)

        action = "Would re-encrypt" if dry_run else "Re-encrypted"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {events_updated} events, {history_updated} history entries, "
                f"{attachments_updated} attachments, {totp_updated} TOTP devices."
            )
        )

    def _reencrypt_events(self, dry_run: bool) -> int:
        updated = 0
        for event in Event.objects.filter(is_deleted=False).iterator(chunk_size=500):
            new_data, changed = _reencrypt_data_json(event.data_json)
            # A4.4 (Refs #1024): zusätzlich Plaintext-PII-Felder (laut
            # document_type ``is_encrypted=True``) nachverschlüsseln — heilt
            # Altbestand, der vor Einführung der Feldverschlüsselung als Klartext
            # gespeichert wurde. ``encrypt_event_data`` lässt bereits verschlüsselte
            # Werte unangetastet.
            healed = encrypt_event_data(event.document_type, new_data) if event.document_type_id else new_data
            if changed or healed != event.data_json:
                updated += 1
                if not dry_run:
                    event.data_json = healed
                    event.save(update_fields=["data_json", "updated_at"])
        return updated

    def _reencrypt_event_history(self, dry_run: bool) -> int:
        """Refs #783 — EventHistory ist append-only (Trigger ``prevent_eventhistory_update``).

        UPDATE wird per ``bypass_replication_triggers`` umgangen — derselbe
        Pfad, den ``services/clients.anonymize_client`` fuer redaktierte
        History-Eintraege nutzt.
        """
        updated = 0
        for hist in EventHistory.objects.iterator(chunk_size=500):
            before, before_changed = _reencrypt_data_json(hist.data_before)
            after, after_changed = _reencrypt_data_json(hist.data_after)
            if not (before_changed or after_changed):
                continue
            updated += 1
            if dry_run:
                continue
            with transaction.atomic(), bypass_replication_triggers():
                EventHistory.objects.filter(pk=hist.pk).update(
                    data_before=before,
                    data_after=after,
                )
        return updated

    def _reencrypt_totp_devices(self, dry_run: bool) -> int:
        """Wickelt jedes TOTP-Secret unter den aktuellen Primaerschluessel neu ein.

        Refs #1362: Das Secret liegt at rest Fernet-verschluesselt in
        ``otp_totp_totpdevice.key``. Bei einer Key-Rotation (MultiFernet) bleiben
        Alt-Tokens dank Mehr-Key-Decrypt zwar lesbar — dieser Lauf wickelt sie
        aber aktiv unter den neuen Primaerschluessel um, sodass ein
        ausrangierter Schluessel wirklich entfernt werden kann. Ein
        unmigrierter Klartext-Key wird dabei mitgeheilt.

        Rewrap-Race (Refs #1362, Review-Befund): Der eigentliche Verursacher —
        ``django-otp.verify_token`` schreibt beim Verify ein Voll-``save()`` inkl.
        (evtl. Alt-Key-)``key`` — ist an der Quelle geschlossen: der Verify-Pfad
        laesst ``key`` nun unangetastet (siehe :class:`core.models.mfa.
        EncryptedTOTPDevice`). Ein Row-Lock im Command allein koennte diese Race
        NICHT schliessen, weil der Verify keinen Lock nimmt und einen vor dem
        Lock gelesenen Wert **nach** unserem Commit zurueckschreiben koennte.

        Zusaetzlich haerten wir hier den Rewrap gegen jeden **anderen** parallelen
        Schreiber der ``key``-Spalte ab (kuenftige Re-Provisionierung, zweiter
        Rewrap-Lauf): je Geraet eine kurze ``transaction.atomic()`` mit
        ``select_for_update()`` (Row-Lock) + wertgebundenem Compare-and-Swap-
        ``update()``. Aendert sich die Zeile zwischen unserem gelockten Lesen und
        dem Schreiben doch (0 betroffene Zeilen), wird bis zu einer Obergrenze
        erneut versucht und andernfalls die pk laut gemeldet — damit der Operator
        den Alt-Schluessel erst nach einem sauberen Lauf entfernt (idempotent).
        """
        from core.models import EncryptedTOTPDevice
        from core.services.security.totp import decrypt_totp_key, encrypt_totp_key

        updated = 0
        contended: list[int] = []
        # pks vorab einsammeln statt ``select_for_update()`` ueber einen offenen
        # ``iterator()``-Cursor zu spannen — je Geraet eine eigene kurze,
        # gelockte Transaktion (minimaler Blast-Radius, keine Tabellen-weiten Locks).
        pks = list(EncryptedTOTPDevice.objects.values_list("pk", flat=True))
        for pk in pks:
            if dry_run:
                key = EncryptedTOTPDevice.objects.filter(pk=pk).values_list("key", flat=True).first()
                if not key:
                    continue
                decrypt_totp_key(key)  # validiert Entschluesselbarkeit vorab (deckt Alt-Key-Reste auf)
                updated += 1
                continue
            outcome = self._rewrap_one_totp_device(pk, decrypt_totp_key, encrypt_totp_key)
            if outcome == "rewrapped":
                updated += 1
            elif outcome == "contended":
                contended.append(pk)
        if contended:
            self.stderr.write(
                self.style.WARNING(
                    f"TOTP-Rewrap: {len(contended)} Geraet(e) blieben trotz Retries unter "
                    f"dem Alt-Schluessel (parallele MFA-Verifikation?): pks={contended}. Lauf "
                    "ERNEUT ausfuehren und den Alt-Schluessel NICHT aus ENCRYPTION_KEYS "
                    "entfernen, bis dieser Lauf ohne Meldung durchlaeuft."
                )
            )
        return updated

    def _rewrap_one_totp_device(self, pk, decrypt_totp_key, encrypt_totp_key, *, attempts: int = 3) -> str:
        """Rewrap eines einzelnen TOTP-Geraets unter Row-Lock + Compare-and-Swap.

        Liest ``key`` unter ``select_for_update()`` (also den *aktuell*
        committeten Wert — ein unmittelbar zuvor committeter Verify wird
        mitgenommen) und schreibt wertgebunden zurueck
        (``filter(pk=pk, key=current).update(...)``). Geschrieben wird per
        ``update()`` bewusst am Proxy-``save`` vorbei (das ein bereits
        verschluesseltes Secret unangetastet liesse) und der Wert-Filter macht
        die Schreiboperation zur atomaren CAS. Kollidiert ausnahmsweise dennoch
        etwas (0 betroffene Zeilen), wird bis ``attempts`` erneut versucht.

        Rueckgabe: ``"rewrapped"`` (Secret frisch umgewickelt), ``"empty"``
        (kein Secret, uebersprungen) oder ``"contended"`` (nach ``attempts``
        weiter Kontention → Melde-Anker fuer den Operator).
        """
        from core.models import EncryptedTOTPDevice

        for _ in range(attempts):
            with transaction.atomic():
                current = (
                    EncryptedTOTPDevice.objects.select_for_update().filter(pk=pk).values_list("key", flat=True).first()
                )
                if not current:
                    return "empty"
                new_value = encrypt_totp_key(decrypt_totp_key(current))
                affected = EncryptedTOTPDevice.objects.filter(pk=pk, key=current).update(key=new_value)
            if affected:
                return "rewrapped"
        return "contended"

    def _reencrypt_attachments(self, dry_run: bool) -> int:
        """``EventAttachment.original_filename_encrypted`` ist ein Fernet-
        verschluesseltes JSON-Marker-Dict. Hier reicht ein normales Save —
        keine append-only-Trigger schuetzen die Tabelle.
        """
        updated = 0
        for att in EventAttachment.objects.iterator(chunk_size=500):
            value = att.original_filename_encrypted
            if not is_encrypted_value(value):
                continue
            decrypted = safe_decrypt(value)
            if decrypted == "[verschlüsselt]":
                continue
            updated += 1
            if dry_run:
                continue
            att.original_filename_encrypted = encrypt_field(decrypted)
            att.save(update_fields=["original_filename_encrypted"])
        return updated
