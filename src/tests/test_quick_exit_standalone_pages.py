"""Standalone pages carry the quick-exit button wherever a person can see them (Refs #2660).

``base.html`` and ``offline_base.html`` render the button for every page that
extends them. A handful of full documents do not extend a layout: some are seen
by a signed-in or offline-working person (the forced password change, the
two-factor setup, the offline fallback, the notices for a signed-in session and
for a locked facility, the demo area picker), others only before a sign-in or
without any session. This module classifies EVERY such document, so a new one
cannot appear unnoticed, and checks the rendered pages of the first group:

* the button is the first focusable element after the skip link,
* the privacy screen and ``quick-exit.js`` are present (an armed privacy screen
  without its overlay would leave a hidden page without a way back),
* the page keeps clear of the button (top padding), and
* the sign-out on exit can work: a CSRF token and the logout address (the
  session-less offline fallback gets neither, like the offline shells).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.test import override_settings
from django.urls import reverse

from tests.test_quick_exit import _button_markup

TEMPLATES = Path(settings.BASE_DIR) / "templates"

#: Standalone documents that carry the button (they extend no layout).
WITH_BUTTON = {
    "offline.html",
    "auth/mfa_setup.html",
    "auth/password_change.html",
    "auth/signed_in_link_notice.html",
    "auth/demo_scenario_select.html",
    "auth/facility_archived.html",
    "auth/facility_missing.html",
}

#: Standalone documents without the button, each with the reason.
WITHOUT_BUTTON = {
    "500.html": "error page rendered without session, context or scripts",
    "503.html": "maintenance page rendered without session, context or scripts",
    "auth/login.html": "before sign-in",
    "auth/mfa_login.html": "second factor of the sign-in; nothing of the application is visible yet",
    "auth/lockout_recovery_backup_code.html": "account recovery before sign-in",
    "auth/lockout_recovery_confirm.html": "account recovery before sign-in",
    "auth/lockout_recovery_invalid.html": "account recovery before sign-in",
    "auth/lockout_recovery_request.html": "account recovery before sign-in",
    "auth/lockout_recovery_sent.html": "account recovery before sign-in",
    "registration/password_reset_complete.html": "password reset before sign-in",
    "registration/password_reset_confirm.html": "password reset before sign-in",
    "registration/password_reset_done.html": "password reset before sign-in",
    "registration/password_reset_form.html": "password reset before sign-in",
    "core/export/client_data_pdf.html": "source of a server-side PDF, never shown in a browser",
    "core/export/dataset_pdf.html": "source of a server-side PDF, never shown in a browser",
    "core/export/sample_report_1_pdf.html": "source of a server-side PDF, never shown in a browser",
}

#: Layouts: covered by ``test_quick_exit.py`` (online and offline shell).
LAYOUTS = {"base.html", "offline_base.html"}

PARTIAL = "partials/_quick_exit_standalone.html"

_VOID = {"meta", "link", "input", "img", "br", "hr", "source", "path"}
_FOCUSABLE = {"a", "button", "input", "select", "textarea"}


def _standalone_documents():
    """Every template that is a complete document and extends no layout."""
    found = set()
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"<html[\s>]", text, re.I) and "{% extends" not in text:
            found.add(path.relative_to(TEMPLATES).as_posix())
    return found - LAYOUTS


class _Document(HTMLParser):
    """Focusable elements in document order, ids, the body and head facts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.focusables = []
        self.ids = {}
        self.body = {}
        self.scripts = []
        self.metas = []
        self.button_count = 0
        self.button_ancestor_classes = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        hidden_ancestor = any("hidden" in a for _, a in self.stack)
        if attributes.get("id"):
            self.ids[attributes["id"]] = attributes
        if attributes.get("id") == "quick-exit-button":
            self.button_count += 1
            self.button_ancestor_classes = [set(a.get("class", "").split()) for _, a in self.stack]
        if tag == "body":
            self.body = attributes
        if tag == "script" and attributes.get("src"):
            self.scripts.append(attributes["src"])
        if tag == "meta":
            self.metas.append(attributes)
        if tag in _FOCUSABLE and "hidden" not in attributes and not hidden_ancestor:
            if not (tag == "input" and attributes.get("type") == "hidden"):
                self.focusables.append((tag, attributes))
        if tag not in _VOID:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        while self.stack:
            if self.stack.pop()[0] == tag:
                break


def _parse(html):
    document = _Document()
    document.feed(html)
    return document


def test_every_standalone_document_is_classified():
    """A new full document must be put into one of the two lists, with a reason."""
    discovered = _standalone_documents()
    classified = WITH_BUTTON | set(WITHOUT_BUTTON)
    assert discovered == classified, {
        "unclassified": sorted(discovered - classified),
        "stale": sorted(classified - discovered),
    }
    assert not WITH_BUTTON & set(WITHOUT_BUTTON)


