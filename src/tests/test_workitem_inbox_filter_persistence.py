"""Tests für die Inbox-Filter-Persistenz ohne Flash (Refs #1611).

Deckt die serverseitig prüfbaren Teile des Fixes ab:

- Die Filter-Selects tragen ``hx-replace-url`` (Refs #1611, Task 1) — der
  aktive Filter landet als Querystring in der URL, ohne die History mit jedem
  Filterwechsel vollzuschreiben (bewusst ``replace`` statt ``push``, siehe
  Issue-Vorgabe "History-Spam vermeiden").
- ``has_active_filters`` steht im Template-Kontext (Task 3) und unterscheidet
  Default-Sicht von aktiv gefilterter Sicht.
- Der Empty-State ist filterbewusst: bei aktiven Filtern "Keine Treffer für die
  aktuellen Filter" + "Filter zurücksetzen", sonst der bisherige Text.

Die JS-Umbauten (Redirect statt künstlichem ``change``-Trigger, Reset-Link
löscht sessionStorage) sind nur per E2E prüfbar (siehe
``src/tests/e2e/test_filters.py``) und hier bewusst nicht abgedeckt.
"""

import pytest
from django.urls import reverse

from core.models import WorkItem


@pytest.mark.django_db
class TestInboxReplaceUrl:
    """Die Filter-Selects schreiben den Querystring in die URL (replace, kein push)."""

    def test_filter_selects_carry_replace_url(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        content = response.content.decode()
        # Sechs Filter-Selects (Typ, Priorität, Zuweisung, Fälligkeit, Status,
        # Zeitraum) tragen je hx-replace-url="true".
        assert content.count('hx-replace-url="true"') >= 6

    def test_filter_bar_opts_into_url_restore(self, client, staff_user):
        """Die Filterleiste signalisiert dem JS den URL-basierten Restore."""
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        content = response.content.decode()
        assert "data-filter-persist-url" in content


@pytest.mark.django_db
class TestHasActiveFilters:
    """``has_active_filters`` unterscheidet Default- von gefilterter Sicht."""

    def test_default_view_has_no_active_filters(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        assert response.context["has_active_filters"] is False

    def test_mine_team_sentinel_is_not_an_active_filter(self, client, staff_user):
        """Der Default-Sentinel ``mine_team`` ist die Default-Sicht, kein Filter."""
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": "mine_team"})
        assert response.context["has_active_filters"] is False

    def test_priority_filter_is_active(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"priority": "important"})
        assert response.context["has_active_filters"] is True

    def test_item_type_filter_is_active(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"item_type": "task"})
        assert response.context["has_active_filters"] is True

    def test_explicit_all_assignment_is_active(self, client, staff_user):
        """Explizit "Alle" (assigned_to leer) weicht bewusst vom Default ab."""
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": ""})
        assert response.context["has_active_filters"] is True

    def test_status_filter_is_active(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"status": "open"})
        assert response.context["has_active_filters"] is True


@pytest.mark.django_db
class TestFilterAwareEmptyState:
    """Der Empty-State erwähnt aktive Filter und bietet einen Weg zurück."""

    def test_empty_state_without_filter_is_neutral(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        content = response.content.decode()
        assert "Keine offenen Aufgaben" in content
        assert "Keine Treffer für die aktuellen Filter" not in content

    def test_empty_state_with_active_filter_mentions_filter(self, client, staff_user):
        """Bei aktivem Filter ohne Treffer: filterbewusster Text + Reset-Aktion."""
        client.force_login(staff_user)
        # Keine Aufgaben angelegt -> mit priority-Filter ist die Liste leer.
        response = client.get(reverse("core:workitem_inbox"), {"priority": "urgent"})
        content = response.content.decode()
        assert "Keine Treffer für die aktuellen Filter" in content
        assert "Filter zurücksetzen" in content
        assert "data-filter-reset" in content
        assert "Keine offenen Aufgaben" not in content

    def test_filtered_empty_state_hidden_when_all_sections_have_results(self, client, staff_user, facility):
        """Haben beide Sektionen (Offen + In Bearbeitung) Treffer, erscheint kein Empty-State."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            priority=WorkItem.Priority.URGENT,
            status=WorkItem.Status.OPEN,
            title="Dringend offen",
            assigned_to=staff_user,
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            priority=WorkItem.Priority.URGENT,
            status=WorkItem.Status.IN_PROGRESS,
            title="Dringend in Bearbeitung",
            assigned_to=staff_user,
        )
        response = client.get(reverse("core:workitem_inbox"), {"priority": "urgent"})
        content = response.content.decode()
        assert "Keine Treffer für die aktuellen Filter" not in content
