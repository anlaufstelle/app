"""Bereichs-Hubs „Datenschutz" und „Verwaltung" (Refs #1813, Konzept #1801 §3–§4).

Die Hubs sind reines Navigations-Chrom: **null URL-Umzüge**, **null neue
Rechte**, genau zwei neue Routen. Genau deshalb prüft dieser Guard nicht die
Zielseiten (die haben ihre eigenen Tests), sondern die vier Aussagen, die der
Umbau neu behauptet:

1. **Regel N2** — ein Hub ist sichtbar ⟺ mindestens einer seiner Reiter ist es.
   Die Regel wird in :func:`core.nav.section_gates` *berechnet*; hier steht,
   was sie je Rolle bedeutet — inklusive der Fachkraft, die durch einen eigenen
   offenen Löschantrag genau einen Reiter sieht (Konzept §3.3).
2. **Reiter-Rollenfilter** — eine ``LEAD_PLUS``-Seite darf keinen
   ``ADMIN_ONLY``-Reiter und keinen ``ADMIN_ONLY``-Link rendern. Das ist die
   Aussage, die ``test_authz_template_links.py`` breit prüft; hier punktuell
   und mit Begründung am Ort.
3. **Einzelreiter-Weiterleitung** — wer den Übersichts-Reiter nicht sieht,
   landet direkt auf seinem einzigen sichtbaren Reiter statt auf einer leeren
   Hülle (Muster ``RoleDashboardView``).
4. **Zwei-Ebenen-Aktivzustand** — Sidebar-Anker (``bg-accent-light``) und
   Reiter-Pill (``bg-accent text-white`` + ``aria-current="page"``) sind
   gleichzeitig aktiv, aber nie zwei Hubs gleichzeitig.

Dazu die 44-px-Mindest-Touchfläche der Pills (Refs #810) — über **alle**
Bereiche parametrisiert, damit das gemeinsame Partial die Hausregel nicht nur
dort einhält, wo gerade jemand hinschaut.
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse

from core import nav
from core.models.user import User
from core.models.workitem import DeletionRequest

SIDEBAR_ACTIVE = "bg-accent-light"
PILL_ACTIVE = "bg-accent text-white"

# Je Bereich eine Rolle, die ihn vollständig sieht, plus seine Landing-Route.
# Der Guard ``test_every_section_has_a_probe`` erzwingt, dass ein neuer Bereich
# hier eingetragen wird — sonst wäre die 44-px-Prüfung für ihn still blind.
SECTION_PROBES: dict[str, str] = {
    "system": "super_admin_user",
    "datenschutz": "admin_user",
    "verwaltung": "admin_user",
}


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------


def _nav_items(content: str) -> str:
    """Der Eintrags-Container der Desktop-Sidebar (Marker ``nav-items``, Refs #1813)."""
    start = content.index('data-testid="nav-items"')
    end = content.index("</div>", start)
    return content[start:end]


def _opening_anchor(content: str, testid: str) -> str:
    """Öffnender ``<a …>``-Tag mit diesem ``data-testid``."""
    idx = content.index(f'data-testid="{testid}"')
    return content[content.rindex("<a", 0, idx) : content.index(">", idx)]


def _pills(content: str) -> list[str]:
    """Alle Reiter-Pills der Seite als öffnende ``<a …>``-Tags."""
    pills: list[str] = []
    cursor = 0
    marker = 'data-testid="subnav-tab-'
    while (idx := content.find(marker, cursor)) != -1:
        pills.append(content[content.rindex("<a", 0, idx) : content.index(">", idx)])
        cursor = idx + len(marker)
    return pills


def _staff_with_pending_request(facility, user):
    DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=uuid.uuid4(),
        reason="Hub-Guard",
        requested_by=user,
        status=DeletionRequest.Status.PENDING,
    )
    return user


