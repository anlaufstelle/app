"""Public options editor documents the accepted slug format."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_options_editor_shows_slug_format_hint(client, admin_user):
    client.force_login(admin_user)

    body = client.get(reverse("core:config_fieldtemplate_create")).content.decode()

    assert "Slug: nur a–z, 0–9 und einzelne Bindestriche" in body
