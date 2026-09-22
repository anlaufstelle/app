"""Abgelehnte Statuswechsel kommen als Meldung an, nicht als Serverfehler.

Der Zustandsvertrag (Refs #2288/#2295) und das aeltere Art.-18-Gate
(Refs #2045) weisen Statuswechsel mit einer deutschen ``ValidationError`` ab.
Im HTTP-Pfad fing die keine der vier Views — aus einer fachlichen Absage wurde
ein **HTTP 500**, obwohl Changelog und ADR „mit einer Meldung ab" zusagen. Die
Fachkraft sah eine Fehlerseite ohne Grund; der Vorgang war weder erklaert noch
wiederholbar.

Die Guards fahren die vier Routen mit dem Testclient und pruefen den
ausgelieferten Vertrag:

* **Vollseiten-Routen** (``case_close``/``case_reopen``, Formular-POST mit
  Redirect): ``302`` auf die Fallseite plus Flash-Meldung im Wortlaut.
* **HTMX-Routen** (``goal_toggle``/``milestone_toggle``): ``409`` mit kurzem,
  markup-freiem Klartext — das Antwortmuster abgelehnter HTMX-Aktionen im
  Repo (``RetentionApproveView``, Refs #2109). Der globale
  ``htmx:responseError``-Toast (``static/js/htmx-errors.js``) zeigt genau
  diesen Text, und die veraltete Kachel bleibt unangetastet stehen.

In beiden Faellen gilt zusaetzlich: **kein Schreibvorgang**.

``raise_request_exception = False`` ist Absicht: geprueft wird die
HTTP-Antwort, die die Fachkraft bekommt. Ohne die Umstellung risse die
durchschlagende Ausnahme den Test mit einem Traceback ab, statt den 500 zu
zeigen, um den es geht.
"""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from core.models import Case
from core.models.outcome import Milestone, OutcomeGoal
from core.services.case.state_contract import STATUS_CHANGE_REDACTED_MESSAGE
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE


@pytest.fixture
def antwortender_client(client):
    """Testclient, der den 500 ausliefert statt die Ausnahme durchzureichen."""
    client.raise_request_exception = False
    return client


