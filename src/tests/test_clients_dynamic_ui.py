"""Client-UI fuer dynamische Felder: Anlage, Bearbeitung, Detail (M2 P7, Refs #1058).

P4b hat dem ``Client``-Aggregat ``document_type`` + ``data_json`` gegeben, P3 die
entity-neutrale :class:`~core.forms.dynamic.DynamicDataForm` samt
``services/dynamic_data``. P7 verdrahtet beides in die Personen-Oberflaeche —
nach dem erprobten Event-Muster (``views/events.py`` + ``EventFieldsPartialView``).

Abgedeckt:

* **Typ-Picker** — nur ``entity_type=client``-Typen der eigenen Einrichtung, und
  nur solche, deren Sensitivitaet die Rolle sehen darf.
* **Anlage/Bearbeitung** — Werte landen (verschluesselt) in ``data_json``,
  Update-GET zeigt Klartext, restricted-Felder ueberleben den Merge, ein
  Typwechsel verwirft Alt-Keys (E11).
* **Typ-Entfernen** — View sendet ``document_type=None`` + ``data_json={}``
  **gemeinsam**, sonst greift der fail-closed-Service-Vertrag (P4b-Review).
* **Partial** — AuthZ (STAFF_PLUS), Sensitivity-Gate (#774), Facility-Scope und
  ``entity_type``-Scope; Bedingungs-Payload landet im HTML.
* **Detail** — Werte sichtbar, ``[Eingeschraenkt]`` je Rolle, Sektion nur bei
  sichtbarem Typ.
* **Art. 15** — dynamische Felder entschluesselt + mit Label im Auskunftsexport.
"""

from __future__ import annotations

import json
import re

import pytest
from cryptography.fernet import Fernet
from django.contrib.messages import get_messages
from django.db import connection
from django.test import override_settings
from django.urls import reverse

from core.models import (
    Client,
    DocumentType,
    DocumentTypeField,
    FieldCondition,
    FieldTemplate,
)
from core.models.attachment import Attachment
from core.services.client import _gather_client_fields, export_client_data
from core.services.file_vault import is_encrypted_value, safe_decrypt


def raw_data_json(client_obj) -> dict:
    """``core_client.data_json`` direkt aus der DB-Zeile (Muster P4b)."""
    with connection.cursor() as cur:
        cur.execute("SELECT data_json FROM core_client WHERE id = %s", [str(client_obj.pk)])
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.fixture
def fernet_key():
    """Frischer Fernet-Key fuer die Verschluesselungs-Proben (Muster P4b)."""
    return Fernet.generate_key().decode()


