"""DSGVO-Regressionstests fuer beide Soft-Delete-Pfade (Refs #714).

Sowohl der manuelle ``soft_delete_event``-Pfad als auch der
automatische ``retention._soft_delete_events``-Pfad muessen identische
redaktierte EventHistory(DELETE)-Eintraege schreiben — kein Klartext
in JSONB persistieren, weil EventHistory append-only ist.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import DocumentType, Event, EventHistory
from core.services.events import build_redacted_delete_history, soft_delete_event
from core.services.retention import _soft_delete_events


@pytest.fixture
def normal_doc_type(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
        category=DocumentType.Category.CONTACT,
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )


@pytest.fixture
def event_with_pii(facility, normal_doc_type, admin_user, client_identified):
    return Event.objects.create(
        facility=facility,
        document_type=normal_doc_type,
        client=client_identified,
        occurred_at=timezone.now() - timedelta(days=200),
        data_json={
            "freitext": "psychische Krise, hat konsumiert",
            "ort": "Cafe X",
            "intervention": "Krisengespraech 30 min",
        },
        created_by=admin_user,
    )


@pytest.mark.django_db
class TestBuildRedactedDeleteHistory:
    """Helper baut nur Slugs in {_redacted: True, fields: [...]}."""

    def test_redacted_payload_contains_no_values(self, event_with_pii):
        payload = build_redacted_delete_history(event_with_pii)
        assert payload["_redacted"] is True
        assert sorted(payload["fields"]) == sorted(["freitext", "ort", "intervention"])
        # Ein einziger zusaetzlicher Test, der explizit die Klartext-Werte
        # negativ pruefen koennte: alle dict-Repr-Strings duerfen die
        # Originalwerte nicht enthalten.
        repr_str = str(payload)
        assert "psychische Krise" not in repr_str
        assert "Cafe X" not in repr_str
        assert "Krisengespraech" not in repr_str

    def test_empty_data_json_yields_empty_fields(self, facility, normal_doc_type, admin_user):
        event = Event.objects.create(
            facility=facility,
            document_type=normal_doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )
        payload = build_redacted_delete_history(event)
        assert payload == {"_redacted": True, "fields": []}


@pytest.mark.django_db
class TestSoftDeleteEventRedacts:
    """Der manuelle Loesch-Pfad redaktiert (war schon so, Regression-Schutz)."""

    def test_manual_soft_delete_writes_redacted_history(self, event_with_pii, admin_user):
        soft_delete_event(event_with_pii, admin_user)
        history = EventHistory.objects.filter(event=event_with_pii, action=EventHistory.Action.DELETE).first()
        assert history is not None
        assert history.data_before == {
            "_redacted": True,
            "fields": ["freitext", "ort", "intervention"],
        } or (
            history.data_before["_redacted"] is True
            and sorted(history.data_before["fields"]) == sorted(["freitext", "ort", "intervention"])
        )
        # Klartext darf nirgends in der EventHistory landen.
        assert "psychische Krise" not in str(history.data_before)


@pytest.mark.django_db
class TestRetentionSoftDeleteRedacts:
    """Der automatische Retention-Pfad redaktiert ebenfalls — fix Refs #714."""

    def test_retention_soft_delete_writes_redacted_history(self, event_with_pii, facility):
        qs = Event.objects.filter(pk=event_with_pii.pk)
        _soft_delete_events(qs, facility, category="anonymous", retention_days=90)

        history = EventHistory.objects.filter(event=event_with_pii, action=EventHistory.Action.DELETE).first()
        assert history is not None
        assert history.data_before["_redacted"] is True
        assert sorted(history.data_before["fields"]) == sorted(["freitext", "ort", "intervention"])

        # Klartext-Werte duerfen NICHT in der EventHistory landen.
        repr_str = str(history.data_before)
        assert "psychische Krise" not in repr_str
        assert "Cafe X" not in repr_str
        assert "Krisengespraech" not in repr_str

    def test_retention_soft_delete_clears_search_text(self, event_with_pii, facility):
        # Refs #1092: ``_soft_delete_events`` muss ``search_text`` in
        # ``update_fields`` aufnehmen, sonst persistiert Django den vom
        # ``pre_save``-Signal geleerten Wert nicht und der Klartext-PII bleibt
        # in der search_text-Spalte stehen (DSGVO-Residue).
        #
        # search_text deterministisch befuellen, ohne Slug-Mechanik: ``.update()``
        # umgeht das pre_save-Signal und schreibt echten Klartext in die Spalte —
        # genau die Residue, die der Soft-Delete tilgen muss (Stil analog
        # test_client_anonymize_characterization.py).
        Event.objects.filter(pk=event_with_pii.pk).update(search_text="Klartext-Krise")

        qs = Event.objects.filter(pk=event_with_pii.pk)
        _soft_delete_events(qs, facility, category="anonymous", retention_days=90)

        event_with_pii.refresh_from_db()
        assert event_with_pii.is_deleted is True
        assert event_with_pii.data_json == {}
        assert event_with_pii.search_text == ""

    def test_retention_soft_delete_includes_field_metadata(self, event_with_pii, facility):
        # Refs #714: bisheriger Retention-Pfad schrieb kein field_metadata,
        # was die Audit-Spur beim Restore unvollstaendig machte. Jetzt
        # konsistent mit soft_delete_event.
        qs = Event.objects.filter(pk=event_with_pii.pk)
        _soft_delete_events(qs, facility, category="anonymous", retention_days=90)
        history = EventHistory.objects.filter(event=event_with_pii, action=EventHistory.Action.DELETE).first()
        assert history is not None
        assert isinstance(history.field_metadata, dict)
