"""Pre-encrypt validation pipeline fuer File-Vault-Uploads.

Buendelt die Validation-Schritte, die ``store_encrypted_file`` vor der
eigentlichen Verschluesselung ausfuehrt: Extension-Whitelist,
Magic-Bytes-Sniffing und ClamAV-Virus-Scan. Jeder fehlgeschlagene Check
schreibt einen ``SECURITY_VIOLATION``-AuditLog (Refs #610) und hebt eine
``ValidationError``.

Frueher lag die Pipeline in ``file_vault_validation.py``; im Subpackage-
Split (#910) ist sie nach ``file_vault/policy.py`` umgezogen. Die
do-not-mutate-Whitelist in ``pyproject.toml`` zeigt entsprechend auf
diesen Pfad — echte Encryption-Pfade ueber ``store_encrypted_file``
produzieren 15-30 s Mutmut-Timeouts pro Mutation.
"""

from __future__ import annotations

import logging
import os
import struct

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.constants import DEFAULT_ALLOWED_FILE_TYPES
from core.models.settings import Settings
from core.services.file_vault.audit import log_attachment_violation
from core.services.file_vault.virus_scan import VirusScannerUnavailableError, scan_file

logger = logging.getLogger(__name__)

# Bild-Extensions, fuer die der Decompression-Bomb-Pixelcheck greift (#1268).
_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff"})

# ZIP-Family-Extensions, fuer die der Archiv-Expansions-Guard greift (#1310, S4).
# OOXML (docx/xlsx/pptx) sind ZIP-Container — derselbe Decompression-Bomb-Vektor.
_ARCHIVE_EXTENSIONS = frozenset({"zip", "docx", "xlsx", "pptx"})


