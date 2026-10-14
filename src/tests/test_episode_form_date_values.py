"""Episode edit form: date inputs carry ISO values a ``type=date`` input accepts.

A localized value (``13.10.2026``) is discarded by the browser, so ``Beginn``
showed empty (and blocked the submit as a missing required field) and ``Ende``
of a closed episode showed empty (a plain save then reopened the episode).
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def closed_episode(episode):
    from datetime import date, timedelta

    episode.ended_at = date.today() - timedelta(days=1)
    episode.save(update_fields=["ended_at"])
    return episode


def test_edit_form_renders_iso_dates_for_start_and_end(client, staff_user, case_open, closed_episode):
    client.force_login(staff_user)
    url = reverse("core:episode_update", kwargs={"case_pk": case_open.pk, "pk": closed_episode.pk})

    html = client.get(url).content.decode()

    assert f'name="started_at" value="{closed_episode.started_at.isoformat()}"' in html
    assert f'name="ended_at" value="{closed_episode.ended_at.isoformat()}"' in html
