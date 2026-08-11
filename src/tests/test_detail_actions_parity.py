"""Aktions-Parität und Rangregel je Entität (Konzept #1801 §6.2/§6.4, Refs #1813 T4).

Zwei Eigenschaften, für jede der vier Detailseiten (Person/Fall/Ereignis/
Aufgabe):

1. **Paritätsregel (§6.4):** Desktop- und Mobil-Aktionsleiste zeigen dieselbe
   Aktionsmenge in derselben Reihenfolge. Geprüft wird strukturell — nicht
   über zwei getrennt gepflegte Erwartungslisten (das wäre genau die Drift,
   die §6.4 verhindern soll), sondern indem beide Varianten aus derselben
   gerenderten Seite extrahiert und ihre Ziel-``href``/``action``-Werte in
   Dokumentreihenfolge verglichen werden.
2. **Rangregel (§6.2):** primäre Aktionen (außerhalb des ⋮-Overflows)
   entsprechen der §6.3-Tabelle; folgenschwere Aktionen (Löschen, Fall
   abschließen, Löschung beantragen) stehen NIE primär.

Rollenwahl: ``admin_user`` (facility_admin) ist ``is_staff_or_above`` UND
``is_lead_or_admin`` zugleich — die einzige Rolle, die auf allen vier Seiten
die VOLLSTÄNDIGE Aktionsmenge sieht (sonst wären Teile der Overflow-Menge
für die Prüfung unsichtbar).
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.urls import reverse


class _ActionSequence(HTMLParser):
    """Ordnet ``(target, testid, in_overflow)`` je ``<a>``/``<form>`` innerhalb
    eines durch ``data-testid`` markierten Containers (Refs #1813 T4).

    Muster analog ``tests.test_button_permissions._ContainerInhalt``: eine
    Tiefen-Stack-Verfolgung isoliert erst den Container, danach zusätzlich das
    ⋮-Dropdown-Panel (jedes Element mit einem ``x-show``-Attribut markiert den
    Eintritt in den Overflow — ``mobileOverflowMenu`` wird für BEIDE Varianten
    verwendet, siehe ``components/_action_overflow.html``).
    """

    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, container_testid: str):
        super().__init__(convert_charrefs=True)
        self._gesucht = container_testid
        self._stack: list[str] = []
        self._eintritt: int | None = None
        self._overflow_ab: int | None = None
        self.gefunden = False
        self.actions: list[tuple[str, str | None, bool]] = []

    def handle_starttag(self, tag, attrs):
        attribute = dict(attrs)
        if self._eintritt is None and attribute.get("data-testid") == self._gesucht:
            self._eintritt = len(self._stack)
            self.gefunden = True
        if self._eintritt is not None:
            if "x-show" in attribute and self._overflow_ab is None:
                self._overflow_ab = len(self._stack)
            target = attribute.get("href") if tag == "a" else (attribute.get("action") if tag == "form" else None)
            if target:
                self.actions.append((target, attribute.get("data-testid"), self._overflow_ab is not None))
        if tag not in self._VOID:
            self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        while self._stack and self._stack.pop() != tag:
            pass
        if self._overflow_ab is not None and len(self._stack) <= self._overflow_ab:
            self._overflow_ab = None
        if self._eintritt is not None and len(self._stack) <= self._eintritt:
            self._eintritt = None


def _actions(html: str, container_testid: str) -> list[tuple[str, str | None, bool]]:
    parser = _ActionSequence(container_testid)
    parser.feed(html)
    assert parser.gefunden, f'Container data-testid="{container_testid}" nicht im HTML'
    return parser.actions


def _targets(actions: list[tuple[str, str | None, bool]]) -> list[str]:
    return [a[0] for a in actions]


def _primary(actions: list[tuple[str, str | None, bool]]) -> list[tuple[str, str | None, bool]]:
    return [a for a in actions if not a[2]]


def _overflow_targets(actions: list[tuple[str, str | None, bool]]) -> list[str]:
    return [a[0] for a in actions if a[2]]


@pytest.mark.django_db
class TestClientDetailActionParityAndRank:
    def test_desktop_and_mobile_show_the_same_actions_in_the_same_order(self, client, admin_user, client_identified):
        client.force_login(admin_user)
        html = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk})).content.decode()
        desktop = _targets(_actions(html, "client-actions-desktop"))
        mobile = _targets(_actions(html, "client-actions-mobile"))
        assert desktop == mobile
        assert desktop  # Gegenprobe: der Guard findet auch wirklich etwas.

    def test_primary_actions_match_e3_table(self, client, admin_user, client_identified):
        client.force_login(admin_user)
        html = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk})).content.decode()
        primary = _targets(_primary(_actions(html, "client-actions-desktop")))
        assert primary == [
            f"{reverse('core:event_create')}?client={client_identified.pk}",
            f"{reverse('core:workitem_create')}?client={client_identified.pk}",
            reverse("core:client_update", kwargs={"pk": client_identified.pk}),
        ]
        assert len(primary) <= 3

    def test_case_create_and_delete_request_are_never_primary(self, client, admin_user, client_identified):
        client.force_login(admin_user)
        html = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk})).content.decode()
        actions = _actions(html, "client-actions-desktop")
        overflow = _overflow_targets(actions)
        primary = _targets(_primary(actions))
        case_create_url = f"{reverse('core:case_create')}?client={client_identified.pk}"
        delete_request_url = reverse("core:client_delete_request", kwargs={"pk": client_identified.pk})
        assert case_create_url in overflow
        assert delete_request_url in overflow
        assert case_create_url not in primary
        assert delete_request_url not in primary


@pytest.mark.django_db
class TestCaseDetailActionParityAndRank:
    def test_desktop_and_mobile_show_the_same_actions_in_the_same_order(self, client, admin_user, case_open):
        client.force_login(admin_user)
        html = client.get(reverse("core:case_detail", kwargs={"pk": case_open.pk})).content.decode()
        desktop = _targets(_actions(html, "case-actions-desktop"))
        mobile = _targets(_actions(html, "case-actions-mobile"))
        assert desktop == mobile
        assert desktop

    def test_primary_actions_match_e3_table(self, client, admin_user, case_open):
        client.force_login(admin_user)
        html = client.get(reverse("core:case_detail", kwargs={"pk": case_open.pk})).content.decode()
        primary = _targets(_primary(_actions(html, "case-actions-desktop")))
        assert primary == [
            f"{reverse('core:event_create')}?case={case_open.pk}",
            f"{reverse('core:workitem_create')}?client={case_open.client.pk}",
            reverse("core:case_update", kwargs={"pk": case_open.pk}),
        ]
        assert len(primary) <= 3

    def test_close_is_never_primary(self, client, admin_user, case_open):
        client.force_login(admin_user)
        html = client.get(reverse("core:case_detail", kwargs={"pk": case_open.pk})).content.decode()
        actions = _actions(html, "case-actions-desktop")
        close_url = reverse("core:case_close", kwargs={"pk": case_open.pk})
        assert close_url in _overflow_targets(actions)
        assert close_url not in _targets(_primary(actions))

    def test_reopen_is_never_primary(self, client, admin_user, case_closed):
        client.force_login(admin_user)
        html = client.get(reverse("core:case_detail", kwargs={"pk": case_closed.pk})).content.decode()
        actions = _actions(html, "case-actions-desktop")
        reopen_url = reverse("core:case_reopen", kwargs={"pk": case_closed.pk})
        assert reopen_url in _overflow_targets(actions)
        assert reopen_url not in _targets(_primary(actions))


@pytest.mark.django_db
class TestEventDetailActionParityAndRank:
    def test_desktop_and_mobile_show_the_same_actions_in_the_same_order(self, client, admin_user, sample_event):
        client.force_login(admin_user)
        html = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk})).content.decode()
        desktop = _targets(_actions(html, "event-actions-desktop"))
        mobile = _targets(_actions(html, "event-actions-mobile"))
        assert desktop == mobile
        assert desktop

    def test_primary_actions_match_e3_table(self, client, admin_user, sample_event):
        client.force_login(admin_user)
        html = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk})).content.decode()
        primary = _targets(_primary(_actions(html, "event-actions-desktop")))
        assert primary == [
            f"{reverse('core:workitem_create')}?client={sample_event.client.pk}",
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
        ]
        assert len(primary) <= 3

    def test_anonymous_event_keeps_the_task_action_without_a_client_param(
        self, client, admin_user, facility, staff_user, doc_type_contact
    ):
        from django.utils import timezone

        from core.models import Event

        anon_event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        client.force_login(admin_user)
        html = client.get(reverse("core:event_detail", kwargs={"pk": anon_event.pk})).content.decode()
        primary = _targets(_primary(_actions(html, "event-actions-desktop")))
        assert primary == [
            reverse("core:workitem_create"),
            reverse("core:event_update", kwargs={"pk": anon_event.pk}),
        ]

    def test_delete_is_never_primary(self, client, admin_user, sample_event):
        client.force_login(admin_user)
        html = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk})).content.decode()
        actions = _actions(html, "event-actions-desktop")
        delete_url = reverse("core:event_delete", kwargs={"pk": sample_event.pk})
        assert delete_url in _overflow_targets(actions)
        assert delete_url not in _targets(_primary(actions))

    def test_to_client_and_to_case_are_overflow_only(
        self, client, admin_user, facility, staff_user, doc_type_contact, client_identified, case_open
    ):
        from django.utils import timezone

        from core.models import Event

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            case=case_open,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(admin_user)
        html = client.get(reverse("core:event_detail", kwargs={"pk": event.pk})).content.decode()
        actions = _actions(html, "event-actions-desktop")
        to_client_url = reverse("core:client_detail", kwargs={"pk": client_identified.pk})
        to_case_url = reverse("core:case_detail", kwargs={"pk": case_open.pk})
        overflow = _overflow_targets(actions)
        primary = _targets(_primary(actions))
        assert to_client_url in overflow
        assert to_case_url in overflow
        assert to_client_url not in primary
        assert to_case_url not in primary


@pytest.mark.django_db
class TestWorkitemDetailActionParityAndRank:
    """Aufgabe: "Status umschalten" ist EIN benannter Eintrag, der je nach
    Status auf mehrere physische Buttons expandiert (bestehende Forms,
    unverändert aus dem Body übernommen) — die "max. 3 Buttons"-Faustregel
    gilt hier bewusst nicht 1:1 (§6.2 nennt "Status umschalten" selbst als
    einen Eintrag neben "Bearbeiten")."""

    def test_desktop_and_mobile_show_the_same_actions_in_the_same_order(self, client, admin_user, sample_workitem):
        client.force_login(admin_user)
        html = client.get(reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk})).content.decode()
        desktop = _targets(_actions(html, "workitem-actions-desktop"))
        mobile = _targets(_actions(html, "workitem-actions-mobile"))
        assert desktop == mobile
        assert desktop

    def test_status_forms_and_edit_are_primary(self, client, admin_user, sample_workitem):
        assert sample_workitem.status == "open"
        client.force_login(admin_user)
        html = client.get(reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk})).content.decode()
        primary = _targets(_primary(_actions(html, "workitem-actions-desktop")))
        status_url = reverse("core:workitem_status_update", kwargs={"pk": sample_workitem.pk})
        edit_url = reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk})
        # Drei Status-Buttons (übernehmen/erledigt/verwerfen) posten alle auf
        # dieselbe Route, danach Bearbeiten.
        assert primary == [status_url, status_url, status_url, edit_url]

    def test_to_client_is_overflow_only(self, client, admin_user, sample_workitem):
        client.force_login(admin_user)
        html = client.get(reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk})).content.decode()
        actions = _actions(html, "workitem-actions-desktop")
        to_client_url = reverse("core:client_detail", kwargs={"pk": sample_workitem.client.pk})
        assert to_client_url in _overflow_targets(actions)
        assert to_client_url not in _targets(_primary(actions))
