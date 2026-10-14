"""Stable, translated announcement regions in both rendered layouts."""

from xml.etree.ElementTree import tostring

import pytest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import override

from tests.test_a11y_board_navigation import TreeParser, find

MESSAGES = {
    "data-offline-message": "Verbindung unterbrochen. Sie arbeiten jetzt offline.",
    "data-online-message": "Verbindung wiederhergestellt.",
    "data-online-sync-message": "Verbindung wiederhergestellt. Wartende Änderungen werden übertragen.",
    "data-sync-complete-message": "Übertragung beendet.",
    "data-conflict-message": (
        "Neuer Konflikt. Öffnen Sie die Seite „Konflikte“ über den Link im Konfliktbanner zur Auflösung."
    ),
}


@pytest.mark.django_db
def test_layouts_share_one_always_present_translated_announcer(client, admin_user):
    client.force_login(admin_user)
    with override("de"):
        response = client.get(reverse("core:client_list"))
        assert response.status_code == 200
        sources = [response.content.decode(), render_to_string("offline_base.html")]
    regions = []
    attributes = []
    for source in sources:
        root = find(TreeParser(source).root, "id", "offline-status")
        matches = [node for node in root.iter() if node.get("data-testid") == "offline-status-announcer"]
        assert len(matches) == 1
        region = matches[0]
        assert region in list(root)
        assert {key: region.get(key) for key in ("role", "aria-live", "aria-atomic")} == {
            "role": "status",
            "aria-live": "polite",
            "aria-atomic": "true",
        }
        assert "sr-only" in region.get("class", "").split()
        assert region.get("x-text") == "announcement"
        for node in (root, region):
            assert not {"x-show", "x-cloak", "hidden"} & node.attrib.keys()
        for banner in ("offline-banner", "sync-banner", "conflict-banner"):
            node = find(root, "data-testid", banner)
            assert not {"role", "aria-live"} & node.attrib.keys()
            assert region not in list(node.iter())
        messages = {key: root.get(key) for key in MESSAGES}
        assert messages == MESSAGES
        assert all(not any(char.isdigit() for char in text) and "{" not in text for text in messages.values())
        attributes.append(messages)
        regions.append(tostring(region, encoding="unicode").strip())
    assert regions[0] == regions[1]
    assert attributes[0] == attributes[1]
