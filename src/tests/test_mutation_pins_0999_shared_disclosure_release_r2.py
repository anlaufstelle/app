"""Pins for the live release projection of the shared count mask.

Source: mutation delta of the release-0.99.9 checkpoint, 27 mutants.
Groups: facility scoping and diagnostic (2), returned mask (3), observation normalization (4),
sample/general table projection (5), contact-stage removal (4), hybrid figures (9).

The shared solver stays real. A thin wrapper around it overrides the decision for named
count identities (suppressed number or removed table), so each projection rule is observable
without relying on solver internals.
"""

import calendar
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from django.utils import timezone

from core.models import Settings
from core.services.dashboard import external_report
from core.services.dashboard import shared_disclosure as disclosure
from tests._factories import make_client, make_document_type, make_event

JAN_START = date(2026, 1, 1)
JAN_END = date(2026, 1, 31)
EVENT_DAY = date(2026, 1, 5)


@pytest.fixture
def world(facility, admin_user):
    """Twelve persons with two events each on one January day, all counts far above the threshold."""
    contact = make_document_type(facility=facility, name="Contact", report_key="contact")
    crisis = make_document_type(facility=facility, name="Crisis", report_key="crisis")
    when = timezone.make_aware(datetime(2026, 1, 5, 12))
    for index in range(12):
        person = make_client(
            facility=facility,
            pseudonym=f"person-{index}",
            created_by=admin_user,
            age_cluster="18_26" if index < 6 else "27_plus",
            contact_stage="identified" if index < 6 else "qualified",
        )
        for document_type in (contact, crisis):
            make_event(
                facility=facility,
                client=person,
                document_type=document_type,
                occurred_at=when,
                data_json={},
                created_by=admin_user,
            )
    return SimpleNamespace(contact=contact, crisis=crisis)


def release_with_overrides(facility, start, end, monkeypatch, *, suppress=(), drop=()):
    """Build a release whose mask suppresses or drops the given ``(dimension, identity)`` pairs."""
    real = disclosure.protect_temporal_counts

    def overridden(*args, **kwargs):
        mask = real(*args, **kwargs)
        return disclosure.SharedMask(
            mask.facility_id,
            mask.threshold,
            {
                key: None if (key.dimension, key.population) in suppress else value
                for key, value in mask.published.items()
            },
            mask.dropped | frozenset(key for key in mask.published if (key.dimension, key.population) in drop),
        )

    monkeypatch.setattr(disclosure, "protect_temporal_counts", overridden)
    return disclosure.build_live_release(facility, start, end)


def rows_by(rows, label):
    return {row[label]: row["count"] for row in rows}


@pytest.mark.django_db
def test_live_release_returns_the_computed_mask_of_the_facility(facility, world):
    """The release carries the mask with facility and threshold.

    x_build_live_release__mutmut_147,
    x_build_live_release__mutmut_148,
    x_build_live_release__mutmut_381.
    """
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 4})
    release = disclosure.build_live_release(facility, JAN_START, JAN_END)
    assert isinstance(release.mask, disclosure.SharedMask)
    assert release.mask.facility_id == facility.pk
    assert release.mask.threshold == 4
    assert any(key.population == "all-events" for key in release.mask.published)


@pytest.mark.django_db
def test_additional_populations_are_collected_for_the_requested_facility(facility, world, monkeypatch):
    """Admission and case sources receive the requested facility: x_build_live_release__mutmut_11."""
    from core.services.dashboard import shared_sources

    probe = disclosure.TemporalPopulation("event_counts", "probe-source", (("probe-unit", EVENT_DAY),))
    real = shared_sources.collect_additional_populations
    monkeypatch.setattr(
        shared_sources,
        "collect_additional_populations",
        lambda scope: (*real(scope), probe) if scope == facility else real(scope),
    )
    release = disclosure.build_live_release(facility, JAN_START, JAN_END)
    assert any(key.population == "probe-source" for key in release.mask.published)


