"""Lösch-Blocker weisen Papierkorb-Anteile aus (Refs #1737, Option A).

Die M2-Blocker zählen über den Default-Manager, der ``is_deleted`` bewusst
nicht filtert — soft-gelöschte Personen blockieren damit Feldvorlagen UND
Dokumentationstypen. Die Meldung verlangte dafür einen unmöglichen Schritt
(„müsste zuerst geändert werden"), ohne Papierkorb zu erwähnen und ohne
Ausweg zu nennen.
"""

import pytest
from django.urls import reverse

from core.models import Client, DocumentType, FieldTemplate
from core.services import config as config_service
from core.services.config import (
    ConfigDeleteBlocked,
    delete_document_type,
    field_template_delete_blockers,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_doc_type(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Personenbogen",
        slug="personenbogen",
        entity_type=DocumentType.EntityType.CLIENT,
    )


@pytest.fixture
def slug_template(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Feld X",
        slug="feld-x",
        field_type=FieldTemplate.FieldType.TEXT,
    )


def _make_client(facility, doc_type, *, slug=None, deleted=False, pseudonym="P-Blocker"):
    obj = Client.objects.create(
        facility=facility,
        pseudonym=pseudonym,
        document_type=doc_type,
        data_json={slug: "Wert"} if slug else {},
    )
    if deleted:
        obj.soft_delete()
    return obj


class TestFieldTemplateBlockerMentionsTrash:
    def test_trashed_client_is_counted_out(self, facility, client_doc_type, slug_template):
        _make_client(facility, client_doc_type, slug=slug_template.slug, deleted=True)
        reasons = field_template_delete_blockers(slug_template)
        assert any("Papierkorb" in r for r in reasons)

    def test_no_trash_hint_without_trashed_clients(self, facility, client_doc_type, slug_template):
        _make_client(facility, client_doc_type, slug=slug_template.slug)
        reasons = field_template_delete_blockers(slug_template)
        assert reasons
        assert not any("Papierkorb" in r for r in reasons)


class TestDocumentTypeBlockerMentionsTrash:
    def test_trashed_client_is_counted_out(self, rf, facility, client_doc_type, admin_user):
        _make_client(facility, client_doc_type, deleted=True)
        request = rf.post("/")
        request.user = admin_user
        request.current_facility = facility
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(request, client_doc_type)
        assert any("Papierkorb" in r for r in exc.value.reasons)

    def test_no_trash_hint_without_trashed_clients(self, rf, facility, client_doc_type, admin_user):
        _make_client(facility, client_doc_type)
        request = rf.post("/")
        request.user = admin_user
        request.current_facility = facility
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(request, client_doc_type)
        assert not any("Papierkorb" in r for r in exc.value.reasons)


class TestCaseTrashShareOfTheBlocker:
    """Fall-Papierkorb als zweite Sackgasse (Refs #1818, ADR-044 §9).

    Gegenstueck zu ``TestFieldTemplateBlockerMentionsTrash`` auf der Fall-Achse:
    ein Fall im Papierkorb haelt ``document_type`` (PROTECT) und die Slugs
    seines ``data_json`` fest, ist ueber die Fallliste aber nicht mehr
    erreichbar. Der Zaehler speist den zweiten Ausweg-Link.
    """

    def _fall(self, facility, *, slug=None, deleted=False, titel="Blocker-Fall"):
        from core.models import Case

        case = Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym=f"P-{titel}"),
            title=titel,
            data_json={slug: "Wert"} if slug else {},
        )
        if deleted:
            case.soft_delete()
        return case

    def test_trashed_case_with_slug_value_is_counted(self, facility, slug_template):
        self._fall(facility, slug=slug_template.slug, deleted=True)
        assert config_service.field_template_trashed_case_count(slug_template) == 1

    def test_live_case_with_slug_value_is_not_counted(self, facility, slug_template):
        self._fall(facility, slug=slug_template.slug)
        assert config_service.field_template_trashed_case_count(slug_template) == 0

    def test_template_without_slug_counts_nothing(self, facility):
        vorlage = FieldTemplate.objects.create(
            facility=facility, name="Ohne Slug", field_type=FieldTemplate.FieldType.TEXT
        )
        self._fall(facility, deleted=True)
        assert config_service.field_template_trashed_case_count(vorlage) == 0

    def test_dialog_links_to_the_case_trash(self, client, admin_user, facility, slug_template):
        self._fall(facility, slug=slug_template.slug, deleted=True)
        client.force_login(admin_user)
        body = client.get(reverse("core:config_fieldtemplate_delete", args=[slug_template.pk])).content.decode()
        assert 'data-testid="config-delete-case-trash-count"' in body
        assert 'data-testid="config-delete-case-trash-link"' in body
        assert reverse("core:case_trash") in body

    def test_dialog_without_trashed_cases_has_no_case_hint(self, client, admin_user, facility, slug_template):
        self._fall(facility, slug=slug_template.slug)
        client.force_login(admin_user)
        body = client.get(reverse("core:config_fieldtemplate_delete", args=[slug_template.pk])).content.decode()
        assert 'data-testid="config-delete-case-trash-count"' not in body


