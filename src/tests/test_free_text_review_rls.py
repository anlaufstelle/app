"""Review metadata obeys real PostgreSQL read and write tenant boundaries."""

from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction
from django.utils import timezone

from core.models import DocumentTypeField, FieldTemplate, FreeTextReview
from core.services.free_text_review import get_entry, mark_review
from tests.test_rls_functional import as_rls_role, rls_test_role  # noqa: F401


@pytest.mark.django_db(transaction=True)
def test_review_policy_hides_foreign_rows_and_rejects_foreign_writes(
    rls_test_role,  # noqa: F811
    facility,
    staff_user,
    sample_event,
    second_facility,
    foreign_staff,
    foreign_event,
):
    field = FieldTemplate.objects.create(facility=second_facility, name="Notiz", field_type="textarea")
    DocumentTypeField.objects.create(document_type=foreign_event.document_type, field_template=field)
    reviews = []
    for scope, user, person in [
        (facility, staff_user, sample_event),
        (second_facility, foreign_staff, foreign_event),
    ]:
        person.data_json = {"notiz": "Neutral source content"}
        person.save()
        entry = get_entry(scope, user, "Event", person.pk, "notiz")
        reviews.append(mark_review(scope, user, "Event", person.pk, "notiz", timezone.localdate(), entry.fingerprint))
    with as_rls_role(rls_test_role, facility_id=facility.pk) as cursor:
        cursor.execute("SELECT set_config('app.is_super_admin', 'false', false)")
        assert list(FreeTextReview.objects.values_list("pk", flat=True)) == [reviews[0].pk]
        with pytest.raises(DatabaseError, match="row-level security"), transaction.atomic():
            FreeTextReview.objects.create(
                id=uuid4(),
                facility=second_facility,
                target_type="Event",
                target_id=foreign_event.pk,
                field_key="notiz",
                reviewed_until=timezone.localdate(),
                content_fingerprint="0" * 64,
            )
        with pytest.raises(DatabaseError, match="row-level security"), transaction.atomic():
            cursor.execute(
                "UPDATE core_freetextreview SET facility_id = %s WHERE id = %s", [second_facility.pk, reviews[0].pk]
            )
    assert FreeTextReview.objects.for_facility(facility).count() == 1
