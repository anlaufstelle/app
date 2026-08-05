"""Typwechsel-Rueckfrage nennt Feldwerte UND Dateien (M24/AP4, Refs #1738/#1733).

Der Dialog aus #1733 bezifferte bislang nur ``count_filled_dynamic_values``.
Seit ADR-039 koennen Personen und Faelle Datei-Anhaenge tragen, die ein
Typwechsel ebenfalls verwirft (soft-delete,
:func:`~core.services.attachments.discard_type_switch_file_chains`).

Semantik, hier gepinnt:

* ein ``__files__``/``__file__``-Marker-Slug ist **kein Feldwert** — er faellt
  aus der Wertzaehlung heraus,
* Dateien werden separat als **Ketten** gezaehlt (nicht als Einzelversionen),
  exakt die Zahl, die ``discard_type_switch_file_chains`` zurueckgibt,
* der Kontext-Key ist ``None``, wenn nichts zu verlieren ist — nur dann
  bleiben Confirm und Warnbox aus.
"""

from __future__ import annotations

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Case, Client, DocumentType, DocumentTypeField, FieldTemplate
from core.services.file_vault import store_encrypted_file

pytestmark = pytest.mark.django_db

#: Traegersatz der Rueckfrage bzw. der Warnbox — beide muessen erscheinen,
#: sobald IRGENDEIN Risiko besteht (Refs #1738, Integrations-Review).
CONFIRM_LEAD = "Dokumentationstyp wechseln oder entfernen?"
HINT_LEAD = "Achtung: Wird der Typ gewechselt oder entfernt"

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


@pytest.fixture
def client_type_file_only(facility):
    """Personen-Typ mit AUSSCHLIESSLICH einem FILE-Feld."""
    dt = DocumentType.objects.create(
        facility=facility,
        name="Nur-Datei-Typ",
        entity_type=DocumentType.EntityType.CLIENT,
    )
    ft = FieldTemplate.objects.create(facility=facility, name="Anlage", field_type=FieldTemplate.FieldType.FILE)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
    return dt, ft


@pytest.fixture
def case_type_file_and_text(facility):
    dt = DocumentType.objects.create(
        facility=facility,
        name="Fall-Datei-Typ",
        entity_type=DocumentType.EntityType.CASE,
    )
    ft_text = FieldTemplate.objects.create(facility=facility, name="Notizfeld", field_type=FieldTemplate.FieldType.TEXT)
    ft_file = FieldTemplate.objects.create(facility=facility, name="Beleg", field_type=FieldTemplate.FieldType.FILE)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_text, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=1)
    return dt, ft_text, ft_file


def _attach(facility, owner, ft, user, filename, *, supersedes=None):
    upload = SimpleUploadedFile(filename, PDF_HEADER + filename.encode(), content_type="application/pdf")
    return store_encrypted_file(facility, upload, ft, owner, user, supersedes=supersedes)


def _confirm_text(content: str) -> str:
    """Text des ``data-confirm-discard``-Attributs (HTML-entschaerft)."""
    match = re.search(r'data-confirm-discard="([^"]*)"', content)
    assert match, "Kein data-confirm-discard im Formular"
    return match.group(1).replace("&#x27;", "'").replace("&amp;", "&").replace("&quot;", '"')


def _hint_text(content: str) -> str:
    """Text der Warnbox am Typ-Select."""
    match = re.search(
        r'data-testid="doc-type-switch-hint".*?<p[^>]*>(.*?)</p>',
        content,
        re.DOTALL,
    )
    assert match, "Keine Warnbox am Typ-Select"
    return " ".join(match.group(1).split())


def _set_marker(owner, slug, attachments):
    owner.data_json = dict(owner.data_json or {})
    owner.data_json[slug] = {
        "__files__": True,
        "entries": [{"id": str(a.pk), "sort": i} for i, a in enumerate(attachments)],
    }
    owner.save(update_fields=["data_json"])


