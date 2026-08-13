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
3. **Mobile Pixelform (Refs #1859):** jede primäre Aktion der MOBILEN Leiste
   ist fingergroß (44 × 44 px), ihr sichtbarer Kurztext steht im zugänglichen
   Namen (WCAG 2.5.3), und die beiden Anlege-Aktionen tragen unterscheidbare
   Icons.

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
    ⋮-Dropdown-Panel (``x-show="mobileMenu"`` markiert den Eintritt in den
    Overflow — ``mobileOverflowMenu`` wird für BEIDE Varianten verwendet, siehe
    ``components/_action_overflow.html``).

    Refs #1864: Der Erkenner hing bis dahin an einem BELIEBIGEN ``x-show``.
    Das trug, solange ``x-show`` ausschließlich am Dropdown-Panel vorkam —
    seit „Offline mitnehmen“ als Einzelaktion INLINE in der Leiste stehen kann
    (mit zwei zustandsabhängigen Labels), gibt es ``x-show`` auch außerhalb
    des Menüs, und der Erkenner hätte alles danach fälschlich in den Overflow
    gerechnet. Er zielt jetzt auf den konkreten Panel-Ausdruck.
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
            if attribute.get("x-show") == "mobileMenu" and self._overflow_ab is None:
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


class _MobileButtonForm(HTMLParser):
    """Pixelform der PRIMÄREN Aktionen einer mobilen Leiste (Refs #1859).

    Sammelt je ``<a>``/``<button>`` außerhalb des ⋮-Panels: ``class``,
    ``aria-label``, ``title``, ``data-testid``, den sichtbaren Text (ohne
    ``sr-only``-Spans, die kein Auge sieht) und die SVG-``d``-Pfade.

    Der Overflow-Eintritt wird am Panel-Attribut ``x-show="mobileMenu"``
    erkannt — bewusst NICHT an einem beliebigen ``x-show`` wie in
    :class:`_ActionSequence`: ein Alpine-Zustandswechsel INNERHALB einer
    primären Aktion (zwei Label-Spans, die einander ablösen) würde den
    Erkenner sonst zu früh ins Menü schicken und die Aktion aus der
    44-px-Prüfung fallen lassen.

    Der Name nennt den Anlass, nicht die Grenze: der Parser ist
    container-agnostisch und liest genauso die ``*-actions-desktop``-Leiste —
    Refs #1865 vergleicht damit die Beschriftungen beider Varianten.
    """

    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
    _INTERAKTIV = {"a", "button"}

    def __init__(self, container_testid: str):
        super().__init__(convert_charrefs=True)
        self._gesucht = container_testid
        self._stack: list[str] = []
        self._eintritt: int | None = None
        self._overflow_ab: int | None = None
        self._button_ab: int | None = None
        self._sr_only_ab: int | None = None
        self._aktuell: dict | None = None
        self.gefunden = False
        self.buttons: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attribute = dict(attrs)
        if self._eintritt is None and attribute.get("data-testid") == self._gesucht:
            self._eintritt = len(self._stack)
            self.gefunden = True
        if self._eintritt is not None:
            if attribute.get("x-show") == "mobileMenu" and self._overflow_ab is None:
                self._overflow_ab = len(self._stack)
            if self._overflow_ab is None:
                if tag in self._INTERAKTIV and self._aktuell is None:
                    self._aktuell = {
                        "testid": attribute.get("data-testid"),
                        "klassen": attribute.get("class", "").split(),
                        "aria_label": attribute.get("aria-label"),
                        "title": attribute.get("title"),
                        "text": "",
                        "icons": [],
                    }
                    self._button_ab = len(self._stack)
                if self._aktuell is not None:
                    if tag == "path" and attribute.get("d"):
                        self._aktuell["icons"].append(attribute["d"])
                    if self._sr_only_ab is None and "sr-only" in attribute.get("class", "").split():
                        self._sr_only_ab = len(self._stack)
        if tag not in self._VOID:
            self._stack.append(tag)

    def handle_data(self, data):
        if self._aktuell is not None and self._sr_only_ab is None:
            self._aktuell["text"] += data

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        while self._stack and self._stack.pop() != tag:
            pass
        tiefe = len(self._stack)
        if self._sr_only_ab is not None and tiefe <= self._sr_only_ab:
            self._sr_only_ab = None
        if self._button_ab is not None and tiefe <= self._button_ab:
            self._aktuell["text"] = " ".join(self._aktuell["text"].split())
            self.buttons.append(self._aktuell)
            self._aktuell = None
            self._button_ab = None
        if self._overflow_ab is not None and tiefe <= self._overflow_ab:
            self._overflow_ab = None
        if self._eintritt is not None and tiefe <= self._eintritt:
            self._eintritt = None


def _mobile_buttons(html: str, container_testid: str) -> list[dict]:
    parser = _MobileButtonForm(container_testid)
    parser.feed(html)
    assert parser.gefunden, f'Container data-testid="{container_testid}" nicht im HTML'
    return parser.buttons


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
        to_client_url = reverse("core:client_detail", kwargs={"pk": sample_workitem.client.pk})
        # Drei Status-Buttons (übernehmen/erledigt/verwerfen) posten alle auf
        # dieselbe Route, danach Bearbeiten — und seit Refs #1864 „Zur Person“
        # als letzter Eintrag: die Aufgabenseite hat kein ⋮ mehr, weil dort nur
        # dieser eine, harmlose Eintrag lag.
        assert primary == [status_url, status_url, status_url, edit_url, to_client_url]

    def test_to_client_is_primary_because_it_is_the_only_secondary_action(self, client, admin_user, sample_workitem):
        """ANFORDERUNGSWECHSEL (Refs #1864): „Zur Person“ lag hinter dem ⋮ und
        war dort — sobald ``workitem.client`` gesetzt ist, also praktisch
        immer — der EINZIGE Eintrag. Zwei Klicks fuer eine Aktion, die genauso
        gut direkt in der Leiste stehen kann; der Knopf versprach „weitere
        Aktionen" und hielt genau eine bereit. Die Vorgaengerfassung dieses
        Tests (``test_to_client_is_overflow_only``) hielt die alte Regel fest.
        Folgenschwere Aktionen bleiben unveraendert immer im ⋮ — das prueft
        :class:`TestOverflowOnlyForTwoOrMoreEntries`."""
        client.force_login(admin_user)
        html = client.get(reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk})).content.decode()
        actions = _actions(html, "workitem-actions-desktop")
        to_client_url = reverse("core:client_detail", kwargs={"pk": sample_workitem.client.pk})
        assert to_client_url in _targets(_primary(actions))
        assert to_client_url not in _overflow_targets(actions)

    def test_status_buttons_are_short_on_mobile_and_full_on_desktop(self, client, admin_user, sample_workitem):
        """Refs #1865: Die drei Status-Knöpfe im Status „Offen“ sind die
        einzigen Kopfaktionen der Anwendung, die mobil den vollen Desktop-Satz
        rendern („Als nicht relevant schließen“) — sie sind rohe POST-Forms und
        laufen nicht durch ``_action_button.html``. Bei 390 px ergab das eine
        Wunschbreite von ca. 605 px auf 358 px verfügbarer Fläche.

        Die Paritätsregel §6.4 bleibt: dieselben Knöpfe in derselben
        Reihenfolge, nur kürzer beschriftet. Dass der Kurztext im
        ``aria-label`` steckt, prüft
        :class:`TestMobileActionPixelForm` (DE und EN)."""
        assert sample_workitem.status == "open"
        client.force_login(admin_user)
        html = client.get(reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk})).content.decode()

        mobil = [b["text"] for b in _mobile_buttons(html, "workitem-actions-mobile")]
        assert "Übernehmen" in mobil
        assert "Erledigt" in mobil
        assert "Nicht relevant" in mobil
        assert "Als nicht relevant schließen" not in mobil, "Langtext steht noch in der mobilen Leiste"

        desktop = [b["text"] for b in _mobile_buttons(html, "workitem-actions-desktop")]
        assert "Aufgabe übernehmen" in desktop
        assert "Als erledigt markieren" in desktop
        assert "Als nicht relevant schließen" in desktop


