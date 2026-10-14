"""Fall und Ereignis bearbeiten: Neu-Anzeige und Konfliktprüfung im Zusammenspiel.

Zwei Zusagen treffen im Bearbeiten-Formular aufeinander:

* Refs #2371: zeigt der Server das Formular nach einer ungültigen Eingabe
  erneut an, trägt es weiterhin den Versions-Token ``expected_updated_at``
  aus dem ersten Laden (auch einen leeren) — der Instanzwert gilt nur
  beim Laden per GET.
* Refs #2359: der versteckte Fingerabdruck ``expected_form_state`` rettet
  einen Versionskonflikt nur, wenn sich seit dem Laden kein Formularfeld
  geändert hat (ein Statuswechsel bzw. eine Fallzuordnung außerhalb des
  Formulars also nicht als Konflikt zählt).

Die Tests fahren beides über den HTTP-Weg hintereinander, wie zwei
Browser-Tabs: Tab A lädt das Formular, Tab B ändert parallel, Tab A schickt
zuerst eine ungültige und danach eine korrigierte Eingabe ab. Die
versteckten Felder werden jedes Mal aus dem gerenderten HTML gelesen und
unverändert zurückgeschickt — so prüfen die Tests den Weg der Oberfläche,
nicht einen einzelnen Mechanismus. Würde die Neu-Anzeige Token oder
Fingerabdruck auf den Datenbankstand vorrücken, überschriebe der zweite
Versuch die fremde Änderung still.
"""

from html.parser import HTMLParser

import pytest
from django.urls import reverse

from core.models import Case, Event
from core.services.case import assign_event_to_case, close_case, remove_event_from_case, update_case
from core.services.case.cases import case_form_state_token
from core.services.events import update_event
from core.services.events.crud import event_form_state_token

CONFLICT_TEXT = "zwischenzeitlich bearbeitet"


class _HiddenInputs(HTMLParser):
    """Sammelt ``<input type="hidden">`` des Formulars (ohne CSRF)."""

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


def _hidden_values(response):
    parser = _HiddenInputs()
    parser.feed(response.content.decode())
    return parser.values


def _load_form(http, url):
    """Tab A: Formular laden, Versions-Token und Fingerabdruck übernehmen."""
    response = http.get(url)
    assert response.status_code == 200
    hidden = _hidden_values(response)
    assert hidden.get("expected_updated_at"), "Formular muss den Versions-Token tragen"
    assert hidden.get("expected_form_state"), "Formular muss den Fingerabdruck tragen"
    return hidden


def _assert_rerender_keeps(response, hidden):
    """Neu-Anzeige mit Fehlern trägt Token und Fingerabdruck aus dem ersten Laden."""
    assert response.status_code == 200, "Ungültige Eingabe wird inline erneut angezeigt"
    rerendered = _hidden_values(response)
    assert rerendered.get("expected_updated_at") == hidden["expected_updated_at"], (
        "Neu-Anzeige muss den Versions-Token aus dem ersten Laden behalten"
    )
    assert rerendered.get("expected_form_state") == hidden["expected_form_state"], (
        "Neu-Anzeige muss den Fingerabdruck aus dem ersten Laden behalten"
    )
    return rerendered


def _messages_text(response):
    return " ".join(str(message) for message in response.context["messages"])


# ---------------------------------------------------------------------------
# Fall bearbeiten
# ---------------------------------------------------------------------------


def _case_url(case):
    return reverse("core:case_update", kwargs={"pk": case.pk})


def _case_payload(case, hidden, **overrides):
    """Sichtbare Felder mit dem Stand zum Renderzeitpunkt + Änderungen."""
    payload = dict(hidden)
    payload.update(
        {
            "title": case.title,
            "description": case.description or "",
            "lead_user": str(case.lead_user_id or ""),
            "document_type": str(case.document_type_id or ""),
            "client": str(case.client_id),
        }
    )
    payload.update(overrides)
    return payload


@pytest.fixture
def described_case(case_open):
    case_open.description = "Beschreibung aus dem ersten Laden."
    case_open.save(update_fields=["description", "updated_at"])
    return case_open


