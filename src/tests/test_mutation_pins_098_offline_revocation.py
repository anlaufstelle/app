"""Pin-Tests der Welle K (0.98, Refs #2193) — Prio-2-Testlücken in
``core.services.system.offline_revocation``, Batch ``case-client``.

Quelle: ``welle-k-0.98/reports/c-scratch/batch-case-client.json``
(``triage-097-sec.json``). Zwei der drei Batch-Mutanten sind NEU
(``x_list_offline_client_revocations__mutmut_17`` ist bereits als „Beifang" in
``test_mutation_pins_097_offline_revocation.py`` gepinnt — siehe Bericht,
nicht hier erneut aufgenommen).

* **``x_list_offline_client_revocations__mutmut_9``** UND
  **``x_prune_offline_client_revocations__mutmut_4``**: beide ersetzen
  ``_cutoff(now)`` durch ``_cutoff(None)`` — der injizierte Zeitpunkt wird
  ignoriert, das Retention-Fenster rechnet immer gegen die Echtzeituhr statt
  gegen den vom Aufrufer übergebenen (bei ``enforce_retention`` EINMAL zu
  Laufbeginn gebildeten) Wert.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Client, OfflineClientRevocation
from core.services.system.offline_revocation import (
    OFFLINE_REVOCATION_RETENTION_SECONDS,
    list_offline_client_revocations,
    prune_offline_client_revocations,
    revoke_offline_client_bundles,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _revoked_client(facility, staff_user, *, suffix):
    client = Client.objects.create(
        facility=facility,
        pseudonym=f"Pin-098-Cutoff-{suffix}",
        created_by=staff_user,
    )
    revoke_offline_client_bundles(client)
    return client


def test_liste_rechnet_mit_dem_uebergebenen_now(facility, staff_user):
    """Pins ``x_list_offline_client_revocations__mutmut_9``: ``_cutoff(now)``
    wird zu ``_cutoff(None)`` — ein weit in der Zukunft liegendes ``now``
    (jenseits des Retention-Fensters) muss den frischen Widerruf ausschließen;
    unter dem Mutanten bleibt er wegen der ignorierten Echtzeituhr weiter
    sichtbar."""
    client = _revoked_client(facility, staff_user, suffix="Liste")
    weit_in_der_zukunft = timezone.now() + timedelta(seconds=OFFLINE_REVOCATION_RETENTION_SECONDS + 3600)

    assert list_offline_client_revocations(facility, now=weit_in_der_zukunft) == []
    assert list_offline_client_revocations(facility, now=timezone.now()) == [str(client.pk)]


def test_prune_rechnet_mit_dem_uebergebenen_now(facility, staff_user):
    """Pins ``x_prune_offline_client_revocations__mutmut_4``: derselbe
    ``_cutoff``-Defekt im Prune-Pfad — ein weit in der Zukunft liegendes
    ``now`` muss den frischen Widerruf löschen; unter dem Mutanten schneidet
    der Prune gegen die Echtzeituhr und löscht nichts."""
    client = _revoked_client(facility, staff_user, suffix="Prune")
    weit_in_der_zukunft = timezone.now() + timedelta(seconds=2 * OFFLINE_REVOCATION_RETENTION_SECONDS)

    deleted = prune_offline_client_revocations(facility, weit_in_der_zukunft)

    assert deleted == 1
    assert not OfflineClientRevocation.objects.filter(client_pk=client.pk).exists()
