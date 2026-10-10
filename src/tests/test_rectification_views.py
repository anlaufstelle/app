"""View-Tests des Berichtigungsworkflows (ADR-041, Refs #1060).

Die reine Rollen-Matrix (staff+ fuer den Antrag, lead+ fuer Liste und Pruefung)
und die IDOR-Proben laufen deklarativ ueber ``_authz_expectations.py``
(``test_authz_matrix.py`` / ``test_authz_idor.py``). Hier steht, was die Matrix
nicht sieht: die Happy-Paths, das Verhalten der Formulare und die Stellen, an
denen ein Fachfehler eine Meldung statt eines 500 ergeben muss.
"""

import pytest
from django.urls import reverse

from core.models import RectificationRequest
from core.services.client.rectification import (
    decode_rectification_text,
    decode_rectification_value,
    request_rectification,
)


def _login(client, user):
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestCreateView:
    def test_form_offers_only_visible_fields(self, client, client_identified, doc_type_client_profile, staff_user):
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])
        _login(client, staff_user)
        response = client.get(reverse("core:rectification_create", args=[client_identified.pk]))
        assert response.status_code == 200
        choices = dict(response.context["form"].fields["field_path"].choices)
        assert "pseudonym" in choices
        assert "data_json.wohnsituation" in choices
        assert "contact_stage" not in choices

    def test_post_creates_pending_request(self, client, client_identified, staff_user):
        _login(client, staff_user)
        response = client.post(
            reverse("core:rectification_create", args=[client_identified.pk]),
            {"field_path": "notes", "new_value": "Korrigierte Notiz", "reason": "Falsch protokolliert"},
        )
        assert response.status_code == 302
        rr = RectificationRequest.objects.get(target_client=client_identified)
        assert rr.status == RectificationRequest.Status.PENDING
        assert rr.requested_by == staff_user
        # Nichts wird sofort angewendet — erst die Genehmigung schreibt.
        client_identified.refresh_from_db()
        assert client_identified.notes != "Korrigierte Notiz"

    def test_date_field_post_is_no_500(self, client, facility, client_identified, doc_type_client_profile, staff_user):
        """DATE-Slug ueber das FORMULAR — ``parse_default`` liefert ein ``date``.

        Die View faengt nur ``ValidationError``; ein ``TypeError`` aus
        ``json.dumps`` waere ein glatter 500 im Antragspfad.
        """
        from core.models import DocumentTypeField, FieldTemplate

        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Erstkontakt",
            field_type=FieldTemplate.FieldType.DATE,
        )
        DocumentTypeField.objects.create(document_type=doc_type_client_profile, field_template=ft, sort_order=11)
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])

        _login(client, staff_user)
        response = client.post(
            reverse("core:rectification_create", args=[client_identified.pk]),
            {"field_path": f"data_json.{ft.slug}", "new_value": "2026-01-15", "reason": "Falsches Datum"},
        )
        assert response.status_code == 302
        rr = RectificationRequest.objects.get(target_client=client_identified)
        assert decode_rectification_value(rr.new_value) == "2026-01-15"

    def test_manipulated_field_path_is_refused(self, client, client_identified, staff_user):
        _login(client, staff_user)
        response = client.post(
            reverse("core:rectification_create", args=[client_identified.pk]),
            {"field_path": "contact_stage", "new_value": "qualified", "reason": "Grund"},
        )
        assert response.status_code == 200
        assert response.context["form"].errors
        assert not RectificationRequest.objects.filter(target_client=client_identified).exists()

    def test_second_open_request_shows_form_error(self, client, client_identified, staff_user):
        request_rectification(client_identified, staff_user, "notes", "A", "Grund")
        _login(client, staff_user)
        response = client.post(
            reverse("core:rectification_create", args=[client_identified.pk]),
            {"field_path": "notes", "new_value": "B", "reason": "Grund"},
        )
        assert response.status_code == 200
        assert response.context["form"].non_field_errors()
        assert RectificationRequest.objects.filter(target_client=client_identified).count() == 1

    def test_anonymized_client_is_not_rectifiable(self, client, client_identified, staff_user):
        """Widerrufene Personen sind 404 — es gibt nichts mehr zu berichtigen."""
        client_identified.is_active = False
        client_identified.save(update_fields=["is_active"])
        _login(client, staff_user)
        response = client.get(reverse("core:rectification_create", args=[client_identified.pk]))
        assert response.status_code == 404

    def test_foreign_client_is_404_before_payload_validation(self, client, foreign_client, staff_user):
        _login(client, staff_user)
        response = client.post(
            reverse("core:rectification_create", args=[foreign_client.pk]),
            {"field_path": "notes", "new_value": "X", "reason": "Grund"},
        )
        assert response.status_code == 404
        assert not RectificationRequest.objects.filter(target_client=foreign_client).exists()


