"""Concurrent public marking calls serialize on the same visible source."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections
from django.utils import timezone

from core.models import AuditLog, FreeTextReview
from core.services.free_text_review import get_entry, mark_review


@pytest.mark.django_db(transaction=True)
def test_parallel_marks_keep_one_row_and_audit_both(facility, staff_user, sample_workitem):
    sample_workitem.description = "Neutral review content"
    sample_workitem.save()
    entry = get_entry(facility, staff_user, "WorkItem", sample_workitem.pk, "description")
    start = Barrier(2)

    def mark():
        try:
            start.wait(timeout=10)
            review = mark_review(
                facility,
                staff_user,
                "WorkItem",
                sample_workitem.pk,
                "description",
                timezone.localdate(),
                entry.fingerprint,
            )
            return review.pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(mark)
        second = pool.submit(mark)
        assert first.result(timeout=20) == second.result(timeout=20)
    assert FreeTextReview.objects.for_facility(facility).count() == 1
    assert AuditLog.objects.for_facility(facility).filter(action="free_text_reviewed").count() == 2
