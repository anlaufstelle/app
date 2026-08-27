"""Parallele Löschanträge enden idempotent, nicht in einem 500 (Refs #2015).

Die vier ``request_*``-Creator sind get-then-create ohne Transaktion: Zwischen
dem „läuft schon einer?"-Lookup und dem ``create`` liegt ein Fenster, in dem
ein zweiter Request denselben Antrag anlegen kann. Der partielle Unique-Index
``unique_pending_deletion_request`` (``models/workitem.py``) macht den Verlierer
sicher — er bekommt einen ``IntegrityError``. Die Views fingen bisher nur
``ValidationError``, ein Doppelklick oder Doppel-POST endete deshalb im 500
statt in der vorgesehenen „Antrag existiert bereits"-Meldung.

Testaufbau: Der Race wird deterministisch nachgestellt, indem der
Vorab-Lookup der View auf ``None`` gepatcht wird — genau der Zustand, den ein
Request sieht, dessen Lookup lief, bevor der Gegenspieler committete. Der
``IntegrityError`` kommt danach aus der **echten** DB-Constraint, nicht aus
einem Mock — in allen vier Pfaden.

Zusaetzlich gepinnt (Refs #2015, Cross-Review): jede View laedt den Gewinner
des Rennens NACH, bevor sie seine Existenz behauptet. Verschwindet der
Antrag im selben Moment (Leitung entscheidet ihn), darf die View nicht
"laeuft bereits" melden — sie reicht den Fehler dann weiter.
"""

import threading
from unittest import mock

import pytest
from django.contrib.messages import get_messages
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Client, DeletionRequest, Event
from core.services import case as core_case_services
from core.services import client as core_client_services
from core.services.case import request_case_deletion
from core.services.client import request_client_deletion, request_client_merge
from core.services.events import request_deletion

pytestmark = pytest.mark.django_db

PENDING_CONFLICT = IntegrityError(
    'duplicate key value violates unique constraint "unique_pending_deletion_request"\n'
    "DETAIL:  Key (facility_id, target_type, target_id) already exists."
)


def _blind_first_call(real_lookup):
    """Nur der VORAB-Check der View sieht nichts — genau das ist die Race.

    Der zweite Aufruf (das Nachladen des Gewinners im ``except``-Zweig, Refs
    #2015) laeuft wieder gegen die echte DB. Ein pauschales
    ``return_value=None`` wuerde auch ihn blenden und damit den Nachlade-Guard
    testen statt den Idempotenz-Pfad.
    """
    state = {"first": True}

    def _lookup(*args, **kwargs):
        if state["first"]:
            state["first"] = False
            return None
        return real_lookup(*args, **kwargs)

    return _lookup


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


class TestClientDeletionRequestRace:
    def test_second_parallel_request_is_answered_idempotently(self, client, staff_user, client_qualified):
        """Refs #2015: der Verlierer sieht die „läuft bereits"-Meldung, kein 500."""
        request_client_deletion(client_qualified, staff_user, "Erster Antrag")
        client.force_login(staff_user)

        with (
            mock.patch(
                "core.views.client_deletion.get_pending_deletion_request",
                side_effect=_blind_first_call(core_client_services.get_pending_deletion_request),
            ),
            mock.patch("core.services.client.main.get_pending_deletion_request", return_value=None),
        ):
            response = client.post(
                reverse("core:client_delete_request", kwargs={"pk": client_qualified.pk}),
                {"reason": "Zweiter, paralleler Antrag"},
            )

        assert response.status_code == 302
        assert "läuft bereits ein Löschantrag" in " ".join(_messages(response))
        assert DeletionRequest.objects.filter(target_id=client_qualified.pk).count() == 1


class TestCaseDeletionRequestRace:
    def test_second_parallel_request_is_answered_idempotently(self, client, staff_user, facility, client_qualified):
        # Vier-Augen-Weg: der direkte Loeschweg gilt nur fuer Faelle einer
        # Person ohne Zusatzangaben und legt gar keinen Antrag an.
        case_open = Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Offener Fall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        request_case_deletion(case_open, staff_user, "Erster Antrag")
        client.force_login(staff_user)

        with (
            mock.patch(
                "core.views.case_deletion.get_pending_case_deletion_request",
                side_effect=_blind_first_call(core_case_services.get_pending_case_deletion_request),
            ),
            mock.patch("core.services.case.deletion.get_pending_case_deletion_request", return_value=None),
        ):
            response = client.post(
                reverse("core:case_delete_request", kwargs={"pk": case_open.pk}),
                {"reason": "Zweiter, paralleler Antrag"},
            )

        assert response.status_code == 302
        assert "läuft bereits ein Löschantrag" in " ".join(_messages(response))
        assert DeletionRequest.objects.filter(target_id=case_open.pk).count() == 1


