"""Serverseitige Ablehnung von Schreibzugriffen auf widerrufene Personen (Refs #1771).

Eine Person kennt **zwei** Widerrufs-Zustaende, und nur einer davon war bisher
serverseitig durchgaengig gegated:

* ``is_deleted=True`` — Papierkorb. Seit #1737 an allen Personen-Pfaden dicht.
* ``is_active=False`` — **anonymisiert** (Klartext redigiert). Dieser Zustand
  tritt ausdruecklich auch OHNE Papierkorb auf: die K-Anon-Bridge
  (:func:`core.retention.anonymization.anonymize_clients`) anonymisiert jede
  Person, deren Ereignisse alle soft-geloescht sind — ``is_deleted`` bleibt
  dabei ``False``. Genau diese Zeilen passierten jedes ``is_deleted=False``-Gate.

Folge (der Befund aus #1771): ein Offline-Replay oder ein stales offenes
Formular schrieb frischen Klartext an einen bereits anonymisierten Datensatz —
die Loeschung lief rueckwaerts. Seit M24 (#1738) kann derselbe Schreibzugriff
zusaetzlich einen Datei-Upload tragen (``Attachment``-Zeile + ``.enc`` auf
Disk), nicht nur Feldwerte.

Der Widerrufs-Kanal aus #1760 ist bewusst *best effort* (fail-open bei
Netzfehler/429, dokumentiert in ``docs/security-notes.md``). Diese Tests pinnen
die robuste zweite Linie: der Server nimmt den Schreibzugriff gar nicht erst an,
und der Offline-Replay landet ueber den bestehenden 422-Contract als
**Dead-Letter** (``offline-queue.js``: 422 -> ``localStatus: "dead"``), nicht in
einer Retry-Schleife.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from core.forms.cases import CaseForm
from core.forms.events import EventMetaForm
from core.forms.workitems import WorkItemForm
from core.models import Attachment, Case, Client, DocumentType, DocumentTypeField, Event, FieldTemplate, WorkItem
from tests._form_helpers import assert_field_error, assert_form_valid

pytestmark = pytest.mark.django_db


PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


@pytest.fixture
def client_anonymized(client_identified):
    """Anonymisiert, aber NICHT im Papierkorb — der K-Anon-Bridge-Zustand.

    Bewusst nur die beiden Marker der Redaktion (``is_active``/``pseudonym``)
    statt der vollen Kaskade: der Test prueft das Gate, nicht die
    Anonymisierung.
    """
    client_identified.is_active = False
    client_identified.pseudonym = f"Gelöscht-{str(client_identified.pk)[:8]}"
    client_identified.save(update_fields=["is_active", "pseudonym"])
    return client_identified


def _file_doc_type(facility, entity_type, name, field_name):
    doc_type = DocumentType.objects.create(
        facility=facility,
        name=name,
        category=DocumentType.Category.ADMIN,
        entity_type=entity_type,
    )
    ft_file = FieldTemplate.objects.create(facility=facility, name=field_name, field_type=FieldTemplate.FieldType.FILE)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_file, sort_order=0)
    return doc_type, ft_file


def _enc_files(media_root) -> list:
    return sorted(Path(media_root).rglob("*.enc"))


class TestEventMetaFormRejectsRevokedClient:
    """``EventMetaForm.clean_client`` — der Befund-Pfad aus #1771."""

    def test_anonymized_client_is_rejected(self, facility, staff_user, doc_type_contact, client_anonymized):
        form = EventMetaForm(
            data={
                "document_type": str(doc_type_contact.pk),
                "client": str(client_anonymized.pk),
                "occurred_at": "2026-05-16T10:00",
            },
            facility=facility,
            user=staff_user,
        )
        assert_field_error(form, "client", "gelöscht oder anonymisiert")

    def test_trashed_client_is_rejected(self, facility, staff_user, doc_type_contact, client_trashed):
        form = EventMetaForm(
            data={
                "document_type": str(doc_type_contact.pk),
                "client": str(client_trashed.pk),
                "occurred_at": "2026-05-16T10:00",
            },
            facility=facility,
            user=staff_user,
        )
        assert_field_error(form, "client", "gelöscht oder anonymisiert")

    def test_live_client_still_resolves(self, facility, staff_user, doc_type_contact, client_identified):
        form = EventMetaForm(
            data={
                "document_type": str(doc_type_contact.pk),
                "client": str(client_identified.pk),
                "occurred_at": "2026-05-16T10:00",
            },
            facility=facility,
            user=staff_user,
        )
        assert_form_valid(form)
        assert form.cleaned_data["client"] == client_identified


