"""Tally (Strichliste) events in the bulk seed.

The seeded tally events must look exactly like the ones created through the
tally form: ``created_via=tally``, anonymous, no person, only on types that
``evaluate_tally_eligibility`` accepts, with ``data_json`` like the tally form
(NUMBER mode: every required number field; DIRECT mode: empty).
"""

from datetime import time, timedelta
from urllib.parse import parse_qsl

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import (
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    Facility,
    FieldTemplate,
    TimeFilter,
    User,
)
from core.seed.flush import flush_seed_data
from core.seed.scale import SCALE_CONFIG

pytest.importorskip("magic", reason="The seed command needs libmagic for its file uploads.")

from core.seed.events import seed_events_bulk  # noqa: E402
from core.services.events import build_tally_summary  # noqa: E402
from core.services.events.feed import get_time_range  # noqa: E402
from core.services.events.list_filters import (  # noqa: E402
    event_list_queryset,
    resolve_event_list_filters,
)
from core.services.events.tally import (  # noqa: E402
    TallyLiveContext,
    TallyMode,
    current_tally_context,
    evaluate_tally_eligibility,
)
from core.services.zeitstrom_filters import resolve_zeitstrom_state  # noqa: E402
from core.services.zeitstrom_kpis import build_zeitstrom_kpis  # noqa: E402

TALLY = Event.CreatedVia.TALLY


def _in_any_window(event, windows):
    moment = timezone.localtime(event.occurred_at)
    return any(window.covers_time(moment) for window in windows)


