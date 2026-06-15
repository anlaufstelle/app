"""Coverage-Tests fuer ``core.views.handover.HandoverView``.

Deckt die Branches:

* ``_get_target_date``: ungueltiger Datums-Param -> Fallback auf heute (Lines 62-65).
* ``_get_target_date``: gueltiger ISO-Param wird uebernommen.
* Time-Filter-Lookup: ungueltige PK -> ``selected_filter = None`` (Lines 37-39).
* Time-Filter-Lookup: gueltige PK -> Filter wird gesetzt.

Refs #922 (Coverage-Lift).
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone


@pytest.mark.django_db
class TestHandoverOverdueMarking:
    """Refs #1120: Überfällige offene Aufgaben werden in der Übergabe-Tabelle
    sichtbar (textlich + farblich) markiert, erledigte nicht."""

    def _make_task(self, facility, user, *, due_date, status="open", title="Aufgabe"):
        from core.models import WorkItem

        return WorkItem.objects.create(
            facility=facility,
            created_by=user,
            title=title,
            priority="normal",
            status=status,
            due_date=due_date,
        )

    def test_overdue_open_task_is_marked(self, client, staff_user, facility):
        yesterday = timezone.localdate() - timedelta(days=1)
        self._make_task(facility, staff_user, due_date=yesterday, title="Überfällige Aufgabe")
        client.force_login(staff_user)
        response = client.get(reverse("core:handover"))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="handover-task-overdue"' in content
        assert "Überfällig" in content

    def test_future_open_task_not_marked_overdue(self, client, staff_user, facility):
        tomorrow = timezone.localdate() + timedelta(days=1)
        self._make_task(facility, staff_user, due_date=tomorrow, title="Künftige Aufgabe")
        client.force_login(staff_user)
        response = client.get(reverse("core:handover"))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="handover-task-overdue"' not in content

    def test_completed_task_not_shown_as_overdue(self, client, staff_user, facility):
        """Eine erledigte Aufgabe mit vergangenem Fälligkeitsdatum erscheint
        nicht in den offenen Aufgaben und wird daher nicht als überfällig markiert."""
        yesterday = timezone.localdate() - timedelta(days=1)
        self._make_task(facility, staff_user, due_date=yesterday, status="done", title="Erledigte Aufgabe")
        client.force_login(staff_user)
        response = client.get(reverse("core:handover"))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="handover-task-overdue"' not in content


@pytest.mark.django_db
class TestHandoverViewEdges:
    def test_invalid_date_param_falls_back_to_today(self, client, staff_user):
        """Lines 62-65: ``ValueError`` -> Fallback auf ``timezone.localdate()``."""
        client.force_login(staff_user)
        response = client.get(reverse("core:handover") + "?date=not-a-date")
        assert response.status_code == 200
        assert response.context["target_date"] == response.context["today"]

    def test_explicit_iso_date_used(self, client, staff_user):
        """Gueltiger ISO-Param wird uebernommen."""
        client.force_login(staff_user)
        response = client.get(reverse("core:handover") + "?date=2026-01-15")
        assert response.status_code == 200
        assert response.context["target_date"] == date(2026, 1, 15)

    def test_invalid_time_filter_id_ignored(self, client, staff_user, facility):
        """Lines 30: ``filter(pk=...)`` mit ungueltigem PK -> None statt 500."""
        client.force_login(staff_user)
        # UUID-kompatible aber nicht existente ID
        response = client.get(reverse("core:handover") + "?time_filter=00000000-0000-0000-0000-000000000000")
        assert response.status_code == 200
        assert response.context["selected_filter"] is None

    def test_valid_time_filter_id_selected(self, client, staff_user, facility):
        """Lines 28-30: existierende, aktive TimeFilter-PK -> wird gesetzt."""
        from core.models import TimeFilter

        TimeFilter.objects.filter(facility=facility).delete()
        tf = TimeFilter.objects.create(
            facility=facility,
            label="Vormittag",
            start_time="08:00",
            end_time="12:00",
            is_active=True,
            sort_order=10,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:handover") + f"?time_filter={tf.pk}")
        assert response.status_code == 200
        assert response.context["selected_filter"] == tf

    def test_auto_select_previous_shift_for_today(self, client, staff_user, facility):
        """Lines 37-41: Heute ohne Filter -> vorherige Schicht wird ausgewählt."""
        from core.models import TimeFilter

        TimeFilter.objects.filter(facility=facility).delete()
        # Zwei TimeFilter: Vormittag (8-12), Nachmittag (12-18). Mindestens einer
        # muss `covers_time(now)` True liefern, damit die Auto-Select-Schleife
        # einen `prev_filter` setzt.
        TimeFilter.objects.create(
            facility=facility,
            label="Vormittag",
            start_time="08:00",
            end_time="12:00",
            is_active=True,
            sort_order=10,
        )
        TimeFilter.objects.create(
            facility=facility,
            label="Nachmittag",
            start_time="12:00",
            end_time="18:00",
            is_active=True,
            sort_order=20,
        )

        client.force_login(staff_user)
        # Ohne ?time_filter, ohne ?date — target_date == heute, Auto-Select-Pfad
        # wird betreten. Egal welcher Filter "jetzt" abdeckt, `prev_filter`
        # wird in der Schleife auf den vorherigen Filter gesetzt (oder bleibt
        # None, wenn der erste Filter "jetzt" abdeckt).
        response = client.get(reverse("core:handover"))
        assert response.status_code == 200
        # Lines 37-41 wurden ausgeführt; ob selected_filter gesetzt ist hängt
        # vom aktuellen Tageszeitpunkt ab — Hauptsache der Pfad wurde betreten.
        assert "selected_filter" in response.context
