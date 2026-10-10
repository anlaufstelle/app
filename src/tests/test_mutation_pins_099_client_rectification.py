"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.client.rectification``.

Quelle: Vollmutation #2174, Batch ``client-1``
(``welle-l-0.99/reports/m-scratch/batch-client-1.json``,
Triage ``welle-l-0.99/reports/m-scratch/pins-client-1/triage-099-client-1.json``). Die Batch hat 61
Survivor-Mutanten; 56 sind Testlücken (``test``) — darunter 30 reine WORTLAUT-Varianten desselben
Textes (Groß-/Kleinschreibung oder der mutmut-``XX``-Marker), die von den exakten Meldungs-Pins
unten mitgetötet werden und deshalb nicht als ``skip`` gelten — und fünf sind
``equivalent`` (``_core_field_label``/``_validate_new_value``: ``getattr``-Default für
``verbose_name``/``max_length`` ist auf jedem Django-``Field``-Objekt unerreichbar, siehe Triage;
``approve_``/``reject_rectification``: ``save(update_fields=None)`` persistiert unter der
``select_for_update()``-Sperre byte-identische Werte wie das Teil-Save, keine Signale auf
``RectificationRequest``). Die 56 ``test``-Mutanten sind hier mit 16 Tests gepinnt:

* **Meldungs-VERLUST an der Service-API (19 Mutanten, zwölf Guards, zwölf neue Tests unten in
  ``TestExakteFehlermeldungAnDenGuards``).** ``raise ValidationError(_(text))`` wird zu
  ``raise ValidationError(None)`` (den kompletten Aufruf ``_(text)`` durch ``None`` ersetzt — die
  Exception traegt dann buchstaeblich die Meldung ``"None"``, siehe ``ValidationError(None).messages
  == ['None']``) bzw. zu ``raise ValidationError(_(None))`` (``gettext_lazy(None)`` — die Exception
  wird zwar geworfen, aber JEDE Stringifizierung des Objekts — ``.messages``, ``str(exc)``,
  Formular-/Template-Rendering — bricht mit ``AttributeError: 'NoneType' object has no attribute
  'replace'`` in Djangos Übersetzungsmaschine ab, empirisch verifiziert). Beides ist an der
  Service-API beobachtbares Verhalten (die aufrufende View zeigt entweder den Literaltext ``"None"``
  oder crasht mit 500 statt eines Formularfehlers) — anders als reine Groß-/Klein- oder
  ``XX``-Markervarianten desselben Wortlauts (die bleiben ``skip``). Zwölf Tests rufen je die
  betroffene Guard-Stelle auf und vergleichen ``exc_info.value.messages`` exakt gegen den
  Original-Wortlaut (kein ``match=`` — das ist Teilstring-Suche und würde weder den ``XX``-Marker-
  Fall noch den ``None``-Verlust zuverlässig von einem echten, inhaltlich ähnlichen Konflikt-Text
  unterscheiden; ein Guard mit zwei Survivor-Varianten (``None``-Aufruf UND ``_(None)``) wird von
  DEMSELBEN Test gemeinsam getötet, da beide an derselben Stelle denselben Meldungsverlust
  erzeugen).

* **Feldreihenfolge im Berichtigungsformular.** ``rectifiable_fields`` reicht
  ``ordered=True`` an :func:`~core.services.dynamic_data.build_field_template_lookup` durch, damit
  dynamische Felder in der globalen BLOCK-Reihenfolge erscheinen (Gruppenblock vor/nach freiem
  Feld je Blockposition), nicht im rohen ``sort_order`` der ``DocumentTypeField``-Zeile (der
  innerhalb einer Gruppe nur die Position IM Block ist). Drei Survivor
  (``x_rectifiable_fields__mutmut_7/9/10``) setzen ``ordered`` effektiv auf falsy — EIN Test mit
  einer Gruppe (Blockposition 5) und einem freien Feld (Blockposition 1) deckt alle drei, weil sie
  identisch auf die unsortierte Relation zurückfallen.
