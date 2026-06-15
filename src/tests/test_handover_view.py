"""Tests für die Übergabe-Ansicht (?view=uebergabe im Zeitstrom).

Die frühere ``HandoverView`` ist in den Zeitstrom gefaltet (Refs #1124);
``/uebergabe/`` ist ein permanenter Redirect auf ``/?view=uebergabe``. Die
Übergabe-spezifischen Darstellungs-Regeln (überfällige Aufgaben, Refs #1120)
werden hier weiterhin am Übergabe-Modus verifiziert.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone


@pytest.mark.django_db
class TestUebergabeOverdueMarking:
    """Refs #1120: Überfällige offene Aufgaben werden in der Übergabe-Ansicht
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

    def _get_uebergabe(self, client):
        return client.get(reverse("core:zeitstrom"), {"view": "uebergabe"})

    def test_overdue_open_task_is_marked(self, client, staff_user, facility):
        yesterday = timezone.localdate() - timedelta(days=1)
        self._make_task(facility, staff_user, due_date=yesterday, title="Überfällige Aufgabe")
        client.force_login(staff_user)
        response = self._get_uebergabe(client)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="handover-task-overdue"' in content
        assert "Überfällig" in content

    def test_future_open_task_not_marked_overdue(self, client, staff_user, facility):
        tomorrow = timezone.localdate() + timedelta(days=1)
        self._make_task(facility, staff_user, due_date=tomorrow, title="Künftige Aufgabe")
        client.force_login(staff_user)
        response = self._get_uebergabe(client)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="handover-task-overdue"' not in content

    def test_completed_task_not_shown_as_overdue(self, client, staff_user, facility):
        """Eine erledigte Aufgabe mit vergangenem Fälligkeitsdatum erscheint
        nicht in den offenen Aufgaben und wird daher nicht als überfällig markiert."""
        yesterday = timezone.localdate() - timedelta(days=1)
        self._make_task(facility, staff_user, due_date=yesterday, status="done", title="Erledigte Aufgabe")
        client.force_login(staff_user)
        response = self._get_uebergabe(client)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="handover-task-overdue"' not in content