class TestCaseFormRejectsRevokedClient:
    def test_anonymized_client_is_rejected(self, facility, client_anonymized):
        form = CaseForm(
            data={"client": str(client_anonymized.pk), "title": "Neuer Fall", "description": ""},
            facility=facility,
        )
        assert_field_error(form, "client", "gelöscht oder anonymisiert")

    def test_trashed_client_is_rejected(self, facility, client_trashed):
        form = CaseForm(
            data={"client": str(client_trashed.pk), "title": "Neuer Fall", "description": ""},
            facility=facility,
        )
        assert_field_error(form, "client", "gelöscht oder anonymisiert")

    def test_live_client_still_resolves(self, facility, client_identified):
        form = CaseForm(
            data={"client": str(client_identified.pk), "title": "Neuer Fall", "description": ""},
            facility=facility,
        )
        assert_form_valid(form)
        assert form.cleaned_data["client"] == client_identified


class TestWorkItemFormRejectsRevokedClient:
    def test_anonymized_client_is_rejected(self, facility, staff_user, client_anonymized):
        form = WorkItemForm(
            data={
                "item_type": WorkItem.ItemType.TASK,
                "title": "Aufgabe",
                "priority": WorkItem.Priority.NORMAL,
                "client": str(client_anonymized.pk),
            },
            facility=facility,
        )
        assert_field_error(form, "client", "gelöscht oder anonymisiert")

    def test_trashed_client_is_rejected(self, facility, staff_user, client_trashed):
        form = WorkItemForm(
            data={
                "item_type": WorkItem.ItemType.TASK,
                "title": "Aufgabe",
                "priority": WorkItem.Priority.NORMAL,
                "client": str(client_trashed.pk),
            },
            facility=facility,
        )
        assert_field_error(form, "client", "gelöscht oder anonymisiert")

    def test_live_client_still_resolves(self, facility, client_identified):
        form = WorkItemForm(
            data={
                "item_type": WorkItem.ItemType.TASK,
                "title": "Aufgabe",
                "priority": WorkItem.Priority.NORMAL,
                "client": str(client_identified.pk),
            },
            facility=facility,
        )
        assert_form_valid(form)
        assert form.cleaned_data["client"] == client_identified


class TestOfflineReplayEndsAsDeadLetter:
    """Der Replay-Contract: 422 = ``dead`` in ``offline-queue.js``, kein Retry.

    ``offline-queue.js`` klassifiziert die Antwort **ausschliesslich ueber den
    Status**: 409 -> ``conflict`` (user-retry-bar), 429/5xx -> Backoff-Retry,
    422/400 -> ``_markQueueDead(record, "invalid")``. Ein abgelehnter
    Schreibzugriff auf eine widerrufene Person muss deshalb im 422-Zweig landen
    — und zwar reproduzierbar, weil ein Retry denselben Server-Zustand
    vorfindet und niemals gruen werden kann.
    """

    def _replay(self, client, doc_type, person, *, notiz="Offline-Notiz"):
        return client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type.pk),
                "client": str(person.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "15",
                "notiz": notiz,
            },
            HTTP_ACCEPT="application/json",
        )

    def test_replay_on_anonymized_client_returns_422_and_writes_nothing(
        self, client, staff_user, doc_type_contact, client_anonymized
    ):
        client.force_login(staff_user)
        response = self._replay(client, doc_type_contact, client_anonymized)

        assert response.status_code == 422
        payload = response.json()
        assert payload["error"] == "invalid"
        assert "client" in payload["errors"]
        # Kein Klartext-Write an den anonymisierten Datensatz.
        assert not Event.objects.filter(client=client_anonymized).exists()
        assert not Event.objects.filter(created_by=staff_user).exists()

    def test_replay_on_trashed_client_returns_422(self, client, staff_user, doc_type_contact, client_trashed):
        client.force_login(staff_user)
        response = self._replay(client, doc_type_contact, client_trashed)

        assert response.status_code == 422
        assert response.json()["error"] == "invalid"
        assert not Event.objects.filter(client=client_trashed).exists()

    def test_replay_is_terminal_not_retryable(self, client, staff_user, doc_type_contact, client_anonymized):
        """Wiederholung liefert exakt dieselbe endgueltige Antwort.

        Der Gegenbeweis zur Retry-Schleife: waere die Ablehnung ein 409
        (``conflict``) oder 429/5xx (Backoff), spielte die Queue den Eintrag
        erneut aus. 422 in JEDEM Versuch heisst: einmal ``dead``, nie wieder
        angefasst — und in keinem Versuch entsteht ein Ereignis.
        """
        client.force_login(staff_user)
        stati = [self._replay(client, doc_type_contact, client_anonymized).status_code for _ in range(3)]

        assert stati == [422, 422, 422]
        assert not Event.objects.exists()

    def test_workitem_replay_on_anonymized_client_returns_422(self, client, staff_user, client_anonymized):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": WorkItem.ItemType.TASK,
                "title": "Offline-Aufgabe mit Klartext",
                "priority": WorkItem.Priority.NORMAL,
                "client": str(client_anonymized.pk),
            },
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid"
        assert not WorkItem.objects.filter(client=client_anonymized).exists()

    def test_live_client_replay_still_succeeds(self, client, staff_user, doc_type_contact, client_identified):
        """Regressionswaechter: der gute Fall bleibt ein 302-Erfolg."""
        client.force_login(staff_user)
        response = self._replay(client, doc_type_contact, client_identified)

        assert response.status_code == 302
        assert Event.objects.filter(client=client_identified).exists()


