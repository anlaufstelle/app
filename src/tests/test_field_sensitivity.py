"""Tests für rollenbasierte Feldsensitivität in Event-Views (Issue #113)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Case, DocumentType, DocumentTypeField, Event, FieldTemplate


@pytest.fixture
def doc_type_high(facility):
    """DocumentType mit HIGH-Sensibilität und einem verschlüsselten Feld."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.HIGH,
        name="Hochsensibel",
    )
    ft_secret = FieldTemplate.objects.create(
        facility=facility,
        name="Geheimfeld",
        field_type=FieldTemplate.FieldType.TEXT,
        is_encrypted=True,
        sensitivity="high",
    )
    ft_normal = FieldTemplate.objects.create(
        facility=facility,
        name="Normalfeld",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_secret, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_normal, sort_order=1)
    return doc_type


@pytest.fixture
def doc_type_elevated(facility):
    """DocumentType mit ELEVATED-Sensibilität."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        name="ErhoehtSensibel",
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="ErhoehtFeld",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
    return doc_type


@pytest.fixture
def event_high(facility, doc_type_high, staff_user):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_high,
        occurred_at=timezone.now(),
        data_json={"geheimfeld": "secret-value", "normalfeld": "normal-value"},
        created_by=staff_user,
    )


@pytest.fixture
def event_elevated(facility, doc_type_elevated, staff_user):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_elevated,
        occurred_at=timezone.now(),
        data_json={"erhoehtfeld": "elevated-value"},
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestEventDetailFieldSensitivity:
    """EventDetailView zeigt Felder rollenabhängig an."""

    def test_admin_sees_all_fields(self, client, admin_user, event_high):
        client.force_login(admin_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "secret-value" in content
        assert "normal-value" in content
        assert "[Eingeschränkt]" not in content

    def test_lead_sees_all_fields(self, client, lead_user, event_high):
        client.force_login(lead_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "secret-value" in content
        assert "normal-value" in content

    def test_staff_cannot_see_high_sensitivity(self, client, staff_user, event_high):
        """Staff gets 404 for a HIGH-sensitivity event — existence must not leak."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        assert response.status_code == 404

    def test_staff_can_see_elevated(self, client, staff_user, event_elevated):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_elevated.pk}))
        content = response.content.decode()
        assert "elevated-value" in content
        assert "[Eingeschränkt]" not in content

    def test_assistant_cannot_see_elevated(self, client, assistant_user, event_elevated):
        """Assistant gets 404 for an ELEVATED event — existence must not leak."""
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_elevated.pk}))
        assert response.status_code == 404

    def test_assistant_can_see_normal(self, client, assistant_user, sample_event):
        """sample_event has NORMAL sensitivity doc type."""
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk}))
        content = response.content.decode()
        assert "Testnotiz" in content
        assert "[Eingeschränkt]" not in content

    def test_encrypted_field_restricted_for_staff(self, client, staff_user, facility):
        """Even on NORMAL doc type, an encrypted field is HIGH → hidden for staff."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="NormalMitEncrypted",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        ft_enc = FieldTemplate.objects.create(
            facility=facility,
            name="EncField",
            field_type=FieldTemplate.FieldType.TEXT,
            is_encrypted=True,
            sensitivity="high",
        )
        ft_plain = FieldTemplate.objects.create(
            facility=facility,
            name="PlainField",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_enc, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_plain, sort_order=1)
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"encfield": "enc-val", "plainfield": "plain-val"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        content = response.content.decode()
        assert "plain-val" in content
        assert "enc-val" not in content
        assert "[Eingeschränkt]" in content

    def test_encrypted_without_sensitivity_visible_for_staff(self, client, staff_user, facility):
        """Encrypted field WITHOUT sensitivity override → staff CAN see it.

        Proves that is_encrypted alone no longer restricts visibility.
        """
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="NormalEncNoSens",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        ft_enc = FieldTemplate.objects.create(
            facility=facility,
            name="EncNoRestrict",
            field_type=FieldTemplate.FieldType.TEXT,
            is_encrypted=True,
            # sensitivity="" (default) — keine Sichtbarkeitsbeschränkung
        )
        ft_plain = FieldTemplate.objects.create(
            facility=facility,
            name="PlainNoRestrict",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_enc, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_plain, sort_order=1)
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"encnorestrict": "enc-visible", "plainnorestrict": "plain-visible"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        content = response.content.decode()
        # Staff sees both field labels — is_encrypted alone doesn't restrict visibility
        assert "PlainNoRestrict" in content
        assert "EncNoRestrict" in content
        assert "[Eingeschränkt]" not in content

    def test_sensitivity_high_without_encrypted_restricted_for_staff(self, client, staff_user, facility):
        """Non-encrypted field WITH sensitivity='high' → staff CANNOT see it.

        Proves that sensitivity alone controls visibility, independent of encryption.
        """
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="NormalMitHighField",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        ft_high = FieldTemplate.objects.create(
            facility=facility,
            name="HighNoEncrypt",
            field_type=FieldTemplate.FieldType.TEXT,
            is_encrypted=False,
            sensitivity="high",
        )
        ft_plain = FieldTemplate.objects.create(
            facility=facility,
            name="PlainControl",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_high, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_plain, sort_order=1)
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"highnoencrypt": "secret-plain-val", "plaincontrol": "visible-val"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        content = response.content.decode()
        assert "visible-val" in content  # plain field visible
        assert "secret-plain-val" not in content  # high-sensitivity field hidden
        assert "[Eingeschränkt]" in content


@pytest.mark.django_db
class TestEventUpdateFieldSensitivity:
    """EventUpdateView blendet sensitive Felder im Formular aus."""

    def test_admin_sees_all_form_fields(self, client, admin_user, event_high):
        client.force_login(admin_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "Geheimfeld" in content
        assert "Normalfeld" in content

    def test_staff_update_high_sensitivity_returns_404(self, client, staff_user, event_high):
        """Staff is blocked from the edit route for a HIGH event — 404, not empty form."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event_high.pk}))
        assert response.status_code == 404

    def test_assistant_update_elevated_returns_404(self, client, assistant_user, event_elevated):
        """Assistant is blocked from the edit route for an ELEVATED event — 404."""
        event_elevated.created_by = assistant_user
        event_elevated.save()
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event_elevated.pk}))
        assert response.status_code == 404

    def test_staff_post_on_high_event_returns_404_and_preserves_data(self, client, staff_user, event_high):
        """Staff POST to the edit route of a HIGH event is 404 and leaves data untouched."""
        original_data = event_high.data_json.copy()
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": event_high.pk}),
            {},
        )
        assert response.status_code == 404
        event_high.refresh_from_db()
        assert event_high.data_json == original_data


