"""Case-UI fuer dynamische Felder: Anlage, Bearbeitung, Detail (M2 P8, Refs #1058).

P5 hat dem ``Case``-Aggregat ``document_type`` + ``data_json`` gegeben, P3 die
entity-neutrale :class:`~core.forms.dynamic.DynamicDataForm` samt
``services/dynamic_data``, P7 das Personen-Pendant dieser Oberflaeche. P8
spiegelt P7 fuer Faelle — dieselben Grenzen, dieselben Fallen.

Abgedeckt:

* **Typ-Picker** — nur ``entity_type=case``-Typen der eigenen Einrichtung, und
  nur solche, deren Sensitivitaet die Rolle sehen darf.
* **Anlage/Bearbeitung** — Werte landen (verschluesselt) in ``data_json``,
  Update-GET zeigt Klartext, restricted-Felder ueberleben den Merge, ein
  Typwechsel verwirft Alt-Keys (E11).
* **Typ-Entfernen** — View sendet ``document_type=None`` + ``data_json={}``
  **gemeinsam**, sonst greift der fail-closed-Service-Vertrag (P5/P4b-Review).
* **Inaktiver zugewiesener Typ** — Queryset-Ausnahme (#1160) + ``?case=<pk>`` am
  Partial (P7-Fix-Runde 1+2, E-10/E-12).
* **Typ-Sperre** — ``_lock_invisible_document_type`` in vier Richtungen (E-2).
* **Partial** — AuthZ (STAFF_PLUS), Sensitivity-Gate (403, E-9), Facility-Scope
  und ``entity_type``-Scope (404); Bedingungs-Payload landet im HTML.
* **Detail** — Werte sichtbar, ``[Eingeschraenkt]`` je Rolle, Sektion nur bei
  sichtbarem Typ.
* **Optimistic Locking** — ``expected_updated_at`` bleibt mit den neuen Feldern
  intakt (Erfolg + Konflikt).
* **Audit** — der View-Pfad schreibt weiterhin ``CASE_UPDATE`` mit Feldnamen.
* **Art. 15** — dynamische Fall-Felder entschluesselt + mit Label im
  Auskunftsexport der Person.
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import override_settings
from django.urls import reverse

from core.models import (
    AuditLog,
    Case,
    DocumentType,
    DocumentTypeField,
    FieldCondition,
    FieldTemplate,
)
from core.models.attachment import Attachment
from core.services.client import _gather_cases, export_client_data
from core.services.file_vault import is_encrypted_value, safe_decrypt
from tests._entity_expectations import ENTITY_PARAMS, EntitySpec
from tests.test_clients_dynamic_ui import (
    doc_type_client_conditional,
    doc_type_client_file_condition,
    doc_type_client_high,
    doc_type_client_mixed,
)

_IMPORTED_CLIENT_FIXTURES = (
    doc_type_client_conditional,
    doc_type_client_file_condition,
    doc_type_client_high,
    doc_type_client_mixed,
)


def raw_data_json(case_obj) -> dict:
    """``core_case.data_json`` direkt aus der DB-Zeile (Muster P5)."""
    with connection.cursor() as cur:
        cur.execute("SELECT data_json FROM core_case WHERE id = %s", [str(case_obj.pk)])
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.fixture
def fernet_key():
    """Frischer Fernet-Key fuer die Verschluesselungs-Proben (Muster P5)."""
    return Fernet.generate_key().decode()


@pytest.fixture
def doc_type_case_high(facility):
    """Fall-Typ mit HIGH-Sensitivitaet — fuer Staff unsichtbar (Rolle max ELEVATED)."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CASE,
        sensitivity=DocumentType.Sensitivity.HIGH,
        name="Fall-Hochsensibel",
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Gefahrenlage",
        field_type=FieldTemplate.FieldType.TEXTAREA,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
    return doc_type


@pytest.fixture
def doc_type_case_mixed(facility):
    """Fall-Typ (NORMAL) mit einem HIGH-Feld — Staff darf genau dieses Feld nicht sehen."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CASE,
        name="Fallakte",
    )
    ft_offen = FieldTemplate.objects.create(
        facility=facility,
        name="Zielsetzung",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_geheim = FieldTemplate.objects.create(
        facility=facility,
        name="Risikoeinschaetzung",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        sensitivity="high",
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_offen, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_geheim, sort_order=1)
    return doc_type


@pytest.fixture
def doc_type_case_select(facility):
    """Fall-Typ mit SELECT + BOOLEAN — fuer die Anzeige-/Export-Formatierung."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CASE,
        name="Fall-Auswahl",
    )
    ft_hilfeform = FieldTemplate.objects.create(
        facility=facility,
        name="Hilfeform",
        field_type=FieldTemplate.FieldType.SELECT,
        options_json=[
            {"slug": "ambulant", "label": "Ambulant", "is_active": True},
            {"slug": "stationaer", "label": "Stationär", "is_active": True},
        ],
    )
    ft_kostenzusage = FieldTemplate.objects.create(
        facility=facility,
        name="Kostenzusage",
        field_type=FieldTemplate.FieldType.BOOLEAN,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_hilfeform, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_kostenzusage, sort_order=1)
    return doc_type


@pytest.fixture
def doc_type_case_conditional(facility):
    """Fall-Typ mit Bedingung TEXT->TEXTAREA (Payload-Smoke-Test)."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CASE,
        name="Fall-Bedingt",
    )
    ft_quelle = FieldTemplate.objects.create(
        facility=facility,
        name="Betreuungsform",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_ziel = FieldTemplate.objects.create(
        facility=facility,
        name="Betreuungsdetails",
        field_type=FieldTemplate.FieldType.TEXTAREA,
    )
    quelle = DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_quelle, sort_order=0)
    ziel = DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_ziel, sort_order=1)
    FieldCondition.objects.create(
        target_field=ziel,
        depends_on_field=quelle,
        operator=FieldCondition.Operator.IS_NOT_EMPTY,
        group_index=0,
    )
    return doc_type


@pytest.fixture
def doc_type_case_file_condition(facility):
    """Fall-Typ, dessen Bedingungs-QUELLE ein FILE-Feld ist (P3-Review-Nachzug).

    M24/AP4 (Refs #1738, ADR-039 §9): FILE-Felder sind an Fall-Typen regulaer —
    die E8-Sperre ist gefallen. Der Fixture pinnt, dass die Bedingung dadurch
    aktiv ist (Spiegel aus dem Personen-Pfad).
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CASE,
        name="Fall-Dateibedingt",
    )
    ft_datei = FieldTemplate.objects.create(
        facility=facility,
        name="Kostenbescheid",
        field_type=FieldTemplate.FieldType.FILE,
    )
    ft_ziel = FieldTemplate.objects.create(
        facility=facility,
        name="Bescheid-Erlaeuterung",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_required=True,
    )
    quelle = DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_datei, sort_order=0)
    ziel = DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_ziel, sort_order=1)
    FieldCondition.objects.create(
        target_field=ziel,
        depends_on_field=quelle,
        operator=FieldCondition.Operator.IS_NOT_EMPTY,
        group_index=0,
    )
    return doc_type