@pytest.fixture
def confirmer_staff(facility):
    """Fachkraft mit dem Recht „Löschbestätigung", ohne Leitungsrolle (Refs #1053).

    Der Fall, den eine handgeschriebene ``if not is_lead_or_admin``-Bedingung
    genauso träfe — den die aus den Reiter-Gates abgeleitete Variante aber
    automatisch mitnimmt, wenn sich Gates ändern.
    """
    user = User.objects.create_user(
        username="hub_confirmer",
        role=User.Role.STAFF,
        facility=facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


# ---------------------------------------------------------------------------
# Regel N2 — Hub sichtbar ⟺ mindestens ein Reiter sichtbar
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHubVisibilityRuleN2:
    """Je Rolle: welcher Hub steht in der Sidebar — und stimmt das mit der Regel überein?

    Geprüft wird beides, weil beides schiefgehen kann: die *Regel*
    (``nav.section_is_visible``, Datenebene) und ihre *Wirkung* (der gerenderte
    Sidebar-Anker). Ein Hub-Eintrag, den die Regel erlaubt, den aber niemand
    rendert, wäre genauso ein Befund wie umgekehrt.
    """

    def _sidebar(self, client, user, url_name):
        client.force_login(user)
        return _nav_items(client.get(reverse(url_name)).content.decode())

    def test_assistant_sees_neither_hub(self, client, assistant_user):
        sidebar = self._sidebar(client, assistant_user, "core:zeitstrom")
        assert 'data-testid="nav-datenschutz"' not in sidebar
        assert 'data-testid="nav-verwaltung"' not in sidebar
        assert not nav.section_is_visible(nav.DATENSCHUTZ, assistant_user)
        assert not nav.section_is_visible(nav.VERWALTUNG, assistant_user)

    def test_staff_without_own_request_sees_neither_hub(self, client, staff_user):
        sidebar = self._sidebar(client, staff_user, "core:zeitstrom")
        assert 'data-testid="nav-datenschutz"' not in sidebar
        assert 'data-testid="nav-verwaltung"' not in sidebar

    def test_staff_with_own_pending_request_sees_datenschutz_only(self, client, facility, staff_user):
        """Konzept §3.3: der Edge-Case löst sich ohne Sonderfall und ohne neues Recht."""
        _staff_with_pending_request(facility, staff_user)
        sidebar = self._sidebar(client, staff_user, "core:zeitstrom")
        assert 'data-testid="nav-datenschutz"' in sidebar
        assert 'data-testid="nav-verwaltung"' not in sidebar

    def test_lead_sees_datenschutz_not_verwaltung(self, client, lead_user):
        sidebar = self._sidebar(client, lead_user, "core:zeitstrom")
        assert 'data-testid="nav-datenschutz"' in sidebar
        assert 'data-testid="nav-verwaltung"' not in sidebar
        assert nav.section_is_visible(nav.DATENSCHUTZ, lead_user)
        assert not nav.section_is_visible(nav.VERWALTUNG, lead_user)

    def test_facility_admin_sees_both_hubs(self, client, admin_user):
        sidebar = self._sidebar(client, admin_user, "core:zeitstrom")
        assert 'data-testid="nav-datenschutz"' in sidebar
        assert 'data-testid="nav-verwaltung"' in sidebar

    def test_super_admin_sees_neither_hub(self, client, super_admin_user):
        """``facility=None`` — die Hubs sind einrichtungsgebunden (Matrix-Gegenprobe)."""
        sidebar = self._sidebar(client, super_admin_user, "core:system_dashboard")
        assert 'data-testid="nav-datenschutz"' not in sidebar
        assert 'data-testid="nav-verwaltung"' not in sidebar
        assert not nav.section_is_visible(nav.DATENSCHUTZ, super_admin_user)
        assert not nav.section_is_visible(nav.VERWALTUNG, super_admin_user)


# ---------------------------------------------------------------------------
# Reiter-Rollenfilter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHubTabRoleFilter:
    """Der Filter sitzt in der Registry, nicht im Template.

    Damit kann eine ``LEAD_PLUS``-Seite strukturell keinen ``ADMIN_ONLY``-Reiter
    rendern — es gibt keine ``{% if user.is_facility_admin %}``-Stelle, die man
    vergessen könnte.
    """

    def test_lead_datenschutz_subnav_has_no_admin_only_tabs(self, client, lead_user):
        client.force_login(lead_user)
        content = client.get(reverse("core:datenschutz_uebersicht")).content.decode()

        assert 'data-testid="subnav-tab-deletion-requests"' in content
        assert 'data-testid="subnav-tab-rectification-requests"' in content
        assert 'data-testid="subnav-tab-retention"' in content
        assert 'data-testid="subnav-tab-audit"' not in content
        assert 'data-testid="subnav-tab-dsgvo"' not in content

    def test_facility_admin_datenschutz_subnav_has_six_tabs(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:datenschutz_uebersicht")).content.decode()

        assert len(_pills(content)) == 6

    def test_verwaltung_subnav_has_four_tabs_for_admin(self, client, admin_user):
        """F1 (Pakete/Export) ist Folgeticket — der Hub startet mit vier Reitern."""
        client.force_login(admin_user)
        content = client.get(reverse("core:verwaltung_uebersicht")).content.decode()

        assert len(_pills(content)) == 4

    def test_lead_overview_body_links_no_admin_only_route(self, client, lead_user):
        """Ergänzt ``test_authz_template_links`` punktuell — der Kennzahlen-Service
        filtert selbst, die Leitung bekommt die beiden ADMIN_ONLY-Zeilen gar nicht
        erst in die Liste (und ihre Queries laufen für sie nicht)."""
        client.force_login(lead_user)
        content = client.get(reverse("core:datenschutz_uebersicht")).content.decode()

        for admin_only in (reverse("core:audit_log"), reverse("core:dsgvo_package")):
            assert f'href="{admin_only}"' not in content, f"ADMIN_ONLY-Link {admin_only} auf einer LEAD_PLUS-Seite"


# ---------------------------------------------------------------------------
# Einzelreiter-Weiterleitung
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSingleTabRedirect:
    """Wer den Übersichts-Reiter nicht sieht, bekommt keine leere Hülle."""

    def test_requesting_staff_is_redirected_to_deletion_request_list(self, client, facility, staff_user):
        _staff_with_pending_request(facility, staff_user)
        client.force_login(staff_user)
        response = client.get(reverse("core:datenschutz_uebersicht"))

        assert response.status_code == 302
        assert response.url == reverse("core:deletion_request_list")

    def test_confirmer_without_lead_role_is_redirected_to_deletion_request_list(self, client, confirmer_staff):
        client.force_login(confirmer_staff)
        response = client.get(reverse("core:datenschutz_uebersicht"))

        assert response.status_code == 302
        assert response.url == reverse("core:deletion_request_list")

    def test_lead_gets_the_overview_itself(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:datenschutz_uebersicht"))

        assert response.status_code == 200

    def test_staff_without_any_visible_tab_is_forbidden(self, client, staff_user):
        """Matrix-Gegenprobe: kein Reiter ⇒ 403, kein Redirect ins Leere."""
        client.force_login(staff_user)
        assert client.get(reverse("core:datenschutz_uebersicht")).status_code == 403

    def test_super_admin_is_forbidden_on_both_hubs(self, client, super_admin_user):
        client.force_login(super_admin_user)
        assert client.get(reverse("core:datenschutz_uebersicht")).status_code == 403
        assert client.get(reverse("core:verwaltung_uebersicht")).status_code == 403


# ---------------------------------------------------------------------------
# Barrierefreiheit der Reiterzeile
# ---------------------------------------------------------------------------


def test_every_section_has_a_probe():
    """Ein neuer Bereich ohne Probe würde die 44-px-Prüfung still übergehen."""
    assert {section.key for section in nav.SECTIONS} == set(SECTION_PROBES), (
        "SECTION_PROBES und nav.SECTIONS sind auseinandergelaufen: "
        f"{sorted({s.key for s in nav.SECTIONS} ^ set(SECTION_PROBES))}"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("section", nav.SECTIONS, ids=lambda s: s.key)
class TestSubnavAccessibility:
    """Refs #810: 44 px Mindest-Touchfläche — im gemeinsamen Partial, für alle Bereiche.

    Das Vorbild ``core/system/_subnav.html`` lag mit ``px-3 py-1.5`` bei
    ``text-[13px]`` rechnerisch bei ~32 px. Wird ein Muster vervielfältigt,
    vervielfältigt sich sein Verstoß — deshalb liegt der Fix im Partial und die
    Prüfung über allen Bereichen.
    """

    def _content(self, client, request, section):
        user = request.getfixturevalue(SECTION_PROBES[section.key])
        client.force_login(user)
        return client.get(reverse(section.tabs[0].url_name)).content.decode()

    def test_all_pills_carry_min_h_44px(self, client, request, section):
        pills = _pills(self._content(client, request, section))
        assert pills, f"Bereich {section.key!r} rendert keine Reiter."
        for pill in pills:
            assert "min-h-[44px]" in pill, f"Reiter ohne 44-px-Touchfläche in {section.key!r}: {pill}"
            assert "inline-flex" in pill and "items-center" in pill, (
                f"min-h ohne Flex-Zentrierung wirkt nicht auf den Text in {section.key!r}: {pill}"
            )

    def test_active_pill_has_aria_current_page(self, client, request, section):
        pills = _pills(self._content(client, request, section))
        active = [pill for pill in pills if PILL_ACTIVE in pill]
        assert len(active) == 1, f"Bereich {section.key!r}: genau ein Reiter muss aktiv sein, sind {len(active)}."
        assert 'aria-current="page"' in active[0]

    def test_inactive_pills_have_no_aria_current(self, client, request, section):
        pills = _pills(self._content(client, request, section))
        for pill in pills:
            if PILL_ACTIVE in pill:
                continue
            assert "aria-current" not in pill, f"Inaktiver Reiter mit aria-current in {section.key!r}: {pill}"


# ---------------------------------------------------------------------------
# Zwei-Ebenen-Aktivzustand
# ---------------------------------------------------------------------------

_DATENSCHUTZ_TAB_ROUTES = [tab.url_name for tab in nav.DATENSCHUTZ.tabs]
_VERWALTUNG_TAB_ROUTES = [tab.url_name for tab in nav.VERWALTUNG.tabs]


@pytest.mark.django_db
class TestHubActiveState:
    """Die Ortsanzeige darf innerhalb eines Bereichs nie erlöschen — und nie doppelt leuchten."""

    @pytest.mark.parametrize("url_name", _DATENSCHUTZ_TAB_ROUTES)
    def test_datenschutz_hub_active_on_every_member_route(self, client, admin_user, url_name):
        client.force_login(admin_user)
        content = client.get(reverse(url_name)).content.decode()

        assert SIDEBAR_ACTIVE in _opening_anchor(content, "nav-datenschutz"), (
            f"Hub „Datenschutz“ muss auf {url_name} aktiv sein"
        )

    @pytest.mark.parametrize("url_name", _VERWALTUNG_TAB_ROUTES)
    def test_verwaltung_hub_active_on_every_member_route(self, client, admin_user, url_name):
        client.force_login(admin_user)
        content = client.get(reverse(url_name)).content.decode()

        assert SIDEBAR_ACTIVE in _opening_anchor(content, "nav-verwaltung"), (
            f"Hub „Verwaltung“ muss auf {url_name} aktiv sein"
        )

    @pytest.mark.parametrize("url_name", _DATENSCHUTZ_TAB_ROUTES + _VERWALTUNG_TAB_ROUTES)
    def test_never_both_hubs_active_simultaneously(self, client, admin_user, url_name):
        """Die Substring-Regressionsklasse aus ``test_navigation_active_state.py``,
        eine Ebene höher: zwei gleichzeitig markierte Bereiche sind keine Ortsanzeige."""
        client.force_login(admin_user)
        content = client.get(reverse(url_name)).content.decode()

        active = [
            testid
            for testid in ("nav-datenschutz", "nav-verwaltung")
            if SIDEBAR_ACTIVE in _opening_anchor(content, testid)
        ]
        assert len(active) == 1, f"{url_name}: genau ein Hub darf aktiv sein, aktiv sind {active}."

    def test_hub_and_tab_are_active_together_on_a_member_page(self, client, admin_user):
        """Zwei-Ebenen-Semantik: Sidebar-Marker und Reiter-Marker sind verschieden
        (``bg-accent-light`` vs. ``bg-accent text-white``) und leuchten zusammen."""
        client.force_login(admin_user)
        content = client.get(reverse("core:audit_log")).content.decode()

        assert SIDEBAR_ACTIVE in _opening_anchor(content, "nav-datenschutz")
        audit_pill = _opening_anchor(content, "subnav-tab-audit")
        assert PILL_ACTIVE in audit_pill
        assert 'aria-current="page"' in audit_pill
