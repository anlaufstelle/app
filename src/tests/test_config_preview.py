"""Tests für die Live-Formularvorschau in der Config-UI (M0 P11, Refs #1056).

Die Vorschau lädt das ECHTE Event-Formular-Partial ``core:event_fields_partial``
für den DocumentType per HTMX-GET und aktualisiert sich nach jeder Board-Mutation
über den ``HX-Trigger: refresh-preview``-Response-Header (CSP-konform, kein
Inline-JS). Deckt: Vorschau-Pane auf der Detailseite (aktiver Typ), Hinweis statt
Pane bei inaktivem Typ, die additive ``board_response``-Header-Erweiterung (ohne
sonstige Verhaltensänderung), den AuthZ-Pfad des Partials für facility_admin
sowie die CSP-Ladereihenfolge von ``alpine/config.js`` in ``base.html``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from core.models import DocumentType, DocumentTypeField, FieldGroup, FieldTemplate

BOARD_PARTIAL = "core/config/document_types/partials/field_board.html"


def _template_names(response):
    return [t.name for t in response.templates if t.name]


# ---------------------------------------------------------------------------
# Vorschau-Pane auf der Detailseite (aktiver Typ)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDetailPreviewPane:
    def test_detail_renders_preview_pane_with_hx_get_and_document_type_param(
        self, client, admin_user, doc_type_contact
    ):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        assert response.status_code == 200
        body = response.content.decode()
        expected_url = f"{reverse('core:event_fields_partial')}?document_type={doc_type_contact.pk}"
        assert f'hx-get="{expected_url}"' in body
        assert 'data-testid="config-preview-pane"' in body

    def test_preview_pane_refreshes_on_board_mutation_via_trigger(self, client, admin_user, doc_type_contact):
        """Die Vorschau lauscht CSP-konform auf das gebubbelte ``refresh-preview``-
        Event am Body — passend zum ``HX-Trigger``-Header der Board-Mutationen."""
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        body = response.content.decode()
        assert "load" in body
        assert "refresh-preview from:body" in body

    def test_preview_uses_configpreview_alpine_component(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        body = response.content.decode()
        assert 'x-data="configPreview"' in body
        assert "data-start-open" in body


# ---------------------------------------------------------------------------
# Inaktiver Typ → Hinweis statt Vorschau (Partial würde 404 filtern)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInactiveDocTypePreview:
    def test_inactive_doctype_shows_hint_not_preview(self, client, admin_user, facility):
        inactive = DocumentType.objects.create(
            facility=facility,
            name="Inaktiver Typ",
            is_active=False,
        )
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": inactive.pk}))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Vorschau nur für aktive Typen" in body
        # Kein HTMX-Vorschau-Pane (das Partial würde is_active=True filtern → 404).
        assert 'data-testid="config-preview-pane"' not in body
        assert "event_fields_partial" not in body
        assert reverse("core:event_fields_partial") not in body


# ---------------------------------------------------------------------------
# board_response-Erweiterung: HX-Trigger: refresh-preview (additiv)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBoardResponseRefreshHeader:
    def test_field_add_htmx_carries_refresh_trigger(self, client, admin_user, doc_type_contact, field_template):
        client.force_login(admin_user)
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"field_template": str(field_template.pk)}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        # #1718: neben der Vorschau stößt die Mutation auch das Bedingungs-Board an.
        assert response["HX-Trigger"] == "refresh-preview, refresh-conditions"
        # Verhalten sonst unverändert: weiterhin das Board-Partial.
        assert BOARD_PARTIAL in _template_names(response)

    def test_field_remove_htmx_carries_refresh_trigger(self, client, admin_user, doc_type_contact, doc_type_field):
        client.force_login(admin_user)
        url = reverse(
            "core:config_doctype_field_remove",
            kwargs={"pk": doc_type_contact.pk, "dtf_pk": doc_type_field.pk},
        )
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert response["HX-Trigger"] == "refresh-preview, refresh-conditions"

    def test_non_htmx_redirect_has_no_trigger_header(self, client, admin_user, doc_type_contact, doc_type_field):
        """Der Nicht-HX-Zweig (Redirect) trägt den Header NICHT — Verhalten unverändert."""
        client.force_login(admin_user)
        url = reverse(
            "core:config_doctype_field_remove",
            kwargs={"pk": doc_type_contact.pk, "dtf_pk": doc_type_field.pk},
        )
        response = client.post(url)
        assert response.status_code == 302
        assert not response.has_header("HX-Trigger")


# ---------------------------------------------------------------------------
# AuthZ: facility_admin darf das Vorschau-Partial abrufen (Gruppen-Header im Body)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPreviewPartialAuthZ:
    def test_facility_admin_gets_partial_with_group_header(self, client, admin_user, doc_type_contact, facility):
        # Ein Feld einer benannten Gruppe zuordnen → Gruppen-Header erscheint.
        group = FieldGroup.objects.create(document_type=doc_type_contact, name="Vorschau-Gruppe")
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Gruppenfeld-Preview",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(
            document_type=doc_type_contact,
            field_template=ft,
            field_group=group,
            sort_order=5,
        )
        client.force_login(admin_user)
        url = f"{reverse('core:event_fields_partial')}?document_type={doc_type_contact.pk}"
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Vorschau-Gruppe" in body
        assert "Gruppenfeld-Preview" in body


# ---------------------------------------------------------------------------
# CSP: alpine/config.js VOR alpine-csp.min.js laden (Reihenfolge-Guard)
# ---------------------------------------------------------------------------


class TestConfigJsLoadOrder:
    _BASE = Path("src/templates/base.html")
    _CONFIG_JS = Path("src/static/js/alpine/config.js")

    def test_config_js_loaded_before_alpine_csp(self):
        source = self._BASE.read_text()
        config_marker = "js/alpine/config.js"
        csp_marker = "js/alpine-csp.min.js"
        assert config_marker in source, "base.html lädt alpine/config.js nicht."
        assert csp_marker in source
        assert source.index(config_marker) < source.index(csp_marker), (
            "alpine/config.js muss VOR alpine-csp.min.js geladen werden, sonst ist "
            "die 'alpine:init'-Registrierung von configPreview nicht rechtzeitig da."
        )

    def test_config_js_registers_config_preview(self):
        source = self._CONFIG_JS.read_text()
        assert 'Alpine.data("configPreview"' in source
