"""Rendered keyboard controls and navigation layout contracts."""

from html.parser import HTMLParser
from xml.etree.ElementTree import Element, SubElement

import pytest
from django.urls import reverse


class TreeParser(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.root = Element("root")
        self.stack = [self.root]
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        node = SubElement(self.stack[-1], tag, {key: value or "" for key, value in attrs})
        if tag not in {"input", "meta", "link", "br", "hr", "img", "path", "use"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].text = (self.stack[-1].text or "") + data


def find(root, attr, value):
    return next(node for node in root.iter() if node.get(attr) == value)


@pytest.mark.django_db
def test_desktop_account_wraps_role_and_separates_language(client, admin_user):
    admin_user.first_name = "Alexandra" * 10
    admin_user.save(update_fields=["first_name"])
    client.force_login(admin_user)
    root = TreeParser(client.get(reverse("core:client_list")).content.decode()).root
    profile = next(node for node in root.iter("a") if node.get("href") == reverse("core:account_profile"))
    role = next(node for node in profile.iter("div") if "Anwendungsbetreuung" in (node.text or ""))
    assert {"min-w-0", "break-words"} <= set(role.get("class").split())
    assert "whitespace-nowrap" not in role.get("class")
    parent = next(node for node in root.iter() if profile in list(node))
    assert "flex-col" in parent.get("class", "") or "flex-wrap" in parent.get("class", "")
    assert {node.get("value") for node in parent.iter("button")} >= {"de", "en"}
    assert any("Abmelden" in (node.text or "") for node in root.iter("button"))


@pytest.mark.django_db
def test_mobile_more_has_viewport_bounded_scroll_container(client, admin_user):
    client.force_login(admin_user)
    root = TreeParser(client.get(reverse("core:client_list")).content.decode()).root
    panel = find(root, "id", "mobile-more-items")
    classes = panel.get("class").split()
    assert {"overflow-y-auto", "overscroll-contain"} <= set(classes)
    assert any(cls.startswith("max-h-") and "dvh" in cls and "safe-area-inset-bottom" in cls for cls in classes)
    assert find(panel, "value", "en") is not None
    assert any("Abmelden" in (node.text or "") for node in panel.iter("button"))
