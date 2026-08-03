"""Tests für P9 — ``entity_type`` in der Config-UI (M2, Refs #1058).

Deckt:

1. ``entity_type`` als First-Class-Formularfeld in der DocumentType-Config-UI
   — nur beim Anlegen wählbar, serverseitig unveränderbar bei Bearbeitung
   (Form siebt aus + Model-Guard als zweite Verteidigungslinie, Muster
   ``system_type``).
2. Den gehärteten Löschschutz — Client-/Case-Referenzen zählen zusätzlich zu
   Events (Muster ``field_template_delete_blockers``: alle zutreffenden
   Gründe, nicht nur der erste).
3. Entscheid E8, Schicht 2: FILE-Feldvorlagen sind nur Ereignis-Typen
   zuordenbar (Schicht 1 = Paketformat, Schicht 3 = stille Formular-
   Auslassung — beide Bestand).
4. Den ``entity_type``-Badge in Liste/Detail.
5. (Fix-Runde 1, V-1) Die Live-Vorschau wählt jetzt die passende
   ``*_fields_partial``-URL nach ``entity_type`` statt hart auf
   ``event_fields_partial`` zu zeigen (vormals dokumentierte 404-Lücke,
   ``TestLivePreviewEntityTypeGapDocumented`` — durch
   ``TestLivePreviewEntityTypeAware`` ersetzt).
6. (Fix-Runde 1, V-2) Die Delete-Confirm-Seite zeigt jetzt Client-/Case-
   Zähler + Warnbox + deaktivierten Löschen-Button, analog ``event_count``.

Nutzt ausschließlich Bestandsfixtures (``doc_type_contact``/
``doc_type_client_profile``/``doc_type_case_form``/``client_identified``/
``case_open``, alle aus ``conftest.py``). Die Datei-Scope-Beschränkung aus der
ersten P9-Runde (``core/urls.py``/``_authz_expectations.py``/``conftest.py``
= P8-Hoheitsgebiet) ist für diese Fix-Runde vom Controller aufgehoben (P8 ist
fertig, berührt ``views/config``/Config-Templates nicht) — die neuen Tests
nutzen zusätzlich ``core:client_fields_partial``/``core:case_fields_partial``
(P7/P8, jetzt via ``git merge m2-p8`` im Worktree verfügbar) sowie
``client_count``/``case_count`` im View-Context von
``ConfigDocumentTypeDeleteView``.
"""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.forms.config import DocumentTypeForm
from core.models import Case, Client, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.config import ConfigDeleteBlocked, add_field_to_doctype, delete_document_type


@pytest.fixture
def req(staff_user, facility):
    """Request mit authentifiziertem User + Facility-Scope (Muster test_config_service.py)."""
    request = RequestFactory().post("/")
    request.user = staff_user
    request.current_facility = facility
    return request


def _valid_post(**overrides):
    data = {
        "name": "Neuer Typ",
        "category": DocumentType.Category.CONTACT,
        "sensitivity": DocumentType.Sensitivity.NORMAL,
        "is_active": "on",
    }
    data.update(overrides)
    return data


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------------------
# Create — entity_type wählbar, Default bleibt EVENT (Rückwärtskompatibilität)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateEntityType:
    def test_create_form_offers_entity_type(self, facility):
        # Regression (Muster test_create_form_offers_system_type): UUID-Default-PK
        # macht instance.pk auch VOR dem ersten Speichern truthy — ein
        # pk-basierter disabled-Guard würde entity_type dann fälschlich schon
        # beim Anlegen sperren (richtig: _state.adding).
        form = DocumentTypeForm(facility=facility)
        assert form.fields["entity_type"].disabled is False

    def test_create_with_entity_type_client_persists(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_create"),
            _valid_post(name="Personentyp", entity_type=DocumentType.EntityType.CLIENT),
        )
        assert response.status_code == 302
        obj = DocumentType.objects.get(name="Personentyp")
        assert obj.entity_type == DocumentType.EntityType.CLIENT

    def test_create_with_entity_type_case_persists(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_create"),
            _valid_post(name="Falltyp", entity_type=DocumentType.EntityType.CASE),
        )
        assert response.status_code == 302
        obj = DocumentType.objects.get(name="Falltyp")
        assert obj.entity_type == DocumentType.EntityType.CASE

    def test_create_without_entity_type_defaults_to_event(self, client, admin_user):
        # Rückwärtskompatibilität (Brief-Kontext): vor P9 entstanden Typen über
        # die Config-UI immer als event. Ein POST ohne den entity_type-Schlüssel
        # (wie jeder Alt-Client/Bestandstest, z.B. test_config_document_types.py)
        # muss weiterhin klaglos "event" ergeben statt an einem neuen
        # Pflichtfeld-Fehler zu scheitern.
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_create"), _valid_post(name="Ohne Auswahl"))
        assert response.status_code == 302
        obj = DocumentType.objects.get(name="Ohne Auswahl")
        assert obj.entity_type == DocumentType.EntityType.EVENT


