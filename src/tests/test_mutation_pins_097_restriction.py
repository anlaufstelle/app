"""Pin-Tests zum Meilenstein release-0.97 — Survivors in ``core.services.client.restriction``.

Quelle: atomic-Messlauf der Welle J (https://github.com/anlaufstelle/app/issues/2149),
Batch ``client``. Gepinnt werden

* der Prio-1-Survivor ``x_lift_processing_restriction__mutmut_42``: die
  Aufhebung des Art.-18-Vermerks wird ohne handelnde Person auditiert.
  ``audit_client_event`` laesst ``user=None`` ausdruecklich zu, und der
  Bestandstest ``test_lift_writes_audit_row`` prueft nur ``exists()`` — genau
  die Zurechnung, die die Vier-Augen-Zusage der Aufhebung traegt (Art. 18
  Abs. 3), faellt also lautlos weg.
* der Prio-2-Survivor ``x__restricted_flag__mutmut_1``: der Default ``lock``
  kippt auf ``True``. Das Gate uebergibt ``lock`` explizit und bleibt
  unberuehrt — aber der reine ANZEIGE-Pfad ``is_processing_restricted`` setzte
  dann ein ``SELECT … FOR UPDATE`` ab und riss ohne umschliessende Transaktion
  (kein ``ATOMIC_REQUESTS`` im Repo) einen ``TransactionManagementError`` im
  Attachment-View. Der Pin braucht deshalb ``django_db(transaction=True)``:
  unter dem normalen ``django_db`` liegt jeder Test in einer Transaktion, in
  der ``select_for_update`` klaglos durchgeht.

Konventionen wie ``test_mutation_pins_096_merge.py``: exakte Vergleiche,
Fixtures aus ``src/tests/conftest.py``, kein Nachbau von Produktionslogik.
"""

import pytest

from core.models import AuditLog, Client
from core.services.client.restriction import (
    is_processing_restricted,
    lift_processing_restriction,
    restrict_processing,
)


@pytest.mark.django_db
def test_aufhebung_wird_der_handelnden_person_zugerechnet(facility, client_identified, staff_user, lead_user):
    """Pins ``x_lift_processing_restriction__mutmut_42``: ``audit_client_event``
    erhaelt ``None`` statt der aufhebenden Person. Die Audit-Zeile entstuende
    ohne Akteurin — nicht mehr nachweisbar, dass eine ZWEITE Person aufgehoben
    hat (Vier-Augen-Zusage der Aufhebung, Refs #2045)."""
    restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)

    lift_processing_restriction(client_identified, lead_user)

    entry = AuditLog.objects.get(
        action=AuditLog.Action.PROCESSING_RESTRICTION_LIFTED,
        target_id=str(client_identified.pk),
    )
    assert entry.user_id == lead_user.pk
    assert entry.facility_id == facility.pk
    assert entry.target_type == "Client"


@pytest.mark.django_db(transaction=True)
def test_anzeige_praedikat_liest_ohne_zeilensperre(client_identified):
    """Pins ``x__restricted_flag__mutmut_1``: kippt der Default ``lock`` auf
    ``True``, setzt der reine Anzeigepfad ``is_processing_restricted`` ein
    ``SELECT … FOR UPDATE`` ab und scheitert ausserhalb einer Transaktion mit
    ``TransactionManagementError`` (Prio-2-Survivor, mitgenommen weil er
    dieselbe Datei betrifft)."""
    assert is_processing_restricted(client_identified) is False
