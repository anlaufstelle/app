"""Der geprüfte Vier-Augen-Löschweg unter aktiver Aufbewahrungssperre (Refs #2197).

``approve_deletion`` ruft ``soft_delete_event`` und ist damit der dritte
Einstieg in denselben Guard — neben dem direkten Klick der Fachkraft und dem
Strichlisten-Undo. Er hat eine Besonderheit: er öffnet den benannten
Löschworkflow-Kontext, der das Art.-18-Gate passieren lässt (Refs #2045). Die
Aufbewahrungssperre ist davon unberührt — Art. 18 schränkt die *Verarbeitung*
ein, ein Legal Hold sichert *Beweismittel*; die Genehmigung durch eine zweite
Person hebt ihn nicht auf.

Bewusst eine eigene Datei statt einer Erweiterung von
``test_deletion_confirmer.py``: dort liegen die Vier-Augen-Rechte (Refs #2082),
hier die Hold-Kopplung.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, DeletionRequest, LegalHold
from core.services.events.crud import LEGAL_HOLD_BLOCKS_DELETE_MESSAGE
from core.services.events.deletion import approve_deletion, request_deletion

pytestmark = pytest.mark.django_db


def _hold(facility, admin_user, target_type, target_id):
    return LegalHold.objects.create(
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        reason="Ermittlungsverfahren",
        created_by=admin_user,
    )


class TestApproveDeletionHonoursHolds:
    def test_case_hold_stops_the_approved_deletion(self, facility, case_open, case_event, staff_user, admin_user):
        """Refs #2197 (a): der geprüfte Weg darf nicht mehr dürfen als der Cron.

        Ohne den Fall-Zweig war die Genehmigung der bequemste Ausstieg aus
        einem Fall-Hold: zwei Klicks, ein sauberes Audit, geleerte Nutzdaten.
        """
        dr = request_deletion(case_event, staff_user, reason="Auf Wunsch der Person")
        _hold(facility, admin_user, "Case", case_open.pk)

        with pytest.raises(ValidationError) as exc_info:
            approve_deletion(dr, admin_user)

        assert exc_info.value.messages == [str(LEGAL_HOLD_BLOCKS_DELETE_MESSAGE)]
        case_event.refresh_from_db()
        assert case_event.is_deleted is False
        assert case_event.data_json == {"dauer": 15, "notiz": "Testnotiz"}
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING, "der Antrag bleibt offen statt still zu verfallen"
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_id=str(case_event.pk)).exists()
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETION_APPROVED).exists()

    def test_a_case_assigned_between_load_and_delete_still_stops_the_approval(
        self, facility, case_open, case_event, staff_user, admin_user, monkeypatch
    ):
        """Cross-Review Runde 1, Befund 1 — auf dem geprüften Weg.

        ``approve_deletion`` lädt das Ereignis selbst und reicht die Instanz an
        ``soft_delete_event`` weiter. Zwischen diesem Laden und dem Erwerb der
        Ereignissperre liegt ein Fenster; wird das Ereignis darin einem
        gehaltenen Fall zugeordnet (regulärer Service, kein ORM-Bypass), sah der
        Guard die alte, fallslose Instanz. Der Wrapper bildet genau dieses
        Fenster ab und lässt die Guards unangetastet.
        """
        from core.models import Event
        from core.services.case.cases import assign_event_to_case, remove_event_from_case
        from core.services.events import deletion as deletion_module

        remove_event_from_case(Event.objects.get(pk=case_event.pk), staff_user)
        dr = request_deletion(Event.objects.get(pk=case_event.pk), staff_user, reason="Auf Wunsch der Person")
        _hold(facility, admin_user, "Case", case_open.pk)
        original = deletion_module.soft_delete_event

        def _assign_to_the_held_case_first(event, user, **kwargs):
            assign_event_to_case(case_open, Event.objects.get(pk=event.pk), staff_user)
            return original(event, user, **kwargs)

        monkeypatch.setattr(deletion_module, "soft_delete_event", _assign_to_the_held_case_first)

        with pytest.raises(ValidationError) as exc_info:
            approve_deletion(dr, admin_user)

        assert exc_info.value.messages == [str(LEGAL_HOLD_BLOCKS_DELETE_MESSAGE)]
        stored = Event.objects.get(pk=case_event.pk)
        assert stored.is_deleted is False
        assert stored.data_json == {"dauer": 15, "notiz": "Testnotiz"}
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_id=str(case_event.pk)).exists()
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETION_APPROVED).exists()

    def test_event_hold_stops_the_approved_deletion(self, facility, sample_event, staff_user, admin_user):
        """Der schon vorhandene Ereignis-Zweig, über denselben Einstieg belegt —
        er hielt den Vier-Augen-Weg bisher als einziger auf."""
        dr = request_deletion(sample_event, staff_user, reason="Auf Wunsch der Person")
        _hold(facility, admin_user, "Event", sample_event.pk)

        with pytest.raises(ValidationError):
            approve_deletion(dr, admin_user)

        sample_event.refresh_from_db()
        assert sample_event.is_deleted is False

    def test_approval_without_a_hold_still_deletes(self, case_event, staff_user, admin_user):
        """Gegenprobe: der Guard darf den geprüften Weg nicht generell zumauern."""
        dr = request_deletion(case_event, staff_user, reason="Auf Wunsch der Person")

        approve_deletion(dr, admin_user)

        case_event.refresh_from_db()
        assert case_event.is_deleted is True
        assert case_event.deleted_by == admin_user