class TestClientMergeRequestRace:
    def test_second_parallel_request_is_answered_idempotently(self, client, lead_user, facility):
        source = Client.objects.create(
            facility=facility,
            pseudonym="P-Merge-Quelle",
            contact_stage=Client.ContactStage.QUALIFIED,
        )
        target = Client.objects.create(
            facility=facility,
            pseudonym="P-Merge-Ziel",
            contact_stage=Client.ContactStage.QUALIFIED,
        )
        request_client_merge(source, target, lead_user, "Erster Antrag")
        client.force_login(lead_user)

        with (
            mock.patch(
                "core.views.client_merge.get_pending_deletion_request",
                side_effect=_blind_first_call(core_client_services.get_pending_deletion_request),
            ),
            mock.patch("core.services.client.merge.get_pending_merge_request", return_value=None),
            mock.patch("core.services.client.merge.get_pending_deletion_request", return_value=None),
        ):
            response = client.post(
                reverse("core:client_merge", kwargs={"pk": source.pk}),
                {"target": str(target.pk), "reason": "Zweiter, paralleler Antrag"},
            )

        assert response.status_code == 302
        assert "läuft bereits ein" in " ".join(_messages(response))
        assert DeletionRequest.objects.filter(target_id=source.pk).count() == 1


class TestEventDeletionRequestRace:
    def test_second_parallel_request_is_answered_idempotently(
        self, client, staff_user, facility, doc_type_contact, client_qualified
    ):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 10, "notiz": "Test"},
            created_by=staff_user,
        )
        request_deletion(event, staff_user, "Erster Antrag")
        client.force_login(staff_user)

        # Refs #2015 (Cross-Review): seit ``get_pending_event_deletion_request``
        # als benannter Helfer existiert, laesst sich auch dieser Pfad gegen den
        # ECHTEN DB-Constraint fahren — kein injizierter Fehler mehr. Gepatcht
        # wird nur der Lookup IM SERVICE (die View hat keinen Vorab-Check);
        # ihr Nachlade-Lookup bleibt scharf und findet den Gewinner.
        with mock.patch("core.services.events.deletion.get_pending_event_deletion_request", return_value=None):
            response = client.post(
                reverse("core:event_delete", kwargs={"pk": event.pk}),
                {"reason": "Zweiter, paralleler Antrag"},
            )

        assert response.status_code == 302
        # Identische Antwort wie beim gewoehnlichen Idempotenz-Treffer: der
        # Service liefert dort den bestehenden Antrag zurueck, und die View
        # gibt dieselbe Meldung aus. Entscheidend ist, dass kein 500 entsteht
        # und kein zweiter Antrag existiert.
        assert "Löschantrag wurde gestellt" in " ".join(_messages(response))
        assert DeletionRequest.objects.filter(target_id=event.pk).count() == 1


class TestWinnerIsReloadedBeforeClaimingItExists:
    """Refs #2015 (Cross-Review): „IntegrityError catch → pending Request neu
    laden → identische Response wie beim Erstfund".

    Das Nachladen ist nicht bloss Formsache: Der Constraint beweist nur, dass
    im Moment des ``create`` eine PENDING-Zeile existierte. Entscheidet eine
    Leitung sie unmittelbar danach, gibt es keinen laufenden Antrag mehr — die
    Meldung „laeuft bereits ein Loeschantrag" waere dann schlicht falsch. Die
    View reicht den Fehler in diesem Fall weiter (Muster ``EventCreateView``:
    Original nachladen, sonst re-raisen), statt eine Vermutung zu behaupten.
    """

    def test_client_path_reraises_when_the_winner_is_already_decided(self, client, staff_user, client_qualified):
        request_client_deletion(client_qualified, staff_user, "Erster Antrag")
        client.force_login(staff_user)

        with (
            mock.patch("core.views.client_deletion.get_pending_deletion_request", side_effect=[None, None]),
            mock.patch("core.services.client.main.get_pending_deletion_request", return_value=None),
        ):
            with pytest.raises(IntegrityError):
                client.post(
                    reverse("core:client_delete_request", kwargs={"pk": client_qualified.pk}),
                    {"reason": "Zweiter, paralleler Antrag"},
                )


