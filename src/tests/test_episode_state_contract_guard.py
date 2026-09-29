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

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, AuditLog, Case, Client, Episode, User
from core.services.case.episodes import close_episode, create_episode, update_episode
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

        with pytest.raises(ValidationError) as excinfo:
            create_episode(fall, staff_user, title="Zu spaet")

        assert excinfo.value.messages == ["Episoden können nur für offene Fälle erstellt werden."]
        assert not Episode.objects.filter(case_id=case_open.pk).exists()

    def test_eigene_einrichtung_legt_an_der_gesperrten_akte_an(self, case_open, staff_user):
        fall = Case.objects.get(pk=case_open.pk)

        with mitschnitt() as m:
            episode = create_episode(fall, staff_user, title="Neu", description="Text")

        assert m.sperrfolge()[:2] == [_PERSON, _FALL]
        frisch = Episode.objects.get(pk=episode.pk)
        assert (frisch.case_id, frisch.title, frisch.description) == (case_open.pk, "Neu", "Text")
        assert frisch.created_by_id == staff_user.pk


def _episode_geloescht(episode_id):
    Episode.objects.filter(pk=episode_id).update(is_deleted=True, deleted_at=timezone.now())


def _episodenstand(episode_id):
    return Episode.objects.filter(pk=episode_id).values_list("title", "description", "ended_at").get()


def _episode_entfernt(episode_id):
    """Die Episodenzeile ist physisch weg; die Instanz des Aufrufers bleibt bestehen."""
    Episode.objects.filter(pk=episode_id).delete()


class TestFehlendeEpisode:
    """Eine Episode, die schon beim Vorlesen fehlt, endet vor jeder Zeilensperre.

    Ohne Episodenzeile gibt es keine Einrichtung, mit der sich die handelnde
    Person vergleichen liesse. Die Absage muss deshalb schon an dieser Stelle
    fallen — sonst naehme eine Fachkraft aus einer anderen Einrichtung (oder
    ohne Einrichtung) die Personen- und Fallsperre einer fremden Akte, bevor
    der Aufruf scheitert. Dieselbe Absage gilt fuer die eigene Einrichtung.
    """

    @pytest.mark.parametrize("akteur", [*_AKTEURE, "staff_user"])
    def test_bearbeiten(self, request, akteur, episode):
        user = request.getfixturevalue(akteur)
        alt = Episode.objects.select_related("case").get(pk=episode.pk)
        _episode_entfernt(episode.pk)

        _abgewiesen(lambda: update_episode(alt, user, title="Fehlt"), CASE_WRITE_GONE_MESSAGE, [])

        assert not Episode.objects.filter(pk=episode.pk).exists()

    @pytest.mark.parametrize("akteur", [*_AKTEURE, "staff_user"])
    def test_abschliessen(self, request, akteur, episode):
        user = request.getfixturevalue(akteur)
        alt = Episode.objects.select_related("case").get(pk=episode.pk)
        _episode_entfernt(episode.pk)

        _abgewiesen(lambda: close_episode(alt, user), CASE_WRITE_GONE_MESSAGE, [])

        assert not Episode.objects.filter(pk=episode.pk).exists()


