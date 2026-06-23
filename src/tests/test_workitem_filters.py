"""Tests für WorkItem-Inbox-Filter."""

import re

import pytest
from django.urls import reverse

from core.models import WorkItem


@pytest.mark.django_db
class TestWorkItemInboxFilters:
    """WorkItem-Inbox filtert nach Typ, Priorität und Zuweisung."""

    def test_filter_by_item_type(self, client, staff_user, facility):
        """Nur WorkItems des gewählten Typs werden angezeigt."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            title="Aufgabe 1",
            assigned_to=staff_user,
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            item_type=WorkItem.ItemType.HINT,
            title="Hinweis 1",
            assigned_to=staff_user,
        )

        response = client.get(reverse("core:workitem_inbox"), {"item_type": "task"})
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert all(wi.item_type == WorkItem.ItemType.TASK for wi in all_items)

    def test_filter_by_priority(self, client, staff_user, facility):
        """Nur WorkItems der gewählten Priorität werden angezeigt."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            priority=WorkItem.Priority.URGENT,
            title="Dringend",
            assigned_to=staff_user,
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            priority=WorkItem.Priority.NORMAL,
            title="Normal",
            assigned_to=staff_user,
        )

        response = client.get(reverse("core:workitem_inbox"), {"priority": "urgent"})
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert all(wi.priority == WorkItem.Priority.URGENT for wi in all_items)

    def test_filter_by_assigned_to(self, client, staff_user, lead_user, facility):
        """Nur WorkItems des zugewiesenen Users werden angezeigt."""
        client.force_login(staff_user)
        wi_staff = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            title="Für Staff",
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Für Lead",
        )

        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": str(staff_user.pk)})
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        assert wi_staff in open_items

    def test_inbox_me_filter_shows_own_items_only(self, client, staff_user, lead_user, facility):
        """Sentinel 'me' zeigt nur eigene zugewiesene WorkItems."""
        client.force_login(staff_user)
        wi_self = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            title="Mir",
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Anderem",
        )

        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": "me"})
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        assert wi_self in open_items
        assert all(wi.assigned_to_id == staff_user.id for wi in open_items)

    def test_inbox_me_filter_excludes_other_assigned_items(self, client, staff_user, lead_user, facility):
        """Sentinel 'me' schließt Items anderer Zuweisungen (inkl. unassigned) aus."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Für Lead",
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=None,
            title="Unassigned",
        )

        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": "me"})
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        in_progress_items = list(response.context["in_progress_items"])
        done_items = list(response.context["done_items"])
        assert all(wi.assigned_to_id == staff_user.id for wi in open_items)
        assert all(wi.assigned_to_id == staff_user.id for wi in in_progress_items)
        assert all(wi.assigned_to_id == staff_user.id for wi in done_items)
        assert len(open_items) == 0

    def test_inbox_default_remains_all(self, client, staff_user, lead_user, facility):
        """Default-Verhalten ohne Filter: eigene + unassigned in Open/In-Progress."""
        client.force_login(staff_user)
        wi_self = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            title="Mir",
        )
        wi_unassigned = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=None,
            title="Unassigned",
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Für Lead",
        )

        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        # Implicit filter: eigene + unassigned sichtbar, fremde nicht
        assert wi_self in open_items
        assert wi_unassigned in open_items
        assert len(open_items) == 2
        # Refs #1145: Die Default-Sicht (kein Parameter) ist nicht der strikte
        # "me"-Filter. Der sichtbare Filter trägt den eigenen Sentinel
        # ``mine_team`` ("Mir & unzugewiesene"), damit Anzeige und Query-Logik
        # übereinstimmen — vorher meldete der Default ``""`` und das Template
        # zeigte mangels gesetztem ``selected`` fälschlich die erste Option
        # ("Mir zugewiesen") an.
        assert response.context["selected_assigned_to"] == "mine_team"

    def test_default_filter_not_marked_as_me(self, client, staff_user, facility):
        """Refs #1145: Im Default ist nicht der strikte "me"-Filter aktiv.

        Beim Aufruf aus einem anderen Menüpunkt (kein ``assigned_to``-Parameter)
        zeigte die Inbox den Filter sichtbar als "Mir zugewiesen" an — die erste
        ``<option>`` ohne gesetztes ``selected`` wird vom Browser angezeigt —,
        lieferte aber die breitere Default-Liste (eigene + unzugewiesene). Die
        sichtbare Auswahl muss zur Query passen: der Default-Sentinel
        ``mine_team`` ist gewählt, nicht ``me``.
        """
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        assert response.context["selected_assigned_to"] == "mine_team"
        html = response.content.decode()
        # Die Default-Option ist als selected markiert, die strikte
        # "Mir zugewiesen"-Option (value="me") nicht.
        assert '<option value="mine_team" selected' in html
        assert '<option value="me" selected' not in html

    def test_me_filter_marked_selected_in_rendered_html(self, client, staff_user, facility):
        """Refs #1145: Mit ``assigned_to=me`` ist die strikte Option selected.

        Gegenstück zu ``test_default_filter_not_marked_as_me`` — nur wenn die
        Nutzer:in bewusst "Mir zugewiesen" wählt, trägt diese Option das
        ``selected``-Attribut, und der Default-Sentinel nicht.
        """
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": "me"})
        assert response.status_code == 200
        assert response.context["selected_assigned_to"] == "me"
        html = response.content.decode()
        assert '<option value="me" selected' in html
        assert '<option value="mine_team" selected' not in html

    def test_default_sentinel_round_trips_to_default_scope(self, client, staff_user, lead_user, facility):
        """Refs #1145: ``assigned_to=mine_team`` reproduziert die Default-Sicht.

        Der Bulk-Redirect (Refs #1132) und die Filter-Persistenz schreiben den
        aktiven Filterwert zurück in die URL bzw. den Storage. Damit Anzeige und
        Query nach so einem Round-Trip übereinstimmen, muss der Sentinel ``mine_team``
        dieselbe Liste liefern wie der parameterlose Default: eigene +
        unzugewiesene, aber keine fremd-zugewiesenen.
        """
        client.force_login(staff_user)
        wi_self = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            title="Mir",
        )
        wi_unassigned = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=None,
            title="Unassigned",
        )
        wi_foreign = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Für Lead",
        )

        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": "mine_team"})
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        assert wi_self in open_items
        assert wi_unassigned in open_items
        assert wi_foreign not in open_items
        assert response.context["selected_assigned_to"] == "mine_team"

    def test_explicit_all_filter_shows_foreign_assigned(self, client, staff_user, lead_user, facility):
        """Explizit ``assigned_to=`` (Alle) zeigt auch fremd-zugewiesene Aufgaben.

        Refs #1125: Solange es keine privaten Aufgaben (#607) gibt, muss eine
        normale Aufgabe innerhalb der Facility über den Alle-Filter auffindbar
        sein — auch wenn sie einer anderen Person zugewiesen ist.
        """
        client.force_login(staff_user)
        wi_foreign = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Für Lead, von Staff angelegt",
        )

        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": ""})
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        # Erstellerin (Staff) findet die fremd-zugewiesene Aufgabe über "Alle".
        assert wi_foreign in open_items

    def test_default_done_list_scoped_like_open_inprogress(self, client, staff_user, lead_user, facility):
        """Refs #1134: Die "Kürzlich erledigt"-Liste folgt im Default derselben
        Mir-zugewiesen-+-Teamaufgaben-Eingrenzung wie Offen/In-Bearbeitung.

        Vorher war die Done-Liste die *einzige* unscoped Liste: in der Default-
        Sicht (kein ``assigned_to``-Parameter) zeigte sie auch fremd-zugewiesene
        erledigte Aufgaben an und machte sie per Bulk auswählbar. Eine Bulk-
        Statusänderung (z.B. Erledigt → In Bearbeitung) verschob das Item dann in
        die *scoped* In-Bearbeitung-Liste, wo es für die handelnde Person nicht
        mehr auftauchte — Liste und tatsächlicher Status liefen auseinander.
        """
        client.force_login(staff_user)
        done_self = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            title="Erledigt – mir",
            status=WorkItem.Status.DONE,
        )
        done_unassigned = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=None,
            title="Erledigt – Team",
            status=WorkItem.Status.DONE,
        )
        done_foreign = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Erledigt – fremd",
            status=WorkItem.Status.DONE,
        )

        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        done_items = list(response.context["done_items"])
        assert done_self in done_items
        assert done_unassigned in done_items
        # Konsistent mit Offen/In-Bearbeitung: fremd-zugewiesene erledigte
        # Aufgaben tauchen im Default nicht auf (und sind damit nicht per Bulk
        # auswählbar).
        assert done_foreign not in done_items

    def test_explicit_all_filter_shows_foreign_done(self, client, staff_user, lead_user, facility):
        """Refs #1134: Über den expliziten "Alle"-Filter bleibt eine fremd-
        zugewiesene erledigte Aufgabe weiterhin sichtbar — analog zu
        ``test_explicit_all_filter_shows_foreign_assigned`` für Offen."""
        client.force_login(staff_user)
        done_foreign = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Erledigt – fremd, über Alle auffindbar",
            status=WorkItem.Status.DONE,
        )

        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": ""})
        assert response.status_code == 200
        done_items = list(response.context["done_items"])
        assert done_foreign in done_items

    def test_person_filter_shows_other_persons_items(self, client, staff_user, lead_user, facility):
        """Personenfilter auf eine *andere* Person zeigt deren Aufgaben.

        Refs #1125: Vorher schnitt die Inbox jede Liste hart mit
        ``Q(assigned_to=user) | isnull`` — ein Personenfilter auf jemand
        anderen lieferte damit eine leere Liste.
        """
        client.force_login(staff_user)
        wi_lead = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Lead-Aufgabe",
        )

        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": str(lead_user.pk)})
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        assert wi_lead in open_items
        assert all(wi.assigned_to_id == lead_user.id for wi in open_items)

    def test_creator_finds_own_task_assigned_to_other_via_all(self, client, staff_user, lead_user, facility):
        """Selbst erstellte, an andere Person zugewiesene Aufgabe bleibt auffindbar.

        Refs #1125 (Kern des wiedereröffneten Tickets): Miriam (Staff) legt eine
        Aufgabe an und weist sie Emma (Lead) zu. Über den Alle-Filter muss sie
        die Aufgabe wiederfinden — sie darf nicht "verschwinden".
        """
        client.force_login(staff_user)
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Von Miriam für Emma",
        )
        # Default (Mir zugewiesen) blendet sie aus …
        default_resp = client.get(reverse("core:workitem_inbox"))
        assert wi not in list(default_resp.context["open_items"])
        # … aber über "Alle" ist sie auffindbar.
        all_resp = client.get(reverse("core:workitem_inbox"), {"assigned_to": ""})
        assert wi in list(all_resp.context["open_items"])

    def test_no_filter_returns_all(self, client, staff_user, facility):
        """Ohne Filter werden alle eigenen WorkItems angezeigt."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            title="T1",
            assigned_to=staff_user,
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            item_type=WorkItem.ItemType.HINT,
            title="H1",
            assigned_to=staff_user,
        )

        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        all_items = list(response.context["open_items"])
        assert len(all_items) == 2

    def test_invalid_filter_value_ignored(self, client, staff_user, facility):
        """Ungültige Filterwerte werden ignoriert (kein Fehler)."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="T1",
            assigned_to=staff_user,
        )

        response = client.get(reverse("core:workitem_inbox"), {"item_type": "invalid"})
        assert response.status_code == 200

    def test_combined_filters_me_task_urgent(self, client, staff_user, lead_user, facility):
        """Kombi-Filter ?assigned_to=me&item_type=task&priority=urgent schneidet alle drei Kriterien.

        Refs #591 WP3.
        """
        client.force_login(staff_user)

        # 1) me + task + urgent → matcht
        wi_match = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            item_type=WorkItem.ItemType.TASK,
            priority=WorkItem.Priority.URGENT,
            title="Match Me+Task+Urgent",
        )
        # 2) me + task + normal → item_type OK, priority falsch
        wi_wrong_priority = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            item_type=WorkItem.ItemType.TASK,
            priority=WorkItem.Priority.NORMAL,
            title="Me+Task+Normal",
        )
        # 3) me + hint + urgent → priority OK, item_type falsch
        wi_wrong_type = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            item_type=WorkItem.ItemType.HINT,
            priority=WorkItem.Priority.URGENT,
            title="Me+Hint+Urgent",
        )
        # 4) lead + task + urgent → item_type+priority OK, Zuweisung falsch
        wi_wrong_assignee = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            item_type=WorkItem.ItemType.TASK,
            priority=WorkItem.Priority.URGENT,
            title="Lead+Task+Urgent",
        )

        response = client.get(
            reverse("core:workitem_inbox"),
            {
                "assigned_to": "me",
                "item_type": "task",
                "priority": "urgent",
            },
        )
        assert response.status_code == 200

        all_items = (
            list(response.context["open_items"])
            + list(response.context["in_progress_items"])
            + list(response.context["done_items"])
        )
        assert wi_match in all_items
        assert wi_wrong_priority not in all_items
        assert wi_wrong_type not in all_items
        assert wi_wrong_assignee not in all_items

    def test_htmx_request_returns_partial(self, client, staff_user, facility):
        """HTMX-Requests liefern nur das Inbox-Content-Partial."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:workitem_inbox"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "core/workitems/partials/inbox_content.html" in [t.name for t in response.templates]


