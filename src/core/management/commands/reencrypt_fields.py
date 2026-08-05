"""Re-encrypt all encrypted Event/EventHistory/Attachment/Client/Case/
RectificationRequest fields with the current primary key.

M4 Strang C (Refs #1060): ``RectificationRequest`` ist der siebte Rotationspfad —
seine vier verschluesselten Spalten (Wertpaar, Begruendung, Pruefvermerk) sind
nach ADR-041 dauerhafte Rechenschaftsbelege und muessen daher mitrotieren, sonst
liesse sich ein ausrangierter Schluessel nie entfernen.

M24 (Refs #1738): ``Attachment`` ist seit ADR-039 owner-generisch (Ereignis,
Person, Fall) — die Rotation erfasst weiterhin ALLE Zeilen ohne Owner-Filter
(kein Verhaltensunterschied, aber jetzt ausdruecklich dokumentiert).

A4.4 (Refs #1024): Für ``Event.data_json`` werden zusätzlich Plaintext-PII-Felder
(laut document_type ``is_encrypted=True``) nachverschlüsselt — Heal von Altbestand
vor Einführung der Feldverschlüsselung. M2/P6 (Refs #1058): dasselbe Heal-Muster
gilt fuer ``Client.data_json``/``Case.data_json`` (``DynamicDataEncryptionMixin``,
P4a/P4b/P5) — sonst entstuende eine Rotationsluecke fuer die neuen Modellgruppen.

E17-Nachzug (Refs #1724): Event/Client/Case rotieren ALLE Rows inkl.
soft-deleted (kein ``is_deleted``-Filter) — der fruehere ``is_deleted=False``-
Filter bei Events liess soft-deleted Events mit noch vorhandenem ``data_json``
dauerhaft unter einem ausrangierten Schluessel liegen.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Case, Client, Event, EventHistory, RectificationRequest
from core.models.attachment import Attachment
from core.services.file_vault import encrypt_dynamic_data, encrypt_field, is_encrypted_value, safe_decrypt
from core.services.system import bypass_replication_triggers

# Refs #1016/#1070/#1512: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


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
        "Attachment.original_filename_encrypted (Refs #783), TOTP-Secrets "
        "(Refs #1362), Client.data_json, Case.data_json (Refs #1058), "
        "RectificationRequest-Wertpaare (Refs #1060). Heilt zusätzlich "
        "Plaintext-PII in Event/Client/Case.data_json (Refs #1024 A4.4, #1058)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be re-encrypted without saving.")

    def handle(self, *args, **options):
        # Final-Review A/Split (Refs #1058): Event/EventHistory/Attachment/
        # Client/Case stehen unter FORCE RLS. Als App-Rolle ohne Bypass-Kontext
        # liefen alle Schleifen ueber 0 Zeilen und der Command meldete trotzdem
        # Erfolg — die Rotations-Prozedur (docs/admin-guide.md § Schluessel-
        # Rotation) entfernte danach den Alt-Schluessel, obwohl nichts rotiert
        # wurde. Muster: enforce_retention (Refs #1016).
        if not _has_rls_bypass_context():
            raise CommandError(
                "Re-Verschlüsselung läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — "
                "sonst würden 0 Zeilen rotiert und trotzdem Erfolg gemeldet. Command mit der "
                "Admin-Rolle aufrufen: docker compose exec -e POSTGRES_USER=$POSTGRES_ADMIN_USER "
                "-e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD web python manage.py reencrypt_fields "
                "(siehe docs/ops-runbook.md § 9.5)."
            )
        dry_run = options["dry_run"]
        events_updated = self._reencrypt_events(dry_run)
        history_updated = self._reencrypt_event_history(dry_run)
        attachments_updated = self._reencrypt_attachments(dry_run)
        totp_updated = self._reencrypt_totp_devices(dry_run)
        clients_updated = self._reencrypt_clients(dry_run)
        cases_updated = self._reencrypt_cases(dry_run)
        rectifications_updated = self._reencrypt_rectification_requests(dry_run)

        action = "Would re-encrypt" if dry_run else "Re-encrypted"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {events_updated} events, {history_updated} history entries, "
                f"{attachments_updated} attachments, {totp_updated} TOTP devices, "
                f"{clients_updated} clients, {cases_updated} cases, "
                f"{rectifications_updated} rectification requests."
            )
        )

    def _reencrypt_events(self, dry_run: bool) -> int:
        """M2/P6-Nachzug (Refs #1724, Entscheid E17): rotiert ALLE ``Event``-Rows
        inkl. soft-deleted.

        Der bisherige ``is_deleted=False``-Filter war eine bewusst offen gelassene
        Rotations-Luecke (dokumentiert als "bekannte Luecke" in
        ``docs/admin-guide.md``): ein soft-deleted Event mit noch vorhandenem
        ``data_json`` (z.B. Altbestand von vor Einfuehrung der ``data_json``-
        Leerung in ``soft_delete_event``, oder Restore-Faelle) blieb dauerhaft
        unter einem ausrangierten Schluessel liegen. ``EventManager``/
        ``FacilityScopedManager`` filtert ``is_deleted`` nicht, ``Event.objects``
        deckt damit ohne Extra-Filter bereits den vollen Bestand ab — analog
        :meth:`_reencrypt_clients`/:meth:`_reencrypt_cases`.
        """
        updated = 0
        for event in Event.objects.iterator(chunk_size=500):
            new_data, changed = _reencrypt_data_json(event.data_json)
            # A4.4 (Refs #1024): zusätzlich Plaintext-PII-Felder (laut
            # document_type ``is_encrypted=True``) nachverschlüsseln — heilt
            # Altbestand, der vor Einführung der Feldverschlüsselung als Klartext
            # gespeichert wurde. ``encrypt_dynamic_data`` lässt bereits verschlüsselte
            # Werte unangetastet.
            healed = encrypt_dynamic_data(event.document_type, new_data) if event.document_type_id else new_data
            if changed or healed != event.data_json:
                updated += 1
                if not dry_run:
                    event.data_json = healed
                    event.save(update_fields=["data_json", "updated_at"])
        return updated

    def _reencrypt_clients(self, dry_run: bool) -> int:
        """M2/P6 (Refs #1058, Entscheid E17): rotiert ALLE ``Client``-Rows inkl.
        soft-deleted.

        ``FacilityScopedManager`` filtert ``is_deleted`` nicht (anders als eine
        ``SoftDeletableFacilityScopedManager``-Variante) — ``Client.objects``
        deckt damit ohne Extra-Filter bereits den vollen Bestand ab. Personen-
        Altbestände sollen nicht dauerhaft unter einem ausrangierten Schlüssel
        liegen bleiben, nur weil sie gelöscht sind (seit #1724 gilt dasselbe
        fuer Events, siehe :meth:`_reencrypt_events`).
        """
        updated = 0
        for client in Client.objects.iterator(chunk_size=500):
            new_data, changed = _reencrypt_data_json(client.data_json)
            # Refs #1730: ``Client.document_type`` ist nullable — ueber die lokale
            # Variable auf ``is not None`` narrowen (statt auf ``document_type_id``
            # zu prüfen), damit mypy den ``DocumentType | None``-Typ real
            # ausschliesst. Verhaltensgleich: der FK-Dereferenz passiert weiterhin
            # nur, wenn eine Zuordnung existiert (Kurzschluss dank ``if``-Ausdruck).
            document_type = client.document_type if client.document_type_id else None
            healed = encrypt_dynamic_data(document_type, new_data) if document_type is not None else new_data
            if changed or healed != client.data_json:
                updated += 1
                if not dry_run:
                    client.data_json = healed
                    client.save(update_fields=["data_json", "updated_at"])
        return updated

    def _reencrypt_cases(self, dry_run: bool) -> int:
        """M2/P6 (Refs #1058, Entscheid E17): rotiert ALLE ``Case``-Rows inkl.
        soft-deleted — Begruendung analog :meth:`_reencrypt_clients`.
        """
        updated = 0
        for case in Case.objects.iterator(chunk_size=500):
            new_data, changed = _reencrypt_data_json(case.data_json)
            # Refs #1730: analog ``_reencrypt_clients`` — ``Case.document_type``
            # ist nullable, echtes Narrowing statt ``document_type_id``-Check.
            document_type = case.document_type if case.document_type_id else None
            healed = encrypt_dynamic_data(document_type, new_data) if document_type is not None else new_data
            if changed or healed != case.data_json:
                updated += 1
                if not dry_run:
                    case.data_json = healed
                    case.save(update_fields=["data_json", "updated_at"])
        return updated

    #: Die vier feldverschluesselten Spalten von ``RectificationRequest`` (M4,
    #: Refs #1060). Als Konstante, damit ein spaeter ergaenztes Feld hier
    #: sichtbar fehlt statt still aus der Rotation zu fallen.
    _RECTIFICATION_COLUMNS = ("old_value", "new_value", "reason", "review_note")

    def _reencrypt_rectification_requests(self, dry_run: bool) -> int:
        """Siebter Rotationspfad: die Wertpaare der Art.-16-Antraege (Refs #1060).

        ``old_value``/``new_value``/``reason``/``review_note`` sind
        ``encrypt_field``-Marker-Dicts (ADR-041) — mechanisch derselbe Fall wie
        ``Attachment.original_filename_encrypted``, nur vier Spalten pro Zeile.
        Die JSON-Stufe **innerhalb** des Klartexts bleibt dabei unangetastet:
        entschluesselt wird zum JSON-String, der unveraendert wieder eingewickelt
        wird — hier wird rotiert, nicht interpretiert.

        Es wird ueber ALLE Zeilen iteriert, auch ueber abgeschlossene Antraege:
        genehmigte/abgelehnte Antraege sind der Rechenschaftsnachweis und
        duerfen nicht unter einem ausrangierten Schluessel liegenbleiben (Muster
        :meth:`_reencrypt_clients`, Refs #1724/E17).
        """
        updated = 0
        for rr in RectificationRequest.objects.iterator(chunk_size=500):
            changed: list[str] = []
            for column in self._RECTIFICATION_COLUMNS:
                value = getattr(rr, column)
                if not is_encrypted_value(value):
                    continue
                decrypted = safe_decrypt(value)
                if decrypted == "[verschlüsselt]":
                    continue
                setattr(rr, column, encrypt_field(decrypted))
                changed.append(column)
            if not changed:
                continue
            updated += 1
            if not dry_run:
                rr.save(update_fields=changed)
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
        """``Attachment.original_filename_encrypted`` ist ein Fernet-
        verschluesseltes JSON-Marker-Dict. Hier reicht ein normales Save —
        keine append-only-Trigger schuetzen die Tabelle.

        M24 (Refs #1738, ADR-039): Die Iteration ist **owner-generisch** und
        war es immer — sie laeuft ueber ALLE ``Attachment``-Zeilen, gleich ob
        sie an einem Ereignis, einer Person oder einem Fall haengen, und
        schliesst soft-geloeschte (``deleted_at``) mit ein. Kein Owner-Filter,
        keine Verhaltensaenderung durch die Owner-Generik; die neuen
        personen-/falleigenen Zeilen rotieren automatisch mit.
        """
        updated = 0
        for att in Attachment.objects.iterator(chunk_size=500):
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