@pytest.mark.django_db
class TestListView:
    def test_lists_only_own_facility(self, client, rectification_request, foreign_rectification_request, lead_user):
        _login(client, lead_user)
        response = client.get(reverse("core:rectification_list"))
        assert response.status_code == 200
        pending = response.context["pending_requests"]
        assert rectification_request in pending
        assert foreign_rectification_request not in pending

    def test_pending_list_shows_the_field_label_not_the_raw_path(self, client, rectification_request, lead_user):
        """Die Liste zeigt dasselbe „Label (pfad)" wie die Pruefansicht (Refs #1793).

        Ein roher ``field_path`` (``notes``, ``data_json.<slug>``) ist fuer die
        pruefende Leitung nicht lesbar — sie muesste jeden Antrag oeffnen, nur
        um zu sehen, um welches Feld es geht.
        """
        _login(client, lead_user)
        response = client.get(reverse("core:rectification_list"))
        content = response.content.decode()
        assert "Notizen" in content
        assert "(notes)" in content

    def test_own_request_shows_no_review_link(self, client, client_identified, lead_user):
        request_rectification(client_identified, lead_user, "notes", "A", "Grund")
        _login(client, lead_user)
        response = client.get(reverse("core:rectification_list"))
        content = response.content.decode()
        assert "rectification-own-request-hint" in content
        assert "rectification-review-link" not in content