@pytest.mark.django_db
class TestWorkItemInboxFilterHxIncludeScope:
    """Refs #1145 (Reopen): Die Filter-Selects dürfen beim HTMX-Reload nicht
    die gleichnamigen Bulk-Aktions-Selects mit einsammeln.

    Wurzel des wiedereröffneten Tickets: Die Bulk-Forms enthalten eigene
    ``<select name="assigned_to">`` und ``<select name="priority">``. Die
    Filter-Selects bündelten ihre Geschwister früher per ``hx-include`` über
    ``[name='assigned_to']`` / ``[name='priority']`` — diese Selektoren matchen
    aber **auch** die (leeren) Bulk-Selects. HTMX hängte deren leeren Wert als
    zweiten ``assigned_to=``/``priority=`` an die Query; ``request.GET.get`` nimmt
    den *letzten* Wert (leer = "Alle"). Ergebnis: Der sichtbare Filter zeigte
    weiter "Mir & unzugewiesene"/"Mir zugewiesen", die Liste war aber "Alle" und
    zeigte fremd-zugewiesene Aufgaben — exakt das Reopen-Symptom.
    """

    def test_filter_hx_include_does_not_match_bulk_selects(self, client, staff_user, facility):
        """``hx-include`` der Filter-Selects zielt auf die Filter-IDs, nicht auf
        die kollidierenden ``[name='assigned_to']``/``[name='priority']``.

        Die Bulk-Forms tragen eigene Selects ``name="assigned_to"`` und
        ``name="priority"``. Solange die Filter-Selects ihre Geschwister über
        ``[name='…']`` einbinden, hängt HTMX beim Filter-Reload zusätzlich den
        leeren Bulk-Wert an die Query — der überschreibt (als letzter Wert) den
        echten Filter. Das Einbinden über die stabilen Filter-IDs schließt die
        Bulk-Selects sauber aus.
        """
        client.force_login(staff_user)
        html = client.get(reverse("core:workitem_inbox")).content.decode()

        includes = re.findall(r'hx-include="([^"]*)"', html)
        assert includes, "Filter-Selects ohne hx-include — Test-Annahme stimmt nicht mehr"
        for include in includes:
            assert "[name='assigned_to']" not in include, (
                "hx-include sammelt das Bulk-Assign-Select mit ein: " + include
            )
            assert "[name='priority']" not in include, "hx-include sammelt das Bulk-Priority-Select mit ein: " + include
