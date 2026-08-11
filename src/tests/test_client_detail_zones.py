"""Zonenregel der Personenakte (Konzept #1801 §6.1, Refs #1813 T4).

Zustandshinweise (Hausverbot, laufender Löschantrag, Berichtigung) stehen als
Banner **zwischen Kopf und Inhalt**, in fester Reihenfolge — Zustände sind
keine Aktionen und gehören nie in den Overflow. §6.6 (Rückkanal) ist bereits
gebaut (Refs #1736); dieser Test schreibt nur die bestehende Reihenfolge fest
und ändert an ihr nichts (N5, Design-Report §E.6).
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.client import request_client_deletion
from core.services.client.rectification import request_rectification

pytestmark = pytest.mark.django_db


@pytest.fixture
def doc_type_hausverbot(facility):
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
    )
    ft_grund = FieldTemplate.objects.create(
        facility=facility, name="Grund", field_type=FieldTemplate.FieldType.TEXTAREA
    )
    ft_bis = FieldTemplate.objects.create(facility=facility, name="Bis", field_type=FieldTemplate.FieldType.DATE)
    ft_aktiv = FieldTemplate.objects.create(facility=facility, name="Aktiv", field_type=FieldTemplate.FieldType.BOOLEAN)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_grund, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_bis, sort_order=1)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_aktiv, sort_order=2)
    return doc_type


@pytest.fixture
def active_ban_event(facility, client_identified, staff_user, doc_type_hausverbot):
    future_date = (date.today() + timedelta(days=30)).isoformat()
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_hausverbot,
        occurred_at=timezone.now(),
        data_json={"grund": "Bedrohung", "bis": future_date, "aktiv": True},
        created_by=staff_user,
    )


class TestStateBannersPrecedeContent:
    def test_state_banners_precede_content_and_keep_their_order(
        self, client, staff_user, client_identified, active_ban_event
    ):
        """Hausverbot → Löschantrag → Berichtigung, alle drei vor der
        Kontakt-Chronik (dem ersten Inhaltsblock)."""
        request_client_deletion(client_identified, staff_user, "Widerruf")
        request_rectification(client_identified, staff_user, "notes", "Neuer Wert", "Grund")

        client.force_login(staff_user)
        content = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()

        ban_idx = content.index('class="mb-4 space-y-2"')  # Hausverbot-Banner-Container
        deletion_idx = content.index('data-testid="client-deletion-pending-banner"')
        rectification_idx = content.index('data-testid="client-rectification-section"')
        content_idx = content.index(">Kontakt-Chronik<")

        assert ban_idx < deletion_idx < rectification_idx < content_idx, (
            ban_idx,
            deletion_idx,
            rectification_idx,
            content_idx,
        )

    def test_offline_conflict_banner_precedes_the_page_content_block(self, client, staff_user, client_identified):
        """Der globale Offline-Konflikt-Banner (base.html) rendert vor
        ``{% block content %}`` auf JEDER Seite — strukturell garantiert,
        hier nur als Beleg fürs Personen-Detail."""
        client.force_login(staff_user)
        content = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()

        conflict_banner_idx = content.index("showConflictBanner")
        content_idx = content.index(">Kontakt-Chronik<")

        assert conflict_banner_idx < content_idx

    def test_rectification_section_still_gated_staff_plus(self, client, assistant_user, client_identified):
        """Gegenprobe: die Zonenregel ändert nichts an den bestehenden
        Rollen-Gates (Refs #1060/ADR-041)."""
        client.force_login(assistant_user)
        content = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()
        assert 'data-testid="client-rectification-section"' not in content
