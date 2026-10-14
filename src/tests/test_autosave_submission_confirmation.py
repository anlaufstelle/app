"""Server acknowledgement of an autosaved contact submission."""

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext, override

TOKEN = "11111111-2222-4333-8444-555555555555"


@pytest.mark.django_db
@pytest.mark.parametrize("htmx", [False, True])
@pytest.mark.parametrize("save_and_new", [False, True])
def test_saved_contact_confirms_draft_token(client, staff_user, doc_type_contact, htmx, save_and_new):
    client.force_login(staff_user)
    payload = {
        "document_type": str(doc_type_contact.pk),
        "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        "dauer": "15",
        "_autosave_token": TOKEN,
    }
    if save_and_new:
        payload["_save_and_new"] = "1"
    response = client.post(
        reverse("core:event_create"), payload, follow=True, headers={"HX-Request": "true"} if htmx else {}
    )
    assert response.status_code == 200
    assert f'data-autosave-confirmed="autosave-confirmed:{TOKEN}"' in response.content.decode()


@pytest.mark.django_db
def test_invalid_contact_retains_bound_form_without_confirmation(client, staff_user, doc_type_contact):
    client.force_login(staff_user)
    response = client.post(
        reverse("core:event_create"),
        {"document_type": str(doc_type_contact.pk), "occurred_at": "invalid", "_autosave_token": TOKEN},
    )
    assert response.status_code == 200
    assert "data-autosave-bound" in response.content.decode()
    assert f"autosave-confirmed:{TOKEN}" not in response.content.decode()


@pytest.mark.django_db
def test_contact_form_labels_explicit_recovery_of_possibly_saved_draft(client, staff_user, doc_type_contact):
    client.force_login(staff_user)
    response = client.get(reverse("core:event_create"), {"document_type": str(doc_type_contact.pk)})
    assert response.status_code == 200
    content = response.content.decode()
    with override(response.headers["Content-Language"]):
        warning = gettext(
            "Dieser Entwurf wurde möglicherweise bereits gespeichert. "
            "Das Zwischenspeichern ist angehalten. Bitte erst prüfen, dann verwenden."
        )
        use_draft = gettext("Entwurf trotzdem verwenden")
    assert f'data-i18n-draft-possibly-saved="{warning}"' in content
    assert f'data-i18n-use-draft="{use_draft}"' in content