* **Nicht-Text-Rückgabe von ``decode_rectification_text``.** ``x_decode_rectification_text__mutmut_5``
  ersetzt den ``str(plain)``-Fallback (für einen von ``safe_decrypt`` unverändert durchgereichten,
  nicht-verschlüsselten Rohwert) durch das Literal ``str(None)`` — der tatsächliche Inhalt eines
  Nicht-Text-Werts würde als ``"None"`` angezeigt statt seiner selbst.
* **Stiller Platzhaltertext bei Genehmigung ohne Prüfvermerk.** ``x_approve_rectification__mutmut_1``
  ändert den Default von ``review_note`` von ``""`` auf ``"XXXX"`` — ein Aufruf der Service-Funktion
  ohne Prüfvermerk (die View übergibt zwar immer explizit, die Funktion ist aber öffentliche
  Service-API) speicherte einen erfundenen Text, als hätte die Leitung etwas notiert.
* **Optimistic-Locking-Guard von ``_apply_rectification``.** ``x__apply_rectification__mutmut_14``
  (``expected_updated_at=None``) und ``…__mutmut_17`` (Argument weggelassen) schalten den
  Versions-Guard von ``update_client`` für den Anwendungsschritt stumm ab. Im regulären
  Genehmigungspfad ist das durch die äußere ``select_for_update()``-Sperre der Client-Zeile
  überdeckt — ``_apply_rectification`` ist aber eigenständig aufrufbar, und ihr eigener Docstring
  nennt genau diesen Schutzzweck („schützt gegen Schreiber, die zwischen unserem Lesen und dem
  Save dazwischenfunken"). EIN Test ruft die Funktion direkt mit einem zwischenzeitlich per
  Direkt-Update geänderten Client auf.

``_encryption_key`` liegt modulweit an: der Mutantenbaum (``/work/anlaufstelle/mutants``) hat keine
``.env`` und damit keinen ``ENCRYPTION_KEY``; ohne die Fixture wären die Pins dort aus dem FALSCHEN
Grund rot.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.constants import DATA_JSON_PREFIX
from core.models import Client, DocumentType, DocumentTypeField, FieldGroup, FieldTemplate, User
from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE, update_client
from core.services.client.rectification import (
    _apply_rectification,
    _validate_new_value,
    approve_rectification,
    decode_rectification_text,
    rectifiable_fields,
    reject_rectification,
    request_rectification,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestRectifiableFieldsBlockReihenfolge:
    def test_dynamische_felder_folgen_der_block_reihenfolge_nicht_dem_rohen_sort_order(
        self, facility, client_identified, staff_user
    ):
        """Pins ``x_rectifiable_fields__mutmut_7`` (``ordered=None``),
        ``…__mutmut_9`` (Argument weggelassen) und ``…__mutmut_10``
        (``ordered=False``): alle drei fallen auf die UNSORTIERTE
        ``document_type.fields``-Relation zurück (Meta-``ordering=["sort_order"]``
        sortiert dort nach der ROHEN Zeilen-Spalte, die innerhalb einer Gruppe nur
        die Position IM Block ist). Aufbau: eine Gruppe mit Blockposition 5, darin
        EIN Feld mit gruppeninterner Position 0; dazu ein freies Feld mit
        Blockposition 1. Block-Reihenfolge (Original): frei (1) VOR Gruppe (5).
        Rohe ``sort_order``-Reihenfolge (alle drei Mutanten): Gruppenfeld (0) VOR
        freiem Feld (1) — exakt vertauscht."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.CLIENT,
            name="Pin099-Client1-Reihenfolge",
        )
        gruppe = FieldGroup.objects.create(document_type=doc_type, name="Pin099-Client1-Gruppe", sort_order=5)
        ft_frei = FieldTemplate.objects.create(
            facility=facility, name="Pin099-Client1-Frei", field_type=FieldTemplate.FieldType.TEXT
        )
        ft_gruppe = FieldTemplate.objects.create(
            facility=facility, name="Pin099-Client1-Gruppiert", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_frei, sort_order=1)
        DocumentTypeField.objects.create(
            document_type=doc_type, field_template=ft_gruppe, field_group=gruppe, sort_order=0
        )
        client_identified.document_type = doc_type
        client_identified.save(update_fields=["document_type", "data_json"])

        result = rectifiable_fields(client_identified, staff_user)
        dynamic_paths = [path for path, _label in result if path.startswith(DATA_JSON_PREFIX)]

        assert dynamic_paths == [
            f"{DATA_JSON_PREFIX}{ft_frei.slug}",
            f"{DATA_JSON_PREFIX}{ft_gruppe.slug}",
        ]


def test_decode_rectification_text_gibt_nicht_string_werte_als_text_wieder():
    """Pins ``x_decode_rectification_text__mutmut_5``: der ELSE-Zweig (``plain``
    ist kein ``str``, weil ``safe_decrypt`` einen nicht als verschlüsselt
    erkannten Rohwert unverändert durchreicht) wird von ``str(plain)`` auf das
    Literal ``str(None)`` verfälscht — ein Nicht-Text-Wert würde als ``"None"``
    angezeigt statt seines tatsächlichen Inhalts."""
    stored = {"foo": "bar"}  # kein Encrypted-Marker (kein "__encrypted__": True) -> unveraendert durchgereicht
    assert decode_rectification_text(stored) == str(stored)


class TestApproveRectificationDefaultPruefvermerk:
    def test_genehmigung_ohne_pruefvermerk_speichert_leertext_nicht_platzhalter(
        self, client_identified, staff_user, lead_user
    ):
        """Pins ``x_approve_rectification__mutmut_1``: der Default-Wert von
        ``review_note`` wird von ``""`` auf das Literal ``"XXXX"`` verfälscht —
        ein Aufruf der Service-Funktion ohne Prüfvermerk speicherte dann einen
        erfundenen Text, als hätte die Leitung tatsächlich etwas notiert."""
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")

        approve_rectification(rr, lead_user)  # kein review_note uebergeben -> Default greift

        rr.refresh_from_db()
        assert decode_rectification_text(rr.review_note) == ""


class TestApplyRectificationVersionsGuard:
    def test_apply_rectification_lehnt_einen_zwischenzeitlich_geaenderten_client_ab(self, client_identified, lead_user):
        """Pins ``x__apply_rectification__mutmut_14`` (``expected_updated_at=None``)
        und ``…__mutmut_17`` (Argument ganz weggelassen): beide schalten den
        Optimistic-Locking-Guard von ``update_client`` für den Anwendungsschritt
        STUMM ab. Im reguären Genehmigungspfad deckt die äußere
        ``select_for_update()``-Sperre der Client-Zeile das ab — aber
        ``_apply_rectification`` ist eigenständig aufrufbar (z. B. aus einem
        künftigen Command-/Replay-Pfad ohne diese Sperre), und ihr eigener
        Docstring nennt genau diesen Schutzzweck: ,schützt gegen Schreiber, die
        zwischen unserem Lesen und dem Save dazwischenfunken'. Simuliert wird das
        hier durch einen Direkt-Update auf die Zeile zwischen dem Lesen des
        Python-Objekts und dem Aufruf."""
        stale_client = Client.objects.get(pk=client_identified.pk)
        Client.objects.filter(pk=client_identified.pk).update(updated_at=timezone.now())

        with pytest.raises(ValidationError, match="zwischenzeitlich bearbeitet"):
            _apply_rectification(stale_client, lead_user, "notes", "Sollte nicht greifen")

        client_identified.refresh_from_db()
        assert client_identified.notes == ""


class TestExakteFehlermeldungAnDenGuards:
    """Zwölf Guard-Stellen mit exaktem ``exc_info.value.messages``-Vergleich.

    Nachtrag (Zweitgutachtung): ein Meldungsverlust (``raise ValidationError(_(text))`` →
    ``raise ValidationError(None)`` bzw. ``raise ValidationError(_(None))``) ist an der
    Service-API beobachtbares Verhalten — die aufrufende Seite bekäme entweder den Literaltext
    ``"None"`` oder einen ``AttributeError`` statt eines Formularfehlers zu sehen — und gilt
    deshalb als ``test``, nicht als ``skip``. Jeder Test hier deckt beide Survivor-Varianten
    derselben Guard-Stelle ab, wo beide existieren.
    """

    def test_unerlaubtes_feld_meldet_den_exakten_wortlaut(self, client_identified, staff_user):
        """Pins ``x__assert_rectifiable__mutmut_6`` (``ValidationError(None)``) und
        ``…__mutmut_7`` (``ValidationError(_(None))``)."""
        with pytest.raises(ValidationError) as exc_info:
            request_rectification(client_identified, staff_user, "facility_id", "x", "Grund")

        assert exc_info.value.messages == ["Dieses Feld kann nicht über den Berichtigungsworkflow geändert werden."]

    def test_ungueltige_altersgruppe_meldet_den_exakten_wortlaut(self):
        """Pins ``x__validate_new_value__mutmut_7`` (``ValidationError(None)``) und
        ``…__mutmut_8`` (``ValidationError(_(None))``)."""
        with pytest.raises(ValidationError) as exc_info:
            _validate_new_value("age_cluster", "pin099-keine-gueltige-gruppe")

        assert exc_info.value.messages == ["Ungültige Altersgruppe."]

    def test_leeres_pseudonym_meldet_den_exakten_wortlaut(self):
        """Pins ``x__validate_new_value__mutmut_21`` (``ValidationError(None)``) und
        ``…__mutmut_22`` (``ValidationError(_(None))``)."""
        with pytest.raises(ValidationError) as exc_info:
            _validate_new_value("pseudonym", "")

        assert exc_info.value.messages == ["Das Pseudonym darf nicht leer sein."]

    def test_zu_langer_wert_meldet_den_exakten_wortlaut(self):
        """Pins ``x__validate_new_value__mutmut_37`` (``ValidationError(None)``) und
        ``…__mutmut_38`` (``ValidationError(_(None))``)."""
        with pytest.raises(ValidationError) as exc_info:
            _validate_new_value("pseudonym", "x" * 101)

        assert exc_info.value.messages == ["Der Wert ist zu lang für dieses Feld."]

    def test_doppelter_offener_antrag_meldet_den_exakten_wortlaut(self, client_identified, staff_user):
        """Pins ``x_request_rectification__mutmut_35`` (``ValidationError(None, ...)``); die
        ``_(None)``-Variante derselben Stelle war bereits vor dieser Batch getötet."""
        request_rectification(client_identified, staff_user, "notes", "Erster Wunschwert", "Grund")

        with pytest.raises(ValidationError) as exc_info:
            request_rectification(client_identified, staff_user, "notes", "Zweiter Wunschwert", "Grund")

        assert exc_info.value.messages == ["Für dieses Feld läuft bereits ein Berichtigungsantrag."]

    def test_bereits_abgeschlossener_antrag_meldet_den_exakten_wortlaut(self, client_identified, staff_user, lead_user):
        """Pins ``x__guard_reviewable__mutmut_2`` (``ValidationError(None)``) und
        ``…__mutmut_3`` (``ValidationError(_(None))``)."""
        rr = request_rectification(client_identified, staff_user, "notes", "A", "Grund")
        approve_rectification(rr, lead_user)

        with pytest.raises(ValidationError) as exc_info:
            approve_rectification(rr, lead_user)

        assert exc_info.value.messages == ["Dieser Berichtigungsantrag ist bereits abgeschlossen."]

    def test_selbst_review_meldet_den_exakten_wortlaut(self, client_identified, lead_user):
        """Pins ``x__guard_reviewable__mutmut_10`` (``ValidationError(None)``); die
        ``_(None)``-Variante derselben Stelle war bereits vor dieser Batch getötet."""
        rr = request_rectification(client_identified, lead_user, "notes", "A", "Grund")

        with pytest.raises(ValidationError) as exc_info:
            approve_rectification(rr, lead_user)

        assert exc_info.value.messages == [
            "Sie können Ihren eigenen Berichtigungsantrag nicht prüfen (Vier-Augen-Prinzip)."
        ]

    def test_fehlende_leitungsrolle_meldet_den_exakten_wortlaut(self, facility, client_identified, staff_user):
        """Pins ``x__guard_reviewable__mutmut_16`` (``ValidationError(None)``) und
        ``…__mutmut_17`` (``ValidationError(_(None))``)."""
        other_staff = User.objects.create_user(username="pin099-zweitkraft", role=User.Role.STAFF, facility=facility)
        rr = request_rectification(client_identified, staff_user, "notes", "A", "Grund")

        with pytest.raises(ValidationError) as exc_info:
            approve_rectification(rr, other_staff)

        assert exc_info.value.messages == ["Nur die Leitung darf Berichtigungsanträge prüfen."]

    def test_genehmigung_einer_geloeschten_person_meldet_den_exakten_wortlaut(
        self, client_identified, staff_user, lead_user
    ):
        """Pins ``x_approve_rectification__mutmut_24``: ``raise
        ValidationError(CLIENT_WRITE_REVOKED_MESSAGE)`` wird zu ``raise ValidationError(None)``."""
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")
        client_identified.is_deleted = True
        client_identified.save(update_fields=["is_deleted"])

        with pytest.raises(ValidationError) as exc_info:
            approve_rectification(rr, lead_user)

        assert exc_info.value.messages == [str(CLIENT_WRITE_REVOKED_MESSAGE)]

    def test_klartext_konflikt_meldet_den_exakten_wortlaut(self, client_identified, staff_user, lead_user):
        """Pins ``x_approve_rectification__mutmut_38``: ``ValidationError(_(text))`` wird zu
        ``ValidationError(None, ...)``; die ``_(None)``-Variante derselben Stelle war bereits
        vor dieser Batch getötet."""
        rr = request_rectification(client_identified, staff_user, "notes", "Antragswert", "Grund")
        update_client(client_identified, lead_user, notes="Jemand anderes war schneller")

        with pytest.raises(ValidationError) as exc_info:
            approve_rectification(rr, lead_user)

        assert exc_info.value.messages == ["Der Wert wurde zwischenzeitlich geändert — bitte den Antrag neu stellen."]

    def test_pseudonym_kollision_meldet_den_exakten_wortlaut(self, facility, client_identified, staff_user, lead_user):
        """Pins ``x__apply_rectification__mutmut_19`` (``ValidationError(None, ...)``) und
        ``…__mutmut_20`` (``ValidationError(_(None), ...)``)."""
        Client.objects.create(facility=facility, pseudonym="Pin099-Client1-Belegt", created_by=staff_user)
        rr = request_rectification(client_identified, staff_user, "pseudonym", "Pin099-Client1-Belegt", "Grund")

        with pytest.raises(ValidationError) as exc_info:
            approve_rectification(rr, lead_user)

        assert exc_info.value.messages == ["Die Berichtigung konnte nicht angewendet werden (Wert bereits vergeben)."]

    def test_ablehnung_einer_geloeschten_person_meldet_den_exakten_wortlaut(
        self, client_identified, staff_user, lead_user
    ):
        """Pins ``x_reject_rectification__mutmut_20``: Spiegel von
        ``test_genehmigung_einer_geloeschten_person_meldet_den_exakten_wortlaut`` für den
        Ablehnungspfad."""
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")
        client_identified.is_deleted = True
        client_identified.save(update_fields=["is_deleted"])

        with pytest.raises(ValidationError) as exc_info:
            reject_rectification(rr, lead_user, "Vermerk")

        assert exc_info.value.messages == [str(CLIENT_WRITE_REVOKED_MESSAGE)]
