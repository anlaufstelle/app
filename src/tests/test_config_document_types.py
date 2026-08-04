"""Tests für die DocumentType-Config-CRUD-UI (M0 P5a, Refs #1056).

Deckt Rollen-Gate (nur facility_admin), Facility-Scoping/Suche/Filter der Liste,
den HTMX-Partial-Branch, das Anlegen (facility + CONFIG_CREATE-Audit über den
Service), die system_type-Immutabilität bei Bearbeitung, den freundlichen
Unique-Fehler, das blockierte/erfolgreiche Löschen (CONFIG_DELETE-Audit) sowie
die IDOR-404-Semantik. Mutations-/Audit-Feinheiten selbst prüft
``test_config_service.py`` — hier geht es um die View-Schicht.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from core.forms.config import DocumentTypeForm
from core.models import AuditLog, DocumentType, Event, QuickTemplate


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
# Rollen-Gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccessControl:
    @pytest.mark.parametrize("user_fixture", ["lead_user", "staff_user", "assistant_user", "super_admin_user"])
    def test_non_admin_forbidden_on_list(self, client, request, user_fixture):
        client.force_login(request.getfixturevalue(user_fixture))
        response = client.get(reverse("core:config_doctype_list"))
        assert response.status_code == 403

    def test_admin_allowed_on_list(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Liste: Facility-Scoping, Suche, Filter, HTMX-Partial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestList:
    def test_foreign_doctype_not_visible(self, client, admin_user, doc_type_contact, foreign_doc_type):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"))
        body = response.content.decode()
        assert doc_type_contact.name in body
        assert foreign_doc_type.name not in body

    def test_search_filters_by_name(self, client, admin_user, facility):
        DocumentType.objects.create(facility=facility, name="Alpha", category="contact")
        DocumentType.objects.create(facility=facility, name="Beta", category="contact")
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"), {"q": "Alph"})
        body = response.content.decode()
        assert "Alpha" in body
        assert "Beta" not in body

    def test_category_filter(self, client, admin_user, facility):
        DocumentType.objects.create(facility=facility, name="Kontaktart", category="contact")
        DocumentType.objects.create(facility=facility, name="Leistungsart", category="service")
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"), {"category": "service"})
        body = response.content.decode()
        assert "Leistungsart" in body
        assert "Kontaktart" not in body

    def test_is_active_filter(self, client, admin_user, facility):
        DocumentType.objects.create(facility=facility, name="Aktiver", category="contact", is_active=True)
        DocumentType.objects.create(facility=facility, name="Inaktiver", category="contact", is_active=False)
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"), {"is_active": "false"})
        body = response.content.decode()
        assert "Inaktiver" in body
        assert "Aktiver" not in body

    def test_htmx_request_renders_partial_only(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        names = [t.name for t in response.templates if t.name]
        assert "core/config/document_types/partials/table.html" in names
        assert "core/config/document_types/list.html" not in names
        assert "base.html" not in names


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreate:
    def test_get_form_renders(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_create"))
        assert response.status_code == 200

    def test_create_sets_facility_and_writes_audit(self, client, admin_user, facility):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_create"), _valid_post(name="Frisch"))
        assert response.status_code == 302
        obj = DocumentType.objects.get(name="Frisch")
        assert obj.facility == facility
        assert response.url == reverse("core:config_doctype_detail", kwargs={"pk": obj.pk})
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(obj.pk)).exists()

    def test_create_form_offers_system_type(self, facility):
        # Regression: UUID-Default-PK macht instance.pk auch VOR dem ersten
        # Speichern truthy — ein pk-basierter disabled-Guard sperrt system_type
        # dann faelschlich schon beim Anlegen (richtig: _state.adding).
        form = DocumentTypeForm(facility=facility)
        assert form.fields["system_type"].disabled is False

    def test_create_persists_chosen_system_type(self, client, admin_user, facility):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_create"),
            _valid_post(name="Krisenbogen", system_type=DocumentType.SystemType.CRISIS),
        )
        assert response.status_code == 302
        obj = DocumentType.objects.get(name="Krisenbogen")
        assert obj.system_type == DocumentType.SystemType.CRISIS

    def test_empty_post_re_renders_without_500(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_create"), {})
        assert response.status_code == 200
        assert not DocumentType.objects.filter(name="").exists()

    def test_unique_collision_is_form_error_not_500(self, client, admin_user, facility):
        DocumentType.objects.create(facility=facility, name="Doppel", category="contact")
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_create"), _valid_post(name="Doppel", category="contact"))
        assert response.status_code == 200
        assert DocumentType.objects.filter(facility=facility, name="Doppel", category="contact").count() == 1
        assert b"bereits" in response.content


# ---------------------------------------------------------------------------
# Update — system_type-Immutabilität, category editierbar
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdate:
    def test_system_type_immutable_on_edit(self, client, admin_user, facility):
        dt = DocumentType.objects.create(
            facility=facility, name="Bann", category="admin", system_type=DocumentType.SystemType.BAN
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_update", kwargs={"pk": dt.pk}),
            _valid_post(name="Bann", category="admin", system_type=DocumentType.SystemType.CRISIS),
        )
        assert response.status_code == 302
        dt.refresh_from_db()
        assert dt.system_type == DocumentType.SystemType.BAN

    def test_category_is_editable(self, client, admin_user, facility):
        dt = DocumentType.objects.create(facility=facility, name="Wandel", category="contact")
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_update", kwargs={"pk": dt.pk}),
            _valid_post(name="Wandel", category="service"),
        )
        assert response.status_code == 302
        dt.refresh_from_db()
        assert dt.category == "service"
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(dt.pk)).exists()


# ---------------------------------------------------------------------------
# Delete — Block bei Events, Erfolg ohne Referenzen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDelete:
    def test_delete_blocked_by_events(
        self, client, admin_user, facility, client_identified, doc_type_contact, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(admin_user)
        url = reverse("core:config_doctype_delete", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url)
        assert response.status_code == 302
        assert response.url == reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk})
        assert DocumentType.objects.filter(pk=doc_type_contact.pk).exists()
        assert any("Ereignis" in m for m in _messages(response))
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.CONFIG_DELETE, target_id=str(doc_type_contact.pk)
        ).exists()

    def test_delete_without_references(self, client, admin_user, facility):
        dt = DocumentType.objects.create(facility=facility, name="Wegwerf", category="contact")
        pk = str(dt.pk)
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_delete", kwargs={"pk": dt.pk}))
        assert response.status_code == 302
        assert response.url == reverse("core:config_doctype_list")
        assert not DocumentType.objects.filter(pk=pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).exists()

    def test_confirm_page_shows_event_count(
        self, client, admin_user, facility, client_identified, doc_type_contact, staff_user
    ):
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
        assert b"doctype-delete-blocker" in response.content

    def test_confirm_page_shows_quick_template_cascade_hint(self, client, admin_user, facility, doc_type_contact):
        # QuickTemplate.document_type ist FK CASCADE → beim Löschen des Typs
        # werden zugehörige Schnellvorlagen still mitgelöscht; der Dialog muss
        # die Kaskade ausweisen.
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_contact, name="Vorlage A")
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_contact, name="Vorlage B")
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_delete", kwargs={"pk": doc_type_contact.pk}))
        assert response.status_code == 200
        assert response.context["quick_template_count"] == 2
        assert b"doctype-delete-quicktemplates" in response.content

    def test_confirm_page_hides_quick_template_hint_when_none(self, client, admin_user, facility):
        dt = DocumentType.objects.create(facility=facility, name="Ohne Vorlagen", category="contact")
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_delete", kwargs={"pk": dt.pk}))
        assert response.status_code == 200
        assert response.context["quick_template_count"] == 0
        assert b"doctype-delete-quicktemplates" not in response.content


# ---------------------------------------------------------------------------
# IDOR — fremde Facility → 404 (kein Existenz-Leak)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIDOR:
    @pytest.mark.parametrize("route", ["config_doctype_detail", "config_doctype_update", "config_doctype_delete"])
    def test_get_foreign_object_is_404(self, client, admin_user, foreign_doc_type, route):
        client.force_login(admin_user)
        response = client.get(reverse(f"core:{route}", kwargs={"pk": foreign_doc_type.pk}))
        assert response.status_code == 404

    @pytest.mark.parametrize("route", ["config_doctype_update", "config_doctype_delete"])
    def test_post_foreign_object_is_404(self, client, admin_user, foreign_doc_type, route):
        client.force_login(admin_user)
        response = client.post(reverse(f"core:{route}", kwargs={"pk": foreign_doc_type.pk}), _valid_post())
        assert response.status_code == 404
        assert DocumentType.objects.filter(pk=foreign_doc_type.pk).exists()


# ---------------------------------------------------------------------------
# Final-Review D3/E — Namens-/Invarianten-Validierung im Formular
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNonSlugifiableName:
    """Final-Review D3 (Refs #1058): seit M2 erzeugt ``DocumentType.save()`` im
    Anlege-Zweig einen Slug und wirft bei einem Namen ohne slugifizierbare
    Zeichen eine ``ValidationError`` — die View fing sie nicht, Ergebnis war ein
    HTTP 500 statt eines Formularfehlers."""

    @pytest.mark.parametrize("name", ["!!!", "###", "Кризис"])
    def test_create_with_unslugifiable_name_is_form_error(self, client, admin_user, name):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_create"), _valid_post(name=name))
        assert response.status_code == 200
        assert not DocumentType.objects.filter(name=name).exists()
        assert "Slug" in response.content.decode()

    def test_umlaut_name_still_accepted(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_create"), _valid_post(name="Krisengespräch"))
        assert response.status_code == 302
        assert DocumentType.objects.get(name="Krisengespräch").slug == "krisengespraech"


@pytest.mark.django_db
class TestFormFieldOrderAndHints:
    """Refs #1731: ``entity_type`` steuert, welche der übrigen Felder überhaupt
    wirken, und ist nach dem Anlegen unveränderbar — es muss deshalb ganz vorne
    abgefragt werden. Seit Refs #1757 rendert das Template die Felder einzeln in
    genau der ``Meta.fields``-Reihenfolge; diese Liste bleibt damit die
    Anzeigereihenfolge. ``system_type`` erklärt seine Entitätstyp-Bindung vorab
    statt erst als Absende-Fehler."""

    def test_entity_type_is_rendered_directly_after_name(self, facility):
        order = list(DocumentTypeForm(facility=facility).fields)
        assert order[:2] == ["name", "entity_type"], f"Feldreihenfolge: {order}"

    def test_entity_type_precedes_every_field_it_governs(self, facility):
        order = list(DocumentTypeForm(facility=facility).fields)
        entity_pos = order.index("entity_type")
        for governed in ("system_type", "min_contact_stage", "retention_days"):
            assert entity_pos < order.index(governed), f"{governed} steht vor entity_type: {order}"

    def test_system_type_help_text_names_the_entity_restriction(self, facility):
        help_text = str(DocumentTypeForm(facility=facility).fields["system_type"].help_text)
        assert "Ereignis" in help_text
        # Der Modell-Hinweis (Zweck + Unveränderbarkeit) bleibt erhalten.
        assert "unveränderbar" in help_text

    def test_system_type_help_text_trennt_app_logik_von_berichts_zuordnung(self, facility):
        """Refs #1748
        (RFC, Option O1): Der Hilfetext muss sagen, was die Auswahl
        wirklich bewirkt — sonst liest sich das Dropdown wie neun gleichwertige
        Verhaltensschalter, obwohl nur zwei Werte App-Logik tragen, sechs reine
        Bericht-Opt-ins sind und „Notiz" exportseitig wie „kein Systemtyp"
        wirkt. Genau diese Fehlvorstellung ist der Anlass des RFC.
        """
        help_text = str(DocumentTypeForm(facility=facility).fields["system_type"].help_text)
        assert "Hausverbot" in help_text and "Krisengespräch" in help_text
        assert "Beispiel-Sachbericht" in help_text
        assert "Notiz" in help_text

    def test_form_page_shows_the_system_type_hint(self, client, admin_user, facility):
        """Der Hinweis muss auch wirklich gerendert werden (einzeln gerenderte Felder)."""
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_create"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Entitätstyp" in content
        # Das Entitätstyp-Select steht im Markup vor dem Systemtyp-Select.
        assert content.index('name="entity_type"') < content.index('name="system_type"')


@pytest.mark.django_db
class TestEntityScopedFieldVisibility:
    """Refs #1757 (Option C aus #1731): nur-für-Ereignisse-Felder dynamisch ausblenden.

    ``system_type``, ``min_contact_stage`` und ``retention_days`` wirken
    ausschliesslich an ``entity_type=event``-Typen. Bislang bot das Formular sie
    fuer jeden Entitaetstyp vollwertig an — bei ``retention_days`` mit
    datenschutzrechtlicher Fehlannahme („Loeschfrist gesetzt"), bei
    ``system_type`` mit einem erst beim Absenden sichtbaren Fehler.

    Umgesetzt als reines UI-Ausblenden:

    * Die Zuordnung Entitaetstyp→Felder steht EINMAL in
      ``DocumentTypeForm.EVENT_ONLY_FIELDS`` und kommt als ``data``-Attribut ins
      Template; die Alpine-Komponente haelt keinen zweiten Katalog.
    * Ausgeblendete Felder bleiben echte, aktivierte Eingabefelder im DOM —
      ihr Wert wird also unveraendert mitgepostet. Kein Feld darf durchs
      Ausblenden auf den Modell-Default zurueckfallen (``category`` ist Teil des
      Natural Keys ``(facility, name, category)`` und wird ohnehin nie versteckt).
    * Ohne JavaScript bleibt alles sichtbar (progressive enhancement) — der
      Server rendert nichts versteckt.
    """

    _TEMPLATE = Path("src/templates/core/config/document_types/form.html")

    def test_template_renders_every_field_individually(self):
        """Der generische Feld-Loop ist weg — sonst gibt es keine Wrapper zum Ausblenden."""
        source = self._TEMPLATE.read_text()
        assert "for field in form" not in source
        for field_name in DocumentTypeForm.Meta.fields:
            assert f"field=form.{field_name}" in source, f"{field_name} wird nicht einzeln gerendert"

    def test_form_page_still_renders_all_nine_fields(self, client, admin_user):
        """Der Umbau darf kein Feld verlieren (Anlage-Formular, alle editierbar)."""
        client.force_login(admin_user)
        content = client.get(reverse("core:config_doctype_create")).content.decode()
        for field_name in DocumentTypeForm.Meta.fields:
            assert f'name="{field_name}"' in content, f"{field_name} fehlt im Markup"

    def test_field_order_is_unchanged(self, client, admin_user):
        """Reihenfolge = ``Meta.fields`` (Refs #1731, A): Entitaetstyp direkt hinter Name."""
        client.force_login(admin_user)
        content = client.get(reverse("core:config_doctype_create")).content.decode()
        positions = [content.index(f'name="{name}"') for name in DocumentTypeForm.Meta.fields]
        assert positions == sorted(positions), f"Feldreihenfolge im Markup: {positions}"

    def test_card_registers_the_alpine_component(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:config_doctype_create")).content.decode()
        assert 'x-data="documentTypeEntityFields"' in content

    def test_scope_attributes_come_from_the_form_constant(self, client, admin_user):
        """EINE Wahrheitsquelle: die Feldliste steht im Formular, nicht im JS/Template."""
        assert DocumentTypeForm.EVENT_ONLY_FIELDS == ("system_type", "min_contact_stage", "retention_days")
        client.force_login(admin_user)
        content = client.get(reverse("core:config_doctype_create")).content.decode()
        assert 'data-entity-scoped-fields="system_type min_contact_stage retention_days"' in content
        assert 'data-entity-scope-value="event"' in content

    def test_scoped_fields_stay_real_inputs(self, client, admin_user):
        """Ausblenden heisst NICHT ``<input type="hidden">`` — sonst waere der Wert
        nicht mehr editierbar, sobald der Entitaetstyp wieder passt."""
        client.force_login(admin_user)
        content = client.get(reverse("core:config_doctype_create")).content.decode()
        for field_name in DocumentTypeForm.EVENT_ONLY_FIELDS:
            assert f'type="hidden" name="{field_name}"' not in content
            assert f'name="{field_name}" type="hidden"' not in content

    def test_nothing_is_hidden_server_side(self, client, admin_user):
        """Kein-JS-Fallback: ohne Alpine bleiben alle Felder sichtbar."""
        client.force_login(admin_user)
        content = client.get(reverse("core:config_doctype_create")).content.decode()
        assert "<div hidden" not in content
        assert "display:none" not in content
        assert "display: none" not in content

    def test_edit_form_carries_the_same_scope_attributes(self, client, admin_user, facility):
        """Auch beim Bearbeiten (entity_type disabled) greift die Sichtbarkeitsregel."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Personenbogen 1757",
            entity_type=DocumentType.EntityType.CLIENT,
        )
        client.force_login(admin_user)
        content = client.get(reverse("core:config_doctype_update", args=[doc_type.pk])).content.decode()
        assert 'x-data="documentTypeEntityFields"' in content
        assert 'data-entity-scoped-fields="system_type min_contact_stage retention_days"' in content

    def test_hiding_never_drops_a_value_from_the_post(self, client, admin_user):
        """Regressionsschutz POST-Semantik: ``category`` faellt nie auf den Modell-Default.

        Der Default waere ``CONTACT``; hier wird bewusst ``ADMIN`` gepostet —
        ``category`` ist Teil des Natural Keys ``(facility, name, category)``.
        """
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_create"),
            _valid_post(
                name="Personenbogen POST",
                entity_type=DocumentType.EntityType.CLIENT,
                category=DocumentType.Category.ADMIN,
            ),
        )
        assert response.status_code == 302
        obj = DocumentType.objects.get(name="Personenbogen POST")
        assert obj.category == DocumentType.Category.ADMIN
        assert obj.entity_type == DocumentType.EntityType.CLIENT

    def test_scoped_values_survive_a_roundtrip_on_an_event_type(self, client, admin_user, facility):
        """Ein Ereignis-Typ behaelt seine ausblendbaren Werte ueber Bearbeiten hinweg."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Ereignisbogen 1757",
            entity_type=DocumentType.EntityType.EVENT,
            retention_days=90,
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_update", args=[doc_type.pk]),
            _valid_post(name="Ereignisbogen 1757", retention_days="90"),
        )
        assert response.status_code == 302
        doc_type.refresh_from_db()
        assert doc_type.retention_days == 90


@pytest.mark.django_db
class TestSystemTypeOnlyForEventTypes:
    """Final-Review E (Refs #1058): das Paketschema verbietet ``system_type`` an
    ``client``/``case``-Typen hart. Beide Felder sind nach dem Anlegen
    unveränderbar — ein über die Config-UI angelegter Typ mit dieser Kombination
    wäre dauerhaft nicht paketfähig und nicht reparierbar."""

    def test_create_client_type_with_system_type_is_form_error(self, client, admin_user, facility):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_create"),
            _valid_post(
                name="Personen-Bann",
                entity_type=DocumentType.EntityType.CLIENT,
                system_type=DocumentType.SystemType.BAN,
            ),
        )
        assert response.status_code == 200
        assert not DocumentType.objects.filter(name="Personen-Bann").exists()

    def test_create_client_type_without_system_type_is_fine(self, client, admin_user, facility):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_create"),
            _valid_post(name="Personenbogen", entity_type=DocumentType.EntityType.CLIENT),
        )
        assert response.status_code == 302
        obj = DocumentType.objects.get(name="Personenbogen")
        assert obj.entity_type == DocumentType.EntityType.CLIENT
        assert not obj.system_type

    def test_create_event_type_with_system_type_is_fine(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_create"),
            _valid_post(name="Krisenbogen E", system_type=DocumentType.SystemType.CRISIS),
        )
        assert response.status_code == 302

    def test_editing_legacy_inconsistent_type_stays_possible(self, client, admin_user, facility):
        """Bestandsdaten (vor dem Guard angelegt) dürfen nicht unbearbeitbar
        werden — beide Felder sind bei der Bearbeitung ohnehin ``disabled``."""
        dt = DocumentType.objects.create(
            facility=facility,
            name="Alt-Inkonsistent",
            category=DocumentType.Category.ADMIN,
            entity_type=DocumentType.EntityType.CLIENT,
            system_type=DocumentType.SystemType.BAN,
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_update", kwargs={"pk": dt.pk}),
            _valid_post(name="Alt-Inkonsistent", category=DocumentType.Category.ADMIN),
        )
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# Entfern-Bestätigung mit Datenzähler (Refs #1725(b))
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFieldRemovalConfirm:
    """Refs #1725(b): Das Feld-Board hängt an jedes „Entfernen" ein ``hx-confirm``.
    Existieren Werte unter dem Slug, warnt der Text mit Zähler, dass sie als
    „Nicht mehr zugeordnet" erhalten bleiben; bei 0 Einträgen schlichter Text."""

    def test_confirm_shows_count_and_orphan_warning(
        self, client, admin_user, facility, doc_type_contact, client_identified
    ):
        # Zwei Ereignisse mit einem Wert unter „dauer".
        for _ in range(2):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={"dauer": 15},
                created_by=admin_user,
            )
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        body = response.content.decode()
        assert "hx-confirm" in body
        assert "2 Einträge" in body
        assert "Nicht mehr zugeordnet" in body

    def test_confirm_is_plain_without_data(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        body = response.content.decode()
        assert "hx-confirm" in body
        # 0-Fall: schlichter Text ohne Warnton — kein „behalten"/Zähler.
        assert "entfernen?" in body
        assert "behalten" not in body
