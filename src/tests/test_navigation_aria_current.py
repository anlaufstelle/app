"""Programmatische Ortsanzeige der beiden Hauptnavigationen (Refs #2007).

Der aktive Eintrag war bisher **nur** eingefaerbt — Desktop-Sidebar
``bg-accent-light``, mobile Bottom-Bar ``text-accent``. Farbe ist fuer
Screenreader-Nutzerinnen keine Information: die Ortsanzeige, die sehende
Nutzerinnen mit einem Blick bekommen, fehlte in der Accessibility-Tree
vollstaendig.

Die Reiterzeile (``partials/_subnav.html``) macht es seit Refs #1813 richtig
vor — ``aria-current="page"`` am aktiven Reiter, geprueft in
``test_navigation_hubs.py`` und ``test_navigation_statistics_subnav.py``.
Dieser Guard zieht dieselbe Zusage fuer die beiden **Haupt**-Navigationen
nach und haelt zusaetzlich die Gegenprobe fest: ein inaktiver Eintrag darf
``aria-current`` NICHT tragen (sonst waere die Angabe wertlos — es gaebe
mehrere „aktuelle" Seiten).

Sonderfall „Mehr": der mobile Sammelknopf ist selbst keine Seite, sondern
oeffnet ein Dropdown. Er traegt deshalb ``aria-current="true"`` statt
``"page"`` — dasselbe Muster wie die Zeitraum-Pille
(``core/statistics/_zeitraum_pill.html``), die ebenfalls einen Zustand und
keinen Ort markiert.
"""

from __future__ import annotations

import pytest
from django.urls import reverse


def _opening_tag(content: str, testid: str) -> str:
    """Der oeffnende Tag (``<a …>``/``<button …>``) mit dem gegebenen data-testid."""
    idx = content.index(f'data-testid="{testid}"')
    start = content.rindex("<", 0, idx)
    return content[start : content.index(">", idx)]


@pytest.mark.django_db
class TestSidebarAriaCurrent:
    """Desktop-Sidebar (``partials/_nav_sidebar_items.html``)."""

    def test_active_sidebar_entry_is_marked_as_current_page(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:datenschutz_uebersicht")).content.decode()

        assert 'aria-current="page"' in _opening_tag(content, "nav-datenschutz")

    def test_inactive_sidebar_entry_carries_no_aria_current(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:datenschutz_uebersicht")).content.decode()

        assert "aria-current" not in _opening_tag(content, "nav-verwaltung")


@pytest.mark.django_db
class TestMobileBottomBarAriaCurrent:
    """Mobile Bottom-Bar (``partials/_navigation_mobile.html``)."""

    def test_active_slot_is_marked_as_current_page(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_list")).content.decode()

        assert 'aria-current="page"' in _opening_tag(content, "mobile-nav-clients")

    def test_inactive_slot_carries_no_aria_current(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_list")).content.decode()

        assert "aria-current" not in _opening_tag(content, "mobile-nav-zeitstrom")

    def test_zeitstrom_slot_is_marked_on_its_own_page(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()

        assert 'aria-current="page"' in _opening_tag(content, "mobile-nav-zeitstrom")
        assert "aria-current" not in _opening_tag(content, "mobile-nav-clients")

    def test_workitems_slot_is_marked_on_its_own_page(self, client, staff_user):
        """Der vierte Link-Zweig — zuvor nur negativ mitgeprueft.

        Die vier Slots sind ausgeschriebenes Markup (eigene Pixelform,
        22px-Icon, Label, Overdue-Badge), kein Schleifen-Partial: jeder Zweig
        traegt sein ``aria-current`` einzeln und kann einzeln verloren gehen.
        Der Workitems-Slot ist zusaetzlich der einzige mit eingebettetem Badge
        — der darf das Attribut am ``<a>`` nicht verdraengen.
        """
        client.force_login(staff_user)
        content = client.get(reverse("core:workitem_inbox")).content.decode()

        assert 'aria-current="page"' in _opening_tag(content, "mobile-nav-workitems")
        assert "aria-current" not in _opening_tag(content, "mobile-nav-clients")

    def test_super_admin_system_slot_is_marked(self, client, super_admin_user):
        client.force_login(super_admin_user)
        content = client.get(reverse("core:system_dashboard")).content.decode()

        assert 'aria-current="page"' in _opening_tag(content, "mobile-nav-system")


@pytest.mark.django_db
class TestMobileMoreButtonAriaCurrent:
    """Der „Mehr"-Knopf markiert einen Zustand, keinen Ort — deshalb ``true``.

    Refs #1803/#2007: Auf ``/search/`` ist der Knopf die einzige Markierung der
    unteren Leiste (die Suche hat keinen eigenen Slot mehr). Genau dort darf die
    Information nicht rein visuell bleiben.
    """

    def test_more_button_is_marked_when_a_more_page_is_open(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:search")).content.decode()

        assert 'aria-current="true"' in _opening_tag(content, "mobile-nav-more")

    def test_more_button_carries_no_aria_current_elsewhere(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_list")).content.decode()

        assert "aria-current" not in _opening_tag(content, "mobile-nav-more")
