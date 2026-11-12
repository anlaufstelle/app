"""Pin for the bare removal link of the event list chips.

Source: targeted mutation delta ZS0, batch ``list_filters-e`` (3 mutants; the
two in ``event_list_queryset`` are result-equivalent: a missing ``q`` is falsy
both as ``None`` and as an empty string, so only the link builder is pinned).
Refs #2712
Refs #1997
"""

from core.services.events.list_filters import event_list_active_filters


def test_removing_the_last_filter_links_to_the_bare_list_url():
    """Pins x__url__mutmut_2: no parameters left means no trailing question mark."""
    chips = event_list_active_filters({"mine": "1"}, {}, base_url="/events/")

    assert [(chip["key"], chip["remove_url"]) for chip in chips] == [("mine", "/events/")]
