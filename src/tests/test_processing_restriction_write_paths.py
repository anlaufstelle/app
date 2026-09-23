"""Art.-18-Gate über ALLE fachlichen Schreibpfade (Cross-Review-Befunde 1, 3, 4).

Die erste Fassung von #2045
gatete nur ``update_client`` und ``resolve_writable_client`` — also die
formularbasierte Personen-Bearbeitung und die Neuzuordnung. Der Cross-Review
hat aufgezeigt, dass damit CSV-Import, Ereignis-Update, Ereignis-Loeschung,
Fall-Operationen, Episoden, Ziele, Aufgaben, Merge und der endgueltige
Anhang-Purge offen blieben. Die Akzeptanzforderung „schreibgeschuetzt
ausserhalb des Berichtigungsflows" war damit nicht erfuellt.

**Die fachliche Trennlinie** (Entscheid, hier als Testmatrix):

* **Blockiert** — fachliche Nutzung und Aenderung: Ereignis anlegen/aendern/
  direkt loeschen, CSV-Import, Fall-Operationen, Episoden/Ziele/Meilensteine,
  Aufgaben (einzeln und im Stapel), Zusammenfuehrung, Anhang-Purge.
* **Erlaubt** — Betroffenenrechte: Berichtigungs-Flow, Loeschantrags-Workflow
  (Art. 17 gilt waehrend der Einschraenkung weiter), Auskunft/Export sowie das
  Setzen und Aufheben des Sperrvermerks selbst.

Der zweite Teil ist der wichtigere: ein Gate, das die Betroffenenrechte
mitblockiert, macht die Einschraenkung zur Sackgasse und schadet genau der
Person, die sie schuetzen soll.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError

from core.models import Client, Event, WorkItem
from core.services.client.restriction import restrict_processing

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture
def restricted_client(client_identified, staff_user):
    restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
    client_identified.refresh_from_db()
    return client_identified


def _occurred():
    return datetime(2026, 5, 3, 14, 32, tzinfo=BERLIN)


# ---------------------------------------------------------------------------
# BLOCKIERT — fachliche Nutzung und Änderung
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEventPathsBlocked:
    def test_create_event_blocked(self, facility, restricted_client, doc_type_contact, staff_user):
        from core.services.events import create_event

        with pytest.raises(ValidationError):
            create_event(
                facility,
                staff_user,
                doc_type_contact,
                _occurred(),
                {},
                client=restricted_client,
            )
        assert not Event.objects.filter(client=restricted_client).exists()

    def test_update_event_blocked(self, facility, restricted_client, doc_type_contact, staff_user):
        """Deckt zugleich Offline-Replay und Anhang-Aenderungen ab — alle drei
        laufen durch ``update_event``."""
        from core.services.events import update_event

        event = Event.objects.create(
            facility=facility,
            client=restricted_client,
            document_type=doc_type_contact,
            occurred_at=_occurred(),
            created_by=staff_user,
        )
        with pytest.raises(ValidationError):
            update_event(event, staff_user, {"notiz": "neu"})

    def test_anonymous_event_stays_writable(self, facility, doc_type_contact, staff_user):
        """Gegenprobe: ohne Personenbezug gibt es keine Einschraenkung."""
        from core.services.events import create_event

        event = create_event(facility, staff_user, doc_type_contact, _occurred(), {}, is_anonymous=True)
        assert event.pk is not None


@pytest.mark.django_db
class TestImportBlocked:
    def test_import_row_hits_the_service_gate(self, facility, restricted_client, doc_type_contact, staff_user):
        """Der CSV-Import loest die Person selbst auf und laeuft NICHT durch
        ``resolve_writable_client`` — er landet direkt in ``create_event``.
        Genau deshalb muss dort das Gate sitzen; ohne es haengte ein
        Import-Commit frischen Klartext an die eingeschraenkte Akte.

        Der Import reicht ``created_via`` nicht durch (die Provenienz kennt nur
        ``standard``/``tally``); geprueft wird deshalb derselbe Service-Aufruf,
        den ``_import_event_row`` absetzt.
        """
        from core.services.events import create_event

        with pytest.raises(ValidationError):
            create_event(facility, staff_user, doc_type_contact, _occurred(), {}, client=restricted_client)


@pytest.mark.django_db
class TestCasePathsBlocked:
    def test_create_case_blocked(self, facility, restricted_client, staff_user):
        from core.services.case import create_case

        with pytest.raises(ValidationError):
            create_case(facility, staff_user, restricted_client, "Neuer Fall")

    def test_update_case_blocked(self, case_open, restricted_client, staff_user):
        from core.services.case import update_case

        with pytest.raises(ValidationError):
            update_case(case_open, staff_user, title="Umbenannt")

    def test_close_case_blocked(self, case_open, restricted_client, staff_user):
        from core.services.case import close_case

        with pytest.raises(ValidationError):
            close_case(case_open, staff_user)

    def test_reopen_case_blocked(self, case_closed, restricted_client, staff_user):
        from core.services.case import reopen_case

        with pytest.raises(ValidationError):
            reopen_case(case_closed, staff_user)

    def test_assign_event_to_case_blocked(self, facility, case_open, restricted_client, doc_type_contact, staff_user):
        from core.services.case import assign_event_to_case

        event = Event.objects.create(
            facility=facility,
            client=restricted_client,
            document_type=doc_type_contact,
            occurred_at=_occurred(),
            created_by=staff_user,
        )
        with pytest.raises(ValidationError):
            assign_event_to_case(case_open, event, staff_user)

    def test_remove_event_from_case_blocked(self, facility, case_open, restricted_client, doc_type_contact, staff_user):
        from core.services.case import remove_event_from_case

        event = Event.objects.create(
            facility=facility,
            client=restricted_client,
            case=case_open,
            document_type=doc_type_contact,
            occurred_at=_occurred(),
            created_by=staff_user,
        )
        with pytest.raises(ValidationError):
            remove_event_from_case(event, staff_user)


@pytest.mark.django_db
class TestCaseSubObjectsBlocked:
    def test_update_episode_blocked(self, episode, restricted_client, staff_user):
        from core.services.case import update_episode

        with pytest.raises(ValidationError):
            update_episode(episode, staff_user, title="Umbenannt")

    def test_create_episode_blocked(self, case_open, restricted_client, staff_user):
        """Re-Review-Befund: Das Anlegen lief als reine Modell-Methode an der
        Service-Schicht vorbei."""
        from core.services.case.episodes import create_episode

        with pytest.raises(ValidationError):
            create_episode(case_open, staff_user, title="Neue Episode")

    def test_close_episode_blocked(self, episode, restricted_client, staff_user):
        from core.services.case.episodes import close_episode

        with pytest.raises(ValidationError):
            close_episode(episode, staff_user)

    def test_create_goal_blocked(self, case_open, restricted_client, staff_user):
        from core.services.case.goals import create_goal

        with pytest.raises(ValidationError):
            create_goal(case_open, staff_user, "Neues Ziel")

    def test_update_goal_blocked(self, outcome_goal, restricted_client, staff_user):
        from core.services.case.goals import update_goal

        with pytest.raises(ValidationError):
            update_goal(outcome_goal, staff_user, title="Umbenannt")

    def test_achieve_goal_blocked(self, outcome_goal, restricted_client):
        from core.services.case.goals import achieve_goal

        with pytest.raises(ValidationError):
            achieve_goal(outcome_goal)

    def test_toggle_milestone_blocked(self, milestone, restricted_client):
        from core.services.case.goals import toggle_milestone

        with pytest.raises(ValidationError):
            toggle_milestone(milestone)

    def test_delete_milestone_blocked(self, milestone, restricted_client, staff_user):
        from core.services.case.goals import delete_milestone

        with pytest.raises(ValidationError):
            delete_milestone(milestone, staff_user)


@pytest.mark.django_db
class TestWorkItemPathsBlocked:
    def test_create_workitem_blocked(self, facility, restricted_client, staff_user):
        from core.services.case import create_workitem

        with pytest.raises(ValidationError):
            create_workitem(facility, staff_user, client=restricted_client, title="Neue Aufgabe")

    def test_update_workitem_blocked(self, sample_workitem, restricted_client, staff_user):
        from core.services.case import update_workitem

        with pytest.raises(ValidationError):
            update_workitem(sample_workitem, staff_user, title="Umbenannt")

    def test_update_workitem_status_blocked(self, sample_workitem, restricted_client, staff_user):
        from core.services.case import update_workitem_status

        with pytest.raises(ValidationError):
            update_workitem_status(sample_workitem, WorkItem.Status.DONE, staff_user)

    def test_assigning_a_personless_task_to_a_restricted_client_is_blocked(
        self, facility, restricted_client, staff_user
    ):
        """Re-Review-Befund: Das Gate prüfte nur den BISHERIGEN Personenbezug.

        Das Ursprungs-Pinning erlaubt genau den Übergang „personenlos -> Person"
        — und damit lief die Zuordnung an eine eingeschränkte Person durch.
        """
        from core.services.case import create_workitem, update_workitem

        workitem = create_workitem(facility, staff_user, title="Ohne Person")
        with pytest.raises(ValidationError):
            update_workitem(workitem, staff_user, client=restricted_client)
        workitem.refresh_from_db()
        assert workitem.client_id is None

    def test_recurrence_duplicate_blocked(self, facility, restricted_client, staff_user):
        """Re-Review-Befund: Die Folgeaufgabe entstand als Neuanlage mit
        demselben Personenbezug, ohne Guard."""
        from core.models import WorkItem as WorkItemModel
        from core.services.case import duplicate_recurring_workitem

        # Bewusst per ORM angelegt statt über ``create_workitem``: der Guard
        # dort griffe schon bei der Anlage, geprüft werden soll aber der
        # Bestand, der VOR der Einschränkung entstanden ist.
        workitem = WorkItemModel.objects.create(
            facility=facility,
            created_by=staff_user,
            client=restricted_client,
            title="Wiederkehrend",
            recurrence=WorkItemModel.Recurrence.WEEKLY,
        )
        with pytest.raises(ValidationError):
            duplicate_recurring_workitem(workitem, staff_user)
        assert WorkItemModel.objects.filter(client=restricted_client).count() == 1

    def test_bulk_status_blocked_for_whole_batch(self, sample_workitem, restricted_client, staff_user):
        """Fail-closed fuer den ganzen Stapel — ein Teil-Erfolg waere fuer die
        ausloesende Person nicht nachvollziehbar."""
        from core.services.case import bulk_update_workitem_status

        with pytest.raises(ValidationError):
            bulk_update_workitem_status([sample_workitem], staff_user, WorkItem.Status.DONE)
        sample_workitem.refresh_from_db()
        assert sample_workitem.status != WorkItem.Status.DONE

    def test_bulk_priority_blocked(self, sample_workitem, restricted_client, staff_user):
        from core.services.case import bulk_update_workitem_priority

        with pytest.raises(ValidationError):
            bulk_update_workitem_priority([sample_workitem], staff_user, WorkItem.Priority.URGENT)

    def test_bulk_assign_blocked(self, sample_workitem, restricted_client, staff_user, lead_user):
        from core.services.case import bulk_assign_workitems

        with pytest.raises(ValidationError):
            bulk_assign_workitems([sample_workitem], staff_user, lead_user)


@pytest.mark.django_db
class TestMergeBlocked:
    def test_direct_merge_blocked(self, facility, restricted_client, staff_user, lead_user):
        from core.services.client import merge_clients

        target = Client.objects.create(facility=facility, pseudonym="Ziel-01", created_by=staff_user)
        with pytest.raises(ValidationError):
            merge_clients(restricted_client, target, lead_user)

    def test_merge_request_blocked(self, facility, restricted_client, staff_user):
        """Re-Review-Befund: Der Merge-ANTRAG war ungegatet.

        Anders als der Art.-17-Löschantrag ist die Zusammenführung kein
        Betroffenenrecht, sondern eine Aktenführungs-Entscheidung — sie darf
        während einer bestrittenen Richtigkeit nicht einmal beantragt werden.
        """
        from core.services.client import request_client_merge

        target = Client.objects.create(facility=facility, pseudonym="Ziel-02", created_by=staff_user)
        with pytest.raises(ValidationError):
            request_client_merge(restricted_client, target, staff_user, "Duplikat")

    def test_restricted_target_also_blocked(self, facility, restricted_client, staff_user, lead_user):
        """Beide Seiten: eine eingeschraenkte ZIELakte darf nicht befuellt werden."""
        from core.services.client import merge_clients

        source = Client.objects.create(facility=facility, pseudonym="Quelle-01", created_by=staff_user)
        with pytest.raises(ValidationError):
            merge_clients(source, restricted_client, lead_user)


# ---------------------------------------------------------------------------
# ERLAUBT — Betroffenenrechte bleiben offen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDataSubjectRightsStayOpen:
    def test_client_deletion_request_allowed(self, restricted_client, staff_user):
        """Art. 17 gilt waehrend der Einschraenkung weiter — der gepruefte
        Loeschantrag ist gerade der Weg, der offen bleiben MUSS."""
        from core.services.client import request_client_deletion

        dr = request_client_deletion(restricted_client, staff_user, "Betroffene verlangt Löschung")
        assert dr.pk is not None

    def test_client_deletion_approval_allowed(self, restricted_client, staff_user, admin_user):
        from core.services.client import approve_client_deletion, request_client_deletion

        dr = request_client_deletion(restricted_client, staff_user, "Löschung")
        approve_client_deletion(dr, admin_user)
        restricted_client.refresh_from_db()
        assert restricted_client.is_deleted is True

    def test_case_deletion_request_allowed(self, case_open, restricted_client, staff_user):
        from core.services.case.deletion import request_case_deletion

        dr = request_case_deletion(case_open, staff_user, "Fall irrtümlich angelegt")
        assert dr.pk is not None

    def test_export_allowed(self, facility, restricted_client, admin_user):
        from core.services.client import export_client_data

        data = export_client_data(restricted_client, facility, admin_user)
        assert data["client"]["pseudonym"] == restricted_client.pseudonym

    def test_rectification_approval_allowed(self, restricted_client, staff_user, lead_user):
        from core.services.client.rectification import approve_rectification, request_rectification

        rr = request_rectification(restricted_client, staff_user, "notes", "Richtig", "bestritten")
        approve_rectification(rr, lead_user)
        restricted_client.refresh_from_db()
        assert restricted_client.notes == "Richtig"

    def test_lifting_the_restriction_allowed(self, restricted_client, lead_user):
        from core.services.client import lift_processing_restriction

        lift_processing_restriction(restricted_client, lead_user)
        restricted_client.refresh_from_db()
        assert restricted_client.processing_restricted is False

    def test_everything_works_again_after_lifting(self, facility, restricted_client, doc_type_contact, lead_user):
        """Die Einschraenkung ist reversibel — nach dem Aufheben ist der
        Normalbetrieb vollstaendig zurueck."""
        from core.services.client import lift_processing_restriction
        from core.services.events import create_event

        lift_processing_restriction(restricted_client, lead_user)
        restricted_client.refresh_from_db()
        event = create_event(facility, lead_user, doc_type_contact, _occurred(), {}, client=restricted_client)
        assert event.pk is not None


# ---------------------------------------------------------------------------
# Befund 4 — der Ausstieg ist kein Generalschlüssel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRectificationExitIsNotASkeletonKey:
    def test_update_client_has_no_public_bypass_parameter(self, restricted_client, staff_user):
        """Das frühere ``allow_restricted=True`` hätte jeder interne Aufrufer
        setzen können. Der Parameter existiert nicht mehr — er landet jetzt in
        ``**fields`` und faellt der Mass-Assignment-Allowlist zum Opfer, statt
        das Gate abzuschalten."""
        from core.services.client import update_client

        with pytest.raises(ValidationError):
            update_client(restricted_client, staff_user, notes="X", allow_restricted=True)
        restricted_client.refresh_from_db()
        assert restricted_client.notes != "X"

    def test_generic_update_outside_the_context_stays_blocked(self, restricted_client, staff_user):
        from core.services.client import update_client

        with pytest.raises(ValidationError):
            update_client(restricted_client, staff_user, notes="Ohne Berichtigungsantrag")

    def test_context_closes_again_after_use(self, facility, restricted_client, doc_type_contact, staff_user):
        """Der Kontext gilt fuer seinen Block — danach greift das Gate wieder.

        Ohne diese Zusage waere ein einmal geoeffneter Ausstieg fuer den Rest
        des Requests ein Generalschluessel.
        """
        from core.services.client.restriction import _rectification_write, assert_processing_allowed

        with _rectification_write():
            assert_processing_allowed(restricted_client)
        with pytest.raises(ValidationError):
            assert_processing_allowed(restricted_client)


# ---------------------------------------------------------------------------
# Befund 3 — fail-closed gegen den frisch gelesenen Stand
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGateReadsFreshState:
    def test_stale_instance_does_not_pass_the_gate(self, client_identified, staff_user):
        """Der Kern des Befunds: eine Instanz aus einem Formular-GET von vor
        Minuten weiss von der Einschraenkung nichts. Geprueft wird die DB."""
        from core.services.client import update_client

        stale = Client.objects.get(pk=client_identified.pk)
        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        assert stale.processing_restricted is False  # die Instanz ist veraltet
        with pytest.raises(ValidationError):
            update_client(stale, staff_user, notes="Neu")

    def test_restrict_is_idempotent_on_a_stale_instance(self, client_identified, staff_user, lead_user):
        """Die Idempotenz-Zusage haengt am DB-Stand, nicht am Objekt.

        Zwei Aufrufe mit einer stalen Instanz duerfen weder Provenienz noch
        Zeitpunkt ueberschreiben und nur EINE Audit-Zeile erzeugen.
        """
        from core.models import AuditLog

        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        client_identified.refresh_from_db()
        first_at = client_identified.processing_restricted_at

        stale = Client.objects.get(pk=client_identified.pk)
        stale.processing_restricted = False  # simuliert den veralteten Lesestand
        restrict_processing(stale, lead_user, reason=Client.RestrictionReason.LEGAL_CLAIMS)

        client_identified.refresh_from_db()
        assert client_identified.processing_restricted_by == staff_user
        assert client_identified.processing_restricted_at == first_at
        assert client_identified.processing_restriction_reason == Client.RestrictionReason.ACCURACY_DISPUTED
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.PROCESSING_RESTRICTED,
                target_id=str(client_identified.pk),
            ).count()
            == 1
        )

    def test_lift_decides_on_the_fresh_provenance(self, client_identified, staff_user):
        """Vier-Augen gegen den DB-Stand: eine stale Instanz ohne Provenienz
        darf die Regel nicht aushebeln."""
        from core.services.client import lift_processing_restriction

        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        stale = Client.objects.get(pk=client_identified.pk)
        stale.processing_restricted_by = None
        with pytest.raises(ValidationError):
            lift_processing_restriction(stale, staff_user)


# ---------------------------------------------------------------------------
# Anhang-Purge
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAttachmentPurgeBlocked:
    def test_purge_service_refuses_and_keeps_the_chain(self, facility, restricted_client, staff_user):
        """Der endgueltige Purge ist die einzige Anhang-Operation, die Daten
        unwiderruflich vernichtet — und sie laeuft nicht ueber den
        Vier-Augen-Loeschworkflow.

        Die frühere Fassung dieses Tests legte weder einen Anhang an noch
        setzte sie einen Purge ab (Re-Review-Befund 5). Sie tut jetzt beides:
        echter personen-eigener Anhang, echter Service-Aufruf, Nachweis von
        Refusal UND Fortbestand. Der Weg über die HTTP-Route samt
        Bestätigungsseite liegt bei den übrigen Purge-Tests
        (``test_attachment_purge.py``), wo die Ketten-Fixtures leben.
        """
        from django.core.exceptions import PermissionDenied

        from core.models.attachment import Attachment
        from core.services.attachments import purge_attachment_chain
        from core.services.file_vault import get_attachment_path
        from tests.conftest import _make_attachment

        attachment = _make_attachment(facility, restricted_client, staff_user, ft_name="Anhang (Art. 18)")
        assert get_attachment_path(attachment).exists()

        with pytest.raises(PermissionDenied):
            purge_attachment_chain(restricted_client, attachment.entry_id)

        assert Attachment.objects.filter(entry_id=attachment.entry_id).exists()
        assert get_attachment_path(attachment).exists()
