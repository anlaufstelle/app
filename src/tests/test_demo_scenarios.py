"""Regressionen fuer die Demo-Bereichsauswahl + den Bereichswechsel (Refs #2285).

``core.seed.scenarios`` (#2279) ist zum Zeitpunkt dieser Aenderung noch
nicht gemerged — ``core.views.demo_scenarios`` importiert den Katalog daher
lazy und faellt ohne ihn auf einen leeren Katalog zurueck (s. dortiger
Docstring). Diese Tests patchen ``scenario_catalog``/``_lookup_scenario``
direkt mit einem kleinen, lokalen Test-Double, das die von F-2 DOKUMENTIERTE
Schnittstelle (``key``/``display_name``/``description``/``package``/
``facility_name``/``demo_roles``/``intro_hint``/``solo``) nachbildet — KEINE
Kopie des echten Katalogs (bewusst keine 1:1-Kopie, um Test und
Produktivkatalog nicht zu koppeln), nur seine Form fuer den Test.
"""

from types import SimpleNamespace

import pytest
from django.urls import reverse

from core.demo import DEMO_PASSWORD
from core.models import Facility, User
from core.views import demo_scenarios as views


def _scenario(key, *, facility_name, demo_roles, solo=False):
    return SimpleNamespace(
        key=key,
        display_name=f"Anzeigename {key}",
        description=f"Beschreibung {key}",
        package="default",
        facility_name=facility_name,
        demo_roles=demo_roles,
        intro_hint=f"Hinweis {key}",
        solo=solo,
    )


TEAM_ROLES = (User.Role.FACILITY_ADMIN, User.Role.LEAD, User.Role.STAFF, User.Role.ASSISTANT)
SOLO_ROLES = (User.Role.STAFF, User.Role.FACILITY_ADMIN)


@pytest.fixture
def demo_mode(settings):
    settings.DEMO_MODE = True
    return settings


@pytest.fixture
def kontaktladen_facility(organization):
    return Facility.objects.create(organization=organization, name="Kontaktladen Wärmestube")


@pytest.fixture
def kontaktladen_staff(kontaktladen_facility):
    user = User.objects.create_user(
        username="kl_staff",
        role=User.Role.STAFF,
        facility=kontaktladen_facility,
        is_staff=True,
    )
    user.set_password(DEMO_PASSWORD)
    user.save()
    return user


@pytest.fixture
def kontaktladen_admin(kontaktladen_facility):
    user = User.objects.create_user(
        username="kl_admin",
        role=User.Role.FACILITY_ADMIN,
        facility=kontaktladen_facility,
        is_staff=True,
    )
    user.set_password(DEMO_PASSWORD)
    user.save()
    return user


@pytest.fixture
def solo_facility(organization):
    return Facility.objects.create(organization=organization, name="Beratungspraxis Lindenhof")


@pytest.fixture
def solo_staff(solo_facility):
    user = User.objects.create_user(
        username="solo_staff",
        role=User.Role.STAFF,
        facility=solo_facility,
        is_staff=True,
    )
    user.set_password(DEMO_PASSWORD)
    user.save()
    return user


@pytest.fixture
def catalog(kontaktladen_facility, solo_facility):
    return (
        _scenario("kontaktladen", facility_name=kontaktladen_facility.name, demo_roles=TEAM_ROLES),
        _scenario("solo", facility_name=solo_facility.name, demo_roles=SOLO_ROLES, solo=True),
    )


@pytest.fixture
def patched_catalog(monkeypatch, catalog):
    """``scenario_catalog``/``_lookup_scenario`` gegen das Test-Double."""
    monkeypatch.setattr(views, "scenario_catalog", lambda: catalog)

    def _lookup(key):
        return next((s for s in catalog if s.key == key), None)

    monkeypatch.setattr(views, "_lookup_scenario", _lookup)
    return catalog


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Produktionsmodus: ohne DEMO_MODE existiert der Einstieg faktisch nicht.
# ---------------------------------------------------------------------------


