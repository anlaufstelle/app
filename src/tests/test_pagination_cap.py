"""Tests für ``safe_page_param`` (Refs #733).

Schuetzt die List-Views (clients, cases, audit) gegen ?page=99999-
Angriffe, die Postgres in einen Seq-Scan zwingen.
"""

import pytest
from django.test import RequestFactory

from core.constants import MAX_PAGE
from core.views.utils import safe_page_param


@pytest.mark.django_db
class TestSafePageParam:
    def test_normal_page_pass_through(self):
        rf = RequestFactory()
        request = rf.get("/?page=2")
        assert safe_page_param(request) == 2

    def test_huge_page_capped_at_max(self):
        rf = RequestFactory()
        request = rf.get("/?page=99999")
        assert safe_page_param(request) == MAX_PAGE

    def test_negative_page_clamped_to_one(self):
        rf = RequestFactory()
        request = rf.get("/?page=-5")
        assert safe_page_param(request) == 1

    def test_zero_page_clamped_to_one(self):
        rf = RequestFactory()
        request = rf.get("/?page=0")
        assert safe_page_param(request) == 1

    def test_non_integer_returns_default(self):
        rf = RequestFactory()
        request = rf.get("/?page=abc")
        assert safe_page_param(request) == 1

    def test_missing_page_returns_default(self):
        rf = RequestFactory()
        request = rf.get("/")
        assert safe_page_param(request) == 1

    def test_explicit_default_used(self):
        rf = RequestFactory()
        request = rf.get("/?page=invalid")
        assert safe_page_param(request, default=5) == 5

    def test_custom_max_page(self):
        rf = RequestFactory()
        request = rf.get("/?page=999")
        assert safe_page_param(request, max_page=10) == 10


@pytest.mark.django_db
class TestPaginatedListMixin:
    """Refs #803 (C-36): Mixin gibt das Paginator-get_page-Ergebnis und
    delegiert die Page-Number-Sanitierung an safe_page_param."""

    def _request(self, page=None):
        rf = RequestFactory()
        url = f"/?page={page}" if page is not None else "/"
        return rf.get(url)

    def test_paginate_returns_first_page_for_empty_list(self):
        from core.views.mixins import PaginatedListMixin

        mixin = PaginatedListMixin()
        page = mixin.paginate([], self._request())
        assert page.number == 1
        assert list(page.object_list) == []

    def test_paginate_respects_page_size(self):
        from core.views.mixins import PaginatedListMixin

        class _View(PaginatedListMixin):
            page_size = 3

        page = _View().paginate(list(range(10)), self._request(page=2))
        assert page.number == 2
        assert list(page.object_list) == [3, 4, 5]

    def test_paginate_caps_huge_page_request(self):
        from core.views.mixins import PaginatedListMixin

        class _View(PaginatedListMixin):
            page_size = 5

        # safe_page_param cappt bei MAX_PAGE — Paginator liefert daraufhin
        # die letzte tatsaechliche Seite (3 Eintraege => 1 Seite).
        page = _View().paginate(list(range(3)), self._request(page=99999))
        assert page.number == 1
        assert list(page.object_list) == [0, 1, 2]

    def test_paginate_default_page_size_from_constants(self):
        from core.constants import DEFAULT_PAGE_SIZE
        from core.views.mixins import PaginatedListMixin

        page = PaginatedListMixin().paginate(list(range(DEFAULT_PAGE_SIZE + 5)), self._request())
        assert page.paginator.per_page == DEFAULT_PAGE_SIZE
        assert page.has_next() is True


class _RecordingQuerySet:
    """Minimaler Queryset-Stub: zeichnet ``filter``-Aufrufe auf, ohne DB.

    ``apply_search``/``apply_filters`` rufen nur ``queryset.filter(**kwargs)``
    auf und reichen das Ergebnis weiter — fuer den Verhaltensnachweis genuegt
    es, die uebergebenen Lookup-Kwargs (und ggf. ein ``Q``-Objekt) zu
    protokollieren. So bleibt der Test DB-frei und deterministisch.
    """

    def __init__(self):
        self.filter_calls = []
        self.q_args = []

    def filter(self, *args, **kwargs):
        if args:
            self.q_args.extend(args)
        if kwargs:
            self.filter_calls.append(kwargs)
        return self