class TestConfirmDeletePagesOfferTheWayOut:
    def test_document_type_dialog_links_to_trash(self, client, admin_user, facility, client_doc_type):
        _make_client(facility, client_doc_type, deleted=True)
        client.force_login(admin_user)
        body = client.get(reverse("core:config_doctype_delete", args=[client_doc_type.pk])).content.decode()
        assert "config-delete-trash-hint" in body
        assert reverse("core:client_trash") in body

    def test_document_type_dialog_without_trash_has_no_hint(self, client, admin_user, facility, client_doc_type):
        _make_client(facility, client_doc_type)
        client.force_login(admin_user)
        body = client.get(reverse("core:config_doctype_delete", args=[client_doc_type.pk])).content.decode()
        assert "config-delete-trash-hint" not in body

    def test_field_template_dialog_links_to_trash(self, client, admin_user, facility, client_doc_type, slug_template):
        _make_client(facility, client_doc_type, slug=slug_template.slug, deleted=True)
        client.force_login(admin_user)
        body = client.get(reverse("core:config_fieldtemplate_delete", args=[slug_template.pk])).content.decode()
        assert "config-delete-trash-hint" in body
        assert reverse("core:client_trash") in body

    def test_field_template_dialog_without_trash_has_no_hint(
        self, client, admin_user, facility, client_doc_type, slug_template
    ):
        _make_client(facility, client_doc_type, slug=slug_template.slug)
        client.force_login(admin_user)
        body = client.get(reverse("core:config_fieldtemplate_delete", args=[slug_template.pk])).content.decode()
        assert "config-delete-trash-hint" not in body


# ---------------------------------------------------------------------------
# Zweite Blocker-Quelle: Attachment.field_template (PROTECT) — M24/AP4,
# Refs #1738 (#1737 hat sie als kuenftige Quelle bereits benannt)
# ---------------------------------------------------------------------------


@pytest.fixture
def file_template(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Datei Y",
        slug="datei-y",
        field_type=FieldTemplate.FieldType.FILE,
    )


def _attach_to(facility, owner, field_template, user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.services.file_vault import store_encrypted_file

    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        b"xref\n0 3\n0000000000 65535 f\n"
        b"trailer<</Size 3/Root 1 0 R>>\n"
        b"startxref\n9\n%%EOF\n"
    )
    upload = SimpleUploadedFile("blocker.pdf", pdf, content_type="application/pdf")
    return store_encrypted_file(facility, upload, field_template, owner, user)


