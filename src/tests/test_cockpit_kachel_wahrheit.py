"""Kachel-Wahrheit K1–K3 des Zeitstrom-Cockpits (Refs #1804, Konzept §3.7).

Die Regel, um die es geht: **Die Zahl auf einer verlinkten Einstiegskachel ist
das Ergebnis genau der Abfrage, die der Klick öffnet** — oder die Kachel benennt
ihren Filter.

Der Befund war eine doppelte Divergenz. ``staff_dashboard_context`` zählt
strikt ``assigned_to=user``; die Zielliste ``/workitems/`` hat als Default-Scope
aber ``assigned_to=user OR assigned_to IS NULL``. Und beide Kacheln verlinkten
**parameterlos**, sodass die „Überfällig"-Kachel in einer Liste ohne
Überfällig-Filter landete: Die Zahl auf der Kachel kam in der Zielliste gar
nicht mehr vor.

Ein statischer Drift-Guard taugt hier nicht — Kachelzahlen sind Laufzeitwerte.
Deshalb je Kachel ein ``django_db``-Test, der die Zahl gegen die **tatsächlich
unter dem Kachel-Href gerenderte Liste** stellt. Genau dieser Test hätte den
Befund gefunden; ein Test auf den Kontext allein nicht.
"""

from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import WorkItem

# Der Anker der Kachel: ``<a href="…" …>`` bis zum Ende des Kachel-Blocks.
_CARD_RE = re.compile(r'<a href="([^"]+)"[^>]*>(?:(?!</a>).)*?{label}(?:(?!</a>).)*?</a>', re.DOTALL)


def _kachel_href(content: str, label: str) -> str:
    """Href der Cockpit-Kachel mit der Beschriftung ``label``."""
    cockpit_start = content.index('data-testid="zeitstrom-cockpit"')
    cockpit = content[cockpit_start : content.index("</div>\n\n", cockpit_start) + 8]
    match = re.search(_CARD_RE.pattern.format(label=re.escape(label)), cockpit, re.DOTALL)
    assert match is not None, f"Kachel {label!r} nicht im Cockpit gefunden"
    return match.group(1).replace("&amp;", "&")


def _zeilen(response) -> list:
    """Die Aufgaben, die die Inbox unter diesem Request tatsächlich rendert."""
    return list(response.context["open_items"]) + list(response.context["in_progress_items"])


@pytest.fixture
def kollegin(facility):
    """Zweite Fachkraft derselben Einrichtung — fuer den „fremd zugewiesen"-Fall."""
    from core.models import User

    return User.objects.create_user(
        username="kachel_kollegin",
        role=User.Role.STAFF,
        facility=facility,
        is_staff=True,
    )


@pytest.fixture
def aufgaben(facility, staff_user, kollegin):
    """Drei offene Aufgaben: meine, unzugewiesen, fremd — davon zwei überfällig.

    Die unzugewiesene ist der scharfe Fall: sie liegt im Default-Scope der
    Zielliste (``mine_team``), aber nicht in der Kachelzahl.
    """
    today = timezone.localdate()
    gestern = today - timedelta(days=1)
    return {
        "meine": WorkItem.objects.create(
            facility=facility,
            title="Meine offene Aufgabe",
            item_type=WorkItem.ItemType.TASK,
            assigned_to=staff_user,
            created_by=staff_user,
            due_date=today,
        ),
        "meine_ueberfaellig": WorkItem.objects.create(
            facility=facility,
            title="Meine überfällige Aufgabe",
            item_type=WorkItem.ItemType.TASK,
            assigned_to=staff_user,
            created_by=staff_user,
            due_date=gestern,
        ),
        "unzugewiesen": WorkItem.objects.create(
            facility=facility,
            title="Teamaufgabe ohne Zuweisung",
            item_type=WorkItem.ItemType.TASK,
            assigned_to=None,
            created_by=staff_user,
            due_date=gestern,
        ),
        "fremd": WorkItem.objects.create(
            facility=facility,
            title="Aufgabe einer Kollegin",
            item_type=WorkItem.ItemType.TASK,
            assigned_to=kollegin,
            created_by=staff_user,
            due_date=gestern,
        ),
    }


