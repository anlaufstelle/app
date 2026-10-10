"""Pin-Tests der Welle K (0.98, Refs #2193) — Prio-2-Testlücken in
``core.services.client.rectification``, Batch ``case-client``.

Quelle: ``welle-k-0.98/reports/c-scratch/batch-case-client.json``
(``triage-097-client.json``). 15 Survivors in vier Funktionen — keine
Überschneidung mit den bereits in ``test_mutation_pins_097_rectification.py``
gepinnten Mutanten (dort: Zieltreue der Reloads unter der Zeilensperre,
Rechenschaft der Akteurin; hier: Wertebereichsprüfung, exakter
``target_type``-Wortlaut, Leer-Rückgabe und Schleifensemantik).

* **``_validate_new_value``** — ``x__validate_new_value__mutmut_13``:
  ``text = "" if new_value is None`` wird zu ``text = "XXXX" if …`` — ein
  ``None``-Pseudonym liefe damit NICHT mehr in den Leer-Guard, sondern würde
  als Wunschwert ``"XXXX"`` akzeptiert. ``…__mutmut_36``: ``len(text) >
  max_length`` wird zu ``>=`` — ein Pseudonym von genau 100 Zeichen (Grenzwert
  von ``Client.pseudonym``, ``core/models/client.py:59``) würde fälschlich als
  zu lang abgelehnt.
* **``approve_rectification``/``reject_rectification``** — je fünf Mutanten
  verfälschen ``target_type="RectificationRequest"`` (``None``, entfernt,
  ``"XXRectificationRequestXX"``, klein-, großgeschrieben). Kein Meldungstext,
  sondern das gespeicherte Typkennzeichen der Audit-Zeile — Vergleich exakt
  per ``==``, nie per Teilstring.
* **``decode_rectification_text``** — ``x_decode_rectification_text__mutmut_2``:
  der Leer-Rückgabewert wird zu ``"XXXX"``. ``review_note``/``reason`` sind
  ``null=True``; bei jedem offenen Antrag ist der gespeicherte Wert ``None``.
* **``rectifiable_fields``** — ``…__mutmut_12``/``…__mutmut_22``: je eines der
  beiden ``continue`` in der Feldschleife wird zu ``break``. Beide Mutanten
  brechen die Schleife beim ERSTEN übersprungenen Feld ab (FILE-Feld bzw.
  ein für die Rolle unsichtbares Feld) — alle danach folgenden, eigentlich
  sichtbaren Felder fehlen dann in der Allowlist.

``_encryption_key`` liegt modulweit an: der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``; ohne die Fixture wären die Pins dort aus dem FALSCHEN
Grund rot.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from core.constants import DATA_JSON_PREFIX
from core.models import AuditLog, DocumentType, DocumentTypeField, FieldTemplate
from core.services.client.rectification import (
    _validate_new_value,
    approve_rectification,
    decode_rectification_text,
    rectifiable_fields,
    reject_rectification,
    request_rectification,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestValidateNewValue:
    def test_pseudonym_none_wird_abgelehnt_notes_none_ist_leerstring(self):
        """Pins ``x__validate_new_value__mutmut_13``: der Leer-Fallback
        (``new_value is None``) wird zum Literal ``"XXXX"`` — ein
        ``None``-Pseudonym liefe dann NICHT mehr in den Leer-Guard, sondern
        würde akzeptiert; ein ``None``-Notizfeld läse fälschlich ``"XXXX"``
        statt eines leeren Strings."""
        with pytest.raises(ValidationError):
            _validate_new_value("pseudonym", None)
        assert _validate_new_value("notes", None) == ""

    def test_pseudonym_grenzwert_von_100_zeichen_ist_gueltig(self):
        """Pins ``x__validate_new_value__mutmut_36``: ``len(text) >
        max_length`` wird zu ``>=`` — ein Pseudonym von EXAKT 100 Zeichen
        (``Client.pseudonym``-Grenzwert) würde fälschlich als zu lang
        abgelehnt, obwohl es in die Spalte passt."""
        grenzwert = "x" * 100
        assert _validate_new_value("pseudonym", grenzwert) == grenzwert
        with pytest.raises(ValidationError):
            _validate_new_value("pseudonym", "x" * 101)


class TestAuditTargetTypeExaktheit:
    def test_genehmigung_auditiert_den_exakten_zieltyp(self, client_identified, staff_user, lead_user):
        """Pins ``x_approve_rectification__mutmut_70/76/79/80/81``:
        ``target_type="RectificationRequest"`` wird zu ``None``, entfernt oder
        mit XX-Markern/Groß-Kleinschreibung verfälscht. Kein Meldungstext,
        sondern das gespeicherte Typkennzeichen der Audit-Zeile — Vergleich
        exakt, nie per Teilstring."""
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")

        approve_rectification(rr, lead_user)

        entry = AuditLog.objects.get(action=AuditLog.Action.RECTIFICATION_APPROVED, target_id=str(rr.pk))
        assert entry.target_type == "RectificationRequest"
        assert entry.target_id == str(rr.pk)

    def test_ablehnung_auditiert_den_exakten_zieltyp(self, client_identified, staff_user, lead_user):
        """Pins ``x_reject_rectification__mutmut_38/44/47/48/49`` — Spiegel von
        ``approve_rectification`` für den Ablehnungspfad."""
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")

        reject_rectification(rr, lead_user, "Nicht nachvollziehbar")

        entry = AuditLog.objects.get(action=AuditLog.Action.RECTIFICATION_REJECTED, target_id=str(rr.pk))
        assert entry.target_type == "RectificationRequest"
        assert entry.target_id == str(rr.pk)


def test_decode_rectification_text_ist_bei_fehlendem_wert_leer():
    """Pins ``x_decode_rectification_text__mutmut_2``: der Leer-Rückgabewert
    wird zu ``"XXXX"``. ``review_note``/``reason`` sind ``null=True`` — bei
    jedem offenen Antrag ist der gespeicherte Wert ``None``, Detailseite und
    DSGVO-Auskunft würden sonst den Literaltext ``"XXXX"`` ausgeben."""
    assert decode_rectification_text(None) == ""
    assert decode_rectification_text("") == ""


class TestRectifiableFieldsSchleifensemantik:
    def test_datei_und_unsichtbares_feld_ueberspringen_nur_sich_selbst(self, facility, client_identified, staff_user):
        """Pins ``x_rectifiable_fields__mutmut_12`` (``continue`` -> ``break``
        beim FILE-Feld) UND ``…__mutmut_22`` (``continue`` -> ``break`` bei der
        Sichtbarkeitsprüfung): unter beiden Mutanten bricht die Feldschleife
        beim ERSTEN übersprungenen Feld ab, ein danach folgendes sichtbares
        Textfeld fehlt dann in der Allowlist. Reihenfolge: FILE-Feld, dann ein
        für ``staff`` (``ROLE_MAX_SENSITIVITY`` bis ``ELEVATED``) unsichtbares
        HIGH-Feld, dann ein sichtbares TEXT-Feld."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.CLIENT,
            name="Pin098-Reihenfolge",
        )
        ft_datei = FieldTemplate.objects.create(
            facility=facility,
            name="Anhang (Pin098)",
            field_type=FieldTemplate.FieldType.FILE,
        )
        ft_hoch = FieldTemplate.objects.create(
            facility=facility,
            name="Vertraulich (Pin098)",
            field_type=FieldTemplate.FieldType.TEXT,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        ft_sichtbar = FieldTemplate.objects.create(
            facility=facility,
            name="Sichtbar (Pin098)",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_datei, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_hoch, sort_order=1)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_sichtbar, sort_order=2)
        client_identified.document_type = doc_type
        client_identified.save(update_fields=["document_type", "data_json"])

        result = dict(rectifiable_fields(client_identified, staff_user))

        assert f"{DATA_JSON_PREFIX}{ft_sichtbar.slug}" in result
