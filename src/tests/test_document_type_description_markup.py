"""The description on the document type detail page is a valid definition pair inside a dl."""

from html.parser import HTMLParser

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

VOID = {"br", "hr", "img", "input", "meta", "link"}


class _Items(HTMLParser):
    """Records, for each dt/dd, whether it sits inside a dl, and its text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.items: list[tuple[str, bool, str]] = []  # (tag, inside dl, text)
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag in ("dt", "dd"):
            self._current = [tag, "dl" in self.stack, ""]
            self.items.append(self._current)
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in ("dt", "dd"):
            self._current = None
        if tag in self.stack:
            while self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self._current is not None:
            self._current[2] += data


def _items(client, user, doc_type):
    client.force_login(user)
    response = client.get(reverse("core:config_doctype_detail", args=[doc_type.pk]))
    assert response.status_code == 200
    parser = _Items()
    parser.feed(response.content.decode())
    return [(tag, in_dl, " ".join(text.split())) for tag, in_dl, text in parser.items]


def test_description_is_a_definition_pair_inside_a_dl(client, admin_user, doc_type_contact):
    doc_type_contact.description = "Erste Zeile\nZweite Zeile"
    doc_type_contact.save()

    items = _items(client, admin_user, doc_type_contact)

    # No loose dt/dd: every item sits inside a dl.
    assert items
    assert all(in_dl for _, in_dl, _ in items), [i for i in items if not i[1]]
    assert ("dt", True, "Beschreibung") in items
    description = [t for t in items if t[0] == "dd" and t[2] == "Erste Zeile Zweite Zeile"]
    assert len(description) == 1


def test_without_description_no_description_pair_is_rendered(client, admin_user, doc_type_contact):
    doc_type_contact.description = ""
    doc_type_contact.save()

    items = _items(client, admin_user, doc_type_contact)

    assert not [t for t in items if t[2] == "Beschreibung"]
