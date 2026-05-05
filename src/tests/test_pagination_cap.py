"""Tests für ``safe_page_param`` (Refs #733, Audit-Massnahme #32).

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
