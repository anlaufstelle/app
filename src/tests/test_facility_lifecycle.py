"""Facility-Lifecycle: ``is_active`` wird durchgesetzt (Refs #2067).

``Facility.is_active`` war kosmetisch. Die repo-weite Suche nach einer
Auswertung des Flags lieferte null Treffer: die Middleware setzte
``request.current_facility`` ohne Pruefung, und der Login sah ausschliesslich
``User.is_active``. Eine „archivierte" Einrichtung arbeitete damit voll weiter —
Nutzerinnen meldeten sich an, dokumentierten, exportierten.

Der Entscheid ist **erzwingen, nicht entfernen**. Zwei
Ebenen, beide hier gepinnt:

1. **Login** weist Nutzerinnen einer archivierten Einrichtung mit klarer
   Meldung ab — Muster der bestehenden Lockout-Pruefung in
   ``CustomLoginView.form_valid``, inklusive Audit-Zeile mit Grund.
2. **Middleware** faellt fail-closed. Das ist der tragende Teil: ohne ihn
   behielte jede **bereits laufende** Sitzung ihren Zugriff, und Archivieren
   waere ein Vorgang, der erst beim naechsten Login wirkt. Zusaetzlich wird
   ``app.current_facility_id`` geleert, sodass die RLS-Policies selbst dann
   keine Zeile liefern, wenn ein Pfad die Sperre umginge.

**Abgrenzung des Reaktivierungs-Pfads.** Freigeschaltet wird nicht ueber
Pfad-Ausnahmen, sondern ueber die **Rolle**: ``super_admin`` bleibt
unbeschraenkt, weil ``FacilityAdmin`` super-only ist (Refs #1375) — nur diese
Rolle kann eine Einrichtung ueberhaupt wieder aktivieren. Ein
``facility_admin`` der archivierten Einrichtung wird mitgesperrt; er koennte
sie ohnehin nicht reaktivieren, saehe unter einer Pfad-Ausnahme aber weiter
Fachdaten im Django-Admin. Die Pfad-Ausnahmen decken nur das ab, was sonst
eine Sackgasse waere (Abmelden, Login-Seite, statische Dateien, Health).
"""

from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection

from core.middleware.facility_scope import ARCHIVED_EXEMPT_URLS, FacilityScopeMiddleware
from core.models import AuditLog

REPO_ROOT = Path(__file__).resolve().parents[2]


def _archive(facility):
    facility.is_active = False
    facility.save(update_fields=["is_active"])
    return facility


def _run_middleware(rf, user, path="/"):
    request = rf.get(path)
    request.user = user
    response = FacilityScopeMiddleware(lambda r: "DURCHGELASSEN")(request)
    return request, response


def _current_facility_guc():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.current_facility_id', true)")
        return cursor.fetchone()[0]


# --- Ebene 1: Login ----------------------------------------------------------


@pytest.mark.django_db
class TestLoginWeistArchivierteEinrichtungAb:
    def test_anmeldung_wird_abgewiesen(self, client, staff_user):
        _archive(staff_user.facility)

        response = client.post("/login/", {"username": "teststaff", "password": "testpass123"})

        assert response.status_code == 200, "abgewiesen heisst Formular erneut, nicht Redirect"
        assert not response.wsgi_request.user.is_authenticated
        assert "archiviert" in response.content.decode()

    def test_audit_zeile_nennt_den_grund(self, client, staff_user):
        _archive(staff_user.facility)

        client.post("/login/", {"username": "teststaff", "password": "testpass123"})

        entry = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_FAILED).first()
        assert entry is not None, "abgewiesener Login ohne Spur"
        assert entry.detail.get("reason") == "facility_inactive"

    def test_aktive_einrichtung_meldet_sich_unveraendert_an(self, client, staff_user):
        """Gegenprobe — die Sperre haengt am Flag, nicht am Login-Umbau."""
        response = client.post("/login/", {"username": "teststaff", "password": "testpass123"})
        assert response.status_code == 302

    def test_super_admin_ohne_einrichtung_ist_unberuehrt(self, client, super_admin_user):
        response = client.post("/login/", {"username": super_admin_user.username, "password": "testpass123"})
        assert response.status_code == 302


