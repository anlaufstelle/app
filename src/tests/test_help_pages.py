"""In-App-Hilfe und kontextuelle Orientierung (Refs #2043)."""

import pytest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext, override

from core import nav
from core.services.client import request_client_deletion

HELP_SLUGS = (
    "arbeitszentrale",
    "zeitstrom",
    "aufgaben",
    "personen",
    "faelle",
    "dateien",
    "statistik",
    "datenschutz",
    "verwaltung",
    "system",
)


@pytest.mark.django_db
class TestHelpPages:
    def test_overview_is_login_required(self, client):
        response = client.get(reverse("core:help_overview"))

        assert response.status_code == 302
        assert response.url.startswith(f"{reverse('login')}?next=")

    @pytest.mark.parametrize("slug", HELP_SLUGS)
    def test_every_whitelisted_page_is_available_to_logged_in_users(self, client, staff_user, slug):
        client.force_login(staff_user)

        response = client.get(reverse("core:help_page", kwargs={"slug": slug}))

        assert response.status_code == 200
        assert f'data-help-slug="{slug}"' in response.content.decode()

    def test_work_center_help_names_the_files_page(self, client, lead_user):
        """The storage destination uses the visible page title (Refs #2489)."""
        client.force_login(lead_user)

        response = client.get(reverse("core:help_page", kwargs={"slug": "arbeitszentrale"}))

        assert response.status_code == 200
        assert "die Zeile „Speicher“ zur Seite „Dateien“." in response.content.decode()

    def test_help_page_is_login_required(self, client):
        response = client.get(reverse("core:help_page", kwargs={"slug": "personen"}))

        assert response.status_code == 302
        assert response.url.startswith(f"{reverse('login')}?next=")

    def test_unknown_slug_returns_404(self, client, staff_user):
        client.force_login(staff_user)

        response = client.get(reverse("core:help_page", kwargs={"slug": "nicht-erlaubt"}))

        assert response.status_code == 404

    def test_encoded_traversal_is_not_a_template_path(self, client, staff_user):
        client.force_login(staff_user)

        response = client.get("/hilfe/%2e%2e/base/")

        assert response.status_code == 404

    def test_overview_links_role_visible_registry_areas(self, client, staff_user):
        client.force_login(staff_user)

        body = client.get(reverse("core:help_overview")).content.decode()

        assert 'href="/hilfe/zeitstrom/"' in body
        assert 'href="/hilfe/personen/"' in body
        assert 'href="/hilfe/system/"' not in body


@pytest.mark.django_db
class TestHelpEntryPoints:
    def test_help_is_linked_from_footer_desktop_and_mobile_navigation(self, client, staff_user):
        client.force_login(staff_user)

        body = client.get(reverse("core:client_list")).content.decode()

        assert 'data-testid="footer-help"' in body
        assert 'data-testid="nav-help"' in body
        assert 'data-testid="mobile-nav-help"' in body
        assert body.count('href="/hilfe/"') >= 3


@pytest.mark.django_db
class TestContextHelpPlacements:
    def test_contact_stage_and_offline_help_render_on_client_list(self, client, staff_user):
        client.force_login(staff_user)

        body = client.get(reverse("core:client_list")).content.decode()

        assert 'data-context-help="contact-stage"' in body
        assert 'data-context-help="offline"' in body
        assert "Was bedeutet die Kontaktstufe?" in body
        assert "Wie funktioniert die Offline-Arbeit?" in body

    def test_condition_help_renders_in_document_type_board(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)

        body = client.get(reverse("core:config_condition_board", kwargs={"pk": doc_type_contact.pk})).content.decode()

        assert 'data-context-help="document-conditions"' in body
        assert "Wie funktionieren Feldbedingungen?" in body

    def test_retention_help_renders_on_dashboard(self, client, lead_user):
        client.force_login(lead_user)

        body = client.get(reverse("core:retention_dashboard")).content.decode()

        assert 'data-context-help="retention"' in body
        assert "Wie funktionieren Löschfristen?" in body

    def test_four_eyes_help_renders_on_client_detail(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        body_without_request = client.get(
            reverse("core:client_detail", kwargs={"pk": client_identified.pk})
        ).content.decode()

        assert 'data-context-help="four-eyes"' not in body_without_request

        request_client_deletion(client_identified, staff_user, "Widerruf")

        body = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk})).content.decode()

        assert 'data-context-help="four-eyes"' in body
        assert "Wie funktioniert das Vier-Augen-Prinzip?" in body
        assert body.index('data-testid="client-deletion-pending-banner"') < body.index('data-context-help="four-eyes"')

    def test_precached_client_list_has_no_online_only_help_link(self, client):
        body = client.get(reverse("core:offline_client_list_shell")).content.decode()

        assert 'data-context-help="contact-stage"' in body
        assert 'href="/hilfe/personen/"' not in body

    def test_shared_component_uses_native_details_without_active_content(self):
        html = render_to_string(
            "components/_context_help.html",
            {
                "help_id": "probe",
                "help_text": "Erklärung",
                "help_url": "/hilfe/",
                "summary_label": "Wie funktioniert die Probe?",
            },
        )

        assert '<details data-context-help="probe"' in html
        assert "<summary" in html
        assert "Wie funktioniert die Probe?" in html
        assert "Was ist das?" not in html
        assert "<script" not in html
        assert "<style" not in html
        assert "style=" not in html


class TestHelpNavigationIcon:
    def test_help_uses_its_own_rendered_icon(self):
        help_icon = render_to_string("partials/_nav_icons.html", {"icon": "help"})
        privacy_icon = render_to_string("partials/_nav_icons.html", {"icon": "datenschutz"})

        assert nav.HILFE.icon == "help"
        assert "<svg" in help_icon
        assert help_icon != privacy_icon


class TestHelpTranslations:
    def test_key_help_strings_are_available_in_english(self):
        with override("en"):
            assert gettext("Hilfe") == "Help"
            assert gettext("Was bedeutet die Kontaktstufe?") == "What does the contact level mean?"
            assert gettext("Wie funktionieren Feldbedingungen?") == "How do field conditions work?"
            assert gettext("Wie funktionieren Löschfristen?") == "How do deletion periods work?"
            assert gettext("Wie funktioniert das Vier-Augen-Prinzip?") == "How does the four-eyes principle work?"
            assert gettext("Wie funktioniert die Offline-Arbeit?") == "How does offline work?"
            assert gettext("Mehr erfahren") == "Learn more"
            assert gettext("Hilfe-Übersicht") == "Help overview"
