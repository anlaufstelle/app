"""Fall-Views: eine Absage des Zustandsvertrags endet nicht als Serverfehler.

Refs #2329. ``assign_event_to_case``/``remove_event_from_case`` und
``create_goal``/``update_goal`` lehnen an einer gesperrten, geloeschten oder
getilgten Akte mit einer deutschen ``ValidationError`` ab. Die HTMX-Views
muessen diese Absage wie ``GoalToggleView`` als kurze 409-Antwort mit dem
Vertragstext zurueckgeben — ohne Schreibweg, ohne Audit- und Zeitstrom-Zeile.
Ungefangen wurde daraus ein HTTP 500 (der Testclient reicht die Ausnahme
dann durch).
"""

import pytest
from django.urls import reverse

from core.models import Activity, AuditLog, Case, Client, Event
from core.models.outcome import OutcomeGoal
from core.services.case import soft_delete_case

HTMX = {"HTTP_HX_REQUEST": "true"}


def _spuren():
    return AuditLog.objects.count(), Activity.objects.count(), OutcomeGoal.objects.count()


def _sperre(client_identified):
    Client.objects.filter(pk=client_identified.pk).update(processing_restricted=True)


def _assert_absage(response):
    assert response.status_code == 409, response.status_code
    text = response.content.decode()
    assert text.strip()
    assert "<" not in text, "Absage muss kurzer Klartext sein (htmx-Fehler-Toast)"


def _ordne_zu(event, case):
    Event.objects.filter(pk=event.pk).update(case=case)


@pytest.mark.django_db
class TestGesperrtePerson:
    def test_zuordnung(self, client, staff_user, case_open, sample_event, client_identified):
        _sperre(client_identified)
        client.force_login(staff_user)
        vorher = _spuren()
        response = client.post(
            reverse("core:case_assign_event", kwargs={"pk": case_open.pk}), {"event_id": sample_event.pk}, **HTMX
        )
        _assert_absage(response)
        assert Event.objects.get(pk=sample_event.pk).case_id is None
        assert _spuren() == vorher

    def test_entfernung(self, client, staff_user, case_open, sample_event, client_identified):
        _ordne_zu(sample_event, case_open)
        _sperre(client_identified)
        client.force_login(staff_user)
        vorher = _spuren()
        response = client.post(
            reverse("core:case_remove_event", kwargs={"pk": case_open.pk, "event_pk": sample_event.pk}), **HTMX
        )
        _assert_absage(response)
        assert Event.objects.get(pk=sample_event.pk).case_id == case_open.pk
        assert _spuren() == vorher

    def test_zielanlage(self, client, staff_user, case_open, client_identified):
        _sperre(client_identified)
        client.force_login(staff_user)
        vorher = _spuren()
        response = client.post(reverse("core:goal_create", kwargs={"case_pk": case_open.pk}), {"title": "Neu"}, **HTMX)
        _assert_absage(response)
        assert _spuren() == vorher

    def test_zielbearbeitung(self, client, staff_user, case_open, outcome_goal, client_identified):
        _sperre(client_identified)
        client.force_login(staff_user)
        titel = outcome_goal.title
        vorher = _spuren()
        response = client.post(
            reverse("core:goal_update", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk}),
            {"title": "Geaendert", "description": "Freitext"},
            **HTMX,
        )
        _assert_absage(response)
        assert OutcomeGoal.objects.get(pk=outcome_goal.pk).title == titel
        assert _spuren() == vorher


@pytest.mark.django_db
class TestFallImPapierkorb:
    """Eine geloeschte Akte: entweder schon die Suche (404) oder der Vertrag (409) — nie 500."""

    def _loesche(self, case_open, staff_user):
        soft_delete_case(Case.objects.get(pk=case_open.pk), staff_user)

    def test_zuordnung(self, client, staff_user, case_open, sample_event):
        self._loesche(case_open, staff_user)
        client.force_login(staff_user)
        vorher = _spuren()
        response = client.post(
            reverse("core:case_assign_event", kwargs={"pk": case_open.pk}), {"event_id": sample_event.pk}, **HTMX
        )
        assert response.status_code in (404, 409)
        assert Event.objects.get(pk=sample_event.pk).case_id is None
        assert _spuren() == vorher

    def test_zielanlage(self, client, staff_user, case_open):
        self._loesche(case_open, staff_user)
        client.force_login(staff_user)
        vorher = _spuren()
        response = client.post(reverse("core:goal_create", kwargs={"case_pk": case_open.pk}), {"title": "Neu"}, **HTMX)
        assert response.status_code in (404, 409)
        assert _spuren() == vorher

    def test_zielbearbeitung(self, client, staff_user, case_open, outcome_goal):
        titel = outcome_goal.title
        self._loesche(case_open, staff_user)
        client.force_login(staff_user)
        vorher = _spuren()
        response = client.post(
            reverse("core:goal_update", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk}),
            {"title": "Geaendert", "description": "Freitext"},
            **HTMX,
        )
        assert response.status_code in (404, 409)
        assert OutcomeGoal.objects.get(pk=outcome_goal.pk).title == titel
        assert _spuren() == vorher
