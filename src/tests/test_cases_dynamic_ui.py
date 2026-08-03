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

import pytest
from cryptography.fernet import Fernet
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
from core.services.client import _gather_cases, export_client_data
from core.services.file_vault import is_encrypted_value, safe_decrypt


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
        category=DocumentType.Category.ADMIN,
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
        category=DocumentType.Category.ADMIN,
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
        category=DocumentType.Category.ADMIN,
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
        category=DocumentType.Category.ADMIN,
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

    FILE ist fuer Fall-Typen dreifach ausgeschlossen (Config-UI, Service,
    ``exclude_file_fields``) — diese Konfiguration kann nur als Bestandsrest
    entstehen. Der Fixture stellt sie kuenstlich her, damit das dokumentierte
    Verhalten (Drop-Regel) fixiert ist.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.ADMIN,
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
class TestCaseTypePicker:
    def test_only_case_types_of_own_facility(
        self,
        client,
        staff_user,
        doc_type_case_form,
        doc_type_client_profile,
        doc_type_contact,
        foreign_doc_type_case,
    ):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_create"))
        assert response.status_code == 200
        content = response.content.decode()
        # Auf die Option-PKs pruefen (Muster P7/E-4): Typnamen koennen als
        # Teilstring anderer Labels auftauchen und waeren kein Beweis.
        assert f'value="{doc_type_case_form.pk}"' in content
        assert doc_type_case_form.name in content
        assert f'value="{doc_type_client_profile.pk}"' not in content
        assert f'value="{doc_type_contact.pk}"' not in content
        assert f'value="{foreign_doc_type_case.pk}"' not in content
        assert foreign_doc_type_case.name not in content

    def test_high_sensitivity_type_hidden_for_staff(self, client, staff_user, doc_type_case_form, doc_type_case_high):
        client.force_login(staff_user)
        content = client.get(reverse("core:case_create")).content.decode()
        assert doc_type_case_form.name in content
        assert doc_type_case_high.name not in content

    def test_high_sensitivity_type_visible_for_lead(self, client, lead_user, doc_type_case_high):
        client.force_login(lead_user)
        content = client.get(reverse("core:case_create")).content.decode()
        assert doc_type_case_high.name in content

    def test_inactive_type_hidden(self, client, staff_user, doc_type_case_form):
        doc_type_case_form.is_active = False
        doc_type_case_form.save(update_fields=["is_active"])
        client.force_login(staff_user)
        content = client.get(reverse("core:case_create")).content.decode()
        assert doc_type_case_form.name not in content

    def test_form_shows_type_switch_warning(self, client, staff_user, doc_type_case_form):
        client.force_login(staff_user)
        content = client.get(reverse("core:case_create")).content.decode()
        assert "Zusatzfeld-Werte verworfen" in content


# ---------------------------------------------------------------------------
# Anlage
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseCreateWithDynamicData:
    def test_create_persists_dynamic_values_encrypted(
        self, client, staff_user, client_identified, doc_type_case_form, fernet_key
    ):
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.post(
                reverse("core:case_create"),
                _case_post_data(
                    "P8-Create-01",
                    client_identified,
                    document_type=str(doc_type_case_form.pk),
                    anlass="Mietschulden",
                    verlauf="Sehr sensibel",
                ),
            )
            assert response.status_code == 302
            created = Case.objects.get(title="P8-Create-01")
            assert created.document_type_id == doc_type_case_form.pk
            row = raw_data_json(created)
            assert row["anlass"] == "Mietschulden"
            assert is_encrypted_value(row["verlauf"])
            assert safe_decrypt(row["verlauf"]) == "Sehr sensibel"

    def test_create_without_type_keeps_data_json_empty(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_create"),
            _case_post_data("P8-Create-02", client_identified, anlass="wird verworfen"),
        )
        assert response.status_code == 302
        created = Case.objects.get(title="P8-Create-02")
        assert created.document_type_id is None
        assert created.data_json == {}

    def test_create_rerender_on_invalid_meta_keeps_dynamic_fields(
        self, client, staff_user, client_identified, doc_type_case_form
    ):
        """Leerer Titel -> Re-Render; die dynamischen Felder bleiben befuellt."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_create"),
            _case_post_data(
                "",
                client_identified,
                document_type=str(doc_type_case_form.pk),
                anlass="Anlass-Wert",
            ),
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Anlass" in content
        assert "Anlass-Wert" in content

    def test_post_rerender_does_not_leak_restricted_doctype_labels(
        self, client, staff_user, client_identified, doc_type_case_high
    ):
        """#774-Spiegel: ein HIGH-Typ im POST darf keine Feldlabels ausspielen."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_create"),
            _case_post_data(
                "",
                client_identified,
                document_type=str(doc_type_case_high.pk),
                gefahrenlage="geheim",
            ),
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Gefahrenlage" not in content
        assert doc_type_case_high.name not in content