@pytest.mark.django_db
def test_missing_sample_category_binding_is_explained(facility, monkeypatch):
    """The failure names the missing binding: x_build_live_release__mutmut_36."""
    from core.reporting import registry

    real = registry.get_backend
    monkeypatch.setattr(disclosure, "collect_event_populations", lambda scope, start, end: ())
    monkeypatch.setattr(
        registry,
        "get_backend",
        lambda slug: SimpleNamespace(typ_bindung=None) if slug == "sample-report-1" else real(slug),
    )
    with pytest.raises(ValueError) as error:
        disclosure.build_live_release(facility, JAN_START, JAN_END)
    assert str(error.value) == "The sample report requires a category binding"


def seed_months(facility, user, plan):
    """Create ``(month, persons, age_cluster, contact_stage, events_per_person)`` groups in 2026."""
    contact = make_document_type(facility=facility, name="Contact", report_key="contact")
    crisis = make_document_type(facility=facility, name="Crisis", report_key="crisis")
    number = 0
    for month, persons, age_cluster, contact_stage, events in plan:
        when = timezone.make_aware(datetime(2026, month, 5, 12))
        for _ in range(persons):
            number += 1
            person = make_client(
                facility=facility,
                pseudonym=f"alias-{number}",
                created_by=user,
                age_cluster=age_cluster,
                contact_stage=contact_stage,
            )
            for index in range(events):
                make_event(
                    facility=facility,
                    client=person,
                    document_type=crisis if index % 2 == 0 else contact,
                    occurred_at=when,
                    data_json={},
                    created_by=user,
                )


def figures_of_march(release):
    return (
        release.general["total_contacts"],
        release.general["by_contact_stage"]["qualifiziert"],
        rows_by(release.sample["by_category"], "name")["Beratung"],
    )


def figures_of_admissions(release):
    return (release.datasets["client-metrics"].summary["neuaufnahmen"],)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("plan", "threshold", "window", "figures", "expected"),
    [
        pytest.param(
            [(1, 2, "18_26", "identified", 3), (3, 3, "27_plus", "qualified", 1)],
            3,
            (date(2026, 3, 1), date(2026, 3, 31)),
            figures_of_march,
            (3, 3, 3),
            id="person-sensitive-cells",
        ),
        pytest.param(
            [(1, 1, "18_26", "identified", 3), (3, 6, "27_plus", "qualified", 2)],
            2,
            None,
            figures_of_admissions,
            (7,),
            id="row-existence-bound",
        ),
    ],
)
def test_removed_table_clears_every_alias_of_its_unions(
    facility, admin_user, plan, threshold, window, figures, expected
):
    """Every alias of a locally removed table is withheld and unrelated figures stay published.

    The January persons are too few for the age and stage tables of January, so those
    tables are removed there. The same January union recurs in the longer windows that
    add March; each such alias must lose its row-existence bound and person sensitivity,
    otherwise it forces other published figures into suppression.

    x_build_live_release__mutmut_116,
    x_build_live_release__mutmut_124,
    x_build_live_release__mutmut_125,
    x_build_live_release__mutmut_127.
    """
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": threshold})
    seed_months(facility, admin_user, plan)
    if window is None:
        today = timezone.localdate()
        window = (JAN_START, date(today.year, today.month, calendar.monthrange(today.year, today.month)[1]))
    release = disclosure.build_live_release(facility, *window)
    for dimension in ("event_counts", "person_counts"):
        for end in (JAN_END, date(2026, 3, 31)):
            alias = disclosure.CountKey(dimension, "age:18_26", JAN_START, end)
            assert release.mask.published[alias] is None
            assert alias in release.mask.dropped
    assert figures(release) == expected


@pytest.mark.django_db
def test_sample_tables_follow_the_shared_decision(facility, world, monkeypatch):
    """Sample age and category rows take the shared suppression.

    x_build_live_release__mutmut_251,
    x_build_live_release__mutmut_252,
    x_build_live_release__mutmut_257,
    x_build_live_release__mutmut_258.
    """
    release = release_with_overrides(
        facility,
        JAN_START,
        JAN_END,
        monkeypatch,
        suppress={("event_counts", "age:18_26"), ("event_counts", "category:Kontakte")},
    )
    assert rows_by(release.sample["by_age_cluster"], "cluster") == {"18_26": None, "27_plus": 12}
    assert rows_by(release.sample["by_category"], "name") == {"Kontakte": None, "Beratung": 12}


