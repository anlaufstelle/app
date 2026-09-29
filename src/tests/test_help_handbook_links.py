"""In-app help links to the published user guide and FAQ (Refs #1904).

The help overview already exists (``/hilfe/``, footer link); it lacked a way
to the full user guide and the FAQ, both shipped as Markdown files in the
public repository. The links must follow ``settings.SOURCE_CODE_URL`` so a
fork automatically points at its own mirror, exactly like the existing
license link in the footer.
"""

from html.parser import HTMLParser

import pytest
from django.test import override_settings
from django.urls import reverse

# Every role that can reach the login-required help overview (Refs #1904):
# derived from the fixtures used elsewhere for the same view family
# (test_help_pages.py) rather than guessed.
ROLE_FIXTURES = (
    "super_admin_user",
    "admin_user",
    "lead_user",
    "staff_user",
    "assistant_user",
)


class _LinkCollector(HTMLParser):
    """Collects every ``<a>`` tag's attributes from a rendered page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs))


def _links(body: str) -> list[dict]:
    collector = _LinkCollector()
    collector.feed(body)
    return collector.links


def _link_to(body: str, href: str) -> dict:
    matches = [link for link in _links(body) if link.get("href") == href]
    assert len(matches) == 1, f"expected exactly one link to {href!r}, found {len(matches)}"
    return matches[0]


@pytest.mark.django_db
class TestHelpOverviewLinksHandbookAndFaq:
    @pytest.mark.parametrize("user_fixture", ROLE_FIXTURES)
    def test_overview_links_default_repository_docs(self, client, request, user_fixture):
        user = request.getfixturevalue(user_fixture)
        client.force_login(user)

        body = client.get(reverse("core:help_overview")).content.decode()

        _link_to(body, "https://github.com/anlaufstelle/app/blob/main/docs/user-guide.md")
        _link_to(body, "https://github.com/anlaufstelle/app/blob/main/docs/faq.md")

    @pytest.mark.parametrize("user_fixture", ROLE_FIXTURES)
    def test_overview_links_follow_fork_source_code_url(self, client, request, user_fixture):
        user = request.getfixturevalue(user_fixture)
        client.force_login(user)

        with override_settings(SOURCE_CODE_URL="https://example.org/fork"):
            body = client.get(reverse("core:help_overview")).content.decode()

        _link_to(body, "https://example.org/fork/blob/main/docs/user-guide.md")
        _link_to(body, "https://example.org/fork/blob/main/docs/faq.md")

    def test_overview_links_open_in_a_new_tab_with_noopener(self, client, staff_user):
        client.force_login(staff_user)

        body = client.get(reverse("core:help_overview")).content.decode()

        for href in (
            "https://github.com/anlaufstelle/app/blob/main/docs/user-guide.md",
            "https://github.com/anlaufstelle/app/blob/main/docs/faq.md",
        ):
            link = _link_to(body, href)
            assert link.get("target") == "_blank"
            assert "noopener" in (link.get("rel") or "")

    def test_overview_uses_english_user_guide_for_english_ui(self, client, staff_user):
        staff_user.preferred_language = "en"
        staff_user.save(update_fields=["preferred_language"])
        client.force_login(staff_user)

        body = client.get(reverse("core:help_overview")).content.decode()

        _link_to(body, "https://github.com/anlaufstelle/app/blob/main/docs/en/user-guide.md")
        # The FAQ has no English translation yet — the link stays on the
        # German source document rather than a 404.
        _link_to(body, "https://github.com/anlaufstelle/app/blob/main/docs/faq.md")

    def test_anonymous_request_redirects_as_before(self, client):
        response = client.get(reverse("core:help_overview"))

        assert response.status_code == 302
        assert response.url.startswith(f"{reverse('login')}?next=")

    def test_password_only_session_with_pending_mfa_does_not_see_the_page(self, client, staff_user):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        client.force_login(staff_user)

        response = client.get(reverse("core:help_overview"))

        assert response.status_code == 302
        assert response.url.startswith(f"{reverse('mfa_verify')}?next=")
