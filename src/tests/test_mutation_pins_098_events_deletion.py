"""Pin-Tests der Welle K (0.98, Refs #2193) — Prio-2-Testlücken in
``core.services.events.deletion``, Batch ``case-client``.

Quelle: ``welle-k-0.98/reports/c-scratch/batch-case-client.json``
(``triage-097-sec.json``). Zwei Survivors, keine Überschneidung mit den in
``test_mutation_pins_097_events_deletion.py`` bereits gepinnten Mutanten (dort:
Zieltreue der ``select_for_update``/``Event``-Lookups in ``approve_deletion``/
``reject_deletion`` — hier: das ``created``-Flag von
``create_or_get_event_deletion_request``).

* **``x_create_or_get_event_deletion_request__mutmut_4``**: der
  Idempotenz-Treffer (``return existing, False``) meldet sich als Neuanlage
  (``True``).
* **``…__mutmut_38``**: spiegelbildlich — der frisch angelegte Antrag
  (``return dr, True``) meldet sich als Idempotenz-Treffer (``False``).

Kein Produktivaufrufer wertet ``created`` heute aus (``views/events.py``
verwirft es bewusst), der dokumentierte Vertrag der Funktion ist aber genau
dieses Flag — ``create_or_get_event_deletion_request`` existiert nur wegen
ihm (``request_deletion`` ist die Hülle ohne ihn).
"""

from __future__ import annotations

import pytest

from core.services.events.deletion import create_or_get_event_deletion_request

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_created_flag_unterscheidet_erstantrag_von_idempotenz_treffer(sample_event, staff_user):
    """Pins ``x_create_or_get_event_deletion_request__mutmut_4`` (Treffer
    meldet ``True``) UND ``…__mutmut_38`` (Neuanlage meldet ``False``)."""
    dr1, created1 = create_or_get_event_deletion_request(sample_event, staff_user, "Erster Antrag")
    assert created1 is True

    dr2, created2 = create_or_get_event_deletion_request(sample_event, staff_user, "Zweiter Versuch")
    assert created2 is False
    assert dr2.pk == dr1.pk
