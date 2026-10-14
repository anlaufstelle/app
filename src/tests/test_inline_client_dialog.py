"""Modal semantics and validation focus for inline client creation."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_inline_client_dialog_has_modal_semantics(client, staff_user):
    client.force_login(staff_user)
    body = client.get(reverse("core:client_inline_create")).content.decode()
    assert 'aria-modal="true"' in body
    assert 'role="dialog"' in body
    assert 'tabindex="-1"' in body
    assert 'aria-labelledby="client-inline-create-title"' in body
    assert "data-inline-client-cancel" in body
    assert "autofocus" in body


@pytest.mark.django_db
def test_invalid_stage_focus_is_not_overridden_by_pseudonym_autofocus(client, staff_user):
    client.force_login(staff_user)
    response = client.post(
        reverse("core:client_inline_create"),
        {"pseudonym": "New person", "contact_stage": "invalid", "min_stage": ""},
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "autofocus" not in body
    assert response.context["form"].errors.keys() == {"contact_stage"}
    assert 'aria-invalid="true"' in body
    assert 'id="id_contact_stage"' in body


@pytest.mark.django_db
def test_persistent_dialog_controller_and_focus_opt_out_are_rendered(client, staff_user):
    client.force_login(staff_user)
    body = client.get(reverse("core:event_create")).content.decode()
    slot = body[body.index('id="client-inline-create-modal"') :].split(">", 1)[0]
    assert 'x-data="inlineClientDialog"' in slot
    assert "data-hx-no-focus" in slot
    assert '@htmx:before-swap="beforeSwap"' in slot
    assert '@htmx:after-swap="afterSwap"' in slot
    assert '@keydown="onKeydown"' in slot
    assert '@client-inline-created.window="onCreated"' in slot
    assert body.index('id="client-inline-create-modal"') > body.index("</form>", body.index('id="event-create-form"'))