class TestDemoModeGating:
    def test_select_view_404_without_demo_mode(self, client):
        assert client.get(reverse("demo_scenario_select")).status_code == 404

    def test_start_view_404_without_demo_mode(self, client):
        response = client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        assert response.status_code == 404

    def test_start_view_get_404_without_demo_mode(self, client):
        assert client.get(reverse("demo_scenario_start")).status_code == 404

    @pytest.mark.parametrize("url_name", ["demo_scenario_select", "demo_scenario_start"])
    @pytest.mark.parametrize("method", ["options", "head", "put", "patch", "delete", "trace"])
    def test_every_method_404_without_demo_mode(self, client, url_name, method):
        """Refs #2350: die Pruefung sitzt in ``dispatch`` — ohne ``DEMO_MODE`` verraet
        keine Methode die Route (kein 200 auf OPTIONS, kein 405 mit ``Allow``-Kopf)."""
        response = getattr(client, method)(reverse(url_name))
        assert response.status_code == 404
        assert not response.has_header("Allow")


# ---------------------------------------------------------------------------
# Auswahlseite: nur bereitgestellte/aktive Bereiche, Deep-Link waehlt nur vor.
# ---------------------------------------------------------------------------


class TestScenarioSelectView:
    def test_public_access_without_login(self, client, demo_mode, patched_catalog):
        assert client.get(reverse("demo_scenario_select")).status_code == 200

    def test_empty_catalog_shows_placeholder(self, client, demo_mode, monkeypatch):
        monkeypatch.setattr(views, "scenario_catalog", lambda: ())
        body = client.get(reverse("demo_scenario_select")).content.decode()
        assert "keine Bereiche" in body

    def test_available_scenario_offers_start_form(self, client, demo_mode, patched_catalog):
        body = client.get(reverse("demo_scenario_select")).content.decode()
        assert 'data-scenario-key="kontaktladen"' in body
        assert 'name="scenario" value="kontaktladen"' in body

    def test_unseeded_scenario_marked_unavailable_no_login_form(self, client, demo_mode, catalog, monkeypatch):
        """Facility fehlt (noch nicht geseedet / Reset laeuft) -> kein
        stiller Einstieg, keine Start-Form fuer diese Karte (Ticket-Punkt 3)."""
        missing = _scenario("frauenhaus", facility_name="Nicht existierende Einrichtung", demo_roles=TEAM_ROLES)
        monkeypatch.setattr(views, "scenario_catalog", lambda: (*catalog, missing))
        body = client.get(reverse("demo_scenario_select")).content.decode()
        assert 'data-scenario-key="frauenhaus"' in body
        # Keine Start-Form fuer die nicht verfuegbare Karte: das Formular
        # dieser Karte darf NICHT auf "frauenhaus" zeigen.
        unavailable_marker = body.split('data-scenario-key="frauenhaus"')[1]
        assert "demo-scenario-unavailable" in unavailable_marker.split("</section>")[0]
        assert 'value="frauenhaus"' not in unavailable_marker.split("</section>")[0]

    def test_archived_facility_marked_unavailable(self, client, demo_mode, patched_catalog, kontaktladen_facility):
        kontaktladen_facility.is_active = False
        kontaktladen_facility.save(update_fields=["is_active"])
        body = client.get(reverse("demo_scenario_select")).content.decode()
        section = body.split('data-scenario-key="kontaktladen"')[1].split("</section>")[0]
        assert "demo-scenario-unavailable" in section

    def test_deep_link_preselects_without_login(self, client, demo_mode, patched_catalog):
        """GET/Deep-Link waehlt NUR vor — kein Login, keine Session-Aenderung
        (Ticket-Punkt 8)."""
        response = client.get(reverse("demo_scenario_select"), {"bereich": "kontaktladen"})
        assert response.status_code == 200
        assert "_auth_user_id" not in client.session
        assert b"autofocus" in response.content

    def test_deep_link_with_unknown_key_ignored(self, client, demo_mode, patched_catalog):
        response = client.get(reverse("demo_scenario_select"), {"bereich": "does-not-exist"})
        assert response.status_code == 200
        assert b"autofocus" not in response.content

    def test_current_scenario_marked_for_authenticated_visitor(
        self, client, demo_mode, patched_catalog, kontaktladen_staff
    ):
        client.force_login(kontaktladen_staff)
        body = client.get(reverse("demo_scenario_select")).content.decode()
        section = body.split('data-scenario-key="kontaktladen"')[1].split("</section>")[0]
        assert "demo-scenario-current" in section


