"""HTTP contracts for online review lists, marks, hints and access checks."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import FreeTextReview
from core.services.free_text_review import get_entry, mark_review
from tests._standard_text import record_text, set_record_text
from tests.test_free_text_review import populate

pytestmark = pytest.mark.django_db


def target_url(obj, field):
    return reverse(
        "core:free_text_review_target",
        kwargs={
            "target_type": obj.__class__.__name__,
            "target_id": obj.pk,
            "field_key": field,
        },
    )


@pytest.mark.parametrize("role", ["assistant_user", "staff_user", "lead_user", "admin_user", "super_admin_user"])
@pytest.mark.parametrize("target_type", ["Client", "Case", "Episode", "WorkItem", "Event"])
def test_view_role_matrix(request, client, facility, role, target_type):
    obj, field = populate(request, target_type)
    user = request.getfixturevalue(role)
    client.force_login(user)
    url = target_url(obj, field)
    response = client.get(url)
    expected = 403 if role == "super_admin_user" else (200 if target_type == "Event" else 404)
    assert response.status_code == expected
    response = client.post(url, {"action": "revoke"})
    assert response.status_code == (302 if expected == 200 else expected)
    if expected != 200:
        assert b"Private test content" not in response.content


def test_list_mark_revoke_and_hints(client, facility, staff_user, sample_event):
    sample_event.data_json["notiz"] = "Write person@example.org"
    sample_event.save()
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    response = client.get(url)
    assert response.status_code == 200
    assert b"person@example.org" in response.content
    assert "no-store" in response.headers["Cache-Control"]
    partial = client.get(url, HTTP_HX_REQUEST="true")
    assert b"<!DOCTYPE" not in partial.content
    entry = get_entry(facility, staff_user, "Event", sample_event.pk, "notiz")
    detail_url = target_url(sample_event, "notiz")
    detail = client.get(detail_url)
    assert reverse("core:event_create").encode() in detail.content
    until = timezone.localdate() + timedelta(days=7)
    response = client.post(
        detail_url, {"action": "mark", "reviewed_until": until.isoformat(), "fingerprint": entry.fingerprint}
    )
    assert response.status_code == 302
    assert FreeTextReview.objects.for_facility(facility).get().reviewed_until == until
    assert client.get(detail_url).context["entry"].is_reviewed
    response = client.post(detail_url, {"action": "revoke"})
    assert response.status_code == 302
    assert not FreeTextReview.objects.for_facility(facility).exists()
    sample_event.refresh_from_db()
    assert sample_event.data_json["notiz"] == "Write person@example.org"


def test_stale_and_invalid_posts_never_mark(client, facility, staff_user, sample_event):
    sample_event.data_json["notiz"] = "First text"
    sample_event.save()
    client.force_login(staff_user)
    entry = get_entry(facility, staff_user, "Event", sample_event.pk, "notiz")
    sample_event.data_json["notiz"] = "Changed text"
    sample_event.save()
    url = target_url(sample_event, "notiz")
    response = client.post(
        url, {"action": "mark", "reviewed_until": timezone.localdate().isoformat(), "fingerprint": entry.fingerprint}
    )
    assert response.status_code == 422
    assert not FreeTextReview.objects.for_facility(facility).exists()
    assert client.post(url, {"action": "unexpected"}).status_code == 422
    sample_event.is_deleted = True
    sample_event.save()
    assert client.get(url).status_code == 404
    assert client.post(url, {"action": "mark"}).status_code == 404


@pytest.mark.parametrize("target_type", ["Client", "Case", "Episode", "WorkItem", "Event"])
def test_foreign_target_is_404(request, client, staff_user, target_type):
    fixture, field = {
        "Client": ("foreign_client", "notes"),
        "Case": ("foreign_case", "description"),
        "Episode": ("foreign_episode", "description"),
        "WorkItem": ("foreign_workitem", "description"),
        "Event": ("foreign_event", "notiz"),
    }[target_type]
    obj = request.getfixturevalue(fixture)
    if target_type == "Event":
        from core.models import DocumentTypeField, FieldTemplate

        field_template = FieldTemplate.objects.create(facility=obj.facility, name="Notiz", field_type="textarea")
        DocumentTypeField.objects.create(document_type=obj.document_type, field_template=field_template)
        obj.data_json = {field_template.slug: "Foreign private content"}
    elif target_type not in {"Client", "Case", "Episode", "WorkItem"}:
        setattr(obj, field, "Foreign private content")
    obj.save()
    client.force_login(staff_user)
    url = target_url(obj, field)
    assert client.get(url).status_code == 404
    assert client.post(url, {"action": "revoke"}).status_code == 404


def test_filters_apply_to_visible_entries_only(client, facility, staff_user, sample_event, lead_user):
    sample_event.data_json["notiz"] = "person@example.org"
    sample_event.save()
    entry = get_entry(facility, staff_user, "Event", sample_event.pk, "notiz")
    mark_review(facility, lead_user, "Event", sample_event.pk, "notiz", timezone.localdate(), entry.fingerprint)
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    assert len(client.get(url, {"risk": "email"}).context["entries"]) == 1
    assert not client.get(url, {"risk": "phone"}).context["entries"]
    assert len(client.get(url, {"editor": str(lead_user.pk)}).context["entries"]) == 1
    assert not client.get(url, {"changed_before": (timezone.localdate() - timedelta(days=1)).isoformat()}).context[
        "entries"
    ]
    assert client.get(url, {"changed_before": "invalid"}).status_code == 400
    # A foreign facility parameter cannot change request.current_facility.
    assert len(client.get(url, {"facility": "unknown"}).context["entries"]) == 1


def test_review_list_requires_login(client):
    assert client.get(reverse("core:free_text_review_list")).status_code == 302


def test_filter_keeps_inactive_reviewers(client, facility, staff_user, lead_user, sample_event):
    sample_event.data_json["notiz"] = "Neutral content"
    sample_event.save()
    entry = get_entry(facility, staff_user, "Event", sample_event.pk, "notiz")
    mark_review(facility, lead_user, "Event", sample_event.pk, "notiz", timezone.localdate(), entry.fingerprint)
    lead_user.is_active = False
    lead_user.save(update_fields=["is_active"])
    client.force_login(staff_user)
    response = client.get(reverse("core:free_text_review_list"), {"editor": str(lead_user.pk)})
    assert response.status_code == 200
    assert len(response.context["entries"]) == 1


def test_editor_choices_only_include_visible_source_metadata(client, facility, staff_user, admin_user, sample_event):
    sample_event.data_json["notiz"] = "Neutral source text"
    sample_event.save()
    client.force_login(staff_user)
    response = client.get(reverse("core:free_text_review_list"))
    choices = {value for value, _label in response.context["filter_form"].fields["editor"].choices}
    assert str(staff_user.pk) in choices
    assert str(admin_user.pk) not in choices


def _qualified_audit(facility, person):
    from core.models import AuditLog

    return AuditLog.objects.for_facility(facility).filter(action=AuditLog.Action.VIEW_QUALIFIED, target_id=person.pk)


@pytest.mark.parametrize("page", ["list", "target", "htmx_list"])
def test_removed_qualified_notes_keep_person_read_audit(client, facility, staff_user, client_qualified, page):
    set_record_text(client_qualified, "Qualified review content")
    client_qualified.save(update_fields=["document_type", "data_json"])
    client.force_login(staff_user)
    audit = _qualified_audit(facility, client_qualified)
    normal = client.get(reverse("core:client_detail", kwargs={"pk": client_qualified.pk}))
    assert b"Qualified review content" in normal.content
    assert audit.count() == 1
    if page == "target":
        response = client.get(target_url(client_qualified, "notes"))
        assert response.status_code == 404
    else:
        extra = {"HTTP_HX_REQUEST": "true"} if page == "htmx_list" else {}
        response = client.get(reverse("core:free_text_review_list"), **extra)
        assert response.status_code == 200
        assert response.context["entries"] == []
    assert b"Qualified review content" not in response.content
    assert audit.count() == 1
    client_qualified.refresh_from_db()
    assert record_text(client_qualified) == "Qualified review content"
    assert all("Qualified review content" not in str(row.detail) for row in audit)


def test_read_audit_only_for_displayed_qualified_persons(
    client, facility, staff_user, client_qualified, client_identified
):
    from core.models import AuditLog

    set_record_text(client_qualified, "Qualified plain note")
    client_qualified.save(update_fields=["document_type", "data_json"])
    set_record_text(client_identified, "Write person@example.org")
    client_identified.save(update_fields=["document_type", "data_json"])
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    assert client.get(url, {"risk": "email"}).context["entries"] == []
    assert client.get(url).context["entries"] == []
    assert not AuditLog.objects.for_facility(facility).filter(action=AuditLog.Action.VIEW_QUALIFIED).exists()
    assert client.get(reverse("core:client_detail", kwargs={"pk": client_qualified.pk})).status_code == 200
    assert _qualified_audit(facility, client_qualified).count() == 1
    assert client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk})).status_code == 200
    assert not _qualified_audit(facility, client_identified).exists()


def test_model_change_refreshes_editor_choices(client, facility, staff_user, lead_user, sample_workitem, sample_event):

    sample_event.created_by = lead_user
    sample_event.save(update_fields=["created_by"])
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    initial = client.get(url, {"model": "WorkItem"})
    assert initial.status_code == 404
    assert b"Neutral source text" not in initial.content
    response = client.get(url, {"model": "Event"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert str(lead_user.pk) in dict(response.context["filter_form"].fields["editor"].choices)
    # The partial carries the refreshed form, not only the result list.
    assert b'name="editor"' in response.content
    assert f'value="{lead_user.pk}"'.encode() in response.content


def test_stale_editor_selection_is_discarded_on_model_change(
    client, facility, staff_user, lead_user, sample_workitem, sample_event
):

    sample_event.created_by = staff_user
    sample_event.save(update_fields=["created_by"])
    sample_workitem.created_by = lead_user
    sample_workitem.save(update_fields=["created_by"])
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    # The removed task cannot contribute an editor choice to the event list.
    for extra in ({}, {"HTTP_HX_REQUEST": "true"}):
        response = client.get(url, {"model": "Event", "editor": str(lead_user.pk), "page": 1}, **extra)
        assert response.status_code == 200
        assert [entry.target_id for entry in response.context["entries"]] == [sample_event.pk]
        assert response.context["filter_form"].data.get("editor") is None
        assert "editor=" not in response.context["filter_query"]
