"""Ereignis bearbeiten bei paralleler Fallzuordnung — Oberflächenweg (Refs #2359).

Tab A rendert das Bearbeiten-Formular eines Ereignisses, Tab B ordnet das
Ereignis einem Fall zu (bzw. löst es), Tab A speichert danach eine
geänderte Notiz. Die Fallzuordnung gehört nicht zum Formular — die Eingabe
muss gespeichert werden und die Zuordnung aus Tab B erhalten bleiben. Eine
parallele Inhaltsänderung bleibt ein Konflikt; der JSON-Weg
(Offline-Replay) behält seinen strikten Versionsvertrag.
"""

from html.parser import HTMLParser

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from core.models import Event
from core.services.events import update_event


class _HiddenInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attr = dict(attrs)
        name = attr.get("name")
        if attr.get("type") == "hidden" and name and name != "csrfmiddlewaretoken":
            self.values[name] = attr.get("value") or ""


def _render(http, event):
    response = http.get(reverse("core:event_update", kwargs={"pk": event.pk}))
    assert response.status_code == 200
    parser = _HiddenInputs()
    parser.feed(response.content.decode())
    assert "expected_updated_at" in parser.values
    return parser.values


def _other_tab(user):
    http = HttpClient()
    http.force_login(user)
    return http


def _payload(hidden, **overrides):
    payload = dict(hidden)
    payload.update({"dauer": "15", "notiz": "Neue Notiz aus Tab A"})
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestEventEditSurvivesParallelCaseLink:
    @pytest.mark.parametrize("direction", ["assign", "remove"])
    def test_edit_is_saved_and_link_kept(self, client, staff_user, sample_event, case_open, direction):
        if direction == "remove":
            sample_event.case = case_open
            sample_event.save(update_fields=["case", "updated_at"])
        client.force_login(staff_user)
        hidden = _render(client, sample_event)
        assert hidden.get("expected_form_state"), "Formular muss den Stand des Ereignisses tragen"

        other = _other_tab(staff_user)
        if direction == "assign":
            response = other.post(
                reverse("core:case_assign_event", kwargs={"pk": case_open.pk}), {"event_id": str(sample_event.pk)}
            )
            expected_case = case_open.pk
        else:
            response = other.post(
                reverse("core:case_remove_event", kwargs={"pk": case_open.pk, "event_pk": sample_event.pk})
            )
            expected_case = None
        assert response.status_code in (200, 302)

        response = client.post(reverse("core:event_update", kwargs={"pk": sample_event.pk}), _payload(hidden))

        assert response.status_code == 302
        assert response["Location"] == reverse("core:event_detail", kwargs={"pk": sample_event.pk})
        fresh = Event.objects.get(pk=sample_event.pk)
        assert fresh.case_id == expected_case
        assert fresh.data_json["notiz"] == "Neue Notiz aus Tab A"

    def test_parallel_content_change_stays_conflict(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        hidden = _render(client, sample_event)
        update_event(Event.objects.get(pk=sample_event.pk), staff_user, {"dauer": 5, "notiz": "Aus Tab B"})

        response = client.post(reverse("core:event_update", kwargs={"pk": sample_event.pk}), _payload(hidden))

        assert response.status_code == 302
        assert response["Location"] == reverse("core:event_update", kwargs={"pk": sample_event.pk})
        assert Event.objects.get(pk=sample_event.pk).data_json["notiz"] == "Aus Tab B"

    def test_json_replay_after_parallel_link_stays_409(self, client, staff_user, sample_event, case_open):
        client.force_login(staff_user)
        hidden = _render(client, sample_event)
        _other_tab(staff_user).post(
            reverse("core:case_assign_event", kwargs={"pk": case_open.pk}), {"event_id": str(sample_event.pk)}
        )

        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            _payload(hidden),
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 409
        assert response.json()["error"] == "conflict"
        assert Event.objects.get(pk=sample_event.pk).data_json["notiz"] == "Testnotiz"

    def test_invalid_post_rerenders_the_posted_form_state(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        hidden = _render(client, sample_event)

        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}), _payload(hidden, dauer="x")
        )

        assert response.status_code == 200
        parser = _HiddenInputs()
        parser.feed(response.content.decode())
        assert parser.values.get("expected_form_state") == hidden["expected_form_state"]

    def test_invalid_post_keeps_the_rendered_form_state_after_parallel_edit(self, client, staff_user, sample_event):
        """Tab B ändert die Notiz zwischen Laden und ungültigem POST aus Tab A.

        Die Neu-Anzeige muss den Fingerabdruck aus Tab A weitertragen — nicht
        einen aus dem inzwischen geänderten Datenbankstand neu berechneten.
        """
        from core.services.events.crud import event_form_state_token

        client.force_login(staff_user)
        hidden = _render(client, sample_event)
        update_event(Event.objects.get(pk=sample_event.pk), staff_user, {"dauer": 15, "notiz": "Aus Tab B"})
        current_state = event_form_state_token(Event.objects.get(pk=sample_event.pk))
        assert current_state != hidden["expected_form_state"]

        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}), _payload(hidden, dauer="x")
        )

        assert response.status_code == 200
        parser = _HiddenInputs()
        parser.feed(response.content.decode())
        assert parser.values.get("expected_form_state") == hidden["expected_form_state"]
        assert parser.values.get("expected_form_state") != current_state