# ---------------------------------------------------------------------------
# Bearbeitung
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseUpdateWithDynamicData:
    def test_update_get_prefills_decrypted_values(
        self, client, staff_user, facility, client_identified, doc_type_case_form, fernet_key
    ):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = Case.objects.create(
                facility=facility,
                client=client_identified,
                title="P8-Update-01",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"anlass": "Mietschulden", "verlauf": "Klartext-Probe"},
            )
            client.force_login(staff_user)
            content = client.get(reverse("core:case_update", args=[target.pk])).content.decode()
        assert "Klartext-Probe" in content
        assert "Mietschulden" in content

    def test_update_saves_changed_values(
        self, client, staff_user, facility, client_identified, doc_type_case_form, fernet_key
    ):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = Case.objects.create(
                facility=facility,
                client=client_identified,
                title="P8-Update-02",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"anlass": "Mietschulden"},
            )
            client.force_login(staff_user)
            response = client.post(
                reverse("core:case_update", args=[target.pk]),
                _case_post_data(
                    "P8-Update-02",
                    client_identified,
                    document_type=str(doc_type_case_form.pk),
                    anlass="Wohnungsverlust",
                    verlauf="Neu",
                ),
            )
            assert response.status_code == 302
            target.refresh_from_db()
            assert target.data_json["anlass"] == "Wohnungsverlust"
            assert safe_decrypt(target.data_json["verlauf"]) == "Neu"

    def test_restricted_field_hidden_and_preserved(
        self, client, staff_user, facility, client_identified, doc_type_case_mixed
    ):
        """Ein HIGH-Feld ist fuer Staff unsichtbar — und ueberlebt dessen Update.

        ``FieldTemplate.save()`` erzwingt bei ``sensitivity=high`` zusaetzlich
        ``is_encrypted=True`` (Refs #733/#1270) — der Alt-Wert liegt also als
        Fernet-Token im JSONB und wird von ``merge_dynamic_data`` als solcher
        nachgezogen (keine Doppelverschluesselung).
        """
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Update-03",
            created_by=staff_user,
            document_type=doc_type_case_mixed,
            data_json={"zielsetzung": "Wohnung", "risikoeinschaetzung": "nur fuer Leitung"},
        )
        client.force_login(staff_user)
        content = client.get(reverse("core:case_update", args=[target.pk])).content.decode()
        assert "Zielsetzung" in content
        assert "Risikoeinschaetzung" not in content
        assert 'name="risikoeinschaetzung"' not in content
        assert "nur fuer Leitung" not in content

        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "P8-Update-03",
                client_identified,
                document_type=str(doc_type_case_mixed.pk),
                zielsetzung="Arbeit",
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.data_json["zielsetzung"] == "Arbeit"
        assert safe_decrypt(target.data_json["risikoeinschaetzung"]) == "nur fuer Leitung"

    def test_type_switch_drops_old_keys(
        self, client, staff_user, facility, client_identified, doc_type_case_form, doc_type_case_mixed
    ):
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Update-04",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden"},
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "P8-Update-04",
                client_identified,
                document_type=str(doc_type_case_mixed.pk),
                zielsetzung="Wohnung",
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id == doc_type_case_mixed.pk
        assert "anlass" not in target.data_json
        assert target.data_json["zielsetzung"] == "Wohnung"

    def test_removing_type_clears_data_json_without_error(
        self, client, staff_user, facility, client_identified, doc_type_case_form
    ):
        """P5/P4b-Review: ``document_type=None`` + befuelltes ``data_json`` ist fail-closed.

        Die View MUSS das Paar gemeinsam senden — sonst wirft der Service
        ``ValidationError`` und der Benutzer bekaeme einen Fehler statt eines
        geleerten Datensatzes.
        """
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Update-05",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden"},
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data("P8-Update-05", client_identified, document_type=""),
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
            title="P8-",
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "P8--geaendert",
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
class TestInactiveAssignedDocumentType:
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

    def test_update_get_preselects_inactive_assigned_type(
        self, client, staff_user, facility, client_identified, doc_type_case_form
    ):
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Inaktiv-01",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden"},
        )
        self._deactivate(doc_type_case_form)
        client.force_login(staff_user)
        content = client.get(reverse("core:case_update", args=[target.pk])).content.decode()

        assert f'<option value="{doc_type_case_form.pk}" selected>' in content
        assert "(deaktiviert)" in content
        # Die Zusatzfelder des inaktiven Typs bleiben bearbeitbar.
        assert 'name="anlass"' in content
        assert "Mietschulden" in content

    def test_unchanged_submit_keeps_inactive_type_and_values(
        self, client, staff_user, facility, client_identified, doc_type_case_form
    ):
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Inaktiv-02",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden"},
        )
        self._deactivate(doc_type_case_form)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "P8-Inaktiv-02",
                client_identified,
                document_type=str(doc_type_case_form.pk),
                anlass="Mietschulden",
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id == doc_type_case_form.pk
        assert target.data_json["anlass"] == "Mietschulden"

    def test_create_still_hides_inactive_types(self, client, staff_user, client_identified, doc_type_case_form):
        """Die Ausnahme gilt NUR fuer den bearbeiteten Datensatz — Neuanlagen nicht."""
        self._deactivate(doc_type_case_form)
        client.force_login(staff_user)
        content = client.get(reverse("core:case_create")).content.decode()
        assert f'value="{doc_type_case_form.pk}"' not in content

        # Und ein untergeschobener POST wird abgelehnt (Queryset-Validierung).
        response = client.post(
            reverse("core:case_create"),
            _case_post_data(
                "P8-Inaktiv-03",
                client_identified,
                document_type=str(doc_type_case_form.pk),
                anlass="Mietschulden",
            ),
        )
        assert response.status_code == 200
        assert not Case.objects.filter(title="P8-Inaktiv-03").exists()

    def test_deliberate_removal_still_works(self, client, staff_user, facility, client_identified, doc_type_case_form):
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Inaktiv-04",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden"},
        )
        self._deactivate(doc_type_case_form)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data("P8-Inaktiv-04", client_identified, document_type=""),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id is None
        assert target.data_json == {}

    def test_invalid_rerender_keeps_inactive_type_fields(
        self, client, staff_user, facility, client_identified, doc_type_case_form
    ):
        """Auch der Re-Render eines ungueltigen Formulars behaelt die Feldwerte."""
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Inaktiv-05",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden"},
        )
        self._deactivate(doc_type_case_form)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[target.pk]),
            _case_post_data(
                "",  # leerer Titel -> ungueltig
                client_identified,
                document_type=str(doc_type_case_form.pk),
                anlass="Wohnungsverlust",
            ),
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="anlass"' in content
        assert "Wohnungsverlust" in content

    def test_invisible_type_lock_beats_inactive_exception(
        self, client, staff_user, facility, client_identified, doc_type_case_high
    ):
        """Inaktiv UND unsichtbar: die Lock-Logik hat Vorrang, das Feld faellt ganz weg."""
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Inaktiv-06",
            created_by=staff_user,
            document_type=doc_type_case_high,
            data_json={"gefahrenlage": "streng vertraulich"},
        )
        self._deactivate(doc_type_case_high)
        client.force_login(staff_user)
        content = client.get(reverse("core:case_update", args=[target.pk])).content.decode()
        assert 'name="document_type"' not in content
        assert doc_type_case_high.name not in content
        assert "streng vertraulich" not in content


