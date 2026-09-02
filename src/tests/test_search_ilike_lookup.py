"""Tests for the custom __ilike lookup used by search paths.

Refs https://github.com/anlaufstelle/app/issues/2027
"""

from unittest import mock

import pytest
from django.db.models import Q

from core.models import Client, Facility, Organization

pytestmark = [pytest.mark.django_db]


class _RecordingQuerySet:
    """Minimal queryset double that records filter() q_args."""

    def __init__(self):
        self.q_args = []

    def filter(self, *args):
        self.q_args.extend(args)
        return self


def _request_with_q(q):
    request = mock.MagicMock()
    request.GET = {"q": q}
    return request


def test_apply_search_uses_ilike_not_icontains():
    from core.views.mixins import FilteredPaginatedListMixin

    class _View(FilteredPaginatedListMixin):
        search_fields = ["pseudonym"]

    view = _View()
    qs = _RecordingQuerySet()
    view.apply_search(qs, _request_with_q("Falke"))

    assert len(qs.q_args) == 1
    assert qs.q_args[0] == Q(pseudonym__ilike="Falke")


def test_client_list_search_sql_contains_ilike_no_upper():
    """Generated SQL must use ILIKE and no UPPER() wrapper."""
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    term = r'm"u\%ller'
    qs = Client.objects.for_facility(facility).filter(is_active=True, is_deleted=False, pseudonym__ilike=term)
    sql = str(qs.query)
    assert "ILIKE" in sql, sql
    assert "UPPER(" not in sql, sql


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("Müller", "müller"),
        ("müller", "MÜLLER"),
        ("spaces", "  SPACES  "),
    ],
)
def test_ilike_case_and_umlaut_insensitive(term, expected):
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    Client.objects.create(facility=facility, pseudonym=expected)
    assert Client.objects.filter(facility=facility, pseudonym__ilike=term).exists()


def test_ilike_escapes_wildcards():
    """% and _ in the search term are treated as literals, not wildcards."""
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    Client.objects.create(facility=facility, pseudonym="50% discount")
    Client.objects.create(facility=facility, pseudonym="50 discount")

    assert Client.objects.filter(facility=facility, pseudonym__ilike="50% discount").count() == 1
    assert Client.objects.filter(facility=facility, pseudonym__ilike="50_discount").count() == 0


def test_ilike_plan_uses_trgm_index():
    """With seqscan disabled the planner can use the GIN trgm index for ILIKE.

    The lookup compiles to ``col ~~* '%term%'`` instead of wrapping the column
    with ``UPPER()``. This proves the GIN index on the raw column is usable.
    """
    from django.db import connection

    org = Organization.objects.create(name="Org")
    facility = Facility.objects.create(organization=org, name="Stelle")
    Client.objects.bulk_create([Client(facility=facility, pseudonym=f"person{i:03d}") for i in range(500)])
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE core_client")
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("SET LOCAL enable_indexscan = off")
        # No facility filter: the btree on (facility, pseudonym) cannot help,
        # so the planner should pick the GIN trgm index for the ILIKE predicate.
        qs = Client.objects.filter(pseudonym__ilike="person")
        plan = qs.explain()
        assert "client_pseudonym_trgm_idx" in plan, plan
