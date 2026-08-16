"""Integration der Inline-Zahleneingabe in die Strichliste (Refs #1906)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, EventHistory, FieldTemplate
from core.reporting.backends.needle_exchange_balance import NeedleExchangeBalanceBackend
from core.services.events import build_tally_summary


def _add_field(
    document_type,
    *,
    name,
    required=True,
    field_type=FieldTemplate.FieldType.NUMBER,
    sensitivity="",
    report_key=None,
    min_value=None,
    sort_order=0,
):
    field_template = FieldTemplate.objects.create(
        facility=document_type.facility,
        name=name,
        field_type=field_type,
        is_required=required,
        sensitivity=sensitivity,
        report_key=report_key,
        min_value=min_value,
    )
    DocumentTypeField.objects.create(
        document_type=document_type,
        field_template=field_template,
        sort_order=sort_order,
    )
    return field_template


def _number_tally_type(facility, *, name="Spritzentausch", report_key=None):
    document_type = DocumentType.objects.create(
        facility=facility,
        name=name,
        tally_enabled=True,
        report_key=report_key,
    )
    ausgabe = _add_field(
        document_type,
        name="Ausgabe",
        report_key="ausgabe_stueck" if report_key else None,
        sort_order=0,
    )
    rueckgabe = _add_field(
        document_type,
        name="Rueckgabe",
        report_key="rueckgabe_stueck" if report_key else None,
        sort_order=1,
    )
    return document_type, ausgabe, rueckgabe


def _summary_row(summary, document_type):
    return next((row for row in summary["rows"] if row["document_type"].pk == document_type.pk), None)


@pytest.mark.django_db
class TestTallyNumberEligibility:
    def test_summary_includes_number_required_type_and_marks_input(self, facility, staff_user):
        document_type, _ausgabe, _rueckgabe = _number_tally_type(facility)

        row = _summary_row(build_tally_summary(facility, staff_user), document_type)

        assert row is not None
        assert row["requires_number_input"] is True

    def test_summary_marks_direct_type_without_required_fields(self, facility, staff_user):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Direktkontakt",
            tally_enabled=True,
        )

        row = _summary_row(build_tally_summary(facility, staff_user), document_type)

        assert row is not None
        assert row["requires_number_input"] is False

    def test_summary_excludes_mixed_required_fields(self, facility, staff_user):
        document_type, _ausgabe, _rueckgabe = _number_tally_type(facility)
        _add_field(
            document_type,
            name="Pflichtnotiz",
            field_type=FieldTemplate.FieldType.TEXT,
            sort_order=2,
        )

        assert _summary_row(build_tally_summary(facility, staff_user), document_type) is None

    def test_summary_excludes_restricted_required_field_for_staff_but_not_lead(
        self,
        facility,
        staff_user,
        lead_user,
    ):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Hochsensible Zahl",
            tally_enabled=True,
        )
        _add_field(
            document_type,
            name="Verdeckte Zahl",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )

        assert _summary_row(build_tally_summary(facility, staff_user), document_type) is None
        lead_row = _summary_row(build_tally_summary(facility, lead_user), document_type)
        assert lead_row is not None
        assert lead_row["requires_number_input"] is True


@pytest.mark.django_db
class TestTallyNumberIncrementView:
    def test_get_renders_number_form_and_cancel_restores_action(
        self,
        client,
        facility,
        staff_user,
    ):
        document_type, ausgabe, rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)
        url = reverse("core:tally_increment")

        response = client.get(
            url,
            {"document_type": str(document_type.pk)},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert f'data-testid="tally-inline-form-{document_type.pk}"' in content
        assert f'name="{ausgabe.slug}"' in content
        assert f'name="{rueckgabe.slug}"' in content

        cancel = client.get(
            url,
            {"document_type": str(document_type.pk), "cancel": "1"},
            HTTP_HX_REQUEST="true",
        )
        cancel_content = cancel.content.decode()
        assert cancel.status_code == 200
        assert f'data-testid="tally-increment-{document_type.pk}"' in cancel_content
        assert f'data-testid="tally-inline-form-{document_type.pk}"' not in cancel_content

    def test_get_rejects_type_without_required_values(self, client, facility, staff_user):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Direktkontakt",
            tally_enabled=True,
        )
        client.force_login(staff_user)

        response = client.get(
            reverse("core:tally_increment"),
            {"document_type": str(document_type.pk)},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400

    def test_valid_post_creates_one_anonymous_event_with_only_required_numbers(
        self,
        client,
        facility,
        staff_user,
    ):
        document_type, ausgabe, rueckgabe = _number_tally_type(facility)
        optional = _add_field(
            document_type,
            name="Optionale interne Zahl",
            required=False,
            sensitivity=DocumentType.Sensitivity.HIGH,
            sort_order=2,
        )
        client.force_login(staff_user)
        audit_before = AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE).count()

        response = client.post(
            reverse("core:tally_increment"),
            {
                "document_type": str(document_type.pk),
                ausgabe.slug: "12",
                rueckgabe.slug: "5",
                optional.slug: "99",
                "unbekannt": "77",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert response["HX-Retarget"] == "#tally-panel"
        assert response["HX-Reswap"] == "outerHTML"
        event = Event.objects.get(document_type=document_type)
        assert event.is_anonymous is True
        assert event.client is None
        assert event.data_json == {ausgabe.slug: 12, rueckgabe.slug: 5}
        assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE).count() == audit_before + 1
        assert EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).count() == 1

    def test_get_foreign_facility_type_returns_404(
        self,
        client,
        organization,
        staff_user,
    ):
        from core.models import Facility

        other_facility = Facility.objects.create(organization=organization, name="Andere Einrichtung")
        document_type, _ausgabe, _rueckgabe = _number_tally_type(other_facility)
        client.force_login(staff_user)

        response = client.get(
            reverse("core:tally_increment"),
            {"document_type": str(document_type.pk)},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 404

    @pytest.mark.parametrize("ausgabe", ["", "keine-zahl", "3.5"])
    def test_invalid_number_post_rerenders_inline_form_without_event(
        self,
        client,
        facility,
        staff_user,
        ausgabe,
    ):
        document_type, ausgabe_field, rueckgabe = _number_tally_type(facility)
        client.force_login(staff_user)

        response = client.post(
            reverse("core:tally_increment"),
            {
                "document_type": str(document_type.pk),
                ausgabe_field.slug: ausgabe,
                rueckgabe.slug: "2",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert f'data-testid="tally-inline-form-{document_type.pk}"' in response.content.decode()
        assert "HX-Retarget" not in response
        assert not Event.objects.filter(document_type=document_type).exists()

    def test_post_rejects_non_number_required_field(self, client, facility, staff_user):
        document_type, ausgabe, rueckgabe = _number_tally_type(facility)
        text = _add_field(
            document_type,
            name="Begruendung",
            field_type=FieldTemplate.FieldType.TEXT,
            sort_order=2,
        )
        client.force_login(staff_user)

        response = client.post(
            reverse("core:tally_increment"),
            {
                "document_type": str(document_type.pk),
                ausgabe.slug: "3",
                rueckgabe.slug: "1",
                text.slug: "umgehen",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert not Event.objects.filter(document_type=document_type).exists()

    def test_get_and_post_for_restricted_required_field_are_forbidden(
        self,
        client,
        facility,
        staff_user,
    ):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Hochsensible Zahl",
            tally_enabled=True,
        )
        required = _add_field(
            document_type,
            name="Verdeckte Zahl",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        client.force_login(staff_user)
        url = reverse("core:tally_increment")

        get_response = client.get(
            url,
            {"document_type": str(document_type.pk)},
            HTTP_HX_REQUEST="true",
        )
        post_response = client.post(
            url,
            {"document_type": str(document_type.pk), required.slug: "4"},
            HTTP_HX_REQUEST="true",
        )

        assert get_response.status_code == 403
        assert post_response.status_code == 403
        assert not Event.objects.filter(document_type=document_type).exists()

    def test_tally_event_contributes_to_needle_exchange_report(self, client, facility, admin_user):
        document_type, ausgabe, rueckgabe = _number_tally_type(
            facility,
            report_key="needle_exchange",
        )
        client.force_login(admin_user)

        response = client.post(
            reverse("core:tally_increment"),
            {
                "document_type": str(document_type.pk),
                ausgabe.slug: "12",
                rueckgabe.slug: "5",
            },
            HTTP_HX_REQUEST="true",
        )
        today = timezone.localdate()
        result = NeedleExchangeBalanceBackend().build_dataset(
            facility,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert response.status_code == 200
        assert result.summary["events"] == 1
        assert result.summary["summe_ausgabe"] == 12
        assert result.summary["summe_rueckgabe"] == 5
        assert result.summary["events_ohne_werte"] == 0
        assert result.completeness == []

    def test_negative_tally_value_is_rejected_before_it_reaches_needle_exchange_report(
        self, client, facility, admin_user
    ):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Spritzentausch",
            tally_enabled=True,
            report_key="needle_exchange",
        )
        ausgabe = _add_field(
            document_type,
            name="Ausgabe",
            report_key="ausgabe_stueck",
            min_value=0,
            sort_order=0,
        )
        rueckgabe = _add_field(
            document_type,
            name="Rückgabe",
            report_key="rueckgabe_stueck",
            min_value=0,
            sort_order=1,
        )
        client.force_login(admin_user)

        response = client.post(
            reverse("core:tally_increment"),
            {
                "document_type": str(document_type.pk),
                ausgabe.slug: "-1",
                rueckgabe.slug: "0",
            },
            HTTP_HX_REQUEST="true",
        )
        today = timezone.localdate()
        result = NeedleExchangeBalanceBackend().build_dataset(
            facility,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert response.status_code == 200
        assert not Event.objects.filter(document_type=document_type).exists()
        assert result.summary["events"] == 0