# ---------------------------------------------------------------------------
# Start/Wechsel: serverseitige Validierung, CSRF, fester Redirect.
# ---------------------------------------------------------------------------


class TestScenarioStartView:
    def test_valid_scenario_logs_in_default_role(
        self, client, demo_mode, patched_catalog, kontaktladen_staff, kontaktladen_admin
    ):
        response = client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        assert response.status_code == 302
        assert response.url == "/"
        assert int(client.session["_auth_user_id"]) == kontaktladen_staff.pk  # STAFF ist Alltagsrolle

    def test_valid_scenario_with_explicit_other_role(
        self, client, demo_mode, patched_catalog, kontaktladen_staff, kontaktladen_admin
    ):
        response = client.post(
            reverse("demo_scenario_start"),
            {"scenario": "kontaktladen", "role": User.Role.FACILITY_ADMIN},
        )
        assert response.status_code == 302
        assert int(client.session["_auth_user_id"]) == kontaktladen_admin.pk

    def test_response_carries_clear_site_data_header(self, client, demo_mode, patched_catalog, kontaktladen_staff):
        response = client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        assert response["Clear-Site-Data"] == '"storage", "cache"'

    def test_unknown_scenario_key_rejected(self, client, demo_mode, patched_catalog):
        """Manipulierte Szenario-Angabe: kein Login, Redirect zur Auswahl."""
        response = client.post(reverse("demo_scenario_start"), {"scenario": "does-not-exist"})
        assert response.status_code == 302
        assert response.url == reverse("demo_scenario_select")
        assert "_auth_user_id" not in client.session

    def test_role_not_in_scenario_catalog_rejected(self, client, demo_mode, patched_catalog, kontaktladen_staff):
        """Manipulierte Rollenangabe (z. B. super_admin durchreichen)."""
        response = client.post(
            reverse("demo_scenario_start"),
            {"scenario": "kontaktladen", "role": User.Role.SUPER_ADMIN},
        )
        assert response.status_code == 302
        assert "_auth_user_id" not in client.session

    def test_role_valid_in_catalog_but_not_for_this_scenario_rejected(
        self, client, demo_mode, patched_catalog, solo_staff
    ):
        """``lead`` ist ein gueltiger ``User.Role``-Wert, aber im Solo-Szenario
        nicht in ``demo_roles`` — muss trotzdem abgelehnt werden."""
        response = client.post(
            reverse("demo_scenario_start"),
            {"scenario": "solo", "role": User.Role.LEAD},
        )
        assert response.status_code == 302
        assert "_auth_user_id" not in client.session

    def test_role_catalog_membership_rejected_even_if_matching_account_exists(
        self, client, demo_mode, catalog, monkeypatch, kontaktladen_facility
    ):
        """Isoliert die Katalog-Pruefung vom sekundaeren "kein passendes Konto"-
        Fallback: hier EXISTIERT ein passendes ``lead``-Konto fuer die Facility
        — verboten ist es einzig, weil das (bewusst enger gefasste) Szenario
        die Rolle nicht in ``demo_roles`` fuehrt. Ohne die Pruefung in
        ``DemoScenarioStartView.post`` waere dieser Test rot."""
        User.objects.create_user(
            username="kl_lead_restricted",
            role=User.Role.LEAD,
            facility=kontaktladen_facility,
            is_staff=True,
        )
        restricted = _scenario(
            "kontaktladen",
            facility_name=kontaktladen_facility.name,
            demo_roles=(User.Role.STAFF,),
        )
        monkeypatch.setattr(views, "scenario_catalog", lambda: (restricted,))
        monkeypatch.setattr(views, "_lookup_scenario", lambda key: restricted if key == "kontaktladen" else None)

        response = client.post(
            reverse("demo_scenario_start"),
            {"scenario": "kontaktladen", "role": User.Role.LEAD},
        )
        assert response.status_code == 302
        assert "_auth_user_id" not in client.session

    def test_unseeded_scenario_rejected(self, client, demo_mode, catalog, monkeypatch):
        missing = _scenario("frauenhaus", facility_name="Nicht existierende Einrichtung", demo_roles=TEAM_ROLES)
        monkeypatch.setattr(views, "scenario_catalog", lambda: (*catalog, missing))
        monkeypatch.setattr(views, "_lookup_scenario", lambda key: missing if key == "frauenhaus" else None)
        response = client.post(reverse("demo_scenario_start"), {"scenario": "frauenhaus"})
        assert response.status_code == 302
        assert "_auth_user_id" not in client.session

    def test_archived_facility_rejected(
        self, client, demo_mode, patched_catalog, kontaktladen_facility, kontaktladen_staff
    ):
        kontaktladen_facility.is_active = False
        kontaktladen_facility.save(update_fields=["is_active"])
        response = client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        assert response.status_code == 302
        assert "_auth_user_id" not in client.session

    def test_no_matching_account_rejected(self, client, demo_mode, patched_catalog, kontaktladen_facility):
        """Katalog nennt eine Rolle, aber kein passendes Konto wurde geseedet."""
        response = client.post(
            reverse("demo_scenario_start"),
            {"scenario": "kontaktladen", "role": User.Role.LEAD},
        )
        assert response.status_code == 302
        assert "_auth_user_id" not in client.session

    def test_get_does_not_log_in(self, client, demo_mode, patched_catalog):
        """Kein Login per GET/Deep-Link auf den Start-Endpunkt selbst."""
        response = client.get(reverse("demo_scenario_start"))
        assert response.status_code == 302
        assert "_auth_user_id" not in client.session

    def test_csrf_required(self, demo_mode, patched_catalog, kontaktladen_staff):
        from django.test import Client

        strict_client = Client(enforce_csrf_checks=True)
        response = strict_client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        assert response.status_code == 403

    def test_next_parameter_is_ignored_redirect_stays_fixed(
        self, client, demo_mode, patched_catalog, kontaktladen_staff
    ):
        """Weiterleitungsziel bleibt auf die vorgesehene Demo-Seite begrenzt —
        ein mitgesendetes ``next`` darf das Ziel nicht veraendern."""
        response = client.post(
            reverse("demo_scenario_start"),
            {"scenario": "kontaktladen", "next": "https://evil.example/"},
        )
        assert response.status_code == 302
        assert response.url == "/"


