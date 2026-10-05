"""Konten ohne Einrichtung laufen nicht mehr in den Serverfehler (Refs #2090).

``User.facility`` ist ``null=True`` — und das ist kein Versehen: ``super_admin``
traegt die Bindung per Design nicht, und die Benutzerverwaltung kann sie jedem
Konto entziehen. Fuer jede **andere** Rolle war dieser Zustand bis hierher ein
Minenfeld: ``FacilityScopeMiddleware`` setzte ``request.current_facility = None``
und liess den Request weiterlaufen, worauf ``get_scoped_object()`` mit Absicht
ein ``ValueError`` warf (66 Aufrufstellen in 17 View-Modulen) und rund ein
Dutzend Service-Funktionen ``facility.settings`` bzw. ``facility.pk`` ungeprueft
dereferenzierten. Beim Nutzer kam das als **HTTP 500** an — direkt nach dem
Login, weil ``LOGIN_REDIRECT_URL = "/"`` auf den Zeitstrom zeigt.

Der Entscheid ist **an der Wurzel abfangen, nicht in jeder View** (Maintainer,
Welle D): der Guard sitzt in derselben Middleware wie die Archiv-Sperre aus
Refs #2067 und antwortet mit einer erklaerenden **403** — ohne neue Route, damit
weder die Routenzahl noch der Backlink-Guard noch die AuthZ-Matrix eine
Kaskade auslosen. ``get_scoped_object`` behaelt seine ``ValueError``-Semantik
(sie ist das IDOR-Signal, gepinnt in ``test_scoping_helper.py``); der Guard
macht sie nur unerreichbar.

Freigestellt wird die **Rolle**, nicht der Systempfad: ``super_admin`` passiert
den Guard, ``/system/`` und ``/admin-mgmt/`` stehen bewusst NICHT auf der
Pfad-Whitelist. Sonst kaeme ein einrichtungsloser ``facility_admin`` in den
Systembereich — dieselbe Doktrin, die ``_is_archived_blocked`` begruendet.

Refs #2090, #2090.
"""

import re
import uuid

import pytest
from django.db import connection
from django.urls import get_resolver, reverse
from django.urls.resolvers import URLPattern, URLResolver

from core.middleware.facility_scope import (
    CLEAR_SITE_DATA_VALUE,
    FACILITY_MISSING_EXEMPT_PATHS,
    FACILITY_MISSING_EXEMPT_URLS,
)
from tests._authz_expectations import EXPECTATIONS
from tests.test_authz_facility_less import EXEMPT_CATEGORIES

pytestmark = pytest.mark.django_db


def _current_facility_guc():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.current_facility_id', true)")
        return cursor.fetchone()[0]


def _named_routes():
    """``url_name -> Pfad`` fuer alle benannten Patterns ausserhalb Admin/Toolbar.

    Muster von ``test_authz_matrix._collect_patterns``; hier wird zusaetzlich
    der Pfad behalten, weil der Guard auf Pfaden arbeitet, nicht auf Namen.
    """
    routes = {}

    def walk(resolver, namespace, prefix):
        for pattern in resolver.url_patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLPattern):
                if pattern.name:
                    qualified = f"{namespace}:{pattern.name}" if namespace else pattern.name
                    routes[qualified] = "/" + route
            elif isinstance(pattern, URLResolver):
                child = pattern.namespace
                if child in {"admin", "djdt"}:
                    continue
                walk(pattern, f"{namespace}:{child}" if namespace and child else (child or namespace), route)

    walk(get_resolver(), None, "")
    return routes


# --- Reproduktion: die Wege, die heute in den 500 laufen ---------------------


