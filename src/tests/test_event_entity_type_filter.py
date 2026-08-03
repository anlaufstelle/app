"""P2 (Refs #1058): Der gesamte Event-Pfad bietet/akzeptiert nur noch
``DocumentType.entity_type=event``.

P1 hat ``DocumentType.entity_type`` eingefuehrt (Default ``event``, Werte
``event``/``client``/``case``). Ohne Filter im Event-Pfad wuerden kuenftige
client-/case-Dokumenttypen in Event-Dropdowns auftauchen bzw. sich per
manipuliertem POST/GET als Event-Dokumenttyp unterschieben lassen. Diese
Tests pinnen die Filterung an jeder betroffenen Stelle — inline angelegte
``entity_type=client``-Fixtures statt einer geteilten conftest-Fixture, wie
im P2-Brief vorgegeben.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.forms.events import EventMetaForm
from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, Settings
from core.services.events.context import resolve_default_document_type
from core.services.events.tally import tally_document_types


@pytest.fixture
def doc_type_client(facility):
    """entity_type=client Dokumenttyp — darf NIRGENDS im Event-Pfad sichtbar/waehlbar sein."""
    return DocumentType.objects.create(
        facility=facility,
        name="Personendaten",
        entity_type=DocumentType.EntityType.CLIENT,
    )


@pytest.mark.django_db
class TestEventMetaFormEntityTypeFilter:
    """Brief-Punkt 1: ``EventMetaForm.document_type``-Queryset."""

    def test_client_type_excluded_from_queryset(self, facility, doc_type_client, doc_type_contact):
        form = EventMetaForm(facility=facility)
        pks = list(form.fields["document_type"].queryset.values_list("pk", flat=True))
        assert doc_type_client.pk not in pks
        assert doc_type_contact.pk in pks


@pytest.mark.django_db
class TestZeitstromDropdownEntityTypeFilter:
    """Brief-Punkt 3: Zeitstrom-Filter-Dropdown."""

    def test_client_type_excluded(self, client, staff_user, doc_type_client, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        pks = [dt.pk for dt in response.context["document_types"]]
        assert doc_type_client.pk not in pks
        assert doc_type_contact.pk in pks


@pytest.mark.django_db
class TestStatisticsDropdownEntityTypeFilter:
    """Brief-Punkt 4: Statistik-Filter-Dropdown."""

    def test_client_type_excluded(self, client, lead_user, doc_type_client, doc_type_contact):
        client.force_login(lead_user)
        response = client.get(reverse("core:statistics"))
        assert response.status_code == 200
        pks = [dt.pk for dt in response.context["document_types"]]
        assert doc_type_client.pk not in pks
        assert doc_type_contact.pk in pks


@pytest.mark.django_db
class TestAttachmentsDropdownEntityTypeFilter:
    """Brief-Punkt 5: Attachments-Filter-Dropdown."""

    def test_client_type_excluded(self, client, staff_user, doc_type_client, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(reverse("core:attachment_list"))
        assert response.status_code == 200
        pks = [dt.pk for dt in response.context["doc_types"]]
        assert doc_type_client.pk not in pks
        assert doc_type_contact.pk in pks


@pytest.mark.django_db
class TestEventFieldsPartialEntityTypeFilter:
    """Brief-Punkt 2 (EventFieldsPartialView): client-Typ ⇒ 404."""

    def test_client_type_404(self, client, staff_user, doc_type_client):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:event_fields_partial"),
            {"document_type": str(doc_type_client.pk)},
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestEventCreatePostReRenderEntityTypeFilter:
    """Brief-Punkt 2 (#774-Guard): client-Typ im invalid-meta-Re-Render-Pfad
    darf weder Feldlabels leaken noch ein Event anlegen.

    Muster identisch zu
    ``test_events_crud.TestEventCreate.test_invalid_meta_post_does_not_leak_high_field_labels_to_assistant``,
    nur mit einem entity_type=client- statt einem HIGH-Dokumenttyp.
    """

    def test_client_type_id_fields_not_rendered(self, client, staff_user, facility):
        unique_label = "Klienten-Feld-RF1058"
        client_dt = DocumentType.objects.create(
            facility=facility,
            name="Personendaten",
            entity_type=DocumentType.EntityType.CLIENT,
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name=unique_label,
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=client_dt, field_template=ft, sort_order=0)

        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(client_dt.pk),
                # occurred_at fehlt bewusst -> meta_form invalid -> Re-Render-Branch
            },
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert unique_label not in body, (
            "client-Typ darf im invalid-meta-Re-Render-Pfad keine Feldlabels leaken (Refs #774-Muster)."
        )
        assert not Event.objects.filter(document_type=client_dt).exists()


@pytest.mark.django_db
class TestResolveDefaultDocumentTypeEntityTypeFilter:
    """Brief-Punkt 6: ``resolve_default_document_type`` ignoriert einen client-Default."""

    def test_client_default_ignored(self, facility, doc_type_client):
        Settings.objects.create(facility=facility, default_document_type=doc_type_client)
        doc_type, initial = resolve_default_document_type(facility)
        assert doc_type is None
        assert initial == {}


# ---------------------------------------------------------------------------
# Erweiterung ueber die explizit im Brief gelistete Stellen hinaus.
#
# Beim Abschluss-Grep ueber ``DocumentType.objects``/facility-gescopte
# Einzel-Lookups im Event-Pfad fielen zwei weitere Stellen auf, die vom
# Brief nicht namentlich genannt, aber Teil des "GESAMTEN Event-Pfads" sind
# (Begruendung je Testklasse). Siehe Report fuer die Einordnung.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerienerfassungPrefillEntityTypeFilter:
    """``EventCreateView.get`` — ``?document_type=``-Prefill (Refs #1349).

    Nicht im Brief namentlich genannt, aber derselbe IDOR-/Leak-Schutz wie
    der #774-Guard: ohne Filter wuerde ``default_doc_type`` auf den
    client-Typ zeigen und dessen Felder im ``DynamicEventDataForm``
    vorrendern. Bestehendes Verhalten bei Fremd-Facility/unbekannt ist
    bereits 404 — client-Typ reiht sich dort ein.
    """

    def test_client_type_prefill_404(self, client, staff_user, doc_type_client):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"), {"document_type": str(doc_type_client.pk)})
        assert response.status_code == 404


@pytest.mark.django_db
class TestTallyDocumentTypesEntityTypeFilter:
    """``services.events.tally.tally_document_types`` — Strichlisten-Panel auf der Zeitstrom-Seite.

    Nicht im Brief namentlich genannt. Die Funktion baut das Dropdown/Panel
    der "+1"-Strichliste (direkt neben dem in Brief-Punkt 3 gefixten
    Zeitstrom-Filter-Dropdown, selbe View) und wird von ``create_event()``
    fuer Events verwendet — faellt unter "GESAMTER Event-Pfad".
    """

    def test_client_type_excluded(self, facility, staff_user, doc_type_client, doc_type_contact):
        qs = tally_document_types(facility, staff_user)
        pks = list(qs.values_list("pk", flat=True))
        assert doc_type_client.pk not in pks
        assert doc_type_contact.pk in pks


@pytest.mark.django_db
class TestTallyIncrementEntityTypeFilter:
    """``TallyIncrementView.post`` — Schreibpfad-Guard, Analogon zu ``EventFieldsPartialView``.

    Defense-in-Depth: selbst wenn ``tally_document_types`` das Dropdown
    filtert, darf ein manipulierter POST keinen Event mit client-Typ
    anlegen koennen.
    """

    def test_client_type_404(self, client, staff_user, doc_type_client):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:tally_increment"),
            {"document_type": str(doc_type_client.pk)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 404
        assert not Event.objects.filter(document_type=doc_type_client).exists()
