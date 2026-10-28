"""Pin processing locks and explicit empty document type selections.

Source: mutation delta of the release-0.99.9 checkpoint, 8 mutants.
Groups: processing gate row locks (6), explicit type selection (2).
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.translation import gettext

from core.models import DocumentType
from core.services.client import main

pytestmark = pytest.mark.django_db


@pytest.mark.postgres
@pytest.mark.parametrize("operation", ["update", "anonymize"])
def test_processing_gate_locks_the_person(monkeypatch, client_identified, staff_user, operation):
    """The processing gate itself holds the person row before allowing writes.

    x_update_client__mutmut_26, x_update_client__mutmut_28,
    x_update_client__mutmut_29, x_anonymize_client_now__mutmut_40,
    x_anonymize_client_now__mutmut_42, x_anonymize_client_now__mutmut_43.
    """
    original = main.assert_processing_allowed
    gate_queries = []

    def observe(*args, **kwargs):
        with CaptureQueriesContext(connection) as queries:
            result = original(*args, **kwargs)
        gate_queries.extend(query["sql"] for query in queries)
        return result

    monkeypatch.setattr(main, "assert_processing_allowed", observe)
    if operation == "update":
        main.update_client(client_identified, staff_user, pseudonym="Updated person")
    else:
        client_identified.soft_delete(user=staff_user)
        main.anonymize_client_now(client_identified, staff_user)

    locks = [
        sql
        for sql in gate_queries
        if 'FROM "core_client"' in sql and any(mode in sql for mode in ("FOR UPDATE", "FOR NO KEY UPDATE"))
    ]
    assert locks
    assert any(client_identified.pk.hex in sql and "WHERE" in sql for sql in locks)


def test_explicit_empty_type_does_not_choose_the_only_active_type(client_identified, facility, staff_user):
    """An explicit empty selection is rejected even with a sole active type.

    x_update_client__mutmut_96, x_update_client__mutmut_97.
    """
    DocumentType.objects.create(facility=facility, name="Only profile", entity_type=DocumentType.EntityType.CLIENT)

    with pytest.raises(ValidationError) as error:
        main.update_client(client_identified, staff_user, document_type=None)

    assert error.value.messages == [gettext("Bitte wählen Sie einen Dokumentationstyp aus.")]
    client_identified.refresh_from_db()
    assert client_identified.document_type_id is None