# ---------------------------------------------------------------------------
# Bereichstrennung + unabhaengige Besuchersitzungen + Wechsel.
# ---------------------------------------------------------------------------


class TestFacilitySeparationAndSwitching:
    def test_switch_never_rewrites_shared_account_facility(
        self, client, demo_mode, patched_catalog, kontaktladen_staff, solo_staff, kontaktladen_facility, solo_facility
    ):
        """Ticket-Punkt 5: der Wechsel darf NIE die ``facility``-Zuordnung
        eines geteilten Kontos umschreiben — er meldet ein ANDERES, bereits
        fest zugeordnetes Konto an."""
        client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        client.post(reverse("demo_scenario_start"), {"scenario": "solo"})

        kontaktladen_staff.refresh_from_db()
        solo_staff.refresh_from_db()
        assert kontaktladen_staff.facility_id == kontaktladen_facility.pk
        assert solo_staff.facility_id == solo_facility.pk

    def test_switch_replaces_session_with_new_scenario_user(
        self, client, demo_mode, patched_catalog, kontaktladen_staff, solo_staff
    ):
        client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        assert int(client.session["_auth_user_id"]) == kontaktladen_staff.pk

        client.post(reverse("demo_scenario_start"), {"scenario": "solo"})
        assert int(client.session["_auth_user_id"]) == solo_staff.pk

    def test_switch_invalidates_previous_session_key(
        self, client, demo_mode, patched_catalog, kontaktladen_staff, solo_staff
    ):
        """Alte Sitzung ueberlebt den Wechsel nicht (Ticket-Punkt 9): Djangos
        ``login()`` flusht/cycelt die Session bei Nutzerwechsel — die vorher
        gueltige Session-ID ist danach keinem angemeldeten Nutzer mehr
        zugeordnet."""
        from django.contrib.sessions.models import Session

        client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        old_session_key = client.session.session_key
        assert Session.objects.filter(pk=old_session_key).exists()

        client.post(reverse("demo_scenario_start"), {"scenario": "solo"})
        new_session_key = client.session.session_key
        assert new_session_key != old_session_key
        assert not Session.objects.filter(pk=old_session_key).exists()

    def test_independent_visitor_sessions_do_not_interfere(
        self, demo_mode, patched_catalog, kontaktladen_staff, solo_staff
    ):
        """Ticket-Punkt 5: eine Besuchersitzung darf keine andere Sitzung
        beeinflussen — zwei unabhaengige Clients waehlen unterschiedliche
        Bereiche, ohne sich gegenseitig zu veraendern."""
        from django.test import Client

        visitor_a = Client()
        visitor_b = Client()
        visitor_a.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        visitor_b.post(reverse("demo_scenario_start"), {"scenario": "solo"})

        assert int(visitor_a.session["_auth_user_id"]) == kontaktladen_staff.pk
        assert int(visitor_b.session["_auth_user_id"]) == solo_staff.pk