@pytest.mark.parametrize("name", sorted(WITH_BUTTON))
def test_the_template_includes_the_button_right_after_the_skip_link(name):
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    skip = text.index('href="#main-content"')
    main = text.index("<main")
    if name == "offline.html":
        # Session-less shell with inline CSS: the markup is spelled out in the page.
        marker = '<button type="button" id="quick-exit-button"'
    else:
        marker = '{% include "' + PARTIAL + '"'
    assert text.count(marker) == 1, f"{name} must include the button exactly once"
    assert skip < text.index(marker) < main, f"{name}: the button must follow the skip link and precede <main>"


@pytest.mark.parametrize("name", sorted(WITHOUT_BUTTON))
def test_pages_without_the_button_do_not_carry_it(name):
    assert "quick-exit" not in (TEMPLATES / name).read_text(encoding="utf-8")


@pytest.fixture
def rendered(client, staff_user, facility_less_admin, settings_obj):
    """Rendered HTML of each standalone page that carries the button."""

    def render(name):
        if name == "offline.html":
            return client.get(reverse("offline_fallback")).content.decode()
        if name in {"auth/facility_archived.html", "auth/facility_missing.html"}:
            # The middleware renders these without a request (no facility data may be read).
            return render_to_string(name, {"csrf_token": "token"})
        client.force_login(staff_user)
        if name == "auth/password_change.html":
            return client.get(reverse("password_change")).content.decode()
        if name == "auth/mfa_setup.html":
            return client.get(reverse("mfa_setup")).content.decode()
        if name == "auth/demo_scenario_select.html":
            with override_settings(DEMO_MODE=True):
                return client.get(reverse("demo_scenario_select")).content.decode()
        if name == "auth/signed_in_link_notice.html":
            return client.get("/invite/aaaa/bbbb-cccc/").content.decode()
        raise AssertionError(name)

    return render


@pytest.mark.django_db
@pytest.mark.parametrize("name", sorted(WITH_BUTTON))
class TestRenderedStandalonePages:
    def test_the_button_is_the_first_focusable_after_the_skip_link(self, rendered, name):
        document = _parse(rendered(name))
        assert document.button_count == 1
        (first_tag, first), (second_tag, second) = document.focusables[:2]
        assert first.get("href") == "#main-content", "the skip link comes first"
        assert second.get("id") == "quick-exit-button" and second_tag == "button"

    def test_the_button_is_the_same_markup_as_in_the_layouts(self, rendered, client, staff_user, name):
        html = rendered(name)
        client.force_login(staff_user)
        reference = client.get(reverse("core:zeitstrom")).content.decode()

        # The session-less fallback styles the button with inline CSS: compare without classes.
        def bare(markup):
            return re.sub(r' class="[^"]*"', "", markup)

        assert bare(_button_markup(html)) == bare(_button_markup(reference))

    def test_privacy_screen_and_script_are_present(self, rendered, name):
        document = _parse(rendered(name))
        assert "hidden" in document.ids["privacy-screen"]
        assert "privacy-screen-resume" in document.ids
        assert any(src.endswith("js/quick-exit.js") for src in document.scripts)

    def test_the_page_keeps_clear_of_the_button(self, rendered, name):
        html = rendered(name)
        if name == "offline.html":
            padding = re.search(r"body\s*\{[^}]*padding:\s*([\d.]+)rem", html, re.S)
            assert padding and float(padding.group(1)) * 16 >= 56
        else:
            assert "pt-14" in _parse(html).body["class"].split()

    def test_sign_out_on_exit_can_work(self, rendered, name):
        document = _parse(rendered(name))
        if name == "offline.html":
            # Static, session-less document like the offline shells: nothing to sign out of.
            assert "data-quick-exit-logout-url" not in document.body
            return
        assert document.body.get("data-quick-exit-logout-url") == "/logout/"
        assert any(m.get("name") == "csrf-token" for m in document.metas)
        assert any(src.endswith("js/csrf.js") for src in document.scripts)

    def test_the_geometry_spec_can_find_the_wrapper_of_the_button(self, rendered, name):
        """The scrolled-state measurement locates the bar with ``closest(<selector>)``.

        A page whose bar carries none of those classes would crash the measurement
        instead of being measured, so the classes of every rendered page are checked
        against the selector the spec uses.
        """
        spec = (Path(settings.BASE_DIR) / "tests" / "e2e" / "test_quick_exit_geometry.py").read_text(encoding="utf-8")
        match = re.search(r'button\.closest\("([^"]+)"\)', spec)
        assert match, "the geometry spec no longer resolves the wrapper with closest()"
        wrapper_classes = {part.strip().lstrip(".") for part in match.group(1).split(",")}
        ancestors = _parse(rendered(name)).button_ancestor_classes
        assert any(classes & wrapper_classes for classes in ancestors), (
            f"{name}: no ancestor of the button carries one of {sorted(wrapper_classes)}"
        )
