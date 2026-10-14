"""Case context survives create-form redisplay and template navigation."""

from html.parser import HTMLParser
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Event, QuickTemplate


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["meta", "dynamic", "service", "upload"])
def test_case_survives_validation_error(client, staff_user, case_open, doc_type_contact, failure):
    client.force_login(staff_user)
    payload = {
        "client": str(case_open.client_id),
        "case": str(case_open.pk),
        "document_type": str(doc_type_contact.pk),
        "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        "dauer": "15",
    }
    if failure == "meta":
        payload["occurred_at"] = "invalid"
    if failure == "upload":
        payload["file"] = SimpleUploadedFile("note.txt", b"content")
    if failure == "dynamic":
        payload["dauer"] = "invalid"
    target = "stage_uploads" if failure == "upload" else "create_event"
    with patch(f"core.views.events.{target}", side_effect=ValidationError("Rejected")) as rejected:
        response = client.post(reverse("core:event_create"), payload)
    assert response.status_code == 200
    if failure in {"service", "upload"}:
        rejected.assert_called_once()
    assert f'data-initial-case-id="{case_open.pk}"' in response.content.decode()
    assert response.context["meta_form"]["case"].value() == str(case_open.pk)
    if failure == "upload":
        assert "Datei erneut auswählen" in response.content.decode()
        payload.pop("file")
    payload.update(occurred_at=timezone.localtime().strftime("%Y-%m-%dT%H:%M"), dauer="15")
    response = client.post(reverse("core:event_create"), payload)
    assert response.status_code == 302
    assert Event.objects.get(client_id=case_open.client_id).case_id == case_open.pk


@pytest.mark.django_db
@pytest.mark.parametrize("invalid", ["foreign", "closed", "mismatch", "deleted", "invisible", "role"])
@pytest.mark.parametrize("method", ["get", "post"])
def test_invalid_case_is_discarded_with_notice(
    client, staff_user, assistant_user, case_open, client_qualified, other_facility, doc_type_case_form, invalid, method
):
    from core.models import DocumentType

    user = staff_user
    owner = case_open.client_id
    if invalid == "foreign":
        case_open.facility = other_facility
    elif invalid == "closed":
        case_open.status = Case.Status.CLOSED
    elif invalid == "mismatch":
        owner = client_qualified.pk
    elif invalid == "deleted":
        case_open.is_deleted = True
    elif invalid == "invisible":
        doc_type_case_form = DocumentType.objects.create(
            facility=case_open.facility,
            name="Private case",
            entity_type=DocumentType.EntityType.CASE,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        case_open.document_type = doc_type_case_form
    elif invalid == "role":
        user = assistant_user
    case_open.save()
    client.force_login(user)
    response = getattr(client, method)(
        reverse("core:event_create"), {"case": str(case_open.pk), "client": str(owner), "occurred_at": "invalid"}
    )
    assert response.status_code == 200
    assert 'data-initial-case-id=""' in response.content.decode()
    assert "Die Fallauswahl wurde zurückgesetzt" in response.content.decode()
    assert not response.context["meta_form"]["case"].value()


@pytest.mark.django_db
def test_template_links_keep_valid_case(client, staff_user, case_open, doc_type_contact, facility):
    template = QuickTemplate.objects.create(facility=facility, document_type=doc_type_contact, name="Preset")
    client.force_login(staff_user)
    response = client.get(reverse("core:event_create"), {"case": case_open.pk, "template": template.pk})

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "a" and (attrs.get("data-testid") == "template-remove-link" or "data-template-id" in attrs):
                self.links.append(attrs["href"])

    html = Links()
    html.feed(response.content.decode())
    assert len(html.links) == 2
    for link in html.links:
        assert f"case={case_open.pk}" in link
        destination = client.get(link)
        assert f'data-initial-case-id="{case_open.pk}"' in destination.content.decode()


@pytest.fixture
def stricter_template(facility, staff_user, doc_type_contact):
    """Quick template whose document type needs a higher contact stage than ``client_identified``."""
    from core.models import Client as PersonModel
    from core.models import DocumentType

    stricter_type = DocumentType.objects.create(
        facility=facility,
        name="Qualified only",
        sensitivity=DocumentType.Sensitivity.NORMAL,
        min_contact_stage=PersonModel.ContactStage.QUALIFIED,
    )
    return QuickTemplate.objects.create(
        facility=facility,
        document_type=stricter_type,
        name="Stricter preset",
        created_by=staff_user,
    )


@pytest.mark.django_db
def test_template_of_stricter_contact_stage_is_reset_with_explanation(client, staff_user, case_open, stricter_template):
    """#2534: a template the person's stage excludes is not applied; person and case stay."""
    client.force_login(staff_user)
    response = client.get(
        reverse("core:event_create"),
        {"template": stricter_template.pk, "client": str(case_open.client_id), "case": str(case_open.pk)},
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert response.context["applied_template"] is None
    assert response.context["template_stage_reset"] is True
    assert "wurde nicht angewendet" in html
    assert "Stricter preset" in html
    assert "Mit Zusatzangaben" in html
    assert "Person und Fall bleiben erhalten" in html
    assert str(response.context["meta_form"]["document_type"].value()) != str(stricter_template.document_type_id)
    assert response.context["client_id"] == str(case_open.client_id)
    assert response.context["case_id"] == str(case_open.pk)
    assert f'data-initial-case-id="{case_open.pk}"' in html


@pytest.mark.django_db
def test_template_of_permitted_contact_stage_is_applied_without_reset(
    client, staff_user, client_qualified, stricter_template
):
    client.force_login(staff_user)
    response = client.get(
        reverse("core:event_create"), {"template": stricter_template.pk, "client": str(client_qualified.pk)}
    )

    assert response.context["applied_template"] == stricter_template
    assert not response.context.get("template_stage_reset")
    assert "wurde nicht angewendet" not in response.content.decode()


@pytest.mark.django_db
def test_template_of_stricter_stage_without_person_stays_applied(client, staff_user, stricter_template):
    """Without a chosen person the stage is still open: the person picker filters by it."""
    client.force_login(staff_user)
    response = client.get(reverse("core:event_create"), {"template": stricter_template.pk})

    assert response.context["applied_template"] == stricter_template
    assert not response.context.get("template_stage_reset")