@pytest.mark.django_db
class TestAdminLoginHatDieselbeSperre:
    """Cross-Review Finding 3: ``/admin-mgmt/login/`` ist eine zweite,
    ausdrücklich vorhandene Login-Oberfläche. Sie prüfte nur den
    Account-Lockout — ein archivierter ``facility_admin`` bekam dort eine
    Session, und erst der Folgerequest lief in die Middleware-Sperre.

    Die Prüfung ist in ``admin_site.py`` bewusst dupliziert (das Modul lädt zu
    früh, um am View-Modul zu hängen — wie ``_admin_login_username_key``).
    Gegen Auseinanderlaufen sichert dieser Paritätstest.
    """

    def _form(self, rf, username, password="testpass123"):
        from core.admin_site import AdminLockoutAuthenticationForm

        request = rf.post("/admin-mgmt/login/")
        request.session = {}
        # Wie im echten Ablauf: ``AuthenticationMiddleware`` hat auf der
        # Login-Seite bereits einen anonymen User gesetzt, den der
        # Audit-Helper liest.
        request.user = AnonymousUser()
        return AdminLockoutAuthenticationForm(request=request, data={"username": username, "password": password})

    def test_archivierter_admin_wird_abgewiesen(self, rf, admin_user):
        _archive(admin_user.facility)

        form = self._form(rf, admin_user.username)

        assert not form.is_valid()
        assert "archiviert" in " ".join(form.non_field_errors())

    def test_abweisung_erzeugt_dieselbe_protokollzeile(self, rf, admin_user):
        _archive(admin_user.facility)

        self._form(rf, admin_user.username).is_valid()

        entry = AuditLog.objects.filter(user=admin_user, action=AuditLog.Action.LOGIN_FAILED).first()
        assert entry is not None
        assert entry.detail.get("reason") == "facility_inactive"

    def test_aktive_einrichtung_kommt_durch(self, rf, admin_user):
        """Gegenprobe — die Sperre hängt am Flag, nicht am Formular-Umbau."""
        assert self._form(rf, admin_user.username).is_valid()

    def test_super_admin_bleibt_ausgenommen(self, rf, super_admin_user, facility):
        """Der Reaktivierungspfad darf sich nicht selbst aussperren."""
        super_admin_user.facility = facility
        super_admin_user.save(update_fields=["facility"])
        _archive(facility)

        assert self._form(rf, super_admin_user.username).is_valid()


# --- Ebene 2: Middleware -----------------------------------------------------


@pytest.mark.django_db
class TestMiddlewareFaelltFailClosed:
    def test_laufende_sitzung_verliert_den_zugriff(self, client, staff_user):
        """Der Kern: Archivieren wirkt SOFORT, nicht erst beim naechsten Login."""
        assert client.login(username="teststaff", password="testpass123")
        assert client.get("/").status_code == 200

        _archive(staff_user.facility)

        response = client.get("/")
        assert response.status_code == 403
        assert 'data-testid="facility-archived"' in response.content.decode()

    def test_abmelden_bleibt_erreichbar(self, client, staff_user):
        assert client.login(username="teststaff", password="testpass123")
        _archive(staff_user.facility)

        response = client.post("/logout/")

        assert response.status_code == 302, "ohne Ausnahme fuer /logout/ sitzt die Nutzerin fest"

    def test_ausnahmeliste_deckt_die_sackgassen_ab(self):
        for path in ("/login/", "/logout/", "/static/", "/health/"):
            assert path in ARCHIVED_EXEMPT_URLS

    def test_facility_admin_wird_mitgesperrt(self, rf, admin_user):
        """Er kann die Einrichtung nicht reaktivieren (FacilityAdmin ist
        super-only, Refs #1375) — eine Ausnahme liesse ihn nur weiter
        Fachdaten sehen."""
        _archive(admin_user.facility)

        request, response = _run_middleware(rf, admin_user, "/admin-mgmt/core/facility/")

        assert response != "DURCHGELASSEN"
        assert response.status_code == 403

    def test_super_admin_bleibt_unbeschraenkt(self, rf, super_admin_user):
        request, response = _run_middleware(rf, super_admin_user, "/system/")
        assert response == "DURCHGELASSEN"

    def test_aktive_einrichtung_passiert_unveraendert(self, rf, staff_user):
        request, response = _run_middleware(rf, staff_user)
        assert response == "DURCHGELASSEN"
        assert request.current_facility == staff_user.facility

    def test_sperr_403_traegt_den_offline_marker(self, rf, staff_user):
        """Refs #2067 (Cross-Review Finding 1): ohne diesen Header käme die
        Sperre beim Offline-Client nie an — ein nacktes 403 ist dort seit
        #1354 bewusst
        kein Purge-Grund."""
        _archive(staff_user.facility)

        _request, response = _run_middleware(rf, staff_user)

        assert response["X-Facility-Archived"] == "1"

    def test_sperr_403_raeumt_browser_speicher_ab(self, rf, staff_user):
        """Zweitschlag für den Navigations-Fall, in dem kein Offline-JS läuft —
        derselbe Mechanismus wie beim Logout. Cookies bleiben bewusst stehen,
        sonst trüge der Abmelden-Knopf auf der Sperrseite nicht."""
        _archive(staff_user.facility)

        _request, response = _run_middleware(rf, staff_user)

        assert response["Clear-Site-Data"] == '"storage", "cache"'
        assert "cookies" not in response["Clear-Site-Data"]

    def test_sperrseite_traegt_den_eigenstaendigen_purge(self, rf, staff_user):
        """Cross-Review Finding 1 (Rest): der Navigationsfall darf sich nicht
        allein auf ``Clear-Site-Data`` verlassen — Safari setzt den Header nur
        unvollständig um. Die Seite räumt deshalb selbst auf; das Modul ist
        abhängigkeitsfrei, weil hier kein ``base.html`` lädt."""
        _archive(staff_user.facility)

        _request, response = _run_middleware(rf, staff_user)

        body = response.content.decode()
        assert "data-facility-archived-purge" in body, "DOM-Marker fehlt — das Modul startet nicht"
        assert "js/facility-archived-purge.js" in body, "Purge-Modul wird nicht geladen"

    def test_durchgelassener_request_traegt_die_marker_nicht(self, rf, staff_user):
        _request, response = _run_middleware(rf, staff_user)
        assert response == "DURCHGELASSEN"

    def test_anonymer_request_passiert(self, rf, db):
        request = rf.get("/")
        request.user = AnonymousUser()
        response = FacilityScopeMiddleware(lambda r: "DURCHGELASSEN")(request)
        assert response == "DURCHGELASSEN"