@pytest.mark.django_db
class TestKachelWahrheit:
    def _cockpit(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        return response

    @pytest.mark.parametrize(
        "label,zaehler",
        [("Meine Aufgaben", "my_open_workitems_count"), ("Überfällig", "my_overdue_count")],
    )
    def test_kachelzahl_deckt_die_zielliste(self, client, staff_user, aufgaben, label, zaehler):
        """K1: Zahl == Länge der Liste, die der Klick öffnet.

        Ohne die Filterparameter im Href schlägt genau diese Assertion fehl: die
        parameterlose Inbox zeigt zusätzlich die unzugewiesene Teamaufgabe
        (Default-Scope ``mine_team``), die „Überfällig"-Kachel landete zudem in
        einer Liste ganz ohne Fälligkeitsfilter.
        """
        cockpit = self._cockpit(client, staff_user)
        zahl = cockpit.context["cockpit"][zaehler]
        assert zahl > 0, "Die Fixture muss eine nicht-leere Kachel erzeugen, sonst prüft der Test nichts."

        href = _kachel_href(cockpit.content.decode(), label)
        ziel = client.get(href)

        assert ziel.status_code == 200
        assert len(_zeilen(ziel)) == zahl, (
            f"Kachel {label!r} verspricht {zahl}, die Zielliste unter {href} liefert {len(_zeilen(ziel))}."
        )

    def test_kachel_links_tragen_ihre_filter(self, client, staff_user, aufgaben):
        """K2: Eine Kachel verlinkt nie parameterlos, wenn ihre Zahl gefiltert ist."""
        content = self._cockpit(client, staff_user).content.decode()

        meine = parse_qs(urlparse(_kachel_href(content, "Meine Aufgaben")).query)
        assert meine.get("assigned_to") == ["me"]
        assert "due" not in meine

        ueberfaellig = parse_qs(urlparse(_kachel_href(content, "Überfällig")).query)
        assert ueberfaellig.get("assigned_to") == ["me"]
        assert ueberfaellig.get("due") == ["overdue"]

    def test_meine_aufgaben_bleibt_strikt_meine(self, client, staff_user, aufgaben):
        """Die Kachel heißt „Meine Aufgaben", und das Wort ist die Zusage.

        Die verworfene Alternative wäre gewesen, die Zahl auf ``mine_team`` zu
        verbreitern — dann stünde unter „Meine Aufgaben" auch, was niemandem
        zugewiesen ist.
        """
        cockpit = self._cockpit(client, staff_user)
        ziel = client.get(_kachel_href(cockpit.content.decode(), "Meine Aufgaben"))

        titel = {item.title for item in _zeilen(ziel)}
        assert aufgaben["unzugewiesen"].title not in titel
        assert aufgaben["fremd"].title not in titel

    def test_zielliste_weist_den_filter_aus_und_bietet_den_rueckweg(self, client, staff_user, aufgaben):
        """Delta-Risiko 4: Mit Parametern zeigt die Inbox ihre „Filter aktiv"-UI.

        Das ist gewollt — die Liste IST gefiltert, und ein stiller Filter wäre
        genau die Sorte Zusage, die dieses Ticket abschafft. Wichtig ist, dass
        der Rückweg auf die ungefilterte Inbox führt.
        """
        cockpit = self._cockpit(client, staff_user)
        ziel = client.get(_kachel_href(cockpit.content.decode(), "Überfällig"))

        assert ziel.context["has_active_filters"] is True
        assert ziel.context["selected_due"] == "overdue"

        ungefiltert = client.get(reverse("core:workitem_inbox"))
        assert ungefiltert.context["has_active_filters"] is False
        assert len(_zeilen(ungefiltert)) > len(_zeilen(ziel))

    def test_heutige_kontakte_benennt_ihren_filter_und_landet_heute(self, client, staff_user, aufgaben):
        """K3: Die Kachel ist nicht deckungsgleich (das Ziel ist die Übergabe einer
        Schicht, die Zahl zählt den ganzen Tag) — dann muss die Unterzeile den
        Filter benennen, und das Ziel muss wenigstens auf dem heutigen Tag landen.
        """
        cockpit = self._cockpit(client, staff_user)
        content = cockpit.content.decode()

        cockpit_start = content.index('data-testid="zeitstrom-cockpit"')
        assert "Ereignisse heute" in content[cockpit_start : cockpit_start + 1500]

        ziel = client.get(_kachel_href(content, "Heutige Kontakte"))
        assert ziel.status_code == 200
        assert ziel.context["target_date"] == timezone.localdate()