def _selected_option(html: str, field_name: str) -> str:
    """Wert der im ``<select name=field_name>`` vorausgewaehlten Option.

    Simuliert, was ein Browser aus dem re-gerenderten Formular posten wuerde:
    ohne vorausgewaehlte Option (weil sie aus den Choices gefallen ist) sendet
    er die leere Auswahl.
    """
    block = re.search(rf'<select[^>]*name="{re.escape(field_name)}"(.*?)</select>', html, re.S)
    if block is None:
        return ""
    selected = re.search(r'<option value="([^"]*)"[^>]*\bselected\b', block.group(1))
    return selected.group(1) if selected else ""


def _case_post_data(title, client_obj, **extra):
    """Basis-POST-Body fuer ``CaseForm`` (Pflichtfelder + Ueberschreibungen)."""
    data = {
        "title": title,
        "client": str(client_obj.pk),
        "description": "",
        "lead_user": "",
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# Typ-Picker
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("entity", ENTITY_PARAMS)
class TestEntityTypePicker:
    def test_only_entity_types_of_own_facility(self, client, staff_user, request, entity: EntitySpec):
        own_type = entity.doctype(request, "form")
        foreign_type = entity.fixture(request, "foreign_doctype")
        excluded_types = tuple(request.getfixturevalue(name) for name in entity.picker_excluded_fixtures)
        client.force_login(staff_user)
        response = client.get(reverse(entity.url("create")))
        assert response.status_code == 200
        content = response.content.decode()
        # Auf die Option-PKs pruefen (Muster P7/E-4): Typnamen koennen als
        # Teilstring anderer Labels auftauchen und waeren kein Beweis.
        assert f'value="{own_type.pk}"' in content
        assert own_type.name in content
        for excluded_type in excluded_types:
            assert f'value="{excluded_type.pk}"' not in content
        assert f'value="{foreign_type.pk}"' not in content
        assert foreign_type.name not in content

    def test_high_sensitivity_type_hidden_for_staff(self, client, staff_user, request, entity: EntitySpec):
        own_type = entity.doctype(request, "form")
        high_type = entity.doctype(request, "high")
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("create"))).content.decode()
        assert own_type.name in content
        assert high_type.name not in content

    def test_high_sensitivity_type_visible_for_lead(self, client, lead_user, request, entity: EntitySpec):
        high_type = entity.doctype(request, "high")
        client.force_login(lead_user)
        content = client.get(reverse(entity.url("create"))).content.decode()
        assert high_type.name in content

    def test_inactive_type_hidden(self, client, staff_user, request, entity: EntitySpec):
        own_type = entity.doctype(request, "form")
        own_type.is_active = False
        own_type.save(update_fields=["is_active"])
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("create"))).content.decode()
        assert own_type.name not in content

    def test_create_form_has_no_type_switch_warning(self, client, staff_user, request, entity: EntitySpec):
        """Refs #1733: im Anlage-Formular kann nichts verloren gehen."""
        entity.doctype(request, "form")
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("create"))).content.decode()
        assert "doc-type-switch-hint" not in content
        assert "data-confirm-discard" not in content


# ---------------------------------------------------------------------------
# Typwechsel/-entfernen: Warnung + Rückfrage (Refs #1733)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("entity", ENTITY_PARAMS)
class TestEntityTypeChangeConfirm:
    """Typwechsel warnt bei beiden Entitäten vor dynamischem Datenverlust."""

    def _target(self, request, entity: EntitySpec, facility, staff_user, doc_type, data_json):
        return entity.model.objects.create(
            facility=facility,
            created_by=staff_user,
            document_type=doc_type,
            data_json=data_json,
            **entity.identity_kwargs(request, "Confirm"),
        )

    def test_edit_with_values_carries_confirm_attributes_and_count(
        self, client, staff_user, facility, fernet_key, request, entity: EntitySpec
    ):
        doc_type = entity.doctype(request, "form")
        data_json = {
            entity.field("primary"): entity.value("confirm_primary"),
            entity.field("secondary"): entity.value("confirm_secondary"),
        }
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = self._target(request, entity, facility, staff_user, doc_type, data_json)
            client.force_login(staff_user)
            content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()
        assert 'x-data="confirmDynamicDataLoss"' in content
        assert 'data-confirm-discard="' in content
        confirm = re.search(r'data-confirm-discard="([^"]*)"', content).group(1)
        assert "2" in confirm
        assert "entfernen" in confirm.lower()

    def test_edit_without_values_has_no_confirm_and_no_warning(
        self, client, staff_user, facility, request, entity: EntitySpec
    ):
        doc_type = entity.doctype(request, "form")
        target = self._target(request, entity, facility, staff_user, doc_type, {})
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()
        assert "data-confirm-discard" not in content
        assert "doc-type-switch-hint" not in content

    def test_edit_with_values_shows_alert_styled_warning(
        self, client, staff_user, facility, fernet_key, request, entity: EntitySpec
    ):
        doc_type = entity.doctype(request, "form")
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = self._target(
                request,
                entity,
                facility,
                staff_user,
                doc_type,
                {entity.field("primary"): entity.value("confirm_primary")},
            )
            client.force_login(staff_user)
            content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()
        hint = re.search(r'<[^>]*data-testid="doc-type-switch-hint"[^>]*>(.*?)</div>', content, re.S)
        assert hint is not None, "Hinweis fehlt im Bearbeiten-Formular mit Werten"
        assert 'role="alert"' in hint.group(0)
        assert "amber" in hint.group(0)
        assert "entfern" in hint.group(1).lower()

    def test_locked_invisible_type_gets_no_confirm(
        self, client, staff_user, facility, fernet_key, request, entity: EntitySpec
    ):
        doc_type = entity.doctype(request, "high")
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = self._target(
                request,
                entity,
                facility,
                staff_user,
                doc_type,
                {entity.field("high"): entity.value("high_secret")},
            )
            client.force_login(staff_user)
            content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()
        assert "data-confirm-discard" not in content


# ---------------------------------------------------------------------------
# Anlage
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("entity", ENTITY_PARAMS)
class TestEntityCreateWithDynamicData:
    def test_create_persists_dynamic_values_encrypted(
        self, client, staff_user, fernet_key, request, entity: EntitySpec
    ):
        doc_type = entity.doctype(request, "form")
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.post(
                reverse(entity.url("create")),
                entity.post_data(
                    request,
                    "Create-01",
                    document_type=str(doc_type.pk),
                    **{
                        entity.field("primary"): entity.value("primary"),
                        entity.field("secondary"): entity.value("secondary"),
                    },
                ),
            )
            assert response.status_code == 302
            created = entity.model.objects.get(**{entity.identity_field: f"{entity.id_prefix}Create-01"})
            assert created.document_type_id == doc_type.pk
            row = entity.raw_data_json(created)
            assert row[entity.field("primary")] == entity.value("primary")
            assert is_encrypted_value(row[entity.field("secondary")])
            assert safe_decrypt(row[entity.field("secondary")]) == entity.value("secondary")

    def test_create_without_type_keeps_data_json_empty(self, client, staff_user, request, entity: EntitySpec):
        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("create")),
            entity.post_data(
                request,
                "Create-02",
                **{entity.field("primary"): entity.value("discarded")},
            ),
        )
        assert response.status_code == 302
        created = entity.model.objects.get(**{entity.identity_field: f"{entity.id_prefix}Create-02"})
        assert created.document_type_id is None
        assert created.data_json == {}

    def test_create_rerender_on_invalid_meta_keeps_dynamic_fields(
        self, client, staff_user, request, entity: EntitySpec
    ):
        """Ungueltige Stammdaten rendern mit befuellten dynamischen Feldern neu."""
        doc_type = entity.doctype(request, "form")
        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("create")),
            entity.post_data_builder(
                request,
                entity.invalid_identity_builder(request),
                {
                    "document_type": str(doc_type.pk),
                    entity.field("primary"): entity.value("rerender_primary"),
                },
            ),
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert entity.label("primary") in content
        assert entity.value("rerender_primary") in content

    def test_post_rerender_does_not_leak_restricted_doctype_labels(
        self, client, staff_user, request, entity: EntitySpec
    ):
        """#774-Spiegel: ein HIGH-Typ im POST darf keine Feldlabels ausspielen."""
        doc_type = entity.doctype(request, "high")
        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("create")),
            entity.post_data_builder(
                request,
                entity.invalid_identity_builder(request),
                {
                    "document_type": str(doc_type.pk),
                    entity.field("high"): entity.value("high_secret"),
                },
            ),
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert entity.label("high") not in content
        assert doc_type.name not in content


