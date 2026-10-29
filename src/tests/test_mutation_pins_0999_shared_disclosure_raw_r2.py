"""Pins for live raw counts and hybrid report provenance.

Source: mutation delta of the release-0.99.9 checkpoint, 11 mutants.
Groups: stage person thresholds (2), raw type ordering (1), age fallback (4),
hybrid snapshot date (1), live unique-person provenance (3).
"""

from datetime import date, datetime

import pytest
from django.utils import timezone

from core.models import Settings, StatisticsSnapshot
from core.reporting.data_source import LIVE
from core.services.dashboard import shared_disclosure
from core.services.dashboard.external_report import build_external_report
from core.services.dashboard.snapshot import SNAPSHOT_AS_OF_KEY
from tests._factories import make_client, make_document_type, make_event

pytestmark = pytest.mark.django_db
START, END = date(2026, 1, 1), date(2026, 1, 31)


@pytest.fixture
def report_event(facility, admin_user):
    """Create live report contacts at a fixed calendar date."""
    document_type = make_document_type(facility=facility, name="Report contacts", report_key="contact")
    when = timezone.make_aware(datetime(2026, 1, 15, 12))

    def create(person=None):
        return make_event(
            facility=facility,
            client=person,
            document_type=document_type,
            created_by=admin_user,
            occurred_at=when,
            data_json={},
            is_anonymous=False,
        )

    return create


@pytest.mark.parametrize("stage", ["identified", "qualified"])
def test_personless_contacts_cannot_lift_a_small_linked_stage(facility, admin_user, report_event, stage):
    """Unlinked events never lift another stage over its person threshold:
    x__raw_live_statistics__mutmut_104, x__raw_live_statistics__mutmut_111.
    """
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 5})
    for contact_stage, count in ((stage, 4), ("qualified" if stage == "identified" else "identified", 6)):
        for number in range(count):
            person = make_client(
                facility=facility,
                pseudonym=f"{contact_stage} {number}",
                created_by=admin_user,
                contact_stage=contact_stage,
                age_cluster="u18",
            )
            report_event(person)
            report_event(person)
    for _ in range(8):
        report_event()

    report = build_external_report(facility, START, END)

    key = "identifiziert" if stage == "identified" else "qualifiziert"
    assert report["by_contact_stage"][key] is None


def test_raw_document_types_are_ranked_by_descending_count():
    """Raw ordering precedes canonical public ordering: x__raw_live_statistics__mutmut_116.

    Public report projection sorts by name, so the raw ranking is only
    observable at this aggregation boundary.
    """
    populations = (
        shared_disclosure.TemporalPopulation("event_counts", "document-type:small", (("one", START),)),
        shared_disclosure.TemporalPopulation("event_counts", "document-type:large", (("two", START), ("three", START))),
    )

    _, raw, _ = shared_disclosure._raw_live_statistics(populations, {"Small": "small", "Large": "large"}, START, END)

    assert raw["by_document_type"] == [{"name": "Large", "count": 2}, {"name": "Small", "count": 1}]


def test_unknown_age_keeps_its_unprefixed_public_label(facility, admin_user, report_event):
    """Legacy age identities retain their fallback label in the published report:
    x__raw_live_statistics__mutmut_31, x__raw_live_statistics__mutmut_32,
    x__raw_live_statistics__mutmut_33, x__raw_live_statistics__mutmut_39.
    """
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 5})
    for age in ("legacy", "u18"):
        for number in range(6):
            person = make_client(facility=facility, pseudonym=f"{age} {number}", created_by=admin_user, age_cluster=age)
            report_event(person)

    report = build_external_report(facility, START, END)

    legacy = next(row for row in report["by_age_cluster"] if row["cluster"] == "legacy")
    assert legacy["count"] == 6
    assert legacy["label"] == "legacy"


def test_hybrid_keeps_newest_snapshot_date_and_live_person_provenance(facility):
    """Hybrid reports preserve the newest contributing snapshot and live person source:
    x__raw_hybrid_sample__mutmut_20, x__raw_hybrid_sample__mutmut_54,
    x__raw_hybrid_sample__mutmut_55, x__raw_hybrid_sample__mutmut_56.
    """
    newest = timezone.make_aware(datetime(2026, 3, 20, 12))
    older = timezone.make_aware(datetime(2026, 3, 10, 12))
    for month, updated_at in ((1, newest), (2, older)):
        snapshot = StatisticsSnapshot.objects.create(
            facility=facility,
            year=2026,
            month=month,
            backend_data={"sample-report-1": {"total": 6, "by_category": [("kontakt", 6)]}},
        )
        StatisticsSnapshot.objects.for_facility(facility).filter(pk=snapshot.pk).update(updated_at=updated_at)

    report = shared_disclosure.build_live_release(facility, START, date(2026, 2, 28)).hybrid

    assert report[SNAPSHOT_AS_OF_KEY] == newest
    assert report["unique_clients_data_source"] == LIVE
