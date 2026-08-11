"""Reihenfolge der operativen Hauptnavigation (Refs #1126).

Aufgaben gehoeren fachlich zum laufenden Dienst (Zeitstrom/Uebergabe) und
sollen darum in der Desktop-Sidebar direkt unter Zeitstrom stehen — vor den
Stammdaten-Bereichen Personen und Faelle. Erwartete operative Reihenfolge:

    Zeitstrom -> Aufgaben -> Personen -> Faelle
"""

from __future__ import annotations

import pytest
from django.urls import reverse


def _desktop_nav(content: str) -> str:
    """Schneidet die Eintragsliste der Desktop-Sidebar aus der gerenderten Seite.

    Grenzen sind der Container-Marker ``data-testid="nav-items"`` (Refs #1813)
    und sein schliessendes ``</div>``. Der engere Schnitt haelt sowohl die
    Mobile-Bottom-Nav (eigene Reihenfolge, 5-Slot-Layout) als auch den
    Org-Header ueber der Liste heraus — dessen ``href="/"`` war bisher ein
    Zufallstreffer fuer die Zeitstrom-Position.
    """
    start = content.index('data-testid="nav-items"')
    end = content.index("</div>", start)
    assert start < end
    return content[start:end]


def _mobile_nav(content: str) -> str:
    """Schneidet die Mobile-Bottom-Nav (ab ``aria-label`` bis Dokumentende)."""
    return content[content.index('aria-label="Mobile Navigation"') :]


@pytest.mark.django_db
class TestOperativeNavOrder:
    def test_desktop_nav_aufgaben_before_personen_and_faelle(self, client, staff_user):
        """Aufgaben (/workitems/) steht vor Personen (/clients/) und Faellen (/cases/)."""
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()
        nav = _desktop_nav(content)

        pos_zeitstrom = nav.index('href="/"')
        pos_aufgaben = nav.index('href="/workitems/"')
        pos_personen = nav.index('href="/clients/"')
        pos_faelle = nav.index('href="/cases/"')

        assert pos_zeitstrom < pos_aufgaben < pos_personen < pos_faelle

    def test_assistant_sees_aufgaben_before_personen(self, client, assistant_user):
        """Assistenz sieht keine Faelle, aber Aufgaben weiter vor Personen."""
        client.force_login(assistant_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()
        nav = _desktop_nav(content)

        pos_aufgaben = nav.index('href="/workitems/"')
        pos_personen = nav.index('href="/clients/"')

        assert pos_aufgaben < pos_personen

    def test_mobile_nav_aufgaben_before_personen(self, client, staff_user):
        """Konsistenz mobil: Aufgaben-Slot steht vor Personen-Slot (Refs #1126)."""
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()
        nav = _mobile_nav(content)

        pos_aufgaben = nav.index("mobile-nav-workitems")
        pos_personen = nav.index("mobile-nav-clients")

        assert pos_aufgaben < pos_personen
