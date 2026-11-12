"""Rendered pages leave room for the quick-exit button on every width (Refs #2660).

The button is a 0 px high bar whose 44 px button reaches y = 4..48 px into the
page, at the right edge. Two kinds of page elements could end up underneath it:

* the content below the layout wrapper (title rows with action buttons, back
  links, form fields) — covered structurally by one top padding on the content
  wrapper at every width, instead of a rule per template, and
* full-width notices that start at y = 0 (offline status, demo notice, 2FA
  reminder, the offline navigation) — they keep the right edge free.

These tests read the classes off RENDERED pages (the dashboard, a list with a
"new" action, configuration lists, the offline shell) and compute the clearance
from the Tailwind spacing scale (1 unit = 4 px). The pixel proof in a browser is
``src/tests/e2e/test_quick_exit_geometry.py``.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from django.template.loader import render_to_string
from django.test import override_settings
from django.urls import reverse

# Geometry of the button (see base.html / offline_base.html): ``mt-1`` + 44 px.
BUTTON_BOTTOM_PX = 4 + 44
# Footprint measured from the right edge of the viewport, worst case with a wide
# fallback font: side padding + border + padding + icon + gap + label.
MOBILE_FOOTPRINT_PX = 16 + 120
DESKTOP_FOOTPRINT_PX = 32 + 176

_VOID = {"meta", "link", "input", "img", "br", "hr", "source", "path"}


class _Page(HTMLParser):
    """Record the parent of ``#flash-messages`` and what lives inside it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.by_id = {}
        self.wrapper_depth = None
        self.wrapper = None
        self.inside = []  # (tag, attrs) of every element inside the content wrapper

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.by_id[attributes["id"]] = attributes
        if attributes.get("id") == "flash-messages" and self.stack:
            self.wrapper_depth = len(self.stack) - 1
            self.wrapper = self.stack[-1][1]
        elif self.wrapper_depth is not None and len(self.stack) > self.wrapper_depth:
            self.inside.append((tag, attributes))
        if tag not in _VOID:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        while self.stack:
            if self.stack.pop()[0] == tag:
                break
        if self.wrapper_depth is not None and len(self.stack) <= self.wrapper_depth:
            self.wrapper_depth = None


def _parse(html):
    page = _Page()
    page.feed(html)
    return page


def _classes(attributes):
    return set((attributes.get("class") or "").split())


def _px(classes, prefix, *, variant=""):
    """Spacing in px of the ``<variant>:<prefix>-N`` utility, or ``None``."""
    pattern = re.compile(rf"^{re.escape(variant + ':' if variant else '')}{re.escape(prefix)}-(\d+(?:\.\d+)?)$")
    for name in classes:
        match = pattern.match(name)
        if match:
            return float(match.group(1)) * 4
    return None


def _right_reserve(classes):
    """Free space on the right edge in px: ``(mobile, desktop)``."""
    mobile = _px(classes, "pr")
    desktop = _px(classes, "pr", variant="md")
    return mobile or 0, desktop if desktop is not None else (mobile or 0)


@pytest.mark.django_db
class TestContentWrapperStartsBelowTheButton:
    """Every content page starts below the button, whatever its first row is."""

    @pytest.mark.parametrize(
        ("url_name", "user_fixture"),
        [
            ("core:zeitstrom", "staff_user"),
            ("core:client_list", "staff_user"),
            ("core:dashboard", "lead_user"),
            ("core:config_doctype_list", "admin_user"),
            ("core:config_fieldtemplate_list", "admin_user"),
            ("core:import_list", "admin_user"),
            ("core:user_access_list", "admin_user"),
        ],
    )
    def test_the_content_wrapper_clears_the_button_at_every_width(
        self, client, request, settings_obj, url_name, user_fixture
    ):
        client.force_login(request.getfixturevalue(user_fixture))
        response = client.get(reverse(url_name), follow=True)
        assert response.status_code == 200
        page = _parse(response.content.decode())
        assert page.wrapper is not None, "#flash-messages must sit directly in the content wrapper"
        classes = _classes(page.wrapper)

        top = _px(classes, "pt")
        assert top is not None and top >= BUTTON_BOTTOM_PX + 4, "the wrapper must start below the button"
        # No smaller top padding from md upwards, and no per-template right inset rule.
        md_top = _px(classes, "pt", variant="md")
        assert md_top is None or md_top >= BUTTON_BOTTOM_PX + 4
        assert not any(name.startswith("py-") or name.startswith("md:py-") for name in classes)
        assert not any("flash-messages" in name for name in classes), "no selector that depends on the first row"

        assert any(tag == "h1" for tag, _ in page.inside), f"{url_name}: the title must live inside the wrapper"

    def test_actions_of_the_title_row_live_inside_the_wrapper(self, client, staff_user, settings_obj):
        """The dashboard's "new person" action and a list's "new" button are not above the wrapper."""
        client.force_login(staff_user)
        html = client.get(reverse("core:zeitstrom")).content.decode()
        page = _parse(html)
        links = [attrs.get("href") for tag, attrs in page.inside if tag == "a"]
        assert reverse("core:client_create") in links


@pytest.mark.django_db
class TestFullWidthNoticesKeepTheRightEdgeFree:
    """Elements that start at y = 0 cannot move below the button: they leave it room."""

    def _assert_reserve(self, classes):
        mobile, desktop = _right_reserve(classes)
        assert mobile >= MOBILE_FOOTPRINT_PX + 4, f"mobile reserve {mobile}px < footprint {MOBILE_FOOTPRINT_PX}px"
        assert desktop >= DESKTOP_FOOTPRINT_PX + 4, f"desktop reserve {desktop}px < footprint {DESKTOP_FOOTPRINT_PX}px"

    def test_online_offline_status_bar(self, client, staff_user, settings_obj):
        client.force_login(staff_user)
        page = _parse(client.get(reverse("core:zeitstrom")).content.decode())
        self._assert_reserve(_classes(page.by_id["offline-status"]))

    def test_offline_shell_status_bar_and_navigation(self, client, settings_obj):
        html = client.get(reverse("core:offline_client_shell")).content.decode()
        page = _parse(html)
        self._assert_reserve(_classes(page.by_id["offline-status"]))
        nav_inner = re.search(r'<nav[^>]*data-testid="offline-nav".*?<div class="([^"]+)"', html, re.S)
        assert nav_inner, "the offline shell must render its navigation"
        self._assert_reserve(set(nav_inner.group(1).split()))
        # The content below the navigation (>= 60 px high) starts beneath the button.
        assert "min-h-[44px]" in html[nav_inner.start() : nav_inner.end() + 600]
        assert "py-2" in nav_inner.group(1)

    def test_demo_notice(self, client, staff_user, settings_obj):
        client.force_login(staff_user)
        with override_settings(DEMO_MODE=True):
            page = _parse(client.get(reverse("core:zeitstrom")).content.decode())
        self._assert_reserve(_classes(page.by_id["demo-banner"]))

    def test_two_factor_setup_reminder(self):
        html = render_to_string(
            "partials/_mfa_setup_reminder.html",
            {"mfa_setup_show_banner": True, "mfa_setup_days_remaining": 3},
        )
        match = re.search(r'<div class="([^"]+)" data-testid="mfa-setup-reminder"', html)
        assert match, "the reminder must render"
        self._assert_reserve(set(match.group(1).split()))