def enforce_upload_size(facility, uploaded_file, event, user):
    """Reject uploads exceeding the hard service-layer size ceiling.

    Refs #1268 (T3): Die Form-Schicht prueft die per-Facility-Groesse; der
    Service ist die letzte Bastion fuer programmatische Aufrufer.
    ``FILE_VAULT_MAX_UPLOAD_BYTES`` ist eine absolute Obergrenze, die VOR jeder
    Voll-Pufferung (Virenscan/Encrypt) greift — so wird eine Riesendatei nicht
    erst in den RAM gelesen (authentifizierter Memory-Exhaustion-DoS). Jeder
    Verstoss wird als ``SECURITY_VIOLATION`` protokolliert (Form-Bypass ist
    verdaechtig).
    """
    max_bytes = getattr(django_settings, "FILE_VAULT_MAX_UPLOAD_BYTES", 50 * 1024 * 1024)
    size = getattr(uploaded_file, "size", None)
    if size is None or size <= max_bytes:
        return

    log_attachment_violation(
        facility,
        user,
        event,
        reason="file_too_large",
        filename=uploaded_file.name,
        size=size,
        max_bytes=max_bytes,
    )
    raise ValidationError(
        _("Datei zu groß (%(mb)d MB). Maximum: %(max)d MB.")
        % {"mb": size // (1024 * 1024), "max": max_bytes // (1024 * 1024)}
    )


def enforce_image_limits(facility, uploaded_file, event, user):
    """Reject decompression-bomb images (Refs #1268 (T3)).

    Setzt ``PIL.Image.MAX_IMAGE_PIXELS`` und lehnt Bilder ab, deren (aus dem
    Header gelesene, NICHT dekodierte) Pixelzahl ``FILE_VAULT_MAX_IMAGE_PIXELS``
    uebersteigt — so kann eine klein komprimierte Datei nicht in eine RAM-Bombe
    dekodieren. Nicht-Bild-Uploads (per Extension) werden uebersprungen; fuer sie
    bleiben Extension-/Magic-/Virus-Checks zustaendig.
    """
    name = uploaded_file.name or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in _IMAGE_EXTENSIONS:
        return

    import warnings  # noqa: PLC0415

    from PIL import Image, UnidentifiedImageError  # noqa: PLC0415 — lazy, wie magic in enforce_magic_bytes

    max_pixels = getattr(django_settings, "FILE_VAULT_MAX_IMAGE_PIXELS", 40_000_000)
    # Globaler Pillow-Guard (schuetzt auch spaetere Dekodier-Pfade), zusaetzlich
    # zur expliziten, versionsunabhaengigen Header-Pruefung unten.
    Image.MAX_IMAGE_PIXELS = max_pixels

    uploaded_file.seek(0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            # Image.open liest nur den Header (lazy, keine Pixel-Dekodierung) →
            # .size ist guenstig und auch fuer eine "Bombe" gefahrlos lesbar.
            with Image.open(uploaded_file) as img:
                pixels = img.size[0] * img.size[1]
    except Image.DecompressionBombError:
        pixels = max_pixels + 1  # harte Pillow-Grenze (>2x MAX) → ablehnen
    except (UnidentifiedImageError, OSError, ValueError):
        # Keine dekodierbare Bilddatei trotz Bild-Extension — hier nicht
        # zusaetzlich abweisen; Magic-Bytes/Extension entscheiden.
        uploaded_file.seek(0)
        return
    finally:
        uploaded_file.seek(0)

    if pixels > max_pixels:
        log_attachment_violation(
            facility,
            user,
            event,
            reason="image_too_large",
            filename=name,
            pixels=pixels,
            max_pixels=max_pixels,
        )
        raise ValidationError(
            _("Bild hat zu viele Pixel (%(px)d). Maximum: %(max)d — möglicher Decompression-Bomb-Upload.")
            % {"px": pixels, "max": max_pixels}
        )


def _zip_entry_count(uploaded_file) -> int | None:
    """Cheap EOCD-only read of a ZIP's "total entries" count (#1310, S4).

    Liest NUR den End-Of-Central-Directory-Record (EOCD) am Datei-Ende — OHNE das
    Central Directory zu parsen — damit ein Archiv mit absurd vielen Eintraegen
    abgelehnt werden kann, BEVOR ``zipfile.ZipFile()``/``infolist()`` pro Eintrag
    ein ``ZipInfo`` materialisiert (~1 Mio Mini-Eintraege => hunderte MB transient
    => OOM auf RAM-limitierten Containern).

    Lokalisiert die EOCD-Signatur ``PK\\x05\\x06`` per ``rfind`` im Tail —
    dieselbe Suche, mit der die stdlib den operativen EOCD findet; die Zaehlung
    ist damit spoof-resistent. Gibt die EOCD-"total entries" zurueck oder
    ``None``, wenn kein EOCD gefunden / die Datei zu kurz ist. ZIP64 setzt dieses
    2-Byte-Feld auf 0xFFFF (65535), was jeden vernuenftigen Cap uebersteigt und
    die Mega-Eintrags-Bombe ebenfalls fasst. Spult den Stream am Ende auf 0.
    """
    try:
        uploaded_file.seek(0, os.SEEK_END)
        filesize = uploaded_file.tell()
        read_len = min(filesize, 22 + 0xFFFF)  # EOCD: 22 Byte Fixteil + bis 64 KB Kommentar
        uploaded_file.seek(filesize - read_len)
        tail = uploaded_file.read(read_len)
    finally:
        uploaded_file.seek(0)

    idx = tail.rfind(b"PK\x05\x06")
    if idx < 0 or len(tail) < idx + 12:
        return None
    # EOCD-Layout: Sig(4) DiskNo(2) CDDisk(2) EntriesHere(2) EntriesTotal(2)@+10 …
    (entries,) = struct.unpack_from("<H", tail, idx + 10)
    return entries


def enforce_archive_limits(facility, uploaded_file, event, user):
    """Reject zip-bomb archives (Refs #1310 (S4)).

    Fuer ZIP-Container (zip + OOXML docx/xlsx/pptx) wird aus dem Zip-Directory
    die Summe der UNkomprimierten Eintrags-Groessen sowie das Expansions-
    Verhaeltnis (unkomprimiert/komprimiert) gelesen — OHNE die Eintraege zu
    entpacken. Eine klein gepackte Datei, die zu hunderten MB expandiert
    (Decompression-Bomb), wird abgelehnt, bevor ein nachgelagerter Konsument sie
    auspackt. Davor lehnt ein billiger EOCD-Vorabcheck Archive mit zu vielen
    Eintraegen ab (``FILE_VAULT_MAX_ARCHIVE_ENTRIES``), damit nicht schon
    ``ZipFile()``/``infolist()`` pro Eintrag ein ``ZipInfo`` allokiert (sonst
    waere dieser Guard selbst ein Memory-DoS). Nicht-Archiv-Uploads (per
    Extension) und kaputte ZIPs werden uebersprungen; fuer sie bleiben
    Extension-/Magic-/Virus-Checks zustaendig.
    Der Stream wird NICHT konsumiert (``seek(0)`` am Ende), damit die nach-
    gelagerte Verschluesselung die Datei noch lesen kann.
    """
    name = uploaded_file.name or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in _ARCHIVE_EXTENSIONS:
        return

    import zipfile  # noqa: PLC0415 — lazy, wie magic/PIL in den anderen Checks

    max_bytes = getattr(django_settings, "FILE_VAULT_MAX_ARCHIVE_BYTES", 200 * 1024 * 1024)
    max_ratio = getattr(django_settings, "FILE_VAULT_MAX_ARCHIVE_RATIO", 100)
    max_entries = getattr(django_settings, "FILE_VAULT_MAX_ARCHIVE_ENTRIES", 10000)

    # Billiger EOCD-Vorabcheck VOR ZipFile(): lehnt eine Mega-Eintrags-Bombe ab,
    # bevor infolist() pro Eintrag ein ZipInfo materialisiert — sonst waere dieser
    # Guard selbst ein Memory-DoS auf RAM-limitierten Hosts. Refs #1310 (S4).
    entries = _zip_entry_count(uploaded_file)
    if entries is not None and entries > max_entries:
        log_attachment_violation(
            facility,
            user,
            event,
            reason="archive_too_many_entries",
            filename=name,
            entries=entries,
            max_entries=max_entries,
        )
        raise ValidationError(
            _("Archiv enthält zu viele Einträge (%(entries)d). Maximum: %(max)d — möglicher Zip-Bomb-Upload.")
            % {"entries": entries, "max": max_entries}
        )

    uploaded_file.seek(0)
    try:
        with zipfile.ZipFile(uploaded_file) as zf:
            total_uncompressed = 0
            total_compressed = 0
            for info in zf.infolist():
                total_uncompressed += info.file_size
                total_compressed += info.compress_size
    except zipfile.BadZipFile:
        # Kein lesbares ZIP trotz Archiv-Extension — hier nicht abweisen;
        # Magic-Bytes/Extension/Virus entscheiden.
        uploaded_file.seek(0)
        return
    finally:
        uploaded_file.seek(0)

    # Expansions-Verhaeltnis nur bei vorhandener komprimierter Groesse (div0-Guard).
    ratio = (total_uncompressed / total_compressed) if total_compressed else 0

    if total_uncompressed > max_bytes or ratio > max_ratio:
        log_attachment_violation(
            facility,
            user,
            event,
            reason="archive_bomb",
            filename=name,
            uncompressed=total_uncompressed,
            compressed=total_compressed,
            ratio=round(ratio, 2),
            max_bytes=max_bytes,
            max_ratio=max_ratio,
        )
        raise ValidationError(
            _("Archiv expandiert zu stark (%(unc)d Bytes, Faktor %(ratio).0f) — möglicher Zip-Bomb-Upload.")
            % {"unc": total_uncompressed, "ratio": ratio}
        )


def enforce_allowed_file_types(facility, uploaded_file, event, user):
    """Reject uploads whose extension is not in ``Settings.allowed_file_types``.

    The form layer already performs this check for UX, but the service layer
    is the final authority — direct/programmatic callers bypass the form, so
    we re-check here and log every violation as ``SECURITY_VIOLATION``
    (Refs #610).

    Refs #771 — fail-closed: fehlt die ``Settings``-Row oder ist
    ``allowed_file_types`` leer/whitespace-only, greift
    :data:`core.constants.DEFAULT_ALLOWED_FILE_TYPES` (statt jeder Datei
    Tor und Tuer zu oeffnen).
    """
    try:
        facility_settings = Settings.objects.get(facility=facility)
    except Settings.DoesNotExist:
        facility_settings = None

    raw = (facility_settings.allowed_file_types or "") if facility_settings else ""
    allowed = {ext.strip().lower().lstrip(".") for ext in raw.split(",") if ext.strip()}
    if not allowed:
        allowed = set(DEFAULT_ALLOWED_FILE_TYPES)

    name = uploaded_file.name or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in allowed:
        return

    log_attachment_violation(
        facility,
        user,
        event,
        reason="extension_not_allowed",
        filename=name,
        extension=ext,
        allowed=sorted(allowed),
    )
    raise ValidationError(
        _("Dateityp .%(ext)s ist nicht erlaubt. Erlaubt: %(allowed)s")
        % {"ext": ext or "?", "allowed": ", ".join(sorted(allowed))}
    )


# MIME-Aequivalenzen pro Extension (#662).
#
# Container-Formate wie OOXML (.docx/.xlsx/.pptx) sind ZIP-Archive; libmagic
# liefert je nach Version und genauer Erkennung uneinheitliche MIME-Typen
# zurueck (mal die offizielle OOXML-MIME, mal "application/zip"). Wenn der
# Browser den offiziellen MIME schickt und libmagic "application/zip"
# antwortet, lehnt eine reine Gleichheitspruefung den Upload ab — obwohl
# beide Seiten dieselbe Datei meinen.
#
# Die Map listet pro Extension die MIME-Werte, die als gleichbedeutend gelten.
# Eine Datei darf hochgeladen werden, wenn detected_mime UND declared_mime
# beide in derselben Aequivalenzklasse liegen.
_MIME_EQUIVALENCE = {
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/x-zip-compressed",
    },
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/x-zip-compressed",
    },
    "pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
        "application/x-zip-compressed",
    },
    # JPEG: Browser sendet manchmal "image/jpg" statt des offiziellen "image/jpeg"
    "jpg": {"image/jpeg", "image/jpg"},
    "jpeg": {"image/jpeg", "image/jpg"},
}


