"""Unit-Tests für Attachment-Versionierung Stufe B (Refs #622).

Deckt ab:
- Service-Modi ``add`` / ``replace`` / ``remove``.
- Data-JSON-Format ``__files__`` mit mehreren Einträgen.
- Rückwärtskompatibilität: Stufe-A-``__file__``-Marker bleibt lesbar, wird
  beim ersten Edit ins neue Format überführt.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.models.attachment import EventAttachment
from core.services.event import (
    is_multi_file_marker,
    is_singleton_file_marker,
    normalize_file_marker,
)
from core.services.file_vault import (
    get_current_entries_for_field,
    soft_delete_attachment_chain,
    store_encrypted_file,
)

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _upload(marker=b"A"):
    return SimpleUploadedFile(f"file_{marker.decode()}.pdf", PDF_HEADER + marker, content_type="application/pdf")


@pytest.fixture
def doc_type_with_file(facility):
    dt = DocumentType.objects.create(facility=facility, name="DocFile", category=DocumentType.Category.NOTE)
    ft = FieldTemplate.objects.create(facility=facility, name="Anhang", field_type="file")
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
    return dt, ft


@pytest.mark.django_db
class TestNormalizeFileMarker:
    def test_singleton_detected(self):
        assert is_singleton_file_marker({"__file__": True, "attachment_id": "x"})
        assert not is_multi_file_marker({"__file__": True, "attachment_id": "x"})

    def test_multi_detected(self):
        marker = {"__files__": True, "entries": [{"id": "x", "sort": 0}]}
        assert is_multi_file_marker(marker)
        assert not is_singleton_file_marker(marker)

    def test_normalize_singleton_to_list(self):
        out = normalize_file_marker({"__file__": True, "attachment_id": "abc"})
        assert out == [{"id": "abc", "sort": 0}]

    def test_normalize_multi_keeps_list(self):
        out = normalize_file_marker({"__files__": True, "entries": [{"id": "a", "sort": 0}, {"id": "b", "sort": 1}]})
        assert out == [{"id": "a", "sort": 0}, {"id": "b", "sort": 1}]

    def test_normalize_non_marker(self):
        assert normalize_file_marker("no dict") == []
        assert normalize_file_marker({"foo": "bar"}) == []
        assert normalize_file_marker(None) == []


@pytest.mark.django_db
class TestStoreEncryptedFileModes:
    """Service store_encrypted_file unterstützt add + replace-Modi (Stufe B)."""

    def test_add_creates_new_entry_id(self, facility, staff_user, doc_type_with_file):
        dt, ft = doc_type_with_file
        event = Event.objects.create(
            facility=facility, document_type=dt, occurred_at=timezone.now(), created_by=staff_user
        )
        a1 = store_encrypted_file(facility, _upload(b"1"), ft, event, staff_user)
        a2 = store_encrypted_file(facility, _upload(b"2"), ft, event, staff_user)
        assert a1.entry_id != a2.entry_id
        assert a1.is_current and a2.is_current
        assert a1.sort_order == 0
        assert a2.sort_order == 0  # ohne sort_order kwarg → default

    def test_replace_keeps_entry_id(self, facility, staff_user, doc_type_with_file):
        dt, ft = doc_type_with_file
        event = Event.objects.create(
            facility=facility, document_type=dt, occurred_at=timezone.now(), created_by=staff_user
        )
        a1 = store_encrypted_file(facility, _upload(b"1"), ft, event, staff_user, sort_order=3)
        a2 = store_encrypted_file(facility, _upload(b"2"), ft, event, staff_user, supersedes=a1)
        a1.refresh_from_db()
        a2.refresh_from_db()
        assert a2.entry_id == a1.entry_id, "Replace behält entry_id"
        assert a2.sort_order == 3, "Replace übernimmt sort_order"
        assert a1.is_current is False
        assert a1.superseded_by_id == a2.pk
        assert a2.is_current is True


@pytest.mark.django_db
class TestSoftDeleteAttachmentChain:
    def test_soft_delete_marks_all_versions(self, facility, staff_user, doc_type_with_file):
        dt, ft = doc_type_with_file
        event = Event.objects.create(
            facility=facility, document_type=dt, occurred_at=timezone.now(), created_by=staff_user
        )
        a1 = store_encrypted_file(facility, _upload(b"1"), ft, event, staff_user)
        a2 = store_encrypted_file(facility, _upload(b"2"), ft, event, staff_user, supersedes=a1)
        # Kette von 2 Attachments mit gleicher entry_id.
        count = soft_delete_attachment_chain(event, a2.entry_id, staff_user)
        assert count == 2
        a1.refresh_from_db()
        a2.refresh_from_db()
        assert a1.deleted_at is not None
        assert a2.deleted_at is not None

    def test_soft_delete_idempotent(self, facility, staff_user, doc_type_with_file):
        dt, ft = doc_type_with_file
        event = Event.objects.create(
            facility=facility, document_type=dt, occurred_at=timezone.now(), created_by=staff_user
        )
        a1 = store_encrypted_file(facility, _upload(b"1"), ft, event, staff_user)
        assert soft_delete_attachment_chain(event, a1.entry_id, staff_user) == 1
        assert soft_delete_attachment_chain(event, a1.entry_id, staff_user) == 0


@pytest.mark.django_db
class TestGetCurrentEntriesForField:
    def test_excludes_soft_deleted(self, facility, staff_user, doc_type_with_file):
        dt, ft = doc_type_with_file
        event = Event.objects.create(
            facility=facility, document_type=dt, occurred_at=timezone.now(), created_by=staff_user
        )
        a1 = store_encrypted_file(facility, _upload(b"1"), ft, event, staff_user, sort_order=0)
        a2 = store_encrypted_file(facility, _upload(b"2"), ft, event, staff_user, sort_order=1)
        assert len(get_current_entries_for_field(event, ft)) == 2
        soft_delete_attachment_chain(event, a1.entry_id, staff_user)
        remaining = get_current_entries_for_field(event, ft)
        assert len(remaining) == 1
        assert remaining[0].pk == a2.pk


@pytest.mark.django_db
class TestMultiFileCreateFlow:
    def test_create_event_with_multiple_files(self, client, staff_user, facility, doc_type_with_file):
        dt, ft = doc_type_with_file
        client.force_login(staff_user)

        files = [
            SimpleUploadedFile("a.pdf", PDF_HEADER + b"a", content_type="application/pdf"),
            SimpleUploadedFile("b.pdf", PDF_HEADER + b"b", content_type="application/pdf"),
            SimpleUploadedFile("c.pdf", PDF_HEADER + b"c", content_type="application/pdf"),
        ]
        resp = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(dt.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "anhang": files,
            },
        )
        assert resp.status_code == 302
        event = Event.objects.get(document_type=dt)
        marker = event.data_json["anhang"]
        assert marker.get("__files__") is True
        assert len(marker["entries"]) == 3
        atts = event.attachments.filter(is_current=True).order_by("sort_order")
        assert atts.count() == 3
        assert [a.sort_order for a in atts] == [0, 1, 2]
        # Drei unterschiedliche entry_ids — jede ihrer Kette.
        assert len({a.entry_id for a in atts}) == 3


@pytest.mark.django_db
class TestUpdateFlowAddReplaceRemove:
    def _create_with_two_files(self, client, staff_user, dt):
        files = [
            SimpleUploadedFile("a.pdf", PDF_HEADER + b"a", content_type="application/pdf"),
            SimpleUploadedFile("b.pdf", PDF_HEADER + b"b", content_type="application/pdf"),
        ]
        client.post(
            reverse("core:event_create"),
            {
                "document_type": str(dt.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "anhang": files,
            },
        )
        return Event.objects.get(document_type=dt)

    def test_add_more_files_on_update(self, client, staff_user, facility, doc_type_with_file):
        dt, _ft = doc_type_with_file
        client.force_login(staff_user)
        event = self._create_with_two_files(client, staff_user, dt)
        assert len(event.data_json["anhang"]["entries"]) == 2

        new_file = SimpleUploadedFile("c.pdf", PDF_HEADER + b"c", content_type="application/pdf")
        resp = client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(dt.pk),
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                "anhang": new_file,
            },
        )
        assert resp.status_code == 302
        event.refresh_from_db()
        entries = event.data_json["anhang"]["entries"]
        assert len(entries) == 3

    def test_remove_one_file_on_update(self, client, staff_user, facility, doc_type_with_file):
        dt, _ft = doc_type_with_file
        client.force_login(staff_user)
        event = self._create_with_two_files(client, staff_user, dt)
        first_entry = event.attachments.filter(is_current=True).order_by("sort_order").first()
        entry_id_to_remove = first_entry.entry_id

        resp = client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(dt.pk),
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                "anhang__remove": str(entry_id_to_remove),
            },
        )
        assert resp.status_code == 302
        event.refresh_from_db()
        entries = event.data_json["anhang"]["entries"]
        assert len(entries) == 1
        # Gesoft-gedeleted — nicht mehr in current.
        assert EventAttachment.objects.filter(event=event, is_current=True, deleted_at__isnull=True).count() == 1

    def test_remove_all_files_removes_marker(self, client, staff_user, facility, doc_type_with_file):
        dt, _ft = doc_type_with_file
        client.force_login(staff_user)
        event = self._create_with_two_files(client, staff_user, dt)
        entry_ids = [str(a.entry_id) for a in event.attachments.filter(is_current=True)]
        resp = client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(dt.pk),
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                "anhang__remove": ",".join(entry_ids),
            },
        )
        assert resp.status_code == 302
        event.refresh_from_db()
        # Marker sollte entfernt sein, wenn keine Entries mehr da sind.
        assert "anhang" not in event.data_json or not event.data_json["anhang"].get("entries")

    def test_add_replace_remove_in_one_post(self, client, staff_user, facility, doc_type_with_file):
        """Gemischter Stufe-B-Workflow: Add + Replace + Remove in einem Request."""
        dt, _ft = doc_type_with_file
        client.force_login(staff_user)
        event = self._create_with_two_files(client, staff_user, dt)
        atts = list(event.attachments.filter(is_current=True).order_by("sort_order"))
        to_remove = atts[0]
        to_replace = atts[1]

        resp = client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(dt.pk),
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                "anhang__remove": str(to_remove.entry_id),
                f"anhang__replace__{to_replace.entry_id}": SimpleUploadedFile(
                    "replaced.pdf", PDF_HEADER + b"r", content_type="application/pdf"
                ),
                "anhang": SimpleUploadedFile("new.pdf", PDF_HEADER + b"n", content_type="application/pdf"),
            },
        )
        assert resp.status_code == 302
        event.refresh_from_db()
        entries = event.data_json["anhang"]["entries"]
        # 2 Entries: Replace (neuer att für entry_id von to_replace) + Add (neuer entry_id).
        assert len(entries) == 2
        # Replace-Kette: entry_id von to_replace existiert noch, aber head hat neuen pk.
        replace_chain = event.attachments.filter(entry_id=to_replace.entry_id)
        assert replace_chain.count() == 2  # alt + neu
        head = replace_chain.filter(is_current=True).first()
        assert head is not None
        assert head.pk != to_replace.pk
        # Remove-Kette: alle soft-deleted.
        remove_chain = event.attachments.filter(entry_id=to_remove.entry_id)
        assert all(a.deleted_at is not None for a in remove_chain)


@pytest.mark.django_db
class TestBackwardCompatibility:
    """Ein Event mit altem __file__-Marker bleibt lesbar und wird beim Edit migriert."""

    def test_legacy_singleton_marker_readable(self, facility, staff_user, doc_type_with_file):
        """Detail-Context rendert ein Stufe-A-Event (mit __file__-Marker) korrekt."""
        dt, ft = doc_type_with_file
        event = Event.objects.create(
            facility=facility, document_type=dt, occurred_at=timezone.now(), created_by=staff_user
        )
        # Simuliere einen Stufe-A-Eintrag (das ist rückwärtskompatibler Code-Pfad).
        att = store_encrypted_file(facility, _upload(b"a"), ft, event, staff_user)
        event.data_json = {"anhang": {"__file__": True, "attachment_id": str(att.pk)}}
        event.save(update_fields=["data_json"])

        from core.services.event import build_event_detail_context

        ctx = build_event_detail_context(event, staff_user)
        field = next(f for f in ctx["fields_display"] if f.get("is_file"))
        assert field["attachment_id"] == str(att.pk)
        assert len(field["entries"]) == 1
        assert field["entries"][0]["attachment_id"] == str(att.pk)


@pytest.mark.django_db
class TestEventDetailContextQueryCount:
    """``build_event_detail_context`` darf bei mehr Attachments + Versions-
    Ketten nicht linear mehr Queries machen (#662 FND-05)."""

    def _build_event_with_chain(self, facility, staff_user, dt, ft, *, entries: int, versions: int):
        """Lege ein Event mit ``entries`` File-Eintraegen, jeder mit
        ``versions`` Replace-Versionen. Returns event."""
        event = Event.objects.create(
            facility=facility, document_type=dt, occurred_at=timezone.now(), created_by=staff_user
        )
        marker_entries = []
        for i in range(entries):
            current = store_encrypted_file(facility, _upload(f"e{i}-v0".encode()), ft, event, staff_user, sort_order=i)
            for v in range(1, versions):
                current = store_encrypted_file(
                    facility, _upload(f"e{i}-v{v}".encode()), ft, event, staff_user, supersedes=current
                )
            marker_entries.append({"id": str(current.pk), "sort": i})
        event.data_json = {"anhang": {"__files__": True, "entries": marker_entries}}
        event.save(update_fields=["data_json"])
        return event

    def _count(self, event, user):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from core.services.event import build_event_detail_context

        with CaptureQueriesContext(connection) as ctx:
            build_event_detail_context(event, user)
        return len(ctx.captured_queries)

    def test_constant_query_count_regardless_of_entries(self, facility, staff_user, doc_type_with_file):
        """Egal ob 1 oder 5 File-Entries, der Detail-Context muss mit
        derselben Anzahl Queries auskommen — sonst war wieder ein
        per-Entry-Lookup eingeschlichen."""
        dt, ft = doc_type_with_file

        small = self._build_event_with_chain(facility, staff_user, dt, ft, entries=1, versions=1)
        large = self._build_event_with_chain(facility, staff_user, dt, ft, entries=5, versions=1)

        small_queries = self._count(small, staff_user)
        large_queries = self._count(large, staff_user)

        # 5 Entries duerfen hoechstens 2 Queries mehr brauchen als 1 Entry
        # (Marge fuer kleine Variationen; vor dem Fix waren es 5+).
        assert large_queries <= small_queries + 2, (
            f"Query-Count waechst zu schnell: 1 Entry = {small_queries} Queries, "
            f"5 Entries = {large_queries} Queries. Erwartet <= {small_queries + 2}."
        )

    def test_constant_query_count_regardless_of_version_chain(self, facility, staff_user, doc_type_with_file):
        """Eine Versionskette mit 5 Versionen darf nicht 5x mehr Queries
        ausloesen als eine ohne Versionen."""
        dt, ft = doc_type_with_file

        no_versions = self._build_event_with_chain(facility, staff_user, dt, ft, entries=1, versions=1)
        with_versions = self._build_event_with_chain(facility, staff_user, dt, ft, entries=1, versions=5)

        without_chain_queries = self._count(no_versions, staff_user)
        with_chain_queries = self._count(with_versions, staff_user)

        assert with_chain_queries <= without_chain_queries + 1, (
            f"Versionskette laesst Query-Count wachsen: ohne = {without_chain_queries}, "
            f"mit 4 Vorgaengern = {with_chain_queries}."
        )
