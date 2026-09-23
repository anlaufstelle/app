"""Pin-Test des Mutations-Delta-Checkpoints der Welle M (release-0.99.1) zu
``core.services.case.deletion``.

Quelle: Ticket #2322 (https://github.com/anlaufstelle/app/issues/2322),
Survivor-Triage des Checkpoint-Deltas ``6435c18e`` (BASE v0.99.0). Gepinnt
wird der einzige hier gelistete Survivor: ``x_reject_case_deletion__mutmut_22``
— laut Ticket ein Urteil aus der Vollmutations-Triage 0.99, dessen Pin dort
nicht geschrieben wurde. Der ``audit_event``-Aufruf in ``reject_case_deletion``
gibt ``user=reviewer`` — die einzige Stelle, an der die handelnde Person in
die ``DELETION_REJECTED``-Zeile fliesst (dieselbe Einordnung wie
``x_approve_case_deletion__mutmut_40`` in ``test_mutation_pins_099_deletion_a.py``,
dort fuer den Genehmigungspfad bereits gepinnt).

**S-B- UND S-D-Ueberschneidung (Nachlauf noetig):** Die Aenderung S-B liest
Bausteine aus diesem Modul (``redact_case_goals``); die Aenderung S-D (Ticket
2321/H-3) aendert ``reject_case_deletion`` SELBST (Einrichtungsgrenze des
Reviewers). Der Kill-Nachweis dieser Datei gilt fuer den Stand VOR beiden
Merges — er wird nach dem S-B- UND dem S-D-Merge wiederholt.

Konvention wie ``src/tests/test_mutation_pins_099_deletion_a.py``
(``_case``/``_dr``-Helfer identisch nachgebaut, bewusst keine Importe aus
fremden Testdateien).
"""

import pytest

from core.models import AuditLog, Case, DeletionRequest
from core.services.case.deletion import reject_case_deletion

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _case(facility, client, user, *, title, **extra):
    return Case.objects.create(facility=facility, client=client, title=title, created_by=user, **extra)


def _dr(facility, case, user, *, reason):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CASE,
        target_id=case.pk,
        reason=reason,
        requested_by=user,
    )


class TestRejectCaseDeletionAuditAkteur:
    def test_audit_zeile_traegt_den_ablehnenden_reviewer(self, facility, client_qualified, staff_user, lead_user):
        """Pin: ``x_reject_case_deletion__mutmut_22`` — ``audit_event`` bekommt
        ``user=None`` statt des ablehnenden Reviewers; die
        ``DELETION_REJECTED``-Zeile verliert ihre Akteur-Zuschreibung
        (Art. 5 Abs. 2 DSGVO — Rechenschaftspflicht).
        """
        case = _case(facility, client_qualified, staff_user, title="Pins-0991-Zuschreibung")
        dr = _dr(facility, case, staff_user, reason="Pins-0991-Ablehnung")

        reject_case_deletion(dr, lead_user)

        entry = AuditLog.objects.get(action=AuditLog.Action.DELETION_REJECTED, target_id=str(dr.pk))
        assert entry.user_id == lead_user.pk