def _meldungen(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def _einschraenken(client_obj, user):
    from core.models import Client
    from core.services.client.restriction import restrict_processing

    restrict_processing(client_obj, user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)


def _tilgen_ohne_papierkorb(case):
    """Der Zustand, den die Personen-Anonymisierung am Fall hinterlaesst.

    ``_redact_cases_and_episodes`` stempelt ``anonymized_at``, ohne den Fall in
    den Papierkorb zu legen — er bleibt damit fuer ``cases_with_live_client``
    und die View sichtbar, waehrend der Vertrag ihn ablehnt. Per Queryset
    gesetzt, damit die Person dabei unberuehrt (und die Route erreichbar)
    bleibt.
    """
    Case.objects.filter(pk=case.pk).update(anonymized_at=timezone.now())


@pytest.mark.django_db
class TestVollseitenRoutenMeldenStattAbzustuerzen:
    """``case_close``/``case_reopen``: Redirect mit Flash-Meldung."""

    def test_close_case_an_eingeschraenkter_person(self, antwortender_client, lead_user, case_open, client_identified):
        _einschraenken(client_identified, lead_user)
        antwortender_client.force_login(lead_user)

        response = antwortender_client.post(reverse("core:case_close", kwargs={"pk": case_open.pk}))

        assert response.status_code == 302
        assert response.url == reverse("core:case_detail", kwargs={"pk": case_open.pk})
        assert _meldungen(response) == [str(PROCESSING_RESTRICTED_MESSAGE)]
        case_open.refresh_from_db()
        assert case_open.status == Case.Status.OPEN
        assert case_open.closed_at is None

    def test_close_case_an_getilgter_fallakte(self, antwortender_client, lead_user, case_open):
        _tilgen_ohne_papierkorb(case_open)
        antwortender_client.force_login(lead_user)

        response = antwortender_client.post(reverse("core:case_close", kwargs={"pk": case_open.pk}))

        assert response.status_code == 302
        assert _meldungen(response) == [str(STATUS_CHANGE_REDACTED_MESSAGE)]
        case_open.refresh_from_db()
        assert case_open.status == Case.Status.OPEN

    def test_reopen_case_an_eingeschraenkter_person(
        self, antwortender_client, lead_user, case_closed, client_identified
    ):
        _einschraenken(client_identified, lead_user)
        antwortender_client.force_login(lead_user)

        response = antwortender_client.post(reverse("core:case_reopen", kwargs={"pk": case_closed.pk}))

        assert response.status_code == 302
        assert response.url == reverse("core:case_detail", kwargs={"pk": case_closed.pk})
        assert _meldungen(response) == [str(PROCESSING_RESTRICTED_MESSAGE)]
        case_closed.refresh_from_db()
        assert case_closed.status == Case.Status.CLOSED

    def test_reopen_case_an_getilgter_fallakte(self, antwortender_client, lead_user, case_closed):
        _tilgen_ohne_papierkorb(case_closed)
        antwortender_client.force_login(lead_user)

        response = antwortender_client.post(reverse("core:case_reopen", kwargs={"pk": case_closed.pk}))

        assert response.status_code == 302
        assert _meldungen(response) == [str(STATUS_CHANGE_REDACTED_MESSAGE)]
        case_closed.refresh_from_db()
        assert case_closed.status == Case.Status.CLOSED


@pytest.mark.django_db
class TestHtmxRoutenMeldenStattAbzustuerzen:
    """``goal_toggle``/``milestone_toggle``: 409 mit kurzem Klartext."""

    def test_goal_toggle_an_eingeschraenkter_person(
        self, antwortender_client, staff_user, lead_user, case_open, outcome_goal, client_identified
    ):
        _einschraenken(client_identified, lead_user)
        antwortender_client.force_login(staff_user)

        response = antwortender_client.post(
            reverse("core:goal_toggle", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk})
        )

        assert response.status_code == 409
        inhalt = response.content.decode()
        assert inhalt == str(PROCESSING_RESTRICTED_MESSAGE)
        assert "<" not in inhalt
        outcome_goal.refresh_from_db()
        assert outcome_goal.is_achieved is False

    def test_absagen_des_zustandsvertrags_passen_in_den_fehler_toast(self):
        """Der Toast zeigt nur kurze, markup-freie Antworten (``htmx-errors.js``).

        Ueber 200 Zeichen faellt er auf seinen allgemeinen Text zurueck. Die
        beiden neuen Absagen des Zustandsvertrags bleiben darunter und kommen
        im Wortlaut an.

        **Bekannte Grenze, nicht von dieser Korrektur eingefuehrt:** die
        Art.-18-Meldung ist mit 264 Zeichen laenger — im HTMX-Pfad zeigt der
        Toast dafuer seinen allgemeinen Text, der genaue Grund steht in der
        Antwort (Netzwerk-Log) und, unveraendert, in jedem Vollseiten-Pfad.
        Vor dieser Korrektur war derselbe Fall ein 500.
        """
        from core.services.case.state_contract import STATUS_CHANGE_GONE_MESSAGE

        for meldung in (STATUS_CHANGE_REDACTED_MESSAGE, STATUS_CHANGE_GONE_MESSAGE):
            text = str(meldung)
            assert len(text) <= 200, f"zu lang fuer den Fehler-Toast: {len(text)}"
            assert "<" not in text

    def test_goal_toggle_an_getilgter_fallakte(self, antwortender_client, staff_user, case_open, outcome_goal):
        _tilgen_ohne_papierkorb(case_open)
        antwortender_client.force_login(staff_user)

        response = antwortender_client.post(
            reverse("core:goal_toggle", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk})
        )

        assert response.status_code == 409
        assert response.content.decode() == str(STATUS_CHANGE_REDACTED_MESSAGE)
        assert OutcomeGoal.objects.values_list("is_achieved", flat=True).get(pk=outcome_goal.pk) is False

    def test_milestone_toggle_an_eingeschraenkter_person(
        self, antwortender_client, staff_user, lead_user, case_open, milestone, client_identified
    ):
        _einschraenken(client_identified, lead_user)
        antwortender_client.force_login(staff_user)

        response = antwortender_client.post(
            reverse("core:milestone_toggle", kwargs={"case_pk": case_open.pk, "pk": milestone.pk})
        )

        assert response.status_code == 409
        assert response.content.decode() == str(PROCESSING_RESTRICTED_MESSAGE)
        milestone.refresh_from_db()
        assert milestone.is_completed is False

    def test_milestone_toggle_an_getilgter_fallakte(self, antwortender_client, staff_user, case_open, milestone):
        _tilgen_ohne_papierkorb(case_open)
        antwortender_client.force_login(staff_user)

        response = antwortender_client.post(
            reverse("core:milestone_toggle", kwargs={"case_pk": case_open.pk, "pk": milestone.pk})
        )

        assert response.status_code == 409
        assert response.content.decode() == str(STATUS_CHANGE_REDACTED_MESSAGE)
        assert Milestone.objects.values_list("is_completed", flat=True).get(pk=milestone.pk) is False


@pytest.mark.django_db
class TestNormalpfadeBleibenUnveraendert:
    """Gegenprobe: ohne Absage bleibt jede der vier Routen, wie sie war."""

    def test_close_case_bleibt_redirect_mit_erfolgsmeldung(self, client, lead_user, case_open):
        client.force_login(lead_user)
        response = client.post(reverse("core:case_close", kwargs={"pk": case_open.pk}))
        assert response.status_code == 302
        assert _meldungen(response) == ["Fall wurde geschlossen."]
        case_open.refresh_from_db()
        assert case_open.status == Case.Status.CLOSED

    def test_reopen_case_bleibt_redirect_mit_erfolgsmeldung(self, client, lead_user, case_closed):
        client.force_login(lead_user)
        response = client.post(reverse("core:case_reopen", kwargs={"pk": case_closed.pk}))
        assert response.status_code == 302
        assert _meldungen(response) == ["Fall wurde wiedereröffnet."]
        case_closed.refresh_from_db()
        assert case_closed.status == Case.Status.OPEN

    def test_goal_toggle_bleibt_partial_mit_200(self, client, staff_user, case_open, outcome_goal):
        client.force_login(staff_user)
        response = client.post(reverse("core:goal_toggle", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk}))
        assert response.status_code == 200
        assert 'id="goals-section"' in response.content.decode()
        outcome_goal.refresh_from_db()
        assert outcome_goal.is_achieved is True

    def test_milestone_toggle_bleibt_partial_mit_200(self, client, staff_user, case_open, milestone):
        client.force_login(staff_user)
        response = client.post(reverse("core:milestone_toggle", kwargs={"case_pk": case_open.pk, "pk": milestone.pk}))
        assert response.status_code == 200
        assert 'id="goals-section"' in response.content.decode()
        milestone.refresh_from_db()
        assert milestone.is_completed is True