@pytest.mark.django_db
class TestCaseEditRerenderThenSubmit:
    def test_parallel_title_change_stays_conflict_across_rerender(self, client, staff_user, described_case):
        client.force_login(staff_user)
        url = _case_url(described_case)
        hidden = _load_form(client, url)
        snapshot = Case.objects.get(pk=described_case.pk)

        # Tab B ändert ein Feld, das auch im Formular steht.
        update_case(Case.objects.get(pk=described_case.pk), staff_user, title="Titel aus Tab B")
        fresh = Case.objects.get(pk=described_case.pk)
        assert fresh.updated_at.isoformat() != hidden["expected_updated_at"]
        assert case_form_state_token(fresh) != hidden["expected_form_state"]

        invalid = client.post(url, _case_payload(snapshot, hidden, title=""))
        rerendered = _assert_rerender_keeps(invalid, hidden)

        response = client.post(
            url,
            _case_payload(snapshot, rerendered, title="Titel aus Tab A", description="Beschreibung aus Tab A"),
            follow=True,
        )

        assert response.status_code == 200
        assert response.redirect_chain == []
        assert response.context["has_edit_conflict"]
        assert CONFLICT_TEXT in response.content.decode()
        described_case.refresh_from_db()
        assert described_case.title == "Titel aus Tab B", "Parallele Änderung darf nicht still überschrieben werden"
        assert described_case.description == snapshot.description, "Konflikt darf nichts teilweise schreiben"

    def test_parallel_close_is_saved_across_rerender(self, client, staff_user, lead_user, described_case):
        client.force_login(staff_user)
        url = _case_url(described_case)
        hidden = _load_form(client, url)
        snapshot = Case.objects.get(pk=described_case.pk)

        # Tab B schließt den Fall — Status gehört nicht zum Formular.
        close_case(Case.objects.get(pk=described_case.pk), lead_user)
        assert Case.objects.get(pk=described_case.pk).updated_at.isoformat() != hidden["expected_updated_at"]

        invalid = client.post(url, _case_payload(snapshot, hidden, title=""))
        rerendered = _assert_rerender_keeps(invalid, hidden)

        response = client.post(url, _case_payload(snapshot, rerendered, title="Titel aus Tab A"))

        assert response.status_code == 302
        assert response.url == reverse("core:case_detail", kwargs={"pk": described_case.pk})
        described_case.refresh_from_db()
        assert described_case.title == "Titel aus Tab A", "Eingabe aus Tab A muss gespeichert werden"
        assert described_case.status == Case.Status.CLOSED, "Parallel gesetzter Status muss erhalten bleiben"
        assert described_case.closed_at is not None
        assert described_case.description == snapshot.description

    def test_empty_token_stays_empty_on_rerender(self, client, staff_user, described_case):
        client.force_login(staff_user)
        url = _case_url(described_case)
        hidden = _load_form(client, url)
        snapshot = Case.objects.get(pk=described_case.pk)
        update_case(Case.objects.get(pk=described_case.pk), staff_user, title="Titel aus Tab B")
        fresh_token = Case.objects.get(pk=described_case.pk).updated_at.isoformat()
        hidden.pop("expected_updated_at")

        response = client.post(url, _case_payload(snapshot, hidden, title=""))

        assert response.status_code == 200
        rerendered = _hidden_values(response)
        assert "expected_updated_at" in rerendered
        assert rerendered["expected_updated_at"] == "", "Neu-Anzeige darf keinen frischen Token erzeugen"
        assert rerendered["expected_updated_at"] != fresh_token
        assert rerendered.get("expected_form_state") == hidden["expected_form_state"]

    def test_get_renders_current_state(self, client, staff_user, described_case):
        """Gegenprobe: nur das Laden per GET trägt den aktuellen Datenbankstand."""
        client.force_login(staff_user)
        update_case(Case.objects.get(pk=described_case.pk), staff_user, title="Titel aus Tab B")
        fresh = Case.objects.get(pk=described_case.pk)

        hidden = _load_form(client, _case_url(described_case))

        assert hidden["expected_updated_at"] == fresh.updated_at.isoformat()
        assert hidden["expected_form_state"] == case_form_state_token(fresh)


# ---------------------------------------------------------------------------
# Ereignis bearbeiten
# ---------------------------------------------------------------------------


def _event_url(event):
    return reverse("core:event_update", kwargs={"pk": event.pk})


def _event_payload(hidden, **overrides):
    payload = dict(hidden)
    payload.update({"dauer": "15", "notiz": "Notiz aus Tab A"})
    payload.update(overrides)
    return payload


@pytest.fixture
def linked_event(sample_event, case_open):
    sample_event.case = case_open
    sample_event.save(update_fields=["case", "updated_at"])
    return sample_event


