"""Unit-Tests für die HTMX-UX-Härtung (Refs #1016, Workstream C — C2/C3).

Render-Ebene: prüft, dass der globale Fehler-Handler (C2), der Lade-Spinner (C3)
und der Doppel-Submit-Schutz (C3) korrekt in die Templates verdrahtet sind. Das
Laufzeitverhalten selbst (Toast bei 4xx/5xx, Spinner-Sichtbarkeit, geblockter
2. Submit) ist client-seitig und wird in den E2E-Tests abgedeckt (manuell-first).
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestHtmxErrorHandlerWiring:
    """C2: base.html lädt den globalen htmx:responseError-Fehler-Handler."""

    def test_base_loads_htmx_error_handler(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_list")).content.decode()
        assert "js/htmx-errors.js" in content


@pytest.mark.django_db
class TestHtmxLoadingIndicator:
    """C3: Live-Such-Listen besitzen einen hx-indicator-Lade-Spinner."""

    def test_client_list_has_loading_indicator(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_list")).content.decode()
        assert 'id="client-table-spinner"' in content
        assert 'hx-indicator="#client-table-spinner"' in content
        assert "htmx-indicator" in content

    def test_case_list_has_loading_indicator(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:case_list")).content.decode()
        assert 'id="case-table-spinner"' in content
        assert 'hx-indicator="#case-table-spinner"' in content

    def test_search_has_loading_indicator(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:search")).content.decode()
        assert 'id="search-results-spinner"' in content
        assert 'hx-indicator="#search-results-spinner"' in content


@pytest.mark.django_db
class TestDoubleSubmitGuardWiring:
    """C3: base.html lädt den Doppel-Submit-Schutz für Standard-Formulare."""

    def test_base_loads_double_submit_guard(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_list")).content.decode()
        assert "js/double-submit.js" in content
