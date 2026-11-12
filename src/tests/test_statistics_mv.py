"""The migrated schema no longer contains a statistics materialized cache."""

import pytest
from django.db import connection


@pytest.mark.django_db
@pytest.mark.postgres
def test_no_statistics_cache_after_migrate():
    with connection.cursor() as cursor:
        cursor.execute("SELECT matviewname FROM pg_matviews WHERE matviewname LIKE %s", ["core_statistics_%"])
        assert cursor.fetchall() == []
