"""Hot-Path-Storage fuer encrypted File-Vault-Attachments.

Enthaelt die produktiv aufgerufenen Funktionen rund um Upload, Replace,
Soft-Delete und Read-Pfade. Validation-Policy haengt an
:mod:`core.services.file_vault.policy`; physischer Cleanup (Orphans,
Owner-Loeschung) liegt in :mod:`core.services.file_vault.cleanup`.

Owner-Generik (AP2, Refs #1738 / ADR-039): Ein Anhang haengt an genau EINEM
Owner — ``Event``, ``Client`` oder ``Case``. Alle Schreib-/Lesepfade
adressieren ihn ueber den geteilten Reverse-Accessor ``owner.attachments``
und leiten die ``facility`` IMMER aus ``owner.facility`` ab (ADR-039 §2:
``facility`` ist nie ein freier Parameter). Wo ein Aufrufer die ``facility``
zusaetzlich mitgibt (``prepare_encrypted_upload``/``store_encrypted_file``,
weil die Pre-Encrypt-Policy sie ohne Owner braucht), erzwingt ein Guard die
Uebereinstimmung fail-loud.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings as django_settings
from django.db import connection, transaction
from django.utils import timezone

from core.models.attachment import Attachment, owner_field_name
from core.services.file_vault.encryption import decrypt_file_stream, encrypt_field, encrypt_file, safe_decrypt
from core.services.file_vault.policy import (
    StorageQuotaBackstopError,
    check_storage_quota_at_commit,
    enforce_allowed_file_types,
    enforce_archive_limits,
    enforce_image_limits,
    enforce_magic_bytes,
    enforce_storage_quota,
    enforce_upload_size,
    run_virus_scan,
    strip_image_metadata,
)

logger = logging.getLogger(__name__)


def _facility_dir(facility):
    """Return the storage directory for a facility."""
    return Path(django_settings.MEDIA_ROOT) / str(facility.pk)


@dataclass
class StagedUpload:
    """Ergebnis von :func:`prepare_encrypted_upload` — eine bereits verschluesselt
    auf Disk liegende, aber noch NICHT als ``Attachment`` persistierte Datei.

    Traegt alle Werte, die :func:`commit_staged_upload` fuer den atomaren
    DB-/Audit-Write braucht, sodass zwischen Scan/Encrypt (teuer, synchron)
    und dem schnellen Persistieren keine erneute Datei-Beruehrung noetig ist
    (Refs #1345/#1960).
    """

    storage_name: str
    output_path: Path
    detected_mime: str
    file_size: int
    original_name: str
    content_type: str


def prepare_encrypted_upload(facility, uploaded_file, user, *, owner=None, pending_bytes=0):
    """Validieren, scannen und verschluesseln — OHNE DB-Write (Refs #1345).

    Fuehrt die volle Pre-Encrypt-Policy-Pipeline plus ClamAV-Scan und
    Fernet-Verschluesselung auf Disk aus und liefert einen :class:`StagedUpload`.
    Bewusst OHNE Aufrufer-Transaktion gedacht: der teure, synchrone Teil (ClamAV
    bis ``CLAMAV_TIMEOUT`` pro Datei + Verschluesselung) wird so VOR den
    ``transaction.atomic()``-Block des Aufrufers gezogen. Dadurch wird der
    per-Facility ``pg_advisory_xact_lock`` des AuditLog-Inserts (Hashkette) NICHT
    ueber den Scan gehalten — andere audit-schreibende Aktionen derselben
    Facility blockieren nicht mehr (Schreibserialisierung je Facility, #1345).

    ``pending_bytes`` (Refs #2129, Fix-Runde 1): Summe der im selben Request
    bereits gestagten, aber noch nicht persistierten Dateien. Nur die
    Staging-Schleifen in ``services/attachments/crud.py`` fuellen den Wert; fuer
    Einzel-Aufrufer bleibt der Default 0 und damit das bisherige Verhalten.

    Reihenfolge der Checks bleibt EXAKT wie bisher (#1268: Groesse VOR
    Voll-Pufferung im Virenscan — Memory-DoS-Schutz; #1274: verifizierter
    libmagic-MIME festgehalten). ``owner`` (Ereignis, Person oder Fall) ist
    optional und geht nur in die ``SECURITY_VIOLATION``-Audit-Attribution ein
    (``None`` bei Anlage eines noch nicht existierenden Owners — der Create-Pfad
    staged VOR dem Insert). Bei jedem Fehler wird eine bereits geschriebene
    ``.enc``-Datei entfernt und die Exception re-raised.

    1. Enforce ``Settings.allowed_file_types`` extension whitelist (Refs #610).
    2. Enforce ``min(Facility-Limit, globaler Cap)`` VOR jeder Voll-Pufferung
       (#1268/#1363).
    2a. Enforce das Speicherkontingent der Einrichtung (#2129) — vor dem Scan.
    3. Scan file with ClamAV (if CLAMAV_ENABLED) BEFORE encryption — infizierte
       Uploads werden mit ``ValidationError`` abgelehnt und als
       ``SECURITY_VIOLATION`` protokolliert (fail-closed, Issue #524).
    4. Verify magic-bytes MIME matches the declared ``content_type`` (Refs #610).
    5. Decompression-/Zip-Bomb-Guards (#1268/#1310).
    6. Metadaten-Bereinigung der Bild-Uploads (#2128) — EXIF/GPS, PNG-Textchunks
       und XMP/IPTC werden entfernt, bevor irgendetwas verschluesselt wird.
    7. UUID-Dateiname + Facility-Unterverzeichnis + Fernet-Encrypt auf Disk
       (Chunks an die Storage-ID gebunden, A4.5 / Refs #1016).
    """
    enforce_allowed_file_types(facility, uploaded_file, owner, user)
    # #1268: harte Service-Groessenobergrenze VOR der Voll-Pufferung im Virenscan
    # (Memory-DoS-Schutz). #1363: erzwingt zugleich das per-Facility-Limit als SSOT
    # (min(Facility-Limit, globaler Cap)) fuer ALLE Aufrufer, auch den Replace-Pfad,
    # der am DynamicEventDataForm vorbeilaeuft.
    enforce_upload_size(facility, uploaded_file, owner, user)
    # #2129 (N-5): Speicherkontingent der Einrichtung VOR dem Virenscan — fuer
    # einen Upload, der ohnehin abgelehnt wird, soll kein synchroner ClamAV-Lauf
    # anfallen. Bewusst kein SECURITY_VIOLATION (normaler Betriebszustand), aber
    # mit eigener ATTACHMENT_QUOTA_EXCEEDED-Audit-Zeile.
    enforce_storage_quota(facility, uploaded_file, owner, user, pending_bytes=pending_bytes)
    run_virus_scan(facility, uploaded_file, owner, user)
    # #1274: den verifizierten (libmagic) MIME festhalten, damit er beim Download
    # massgeblich ist statt des browser-gemeldeten content_type.
    detected_mime = enforce_magic_bytes(facility, uploaded_file, owner, user)
    # #1268: Decompression-Bomb-Schutz fuer Bild-Uploads (Pixel-Obergrenze).
    # #2128 (Fix-Runde 1): der erkannte MIME entscheidet, ob es ein Bild ist —
    # nicht die Endung (ein JPEG unter ``foto.docx`` umging sonst den Cap).
    enforce_image_limits(facility, uploaded_file, owner, user, detected_mime)
    # #1310 (S4): Archiv-Expansions-Guard (Zip-Bomb) fuer ZIP/OOXML-Container.
    enforce_archive_limits(facility, uploaded_file, owner, user)
    # #2128 (N-1): Bild-Metadaten (EXIF/GPS, PNG-Textchunks, XMP/IPTC) entfernen —
    # NACH dem Pixel-Cap (sonst dekodierte der Re-Encode eine Decompression-Bomb
    # voll in den RAM) und unmittelbar VOR der Verschluesselung. ``payload`` ist
    # fuer Nicht-Bilder dasselbe Objekt, sonst die bereinigte Datei; ab hier
    # gilt AUSSCHLIESSLICH sie (Groesse/MIME der Zeile muessen zu den
    # tatsaechlich gespeicherten Bytes passen).
    payload = strip_image_metadata(facility, uploaded_file, owner, user, detected_mime)

    storage_name = f"{uuid.uuid4()}.enc"
    output_path = _facility_dir(facility) / storage_name

    # A4.5 (Refs #1016): Chunks an die Storage-ID binden (v2-Format) — schützt
    # Bestandsdateien gegen Reorder/Truncation/Cross-File-Splicing auf der Disk.
    try:
        encrypt_file(payload, output_path, file_id=storage_name)
    except Exception:
        # Scheitert die Verschluesselung, keine halbe ``.enc`` auf Disk lassen (#662).
        output_path.unlink(missing_ok=True)
        raise

    return StagedUpload(
        storage_name=storage_name,
        output_path=output_path,
        # #2128: der Container bleibt beim Re-Encode identisch (gespeichert wird
        # im von Pillow erkannten Quellformat), der verifizierte libmagic-MIME
        # gilt deshalb unveraendert weiter.
        detected_mime=detected_mime or "",
        file_size=payload.size,
        original_name=uploaded_file.name,
        content_type=uploaded_file.content_type or "application/octet-stream",
    )


def _lock_superseded_row(owner, supersedes) -> None:
    """Sperrt die abzuloesende ``Attachment``-Zeile — Schritt 1 der Reihenfolge.

    Der Replace-Pfad schreibt die Vorgaengerzeile erst am Ende
    (``is_current``/``superseded_by``/``superseded_at``). Ohne diese Sperre
    faellt die Zeilensperre also NACH dem Advisory-Lock der Einrichtung an,
    waehrend ``soft_delete_attachment_chain`` sie VOR seiner Audit-Zeile nimmt —
    zwei gegenlaeufige Reihenfolgen auf denselben zwei Sperren. Ein
    ``SELECT … FOR UPDATE`` zieht sie an den Anfang und macht die Reihenfolge
    ueberall gleich (Refs #2178).

    Ist die Zeile inzwischen physisch getilgt, sperrt das ``SELECT`` nichts —
    das anschliessende ``save(update_fields=…)`` verhaelt sich unveraendert.

    Der Zugriff laeuft ueber den Reverse-Accessor des Owners (ADR-039 §2, wie
    ``soft_delete_attachment_chain``) — damit ist er owner- und facility-gescopt
    und sperrt nie eine fremde Zeile, selbst wenn eine falsche ``pk`` hereinkaeme.
    """
    owner.attachments.select_for_update().filter(pk=supersedes.pk).values_list("pk", flat=True).first()


def commit_staged_upload(owner, field_template, staged, user, *, supersedes=None, sort_order=None, audit=True):
    """Persistiere Upload und Audit-Zeile atomar; Fehler raeumen die Datei auf.

    Reisst der Kontingent-Backstop (Refs #2178), traegt die Ausnahme die
    Nutzdaten der Rechenschaftszeile ``ATTACHMENT_QUOTA_EXCEEDED`` mit sich.
    Geschrieben wird sie erst, wenn keine Transaktion mehr offen ist, die sie
    wieder mitnehmen wuerde:

    * **ohne Aufrufer-``atomic()``** (Seed, direkter Service-Aufruf) ist der
      Rollback hier schon durch — die Zeile entsteht sofort;
    * **mit Aufrufer-``atomic()``** (Regelfall: ``crud.finalize_staged_files``/
      ``crud.apply_attachment_changes`` unter der View-Transaktion) faellt sie
      dem aeussersten Rand zu, den
      :func:`~core.services.attachments.crud.quota_backstop_audit_boundary`
      markiert. Der Vorab-Check ersetzt sie NICHT: im abgefangenen Rennen hat
      er erfolgreich geprueft und ist ohne Audit zurueckgekehrt.

    Die Ausnahme IST ein ``ValidationError`` mit dem Text des Vorab-Checks —
    Views und Formulare reagieren unveraendert, auch wenn niemand sie abfaengt
    (dann fehlt nur die Rechenschaftszeile, nie die Ablehnung).
    """
    try:
        with transaction.atomic():
            return _commit_staged_upload(
                owner,
                field_template,
                staged,
                user,
                supersedes=supersedes,
                sort_order=sort_order,
                audit=audit,
            )
    except StorageQuotaBackstopError as exc:
        staged.output_path.unlink(missing_ok=True)
        if not connection.in_atomic_block:
            # Kein Aufrufer-``atomic()`` mehr offen: der Rollback ist durch, die
            # Zeile kann sofort und dauerhaft geschrieben werden.
            exc.write_audit()
        # Sonst traegt die Ausnahme die Nutzdaten weiter nach oben; auditiert
        # wird am aeussersten Transaktionsrand (``quota_backstop_audit_boundary``).
        raise
    except Exception:
        staged.output_path.unlink(missing_ok=True)
        raise


def _commit_staged_upload(owner, field_template, staged, user, *, supersedes=None, sort_order=None, audit=True):
    """Persistiere eine per :func:`prepare_encrypted_upload` gestagte Datei als
    ``Attachment`` samt Audit-Zeile — schneller DB-Write (Refs #1345/#1960).

    Sicher innerhalb der Aufrufer-``transaction.atomic()`` aufzurufen: hier faellt
    kein ClamAV-Scan und keine Verschluesselung mehr an, nur Attachment-,
    Versions- und AuditLog-Schreibvorgaenge. Eine Transaktion ist Pflicht — der
    Kontingent-Backstop (Refs #2178) nimmt eine ``pg_advisory_xact_lock``-Sperre,
    die ausserhalb einer Transaktion wirkungslos waere (fail-loud in
    ``quota.lock_facility_storage``).

    ``owner`` ist ein ``Event``, ``Client`` oder ``Case`` (AP2, Refs #1738); der
    passende Owner-FK wird ueber :func:`~core.models.attachment.owner_field_name`
    gesetzt (fail-loud bei fremden Modellen), die ``facility`` IMMER aus
    ``owner.facility`` abgeleitet (ADR-039 §2).

    Modi:
    * ``supersedes=None`` → **add**: Neue Versionskette mit frischer ``entry_id``.
    * ``supersedes=<attachment>`` → **replace**: Übernimmt ``entry_id`` und
      ``sort_order`` des Vorgängers, markiert diesen als ersetzt
      (``is_current=False``, ``superseded_by=<new>``, ``superseded_at``).

    Bei einem DB-Fehler wird die gestagte ``.enc``-Datei entfernt (#662).
    Disk file stays until the owner itself is deleted or anonymized
    (Refs #587/622 — Versionshistorie + Stufe-B Multi-Entry).
    """
    try:
        # Fail-loud im try-Block: ein unzulaessiger Owner soll dieselbe
        # Cleanup-Garantie ausloesen wie ein DB-Fehler (#662) — sonst bliebe
        # die bereits verschluesselte ``.enc`` als Waise auf der Disk liegen.
        owner_field = owner_field_name(owner)
        # SPERR-REIHENFOLGE (Refs #2178): (1) Attachment-Zeile, (2) Advisory-Lock
        # der Einrichtung (Kontingent = Audit-Hashkette, ein einziger Key).
        # Dieselbe Reihenfolge haelt ``soft_delete_attachment_chain`` ein (erst
        # das Zeilen-Update, dann die Audit-Zeile). Ohne Schritt 1 nahm der
        # Replace-Pfad den Advisory-Lock ZUERST und die Zeilensperre erst beim
        # ``supersedes.save()`` — gegen einen gleichzeitigen Soft-Delete
        # derselben Kette ein Deadlock-Zyklus (PostgreSQL bricht eine der beiden
        # Transaktionen mit SQLSTATE 40P01 ab).
        if supersedes is not None:
            _lock_superseded_row(owner, supersedes)
        # #2178 (Red-Team B-13): fail-closed Backstop VOR dem Insert — die Summe
        # ein zweites Mal lesen, diesmal unter der Sperre der Einrichtung. Der
        # Vorab-Check in ``prepare_encrypted_upload`` liegt ausserhalb jeder
        # Transaktion und spart nur den Scan; ohne diese Stufe ueberziehen
        # gleichzeitige Uploads das Kontingent gemeinsam. Gesperrt wird
        # ausschliesslich der kurze DB-Teil (Scan/Encrypt sind laengst vorbei),
        # und zwar mit derselben Sperre, die die AuditLog-Hashkette wenige
        # Anweisungen spaeter ohnehin nimmt — ein zweiter Namensraum brauchte
        # eine Lock-Reihenfolge, die die Aufrufer-Transaktionen nicht einhalten
        # koennen (siehe ``quota.storage_lock_name``).
        check_storage_quota_at_commit(owner, user, staged.file_size)
        if supersedes is not None:
            # Replace-Modus: entry_id + sort_order vom Vorgänger übernehmen.
            entry_id = supersedes.entry_id
            effective_sort = supersedes.sort_order if sort_order is None else sort_order
            mode = "replace"
        else:
            # Add-Modus: frische entry_id, sort_order wie übergeben (oder 0).
            entry_id = uuid.uuid4()
            effective_sort = sort_order if sort_order is not None else 0
            mode = "add"

        new_attachment = Attachment.objects.create(
            # ADR-039 §2: facility IMMER aus dem Owner abgeleitet, nie als
            # freier Parameter (owner-generisch ab AP2, Refs #1738).
            facility=owner.facility,
            **{owner_field: owner},
            field_template=field_template,
            storage_filename=staged.storage_name,
            original_filename_encrypted=encrypt_field(staged.original_name),
            file_size=staged.file_size,
            mime_type=staged.content_type,
            detected_mime=staged.detected_mime or "",
            created_by=user,
            is_current=True,
            entry_id=entry_id,
            sort_order=effective_sort,
        )

        if supersedes is not None:
            supersedes.is_current = False
            supersedes.superseded_by = new_attachment
            supersedes.superseded_at = timezone.now()
            supersedes.save(update_fields=["is_current", "superseded_by", "superseded_at"])

        if audit:
            from core.services.file_vault.audit import audit_attachment_upload

            audit_attachment_upload(owner, field_template, new_attachment, staged, user, mode=mode)
    except Exception:
        # Synchroner Fehler in DB-Operationen — geschriebene Datei sofort
        # entfernen, damit der Disk nicht waechst (#662).
        staged.output_path.unlink(missing_ok=True)
        raise

    return new_attachment


def store_encrypted_file(
    facility,
    uploaded_file,
    field_template,
    owner,
    user,
    supersedes=None,
    sort_order=None,
    *,
    audit=True,
):
    """Encrypt and store an uploaded file. Returns Attachment instance.

    Duenner Wrapper um :func:`prepare_encrypted_upload` + :func:`commit_staged_upload`
    (Refs #1345). Fuer Aufrufer OHNE eigene Transaktion, die Scan/Encrypt und
    DB-Write nicht trennen muessen (Seed): der Vertrag (Reihenfolge der Checks,
    Cleanup-Garantie) bleibt unveraendert; ``event`` heisst ab AP2 ``owner``
    (Ereignis, Person oder Fall, Refs #1738). ``audit=False`` ist ausschliesslich
    fuer den expliziten dev/e2e-Seed-Pfad vorgesehen (M4-Entscheid zu #1960);
    produktive Aufrufer behalten den fail-closed Default.

    **Nicht** in einem ``transaction.atomic`` verwenden, das eine AuditLog-Zeile
    schreibt: der Scan liefe dann unter dem per-Facility-Advisory-Lock der
    Hashkette (Refs #1345/#1956). Die Anlage-/Update-Pfade stagen deshalb ueber
    :func:`~core.services.attachments.crud.stage_attachment_uploads`.

    ADR-039 §2 fail-loud: ``facility`` ist hier nur noch der Policy-/Pfad-Kontext
    der Pre-Encrypt-Pipeline — massgeblich fuer die Zeile ist ``owner.facility``.
    Weichen beide voneinander ab, laege die ``.enc``-Datei in einem fremden
    Facility-Verzeichnis, waehrend die DB-Zeile (und damit die RLS-Sicht) zur
    Owner-Facility gehoerte. Das wird abgewiesen statt still divergieren zu
    lassen — VOR jedem Scan/Encrypt, damit keine Datei entsteht.
    """
    if owner is not None and getattr(owner, "facility_id", None) != facility.pk:
        raise ValueError(
            "Facility des Owners weicht von der uebergebenen Facility ab "
            f"(owner.facility_id={getattr(owner, 'facility_id', None)!r}, facility={facility.pk!r})"
        )
    staged = prepare_encrypted_upload(facility, uploaded_file, user, owner=owner)
    return commit_staged_upload(
        owner,
        field_template,
        staged,
        user,
        supersedes=supersedes,
        sort_order=sort_order,
        audit=audit,
    )


def soft_delete_attachment_chain(owner, entry_id, user, *, audit=True, field=None):
    """Markiere alle Attachments einer Versionskette (entry_id) als soft-deleted.

    Für UI-Lösch-Aktionen (Stufe B, Refs #622). Physischer Disk-Cleanup
    erfolgt weiterhin erst im Owner-Delete/Anonymize. Bereits soft-deleted
    eingetragene Entries werden nicht erneut angefasst.

    ``owner`` ist ein ``Event``, ``Client`` oder ``Case``; der Zugriff laeuft
    ueber den geteilten Reverse-Accessor ``owner.attachments`` und ist damit
    automatisch owner-gescopt (AP2, Refs #1738).

    Returns: Anzahl der neu soft-deleted Attachments in der Kette.
    """
    with transaction.atomic():
        qs = owner.attachments.filter(entry_id=entry_id, deleted_at__isnull=True)
        if audit and field is None:
            field = qs.values_list("field_template__slug", flat=True).first()
        updated = qs.update(deleted_at=timezone.now())
        if updated and audit:
            from core.services.file_vault.audit import audit_attachment_delete

            audit_attachment_delete(owner, user, entry_id=entry_id, field=field or "", versions=updated)
        return updated


def get_current_entries_for_field(owner, field_template):
    """Liefert die aktuellen, nicht soft-deleted Einträge für ein FILE-Feld.

    Gibt Heads der Versionsketten zurück (is_current=True, deleted_at IS NULL),
    sortiert nach ``sort_order`` und ``created_at``. Der Aufrufer kann daraus
    ``entry_id``, ``pk``, ``original_filename`` etc. ableiten. ``owner`` ist ein
    ``Event``, ``Client`` oder ``Case`` (AP2, Refs #1738).
    """
    return list(
        owner.attachments.filter(
            field_template=field_template,
            is_current=True,
            deleted_at__isnull=True,
        ).order_by("sort_order", "created_at")
    )


def get_attachment_path(attachment):
    """Return the full filesystem path of an encrypted attachment.

    G2-Befund (Refs #1738): laeuft ueber die **eigene** ``attachment.facility``
    statt ueber ``attachment.event.facility`` — fuer personen-/falleigene Zeilen
    waere der Umweg ein ``AttributeError`` (``event`` ist dort ``None``). Fuer
    Ereignis-Anhaenge ist das Ergebnis bitgleich zum Bestand: die
    ``facility``-Spalte wurde in Migration 0130 genau aus ``event.facility``
    zurueckgeschrieben — es gibt keine Datei-Moves.
    """
    return _facility_dir(attachment.facility) / attachment.storage_filename


def get_decrypted_file_stream(attachment):
    """Return a generator of decrypted chunks for streaming download."""
    # A4.5: v2-Bindung gegen die Storage-ID verifizieren (Legacy-v1 ignoriert sie).
    return decrypt_file_stream(get_attachment_path(attachment), file_id=attachment.storage_filename)


def get_original_filename(attachment):
    """Decrypt and return the original filename."""
    return safe_decrypt(attachment.original_filename_encrypted, default="download")


def delete_attachment_file(attachment):
    """Delete the physical encrypted file from disk."""
    path = get_attachment_path(attachment)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to delete attachment file: %s", path)
