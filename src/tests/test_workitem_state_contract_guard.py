"""Aufgabendienste entscheiden am frischen, gesperrten Stand.

Eine Person, die nach dem Laden in den Papierkorb gelegt, deaktiviert oder
zusammengefuehrt wurde, nimmt keine neuen oder geaenderten Aufgabendaten mehr
an; eine inzwischen geloeschte Aufgabe wird nicht mehr geaendert. Der veraltete
Zustand wird dafuer in der Datenbank hergestellt (``update()`` bzw. eine zweite
Instanz), nie nur im Speicher. Beobachtet werden Meldung, geschriebene Zeilen,
Audit- und Zeitstrom-Eintraege und die Reihenfolge der gesperrten Tabellen
(Person vor Aufgabe).

Die Views bilden eine solche Absage des Dienstes ab, statt mit HTTP 500 zu
enden.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import Activity, AuditLog, Client, WorkItem
from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

WIDERRUFE = ["papierkorb", "inaktiv", "zusammengefuehrt"]


def _widerrufen(person, art):
    """Stellt den Widerruf in der Datenbank her — die geladene Instanz bleibt veraltet."""
    zeilen = Client.objects.filter(pk=person.pk)
    if art == "papierkorb":
        zeilen.update(is_deleted=True, deleted_at=timezone.now())
    elif art == "inaktiv":
        zeilen.update(is_active=False)
    else:
        ziel = Client.objects.create(facility_id=person.facility_id, pseudonym="Ziel der Zusammenführung")
        zeilen.update(merged_into=ziel, is_deleted=True, deleted_at=timezone.now())


def _zaehler():
    return WorkItem.objects.count(), AuditLog.objects.count(), Activity.objects.count()


def _assert_refused(aufruf, meldung):
    vorher = _zaehler()
    with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
        aufruf()
    assert excinfo.value.messages == [str(meldung)]
    assert _zaehler() == vorher
    return m


def _person_vor_aufgabe(m):
    folge = m.sperrfolge()
    if "core_workitem" in folge:
        assert "core_client" in folge, folge
        assert folge.index("core_client") < folge.index("core_workitem"), folge
    return folge


class TestAnlegen:
    @pytest.mark.parametrize("art", WIDERRUFE)
    def test_widerrufene_person(self, facility, client_identified, staff_user, art):
        from core.services.case import create_workitem

        _widerrufen(client_identified, art)
        _assert_refused(
            lambda: create_workitem(facility, staff_user, client=client_identified, title="Neu"),
            CLIENT_WRITE_REVOKED_MESSAGE,
        )
        assert not WorkItem.objects.filter(title="Neu").exists()


class TestAnlegenUeberDieView:
    """Die Person verschwindet zwischen Formularpruefung und Dienst."""

    def _post(self, client, person, **header):
        return client.post(
            reverse("core:workitem_create"),
            {"item_type": "task", "title": "Neu im Rennen", "priority": "normal", "client": str(person.pk)},
            **header,
        )

    def test_formular_zeigt_die_meldung(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        vorher = AuditLog.objects.count()
        with mitschnitt() as m:
            m.nach_sperre("core_client", lambda: _widerrufen(client_identified, "papierkorb"))
            response = self._post(client, client_identified)
        assert m.offene_schranken() == [], "das Rennen wurde nicht ausgeloest"
        assert response.status_code == 200
        assert str(CLIENT_WRITE_REVOKED_MESSAGE) in response.content.decode()
        assert not WorkItem.objects.filter(title="Neu im Rennen").exists()
        assert AuditLog.objects.count() == vorher

    def test_offline_replay_bekommt_422(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        with mitschnitt() as m:
            m.nach_sperre("core_client", lambda: _widerrufen(client_identified, "papierkorb"))
            response = self._post(client, client_identified, HTTP_ACCEPT="application/json")
        assert m.offene_schranken() == [], "das Rennen wurde nicht ausgeloest"
        assert response.status_code == 422
        assert response.json()["errors"]["__all__"][0]["message"] == str(CLIENT_WRITE_REVOKED_MESSAGE)
        assert not WorkItem.objects.filter(title="Neu im Rennen").exists()
