"""Episoden-Views: eine Absage des Dienstes kommt als Meldung an, nicht als Serverfehler.

Anlegen, Bearbeiten und Abschliessen einer Episode pruefen den Fall in der
View vorab (lebende Akte, beim Anlegen zusaetzlich der offene Status).
Entschieden wird aber erst im Dienst, unter Sperre und am frischen Stand:
Einschraenkung der Person nach Art. 18, eine inzwischen geloeschte oder
getilgte Akte, ein inzwischen geschlossener Fall. Lehnt der Dienst ab,
antwortet die View wie ihre Geschwister:

* Formular-POST: Redirect auf die Fallseite, die Meldung als Flash (wie beim
  Schliessen und Wiedereroeffnen eines Falls). Liegt die Akte inzwischen im
  Papierkorb, geht der Redirect auf die Fallliste — die Fallseite antwortete
  dort mit 404, und die Meldung ginge verloren (wie beim Bearbeiten eines
  Falls).
* HTMX-Aufruf: 409 mit kurzem Klartext und ``HX-Reswap: none`` (wie die
  Ziel- und Meilenstein-Aktionen), keine Flash-Meldung.

In jedem Fall entsteht oder aendert sich keine Episode.

Der parallele Vorgang wird direkt nach der Vorpruefung der View in die
Datenbank geschrieben (Muster ``test_case_update_lost_update_guard.py``) —
ausserhalb der Transaktion des Dienstes, wie ein bereits bestaetigter
Vorgang einer anderen Sitzung. ``raise_request_exception = False``: geprueft
wird die Antwort, die die Fachkraft bekommt, auch ein 500.
"""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Client
from core.models.episode import Episode
from core.services.case.state_contract import CASE_WRITE_REDACTED_MESSAGE
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE, restrict_processing

pytestmark = pytest.mark.django_db

_NUR_OFFENE = "Episoden können nur für offene Fälle erstellt werden."
_HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def antwortender_client(client, staff_user):
    client.raise_request_exception = False
    client.force_login(staff_user)
    return client


@pytest.fixture
def nach_vorpruefung(monkeypatch):
    """Haengt eine Aenderung des Bestands direkt hinter die Vorpruefung der View."""
    import core.views.case_episodes as episoden_views

    original = episoden_views.get_scoped_object

    def _einhaengen(aenderung):
        def _vorpruefung(*args, **kwargs):
            fall = original(*args, **kwargs)
            aenderung(fall)
            return fall

        monkeypatch.setattr(episoden_views, "get_scoped_object", _vorpruefung)

    return _einhaengen


def _anlegen(case, episode):
    url = reverse("core:episode_create", kwargs={"case_pk": case.pk})
    return url, {"title": "Neu", "description": "", "started_at": timezone.localdate().isoformat()}


def _bearbeiten(case, episode):
    url = reverse("core:episode_update", kwargs={"case_pk": case.pk, "pk": episode.pk})
    return url, {"title": "Geaendert", "description": "", "started_at": episode.started_at.isoformat(), "ended_at": ""}


def _abschliessen(case, episode):
    return reverse("core:episode_close", kwargs={"case_pk": case.pk, "pk": episode.pk}), {}


VIEWS = [
    pytest.param(_anlegen, id="anlegen"),
    pytest.param(_bearbeiten, id="bearbeiten"),
    pytest.param(_abschliessen, id="abschliessen"),
]
MODI = [pytest.param(False, id="formular"), pytest.param(True, id="htmx")]


def _episodenbestand(case_pk):
    return sorted(Episode.objects.filter(case_id=case_pk).values_list("pk", "title", "description", "ended_at"))


def _meldungen(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def _post(http, aufruf, case, episode, htmx):
    url, daten = aufruf(case, episode)
    return http.post(url, daten, **(_HTMX if htmx else {}))


def _abgesagt(response, htmx, meldung, ziel):
    """Formular: Redirect mit genau dieser Flash-Meldung. HTMX: 409, Klartext, kein Swap, kein Flash."""
    if htmx:
        assert response.status_code == 409
        assert response["HX-Reswap"] == "none"
        assert response.content.decode() == str(meldung)
        assert _meldungen(response) == []
    else:
        assert response.status_code == 302
        assert response.url == ziel
        assert _meldungen(response) == [str(meldung)]


class TestEingeschraenktePerson:
    @pytest.mark.parametrize("htmx", MODI)
    @pytest.mark.parametrize("aufruf", VIEWS)
    def test_absage_statt_serverfehler(
        self, antwortender_client, aufruf, htmx, case_open, episode, client_identified, lead_user
    ):
        restrict_processing(client_identified, lead_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        vorher = _episodenbestand(case_open.pk)

        response = _post(antwortender_client, aufruf, case_open, episode, htmx)

        _abgesagt(
            response, htmx, PROCESSING_RESTRICTED_MESSAGE, reverse("core:case_detail", kwargs={"pk": case_open.pk})
        )
        assert _episodenbestand(case_open.pk) == vorher


class TestAkteNachDerVorpruefungImPapierkorb:
    @pytest.mark.parametrize("htmx", MODI)
    @pytest.mark.parametrize("aufruf", VIEWS)
    def test_absage_statt_serverfehler(self, antwortender_client, nach_vorpruefung, aufruf, htmx, case_open, episode):
        vorher = _episodenbestand(case_open.pk)
        nach_vorpruefung(
            lambda fall: Case.objects.filter(pk=fall.pk).update(is_deleted=True, deleted_at=timezone.now())
        )

        response = _post(antwortender_client, aufruf, case_open, episode, htmx)

        _abgesagt(response, htmx, CASE_WRITE_REDACTED_MESSAGE, reverse("core:case_list"))
        assert _episodenbestand(case_open.pk) == vorher


class TestFallNachDerVorpruefungGeschlossen:
    @pytest.mark.parametrize("htmx", MODI)
    def test_anlegen_meldet_statt_serverfehler(self, antwortender_client, nach_vorpruefung, htmx, case_open, episode):
        vorher = _episodenbestand(case_open.pk)
        nach_vorpruefung(
            lambda fall: Case.objects.filter(pk=fall.pk).update(status=Case.Status.CLOSED, closed_at=timezone.now())
        )

        response = _post(antwortender_client, _anlegen, case_open, episode, htmx)

        _abgesagt(response, htmx, _NUR_OFFENE, reverse("core:case_detail", kwargs={"pk": case_open.pk}))
        assert _episodenbestand(case_open.pk) == vorher