class TestClientConfirmCounts:
    def test_file_marker_alone_counts_zero_values_and_one_file(
        self, client, staff_user, facility, client_type_file_only
    ):
        """Ein gefuellter Datei-Marker allein: 0 Feldwerte, 1 Datei."""
        dt, ft = client_type_file_only
        person = Client.objects.create(
            facility=facility,
            pseudonym="Nur-Datei",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        _set_marker(person, "anlage", [_attach(facility, person, ft, staff_user, "allein.pdf")])

        client.force_login(staff_user)
        response = client.get(reverse("core:client_update", kwargs={"pk": person.pk}))
        assert response.status_code == 200
        at_risk = response.context["dynamic_values_at_risk"]
        assert at_risk == {"field_values": 0, "files": 1}

    def test_confirm_and_hint_name_both_numbers(self, client, staff_user, facility, client_type_file_only):
        dt, ft = client_type_file_only
        ft_text = FieldTemplate.objects.create(
            facility=facility, name="Beischrift", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_text, sort_order=1)
        person = Client.objects.create(
            facility=facility,
            pseudonym="Beides",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            data_json={"beischrift": "Text"},
            created_by=staff_user,
        )
        _set_marker(person, "anlage", [_attach(facility, person, ft, staff_user, "beides.pdf")])

        client.force_login(staff_user)
        response = client.get(reverse("core:client_update", kwargs={"pk": person.pk}))
        content = response.content.decode()
        assert response.context["dynamic_values_at_risk"] == {"field_values": 1, "files": 1}
        assert "data-confirm-discard=" in content
        assert "1 Zusatzfeld" in content
        assert "1 Datei" in content

    def test_files_only_keeps_the_carrier_sentence(self, client, staff_user, facility, client_type_file_only):
        """Nur-Datei-Typ: Rueckfrage und Warnbox nennen den Typwechsel.

        Integrations-Review (Refs #1738): Traegersatz und Zahlensatz hingen
        beide am ``field_values``-Zweig. Bei ``{"field_values": 0, "files": n}``
        — ein regulaer erreichbarer Zustand, siehe ``client_type_file_only`` —
        blieb nur das zusammenhanglose „Außerdem wird 1 Datei entfernt." stehen:
        der Benutzer erfuhr weder, WORUEBER er entscheidet, noch dass die
        Entscheidung unumkehrbar ist.
        """
        dt, ft = client_type_file_only
        person = Client.objects.create(
            facility=facility,
            pseudonym="Nur-Datei-Text",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        _set_marker(person, "anlage", [_attach(facility, person, ft, staff_user, "traeger.pdf")])

        client.force_login(staff_user)
        response = client.get(reverse("core:client_update", kwargs={"pk": person.pk}))
        assert response.context["dynamic_values_at_risk"] == {"field_values": 0, "files": 1}

        confirm = _confirm_text(response.content.decode())
        assert confirm.startswith(CONFIRM_LEAD), confirm
        assert "1 hinterlegte Datei" in confirm
        # Der Anschlusssatz ergibt ohne Vorsatz keinen Sinn — er darf hier
        # nicht der ganze Text sein.
        assert not confirm.startswith("Außerdem"), confirm

        hint = _hint_text(response.content.decode())
        assert hint.startswith(HINT_LEAD), hint
        assert "1 hinterlegte Datei" in hint

    def test_field_values_only_keeps_the_existing_wording(self, client, staff_user, facility):
        """Bestandsfall ohne Dateien: unveraenderter Wortlaut."""
        dt = DocumentType.objects.create(
            facility=facility,
            name="Nur-Text-Typ",
            entity_type=DocumentType.EntityType.CLIENT,
        )
        ft_text = FieldTemplate.objects.create(
            facility=facility, name="Beischrift", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_text, sort_order=0)
        person = Client.objects.create(
            facility=facility,
            pseudonym="Nur-Text",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            data_json={"beischrift": "Text"},
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:client_update", kwargs={"pk": person.pk}))
        assert response.context["dynamic_values_at_risk"] == {"field_values": 1, "files": 0}

        confirm = _confirm_text(response.content.decode())
        assert confirm == (
            "Dokumentationstyp wechseln oder entfernen? Der gespeicherte Wert von 1 Zusatzfeld "
            "wird beim Speichern verworfen und lässt sich nicht wiederherstellen."
        )
        assert _hint_text(response.content.decode()) == (
            "Achtung: Wird der Typ gewechselt oder entfernt, wird der gespeicherte Wert von "
            "1 Zusatzfeld verworfen — er lässt sich nicht wiederherstellen."
        )

    def test_versions_count_as_one_chain(self, client, staff_user, facility, client_type_file_only):
        """Ersetzte Vorversionen sind dieselbe Kette — der Dialog zaehlt Ketten
        (``discard_type_switch_file_chains``-Semantik), nicht Einzelversionen."""
        dt, ft = client_type_file_only
        person = Client.objects.create(
            facility=facility,
            pseudonym="Kette",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        first = _attach(facility, person, ft, staff_user, "v1.pdf")
        second = _attach(facility, person, ft, staff_user, "v2.pdf", supersedes=first)
        _set_marker(person, "anlage", [second])

        client.force_login(staff_user)
        response = client.get(reverse("core:client_update", kwargs={"pk": person.pk}))
        assert response.context["dynamic_values_at_risk"]["files"] == 1

    def test_nothing_at_risk_is_none(self, client, staff_user, facility, client_type_file_only):
        dt, _ft = client_type_file_only
        person = Client.objects.create(
            facility=facility,
            pseudonym="Leer",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:client_update", kwargs={"pk": person.pk}))
        assert response.context["dynamic_values_at_risk"] is None
        assert "data-confirm-discard=" not in response.content.decode()


class TestOriginalDocumentTypeAttribute:
    """Refs #1776: der Confirm haengt seit dem #1758-Fix faelschlich am blossen
    Nachladen des Feldsets statt am tatsaechlichen Wertverlust — ein Weg-und-
    wieder-zurueck-Wechsel des Dokumentationstyps loeste die Rueckfrage aus,
    obwohl der Rueckweg das Feldset (seit #1758) wieder mit den Original-
    Werten befuellt und nichts verworfen wird.

    Die Behebung sitzt in ``confirmDynamicDataLoss``
    (``static/js/alpine/forms.js``): sie vergleicht den Select-Wert beim
    Submit mit dem hier gepinnten ``data-original-document-type``-Attribut
    und laesst den Confirm nur, wenn beide voneinander abweichen. Pytest kann
    die JS-Laufzeitlogik nicht ausfuehren (kein Browser) — hier wird nur der
    Markup-Vertrag gepinnt, den die Komponente liest.
    """

    def test_client_form_carries_the_stored_type_as_original_value(
        self, client, staff_user, facility, client_type_file_only
    ):
        dt, ft = client_type_file_only
        person = Client.objects.create(
            facility=facility,
            pseudonym="Original-Wert",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        _set_marker(person, "anlage", [_attach(facility, person, ft, staff_user, "original.pdf")])

        client.force_login(staff_user)
        response = client.get(reverse("core:client_update", kwargs={"pk": person.pk}))
        content = response.content.decode()

        assert f'data-original-document-type="{dt.pk}"' in content

    def test_case_form_carries_the_stored_type_as_original_value(
        self, client, staff_user, facility, case_type_file_and_text
    ):
        dt, _ft_text, ft_file = case_type_file_and_text
        case = Case.objects.create(
            facility=facility,
            client=Client.objects.create(
                facility=facility,
                pseudonym="Original-Wert-Fall",
                contact_stage=Client.ContactStage.IDENTIFIED,
                created_by=staff_user,
            ),
            title="Fall mit Original-Attribut",
            document_type=dt,
            data_json={"notizfeld": "Text"},
            created_by=staff_user,
        )
        _set_marker(case, "beleg", [_attach(facility, case, ft_file, staff_user, "beleg.pdf")])

        client.force_login(staff_user)
        response = client.get(reverse("core:case_update", kwargs={"pk": case.pk}))
        content = response.content.decode()

        assert f'data-original-document-type="{dt.pk}"' in content

    def test_client_invalid_post_keeps_the_stored_type_not_the_posted_one(
        self, client, staff_user, facility, client_type_file_only
    ):
        """Regression (Refs #1785, Review): im Re-Render nach ungueltigem POST
        hat ``ModelForm._post_clean`` ``instance.document_type`` bereits auf den
        GEPOSTETEN Typ gesetzt. Laese das Template den Ausgangswert aus der
        Instanz, stuende dort der Versuchs-Typ — und ein B->C->B-Wechsel auf der
        re-gerenderten Seite unterdrueckte die Verlust-Rueckfrage. Die Baseline
        muss der gespeicherte Typ bleiben.
        """
        dt_old, ft = client_type_file_only
        dt_new = DocumentType.objects.create(
            facility=facility, name="Anderer-Typ", entity_type=DocumentType.EntityType.CLIENT
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Stammt-von-dt-old",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt_old,
            created_by=staff_user,
        )
        # Ein Wert am alten Typ, damit die Rueckfrage (und mit ihr das
        # ``data-original-document-type``-Attribut) ueberhaupt rendert.
        _set_marker(person, "anlage", [_attach(facility, person, ft, staff_user, "original.pdf")])
        # Namenskollision erzwingt den ungueltigen MAIN-Form-Zweig, waehrend das
        # gueltige document_type=dt_new die Instanz ueber _post_clean mutiert.
        Client.objects.create(
            facility=facility, pseudonym="Kollision", contact_stage=Client.ContactStage.IDENTIFIED
        )

        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {
                "pseudonym": "Kollision",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
                "document_type": str(dt_new.pk),
            },
        )
        content = response.content.decode()

        assert response.status_code == 200
        assert f'data-original-document-type="{dt_old.pk}"' in content
        assert f'data-original-document-type="{dt_new.pk}"' not in content


class TestCaseConfirmCounts:
    def test_case_counts_values_and_files_separately(self, client, staff_user, facility, case_type_file_and_text):
        dt, _ft_text, ft_file = case_type_file_and_text
        case = Case.objects.create(
            facility=facility,
            client=Client.objects.create(
                facility=facility,
                pseudonym="Fall-Person",
                contact_stage=Client.ContactStage.IDENTIFIED,
                created_by=staff_user,
            ),
            title="Fall mit Beleg",
            document_type=dt,
            data_json={"notizfeld": "Text"},
            created_by=staff_user,
        )
        _set_marker(
            case,
            "beleg",
            [
                _attach(facility, case, ft_file, staff_user, "b1.pdf"),
                _attach(facility, case, ft_file, staff_user, "b2.pdf"),
            ],
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:case_update", kwargs={"pk": case.pk}))
        assert response.context["dynamic_values_at_risk"] == {"field_values": 1, "files": 2}
        content = response.content.decode()
        assert "1 Zusatzfeld" in content
        assert "2 Dateien" in content

    def test_case_files_only_keeps_the_carrier_sentence(self, client, staff_user, facility, case_type_file_and_text):
        """Spiegel zum Personen-Fall: auch das Fall-Formular braucht den Traeger."""
        dt, _ft_text, ft_file = case_type_file_and_text
        case = Case.objects.create(
            facility=facility,
            client=Client.objects.create(
                facility=facility,
                pseudonym="Fall-Person-2",
                contact_stage=Client.ContactStage.IDENTIFIED,
                created_by=staff_user,
            ),
            title="Fall nur mit Beleg",
            document_type=dt,
            created_by=staff_user,
        )
        _set_marker(case, "beleg", [_attach(facility, case, ft_file, staff_user, "nur_beleg.pdf")])

        client.force_login(staff_user)
        response = client.get(reverse("core:case_update", kwargs={"pk": case.pk}))
        assert response.context["dynamic_values_at_risk"] == {"field_values": 0, "files": 1}

        confirm = _confirm_text(response.content.decode())
        assert confirm.startswith(CONFIRM_LEAD), confirm
        assert "1 hinterlegte Datei" in confirm
        assert _hint_text(response.content.decode()).startswith(HINT_LEAD)