# ---------------------------------------------------------------------------
# Bearbeitung
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("entity", ENTITY_PARAMS)
class TestEntityUpdateWithDynamicData:
    @staticmethod
    def _target(request, entity: EntitySpec, facility, staff_user, suffix, doc_type, data_json):
        return entity.model.objects.create(
            facility=facility,
            created_by=staff_user,
            document_type=doc_type,
            data_json=data_json,
            **entity.identity_kwargs(request, suffix),
        )

    def test_update_get_prefills_decrypted_values(
        self, client, staff_user, facility, fernet_key, request, entity: EntitySpec
    ):
        doc_type = entity.doctype(request, "form")
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = self._target(
                request,
                entity,
                facility,
                staff_user,
                "Update-01",
                doc_type,
                {
                    entity.field("primary"): entity.value("primary"),
                    entity.field("secondary"): "Klartext-Probe",
                },
            )
            client.force_login(staff_user)
            content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()
        assert "Klartext-Probe" in content
        assert entity.value("primary") in content

    def test_update_saves_changed_values(self, client, staff_user, facility, fernet_key, request, entity: EntitySpec):
        doc_type = entity.doctype(request, "form")
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = self._target(
                request,
                entity,
                facility,
                staff_user,
                "Update-02",
                doc_type,
                {entity.field("primary"): entity.value("primary")},
            )
            client.force_login(staff_user)
            response = client.post(
                reverse(entity.url("update"), args=[target.pk]),
                entity.post_data(
                    request,
                    "Update-02",
                    document_type=str(doc_type.pk),
                    **{
                        entity.field("primary"): entity.value("update_primary"),
                        entity.field("secondary"): "Neu",
                    },
                ),
            )
            assert response.status_code == 302
            target.refresh_from_db()
            assert target.data_json[entity.field("primary")] == entity.value("update_primary")
            assert safe_decrypt(target.data_json[entity.field("secondary")]) == "Neu"

    def test_restricted_field_hidden_and_preserved(self, client, staff_user, facility, request, entity: EntitySpec):
        """Ein HIGH-Feld ist fuer Staff unsichtbar — und ueberlebt dessen Update.

        ``FieldTemplate.save()`` erzwingt bei ``sensitivity=high`` zusaetzlich
        ``is_encrypted=True`` (Refs #733/#1270) — der Alt-Wert liegt also als
        Fernet-Token im JSONB und wird von ``merge_dynamic_data`` als solcher
        nachgezogen (keine Doppelverschluesselung).
        """
        doc_type = entity.doctype(request, "mixed")
        target = self._target(
            request,
            entity,
            facility,
            staff_user,
            "Update-03",
            doc_type,
            {
                entity.field("mixed_visible"): entity.value("mixed_initial"),
                entity.field("mixed_restricted"): "nur fuer Leitung",
            },
        )
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()
        assert entity.label("mixed_visible") in content
        assert entity.label("mixed_restricted") not in content
        assert f'name="{entity.field("mixed_restricted")}"' not in content
        assert "nur fuer Leitung" not in content

        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data(
                request,
                "Update-03",
                document_type=str(doc_type.pk),
                **{entity.field("mixed_visible"): entity.value("mixed_updated")},
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.data_json[entity.field("mixed_visible")] == entity.value("mixed_updated")
        assert safe_decrypt(target.data_json[entity.field("mixed_restricted")]) == "nur fuer Leitung"

    def test_historical_restricted_number_outside_new_range_redirects_without_writing(
        self, client, staff_user, facility, request, entity: EntitySpec
    ):
        """Refs #1927: Der E11-Nachzug kann einen Feldfehler als dict auslösen.

        Der historische, für Staff unsichtbare Wert entstand vor der späteren
        Bereichsverschärfung. Sein Merge darf weder einen 500er verursachen
        noch Falldaten oder Anhänge verändern.
        """
        document_type = DocumentType.objects.create(
            facility=facility,
            entity_type=entity.entity_type,
            name=entity.value("range_doctype_name"),
        )
        number_field = FieldTemplate.objects.create(
            facility=facility,
            name="Historische Anzahl",
            field_type=FieldTemplate.FieldType.NUMBER,
            sensitivity="high",
        )
        visible_field = FieldTemplate.objects.create(
            facility=facility,
            name="Sichtbarer Vermerk",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=document_type, field_template=number_field)
        DocumentTypeField.objects.create(document_type=document_type, field_template=visible_field)
        target = self._target(
            request,
            entity,
            facility,
            staff_user,
            entity.value("historical_identity"),
            document_type,
            {number_field.slug: 0, visible_field.slug: "vorher"},
        )
        # Ein historischer Klartextwert kann den heutigen HIGH=>verschlüsselt-
        # Backstop umgangen haben. Die nachträglich gesetzte Untergrenze macht
        # den vom Merge erneut validierten Wert ungültig.
        entity.model.objects.filter(pk=target.pk).update(
            data_json={number_field.slug: -1, visible_field.slug: "vorher"}
        )
        number_field.min_value = 0
        number_field.save(update_fields=["min_value"])

        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data(
                request,
                entity.value("historical_new_identity"),
                document_type=str(document_type.pk),
                **{visible_field.slug: "nachher"},
            ),
        )

        assert response.status_code == 302
        assert response.url == reverse(entity.url("update"), args=[target.pk])
        # Refs #1936-Vertrag: Die Grenze eines fuer diese Rolle unsichtbaren
        # Feldes ist eine interne Regelkennung und wird vollstaendig ersetzt.
        assert [str(message) for message in get_messages(response.wsgi_request)] == ["Ungültige Eingabe."]
        target.refresh_from_db()
        expected_identity = f"{entity.id_prefix}{entity.value('historical_identity')}"
        assert getattr(target, entity.identity_field) == expected_identity
        assert target.data_json == {number_field.slug: -1, visible_field.slug: "vorher"}
        assert Attachment.objects.filter(**{entity.attachment_relation: target}).count() == 0

    def test_visible_field_service_error_keeps_its_concrete_message(
        self, client, staff_user, facility, request, entity: EntitySpec
    ):
        """Refs #1936: Nur restricted/unbekannte Keys werden redigiert."""
        document_type = DocumentType.objects.create(
            facility=facility,
            entity_type=entity.entity_type,
            name=entity.value("visible_error_doctype_name"),
        )
        visible_field = FieldTemplate.objects.create(
            facility=facility,
            name="Sichtbare Anzahl",
            field_type=FieldTemplate.FieldType.NUMBER,
        )
        DocumentTypeField.objects.create(document_type=document_type, field_template=visible_field)
        target = self._target(
            request,
            entity,
            facility,
            staff_user,
            entity.value("visible_error_identity"),
            document_type,
            {visible_field.slug: 3},
        )

        client.force_login(staff_user)
        with patch(
            entity.update_service_path,
            side_effect=ValidationError({visible_field.slug: ["Wert muss mindestens 5 sein."]}),
        ):
            response = client.post(
                reverse(entity.url("update"), args=[target.pk]),
                entity.post_data(
                    request,
                    entity.value("visible_error_new_identity"),
                    document_type=str(document_type.pk),
                    **{visible_field.slug: "3"},
                ),
            )

        assert response.status_code == 302
        assert [str(message) for message in get_messages(response.wsgi_request)] == ["Wert muss mindestens 5 sein."]

    def test_type_switch_drops_old_keys(self, client, staff_user, facility, request, entity: EntitySpec):
        old_type = entity.doctype(request, "form")
        new_type = entity.doctype(request, "mixed")
        target = self._target(
            request,
            entity,
            facility,
            staff_user,
            "Update-04",
            old_type,
            {entity.field("primary"): entity.value("primary")},
        )
        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data(
                request,
                "Update-04",
                document_type=str(new_type.pk),
                **{entity.field("mixed_visible"): entity.value("mixed_initial")},
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id == new_type.pk
        assert entity.field("primary") not in target.data_json
        assert target.data_json[entity.field("mixed_visible")] == entity.value("mixed_initial")

    def test_removing_type_clears_data_json_without_error(
        self, client, staff_user, facility, request, entity: EntitySpec
    ):
        """P5/P4b-Review: ``document_type=None`` + befuelltes ``data_json`` ist fail-closed.

        Die View MUSS das Paar gemeinsam senden — sonst wirft der Service
        ``ValidationError`` und der Benutzer bekaeme einen Fehler statt eines
        geleerten Datensatzes.
        """
        doc_type = entity.doctype(request, "form")
        target = self._target(
            request,
            entity,
            facility,
            staff_user,
            "Update-05",
            doc_type,
            {entity.field("primary"): entity.value("primary")},
        )
        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data(request, "Update-05", document_type=""),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id is None
        assert target.data_json == {}


# ---------------------------------------------------------------------------
# Optimistic Locking + Audit (Bestandsvertrag bleibt intakt)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseUpdateOptimisticLockingAndAudit:
    def test_expected_updated_at_conflict_still_blocks(
        self, client, staff_user, facility, client_identified, doc_type_case_form
    ):
        """Der Konfliktpfad (Refs #531) bleibt mit den neuen Feldern unveraendert."""
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Lock-01",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Alt"},
        )
        stale = "2020-01-01T00:00:00+00:00"
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "P8-Lock-01-geaendert",
                client_identified,
                document_type=str(doc_type_case_form.pk),
                anlass="Neu",
                expected_updated_at=stale,
            ),
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("core:case_update", args=[target.pk])
        target.refresh_from_db()
        assert target.title == "P8-Lock-01"
        assert target.data_json["anlass"] == "Alt"

    def test_expected_updated_at_current_token_saves(
        self, client, staff_user, facility, client_identified, doc_type_case_form
    ):
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Lock-02",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Alt"},
        )
        target.refresh_from_db()
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "P8-Lock-02",
                client_identified,
                document_type=str(doc_type_case_form.pk),
                anlass="Neu",
                expected_updated_at=target.updated_at.isoformat(),
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.data_json["anlass"] == "Neu"

    def test_update_form_carries_expected_updated_at(self, client, staff_user, case_open):
        client.force_login(staff_user)
        content = client.get(reverse("core:case_update", args=[case_open.pk])).content.decode()
        assert 'name="expected_updated_at"' in content

    def test_view_path_update_writes_audit_with_field_names(
        self, client, staff_user, facility, client_identified, doc_type_case_form
    ):
        """Refs #1613-Spiegel: ``CaseForm(instance=case)`` mutiert die Instanz in
        ``_post_clean`` — der Diff muss deshalb gegen den DB-Stand laufen, sonst
        bliebe ``changed_fields`` leer und ``CASE_UPDATE`` wuerde nie
        geschrieben (Threat-Model TB6, Repudiation).
        """
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Fall-Titel vorher",
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "Fall-Titel nachher",
                client_identified,
                document_type=str(doc_type_case_form.pk),
                anlass="Streng geheime Adresse",
            ),
        )
        assert response.status_code == 302
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CASE_UPDATE,
            target_id=str(target.pk),
        ).latest("timestamp")
        changed = set(entry.detail.get("changed_fields", []))
        assert {"title", "document_type", "data_json"} <= changed
        assert "Streng geheime Adresse" not in str(entry.detail)