# ---------------------------------------------------------------------------
# Reine Funktionstests der Hilfsfunktionen.
# ---------------------------------------------------------------------------


class TestDefaultRole:
    def test_prefers_staff_when_offered(self):
        scenario = _scenario("x", facility_name="X", demo_roles=TEAM_ROLES)
        assert views._default_role(scenario) == User.Role.STAFF

    def test_falls_back_to_first_role_without_staff(self):
        scenario = _scenario("x", facility_name="X", demo_roles=(User.Role.FACILITY_ADMIN, User.Role.LEAD))
        assert views._default_role(scenario) == User.Role.FACILITY_ADMIN

    def test_fail_closed_on_empty_demo_roles(self):
        """Katalog-Datenfehler (leere ``demo_roles``): ``None`` statt
        ``IndexError`` im heissen Pfad."""
        scenario = _scenario("x", facility_name="X", demo_roles=())
        assert views._default_role(scenario) is None


class TestScenarioCardsFailClosedOnEmptyRoles:
    def test_card_with_no_roles_is_marked_unavailable(self, demo_mode, monkeypatch, kontaktladen_facility):
        """Eine Facility existiert und ist aktiv, aber der Katalogeintrag hat
        keine Rollen — die Karte darf trotzdem nicht als startbar gelten."""
        broken = _scenario("broken", facility_name=kontaktladen_facility.name, demo_roles=())
        monkeypatch.setattr(views, "scenario_catalog", lambda: (broken,))
        cards = views.scenario_cards()
        assert cards[0]["available"] is False
        assert cards[0]["default_role"] is None

    def test_start_with_no_roles_in_catalog_is_rejected(self, client, demo_mode, monkeypatch, kontaktladen_facility):
        broken = _scenario("broken", facility_name=kontaktladen_facility.name, demo_roles=())
        monkeypatch.setattr(views, "scenario_catalog", lambda: (broken,))
        monkeypatch.setattr(views, "_lookup_scenario", lambda key: broken if key == "broken" else None)
        response = client.post(reverse("demo_scenario_start"), {"scenario": "broken"})
        assert response.status_code == 302
        assert "_auth_user_id" not in client.session