@pytest.fixture
def doc_type_client_high(facility):
    """Personen-Typ mit HIGH-Sensitivitaet — fuer Staff unsichtbar (Rolle max ELEVATED)."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CLIENT,
        sensitivity=DocumentType.Sensitivity.HIGH,
        name="Personen-Hochsensibel",
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Schutzkonzept",
        field_type=FieldTemplate.FieldType.TEXTAREA,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
    return doc_type


@pytest.fixture
def doc_type_client_mixed(facility):
    """Personen-Typ (NORMAL) mit einem HIGH-Feld — Staff darf genau dieses Feld nicht sehen."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CLIENT,
        name="Personenakte",
    )
    ft_offen = FieldTemplate.objects.create(
        facility=facility,
        name="Sprache",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_geheim = FieldTemplate.objects.create(
        facility=facility,
        name="Gefaehrdung",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        sensitivity="high",
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_offen, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_geheim, sort_order=1)
    return doc_type


@pytest.fixture
def doc_type_client_select(facility):
    """Personen-Typ mit SELECT + BOOLEAN — fuer die Anzeige-/Export-Formatierung."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CLIENT,
        name="Personen-Auswahl",
    )
    ft_wohnform = FieldTemplate.objects.create(
        facility=facility,
        name="Wohnform",
        field_type=FieldTemplate.FieldType.SELECT,
        options_json=[
            {"slug": "wohnungslos", "label": "Wohnungslos", "is_active": True},
            {"slug": "eigene-wohnung", "label": "Eigene Wohnung", "is_active": True},
        ],
    )
    ft_einwilligung = FieldTemplate.objects.create(
        facility=facility,
        name="Einwilligung",
        field_type=FieldTemplate.FieldType.BOOLEAN,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_wohnform, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_einwilligung, sort_order=1)
    return doc_type


@pytest.fixture
def doc_type_client_conditional(facility):
    """Personen-Typ mit Bedingung TEXT->TEXTAREA (Payload-Smoke-Test)."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CLIENT,
        name="Personen-Bedingt",
    )
    ft_quelle = FieldTemplate.objects.create(
        facility=facility,
        name="Betreuung",
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
def doc_type_client_file_condition(facility):
    """Personen-Typ, dessen Bedingungs-QUELLE ein FILE-Feld ist (P3-Review-Nachzug).

    M24/AP4 (Refs #1738, ADR-039 §9): FILE-Felder sind an Personen-Typen
    regulaer — die frueher dreischichtige E8-Sperre ist gefallen. Diese
    Konfiguration ist damit kein Bestandsrest mehr, sondern ein gueltiges
    Setup; der Fixture pinnt, dass die Bedingung dadurch aktiv ist.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CLIENT,
        name="Personen-Dateibedingt",
    )
    ft_datei = FieldTemplate.objects.create(
        facility=facility,
        name="Nachweis",
        field_type=FieldTemplate.FieldType.FILE,
    )
    ft_ziel = FieldTemplate.objects.create(
        facility=facility,
        name="Nachweis-Erlaeuterung",
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


def _client_post_data(pseudonym, **extra):
    """Basis-POST-Body fuer ``ClientForm`` (Pflichtfelder + Ueberschreibungen)."""
    data = {
        "pseudonym": pseudonym,
        "contact_stage": Client.ContactStage.IDENTIFIED,
        "age_cluster": Client.AgeCluster.UNKNOWN,
        "notes": "",
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# Typ-Picker
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClientTypePicker:
    def test_only_client_types_of_own_facility(
        self,
        client,
        staff_user,
        doc_type_client_profile,
        doc_type_contact,
        foreign_doc_type_client,
    ):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_create"))
        assert response.status_code == 200
        content = response.content.decode()
        # Auf die Option-PKs pruefen: der Name „Kontakt" von ``doc_type_contact``
        # ist ein Teilstring der ContactStage-Labels und waere kein Beweis.
        assert f'value="{doc_type_client_profile.pk}"' in content
        assert doc_type_client_profile.name in content
        assert f'value="{doc_type_contact.pk}"' not in content
        assert f'value="{foreign_doc_type_client.pk}"' not in content
        assert foreign_doc_type_client.name not in content

    def test_high_sensitivity_type_hidden_for_staff(
        self, client, staff_user, doc_type_client_profile, doc_type_client_high
    ):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_create")).content.decode()
        assert doc_type_client_profile.name in content
        assert doc_type_client_high.name not in content

    def test_high_sensitivity_type_visible_for_lead(self, client, lead_user, doc_type_client_high):
        client.force_login(lead_user)
        content = client.get(reverse("core:client_create")).content.decode()
        assert doc_type_client_high.name in content

    def test_inactive_type_hidden(self, client, staff_user, doc_type_client_profile):
        doc_type_client_profile.is_active = False
        doc_type_client_profile.save(update_fields=["is_active"])
        client.force_login(staff_user)
        content = client.get(reverse("core:client_create")).content.decode()
        assert doc_type_client_profile.name not in content

    def test_create_form_has_no_type_switch_warning(self, client, staff_user, doc_type_client_profile):
        """Refs #1733: im Anlage-Formular kann nichts verloren gehen — der
        Hinweis erschien dort bislang trotzdem und war reines Rauschen."""
        client.force_login(staff_user)
        content = client.get(reverse("core:client_create")).content.decode()
        assert "doc-type-switch-hint" not in content
        assert "data-confirm-discard" not in content


# ---------------------------------------------------------------------------
# Typwechsel/-entfernen: Warnung + Rückfrage (Refs #1733)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClientTypeChangeConfirm:
    """Der Typwechsel ist die einzige irreversible Datenwirkung ohne Rückfrage
    gewesen (E11-Rebuild, ``merge_dynamic_data``). Warnung + bedingter Confirm
    erscheinen nur dort, wo tatsächlich etwas zu verlieren ist."""

    def _target(self, facility, staff_user, doc_type, data_json):
        return Client.objects.create(
            facility=facility,
            pseudonym="P7-Confirm",
            created_by=staff_user,
            document_type=doc_type,
            data_json=data_json,
        )

    def test_edit_with_values_carries_confirm_attributes_and_count(
        self, client, staff_user, facility, doc_type_client_profile, fernet_key
    ):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = self._target(
                facility, staff_user, doc_type_client_profile, {"wohnsituation": "WG", "diagnose": "Text"}
            )
            client.force_login(staff_user)
            content = client.get(reverse("core:client_update", args=[target.pk])).content.decode()
        assert 'x-data="confirmDynamicDataLoss"' in content
        assert 'data-confirm-discard="' in content
        # Der Confirm-Text nennt die Anzahl der betroffenen Felder.
        confirm = re.search(r'data-confirm-discard="([^"]*)"', content).group(1)
        assert "2" in confirm
        # Der Fall „Typ entfernen" wird ausdrücklich mitgenannt.
        assert "entfernen" in confirm.lower()

    def test_edit_without_values_has_no_confirm_and_no_warning(
        self, client, staff_user, facility, doc_type_client_profile
    ):
        target = self._target(facility, staff_user, doc_type_client_profile, {})
        client.force_login(staff_user)
        content = client.get(reverse("core:client_update", args=[target.pk])).content.decode()
        assert "data-confirm-discard" not in content
        assert "doc-type-switch-hint" not in content

    def test_edit_with_values_shows_alert_styled_warning(
        self, client, staff_user, facility, doc_type_client_profile, fernet_key
    ):
        """Option A: keine graue 12px-Zeile mehr, sondern Amber-Box mit
        ``role="alert"`` (Muster ``config/document_types/confirm_delete.html``)
        — und der Wortlaut deckt das Entfernen mit ab."""
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = self._target(facility, staff_user, doc_type_client_profile, {"wohnsituation": "WG"})
            client.force_login(staff_user)
            content = client.get(reverse("core:client_update", args=[target.pk])).content.decode()
        hint = re.search(r'<[^>]*data-testid="doc-type-switch-hint"[^>]*>(.*?)</div>', content, re.S)
        assert hint is not None, "Hinweis fehlt im Bearbeiten-Formular mit Werten"
        assert 'role="alert"' in hint.group(0)
        assert "amber" in hint.group(0)
        assert "entfern" in hint.group(1).lower()

    def test_locked_invisible_type_gets_no_confirm(
        self, client, lead_user, staff_user, facility, doc_type_client_high, fernet_key
    ):
        """Ist der Typ für die Rolle unsichtbar, entfernt die View das Select —
        ohne Wechselmöglichkeit gibt es auch nichts zu bestätigen."""
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = self._target(facility, staff_user, doc_type_client_high, {"schutzkonzept": "geheim"})
            client.force_login(staff_user)
            content = client.get(reverse("core:client_update", args=[target.pk])).content.decode()
        assert "data-confirm-discard" not in content


# ---------------------------------------------------------------------------
# Anlage
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClientCreateWithDynamicData:
    def test_create_persists_dynamic_values_encrypted(
        self, client, staff_user, facility, doc_type_client_profile, fernet_key
    ):
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.post(
                reverse("core:client_create"),
                _client_post_data(
                    "P7-Create-01",
                    document_type=str(doc_type_client_profile.pk),
                    wohnsituation="WG",
                    diagnose="Sehr sensibel",
                ),
            )
            assert response.status_code == 302
            created = Client.objects.get(pseudonym="P7-Create-01")
            assert created.document_type_id == doc_type_client_profile.pk
            row = raw_data_json(created)
            assert row["wohnsituation"] == "WG"
            assert is_encrypted_value(row["diagnose"])
            assert safe_decrypt(row["diagnose"]) == "Sehr sensibel"

    def test_create_without_type_keeps_data_json_empty(self, client, staff_user, doc_type_client_profile):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_create"),
            _client_post_data("P7-Create-02", wohnsituation="wird verworfen"),
        )
        assert response.status_code == 302
        created = Client.objects.get(pseudonym="P7-Create-02")
        assert created.document_type_id is None
        assert created.data_json == {}

    def test_create_rerender_on_invalid_meta_keeps_dynamic_fields(
        self, client, staff_user, client_identified, doc_type_client_profile
    ):
        """Pseudonym-Kollision -> Re-Render; die dynamischen Felder bleiben befuellt."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_create"),
            _client_post_data(
                client_identified.pseudonym,
                document_type=str(doc_type_client_profile.pk),
                wohnsituation="WG-Wert",
            ),
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Wohnsituation" in content
        assert "WG-Wert" in content

    def test_post_rerender_does_not_leak_restricted_doctype_labels(
        self, client, staff_user, client_identified, doc_type_client_high
    ):
        """#774-Spiegel: ein HIGH-Typ im POST darf keine Feldlabels ausspielen."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_create"),
            _client_post_data(
                client_identified.pseudonym,
                document_type=str(doc_type_client_high.pk),
                schutzkonzept="geheim",
            ),
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Schutzkonzept" not in content
        assert doc_type_client_high.name not in content


# ---------------------------------------------------------------------------
# Bearbeitung
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClientUpdateWithDynamicData:
    def test_update_get_prefills_decrypted_values(
        self, client, staff_user, facility, doc_type_client_profile, fernet_key
    ):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = Client.objects.create(
                facility=facility,
                pseudonym="P7-Update-01",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "WG", "diagnose": "Klartext-Probe"},
            )
            client.force_login(staff_user)
            content = client.get(reverse("core:client_update", args=[target.pk])).content.decode()
        assert "Klartext-Probe" in content
        assert "WG" in content

    def test_update_saves_changed_values(self, client, staff_user, facility, doc_type_client_profile, fernet_key):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = Client.objects.create(
                facility=facility,
                pseudonym="P7-Update-02",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "WG"},
            )
            client.force_login(staff_user)
            response = client.post(
                reverse("core:client_update", args=[target.pk]),
                _client_post_data(
                    "P7-Update-02",
                    document_type=str(doc_type_client_profile.pk),
                    wohnsituation="Eigene Wohnung",
                    diagnose="Neu",
                ),
            )
            assert response.status_code == 302
            target.refresh_from_db()
            assert target.data_json["wohnsituation"] == "Eigene Wohnung"
            assert safe_decrypt(target.data_json["diagnose"]) == "Neu"

    def test_restricted_field_hidden_and_preserved(self, client, staff_user, facility, doc_type_client_mixed):
        """Ein HIGH-Feld ist fuer Staff unsichtbar — und ueberlebt dessen Update.

        ``FieldTemplate.save()`` erzwingt bei ``sensitivity=high`` zusaetzlich
        ``is_encrypted=True`` (Refs #733/#1270) — der Alt-Wert liegt also als
        Fernet-Token im JSONB und wird von ``merge_dynamic_data`` als solcher
        nachgezogen (keine Doppelverschluesselung).
        """
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Update-03",
            created_by=staff_user,
            document_type=doc_type_client_mixed,
            data_json={"sprache": "DE", "gefaehrdung": "nur fuer Leitung"},
        )
        client.force_login(staff_user)
        content = client.get(reverse("core:client_update", args=[target.pk])).content.decode()
        assert "Sprache" in content
        assert "Gefaehrdung" not in content
        assert 'name="gefaehrdung"' not in content
        assert "nur fuer Leitung" not in content

        response = client.post(
            reverse("core:client_update", args=[target.pk]),
            _client_post_data(
                "P7-Update-03",
                document_type=str(doc_type_client_mixed.pk),
                sprache="EN",
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.data_json["sprache"] == "EN"
        assert safe_decrypt(target.data_json["gefaehrdung"]) == "nur fuer Leitung"

    def test_historical_restricted_number_outside_new_range_redirects_without_writing(
        self, client, staff_user, facility
    ):
        """Refs #1927: Der E11-Nachzug kann einen Feldfehler als dict auslösen.

        Der historische, für Staff unsichtbare Wert entstand vor der späteren
        Bereichsverschärfung. Sein Merge darf weder einen 500er verursachen
        noch Personendaten oder Anhänge verändern.
        """
        document_type = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.CLIENT,
            name="Personen-Zahlengrenze",
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
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Historische-Zahl",
            created_by=staff_user,
            document_type=document_type,
            data_json={number_field.slug: 0, visible_field.slug: "vorher"},
        )
        # Ein historischer Klartextwert kann den heutigen HIGH=>verschlüsselt-
        # Backstop umgangen haben. Die nachträglich gesetzte Untergrenze macht
        # den vom Merge erneut validierten Wert ungültig.
        Client.objects.filter(pk=target.pk).update(data_json={number_field.slug: -1, visible_field.slug: "vorher"})
        number_field.min_value = 0
        number_field.save(update_fields=["min_value"])

        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", args=[target.pk]),
            _client_post_data(
                "P7-Neuer-Pseudonym",
                document_type=str(document_type.pk),
                **{visible_field.slug: "nachher"},
            ),
        )

        assert response.status_code == 302
        assert response.url == reverse("core:client_update", args=[target.pk])
        assert [str(message) for message in get_messages(response.wsgi_request)] == ["Wert muss mindestens 0 sein."]
        target.refresh_from_db()
        assert target.pseudonym == "P7-Historische-Zahl"
        assert target.data_json == {number_field.slug: -1, visible_field.slug: "vorher"}
        assert Attachment.objects.filter(client=target).count() == 0

    def test_type_switch_drops_old_keys(
        self, client, staff_user, facility, doc_type_client_profile, doc_type_client_mixed
    ):
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Update-04",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG"},
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", args=[target.pk]),
            _client_post_data(
                "P7-Update-04",
                document_type=str(doc_type_client_mixed.pk),
                sprache="DE",
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id == doc_type_client_mixed.pk
        assert "wohnsituation" not in target.data_json
        assert target.data_json["sprache"] == "DE"

    def test_removing_type_clears_data_json_without_error(self, client, staff_user, facility, doc_type_client_profile):
        """P4b-Review: ``document_type=None`` + befuelltes ``data_json`` ist fail-closed.

        Die View MUSS das Paar gemeinsam senden — sonst wirft der Service
        ``ValidationError`` und der Benutzer bekaeme einen Fehler statt eines
        geleerten Datensatzes.
        """
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Update-05",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG"},
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", args=[target.pk]),
            _client_post_data("P7-Update-05", document_type=""),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id is None
        assert target.data_json == {}


# ---------------------------------------------------------------------------
# Deaktivierter, aber zugewiesener Dokumentationstyp (P7-Fix-Runde)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInactiveAssignedDocumentType:
    """Ein zwischenzeitlich deaktivierter Typ darf beim Update nicht still wegfallen.

    Ohne die Queryset-Ausnahme (Muster #1160, ``_select_choices``) faellt der
    zugewiesene Typ aus der Auswahl, das Select zeigt „— Kein Typ —"
    vorausgewaehlt, und ein voellig normaler Submit loescht Typ UND alle
    (potenziell Art.-9-nahen) Zusatzfeld-Werte, ohne dass die Bearbeiterin es
    bemerkt.
    """

    @staticmethod
    def _deactivate(doc_type):
        doc_type.is_active = False
        doc_type.save(update_fields=["is_active"])
        return doc_type

    def test_update_get_preselects_inactive_assigned_type(self, client, staff_user, facility, doc_type_client_profile):
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Inaktiv-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG"},
        )
        self._deactivate(doc_type_client_profile)
        client.force_login(staff_user)
        content = client.get(reverse("core:client_update", args=[target.pk])).content.decode()

        assert f'<option value="{doc_type_client_profile.pk}" selected>' in content
        assert "(deaktiviert)" in content
        # Die Zusatzfelder des inaktiven Typs bleiben bearbeitbar.
        assert 'name="wohnsituation"' in content
        assert "WG" in content

    def test_unchanged_submit_keeps_inactive_type_and_values(
        self, client, staff_user, facility, doc_type_client_profile
    ):
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Inaktiv-02",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG"},
        )
        self._deactivate(doc_type_client_profile)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", args=[target.pk]),
            _client_post_data(
                "P7-Inaktiv-02",
                document_type=str(doc_type_client_profile.pk),
                wohnsituation="WG",
            ),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id == doc_type_client_profile.pk
        assert target.data_json["wohnsituation"] == "WG"

    def test_create_still_hides_inactive_types(self, client, staff_user, doc_type_client_profile):
        """Die Ausnahme gilt NUR fuer den bearbeiteten Datensatz — Neuanlagen nicht."""
        self._deactivate(doc_type_client_profile)
        client.force_login(staff_user)
        content = client.get(reverse("core:client_create")).content.decode()
        assert f'value="{doc_type_client_profile.pk}"' not in content

        # Und ein untergeschobener POST wird abgelehnt (Queryset-Validierung).
        response = client.post(
            reverse("core:client_create"),
            _client_post_data(
                "P7-Inaktiv-03",
                document_type=str(doc_type_client_profile.pk),
                wohnsituation="WG",
            ),
        )
        assert response.status_code == 200
        assert not Client.objects.filter(pseudonym="P7-Inaktiv-03").exists()

    def test_deliberate_removal_still_works(self, client, staff_user, facility, doc_type_client_profile):
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Inaktiv-04",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG"},
        )
        self._deactivate(doc_type_client_profile)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", args=[target.pk]),
            _client_post_data("P7-Inaktiv-04", document_type=""),
        )
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.document_type_id is None
        assert target.data_json == {}

    def test_invalid_rerender_keeps_inactive_type_fields(
        self, client, staff_user, facility, client_identified, doc_type_client_profile
    ):
        """Auch der Re-Render eines ungueltigen Formulars behaelt die Feldwerte."""
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Inaktiv-05",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG"},
        )
        self._deactivate(doc_type_client_profile)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", args=[target.pk]),
            _client_post_data(
                client_identified.pseudonym,  # Pseudonym-Kollision -> ungueltig
                document_type=str(doc_type_client_profile.pk),
                wohnsituation="Eigene Wohnung",
            ),
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="wohnsituation"' in content
        assert "Eigene Wohnung" in content

    def test_invisible_type_lock_beats_inactive_exception(self, client, staff_user, facility, doc_type_client_high):
        """Inaktiv UND unsichtbar: die Lock-Logik hat Vorrang, das Feld faellt ganz weg."""
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Inaktiv-06",
            created_by=staff_user,
            document_type=doc_type_client_high,
            data_json={"schutzkonzept": "streng vertraulich"},
        )
        self._deactivate(doc_type_client_high)
        client.force_login(staff_user)
        content = client.get(reverse("core:client_update", args=[target.pk])).content.decode()
        assert 'name="document_type"' not in content
        assert doc_type_client_high.name not in content
        assert "streng vertraulich" not in content


# ---------------------------------------------------------------------------
# Sperre bei unsichtbarem Dokumentationstyp (P7-Fix-Runde)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvisibleDocumentTypeLock:
    """``_lock_invisible_document_type`` — vier Richtungen, alle fixiert.

    Darf die Rolle den Typ der Person nicht sehen, faellt das Typ-Select ganz
    aus dem Formular. Sonst wuerde (a) der Typname geleakt und (b) ein Submit
    mit leerem Feld Typ und alle unsichtbaren Werte still loeschen.
    """

    @pytest.fixture
    def locked_client(self, facility, staff_user, doc_type_client_high):
        return Client.objects.create(
            facility=facility,
            pseudonym="P7-Lock-01",
            created_by=staff_user,
            document_type=doc_type_client_high,
            data_json={"schutzkonzept": "streng vertraulich"},
        )

    def test_get_leaks_nothing(self, client, staff_user, locked_client, doc_type_client_high):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_update", args=[locked_client.pk])).content.decode()
        assert 'name="document_type"' not in content
        assert doc_type_client_high.name not in content
        assert "Schutzkonzept" not in content
        assert "streng vertraulich" not in content

    def test_normal_post_keeps_type_and_hidden_values(self, client, staff_user, locked_client, doc_type_client_high):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", args=[locked_client.pk]),
            _client_post_data("P7-Lock-01", notes="Neue Notiz"),
        )
        assert response.status_code == 302
        locked_client.refresh_from_db()
        assert locked_client.notes == "Neue Notiz"
        assert locked_client.document_type_id == doc_type_client_high.pk
        assert safe_decrypt(locked_client.data_json["schutzkonzept"]) == "streng vertraulich"

    def test_smuggled_visible_type_is_ignored(
        self, client, staff_user, locked_client, doc_type_client_high, doc_type_client_profile
    ):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_update", args=[locked_client.pk]),
            _client_post_data(
                "P7-Lock-01",
                document_type=str(doc_type_client_profile.pk),
                wohnsituation="untergeschoben",
            ),
        )
        assert response.status_code == 302
        locked_client.refresh_from_db()
        assert locked_client.document_type_id == doc_type_client_high.pk
        assert "wohnsituation" not in locked_client.data_json
        assert safe_decrypt(locked_client.data_json["schutzkonzept"]) == "streng vertraulich"

    def test_authorized_role_is_unaffected(self, client, lead_user, locked_client, doc_type_client_high):
        client.force_login(lead_user)
        content = client.get(reverse("core:client_update", args=[locked_client.pk])).content.decode()
        assert 'name="document_type"' in content
        assert doc_type_client_high.name in content
        assert "Schutzkonzept" in content
        assert "streng vertraulich" in content

        response = client.post(
            reverse("core:client_update", args=[locked_client.pk]),
            _client_post_data(
                "P7-Lock-01",
                document_type=str(doc_type_client_high.pk),
                schutzkonzept="von der Leitung geaendert",
            ),
        )
        assert response.status_code == 302
        locked_client.refresh_from_db()
        assert safe_decrypt(locked_client.data_json["schutzkonzept"]) == "von der Leitung geaendert"


# ---------------------------------------------------------------------------
# HTMX-Partial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClientFieldsPartial:
    def test_staff_gets_fields(self, client, staff_user, doc_type_client_profile):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_profile.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Wohnsituation" in content
        assert "Diagnose" in content

    def test_assistant_is_denied(self, client, assistant_user, doc_type_client_profile):
        client.force_login(assistant_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_profile.pk)},
        )
        assert response.status_code in (403, 302)
        if response.status_code == 302:
            assert "/login" in response["Location"]

    def test_without_param_renders_empty(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_fields_partial"))
        assert response.status_code == 200

    def test_restricted_sensitivity_blocked(self, client, staff_user, doc_type_client_high):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_high.pk)},
        )
        assert response.status_code == 403

    def test_foreign_facility_type_is_404(self, client, staff_user, foreign_doc_type_client):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(foreign_doc_type_client.pk)},
        )
        assert response.status_code == 404

    def test_event_type_is_404(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_contact.pk)},
        )
        assert response.status_code == 404

    def test_inactive_type_is_404(self, client, staff_user, doc_type_client_profile):
        """Ohne ``client``-Parameter bleibt das Verhalten unveraendert (Fall d)."""
        doc_type_client_profile.is_active = False
        doc_type_client_profile.save(update_fields=["is_active"])
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_profile.pk)},
        )
        assert response.status_code == 404

    def test_malformed_pk_is_404(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_fields_partial"), {"document_type": "keine-uuid"})
        assert response.status_code == 404

    def test_conditions_payload_lands_in_html(self, client, staff_user, doc_type_client_conditional):
        """M1-Bedingungen greifen auf der Personen-Seite ohne Zusatzarbeit."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_conditional.pk)},
        )
        content = response.content.decode()
        assert 'id="field-conditions"' in content
        assert "betreuungsdetails" in content
        assert "is_not_empty" in content

    def test_file_source_condition_is_active(self, client, staff_user, facility, doc_type_client_file_condition):
        """Anforderungsaenderung M24/AP4 (Refs #1738, ADR-039 §9).

        **Vorher (E8):** die Bedingungs-Quelle war ein per
        ``exclude_file_fields`` uebersprungenes FILE-Feld. Die Bedingung fiel
        deshalb der Drop-Regel (#774) zum Opfer, und das Ziel wurde **still
        unbedingt sichtbar und pflichtig** — akzeptiert, weil ein FILE-Feld an
        einem Personen-Typ nur als Bestands-Konfigurationsrest entstehen
        konnte.

        **Jetzt:** Datei-Felder sind an Personen-Typen regulaer (eigene
        Owner-FK, Download-Route, Versionskette). Die Quelle liegt im Feldset,
        die Bedingung wird ausgeliefert, und das Ziel ist ein echtes
        Bedingungsziel: die Pflicht greift nur, wenn die Quelle gefuellt ist.
        Der Sonderfall ist damit ersatzlos entfallen.
        """
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_file_condition.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        # FILE-Feld wird gebaut ...
        assert 'name="nachweis"' in content
        # ... die Bedingung wird ausgeliefert ...
        assert 'id="field-conditions"' in content
        # ... und das Ziel ist ein Bedingungsziel.
        assert 'name="nachweis-erlaeuterung"' in content

        form = response.context["data_form"]
        assert "nachweis" in form.fields
        assert form.fields["nachweis-erlaeuterung"].required is False
        assert form.condition_target_slugs == {"nachweis-erlaeuterung"}
        # ``facility`` ist Pflicht: ``DynamicDataForm.clean()`` steigt ohne sie
        # frueh aus und liesse die konditionale Pflichtpruefung aus. Ohne
        # gewaehlte Datei ist die Quelle leer -> Ziel unsichtbar -> keine Pflicht.
        bound = form.__class__(
            {"nachweis-erlaeuterung": ""},
            document_type=doc_type_client_file_condition,
            facility=facility,
        )
        assert bound.is_valid(), bound.errors


# ---------------------------------------------------------------------------
# Partial + inaktiver Ist-Typ der bearbeiteten Person (P7-Fix-Runde 2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPartialAcceptsAssignedInactiveType:
    """``?client=<pk>`` erlaubt genau den bereits zugewiesenen (inaktiven) Typ.

    Ohne diese Symmetrie zum ``ClientForm``-Queryset entsteht ein **stiller
    Datenverlust**: Bearbeiterin wechselt im Select weg vom inaktiven Ist-Typ
    (HTMX laedt die Felder des anderen Typs), wechselt zurueck ⇒ 404 ⇒ HTMX
    swapt bei 4xx NICHT ⇒ im DOM stehen weiter die Felder des ANDEREN Typs ⇒
    der (voellig plausible) Submit traegt die Ist-Slugs nicht ⇒
    ``merge_dynamic_data`` zieht nur ``restricted_slugs`` nach und leert die
    Bestandswerte.
    """

    @pytest.fixture
    def client_with_inactive_type(self, facility, staff_user, doc_type_client_profile):
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Partial-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG"},
        )
        doc_type_client_profile.is_active = False
        doc_type_client_profile.save(update_fields=["is_active"])
        return target

    def test_assigned_inactive_type_is_served(
        self, client, staff_user, client_with_inactive_type, doc_type_client_profile
    ):
        """(a) mit ``client``-Param + inaktivem Ist-Typ ⇒ 200 mit dessen Feldern."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_profile.pk), "client": str(client_with_inactive_type.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="wohnsituation"' in content
        assert 'name="diagnose"' in content

    def test_other_inactive_type_stays_404(self, client, staff_user, client_with_inactive_type, doc_type_client_mixed):
        """(b) ein inaktiver, aber NICHT zugewiesener Typ bleibt trotz Param 404."""
        doc_type_client_mixed.is_active = False
        doc_type_client_mixed.save(update_fields=["is_active"])
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_mixed.pk), "client": str(client_with_inactive_type.pk)},
        )
        assert response.status_code == 404

    def test_foreign_client_pk_is_404(self, client, staff_user, doc_type_client_profile, foreign_client):
        """(c) fremde Personen-PK ⇒ 404 (facility-gescopter Lookup, kein IDOR-Fenster)."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_profile.pk), "client": str(foreign_client.pk)},
        )
        assert response.status_code == 404

    def test_malformed_client_pk_is_404(self, client, staff_user, doc_type_client_profile):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_profile.pk), "client": "keine-uuid"},
        )
        assert response.status_code == 404

    def test_active_type_unaffected_by_param(self, client, staff_user, client_identified, doc_type_client_profile):
        """Der Param lockert nichts fuer aktive Typen — er ist nur eine Ausnahme."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_profile.pk), "client": str(client_identified.pk)},
        )
        assert response.status_code == 200

    def test_invisible_assigned_type_still_403(self, client, staff_user, facility, doc_type_client_high):
        """Sensitivity bleibt hart: der Param hebelt das 403-Gate nicht aus."""
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Partial-02",
            created_by=staff_user,
            document_type=doc_type_client_high,
            data_json={"schutzkonzept": "streng vertraulich"},
        )
        doc_type_client_high.is_active = False
        doc_type_client_high.save(update_fields=["is_active"])
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_high.pk), "client": str(target.pk)},
        )
        assert response.status_code == 403

    def test_update_form_hx_get_carries_client_param(self, client, staff_user, client_with_inactive_type):
        """Ohne den Param im ``hx-get`` schickt der Browser ihn nie mit."""
        client.force_login(staff_user)
        content = client.get(reverse("core:client_update", args=[client_with_inactive_type.pk])).content.decode()
        expected = f"{reverse('core:client_fields_partial')}?client={client_with_inactive_type.pk}"
        assert f'hx-get="{expected}"' in content

    def test_create_form_hx_get_has_no_client_param(self, client, staff_user, doc_type_client_profile):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_create")).content.decode()
        assert f'hx-get="{reverse("core:client_fields_partial")}"' in content
        assert "?client=" not in content

    def test_switch_away_and_back_returns_own_fields(
        self, client, staff_user, client_with_inactive_type, doc_type_client_profile, doc_type_client_mixed
    ):
        """(e) Charakterisierung des Ausgangsszenarios auf View-Ebene."""
        client.force_login(staff_user)
        partial = reverse("core:client_fields_partial")
        client_pk = str(client_with_inactive_type.pk)

        # 1. Weg vom Ist-Typ auf einen anderen (aktiven) Typ.
        away = client.get(partial, {"document_type": str(doc_type_client_mixed.pk), "client": client_pk})
        assert away.status_code == 200
        assert 'name="sprache"' in away.content.decode()

        # 2. Zurueck auf den inaktiven Ist-Typ — vor dem Fix ein 404, bei dem
        #    HTMX den DOM unveraendert liess.
        back = client.get(partial, {"document_type": str(doc_type_client_profile.pk), "client": client_pk})
        assert back.status_code == 200
        back_content = back.content.decode()
        assert 'name="wohnsituation"' in back_content
        assert 'name="sprache"' not in back_content

        # 3. Und der darauf folgende Submit erhaelt die Bestandswerte.
        response = client.post(
            reverse("core:client_update", args=[client_with_inactive_type.pk]),
            _client_post_data(
                "P7-Partial-01",
                document_type=str(doc_type_client_profile.pk),
                wohnsituation="WG",
            ),
        )
        assert response.status_code == 302
        client_with_inactive_type.refresh_from_db()
        assert client_with_inactive_type.document_type_id == doc_type_client_profile.pk
        assert client_with_inactive_type.data_json["wohnsituation"] == "WG"


# ---------------------------------------------------------------------------
# Partial + Rueckweg auf den bereits zugewiesenen Typ (Refs #1758)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPartialPrefillsAssignedType:
    """Der Rueckweg auf den gespeicherten Typ ist KEIN Typwechsel.

    Bislang rendert das Partial immer ein leeres Feldset. Wechselt man im
    Select weg und wieder zurueck, kommen die Felder des Ist-Typs leer zurueck
    — der anschliessende, voellig plausible Submit ueberschreibt die
    Bestandswerte mit Leerstrings. Der ``#1733``-Confirm haengt am erfolgten
    Nachladen und warnt zwar, behebt die Ursache aber nicht.

    Entscheid: ``initial_data`` genau dann mitliefern, wenn der angefragte Typ
    dem am Objekt gespeicherten entspricht. Bei echtem Typwechsel bleibt das
    Feldset leer (E11-Policy „Typwechsel verwirft" unveraendert).
    """

    @pytest.fixture
    def client_with_values(self, facility, staff_user, doc_type_client_profile, fernet_key):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            yield Client.objects.create(
                facility=facility,
                pseudonym="P7-Prefill-01",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "WG im Nordviertel", "diagnose": "vertraulicher Klartext"},
            )

    def test_assigned_type_comes_back_prefilled(
        self, client, staff_user, client_with_values, doc_type_client_profile, fernet_key
    ):
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.get(
                reverse("core:client_fields_partial"),
                {"document_type": str(doc_type_client_profile.pk), "client": str(client_with_values.pk)},
            )
        assert response.status_code == 200
        content = response.content.decode()
        assert "WG im Nordviertel" in content
        # Verschluesselte Slugs kommen entschluesselt zurueck (Muster
        # ``ClientUpdateView.get``) — nie als Fernet-Token.
        assert "vertraulicher Klartext" in content
        assert "gAAAAA" not in content

    def test_real_type_switch_stays_empty(
        self, client, staff_user, client_with_values, doc_type_client_mixed, fernet_key
    ):
        """E11 bleibt: ein echter Wechsel liefert weiterhin ein leeres Feldset."""
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.get(
                reverse("core:client_fields_partial"),
                {"document_type": str(doc_type_client_mixed.pk), "client": str(client_with_values.pk)},
            )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="sprache"' in content
        assert "WG im Nordviertel" not in content

    def test_without_client_param_stays_empty(
        self, client, staff_user, client_with_values, doc_type_client_profile, fernet_key
    ):
        """Anlage-Formular/Config-Vorschau haben kein Objekt — nichts vorbefuellen."""
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.get(
                reverse("core:client_fields_partial"),
                {"document_type": str(doc_type_client_profile.pk)},
            )
        assert response.status_code == 200
        assert "WG im Nordviertel" not in response.content.decode()

    def test_restricted_field_value_does_not_leak(self, client, staff_user, facility, doc_type_client_mixed):
        """Sensitivity-Grenze bleibt hart: das HIGH-Feld wird entfernt, sein Wert erscheint nie."""
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Prefill-02",
            created_by=staff_user,
            document_type=doc_type_client_mixed,
            data_json={"sprache": "DE", "gefaehrdung": "nur fuer Leitung"},
        )
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_fields_partial"),
            {"document_type": str(doc_type_client_mixed.pk), "client": str(target.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'value="DE"' in content
        assert 'name="gefaehrdung"' not in content
        assert "nur fuer Leitung" not in content

    def test_trashed_client_is_404_and_never_prefills(
        self, client, staff_user, admin_user, client_with_values, doc_type_client_profile, fernet_key
    ):
        """Papierkorb-Grenze (Refs #1737-Konvention): soft-geloescht ⇒ 404, kein Klartext.

        Der ``?client=``-Lookup ist der einzige Personen-Pfad, der den
        ``is_deleted=False``-Scope nicht setzte (Detail ``clients.py:206``,
        Bearbeiten ``:342``/``:362`` setzen ihn). Solange das Partial nur
        ``document_type_id`` las, war das folgenlos — mit dem Prefill wuerde es
        zum Lesepfad auf die ENTSCHLUESSELTEN Zusatzfeld-Werte einer Person, die
        Detail und Bearbeiten mit 404 verweigern. Einen legitimen Aufrufer gibt
        es nicht: das Bearbeiten-Formular, das den Parameter setzt, ist fuer
        Papierkorb-Personen unerreichbar.
        """
        client_with_values.soft_delete(user=admin_user)
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.get(
                reverse("core:client_fields_partial"),
                {"document_type": str(doc_type_client_profile.pk), "client": str(client_with_values.pk)},
            )
        assert response.status_code == 404
        assert "WG im Nordviertel" not in response.content.decode()
        assert "vertraulicher Klartext" not in response.content.decode()

    def test_restored_client_prefills_again(
        self, client, staff_user, admin_user, client_with_values, doc_type_client_profile, fernet_key
    ):
        """Gegenprobe: nach dem Wiederherstellen greift der Rueckweg-Prefill wieder."""
        client_with_values.soft_delete(user=admin_user)
        client_with_values.restore()
        client.force_login(staff_user)
        with override_settings(ENCRYPTION_KEY=fernet_key):
            response = client.get(
                reverse("core:client_fields_partial"),
                {"document_type": str(doc_type_client_profile.pk), "client": str(client_with_values.pk)},
            )
        assert response.status_code == 200
        assert "WG im Nordviertel" in response.content.decode()

    def test_switch_away_and_back_then_submit_keeps_values(
        self, client, staff_user, client_with_values, doc_type_client_profile, doc_type_client_mixed, fernet_key
    ):
        """Regression: der Rueckweg + unveraenderter Submit verliert nichts mehr.

        Der Submit traegt genau das, was das zurueckgeholte Feldset rendert —
        vor dem Fix waren das Leerstrings.
        """
        client.force_login(staff_user)
        partial = reverse("core:client_fields_partial")
        client_pk = str(client_with_values.pk)

        with override_settings(ENCRYPTION_KEY=fernet_key):
            away = client.get(partial, {"document_type": str(doc_type_client_mixed.pk), "client": client_pk})
            assert away.status_code == 200

            back = client.get(partial, {"document_type": str(doc_type_client_profile.pk), "client": client_pk})
            assert back.status_code == 200
            back_form = back.context["data_form"]
            posted = {slug: field.initial or "" for slug, field in back_form.fields.items()}

            response = client.post(
                reverse("core:client_update", args=[client_with_values.pk]),
                _client_post_data(
                    "P7-Prefill-01",
                    document_type=str(doc_type_client_profile.pk),
                    **posted,
                ),
            )
            assert response.status_code == 302
            client_with_values.refresh_from_db()
            assert client_with_values.data_json["wohnsituation"] == "WG im Nordviertel"
            assert safe_decrypt(client_with_values.data_json["diagnose"]) == "vertraulicher Klartext"


# ---------------------------------------------------------------------------
# Detailansicht
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClientDetailDynamicSection:
    def test_values_are_visible(self, client, staff_user, facility, doc_type_client_profile, fernet_key):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = Client.objects.create(
                facility=facility,
                pseudonym="P7-Detail-01",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "WG", "diagnose": "Detail-Klartext"},
            )
            client.force_login(staff_user)
            content = client.get(reverse("core:client_detail", args=[target.pk])).content.decode()
        assert "Zusatzfelder" in content
        assert "Wohnsituation" in content
        assert "WG" in content
        assert "Detail-Klartext" in content

    def test_restricted_field_shows_placeholder(self, client, staff_user, facility, doc_type_client_mixed):
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Detail-02",
            created_by=staff_user,
            document_type=doc_type_client_mixed,
            data_json={"sprache": "DE", "gefaehrdung": "nur fuer Leitung"},
        )
        client.force_login(staff_user)
        content = client.get(reverse("core:client_detail", args=[target.pk])).content.decode()
        assert "Eingeschränkt" in content
        assert "nur fuer Leitung" not in content

    def test_lead_sees_restricted_field(self, client, lead_user, facility, staff_user, doc_type_client_mixed):
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Detail-03",
            created_by=staff_user,
            document_type=doc_type_client_mixed,
            data_json={"sprache": "DE", "gefaehrdung": "nur fuer Leitung"},
        )
        client.force_login(lead_user)
        content = client.get(reverse("core:client_detail", args=[target.pk])).content.decode()
        assert "nur fuer Leitung" in content

    def test_section_absent_without_type(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()
        assert "Zusatzfelder" not in content

    def test_section_absent_when_doctype_not_visible(self, client, staff_user, facility, doc_type_client_high):
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Detail-04",
            created_by=staff_user,
            document_type=doc_type_client_high,
            data_json={"schutzkonzept": "streng vertraulich"},
        )
        client.force_login(staff_user)
        content = client.get(reverse("core:client_detail", args=[target.pk])).content.decode()
        assert "Zusatzfelder" not in content
        assert "streng vertraulich" not in content
        assert doc_type_client_high.name not in content


# ---------------------------------------------------------------------------
# Art.-15-Auskunftsexport
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClientExportDynamicData:
    def test_export_contains_label_and_plaintext(
        self, facility, staff_user, lead_user, doc_type_client_profile, fernet_key
    ):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = Client.objects.create(
                facility=facility,
                pseudonym="P7-Export-01",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "WG", "diagnose": "Export-Klartext"},
            )
            data = export_client_data(target, facility, lead_user)

        assert data["client"]["document_type"] == doc_type_client_profile.name
        entries = data["client"]["dynamic_data"]
        by_label = {entry["label"]: entry["value"] for entry in entries}
        assert by_label["Wohnsituation"] == "WG"
        assert by_label["Diagnose"] == "Export-Klartext"

    def test_export_without_type_carries_no_dynamic_keys(self, client_identified):
        """Ohne ``document_type`` erscheinen keine Dynamik-Keys im Stammdaten-Dict.

        Refs #1752: Der Basis-Schluesselsatz ist per Produktentscheid vom
        2026-08-03 um ``notes``, die Papierkorb-/Anonymisierungs-Metadaten und
        ``updated_at`` gewachsen. Die urspruengliche Aussage des Tests —
        ``dynamic_data``/``dynamic_data_orphaned`` bleiben ohne Typ fern —
        bleibt unveraendert gepinnt.
        """
        result = _gather_client_fields(client_identified)
        assert set(result.keys()) == {
            "pseudonym",
            "contact_stage",
            "age_cluster",
            "notes",
            "is_active",
            "k_anonymized",
            "created_at",
            "updated_at",
            "created_by",
            "is_deleted",
            "deleted_at",
            "deleted_by",
        }

    def test_export_formats_select_and_boolean_values(self, facility, staff_user, doc_type_client_select):
        """Slugs/Booleans sind fuer die betroffene Person nicht lesbar — Labels ausweisen."""
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Export-03",
            created_by=staff_user,
            document_type=doc_type_client_select,
            data_json={"wohnform": "wohnungslos", "einwilligung": True},
        )
        entries = _gather_client_fields(target)["dynamic_data"]
        by_label = {entry["label"]: str(entry["value"]) for entry in entries}
        assert by_label["Wohnform"] == "Wohnungslos"
        assert by_label["Einwilligung"] == "Ja"

    def test_pdf_export_renders_dynamic_data(
        self, facility, staff_user, lead_user, doc_type_client_profile, fernet_key
    ):
        """Label UND Klartext-Wert muessen wirklich im gerenderten Dokument stehen.

        Statt nur die ``%PDF``-Magic zu pruefen (die auch ein leeres Dokument
        traegt), wird das HTML abgefangen, das WeasyPrint zu sehen bekommt.
        """
        from unittest.mock import patch

        import weasyprint

        from core.services.client import export_client_data_pdf

        captured: dict[str, str] = {}
        real_html = weasyprint.HTML

        def capturing_html(*args, **kwargs):
            captured["string"] = kwargs.get("string", "")
            return real_html(*args, **kwargs)

        with override_settings(ENCRYPTION_KEY=fernet_key):
            target = Client.objects.create(
                facility=facility,
                pseudonym="P7-Export-02",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "WG", "diagnose": "PDF-Klartext"},
            )
            with patch("core.services.client.export.weasyprint.HTML", side_effect=capturing_html):
                pdf_bytes = export_client_data_pdf(target, facility, lead_user)

        assert pdf_bytes[:4] == b"%PDF"
        html = captured["string"]
        assert doc_type_client_profile.name in html
        assert "Wohnsituation" in html
        assert "WG" in html
        assert "Diagnose" in html
        assert "PDF-Klartext" in html


@pytest.mark.django_db
class TestRerenderKeepsInactiveSelectOption:
    """Final-Review C (Refs #1058): Der Re-Render nach einem ungueltigen
    ``ClientForm`` baute das Dynamik-Formular ohne ``initial_data`` — eine
    deaktivierte SELECT-Option, die der Datensatz weiterhin traegt, fiel damit
    aus den Choices. Folge: Scheinfehler „Waehlen Sie eine gueltige Option" an
    einem unberuehrten Feld und, nach Korrektur des eigentlichen Fehlers,
    stiller Verlust des Bestandswerts (``merge_dynamic_data`` baut das Ergebnis
    komplett aus ``cleaned_data``)."""

    @staticmethod
    def _deactivate_option(doc_type, slug="wohnungslos"):
        ft = FieldTemplate.objects.get(facility=doc_type.facility, name="Wohnform")
        ft.options_json = [{**option, "is_active": option["slug"] != slug} for option in ft.options_json]
        ft.save(update_fields=["options_json"])
        return ft

    def test_rerender_keeps_deactivated_option_and_no_phantom_error(
        self, client, staff_user, facility, client_identified, doc_type_client_select
    ):
        ft = self._deactivate_option(doc_type_client_select)
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Option-01",
            created_by=staff_user,
            document_type=doc_type_client_select,
            data_json={ft.slug: "wohnungslos"},
        )
        client.force_login(staff_user)

        response = client.post(
            reverse("core:client_update", args=[target.pk]),
            _client_post_data(
                client_identified.pseudonym,  # Pseudonym-Kollision -> ungueltig
                document_type=str(doc_type_client_select.pk),
                **{ft.slug: "wohnungslos"},
            ),
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert 'value="wohnungslos"' in content, "Deaktivierte Bestands-Option fehlt im Re-Render-Select"
        assert "gültige Option" not in content and "gueltige Option" not in content

    def test_repeated_submit_after_rerender_keeps_value(
        self, client, staff_user, facility, client_identified, doc_type_client_select
    ):
        """Zweiter Submit (nur das Pseudonym korrigiert) darf den Bestandswert
        nicht stillschweigend leeren.

        Der zweite POST postet exakt das, was ein Browser aus dem re-gerenderten
        Formular schicken wuerde (= die im Select vorausgewaehlte Option) — ohne
        die Option im Select ist das die leere Auswahl, und
        ``merge_dynamic_data`` schreibt den Bestandswert auf ``""``."""
        ft = self._deactivate_option(doc_type_client_select)
        target = Client.objects.create(
            facility=facility,
            pseudonym="P7-Option-02",
            created_by=staff_user,
            document_type=doc_type_client_select,
            data_json={ft.slug: "wohnungslos"},
        )
        client.force_login(staff_user)

        rerender = client.post(
            reverse("core:client_update", args=[target.pk]),
            _client_post_data(
                client_identified.pseudonym,
                document_type=str(doc_type_client_select.pk),
                **{ft.slug: "wohnungslos"},
            ),
        )
        resubmitted = _selected_option(rerender.content.decode(), ft.slug)

        response = client.post(
            reverse("core:client_update", args=[target.pk]),
            _client_post_data(
                "P7-Option-02b",
                document_type=str(doc_type_client_select.pk),
                **{ft.slug: resubmitted},
            ),
        )

        assert response.status_code == 302
        target.refresh_from_db()
        assert target.data_json[ft.slug] == "wohnungslos"