# ---------------------------------------------------------------------------
# Edit — entity_type unveränderbar (Form siebt aus, Model-Guard als Backstop)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEditEntityTypeImmutable:
    def test_entity_type_field_disabled_on_edit(self, doc_type_client_profile, facility):
        form = DocumentTypeForm(instance=doc_type_client_profile, facility=facility)
        assert form.fields["entity_type"].disabled is True

    def test_entity_type_immutable_on_edit_post_no_500(self, client, admin_user, doc_type_client_profile):
        # Manipulierter POST (entity_type=event auf einem client-Typ): das
        # disabled-Formfeld ignoriert den POST-Wert (Django-Semantik, Muster
        # system_type) — der Wert bleibt unverändert, kein 500 (Model-Guard in
        # DocumentType.save() würde bei einer tatsächlichen Änderung sonst eine
        # ValidationError werfen, die aus Model.save() heraus NICHT abgefangen wird).
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_update", kwargs={"pk": doc_type_client_profile.pk}),
            _valid_post(
                name=doc_type_client_profile.name,
                category=doc_type_client_profile.category,
                entity_type=DocumentType.EntityType.EVENT,
            ),
        )
        assert response.status_code == 302
        doc_type_client_profile.refresh_from_db()
        assert doc_type_client_profile.entity_type == DocumentType.EntityType.CLIENT

    def test_entity_type_immutable_on_edit_post_no_500_case_variant(self, client, admin_user, doc_type_case_form):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_update", kwargs={"pk": doc_type_case_form.pk}),
            _valid_post(
                name=doc_type_case_form.name,
                category=doc_type_case_form.category,
                entity_type=DocumentType.EntityType.CLIENT,
            ),
        )
        assert response.status_code == 302
        doc_type_case_form.refresh_from_db()
        assert doc_type_case_form.entity_type == DocumentType.EntityType.CASE


# ---------------------------------------------------------------------------
# Delete-Block — Client-/Case-Zähler zusätzlich zu Events
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteBlockedByClientOrCase:
    def test_blocked_by_client_reference(self, req, facility, doc_type_client_profile, staff_user):
        Client.objects.create(
            facility=facility,
            pseudonym="Person-01",
            document_type=doc_type_client_profile,
            created_by=staff_user,
        )
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(req, doc_type_client_profile)
        assert any("Person" in r for r in exc.value.reasons)
        assert DocumentType.objects.filter(pk=doc_type_client_profile.pk).exists()

    def test_blocked_by_case_reference(self, req, facility, doc_type_case_form, client_identified, staff_user):
        Case.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_case_form,
            title="Fall X",
            created_by=staff_user,
        )
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(req, doc_type_case_form)
        assert any("Fall" in r for r in exc.value.reasons)
        assert DocumentType.objects.filter(pk=doc_type_case_form.pk).exists()

    def test_client_count_in_message(self, req, facility, doc_type_client_profile, staff_user):
        for i in range(2):
            Client.objects.create(
                facility=facility,
                pseudonym=f"Person-{i:02d}",
                document_type=doc_type_client_profile,
                created_by=staff_user,
            )
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(req, doc_type_client_profile)
        assert any("2 Person" in r for r in exc.value.reasons)

    def test_event_only_reason_bestand_unchanged(self, req, facility, client_identified, doc_type_contact, staff_user):
        # Bestandsverhalten (test_config_service.TestDeleteDocumentType) bleibt
        # unverändert: ein reiner Event-Verweis liefert weiterhin genau einen
        # Grund — die neuen Client-/Case-Zähler dürfen bei 0 Referenzen nicht
        # mitgezählt werden.
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(req, doc_type_contact)
        assert len(exc.value.reasons) == 1
        assert "Ereignis" in exc.value.reasons[0]

    def test_all_three_reasons_combined(self, req, facility, client_identified, doc_type_contact, staff_user):
        # Ein einzelner Dokumentationstyp kann (Bestand/Legacy — die
        # entity_type-Beschränkung sitzt nur im Formular-Queryset, nicht als
        # DB-Constraint) gleichzeitig von Event, Client UND Case referenziert
        # werden. delete_document_type muss dann ALLE drei Gründe melden.
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        Client.objects.create(
            facility=facility, pseudonym="Kombi-Person", document_type=doc_type_contact, created_by=staff_user
        )
        Case.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            title="Kombi-Fall",
            created_by=staff_user,
        )
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(req, doc_type_contact)
        assert len(exc.value.reasons) == 3

    def test_view_post_delete_client_referenced_type_no_500(
        self, client, admin_user, facility, doc_type_client_profile, staff_user
    ):
        # PROTECT-Fehler darf nie als 500 durchschlagen — auch nicht über den
        # vollen View-POST-Pfad (Brief-Vorgabe).
        Client.objects.create(
            facility=facility, pseudonym="Person-View", document_type=doc_type_client_profile, created_by=staff_user
        )
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_delete", kwargs={"pk": doc_type_client_profile.pk}))
        assert response.status_code == 302
        assert response.url == reverse("core:config_doctype_detail", kwargs={"pk": doc_type_client_profile.pk})
        assert DocumentType.objects.filter(pk=doc_type_client_profile.pk).exists()
        assert any("Person" in m for m in _messages(response))


