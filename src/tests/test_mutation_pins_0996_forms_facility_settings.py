"""Pins for the audit retention warning of the facility settings form.

Source: mutation delta of the release-0.99.6 checkpoint, 4 mutants of
``FacilitySettingsForm._audit_retention_warning`` (Refs #2461), observed through
the public ``setting_warnings`` property. Groups:

* Longest event document type (``__mutmut_30``, ``__mutmut_31``) -- of several
  event types with a period longer than the settings, the one with the longest
  period is named; on a tie the name decides, so the note is deterministic.
* Boundary (``__mutmut_69``) -- an audit period exactly as long as the longest
  period is enough and stays silent.
* Tie among the settings periods (``__mutmut_23``) -- the first field in form
  order is named, not a later one with the same period.
"""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.translation import gettext as _

from core.forms.facility_settings import FacilitySettingsForm
from core.models import DocumentType, Settings
from tests.test_facility_settings_hub import _post_data

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

AUDIT_MARKER = "ist kürzer als die längste Fach-Frist"
_LONGER_TYPES = '"core_documenttype"."retention_days" > '


def _form(settings_obj, **changes):
    data = _post_data(settings_obj)
    data.update({name: str(value) for name, value in changes.items()})
    form = FacilitySettingsForm(data, instance=settings_obj, facility=settings_obj.facility)
    assert form.is_valid(), form.errors
    return form


def _audit_warnings(form):
    return [str(warning) for warning in form.setting_warnings if AUDIT_MARKER in str(warning)]


def _expected(audit_days, field, days):
    return _(
        "Die Audit-Log-Frist (%(audit_days)s Tage) ist kürzer als die längste Fach-Frist "
        "„%(field)s“ (%(period)s). Audit-Spuren werden gelöscht, während die zugehörigen "
        "Fachdaten noch bestehen können; lückenlos bleibt das Audit-Log nur mit 0."
    ) % {"audit_days": audit_days, "field": field, "period": _("%(days)s Tage") % {"days": days}}


def _type_label(name):
    return _("Dokumentationstyp %(name)s") % {"name": name}


def _field_label(name):
    return str(Settings._meta.get_field(name).verbose_name)


def _event_type(facility, name, retention_days):
    return DocumentType.objects.create(
        facility=facility,
        name=name,
        entity_type=DocumentType.EntityType.EVENT,
        retention_days=retention_days,
    )


class TestLongestDocumentTypeIsNamed:
    def test_the_type_with_the_longest_period_is_named(self, settings_obj):
        """Of two event types longer than the settings, the longer one is named with its period,
        although the shorter one comes first by name.

        ``xǁFacilitySettingsFormǁ_audit_retention_warning__mutmut_30`` orders by name only.
        """
        _event_type(settings_obj.facility, "A Short", 4000)
        _event_type(settings_obj.facility, "B Long", 5000)

        warnings = _audit_warnings(_form(settings_obj, auditlog_retention_months=122))

        assert warnings == [_expected(3660, _type_label("B Long"), 5000)]

    def test_an_audit_period_between_both_types_still_warns(self, settings_obj):
        """An audit period longer than the shorter type but shorter than the longer one warns.

        ``xǁFacilitySettingsFormǁ_audit_retention_warning__mutmut_30`` compares with the type
        that comes first by name and drops the warning.
        """
        _event_type(settings_obj.facility, "A Short", 4000)
        _event_type(settings_obj.facility, "B Long", 5000)

        warnings = _audit_warnings(_form(settings_obj, auditlog_retention_months=140))

        assert warnings == [_expected(4200, _type_label("B Long"), 5000)]

    def test_a_tie_names_the_type_first_by_name(self, settings_obj):
        """Two event types with the same longest period: the note names the one first by name.

        PostgreSQL returns rows that tie on every sort key in whatever order the scan delivers
        them (here the unique index on ``(facility, name)``, elsewhere the heap), so the named
        type alone cannot show a missing tie-break. The pin therefore also reads the query
        that fetches the longer event types: after the period it orders by name.
        ``xǁFacilitySettingsFormǁ_audit_retention_warning__mutmut_31`` drops the name key.
        """
        _event_type(settings_obj.facility, "Zeta", 5000)
        _event_type(settings_obj.facility, "Alpha", 5000)
        form = _form(settings_obj, auditlog_retention_months=122)

        with CaptureQueriesContext(connection) as captured:
            warnings = _audit_warnings(form)

        type_queries = [q["sql"].replace('"', "") for q in captured.captured_queries if _LONGER_TYPES in q["sql"]]
        assert type_queries
        for sql in type_queries:
            order_by = re.search(r" ORDER BY (.+?)(?: LIMIT \d+)?$", sql).group(1).split(", ")
            assert order_by[:2] == ["core_documenttype.retention_days DESC", "core_documenttype.name ASC"]
        assert warnings == [_expected(3660, _type_label("Alpha"), 5000)]


class TestAuditPeriodEqualToTheLongestPeriod:
    def test_equal_settings_period_is_silent(self, settings_obj):
        """122 months are 3660 days; a longest settings period of 3660 days is covered.

        ``xǁFacilitySettingsFormǁ_audit_retention_warning__mutmut_69`` warns on equality.
        """
        form = _form(settings_obj, auditlog_retention_months=122, retention_qualified_days=3660)

        assert _audit_warnings(form) == []

    def test_equal_document_type_period_is_silent(self, settings_obj):
        """An event type period of exactly the audit period is covered as well.

        ``xǁFacilitySettingsFormǁ_audit_retention_warning__mutmut_69`` warns on equality.
        """
        _event_type(settings_obj.facility, "Exact", 3660)

        assert _audit_warnings(_form(settings_obj, auditlog_retention_months=122)) == []


class TestTieAmongSettingsPeriods:
    def test_the_first_field_in_form_order_is_named(self, settings_obj):
        """Detail and chronicle history with the same longest period: the detail field, first
        in form order, is named.

        ``xǁFacilitySettingsFormǁ_audit_retention_warning__mutmut_23`` lets the later field win.
        """
        form = _form(
            settings_obj,
            auditlog_retention_months=24,
            eventhistory_detail_months=200,
            eventhistory_chronicle_months=200,
        )

        assert _audit_warnings(form) == [_expected(720, _field_label("eventhistory_detail_months"), 6000)]
