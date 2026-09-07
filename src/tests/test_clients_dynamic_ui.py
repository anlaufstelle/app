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
# Typwechsel/-entfernen: Warnung + Rückfrage (Refs #1733)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Anlage
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Bearbeitung
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Deaktivierter, aber zugewiesener Dokumentationstyp (P7-Fix-Runde)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Sperre bei unsichtbarem Dokumentationstyp (P7-Fix-Runde)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HTMX-Partial
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Partial + inaktiver Ist-Typ der bearbeiteten Person (P7-Fix-Runde 2)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Detailansicht
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Art.-15-Auskunftsexport
# ---------------------------------------------------------------------------


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
