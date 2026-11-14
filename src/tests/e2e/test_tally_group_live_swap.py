"""E2E: an opened tally-hour group stays open when the live watch swaps the feed (#2811).

The feed is replaced in the background whenever it changed on the server. The
reader who just opened an hour group to follow its list link must not find the
group closed again.
"""

import re

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e

PREFIX = "E2E TallySwap"
SWAP_DAY = "2019-04-17"

_CLEANUP = f"""
from core.models import DocumentType, Event

Event.objects.filter(document_type__name__startswith='{PREFIX}').delete()
DocumentType.objects.filter(name__startswith='{PREFIX}').delete()
"""

# Two hour groups of tally contacts on SWAP_DAY (hour 13: two contacts, hour 14: one).
_SETUP = f"""
from datetime import datetime
from django.utils import timezone
from core.models import DocumentType, Event, Facility, User

facility = Facility.objects.get(name='Hauptstelle')
actor = User.objects.get(username='miriam')
doc_type = DocumentType.objects.create(
    facility=facility,
    name='{PREFIX} Kontakt',
    entity_type=DocumentType.EntityType.EVENT,
    sensitivity=DocumentType.Sensitivity.NORMAL,
)
for hour in (13, 13, 14):
    Event.objects.create(
        facility=facility, document_type=doc_type,
        occurred_at=timezone.make_aware(datetime(2019, 4, 17, hour, 0)), is_anonymous=True,
        created_via=Event.CreatedVia.TALLY, data_json={{}}, created_by=actor,
    )
"""

# A further tally contact in a new hour: the feed validator changes and the next watch cycle swaps the feed.
_ADD_GROUP = f"""
from datetime import datetime
from django.utils import timezone
from core.models import DocumentType, Event, Facility, User

facility = Facility.objects.get(name='Hauptstelle')
doc_type = DocumentType.objects.get(facility=facility, name='{PREFIX} Kontakt')
Event.objects.create(
    facility=facility, document_type=doc_type,
    occurred_at=timezone.make_aware(datetime(2019, 4, 17, 16, 0)), is_anonymous=True,
    created_via=Event.CreatedVia.TALLY, data_json={{}}, created_by=User.objects.get(username='miriam'),
)
"""


@pytest.fixture
def tally_groups(e2e_env):
    """Create the hour groups of ``SWAP_DAY``; remove everything afterwards."""
    django_shell(e2e_env, _CLEANUP)
    django_shell(e2e_env, _SETUP)
    yield
    django_shell(e2e_env, _CLEANUP)


@pytest.mark.parametrize(
    "viewport",
    [{"width": 1440, "height": 900}, {"width": 390, "height": 844}],
    ids=["desktop", "mobile"],
)
def test_opened_tally_group_stays_open_after_a_live_swap(authenticated_page, base_url, e2e_env, tally_groups, viewport):
    page = authenticated_page
    page.set_viewport_size(viewport)
    page.goto(f"{base_url}/?date={SWAP_DAY}&time_filter=all", wait_until="load")
    page.wait_for_function("() => !!window.zeitstromWatch")
    # Finish the first watch cycle (it always swaps the feed once) before opening a group.
    page.evaluate("() => window.zeitstromWatch.checkNow()")
    groups = page.get_by_test_id("tally-group")
    expect(groups).to_have_count(2)

    toggle = groups.first.get_by_test_id("tally-group-toggle")
    controls = toggle.get_attribute("aria-controls")
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    link = page.locator(f"#{controls}").get_by_role("link", name="Gezählte Kontakte anzeigen")
    expect(link).to_be_visible()
    page.evaluate(
        "controls => document.querySelector(`[aria-controls='${controls}']`).closest('[data-testid=tally-group]')"
        ".dataset.swapProbe = '1'",
        controls,
    )

    # A new hour group appears on the server; the next cycle swaps the whole feed list.
    django_shell(e2e_env, _ADD_GROUP)
    page.evaluate("() => window.zeitstromWatch.checkNow()")
    expect(groups).to_have_count(3)
    assert page.locator("[data-swap-probe]").count() == 0, "the feed was not replaced by the live watch"

    reopened = page.locator(f"button[aria-controls='{controls}']")
    expect(reopened).to_have_attribute("aria-expanded", "true")
    link = page.locator(f"#{controls}").get_by_role("link", name="Gezählte Kontakte anzeigen")
    expect(link).to_be_visible()
    link.click()
    expect(page).to_have_url(re.compile(r"created_via=tally"))
