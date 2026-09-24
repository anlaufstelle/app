"""Fachliche HTMX-Absagen (409) werden nicht in das Ziel eingewechselt.

Refs #2358. Seit der globalen 4xx-Swap-Regel in ``base.html`` (Refs #2232)
wechselt htmx jede 4xx-Antwort in das ``hx-target`` des ausloesenden
Elements ein. Die fachliche Absage einer Aktion (Einschraenkung nach
Art. 18, Zustandsvertrag der Fallakte) ist aber ein kurzer Klartext und
kein Partial: eingewechselt ersetzte er den ganzen Kasten „Wirkungsziele“
bzw. die Ereignisliste des Falls. Die Antwort traegt
deshalb ``HX-Reswap: none`` — der Kasten bleibt stehen, und der globale
Fehler-Toast aus ``static/js/htmx-errors.js`` zeigt die Meldung (er
erscheint genau dann, wenn nicht eingewechselt wurde).
"""

import pytest
from django.urls import reverse

from core.models import Client, Event
from core.models.outcome import Milestone

HTMX = {"HTTP_HX_REQUEST": "true"}


def _sperre(client_identified):
    Client.objects.filter(pk=client_identified.pk).update(processing_restricted=True)


def _assert_absage_ohne_swap(response):
    assert response.status_code == 409, response.status_code
    assert response.headers.get("HX-Reswap") == "none", dict(response.headers)
    text = response.content.decode()
    assert text.strip()
    assert "<" not in text, "Absage muss kurzer Klartext sein (htmx-Fehler-Toast)"


@pytest.mark.django_db
class TestWirkungszieleBeiEinschraenkung:
    def test_ziel_anlegen(self, client, staff_user, case_open, client_identified):
        _sperre(client_identified)
        client.force_login(staff_user)
        response = client.post(reverse("core:goal_create", kwargs={"case_pk": case_open.pk}), {"title": "Neu"}, **HTMX)
        _assert_absage_ohne_swap(response)

    def test_ziel_bearbeiten(self, client, staff_user, case_open, outcome_goal, client_identified):
        _sperre(client_identified)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:goal_update", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk}),
            {"title": "Geaendert"},
            **HTMX,
        )
        _assert_absage_ohne_swap(response)

    def test_ziel_abhaken(self, client, staff_user, case_open, outcome_goal, client_identified):
        _sperre(client_identified)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:goal_toggle", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk}), **HTMX
        )
        _assert_absage_ohne_swap(response)

    def test_meilenstein_anlegen(self, client, staff_user, case_open, outcome_goal, milestone, client_identified):
        _sperre(client_identified)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:milestone_create", kwargs={"case_pk": case_open.pk, "goal_pk": outcome_goal.pk}),
            {"title": "Neuer Meilenstein"},
            **HTMX,
        )
        _assert_absage_ohne_swap(response)
        assert list(Milestone.objects.filter(goal=outcome_goal)) == [milestone]

    def test_meilenstein_abhaken(self, client, staff_user, case_open, milestone, client_identified):
        _sperre(client_identified)
        client.force_login(staff_user)
        vorher = Milestone.objects.get(pk=milestone.pk).is_completed
        response = client.post(
            reverse("core:milestone_toggle", kwargs={"case_pk": case_open.pk, "pk": milestone.pk}), **HTMX
        )
        _assert_absage_ohne_swap(response)
        assert Milestone.objects.get(pk=milestone.pk).is_completed == vorher

    def test_meilenstein_loeschen(self, client, staff_user, case_open, milestone, client_identified):
        _sperre(client_identified)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:milestone_delete", kwargs={"case_pk": case_open.pk, "pk": milestone.pk}), **HTMX
        )
        _assert_absage_ohne_swap(response)
        assert Milestone.objects.filter(pk=milestone.pk).exists()

    def test_gegenprobe_ohne_einschraenkung_wird_eingewechselt(
        self, client, staff_user, case_open, outcome_goal, client_identified
    ):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:milestone_create", kwargs={"case_pk": case_open.pk, "goal_pk": outcome_goal.pk}),
            {"title": "Neuer Meilenstein"},
            **HTMX,
        )
        assert response.status_code == 200
        assert "HX-Reswap" not in response.headers
        assert "Neuer Meilenstein" in response.content.decode()


@pytest.mark.django_db
class TestFallereignisseBeiEinschraenkung:
    def test_zuordnung(self, client, staff_user, case_open, sample_event, client_identified):
        _sperre(client_identified)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_assign_event", kwargs={"pk": case_open.pk}), {"event_id": sample_event.pk}, **HTMX
        )
        _assert_absage_ohne_swap(response)

    def test_entfernung(self, client, staff_user, case_open, sample_event, client_identified):
        Event.objects.filter(pk=sample_event.pk).update(case=case_open)
        _sperre(client_identified)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_remove_event", kwargs={"pk": case_open.pk, "event_pk": sample_event.pk}), **HTMX
        )
        _assert_absage_ohne_swap(response)
