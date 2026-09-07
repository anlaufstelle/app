"""Metadaten-Bereinigung hochgeladener Bilder (Refs #2128, Befund N-1).

Die Pre-Encrypt-Pipeline prueft bis hierher Extension, Groesse, Viren,
Magic-Bytes, Pixel-Cap und Zip-Bomben — die Upload-Bytes gehen danach
**unveraendert** in ``encrypt_file``. Smartphone-Fotos aus der Praxis tragen
EXIF-GPS (Aufnahmeort = Aufenthaltsort der betroffenen Person), Geraete-
Seriennummern, Zeitstempel und ein eingebettetes, oft **unbeschnittenes**
Vorschaubild; PNG traegt ``tEXt``/``iTXt``-Chunks, TIFF zusaetzlich
IPTC/Photoshop/XMP. All das lag verschluesselt, aber vollstaendig im Vault und
wurde bei Download, Art.-15-Auskunft und Einrichtungs-Export 1:1 wieder
herausgegeben — entgegen Art. 5 Abs. 1 lit. c DSGVO (Datenminimierung).

Diese Suite nagelt die neue Stufe ``strip_image_metadata`` fest:

* Bild-Formate mit Metadaten-Container (jpg/jpeg/png/webp/tif/tiff) werden
  re-encodiert; die **Orientierung** bleibt als Pixel-Rotation erhalten,
  damit Handyfotos nicht kippen.
* ``gif``/``bmp`` bleiben unangetastet (kein relevanter Metadaten-Container).
* Nicht-Bild-Uploads (PDF/DOCX) bleiben byte-identisch.
* Ein Bild, das den Magic-Bytes-Check besteht, aber nicht dekodierbar ist,
  wird **fail-closed** abgewiesen statt ungestrippt gespeichert.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

# ``store_encrypted_file`` prueft Magic-Bytes via libmagic (#610).
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffaehig ({_libmagic_exc}) — Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from PIL import ExifTags, Image, ImageSequence, PngImagePlugin  # noqa: E402

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, Settings  # noqa: E402
from core.models.attachment import Attachment  # noqa: E402
from core.services.file_vault import get_decrypted_file_stream, store_encrypted_file  # noqa: E402

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)

# Klartext-Marker, der in KEINER gespeicherten Bilddatei mehr auftauchen darf.
SECRET_MARKER = "PROBE-METADATA-MARKER-2128"


def _gradient(width: int, height: int) -> Image.Image:
    """Ein nicht-uniformes Bild — uniforme Flaechen komprimieren so gut, dass
    Groessenvergleiche nichts mehr aussagen."""
    img = Image.new("RGB", (width, height))
    img.putdata([((x * 7) % 256, (y * 13) % 256, (x * y) % 256) for y in range(height) for x in range(width)])
    return img


def _jpeg_with_gps(width: int = 60, height: int = 20, *, orientation: int = 6, quality: int = 60) -> bytes:
    """JPEG mit EXIF-GPS, Kamerahersteller und ``Orientation``."""
    img = _gradient(width, height)
    exif = img.getexif()
    exif[ExifTags.Base.Orientation] = orientation
    exif[ExifTags.Base.Make] = SECRET_MARKER
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (52, 31, 0)
    gps[ExifTags.GPS.GPSLongitudeRef] = "E"
    gps[ExifTags.GPS.GPSLongitude] = (13, 24, 0)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif, quality=quality)
    return buf.getvalue()


def _png_with_text() -> bytes:
    """PNG mit ``tEXt``-Chunks und eingebettetem EXIF."""
    img = _gradient(40, 12)
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Comment", SECRET_MARKER)
    meta.add_text("Author", "Erika Mustermann")
    exif = img.getexif()
    exif[ExifTags.Base.Make] = SECRET_MARKER
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta, exif=exif)
    return buf.getvalue()


def _webp_with_exif() -> bytes:
    img = _gradient(40, 12)
    exif = img.getexif()
    exif[ExifTags.Base.Make] = SECRET_MARKER
    buf = io.BytesIO()
    img.save(buf, format="WEBP", exif=exif, quality=80)
    return buf.getvalue()


def _webp_lossless() -> bytes:
    img = _gradient(40, 12)
    exif = img.getexif()
    exif[ExifTags.Base.Make] = SECRET_MARKER
    buf = io.BytesIO()
    img.save(buf, format="WEBP", lossless=True, exif=exif)
    return buf.getvalue()


def _gif_in_tif_container() -> bytes:
    """GIF-Bytes unter ``.tiff``-Namen — libmagic erkennt ``image/gif``, und
    ``tif``/``tiff`` haben keine Positiv-Allowlist in ``_STRONG_MAGIC``."""
    return _gif_bytes()


def _tiff_two_frames() -> bytes:
    first = _gradient(30, 10)
    second = _gradient(30, 10).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    buf = io.BytesIO()
    first.save(
        buf,
        format="TIFF",
        save_all=True,
        append_images=[second],
        description=SECRET_MARKER,
        software="ProbeSoft",
    )
    return buf.getvalue()


def _gif_bytes() -> bytes:
    buf = io.BytesIO()
    _gradient(20, 8).convert("P").save(buf, format="GIF")
    return buf.getvalue()


def _bmp_bytes() -> bytes:
    buf = io.BytesIO()
    _gradient(20, 8).save(buf, format="BMP")
    return buf.getvalue()


def _srgb_icc() -> bytes:
    """Ein echtes, kleines sRGB-ICC-Profil (588 Byte) aus Pillows Mitgabe."""
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _with_icc(fmt: str, **save_kwargs) -> tuple[bytes, bytes]:
    """(Bytes, ICC-Profil) eines Bildes mit eingebettetem ICC **und** EXIF."""
    icc = _srgb_icc()
    img = _gradient(40, 12)
    exif = img.getexif()
    exif[ExifTags.Base.Make] = SECRET_MARKER
    buf = io.BytesIO()
    img.save(buf, format=fmt, icc_profile=icc, exif=exif, **save_kwargs)
    return buf.getvalue(), icc


def _webp_lossless_hinter_grossem_icc() -> bytes:
    """Verlustfreies WebP, dessen ``VP8L``-Chunk hinter einem 70-kB-ICC liegt.

    Genau der Fall, an dem ein 64-KiB-Praefix-Scan scheitert.
    """
    img = _gradient(40, 12)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", lossless=True, icc_profile=b"\x00" * 70_000)
    return buf.getvalue()


def _docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
    return buf.getvalue()


def _stored_bytes(attachment) -> bytes:
    return b"".join(get_decrypted_file_stream(attachment))


@pytest.fixture
def facility_with_settings(facility):
    Settings.objects.update_or_create(
        facility=facility,
        defaults={
            "allowed_file_types": "pdf,docx,odt,jpg,jpeg,png,webp,tif,tiff,gif,bmp",
            "max_file_size_mb": 10,
        },
    )
    return facility


@pytest.fixture
def doc_type_with_file(facility_with_settings):
    dt = DocumentType.objects.create(
        facility=facility_with_settings,
        name="DocWithFile",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    ft_file = FieldTemplate.objects.create(
        facility=facility_with_settings,
        name="Scan",
        field_type=FieldTemplate.FieldType.FILE,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    return dt, ft_file


@pytest.fixture
def event(facility_with_settings, staff_user, doc_type_with_file):
    dt, _ = doc_type_with_file
    return Event.objects.create(
        facility=facility_with_settings,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )


@pytest.fixture
def upload(facility_with_settings, staff_user, doc_type_with_file, event):
    """Fahre eine Datei durch die komplette Upload-Pipeline."""
    _, ft_file = doc_type_with_file

    def _upload(name, payload, content_type):
        uploaded = SimpleUploadedFile(name, payload, content_type=content_type)
        return store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)

    return _upload


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestJpegMetadataStrip:
    """EXIF-GPS und Kameradaten duerfen den Vault nicht erreichen."""

    def test_gps_und_kameradaten_sind_entfernt(self, upload):
        source = _jpeg_with_gps()
        assert SECRET_MARKER.encode() in source, "Die Probe traegt den Marker nicht — Test waere wertlos"

        attachment = upload("foto.jpg", source, "image/jpeg")

        stored = _stored_bytes(attachment)
        assert SECRET_MARKER.encode() not in stored
        result = Image.open(io.BytesIO(stored))
        assert dict(result.getexif()) == {}
        assert dict(result.getexif().get_ifd(ExifTags.IFD.GPSInfo)) == {}

    def test_orientierung_bleibt_als_pixelrotation(self, upload):
        """``Orientation=6`` (90° im Uhrzeigersinn) wird in die Pixel gedreht —
        sonst kippt jedes Hochformat-Handyfoto beim Anzeigen."""
        source = _jpeg_with_gps(width=60, height=20, orientation=6)
        assert Image.open(io.BytesIO(source)).size == (60, 20)

        attachment = upload("hochkant.jpg", source, "image/jpeg")

        assert Image.open(io.BytesIO(_stored_bytes(attachment))).size == (20, 60)

    def test_file_size_entspricht_der_gespeicherten_datei(self, upload):
        attachment = upload("foto.jpg", _jpeg_with_gps(), "image/jpeg")

        assert attachment.file_size == len(_stored_bytes(attachment))

    def test_detected_mime_bleibt_erhalten(self, upload):
        attachment = upload("foto.jpg", _jpeg_with_gps(), "image/jpeg")

        assert attachment.detected_mime == "image/jpeg"

    def test_reencode_waechst_nicht_gegenueber_der_quelle(self, upload):
        """R-L7a-1 toleriert den Groessen-Cap nach dem Re-Encode, weil der
        Re-Encode die Datei eher verkleinert. Genau das wird hier gemessen —
        ohne uebernommene Quantisierungstabellen waere die Zusage falsch."""
        source = _jpeg_with_gps(width=200, height=120, quality=60)

        attachment = upload("gross.jpg", source, "image/jpeg")

        assert attachment.file_size <= len(source)


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestPngMetadataStrip:
    def test_text_chunks_und_exif_sind_entfernt(self, upload):
        source = _png_with_text()
        assert SECRET_MARKER.encode() in source

        attachment = upload("scan.png", source, "image/png")

        stored = _stored_bytes(attachment)
        assert SECRET_MARKER.encode() not in stored
        assert b"Erika Mustermann" not in stored
        result = Image.open(io.BytesIO(stored))
        assert result.format == "PNG"
        assert getattr(result, "text", {}) == {}
        assert dict(result.getexif()) == {}

    def test_png_bleibt_verlustfrei(self, upload):
        """PNG wird verlustfrei re-encodiert — die Pixel muessen identisch bleiben."""
        source = _png_with_text()

        attachment = upload("scan.png", source, "image/png")

        original = Image.open(io.BytesIO(source)).convert("RGB")
        stored = Image.open(io.BytesIO(_stored_bytes(attachment))).convert("RGB")
        assert stored.tobytes() == original.tobytes()


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestWebpUndTiff:
    def test_webp_exif_ist_entfernt(self, upload):
        attachment = upload("bild.webp", _webp_with_exif(), "image/webp")

        stored = _stored_bytes(attachment)
        assert SECRET_MARKER.encode() not in stored
        assert Image.open(io.BytesIO(stored)).format == "WEBP"
        assert dict(Image.open(io.BytesIO(stored)).getexif()) == {}

    def test_verlustfreies_webp_bleibt_verlustfrei(self, upload):
        """Pillow legt die Verlustfreiheit nicht in ``Image.info`` ab — sie wird
        aus dem RIFF-Containerkopf gelesen (``VP8L``). Ohne das wuerde ein
        verlustfrei gespeicherter Scan beim Bereinigen verlustbehaftet neu
        komprimiert."""
        source = _webp_lossless()
        assert b"VP8L" in source

        attachment = upload("scan.webp", source, "image/webp")

        stored = _stored_bytes(attachment)
        assert SECRET_MARKER.encode() not in stored
        assert b"VP8L" in stored
        original = Image.open(io.BytesIO(source)).convert("RGB")
        cleaned = Image.open(io.BytesIO(stored)).convert("RGB")
        assert cleaned.tobytes() == original.tobytes()

    def test_verlustfreies_webp_auch_hinter_grossem_icc_chunk(self, upload):
        """Der ``VP8L``-Chunk liegt hier bei Offset ~70 000 — ein Scan ueber ein
        64-KiB-Praefix haette das Bild faelschlich als verlustbehaftet
        eingestuft und mit ``quality=95`` neu codiert."""
        source = _webp_lossless_hinter_grossem_icc()
        assert source.find(b"VP8L") > 64 * 1024

        attachment = upload("grossicc.webp", source, "image/webp")

        stored = _stored_bytes(attachment)
        assert b"VP8L" in stored
        assert b"VP8 " not in stored

    def test_format_ohne_eigene_encoder_optionen_bleibt_im_container(self, upload):
        """Ein Format, fuer das ``_image_save_kwargs`` nichts Besonderes kennt
        (hier GIF-Bytes unter ``.tiff``-Namen), wird im **erkannten** Format
        zurueckgeschrieben — der Container und damit der verifizierte MIME-Typ
        bleiben stimmig."""
        attachment = upload("getarnt.tiff", _gif_in_tif_container(), "image/gif")

        assert attachment.detected_mime == "image/gif"
        assert Image.open(io.BytesIO(_stored_bytes(attachment))).format == "GIF"

    def test_tiff_bleibt_mehrseitig_ohne_beschreibung(self, upload):
        """Mehrseitige TIFF: alle Frames bleiben erhalten (``save_all``),
        die IPTC-/Description-Tags der Quelle verschwinden."""
        source = _tiff_two_frames()
        assert SECRET_MARKER.encode() in source

        attachment = upload("mehrseitig.tiff", source, "image/tiff")

        stored = _stored_bytes(attachment)
        assert SECRET_MARKER.encode() not in stored
        result = Image.open(io.BytesIO(stored))
        assert result.format == "TIFF"
        assert len(list(ImageSequence.Iterator(result))) == 2


class TestWebpVerlustfreiErkennung:
    """Direkte Sonde auf ``_webp_is_lossless`` — die Zweige, die über die
    Upload-Pipeline nicht (Nicht-WebP-Stream) bzw. nur mit einer animierten
    Datei (``ANMF``) erreichbar sind. Der Chunk-Walk arbeitet auf dem Stream
    und liest je Chunk nur 8 Byte Kopf."""

    def test_nicht_webp_bytes_gelten_als_verlustbehaftet(self):
        from core.services.file_vault.policy import _webp_is_lossless

        assert _webp_is_lossless(io.BytesIO(b"")) is False
        assert _webp_is_lossless(io.BytesIO(PDF_BYTES)) is False
        assert _webp_is_lossless(io.BytesIO(b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 8)) is False

    def test_verlustfreies_standbild_wird_erkannt(self):
        from core.services.file_vault.policy import _webp_is_lossless

        assert _webp_is_lossless(io.BytesIO(_webp_lossless())) is True

    def test_verlustbehaftetes_standbild_wird_nicht_faelschlich_erkannt(self):
        from core.services.file_vault.policy import _webp_is_lossless

        assert _webp_is_lossless(io.BytesIO(_webp_with_exif())) is False

    def test_grosser_chunk_vor_dem_bilddatenstrom_wird_uebersprungen(self):
        """Der Kern der Fix-Runde: ``VP8L`` liegt hinter einem 70-kB-ICC-Chunk.
        Ein Praefix-Scan haette hier ``False`` gesagt."""
        from core.services.file_vault.policy import _webp_is_lossless

        data = _webp_lossless_hinter_grossem_icc()
        assert data.find(b"VP8L") > 64 * 1024

        assert _webp_is_lossless(io.BytesIO(data)) is True

    def test_animiertes_verlustfreies_webp_wird_erkannt(self):
        from core.services.file_vault.policy import _webp_is_lossless

        first = _gradient(20, 8)
        second = _gradient(20, 8).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        buf = io.BytesIO()
        first.save(buf, format="WEBP", save_all=True, append_images=[second], lossless=True, duration=100)

        assert _webp_is_lossless(io.BytesIO(buf.getvalue())) is True

    def test_abgeschnittener_chunk_kopf_gilt_als_verlustbehaftet(self):
        """Untrusted Bytes: bricht die Chunk-Kette mitten im Kopf ab, gilt die
        Datei als verlustbehaftet statt eine Ausnahme zu werfen."""
        from core.services.file_vault.policy import _webp_is_lossless

        # Gueltiger RIFF/WEBP-Kopf, danach ein abgeschnittener Chunk-Kopf.
        stumpf = b"RIFF" + (100).to_bytes(4, "little") + b"WEBP" + b"VP8"

        assert _webp_is_lossless(io.BytesIO(stumpf)) is False

    def test_endlose_chunk_kette_bricht_nach_der_schranke_ab(self):
        """Eine praeparierte Kette aus lauter Null-Laengen-Chunks darf die
        Schleife nicht ewig laufen lassen."""
        from core.services.file_vault.policy import _WEBP_MAX_CHUNKS, _webp_is_lossless

        fuellchunks = (b"XXXX" + (0).to_bytes(4, "little")) * (_WEBP_MAX_CHUNKS + 5)
        data = b"RIFF" + len(fuellchunks).to_bytes(4, "little") + b"WEBP" + fuellchunks

        assert _webp_is_lossless(io.BytesIO(data)) is False

    def test_animiertes_verlustbehaftetes_webp_wird_nicht_faelschlich_erkannt(self):
        from core.services.file_vault.policy import _webp_is_lossless

        first = _gradient(20, 8)
        second = _gradient(20, 8).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        buf = io.BytesIO()
        first.save(buf, format="WEBP", save_all=True, append_images=[second], quality=50, duration=100)

        assert _webp_is_lossless(io.BytesIO(buf.getvalue())) is False


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFormateOhneMetadatenContainer:
    """``gif``/``bmp`` bleiben bewusst unangetastet (R-L7a-1) — sie tragen
    keinen Metadaten-Container, ein Re-Encode brächte nur Risiko."""

    def test_gif_bleibt_byte_identisch(self, upload):
        source = _gif_bytes()

        attachment = upload("anim.gif", source, "image/gif")

        assert _stored_bytes(attachment) == source

    def test_bmp_bleibt_byte_identisch(self, upload):
        source = _bmp_bytes()

        attachment = upload("bild.bmp", source, "image/bmp")

        assert _stored_bytes(attachment) == source


class TestSaveArgumenteJeFormat:
    """Direkte Sonde auf ``_image_save_kwargs`` — der Rueckfall fuer ein Format
    ohne eigene Encoder-Optionen (z. B. ``MPO``, der Mehrbild-JPEG-Container)
    ist ueber die Pipeline nur schwer erreichbar."""

    def test_unbekanntes_zielformat_bekommt_keine_optionen(self):
        from types import SimpleNamespace

        from core.services.file_vault.policy import _image_save_kwargs

        quelle = SimpleNamespace(info={"icc_profile": b"x" * 8})

        assert _image_save_kwargs("MPO", quelle) == {}

    def test_icc_wandert_nur_bei_jpeg_und_webp_in_die_argumente(self):
        from types import SimpleNamespace

        from core.services.file_vault.policy import _image_save_kwargs

        quelle = SimpleNamespace(info={"icc_profile": b"profil"})

        assert _image_save_kwargs("JPEG", quelle)["icc_profile"] == b"profil"
        assert _image_save_kwargs("WEBP", quelle)["icc_profile"] == b"profil"
        # PNG/TIFF ziehen es aus ``im.info`` — dort steht es dank _cleaned_image.
        assert "icc_profile" not in _image_save_kwargs("PNG", quelle)
        assert "icc_profile" not in _image_save_kwargs("TIFF", quelle)


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestBildUnterNichtBildEndung:
    """Refs #2128 (Fix-Runde 1, R-L7a-6): Die Bereinigung darf nicht an der
    Dateiendung haengen.

    Ein JPEG mit korrekt deklariertem ``image/jpeg`` unter der standardmaessig
    erlaubten Endung ``.docx``/``.odt`` bestand den Magic-Bytes-Check (erkannt
    == deklariert) und wurde danach von einer rein endungsbasierten
    Strip-Entscheidung durchgewunken — EXIF/GPS blieben drin, und der Pixel-Cap
    griff ebenfalls nicht.
    """

    @pytest.mark.parametrize("name", ["foto.docx", "foto.odt"])
    def test_jpeg_unter_nicht_bild_endung_wird_abgelehnt(self, upload, event, name):
        with pytest.raises(ValidationError):
            upload(name, _jpeg_with_gps(), "image/jpeg")

        assert Attachment.objects.filter(event=event).count() == 0

    def test_ablehnung_wird_als_mime_mismatch_protokolliert(self, upload, facility_with_settings):
        with pytest.raises(ValidationError):
            upload("foto.docx", _jpeg_with_gps(), "image/jpeg")

        entry = AuditLog.objects.filter(
            facility=facility_with_settings,
            action=AuditLog.Action.SECURITY_VIOLATION,
        ).latest("timestamp")
        assert entry.detail["reason"] == "mime_mismatch"
        assert entry.detail["detected"] == "image/jpeg"
        assert entry.detail["extension"] == "docx"

    def test_bild_unter_eigener_endung_bleibt_unveraendert_erlaubt(self, upload):
        """Gegenprobe: derselbe Inhalt unter ``.jpg`` wird normal gestrippt."""
        attachment = upload("foto.jpg", _jpeg_with_gps(), "image/jpeg")

        assert SECRET_MARKER.encode() not in _stored_bytes(attachment)

    def test_echte_docx_bleibt_erlaubt(self, upload):
        source = _docx_bytes()

        attachment = upload("bericht.docx", source, DOCX_MIME)

        assert _stored_bytes(attachment) == source

    def test_pixel_cap_greift_am_erkannten_mime_statt_an_der_endung(self, facility_with_settings, staff_user, settings):
        """Direkte Sonde: ``enforce_image_limits`` darf einen Bild-Upload nicht
        deshalb durchlassen, weil die Endung nicht nach Bild aussieht."""
        from core.services.file_vault.policy import enforce_image_limits

        settings.FILE_VAULT_MAX_IMAGE_PIXELS = 50
        buf = io.BytesIO()
        _gradient(10, 10).save(buf, format="PNG")  # 100 Pixel > 50
        getarnt = SimpleUploadedFile("tarnung.docx", buf.getvalue(), content_type="image/png")

        with pytest.raises(ValidationError):
            enforce_image_limits(facility_with_settings, getarnt, None, staff_user, detected_mime="image/png")


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestIccProfilBleibtErhalten:
    """R-L7a-1: „ICC-Profil bleibt". Fuer JPEG und WebP zieht der Pillow-Encoder
    das Profil NUR aus den Save-Argumenten — ``info`` allein reicht nicht."""

    @pytest.mark.parametrize(
        ("fmt", "name", "content_type", "save_kwargs"),
        [
            ("JPEG", "foto.jpg", "image/jpeg", {"quality": 60}),
            ("WEBP", "bild.webp", "image/webp", {"quality": 80}),
            ("PNG", "scan.png", "image/png", {}),
            ("TIFF", "scan.tiff", "image/tiff", {}),
        ],
    )
    def test_profil_bleibt_exif_geht(self, upload, fmt, name, content_type, save_kwargs):
        source, icc = _with_icc(fmt, **save_kwargs)

        attachment = upload(name, source, content_type)

        stored = _stored_bytes(attachment)
        result = Image.open(io.BytesIO(stored))
        assert result.info.get("icc_profile") == icc, f"{fmt}: ICC-Profil verloren"
        assert SECRET_MARKER.encode() not in stored
        # ``getexif()`` liefert bei TIFF die Basis-Tags des Containers (Breite,
        # Hoehe, Bittiefe) — dort ist die leere Erwartung fachlich falsch. Dass
        # die Aufnahme-Metadaten weg sind, belegt der Marker-Check darueber.
        if fmt != "TIFF":
            assert dict(result.getexif()) == {}


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestNichtBildUploadsUnveraendert:
    """Regressionsschutz: die neue Stufe darf ausschliesslich Bilder anfassen."""

    def test_pdf_bleibt_byte_identisch(self, upload):
        attachment = upload("bericht.pdf", PDF_BYTES, "application/pdf")

        assert _stored_bytes(attachment) == PDF_BYTES

    def test_docx_bleibt_byte_identisch(self, upload):
        source = _docx_bytes()

        attachment = upload("bericht.docx", source, DOCX_MIME)

        assert _stored_bytes(attachment) == source


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFailClosedBeiUndekodierbarenBildern:
    """Ein Bild, das den Magic-Bytes-Check besteht, aber nicht dekodierbar ist,
    darf NICHT ungestrippt gespeichert werden (fail-closed)."""

    def test_abgeschnittenes_png_wird_abgewiesen(self, upload, event):
        # libmagic erkennt die 8-Byte-Signatur weiterhin als image/png,
        # Pillow scheitert an der abgeschnittenen Datei.
        truncated = _png_with_text()[:20]

        with pytest.raises(ValidationError):
            upload("kaputt.png", truncated, "image/png")

        assert Attachment.objects.filter(event=event).count() == 0

    def test_reject_schreibt_security_violation_mit_grund(self, upload, facility_with_settings):
        truncated = _png_with_text()[:20]

        with pytest.raises(ValidationError):
            upload("kaputt.png", truncated, "image/png")

        entry = AuditLog.objects.filter(
            facility=facility_with_settings,
            action=AuditLog.Action.SECURITY_VIOLATION,
        ).latest("timestamp")
        assert entry.detail["reason"] == "image_undecodable"
        assert entry.detail["extension"] == "png"
        assert "kaputt.png" not in str(entry.detail)

    def test_bild_ohne_erkennbares_quellformat_wird_abgelehnt(self, upload, event, monkeypatch):
        """Defensiv: liefert Pillow kein ``format``, ist unbekannt, in welchem
        Container zurueckzuschreiben waere — dann lieber ablehnen als
        ungestrippt speichern."""
        from contextlib import contextmanager

        from PIL import Image as PILImage

        @contextmanager
        def _formatlos(*args, **kwargs):
            with _echtes_open(*args, **kwargs) as img:
                img.format = None
                yield img

        _echtes_open = PILImage.open
        monkeypatch.setattr(PILImage, "open", _formatlos)

        with pytest.raises(ValidationError):
            upload("ohne-format.png", _png_with_text(), "image/png")

        assert Attachment.objects.filter(event=event).count() == 0

    def test_keine_verwaiste_enc_datei_bleibt_liegen(self, upload, facility_with_settings, settings):
        from pathlib import Path

        facility_dir = Path(settings.MEDIA_ROOT) / str(facility_with_settings.pk)
        before = sorted(p.name for p in facility_dir.glob("*.enc")) if facility_dir.exists() else []

        with pytest.raises(ValidationError):
            upload("kaputt.png", _png_with_text()[:20], "image/png")

        after = sorted(p.name for p in facility_dir.glob("*.enc")) if facility_dir.exists() else []
        assert after == before