# ---------------------------------------------------------------------------
# Deaktivierter, aber zugewiesener Dokumentationstyp (P7-Fix-Runde 1, E-10)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("entity", ENTITY_PARAMS)
class TestEntityInactiveAssignedDocumentType:
    """Ein zwischenzeitlich deaktivierter Typ darf beim Update nicht still wegfallen.

    Ohne die Queryset-Ausnahme (Muster #1160, ``_select_choices``) faellt der
    zugewiesene Typ aus der Auswahl, das Select zeigt „— Kein Typ —"
    vorausgewaehlt, und ein voellig normaler Submit loescht Typ UND alle
    Zusatzfeld-Werte, ohne dass die Bearbeiterin es bemerkt.
    """

    @staticmethod
    def _deactivate(doc_type):
        doc_type.is_active = False
        doc_type.save(update_fields=["is_active"])
        return doc_type

    @staticmethod
    def _target(request, entity: EntitySpec, facility, staff_user, suffix, doc_type, data_json):
        return entity.model.objects.create(
            facility=facility,
            created_by=staff_user,
            document_type=doc_type,
            data_json=data_json,
            **entity.identity_kwargs(request, suffix),
        )

    def test_update_get_preselects_inactive_assigned_type(
        self, client, staff_user, facility, request, entity: EntitySpec
    ):
        doc_type = entity.doctype(request, "form")
        target = self._target(
            request,
            entity,
            facility,
            staff_user,
            "Inaktiv-01",
            doc_type,
            {entity.field("primary"): entity.value("primary")},
        )
        self._deactivate(doc_type)
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()

        assert f'<option value="{doc_type.pk}" selected>' in content
        assert "(deaktiviert)" in content
        # Die Zusatzfelder des inaktiven Typs bleiben bearbeitbar.
        assert f'name="{entity.field("primary")}"' in content
        assert entity.value("primary") in content

    def test_unchanged_submit_keeps_inactive_type_and_values(
        self, client, staff_user, facility, request, entity: EntitySpec
    ):
        doc_type = entity.doctype(request, "form")
        target = self._target(
            request,
            entity,
            facility,
            staff_user,
            "Inaktiv-02",
            doc_type,
            {entity.field("primary"): entity.value("primary")},
        )
        self._deactivate(doc_type)
        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data(
                request,
                "Inaktiv-02",
                document_type=str(doc_type.pk),
                **{entity.field("primary"): entity.value("primary")},
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id == doc_type.pk
        assert target.data_json[entity.field("primary")] == entity.value("primary")

    def test_create_still_hides_inactive_types(self, client, staff_user, request, entity: EntitySpec):
        """Die Ausnahme gilt NUR fuer den bearbeiteten Datensatz — Neuanlagen nicht."""
        doc_type = entity.doctype(request, "form")
        self._deactivate(doc_type)
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("create"))).content.decode()
        assert f'value="{doc_type.pk}"' not in content

        # Und ein untergeschobener POST wird abgelehnt (Queryset-Validierung).
        response = client.post(
            reverse(entity.url("create")),
            entity.post_data(
                request,
                "Inaktiv-03",
                document_type=str(doc_type.pk),
                **{entity.field("primary"): entity.value("primary")},
            ),
        )
        assert response.status_code == 200
        assert not entity.model.objects.filter(**{entity.identity_field: f"{entity.id_prefix}Inaktiv-03"}).exists()

    def test_deliberate_removal_still_works(self, client, staff_user, facility, request, entity: EntitySpec):
        doc_type = entity.doctype(request, "form")
        target = self._target(
            request,
            entity,
            facility,
            staff_user,
            "Inaktiv-04",
            doc_type,
            {entity.field("primary"): entity.value("primary")},
        )
        self._deactivate(doc_type)
        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data(request, "Inaktiv-04", document_type=""),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id is None
        assert target.data_json == {}

    def test_invalid_rerender_keeps_inactive_type_fields(
        self, client, staff_user, facility, request, entity: EntitySpec
    ):
        """Auch der Re-Render eines ungueltigen Formulars behaelt die Feldwerte."""
        doc_type = entity.doctype(request, "form")
        target = self._target(
            request,
            entity,
            facility,
            staff_user,
            "Inaktiv-05",
            doc_type,
            {entity.field("primary"): entity.value("primary")},
        )
        self._deactivate(doc_type)
        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data_builder(
                request,
                entity.invalid_identity_builder(request),
                {
                    "document_type": str(doc_type.pk),
                    entity.field("primary"): entity.value("update_primary"),
                },
            ),
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert f'name="{entity.field("primary")}"' in content
        assert entity.value("update_primary") in content

    def test_invisible_type_lock_beats_inactive_exception(
        self, client, staff_user, facility, request, entity: EntitySpec
    ):
        """Inaktiv UND unsichtbar: die Lock-Logik hat Vorrang, das Feld faellt ganz weg."""
        doc_type = entity.doctype(request, "high")
        target = self._target(
            request,
            entity,
            facility,
            staff_user,
            "Inaktiv-06",
            doc_type,
            {entity.field("high"): entity.value("high_confidential")},
        )
        self._deactivate(doc_type)
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()
        assert 'name="document_type"' not in content
        assert doc_type.name not in content
        assert entity.value("high_confidential") not in content


