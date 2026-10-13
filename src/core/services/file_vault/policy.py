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

AP2 (Refs #1738 / ADR-039): Der dritte Parameter aller Enforcer heisst
``owner`` statt ``event`` — er ist ein ``Event``, ``Client`` oder ``Case``
(oder ``None``, wenn der Owner beim Pre-Save-Reject noch nicht existiert) und
wird ausschliesslich an die Audit-Attribution durchgereicht. Keine
Enforcement-Regel liest den Owner selbst.
"""

from __future__ import annotations

import io
import logging
import os
import struct
from typing import Any

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.constants import DEFAULT_ALLOWED_FILE_TYPES, DEFAULT_MAX_FILE_SIZE_MB
from core.models.settings import Settings
from core.services.file_vault.audit import log_attachment_violation
from core.services.file_vault.virus_scan import VirusScannerUnavailableError, scan_file

logger = logging.getLogger(__name__)

# Bild-Extensions, fuer die der Decompression-Bomb-Pixelcheck greift (#1268).
_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff"})

# Bild-MIME-Typen, die libmagic fuer diese Endungsfamilie liefert (#2128,
# R-L7a-6). Sie binden die Inhalts- an die Endungsfamilie: ein Bild MUSS unter
# einer Bild-Endung hochgeladen werden. Ohne diese Bindung genuegte es, ein
# JPEG ``foto.docx`` zu nennen und ``image/jpeg`` korrekt zu deklarieren —
# ``enforce_magic_bytes`` sah "erkannt == deklariert" und liess es durch, und
# jede endungsbasierte Folgestufe (Pixel-Cap, Metadaten-Strip) sah kein Bild.
# Bewusst eine feste Liste statt ``startswith("image/")``: sie soll nur
# Formate abweisen, deren Endungsfamilie hier tatsaechlich bekannt ist.
_IMAGE_MIMES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/tiff",
        "image/bmp",
        "image/x-ms-bmp",
    }
)


def _is_image_mime(mime) -> bool:
    """Sieht der per Magic-Bytes erkannte Typ nach Bild aus? (#2128)

    Bewusst breiter als :data:`_IMAGE_MIMES`: fuer den *Pixel-Cap* zaehlt jeder
    ``image/*``-Typ, auch ein hier nicht namentlich gefuehrter (``image/avif``,
    ``image/heic``). Ein Dekodier-Guard darf nicht daran scheitern, dass eine
    Formatliste nicht nachgezogen wurde.
    """
    return (mime or "").lower().startswith("image/")


# ZIP-Family-Extensions, fuer die der Archiv-Expansions-Guard greift (#1310, S4).
# OOXML (docx/xlsx/pptx) sind ZIP-Container — derselbe Decompression-Bomb-Vektor.
_ARCHIVE_EXTENSIONS = frozenset({"zip", "docx", "xlsx", "pptx"})


def _extension(uploaded_file) -> str:
    """Dateiendung (lowercased, ohne Punkt) aus dem Upload-Namen — leer, wenn keine vorhanden.

    Refs #1827: 4x identisch dupliziert (``enforce_image_limits``,
    ``enforce_archive_limits``, ``enforce_allowed_file_types``,
    ``enforce_magic_bytes``). Der ``or ""``-Fallback bleibt erhalten —
    ``uploaded_file.name`` kann leer sein.
    """
    name = uploaded_file.name or ""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _reject(facility, user, owner, *, reason: str, message, cause: Exception | None = None, **detail):
    """Loggt eine ``SECURITY_VIOLATION`` und wirft die zugehoerige ``ValidationError``.

    Refs #1827: 9 Aufrufstellen wiederholten denselben
    ``log_attachment_violation(...)`` + ``raise ValidationError(...)``-Zweizeiler
    identisch — nur ``reason``, die Detail-Felder und die (bereits
    interpolierte) Meldung unterschieden sich. Die %-Interpolation bleibt an
    der Aufrufstelle, weil ihre Platzhalter/Werte je Regel verschieden sind.
    ``cause`` erhaelt die ``raise ... from cause``-Verkettung fuer den einzigen
    Aufrufer, der aus einem ``except``-Block heraus ablehnt
    (``run_virus_scan``/``virus_scanner_unavailable``); alle anderen lassen es
    weg (kein Verhaltenswechsel gegenueber dem bisherigen ``raise ValidationError(...)``
    ohne ``from``).
    """
    log_attachment_violation(facility, user, owner, reason=reason, **detail)
    if cause is not None:
        raise ValidationError(message) from cause
    raise ValidationError(message)


def enforce_upload_size(facility, uploaded_file, owner, user):
    """Reject uploads exceeding ``min(Facility-Limit, globaler Cap)``.

    Refs #1268 (T3): urspruenglich nur die harte globale Obergrenze
    (``FILE_VAULT_MAX_UPLOAD_BYTES``) als letzte Bastion fuer programmatische
    Aufrufer, die den Formular-Layer (``Settings.max_file_size_mb``) umgehen.

    Refs #1363 (N10): Das reichte nicht — ``DynamicEventDataForm.clean()``
    prueft das (typischerweise engere) Facility-Limit nur fuer regulaere
    Formularfelder. Replace-Uploads (``<slug>__replace__<id>``,
    ``services/events/crud.py:_apply_replace``) laufen direkt an
    ``store_encrypted_file`` vorbei am Formular und wurden bis zur globalen
    50-MB-Grenze durchgelassen. Diese Funktion ist die SSOT fuer JEDEN
    Aufrufer: sie zieht ``Settings.max_file_size_mb`` selbst (gleiches
    fail-closed-Muster wie ``enforce_allowed_file_types`` — fehlende Row
    faellt auf :data:`core.constants.DEFAULT_MAX_FILE_SIZE_MB` zurueck) und
    erzwingt das striktere der beiden Limits.

    Greift VOR jeder Voll-Pufferung (Virenscan/Encrypt) — so wird eine
    Riesendatei nicht erst in den RAM gelesen (authentifizierter
    Memory-Exhaustion-DoS). Jeder Verstoss wird als ``SECURITY_VIOLATION``
    protokolliert (Form-Bypass ist verdaechtig).
    """
    global_cap_bytes = getattr(django_settings, "FILE_VAULT_MAX_UPLOAD_BYTES", 50 * 1024 * 1024)

    try:
        facility_settings = Settings.objects.get(facility=facility)
    except Settings.DoesNotExist:
        facility_settings = None
    facility_max_mb = facility_settings.max_file_size_mb if facility_settings else DEFAULT_MAX_FILE_SIZE_MB
    facility_cap_bytes = facility_max_mb * 1024 * 1024

    max_bytes = min(global_cap_bytes, facility_cap_bytes)
    size = getattr(uploaded_file, "size", None)
    if size is None or size <= max_bytes:
        return

    _reject(
        facility,
        user,
        owner,
        reason="file_too_large",
        message=_("Datei zu groß (%(mb)d MB). Maximum: %(max)d MB.")
        % {"mb": size // (1024 * 1024), "max": max_bytes // (1024 * 1024)},
        filename=uploaded_file.name,
        size=size,
        max_bytes=max_bytes,
    )


# Bound retained frame copies and reserve space for decoding/transform buffers.
# This estimate is not a bound on Pillow's format-specific encoder allocations.
_MAX_IMAGE_FRAMES = 100
_MAX_IMAGE_DECODED_BYTES = 256 * 1024 * 1024
_IMAGE_BUFFER_RESERVE = 3


def _gif_frame_sizes(stream):
    """Read GIF canvas extents without Pillow's decoding seek operation.

    Later descriptors can exceed the logical screen size. Count their expanded
    canvases, including offsets, rather than trusting the first-frame header.
    The caller stops consuming headers as soon as any budget is exceeded.
    """
    stream.seek(0)
    header = stream.read(13)
    if len(header) != 13 or header[:6] not in {b"GIF87a", b"GIF89a"}:
        raise ValueError("Invalid GIF header")
    width, height = struct.unpack_from("<HH", header, 6)
    if header[10] & 0x80:
        stream.seek(3 * (2 ** ((header[10] & 7) + 1)), os.SEEK_CUR)

    def skip_subblocks():
        while True:
            size = stream.read(1)
            if not size:
                raise ValueError("Truncated GIF subblock")
            if size == b"\x00":
                return
            stream.seek(size[0], os.SEEK_CUR)

    while True:
        block = stream.read(1)
        if block == b";":
            return
        if block == b"!":
            if not stream.read(1):
                raise ValueError("Truncated GIF extension")
            skip_subblocks()
        elif block == b",":
            descriptor = stream.read(9)
            if len(descriptor) != 9:
                raise ValueError("Truncated GIF frame descriptor")
            left, top, frame_width, frame_height = struct.unpack_from("<HHHH", descriptor)
            width, height = max(width, left + frame_width), max(height, top + frame_height)
            yield width, height
            if descriptor[8] & 0x80:
                stream.seek(3 * (2 ** ((descriptor[8] & 7) + 1)), os.SEEK_CUR)
            if not stream.read(1):
                raise ValueError("Missing GIF LZW code size")
            skip_subblocks()
        else:
            raise ValueError("Invalid GIF block")


def _webp_frame_sizes(stream):
    """Read RIFF geometry and actual frame chunks before libwebp allocates canvases."""
    stream.seek(0, os.SEEK_END)
    file_size = stream.tell()
    stream.seek(0)
    header = stream.read(12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WEBP":
        raise ValueError("Invalid WebP header")
    end = 8 + int.from_bytes(header[4:8], "little")
    if end > file_size or end < 12:
        raise ValueError("Truncated WebP container")
    canvas = None
    animated = animation_header = False
    frames = 0
    offset = 12
    for _chunk_index in range(_MAX_IMAGE_FRAMES + _WEBP_MAX_CHUNKS):
        if offset == end:
            if not frames:
                raise ValueError("Missing WebP image data")
            return
        stream.seek(offset)
        chunk = stream.read(8)
        if len(chunk) != 8:
            raise ValueError("Truncated WebP chunk")
        kind, size = chunk[:4], int.from_bytes(chunk[4:], "little")
        next_offset = offset + 8 + size + size % 2
        if next_offset > end:
            raise ValueError("WebP chunk exceeds container")
        if kind == b"VP8X":
            if size != 10 or canvas is not None or offset != 12:
                raise ValueError("Invalid extended WebP header")
            data = stream.read(10)
            animated = bool(data[0] & 2)
            canvas = (1 + int.from_bytes(data[4:7], "little"), 1 + int.from_bytes(data[7:10], "little"))
        elif kind == b"ANIM":
            if not animated or size != 6 or animation_header:
                raise ValueError("Invalid WebP animation header")
            animation_header = True
        elif kind == b"ANMF":
            if not canvas or not animation_header or size < 16:
                raise ValueError("Invalid WebP animation frame")
            data = stream.read(16)
            left, top = 2 * int.from_bytes(data[:3], "little"), 2 * int.from_bytes(data[3:6], "little")
            width, height = 1 + int.from_bytes(data[6:9], "little"), 1 + int.from_bytes(data[9:12], "little")
            if left + width > canvas[0] or top + height > canvas[1]:
                raise ValueError("WebP frame exceeds canvas")
            frames += 1
            yield canvas
        elif kind in {b"VP8 ", b"VP8L"}:
            if animated or frames:
                raise ValueError("Unexpected WebP image data")
            data = stream.read(min(size, 10))
            if kind == b"VP8L":
                if len(data) < 5 or data[0] != 0x2F:
                    raise ValueError("Invalid lossless WebP header")
                bits = int.from_bytes(data[1:5], "little")
                width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            else:
                if len(data) != 10 or data[3:6] != b"\x9d\x01\x2a":
                    raise ValueError("Invalid lossy WebP header")
                width, height = struct.unpack_from("<HH", data, 6)
                width, height = width & 0x3FFF, height & 0x3FFF
                if not width or not height:
                    raise ValueError("Invalid WebP dimensions")
            if canvas is not None and canvas != (width, height):
                raise ValueError("WebP dimensions disagree with canvas")
            frames = 1
            yield width, height
        offset = next_offset
    raise ValueError("Too many WebP header chunks")


def _png_image_size(stream):
    """Read PNG/APNG geometry before opening can allocate a disposal buffer."""
    start = stream.tell()
    stream.seek(0, os.SEEK_END)
    end = stream.tell()
    stream.seek(start)
    header = stream.read(33)
    if len(header) != 33 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR":
        raise ValueError("Invalid PNG header")
    width, height = struct.unpack_from(">II", header, 16)
    if not width or not height:
        raise ValueError("Invalid PNG dimensions")
    frames = 1
    animated = animation_frame = False
    while stream.tell() < end:
        chunk = stream.read(8)
        if len(chunk) != 8:
            break
        size, kind = int.from_bytes(chunk[:4], "big"), chunk[4:]
        next_offset = stream.tell() + size + 4
        if next_offset > end:
            raise ValueError("Truncated PNG chunk")
        if kind == b"IHDR":
            raise ValueError("Conflicting PNG canvas headers")
        if kind == b"IDAT":
            return width, height, frames + int(animated and not animation_frame)
        if kind == b"acTL":
            if animated or size != 8:
                raise ValueError("Invalid APNG animation header")
            animated = True
            frames = int.from_bytes(stream.read(4), "big")
            if not frames:
                raise ValueError("Missing APNG frames")
        elif kind == b"fcTL":
            animation_frame = True
        stream.seek(next_offset)
    raise ValueError("Missing PNG image data")


def _ico_frame_sizes(stream):
    """Read embedded raster headers before Pillow eagerly loads an ICO entry.

    Directory dimensions are untrusted: PNG/DIB payloads may disagree with them.
    Check each variant separately; Pillow only materializes one variant.
    """
    stream.seek(0)
    header = stream.read(6)
    if len(header) != 6 or header[:4] != b"\x00\x00\x01\x00":
        raise ValueError("Invalid ICO header")
    count = int.from_bytes(header[4:], "little")
    if not count:
        raise ValueError("Missing ICO entries")
    for index in range(count):
        stream.seek(6 + index * 16)
        entry = stream.read(16)
        if len(entry) != 16:
            raise ValueError("Truncated ICO directory")
        stream.seek(int.from_bytes(entry[12:16], "little"))
        payload = stream.read(12)
        if payload[:8] == b"\x89PNG\r\n\x1a\n":
            stream.seek(-len(payload), os.SEEK_CUR)
            width, height, _frame_count = _png_image_size(stream)
        elif len(payload) == 12:
            header_size = int.from_bytes(payload[:4], "little")
            if header_size == 12:
                width, height = struct.unpack_from("<HH", payload, 4)
            elif header_size in {40, 64, 108, 124}:
                width, height = struct.unpack_from("<ii", payload, 4)
                height = abs(height)
            else:
                raise ValueError("Unsupported ICO bitmap header")
            height //= 2
        else:
            raise ValueError("Truncated ICO raster header")
        if width <= 0 or height <= 0:
            raise ValueError("Invalid ICO raster dimensions")
        yield width, height


def _check_image_budget(facility, uploaded_file, owner, user, *, pixels, frames, decoded_bytes, max_pixels):
    """Reject before frame copies can exhaust the aggregate image budget."""
    if frames > _MAX_IMAGE_FRAMES:
        _reject(
            facility,
            user,
            owner,
            reason="image_too_many_frames",
            message=_("Bild enthält zu viele Frames oder Seiten. Maximum: %(max)d.") % {"max": _MAX_IMAGE_FRAMES},
            filename=uploaded_file.name,
        )
    if pixels > max_pixels:
        _reject(
            facility,
            user,
            owner,
            reason="image_too_large",
            message=_("Bild hat insgesamt zu viele Pixel (%(px)d). Maximum über alle Frames und Seiten: %(max)d.")
            % {"px": pixels, "max": max_pixels},
            filename=uploaded_file.name,
            pixels=pixels,
            max_pixels=max_pixels,
        )
    if decoded_bytes > _MAX_IMAGE_DECODED_BYTES:
        _reject(
            facility,
            user,
            owner,
            reason="image_memory_budget",
            message=_("Bild überschreitet das Speicherbudget für alle Frames und Seiten — Upload abgelehnt."),
            filename=uploaded_file.name,
        )


def enforce_image_limits(facility, uploaded_file, owner, user, detected_mime=None):
    """Check aggregate pixels, frame count and estimated buffers before decoding.

    TIFF pages may have different sizes/modes; seeking reads their headers only.
    Animations reserve a full canvas per frame, including compositing and mode
    promotion to RGBA. Their frame count is read without decoding pixel data.
    WebP, PNG/APNG, GIF and ICO headers are checked before Image.open(),
    which can already allocate canvas/disposal buffers or decode an icon.
    The byte estimate reserves three buffers of at least four bytes per pixel:
    source, retained clean copy and transform/encoding workspace. Encoder
    allocations are format-dependent and are not proven bounded by this estimate.
    """
    ext = _extension(uploaded_file)
    if ext not in _IMAGE_EXTENSIONS and not _is_image_mime(detected_mime):
        return
    if ext not in _IMAGE_EXTENSIONS and (detected_mime or "").lower() == "image/svg+xml":
        # SVG is passed through without rasterizing; the extension allowlist still applies.
        return

    import warnings  # noqa: PLC0415

    from PIL import Image, UnidentifiedImageError  # noqa: PLC0415

    max_pixels = getattr(django_settings, "FILE_VAULT_MAX_IMAGE_PIXELS", 40_000_000)
    Image.MAX_IMAGE_PIXELS = max_pixels
    uploaded_file.seek(0)
    try:
        prefix = uploaded_file.read(12)
        uploaded_file.seek(0)
        sizes = None
        icon = prefix[:4] == b"\x00\x00\x01\x00"
        if prefix[:6] in {b"GIF87a", b"GIF89a"}:
            sizes = ((width, height, 1) for width, height in _gif_frame_sizes(uploaded_file))
        elif prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
            sizes = ((width, height, 1) for width, height in _webp_frame_sizes(uploaded_file))
        elif prefix[:8] == b"\x89PNG\r\n\x1a\n":
            sizes = [_png_image_size(uploaded_file)]
        elif icon:
            sizes = ((width, height, 1) for width, height in _ico_frame_sizes(uploaded_file))
        preflighted = sizes is not None
        if sizes is not None:
            pixels = frames = 0
            for width, height, count in sizes:
                frame_pixels = width * height * count
                pixels = max(pixels, frame_pixels) if icon else pixels + frame_pixels
                frames = 1 if icon else frames + count
                _check_image_budget(
                    facility,
                    uploaded_file,
                    owner,
                    user,
                    pixels=pixels,
                    frames=frames,
                    decoded_bytes=pixels * 4 * _IMAGE_BUFFER_RESERVE,
                    max_pixels=max_pixels,
                )
        uploaded_file.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(uploaded_file) as img:
                if preflighted:
                    return
                pixels = decoded_bytes = 0
                if img.format == "TIFF":
                    # Avoid n_frames: stop after the first over-budget page header.
                    for index in range(_MAX_IMAGE_FRAMES + 1):
                        try:
                            img.seek(index)
                        except EOFError:
                            break
                        page_pixels = img.width * img.height
                        pixels += page_pixels
                        decoded_bytes += page_pixels * max(4, len(img.getbands())) * _IMAGE_BUFFER_RESERVE
                        _check_image_budget(
                            facility,
                            uploaded_file,
                            owner,
                            user,
                            pixels=pixels,
                            frames=index + 1,
                            decoded_bytes=decoded_bytes,
                            max_pixels=max_pixels,
                        )
                else:
                    frames = getattr(img, "n_frames", 1)
                    pixels = img.width * img.height * frames
                    decoded_bytes = pixels * max(4, len(img.getbands())) * _IMAGE_BUFFER_RESERVE
                    _check_image_budget(
                        facility,
                        uploaded_file,
                        owner,
                        user,
                        pixels=pixels,
                        frames=frames,
                        decoded_bytes=decoded_bytes,
                        max_pixels=max_pixels,
                    )
    except Image.DecompressionBombError:
        _check_image_budget(
            facility,
            uploaded_file,
            owner,
            user,
            pixels=max_pixels + 1,
            frames=1,
            decoded_bytes=0,
            max_pixels=max_pixels,
        )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        _reject(
            facility,
            user,
            owner,
            reason="image_undecodable",
            message=_("Bilddatei konnte nicht gelesen werden — Upload abgelehnt."),
            cause=exc,
            filename=uploaded_file.name,
            extension=ext,
        )
    finally:
        uploaded_file.seek(0)


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


def enforce_archive_limits(facility, uploaded_file, owner, user):
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
    ext = _extension(uploaded_file)
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
        _reject(
            facility,
            user,
            owner,
            reason="archive_too_many_entries",
            message=_("Archiv enthält zu viele Einträge (%(entries)d). Maximum: %(max)d — möglicher Zip-Bomb-Upload.")
            % {"entries": entries, "max": max_entries},
            filename=name,
            entries=entries,
            max_entries=max_entries,
        )

    uploaded_file.seek(0)
    try:
        with zipfile.ZipFile(uploaded_file) as zf:
            total_uncompressed = 0
            total_compressed = 0
            for info in zf.infolist():
                total_uncompressed += info.file_size
                total_compressed += info.compress_size
    except (zipfile.BadZipFile, OSError, EOFError, ValueError, struct.error):
        # Kein lesbares ZIP trotz Archiv-Extension — BadZipFile ODER andere
        # Parser-Fehler auf pathologischem Input (graceful skip statt 500); hier
        # nicht abweisen, Magic-Bytes/Extension/Virus entscheiden.
        uploaded_file.seek(0)
        return
    finally:
        uploaded_file.seek(0)

    # Expansions-Verhaeltnis nur bei vorhandener komprimierter Groesse (div0-Guard).
    ratio = (total_uncompressed / total_compressed) if total_compressed else 0

    if total_uncompressed > max_bytes or ratio > max_ratio:
        _reject(
            facility,
            user,
            owner,
            reason="archive_bomb",
            message=_("Archiv expandiert zu stark (%(unc)d Bytes, Faktor %(ratio).0f) — möglicher Zip-Bomb-Upload.")
            % {"unc": total_uncompressed, "ratio": ratio},
            filename=name,
            uncompressed=total_uncompressed,
            compressed=total_compressed,
            ratio=round(ratio, 2),
            max_bytes=max_bytes,
            max_ratio=max_ratio,
        )


def enforce_allowed_file_types(facility, uploaded_file, owner, user):
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
    ext = _extension(uploaded_file)
    if ext in allowed:
        return

    _reject(
        facility,
        user,
        owner,
        reason="extension_not_allowed",
        message=_("Dateityp .%(ext)s ist nicht erlaubt. Erlaubt: %(allowed)s")
        % {"ext": ext or "?", "allowed": ", ".join(sorted(allowed))},
        filename=name,
        extension=ext,
        allowed=sorted(allowed),
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


def enforce_magic_bytes(facility, uploaded_file, owner, user):
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

    extension = _extension(uploaded_file)

    # S3 (#1310): Positive Magic-Byte-Allowlist fuer Endungen mit eindeutiger
    # Signatur. Fuer diese MUSS libmagic einen erlaubten MIME bestaetigen — ein
    # generisches "application/octet-stream" ODER ein fremder Positiv-Typ wird
    # abgelehnt. Laeuft VOR der tolerant-octet-stream-Rueckgabe unten, die damit
    # nur noch fuer mehrdeutige Container-Formate (OOXML/ZIP) gilt.
    allowed_strong = _STRONG_MAGIC.get(extension)
    if allowed_strong is not None and detected_mime not in allowed_strong:
        _reject(
            facility,
            user,
            owner,
            reason="magic_not_allowlisted",
            message=_("Datei-Inhalt passt nicht zur Endung .%(ext)s (erkannt: %(detected)s).")
            % {"ext": extension, "detected": detected_mime},
            filename=uploaded_file.name,
            declared=declared_mime,
            detected=detected_mime,
            allowed=sorted(allowed_strong),
        )

    # #2128 (R-L7a-6): Inhalts- an Endungsfamilie binden.
    # Der Gleichheits-Zweig unten laesst jede Datei durch, deren erkannter Typ
    # dem deklarierten entspricht — ohne den Inhalt je an die Endung zu binden.
    # Ein JPEG mit korrekt deklariertem ``image/jpeg`` unter der per Default
    # erlaubten Endung ``.docx``/``.odt`` kam so unverandert durch: der
    # Archiv-Guard uebersprang es (kein ZIP), der Pixel-Cap und die
    # Metadaten-Bereinigung entschieden endungsbasiert und sahen kein Bild.
    # Ergebnis waeren EXIF/GPS im Vault UND ein umgangener Decompression-Bomb-
    # Schutz gewesen. Fail-closed: erkanntes Bild verlangt eine Bild-Endung.
    if detected_mime in _IMAGE_MIMES and extension not in _IMAGE_EXTENSIONS:
        _reject(
            facility,
            user,
            owner,
            reason="mime_mismatch",
            message=_("Datei-Inhalt ist ein Bild (%(detected)s), die Endung .%(ext)s ist keine Bild-Endung.")
            % {"detected": detected_mime, "ext": extension or "?"},
            filename=uploaded_file.name,
            declared=declared_mime,
            detected=detected_mime,
            extension=extension,
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

    _reject(
        facility,
        user,
        owner,
        reason="mime_mismatch",
        message=_(
            "Datei-Inhalt stimmt nicht mit dem angegebenen Typ überein (erkannt: %(detected)s, erwartet: %(declared)s)."
        )
        % {"detected": detected_mime, "declared": declared_mime},
        filename=uploaded_file.name,
        declared=declared_mime,
        detected=detected_mime,
    )


def enforce_storage_quota(
    facility,
    uploaded_file,
    owner,
    user,
    *,
    pending_bytes: int = 0,
    target_type: str = "Attachment",
):
    """Reject uploads that would exceed the facility's storage quota (Refs #2129).

    Befund N-5: Die Groessenbegrenzung galt bis hierher ausschliesslich **pro
    Datei**. Im Mehr-Mandanten-Betrieb konnte damit eine Einrichtung mit lauter
    legitimen Uploads den gemeinsamen ``MEDIA_ROOT`` fuellen — die Folge
    (Upload-Fehler, Backup-Fehler) traf **alle** Mandanten. Der globale
    Disk-Check in ``/health/`` meldet das, verhindert es nicht und kennt keine
    Einrichtung.

    Wirksames Limit, Verbrauch und Papierkorb-Anteil kommen aus
    :func:`~core.services.file_vault.quota.facility_storage_usage` — derselbe
    Rechner, den die Kacheln im Verwaltungs-Hub und in der
    Leitungs-Arbeitszentrale anzeigen. Der Grenzwert ist **inklusiv**: genau
    aufgehen darf noch.

    Reihenfolge (bindend): laeuft NACH ``enforce_upload_size`` und VOR
    ``run_virus_scan`` — fuer einen Upload, der ohnehin abgelehnt wird, soll
    kein ClamAV-Scan (bis ``CLAMAV_TIMEOUT`` synchron) anfallen. Gerechnet wird
    deshalb mit der Groesse VOR der Metadaten-Bereinigung (#2128); gespeichert
    wird anschliessend die bereinigte, in aller Regel kleinere Datei. Die
    Abweichung geht bewusst in die sichere Richtung — sie kann ein Kontingent
    nur zu frueh, nie zu spaet greifen lassen.

    Bewusst **kein** ``SECURITY_VIOLATION`` und damit nicht ueber
    :func:`_reject`: ein erschoepftes Kontingent ist ein normaler
    Betriebszustand, kein Angriff. Die Rechenschaftsspur ist eine eigene
    ``ATTACHMENT_QUOTA_EXCEEDED``-Zeile.

    ``pending_bytes`` (Refs #2129, R-L7a-5) traegt die Summe der
    Dateien, die im SELBEN Request bereits gestagt, aber noch nicht als
    ``Attachment``-Zeile persistiert sind. Ohne diesen Offset sah jede Datei
    eines Formular-Submits denselben alten DB-Verbrauch: ein einzelner Submit
    mit bis zu ``FILE_VAULT_MAX_UPLOAD_FILES`` Dateien konnte das Kontingent
    deterministisch und wiederholbar reissen. Das ist ausdruecklich NICHT der
    unten tolerierte Renn-Fall — hier gibt es kein Rennen, nur eine Schleife.

    ``target_type`` aendert ausschliesslich die Zielklassifikation der
    Audit-Zeile. Der Default ``Attachment`` haelt alle bestehenden Aufrufer
    unveraendert; der CSV-Import setzt ``ImportJob``.

    **Diese Stufe entscheidet nicht endgueltig** (Refs #2178): die Summe wird
    hier bewusst ohne Lock gelesen — zwischen dem Lesen und der spaeteren
    Attachment-Zeile liegt die gesamte teure Pipeline, und die soll gerade
    NICHT je Einrichtung serialisiert werden (Entwurfsziel #1345: Scan/Encrypt
    laufen nicht unter dem Facility-Lock der AuditLog-Hashkette). Ihr Zweck ist,
    Arbeit zu sparen. Dass gleichzeitige Uploads das Kontingent gemeinsam
    ueberschreiten, verhindert der Backstop im Commit-Pfad
    (:func:`check_storage_quota_at_commit`, aufgerufen aus
    ``storage._commit_staged_upload``) — dort ist der Vorgang ein kurzer
    DB-Write, den eine Sperre nichts kostet.
    """
    from core.services.file_vault.audit import audit_attachment_quota_exceeded  # noqa: PLC0415 — Zyklus vermeiden
    from core.services.file_vault.quota import facility_storage_usage  # noqa: PLC0415

    size = (getattr(uploaded_file, "size", None) or 0) + max(pending_bytes, 0)
    usage = facility_storage_usage(facility)
    if usage.fits(size):
        return

    audit_attachment_quota_exceeded(
        facility,
        user,
        owner,
        usage=usage,
        requested_bytes=size,
        target_type=target_type,
    )
    raise ValidationError(storage_quota_message(usage))


def storage_quota_message(usage) -> str:
    """Der Meldungstext fuer ein erschoepftes Kontingent — EINE Quelle.

    Vorab-Check und Commit-Backstop (Refs #2178) muessen nach aussen
    ununterscheidbar sein: Views und Formulare reagieren auf Fehlerklasse und
    Text, nicht darauf, an welcher Stelle der Pipeline das Kontingent gerissen
    ist. Zwei getrennte Formulierungen waeren zwei msgids und zwei Gelegenheiten
    auseinanderzudriften.
    """
    return _(
        "Speicherkontingent der Einrichtung erschöpft: %(used)d von %(limit)d MB belegt "
        "(davon %(trash)d MB im Papierkorb)."
    ) % {"used": usage.used_mb, "limit": usage.limit_mb, "trash": usage.trash_mb}


class StorageQuotaBackstopError(ValidationError):
    """Der Backstop hat angeschlagen — traegt die Nutzdaten der Audit-Zeile mit.

    **Ein ``ValidationError``**, damit Views und Formulare ohne Sonderbehandlung
    reagieren: Klasse, ``messages`` und Text sind die des Vorab-Checks
    (:func:`storage_quota_message`). Wer die Ausnahme nur durchreicht, bekommt
    exakt das bisherige Nutzerverhalten.

    Zusaetzlich traegt sie alles, was die ``ATTACHMENT_QUOTA_EXCEEDED``-Zeile
    braucht. Das ist noetig, weil die Zeile NICHT dort geschrieben werden darf,
    wo der Fehler entsteht: sie entstuende in der Transaktion, die gleich
    zurueckrollt, und verschwaende mit ihr. Geschrieben wird sie am **aeussersten
    Transaktionsrand** — ohne Aufrufer-Transaktion direkt in
    ``storage.commit_staged_upload``, sonst von
    :func:`~core.services.attachments.crud.quota_backstop_audit_boundary`
    (Refs #2178).

    :meth:`write_audit` ist idempotent: es schreibt genau einmal, egal wie viele
    Raender die Ausnahme passiert.

    ``target_type`` klassifiziert allein die Zielangabe der Audit-Zeile — wie
    beim Vorab-Check (:func:`enforce_storage_quota`). Der Default ``Attachment``
    haelt die Anhangspfade unveraendert; der CSV-Import setzt ``ImportJob``
    (Refs #2259).
    """

    def __init__(self, *, facility, user, owner, usage, requested_bytes: int, target_type: str = "Attachment"):
        super().__init__(storage_quota_message(usage))
        self.facility = facility
        self.user = user
        self.owner = owner
        self.usage = usage
        self.requested_bytes = requested_bytes
        self.target_type = target_type
        self.audited = False

    def write_audit(self) -> None:
        """Schreibt die Rechenschaftszeile — nur beim ersten Aufruf.

        MUSS ausserhalb jeder Transaktion laufen, die wegen dieser Ausnahme
        zurueckrollt; sonst ist die Zeile mit dem Rollback wieder weg.
        """
        if self.audited:
            return
        from core.services.file_vault.audit import audit_attachment_quota_exceeded  # noqa: PLC0415 — Zyklus vermeiden

        audit_attachment_quota_exceeded(
            self.facility,
            self.user,
            self.owner,
            usage=self.usage,
            requested_bytes=self.requested_bytes,
            target_type=self.target_type,
        )
        self.audited = True


def check_storage_quota_at_commit(owner, user, additional_bytes: int) -> None:
    """Der fail-closed Backstop unmittelbar vor der Attachment-Zeile (#2178).

    Laeuft in der kurzen Commit-Transaktion, nimmt dort die Sperre der
    Einrichtung und liest die Summe erneut. Erst diese Stufe schliesst das
    Rennen: der Vorab-Check in ``prepare_encrypted_upload`` liegt ausserhalb
    jeder Transaktion, beliebig viele gleichzeitige Uploads sehen dort
    denselben alten Stand und ueberziehen das Kontingent gemeinsam (Red-Team
    B-13, dynamisch mit zwoelf parallelen Uploads belegt).

    **Vor** dem Insert, nicht danach: ein blosses Nachrechnen nach dem Insert
    haette dasselbe Rennen, denn unter READ COMMITTED sieht jede Transaktion
    ihre eigene unbestaetigte Zeile, aber keine fremde. Entscheidend ist nicht
    der Zeitpunkt der Pruefung, sondern dass „Summe lesen + Zeile schreiben"
    je Einrichtung serialisiert ist. Vor dem Insert zu pruefen spart zusaetzlich
    den Rollback einer bereits geschriebenen Zeile.

    ``owner``/``user`` gehen nur in die Nutzdaten der Ausnahme (Audit-Attribution);
    die ``facility`` kommt IMMER aus ``owner.facility`` (ADR-039 §2).

    Raises:
        StorageQuotaBackstopError: wenn die Datei nicht mehr hineinpasst.
    """
    from core.services.file_vault.quota import facility_storage_overrun  # noqa: PLC0415 — Zyklus vermeiden

    facility = owner.facility
    usage = facility_storage_overrun(facility, additional_bytes)
    if usage is not None:
        raise StorageQuotaBackstopError(
            facility=facility,
            user=user,
            owner=owner,
            usage=usage,
            requested_bytes=additional_bytes,
        )


def check_import_storage_quota_at_commit(facility, user, additional_bytes: int) -> None:
    """Derselbe Backstop fuer die CSV-Quelldatei eines Importlaufs (Refs #2259).

    Der Importpfad speichert seinen ``ImportJob`` nicht ueber
    ``storage.commit_staged_upload``, sondern selbst — er braucht deshalb einen
    eigenen Einstieg, aber **keinen eigenen Mechanismus**: dieselbe Sperre
    (ein einziger Advisory-Key je Einrichtung, siehe ``quota.storage_lock_name``),
    dieselbe vollstaendige Summe (``quota._enforcement_totals``: Anhaenge plus
    offene Importdateien) und dieselbe Ausnahme mit denselben Nutzdaten. Ein
    zweiter Lock-Namensraum brauchte eine Lock-Reihenfolge, die die
    Aufrufer-Transaktionen nicht einhalten koennen.

    Unterschied zu :func:`check_storage_quota_at_commit` ist allein die
    Herkunft der Bezugsgroessen: eine Importdatei hat keinen Owner (``None``,
    wie beim Vorab-Check), die ``facility`` kommt deshalb direkt herein statt
    aus ``owner.facility``. Die Audit-Zeile traegt ``target_type="ImportJob"``.

    ``additional_bytes`` ist die Groesse der **verschluesselten** Datei, so wie
    sie auf der Platte liegt und wie ``import_engine`` sie fuer offene Laeufe
    spaeter wieder aufsummiert — nur so ist die Rechnung in sich geschlossen.

    Raises:
        StorageQuotaBackstopError: wenn die Datei nicht mehr hineinpasst.
    """
    from core.services.file_vault.quota import facility_storage_overrun  # noqa: PLC0415 — Zyklus vermeiden

    usage = facility_storage_overrun(facility, additional_bytes)
    if usage is not None:
        raise StorageQuotaBackstopError(
            facility=facility,
            user=user,
            owner=None,
            usage=usage,
            requested_bytes=additional_bytes,
            target_type="ImportJob",
        )


def run_virus_scan(facility, uploaded_file, owner, user):
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
        _reject(
            facility,
            user,
            owner,
            reason="virus_scanner_unavailable",
            message=_("Datei-Upload abgelehnt: Virenscanner ist nicht erreichbar."),
            cause=exc,
            filename=uploaded_file.name,
            error=str(exc),
        )

    if result.infected:
        _reject(
            facility,
            user,
            owner,
            reason="virus_detected",
            message=_("Datei wurde von Virenscanner abgewiesen: %(signature)s")
            % {"signature": result.signature or "unknown"},
            filename=uploaded_file.name,
            signature=result.signature or "",
        )


# Bild-Formate MIT bereinigtem Metadaten-Container (Refs #2128, Befund N-1).
#
# ``gif``/``bmp`` bleiben bewusst draussen — aber NICHT, weil sie keine
# Metadaten tragen koennten: GIF89a kennt sowohl die *Comment Extension* als
# auch die *XMP-Data-Application-Extension*, BMP v5 kann ein ICC-Profil
# einbetten. Beides bleibt hier erhalten. Der Grund fuer die Ausnahme ist ein
# anderer: ein Pillow-Re-Encode wuerde bei GIF die Palette neu quantisieren und
# bei animierten Dateien Frame-Timing/Disposal-Verhalten veraendern — ein
# sichtbarer Eingriff ins Bild fuer einen Metadaten-Kanal, den die Praxis nicht
# nutzt (Beratungsfotos sind JPEG/PNG). Bewusst dokumentiertes Restrisiko
# (Admin-Handbuch § 2.9): wer es ausschliessen will, laesst ``gif``/``bmp``
# aus ``Settings.allowed_file_types`` heraus.
_METADATA_STRIP_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp", "tif", "tiff"})

# Dieselbe Menge auf der MIME-Seite (#2128, R-L7a-6). Massgeblich
# ist der per Magic-Bytes ERKANNTE Typ; die Endung entscheidet nur noch dort,
# wo libmagic nichts Belastbares liefert (``application/octet-stream``).
_METADATA_STRIP_MIMES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/webp", "image/tiff"})

# Formate, deren Encoder mehrere Frames schreiben kann. Nur fuer sie wird
# ``save_all`` gesetzt — sonst verlöre ein mehrseitiges TIFF bzw. ein
# animiertes WebP/APNG beim Bereinigen alle Frames ausser dem ersten.
_MULTIFRAME_SAVE_FORMATS = frozenset({"TIFF", "WEBP", "PNG", "GIF"})

# ``Image.info``-Schluessel, die den Re-Encode ueberleben duerfen. Beides ist
# Bild-*Wiedergabe*, keine Aufnahme-Metadatenspur: ohne ICC-Profil kippen die
# Farben, ohne ``transparency`` wird ein transparentes Palettenbild opak.
# Alles andere (``exif``, ``xmp``, ``comment``, ``dpi``, PNG-Textchunks,
# TIFF-``tag_v2``) faellt weg.
_KEEP_IMAGE_INFO_KEYS = frozenset({"icc_profile", "transparency"})

# Obergrenze der abgelaufenen RIFF-Chunks. Vor dem Bilddatenstrom stehen in
# realen Dateien eine Handvoll Chunks (``VP8X``, ``ICCP``, ``EXIF``, ``XMP ``,
# ``ANIM``); die Schranke ist reine Schleifen-Hygiene gegen eine praeparierte
# Chunk-Kette und kostet nur 8 gelesene Byte je Durchlauf.
_WEBP_MAX_CHUNKS = 64


def _webp_is_lossless(stream) -> bool:
    """Traegt dieser WebP-Container einen verlustfreien Bilddatenstrom?

    Pillow 12 legt die Verlustfreiheit **nicht** in ``Image.info`` ab (weder
    ``lossless`` noch ein Aequivalent) — verifiziert gegen 12.3.0. Ohne eigene
    Erkennung wuerde ein verlustfrei gespeicherter Dokumentenscan beim
    Bereinigen verlustbehaftet neu komprimiert; genau die Schrift-Artefakte,
    die man auf einem Bescheid-Scan nicht will.

    Die Antwort steht im RIFF-Containerkopf: ``VP8L`` ist der verlustfreie
    Datenstrom, ``VP8 `` der verlustbehaftete, ``VP8X`` nur der Kopf des
    Extended-Formats (er sagt nichts ueber die Codierung und wird
    uebersprungen). Gelaufen wird die **vollstaendige** Chunk-Kette ueber den
    Stream: je Chunk werden nur 8 Byte Kopf gelesen, die Nutzlast wird
    uebersprungen (RIFF polstert auf gerade Laenge). Ein Praefix-Scan waere
    falsch — ein zulaessiges ICC-/EXIF-/XMP-Chunk vor dem Bilddatenstrom kann
    beliebig gross sein (Refs #2128: bei 70 kB ICC lag ``VP8L``
    bei Offset 70 038 und wurde uebersehen).

    Bei animierten Dateien steckt der Datenstrom im ``ANMF``-Chunk hinter
    dessen 16-Byte-Kopf; der erste Frame entscheidet fuer die ganze Datei.
    """
    try:
        stream.seek(0)
        if stream.read(4) != b"RIFF":
            return False
        stream.seek(8)
        if stream.read(4) != b"WEBP":
            return False
        offset = 12
        for _ in range(_WEBP_MAX_CHUNKS):
            stream.seek(offset)
            head = stream.read(8)
            if len(head) < 8:
                return False
            fourcc = head[:4]
            size = int.from_bytes(head[4:8], "little")
            if fourcc == b"VP8L":
                return True
            if fourcc == b"VP8 ":
                return False
            if fourcc == b"ANMF":
                # ANMF-Kopf: 16 Byte Frame-Geometrie/-Timing, dann der
                # eingebettete Bilddatenstrom (ggf. nach einem ALPH-Chunk).
                return stream.read(20)[16:20] == b"VP8L"
            offset += 8 + size + (size % 2)
    except (OSError, ValueError):  # pragma: no cover - defensiv, Stream kaputt
        return False
    return False


def _image_save_kwargs(save_format: str, source, webp_lossless: bool = False) -> dict[str, Any]:
    """Encoder-Optionen je Zielformat (Refs #2128).

    JPEG bekommt ``quality="keep"``/``subsampling="keep"``: ohne sie wuerde
    jedes Handyfoto verlustbehaftet neu komprimiert — bei einem q60-Original
    ergibt ein Re-Encode mit ``quality=95`` eine **doppelt so grosse** Datei.
    ``keep`` verlangt allerdings, dass der Encoder die Quantisierungstabellen
    der Quelle sieht; das erledigt :func:`_cleaned_image` (siehe dort).

    PNG ist per Definition verlustfrei. WebP folgt der Quelle (``webp_lossless``
    aus :func:`_webp_is_lossless`), sonst hoher Qualitaetswert. TIFF behaelt die
    Kompression der Quelle, damit ein LZW-Scan nicht unkomprimiert aufgeblasen
    wird.

    **ICC-Profil:** Die JPEG- und WebP-Encoder von Pillow lesen
    das Profil ausschliesslich aus den Save-Argumenten — ein Profil in
    ``im.info`` ignorieren sie. Die Zusage aus R-L7a-1 („ICC-Profil bleibt")
    galt deshalb nur fuer PNG und TIFF, deren Encoder auf ``im.info``
    zurueckfallen; JPEG und WebP verloren die Farbtreue still. Hier wird es
    fuer beide explizit durchgereicht.
    """
    icc_profile = source.info.get("icc_profile")
    kwargs: dict[str, Any] = {}
    if save_format == "JPEG":
        kwargs = {"quality": "keep", "subsampling": "keep"}
    elif save_format == "PNG":
        return {"optimize": True}
    elif save_format == "WEBP":
        kwargs = {"lossless": True} if webp_lossless else {"quality": 95}
    elif save_format == "TIFF":
        compression = source.info.get("compression")
        return {"compression": compression} if compression else {}
    else:
        return {}
    # Nur JPEG und WebP brauchen das Profil explizit; PNG/TIFF ziehen es aus
    # ``im.info`` (dort steht es dank ``_cleaned_image``).
    if icc_profile:
        kwargs["icc_profile"] = icc_profile
    return kwargs


def _cleaned_image(frame, source):
    """Ein Frame ohne Metadaten, mit in die Pixel gedrehter Orientierung.

    ``ImageOps.exif_transpose`` liefert **immer** ein neues, "nacktes"
    ``Image`` (auch wenn nichts zu drehen war — dann eine ``copy()``). Genau
    das ist hier der Hebel: die Kopie hat kein ``tag_v2`` mehr, weshalb der
    TIFF-Encoder die sonst uebernommenen IPTC-/Photoshop-/XMP-Tags der Quelle
    nicht mehr findet, und kein ``exif``/``xmp``/``comment`` in ``encoderinfo``.
    Uebrig bleibt ``info``, aus dem JPEG-/PNG-/TIFF-Encoder sonst noch
    ``exif``/``xmp``/``comment`` nachziehen — das wird hier auf
    :data:`_KEEP_IMAGE_INFO_KEYS` eingedampft.

    ``format``/``quantization`` werden bewusst wieder gesetzt: der
    JPEG-Encoder prueft fuer ``quality="keep"`` genau diese beiden Attribute
    (``Cannot use 'keep' when original image is not a JPEG``).
    """
    from PIL import ImageOps  # noqa: PLC0415 — lazy wie die uebrigen PIL-Importe

    cleaned = ImageOps.exif_transpose(frame)
    cleaned.info = {key: value for key, value in frame.info.items() if key in _KEEP_IMAGE_INFO_KEYS}
    cleaned.format = source.format
    quantization = getattr(source, "quantization", None)
    if quantization is not None:
        cleaned.quantization = quantization
    return cleaned


def strip_image_metadata(facility, uploaded_file, owner, user, detected_mime=None):
    """Entferne eingebettete Metadaten aus Bild-Uploads (Refs #2128, Befund N-1).

    Liefert die zu speichernde Datei zurueck — fuer Nicht-Bilder und fuer
    ``gif``/``bmp`` **dasselbe** Objekt (byte-identisch), fuer die Formate aus
    :data:`_METADATA_STRIP_MIMES` eine neue, re-encodierte Datei ohne EXIF/GPS,
    ohne PNG-Textchunks, ohne XMP/IPTC.

    Massgeblich ist der per Magic-Bytes **erkannte** Typ (``detected_mime``),
    nicht die Endung (Refs #2128, R-L7a-6). Die Endung entscheidet
    nur noch, wenn libmagic nichts Belastbares liefert
    (``application/octet-stream``) — dann faellt die Stufe auf
    :data:`_METADATA_STRIP_EXTENSIONS` zurueck und Pillow hat das letzte Wort.
    Ein erkanntes Bild unter einer Nicht-Bild-Endung erreicht diese Stufe gar
    nicht mehr: ``enforce_magic_bytes`` weist es vorher fail-closed ab.

    Warum ueberhaupt: Fotos aus der Beratungspraxis (Verletzungsdokumentation,
    Bescheid-Scan) tragen regelmaessig den Aufnahmeort per EXIF-GPS — also den
    Aufenthaltsort der betroffenen Person —, dazu Geraete-Seriennummer,
    Zeitstempel und ein eingebettetes Vorschaubild, das den **unbeschnittenen**
    Originalausschnitt zeigt. Das lag bisher vollstaendig im Vault und ging bei
    Download, Art.-15-Auskunft und Einrichtungs-Export 1:1 wieder heraus —
    entgegen Art. 5 Abs. 1 lit. c DSGVO. Kein Schalter: die Anwendung ist kein
    Beweissicherungswerkzeug, und ein Opt-out machte die Datenminimierung zur
    Disziplinfrage.

    Reihenfolge (bindend): laeuft NACH ``enforce_image_limits`` und damit nach
    dem Pixel-Cap (#1268) — sonst dekodierte ausgerechnet diese Stufe eine
    Decompression-Bomb voll in den RAM — und unmittelbar VOR ``encrypt_file``.

    Fail-closed: laesst sich die Datei trotz bestandenem Magic-Bytes-Check
    nicht dekodieren oder nicht zurueckschreiben, wird der Upload mit
    ``image_undecodable`` abgewiesen. Sie stillschweigend ungestrippt zu
    speichern waere genau das Loch, das dieses Ticket schliesst.

    Zwei bewusste Entscheide (R-L7a-1):

    * **Groessen-Cap nach dem Re-Encode wird nicht erneut geprueft.**
      ``enforce_upload_size`` laeuft davor und bleibt der Memory-DoS-Schutz;
      der Re-Encode entfernt Metadaten und behaelt (JPEG) die
      Quantisierungstabellen, verkleinert also praktisch immer. Eine zweite
      Pruefung waere Komplexitaet ohne Sicherheitsgewinn.
    * **``detected_mime`` wird beibehalten, nicht neu gesniffed.** Gespeichert
      wird im Format, das Pillow in der Quelle erkannt hat (``img.format``) —
      der Container und damit die Magic-Bytes bleiben identisch, ein zweiter
      libmagic-Lauf koennte nur dasselbe Ergebnis liefern.
    """
    ext = _extension(uploaded_file)
    mime = (detected_mime or "").lower()
    if mime in _METADATA_STRIP_MIMES:
        # Erkannter Typ ist massgeblich — unabhaengig davon, wie die Datei heisst.
        pass
    elif _is_image_mime(mime):
        # Erkanntes Bild, aber kein Strip-Format (gif/bmp und exotische
        # image/*-Typen, die Pillow ggf. gar nicht oeffnen kann): unveraendert.
        return uploaded_file
    elif ext not in _METADATA_STRIP_EXTENSIONS:
        # libmagic liefert nichts Belastbares (``application/octet-stream``)
        # oder gar keinen Bild-Typ — dann entscheidet wie bisher die Endung.
        return uploaded_file

    import warnings  # noqa: PLC0415

    from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: PLC0415
    from PIL import Image, ImageSequence, UnidentifiedImageError  # noqa: PLC0415 — lazy, wie in enforce_image_limits

    # Verlustfreiheit VOR dem Oeffnen bestimmen (siehe ``_webp_is_lossless``):
    # der Chunk-Walk liest nur Kopf-Bytes und spult den Stream selbst, waehrend
    # Pillow ihn danach exklusiv haelt. Fuer Nicht-WebP kostet das genau einen
    # 4-Byte-Read (kein ``RIFF`` -> sofort ``False``).
    webp_lossless = _webp_is_lossless(uploaded_file)
    uploaded_file.seek(0)
    buffer = io.BytesIO()
    try:
        with warnings.catch_warnings():
            # Der Pixel-Cap hat bereits entschieden; eine Bomb-Warnung des
            # vollen Dekodierens darf hier nicht zum Fehler werden.
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(uploaded_file) as source:
                save_format = source.format
                if not save_format:
                    raise ValueError("Pillow lieferte kein Quellformat")
                multiframe = save_format in _MULTIFRAME_SAVE_FORMATS and getattr(source, "n_frames", 1) > 1
                if multiframe:
                    frames = [_cleaned_image(frame, source) for frame in ImageSequence.Iterator(source)]
                else:
                    frames = [_cleaned_image(source, source)]
                save_kwargs = _image_save_kwargs(save_format, source, webp_lossless)
                if multiframe:
                    save_kwargs = {**save_kwargs, "save_all": True, "append_images": frames[1:]}
                frames[0].save(buffer, format=save_format, **save_kwargs)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        _reject(
            facility,
            user,
            owner,
            reason="image_undecodable",
            message=_("Bilddatei konnte nicht gelesen werden — Upload abgelehnt."),
            cause=exc,
            filename=uploaded_file.name,
            extension=ext,
        )
    finally:
        uploaded_file.seek(0)

    return SimpleUploadedFile(
        uploaded_file.name,
        buffer.getvalue(),
        content_type=uploaded_file.content_type or "application/octet-stream",
    )
