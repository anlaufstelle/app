"""Tests fuer den zentralen Facility-Scoping-Helfer (Refs #1346).

``get_scoped_object`` ersetzt das ~40x copy-paste Muster
``get_object_or_404(Model, pk=pk, facility=request.current_facility)`` durch
einen einzigen, strukturell unvergesslichen Lookup-Pfad.
"""

import pytest
from django.http import Http404
from django.test import RequestFactory

from core.models import Event, WorkItem
from core.services.scoping import get_scoped_object

pytestmark = pytest.mark.django_db


@pytest.fixture
def rf():
    return RequestFactory()


def _request(rf, facility):
    """Request-Stub analog zur FacilityScopeMiddleware (setzt current_facility)."""
    request = rf.get("/")
    request.current_facility = facility
    return request


class TestGetScopedObject:
    def test_loads_object_scoped_to_current_facility(self, rf, facility, client_identified):
        request = _request(rf, facility)
        obj = get_scoped_object(type(client_identified), request, pk=client_identified.pk)
        assert obj.pk == client_identified.pk

    def test_foreign_facility_object_raises_404(self, rf, facility, foreign_client):
        """Ein Objekt einer anderen Facility darf NIE geladen werden — 404 statt Leak."""
        request = _request(rf, facility)
        with pytest.raises(Http404):
            get_scoped_object(type(foreign_client), request, pk=foreign_client.pk)

    def test_missing_current_facility_raises_instead_of_silently_unscoping(self, rf, client_identified):
        """Ohne ``request.current_facility`` darf der Lookup NICHT still ungescoped laufen."""
        request = rf.get("/")
        request.current_facility = None
        with pytest.raises(ValueError):
            get_scoped_object(type(client_identified), request, pk=client_identified.pk)

    def test_queryset_argument_keeps_select_related(self, rf, facility, sample_workitem, django_assert_num_queries):
        """QuerySet/Manager als erstes Argument (z.B. mit select_related) bleibt erhalten —
        wichtig fuer Call-Sites, die Prefetching/Locking vor dem Scoping-Filter aufbauen."""
        request = _request(rf, facility)
        qs = WorkItem.objects.select_related("client", "created_by")
        obj = get_scoped_object(qs, request, pk=sample_workitem.pk)
        assert obj.pk == sample_workitem.pk
        # select_related wurde tatsaechlich angewendet — kein Extra-Query fuer client/created_by.
        with django_assert_num_queries(0):
            _ = obj.client
            _ = obj.created_by

    def test_event_is_rejected_with_reference_to_dedicated_loader(self, rf, facility):
        """Event-Einzel-Lookups muessen ueber get_visible_event_or_404 laufen
        (Sensitivitaets-Policy) — analog zu TestEventAccessPolicyGuard."""
        request = _request(rf, facility)
        with pytest.raises(ValueError, match="get_visible_event_or_404"):
            get_scoped_object(Event, request, pk="00000000-0000-0000-0000-000000000000")
