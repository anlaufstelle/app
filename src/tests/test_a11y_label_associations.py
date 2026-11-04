"""Visible labels are bound to their controls with unique ids (offline, hold, config search, person search)."""

import copy
import uuid
from collections import Counter
from html.parser import HTMLParser

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from tests.test_retention_two_person_approval import proposal as proposal  # noqa: F401

pytestmark = pytest.mark.django_db


class _Dom(HTMLParser):
    """Collects ids, label targets and label text of a rendered page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.tags = {}
        self.labels = []  # (for_value, dynamic_for_value, text)
        self._label = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
            self.tags[attrs["id"]] = (tag, attrs)
        if tag == "label":
            self._label = [attrs.get("for"), attrs.get(":for"), ""]
            self.labels.append(self._label)

    def handle_data(self, data):
        if self._label is not None:
            self._label[2] += data

    def handle_endtag(self, tag):
        if tag == "label":
            self._label = None


def _dom(html):
    dom = _Dom()
    dom.feed(html)
    return dom


def _label_for(dom, control_id):
    matches = [text for target, _, text in dom.labels if target == control_id]
    assert len(matches) == 1, (control_id, dom.labels)
    return " ".join(matches[0].split())


def _page(client, user, url):
    client.force_login(user)
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def test_offline_contact_selects_are_named_by_their_visible_labels(client, staff_user):
    dom = _dom(_page(client, staff_user, reverse("core:offline_client_detail", args=[uuid.uuid4()])))

    for testid, label in (("offline-create-doctype", "Dokumentationstyp"), ("offline-create-case", "Fall (optional)")):
        ids = [i for i, (tag, a) in dom.tags.items() if a.get("data-testid") == testid]
        assert len(ids) == 1, testid
        assert dom.tags[ids[0]][0] == "select"
        assert _label_for(dom, ids[0]) == label
    assert not [i for i, n in Counter(dom.ids).items() if n > 1]


def test_hold_reason_label_targets_the_textarea_of_each_proposal(proposal):  # noqa: F811
    other = copy.copy(proposal)
    other.pk = uuid.uuid4()
    html = "".join(
        render_to_string("core/retention/partials/proposal_card.html", {"proposal": p, "retention_approver_count": 2})
        for p in (proposal, other)
    )
    dom = _dom(html)

    for p in (proposal, other):
        field_id = f"hold-reason-{p.pk}"
        tag, attrs = dom.tags[field_id]
        assert tag == "textarea" and attrs["name"] == "reason"
        assert _label_for(dom, field_id) == "Begründung *"
    assert not [i for i, n in Counter(dom.ids).items() if n > 1]


@pytest.mark.parametrize(
    ("url_name", "input_id", "label"),
    [
        ("core:config_doctype_list", "doctype-search", "Dokumentationstypen durchsuchen"),
        ("core:config_fieldtemplate_list", "fieldtemplate-search", "Feldvorlagen durchsuchen"),
    ],
)
def test_config_searches_have_an_explicit_label(client, admin_user, url_name, input_id, label):
    dom = _dom(_page(client, admin_user, reverse(url_name)))

    tag, attrs = dom.tags[input_id]
    assert tag == "input" and attrs["name"] == "q"
    assert _label_for(dom, input_id) == label


@pytest.mark.parametrize(
    "url_name",
    ["core:case_create", "core:workitem_create", "core:event_create"],
)
def test_person_label_targets_the_combobox_input(client, staff_user, url_name):
    dom = _dom(_page(client, staff_user, reverse(url_name)))

    tag, attrs = dom.tags["client-autocomplete-input"]
    assert tag == "input" and attrs["role"] == "combobox"
    assert attrs["aria-label"] == "Person suchen"
    # Listbox and error references stay in place.
    assert attrs["aria-controls"] == "client-autocomplete-list"
    assert _label_for(dom, "client-autocomplete-input").startswith("Person")
    # Per-instance ids follow the listbox id at runtime.
    target = [(f, d) for f, d, _ in dom.labels if f == "client-autocomplete-input"][0]
    assert target[1] == "inputId" and attrs[":id"] == "inputId"
    assert not [i for i, n in Counter(dom.ids).items() if n > 1]
