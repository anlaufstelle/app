"""HTTP contracts for online review lists, marks, hints and access checks."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import FreeTextReview
from core.services.free_text_review import get_entry, mark_review
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
    expected = (
        403
        if role == "super_admin_user"
        else (404 if role == "assistant_user" and target_type not in {"WorkItem", "Event"} else 200)
    )
    assert response.status_code == expected
    response = client.post(url, {"action": "revoke"})
    assert response.status_code == (302 if expected == 200 else expected)
    if expected != 200:
        assert b"Private test content" not in response.content


def test_list_mark_revoke_and_hints(client, facility, staff_user, client_identified):
    client_identified.notes = "Write person@example.org"
    client_identified.save()
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    response = client.get(url)
    assert response.status_code == 200
    assert b"person@example.org" in response.content
    assert "no-store" in response.headers["Cache-Control"]
    partial = client.get(url, HTTP_HX_REQUEST="true")
    assert b"<!DOCTYPE" not in partial.content
    entry = get_entry(facility, staff_user, "Client", client_identified.pk, "notes")
    detail_url = target_url(client_identified, "notes")
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
    client_identified.refresh_from_db()
    assert client_identified.notes == "Write person@example.org"


def test_stale_and_invalid_posts_never_mark(client, facility, staff_user, client_identified):
    client_identified.notes = "First text"
    client_identified.save()
    client.force_login(staff_user)
    entry = get_entry(facility, staff_user, "Client", client_identified.pk, "notes")
    client_identified.notes = "Changed text"
    client_identified.save()
    url = target_url(client_identified, "notes")
    response = client.post(
        url, {"action": "mark", "reviewed_until": timezone.localdate().isoformat(), "fingerprint": entry.fingerprint}
    )
    assert response.status_code == 422
    assert not FreeTextReview.objects.for_facility(facility).exists()
    assert client.post(url, {"action": "unexpected"}).status_code == 422
    client_identified.is_deleted = True
    client_identified.save()
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
    else:
        setattr(obj, field, "Foreign private content")
    obj.save()
    client.force_login(staff_user)
    url = target_url(obj, field)
    assert client.get(url).status_code == 404
    assert client.post(url, {"action": "revoke"}).status_code == 404


def test_filters_apply_to_visible_entries_only(client, facility, staff_user, client_identified, lead_user):
    client_identified.notes = "person@example.org"
    client_identified.save()
    entry = get_entry(facility, staff_user, "Client", client_identified.pk, "notes")
    mark_review(facility, lead_user, "Client", client_identified.pk, "notes", timezone.localdate(), entry.fingerprint)
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


def test_filter_keeps_inactive_reviewers(client, facility, staff_user, lead_user, client_identified):
    client_identified.notes = "Neutral content"
    client_identified.save()
    entry = get_entry(facility, staff_user, "Client", client_identified.pk, "notes")
    mark_review(facility, lead_user, "Client", client_identified.pk, "notes", timezone.localdate(), entry.fingerprint)
    lead_user.is_active = False
    lead_user.save(update_fields=["is_active"])
    client.force_login(staff_user)
    response = client.get(reverse("core:free_text_review_list"), {"editor": str(lead_user.pk)})
    assert response.status_code == 200
    assert len(response.context["entries"]) == 1


def test_editor_choices_only_include_visible_source_metadata(
    client, facility, staff_user, admin_user, client_identified
):
    client_identified.notes = "Neutral source text"
    client_identified.save()
    client.force_login(staff_user)
    response = client.get(reverse("core:free_text_review_list"))
    choices = {value for value, _label in response.context["filter_form"].fields["editor"].choices}
    assert str(staff_user.pk) in choices
    assert str(admin_user.pk) not in choices


def _qualified_audit(facility, person):
    from core.models import AuditLog

    return AuditLog.objects.for_facility(facility).filter(action=AuditLog.Action.VIEW_QUALIFIED, target_id=person.pk)


@pytest.mark.parametrize("page", ["list", "target", "htmx_list"])
def test_qualified_notes_keep_existing_read_audit(client, facility, staff_user, client_qualified, page):
    client_qualified.notes = "Qualified review content"
    client_qualified.save(update_fields=["notes"])
    client.force_login(staff_user)
    audit = _qualified_audit(facility, client_qualified)
    normal = client.get(reverse("core:client_detail", kwargs={"pk": client_qualified.pk}))
    assert b"Qualified review content" in normal.content
    assert audit.count() == 1
    if page == "target":
        response = client.get(target_url(client_qualified, "notes"))
    else:
        extra = {"HTTP_HX_REQUEST": "true"} if page == "htmx_list" else {}
        response = client.get(reverse("core:free_text_review_list"), **extra)
    assert response.status_code == 200
    assert b"Qualified review content" in response.content
    assert audit.count() == 2
    # The audit stores the person reference only, never the text.
    assert all("Qualified review content" not in str(row.detail) for row in audit)


def test_read_audit_only_for_displayed_qualified_persons(
    client, facility, staff_user, client_qualified, client_identified
):
    from core.models import AuditLog

    client_qualified.notes = "Qualified plain note"
    client_qualified.save(update_fields=["notes"])
    client_identified.notes = "Write person@example.org"
    client_identified.save(update_fields=["notes"])
    client.force_login(staff_user)
    url = reverse("core:free_text_review_list")
    # The email filter displays only the identified person: no qualified read happens.
    assert len(client.get(url, {"risk": "email"}).context["entries"]) == 1
    assert not AuditLog.objects.for_facility(facility).filter(action=AuditLog.Action.VIEW_QUALIFIED).exists()
    # Without a filter both are displayed; only the qualified person is audited, once.
    assert len(client.get(url).context["entries"]) == 2
    assert _qualified_audit(facility, client_qualified).count() == 1
    assert not _qualified_audit(facility, client_identified).exists()
