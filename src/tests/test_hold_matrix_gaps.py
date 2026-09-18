"""Hold-Matrix-Lücken L1/L2/L3 (Refs #2234).

Die Hold-Matrix (Ziel-Typ Ereignis/Personen/Fall × Pfad) ist über viele
Testdateien verteilt belegt; drei Zellen hatten bislang keinen dedizierten
Test, obwohl der jeweilige Guard bereits über einen anderen Pfad mitgeprüft
wurde. Bestandscode — kein Produktionsfix nötig:

* **L1** — der DIREKTE ``soft_delete_event``-Aufruf (manueller Löschpfad)
  unter Personen-Hold. Nur der Vier-Augen-Weg (``approve_deletion``) war
  über ``test_eventhistory_retention.py::TestApprovedDeletionTakesClientLock::
  test_genehmigter_loeschweg_respektiert_den_personen_hold`` belegt.
* **L2** — ``_validate_merge`` weist einen Merge auch ab, wenn nicht die
  Person selbst, sondern eines ihrer Ereignisse bzw. eines ihrer Fälle unter
  Hold steht (``client_has_active_hold`` deckt beide Zweige über
  ``exclude_clients_with_active_holds`` — ``events__pk__in`` /
  ``cases__pk__in``). Direkt getestet war bisher nur der Personen-Hold selbst
  (``test_client_merge_workflow.py::TestMergeValidation::
  test_refuses_participant_under_legal_hold``).
* **L3** — der Fall-Hold im Cron-Recheck unter der Zeilensperre
  (``core/retention/enforcement.py::_lock_event_and_recheck_hold``), wenn der
  Hold NACH dem Sammeln entsteht. Interleaving-Muster analog dem
  Personen-Zweig in ``test_retention_redaction.py::
  TestRetentionSoftDeleteHoldRace::
  test_client_hold_after_collection_defers_the_event``.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Case, Client, Event, EventHistory
from core.retention.legal_holds import create_hold_for_target
from core.services.client.merge import merge_clients
from core.services.events import create_event, soft_delete_event
from core.services.retention import _soft_delete_events

pytestmark = pytest.mark.django_db


class TestL1DirectDeleteUnderClientHold:
    """L1: der Client-Zweig von ``_assert_no_active_hold`` am DIREKTEN
    manuellen Löschpfad."""

    def test_direct_soft_delete_refuses_under_client_hold(
        self, facility, admin_user, staff_user, client_identified, doc_type_contact
    ):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        create_hold_for_target(
            facility,
            target_type="Client",
            target_id=client_identified.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        with pytest.raises(ValidationError):
            soft_delete_event(event, staff_user)

        event.refresh_from_db()
        assert event.is_deleted is False
        assert event.data_json == {"dauer": 15}
        assert EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).count() == 0


class TestL2MergeUnderEventOrCaseHold:
    """L2: ``_validate_merge`` unter Ereignis- bzw. Fall-Hold der Quelle,
    je eigener Test."""

    HOLD_MESSAGE = (
        "Für eine der beiden Personen besteht eine Aufbewahrungssperre (Legal Hold) — Zusammenführen nicht möglich."
    )

    def _client(self, facility, staff_user, pseudonym):
        return Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym=pseudonym,
            created_by=staff_user,
        )

    def test_merge_refuses_when_an_event_of_the_source_is_under_hold(
        self, facility, admin_user, staff_user, lead_user, doc_type_contact
    ):
        source = self._client(facility, staff_user, "L2-Quelle-Ereignis")
        target = self._client(facility, staff_user, "L2-Ziel-Ereignis")
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=source,
        )
        create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        with pytest.raises(ValidationError) as exc_info:
            merge_clients(source, target, lead_user)

        assert exc_info.value.messages == [self.HOLD_MESSAGE]
        source.refresh_from_db()
        assert source.merged_into_id is None

    def test_merge_refuses_when_a_case_of_the_source_is_under_hold(self, facility, admin_user, staff_user, lead_user):
        source = self._client(facility, staff_user, "L2-Quelle-Fall")
        target = self._client(facility, staff_user, "L2-Ziel-Fall")
        case = Case.objects.create(
            facility=facility,
            client=source,
            title="Fall unter Hold",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        create_hold_for_target(
            facility,
            target_type="Case",
            target_id=case.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        with pytest.raises(ValidationError) as exc_info:
            merge_clients(source, target, lead_user)

        assert exc_info.value.messages == [self.HOLD_MESSAGE]
        source.refresh_from_db()
        assert source.merged_into_id is None


class TestL3CaseHoldInterleavesWithTheRecheck:
    """L3: der Fall-Hold entsteht NACH dem Sammeln — derselbe Race wie beim
    Personen-Zweig, hier für den Fall-Zweig von
    ``_lock_event_and_recheck_hold``."""

    def test_case_hold_created_after_collection_defers_the_event(
        self, facility, doc_type_contact, admin_user, staff_user, client_identified, case_open
    ):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now() - timedelta(days=400),
            data_json={"dauer": 15},
            client=client_identified,
            case=case_open,
        )
        qs_t0 = Event.objects.filter(pk=event.pk)

        create_hold_for_target(
            facility,
            target_type="Case",
            target_id=case_open.pk,
            user=admin_user,
            reason="Behördenanordnung mitten im Lauf",
        )

        count, deleted_ids = _soft_delete_events(qs_t0, facility, category="identified", retention_days=3650)

        event.refresh_from_db()
        assert event.is_deleted is False, "Event unter aktivem Fall-Hold wurde redigiert (Spoliation)"
        assert event.data_json == {"dauer": 15}
        assert count == 0
        assert deleted_ids == []
        assert EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).count() == 0
