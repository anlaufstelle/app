"""Settings with privacy consequences: minimum values and visible warnings.

k-anonymity threshold (ADR-023): the form rejects ``1``, ``2`` is stored with a warning on the
settings page and on the report pages, ``5`` stays silent. A stored value below the minimum is
logged; ``0`` computes with the default, ``1`` keeps computing as stored and every effective
value below 3 carries the warning on the settings page and on the report pages.
"""

from __future__ import annotations

import logging

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.forms.facility_settings import FacilitySettingsForm
from core.models import Settings
from core.services.dashboard.external_report import DEFAULT_K_THRESHOLD, _get_threshold
from tests.test_facility_settings_hub import _post_data

K_WARNING = "Die k-Anonymitäts-Schwelle liegt unter 3."
K_BELOW_MINIMUM = "liegt unter dem zulässigen Minimum 2"
K_NO_SUPPRESSION = "Bei k = 1 wird keine Zahl unterdrückt"

pytestmark = pytest.mark.django_db


def _form(settings_obj, **changes):
    data = _post_data(settings_obj)
    data.update({name: str(value) for name, value in changes.items()})
    return FacilitySettingsForm(data, instance=settings_obj, facility=settings_obj.facility)


def _settings_page(client, admin_user):
    client.force_login(admin_user)
    return client.get(reverse("core:facility_settings")).content.decode()


class TestKAnonymityMinimum:
    def test_one_is_rejected_by_the_form(self, settings_obj):
        form = _form(settings_obj, k_anonymity_threshold=1)

        assert not form.is_valid()
        assert "k_anonymity_threshold" in form.errors

    def test_one_is_rejected_by_model_validation(self, settings_obj):
        settings_obj.k_anonymity_threshold = 1

        with pytest.raises(ValidationError) as exc:
            settings_obj.full_clean()

        assert "k_anonymity_threshold" in exc.value.message_dict

    def test_two_is_saved_and_the_settings_page_warns(self, client, admin_user, settings_obj):
        client.force_login(admin_user)
        data = _post_data(settings_obj)
        data["k_anonymity_threshold"] = "2"

        response = client.post(reverse("core:facility_settings"), data)

        assert response.status_code == 302
        settings_obj.refresh_from_db()
        assert settings_obj.k_anonymity_threshold == 2
        assert K_WARNING in _settings_page(client, admin_user)

    def test_five_is_silent_on_the_settings_page(self, client, admin_user, settings_obj):
        assert settings_obj.k_anonymity_threshold == 5

        assert K_WARNING not in _settings_page(client, admin_user)

    def test_bound_form_with_two_warns_before_saving(self, settings_obj):
        form = _form(settings_obj, k_anonymity_threshold=2)

        assert form.is_valid()
        assert any(K_WARNING in str(warning) for warning in form.setting_warnings)

    def test_stored_value_below_minimum_is_named_on_the_settings_page(self, client, admin_user, settings_obj):
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=0)

        content = _settings_page(client, admin_user)

        assert K_BELOW_MINIMUM in content


class TestKAnonymityOnTheReportPage:
    def _report(self, client, lead_user):
        client.force_login(lead_user)
        return client.get(reverse("core:statistics_external_report")).content.decode()

    def test_report_page_warns_for_two(self, client, lead_user, settings_obj):
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=2)

        assert K_WARNING in self._report(client, lead_user)

    def test_report_page_is_silent_for_five(self, client, lead_user, settings_obj):
        assert K_WARNING not in self._report(client, lead_user)

    def test_report_page_warns_for_a_stored_one(self, client, lead_user, settings_obj):
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=1)
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_external_report"))

        assert response.status_code == 200
        assert response.context["report"]["metadata"]["k_anonymity_threshold"] == 1
        content = response.content.decode()
        assert K_WARNING in content
        assert K_NO_SUPPRESSION in content

    def test_report_page_is_silent_for_a_stored_zero(self, client, lead_user, settings_obj):
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=0)

        assert K_WARNING not in self._report(client, lead_user)

    @pytest.mark.parametrize("stored", [1, 2])
    def test_report_preview_warns_below_three(self, client, lead_user, settings_obj, stored):
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=stored)
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        assert response.status_code == 200
        assert response.context["k_anonymity_threshold"] == stored
        assert K_WARNING in response.content.decode()