# ---------------------------------------------------------------------------
# Sperre bei unsichtbarem Dokumentationstyp (E-2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("entity", ENTITY_PARAMS)
class TestEntityInvisibleDocumentTypeLock:
    """``_lock_invisible_document_type`` — vier Richtungen, alle fixiert.

    Darf die Rolle den Typ des Falls nicht sehen, faellt das Typ-Select ganz aus
    dem Formular. Sonst wuerde (a) der Typname geleakt und (b) ein Submit mit
    leerem Feld Typ und alle unsichtbaren Werte still loeschen.
    """

    @staticmethod
    def _locked(request, entity: EntitySpec, facility, staff_user):
        doc_type = entity.doctype(request, "high")
        target = entity.model.objects.create(
            facility=facility,
            created_by=staff_user,
            document_type=doc_type,
            data_json={entity.field("high"): entity.value("high_confidential")},
            **entity.identity_kwargs(request, entity.value("lock_identity")),
        )
        return target, doc_type

    def test_get_leaks_nothing(self, client, staff_user, facility, request, entity: EntitySpec):
        target, doc_type = self._locked(request, entity, facility, staff_user)
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()
        assert 'name="document_type"' not in content
        assert doc_type.name not in content
        assert entity.label("high") not in content
        assert entity.value("high_confidential") not in content

    def test_normal_post_keeps_type_and_hidden_values(self, client, staff_user, facility, request, entity: EntitySpec):
        target, doc_type = self._locked(request, entity, facility, staff_user)
        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data(
                request,
                entity.value("lock_identity"),
                **{entity.field("meta"): entity.value("meta_updated")},
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert getattr(target, entity.field("meta")) == entity.value("meta_updated")
        assert target.document_type_id == doc_type.pk
        assert safe_decrypt(target.data_json[entity.field("high")]) == entity.value("high_confidential")

    def test_smuggled_visible_type_is_ignored(self, client, staff_user, facility, request, entity: EntitySpec):
        target, high_type = self._locked(request, entity, facility, staff_user)
        visible_type = entity.doctype(request, "form")
        client.force_login(staff_user)
        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data(
                request,
                entity.value("lock_identity"),
                document_type=str(visible_type.pk),
                **{entity.field("primary"): "untergeschoben"},
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id == high_type.pk
        assert entity.field("primary") not in target.data_json
        assert safe_decrypt(target.data_json[entity.field("high")]) == entity.value("high_confidential")

    def test_authorized_role_is_unaffected(self, client, lead_user, staff_user, facility, request, entity: EntitySpec):
        target, doc_type = self._locked(request, entity, facility, staff_user)
        client.force_login(lead_user)
        content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()
        assert 'name="document_type"' in content
        assert doc_type.name in content
        assert entity.label("high") in content
        assert entity.value("high_confidential") in content

        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data(
                request,
                entity.value("lock_identity"),
                document_type=str(doc_type.pk),
                **{entity.field("high"): "von der Leitung geaendert"},
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert safe_decrypt(target.data_json[entity.field("high")]) == "von der Leitung geaendert"


# ---------------------------------------------------------------------------
# HTMX-Partial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("entity", ENTITY_PARAMS)
class TestEntityFieldsPartial:
    def test_staff_gets_fields(self, client, staff_user, request, entity: EntitySpec):
        doc_type = entity.doctype(request, "form")
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert entity.label("primary") in content
        assert entity.label("secondary") in content

    def test_assistant_is_denied(self, client, assistant_user, request, entity: EntitySpec):
        doc_type = entity.doctype(request, "form")
        client.force_login(assistant_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk)},
        )
        assert response.status_code in (403, 302)
        if response.status_code == 302:
            assert "/login" in response["Location"]

    def test_without_param_renders_empty(self, client, staff_user, entity: EntitySpec):
        client.force_login(staff_user)
        response = client.get(reverse(entity.url("fields_partial")))
        assert response.status_code == 200

    def test_restricted_sensitivity_blocked(self, client, staff_user, request, entity: EntitySpec):
        """E-9: Sensitivity-Gate antwortet 403 (Event-/Personen-Muster), nicht 404."""
        doc_type = entity.doctype(request, "high")
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk)},
        )
        assert response.status_code == 403

    def test_foreign_facility_type_is_404(self, client, staff_user, request, entity: EntitySpec):
        doc_type = entity.fixture(request, "foreign_doctype")
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk)},
        )
        assert response.status_code == 404

    def test_event_type_is_404(self, client, staff_user, request, entity: EntitySpec):
        doc_type = request.getfixturevalue("doc_type_contact")
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk)},
        )
        assert response.status_code == 404

    def test_inactive_type_is_404(self, client, staff_user, request, entity: EntitySpec):
        """Ohne Objekt-Parameter bleibt das Verhalten unveraendert (Fall d)."""
        doc_type = entity.doctype(request, "form")
        doc_type.is_active = False
        doc_type.save(update_fields=["is_active"])
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk)},
        )
        assert response.status_code == 404

    def test_malformed_pk_is_404(self, client, staff_user, entity: EntitySpec):
        client.force_login(staff_user)
        response = client.get(reverse(entity.url("fields_partial")), {"document_type": "keine-uuid"})
        assert response.status_code == 404

    def test_conditions_payload_lands_in_html(self, client, staff_user, request, entity: EntitySpec):
        """M1-Bedingungen greifen auf beiden Entity-Seiten ohne Zusatzarbeit."""
        doc_type = entity.doctype(request, "conditional")
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk)},
        )
        content = response.content.decode()
        assert 'id="field-conditions"' in content
        assert entity.field("conditional_target") in content
        assert "is_not_empty" in content

    def test_file_source_condition_is_active(self, client, staff_user, facility, request, entity: EntitySpec):
        """Anforderungsaenderung M24/AP4 (Refs #1738, ADR-039 §9).

        **Vorher (E8):** die Bedingungs-Quelle war ein per
        ``exclude_file_fields`` uebersprungenes FILE-Feld; die Bedingung fiel
        der Drop-Regel (#774) zum Opfer, das Ziel wurde still unbedingt
        sichtbar und pflichtig.

        **Jetzt:** Datei-Felder sind an Fall-Typen regulaer — die Quelle liegt
        im Feldset, die Bedingung wird ausgeliefert, die Pflicht des Ziels
        greift nur bei gefuellter Quelle (Spiegel aus dem Personen-Pfad).
        """
        doc_type = entity.doctype(request, "file_condition")
        source_field = entity.field("file_source")
        target_field = entity.field("file_target")
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert f'name="{source_field}"' in content
        assert 'id="field-conditions"' in content
        assert f'name="{target_field}"' in content

        form = response.context["data_form"]
        assert source_field in form.fields
        assert form.fields[target_field].required is False
        assert form.condition_target_slugs == {target_field}
        # ``facility`` ist Pflicht: ``DynamicDataForm.clean()`` steigt ohne sie
        # frueh aus und liesse die konditionale Pflichtpruefung aus.
        bound = form.__class__(
            {target_field: ""},
            document_type=doc_type,
            facility=facility,
        )
        assert bound.is_valid(), bound.errors


