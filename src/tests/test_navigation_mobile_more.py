"""Das mobile „Mehr"-Menü als vollständige Zweitnavigation (Refs #1813, Konzept #1801 §5.2).

Mobil gibt es keine Sidebar: alles, was nicht einer der fünf Slots der
Bottom-Bar ist, muss im „Mehr"-Menü stehen — sonst ist es auf dem Telefon
schlicht nicht erreichbar. Genau zwei Dinge waren es bisher nicht: die
**Arbeitszentrale** (Desktop-Eintrag ohne mobiles Gegenstück) und der
**Sprachumschalter** (nur im Desktop-Footer, mobil also gar keine Möglichkeit,
die Sprache zu wechseln).

Dieser Guard prüft die **ganze Liste je Rolle in Dokumentreihenfolge**, nicht
einzelne Anwesenheiten. Das ist Absicht: die Mehr-Liste ist der Ort, an dem
Einträge historisch still verschwunden sind (der Katalog wurde vergessen, ein
Gate driftete gegenüber der Sidebar). Eine Positivliste je Rolle fällt bei
jedem Zuwachs *und* bei jedem Verlust — eine Sammlung von ``assert x in html``
nur bei Verlusten.

Die Zeilen der festen Fußzeile (Profil · Sprache · Abmelden) sind Teil der
erwarteten Liste, weil ihre **Reihenfolge** eine Aussage ist: der
Sprachumschalter steht über „Abmelden" (Konzept §5.3) und damit nicht hinter
der Aktion, die die Sitzung beendet.
"""

from __future__ import annotations

import re
import uuid

import pytest
from django.urls import reverse

from core.models.workitem import DeletionRequest

# Die Rahmen-Zeilen des Menüs, die keine Registry-Einträge sind (Konzept §5.2:
# „… ┊ Profil · DE/EN · Abmelden"). Sie stehen hier als sprechende Kürzel, damit
# die Erwartungstabelle unten wie die Konzept-Tabelle liest.
SUCHE = "suche"  # Overlay-Button (kein href), deshalb kein Registry-Eintrag
SPRACHE = "sprache"  # set_language-POST-Form
ABMELDEN = "abmelden"  # logout-POST-Form
PROFIL = "/account/"
HILFE = "/hilfe/"


def _more_menu(content: str) -> str:
    """Das Mehr-Dropdown als balancierter ``<div>``-Schnitt (Marker ``mobile-more-items``).

    Ein Schnitt bis zum ersten ``</div>`` würde am ersten Trennstrich enden;
    ein Schnitt bis zum Dokumentende zöge das Such-Overlay mit hinein, dessen
    Links sonst als Menü-Zeilen gezählt würden.
    """
    marker = 'data-testid="mobile-more-items"'
    start = content.rindex("<div", 0, content.index(marker))
    depth = 0
    pos = start
    while True:
        open_idx = content.find("<div", pos)
        close_idx = content.find("</div>", pos)
        assert close_idx != -1, "Mehr-Dropdown ist nicht geschlossen — Markup kaputt?"
        if open_idx != -1 and open_idx < close_idx:
            depth += 1
            pos = open_idx + len("<div")
            continue
        depth -= 1
        pos = close_idx + len("</div>")
        if depth == 0:
            return content[start:pos]


def _more_entries(content: str) -> list[str]:
    """Die Zeilen des Mehr-Menüs in Dokumentreihenfolge.

    Links werden über ihr ``href`` benannt (das ist die Aussage, die zählt —
    ein Label lässt sich übersetzen, ein Ziel nicht), die drei Nicht-Link-Zeilen
    über die Kürzel oben.
    """
    menu = _more_menu(content)
    entries: list[str] = []
    for match in re.finditer(r"<(a|button|form)\b[^>]*>", menu):
        tag = match.group(0)
        if 'data-testid="mobile-nav-search"' in tag:
            entries.append(SUCHE)
        elif match.group(1) == "a":
            href = re.search(r'href="([^"]+)"', tag)
            assert href, f"Menü-Link ohne href: {tag}"
            entries.append(href.group(1))
        elif match.group(1) == "form":
            action = re.search(r'action="([^"]+)"', tag)
            assert action, f"Menü-Formular ohne action: {tag}"
            entries.append(SPRACHE if action.group(1) == reverse("set_language") else ABMELDEN)
    return entries


def _render(client, user, url_name: str = "core:zeitstrom") -> str:
    client.force_login(user)
    return client.get(reverse(url_name)).content.decode()


