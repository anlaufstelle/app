"""Refs #805 (C-38): non_field_errors-Block in clients/cases/workitems-Forms.

Cross-Field-Fehler aus Service-Layer-ValidationErrors verschwanden frueher
stumm, weil die drei Form-Templates keinen ``form.non_field_errors``-Block
hatten. Das gemeinsame Partial ``core/_form_errors.html`` rendert solche
Fehler jetzt mit ``role="alert"``.
"""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string


def _render(template_name, form):
    return render_to_string(
        template_name,
        {
            "form": form,
            "is_edit": False,
            "client": None,
            "case": None,
            "workitem": None,
            "client_id": "",
            "client_pseudonym": "",
            "stages": [],
            "age_clusters": [],
            "field_set": None,
            "all_clients": [],
        },
    )


@pytest.mark.django_db
class TestFormErrorsPartial:
    def test_partial_renders_non_field_error(self):
        from core.forms.clients import ClientForm

        form = ClientForm(data={})
        form.add_error(None, "Cross-field-Fehler aus Service-Layer.")

        html = render_to_string("core/_form_errors.html", {"form": form})
        assert 'role="alert"' in html
        assert "Cross-field-Fehler aus Service-Layer." in html

    def test_partial_renders_nothing_without_errors(self):
        from core.forms.clients import ClientForm

        form = ClientForm()
        html = render_to_string("core/_form_errors.html", {"form": form}).strip()
        # Nur der Comment-Header, kein <div role="alert">.
        assert 'role="alert"' not in html

    def test_clients_form_includes_partial(self, facility):
        from core.forms.clients import ClientForm

        form = ClientForm(data={}, facility=facility)
        form.add_error(None, "Cross-field-Fehler-Klient.")
        html = _render("core/clients/form.html", form)
        assert 'role="alert"' in html
        assert "Cross-field-Fehler-Klient." in html

    def test_cases_form_includes_partial(self, facility):
        from core.forms.cases import CaseForm

        form = CaseForm(data={}, facility=facility)
        form.add_error(None, "Cross-field-Fehler-Fall.")
        html = _render("core/cases/form.html", form)
        assert 'role="alert"' in html
        assert "Cross-field-Fehler-Fall." in html

    def test_workitems_form_includes_partial(self, facility, staff_user):
        from core.forms.workitems import WorkItemForm

        form = WorkItemForm(data={}, facility=facility)
        form.add_error(None, "Cross-field-Fehler-Aufgabe.")
        html = _render("core/workitems/form.html", form)
        assert 'role="alert"' in html
        assert "Cross-field-Fehler-Aufgabe." in html