# ---------------------------------------------------------------------------
# Sperre bei unsichtbarem Dokumentationstyp (E-2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvisibleDocumentTypeLock:
    """``_lock_invisible_document_type`` — vier Richtungen, alle fixiert.

    Darf die Rolle den Typ des Falls nicht sehen, faellt das Typ-Select ganz aus
    dem Formular. Sonst wuerde (a) der Typname geleakt und (b) ein Submit mit
    leerem Feld Typ und alle unsichtbaren Werte still loeschen.
    """

    @pytest.fixture
    def locked_case(self, facility, client_identified, staff_user, doc_type_case_high):
        return Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Lock-Typ-01",
            created_by=staff_user,
            document_type=doc_type_case_high,
            data_json={"gefahrenlage": "streng vertraulich"},
        )

    def test_get_leaks_nothing(self, client, staff_user, locked_case, doc_type_case_high):
        client.force_login(staff_user)
        content = client.get(reverse("core:case_update", args=[locked_case.pk])).content.decode()
        assert 'name="document_type"' not in content
        assert doc_type_case_high.name not in content
        assert "Gefahrenlage" not in content
        assert "streng vertraulich" not in content

    def test_normal_post_keeps_type_and_hidden_values(
        self, client, staff_user, client_identified, locked_case, doc_type_case_high
    ):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[locked_case.pk]),
            _case_post_data("P8-Lock-Typ-01", client_identified, description="Neue Beschreibung"),
        )
        assert response.status_code == 302
        locked_case.refresh_from_db()
        assert locked_case.description == "Neue Beschreibung"
        assert locked_case.document_type_id == doc_type_case_high.pk
        assert safe_decrypt(locked_case.data_json["gefahrenlage"]) == "streng vertraulich"

    def test_smuggled_visible_type_is_ignored(
        self, client, staff_user, client_identified, locked_case, doc_type_case_high, doc_type_case_form
    ):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[locked_case.pk]),
            _case_post_data(
                "P8-Lock-Typ-01",
                client_identified,
                document_type=str(doc_type_case_form.pk),
                anlass="untergeschoben",
            ),
        )
        assert response.status_code == 302
        locked_case.refresh_from_db()
        assert locked_case.document_type_id == doc_type_case_high.pk
        assert "anlass" not in locked_case.data_json
        assert safe_decrypt(locked_case.data_json["gefahrenlage"]) == "streng vertraulich"

    def test_authorized_role_is_unaffected(self, client, lead_user, client_identified, locked_case, doc_type_case_high):
        client.force_login(lead_user)
        content = client.get(reverse("core:case_update", args=[locked_case.pk])).content.decode()
        assert 'name="document_type"' in content
        assert doc_type_case_high.name in content
        assert "Gefahrenlage" in content
        assert "streng vertraulich" in content

        response = client.post(
            reverse("core:case_update", args=[locked_case.pk]),
            _case_post_data(
                "P8-Lock-Typ-01",
                client_identified,
                document_type=str(doc_type_case_high.pk),
                gefahrenlage="von der Leitung geaendert",
            ),
        )
        assert response.status_code == 302
        locked_case.refresh_from_db()
        assert safe_decrypt(locked_case.data_json["gefahrenlage"]) == "von der Leitung geaendert"


