"""Context-help links must land on the section that answers the question (Refs #2459).

Every "Learn more" link of a context help block has to resolve to an existing
in-app help page *and* to an existing anchor on that page. The offline banner
must not link to an online-only page at all.
"""

from html.parser import HTMLParser

import pytest
from django.urls import reverse

from core.services.client import request_client_deletion
from core.views.help import HELP_PAGES


class _ContextHelpLinks(HTMLParser):
    """Collects ``{help_id: [href, ...]}`` for every ``<details data-context-help>`` block."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: dict[str, list[str]] = {}
        self._current: str | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "details" and "data-context-help" in attributes and self._current is None:
            self._current = attributes["data-context-help"]
            self._depth = 1
            self.links.setdefault(self._current, [])
            return
        if self._current is None:
            return
        if tag == "details":
            self._depth += 1
        if tag == "a" and attributes.get("href"):
            self.links[self._current].append(attributes["href"])

    def handle_endtag(self, tag):
        if self._current is not None and tag == "details":
            self._depth -= 1
            if self._depth == 0:
                self._current = None


class _AnchorIds(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        element_id = dict(attrs).get("id")
        if element_id:
            self.ids.add(element_id)


def _context_help_links(body: str) -> dict[str, list[str]]:
    parser = _ContextHelpLinks()
    parser.feed(body)
    return parser.links


def _anchor_ids(body: str) -> set[str]:
    parser = _AnchorIds()
    parser.feed(body)
    return parser.ids


def _assert_link_lands_on_existing_anchor(client, href: str, anchor: str) -> str:
    """Follow ``href``; the page must exist and carry the expected fragment id."""
    path, _, fragment = href.partition("#")
    assert fragment == anchor, f"{href!r} should end in #{anchor}"
    response = client.get(path)
    assert response.status_code == 200
    body = response.content.decode()
    assert anchor in _anchor_ids(body), f"{path} has no element with id={anchor!r}"
    return body


@pytest.mark.django_db
class TestFieldConditionsHelpLink:
    def test_more_link_points_at_the_field_conditions_section(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)

        body = client.get(reverse("core:config_condition_board", kwargs={"pk": doc_type_contact.pk})).content.decode()
        hrefs = _context_help_links(body)["document-conditions"]

        assert hrefs == [f"{reverse('core:help_page', kwargs={'slug': 'verwaltung'})}#feldbedingungen"]
        page = _assert_link_lands_on_existing_anchor(client, hrefs[0], "feldbedingungen")
        section = page.split('id="feldbedingungen"', 1)[1]
        # The section answers the questions of the form text: AND / OR, an example,
        # the operators and the hidden-required rule.
        for needle in ("UND", "ODER", "Beispiel", "ist gleich", "Pflichtfeld"):
            assert needle in section, needle

    def test_general_administration_page_stays_available_as_overview(self, client, admin_user):
        client.force_login(admin_user)

        response = client.get(reverse("core:help_page", kwargs={"slug": "verwaltung"}))

        assert response.status_code == 200
        assert "Die Verwaltung passt die Anwendung an die Abläufe der Einrichtung an." in response.content.decode()


@pytest.mark.django_db
class TestPrivacyHelpLinks:
    def test_four_eyes_link_points_at_the_four_eyes_section(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        request_client_deletion(client_identified, staff_user, "Widerruf")

        body = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk})).content.decode()
        hrefs = _context_help_links(body)["four-eyes"]

        assert hrefs == [f"{reverse('core:help_page', kwargs={'slug': 'datenschutz'})}#vier-augen-prinzip"]
        page = _assert_link_lands_on_existing_anchor(client, hrefs[0], "vier-augen-prinzip")
        section = page.split('id="vier-augen-prinzip"', 1)[1]
        assert "Vier-Augen-Prinzip" in section
        assert "Antrag" in section

    def test_retention_link_points_at_the_deletion_period_section(self, client, lead_user):
        client.force_login(lead_user)

        body = client.get(reverse("core:retention_dashboard")).content.decode()
        hrefs = _context_help_links(body)["retention"]

        assert hrefs == [f"{reverse('core:help_page', kwargs={'slug': 'datenschutz'})}#loeschfristen"]
        page = _assert_link_lands_on_existing_anchor(client, hrefs[0], "loeschfristen")
        section = page.split('id="loeschfristen"', 1)[1]
        for needle in ("Aufbewahrungsfrist", "Löschvorschlag", "Legal Hold"):
            assert needle in section, needle


@pytest.mark.django_db
class TestOtherContextHelpLinks:
    def test_contact_stage_link_lands_on_the_clients_help_page(self, client, staff_user):
        client.force_login(staff_user)

        body = client.get(reverse("core:client_list")).content.decode()
        hrefs = _context_help_links(body)["contact-stage"]

        assert hrefs == [reverse("core:help_page", kwargs={"slug": "personen"})]
        page = client.get(hrefs[0]).content.decode()
        assert "Kontaktstufe" in page

    def test_offline_banner_carries_no_link_to_an_online_only_page(self, client, staff_user):
        client.force_login(staff_user)

        body = client.get(reverse("core:client_list")).content.decode()

        assert _context_help_links(body)["offline"] == []


class TestHelpSectionRegistry:
    def test_anchors_are_unique_per_page_and_valid_ids(self):
        for page in HELP_PAGES.values():
            anchors = [section.anchor for section in page.sections]
            assert len(anchors) == len(set(anchors)), page.slug
            for anchor in anchors:
                assert anchor and anchor == anchor.lower() and " " not in anchor, anchor

    @pytest.mark.django_db
    def test_every_section_renders_with_its_anchor(self, client, staff_user):
        client.force_login(staff_user)
        for page in HELP_PAGES.values():
            body = client.get(reverse("core:help_page", kwargs={"slug": page.slug})).content.decode()
            ids = _anchor_ids(body)
            for section in page.sections:
                assert section.anchor in ids, (page.slug, section.anchor)
                assert str(section.heading) in body