@pytest.mark.django_db
class TestMobileActionPixelForm:
    """Mobile Pixelform der Kopfleisten (Refs #1859).

    Der Handtest bei 390 px fand drei Befunde, die alle in den GETEILTEN
    Komponenten sitzen und deshalb hier über alle vier Detailseiten gemeinsam
    festgenagelt werden:

    * **B1** — die beiden Anlege-Aktionen waren mobil icon-only und trugen
      dasselbe „+“-Icon; welche welche ist, war nicht erkennbar.
    * **B2** — der ⋮-Knopf war ~34 px hoch statt 44 px wie seine Nachbarn.
    * **WCAG 2.5.3** — sobald mobil ein Kurztext sichtbar wird, MUSS er im
      zugänglichen Namen stecken, sonst kann Sprachsteuerung den Knopf nicht
      ansprechen. Die Regel gilt in jeder Sprache, deshalb die EN-Gegenprobe.
    """

    def _seiten(self, client_identified, case_open, sample_event, sample_workitem):
        return (
            (reverse("core:client_detail", kwargs={"pk": client_identified.pk}), "client-actions-mobile"),
            (reverse("core:case_detail", kwargs={"pk": case_open.pk}), "case-actions-mobile"),
            (reverse("core:event_detail", kwargs={"pk": sample_event.pk}), "event-actions-mobile"),
            (reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk}), "workitem-actions-mobile"),
        )

    def test_every_mobile_primary_action_is_a_44px_touch_target(
        self, client, admin_user, client_identified, case_open, sample_event, sample_workitem
    ):
        client.force_login(admin_user)
        for url, container in self._seiten(client_identified, case_open, sample_event, sample_workitem):
            html = client.get(url).content.decode()
            buttons = _mobile_buttons(html, container)
            assert buttons, f"Keine mobile Primäraktion in {container} gefunden ({url})"
            for button in buttons:
                kennung = button["testid"] or button["text"] or button["aria_label"]
                assert "min-h-[44px]" in button["klassen"], f"{container}/{kennung}: min-h-[44px] fehlt ({url})"
                assert "min-w-[44px]" in button["klassen"], f"{container}/{kennung}: min-w-[44px] fehlt ({url})"

    def test_visible_mobile_text_is_part_of_the_accessible_name(
        self, client, admin_user, client_identified, case_open, sample_event, sample_workitem
    ):
        client.force_login(admin_user)
        geprueft = 0
        for url, container in self._seiten(client_identified, case_open, sample_event, sample_workitem):
            html = client.get(url).content.decode()
            for button in _mobile_buttons(html, container):
                if not (button["aria_label"] and button["text"]):
                    continue
                geprueft += 1
                assert button["text"].lower() in button["aria_label"].lower(), (
                    f"WCAG 2.5.3 verletzt in {container}: sichtbar {button['text']!r}, "
                    f"zugänglicher Name {button['aria_label']!r} ({url})"
                )
        assert geprueft, "Gegenprobe: kein einziger mobiler Kurztext gefunden — prüft der Test noch etwas?"

    def test_visible_mobile_text_is_part_of_the_accessible_name_in_english(
        self, client, admin_user, client_identified, case_open, sample_event, sample_workitem
    ):
        """Die Kurzlabels sind eigene msgids — „Kontakt“ ⊂ „Kontakt dokumentieren“
        sagt nichts darüber, ob „Contact“ ⊂ „Document contact“ gilt."""
        admin_user.preferred_language = "en"
        admin_user.save(update_fields=["preferred_language"])
        client.force_login(admin_user)
        geprueft = 0
        for url, container in self._seiten(client_identified, case_open, sample_event, sample_workitem):
            html = client.get(url).content.decode()
            for button in _mobile_buttons(html, container):
                if not (button["aria_label"] and button["text"]):
                    continue
                geprueft += 1
                assert button["text"].lower() in button["aria_label"].lower(), (
                    f"WCAG 2.5.3 verletzt in {container} (EN): sichtbar {button['text']!r}, "
                    f"zugänglicher Name {button['aria_label']!r} ({url})"
                )
        assert geprueft, "Gegenprobe: kein einziger mobiler Kurztext gefunden (EN)"

    def test_contact_and_task_actions_have_distinguishable_icons(
        self, client, admin_user, client_identified, case_open
    ):
        """B1: beide trugen ``M12 4v16m8-8H4`` — ein „+“ neben einem „+“."""
        client.force_login(admin_user)
        for url, container, kontakt, aufgabe in (
            (
                reverse("core:client_detail", kwargs={"pk": client_identified.pk}),
                "client-actions-mobile",
                "client-action-contact",
                "client-action-task",
            ),
            (
                reverse("core:case_detail", kwargs={"pk": case_open.pk}),
                "case-actions-mobile",
                "case-action-contact",
                "case-action-task",
            ),
        ):
            html = client.get(url).content.decode()
            icons = {b["testid"]: b["icons"] for b in _mobile_buttons(html, container)}
            assert icons.get(kontakt), f"{kontakt} hat kein Icon ({url})"
            assert icons.get(aufgabe), f"{aufgabe} hat kein Icon ({url})"
            assert icons[kontakt] != icons[aufgabe], (
                f"{kontakt} und {aufgabe} tragen dasselbe Icon {icons[kontakt]!r} — "
                f"mobil sind die beiden Anlege-Aktionen damit ununterscheidbar ({url})"
            )