# ---------------------------------------------------------------------------
# HTMX-Partial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseFieldsPartial:
    def test_staff_gets_fields(self, client, staff_user, doc_type_case_form):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_form.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Anlass" in content
        assert "Verlauf" in content

    def test_assistant_is_denied(self, client, assistant_user, doc_type_case_form):
        client.force_login(assistant_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_form.pk)},
        )
        assert response.status_code in (403, 302)
        if response.status_code == 302:
            assert "/login" in response["Location"]

    def test_without_param_renders_empty(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_fields_partial"))
        assert response.status_code == 200

    def test_restricted_sensitivity_blocked(self, client, staff_user, doc_type_case_high):
        """E-9: Sensitivity-Gate antwortet 403 (Event-/Personen-Muster), nicht 404."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_high.pk)},
        )
        assert response.status_code == 403

    def test_foreign_facility_type_is_404(self, client, staff_user, foreign_doc_type_case):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(foreign_doc_type_case.pk)},
        )
        assert response.status_code == 404

    def test_client_type_is_404(self, client, staff_user, doc_type_client_profile):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_client_profile.pk)},
        )
        assert response.status_code == 404

    def test_event_type_is_404(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_contact.pk)},
        )
        assert response.status_code == 404

    def test_inactive_type_is_404(self, client, staff_user, doc_type_case_form):
        """Ohne ``case``-Parameter bleibt das Verhalten unveraendert (Fall d)."""
        doc_type_case_form.is_active = False
        doc_type_case_form.save(update_fields=["is_active"])
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_form.pk)},
        )
        assert response.status_code == 404

    def test_malformed_pk_is_404(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_fields_partial"), {"document_type": "keine-uuid"})
        assert response.status_code == 404

    def test_conditions_payload_lands_in_html(self, client, staff_user, doc_type_case_conditional):
        """M1-Bedingungen greifen auf der Fall-Seite ohne Zusatzarbeit."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_conditional.pk)},
        )
        content = response.content.decode()
        assert 'id="field-conditions"' in content
        assert "betreuungsdetails" in content
        assert "is_not_empty" in content

    def test_file_source_condition_target_is_unconditional_and_required(
        self, client, staff_user, facility, doc_type_case_file_condition
    ):
        """P3-Review-Nachzug: Quelle ist ein per ``exclude_file_fields`` geskipptes FILE-Feld.

        Dokumentiertes (akzeptiertes) Verhalten: die Bedingung faellt der
        Drop-Regel (E8/#774) zum Opfer, weil ihre Quelle nicht mehr im Feldset
        liegt. Das Ziel wird damit **still unbedingt sichtbar** und behaelt
        seine Pflicht (``_enforce_conditional_required`` setzt ohne Payload
        ``visible = alle Felder``). Akzeptiert, weil FILE fuer Fall-Typen
        dreischichtig ausgeschlossen ist — der Fall entsteht nur aus
        Bestands-Konfigurationsresten, und „sichtbar + pflichtig" ist die
        fail-closed-Richtung.
        """
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_file_condition.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="kostenbescheid"' not in content
        assert 'id="field-conditions"' not in content
        assert 'name="bescheid-erlaeuterung"' in content

        form = response.context["data_form"]
        assert "kostenbescheid" not in form.fields
        assert form.fields["bescheid-erlaeuterung"].required is False
        assert form.condition_target_slugs == set()
        # ``facility`` ist Pflicht: ``DynamicDataForm.clean()`` steigt ohne sie
        # frueh aus und liesse die konditionale Pflichtpruefung aus.
        bound = form.__class__(
            {"bescheid-erlaeuterung": ""},
            document_type=doc_type_case_file_condition,
            facility=facility,
        )
        assert not bound.is_valid()
        assert "bescheid-erlaeuterung" in bound.errors


