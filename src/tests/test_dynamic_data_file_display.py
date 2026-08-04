"""FILE-Felder in der Read-only-Detailanzeige (M24/AP4, Refs #1738).

``build_dynamic_fields_display`` hatte keinen FILE-Zweig — bis M24 folgenlos,
weil Datei-Felder event-gebunden waren (E8). Seit ADR-039 tragen Personen und
Faelle Anhaenge; ohne den Zweig laesse die Detailseite das rohe
``{"__files__": …}``-Dict als Feldwert stehen.

Gepinnt wird:

* Personen- und Fall-Detail zeigen Dateiname, Groesse und Download-Link ueber
  die owner-eigene Route,
* das Sensitivity-Gate laeuft unveraendert **davor** (restringiertes Feld nennt
  keinen Dateinamen),
* ohne ``owner`` wird ein Marker gar nicht angezeigt statt roh gerendert,
* verwaiste FILE-Slugs (Refs #1725) nehmen denselben Zweig.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Case, Client, DocumentType, DocumentTypeField, FieldTemplate
from core.services.dynamic_data import build_dynamic_data_context
from core.services.file_vault import store_encrypted_file

pytestmark = pytest.mark.django_db

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _attach(facility, owner, ft, user, filename, *, supersedes=None):
    upload = SimpleUploadedFile(filename, PDF_HEADER + filename.encode(), content_type="application/pdf")
    return store_encrypted_file(facility, upload, ft, owner, user, supersedes=supersedes)


def _set_marker(owner, slug, attachments):
    owner.data_json = dict(owner.data_json or {})
    owner.data_json[slug] = {
        "__files__": True,
        "entries": [{"id": str(a.pk), "sort": i} for i, a in enumerate(attachments)],
    }
    owner.save(update_fields=["data_json"])


@pytest.fixture
def person_with_file(facility, staff_user):
    dt = DocumentType.objects.create(
        facility=facility,
        name="Anzeige-Personentyp",
        category=DocumentType.Category.ADMIN,
        entity_type=DocumentType.EntityType.CLIENT,
    )
    ft = FieldTemplate.objects.create(facility=facility, name="Nachweis", field_type=FieldTemplate.FieldType.FILE)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
    person = Client.objects.create(
        facility=facility,
        pseudonym="Anzeige-Person",
        contact_stage=Client.ContactStage.IDENTIFIED,
        document_type=dt,
        created_by=staff_user,
    )
    attachment = _attach(facility, person, ft, staff_user, "nachweis.pdf")
    _set_marker(person, "nachweis", [attachment])
    return person, attachment, dt, ft


@pytest.fixture
def case_with_file(facility, staff_user, client_identified):
    dt = DocumentType.objects.create(
        facility=facility,
        name="Anzeige-Falltyp",
        category=DocumentType.Category.ADMIN,
        entity_type=DocumentType.EntityType.CASE,
    )
    ft = FieldTemplate.objects.create(facility=facility, name="Fallbeleg", field_type=FieldTemplate.FieldType.FILE)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
    case = Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Anzeige-Fall",
        document_type=dt,
        created_by=staff_user,
    )
    attachment = _attach(facility, case, ft, staff_user, "fallbeleg.pdf")
    _set_marker(case, "fallbeleg", [attachment])
    return case, attachment, dt, ft


class TestClientDetailFileDisplay:
    def test_detail_shows_filename_and_owner_download_link(self, client, staff_user, person_with_file):
        person, attachment, _dt, _ft = person_with_file
        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": person.pk}))
        content = response.content.decode()
        assert "nachweis.pdf" in content
        assert (
            reverse(
                "core:client_attachment_download",
                kwargs={"pk": person.pk, "attachment_pk": attachment.pk},
            )
            in content
        )

    def test_raw_marker_dict_never_reaches_the_page(self, client, staff_user, person_with_file):
        person, _attachment, _dt, _ft = person_with_file
        client.force_login(staff_user)
        content = client.get(reverse("core:client_detail", kwargs={"pk": person.pk})).content.decode()
        assert "__files__" not in content

    def test_context_entry_is_flagged_as_file(self, staff_user, person_with_file):
        person, attachment, dt, _ft = person_with_file
        context = build_dynamic_data_context(dt, person.data_json, staff_user, owner=person)
        entry = context["dynamic_fields"][0]
        assert entry["is_file"] is True
        assert entry["entries"][0]["original_filename"] == "nachweis.pdf"
        assert entry["entries"][0]["download_url"] == reverse(
            "core:client_attachment_download",
            kwargs={"pk": person.pk, "attachment_pk": attachment.pk},
        )

    def test_prior_versions_are_listed(self, facility, staff_user, person_with_file):
        person, first, dt, ft = person_with_file
        second = _attach(facility, person, ft, staff_user, "nachweis_v2.pdf", supersedes=first)
        _set_marker(person, "nachweis", [second])
        context = build_dynamic_data_context(dt, person.data_json, staff_user, owner=person)
        prior = context["dynamic_fields"][0]["entries"][0]["prior_versions"]
        assert [p["original_filename"] for p in prior] == ["nachweis.pdf"]

    def test_field_sensitivity_gate_runs_before_the_file_branch(self, client, staff_user, person_with_file):
        """Ein HIGH-Feld nennt weder Dateinamen noch Download-Link."""
        person, _attachment, dt, ft = person_with_file
        ft.sensitivity = DocumentType.Sensitivity.HIGH
        ft.save(update_fields=["sensitivity"])
        context = build_dynamic_data_context(dt, person.data_json, staff_user, owner=person)
        entry = context["dynamic_fields"][0]
        assert entry["restricted"] is True
        assert "entries" not in entry

        client.force_login(staff_user)
        content = client.get(reverse("core:client_detail", kwargs={"pk": person.pk})).content.decode()
        assert "nachweis.pdf" not in content

    def test_soft_deleted_attachment_is_not_displayed(self, client, staff_user, person_with_file):
        from django.utils import timezone

        person, attachment, _dt, _ft = person_with_file
        attachment.deleted_at = timezone.now()
        attachment.save(update_fields=["deleted_at"])
        client.force_login(staff_user)
        content = client.get(reverse("core:client_detail", kwargs={"pk": person.pk})).content.decode()
        assert "nachweis.pdf" not in content

    def test_without_owner_the_marker_is_skipped(self, staff_user, person_with_file):
        """Ohne Owner gibt es keine aufloesbaren Anhaenge — dann lieber gar
        nichts anzeigen als das rohe Marker-Dict."""
        person, _attachment, dt, _ft = person_with_file
        context = build_dynamic_data_context(dt, person.data_json, staff_user)
        assert context["dynamic_fields"] == []


class TestCaseDetailFileDisplay:
    def test_detail_shows_filename_and_owner_download_link(self, client, staff_user, case_with_file):
        case, attachment, _dt, _ft = case_with_file
        client.force_login(staff_user)
        content = client.get(reverse("core:case_detail", kwargs={"pk": case.pk})).content.decode()
        assert "fallbeleg.pdf" in content
        assert (
            reverse(
                "core:case_attachment_download",
                kwargs={"pk": case.pk, "attachment_pk": attachment.pk},
            )
            in content
        )
        assert "__files__" not in content


class TestOrphanFileDisplay:
    def test_orphaned_file_slug_uses_the_file_branch(self, client, staff_user, person_with_file):
        """Refs #1725: Slug nicht mehr am Typ zugeordnet — der Wert bleibt
        gespeichert und muss trotzdem als Datei (nicht als Roh-Dict) erscheinen."""
        person, attachment, dt, ft = person_with_file
        DocumentTypeField.objects.filter(document_type=dt, field_template=ft).delete()
        context = build_dynamic_data_context(dt, person.data_json, staff_user, owner=person)
        assert context["dynamic_fields"] == []
        orphan = context["dynamic_orphan_fields"][0]
        assert orphan["is_file"] is True
        assert orphan["orphaned"] is True
        assert orphan["entries"][0]["original_filename"] == "nachweis.pdf"

        client.force_login(staff_user)
        content = client.get(reverse("core:client_detail", kwargs={"pk": person.pk})).content.decode()
        assert "nachweis.pdf" in content
        assert "__files__" not in content