class TestTicketwegHilfeRueckweg:
    """Der im Ticket beschriebene Weg, mit ``follow=True``.

    ``/hilfe/`` traegt nur ``LoginRequiredMixin`` und ist damit erreichbar; der
    Rueckweg der Uebersicht zeigt fuer Nicht-Superadmins auf ``core:zeitstrom``
    (= ``/``), und ``build_zeitstrom_page_scope`` bricht dort an
    ``str(facility.pk)``. Die AuthZ-Matrix folgt Redirects NICHT und sah den
    Defekt deshalb nie — ``follow=True`` ist hier Pflicht.
    """

    def test_hilfe_uebersicht_ist_erreichbar(self, client, facility_less_admin):
        client.force_login(facility_less_admin)

        response = client.get(reverse("core:help_overview"))

        assert response.status_code == 200

    def test_rueckweg_der_hilfe_liefert_kein_5xx(self, client, facility_less_admin):
        client.force_login(facility_less_admin)

        overview = client.get(reverse("core:help_overview"))
        match = re.search(rb'<a href="([^"]+)"[^>]*data-testid="back-link"', overview.content)
        assert match, "Rueckweg der Hilfe-Uebersicht nicht im Markup gefunden"
        back_url = match.group(1).decode()
        assert back_url == "/", "Rueckweg zeigt fuer Nicht-Superadmins auf den Zeitstrom"

        response = client.get(back_url, follow=True)

        assert response.status_code == 403, f"erwartet 403, bekam {response.status_code}"


class TestLoginLandung:
    """``LOGIN_REDIRECT_URL = "/"`` — die erste Seite nach dem Login ist der
    Zeitstrom. Ein einrichtungsloses Konto sah dort bisher sofort den
    Serverfehler, ohne je eine bedienbare Seite gesehen zu haben."""

    def test_startseite_liefert_403_statt_500(self, client, facility_less_admin):
        client.force_login(facility_less_admin)

        response = client.get("/", follow=True)

        assert response.status_code == 403, f"erwartet 403, bekam {response.status_code}"


class TestGetScopedObjectKlasse:
    """Die groesste Wirkungsklasse: 66 ``get_scoped_object``-Aufrufe werfen ohne
    Facility-Kontext ``ValueError`` → 500.

    Gewaehlt sind bewusst ``core:goal_create`` und ``core:episode_create``:
    beide Module (``views/case_goals.py``, ``views/case_episodes.py``) enthalten
    **kein einziges** ``current_facility`` und faenden sich mit dem Suchanker
    des Tickets nie — sie belegen, dass der Sweep zwei Anker braucht.
    ``goal_create`` ist POST-only (HTMX-Fragment), ``episode_create`` traegt
    beide Methoden; die Methode kommt deshalb aus der Parametrierung.
    """

    @pytest.mark.parametrize("url_name,method", [("core:goal_create", "post"), ("core:episode_create", "get")])
    def test_pk_route_liefert_403_statt_500(self, client, facility_less_admin, url_name, method):
        client.force_login(facility_less_admin)

        url = reverse(url_name, kwargs={"case_pk": uuid.uuid4()})
        response = getattr(client, method)(url)

        assert response.status_code in (403, 404), f"erwartet 403/404, bekam {response.status_code}"


class TestAttributzugriffsKlasse:
    """Die gefaehrlichere Klasse: Dereferenzierung von ``None`` ohne pk in der
    URL — diese Routen sind ohne jedes Vorwissen erreichbar.

    ``/verwaltung/`` bricht in ``services/dashboard/main.py`` an
    ``facility.settings``, ``/retention/`` in ``retention/proposals.py`` an
    derselben Stelle.
    """

    @pytest.mark.parametrize("url_name", ["core:verwaltung_uebersicht", "core:retention_dashboard"])
    def test_route_ohne_pk_liefert_403_statt_500(self, client, facility_less_admin, url_name):
        client.force_login(facility_less_admin)

        response = client.get(reverse(url_name))

        assert response.status_code in (403, 404), f"erwartet 403/404, bekam {response.status_code}"


# --- Gegenproben: der Guard darf nur diesen einen Zustand treffen ------------


class TestGegenprobeRolle:
    """Ohne diese Gegenprobe bliebe ein Guard gruen, der versehentlich ALLE
    Rollen sperrt. ``super_admin`` traegt ``facility=None`` per Design — 70 der
    257 AuthZ-Matrix-Methodenzeilen haengen daran."""

    @pytest.mark.parametrize("path", ["/hilfe/", "/account/", "/start/", "/system/"])
    def test_super_admin_passiert_unveraendert(self, client, super_admin_user, path):
        client.force_login(super_admin_user)

        response = client.get(path)

        assert response.status_code != 403, f"{path}: super_admin darf nicht gesperrt werden"
        assert response.status_code < 500, f"{path}: super_admin bekam {response.status_code}"