@pytest.fixture(scope="class")
def _medium_seed(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        call_command("flush", interactive=False, verbosity=0)
        call_command("seed", scale="medium")
        yield
        call_command("flush", interactive=False, verbosity=0)


@pytest.mark.django_db
class TestMediumSeedTallyEvents:
    def test_every_facility_has_tally_events_in_addition_to_the_standard_volume(self, _medium_seed):
        facilities = list(Facility.objects.all())
        assert len(facilities) == SCALE_CONFIG["medium"]["facilities"]
        for facility in facilities:
            tally = Event.objects.filter(facility=facility, created_via=TALLY).count()
            standard = Event.objects.filter(facility=facility, created_via=Event.CreatedVia.STANDARD).count()
            assert tally > 0, facility.name
            assert standard == SCALE_CONFIG["medium"]["events_per_facility"], facility.name

    def test_tally_events_look_like_form_created_ones(self, _medium_seed):
        events = Event.objects.filter(created_via=TALLY)
        assert events.exists()
        assert not events.filter(is_anonymous=False).exists()
        assert not events.filter(client__isnull=False).exists()
        assert not events.filter(is_deleted=True).exists()

    def test_only_eligible_types_get_tally_events_and_each_eligible_type_gets_some(self, _medium_seed):
        seen_ineligible = 0
        for facility in Facility.objects.all():
            for doc_type in DocumentType.objects.filter(facility=facility, entity_type=DocumentType.EntityType.EVENT):
                eligibility = evaluate_tally_eligibility(doc_type)
                count = Event.objects.filter(document_type=doc_type, created_via=TALLY).count()
                if eligibility.eligible:
                    assert count > 0, (facility.name, doc_type.slug)
                else:
                    seen_ineligible += 1
                    assert count == 0, (facility.name, doc_type.slug, eligibility.reasons)
        assert seen_ineligible > 0, "counter-check would be vacuous without an ineligible type"

    def test_data_json_matches_the_tally_form_contract(self, _medium_seed):
        modes_seen = set()
        for event in Event.objects.filter(created_via=TALLY).select_related("document_type"):
            eligibility = evaluate_tally_eligibility(event.document_type)
            modes_seen.add(eligibility.mode)
            if eligibility.mode is TallyMode.DIRECT:
                assert event.data_json == {}
                continue
            required = {a.field_template.slug: a.field_template for a in eligibility.required_number_fields}
            assert set(event.data_json) == set(required)
            for slug, template in required.items():
                value = event.data_json[slug]
                assert isinstance(value, int) and not isinstance(value, bool)
                if template.min_value is not None:
                    assert value >= template.min_value
                if template.max_value is not None:
                    assert value <= template.max_value
        assert modes_seen == {TallyMode.DIRECT, TallyMode.NUMBER}

    def test_events_are_spread_over_the_period_and_present_on_the_seed_day(self, _medium_seed):
        today = timezone.localdate()
        for facility in Facility.objects.all():
            days = {
                timezone.localdate(moment)
                for moment in Event.objects.filter(facility=facility, created_via=TALLY).values_list(
                    "occurred_at", flat=True
                )
            }
            live_start = current_tally_context(facility).window_start
            assert Event.objects.filter(
                facility=facility, created_via=TALLY, occurred_at__gte=live_start, occurred_at__lte=timezone.now()
            ).exists(), facility.name
            assert min(days) < today - timedelta(days=90), facility.name
            assert len(days) > 10, facility.name
            assert max(days) <= today

    def test_zeitstrom_kpi_and_only_tally_filter_show_the_seeded_events(self, _medium_seed):
        for facility in Facility.objects.all():
            viewer = User.objects.filter(facility=facility, role=User.Role.FACILITY_ADMIN).first()
            assert viewer is not None, facility.name
            state = resolve_zeitstrom_state({}, facility)
            summary = build_tally_summary(facility, viewer, context=state.live_context)
            kpis = build_zeitstrom_kpis(facility, viewer, state, tally_summary=summary)
            assert kpis["tally_total"] and kpis["tally_total"] > 0, facility.name
            params = dict(parse_qsl(kpis["tally_params"]))
            assert params["created_via"] == "tally"
            filters = resolve_event_list_filters(params, facility, viewer, today=timezone.localdate())
            assert event_list_queryset(facility, viewer, filters).count() > 0, facility.name

    def test_kpi_of_a_past_seed_day(self, _medium_seed):
        facility = Facility.objects.get(name="Hauptstelle")
        viewer = User.objects.filter(facility=facility, role=User.Role.FACILITY_ADMIN).first()
        moment = (
            Event.objects.filter(
                facility=facility, created_via=TALLY, occurred_at__lt=timezone.now() - timedelta(days=5)
            )
            .order_by("-occurred_at")
            .values_list("occurred_at", flat=True)
            .first()
        )
        assert moment is not None
        day = timezone.localdate(moment)
        start, end = get_time_range(day, None)
        context = TallyLiveContext(None, day, start, end)
        summary = build_tally_summary(facility, viewer, context=context)
        assert summary["tally_total"] > 0


@pytest.fixture
def tally_setup(facility, staff_user):
    """A facility with tally-eligible and -ineligible event types and one shift."""
    time_filter = TimeFilter.objects.create(
        facility=facility, label="Window", start_time=time(10, 0), end_time=time(12, 0)
    )

    def number_field(name, **extra):
        return FieldTemplate.objects.create(
            facility=facility, name=name, field_type=FieldTemplate.FieldType.NUMBER, is_required=True, **extra
        )

    def attach(doc_type, *templates):
        for order, template in enumerate(templates):
            DocumentTypeField.objects.create(document_type=doc_type, field_template=template, sort_order=order)

    with_default = DocumentType.objects.create(facility=facility, name="Counted", tally_enabled=True)
    attach(
        with_default,
        number_field("Issued", default_value="4", min_value=1, max_value=9),
        number_field("Returned", min_value=0, max_value=5),
    )
    direct = DocumentType.objects.create(facility=facility, name="Direct", tally_enabled=True)
    not_opted_in = DocumentType.objects.create(facility=facility, name="Not opted in", tally_enabled=False)
    needs_person = DocumentType.objects.create(
        facility=facility, name="Needs person", tally_enabled=True, min_contact_stage="qualified"
    )
    text_required = DocumentType.objects.create(facility=facility, name="Text required", tally_enabled=True)
    attach(
        text_required,
        FieldTemplate.objects.create(
            facility=facility, name="Remark", field_type=FieldTemplate.FieldType.TEXT, is_required=True
        ),
    )
    clients = [Client.objects.create(facility=facility, pseudonym=f"P-{i}", created_by=staff_user) for i in range(3)]
    return {
        "facility": facility,
        "users": [staff_user],
        "clients": clients,
        "time_filter": time_filter,
        "with_default": with_default,
        "direct": direct,
        "ineligible": [not_opted_in, needs_person, text_required],
    }


CFG = {"events_per_facility": 60, "zeitraum_days": 120, "tally_ratio": 0.5}


def _run(setup, cfg=None, now=None):
    return seed_events_bulk(setup["facility"], setup["users"], setup["clients"], cfg or CFG, now=now or timezone.now())


@pytest.mark.django_db
class TestSeedEventsBulkTally:
    @pytest.fixture(autouse=True)
    def _fixed_random_seed(self):
        import random

        random.seed(1234)

    def test_ratio_is_additional_and_only_eligible_types_are_used(self, tally_setup):
        created = _run(tally_setup)
        facility = tally_setup["facility"]
        tally = Event.objects.filter(facility=facility, created_via=TALLY)
        standard = Event.objects.filter(facility=facility, created_via=Event.CreatedVia.STANDARD)
        assert standard.count() == CFG["events_per_facility"]
        assert tally.count() == 30
        assert created == 90
        assert set(tally.values_list("document_type_id", flat=True)) == {
            tally_setup["with_default"].pk,
            tally_setup["direct"].pk,
        }
        assert not tally.filter(is_anonymous=False).exists()
        assert not tally.filter(client__isnull=False).exists()

    def test_missing_ratio_key_creates_no_tally_events(self, tally_setup):
        _run(tally_setup, {"events_per_facility": 60, "zeitraum_days": 120})
        assert not Event.objects.filter(created_via=TALLY).exists()

    def test_number_values_use_the_default_and_deviate_within_the_bounds(self, tally_setup):
        _run(tally_setup)
        values = list(
            Event.objects.filter(created_via=TALLY, document_type=tally_setup["with_default"]).values_list(
                "data_json", flat=True
            )
        )
        assert values
        issued = [data["issued"] for data in values]
        returned = [data["returned"] for data in values]
        assert 4 in issued, "the configured default is used"
        assert len(set(issued)) > 1, "values deviate from the default"
        assert all(1 <= value <= 9 for value in issued)
        assert all(0 <= value <= 5 for value in returned)
        assert len(set(returned)) > 1
        direct = Event.objects.filter(created_via=TALLY, document_type=tally_setup["direct"])
        assert direct.exists()
        assert all(data == {} for data in direct.values_list("data_json", flat=True))

    def test_times_cluster_in_the_active_shift_windows(self, tally_setup):
        now = timezone.now()
        _run(tally_setup, now=now)
        windows = [tally_setup["time_filter"]]
        # The events of the seed day follow the running window (or the day so far), not the shift.
        older = [
            e
            for e in Event.objects.filter(created_via=TALLY)
            if timezone.localdate(e.occurred_at) != timezone.localdate(now)
        ]
        assert len(older) >= 20
        inside = sum(_in_any_window(event, windows) for event in older)
        assert inside / len(older) >= 0.65

    def test_events_exist_on_the_seed_day_inside_the_running_window(self, tally_setup):
        now = timezone.now()
        _run(tally_setup, now=now)
        facility = tally_setup["facility"]
        today = timezone.localdate(now)
        todays = [e for e in Event.objects.filter(created_via=TALLY) if timezone.localdate(e.occurred_at) == today]
        assert todays
        assert all(e.occurred_at <= now for e in todays)
        context = current_tally_context(facility, now=timezone.localtime(now))
        viewer = User.objects.filter(facility=facility).first()
        summary = build_tally_summary(facility, viewer, context=context)
        assert summary["tally_total"] > 0

    def test_same_random_seed_gives_the_same_tally_events(self, tally_setup):
        import random

        now = timezone.now()
        facility = tally_setup["facility"]

        def snapshot():
            return sorted(
                (e.document_type.slug, e.occurred_at, str(sorted(e.data_json.items())))
                for e in Event.objects.filter(facility=facility, created_via=TALLY).select_related("document_type")
            )

        random.seed(7)
        _run(tally_setup, now=now)
        first = snapshot()
        Event.objects.filter(facility=facility).delete()
        random.seed(7)
        _run(tally_setup, now=now)
        assert snapshot() == first

    def test_standard_events_and_random_stream_are_unchanged_by_the_ratio(self, tally_setup):
        import random

        now = timezone.now()
        facility = tally_setup["facility"]

        def standard_events():
            return sorted(
                (e.document_type.slug, e.occurred_at, str(sorted(e.data_json.items())))
                for e in Event.objects.filter(facility=facility, created_via=Event.CreatedVia.STANDARD).select_related(
                    "document_type"
                )
            )

        random.seed(11)
        _run(tally_setup, {**CFG, "tally_ratio": 0}, now=now)
        without = standard_events()
        stream_without = random.random()
        Event.objects.filter(facility=facility).delete()
        random.seed(11)
        _run(tally_setup, now=now)
        assert standard_events() == without
        assert random.random() == stream_without

    def test_rerun_adds_nothing_and_flush_removes_the_tally_events(self, tally_setup):
        _run(tally_setup)
        before = Event.objects.count()
        assert _run(tally_setup) == 0
        assert Event.objects.count() == before
        assert Event.objects.filter(created_via=TALLY).exists()
        flush_seed_data()
        assert not Event.objects.filter(created_via=TALLY).exists()
        assert Event.objects.count() == 0

    def test_no_eligible_type_means_no_tally_events(self, tally_setup):
        for doc_type in (tally_setup["with_default"], tally_setup["direct"]):
            doc_type.tally_enabled = False
            doc_type.save()
        _run(tally_setup)
        assert not Event.objects.filter(created_via=TALLY).exists()
        assert Event.objects.exists()