class TestStoredThresholdBelowMinimum:
    """Only a write that bypasses validation can store 0 or 1; neither takes effect silently."""

    @pytest.mark.parametrize(("stored", "effective"), [(0, DEFAULT_K_THRESHOLD), (1, 1)])
    def test_report_threshold_is_logged(self, facility, settings_obj, stored, effective, caplog):
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=stored)

        with caplog.at_level(logging.WARNING, logger="core.services.dashboard.external_report"):
            threshold = _get_threshold(facility)

        assert threshold == effective
        assert any("below the minimum" in record.getMessage() for record in caplog.records)

    def test_valid_low_value_is_used_as_stored_without_log(self, facility, settings_obj, caplog):
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=2)

        with caplog.at_level(logging.WARNING, logger="core.services.dashboard.external_report"):
            assert _get_threshold(facility) == 2

        assert not caplog.records

    @pytest.mark.parametrize(("stored", "effective"), [(0, DEFAULT_K_THRESHOLD), (1, 1)])
    def test_settings_page_names_the_value_reports_compute_with(
        self, client, admin_user, settings_obj, stored, effective
    ):
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=stored)

        content = _settings_page(client, admin_user)

        assert f"Berichte rechnen mit k = {effective}," in content

    def test_settings_page_warns_for_a_stored_one(self, client, admin_user, settings_obj):
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=1)

        content = _settings_page(client, admin_user)

        assert K_BELOW_MINIMUM in content
        assert K_NO_SUPPRESSION in content

    def test_settings_page_has_no_risk_warning_for_a_stored_zero(self, client, admin_user, settings_obj):
        Settings.objects.filter(pk=settings_obj.pk).update(k_anonymity_threshold=0)

        assert K_WARNING not in _settings_page(client, admin_user)


AUDIT_WARNING = "ist kürzer als die längste Fach-Frist"


def _audit_warnings(form):
    assert form.is_valid(), form.errors
    return [str(warning) for warning in form.setting_warnings if AUDIT_WARNING in str(warning)]


class TestAuditRetentionShorterThanFieldRetention:
    """The audit log period (months × 30 days) is compared with every period of the settings."""

    def test_defaults_warn_and_name_the_longest_period(self, settings_obj):
        warnings = _audit_warnings(_form(settings_obj))

        assert len(warnings) == 1
        assert "720 Tage" in warnings[0]
        assert "Aufbewahrung qualifiziert (Tage)" in warnings[0]
        assert "3650 Tage" in warnings[0]

    def test_audit_period_covering_every_period_is_silent(self, settings_obj):
        assert _audit_warnings(_form(settings_obj, auditlog_retention_months=122)) == []

    def test_audit_zero_keeps_everything_and_is_silent(self, settings_obj):
        assert _audit_warnings(_form(settings_obj, auditlog_retention_months=0)) == []

    def test_unlimited_history_period_counts_as_longest(self, settings_obj):
        warnings = _audit_warnings(_form(settings_obj, auditlog_retention_months=122, eventhistory_chronicle_months=0))

        assert len(warnings) == 1
        assert "Historien-Chronologie aufbewahren (Monate)" in warnings[0]
        assert "unbegrenzt" in warnings[0]

    def test_history_months_are_counted_as_thirty_days(self, settings_obj):
        form = _form(
            settings_obj,
            auditlog_retention_months=122,
            retention_qualified_days=365,
            eventhistory_detail_months=123,
        )

        warnings = _audit_warnings(form)

        assert len(warnings) == 1
        assert "3660 Tage" in warnings[0]
        assert "3690 Tage" in warnings[0]

    def test_settings_page_shows_the_warning(self, client, admin_user, settings_obj):
        assert AUDIT_WARNING in _settings_page(client, admin_user)

    def test_settings_page_is_silent_with_audit_zero(self, client, admin_user, settings_obj):
        Settings.objects.filter(pk=settings_obj.pk).update(auditlog_retention_months=0)

        assert AUDIT_WARNING not in _settings_page(client, admin_user)