class TestGegenprobeZustand:
    """Ein ``facility_admin`` MIT Einrichtung bleibt unberuehrt — der Guard
    haengt am Zustand, nicht an der Rolle."""

    @pytest.mark.parametrize("path", ["/", "/hilfe/", "/account/", "/start/", "/verwaltung/", "/retention/"])
    def test_admin_mit_einrichtung_unveraendert(self, client, admin_user, path):
        client.force_login(admin_user)

        response = client.get(path)

        assert response.status_code != 403, f"{path}: Konto MIT Einrichtung darf nicht gesperrt werden"
        assert response.status_code < 500, f"{path}: bekam {response.status_code}"


# --- Mechanik des Guards ------------------------------------------------------


@pytest.mark.django_db
class TestGuardMechanik:
    """Der Guard sitzt in ``FacilityScopeMiddleware`` — nicht als neue
    Middleware und nicht als Mixin.

    Als Mixin waere er wirkungslos: ``/hilfe/``, ``/account/``, ``/start/``,
    ``/sudo/``, die MFA-Kette und der Rueckzugs-Dialog tragen gar kein
    Rollen-Mixin, und alle Access-Mixins in ``views/mixins.py`` sind reine
    Rollenvergleiche ohne Facility-Bezug. Als eigene Middleware waere die
    Reihenfolge gegen ``UserLanguage``/``ForcePasswordChange``/``MFAEnforcement``
    neu zu verhandeln, ohne dass sich etwas gewaenne.
    """

    def test_sperrseite_traegt_den_abmelden_knopf(self, client, facility_less_admin):
        """Refs #2090 (F-20): ``ForcePasswordChangeMiddleware`` und
        ``MFAEnforcementMiddleware`` laufen NACH dem Guard — ein Konto mit
        offenem Passwortwechsel bekommt auf ``/`` die Sperrseite statt des
        Redirects. Tragbar ist das nur, weil die Seite selbst hinausfuehrt."""
        client.force_login(facility_less_admin)

        response = client.get("/")

        body = response.content.decode()
        assert response.status_code == 403
        assert 'data-testid="facility-missing"' in body
        assert 'data-testid="facility-missing-logout"' in body
        assert f'action="{reverse("logout")}"' in body
        assert "csrfmiddlewaretoken" in body

    def test_abmelden_von_der_sperrseite_traegt(self, client, facility_less_admin):
        """Die Sitzung bleibt bewusst bestehen — der Knopf muss also wirken."""
        client.force_login(facility_less_admin)

        response = client.post(reverse("logout"))

        assert response.status_code == 302
        assert "_auth_user_id" not in client.session

    def test_anonym_bekommt_login_redirect_statt_403(self, client):
        """Gegenprobe: ohne Anmeldung ist „keine Einrichtung" kein Defekt."""
        response = client.get("/")

        assert response.status_code == 302
        assert response.url.startswith("/login/")


@pytest.mark.postgres
@pytest.mark.django_db
class TestRlsVariableWirdAuchImSperrfallGeleert:
    """Refs #2090 (F-1): der Guard MUSS nach dem ``set_config``-Block stehen.

    Ein frueher ``return`` uebersprunge das Leeren von
    ``app.current_facility_id`` — und eine wiederverwendete Connection truege
    den Facility-Wert eines FRUEHEREN Requests weiter. Genau der Leak, gegen
    den Refs #586/#733 gebaut wurden.
    """

    def test_geblockter_request_leert_die_session_variable(self, client, facility_less_admin, admin_user):
        # Vorlauf mit einem Konto MIT Einrichtung setzt die GUC auf einen Wert,
        # den der folgende Sperr-Request loeschen muss.
        client.force_login(admin_user)
        client.get("/")
        assert _current_facility_guc() == str(admin_user.facility.pk)

        client.force_login(facility_less_admin)
        response = client.get("/")

        assert response.status_code == 403
        assert _current_facility_guc() == ""