@pytest.mark.postgres
@pytest.mark.django_db
class TestRlsVariableWirdGeleert:
    """Zweite Verteidigungslinie: selbst wenn ein Pfad die Sperre umginge,
    liefern die ``facility_isolation``-Policies ohne gesetzte GUC keine Zeile."""

    def test_geblockter_request_leert_die_session_variable(self, rf, staff_user):
        _archive(staff_user.facility)

        request, _response = _run_middleware(rf, staff_user)

        assert request.current_facility is None
        assert _current_facility_guc() == ""

    def test_durchgelassener_request_setzt_die_session_variable(self, rf, staff_user):
        request, _response = _run_middleware(rf, staff_user)

        assert _current_facility_guc() == str(staff_user.facility.pk)


@pytest.mark.django_db
class TestSperrseiteSprichtDieNutzersprache:
    """Refs #2067 (Cross-Review Finding 8): ``FacilityScopeMiddleware`` liegt
    VOR ``UserLanguageMiddleware`` und antwortet, bevor die Präferenz aktiviert
    wäre — die Seite läge sonst immer auf ``LANGUAGE_CODE``. Der User ist hier
    schon bekannt, die Middleware aktiviert die Präferenz deshalb selbst."""

    def test_englische_praeferenz_rendert_englisch(self, rf, staff_user):
        staff_user.preferred_language = "en"
        staff_user.save(update_fields=["preferred_language"])
        _archive(staff_user.facility)

        _request, response = _run_middleware(rf, staff_user)

        body = response.content.decode()
        assert 'lang="en"' in body
        assert "Facility archived" in body
        # Refs #2067 (Cross-Review N1): ``translation.override`` endet vor der
        # Rueckgabe, die aeussere LocaleMiddleware setzte den Header sonst aus
        # der wiederhergestellten Request-Sprache — englischer Body neben
        # ``Content-Language: de``.
        assert response["Content-Language"] == "en"

    def test_content_language_ueberlebt_die_volle_middleware_kette(self, client, staff_user):
        """Der eigentliche N1-Nachweis: ``LocaleMiddleware`` setzt den Header
        ausgangsseitig aus der wiederhergestellten Request-Sprache. Sie nutzt
        dafür ``setdefault`` — der explizit gesetzte Wert muss also stehen
        bleiben. Nur ein Lauf durch die echte Kette zeigt das; der
        RequestFactory-Test oben sieht die äußere Middleware nicht."""
        staff_user.preferred_language = "en"
        staff_user.save(update_fields=["preferred_language"])
        assert client.login(username="teststaff", password="testpass123")
        _archive(staff_user.facility)

        response = client.get("/", headers={"accept-language": "de"})

        assert response.status_code == 403
        assert response["Content-Language"] == "en"
        assert 'lang="en"' in response.content.decode()

    def test_ohne_praeferenz_bleibt_die_default_sprache(self, rf, staff_user):
        _archive(staff_user.facility)

        _request, response = _run_middleware(rf, staff_user)

        body = response.content.decode()
        assert 'lang="de"' in body
        assert "Einrichtung archiviert" in body
        assert response["Content-Language"] == "de"