# ---------------------------------------------------------------------------
# E8 Schicht 2 — FILE-Feldvorlagen nur an Ereignis-Typen zuordenbar
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFileFieldEntityTypeGuard:
    def test_file_field_on_client_type_raises(self, req, facility, doc_type_client_profile):
        ft_file = FieldTemplate.objects.create(
            facility=facility, name="Anhang", field_type=FieldTemplate.FieldType.FILE
        )
        with pytest.raises(ValidationError):
            add_field_to_doctype(req, doc_type_client_profile, ft_file)
        assert not DocumentTypeField.objects.filter(
            document_type=doc_type_client_profile, field_template=ft_file
        ).exists()

    def test_file_field_on_case_type_raises(self, req, facility, doc_type_case_form):
        ft_file = FieldTemplate.objects.create(facility=facility, name="Beleg", field_type=FieldTemplate.FieldType.FILE)
        with pytest.raises(ValidationError):
            add_field_to_doctype(req, doc_type_case_form, ft_file)
        assert not DocumentTypeField.objects.filter(document_type=doc_type_case_form, field_template=ft_file).exists()

    def test_file_field_on_event_type_still_allowed(self, req, facility, doc_type_contact):
        # Regression: E8 betrifft nur client/case — der bestehende Event-Pfad
        # (Attachment-Pipeline) darf nicht mitgesperrt werden.
        ft_file = FieldTemplate.objects.create(
            facility=facility, name="Nachweis", field_type=FieldTemplate.FieldType.FILE
        )
        dtf = add_field_to_doctype(req, doc_type_contact, ft_file)
        assert dtf.field_template_id == ft_file.pk

    def test_ui_flow_non_hx_redirects_with_error_message_no_500(
        self, client, admin_user, facility, doc_type_client_profile
    ):
        ft_file = FieldTemplate.objects.create(facility=facility, name="Foto", field_type=FieldTemplate.FieldType.FILE)
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_client_profile.pk}),
            {"field_template": str(ft_file.pk), "field_group": ""},
        )
        assert response.status_code == 302
        assert not DocumentTypeField.objects.filter(
            document_type=doc_type_client_profile, field_template=ft_file
        ).exists()
        # Der Redirect-Zweig zeigt die generische board_response-Fehlermeldung;
        # die konkrete deutsche E8-Meldung steht im Formfehler des HX-Zweigs
        # (siehe test_ui_flow_hx_request_rerenders_board_no_500).
        assert any("zugeordnet" in m for m in _messages(response))

    def test_ui_flow_hx_request_rerenders_board_no_500(self, client, admin_user, facility, doc_type_client_profile):
        # Realistischer Board-Pfad (HTMX): kein 500, das Board wird mit
        # Formfehler frisch gerendert statt zu redirecten.
        ft_file = FieldTemplate.objects.create(facility=facility, name="Scan", field_type=FieldTemplate.FieldType.FILE)
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_client_profile.pk}),
            {"field_template": str(ft_file.pk), "field_group": ""},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert not DocumentTypeField.objects.filter(
            document_type=doc_type_client_profile, field_template=ft_file
        ).exists()
        # Deutsche E8-Meldung landet als Formfehler im frisch gerenderten Board.
        assert "Datei-Feldvorlagen" in response.content.decode()

    def test_ui_flow_non_file_field_on_client_type_succeeds(
        self, client, admin_user, facility, doc_type_client_profile
    ):
        # Positivbeleg (Feld-Board ist ansonsten entity-agnostisch, Report-Befund):
        # ein normales Textfeld lässt sich an einen client-Typ regulär zuordnen.
        ft_text = FieldTemplate.objects.create(
            facility=facility, name="Herkunft", field_type=FieldTemplate.FieldType.TEXT
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_client_profile.pk}),
            {"field_template": str(ft_text.pk), "field_group": ""},
        )
        assert response.status_code == 302
        assert DocumentTypeField.objects.filter(document_type=doc_type_client_profile, field_template=ft_text).exists()