@pytest.mark.django_db
class TestEventEditRerenderThenSubmit:
    def test_parallel_note_change_stays_conflict_across_rerender(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        url = _event_url(sample_event)
        hidden = _load_form(client, url)

        # Tab B ändert die Notiz, die auch im Formular steht.
        update_event(Event.objects.get(pk=sample_event.pk), staff_user, {"dauer": 42, "notiz": "Notiz aus Tab B"})
        fresh = Event.objects.get(pk=sample_event.pk)
        assert fresh.updated_at.isoformat() != hidden["expected_updated_at"]
        assert event_form_state_token(fresh) != hidden["expected_form_state"]

        invalid = client.post(url, _event_payload(hidden, dauer="keine-zahl"))
        rerendered = _assert_rerender_keeps(invalid, hidden)

        response = client.post(url, _event_payload(rerendered, dauer="20"), follow=True)

        assert response.redirect_chain[0] == (url, 302), "Konflikt führt zurück ins Formular"
        assert CONFLICT_TEXT in _messages_text(response)
        sample_event.refresh_from_db()
        assert sample_event.data_json == {"dauer": 42, "notiz": "Notiz aus Tab B"}, (
            "Parallele Änderung darf nicht still überschrieben werden"
        )

    @pytest.mark.parametrize("direction", ["assign", "remove"])
    def test_parallel_case_link_is_saved_across_rerender(
        self, request, client, staff_user, case_open, sample_event, direction
    ):
        event = request.getfixturevalue("linked_event") if direction == "remove" else sample_event
        client.force_login(staff_user)
        url = _event_url(event)
        hidden = _load_form(client, url)

        # Tab B ordnet das Ereignis einem Fall zu bzw. löst es — außerhalb des Formulars.
        if direction == "assign":
            assign_event_to_case(case_open, Event.objects.get(pk=event.pk), staff_user)
            expected_case = case_open.pk
        else:
            remove_event_from_case(Event.objects.get(pk=event.pk), staff_user)
            expected_case = None
        fresh = Event.objects.get(pk=event.pk)
        assert fresh.case_id == expected_case
        assert fresh.updated_at.isoformat() != hidden["expected_updated_at"]

        invalid = client.post(url, _event_payload(hidden, dauer="keine-zahl"))
        rerendered = _assert_rerender_keeps(invalid, hidden)

        response = client.post(url, _event_payload(rerendered, dauer="20"))

        assert response.status_code == 302
        assert response.url == reverse("core:event_detail", kwargs={"pk": event.pk})
        fresh = Event.objects.get(pk=event.pk)
        assert fresh.data_json["notiz"] == "Notiz aus Tab A", "Eingabe aus Tab A muss gespeichert werden"
        assert fresh.data_json["dauer"] == 20
        assert fresh.case_id == expected_case, "Parallele Fallzuordnung muss erhalten bleiben"

    def test_empty_token_stays_empty_on_rerender(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        url = _event_url(sample_event)
        hidden = _load_form(client, url)
        update_event(Event.objects.get(pk=sample_event.pk), staff_user, {"dauer": 42, "notiz": "Notiz aus Tab B"})
        fresh_token = Event.objects.get(pk=sample_event.pk).updated_at.isoformat()
        hidden.pop("expected_updated_at")

        response = client.post(url, _event_payload(hidden, dauer="keine-zahl"))

        assert response.status_code == 200
        rerendered = _hidden_values(response)
        assert "expected_updated_at" in rerendered
        assert rerendered["expected_updated_at"] == "", "Neu-Anzeige darf keinen frischen Token erzeugen"
        assert rerendered["expected_updated_at"] != fresh_token
        assert rerendered.get("expected_form_state") == hidden["expected_form_state"]

    def test_get_renders_current_state(self, client, staff_user, sample_event):
        """Gegenprobe: nur das Laden per GET trägt den aktuellen Datenbankstand."""
        client.force_login(staff_user)
        update_event(Event.objects.get(pk=sample_event.pk), staff_user, {"dauer": 42, "notiz": "Notiz aus Tab B"})
        fresh = Event.objects.get(pk=sample_event.pk)

        hidden = _load_form(client, _event_url(sample_event))

        assert hidden["expected_updated_at"] == fresh.updated_at.isoformat()
        assert hidden["expected_form_state"] == event_form_state_token(fresh)