@pytest.mark.django_db
class TestAbgrenzungZurArchivSperre:
    """Zwei Zustaende, zwei Seiten — und nur einer ist ein Purge-Grund.

    Refs #2090 (F-6): die Archiv-403 traegt ``X-Facility-Archived`` und
    ``Clear-Site-Data``, weil dort der Zugriff auf Fachdaten DAUERHAFT endet.
    Beim einrichtungslosen Konto ist der Zustand typischerweise eine
    Fehlkonfiguration, die zurueckgenommen wird — ein Purge loeschte
    Aussendienst-Caches und die IndexedDB-Kryptoschluessel unwiederbringlich.
    Ein nacktes 403 ist fuer den Offline-Client seit Refs #1354 BEWUSST kein
    Invalidierungsgrund; genau das wird hier ausgenutzt.
    """

    def test_neue_sperrseite_traegt_kein_purge_signal(self, client, facility_less_admin):
        client.force_login(facility_less_admin)

        response = client.get("/")

        assert response.status_code == 403
        assert "Clear-Site-Data" not in response
        assert "X-Facility-Archived" not in response

    def test_archiv_sperre_traegt_ihre_marker_unveraendert(self, client, staff_user):
        staff_user.facility.is_active = False
        staff_user.facility.save(update_fields=["is_active"])
        client.force_login(staff_user)

        response = client.get("/")

        assert response.status_code == 403
        assert response["X-Facility-Archived"] == "1"
        assert response["Clear-Site-Data"] == CLEAR_SITE_DATA_VALUE

    def test_archivierte_einrichtung_bekommt_die_archiv_seite(self, client, staff_user):
        """Refs #2090 (F-2): der Guard prueft die lokale Variable ``facility``,
        NICHT ``request.current_facility`` — das ist bei archivierter
        Einrichtung ebenfalls ``None``. Wer das verwechselt, zeigt
        Nutzerinnen archivierter Einrichtungen die sachlich falsche Seite."""
        staff_user.facility.is_active = False
        staff_user.facility.save(update_fields=["is_active"])
        client.force_login(staff_user)

        body = client.get("/").content.decode()

        assert 'data-testid="facility-archived"' in body
        assert 'data-testid="facility-missing"' not in body


@pytest.mark.django_db
class TestSperrseiteSprichtDieNutzersprache:
    """Refs #2090: dieselbe Mechanik wie bei der Archiv-Seite (Refs #2067,
    Cross-Review Finding 8 + N1) — geteilt ueber ``_render_block``.

    Der Lauf durch die VOLLE Kette ist der wichtigere: ``translation.override``
    endet mit dem Renderblock, und die aeussere ``LocaleMiddleware`` setzt
    ``Content-Language`` danach aus der wiederhergestellten Request-Sprache.
    Ein Test nur mit ``RequestFactory`` sieht diesen Fehler nicht.
    """

    def test_englische_praeferenz_durch_die_volle_kette(self, client, facility_less_admin):
        facility_less_admin.preferred_language = "en"
        facility_less_admin.save(update_fields=["preferred_language"])
        client.force_login(facility_less_admin)

        response = client.get("/", headers={"accept-language": "de"})

        body = response.content.decode()
        assert response.status_code == 403
        assert 'lang="en"' in body
        assert "No facility assigned" in body
        assert response["Content-Language"] == "en"

    def test_ohne_praeferenz_bleibt_die_default_sprache(self, client, facility_less_admin):
        client.force_login(facility_less_admin)

        response = client.get("/")

        body = response.content.decode()
        assert 'lang="de"' in body
        assert "Keine Einrichtung zugeordnet" in body
        assert response["Content-Language"] == "de"


# --- Die Whitelist: hergeleitet, nicht gewaehlt -------------------------------