class TestAttachmentBlockerMentionsTrash:
    """Der Anhang-Blocker ist die zweite Quelle einer Papierkorb-Sackgasse.

    ``Attachment.field_template`` ist ``PROTECT``: eine Datei-Feldvorlage bleibt
    loeschgesperrt, solange irgendein Anhang sie referenziert — auch wenn dessen
    Owner (Person/Ereignis/Fall) laengst im Papierkorb liegt und im Fach-UI
    nicht mehr erreichbar ist. Ohne den Papierkorb-Anteil endete der Dialog
    genauso in einer scheinbaren Sackgasse wie vor #1737.
    """

    def test_attachment_of_trashed_client_marks_the_reason(self, facility, staff_user, client_doc_type, file_template):
        person = _make_client(facility, client_doc_type, pseudonym="P-Datei-Trash")
        _attach_to(facility, person, file_template, staff_user)
        person.soft_delete()
        reasons = field_template_delete_blockers(file_template)
        attachment_reason = next(r for r in reasons if "Dateianhang" in r or "Dateianhänge" in r)
        assert "Papierkorb" in attachment_reason

    def test_attachment_via_event_of_trashed_client(
        self, facility, staff_user, client_doc_type, file_template, doc_type_contact
    ):
        from django.utils import timezone

        from core.models import Event

        person = _make_client(facility, client_doc_type, pseudonym="P-Ereignis-Trash")
        event = Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        _attach_to(facility, event, file_template, staff_user)
        person.soft_delete()
        reasons = field_template_delete_blockers(file_template)
        assert any("Papierkorb" in r for r in reasons)

    def test_attachment_via_case_of_trashed_client(self, facility, staff_user, client_doc_type, file_template):
        from core.models import Case

        person = _make_client(facility, client_doc_type, pseudonym="P-Fall-Trash")
        case = Case.objects.create(facility=facility, client=person, title="Blocker-Fall", created_by=staff_user)
        _attach_to(facility, case, file_template, staff_user)
        person.soft_delete()
        reasons = field_template_delete_blockers(file_template)
        assert any("Papierkorb" in r for r in reasons)

    def test_attachment_of_active_client_has_no_trash_share(self, facility, staff_user, client_doc_type, file_template):
        person = _make_client(facility, client_doc_type, pseudonym="P-Datei-Aktiv")
        _attach_to(facility, person, file_template, staff_user)
        reasons = field_template_delete_blockers(file_template)
        assert reasons
        assert not any("Papierkorb" in r for r in reasons)

    def test_case_attachment_dialog_offers_the_case_trash(
        self, client, admin_user, staff_user, facility, client_doc_type, file_template
    ):
        """Fall-Achse des Anhang-Zweigs (Refs #1818) — bisher unbelegt.

        Ein Anhang am FALL haelt die Datei-Feldvorlage per ``PROTECT`` fest.
        Liegt der Fall im Papierkorb (und seine Person NICHT), greift der
        Personen-Zaehler nicht: der Ausweg-Link muss aus
        ``field_template_trashed_case_count`` kommen.
        """
        from core.models import Case

        person = _make_client(facility, client_doc_type, pseudonym="P-Fallanhang")
        case = Case.objects.create(facility=facility, client=person, title="Anhang-Fall", created_by=staff_user)
        _attach_to(facility, case, file_template, staff_user)
        case.soft_delete(user=staff_user)

        assert config_service.field_template_trashed_case_count(file_template) == 1
        assert config_service.field_template_trashed_client_count(file_template) == 0

        client.force_login(admin_user)
        body = client.get(reverse("core:config_fieldtemplate_delete", args=[file_template.pk])).content.decode()
        assert 'data-testid="config-delete-case-trash-count"' in body
        assert reverse("core:case_trash") in body

    def test_confirm_dialog_offers_the_way_out(
        self, client, admin_user, staff_user, facility, client_doc_type, file_template
    ):
        """Ohne diesen Zaehler zeigte der Dialog den ``Papierkorb oeffnen``-Link
        nicht — die Blockade waere ohne sichtbaren Ausweg geblieben."""
        person = _make_client(facility, client_doc_type, pseudonym="P-Dialog-Trash")
        _attach_to(facility, person, file_template, staff_user)
        person.soft_delete()
        client.force_login(admin_user)
        body = client.get(reverse("core:config_fieldtemplate_delete", args=[file_template.pk])).content.decode()
        assert "config-delete-trash-hint" in body
        assert reverse("core:client_trash") in body
