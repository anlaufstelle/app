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
