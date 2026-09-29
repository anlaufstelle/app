"""Bearbeiten einer Person: Einrichtungsgrenze und frischer Zustand unter der Sperre.

``update_client`` sperrt die Personenzeile (Art.-18-Gate) und schreibt danach
die Nutzdaten. Zwei Zusagen gelten unabhaengig von der aufrufenden Oberflaeche:

* **Einrichtung der handelnden Person.** Eine Fachkraft aus fremder Einrichtung
  oder ohne Einrichtung wird mit der Einrichtungsmeldung abgewiesen, bevor die
  Personenzeile gesperrt wird; Nutzdaten, Protokoll und Aktivitaeten bleiben
  unveraendert.
* **Frischer Zustand.** Die uebergebene Instanz kann aelter sein als der
  Datenbankstand: liegt die Person inzwischen im Papierkorb, ist sie endgueltig
  anonymisiert oder zusammengefuehrt, endet der Aufruf mit der vorhandenen
  Meldung — auch ohne Versions-Token, also ohne den optimistischen
  Konflikt-Guard. Der Zustand wird an der gesperrten Zeile gelesen.

Die veralteten Zustaende entstehen per ``update()`` in der Datenbank, nie nur im
Speicher; die Sperren werden am ausgefuehrten SQL beobachtet.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, AuditLog, Client
from core.services.client import update_client
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_FACILITY_MESSAGE = "Person gehört zu einer anderen Einrichtung."
_REVOKED_MESSAGE = (
    "Diese Person wurde gelöscht oder anonymisiert — es können keine Daten mehr zu ihr gespeichert werden."
)
_MERGED_MESSAGE = "Diese Person ist bereits zusammengeführt."
_ORIGINAL_NOTES = "Ausgangsstand"


@pytest.fixture
def person(client_identified):
    Client.objects.filter(pk=client_identified.pk).update(notes=_ORIGINAL_NOTES)
    return Client.objects.get(pk=client_identified.pk)


def _refused(client, user):
    with mitschnitt() as abfragen, pytest.raises(ValidationError) as excinfo:
        update_client(client, user, notes="Nachtrag, der nie ankommen darf")
    return excinfo.value.messages, abfragen


def _assert_unchanged(client):
    assert Client.objects.get(pk=client.pk).notes == _ORIGINAL_NOTES
    assert not AuditLog.objects.filter(action=AuditLog.Action.CLIENT_UPDATE, target_id=str(client.pk)).exists()
    assert not Activity.objects.filter(context__action=str(Activity.Action.CLIENT_UPDATED)).exists()


class TestHandelndePersonAusFremderEinrichtung:
    @pytest.mark.parametrize("akteur", ["foreign_staff", "facility_less_admin"])
    def test_abgewiesen_vor_der_personensperre(self, request, akteur, person):
        user = request.getfixturevalue(akteur)

        messages, abfragen = _refused(person, user)

        assert messages == [_FACILITY_MESSAGE]
        assert abfragen.sperrfolge() == []
        _assert_unchanged(person)

    def test_gegenprobe_lokale_fachkraft_bearbeitet(self, staff_user, person):
        with mitschnitt() as abfragen:
            update_client(person, staff_user, notes="Neuer Stand")

        assert Client.objects.get(pk=person.pk).notes == "Neuer Stand"
        assert abfragen.sperrfolge()[0] == "core_client"
        assert AuditLog.objects.filter(action=AuditLog.Action.CLIENT_UPDATE, target_id=str(person.pk)).exists()


class TestVeralteteInstanz:
    """Die Instanz stammt aus der Bearbeitungsmaske, die Datenbank ist inzwischen weiter."""

    def test_inzwischen_im_papierkorb(self, staff_user, person):
        Client.objects.filter(pk=person.pk).update(is_deleted=True, deleted_at=timezone.now())

        messages, abfragen = _refused(person, staff_user)

        assert messages == [_REVOKED_MESSAGE]
        assert abfragen.sperrfolge()[0] == "core_client"
        _assert_unchanged(person)

    def test_inzwischen_endgueltig_anonymisiert(self, staff_user, person):
        Client.objects.filter(pk=person.pk).update(anonymized_at=timezone.now(), is_active=False)

        messages, abfragen = _refused(person, staff_user)

        assert messages == [_REVOKED_MESSAGE]
        assert abfragen.sperrfolge()[0] == "core_client"
        _assert_unchanged(person)

    def test_inzwischen_zusammengefuehrt(self, staff_user, person, facility):
        target = Client.objects.create(facility=facility, pseudonym="Ziel-Bearbeitung")
        Client.objects.filter(pk=person.pk).update(merged_into=target, is_deleted=True, deleted_at=timezone.now())

        messages, abfragen = _refused(person, staff_user)

        assert messages == [_MERGED_MESSAGE]
        assert abfragen.sperrfolge()[0] == "core_client"
        _assert_unchanged(person)

    def test_zustand_vor_dem_versions_guard(self, staff_user, person):
        """Auch mit veraltetem Versions-Token nennt die Meldung den Zustand, nicht den Konflikt."""
        stale_version = person.updated_at
        Client.objects.filter(pk=person.pk).update(
            is_deleted=True, deleted_at=timezone.now(), updated_at=timezone.now()
        )

        with pytest.raises(ValidationError) as excinfo:
            update_client(person, staff_user, expected_updated_at=stale_version, notes="Nachtrag")

        assert excinfo.value.messages == [_REVOKED_MESSAGE]
        _assert_unchanged(person)


class TestSperreAnDerEinrichtungDerHandelndenPerson:
    """Schon die erste Personensperre ist an die Einrichtung der handelnden Person gebunden.

    Ordnet die Datenbank die Person inzwischen einer anderen Einrichtung zu als
    die uebergebene Instanz, sperrt der Aufruf die fremde Zeile nicht — er
    findet sie nicht (wie bisher ``DoesNotExist``) und schreibt nichts.
    """

    def test_abweichender_datenbankstand_sperrt_keine_fremde_zeile(self, staff_user, person, facility, second_facility):
        Client.objects.filter(pk=person.pk).update(facility=second_facility)

        with mitschnitt() as abfragen, pytest.raises(Client.DoesNotExist):
            update_client(person, staff_user, notes="Nachtrag, der nie ankommen darf")

        client_locks = abfragen.sperren("core_client")
        assert client_locks
        assert all(zugriff.traegt(facility_id=[facility.pk]) for zugriff in client_locks), client_locks
        _assert_unchanged(person)

    def test_gegenprobe_erste_sperre_traegt_die_einrichtung(self, staff_user, person, facility):
        with mitschnitt() as abfragen:
            update_client(person, staff_user, notes="Neuer Stand")

        assert abfragen.sperrfolge()[0] == "core_client"
        assert abfragen.sperren("core_client")[0].traegt(facility_id=[facility.pk])
        assert Client.objects.get(pk=person.pk).notes == "Neuer Stand"
