"""Perf-Regression: das Löschantrags-Nav-Gate cacht seinen EXISTS pro Request.

Refs #1755 (Review) / #1736: ``User.can_access_deletion_requests`` fällt für
Rollen ohne ``is_lead_or_admin``/``can_confirm_deletion`` auf
``has_open_deletion_requests`` zurück — ein ungecachtes
``DeletionRequest.objects.filter(...).exists()``. Beide Navigationsleisten
werten das Gate auf JEDER Seite aus (``_navigation_desktop`` + ``_mobile``), es
lief also doppelt: zwei EXISTS-Queries pro Request für Fachkraft/Assistenz.
``has_open_deletion_requests`` ist jetzt ``cached_property`` — pro Request teilt
sich die Nav eine ``User``-Instanz, der Cache reduziert das auf eine Query.
"""

import uuid

import pytest

from core.models.workitem import DeletionRequest

pytestmark = pytest.mark.django_db


def _pending_request(facility, user):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=uuid.uuid4(),
        reason="Perf-Test",
        requested_by=user,
        status=DeletionRequest.Status.PENDING,
    )


class TestNavGateQueryCount:
    def test_staff_gate_hits_db_once_across_both_navbars(self, facility, staff_user, django_assert_num_queries):
        """Zwei Nav-Auswertungen (Desktop + Mobil) = eine Query dank Cache.

        Vor dem Fix (``@property``) waren es zwei identische EXISTS.
        """
        _pending_request(facility, staff_user)

        with django_assert_num_queries(1):
            first = staff_user.can_access_deletion_requests
            second = staff_user.can_access_deletion_requests

        assert first is True
        assert second is True

    def test_lead_gate_short_circuits_without_query(self, lead_user, django_assert_num_queries):
        """Leitung/Genehmiger:innen brauchen den EXISTS gar nicht (Rollen-Kurzschluss)."""
        with django_assert_num_queries(0):
            assert lead_user.can_access_deletion_requests is True

    def test_cache_is_per_instance_not_process_wide(self, facility, staff_user):
        """Der Cache lebt an der Instanz (Request-Lebensdauer), nicht prozessweit.

        So ist der Redirect-nach-Antragstellung unkritisch: die Folge-Seite lädt
        eine frische ``User``-Instanz und sieht den Antrag.
        """
        assert staff_user.has_open_deletion_requests is False
        _pending_request(facility, staff_user)
        # Dieselbe Instanz bleibt (bewusst) gecacht ...
        assert staff_user.has_open_deletion_requests is False
        # ... eine neu geladene Instanz (= neuer Request) sieht den Stand aktuell.
        reloaded = type(staff_user).objects.get(pk=staff_user.pk)
        assert reloaded.has_open_deletion_requests is True
