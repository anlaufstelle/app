"""Kopfleisten-Aktionen der Personenliste auf Mobil (Refs #1863).

Die Kopfleiste trug bis `13aba67a` einen Wrapper `hidden md:flex`. Der stammt
aus #448 und galt dort **nur** dem Anlegen-Button ("die Bottom-Nav bietet
denselben +-Dropdown"). Papierkorb (#1040) und Sammel-Mitnahme (#1326) sind
später in denselben Wrapper gewandert und waren dadurch unter 768 px
unerreichbar — ohne jeden Ersatzweg, denn beide stehen in keiner Navigation.

Geprüft wird deshalb nicht die Pixelform, sondern die **Erreichbarkeit**: keine
`hidden`-Vorfahrenkette über den beiden Aktionen, während der Create-Link seine
`hidden md:`-Regel behält. Der Test hängt an der Vorfahrenkette statt an einem
Substring-Vergleich auf der ganzen Seite, weil genau ein Vorfahren-Attribut die
Regression verursacht hat — am Button selbst stand nie eine Responsive-Klasse.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.urls import reverse

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Element(HTMLParser):
    """Attribute, Vorfahren-Klassen und Kindelemente zu einem ``data-testid``.

    Muster analog ``tests.test_button_permissions._ContainerInhalt``:
    ``handle_startendtag`` ist eigens überschrieben, damit die
    XHTML-Selbstschließer im Markup (``<path … />``) den Tag-Stack nicht
    verschieben.
    """

    def __init__(self, testid: str):
        super().__init__(convert_charrefs=True)
        self._gesucht = testid
        self._stack: list[tuple[str, str]] = []
        self._eintritt: int | None = None
        self.gefunden = False
        self.attrs: dict[str, str] = {}
        self.vorfahren_klassen: list[str] = []
        self.kinder: list[dict[str, str]] = []

    def handle_starttag(self, tag, attrs):
        attribute = {k: (v or "") for k, v in attrs}
        if self._eintritt is None and attribute.get("data-testid") == self._gesucht:
            self.gefunden = True
            self.attrs = attribute
            self.vorfahren_klassen = [klasse for _, klasse in self._stack]
            self._eintritt = len(self._stack)
        elif self._eintritt is not None:
            self.kinder.append(attribute)
        if tag not in _VOID:
            self._stack.append((tag, attribute.get("class", "")))

    def handle_startendtag(self, tag, attrs):
        if self._eintritt is not None:
            self.kinder.append({k: (v or "") for k, v in attrs})

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        while self._stack and self._stack.pop()[0] != tag:
            pass
        if self._eintritt is not None and len(self._stack) <= self._eintritt:
            self._eintritt = None


def _element(html: str, testid: str) -> _Element:
    parser = _Element(testid)
    parser.feed(html)
    assert parser.gefunden, f'Element data-testid="{testid}" nicht im HTML'
    return parser


def _klassen(wert: str) -> set[str]:
    return set(wert.split())


def _hat_versteckten_vorfahren(element: _Element) -> bool:
    """``hidden`` als eigenständige Klasse in der Vorfahrenkette.

    Tokenisiert statt Substring-Suche: ``base.html`` trägt am ``<body>`` und am
    ``<main>`` ein ``overflow-x-hidden``, das sonst jeden Aufruf rot färbte.
    """
    return any("hidden" in _klassen(klasse) for klasse in element.vorfahren_klassen)


@pytest.mark.django_db
class TestPersonenlisteKopfleisteMobil:
    """Refs #1863: Erreichbarkeit der drei Kopfaktionen je Breakpoint."""

    def _html(self, client, user) -> str:
        client.force_login(user)
        return client.get(reverse("core:client_list")).content.decode()

    def test_sammel_mitnahme_ist_mobil_erreichbar(self, client, staff_user):
        """Kein versteckter Vorfahre und keine ``hidden``-Klasse am Button selbst."""
        button = _element(self._html(client, staff_user), "bulk-take-offline-btn")

        assert not _hat_versteckten_vorfahren(button), (
            f"Sammel-Mitnahme liegt wieder unter einem versteckten Wrapper: {button.vorfahren_klassen}"
        )
        assert "hidden" not in _klassen(button.attrs.get("class", ""))

    def test_papierkorb_ist_mobil_erreichbar(self, client, admin_user):
        """Die Kopfleiste ist der einzige Einstieg — er darf mobil nicht fehlen."""
        link = _element(self._html(client, admin_user), "client-trash-link")

        assert not _hat_versteckten_vorfahren(link), (
            f"Papierkorb liegt wieder unter einem versteckten Wrapper: {link.vorfahren_klassen}"
        )
        assert "hidden" not in _klassen(link.attrs.get("class", ""))
        assert link.attrs.get("href") == reverse("core:client_trash")

    def test_anlegen_bleibt_mobil_versteckt(self, client, staff_user):
        """Gegenprobe zu #448: die Bottom-Nav bietet dieselbe Aktion.

        Ohne diese Zusicherung wäre der Fix für #1863 ein stiller Rückbau von
        #448 — der Create-Button stünde mobil doppelt.
        """
        link = _element(self._html(client, staff_user), "clients-list-create-client")
        klassen = _klassen(link.attrs.get("class", ""))

        assert "hidden" in klassen, f"Anlegen muss mobil versteckt bleiben (#448): {klassen}"
        assert "md:inline-flex" in klassen, "…und ab md wieder erscheinen"

    def test_beide_labelvarianten_liegen_am_button(self, client, staff_user):
        """Kurzform mobil, Langform ab md — genau eine ist je Breakpoint sichtbar."""
        button = _element(self._html(client, staff_user), "bulk-take-offline-btn")
        span_klassen = [_klassen(kind.get("class", "")) for kind in button.kinder]

        assert any("md:hidden" in k for k in span_klassen), "Kurzlabel (Alle mitnehmen) fehlt"
        assert any({"hidden", "md:inline"} <= k for k in span_klassen), "Langlabel fehlt"

    def test_bulk_button_traegt_kein_aria_label(self, client, staff_user):
        """WCAG 2.5.3 (Label in Name).

        Nicht sichtbarer Text zählt nicht zum Accessible Name — der ist damit je
        Breakpoint exakt der sichtbare. Ein ``aria-label`` mit dem Langtext
        überschriebe den mobilen Kurztext („Alle mitnehmen" ist KEIN Teilstring
        von „Alle offline mitnehmen") und bräche 2.5.3 genau dort, wo der
        Kurztext gebraucht wird.
        """
        button = _element(self._html(client, staff_user), "bulk-take-offline-btn")

        assert "aria-label" not in button.attrs, (
            "Der sichtbare Kurztext ist der Accessible Name — ein aria-label bräche WCAG 2.5.3"
        )

    def test_papierkorb_aria_label_deckt_sich_mit_dem_sichtbaren_text(self, client, admin_user):
        """Hier ist ``aria-label`` erlaubt: es ist wortgleich mit dem md-Text."""
        html = self._html(client, admin_user)
        link = _element(html, "client-trash-link")

        assert link.attrs.get("aria-label") == "Papierkorb"
        assert any("md:inline" in _klassen(kind.get("class", "")) for kind in link.kinder), (
            "Ab md muss der Text sichtbar sein, sonst deckt aria-label nichts ab"
        )

    @pytest.mark.parametrize(
        ("testid", "fixture_name"),
        [
            ("bulk-take-offline-btn", "staff_user"),
            ("client-trash-link", "admin_user"),
        ],
    )
    def test_mobile_trefferflaeche_44px(self, client, request, testid, fixture_name):
        """Projektkonvention aus #810 — mobil 44 px, ab md die alte Pixelform."""
        user = request.getfixturevalue(fixture_name)
        klassen = _klassen(_element(self._html(client, user), testid).attrs.get("class", ""))

        assert "min-h-[44px]" in klassen, f"{testid}: Touch-Ziel unter 44 px"

    def test_karten_mitnehmen_hat_44px(self, client, staff_user, client_identified):
        """Der einzige mobile Aktionsknopf IN der Liste lag bei ca. 32x24 px.

        Braucht eine Person im Bestand — ohne Zeile rendert die Karte nicht.
        """
        klassen = _klassen(_element(self._html(client, staff_user), "card-take-offline-btn").attrs.get("class", ""))

        assert {"min-h-[44px]", "min-w-[44px]"} <= klassen, f"Karten-Knopf unter 44 px: {klassen}"