# --- Doku: Raw-Shell-Hard-Delete-Fussfalle -----------------------------------


@pytest.mark.architecture
class TestAdminGuideDokumentiertDieHardDeleteFussfalle:
    """Der Hard-Delete einer Einrichtung stirbt mitten in der Kaskade am
    ``auditlog_immutable``-Trigger — und rollt dabei VOLLSTAENDIG zurueck:
    Django umschliesst den gesamten Collector mit einer Transaktion, auch aus
    ``manage.py shell``. Partielle Zerstoerung entsteht nur durch ein eigenes
    Skript, das Fachtabellen einzeln mit Zwischencommits leert.

    (Der frueher hier stehende Satz „autocommitted-gebatcht" war genau die
    Fehlannahme, die Cross-Review-Finding 6 widerlegt hat — die Guards unten
    halten sie jetzt aus beiden Handbuch-Fassungen heraus. Refs #2067, N2.)

    Das gehoert ins Administrationshandbuch, nicht in einen Code-Kommentar."""

    @staticmethod
    def _admin_guide(root: Path = REPO_ROOT) -> str:
        return (root / "docs" / "admin-guide.md").read_text(encoding="utf-8")

    def test_eigener_abschnitt_nennt_den_ausloesenden_trigger(self):
        text = self._admin_guide()
        assert "### 2.2c" in text, "Der Lifecycle-Abschnitt fehlt"
        assert "auditlog_immutable" in text

    def test_abschnitt_verweist_auf_das_offboarding_ticket(self):
        """Nutzer-Offboarding laeuft als eigenes Ticket (DS7 = #2051) — der
        Querverweis haelt beide Haelften des Lifecycles zusammen."""
        # The public tree keeps the reference as plain text (#2437), so check the number, not the URL.
        assert "#2051" in self._admin_guide()

    def test_abschnitt_nennt_das_archivieren_als_empfohlenen_weg(self):
        """Der Hard-Delete ist kein unterstuetzter Vorgang — der Abschnitt muss
        den gangbaren Weg nennen, nicht nur vor dem ungangbaren warnen."""
        assert "archivieren statt löschen" in self._admin_guide()

    @pytest.mark.parametrize("relative", ["docs/admin-guide.md", "docs/en/admin-guide.md"])
    def test_schadensbild_behauptet_keine_zwischencommits_der_kaskade(self, relative):
        """Cross-Review Finding 6: die erste Fassung behauptete, Djangos
        Collector committe im Autocommit-Betrieb einzelne Loeschstapel vor dem
        Audit-Trigger. Tatsaechlich umschliesst Django die GESAMTE Kaskade mit
        einer Transaktion (``deletion.py``: ``transaction.atomic(savepoint=
        False)``) — der Triggerfehler rollt auch aus ``manage.py shell``
        vollstaendig zurueck. Beide Sprachfassungen muessen das sagen."""
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for behauptung in ("in **Stapeln**", "in **batches**"):
            assert behauptung not in text, f"widerlegtes Schadensbild steht noch in {relative}"
        assert "auditlog_immutable" in text

    def test_echte_fussfalle_ist_das_skript_mit_zwischencommits(self):
        """Die Fussfalle existiert — nur an anderer Stelle: ein selbst
        geschriebenes Aufraeumskript, das Tabellen einzeln leert und dabei
        zwischendurch committet."""
        assert "committet" in self._admin_guide()

    def test_security_notes_halten_den_archivierungs_lifecycle_fest(self):
        """Review-Empfehlung zu B-8: nach dem Offline-Widerruf gehoert der
        Lifecycle auch in die Security Notes."""
        text = (REPO_ROOT / "docs" / "security-notes.md").read_text(encoding="utf-8")
        assert "Facility-Archivierung: Lifecycle des Zugriffsentzugs" in text
        assert "X-Facility-Archived" in text
        assert "Clear-Site-Data" in text

    def test_restrisiko_ist_ehrlich_beschrieben(self):
        """Cross-Review: „nach 48 h ist alles weg" war zu
        optimistisch. Die TTL verwirft nur den Server-Spiegel; ungesyncte
        Arbeit überlebt bewusst, und der Idle-Wipe entfernt bei ihr allein den
        Schlüssel (Refs #1353)."""
        text = (REPO_ROOT / "docs" / "security-notes.md").read_text(encoding="utf-8")
        assert "nur den Schlüssel" in text
        assert "Server-Spiegel" in text