@pytest.mark.django_db
class TestCaseFieldsPartial:
    """Entity-eigener Scope-Guard ohne Client-Gegenstück."""

    def test_client_type_is_404(self, client, staff_user, doc_type_client_profile):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_client_profile.pk)},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Partial + inaktiver Ist-Typ des bearbeiteten Falls (P7-Fix-Runde 2, E-12)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("entity", ENTITY_PARAMS)
class TestEntityPartialAcceptsAssignedInactiveType:
    """Der Objektparameter erlaubt genau den zugewiesenen inaktiven Typ.

    Ohne diese Symmetrie zum ``CaseForm``-Queryset entsteht ein **stiller
    Datenverlust**: Bearbeiterin wechselt im Select weg vom inaktiven Ist-Typ
    (HTMX laedt die Felder des anderen Typs), wechselt zurueck ⇒ 404 ⇒ HTMX
    swapt bei 4xx NICHT ⇒ im DOM stehen weiter die Felder des ANDEREN Typs ⇒
    der (voellig plausible) Submit traegt die Ist-Slugs nicht ⇒
    ``merge_dynamic_data`` zieht nur ``restricted_slugs`` nach und leert die
    Bestandswerte.
    """

    @staticmethod
    def _inactive_target(request, entity: EntitySpec, facility, staff_user):
        doc_type = entity.doctype(request, "form")
        target = entity.model.objects.create(
            facility=facility,
            created_by=staff_user,
            document_type=doc_type,
            data_json={entity.field("primary"): entity.value("primary")},
            **entity.identity_kwargs(request, "Partial-01"),
        )
        doc_type.is_active = False
        doc_type.save(update_fields=["is_active"])
        return target, doc_type

    def test_assigned_inactive_type_is_served(self, client, staff_user, facility, request, entity: EntitySpec):
        """(a) Objektparameter + inaktiver Ist-Typ ⇒ 200 mit dessen Feldern."""
        target, doc_type = self._inactive_target(request, entity, facility, staff_user)
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk), entity.name: str(target.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert f'name="{entity.field("primary")}"' in content
        assert f'name="{entity.field("secondary")}"' in content

    def test_other_inactive_type_stays_404(self, client, staff_user, facility, request, entity: EntitySpec):
        """(b) ein inaktiver, aber NICHT zugewiesener Typ bleibt trotz Param 404."""
        target, _ = self._inactive_target(request, entity, facility, staff_user)
        other_type = entity.doctype(request, "mixed")
        other_type.is_active = False
        other_type.save(update_fields=["is_active"])
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(other_type.pk), entity.name: str(target.pk)},
        )
        assert response.status_code == 404

    def test_foreign_object_pk_is_404(self, client, staff_user, request, entity: EntitySpec):
        """(c) fremde Objekt-PK ⇒ 404 (facility-gescopter Lookup)."""
        doc_type = entity.doctype(request, "form")
        foreign_object = entity.fixture(request, "foreign_object")
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk), entity.name: str(foreign_object.pk)},
        )
        assert response.status_code == 404

    def test_malformed_object_pk_is_404(self, client, staff_user, request, entity: EntitySpec):
        doc_type = entity.doctype(request, "form")
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk), entity.name: "keine-uuid"},
        )
        assert response.status_code == 404

    def test_active_type_unaffected_by_param(self, client, staff_user, request, entity: EntitySpec):
        """Der Param lockert nichts fuer aktive Typen — er ist nur eine Ausnahme."""
        doc_type = entity.doctype(request, "form")
        target = entity.fixture(request, "object")
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk), entity.name: str(target.pk)},
        )
        assert response.status_code == 200

    def test_invisible_assigned_type_still_403(self, client, staff_user, facility, request, entity: EntitySpec):
        """Sensitivity bleibt hart: der Param hebelt das 403-Gate nicht aus."""
        doc_type = entity.doctype(request, "high")
        target = entity.model.objects.create(
            facility=facility,
            created_by=staff_user,
            document_type=doc_type,
            data_json={entity.field("high"): entity.value("high_confidential")},
            **entity.identity_kwargs(request, "Partial-02"),
        )
        doc_type.is_active = False
        doc_type.save(update_fields=["is_active"])
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk), entity.name: str(target.pk)},
        )
        assert response.status_code == 403

    def test_update_form_hx_get_carries_object_param(self, client, staff_user, facility, request, entity: EntitySpec):
        """Ohne den Param im ``hx-get`` schickt der Browser ihn nie mit."""
        target, _ = self._inactive_target(request, entity, facility, staff_user)
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("update"), args=[target.pk])).content.decode()
        expected = f"{reverse(entity.url('fields_partial'))}?{entity.name}={target.pk}"
        assert f'hx-get="{expected}"' in content

    def test_create_form_hx_get_has_no_object_param(self, client, staff_user, request, entity: EntitySpec):
        """M0-Falle: ``Case.id`` traegt einen UUID-Default, ``pk`` allein
        unterscheidet Anlage und Bearbeitung nicht (``_state.adding``, E-13)."""
        entity.doctype(request, "form")
        client.force_login(staff_user)
        content = client.get(reverse(entity.url("create"))).content.decode()
        assert f'hx-get="{reverse(entity.url("fields_partial"))}"' in content
        assert f"?{entity.name}=" not in content

    def test_switch_away_and_back_returns_own_fields(self, client, staff_user, facility, request, entity: EntitySpec):
        """(e) Charakterisierung des Ausgangsszenarios auf View-Ebene."""
        target, own_type = self._inactive_target(request, entity, facility, staff_user)
        other_type = entity.doctype(request, "mixed")
        client.force_login(staff_user)
        partial = reverse(entity.url("fields_partial"))
        target_pk = str(target.pk)

        # 1. Weg vom Ist-Typ auf einen anderen (aktiven) Typ.
        away = client.get(partial, {"document_type": str(other_type.pk), entity.name: target_pk})
        assert away.status_code == 200
        assert f'name="{entity.field("mixed_visible")}"' in away.content.decode()

        # 2. Zurueck auf den inaktiven Ist-Typ — ohne die Ausnahme ein 404, bei
        #    dem HTMX den DOM unveraendert liesse.
        back = client.get(partial, {"document_type": str(own_type.pk), entity.name: target_pk})
        assert back.status_code == 200
        back_content = back.content.decode()
        assert f'name="{entity.field("primary")}"' in back_content
        assert f'name="{entity.field("mixed_visible")}"' not in back_content

        # 3. Und der darauf folgende Submit erhaelt die Bestandswerte.
        response = client.post(
            reverse(entity.url("update"), args=[target.pk]),
            entity.post_data(
                request,
                "Partial-01",
                document_type=str(own_type.pk),
                **{entity.field("primary"): entity.value("primary")},
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id == own_type.pk
        assert target.data_json[entity.field("primary")] == entity.value("primary")


# ---------------------------------------------------------------------------
# Partial + Rueckweg auf den bereits zugewiesenen Typ (Refs #1758)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("entity", ENTITY_PARAMS)
class TestEntityPartialPrefillsAssignedType:
    """Der Rueckweg auf den gespeicherten Typ liefert dessen Werte mit.

    Der Rueckweg auf den gespeicherten Typ ist kein Typwechsel — das Partial
    liefert dafuer ``initial_data`` mit. Bei echtem Typwechsel bleibt das
    Feldset leer (E11).
    """

    @staticmethod
    def _target(request, entity: EntitySpec, facility, staff_user, fernet_key):
        doc_type = entity.doctype(request, "form")
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = entity.model.objects.create(
                facility=facility,
                created_by=staff_user,
                document_type=doc_type,
                data_json={
                    entity.field("primary"): entity.value("prefill_primary"),
                    entity.field("secondary"): "vertraulicher Klartext",
                },
                **entity.identity_kwargs(request, "Prefill-01"),
            )
        return target, doc_type

    def test_assigned_type_comes_back_prefilled(
        self, client, staff_user, facility, fernet_key, request, entity: EntitySpec
    ):
        target, doc_type = self._target(request, entity, facility, staff_user, fernet_key)
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.get(
                reverse(entity.url("fields_partial")),
                {"document_type": str(doc_type.pk), entity.name: str(target.pk)},
            )
        assert response.status_code == 200
        content = response.content.decode()
        assert entity.value("prefill_primary") in content
        assert "vertraulicher Klartext" in content
        assert "gAAAAA" not in content

    def test_real_type_switch_stays_empty(self, client, staff_user, facility, fernet_key, request, entity: EntitySpec):
        target, _ = self._target(request, entity, facility, staff_user, fernet_key)
        other_type = entity.doctype(request, "mixed")
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.get(
                reverse(entity.url("fields_partial")),
                {"document_type": str(other_type.pk), entity.name: str(target.pk)},
            )
        assert response.status_code == 200
        content = response.content.decode()
        assert f'name="{entity.field("mixed_visible")}"' in content
        assert entity.value("prefill_primary") not in content

    def test_without_object_param_stays_empty(
        self, client, staff_user, facility, fernet_key, request, entity: EntitySpec
    ):
        _, doc_type = self._target(request, entity, facility, staff_user, fernet_key)
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.get(
                reverse(entity.url("fields_partial")),
                {"document_type": str(doc_type.pk)},
            )
        assert response.status_code == 200
        assert entity.value("prefill_primary") not in response.content.decode()

    def test_restricted_field_value_does_not_leak(self, client, staff_user, facility, request, entity: EntitySpec):
        doc_type = entity.doctype(request, "mixed")
        target = entity.model.objects.create(
            facility=facility,
            created_by=staff_user,
            document_type=doc_type,
            data_json={
                entity.field("mixed_visible"): entity.value("mixed_initial"),
                entity.field("mixed_restricted"): "nur fuer Leitung",
            },
            **entity.identity_kwargs(request, "Prefill-02"),
        )
        client.force_login(staff_user)
        response = client.get(
            reverse(entity.url("fields_partial")),
            {"document_type": str(doc_type.pk), entity.name: str(target.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert f'value="{entity.value("mixed_initial")}"' in content
        assert f'name="{entity.field("mixed_restricted")}"' not in content
        assert "nur fuer Leitung" not in content

    def test_switch_away_and_back_then_submit_keeps_values(
        self, client, staff_user, facility, fernet_key, request, entity: EntitySpec
    ):
        target, own_type = self._target(request, entity, facility, staff_user, fernet_key)
        other_type = entity.doctype(request, "mixed")
        client.force_login(staff_user)
        partial = reverse(entity.url("fields_partial"))
        target_pk = str(target.pk)

        with override_settings(ENCRYPTION_KEY=fernet_key):
            away = client.get(partial, {"document_type": str(other_type.pk), entity.name: target_pk})
            assert away.status_code == 200

            back = client.get(partial, {"document_type": str(own_type.pk), entity.name: target_pk})
            assert back.status_code == 200
            posted = {slug: field.initial or "" for slug, field in back.context["data_form"].fields.items()}

            response = client.post(
                reverse(entity.url("update"), args=[target.pk]),
                entity.post_data(
                    request,
                    "Prefill-01",
                    document_type=str(own_type.pk),
                    **posted,
                ),
            )
            assert response.status_code == 302
            target.refresh_from_db()
            assert target.data_json[entity.field("primary")] == entity.value("prefill_primary")
            assert safe_decrypt(target.data_json[entity.field("secondary")]) == "vertraulicher Klartext"


# ---------------------------------------------------------------------------
# Detailansicht
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseDetailDynamicSection:
    def test_values_are_visible(self, client, staff_user, facility, client_identified, doc_type_case_form, fernet_key):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = Case.objects.create(
                facility=facility,
                client=client_identified,
                title="P8-Detail-01",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"anlass": "Mietschulden", "verlauf": "Detail-Klartext"},
            )
            client.force_login(staff_user)
            content = client.get(reverse("core:case_detail", args=[target.pk])).content.decode()
        assert "Zusatzfelder" in content
        assert "Anlass" in content
        assert "Mietschulden" in content
        assert "Detail-Klartext" in content

    def test_restricted_field_shows_placeholder(
        self, client, staff_user, facility, client_identified, doc_type_case_mixed
    ):
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Detail-02",
            created_by=staff_user,
            document_type=doc_type_case_mixed,
            data_json={"zielsetzung": "Wohnung", "risikoeinschaetzung": "nur fuer Leitung"},
        )
        client.force_login(staff_user)
        content = client.get(reverse("core:case_detail", args=[target.pk])).content.decode()
        assert "Eingeschränkt" in content
        assert "nur fuer Leitung" not in content

    def test_lead_sees_restricted_field(
        self, client, lead_user, staff_user, facility, client_identified, doc_type_case_mixed
    ):
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Detail-03",
            created_by=staff_user,
            document_type=doc_type_case_mixed,
            data_json={"zielsetzung": "Wohnung", "risikoeinschaetzung": "nur fuer Leitung"},
        )
        client.force_login(lead_user)
        content = client.get(reverse("core:case_detail", args=[target.pk])).content.decode()
        assert "nur fuer Leitung" in content

    def test_section_absent_without_type(self, client, staff_user, case_open):
        client.force_login(staff_user)
        content = client.get(reverse("core:case_detail", args=[case_open.pk])).content.decode()
        assert "Zusatzfelder" not in content

    def test_section_absent_when_doctype_not_visible(
        self, client, staff_user, facility, client_identified, doc_type_case_high
    ):
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Detail-04",
            created_by=staff_user,
            document_type=doc_type_case_high,
            data_json={"gefahrenlage": "streng vertraulich"},
        )
        client.force_login(staff_user)
        content = client.get(reverse("core:case_detail", args=[target.pk])).content.decode()
        assert "Zusatzfelder" not in content
        assert "streng vertraulich" not in content
        assert doc_type_case_high.name not in content


# ---------------------------------------------------------------------------
# Art.-15-Auskunftsexport (Fall-Dynamikdaten im Personen-Export)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseExportDynamicData:
    def test_export_contains_label_and_plaintext(
        self, facility, client_identified, staff_user, lead_user, doc_type_case_form, fernet_key
    ):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            Case.objects.create(
                facility=facility,
                client=client_identified,
                title="P8-Export-01",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"anlass": "Mietschulden", "verlauf": "Export-Klartext"},
            )
            data = export_client_data(client_identified, facility, lead_user)

        case_entry = data["cases"][0]
        assert case_entry["document_type"] == doc_type_case_form.name
        by_label = {entry["label"]: entry["value"] for entry in case_entry["dynamic_data"]}
        assert by_label["Anlass"] == "Mietschulden"
        assert by_label["Verlauf"] == "Export-Klartext"

    def test_export_without_type_carries_no_dynamic_keys(self, case_open, client_identified):
        """Ohne ``document_type`` erscheinen keine Dynamik-Keys im Fall-Dict.

        Refs #1752: Der Basis-Schluesselsatz ist per Produktentscheid vom
        2026-08-03 um ``updated_at`` und die Papierkorb-Metadaten gewachsen,
        mit dem Fall-Loeschweg (Refs #1818, ADR-044 P3a) um ``anonymized_at``.
        Die urspruengliche Aussage — ``dynamic_data``/``dynamic_data_orphaned``
        bleiben ohne Typ fern — bleibt unveraendert gepinnt.
        """
        result = _gather_cases(client_identified)[0]
        assert set(result.keys()) == {
            "title",
            "description",
            "status",
            "created_at",
            "updated_at",
            "closed_at",
            "lead_user",
            "created_by",
            "is_deleted",
            "deleted_at",
            "deleted_by",
            "anonymized_at",
        }

    def test_export_formats_select_and_boolean_values(
        self, facility, client_identified, staff_user, doc_type_case_select
    ):
        """Slugs/Booleans sind fuer die betroffene Person nicht lesbar — Labels ausweisen."""
        Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Export-03",
            created_by=staff_user,
            document_type=doc_type_case_select,
            data_json={"hilfeform": "ambulant", "kostenzusage": True},
        )
        entries = _gather_cases(client_identified)[0]["dynamic_data"]
        by_label = {entry["label"]: str(entry["value"]) for entry in entries}
        assert by_label["Hilfeform"] == "Ambulant"
        assert by_label["Kostenzusage"] == "Ja"

    def test_pdf_export_renders_case_dynamic_data(
        self, facility, client_identified, staff_user, lead_user, doc_type_case_form, fernet_key
    ):
        """Label UND Klartext-Wert muessen wirklich im gerenderten Dokument stehen."""
        from unittest.mock import patch

        import weasyprint

        from core.services.client import export_client_data_pdf

        captured: dict[str, str] = {}
        real_html = weasyprint.HTML

        def capturing_html(*args, **kwargs):
            captured["string"] = kwargs.get("string", "")
            return real_html(*args, **kwargs)

        with override_settings(ENCRYPTION_KEY=fernet_key):
            Case.objects.create(
                facility=facility,
                client=client_identified,
                title="P8-Export-02",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"anlass": "Mietschulden", "verlauf": "PDF-Klartext"},
            )
            with patch("core.services.client.export.weasyprint.HTML", side_effect=capturing_html):
                pdf_bytes = export_client_data_pdf(client_identified, facility, lead_user)

        assert pdf_bytes[:4] == b"%PDF"
        html = captured["string"]
        assert doc_type_case_form.name in html
        assert "Anlass" in html
        assert "Mietschulden" in html
        assert "Verlauf" in html
        assert "PDF-Klartext" in html