def _mime_equivalent(extension: str, declared: str, detected: str) -> bool:
    """True wenn ``declared`` und ``detected`` in derselben Aequivalenzklasse
    fuer die gegebene ``extension`` liegen (#662)."""
    klass = _MIME_EQUIVALENCE.get(extension.lower().lstrip("."))
    if not klass:
        return False
    return declared in klass and detected in klass


# Positive Magic-Byte-Allowlist (#1310, S3).
#
# Anders als Container-Formate (OOXML/ZIP, siehe _MIME_EQUIVALENCE) haben diese
# Endungen eine UNVERWECHSELBARE Magic-Signatur. Fuer sie genuegt es nicht, dass
# libmagic den Inhalt nicht widerlegt — libmagic MUSS den passenden MIME-Typ
# positiv bestaetigen. Ein generisches "application/octet-stream" ODER ein
# fremder Positiv-Typ zaehlt hier NICHT als Pass (schliesst das frueher
# tolerierte octet-stream-Loch fuer diese Endungen, #1254 §4.2 / S3).
#
# Bewusst konservativ: nur Formate mit zuverlaessig erkannter Signatur. Endungen
# OHNE Eintrag (docx/xlsx/pptx/zip/odt …) behalten das tolerante Verhalten
# (Extension-Whitelist + ClamAV + Archiv-Guard decken sie ab).
_STRONG_MAGIC = {
    "pdf": {"application/pdf"},
    "png": {"image/png"},
    # JPEG: libmagic liefert "image/jpeg"; "image/jpg" ist das Browser-Synonym (#662).
    "jpg": {"image/jpeg", "image/jpg"},
    "jpeg": {"image/jpeg", "image/jpg"},
    "gif": {"image/gif"},
}


