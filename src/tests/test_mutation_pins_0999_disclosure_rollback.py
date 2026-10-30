"""Preserve visible ages when a contact stage is hidden for event arithmetic.

Source: mutation delta of the release-0.99.9 rollback, 2 mutants pinned.
Groups by function: _finalize_report.
"""

from copy import deepcopy
from datetime import date

import pytest

from core.models import Client, Settings
from core.services.dashboard import external_report

pytestmark = pytest.mark.django_db


class TestFinalizeReport:
    """Distinguish secondary event suppression from a small person population."""

    @pytest.mark.parametrize("entry_point", ["build_external_report", "suppress_report_stats"])
    def test_secondary_stage_suppression_preserves_safe_age_values(self, facility, monkeypatch, entry_point):
        """Keep safe age values beside a secondarily hidden, person-safe stage.

        x__finalize_report__mutmut_50, x__finalize_report__mutmut_54.
        """
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 5})
        raw = {
            "total_contacts": 31,
            "unique_clients": 30,
            "by_document_type": [{"name": "Contact", "count": 31}],
            "by_contact_stage": {"anonym": 1, "identifiziert": 10, "qualifiziert": 20},
            "persons_by_contact_stage": {"anonym": 1, "identifiziert": 10, "qualifiziert": 20},
            "by_age_cluster": [
                {"cluster": cluster, "count": 10, "persons": 10}
                for cluster in (Client.AgeCluster.U18, Client.AgeCluster.AGE_18_26, Client.AgeCluster.AGE_27_PLUS)
            ],
        }
        original = deepcopy(raw)
        if entry_point == "build_external_report":
            monkeypatch.setattr(external_report, "get_statistics", lambda *args, **kwargs: raw)
            protected = external_report.build_external_report(facility, date(2026, 1, 1), date(2026, 12, 31))
        else:
            protected = external_report.suppress_report_stats(facility, raw)

        # The hidden identified stage has enough persons; only event arithmetic
        # required its suppression, so it must not trigger the person-gap guard.
        assert protected["by_contact_stage"]["identifiziert"] is None
        assert protected["by_contact_stage"]["qualifiziert"] == 20
        visible_ages = [row for row in protected["by_age_cluster"] if row["count"] is not None]
        assert visible_ages
        assert all(row["count"] == 10 and row["suppressed"] is False for row in visible_ages)
        assert "persons_by_contact_stage" not in protected
        assert all("persons" not in row for row in protected["by_age_cluster"])
        assert raw == original
