"""Pin-Tests der Welle L (0.99, Refs #2174, Refs #2193) — Nacharbeit-Batch
``system-1n`` in ``core.services.system``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-system-1n.json``. Die vier
Mutanten dieser Batch-Datei waren in den Batches ``system-3``, ``system-2``
bzw. ``facility_export-d`` als ``equivalent``/``skip`` eingestuft; die
Familienreview hat das widerlegt und bindend vorgegeben: alle vier sind
``test``, Prio 3.

* **``core.services.system.export.x__build_event_row__mutmut_49``**
  (``data.get(field_slug, "")`` -> ``data.get(field_slug, None)``): fehlt ein
  Feld-Slug im ``data_json`` eines Ereignisses vollstaendig, loest das
  Original ueber den Leerstring-Default die Options-Zuordnung der SELECT-
  Feldvorlage auf (``"" -> "Keine Angabe"``). Die Mutante liefert ``None``
  statt ``""``, das ``_resolve_field_value`` nicht mehr als ``str`` erkennt
  und ungemappt durchreicht — die CSV-Zelle bleibt leer statt des Labels.
* **``core.services.system.facility_export.x__attachment_sha256__mutmut_6``**
  (``logger.warning(attachment.pk)`` statt ``logger.warning(Format, pk)``)
  und **``_7``** (Format-Argument entfernt, ``%s`` bleibt im Text stehen):
  eine physisch fehlende Anhang-Datei loest den ``OSError``-Diagnosezweig
  aus. ``_6`` protokolliert nur den blanken PK ohne jeden erklaerenden Text,
  ``_7`` protokolliert den unformatierten Platzhaltertext mit literalem
  ``%s`` statt des echten PKs — ein exakter Abgleich gegen den vollstaendig
  aufgeloesten Originaltext toetet beide in einem Test.
* **``core.services.system.bans.x__project_ban_data__mutmut_37``**
  (``logger.debug("…: %r", bis_raw)`` -> ``logger.debug(None, bis_raw)``):
  ein aktives Hausverbot mit einem nicht ISO-parsbaren ``bis``-Rohwert
  durchlaeuft den Diagnose-Log-Zweig. Unter aktivem Log-Capturing (jeder
  Handler mit DEBUG-Level, wie ``caplog``) bricht die Mutante bereits beim
  Formatieren des Records mit ``TypeError`` ab (``"None" % (bis_raw,)`` hat
  keinen Platzhalter fuer das Argument) — das Original liefert dagegen sauber
  den vollstaendigen Diagnosetext.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone, translation

from core.models import DocumentType, Event, FieldTemplate
from core.services.file_vault import get_attachment_path, store_encrypted_file
from core.services.system import export as sys_export
from core.services.system import facility_export as fx
from core.services.system.bans import _project_ban_data

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

#: Erste dynamische Spalte einer CSV-Zeile — davor stehen Datum, Uhrzeit,
#: Dokumentationstyp, Person, Kontaktstufe, Altersgruppe (``_build_header``).
ERSTE_DYNAMISCHE_SPALTE = 6

FACILITY_EXPORT_LOGGER = "core.services.system.facility_export"
BANS_LOGGER = "core.services.system.bans"

PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _make_attachment(facility, owner, user):
    ft = FieldTemplate.objects.create(
        facility=facility, name=f"Anhang-{uuid.uuid4()}", field_type=FieldTemplate.FieldType.FILE
    )
    upload = SimpleUploadedFile("system-1n-test.pdf", PDF_MINIMAL, content_type="application/pdf")
    return store_encrypted_file(facility, upload, ft, owner, user)


# ---------------------------------------------------------------------------
# _build_event_row (core.services.system.export) — Leerstring-Default der
# Options-Zuordnung bei fehlendem Feld-Slug
# ---------------------------------------------------------------------------


class TestBuildEventRowMissingSlugOptionsDefault:
    def test_fehlender_feld_slug_loest_ueber_die_leerstring_option_auf(self, facility, admin_user):
        """Pins ``core.services.system.export.x__build_event_row__mutmut_49``.

        Die Feldvorlage ist eine sichtbare SELECT-Feldvorlage mit einer
        Leerstring-Option (``"" -> "Keine Angabe"``); das Ereignis enthaelt
        den Feld-Slug ueberhaupt nicht in ``data_json``. Original:
        ``data.get(field_slug, "")`` -> ``""`` -> ueber ``options_json`` zu
        ``"Keine Angabe"`` aufgeloest. Mutante: ``data.get(field_slug, None)``
        -> ``None`` -> ``_resolve_field_value`` erkennt es nicht als ``str``
        und reicht ``None`` durch -> nach ``_sanitize_csv_cell`` eine leere
        Zelle statt des Labels.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        field = FieldTemplate.objects.create(
            facility=facility,
            name="Anliegen",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[
                {"slug": "", "label": "Keine Angabe"},
                {"slug": "beratung", "label": "Beratung"},
            ],
        )
        event = Event.objects.create(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},  # field.slug fehlt komplett, kein Eintrag im Dict
            is_anonymous=True,
            created_by=admin_user,
        )

        with translation.override("de"):
            row = sys_export._build_event_row(event, {field.slug: field}, [field.slug], admin_user)

        assert row[ERSTE_DYNAMISCHE_SPALTE] == "Keine Angabe"


