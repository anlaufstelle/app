"""Zentrale Datei-Uebersicht — Befunde B1-B3 + Owner-Generik (M24/AP4, Refs #1738).

Die Uebersicht (``core:attachment_list``) war bis M24 fest an das Ereignis
gebunden (Basis-Queryset ``event__facility=…``). Beim Umbau auf die
owner-generische Pipeline sind dabei drei Bestandsbefunde aufgefallen, die
hier je mit eigenem Failing-Test fixiert werden:

* **B1** — soft-geloeschte Anhaenge (``deleted_at``) erschienen weiter,
* **B2** — das Dokumentationstyp-Dropdown leakte HIGH-Typnamen an die Assistenz,
* **B3** — ein Nicht-UUID in ``?client=`` erzeugte HTTP 500.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Client, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.file_vault import store_encrypted_file

pytestmark = pytest.mark.django_db

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)

LIST_URL_NAME = "core:attachment_list"


def _title_hrefs(content: str, filename: str) -> list[str]:
    """``href``-Werte aller Links, deren Beschriftung den Dateinamen traegt.

    Trennt die TITEL-Links (Dateiname als Text, Desktop-Zeile + Mobil-Karte)
    von der dedizierten Download-Aktion — die traegt nur ein Icon und keinen
    Dateinamen.
    """
    return [
        match.group(1)
        for match in re.finditer(r'<a href="([^"]*)"[^>]*>(.*?)</a>', content, re.DOTALL)
        if filename in match.group(2)
    ]


def _file_template(facility, name, *, sensitivity=""):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.FILE,
        sensitivity=sensitivity,
    )


def _doc_type(facility, name, entity_type, *, sensitivity=DocumentType.Sensitivity.NORMAL, file_field=None):
    dt = DocumentType.objects.create(
        facility=facility,
        name=name,
        category=DocumentType.Category.ADMIN,
        entity_type=entity_type,
        sensitivity=sensitivity,
    )
    if file_field is not None:
        DocumentTypeField.objects.create(document_type=dt, field_template=file_field, sort_order=0)
    return dt


def _attach(facility, owner, ft, user, filename):
    upload = SimpleUploadedFile(filename, PDF_HEADER + filename.encode(), content_type="application/pdf")
    attachment = store_encrypted_file(facility, upload, ft, owner, user)
    owner.data_json = dict(owner.data_json or {})
    owner.data_json[ft.slug] = {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}
    owner.save(update_fields=["data_json"])
    return attachment


@pytest.fixture
def event_attachment(facility, staff_user, client_identified):
    ft = _file_template(facility, "Ereignis-Datei")
    dt = _doc_type(facility, "Ereignistyp mit Datei", DocumentType.EntityType.EVENT, file_field=ft)
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    return event, _attach(facility, event, ft, staff_user, "ereignis.pdf"), dt, ft


# ---------------------------------------------------------------------------
# B1 — soft-geloeschte Anhaenge
# ---------------------------------------------------------------------------


class TestSoftDeletedAttachmentsAreHidden:
    """B1 (Refs #1738): ``deleted_at`` blendet den Anhang ueberall aus — nur die
    Uebersicht hatte den Filter nicht.

    Der Download-View wies soft-geloeschte Anhaenge seit L5 (Refs #1375) mit 404
    ab; die Uebersicht listete sie weiter samt Dateiname und Download-Link. Wer
    einen Anhang entfernt hat, darf ihn dort nicht wiederfinden.
    """

    def test_soft_deleted_attachment_is_not_listed(self, client, staff_user, event_attachment):
        _event, attachment, _dt, _ft = event_attachment
        attachment.deleted_at = timezone.now()
        attachment.save(update_fields=["deleted_at"])
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME))
        assert response.status_code == 200
        assert "ereignis.pdf" not in response.content.decode()

    def test_active_attachment_is_still_listed(self, client, staff_user, event_attachment):
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME))
        assert "ereignis.pdf" in response.content.decode()


# ---------------------------------------------------------------------------
# B2 — Dropdown-Leak von HIGH-Typnamen
# ---------------------------------------------------------------------------


class TestDropdownSensitivityFilter:
    """B2 (Refs #1738, Security): das Filter-Dropdown listete ALLE aktiven
    Ereignistypen — auch solche, deren Sensitivitaet die Rolle nicht sehen darf.

    Der Typname ist selbst eine Information („Krisengespraech", „Suizidprotokoll"):
    die Zeilen waren korrekt gefiltert, der Name stand trotzdem im HTML.
    """

    def test_high_type_name_is_not_offered_to_assistant(self, client, assistant_user, facility):
        ft = _file_template(facility, "Geheimdatei")
        high = _doc_type(
            facility,
            "Hochsensibler Typ",
            DocumentType.EntityType.EVENT,
            sensitivity=DocumentType.Sensitivity.HIGH,
            file_field=ft,
        )
        client.force_login(assistant_user)
        response = client.get(reverse(LIST_URL_NAME))
        assert response.status_code == 200
        assert high.pk not in [dt.pk for dt in response.context["doc_types"]]
        assert "Hochsensibler Typ" not in response.content.decode()

    def test_normal_type_stays_offered(self, client, assistant_user, event_attachment):
        _event, _attachment, dt, _ft = event_attachment
        client.force_login(assistant_user)
        response = client.get(reverse(LIST_URL_NAME))
        assert dt.pk in [d.pk for d in response.context["doc_types"]]


# ---------------------------------------------------------------------------
# B3 — Nicht-UUID im Personen-Filter
# ---------------------------------------------------------------------------


class TestClientFilterGuard:
    """B3 (Refs #1738): ``?client=keine-uuid`` erzeugte HTTP 500.

    Das Eingabefeld ist ein freies Textfeld mit ``hx-trigger="keyup"`` — jeder
    Tastendruck vor der vollstaendigen UUID schickte einen Request. Postgres
    lehnt den Vergleich mit ``ValidationError`` ab, Django gab 500 zurueck.
    Fail-closed: unbrauchbarer Filter ⇒ leere Ergebnismenge.
    """

    def test_non_uuid_client_filter_returns_empty_result(self, client, staff_user, event_attachment):
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME), {"client": "keine-uuid"})
        assert response.status_code == 200
        assert response.context["attachments"] == []
        assert "ereignis.pdf" not in response.content.decode()

    def test_empty_client_filter_lists_everything(self, client, staff_user, event_attachment):
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME), {"client": ""})
        assert response.status_code == 200
        assert "ereignis.pdf" in response.content.decode()

    def test_matching_client_filter_still_works(self, client, staff_user, event_attachment, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME), {"client": str(client_identified.pk)})
        assert response.status_code == 200
        assert "ereignis.pdf" in response.content.decode()


# ---------------------------------------------------------------------------
# Owner-Generik
# ---------------------------------------------------------------------------


@pytest.fixture
def three_owner_attachments(facility, staff_user, client_identified):
    """Je ein Anhang an Ereignis, Person und Fall — derselben Person zugeordnet."""
    ft_event = _file_template(facility, "E-Datei")
    ft_client = _file_template(facility, "P-Datei")
    ft_case = _file_template(facility, "F-Datei")
    dt_event = _doc_type(facility, "E-Typ", DocumentType.EntityType.EVENT, file_field=ft_event)
    dt_client = _doc_type(facility, "P-Typ", DocumentType.EntityType.CLIENT, file_field=ft_client)
    dt_case = _doc_type(facility, "F-Typ", DocumentType.EntityType.CASE, file_field=ft_case)

    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=dt_event,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    client_identified.document_type = dt_client
    client_identified.save(update_fields=["document_type"])
    case = Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Owner-Fall",
        document_type=dt_case,
        created_by=staff_user,
    )
    return {
        "event": (event, _attach(facility, event, ft_event, staff_user, "aus_ereignis.pdf")),
        "client": (client_identified, _attach(facility, client_identified, ft_client, staff_user, "aus_person.pdf")),
        "case": (case, _attach(facility, case, ft_case, staff_user, "aus_fall.pdf")),
        "types": (dt_event, dt_client, dt_case),
    }


class TestOwnerGenericOverview:
    def test_all_three_owners_are_listed(self, client, staff_user, three_owner_attachments):
        client.force_login(staff_user)
        content = client.get(reverse(LIST_URL_NAME)).content.decode()
        assert "aus_ereignis.pdf" in content
        assert "aus_person.pdf" in content
        assert "aus_fall.pdf" in content

    def test_owner_column_labels_and_links(self, client, staff_user, three_owner_attachments):
        """Spalte „Zugeordnet zu": Owner-Typ + Link dorthin, wo die Rolle darf."""
        client.force_login(staff_user)
        rows = {item["original_filename"]: item for item in client.get(reverse(LIST_URL_NAME)).context["attachments"]}
        event, _ = three_owner_attachments["event"]
        person, _ = three_owner_attachments["client"]
        case, _ = three_owner_attachments["case"]
        assert rows["aus_ereignis.pdf"]["owner_url"] == reverse("core:event_detail", kwargs={"pk": event.pk})
        assert rows["aus_person.pdf"]["owner_url"] == reverse("core:client_detail", kwargs={"pk": person.pk})
        assert rows["aus_fall.pdf"]["owner_url"] == reverse("core:case_detail", kwargs={"pk": case.pk})
        assert {row["owner_type"] for row in rows.values()} == {"event", "client", "case"}

    def test_row_title_links_to_the_owner_record(self, client, staff_user, three_owner_attachments):
        """Der Zeilentitel fuehrt zum Datensatz, nicht ein zweites Mal zum Download.

        Integrations-Review (Refs #1738): Beim Owner-Umbau bekamen Desktop-Zeile
        und Mobil-Karte ``download_url`` als Titel-Link — die Zeile hatte damit
        zwei Links auf dieselbe Datei und keinen Weg mehr zum Kontext (Person,
        Fall, Ereignis). Ein Klick auf den Dateinamen entschluesselte die Datei,
        statt den Datensatz zu oeffnen.
        """
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME))
        content = response.content.decode()
        rows = {item["original_filename"]: item for item in response.context["attachments"]}

        for filename, row in rows.items():
            hrefs = _title_hrefs(content, filename)
            assert hrefs, f"Kein Titel-Link fuer {filename} gefunden"
            assert set(hrefs) == {row["owner_url"]}, f"{filename}: Titel zeigt auf {set(hrefs)}"
            # Der dedizierte Download-Link bleibt bestehen.
            assert row["download_url"] in content

    def test_row_title_without_owner_url_renders_as_plain_text(self):
        """Fallback: ohne ``owner_url`` bleibt der Titel unverlinkt (kein toter Link)."""
        from django.template.loader import render_to_string

        html = render_to_string(
            "core/attachments/partials/attachment_table.html",
            {
                "attachments": [
                    {
                        "attachment": SimpleNamespace(mime_type="application/pdf", created_at=timezone.now()),
                        "owner_type": "case",
                        "owner_label": "Fall",
                        "owner_url": "",
                        "original_filename": "ohne_ziel.pdf",
                        "file_size_display": "1 KB",
                        "doc_type_name": "F-Typ",
                        "client_pseudonym": "—",
                        "download_url": "/attachments/x/download/",
                    }
                ]
            },
        )
        assert "ohne_ziel.pdf" in html
        assert _title_hrefs(html, "ohne_ziel.pdf") == []

    def test_download_links_point_to_the_owner_route(self, client, staff_user, three_owner_attachments):
        client.force_login(staff_user)
        rows = {item["original_filename"]: item for item in client.get(reverse(LIST_URL_NAME)).context["attachments"]}
        person, person_attachment = three_owner_attachments["client"]
        assert rows["aus_person.pdf"]["download_url"] == reverse(
            "core:client_attachment_download",
            kwargs={"pk": person.pk, "attachment_pk": person_attachment.pk},
        )

    def test_client_filter_matches_all_three_owner_sources(self, client, staff_user, three_owner_attachments):
        person, _ = three_owner_attachments["client"]
        client.force_login(staff_user)
        content = client.get(reverse(LIST_URL_NAME), {"client": str(person.pk)}).content.decode()
        assert "aus_ereignis.pdf" in content
        assert "aus_person.pdf" in content
        assert "aus_fall.pdf" in content

    def test_doc_type_filter_matches_all_three_owner_sources(self, client, staff_user, three_owner_attachments):
        _dt_event, dt_client, _dt_case = three_owner_attachments["types"]
        client.force_login(staff_user)
        content = client.get(reverse(LIST_URL_NAME), {"document_type": str(dt_client.pk)}).content.decode()
        assert "aus_person.pdf" in content
        assert "aus_ereignis.pdf" not in content
        assert "aus_fall.pdf" not in content

    def test_assistant_never_sees_case_owned_rows(self, client, assistant_user, three_owner_attachments):
        """Rollen-Gefaelle (ADR-039 §12): Faelle sind fuer die Assistenz
        unsichtbar — auch ihre Dateien und ihre Typnamen."""
        _dt_event, _dt_client, dt_case = three_owner_attachments["types"]
        client.force_login(assistant_user)
        response = client.get(reverse(LIST_URL_NAME))
        content = response.content.decode()
        assert "aus_fall.pdf" not in content
        assert "aus_ereignis.pdf" in content
        assert "aus_person.pdf" in content
        assert dt_case.pk not in [dt.pk for dt in response.context["doc_types"]]

    def test_trashed_client_owner_is_hidden(self, client, staff_user, three_owner_attachments):
        person, _ = three_owner_attachments["client"]
        person.is_deleted = True
        person.deleted_at = timezone.now()
        person.save(update_fields=["is_deleted", "deleted_at"])
        client.force_login(staff_user)
        content = client.get(reverse(LIST_URL_NAME)).content.decode()
        assert "aus_person.pdf" not in content

    def test_trashed_event_owner_is_hidden(self, client, staff_user, three_owner_attachments):
        """Der Papierkorb-Filter gilt fuer ALLE drei Owner-Arten.

        Integrations-Review (Refs #1738): gepinnt war nur der Personen-Zweig.
        ``Event`` traegt ein eigenes ``is_deleted`` (kein
        ``SoftDeletableModel``) — faellt seine Klausel weg, taucht eine aus dem
        Zeitstrom entfernte Datei hier wieder auf, samt Download-Link.
        """
        event, _ = three_owner_attachments["event"]
        event.is_deleted = True
        event.save(update_fields=["is_deleted"])
        client.force_login(staff_user)
        content = client.get(reverse(LIST_URL_NAME)).content.decode()
        assert "aus_ereignis.pdf" not in content
        assert "aus_person.pdf" in content
        assert "aus_fall.pdf" in content

    def test_trashed_case_owner_is_hidden(self, client, staff_user, three_owner_attachments):
        """Spiegel fuer den Fall-Zweig (``Case`` erbt ``is_deleted``)."""
        case, _ = three_owner_attachments["case"]
        case.is_deleted = True
        case.deleted_at = timezone.now()
        case.save(update_fields=["is_deleted", "deleted_at"])
        client.force_login(staff_user)
        content = client.get(reverse(LIST_URL_NAME)).content.decode()
        assert "aus_fall.pdf" not in content
        assert "aus_ereignis.pdf" in content
        assert "aus_person.pdf" in content

    def test_owner_without_document_type_is_hidden_fail_closed(self, client, staff_user, facility, client_identified):
        """Ohne ``document_type`` gibt es keine Bezugs-Sensitivitaet — die Zeile
        bleibt fuer JEDE Rolle unsichtbar (fail-closed), statt still als NORMAL
        durchzurutschen."""
        ft = _file_template(facility, "Typlos-Datei")
        person = Client.objects.create(
            facility=facility,
            pseudonym="Typlos",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )
        _attach(facility, person, ft, staff_user, "typlos.pdf")
        client.force_login(staff_user)
        assert "typlos.pdf" not in client.get(reverse(LIST_URL_NAME)).content.decode()

    def test_field_sensitivity_still_filters(self, client, staff_user, facility, client_identified):
        ft_high = _file_template(facility, "Feld-HIGH", sensitivity=DocumentType.Sensitivity.HIGH)
        dt = _doc_type(facility, "P-Typ-Feldsens", DocumentType.EntityType.CLIENT, file_field=ft_high)
        client_identified.document_type = dt
        client_identified.save(update_fields=["document_type"])
        _attach(facility, client_identified, ft_high, staff_user, "feld_high.pdf")
        client.force_login(staff_user)
        assert "feld_high.pdf" not in client.get(reverse(LIST_URL_NAME)).content.decode()

    def test_dropdown_only_offers_types_with_a_file_field(self, client, staff_user, three_owner_attachments, facility):
        """E21-Revision (Refs #1738): das Dropdown ist ein DATEI-Filter — Typen
        ohne FILE-Feld koennen dort nie ein Ergebnis liefern."""
        without_file = _doc_type(facility, "Ohne Datei", DocumentType.EntityType.EVENT)
        dt_event, dt_client, dt_case = three_owner_attachments["types"]
        client.force_login(staff_user)
        offered = [dt.pk for dt in client.get(reverse(LIST_URL_NAME)).context["doc_types"]]
        assert without_file.pk not in offered
        assert {dt_event.pk, dt_client.pk, dt_case.pk} <= set(offered)

    def test_cross_facility_attachment_is_never_listed(self, client, staff_user, foreign_client_attachment):
        client.force_login(staff_user)
        assert "authz.pdf" not in client.get(reverse(LIST_URL_NAME)).content.decode()
