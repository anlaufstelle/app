"""Sprachumschalter im mobilen „Mehr"-Menü (Refs #1813, Konzept #1801 §5.3).

Die Sprachumschaltung lag ausschließlich im Footer der Desktop-Sidebar — und
die ist unter ``md`` ausgeblendet. Auf dem Telefon ließ sich die Sprache damit
**gar nicht** wechseln; das ist keine Verfeinerung der Navigation, sondern eine
echte Sperre. Der Fix ist dieselbe ``set_language``-POST-Form wie im Desktop-
Footer, als Zeile über „Abmelden".

Zwei Dinge prüft dieser Guard, die im Markup leicht kaputtgehen:

* die Form ist **für jede Rolle** da (sie hängt an keinem Gate — Sprache ist
  Konto-Chrom, kein Bereich), inklusive der Systemrolle, die weder Suche noch
  Facility-Einträge sieht;
* ein POST daraus wechselt die Sprache **wirklich** (Django validiert ``next``
  mit ``url_has_allowed_host_and_scheme``; ein fehlendes oder falsch benanntes
  Feld wäre still — die Antwort käme trotzdem als 302 zurück).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from .test_navigation_mobile_more import _more_menu

MOBILE_ROLES = ("assistant_user", "staff_user", "lead_user", "admin_user", "super_admin_user")
LANDING = {"super_admin_user": "core:system_dashboard"}


def _menu(client, user, url_name: str) -> str:
    client.force_login(user)
    return _more_menu(client.get(reverse(url_name)).content.decode())


@pytest.mark.django_db
@pytest.mark.parametrize("role", MOBILE_ROLES)
class TestLanguageFormIsPresentForEveryRole:
    def test_form_posts_to_set_language(self, client, request, role):
        menu = _menu(client, request.getfixturevalue(role), LANDING.get(role, "core:zeitstrom"))
        assert f'action="{reverse("set_language")}"' in menu, f"{role}: keine Sprachumschalter-Form im Mehr-Menü"
        assert 'method="post"' in menu

    def test_both_languages_are_offered(self, client, request, role):
        menu = _menu(client, request.getfixturevalue(role), LANDING.get(role, "core:zeitstrom"))
        assert 'data-testid="mobile-nav-language-de"' in menu
        assert 'data-testid="mobile-nav-language-en"' in menu

    def test_buttons_meet_the_44px_touch_target(self, client, request, role):
        """Refs #810: die Desktop-Vorlage ist ``py-px`` — als Fingerziel unbrauchbar."""
        menu = _menu(client, request.getfixturevalue(role), LANDING.get(role, "core:zeitstrom"))
        for lang in ("de", "en"):
            idx = menu.index(f'data-testid="mobile-nav-language-{lang}"')
            tag = menu[menu.rindex("<button", 0, idx) : menu.index(">", idx)]
            assert "min-h-[44px]" in tag, f"Sprach-Schaltfläche {lang} ohne 44-px-Touchfläche: {tag}"


@pytest.mark.django_db
class TestLanguageFormMechanics:
    def test_next_field_carries_the_current_path(self, client, staff_user):
        """Ohne ``next`` landet die Nutzerin nach dem Wechsel auf ``/`` statt dort,
        wo sie gerade war — bei einer Umschaltung mitten im Formular ein Datenverlust."""
        client.force_login(staff_user)
        menu = _more_menu(client.get(reverse("core:client_list")).content.decode())

        assert f'<input type="hidden" name="next" value="{reverse("core:client_list")}">' in menu

    def test_language_switch_from_the_mobile_menu_changes_the_locale(self, client, staff_user):
        """Der Wechsel wirkt: die nächste Seite kommt auf Englisch."""
        client.force_login(staff_user)

        response = client.post(
            reverse("set_language"),
            {"language": "en", "next": reverse("core:client_list")},
        )
        assert response.status_code == 302

        content = client.get(reverse("core:client_list")).content.decode()
        assert 'data-testid="mobile-nav-language-en"' in content
        assert "Files" in content, "Locale-Wechsel ist nicht angekommen (msgid „Dateien“ → „Files“)"
