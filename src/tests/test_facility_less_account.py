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

Refs #2090, https://github.com/anlaufstelle/app/issues/2090.
"""

import re
import uuid

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


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