def enforce_magic_bytes(facility, uploaded_file, event, user):
    """Verify the file's true MIME type (sniffed via libmagic) matches the
    browser-declared ``content_type``.

    Rejects files whose byte-content disagrees with the declared MIME — a
    common payload-smuggling pattern (e.g. a PE executable sent as
    ``application/pdf``). Every mismatch is logged as ``SECURITY_VIOLATION``
    (Refs #610). Container-Formate wie DOCX werden via
    :data:`_MIME_EQUIVALENCE` toleriert, wenn beide Seiten in derselben
    Aequivalenzklasse liegen (#662).

    Returns the libmagic-detected MIME type (the *verified* type) on success, so
    the caller can persist it and serve it on download instead of the
    browser-reported ``content_type`` (Refs #1274). Falls libmagic den Inhalt
    nicht eindeutig erkennt, ist das ``application/octet-stream`` (generisch,
    erzwingt beim Download den Attachment-Pfad).
    """
    # Lazy-Import: ``python-magic`` braucht die System-Bibliothek ``libmagic1``.
    # Im Docker-Image (Prod) ist sie installiert; lokale Unit-Tests ohne
    # libmagic sollen trotzdem collectable bleiben und gezielt skippen.
    import magic  # noqa: PLC0415 — intentional lazy import, see docstring

    uploaded_file.seek(0)
    buffer = uploaded_file.read(2048)
    uploaded_file.seek(0)

    detected_mime = magic.from_buffer(buffer, mime=True) or "application/octet-stream"
    declared_mime = uploaded_file.content_type or "application/octet-stream"

    name = uploaded_file.name or ""
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    # S3 (#1310): Positive Magic-Byte-Allowlist fuer Endungen mit eindeutiger
    # Signatur. Fuer diese MUSS libmagic einen erlaubten MIME bestaetigen — ein
    # generisches "application/octet-stream" ODER ein fremder Positiv-Typ wird
    # abgelehnt. Laeuft VOR der tolerant-octet-stream-Rueckgabe unten, die damit
    # nur noch fuer mehrdeutige Container-Formate (OOXML/ZIP) gilt.
    allowed_strong = _STRONG_MAGIC.get(extension)
    if allowed_strong is not None and detected_mime not in allowed_strong:
        log_attachment_violation(
            facility,
            user,
            event,
            reason="magic_not_allowlisted",
            filename=uploaded_file.name,
            declared=declared_mime,
            detected=detected_mime,
            allowed=sorted(allowed_strong),
        )
        raise ValidationError(
            _("Datei-Inhalt passt nicht zur Endung .%(ext)s (erkannt: %(detected)s).")
            % {"ext": extension, "detected": detected_mime}
        )

    # libmagic returns "application/octet-stream" when it cannot confidently
    # identify the content (common for small archives, ZIP-like containers, and
    # formats whose magic.mgc definitions are terse). Treat it as "unknown",
    # not as a mismatch — the extension whitelist and ClamAV remain in force.
    # Only reject when libmagic POSITIVELY identifies a type that contradicts
    # the declared one (e.g. PE executable declared as PDF).
    if detected_mime in (declared_mime, "application/octet-stream"):
        return detected_mime

    # Extension-basierte Aequivalenz (DOCX/OOXML, JPEG-Synonyme).
    if _mime_equivalent(extension, declared_mime, detected_mime):
        return detected_mime

    log_attachment_violation(
        facility,
        user,
        event,
        reason="mime_mismatch",
        filename=uploaded_file.name,
        declared=declared_mime,
        detected=detected_mime,
    )
    raise ValidationError(
        _("Datei-Inhalt stimmt nicht mit dem angegebenen Typ überein (erkannt: %(detected)s, erwartet: %(declared)s).")
        % {"detected": detected_mime, "declared": declared_mime}
    )


