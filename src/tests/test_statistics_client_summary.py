"""Characterize the full-range person terms used by hybrid statistics."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test import Client as WebClient
from django.test.utils import CaptureQueriesContext

from core.models import Client, DocumentType, Event, Settings, StatisticsSnapshot
from core.reporting.data_source import LIVE
from core.services.dashboard.snapshot import SNAPSHOT_AS_OF_KEY, create_or_update_snapshot, get_statistics_hybrid
from core.services.dashboard.statistics import get_live_client_statistics, get_statistics

pytestmark = pytest.mark.django_db


@pytest.fixture
def population(facility, second_facility, admin_user):
    """Include unreadable people, anonymous events, ties and excluded rows."""
    primary = DocumentType.objects.create(facility=facility, name="Primary")
    other = DocumentType.objects.create(facility=facility, name="Other")
    foreign = DocumentType.objects.create(facility=second_facility, name="Foreign")
    tz = ZoneInfo("Europe/Berlin")

    def event(person, day, doc=primary, deleted=False):
        return Event.objects.create(
            facility=doc.facility,
            document_type=doc,
            client=person,
            occurred_at=datetime(2025, 1, day, 0, 30, tzinfo=tz),
            is_deleted=deleted,
            created_by=admin_user,
        )

    people = []
    for index in range(7):
        person = Client.objects.create(
            facility=facility,
            pseudonym=f"Person {index}",
            contact_stage=Client.ContactStage.IDENTIFIED,
            is_active=index != 5,
            is_deleted=index == 6,
        )
        people.append(person)
        for _ in range(8 - index):
            event(person, 11)
    event(people[0], 12, other)
    event(people[1], 12, other)
    event(None, 11)
    event(people[0], 10)
    event(people[0], 13, deleted=True)
    foreign_person = Client.objects.create(facility=second_facility, pseudonym="Foreign")
    event(foreign_person, 11, foreign)
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 5})
    return primary, other


@pytest.mark.parametrize("period", [(11, 11), (11, 12), (1, 31), (20, 21)])
@pytest.mark.parametrize("filtered", [False, True])
def test_full_range_person_terms_match_live_statistics(facility, population, period, filtered):
    start, end = (date(2025, 1, day) for day in period)
    document_type = population[1] if filtered else None
    expected = get_statistics(facility, start, end, document_type)
    narrow = get_live_client_statistics(facility, start, end, document_type)
    assert narrow == {key: expected[key] for key in ("top_clients", "unique_clients")}
    if filtered:
        actual = narrow
    else:
        create_or_update_snapshot(facility, 2025, 1)
        actual = get_statistics_hybrid(facility, start, end)
    assert actual["top_clients"] == expected["top_clients"]
    assert actual["unique_clients"] == expected["unique_clients"]
    if period == (11, 11) and not filtered:
        assert actual["unique_clients"] == 7
        assert [row["count"] for row in actual["top_clients"]] == [8, 7, 6, 5, 4]


@pytest.mark.parametrize("user_fixture", ["admin_user", "lead_user"])
@pytest.mark.parametrize("filtered", [False, True])
def test_statistics_page_person_terms_match_live_service(request, facility, population, user_fixture, filtered):
    web = WebClient()
    web.force_login(request.getfixturevalue(user_fixture))
    params = {"zeitraum": "frei", "von": "2025-01-11", "bis": "2025-01-12"}
    doc = population[1] if filtered else None
    if doc:
        params["doktyp"] = str(doc.pk)
    response = web.get("/statistics/", params)
    assert response.status_code == 200
    expected = get_statistics(facility, date(2025, 1, 11), date(2025, 1, 12), doc)
    assert response.context["stats"]["top_clients"] == expected["top_clients"]
    assert response.context["stats"]["unique_clients"] == expected["unique_clients"]


@pytest.mark.parametrize("persons", [4, 5])
def test_hybrid_person_count_at_suppression_boundary(facility, admin_user, persons):
    from core.services.dashboard import suppress_report_stats

    doc = DocumentType.objects.create(facility=facility, name="Boundary")
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 5})
    for index in range(persons):
        person = Client.objects.create(facility=facility, pseudonym=f"Boundary {index}")
        for month in (1, 2):
            Event.objects.create(
                facility=facility,
                client=person,
                document_type=doc,
                created_by=admin_user,
                occurred_at=datetime(2025, month, 11, 12, tzinfo=ZoneInfo("Europe/Berlin")),
            )
    for month in (1, 2):
        create_or_update_snapshot(facility, 2025, month)
    snapshots = StatisticsSnapshot.objects.filter(facility=facility, year=2025, month__in=(1, 2))
    assert snapshots.count() == 2
    start, end = date(2025, 1, 1), date(2025, 2, 28)
    expected = get_statistics(facility, start, end)
    actual = get_statistics_hybrid(facility, start, end)
    assert actual["unique_clients"] == expected["unique_clients"] == persons
    assert actual["top_clients"] == expected["top_clients"]
    protected_actual = suppress_report_stats(facility, actual)
    protected_expected = suppress_report_stats(facility, expected)
    # Hybrid output also carries snapshot provenance and document type metadata.
    for row in protected_expected["by_document_type"]:
        row.update(system_type=doc.system_type or "", document_type_id=str(doc.pk))
    protected_expected[SNAPSHOT_AS_OF_KEY] = max(snapshot.updated_at for snapshot in snapshots)
    protected_expected["unique_clients_data_source"] = LIVE
    assert protected_actual == protected_expected
    assert protected_actual["unique_clients"] == (None if persons < 5 else persons)
    assert protected_actual["total_contacts"] == persons * 2
    if persons < 5:
        assert "by_age_cluster" not in protected_actual
        assert "by_contact_stage" not in protected_actual


def test_hybrid_query_count(facility, population):
    create_or_update_snapshot(facility, 2025, 1)
    with CaptureQueriesContext(connection) as queries:
        get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))
    assert len(queries) == 3