@pytest.mark.django_db
class TestCaseRerenderKeepsInactiveSelectOption:
    """Final-Review C (Refs #1058) — Spiegel zu
    ``TestRerenderKeepsInactiveSelectOption`` (Personen): der Re-Render nach
    einem ungueltigen ``CaseForm`` verlor die deaktivierte Bestands-Option."""

    @staticmethod
    def _deactivate_option(doc_type, slug="ambulant"):
        ft = FieldTemplate.objects.get(facility=doc_type.facility, name="Hilfeform")
        ft.options_json = [{**option, "is_active": option["slug"] != slug} for option in ft.options_json]
        ft.save(update_fields=["options_json"])
        return ft

    def test_rerender_keeps_deactivated_option_and_no_phantom_error(
        self, client, staff_user, facility, client_identified, doc_type_case_select
    ):
        ft = self._deactivate_option(doc_type_case_select)
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Option-01",
            created_by=staff_user,
            document_type=doc_type_case_select,
            data_json={ft.slug: "ambulant"},
        )
        client.force_login(staff_user)

        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "",  # leerer Titel -> ungueltig
                client_identified,
                document_type=str(doc_type_case_select.pk),
                **{ft.slug: "ambulant"},
            ),
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert 'value="ambulant"' in content, "Deaktivierte Bestands-Option fehlt im Re-Render-Select"
        assert "gültige Option" not in content and "gueltige Option" not in content

    def test_repeated_submit_after_rerender_keeps_value(
        self, client, staff_user, facility, client_identified, doc_type_case_select
    ):
        ft = self._deactivate_option(doc_type_case_select)
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Option-02",
            created_by=staff_user,
            document_type=doc_type_case_select,
            data_json={ft.slug: "ambulant"},
        )
        client.force_login(staff_user)

        rerender = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "",
                client_identified,
                document_type=str(doc_type_case_select.pk),
                **{ft.slug: "ambulant"},
            ),
        )
        resubmitted = _selected_option(rerender.content.decode(), ft.slug)

        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "P8-Option-02b",
                client_identified,
                document_type=str(doc_type_case_select.pk),
                **{ft.slug: resubmitted},
            ),
        )

        assert response.status_code == 302
        target.refresh_from_db()
        assert target.data_json[ft.slug] == "ambulant"