def run_virus_scan(facility, uploaded_file, event, user):
    """Scan ``uploaded_file`` via ClamAV. Raises ``ValidationError`` on hit or
    when the scanner is unreachable while ``CLAMAV_ENABLED`` is true.

    Creates an ``AuditLog`` entry with ``Action.SECURITY_VIOLATION`` for every
    detected infection and also for fail-closed scanner outages — both are
    security-relevant events that operators need to see.
    """
    try:
        result = scan_file(uploaded_file)
    except VirusScannerUnavailableError as exc:
        logger.error(
            "Virenscanner nicht erreichbar — Upload wird abgewiesen (fail-closed): %s",
            exc,
        )
        log_attachment_violation(
            facility,
            user,
            event,
            reason="virus_scanner_unavailable",
            filename=uploaded_file.name,
            error=str(exc),
        )
        raise ValidationError(_("Datei-Upload abgelehnt: Virenscanner ist nicht erreichbar.")) from exc

    if result.infected:
        log_attachment_violation(
            facility,
            user,
            event,
            reason="virus_detected",
            filename=uploaded_file.name,
            signature=result.signature or "",
        )
        raise ValidationError(
            _("Datei wurde von Virenscanner abgewiesen: %(signature)s") % {"signature": result.signature or "unknown"}
        )
