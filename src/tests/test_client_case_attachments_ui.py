"""Datei-Anhaenge an Person und Fall ueber die Formular-Views (M24/AP4, Refs #1738).

Spiegel der Event-Anhang-View-Tests (``test_file_vault.py::TestFileUploadView``,
``test_attachment_versioning_stage_b.py``) fuer die beiden neuen Owner:

* Upload beim Anlegen (Staging VOR der Transaktion, Marker danach),
* Upload beim Bearbeiten (ADD), Ersetzen (REPLACE) und Entfernen (REMOVE),
* Re-Render eines ungueltigen Formulars listet Bestandsanhaenge weiter auf,
* Typwechsel verwirft die Versionsketten per Soft-Delete (ADR-039 §7).
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Case, Client, DocumentType, DocumentTypeField, FieldTemplate
from core.models.attachment import Attachment

pytestmark = pytest.mark.django_db

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _pdf(name):
    return SimpleUploadedFile(name, PDF_HEADER + name.encode(), content_type="application/pdf")


@pytest.fixture
def client_doc_type_with_file(facility):
    """Personen-Typ mit einem Text- und einem FILE-Feld."""
    dt = DocumentType.objects.create(
        facility=facility,
        name="Personenakte",
        category=DocumentType.Category.ADMIN,
        entity_type=DocumentType.EntityType.CLIENT,
    )
    ft_text = FieldTemplate.objects.create(facility=facility, name="Vermerk", field_type=FieldTemplate.FieldType.TEXT)
    ft_file = FieldTemplate.objects.create(facility=facility, name="Ausweis", field_type=FieldTemplate.FieldType.FILE)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_text, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=1)
    return dt, ft_text, ft_file


@pytest.fixture
def case_doc_type_with_file(facility):
    """Fall-Typ mit einem Text- und einem FILE-Feld."""
    dt = DocumentType.objects.create(
        facility=facility,
        name="Fallakte",
        category=DocumentType.Category.ADMIN,
        entity_type=DocumentType.EntityType.CASE,
    )
    ft_text = FieldTemplate.objects.create(facility=facility, name="Sachstand", field_type=FieldTemplate.FieldType.TEXT)
    ft_file = FieldTemplate.objects.create(facility=facility, name="Bescheid", field_type=FieldTemplate.FieldType.FILE)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_text, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=1)
    return dt, ft_text, ft_file


def _entries(owner, slug):
    return (owner.data_json or {}).get(slug, {}).get("entries", [])


class TestClientAttachmentForm:
    def test_create_client_with_file(self, client, staff_user, facility, client_doc_type_with_file):
        dt, _ft_text, ft_file = client_doc_type_with_file
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_create"),
            {
                "pseudonym": "Datei-Anlage",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
                "document_type": str(dt.pk),
                "vermerk": "Text",
                "ausweis": _pdf("anlage.pdf"),
            },
        )
        assert response.status_code == 302
        person = Client.objects.get(pseudonym="Datei-Anlage")
        marker = person.data_json["ausweis"]
        assert marker["__files__"] is True
        assert len(marker["entries"]) == 1
        attachment = Attachment.objects.get(client=person)
        assert attachment.field_template == ft_file
        # ADR-039 §2: die Facility kommt IMMER aus dem Owner.
        assert attachment.facility_id == person.facility_id

    def test_update_client_adds_file(self, client, staff_user, facility, client_doc_type_with_file):
        dt, _ft_text, _ft_file = client_doc_type_with_file
        person = Client.objects.create(
            facility=facility,
            pseudonym="Datei-Update",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {
                "pseudonym": "Datei-Update",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
                "document_type": str(dt.pk),
                "vermerk": "Text",
                "ausweis": _pdf("nachtrag.pdf"),
            },
        )
        assert response.status_code == 302
        person.refresh_from_db()
        assert len(_entries(person, "ausweis")) == 1
        assert Attachment.objects.filter(client=person, deleted_at__isnull=True).count() == 1

    def test_text_edit_keeps_existing_attachment(self, client, staff_user, facility, client_doc_type_with_file):
        """``preserve_file_markers``: der erste reine Text-Edit darf den
        Bestandsanhang nicht still aus dem ``data_json`` werfen."""
        dt, _ft_text, _ft_file = client_doc_type_with_file
        person = Client.objects.create(
            facility=facility,
            pseudonym="Text-Edit",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        base = {
            "pseudonym": "Text-Edit",
            "contact_stage": Client.ContactStage.IDENTIFIED,
            "age_cluster": Client.AgeCluster.UNKNOWN,
            "notes": "",
            "document_type": str(dt.pk),
        }
        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "vermerk": "erste Fassung", "ausweis": _pdf("bestand.pdf")},
        )
        person.refresh_from_db()
        entry_ids = [e["id"] for e in _entries(person, "ausweis")]

        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "vermerk": "nur Text geaendert"},
        )
        person.refresh_from_db()
        assert [e["id"] for e in _entries(person, "ausweis")] == entry_ids

    def test_replace_supersedes_the_chain(self, client, staff_user, facility, client_doc_type_with_file):
        dt, _ft_text, _ft_file = client_doc_type_with_file
        person = Client.objects.create(
            facility=facility,
            pseudonym="Ersetzen",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        base = {
            "pseudonym": "Ersetzen",
            "contact_stage": Client.ContactStage.IDENTIFIED,
            "age_cluster": Client.AgeCluster.UNKNOWN,
            "notes": "",
            "document_type": str(dt.pk),
            "vermerk": "Text",
        }
        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "ausweis": _pdf("alt.pdf")},
        )
        person.refresh_from_db()
        old = Attachment.objects.get(client=person)

        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, f"ausweis__replace__{old.entry_id}": _pdf("neu.pdf")},
        )
        person.refresh_from_db()
        old.refresh_from_db()
        new = Attachment.objects.get(client=person, is_current=True)
        assert old.is_current is False
        assert old.superseded_by_id == new.pk
        assert new.entry_id == old.entry_id
        assert [e["id"] for e in _entries(person, "ausweis")] == [str(new.pk)]

    def test_remove_soft_deletes_and_drops_marker(self, client, staff_user, facility, client_doc_type_with_file):
        dt, _ft_text, _ft_file = client_doc_type_with_file
        person = Client.objects.create(
            facility=facility,
            pseudonym="Entfernen",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        base = {
            "pseudonym": "Entfernen",
            "contact_stage": Client.ContactStage.IDENTIFIED,
            "age_cluster": Client.AgeCluster.UNKNOWN,
            "notes": "",
            "document_type": str(dt.pk),
            "vermerk": "Text",
        }
        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "ausweis": _pdf("weg.pdf")},
        )
        person.refresh_from_db()
        attachment = Attachment.objects.get(client=person)

        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "ausweis__remove": str(attachment.entry_id)},
        )
        person.refresh_from_db()
        attachment.refresh_from_db()
        assert attachment.deleted_at is not None
        assert "ausweis" not in person.data_json

    def test_invalid_form_rerender_lists_existing_attachment(
        self, client, staff_user, facility, client_doc_type_with_file, client_identified
    ):
        """Refs #1056 (P4)-Lehre: ohne ``existing_attachments`` in JEDEM
        Render-Zweig verschwaende die Anhang-Zeile beim Re-Render."""
        dt, _ft_text, _ft_file = client_doc_type_with_file
        person = Client.objects.create(
            facility=facility,
            pseudonym="Re-Render",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        base = {
            "pseudonym": "Re-Render",
            "contact_stage": Client.ContactStage.IDENTIFIED,
            "age_cluster": Client.AgeCluster.UNKNOWN,
            "notes": "",
            "document_type": str(dt.pk),
            "vermerk": "Text",
        }
        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "ausweis": _pdf("bleibt.pdf")},
        )
        # Pseudonym-Kollision => Formular ungueltig => Re-Render.
        response = client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "pseudonym": client_identified.pseudonym},
        )
        assert response.status_code == 200
        assert "bleibt.pdf" in response.content.decode()

    def test_type_switch_discards_chains(self, client, staff_user, facility, client_doc_type_with_file):
        """ADR-039 §7: Typwechsel soft-loescht die Ketten (keine Waisen)."""
        dt, _ft_text, _ft_file = client_doc_type_with_file
        other = DocumentType.objects.create(
            facility=facility,
            name="Personenakte B",
            category=DocumentType.Category.ADMIN,
            entity_type=DocumentType.EntityType.CLIENT,
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Typwechsel",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        base = {
            "pseudonym": "Typwechsel",
            "contact_stage": Client.ContactStage.IDENTIFIED,
            "age_cluster": Client.AgeCluster.UNKNOWN,
            "notes": "",
        }
        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "document_type": str(dt.pk), "vermerk": "Text", "ausweis": _pdf("verworfen.pdf")},
        )
        attachment = Attachment.objects.get(client=person)

        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "document_type": str(other.pk)},
        )
        person.refresh_from_db()
        attachment.refresh_from_db()
        assert attachment.deleted_at is not None
        assert "ausweis" not in person.data_json


class TestCaseAttachmentForm:
    def test_create_case_with_file(self, client, staff_user, facility, client_identified, case_doc_type_with_file):
        dt, _ft_text, ft_file = case_doc_type_with_file
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_create"),
            {
                "client": str(client_identified.pk),
                "title": "Fall mit Anlage",
                "description": "",
                "lead_user": "",
                "document_type": str(dt.pk),
                "sachstand": "Text",
                "bescheid": _pdf("fallanlage.pdf"),
            },
        )
        assert response.status_code == 302
        case = Case.objects.get(title="Fall mit Anlage")
        assert case.data_json["bescheid"]["__files__"] is True
        attachment = Attachment.objects.get(case=case)
        assert attachment.field_template == ft_file
        assert attachment.facility_id == case.facility_id

    def test_update_case_adds_and_removes_file(
        self, client, staff_user, facility, client_identified, case_doc_type_with_file
    ):
        dt, _ft_text, _ft_file = case_doc_type_with_file
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Fall-Update",
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        base = {
            "client": str(client_identified.pk),
            "title": "Fall-Update",
            "description": "",
            "lead_user": "",
            "document_type": str(dt.pk),
            "sachstand": "Text",
        }
        client.post(
            reverse("core:case_update", kwargs={"pk": case.pk}),
            {**base, "bescheid": _pdf("fall.pdf")},
        )
        case.refresh_from_db()
        assert len(_entries(case, "bescheid")) == 1
        attachment = Attachment.objects.get(case=case)

        client.post(
            reverse("core:case_update", kwargs={"pk": case.pk}),
            {**base, "bescheid__remove": str(attachment.entry_id)},
        )
        case.refresh_from_db()
        attachment.refresh_from_db()
        assert attachment.deleted_at is not None
        assert "bescheid" not in case.data_json

    def test_text_edit_keeps_existing_attachment(
        self, client, staff_user, facility, client_identified, case_doc_type_with_file
    ):
        dt, _ft_text, _ft_file = case_doc_type_with_file
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Fall-Text",
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        base = {
            "client": str(client_identified.pk),
            "title": "Fall-Text",
            "description": "",
            "lead_user": "",
            "document_type": str(dt.pk),
        }
        client.post(
            reverse("core:case_update", kwargs={"pk": case.pk}),
            {**base, "sachstand": "erste Fassung", "bescheid": _pdf("fallbestand.pdf")},
        )
        case.refresh_from_db()
        entry_ids = [e["id"] for e in _entries(case, "bescheid")]

        client.post(
            reverse("core:case_update", kwargs={"pk": case.pk}),
            {**base, "sachstand": "nur Text"},
        )
        case.refresh_from_db()
        assert [e["id"] for e in _entries(case, "bescheid")] == entry_ids


FORM_TEMPLATES = ["src/templates/core/clients/form.html", "src/templates/core/cases/form.html"]


class TestFormTemplatesAreMultipart:
    """Ohne ``enctype="multipart/form-data"`` kaeme keine Datei am Server an."""

    @pytest.mark.parametrize("template", FORM_TEMPLATES)
    def test_form_declares_multipart(self, template):
        from pathlib import Path

        assert 'enctype="multipart/form-data"' in Path(template).read_text()


class TestFormTemplatesCarryOfflineDowngrade:
    """AP5 (Refs #1738, ADR-039 §8): inertes Enctype-Downgrade an beiden Formularen.

    Personen-/Fall-Formulare sind bewusst **nicht** queuebar (multipart, siehe
    Warnkommentar an ``QUEUE_PATTERNS``). Attribut und Script liegen trotzdem
    an, damit eine spaetere Queuebarkeit nicht an einem vergessenen Detail
    scheitert — und damit dieses Zukunftsversprechen nicht still aus den
    Templates verschwindet, pinnt dieser Guard es.
    """

    @pytest.mark.parametrize("template", FORM_TEMPLATES)
    def test_form_carries_downgrade_attribute(self, template):
        from pathlib import Path

        body = Path(template).read_text()
        assert "data-offline-enctype-downgrade" in body
        assert "js/event-form-enctype.js" in body
