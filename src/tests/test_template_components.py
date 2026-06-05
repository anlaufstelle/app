"""Unit-Tests für die wiederverwendbaren Template-Komponenten (Refs #1016, C5).

Render-Ebene: prüft, dass ``components/_form_field.html`` (Label + aria_field +
Help-/Error-Markup) korrekt in die refaktorierten Formulare eingebunden ist —
inkl. Pflichtfeld-Stern (über ``field.field.required``) und Fehler-Liste.
"""

import pytest
from django.urls import reverse

_LABEL_CLASS = 'class="block text-[12px] font-bold uppercase tracking-[0.05em] text-ink-soft mb-1.5"'


@pytest.mark.django_db
class TestFormFieldComponent:
    def test_client_form_renders_field_component(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_create")).content.decode()
        assert _LABEL_CLASS in content
        # aria_field setzt aria-required auf Pflichtfeldern.
        assert 'aria-required="true"' in content

    def test_case_form_renders_required_star(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:case_create")).content.decode()
        assert _LABEL_CLASS in content
        # Titel ist Pflicht → Partial rendert das Sternchen.
        assert " *" in content

    def test_workitem_form_renders_field_component(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:workitem_create")).content.decode()
        assert _LABEL_CLASS in content

    def test_field_component_renders_error_list(self, client, staff_user):
        """Invalider POST → Fehler-Liste (id_X-error, role=alert) aus dem Partial."""
        client.force_login(staff_user)
        resp = client.post(reverse("core:case_create"), {"title": "", "description": ""})
        content = resp.content.decode()
        assert 'id="id_title-error"' in content
        assert 'role="alert"' in content
