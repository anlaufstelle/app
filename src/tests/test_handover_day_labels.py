"""Day-mode labels keep shift configuration semantics intact."""

from datetime import time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import TimeFilter

pytestmark = pytest.mark.django_db


def test_day_mode_titles_and_empty_note(client, staff_user, facility):
    client.force_login(staff_user)
    html = client.get(reverse("core:zeitstrom"), {"view": "uebergabe"}).content.decode()
    assert "Übergabenotiz vom Vortag" in html
    assert "Notiz für heute" in html
    assert "Übergabenotiz der letzten Schicht" not in html
    assert "Notiz für die laufende Schicht" not in html
    assert "bis zum Ende des Folgetags" in html


def test_day_mode_older_navigation_is_neutral(client, staff_user, facility):
    client.force_login(staff_user)
    day = timezone.localdate() - timedelta(days=4)
    html = client.get(reverse("core:zeitstrom"), {"view": "uebergabe", "date": day.isoformat()}).content.decode()
    assert "Übergabenotiz dieses Tages" in html
    assert "Übergabenotiz vom Vortag" not in html
    assert "Für diesen Tag wurde keine Übergabenotiz hinterlassen." in html


def test_shift_mode_titles_stay_unchanged(client, staff_user, facility):
    TimeFilter.objects.create(facility=facility, label="Day shift", start_time=time(0), end_time=time(23, 59))
    client.force_login(staff_user)
    html = client.get(reverse("core:zeitstrom"), {"view": "uebergabe"}).content.decode()
    assert "Übergabenotiz der letzten Schicht" in html
    assert "Notiz für die laufende Schicht" in html
    assert "Notiz für heute" not in html