# ---------------------------------------------------------------------------
# _attachment_sha256 (core.services.system.facility_export) — vollstaendiger
# Warn-Meldungstext bei unlesbarer Anhang-Datei
# ---------------------------------------------------------------------------


class TestAttachmentSha256WarningMessage:
    def test_unlesbare_datei_protokolliert_den_vollen_wortlaut_mit_pk(self, facility, sample_event, staff_user, caplog):
        """Pins ``core.services.system.facility_export.x__attachment_sha256__mutmut_6``
        und ``…_7``.

        Die physische Anhang-Datei wird nach dem Anlegen von der Platte
        entfernt — ``get_decrypted_file_stream`` wirft beim ersten
        Iterationsschritt ``FileNotFoundError`` (``OSError``), der
        Diagnosezweig protokolliert Level WARNING mit exakt einer Meldung.
        Original: vollstaendiger Text inkl. aufgeloestem PK. ``_6``: nur der
        blanke PK ohne Text. ``_7``: der unformatierte Text mit literalem
        ``%s`` statt des PKs. Ein exakter ``getMessage()``-Abgleich gegen
        den vollstaendig aufgeloesten Originaltext toetet beide.
        """
        caplog.set_level(logging.WARNING, logger=FACILITY_EXPORT_LOGGER)
        attachment = _make_attachment(facility, sample_event, staff_user)
        get_attachment_path(attachment).unlink()

        result = fx._attachment_sha256(attachment)

        assert result is None
        warnungen = [r for r in caplog.records if r.name == FACILITY_EXPORT_LOGGER and r.levelno == logging.WARNING]
        assert len(warnungen) == 1
        assert warnungen[0].getMessage() == (
            f"Facility-Export: Anhang-Datei unlesbar/korrupt (attachment_id={attachment.pk})"
        )


# ---------------------------------------------------------------------------
# _project_ban_data (core.services.system.bans) — Diagnosetext bei
# nicht parsbarem "bis"-Rohwert
# ---------------------------------------------------------------------------


class TestProjectBanDataInvalidBisLogging:
    def test_defektes_bis_datum_protokolliert_den_rohwert_per_repr(self, caplog):
        """Pins ``core.services.system.bans.x__project_ban_data__mutmut_37``.

        Ein aktives Hausverbot mit einem nicht ISO-parsbaren ``bis``-Rohwert
        (``"kein-datum"``) durchlaeuft den ``except (ValueError, TypeError)``-
        Diagnosezweig. Original: ``logger.debug("…: %r", bis_raw)``
        protokolliert den Rohwert per ``repr``. Mutante:
        ``logger.debug(None, bis_raw)`` — unter aktivem Log-Capturing bricht
        bereits das Formatieren des Records mit ``TypeError`` ab
        (``"None" % (bis_raw,)`` hat keinen Platzhalter fuer das Argument),
        die Funktion liefert unter der Mutante also gar kein Ergebnis mehr.
        """
        caplog.set_level(logging.DEBUG, logger=BANS_LOGGER)

        result = _project_ban_data(
            {"aktiv": True, "bis": "kein-datum", "grund": ""}, today=timezone.localdate(), may_see_grund=False
        )

        assert result == (True, None, "")
        debug_meldungen = [r for r in caplog.records if r.name == BANS_LOGGER and r.levelno == logging.DEBUG]
        assert len(debug_meldungen) == 1
        assert debug_meldungen[0].getMessage() == "Ungueltiges Ban-'bis'-Datum ignoriert: 'kein-datum'"