@pytest.mark.django_db
class TestMoreMenuPerRole:
    """Konzept §5.2 — die Ziel-Liste, Zeile für Zeile, je Rolle."""

    def test_assistant(self, client, assistant_user):
        """Assistants see contacts before files in the complete mobile menu."""
        entries = _more_entries(_render(client, assistant_user))
        assert entries == [SUCHE, "/events/", "/attachments/", HILFE, PROFIL, SPRACHE, ABMELDEN]

    def test_staff(self, client, staff_user):
        """Staff see contacts before cases; privacy needs an own pending request."""
        entries = _more_entries(_render(client, staff_user))
        assert entries == [SUCHE, "/events/", "/cases/", "/attachments/", HILFE, PROFIL, SPRACHE, ABMELDEN]

    def test_staff_with_own_pending_deletion_request(self, client, facility, staff_user):
        """The privacy hub appears when at least one tab is visible."""
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=uuid.uuid4(),
            reason="Mobil-Guard",
            requested_by=staff_user,
            status=DeletionRequest.Status.PENDING,
        )
        entries = _more_entries(_render(client, staff_user))
        assert entries == [
            SUCHE,
            "/events/",
            "/cases/",
            "/attachments/",
            "/datenschutz/",
            HILFE,
            PROFIL,
            SPRACHE,
            ABMELDEN,
        ]

    def test_lead(self, client, lead_user):
        """Leads see the dashboard, contacts and statistics in the complete menu."""
        entries = _more_entries(_render(client, lead_user))
        assert entries == [
            SUCHE,
            "/start/",
            "/events/",
            "/cases/",
            "/attachments/",
            "/statistics/",
            "/datenschutz/",
            HILFE,
            PROFIL,
            SPRACHE,
            ABMELDEN,
        ]

    def test_facility_admin(self, client, admin_user):
        """Facility admins see contacts and both hubs without a dashboard entry.

        Django administration remains a link on the administration overview.
        """
        entries = _more_entries(_render(client, admin_user))
        assert entries == [
            SUCHE,
            "/events/",
            "/cases/",
            "/attachments/",
            "/statistics/",
            "/datenschutz/",
            "/verwaltung/",
            HILFE,
            PROFIL,
            SPRACHE,
            ABMELDEN,
        ]

    def test_super_admin(self, client, super_admin_user):
        """Systemadministration: der Systembereich hat einen eigenen Bottom-Bar-Slot.

        Ihn zusätzlich ins Mehr-Menü zu legen hieße, denselben Ort zweimal
        anzubieten — und beide Marker gleichzeitig leuchten zu lassen.
        """
        entries = _more_entries(_render(client, super_admin_user, "core:system_dashboard"))
        assert entries == ["/admin-mgmt/", HILFE, PROFIL, SPRACHE, ABMELDEN]


@pytest.mark.django_db
class TestMoreMenuMarkers:
    """Die stabilen Marker, an denen E2E und Handtest das Menü greifen."""

    def test_arbeitszentrale_carries_the_mobile_testid(self, client, lead_user):
        assert 'data-testid="mobile-nav-dashboard"' in _more_menu(_render(client, lead_user))

    def test_hub_entries_carry_their_mobile_testids(self, client, admin_user):
        menu = _more_menu(_render(client, admin_user))
        assert 'data-testid="mobile-nav-datenschutz"' in menu
        assert 'data-testid="mobile-nav-verwaltung"' in menu

    def test_assistant_has_no_arbeitszentrale(self, client, assistant_user):
        """Gate-Identität: dasselbe ``sees_arbeitszentrale`` wie in der Sidebar."""
        assert 'data-testid="mobile-nav-dashboard"' not in _more_menu(_render(client, assistant_user))


@pytest.mark.django_db
class TestMoreButtonHighlight:
    """Der „Mehr"-Button trägt die Ortsanzeige für alles, was im Menü liegt."""

    def _more_is_active(self, content: str) -> bool:
        """True, wenn der „Mehr"-Button hervorgehoben ist.

        Geprüft wird die **inaktive** Klasse: ``text-accent`` allein wäre kein
        Beleg, weil der Ruhezustand ``hover:text-accent`` trägt und ein
        Substring-Test darauf immer wahr wäre — dieselbe Falle, die den
        Aktivzustand der Sidebar jahrelang verfälscht hat.
        """
        idx = content.index('data-testid="mobile-nav-more"')
        tag = content[content.rindex("<button", 0, idx) : content.index(">", idx)]
        return "text-ink-muted" not in tag

    def test_more_is_highlighted_on_the_arbeitszentrale(self, client, lead_user):
        """Refs #1795: die Arbeitszentrale ist mobil neu erreichbar — also muss sie
        auch den Katalog erweitern, sonst erlischt dort die Ortsanzeige."""
        assert self._more_is_active(_render(client, lead_user, "core:dashboard"))

    def test_more_is_highlighted_on_a_hub_page(self, client, admin_user):
        assert self._more_is_active(_render(client, admin_user, "core:verwaltung_uebersicht"))

    def test_more_is_not_highlighted_on_a_bottom_bar_page(self, client, staff_user):
        """Gegenprobe: die Personenliste hat einen eigenen Slot — zwei leuchtende
        Slots wären keine Ortsanzeige mehr."""
        assert not self._more_is_active(_render(client, staff_user, "core:client_list"))
