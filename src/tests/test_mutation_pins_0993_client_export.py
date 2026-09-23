"""Pins fuer die Fall-Reihenfolge der Auskunft (``core.services.client.export``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 1 Mutant in
``_gather_cases``: die Faelle stehen neueste zuerst (``-created_at``), nicht
in PK-Reihenfolge. Die PKs sind so gesetzt, dass beide Ordnungen
gegenlaeufig sind.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Case
from core.services.client.export import _gather_cases

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestGatherCases:
    def test_neueste_zuerst_nicht_nach_pk(self, facility, client_identified, staff_user):
        """x__gather_cases__mutmut_5: ohne ``-created_at`` kaeme der Fall mit
        der kleineren PK (der aeltere) zuerst."""
        aelter = Case.objects.create(
            pk=uuid.UUID(int=1), facility=facility, client=client_identified, title="Aelter", created_by=staff_user
        )
        Case.objects.filter(pk=aelter.pk).update(created_at=timezone.now() - timedelta(days=2))
        Case.objects.create(
            pk=uuid.UUID(int=2**128 - 1),
            facility=facility,
            client=client_identified,
            title="Neuer",
            created_by=staff_user,
        )
        assert [entry["title"] for entry in _gather_cases(client_identified)] == ["Neuer", "Aelter"]