class TestAccountForExcludesSuperusers:
    """Verteidigung in der Tiefe: ``account_for`` schliesst ``is_superuser`` aus."""

    def test_superuser_with_matching_facility_and_role_is_never_picked(self, kontaktladen_facility):
        from core.services.demo_scenarios import account_for

        superuser = User.objects.create_user(
            username="kl_super",
            role=User.Role.STAFF,
            facility=kontaktladen_facility,
            is_staff=True,
            is_superuser=True,
        )
        regular = User.objects.create_user(
            username="kl_regular",
            role=User.Role.STAFF,
            facility=kontaktladen_facility,
            is_staff=True,
        )
        # order_by("pk") waehlte ohne den Ausschluss den zuerst angelegten —
        # hier bewusst den Superuser zuerst anlegen, damit der Test bei einer
        # entfernten .exclude(is_superuser=True) rot wuerde.
        assert superuser.pk < regular.pk
        assert account_for(kontaktladen_facility, User.Role.STAFF) == regular


class TestBanner:
    def test_current_scenario_name_empty_outside_demo_mode(self, rf, staff_user):
        from django.template import RequestContext, Template

        request = rf.get("/")
        request.user = staff_user
        tmpl = Template("{% load demo_scenarios %}{% demo_current_scenario_name %}")
        assert tmpl.render(RequestContext(request, {"request": request})) == ""

    def test_current_scenario_name_matches_facility(self, rf, demo_mode, patched_catalog, kontaktladen_staff):
        from django.template import RequestContext, Template

        request = rf.get("/")
        request.user = kontaktladen_staff
        tmpl = Template("{% load demo_scenarios %}{% demo_current_scenario_name %}")
        assert tmpl.render(RequestContext(request, {"request": request})) == "Anzeigename kontaktladen"


class TestStaleRequestAfterSwitch:
    """Requests the old tab sent before the switch arrive with the replaced key."""

    def test_stale_request_keeps_the_cookie_the_switch_set(
        self, client, demo_mode, patched_catalog, kontaktladen_staff, solo_staff
    ):
        from django.conf import settings as django_settings
        from django.test import Client

        client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        old_key = client.session.session_key
        switch = client.post(reverse("demo_scenario_start"), {"scenario": "solo"})
        new_key = switch.cookies[django_settings.SESSION_COOKIE_NAME].value
        assert new_key and new_key != old_key

        stale = Client()
        stale.cookies[django_settings.SESSION_COOKIE_NAME] = old_key
        response = stale.get(reverse("core:offline_workitem_list_shell"))

        assert response.status_code == 200
        assert django_settings.SESSION_COOKIE_NAME not in response.cookies

        # The visitor's own jar still holds the new session and is signed in.
        assert client.cookies[django_settings.SESSION_COOKIE_NAME].value == new_key
        assert int(client.session["_auth_user_id"]) == solo_staff.pk


