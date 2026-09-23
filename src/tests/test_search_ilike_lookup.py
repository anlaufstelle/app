"""Tests for the custom __ilike lookup used by search paths.

Refs https://github.com/anlaufstelle/app/issues/2027
"""

import json
from unittest import mock

import pytest
from django.core.exceptions import FieldError
from django.db import connection
from django.db.models import F, Q, Value
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Client, Facility, Organization, User
from core.services.dashboard.search import (
    client_search_queryset,
    event_search_queryset,
    search_similar_clients,
)

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


def test_apply_search_builds_ilike_for_single_field():
    from core.views.mixins import FilteredPaginatedListMixin

    class _View(FilteredPaginatedListMixin):
        search_fields = ["pseudonym"]

    view = _View()
    qs = _RecordingQuerySet()
    view.apply_search(qs, _request_with_q("Falke"))

    assert len(qs.q_args) == 1
    assert qs.q_args[0] == Q(pseudonym__ilike="Falke")


def test_apply_search_builds_ilike_for_multiple_fields():
    from core.views.mixins import FilteredPaginatedListMixin

    view = FilteredPaginatedListMixin()
    view.search_fields = ["title", "pseudonym"]
    qs = _RecordingQuerySet()
    view.apply_search(qs, _request_with_q("abc"))

    expected = Q(title__ilike="abc") | Q(pseudonym__ilike="abc")
    assert qs.q_args == [expected]


def _sql_has_ilike_no_upper(qs):
    sql = str(qs.query)
    assert "ILIKE" in sql, sql
    assert "UPPER(" not in sql, sql


def test_client_list_search_sql_contains_ilike_no_upper():
    """ClientListView / apply_search produces ILIKE SQL without UPPER()."""
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    term = r'm"u\%ller'
    qs = Client.objects.for_facility(facility).filter(is_active=True, is_deleted=False, pseudonym__ilike=term)
    _sql_has_ilike_no_upper(qs)


def test_client_search_queryset_uses_ilike():
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    qs = client_search_queryset(facility, "abc")
    _sql_has_ilike_no_upper(qs)


def test_event_search_queryset_uses_ilike():
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    user = User.objects.create_user(username="u", password="x", facility=facility, role=User.Role.FACILITY_ADMIN)
    qs = event_search_queryset(facility, user, "abc")
    _sql_has_ilike_no_upper(qs)


def _captured_client_sql(ctx):
    """SQL of the captured statements that actually read ``core_client``.

    Refs #2027: Die beiden folgenden Tests haben frueher auf einem
    HANDGEBAUTEN Queryset assertiert — ein Revert der Produktionsstelle auf
    ``__icontains`` blieb dabei gruen. Jetzt laeuft der echte Code, und gepinnt
    wird die SQL, die er tatsaechlich absetzt.
    """
    statements = [q.get("sql", "") for q in ctx.captured_queries if 'FROM "core_client"' in q.get("sql", "")]
    assert statements, [q.get("sql", "") for q in ctx.captured_queries]
    return "\n".join(statements)


def test_search_similar_clients_exclude_uses_ilike():
    """The exclude inside ``search_similar_clients`` compiles to NOT (... ILIKE ...).

    A revert of ``src/core/services/dashboard/search.py`` to
    ``pseudonym__icontains`` turns the exclude into
    ``NOT (UPPER("core_client"."pseudonym"::text) LIKE ...)`` and makes this red.
    """
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    Client.objects.create(facility=facility, pseudonym="Abcdef")
    with CaptureQueriesContext(connection) as ctx:
        search_similar_clients(facility, "abc", threshold=0.1)
    sql = _captured_client_sql(ctx)
    assert 'NOT ("core_client"."pseudonym" ILIKE ' in sql, sql
    assert 'UPPER("core_client"."pseudonym"' not in sql, sql


def test_client_autocomplete_view_uses_ilike(client):
    """The autocomplete endpoint searches with ILIKE — driven through the real URL.

    Logged-in user, ``?q=`` on ``core:client_autocomplete``; the JSON payload
    proves the filter actually matched, the SQL pin proves how.
    """
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    user = User.objects.create_user(
        username="autocomplete-user", password="x", facility=facility, role=User.Role.ASSISTANT
    )
    Client.objects.create(facility=facility, pseudonym="Abcdef")
    client.force_login(user)
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("core:client_autocomplete"), {"q": "abc"})
    assert response.status_code == 200
    assert [row["pseudonym"] for row in json.loads(response.content)] == ["Abcdef"]
    sql = _captured_client_sql(ctx)
    assert '"core_client"."pseudonym" ILIKE ' in sql, sql
    assert 'UPPER("core_client"."pseudonym"' not in sql, sql


def test_ilike_lookup_only_on_text_fields():
    """UUIDField / DateTimeField must raise FieldError, not compile invalid SQL."""
    with pytest.raises(FieldError, match="Unsupported lookup 'ilike' for UUIDField"):
        str(Client.objects.filter(id__ilike="abc").query)
    with pytest.raises(FieldError, match="Unsupported lookup 'ilike' for DateTimeField"):
        str(Client.objects.filter(created_at__ilike="2026").query)


def test_ilike_expression_rhs_has_wildcards_and_escapes():
    """F()/Value() rhs must still wrap the expression in % wildcards AND escape %/_."""
    qs = Client.objects.filter(pseudonym__ilike=F("pseudonym"))
    sql = str(qs.query)
    assert "ILIKE '%' ||" in sql, sql
    assert "|| '%'" in sql, sql
    # Django's PostgreSQL pattern_esc must be applied to the rhs.
    assert "REPLACE(REPLACE(REPLACE(" in sql, sql

    qs2 = Client.objects.filter(pseudonym__ilike=Value("a%b"))
    sql2, params = qs2.query.sql_with_params()
    assert "ILIKE '%%' || REPLACE(REPLACE(REPLACE(%s" in sql2, sql2
    assert params == ("a%b",)


def test_ilike_value_rhs_escapes_wildcards_in_expression_mode():
    """A Value('%') rhs must be treated as a literal percent, not a wildcard."""
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    Client.objects.create(facility=facility, pseudonym="xa%by")
    Client.objects.create(facility=facility, pseudonym="aXXXb")
    # Value("a%b") should match 'xa%by' (literal percent) but NOT 'aXXXb'.
    qs = Client.objects.filter(facility=facility, pseudonym__ilike=Value("a%b"))
    assert qs.count() == 1
    assert qs.first().pseudonym == "xa%by"


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


def test_ilike_escapes_wildcards_for_direct_values():
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