# ---------------------------------------------------------------------------
# Partial + inaktiver Ist-Typ des bearbeiteten Falls (P7-Fix-Runde 2, E-12)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPartialAcceptsAssignedInactiveType:
    """``?case=<pk>`` erlaubt genau den bereits zugewiesenen (inaktiven) Typ.

    Ohne diese Symmetrie zum ``CaseForm``-Queryset entsteht ein **stiller
    Datenverlust**: Bearbeiterin wechselt im Select weg vom inaktiven Ist-Typ
    (HTMX laedt die Felder des anderen Typs), wechselt zurueck ⇒ 404 ⇒ HTMX
    swapt bei 4xx NICHT ⇒ im DOM stehen weiter die Felder des ANDEREN Typs ⇒
    der (voellig plausible) Submit traegt die Ist-Slugs nicht ⇒
    ``merge_dynamic_data`` zieht nur ``restricted_slugs`` nach und leert die
    Bestandswerte.
    """

    @pytest.fixture
    def case_with_inactive_type(self, facility, client_identified, staff_user, doc_type_case_form):
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Partial-01",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden"},
        )
        doc_type_case_form.is_active = False
        doc_type_case_form.save(update_fields=["is_active"])
        return target

    def test_assigned_inactive_type_is_served(self, client, staff_user, case_with_inactive_type, doc_type_case_form):
        """(a) mit ``case``-Param + inaktivem Ist-Typ ⇒ 200 mit dessen Feldern."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_form.pk), "case": str(case_with_inactive_type.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="anlass"' in content
        assert 'name="verlauf"' in content

    def test_other_inactive_type_stays_404(self, client, staff_user, case_with_inactive_type, doc_type_case_mixed):
        """(b) ein inaktiver, aber NICHT zugewiesener Typ bleibt trotz Param 404."""
        doc_type_case_mixed.is_active = False
        doc_type_case_mixed.save(update_fields=["is_active"])
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_mixed.pk), "case": str(case_with_inactive_type.pk)},
        )
        assert response.status_code == 404

    def test_foreign_case_pk_is_404(self, client, staff_user, doc_type_case_form, foreign_case):
        """(c) fremde Fall-PK ⇒ 404 (facility-gescopter Lookup, kein IDOR-Fenster)."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_form.pk), "case": str(foreign_case.pk)},
        )
        assert response.status_code == 404

    def test_malformed_case_pk_is_404(self, client, staff_user, doc_type_case_form):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_form.pk), "case": "keine-uuid"},
        )
        assert response.status_code == 404

    def test_active_type_unaffected_by_param(self, client, staff_user, case_open, doc_type_case_form):
        """Der Param lockert nichts fuer aktive Typen — er ist nur eine Ausnahme."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_form.pk), "case": str(case_open.pk)},
        )
        assert response.status_code == 200

    def test_invisible_assigned_type_still_403(
        self, client, staff_user, facility, client_identified, doc_type_case_high
    ):
        """Sensitivity bleibt hart: der Param hebelt das 403-Gate nicht aus."""
        target = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P8-Partial-02",
            created_by=staff_user,
            document_type=doc_type_case_high,
            data_json={"gefahrenlage": "streng vertraulich"},
        )
        doc_type_case_high.is_active = False
        doc_type_case_high.save(update_fields=["is_active"])
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_fields_partial"),
            {"document_type": str(doc_type_case_high.pk), "case": str(target.pk)},
        )
        assert response.status_code == 403

    def test_update_form_hx_get_carries_case_param(self, client, staff_user, case_with_inactive_type):
        """Ohne den Param im ``hx-get`` schickt der Browser ihn nie mit."""
        client.force_login(staff_user)
        content = client.get(reverse("core:case_update", args=[case_with_inactive_type.pk])).content.decode()
        expected = f"{reverse('core:case_fields_partial')}?case={case_with_inactive_type.pk}"
        assert f'hx-get="{expected}"' in content

    def test_create_form_hx_get_has_no_case_param(self, client, staff_user, doc_type_case_form):
        """M0-Falle: ``Case.id`` traegt einen UUID-Default, ``pk`` allein
        unterscheidet Anlage und Bearbeitung nicht (``_state.adding``, E-13)."""
        client.force_login(staff_user)
        content = client.get(reverse("core:case_create")).content.decode()
        assert f'hx-get="{reverse("core:case_fields_partial")}"' in content
        assert "?case=" not in content

    def test_switch_away_and_back_returns_own_fields(
        self, client, staff_user, client_identified, case_with_inactive_type, doc_type_case_form, doc_type_case_mixed
    ):
        """(e) Charakterisierung des Ausgangsszenarios auf View-Ebene."""
        client.force_login(staff_user)
        partial = reverse("core:case_fields_partial")
        case_pk = str(case_with_inactive_type.pk)

        # 1. Weg vom Ist-Typ auf einen anderen (aktiven) Typ.
        away = client.get(partial, {"document_type": str(doc_type_case_mixed.pk), "case": case_pk})
        assert away.status_code == 200
        assert 'name="zielsetzung"' in away.content.decode()

        # 2. Zurueck auf den inaktiven Ist-Typ — ohne die Ausnahme ein 404, bei
        #    dem HTMX den DOM unveraendert liesse.
        back = client.get(partial, {"document_type": str(doc_type_case_form.pk), "case": case_pk})
        assert back.status_code == 200
        back_content = back.content.decode()
        assert 'name="anlass"' in back_content
        assert 'name="zielsetzung"' not in back_content

        # 3. Und der darauf folgende Submit erhaelt die Bestandswerte.
        response = client.post(
            reverse("core:case_update", args=[case_with_inactive_type.pk]),
            _case_post_data(
                "P8-Partial-01",
                client_identified,
                document_type=str(doc_type_case_form.pk),
                anlass="Mietschulden",
            ),
        )
        assert response.status_code == 302
        case_with_inactive_type.refresh_from_db()
        assert case_with_inactive_type.document_type_id == doc_type_case_form.pk
        assert case_with_inactive_type.data_json["anlass"] == "Mietschulden"


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

    def test_export_without_type_is_unchanged(self, case_open, client_identified):
        result = _gather_cases(client_identified)[0]
        assert set(result.keys()) == {
            "title",
            "description",
            "status",
            "created_at",
            "closed_at",
            "lead_user",
            "created_by",
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