class TestDemoOfflineBootstrap:
    def test_bootstrap_notices_offer_immediate_recovery_and_success(
        self, client, demo_mode, patched_catalog, kontaktladen_staff
    ):
        response = client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        page = client.get(response.url)
        assert page.status_code == 200
        body = page.content.decode()
        assert "die Demo erneut starten oder regulär anmelden" in body
        assert "nach dem nächsten Reset erneut starten" not in body
        assert "data-demo-offline-success" in body
        assert "Offline-Schlüssel ist bereit." in body

    @pytest.mark.parametrize("url_name", ["demo_imprint", "demo_privacy", "login"])
    def test_anonymous_public_pages_have_no_demo_offline_alert(self, client, demo_mode, url_name):
        response = client.get(reverse(url_name))
        assert response.status_code == 200
        body = response.content.decode()
        assert "data-demo-offline-error" not in body
        assert "Offline-Schlüssel konnte nicht aktiviert werden" not in body

    # Only the roles each scenario offers; a skip here would be unapproved in public runs.
    @pytest.mark.parametrize(
        ("scenario_key", "role"),
        [("kontaktladen", role) for role in TEAM_ROLES] + [("solo", role) for role in SOLO_ROLES],
    )
    def test_start_keeps_password_data_until_confirmation_after_clear_site_data(
        self, client, demo_mode, patched_catalog, kontaktladen_staff, solo_staff, role, scenario_key
    ):
        user = kontaktladen_staff if scenario_key == "kontaktladen" else solo_staff
        scenario = next(item for item in patched_catalog if item.key == scenario_key)
        assert role in scenario.demo_roles
        user.role = role
        user.set_password(DEMO_PASSWORD)
        user.save()
        response = client.post(reverse("demo_scenario_start"), {"scenario": scenario_key, "role": role})
        assert response.status_code == 302
        assert response["Clear-Site-Data"] == views.CLEAR_SITE_DATA_VALUE
        assert "data-demo-offline-password" not in response.content.decode()
        page = client.get(response.url)
        assert page.status_code == 200
        body = page.content.decode()
        assert 'data-demo-offline-password="anlaufstelle2026"' in body
        assert 'src="/static/js/auth-bootstrap.js"' in body
        assert "data-demo-offline-error" in body
        assert body.index("data-demo-offline-error") < body.index('id="flash-messages"')
        assert 'data-demo-offline-password="anlaufstelle2026"' in client.get("/account/").content.decode()
        assert client.session["demo_offline_bootstrap_pending"] is True

    def test_changed_password_shows_error_without_login_or_storage_reset(
        self, client, demo_mode, patched_catalog, kontaktladen_staff
    ):
        kontaktladen_staff.set_password("changed demo password")
        kontaktladen_staff.save()
        password_hash = kontaktladen_staff.password
        salt = kontaktladen_staff.ensure_offline_key_salt()
        response = client.post(reverse("demo_scenario_start"), {"scenario": "kontaktladen"})
        assert response.status_code == 302
        assert "Clear-Site-Data" not in response
        body = client.get(response.url).content.decode()
        assert "Offline-Schlüssel konnte nicht aktiviert werden" in body
        assert "_auth_user_id" not in client.session
        assert "data-demo-offline-password" not in body
        kontaktladen_staff.refresh_from_db()
        assert kontaktladen_staff.password == password_hash
        assert kontaktladen_staff.offline_key_salt == salt

    def test_non_demo_ignores_pending_flag_and_never_exposes_password(self, client, settings, staff_user):
        settings.DEMO_MODE = False
        client.force_login(staff_user)
        session = client.session
        session["demo_offline_bootstrap_pending"] = True
        session.save()
        body = client.get("/account/").content.decode()
        assert "data-demo-offline-password" not in body
        assert 'src="/static/js/auth-bootstrap.js"' not in body
        assert "anlaufstelle2026" not in body

    def test_demo_without_start_has_no_bootstrap(self, client, demo_mode, kontaktladen_staff):
        client.force_login(kontaktladen_staff)
        assert "data-demo-offline-password" not in client.get("/account/").content.decode()

    @pytest.mark.parametrize("demo_enabled", [False, True])
    def test_salt_rejects_demo_bootstrap_with_changed_password(self, client, settings, staff_user, demo_enabled):
        settings.DEMO_MODE = demo_enabled
        client.force_login(staff_user)
        response = client.post("/auth/offline-key-salt/", HTTP_X_DEMO_OFFLINE_BOOTSTRAP="1")
        assert response.status_code == 403
        assert "salt" not in response.json()
        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""

    def test_demo_salt_keeps_audit_and_facility_lifetime(self, client, demo_mode, kontaktladen_staff):
        from core.models import AuditLog, Settings

        Settings.objects.create(facility=kontaktladen_staff.facility, session_absolute_hours=3)
        kontaktladen_staff.set_password(DEMO_PASSWORD)
        kontaktladen_staff.save()
        client.force_login(kontaktladen_staff)
        response = client.post("/auth/offline-key-salt/", HTTP_X_DEMO_OFFLINE_BOOTSTRAP="1")
        assert response.status_code == 200
        assert response.json()["offline_key_max_age"] == 10800
        assert AuditLog.objects.filter(action=AuditLog.Action.OFFLINE_KEY_FETCH, user=kontaktladen_staff).count() == 1