class TestWhitelistIstAusDerAuthzTabelleAbgeleitet:
    """Der Test, der eine zu breit geratene Whitelist fangen muss.

    Die Freistellung ist keine Geschmacksfrage: freigestellt sind genau die
    AuthZ-Kategorien ``public`` (anonym erreichbar) und ``auth-flow`` (jede
    angemeldete Rolle, inklusive des per Design einrichtungslosen
    ``super_admin``). Beide Richtungen werden hier gehalten — sonst waere die
    Konstante nach der dritten Ergaenzung wieder eine Wunschliste.

    Der Praefix ``/static/`` ist die einzige Ausnahme in der einen Richtung:
    dahinter liegt kein benannter URL-Pattern, sondern WhiteNoise
    (``settings.STATIC_URL``, Middleware-Position VOR dieser hier).
    """

    #: Praefix ohne benannten URL-Pattern — von WhiteNoise bedient.
    PREFIX_WITHOUT_NAMED_ROUTE = frozenset({"/static/"})

    def test_jeder_praefix_ist_durch_eine_public_oder_auth_flow_route_gedeckt(self):
        routes = _named_routes()
        categories = {e.url_name: e.category for e in EXPECTATIONS}

        for prefix in FACILITY_MISSING_EXEMPT_URLS:
            if prefix in self.PREFIX_WITHOUT_NAMED_ROUTE:
                continue
            hits = [
                name
                for name, path in routes.items()
                if path.startswith(prefix) and categories.get(name) in EXEMPT_CATEGORIES
            ]
            assert hits, f"Praefix {prefix!r} deckt keine public/auth-flow-Route — unbegruendete Ausnahme"

    def test_jeder_exakte_pfad_ist_eine_public_oder_auth_flow_route(self):
        routes = _named_routes()
        categories = {e.url_name: e.category for e in EXPECTATIONS}

        for path in FACILITY_MISSING_EXEMPT_PATHS:
            hits = [name for name, route in routes.items() if route == path]
            assert hits, f"Exakter Pfad {path!r} zeigt auf keine Route"
            for name in hits:
                assert categories.get(name) in EXEMPT_CATEGORIES, (
                    f"{path!r} ({name}) ist Kategorie {categories.get(name)!r} — gehoert hinter den Guard"
                )

    def test_keine_facility_gebundene_route_faellt_unter_die_whitelist(self):
        """Die scharfe Richtung. Sie hat den Praefix ``/offline/`` gekippt:
        darunter liegen neun PII-freie Shells UND zwei ``offline-api``-pk-Routen
        (``core:offline_client_detail``, ``core:offline_conflict_review``).
        Deshalb wird der ``/offline/``-Baum exakt statt als Praefix gelistet."""
        routes = _named_routes()
        categories = {e.url_name: e.category for e in EXPECTATIONS}

        leaking = sorted(
            f"{name} ({categories[name]}) unter {path}"
            for name, path in routes.items()
            if name in categories
            and categories[name] not in EXEMPT_CATEGORIES
            and (path in FACILITY_MISSING_EXEMPT_PATHS or path.startswith(FACILITY_MISSING_EXEMPT_URLS))
        )
        assert not leaking, f"facility-gebundene Routen vor dem Guard freigestellt: {leaking}"

    def test_jede_public_und_auth_flow_route_ist_erreichbar(self):
        """Gegenrichtung: ohne sie sperrte der Guard Login, MFA, Sudo,
        Passwort-Reset, /hilfe/, /account/, /start/, die Offline-Shells und
        /health/ aus — das Konto saesse fest."""
        routes = _named_routes()

        unreachable = sorted(
            f"{e.url_name} -> {routes[e.url_name]}"
            for e in EXPECTATIONS
            if e.category in EXEMPT_CATEGORIES
            and e.url_name in routes
            and routes[e.url_name] not in FACILITY_MISSING_EXEMPT_PATHS
            and not routes[e.url_name].startswith(FACILITY_MISSING_EXEMPT_URLS)
        )
        assert not unreachable, f"public/auth-flow-Routen vom Guard gesperrt: {unreachable}"

    def test_systempfade_stehen_bewusst_nicht_drin(self):
        """Refs #2090 (§ 2.3): freigestellt wird die ROLLE, nicht der Pfad.
        Eine Pfad-Ausnahme fuer den Systembereich liesse einen
        einrichtungslosen ``facility_admin`` dorthin — die Rollen-Ausnahme
        nicht. Dieselbe Doktrin wie bei ``_is_archived_blocked`` (Refs #2067)."""
        for path in ("/system/", "/admin-mgmt/"):
            assert path not in FACILITY_MISSING_EXEMPT_PATHS
            assert not path.startswith(FACILITY_MISSING_EXEMPT_URLS)
