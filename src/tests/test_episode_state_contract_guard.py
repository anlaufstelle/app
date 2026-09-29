"""Episoden: Einrichtungsgrenze der handelnden Person und Zustandsvertrag unter Sperre.

Zwei Zusagen je Dienst (Anlegen, Bearbeiten, Abschliessen):

(a) Eine Fachkraft aus einer anderen Einrichtung und eine Fachkraft ohne
    Einrichtung (fail-closed) werden mit „Fall gehört zu einer anderen
    Einrichtung.“ abgewiesen — vor jeder Zeilensperre, ohne dass eine
    Episode entsteht oder sich aendert, ohne Audit- und Zeitstrom-Zeile.

(b) Geschrieben wird nur an einer lebenden Akte, gelesen auf den frisch
    gesperrten Zeilen in der Folge Person -> Fall (-> Episode). Die Instanz
    des Aufrufers stammt aus einem Lesevorgang vor der Sperre; was sich
    danach aendert, stellen die Tests ueber die Datenbank her, nie nur im
    Speicher: Fall im Papierkorb oder getilgt, Person im Papierkorb (auch als
    Quelle einer Zusammenfuehrung) oder anonymisiert, Episode geloescht, Fall
    zwischen Vorlesen und Sperre an eine andere Person gehaengt. Jeder dieser
    Zustaende endet in der Meldung des Zustandsvertrags.

Beobachtet werden Meldung, Datenbestand, Protokollzeilen und die tatsaechlich
gesperrten Tabellen in ihrer Reihenfolge (am ausgefuehrten SQL, nicht am
Wortlaut der Anweisung).
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, AuditLog, Case, Client, Episode, User
from core.services.case.episodes import create_episode
from core.services.case.state_contract import CASE_WRITE_GONE_MESSAGE, CASE_WRITE_REDACTED_MESSAGE
from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_FALL_FREMD = "Fall gehört zu einer anderen Einrichtung."
_AKTEURE = ["fremde_fachkraft", "fachkraft_ohne_einrichtung"]
_PERSON, _FALL, _EPISODE = "core_client", "core_case", "core_episode"


@pytest.fixture
def fremde_fachkraft(second_facility):
    return User.objects.create_user(
        username="fremdfachkraft-episode", role=User.Role.LEAD, facility=second_facility, is_staff=True
    )


@pytest.fixture
def fachkraft_ohne_einrichtung():
    return User.objects.create_user(
        username="ohneeinrichtung-episode", role=User.Role.LEAD, facility=None, is_staff=True
    )


@pytest.fixture
def andere_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Andere-Person-Episode", created_by=staff_user)


def _spuren():
    return AuditLog.objects.count(), Activity.objects.count()


def _fall_im_papierkorb(case_id, client_id):
    Case.objects.filter(pk=case_id).update(is_deleted=True, deleted_at=timezone.now())


def _fall_getilgt(case_id, client_id):
    Case.objects.filter(pk=case_id).update(anonymized_at=timezone.now())


def _person_im_papierkorb(case_id, client_id):
    """Auch die Quelle einer Zusammenfuehrung landet so im Papierkorb."""
    Client.objects.filter(pk=client_id).update(is_deleted=True, deleted_at=timezone.now())


def _person_anonymisiert(case_id, client_id):
    Client.objects.filter(pk=client_id).update(is_active=False)


#: (Zustand, Meldung, gesperrte Tabellen bis zur Absage)
AKTENZUSTAENDE = [
    pytest.param(_fall_im_papierkorb, CASE_WRITE_REDACTED_MESSAGE, [_PERSON, _FALL], id="fall-papierkorb"),
    pytest.param(_fall_getilgt, CASE_WRITE_REDACTED_MESSAGE, [_PERSON, _FALL], id="fall-getilgt"),
    pytest.param(_person_im_papierkorb, CLIENT_WRITE_REVOKED_MESSAGE, [_PERSON], id="person-papierkorb"),
    pytest.param(_person_anonymisiert, CLIENT_WRITE_REVOKED_MESSAGE, [_PERSON], id="person-anonymisiert"),
]


def _abgewiesen(aufruf, meldung, sperrfolge):
    """Ruft den Dienst; prueft Meldung, fehlende Spuren und die Sperren bis zur Absage."""
    spuren = _spuren()
    with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
        aufruf()
    assert excinfo.value.messages == [meldung]
    assert _spuren() == spuren
    assert m.sperrfolge() == sperrfolge


def _umhaengen_nach_personensperre(m, case_id, andere_person):
    """Haengt den Fall an eine andere Person — direkt nach der ersten Personensperre."""
    m.nach_sperre(_PERSON, lambda: Case.objects.filter(pk=case_id).update(client=andere_person))


class TestCreateEpisode:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, case_open):
        user = request.getfixturevalue(akteur)
        fall = Case.objects.get(pk=case_open.pk)

        _abgewiesen(lambda: create_episode(fall, user, title="Fremd"), _FALL_FREMD, [])

        assert not Episode.objects.filter(case_id=case_open.pk).exists()

    @pytest.mark.parametrize(("zustand", "meldung", "sperrfolge"), AKTENZUSTAENDE)
    def test_veraltete_akte_wird_abgewiesen(self, zustand, meldung, sperrfolge, case_open, staff_user):
        fall = Case.objects.get(pk=case_open.pk)
        zustand(fall.pk, fall.client_id)

        _abgewiesen(lambda: create_episode(fall, staff_user, title="Veraltet"), meldung, sperrfolge)

        assert not Episode.objects.filter(case_id=case_open.pk).exists()

    def test_umhaengen_zwischen_vorlesen_und_sperre(self, case_open, staff_user, andere_person):
        fall = Case.objects.get(pk=case_open.pk)
        spuren = _spuren()
        with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
            _umhaengen_nach_personensperre(m, fall.pk, andere_person)
            create_episode(fall, staff_user, title="Umgehaengt")
        assert m.offene_schranken() == []
        assert excinfo.value.messages == [CASE_WRITE_GONE_MESSAGE]
        assert _spuren() == spuren
        assert m.sperrfolge() == [_PERSON, _FALL]
        assert not Episode.objects.filter(case_id=case_open.pk).exists()

    def test_inzwischen_geschlossener_fall_wird_am_frischen_stand_abgewiesen(self, case_open, staff_user):
        fall = Case.objects.get(pk=case_open.pk)
        Case.objects.filter(pk=fall.pk).update(status=Case.Status.CLOSED, closed_at=timezone.now())

        with pytest.raises(ValueError) as excinfo:
            create_episode(fall, staff_user, title="Zu spaet")

        assert str(excinfo.value) == "Episoden können nur für offene Fälle erstellt werden."
        assert not Episode.objects.filter(case_id=case_open.pk).exists()

    def test_eigene_einrichtung_legt_an_der_gesperrten_akte_an(self, case_open, staff_user):
        fall = Case.objects.get(pk=case_open.pk)

        with mitschnitt() as m:
            episode = create_episode(fall, staff_user, title="Neu", description="Text")

        assert m.sperrfolge()[:2] == [_PERSON, _FALL]
        frisch = Episode.objects.get(pk=episode.pk)
        assert (frisch.case_id, frisch.title, frisch.description) == (case_open.pk, "Neu", "Text")
        assert frisch.created_by_id == staff_user.pk