# ---------------------------------------------------------------------------
# Badge — Liste + Detail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEntityTypeBadge:
    def test_list_shows_badges_for_all_entity_types(
        self, client, admin_user, doc_type_contact, doc_type_client_profile, doc_type_case_form
    ):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"))
        body = response.content.decode()
        assert "Ereignis" in body
        assert "Person" in body
        assert "Fall" in body
        assert body.count('data-testid="doctype-entity-type-badge"') == 3

    def test_detail_shows_badge_for_client_type(self, client, admin_user, doc_type_client_profile):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_client_profile.pk}))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Person" in body
        assert 'data-testid="doctype-entity-type-badge"' in body

    def test_detail_shows_badge_for_case_type(self, client, admin_user, doc_type_case_form):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_case_form.pk}))
        assert response.status_code == 200
        # Nicht nur "Fall" prüfen — der Fixture-Name "Fallformular" enthält das
        # Substring bereits selbst; der Badge-Marker ist der eindeutige Beleg.
        assert 'data-testid="doctype-entity-type-badge"' in response.content.decode()


# ---------------------------------------------------------------------------
# Live-Vorschau — entity_type-Weiche (P9-Fix-Runde 1, V-1, Refs #1058)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLivePreviewEntityTypeAware:
    """Ersetzt die vormals dokumentierte Lücke (ehem.
    ``TestLivePreviewEntityTypeGapDocumented`` — 404 für client/case, s. P9-
    Report/Fix-Runde 1, V-1): Die Live-Vorschau wählt die hx-get-URL jetzt nach
    ``doc_type.entity_type`` — ``client_fields_partial``/``case_fields_partial``
    (P7/P8) statt hart ``event_fields_partial``. Alle drei Partials rendern
    dasselbe geteilte Template (``core/partials/dynamic_fields.html``) und
    liefern bei einem reinen ``?document_type=`` (ohne ``client=``/``case=``)
    anstandslos ein ungebundenes Formular — kein Kontext-Vertragsbruch, daher
    kein Fallback-Hinweis nötig (Entscheidung s. Report).
    """

    def test_detail_uses_client_partial_for_client_type(self, client, admin_user, doc_type_client_profile):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_client_profile.pk}))
        body = response.content.decode()
        client_partial_url = reverse("core:client_fields_partial")
        assert f'hx-get="{client_partial_url}?document_type={doc_type_client_profile.pk}"' in body
        # KEIN hx-get auf event_fields_partial mehr im Markup (V-1-Vorgabe) —
        # sonst käme weiterhin der 404-Toast aus htmx-errors.js.
        assert reverse("core:event_fields_partial") not in body

    def test_detail_uses_case_partial_for_case_type(self, client, admin_user, doc_type_case_form):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_case_form.pk}))
        body = response.content.decode()
        case_partial_url = reverse("core:case_fields_partial")
        assert f'hx-get="{case_partial_url}?document_type={doc_type_case_form.pk}"' in body
        assert reverse("core:event_fields_partial") not in body

    def test_detail_still_uses_event_partial_for_event_type(self, client, admin_user, doc_type_contact):
        # Regression: Event-Typen zeigen weiterhin auf event_fields_partial.
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        body = response.content.decode()
        event_partial_url = reverse("core:event_fields_partial")
        assert f'hx-get="{event_partial_url}?document_type={doc_type_contact.pk}"' in body

    def test_client_partial_returns_200_with_field_markup(self, client, admin_user, doc_type_client_profile):
        client.force_login(admin_user)
        response = client.get(reverse("core:client_fields_partial"), {"document_type": str(doc_type_client_profile.pk)})
        assert response.status_code == 200
        # Feldname aus der Fixture (FieldTemplate.name ist die Form-Label-Quelle,
        # forms/dynamic.py) muss im gerenderten (ungebundenen) Formular auftauchen.
        assert "Wohnsituation" in response.content.decode()

    def test_case_partial_returns_200_with_field_markup(self, client, admin_user, doc_type_case_form):
        client.force_login(admin_user)
        response = client.get(reverse("core:case_fields_partial"), {"document_type": str(doc_type_case_form.pk)})
        assert response.status_code == 200
        assert "Anlass" in response.content.decode()