class TestUpdateEpisode:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, episode):
        user = request.getfixturevalue(akteur)
        vorher = _episodenstand(episode.pk)

        _abgewiesen(lambda: update_episode(Episode.objects.get(pk=episode.pk), user, title="Fremd"), _FALL_FREMD, [])

        assert _episodenstand(episode.pk) == vorher

    def test_unzulaessiges_feld_behaelt_vorrang_vor_der_einrichtungsgrenze(self, episode, fremde_fachkraft):
        with pytest.raises(ValueError, match="darf nicht aktualisiert werden"):
            update_episode(Episode.objects.get(pk=episode.pk), fremde_fachkraft, case=None)

    @pytest.mark.parametrize(("zustand", "meldung", "sperrfolge"), AKTENZUSTAENDE)
    def test_veraltete_akte_wird_abgewiesen(self, zustand, meldung, sperrfolge, episode, staff_user):
        alt = Episode.objects.select_related("case").get(pk=episode.pk)
        vorher = _episodenstand(episode.pk)
        zustand(alt.case_id, alt.case.client_id)

        _abgewiesen(lambda: update_episode(alt, staff_user, title="Veraltet"), meldung, sperrfolge)

        assert _episodenstand(episode.pk) == vorher

    def test_geloeschte_episode_wird_abgewiesen(self, episode, staff_user):
        alt = Episode.objects.get(pk=episode.pk)
        vorher = _episodenstand(episode.pk)
        _episode_geloescht(episode.pk)

        _abgewiesen(
            lambda: update_episode(alt, staff_user, title="Geloescht"),
            CASE_WRITE_GONE_MESSAGE,
            [_PERSON, _FALL, _EPISODE],
        )

        assert _episodenstand(episode.pk) == vorher

    def test_umhaengen_zwischen_vorlesen_und_sperre(self, episode, staff_user, andere_person):
        alt = Episode.objects.get(pk=episode.pk)
        vorher = _episodenstand(episode.pk)
        spuren = _spuren()
        with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
            _umhaengen_nach_personensperre(m, alt.case_id, andere_person)
            update_episode(alt, staff_user, title="Umgehaengt")
        assert m.offene_schranken() == []
        assert excinfo.value.messages == [CASE_WRITE_GONE_MESSAGE]
        assert _spuren() == spuren
        assert _episodenstand(episode.pk) == vorher

    def test_aufruf_ohne_felder_laeuft_nach_dem_vertrag(self, episode, staff_user):
        alt = Episode.objects.select_related("case").get(pk=episode.pk)
        _fall_im_papierkorb(alt.case_id, alt.case.client_id)

        _abgewiesen(lambda: update_episode(alt, staff_user), CASE_WRITE_REDACTED_MESSAGE, [_PERSON, _FALL])

    def test_eigene_einrichtung_schreibt_nach_der_sperrfolge(self, episode, staff_user):
        with mitschnitt() as m:
            update_episode(Episode.objects.get(pk=episode.pk), staff_user, title="Neu")

        assert m.sperrfolge()[:3] == [_PERSON, _FALL, _EPISODE]
        assert _episodenstand(episode.pk)[0] == "Neu"
        assert AuditLog.objects.filter(action=AuditLog.Action.EPISODE_UPDATE, target_id=str(episode.pk)).count() == 1


class TestCloseEpisode:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, episode):
        user = request.getfixturevalue(akteur)

        _abgewiesen(lambda: close_episode(Episode.objects.get(pk=episode.pk), user), _FALL_FREMD, [])

        assert _episodenstand(episode.pk)[2] is None

    @pytest.mark.parametrize(("zustand", "meldung", "sperrfolge"), AKTENZUSTAENDE)
    def test_veraltete_akte_wird_abgewiesen(self, zustand, meldung, sperrfolge, episode, staff_user):
        alt = Episode.objects.select_related("case").get(pk=episode.pk)
        zustand(alt.case_id, alt.case.client_id)

        _abgewiesen(lambda: close_episode(alt, staff_user), meldung, sperrfolge)

        assert _episodenstand(episode.pk)[2] is None

    def test_geloeschte_episode_wird_abgewiesen(self, episode, staff_user):
        alt = Episode.objects.get(pk=episode.pk)
        _episode_geloescht(episode.pk)

        _abgewiesen(lambda: close_episode(alt, staff_user), CASE_WRITE_GONE_MESSAGE, [_PERSON, _FALL, _EPISODE])

        assert _episodenstand(episode.pk)[2] is None

    def test_umhaengen_zwischen_vorlesen_und_sperre(self, episode, staff_user, andere_person):
        alt = Episode.objects.get(pk=episode.pk)
        spuren = _spuren()
        with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
            _umhaengen_nach_personensperre(m, alt.case_id, andere_person)
            close_episode(alt, staff_user)
        assert m.offene_schranken() == []
        assert excinfo.value.messages == [CASE_WRITE_GONE_MESSAGE]
        assert _spuren() == spuren
        assert _episodenstand(episode.pk)[2] is None

    def test_parallel_abgeschlossene_episode_behaelt_ihr_ende(self, episode, staff_user):
        """Entschieden wird am frischen Stand: ein zwischenzeitliches Ende wird nicht ueberschrieben."""
        alt = Episode.objects.get(pk=episode.pk)
        frueheres_ende = timezone.now().date() - timedelta(days=3)
        Episode.objects.filter(pk=episode.pk).update(ended_at=frueheres_ende)

        ergebnis = close_episode(alt, staff_user)

        assert _episodenstand(episode.pk)[2] == frueheres_ende
        assert ergebnis.ended_at == frueheres_ende

    def test_eigene_einrichtung_schliesst_nach_der_sperrfolge(self, episode, staff_user):
        alt = Episode.objects.get(pk=episode.pk)

        with mitschnitt() as m:
            ergebnis = close_episode(alt, staff_user)

        assert m.sperrfolge()[:3] == [_PERSON, _FALL, _EPISODE]
        heute = timezone.now().date()
        assert _episodenstand(episode.pk)[2] == heute
        assert ergebnis is alt
        assert alt.ended_at == heute
