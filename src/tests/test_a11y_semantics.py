"""Gerenderte ARIA-Semantik der WCAG-2.1-AA-Welle (Refs #1059, #1989).

Die Befunde dieser Welle sind Zustands- und Rollenangaben, die es im
gerenderten HTML entweder gibt oder nicht: ``aria-expanded`` an den
Disclosure-Triggern, ``aria-controls`` auf ein Panel, das es wirklich gibt,
``aria-selected`` an den Zeitfilter-Tabs, ``aria-pressed`` am
Sprachumschalter und die Combobox-Rollen des Personen-Autocompletes.

Bewusst eine eigene Datei statt Anbau an die Feature-Tests: die Zusagen
gehoeren zusammen, nicht zu je einer Ansicht — und ein spaeteres
Screenreader-Audit (B3) findet hier den maschinell pruefbaren Teil.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from core.models import EventHistory


def _html(client, user, url: str) -> str:
    client.force_login(user)
    response = client.get(url)
    assert response.status_code == 200, f"{url} -> {response.status_code}"
    return response.content.decode()


def _id_count(html: str, element_id: str) -> int:
    """Zaehlt echte ``id=``-Attribute.

    Ein nacktes ``in``/``count`` auf ``id="x"`` zaehlt auch
    ``data-testid="x"`` mit — genau die Falle, die ein Doppel-id-Guard
    uebersehen wuerde.
    """
    return len(re.findall(rf'(?:^|\s)id="{re.escape(element_id)}"', html))


def _tag_with(html: str, needle: str) -> str:
    """Gibt das oeffnende Tag zurueck, das ``needle`` enthaelt."""
    index = html.index(needle)
    start = html.rindex("<", 0, index)
    end = html.index(">", index)
    return html[start : end + 1]


@pytest.mark.django_db
class TestOverflowMenuState:
    """A11Y-015 — der ``⋮``-Trigger meldete weder Zustand noch Ziel."""

    #: Detailseite -> Praefix der Panel-id aus der jeweiligen ``_actions.html``.
    def _urls(self, client_identified, case_open, sample_event):
        return [
            (reverse("core:client_detail", args=[client_identified.pk]), "client-overflow-"),
            (reverse("core:case_detail", args=[case_open.pk]), "case-overflow-"),
            (reverse("core:event_detail", args=[sample_event.pk]), "event-overflow-"),
        ]

    def test_trigger_announces_state_and_panel(self, client, lead_user, client_identified, case_open, sample_event):
        for url, prefix in self._urls(client_identified, case_open, sample_event):
            html = _html(client, lead_user, url)
            triggers = [_tag_with(html, f'data-testid="{variant}-overflow-menu"') for variant in ("desktop", "mobile")]
            assert triggers, url
            for variant, tag in zip(("desktop", "mobile"), triggers, strict=True):
                assert 'aria-haspopup="true"' in tag, (url, tag)
                assert 'aria-expanded="false"' in tag, (url, tag)
                assert ':aria-expanded="ariaExpanded"' in tag, (url, tag)
                assert f'aria-controls="{prefix}{variant}"' in tag, (url, tag)

    def test_every_aria_controls_target_exists_exactly_once(
        self, client, lead_user, client_identified, case_open, sample_event
    ):
        """Ein ``aria-controls`` auf eine fehlende — oder doppelte — id ist wertlos."""
        for url, prefix in self._urls(client_identified, case_open, sample_event):
            html = _html(client, lead_user, url)
            for variant in ("desktop", "mobile"):
                assert _id_count(html, f"{prefix}{variant}") == 1, (url, variant)

    def test_escape_is_bound_on_the_menu_scope(self, client, lead_user, client_identified, case_open, sample_event):
        for url, _prefix in self._urls(client_identified, case_open, sample_event):
            html = _html(client, lead_user, url)
            assert '@keydown.escape="closeWithFocus"' in html, url
            assert 'x-ref="trigger"' in html, url


@pytest.mark.django_db
class TestNavigationDisclosureState:
    """A11Y-028/029 — „Neu erstellen", mobiles „Mehr" und der Such-Overlay."""

    def test_sidebar_create_button(self, client, staff_user):
        html = _html(client, staff_user, reverse("core:zeitstrom"))
        tag = _tag_with(html, 'data-testid="sidebar-create-btn"')

        assert 'aria-haspopup="true"' in tag
        assert ':aria-expanded="ariaExpanded"' in tag
        assert 'aria-controls="sidebar-create-menu"' in tag
        assert _id_count(html, "sidebar-create-menu") == 1

    def test_mobile_create_button(self, client, staff_user):
        html = _html(client, staff_user, reverse("core:zeitstrom"))
        tag = _tag_with(html, 'data-testid="mobile-nav-create"')

        assert 'aria-haspopup="true"' in tag
        assert ':aria-expanded="ariaExpanded"' in tag
        assert 'aria-controls="mobile-create-menu"' in tag
        assert _id_count(html, "mobile-create-menu") == 1

    def test_mobile_more_button(self, client, staff_user):
        html = _html(client, staff_user, reverse("core:zeitstrom"))
        tag = _tag_with(html, 'data-testid="mobile-nav-more"')

        assert 'aria-haspopup="true"' in tag
        assert ':aria-expanded="ariaMoreExpanded"' in tag
        assert 'aria-controls="mobile-more-items"' in tag
        assert _id_count(html, "mobile-more-items") == 1

    def test_mobile_search_trigger(self, client, staff_user):
        html = _html(client, staff_user, reverse("core:zeitstrom"))
        tag = _tag_with(html, 'data-testid="mobile-nav-search"')

        assert ':aria-expanded="ariaSearchExpanded"' in tag
        assert 'aria-controls="mobile-search-overlay"' in tag
        assert _id_count(html, "mobile-search-overlay") == 1

    def test_escape_closes_both_menus(self, client, staff_user):
        html = _html(client, staff_user, reverse("core:zeitstrom"))

        assert '@keydown.escape="closeWithFocus"' in html
        assert '@keydown.escape="closeMoreWithFocus"' in html


@pytest.mark.django_db
class TestLanguageSwitchPressedState:
    """A11Y-044 — der aktive Sprachstand lag nur in Farbklassen."""

    @pytest.mark.parametrize(
        ("testid", "value"),
        [("mobile-nav-language-de", "de"), ("mobile-nav-language-en", "en")],
    )
    def test_mobile_language_buttons_carry_aria_pressed(self, client, staff_user, testid, value):
        html = _html(client, staff_user, reverse("core:zeitstrom"))
        tag = _tag_with(html, f'data-testid="{testid}"')

        expected = "true" if value == "de" else "false"
        assert f'aria-pressed="{expected}"' in tag, tag

    def test_sidebar_language_buttons_carry_aria_pressed(self, client, staff_user):
        html = _html(client, staff_user, reverse("core:zeitstrom"))
        buttons = re.findall(r'<button[^>]*name="language"[^>]*>', html)

        assert len(buttons) == 4, "je zwei Umschalter in Sidebar und Mobil-Menue"
        assert sum('aria-pressed="true"' in tag for tag in buttons) == 2
        assert all("aria-pressed=" in tag for tag in buttons)


@pytest.mark.django_db
class TestHistoryEntryDisclosure:
    """A11Y-040 — der Verlauf-Details-Toggle im Ereignis-Detail."""

    def test_toggle_announces_state_and_target(self, client, staff_user, sample_event):
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
            data_before={"notiz": "alt"},
            data_after={"notiz": "neu"},
        )
        html = _html(client, staff_user, reverse("core:event_detail", args=[sample_event.pk]))

        toggles = re.findall(r'<button[^>]*aria-controls="history-detail-\d+"[^>]*>', html)
        assert toggles, "kein Verlauf-Toggle gerendert"
        for tag in toggles:
            assert ':aria-expanded="ariaExpanded"' in tag, tag
            index = re.search(r'aria-controls="(history-detail-\d+)"', tag).group(1)
            assert _id_count(html, index) == 1, index
