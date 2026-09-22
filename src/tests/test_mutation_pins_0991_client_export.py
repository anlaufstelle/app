"""Pin-Test des Mutations-Delta-Checkpoints der Welle M (release-0.99.1) zu
``core.services.client.export``.

Quelle: Ticket #2322 (https://github.com/anlaufstelle/app/issues/2322),
Survivor-Triage des Checkpoint-Deltas ``6435c18e`` (BASE v0.99.0). Gepinnt
wird der einzige Prio-2-Survivor dieses Moduls: ``_gather_cases`` sortiert
per Vertrag ``order_by("-created_at", "pk")`` — neuester Fall zuerst. Kein
Bestandstest legt zwei Faelle mit ABWEICHENDER PK-/``created_at``-Reihenfolge
an, deshalb faellt eine Reduktion auf die reine PK-Reihenfolge nicht auf.

Konvention wie ``src/tests/test_mutation_pins_094_attachments_crud.py``.
"""

import uuid

import pytest

from core.models import Case
from core.services.client.export import _gather_cases

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

#: ``Case.id`` ist ein ``UUIDField(default=uuid.uuid4)`` — die PK ist damit
#: ZUFAELLIG und ordnet sich nicht nach der Anlage-Reihenfolge. Feste,
#: bewusst GEGENLAEUFIG zur ``created_at``-Reihenfolge gewaehlte UUIDs machen
#: den Unterschied deterministisch statt zufaellig ~50 % beobachtbar.
_PK_FALL_ALT = uuid.UUID("00000000-0000-0000-0000-000000000001")
_PK_FALL_NEU = uuid.UUID("00000000-0000-0000-0000-000000000002")


class TestGatherCases:
    def test_neuester_fall_zuerst(self, facility, client_identified, staff_user):
        """Pin: ``x__gather_cases__mutmut_5`` — ``order_by("-created_at", "pk")``
        wird zu ``order_by("pk")``.

        ``fall_alt`` traegt die NIEDRIGERE PK, ist aber ZUERST angelegt
        (``created_at`` aelter); ``fall_neu`` traegt die HOEHERE PK und ist
        DANACH angelegt (``created_at`` juenger) — PK-Reihenfolge und
        ``-created_at``-Reihenfolge zeigen damit in ENTGEGENGESETZTE
        Richtungen.

        Abweichung beim Mutanten: ``entries[0]["title"]`` ist ``fall_alt``
        (niedrigste PK) statt ``fall_neu`` (juengster ``created_at``).
        """
        fall_alt = Case.objects.create(
            id=_PK_FALL_ALT,
            facility=facility,
            client=client_identified,
            title="Pins-0991-Fall-alt",
            created_by=staff_user,
        )
        fall_neu = Case.objects.create(
            id=_PK_FALL_NEU,
            facility=facility,
            client=client_identified,
            title="Pins-0991-Fall-neu",
            created_by=staff_user,
        )
        assert fall_neu.pk > fall_alt.pk
        assert fall_neu.created_at > fall_alt.created_at

        entries = _gather_cases(client_identified)

        assert len(entries) == 2
        assert entries[0]["title"] == "Pins-0991-Fall-neu"
        assert entries[1]["title"] == "Pins-0991-Fall-alt"
