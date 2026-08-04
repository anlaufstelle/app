"""Datei-Anhaenge an Person und Fall ueber die Formular-Views (M24/AP4, Refs #1738).

Spiegel der Event-Anhang-View-Tests (``test_file_vault.py::TestFileUploadView``,
``test_attachment_versioning_stage_b.py``) fuer die beiden neuen Owner:

* Upload beim Anlegen (Staging VOR der Transaktion, Marker danach),
* Upload beim Bearbeiten (ADD), Ersetzen (REPLACE) und Entfernen (REMOVE),
* Re-Render eines ungueltigen Formulars listet Bestandsanhaenge weiter auf,
* Typwechsel verwirft die Versionsketten per Soft-Delete (ADR-039 §7).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Case, Client, DocumentType, DocumentTypeField, FieldCondition, FieldTemplate
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


def _fake_pdf(name):
    """``.pdf``-Endung, Klartext-Inhalt — passiert die Formular-Whitelist,
    faellt aber am Magic-Bytes-Check von ``prepare_encrypted_upload`` durch."""
    return SimpleUploadedFile(name, b"kein PDF, nur Text.\n" * 8, content_type="application/pdf")


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

    def test_type_switch_to_a_type_sharing_the_file_template_leaves_no_marker(
        self, client, staff_user, facility, client_doc_type_with_file
    ):
        """Geteilte FILE-Vorlage: kein Marker auf soft-geloeschte Ketten.

        Integrations-Review (Refs #1738): Feldvorlagen sind mehrfach
        zuordenbar. Traegt der ZIEL-Typ dieselbe FILE-Vorlage, stand ihr Slug
        in ``_file_field_slugs(doc_type)`` — ``preserve_file_markers`` holte den
        Marker des alten Typs also zurueck, waehrend
        ``discard_type_switch_file_chains`` im selben ``atomic`` alle Ketten
        soft-loeschte. Ergebnis war ein ``data_json``, das dauerhaft auf
        ``deleted_at``-Zeilen zeigt — und damit ein Widerspruch zu ADR-039 §7
        („Typwechsel verwirft das data_json").
        """
        dt, _ft_text, ft_file = client_doc_type_with_file
        shared = DocumentType.objects.create(
            facility=facility,
            name="Personenakte C",
            category=DocumentType.Category.ADMIN,
            entity_type=DocumentType.EntityType.CLIENT,
        )
        # Dieselbe Vorlage, zweite Zuordnung — derselbe Slug in beiden Typen.
        DocumentTypeField.objects.create(document_type=shared, field_template=ft_file, sort_order=0)

        person = Client.objects.create(
            facility=facility,
            pseudonym="Geteilte-Vorlage",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        base = {
            "pseudonym": "Geteilte-Vorlage",
            "contact_stage": Client.ContactStage.IDENTIFIED,
            "age_cluster": Client.AgeCluster.UNKNOWN,
            "notes": "",
        }
        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "document_type": str(dt.pk), "vermerk": "Text", "ausweis": _pdf("geteilt.pdf")},
        )
        attachment = Attachment.objects.get(client=person)

        response = client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {**base, "document_type": str(shared.pk)},
        )
        assert response.status_code == 302
        person.refresh_from_db()
        attachment.refresh_from_db()
        assert attachment.deleted_at is not None, "Kette haette soft-geloescht werden muessen"
        assert ft_file.slug not in person.data_json, f"Marker-Leiche im data_json: {person.data_json}"


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

    def test_type_switch_to_a_type_sharing_the_file_template_leaves_no_marker(
        self, client, staff_user, facility, client_identified, case_doc_type_with_file
    ):
        """Spiegel zum Personen-Fall: auch am Fall darf keine Marker-Leiche bleiben."""
        dt, _ft_text, ft_file = case_doc_type_with_file
        shared = DocumentType.objects.create(
            facility=facility,
            name="Fallakte B",
            category=DocumentType.Category.ADMIN,
            entity_type=DocumentType.EntityType.CASE,
        )
        DocumentTypeField.objects.create(document_type=shared, field_template=ft_file, sort_order=0)

        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Fall-Geteilt",
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        base = {
            "client": str(client_identified.pk),
            "title": "Fall-Geteilt",
            "description": "",
            "lead_user": "",
        }
        client.post(
            reverse("core:case_update", kwargs={"pk": case.pk}),
            {**base, "document_type": str(dt.pk), "sachstand": "Text", "bescheid": _pdf("fall_geteilt.pdf")},
        )
        attachment = Attachment.objects.get(case=case)

        response = client.post(
            reverse("core:case_update", kwargs={"pk": case.pk}),
            {**base, "document_type": str(shared.pk)},
        )
        assert response.status_code == 302
        case.refresh_from_db()
        attachment.refresh_from_db()
        assert attachment.deleted_at is not None, "Kette haette soft-geloescht werden muessen"
        assert ft_file.slug not in case.data_json, f"Marker-Leiche im data_json: {case.data_json}"


class TestCreateStagingLeavesNoOrphans:
    """Staging-/Unlink-Pfad der Anlage-Views (Refs #1345 owner-generisch, #1738).

    Scan + Fernet-Verschluesselung laufen bewusst VOR der Transaktion — die
    ``.enc`` liegt also schon auf Disk, bevor Person/Fall existieren. Scheitert
    eine SPAETERE Datei an der Policy, muss die View jede bereits gestagte
    Datei wieder entfernen; sonst waechst unter ``MEDIA_ROOT`` ein
    unreferenzierter, verschluesselter Rest, den kein Loeschpfad je erfasst
    (DSGVO-relevant: der Inhalt ist personenbezogen, die Metadaten fehlen).

    Ausgeloest ueber den Magic-Bytes-Check: ``.pdf``-Endung (Formular-Whitelist
    passiert) mit Klartext-Inhalt.
    """

    @staticmethod
    def _enc_files(media_root) -> list:
        return sorted(Path(media_root).rglob("*.enc"))

    @staticmethod
    def _multi_file_type(facility, entity_type, name, file_name):
        dt = DocumentType.objects.create(
            facility=facility,
            name=name,
            category=DocumentType.Category.ADMIN,
            entity_type=entity_type,
        )
        ft_file = FieldTemplate.objects.create(
            facility=facility, name=file_name, field_type=FieldTemplate.FieldType.FILE
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
        return dt, ft_file

    def test_client_create_unlinks_staged_files_on_policy_reject(
        self, client, staff_user, facility, settings, tmp_path
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        dt, ft_file = self._multi_file_type(
            facility, DocumentType.EntityType.CLIENT, "Personen-Mehrfachdatei", "Unterlagen"
        )
        before = self._enc_files(tmp_path)

        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_create"),
            {
                "pseudonym": "Staging-Person",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
                "document_type": str(dt.pk),
                ft_file.slug: [_pdf("echt.pdf"), _fake_pdf("unecht.pdf")],
            },
        )

        # Formular-Re-Render statt Redirect, Person nicht angelegt.
        assert response.status_code == 200
        assert not Client.objects.filter(pseudonym="Staging-Person").exists()
        assert not Attachment.objects.exists()
        # Und keine verwaiste .enc: die erste Datei war bereits gestagt.
        assert self._enc_files(tmp_path) == before

    def test_case_create_unlinks_staged_files_on_policy_reject(
        self, client, staff_user, facility, client_identified, settings, tmp_path
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        dt, ft_file = self._multi_file_type(facility, DocumentType.EntityType.CASE, "Fall-Mehrfachdatei", "Belege")
        before = self._enc_files(tmp_path)

        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_create"),
            {
                "client": str(client_identified.pk),
                "title": "Staging-Fall",
                "description": "",
                "lead_user": "",
                "document_type": str(dt.pk),
                ft_file.slug: [_pdf("echt2.pdf"), _fake_pdf("unecht2.pdf")],
            },
        )

        assert response.status_code == 200
        assert not Case.objects.filter(title="Staging-Fall").exists()
        assert not Attachment.objects.exists()
        assert self._enc_files(tmp_path) == before


class TestInvalidMetaFormKeepsFileMarkers:
    """E17 auf dem Ungueltig-Meta-Zweig (Integrations-Review, Refs #1738).

    Ist das META-Formular ungueltig (Pseudonym leer, Titel leer), rendert die
    View das Dynamik-Formular ueber ``_rerender_data_form`` GEBUNDEN neu — es
    validiert dabei also mit. Ohne ``existing_file_slugs`` sieht diese
    Validierung keinen Bestandsanhang: ein sichtbares, pflichtiges FILE-Ziel
    einer Feldbedingung bekommt einen Pflichtfehler angehaengt, obwohl die
    Datei laengst hochgeladen ist. Der Benutzer muesste sie erneut waehlen —
    exakt die E17-Regression, die der Valid-Zweig seit #1057 vermeidet.
    """

    @staticmethod
    def _file_condition_type(facility, entity_type, *, source_name, file_name):
        dt = DocumentType.objects.create(
            facility=facility,
            name=f"Bedingter Anhang {entity_type}",
            category=DocumentType.Category.ADMIN,
            entity_type=entity_type,
        )
        ft_source = FieldTemplate.objects.create(
            facility=facility, name=source_name, field_type=FieldTemplate.FieldType.TEXT
        )
        ft_file = FieldTemplate.objects.create(
            facility=facility, name=file_name, field_type=FieldTemplate.FieldType.FILE, is_required=True
        )
        source = DocumentTypeField.objects.create(document_type=dt, field_template=ft_source, sort_order=0)
        target = DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=1)
        FieldCondition.objects.create(
            target_field=target,
            depends_on_field=source,
            operator=FieldCondition.Operator.IS_NOT_EMPTY,
            group_index=0,
        )
        return dt, ft_source, ft_file

    def test_client_rerender_does_not_demand_the_existing_file_again(self, client, staff_user, facility):
        dt, ft_source, ft_file = self._file_condition_type(
            facility, DocumentType.EntityType.CLIENT, source_name="Anlass", file_name="Nachweis"
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Bedingt",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {
                "pseudonym": "Bedingt",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
                "document_type": str(dt.pk),
                ft_source.slug: "Grund",
                ft_file.slug: _pdf("bestand.pdf"),
            },
        )
        person.refresh_from_db()
        assert ft_file.slug in person.data_json

        # Zweiter POST: META ungueltig (Pseudonym leer), Datei-Feld leer.
        response = client.post(
            reverse("core:client_update", kwargs={"pk": person.pk}),
            {
                "pseudonym": "",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
                "document_type": str(dt.pk),
                ft_source.slug: "Grund",
            },
        )
        assert response.status_code == 200
        assert ft_file.slug not in response.context["data_form"].errors, (
            f"Unberechtigter Pflichtfehler am FILE-Ziel: {response.context['data_form'].errors}"
        )

    def test_case_rerender_does_not_demand_the_existing_file_again(
        self, client, staff_user, facility, client_identified
    ):
        dt, ft_source, ft_file = self._file_condition_type(
            facility, DocumentType.EntityType.CASE, source_name="Sachlage", file_name="Beleg"
        )
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Bedingter Fall",
            document_type=dt,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        client.post(
            reverse("core:case_update", kwargs={"pk": case.pk}),
            {
                "client": str(client_identified.pk),
                "title": "Bedingter Fall",
                "description": "",
                "lead_user": "",
                "document_type": str(dt.pk),
                ft_source.slug: "Grund",
                ft_file.slug: _pdf("fallbestand2.pdf"),
            },
        )
        case.refresh_from_db()
        assert ft_file.slug in case.data_json

        response = client.post(
            reverse("core:case_update", kwargs={"pk": case.pk}),
            {
                "client": str(client_identified.pk),
                "title": "",
                "description": "",
                "lead_user": "",
                "document_type": str(dt.pk),
                ft_source.slug: "Grund",
            },
        )
        assert response.status_code == 200
        assert ft_file.slug not in response.context["data_form"].errors, (
            f"Unberechtigter Pflichtfehler am FILE-Ziel: {response.context['data_form'].errors}"
        )


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
        # Integrations-Review-Befund: der Substring steht AUCH im erklaerenden
        # {% comment %}-Block — ein Ganzdatei-Check bliebe gruen, wenn das
        # Attribut nur noch dort stuende. Deshalb gezielt im <form ...>-Tag
        # pruefen (der Kommentar wird beim Rendern gestrippt).
        from pathlib import Path

        body = Path(template).read_text()
        form_start = body.index("<form")
        form_tag = body[form_start : body.index(">", form_start)]
        assert "data-offline-enctype-downgrade" in form_tag
        assert "js/event-form-enctype.js" in body
