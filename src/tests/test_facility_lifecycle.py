"""Facility-Lifecycle: ``is_active`` wird durchgesetzt (Refs #2067).

``Facility.is_active`` war kosmetisch. Die repo-weite Suche nach einer
Auswertung des Flags lieferte null Treffer: die Middleware setzte
``request.current_facility`` ohne Pruefung, und der Login sah ausschliesslich
``User.is_active``. Eine „archivierte" Einrichtung arbeitete damit voll weiter —
Nutzerinnen meldeten sich an, dokumentierten, exportierten.

Der Entscheid ist **erzwingen, nicht entfernen** (Orchestrator, Welle B). Zwei
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
from django.test import RequestFactory

from core.middleware.facility_scope import ARCHIVED_EXEMPT_URLS, FacilityScopeMiddleware
from core.models import AuditLog

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def rf():
    return RequestFactory()


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


# --- Doku: Raw-Shell-Hard-Delete-Fussfalle -----------------------------------


@pytest.mark.architecture
class TestAdminGuideDokumentiertDieHardDeleteFussfalle:
    """Der Hard-Delete einer Einrichtung stirbt mitten in der Kaskade am
    ``auditlog_immutable``-Trigger. Aus einer rohen ORM-Shell laeuft er
    autocommitted-gebatcht — die Zerstoerung bleibt dann partiell. Das gehoert
    ins Administrationshandbuch, nicht in einen Code-Kommentar."""

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
        assert "issues/2051" in self._admin_guide()

    def test_abschnitt_nennt_das_archivieren_als_empfohlenen_weg(self):
        """Der Hard-Delete ist kein unterstuetzter Vorgang — der Abschnitt muss
        den gangbaren Weg nennen, nicht nur vor dem ungangbaren warnen."""
        assert "archivieren statt löschen" in self._admin_guide()
