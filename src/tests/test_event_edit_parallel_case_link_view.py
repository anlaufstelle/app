"""Ereignis bearbeiten bei paralleler Fallzuordnung — Oberflächenweg (Refs #2359).

Tab A rendert das Bearbeiten-Formular eines Ereignisses, Tab B ordnet das
Ereignis einem Fall zu (bzw. löst es), Tab A speichert danach eine
geänderte Notiz. Die Fallzuordnung gehört nicht zum Formular — die Eingabe
muss gespeichert werden und die Zuordnung aus Tab B erhalten bleiben. Eine
parallele Inhaltsänderung bleibt ein Konflikt; der JSON-Weg
(Offline-Replay) behält seinen strikten Versionsvertrag.
"""

import re
from html.parser import HTMLParser
from unittest.mock import patch

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from core.models import Event
from core.services.events import update_event


class _HiddenInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attr = dict(attrs)
        name = attr.get("name")
        if attr.get("type") == "hidden" and name and name != "csrfmiddlewaretoken":
            self.values[name] = attr.get("value") or ""


def _render(http, event):
    response = http.get(reverse("core:event_update", kwargs={"pk": event.pk}))
    assert response.status_code == 200
    parser = _HiddenInputs()
    parser.feed(response.content.decode())
    assert "expected_updated_at" in parser.values
    return parser.values


def _other_tab(user):
    http = HttpClient()
    http.force_login(user)
    return http


def _payload(hidden, **overrides):
    payload = dict(hidden)
    payload.update({"dauer": "15", "notiz": "Neue Notiz aus Tab A"})
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestEventEditSurvivesParallelCaseLink:
    @pytest.mark.parametrize("direction", ["assign", "remove"])
    def test_edit_is_saved_and_link_kept(self, client, staff_user, sample_event, case_open, direction):
        if direction == "remove":
            sample_event.case = case_open
            sample_event.save(update_fields=["case", "updated_at"])
        client.force_login(staff_user)
        hidden = _render(client, sample_event)
        assert hidden.get("expected_form_state"), "Formular muss den Stand des Ereignisses tragen"

        other = _other_tab(staff_user)
        if direction == "assign":
            response = other.post(
                reverse("core:case_assign_event", kwargs={"pk": case_open.pk}), {"event_id": str(sample_event.pk)}
            )
            expected_case = case_open.pk
        else:
            response = other.post(
                reverse("core:case_remove_event", kwargs={"pk": case_open.pk, "event_pk": sample_event.pk})
            )
            expected_case = None
        assert response.status_code in (200, 302)

        response = client.post(reverse("core:event_update", kwargs={"pk": sample_event.pk}), _payload(hidden))

        assert response.status_code == 302
        assert response["Location"] == reverse("core:event_detail", kwargs={"pk": sample_event.pk})
        fresh = Event.objects.get(pk=sample_event.pk)
        assert fresh.case_id == expected_case
        assert fresh.data_json["notiz"] == "Neue Notiz aus Tab A"

    def test_parallel_content_change_stays_conflict(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        hidden = _render(client, sample_event)
        update_event(Event.objects.get(pk=sample_event.pk), staff_user, {"dauer": 5, "notiz": "Aus Tab B"})

        response = client.post(reverse("core:event_update", kwargs={"pk": sample_event.pk}), _payload(hidden))

        assert response.status_code == 200
        assert response.context["has_edit_conflict"]
        assert response.context["data_form"]["notiz"].value() == "Neue Notiz aus Tab A"
        assert Event.objects.get(pk=sample_event.pk).data_json["notiz"] == "Aus Tab B"

    def test_json_replay_after_parallel_link_stays_409(self, client, staff_user, sample_event, case_open):
        client.force_login(staff_user)
        hidden = _render(client, sample_event)
        _other_tab(staff_user).post(
            reverse("core:case_assign_event", kwargs={"pk": case_open.pk}), {"event_id": str(sample_event.pk)}
        )

        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            _payload(hidden),
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 409
        assert response.json()["error"] == "conflict"
        assert Event.objects.get(pk=sample_event.pk).data_json["notiz"] == "Testnotiz"

    def test_invalid_post_rerenders_the_posted_form_state(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        hidden = _render(client, sample_event)

        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}), _payload(hidden, dauer="x")
        )

        assert response.status_code == 200
        parser = _HiddenInputs()
        parser.feed(response.content.decode())
        assert parser.values.get("expected_form_state") == hidden["expected_form_state"]

    def test_invalid_post_keeps_the_rendered_form_state_after_parallel_edit(self, client, staff_user, sample_event):
        """Tab B ändert die Notiz zwischen Laden und ungültigem POST aus Tab A.

        Die Neu-Anzeige muss den Fingerabdruck aus Tab A weitertragen — nicht
        einen aus dem inzwischen geänderten Datenbankstand neu berechneten.
        """
        from core.services.events.crud import event_form_state_token

        client.force_login(staff_user)
        hidden = _render(client, sample_event)
        update_event(Event.objects.get(pk=sample_event.pk), staff_user, {"dauer": 15, "notiz": "Aus Tab B"})
        current_state = event_form_state_token(Event.objects.get(pk=sample_event.pk))
        assert current_state != hidden["expected_form_state"]

        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}), _payload(hidden, dauer="x")
        )

        assert response.status_code == 200
        parser = _HiddenInputs()
        parser.feed(response.content.decode())
        assert parser.values.get("expected_form_state") == hidden["expected_form_state"]
        assert parser.values.get("expected_form_state") != current_state


_PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)