@pytest.mark.django_db
def test_an_absent_general_table_does_not_end_the_projection(facility, world, monkeypatch):
    """Later tables are still projected when an early table is absent: x_build_live_release__mutmut_264."""
    real = external_report._protect_external_statistics

    def without_document_types(*args, **kwargs):
        report = real(*args, **kwargs)
        report.pop("by_document_type", None)
        return report

    monkeypatch.setattr(external_report, "_protect_external_statistics", without_document_types)
    release = release_with_overrides(
        facility, JAN_START, JAN_END, monkeypatch, suppress={("event_counts", "category:Kontakte")}
    )
    assert "by_document_type" not in release.general
    assert rows_by(release.sample["by_category"], "name")["Kontakte"] is None


@pytest.mark.django_db
def test_contact_stage_table_is_removed_when_the_shared_mask_drops_a_stage(facility, world, monkeypatch):
    """A dropped stage removes the whole table without error.

    x_build_live_release__mutmut_293,
    x_build_live_release__mutmut_297,
    x_build_live_release__mutmut_298,
    x_build_live_release__mutmut_299.
    """
    kept = release_with_overrides(facility, JAN_START, JAN_END, monkeypatch)
    assert "by_contact_stage" in kept.general
    release = release_with_overrides(
        facility, JAN_START, JAN_END, monkeypatch, drop={("event_counts", "contact-stage:identifiziert")}
    )
    assert "by_contact_stage" not in release.general


@pytest.mark.django_db
def test_hybrid_person_total_follows_the_shared_decision(facility, world, monkeypatch):
    """The hybrid active-person figure is suppressed under its own key.

    x_build_live_release__mutmut_318,
    x_build_live_release__mutmut_319.
    """
    release = release_with_overrides(
        facility, JAN_START, JAN_END, monkeypatch, suppress={("person_counts", "active-persons")}
    )
    assert release.hybrid["unique_clients"] is None
    assert release.general["unique_clients"] is None
    assert release.sample["unique_clients"] is None


@pytest.mark.django_db
def test_hybrid_category_rows_follow_the_shared_decision_in_calendar_months(facility, world, monkeypatch):
    """Calendar-month hybrid rows take the shared suppression: x_build_live_release__mutmut_341,
    x_build_live_release__mutmut_342."""
    release = release_with_overrides(
        facility, JAN_START, JAN_END, monkeypatch, suppress={("event_counts", "hybrid-category:Kontakte")}
    )
    assert rows_by(release.hybrid["by_category"], "name") == {"Kontakte": None, "Beratung": 12}
    assert release.hybrid["total"] == 24


@pytest.mark.django_db
def test_jointly_dropped_hybrid_row_removes_its_calendar_table(facility, world, monkeypatch):
    """A dropped hybrid row removes its whole table.

    x_build_live_release__mutmut_341,
    x_build_live_release__mutmut_342,
    x_build_live_release__mutmut_359,
    x_build_live_release__mutmut_362,
    x_build_live_release__mutmut_365,
    x_build_live_release__mutmut_367.
    """
    release = release_with_overrides(
        facility, JAN_START, JAN_END, monkeypatch, drop={("event_counts", "hybrid-category:Kontakte")}
    )
    assert "by_category" not in release.hybrid
    assert "by_age_cluster" in release.hybrid


@pytest.mark.django_db
@pytest.mark.parametrize("window", [(JAN_START, date(2026, 1, 15)), (date(2026, 1, 3), JAN_END)])
def test_partial_month_withholds_every_hybrid_event_figure(facility, world, monkeypatch, window):
    """Partial months never publish hybrid event figures.

    x_build_live_release__mutmut_337,
    x_build_live_release__mutmut_341,
    x_build_live_release__mutmut_342,
    x_build_live_release__mutmut_359,
    x_build_live_release__mutmut_367.
    """
    release = release_with_overrides(facility, *window, monkeypatch)
    assert release.hybrid["total"] is None
    assert "by_category" not in release.hybrid
    assert "by_age_cluster" not in release.hybrid