@pytest.mark.django_db
class TestReviewView:
    def test_review_page_shows_decrypted_values(self, client, rectification_request, lead_user):
        _login(client, lead_user)
        response = client.get(reverse("core:rectification_review", args=[rectification_request.pk]))
        assert response.status_code == 200
        assert response.context["new_value_display"] == "AuthZ-Matrix-Wunschwert"
        assert response.context["reason_text"] == "AuthZ-Matrix-Test"

    def test_approve_applies_the_change(self, client, rectification_request, client_identified, lead_user):
        _login(client, lead_user)
        response = client.post(
            reverse("core:rectification_review", args=[rectification_request.pk]),
            {"action": "approve", "review_note": "Nachweis lag vor"},
        )
        assert response.status_code == 302
        client_identified.refresh_from_db()
        assert client_identified.notes == "AuthZ-Matrix-Wunschwert"
        rectification_request.refresh_from_db()
        assert rectification_request.status == RectificationRequest.Status.APPROVED
        assert decode_rectification_text(rectification_request.review_note) == "Nachweis lag vor"

    def test_reject_requires_a_note(self, client, rectification_request, lead_user):
        _login(client, lead_user)
        response = client.post(
            reverse("core:rectification_review", args=[rectification_request.pk]),
            {"action": "reject", "review_note": "   "},
        )
        assert response.status_code == 400
        rectification_request.refresh_from_db()
        assert rectification_request.status == RectificationRequest.Status.PENDING

    def test_reject_with_note(self, client, rectification_request, lead_user):
        _login(client, lead_user)
        response = client.post(
            reverse("core:rectification_review", args=[rectification_request.pk]),
            {"action": "reject", "review_note": "Wahrnehmungsfrage"},
        )
        assert response.status_code == 302
        rectification_request.refresh_from_db()
        assert rectification_request.status == RectificationRequest.Status.REJECTED

    def test_self_review_is_blocked_in_the_view(self, client, client_identified, lead_user):
        rr = request_rectification(client_identified, lead_user, "notes", "A", "Grund")
        _login(client, lead_user)

        page = client.get(reverse("core:rectification_review", args=[rr.pk]))
        assert "rectification-self-review-blocked" in page.content.decode()
        assert "rectification-approve" not in page.content.decode()

        response = client.post(
            reverse("core:rectification_review", args=[rr.pk]),
            {"action": "approve", "review_note": ""},
        )
        assert response.status_code == 400
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING
        client_identified.refresh_from_db()
        assert client_identified.notes != "A"

    def test_invisible_field_hides_the_values_and_the_buttons(
        self, client, facility, client_identified, doc_type_client_profile, staff_user, lead_user
    ):
        """Faellt das Feld am Typ weg, darf die Pruefseite keine Werte mehr zeigen.

        Genehmigen liesse sich der Antrag ohnehin nicht (Pfad-Re-Validierung im
        Service) — die Seite darf den entschluesselten Inhalt dann auch gar nicht
        erst rendern.
        """
        from core.models import DocumentTypeField, FieldTemplate

        ft = FieldTemplate.objects.create(facility=facility, name="Anmerkung", field_type=FieldTemplate.FieldType.TEXT)
        DocumentTypeField.objects.create(document_type=doc_type_client_profile, field_template=ft, sort_order=8)
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])
        rr = request_rectification(client_identified, staff_user, "data_json.anmerkung", "Geheimwert", "Grund")
        DocumentTypeField.objects.filter(document_type=doc_type_client_profile, field_template=ft).delete()

        _login(client, lead_user)
        response = client.get(reverse("core:rectification_review", args=[rr.pk]))
        content = response.content.decode()
        assert response.context["field_visible"] is False
        assert "Geheimwert" not in content
        assert "rectification-field-restricted" in content
        assert "rectification-approve" not in content

    def test_closed_request_is_404(self, client, rectification_request, lead_user):
        rectification_request.status = RectificationRequest.Status.APPROVED
        rectification_request.save(update_fields=["status"])
        _login(client, lead_user)
        response = client.get(reverse("core:rectification_review", args=[rectification_request.pk]))
        assert response.status_code == 404

    def test_revoked_client_request_is_404(self, client, rectification_request, client_identified, lead_user):
        """S5-Restluecke (Refs #1060): der offene Antrag einer anonymisierten
        Person ist kein pruefbarer Vorgang mehr — die Pruefansicht darf ihn
        weder rendern noch per POST re-kontaminieren (Widerruf-Guard-Spiegel)."""
        client_identified.is_active = False
        client_identified.save(update_fields=["is_active"])
        _login(client, lead_user)
        url = reverse("core:rectification_review", args=[rectification_request.pk])
        assert client.get(url).status_code == 404
        assert client.post(url, {"action": "reject", "review_note": "Vermerk"}).status_code == 404
        rectification_request.refresh_from_db()
        assert rectification_request.status == RectificationRequest.Status.PENDING
        assert rectification_request.review_note is None

    def test_conflict_shows_message_not_500(self, client, rectification_request, client_identified, lead_user):
        from core.services.client import update_client

        update_client(client_identified, lead_user, notes="Jemand anderes war schneller")
        _login(client, lead_user)
        response = client.post(
            reverse("core:rectification_review", args=[rectification_request.pk]),
            {"action": "approve", "review_note": ""},
        )
        assert response.status_code == 400
        rectification_request.refresh_from_db()
        assert rectification_request.status == RectificationRequest.Status.PENDING


@pytest.mark.django_db
class TestClientDetailIntegration:
    def test_staff_sees_entry_point_and_open_count(self, client, rectification_request, staff_user, client_identified):
        _login(client, staff_user)
        response = client.get(reverse("core:client_detail", args=[client_identified.pk]))
        content = response.content.decode()
        assert response.context["open_rectification_count"] == 1
        assert "client-rectification-create-link" in content
        assert "client-rectification-pending-badge" in content
        # Der Link in die Pruefliste ist lead+ — die Fachkraft sieht ihn nicht.
        assert "client-rectification-list-link" not in content

    def test_assistant_sees_no_entry_point(self, client, client_identified, assistant_user):
        _login(client, assistant_user)
        response = client.get(reverse("core:client_detail", args=[client_identified.pk]))
        assert "client-rectification-create-link" not in response.content.decode()

    def test_lead_sees_the_list_link(self, client, rectification_request, lead_user, client_identified):
        _login(client, lead_user)
        response = client.get(reverse("core:client_detail", args=[client_identified.pk]))
        assert "client-rectification-list-link" in response.content.decode()