#: Zeilensperren im Mitschnitt: ``FOR UPDATE``/``FOR NO KEY UPDATE`` und die
#: FK-Vorsperren ``FOR KEY SHARE``/``FOR SHARE``. Die Tabelle steht im ersten ``FROM``.
_ROW_LOCK_RE = re.compile(r"\bFOR (?:NO KEY UPDATE|UPDATE|KEY SHARE|SHARE)\b", re.IGNORECASE)
_FROM_TABLE_RE = re.compile(r'FROM "(?P<table>core_\w+)"', re.IGNORECASE)


def _row_locks(queries):
    locks = []
    for query in queries:
        sql = query["sql"]
        if not _ROW_LOCK_RE.search(sql):
            continue
        match = _FROM_TABLE_RE.search(sql)
        locks.append((match.group("table") if match else "?", sql))
    return locks


@pytest.mark.django_db
class TestEditAfterMergeLocksFreshPersonFirst:
    """Ereignis mit Anhang bearbeiten, nachdem die Person zusammengeführt wurde.

    Tab A lädt das Ereignis noch an Person P. Bevor der Speichervorgang seine
    Transaktion öffnet, führt die Leitung P mit Q zusammen: das Ereignis hängt
    jetzt an Q, P liegt im Papierkorb. Tab A entfernt dabei einen Anhang — das
    zieht die Vorsperre der Anhangszeilen in denselben Vorgang.

    Kanonische Sperrfolge ist Person → Fall → Ereignis. Eine parallele
    Fallzuordnung hält Q und wartet auf das Ereignis; nimmt der
    Speichervorgang erst das Ereignis und danach Q, warten beide aufeinander.
    Deshalb muss die erste Zeilensperre die frisch gelesene Person Q treffen,
    danach das Ereignis, und die veraltete Person P aus dem Speicher darf gar
    nicht gesperrt werden.
    """

    def test_first_lock_is_fresh_person_then_event(self, client, staff_user, lead_user, facility):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from django.utils import timezone

        from core.models import Client, DocumentType, DocumentTypeField, FieldTemplate
        from core.services.client.merge import merge_clients
        from core.services.file_vault import store_encrypted_file
        from core.views import events as event_views

        source = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Merge-Quelle",
            created_by=staff_user,
        )
        target = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Merge-Ziel",
            created_by=staff_user,
        )
        doc_type = DocumentType.objects.create(
            facility=facility, name="Ereignis mit Anhang", entity_type=DocumentType.EntityType.EVENT
        )
        ft_file = FieldTemplate.objects.create(
            facility=facility, name="Anhang Merge", field_type=FieldTemplate.FieldType.FILE
        )
        ft_text = FieldTemplate.objects.create(
            facility=facility, name="Notiz Merge", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_file, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_text, sort_order=1)
        event = Event.objects.create(
            facility=facility,
            client=source,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        upload = SimpleUploadedFile("probe.pdf", _PDF_MINIMAL, content_type="application/pdf")
        with patch("core.services.file_vault.storage.run_virus_scan", lambda *a, **kw: None):
            attachment = store_encrypted_file(facility, upload, ft_file, event, staff_user)
        event.data_json = {ft_file.slug: {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}}
        event.save(update_fields=["data_json"])

        real_stage = event_views.stage_attachment_uploads
        merged_at: list[int] = []

        def stage_after_parallel_merge(*args, **kwargs):
            # Die View hält ihre Instanz mit ``client_id`` = P schon in der Hand.
            merge_clients(Client.objects.get(pk=source.pk), Client.objects.get(pk=target.pk), lead_user)
            merged_at.append(len(captured.captured_queries))
            return real_stage(*args, **kwargs)

        client.force_login(staff_user)
        with (
            patch.object(event_views, "stage_attachment_uploads", stage_after_parallel_merge),
            CaptureQueriesContext(connection) as captured,
        ):
            response = client.post(
                reverse("core:event_update", kwargs={"pk": event.pk}),
                {
                    "document_type": str(doc_type.pk),
                    "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                    ft_text.slug: "geaendert",
                    f"{ft_file.slug}__remove": str(attachment.entry_id),
                },
            )

        # Vorbedingungen: Zusammenführung lief zwischen Laden und Speichern,
        # und der Anhangs-Batch war nicht leer (die Vorsperre lief wirklich).
        assert merged_at, "Die Zusammenführung muss im Speichervorgang gelaufen sein"
        assert Event.objects.get(pk=event.pk).client_id == target.pk
        assert Client.objects.get(pk=source.pk).is_deleted
        assert response.status_code == 302, response.content
        attachment.refresh_from_db()
        assert attachment.deleted_at is not None, "Der Anhang muss entfernt worden sein"

        # Die Kennungen stehen im mitgeschnittenen SQL als UUID-Hex ohne Bindestriche.
        locks = _row_locks(captured.captured_queries[merged_at[0] :])
        tables = [table for table, _sql in locks]
        assert "core_attachment" in tables, tables
        assert locks[0][0] == "core_client", f"Erste Sperre muss die Person treffen, war: {tables}"
        assert target.pk.hex in locks[0][1], "Erste Sperre muss die frisch zugeordnete Person Q treffen"
        assert tables[1] == "core_event", f"Direkt danach das Ereignis, war: {tables}"
        stale = [sql for table, sql in locks if table == "core_client" and source.pk.hex in sql]
        assert not stale, f"Die veraltete Person P darf nicht gesperrt werden: {stale}"
        first_event = tables.index("core_event")
        late_person = [sql for table, sql in locks[first_event:] if table == "core_client" and target.pk.hex not in sql]
        assert not late_person, f"Hinter dem Ereignis darf keine weitere Person gesperrt werden: {late_person}"