class TestUnrelatedIntegrityErrorStillSurfaces:
    """Nur der Antrags-Unique-Constraint gilt als Duplikat — Muster
    ``is_idempotency_conflict`` (Refs #1443): ein fachfremder
    ``IntegrityError`` darf nicht als „Antrag existiert bereits" maskiert
    werden, sonst verschwindet ein echter Fehler hinter einer Erfolgsmeldung.
    """

    def test_foreign_integrity_error_is_not_swallowed(self, client, staff_user, client_qualified):
        client.force_login(staff_user)
        foreign = IntegrityError('null value in column "facility_id" violates not-null constraint')

        with mock.patch("core.views.client_deletion.create_or_get_client_deletion_request", side_effect=foreign):
            with pytest.raises(IntegrityError):
                client.post(
                    reverse("core:client_delete_request", kwargs={"pk": client_qualified.pk}),
                    {"reason": "Grund"},
                )


@pytest.mark.django_db(transaction=True)
class TestTrulyConcurrentClientDeletionRequest:
    """Refs #2015 (Re-Review): ZWEI wirklich parallele Requests, echte
    DB-Constraint, kein injizierter Fehler.

    Die konstruierten Tests oben beweisen das Catch-/Reload-Verhalten, aber
    nicht den realen Ablauf. Der ist seit dem Zeilenlock in
    ``request_client_deletion`` (Refs #2017) ein anderer: Der Verlierer laeuft
    nicht mehr in den ``IntegrityError``, sondern wartet auf der Sperre und
    bekommt danach vom Service den bestehenden Antrag **regulaer
    zurueckgeliefert**. Ohne ein Unterscheidungsmerkmal meldete die View ihm
    „Loeschantrag gestellt" — eine Erfolgsmeldung fuer etwas, das nie
    passiert ist.

    Synchronisiert wird nur der Einstieg: beide Threads passieren den
    Vorab-Check der View (der echt laeuft und real ``None`` liefert), bevor
    einer von beiden den Service betritt. Danach entscheidet allein die
    Datenbank.
    """

    def test_loser_gets_the_idempotency_answer_and_only_one_request_exists(
        self, facility, staff_user, django_user_model
    ):
        from django.db import close_old_connections
        from django.test import Client as DjangoTestClient

        person = Client.objects.create(
            facility=facility,
            pseudonym="P-Echt-Parallel",
            contact_stage=Client.ContactStage.QUALIFIED,
        )
        url = reverse("core:client_delete_request", kwargs={"pk": person.pk})

        entered = threading.Barrier(2, timeout=20)
        seen = set()
        seen_lock = threading.Lock()
        real_lookup = core_client_services.get_pending_deletion_request

        def _synced_lookup(*args, **kwargs):
            """Echter Lookup + einmaliger Treffpunkt je Thread.

            Nur der ERSTE Aufruf eines Threads (der Vorab-Check) wartet; das
            Nachladen im ``except``-Zweig darf nicht in die Barriere laufen.
            """
            result = real_lookup(*args, **kwargs)
            with seen_lock:
                first = threading.get_ident() not in seen
                seen.add(threading.get_ident())
            if first:
                entered.wait()
            return result

        results = {}

        def _post(slot):
            try:
                http = DjangoTestClient()
                http.force_login(staff_user)
                # Bewusst OHNE ``follow=True`` und ohne ``response.context``:
                # den Template-Kontext sammelt der Test-Client ueber das
                # GLOBALE ``template_rendered``-Signal ein, das keine Threads
                # kennt — unter Nebenlaeufigkeit landen die Kontexte beider
                # Requests im selben Store. ``get_messages(response.wsgi_request)``
                # haengt dagegen am Request-Objekt dieses einen Aufrufs.
                response = http.post(url, {"reason": f"Paralleler Antrag {slot}"})
                results[slot] = (response.status_code, _messages(response))
            finally:
                close_old_connections()

        with mock.patch("core.views.client_deletion.get_pending_deletion_request", side_effect=_synced_lookup):
            threads = [threading.Thread(target=_post, args=(slot,)) for slot in ("a", "b")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=40)

        assert not any(t.is_alive() for t in threads), "Deadlock in den parallelen Antraegen"
        assert set(results) == {"a", "b"}, results

        assert {code for code, _msgs in results.values()} == {302}, results
        flat = [msg for _code, msgs in results.values() for msg in msgs]
        gestellt = [m for m in flat if "Löschantrag gestellt" in m]
        laeuft_bereits = [m for m in flat if "läuft bereits ein Löschantrag" in m]

        # Genau einer legt an, genau einer bekommt die Idempotenz-Antwort.
        assert len(gestellt) == 1, flat
        assert len(laeuft_bereits) == 1, flat
        assert DeletionRequest.objects.filter(target_id=person.pk).count() == 1
