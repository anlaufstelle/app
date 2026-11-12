"""Pins for the active-filter chips and the remove links of the event list.

Source: targeted mutation delta ZS0, batch ``list_filters-a`` (49 mutants of
``event_list_active_filters``).
Refs #2712
Refs #1997

The earlier view tests only check substrings of single chips. These pins
compare the complete chip list (key, visible text, removal link) under the
German locale, and the conditions that decide when the period chip appears.
"""

from datetime import time

import pytest
from django.utils import translation

from core.models import Case, Event, TimeFilter
from core.services.events.list_filters import event_list_active_filters, event_list_options

pytestmark = pytest.mark.django_db

BASE_URL = "/events/"


def chips(filters, options):
    with translation.override("de"):
        return event_list_active_filters(filters, options, base_url=BASE_URL)


@pytest.fixture
def named_options(facility, staff_user, client_identified, doc_type_contact):
    """Real option lists whose ``str()`` differs from the displayed attribute."""
    case = Case.objects.create(facility=facility, client=client_identified, title="Winter housing")
    shift = TimeFilter.objects.create(facility=facility, label="Early", start_time=time(6), end_time=time(14))
    options = event_list_options(facility, staff_user)
    return {
        "options": options,
        "case": case,
        "shift": shift,
        "doc_type": doc_type_contact,
        "client": client_identified,
        "user": staff_user,
    }


def test_every_chip_has_its_label_name_and_removal_link(named_options):
    """Complete chip list: label text, resolved option name and exact removal URL.

    Pins x_event_list_active_filters__mutmut_13, x_event_list_active_filters__mutmut_41,
    x_event_list_active_filters__mutmut_42, x_event_list_active_filters__mutmut_60,
    x_event_list_active_filters__mutmut_61, x_event_list_active_filters__mutmut_62,
    x_event_list_active_filters__mutmut_63, x_event_list_active_filters__mutmut_64,
    x_event_list_active_filters__mutmut_70, x_event_list_active_filters__mutmut_71,
    x_event_list_active_filters__mutmut_72, x_event_list_active_filters__mutmut_73,
    x_event_list_active_filters__mutmut_74, x_event_list_active_filters__mutmut_80,
    x_event_list_active_filters__mutmut_81, x_event_list_active_filters__mutmut_82,
    x_event_list_active_filters__mutmut_90, x_event_list_active_filters__mutmut_91,
    x_event_list_active_filters__mutmut_92, x_event_list_active_filters__mutmut_100,
    x_event_list_active_filters__mutmut_101, x_event_list_active_filters__mutmut_102,
    x_event_list_active_filters__mutmut_103, x_event_list_active_filters__mutmut_104,
    x_event_list_active_filters__mutmut_109, x_event_list_active_filters__mutmut_167,
    x_event_list_active_filters__mutmut_186.
    """
    n = named_options
    filters = {
        "range": "today",
        "doc_type": str(n["doc_type"].pk),
        "client": str(n["client"].pk),
        "case": str(n["case"].pk),
        "time_filter": str(n["shift"].pk),
        "created_by": str(n["user"].pk),
        "mine": "1",
        "created_via": Event.CreatedVia.TALLY,
    }
    result = chips(filters, n["options"])

    assert [(chip["key"], chip["text"]) for chip in result] == [
        ("period", "Zeitraum: Heute"),
        ("doc_type", f"Typ: {n['doc_type'].name}"),
        ("client", f"Person: {n['client'].pseudonym}"),
        ("case", "Fall: Winter housing"),
        ("time_filter", "Schicht: Early"),
        ("created_by", f"Erfasst von: {n['user']}"),
        ("mine", "Nur meine"),
        ("created_via", "Nur Strichliste"),
    ]
    by_key = {chip["key"]: chip["remove_url"] for chip in result}
    assert by_key["mine"] == (
        f"{BASE_URL}?range=today&doc_type={filters['doc_type']}&client={filters['client']}"
        f"&case={filters['case']}&time_filter={filters['time_filter']}&created_by={filters['created_by']}"
        "&created_via=tally"
    )
    assert by_key["period"].startswith(f"{BASE_URL}?doc_type=")
    assert "range=" not in by_key["period"]


def test_created_via_standard_reads_without_tally(named_options):
    """Pins x_event_list_active_filters__mutmut_193."""
    result = chips({"created_via": Event.CreatedVia.STANDARD}, named_options["options"])

    assert [chip["text"] for chip in result] == ["Ohne Strichliste"]
    assert result[0]["remove_url"] == BASE_URL


def test_anonymous_chip_says_yes_or_no(named_options):
    """Pins x_event_list_active_filters__mutmut_128, x_event_list_active_filters__mutmut_131,
    x_event_list_active_filters__mutmut_132, x_event_list_active_filters__mutmut_133,
    x_event_list_active_filters__mutmut_135, x_event_list_active_filters__mutmut_136,
    x_event_list_active_filters__mutmut_139, x_event_list_active_filters__mutmut_140,
    x_event_list_active_filters__mutmut_141, x_event_list_active_filters__mutmut_143,
    x_event_list_active_filters__mutmut_144.
    """
    options = named_options["options"]

    assert [chip["text"] for chip in chips({"anonymous": "yes"}, options)] == ["Anonym: ja"]
    no_chips = chips({"anonymous": "no", "mine": "1"}, options)
    assert [chip["text"] for chip in no_chips] == ["Anonym: nein", "Nur meine"]
    assert [chip["remove_url"] for chip in no_chips] == [f"{BASE_URL}?mine=1", f"{BASE_URL}?anonymous=no"]


@pytest.mark.parametrize(
    "filters",
    [{"range": "today"}, {"date_from": "2026-06-10"}, {"date_to": "2026-06-10"}],
    ids=["range-only", "date-from-only", "date-to-only"],
)
def test_period_chip_appears_for_each_single_period_key(named_options, filters):
    """Pins x_event_list_active_filters__mutmut_20, x_event_list_active_filters__mutmut_21,
    x_event_list_active_filters__mutmut_22, x_event_list_active_filters__mutmut_23,
    x_event_list_active_filters__mutmut_25, x_event_list_active_filters__mutmut_26,
    x_event_list_active_filters__mutmut_28, x_event_list_active_filters__mutmut_29.
    """
    result = chips(filters, named_options["options"])

    assert [chip["key"] for chip in result] == ["period"]
    assert result[0]["text"].startswith("Zeitraum: ")


def test_missing_option_list_falls_back_to_the_raw_identifier(named_options):
    """Pins x_event_list_active_filters__mutmut_114, x_event_list_active_filters__mutmut_116.

    A caller that supplies no option lists still gets a chip per active filter.
    """
    identifier = str(named_options["doc_type"].pk)

    assert [chip["text"] for chip in chips({"doc_type": identifier}, {})] == [f"Typ: {identifier}"]