@pytest.mark.django_db
class TestEventVisibleToManager:
    """Event.objects.visible_to(user) is the central manager method that
    filters events by the user's role-based sensitivity rank. (Issue #522)
    """

    def test_admin_sees_all_events(self, admin_user, event_high, event_elevated, sample_event):
        ids = set(Event.objects.visible_to(admin_user).values_list("pk", flat=True))
        assert event_high.pk in ids
        assert event_elevated.pk in ids
        assert sample_event.pk in ids

    def test_lead_sees_all_events(self, lead_user, event_high, event_elevated, sample_event):
        ids = set(Event.objects.visible_to(lead_user).values_list("pk", flat=True))
        assert event_high.pk in ids
        assert event_elevated.pk in ids
        assert sample_event.pk in ids

    def test_staff_does_not_see_high_events(self, staff_user, event_high, event_elevated, sample_event):
        ids = set(Event.objects.visible_to(staff_user).values_list("pk", flat=True))
        assert event_high.pk not in ids
        assert event_elevated.pk in ids
        assert sample_event.pk in ids

    def test_assistant_does_not_see_elevated_or_high(self, assistant_user, event_high, event_elevated, sample_event):
        ids = set(Event.objects.visible_to(assistant_user).values_list("pk", flat=True))
        assert event_high.pk not in ids
        assert event_elevated.pk not in ids
        assert sample_event.pk in ids

    def test_chainable_with_filter(self, staff_user, event_elevated):
        """visible_to() returns a chainable QuerySet (not a list)."""
        qs = Event.objects.visible_to(staff_user).filter(pk=event_elevated.pk)
        assert qs.count() == 1


@pytest.mark.django_db
class TestClientDetailHidesEventsBySensitivity:
    """ClientDetailView event timeline must filter by sensitivity (#522)."""

    def test_assistant_does_not_see_high_event_in_client_detail(
        self, client, assistant_user, facility, doc_type_high, client_identified, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(assistant_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        assert "Hochsensibel" not in content

    def test_staff_sees_high_event_in_client_detail(
        self, client, lead_user, facility, doc_type_high, client_identified, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        assert "Hochsensibel" in content


@pytest.mark.django_db
class TestCaseDetailHidesEventsBySensitivity:
    """Case detail event list must filter by sensitivity (#522).

    CaseDetailView is StaffRequiredMixin, so the leak is between staff (max
    ELEVATED) and lead/admin (max HIGH). A staff user must not see HIGH events
    in the case timeline.
    """

    def test_staff_does_not_see_high_event_in_case_detail(
        self, client, staff_user, facility, doc_type_high, client_identified
    ):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Testfall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            case=case,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": case.pk}))
        content = response.content.decode()
        assert "Hochsensibel" not in content

    def test_lead_sees_high_event_in_case_detail(
        self, client, lead_user, facility, doc_type_high, client_identified, staff_user
    ):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Testfall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            case=case,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": case.pk}))
        content = response.content.decode()
        assert "Hochsensibel" in content


@pytest.mark.django_db
class TestZeitstromHidesEventsBySensitivity:
    """Zeitstrom feed must filter events by sensitivity (#522)."""

    def test_assistant_does_not_see_elevated_event_in_zeitstrom(
        self, client, assistant_user, facility, doc_type_elevated, client_identified, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_elevated,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(assistant_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert "ErhoehtSensibel" not in content


@pytest.mark.django_db
class TestHandoverHidesEventsBySensitivity:
    """Handover summary highlights and stats must filter by sensitivity (#522)."""

    def test_handover_summary_hides_high_events_for_staff(self, facility, staff_user, doc_type_high, client_identified):
        from core.services.handover import build_handover_summary

        # Mark the doc type as system_type='crisis' so it shows up in highlights
        doc_type_high.system_type = "crisis"
        doc_type_high.save()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        summary = build_handover_summary(facility, timezone.localdate(), None, staff_user)
        crisis_highlights = [h for h in summary["highlights"] if h["type"] == "crisis"]
        assert len(crisis_highlights) == 0
        assert summary["stats"]["events_total"] == 0

    def test_handover_summary_shows_high_events_for_lead(
        self, facility, lead_user, staff_user, doc_type_high, client_identified
    ):
        from core.services.handover import build_handover_summary

        doc_type_high.system_type = "crisis"
        doc_type_high.save()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        summary = build_handover_summary(facility, timezone.localdate(), None, lead_user)
        crisis_highlights = [h for h in summary["highlights"] if h["type"] == "crisis"]
        assert len(crisis_highlights) == 1
        assert summary["stats"]["events_total"] == 1