class TestClientUpdateViewRejectsAnonymized:
    """``/clients/<pk>/edit/`` — seit #1737 papierkorb-dicht, aber nicht anonymisierungs-dicht."""

    def test_get_is_404(self, client, staff_user, client_anonymized):
        client.force_login(staff_user)
        assert client.get(reverse("core:client_update", args=[client_anonymized.pk])).status_code == 404

    def test_post_is_404_and_keeps_redacted_state(self, client, staff_user, client_anonymized):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", args=[client_anonymized.pk]),
            {
                "pseudonym": "Wieder-Klartext",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "frischer Klartext an anonymisierter Person",
            },
        )
        assert response.status_code == 404
        client_anonymized.refresh_from_db()
        assert client_anonymized.pseudonym != "Wieder-Klartext"
        assert client_anonymized.notes == ""
        assert client_anonymized.is_active is False

    def test_multipart_post_creates_no_attachment_and_no_enc_file(
        self, client, staff_user, facility, client_anonymized, settings, tmp_path
    ):
        """M24-Kopplung (#1738): der Schreibzugriff traegt einen Datei-Upload.

        Ohne Gate entstuende hier eine ``Attachment``-Zeile samt
        verschluesselter ``.enc`` an einer bereits anonymisierten Person — ein
        Datenbestand, den kein Loeschpfad je wieder erfasst.
        """
        settings.MEDIA_ROOT = str(tmp_path)
        doc_type, ft_file = _file_doc_type(
            facility, DocumentType.EntityType.CLIENT, "Personen-Upload", "Unterlagen (Gate)"
        )
        before = _enc_files(tmp_path)

        client.force_login(staff_user)
        with Path(tmp_path / "upload.pdf").open("wb") as fh:
            fh.write(PDF_MINIMAL)
        with Path(tmp_path / "upload.pdf").open("rb") as upload:
            response = client.post(
                reverse("core:client_update", args=[client_anonymized.pk]),
                {
                    "pseudonym": "Gelöscht-x",
                    "contact_stage": Client.ContactStage.IDENTIFIED,
                    "age_cluster": Client.AgeCluster.UNKNOWN,
                    "notes": "",
                    "document_type": str(doc_type.pk),
                    ft_file.slug: upload,
                },
            )

        assert response.status_code == 404
        assert not Attachment.objects.exists()
        assert _enc_files(tmp_path) == before

    def test_live_client_is_still_editable(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        assert client.get(reverse("core:client_update", args=[client_identified.pk])).status_code == 200


class TestCaseCreateRejectsRevokedClient:
    """Fall-Anlage samt Attachment-Staging (M24) auf eine widerrufene Person."""

    def test_case_create_on_anonymized_client_stages_no_file(
        self, client, staff_user, facility, client_anonymized, settings, tmp_path
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        doc_type, ft_file = _file_doc_type(facility, DocumentType.EntityType.CASE, "Fall-Upload", "Belege (Gate)")
        before = _enc_files(tmp_path)

        client.force_login(staff_user)
        with Path(tmp_path / "case.pdf").open("wb") as fh:
            fh.write(PDF_MINIMAL)
        with Path(tmp_path / "case.pdf").open("rb") as upload:
            response = client.post(
                reverse("core:case_create"),
                {
                    "client": str(client_anonymized.pk),
                    "title": "Fall an anonymisierter Person",
                    "description": "",
                    "lead_user": "",
                    "document_type": str(doc_type.pk),
                    ft_file.slug: upload,
                },
            )

        # Formular-Re-Render (HTML-Pfad), kein Fall, kein Anhang, keine ``.enc``.
        assert response.status_code == 200
        assert not Case.objects.filter(title="Fall an anonymisierter Person").exists()
        assert not Attachment.objects.exists()
        assert _enc_files(tmp_path) == before