# ---------------------------------------------------------------------------
# Delete-Confirm-Vorwarnung — Client-/Case-Zähler (P9-Fix-Runde 1, V-2, Refs #1058)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteConfirmClientCaseCounters:
    """Vorab-Zähler auf der Confirm-Seite (GET) für Client-/Case-Referenzen,
    exakt wie beim bestehenden Event-Pfad: Warnbox + deaktivierter
    „Endgültig löschen“-Button. Vorher zeigte die Confirm-Seite nur den
    ``event_count`` — ein Typ mit ausschließlich Client-/Case-Referenzen hätte
    einen aktivierten Button gezeigt, obwohl der POST danach über
    ``ConfigDeleteBlocked`` scheitert (kein 500, aber irreführende UX)."""

    def test_confirm_page_shows_client_count_and_disables_button(
        self, client, admin_user, facility, doc_type_client_profile, staff_user
    ):
        Client.objects.create(
            facility=facility, pseudonym="Confirm-01", document_type=doc_type_client_profile, created_by=staff_user
        )
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_delete", kwargs={"pk": doc_type_client_profile.pk}))
        assert response.status_code == 200
        assert response.context["client_count"] == 1
        body = response.content.decode()
        assert 'data-testid="doctype-delete-blocker-client"' in body
        assert 'disabled aria-disabled="true"' in body

    def test_confirm_page_shows_case_count_and_disables_button(
        self, client, admin_user, facility, doc_type_case_form, client_identified, staff_user
    ):
        Case.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_case_form,
            title="Confirm-Fall",
            created_by=staff_user,
        )
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_delete", kwargs={"pk": doc_type_case_form.pk}))
        assert response.status_code == 200
        assert response.context["case_count"] == 1
        body = response.content.decode()
        assert 'data-testid="doctype-delete-blocker-case"' in body
        assert 'disabled aria-disabled="true"' in body

    def test_confirm_page_event_only_case_unchanged(
        self, client, admin_user, facility, client_identified, doc_type_contact, staff_user
    ):
        # Regression (Bestand, Muster test_confirm_page_shows_event_count):
        # ein reiner Event-Verweis bleibt exakt wie vorher — Button disabled,
        # Event-Blocker-Box da, KEINE Client-/Case-Boxen, Zähler beide 0.
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_delete", kwargs={"pk": doc_type_contact.pk}))
        assert response.status_code == 200
        assert response.context["client_count"] == 0
        assert response.context["case_count"] == 0
        body = response.content.decode()
        assert 'data-testid="doctype-delete-blocker-event"' in body
        assert 'data-testid="doctype-delete-blocker-client"' not in body
        assert 'data-testid="doctype-delete-blocker-case"' not in body
        assert 'disabled aria-disabled="true"' in body

    def test_confirm_page_no_references_button_enabled(self, client, admin_user, facility):
        dt = DocumentType.objects.create(facility=facility, name="Frisch ohne Refs", category="contact")
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_delete", kwargs={"pk": dt.pk}))
        assert response.status_code == 200
        assert response.context["client_count"] == 0
        assert response.context["case_count"] == 0
        assert response.context["event_count"] == 0
        assert 'disabled aria-disabled="true"' not in response.content.decode()