@pytest.mark.django_db
class TestOverflowOnlyForTwoOrMoreEntries:
    """Das ⋮ erscheint nur, wenn es wirklich eine Auswahl bündelt (Refs #1864).

    Die Gates in den ``_actions.html`` waren reine „ist die Menge nicht
    leer"-Bedingungen. In Alltagskonstellationen lag hinter dem ⋮ deshalb
    **genau ein** Eintrag: auf der Aufgabe immer, auf der Personenakte für die
    Assistenz, auf dem Ereignis ohne Fallbezug bzw. ohne Löschrecht. Ein Knopf,
    der „weitere Aktionen“ verspricht und eine bereithält, versteckt, ohne zu
    entlasten.

    Ausnahme bleibt **folgenschwer**: Löschen, Fall schließen/wiedereröffnen
    und Löschung beantragen kosten weiterhin den bewussten Zwischenschritt —
    auch als Einzeleintrag (§6.2).
    """

    @staticmethod
    def _hat_overflow(html: str) -> bool:
        return "overflow-menu" in html

    def test_workitem_with_client_drops_the_overflow_entirely(self, client, admin_user, sample_workitem):
        client.force_login(admin_user)
        html = client.get(reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk})).content.decode()
        assert not self._hat_overflow(html), "⋮ bleibt stehen, obwohl nur „Zur Person“ dahinter liegt"
        bar = _mobile_buttons(html, "workitem-actions-mobile")
        assert "workitem-action-to-client" in [b["testid"] for b in bar]

    def test_client_detail_shows_the_single_offline_action_inline_for_assistants(
        self, client, assistant_user, client_identified
    ):
        """Assistenz sieht weder „Fall anlegen“ noch „Löschung beantragen“ noch
        die Datenauskunft — übrig bleibt „Offline mitnehmen“, eine harmlose
        Aktion."""
        client.force_login(assistant_user)
        html = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk})).content.decode()
        assert not self._hat_overflow(html), "⋮ bleibt stehen, obwohl nur „Offline mitnehmen“ dahinter liegt"
        testids = [b["testid"] for b in _mobile_buttons(html, "client-actions-mobile")]
        assert "mobile-take-offline-btn" in testids

    def test_client_detail_keeps_the_overflow_for_staff(self, client, staff_user, client_identified):
        """Gegenprobe: ab Fachkraft sind es drei Einträge (Fall anlegen ·
        Offline mitnehmen · Löschung beantragen) — das ⋮ bleibt."""
        client.force_login(staff_user)
        html = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk})).content.decode()
        assert self._hat_overflow(html)
        testids = [b["testid"] for b in _mobile_buttons(html, "client-actions-mobile")]
        assert "mobile-take-offline-btn" not in testids, "Offline mitnehmen gehört bei ≥ 2 Einträgen ins Menü"

    def test_event_keeps_the_overflow_when_a_delete_right_joins_the_link(self, client, admin_user, sample_event):
        """Zwei Einträge (Zur Person + Löschen) — das ⋮ bleibt."""
        client.force_login(admin_user)
        html = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk})).content.decode()
        assert self._hat_overflow(html)
        actions = _actions(html, "event-actions-desktop")
        assert reverse("core:client_detail", kwargs={"pk": sample_event.client.pk}) in _overflow_targets(actions)

    def test_event_shows_the_single_link_inline_without_a_delete_right(self, client, assistant_user, sample_event):
        """Assistenz: „Zum Fall“ ist staff-gegated, „Löschen“ auch — übrig
        bleibt „Zur Person“ als einziger, harmloser Eintrag."""
        client.force_login(assistant_user)
        html = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk})).content.decode()
        assert not self._hat_overflow(html), "⋮ bleibt stehen, obwohl nur „Zur Person“ dahinter liegt"
        actions = _actions(html, "event-actions-desktop")
        assert reverse("core:client_detail", kwargs={"pk": sample_event.client.pk}) in _targets(_primary(actions))

    def test_event_shows_the_single_case_link_inline(
        self, client, staff_user, admin_user, facility, case_open, doc_type_contact
    ):
        """Spiegelbild: Ereignis mit Fall- statt Personenbezug. ``created_by``
        ist bewusst NICHT die prüfende Fachkraft — sonst griffe deren
        Löschrecht am eigenen Ereignis und das ⋮ bliebe zu Recht stehen."""
        from django.utils import timezone

        from core.models import Event

        event = Event.objects.create(
            facility=facility,
            case=case_open,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )
        client.force_login(staff_user)
        html = client.get(reverse("core:event_detail", kwargs={"pk": event.pk})).content.decode()
        assert not self._hat_overflow(html), "⋮ bleibt stehen, obwohl nur „Zum Fall“ dahinter liegt"
        actions = _actions(html, "event-actions-desktop")
        assert reverse("core:case_detail", kwargs={"pk": case_open.pk}) in _targets(_primary(actions))

    def test_case_keeps_the_overflow_for_its_single_consequential_entry(self, client, admin_user, case_open):
        """„Schließen“ steht allein im Menü und bleibt trotzdem dort: ein
        versehentlicher Klick auf eine folgenschwere Aktion soll weiterhin
        einen bewussten Zwischenschritt kosten. „Wiedereröffnen“ ebenso, sonst
        spränge die Aktion je nach Fallstatus zwischen Leiste und Menü."""
        client.force_login(admin_user)
        html = client.get(reverse("core:case_detail", kwargs={"pk": case_open.pk})).content.decode()
        assert self._hat_overflow(html)
        actions = _actions(html, "case-actions-desktop")
        assert reverse("core:case_close", kwargs={"pk": case_open.pk}) in _overflow_targets(actions)

    def test_consequential_actions_are_never_primary_in_any_role(
        self, client, assistant_user, staff_user, lead_user, admin_user, client_identified, case_open, sample_event
    ):
        """Verschärfte Rangregel: die vier folgenschweren Ziele stehen auf
        KEINER Seite und in KEINER Rolle außerhalb des ⋮ — auch nicht, wenn
        sie dort allein liegen."""
        verboten = {
            reverse("core:case_close", kwargs={"pk": case_open.pk}),
            reverse("core:case_reopen", kwargs={"pk": case_open.pk}),
            reverse("core:event_delete", kwargs={"pk": sample_event.pk}),
            reverse("core:client_delete_request", kwargs={"pk": client_identified.pk}),
        }
        seiten = (
            (reverse("core:client_detail", kwargs={"pk": client_identified.pk}), "client-actions-desktop"),
            (reverse("core:case_detail", kwargs={"pk": case_open.pk}), "case-actions-desktop"),
            (reverse("core:event_detail", kwargs={"pk": sample_event.pk}), "event-actions-desktop"),
        )
        geprueft = 0
        for user in (assistant_user, staff_user, lead_user, admin_user):
            client.force_login(user)
            for url, container in seiten:
                response = client.get(url)
                # Nicht jede Rolle sieht jede Seite (Fallakte ist STAFF_PLUS).
                if response.status_code != 200:
                    continue
                geprueft += 1
                primary = set(_targets(_primary(_actions(response.content.decode(), container))))
                treffer = primary & verboten
                assert not treffer, f"{user.username} sieht folgenschwere Aktion(en) primär auf {url}: {treffer}"
        assert geprueft >= 8, f"Zu wenige Seiten geprüft ({geprueft}) — Gegenprobe verpufft"