class TestFilteredPaginatedListMixin:
    """Refs #1164 (R5): q-Suche, Equality-Filter und ``pagination_params``
    des ``FilteredPaginatedListMixin`` — DB-frei ueber einen Recording-Stub
    bzw. ``RequestFactory``."""

    def _request(self, query=""):
        rf = RequestFactory()
        url = f"/?{query}" if query else "/"
        return rf.get(url)

    def _view(self, *, search_fields=None, filter_fields=None, page_size=None):
        from core.views.mixins import FilteredPaginatedListMixin

        class _View(FilteredPaginatedListMixin):
            pass

        if search_fields is not None:
            _View.search_fields = search_fields
        if filter_fields is not None:
            _View.filter_fields = filter_fields
        if page_size is not None:
            _View.page_size = page_size
        return _View()

    # --- Suche -----------------------------------------------------------

    def test_apply_search_builds_icontains_for_single_field(self):
        from django.db.models import Q

        view = self._view(search_fields=["pseudonym"])
        qs = _RecordingQuerySet()
        view.apply_search(qs, self._request("q=Falke"))

        assert len(qs.q_args) == 1
        assert qs.q_args[0] == Q(pseudonym__icontains="Falke")

    def test_apply_search_ors_multiple_fields(self):
        from django.db.models import Q

        view = self._view(search_fields=["title", "pseudonym"])
        qs = _RecordingQuerySet()
        view.apply_search(qs, self._request("q=abc"))

        expected = Q(title__icontains="abc") | Q(pseudonym__icontains="abc")
        assert qs.q_args == [expected]

    def test_apply_search_noop_for_empty_query(self):
        view = self._view(search_fields=["pseudonym"])
        qs = _RecordingQuerySet()
        view.apply_search(qs, self._request())

        assert qs.q_args == []
        assert qs.filter_calls == []

    def test_apply_search_noop_when_no_search_fields(self):
        view = self._view(search_fields=[])
        qs = _RecordingQuerySet()
        view.apply_search(qs, self._request("q=abc"))

        assert qs.q_args == []

    def test_get_search_term_strips_whitespace(self):
        view = self._view(search_fields=["pseudonym"])
        assert view.get_search_term(self._request("q=%20%20Falke%20%20")) == "Falke"

    # --- Equality-Filter -------------------------------------------------

    def test_apply_filters_translates_param_to_model_field(self):
        view = self._view(filter_fields={"stage": "contact_stage", "age": "age_cluster"})
        qs = _RecordingQuerySet()
        view.apply_filters(qs, self._request("stage=QUALIFIED&age=A18_25"))

        assert {"contact_stage": "QUALIFIED"} in qs.filter_calls
        assert {"age_cluster": "A18_25"} in qs.filter_calls
        assert len(qs.filter_calls) == 2

    def test_apply_filters_skips_unset_params(self):
        view = self._view(filter_fields={"stage": "contact_stage", "age": "age_cluster"})
        qs = _RecordingQuerySet()
        view.apply_filters(qs, self._request("stage=QUALIFIED"))

        assert qs.filter_calls == [{"contact_stage": "QUALIFIED"}]

    def test_apply_filters_noop_without_filter_fields(self):
        view = self._view(filter_fields={})
        qs = _RecordingQuerySet()
        view.apply_filters(qs, self._request("stage=QUALIFIED"))

        assert qs.filter_calls == []

    # --- pagination_params ----------------------------------------------

    def test_pagination_params_includes_q_and_filters(self):
        view = self._view(search_fields=["pseudonym"], filter_fields={"stage": "contact_stage", "age": "age_cluster"})
        result = view.pagination_params(self._request("q=Falke&stage=QUALIFIED&age=A18_25"))

        assert result == "q=Falke&stage=QUALIFIED&age=A18_25"

    def test_pagination_params_drops_empty_values(self):
        view = self._view(search_fields=["pseudonym"], filter_fields={"stage": "contact_stage", "age": "age_cluster"})
        result = view.pagination_params(self._request("stage=QUALIFIED"))

        assert result == "stage=QUALIFIED"

    def test_pagination_params_omits_q_without_search_fields(self):
        # Audit-Fall: kein q-Feld, aber Equality-Filter.
        view = self._view(search_fields=[], filter_fields={"action": "action", "user": "user_id"})
        result = view.pagination_params(self._request("q=ignored&action=LOGIN"))

        assert result == "action=LOGIN"

    def test_pagination_params_appends_extra_params_after_filters(self):
        # Audit-Fall: Datumsfelder via extra_params, custom geparst.
        view = self._view(search_fields=[], filter_fields={"action": "action", "user": "user_id"})
        result = view.pagination_params(
            self._request("action=LOGIN&user=7"),
            extra_params={"date_from": "2026-01-01", "date_to": ""},
        )

        # action, user, dann date_from; leeres date_to faellt raus.
        assert result == "action=LOGIN&user=7&date_from=2026-01-01"

    # --- Pagination-Cap (geerbt) ----------------------------------------

    def test_inherited_paginate_caps_huge_page_request(self):
        from core.constants import MAX_PAGE

        view = self._view(search_fields=["pseudonym"], page_size=5)
        page = view.paginate(list(range(3)), self._request("page=99999"))

        # safe_page_param cappt bei MAX_PAGE; Paginator liefert die letzte
        # reale Seite — der Filtered-Mixin erbt das Cap-Verhalten unveraendert.
        assert MAX_PAGE >= 1
        assert page.number == 1
        assert list(page.object_list) == [0, 1, 2]
