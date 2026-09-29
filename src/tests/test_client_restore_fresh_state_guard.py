"""Wiederherstellen einer Person: Einrichtungsgrenze und frischer Zustand.

``restore_client`` holt eine Person aus dem Papierkorb zurueck. Zwei Zusagen
gelten dabei unabhaengig von der aufrufenden Oberflaeche:

* **Einrichtung der handelnden Person.** Wer nicht zur Einrichtung der Person
  gehoert (fremde Einrichtung oder gar keine), wird mit der Einrichtungsmeldung
  abgewiesen — bevor irgendeine Personenzeile gesperrt wird. Die Person bleibt
  im Papierkorb, es entsteht kein ``CLIENT_RESTORED``-Eintrag.
* **Frischer Zustand.** Die uebergebene Instanz kann aelter sein als der
  Datenbankstand: zwischen dem Laden der Papierkorb-Liste und dem Klick kann die
  Person bereits wiederhergestellt, endgueltig anonymisiert oder zusammengefuehrt
  worden sein. Geprueft wird deshalb an der gesperrten, neu gelesenen Zeile — mit
  den vorhandenen Meldungen in unveraenderter Reihenfolge.

Die veralteten Zustaende entstehen per ``update()`` in der Datenbank, nie nur im
Speicher; die Sperren werden am ausgefuehrten SQL beobachtet.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog, Client, User
from core.services.client import restore_client
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_FACILITY_MESSAGE = "Person gehört zu einer anderen Einrichtung."
_MERGED_MESSAGE = (
    "Diese Person wurde mit einer anderen zusammengeführt — Wiederherstellen nur über das Aufheben der Zusammenführung."
)
_NOT_IN_TRASH_MESSAGE = "Person ist nicht im Papierkorb."
_ANONYMIZED_MESSAGE = "Diese Person wurde endgültig anonymisiert und kann nicht wiederhergestellt werden."


@pytest.fixture
def foreign_admin(second_facility):
    user = User.objects.create_user(
        username="fremdeverwaltung_restore",
        role=User.Role.FACILITY_ADMIN,
        facility=second_facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


def _restored_entries(client):
    return AuditLog.objects.filter(action=AuditLog.Action.CLIENT_RESTORED, target_id=str(client.pk)).count()


def _refuse(client, user):
    with mitschnitt() as abfragen, pytest.raises(ValidationError) as excinfo:
        restore_client(client, user)
    return excinfo.value.messages, abfragen


class TestHandelndePersonAusFremderEinrichtung:
    @pytest.mark.parametrize("akteur", ["foreign_admin", "facility_less_admin"])
    def test_abgewiesen_vor_jeder_personensperre(self, request, akteur, client_trashed):
        user = request.getfixturevalue(akteur)

        messages, abfragen = _refuse(client_trashed, user)

        assert messages == [_FACILITY_MESSAGE]
        assert abfragen.sperren("core_client") == []
        row = Client.objects.get(pk=client_trashed.pk)
        assert row.is_deleted is True
        assert row.deleted_at is not None
        assert _restored_entries(client_trashed) == 0

    def test_gegenprobe_lokale_verwaltung_stellt_wieder_her(self, admin_user, client_trashed, facility):
        with mitschnitt() as abfragen:
            restore_client(client_trashed, admin_user)

        row = Client.objects.get(pk=client_trashed.pk)
        assert row.is_deleted is False
        assert row.deleted_at is None
        assert _restored_entries(client_trashed) == 1
        sperren = abfragen.sperren("core_client")
        assert sperren, "die Personenzeile wird vor dem Wiederherstellen gesperrt gelesen"
        assert sperren[0].traegt(id=[client_trashed.pk], facility_id=[facility.pk])
        assert abfragen.sperrfolge()[0] == "core_client"


class TestVeralteteInstanz:
    """Die Instanz stammt aus dem Papierkorb, die Datenbank ist inzwischen weiter."""

    def test_inzwischen_wiederhergestellt(self, admin_user, client_trashed):
        stale = Client.objects.get(pk=client_trashed.pk)
        Client.objects.filter(pk=client_trashed.pk).update(is_deleted=False, deleted_at=None, deleted_by=None)

        messages, abfragen = _refuse(stale, admin_user)

        assert messages == [_NOT_IN_TRASH_MESSAGE]
        assert abfragen.sperren("core_client"), "der Zustand wird an der gesperrten Zeile gelesen"
        assert _restored_entries(client_trashed) == 0

    def test_inzwischen_endgueltig_anonymisiert(self, admin_user, client_trashed):
        stale = Client.objects.get(pk=client_trashed.pk)
        Client.objects.filter(pk=client_trashed.pk).update(anonymized_at=timezone.now(), is_active=False)

        messages, abfragen = _refuse(stale, admin_user)

        assert messages == [_ANONYMIZED_MESSAGE]
        assert abfragen.sperren("core_client")
        row = Client.objects.get(pk=client_trashed.pk)
        assert row.is_deleted is True
        assert row.anonymized_at is not None
        assert _restored_entries(client_trashed) == 0

    def test_inzwischen_zusammengefuehrt(self, admin_user, client_trashed, client_identified):
        stale = Client.objects.get(pk=client_trashed.pk)
        Client.objects.filter(pk=client_trashed.pk).update(merged_into=client_identified)

        messages, abfragen = _refuse(stale, admin_user)

        assert messages == [_MERGED_MESSAGE]
        assert abfragen.sperren("core_client")
        row = Client.objects.get(pk=client_trashed.pk)
        assert row.is_deleted is True
        assert row.merged_into_id == client_identified.pk
        assert _restored_entries(client_trashed) == 0

    def test_reihenfolge_der_meldungen_an_der_gesperrten_zeile(self, admin_user, client_trashed, client_identified):
        """Zusammengefuehrt UND anonymisiert UND nicht mehr im Papierkorb: die erste Meldung gewinnt."""
        stale = Client.objects.get(pk=client_trashed.pk)
        Client.objects.filter(pk=client_trashed.pk).update(
            merged_into=client_identified, is_deleted=False, anonymized_at=timezone.now()
        )

        messages, _ = _refuse(stale, admin_user)

        assert messages == [_MERGED_MESSAGE]

    def test_veraltete_lebende_instanz_wird_trotzdem_wiederhergestellt(self, admin_user, client_trashed):
        """Umgekehrt: die Instanz sieht noch „aktiv", die Datenbank sagt „Papierkorb" — der frische Stand zaehlt."""
        stale = Client.objects.get(pk=client_trashed.pk)
        stale.is_deleted = False
        stale.deleted_at = None

        restore_client(stale, admin_user)

        row = Client.objects.get(pk=client_trashed.pk)
        assert row.is_deleted is False
        assert _restored_entries(client_trashed) == 1
        assert stale.is_deleted is False
        assert stale.deleted_at is None
