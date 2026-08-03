"""Client-Dynamikdaten: ``document_type`` + ``data_json`` (M2 P4b, Refs #1058).

P4b gibt dem ``Client``-Aggregat dieselbe dynamische Feldmechanik, die das
``Event`` schon hat: einen ``DocumentType``-FK (``entity_type=client``) und ein
``data_json``-JSONField, dessen als ``is_encrypted`` markierte Slugs beim
``save()`` Fernet-verschluesselt werden (:class:`DynamicDataEncryptionMixin`,
P4a).

Abgedeckt:

* **Verschluesselung** — der encrypted-Slug liegt als Token in der DB-Zeile
  (Raw-Query, nicht ueber das ORM-Objekt), der normale Slug im Klartext;
  Roundtrip via ``safe_decrypt``.
* **Service-Guard** — Cross-Facility-Typ und ``entity_type=event``-Typ werden
  mit deutscher ``ValidationError`` abgewiesen (create + update).
* **Whitelist** — ``validate_data_json`` verwirft Slugs, die nicht am Typ
  haengen; der Service muss den *Rueckgabewert* verwenden.
* **Anonymisierung** — ``anonymize_client`` und ``k_anonymize_client`` leeren
  ``data_json`` und loesen den ``document_type``-PROTECT-Verweis.
* **Audit** — ``changed_fields`` nennt ``data_json``/``document_type``, das
  Detail traegt aber keine Werte.
* **PROTECT** — ein von einer Person referenzierter Typ ist nicht loeschbar.
"""

import json

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import override_settings

from core.models import AuditLog, Client, DocumentType
from core.services.client import anonymize_client, create_client, update_client
from core.services.compliance import k_anonymize_client
from core.services.file_vault import is_encrypted_value, safe_decrypt


def raw_data_json(client) -> dict:
    """``core_client.data_json`` direkt aus der DB-Zeile (ohne ORM-Cache).

    Der Treiber liefert JSONB als rohen Text zurueck (kein automatisches
    Deserialisieren wie im ORM) — genau das ist hier gewuenscht: die Probe soll
    den *gespeicherten* Inhalt sehen, nicht die vom Modell aufbereitete Sicht.
    """
    with connection.cursor() as cur:
        cur.execute("SELECT data_json FROM core_client WHERE id = %s", [str(client.pk)])
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.fixture
def fernet_key():
    """Frischer Fernet-Key fuer die Verschluesselungs-Proben.

    Die Test-Settings erben ``ENCRYPTION_KEY`` aus der Umgebung — die
    Krypto-Tests setzen ihn deshalb explizit (Muster P4a,
    ``test_encrypt_dynamic_data.py``).
    """
    return Fernet.generate_key().decode()


@pytest.mark.django_db
class TestClientDataJsonEncryption:
    """``Client.save()`` verschluesselt die als encrypted markierten Slugs."""

    def test_encrypted_slug_is_ciphertext_in_db(self, facility, doc_type_client_profile, staff_user, fernet_key):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            client = Client.objects.create(
                facility=facility,
                pseudonym="P4b-Krypto-01",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "WG", "diagnose": "Sehr sensibel"},
            )

            row = raw_data_json(client)
            assert is_encrypted_value(row["diagnose"]), "encrypted-Slug liegt im Klartext in der DB"
            assert "Sehr sensibel" not in str(row["diagnose"])
            assert safe_decrypt(row["diagnose"]) == "Sehr sensibel"

    def test_plain_slug_stays_plaintext(self, facility, doc_type_client_profile, staff_user, fernet_key):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            client = Client.objects.create(
                facility=facility,
                pseudonym="P4b-Krypto-02",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "WG", "diagnose": "Sehr sensibel"},
            )

            assert raw_data_json(client)["wohnsituation"] == "WG"

    def test_resave_does_not_double_encrypt(self, facility, doc_type_client_profile, staff_user, fernet_key):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            client = Client.objects.create(
                facility=facility,
                pseudonym="P4b-Krypto-03",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"diagnose": "Einmal"},
            )
            client.notes = "geaendert"
            client.save()

            assert safe_decrypt(raw_data_json(client)["diagnose"]) == "Einmal"

    def test_create_client_service_encrypts_too(self, facility, doc_type_client_profile, staff_user, fernet_key):
        """Der Service-Pfad haengt am selben ``save()`` — kein zweiter Krypto-Ort."""
        with override_settings(ENCRYPTION_KEY=fernet_key):
            client = create_client(
                facility,
                staff_user,
                pseudonym="P4b-Krypto-04",
                document_type=doc_type_client_profile,
                data_json={"diagnose": "Service-Pfad"},
            )

            assert is_encrypted_value(raw_data_json(client)["diagnose"])


@pytest.mark.django_db
class TestClientDocumentTypeGuard:
    """Der Service akzeptiert nur Typen der eigenen Einrichtung mit ``entity_type=client``."""

    def test_create_rejects_foreign_facility_type(self, facility, foreign_doc_type_client, staff_user):
        with pytest.raises(ValidationError, match="Einrichtung"):
            create_client(
                facility,
                staff_user,
                pseudonym="P4b-Guard-01",
                document_type=foreign_doc_type_client,
            )

    def test_create_rejects_event_entity_type(self, facility, doc_type_contact, staff_user):
        assert doc_type_contact.entity_type == DocumentType.EntityType.EVENT
        with pytest.raises(ValidationError, match="Personen"):
            create_client(
                facility,
                staff_user,
                pseudonym="P4b-Guard-02",
                document_type=doc_type_contact,
            )

    def test_update_rejects_foreign_facility_type(self, client_identified, foreign_doc_type_client, staff_user):
        with pytest.raises(ValidationError, match="Einrichtung"):
            update_client(client_identified, staff_user, document_type=foreign_doc_type_client)

    def test_update_rejects_event_entity_type(self, client_identified, doc_type_contact, staff_user):
        with pytest.raises(ValidationError, match="Personen"):
            update_client(client_identified, staff_user, document_type=doc_type_contact)

    def test_data_json_without_document_type_is_rejected(self, facility, staff_user):
        """Ohne Typ gibt es keine Whitelist — dann darf auch nichts gespeichert werden."""
        with pytest.raises(ValidationError, match="Dokumentationstyp"):
            create_client(
                facility,
                staff_user,
                pseudonym="P4b-Guard-03",
                data_json={"irgendwas": "Wert"},
            )

    def test_valid_type_is_accepted(self, facility, doc_type_client_profile, staff_user):
        client = create_client(
            facility,
            staff_user,
            pseudonym="P4b-Guard-04",
            document_type=doc_type_client_profile,
        )
        assert client.document_type_id == doc_type_client_profile.pk


@pytest.mark.django_db
class TestClientDataJsonWhitelist:
    """``validate_data_json`` filtert fremde Keys — der Service nutzt den Rueckgabewert."""

    def test_create_drops_unknown_keys(self, facility, doc_type_client_profile, staff_user):
        client = create_client(
            facility,
            staff_user,
            pseudonym="P4b-White-01",
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", "nicht-am-typ": "Schmuggelware"},
        )

        assert "nicht-am-typ" not in client.data_json
        assert client.data_json["wohnsituation"] == "WG"
        assert "nicht-am-typ" not in raw_data_json(client)

    def test_update_drops_unknown_keys(self, client_identified, doc_type_client_profile, staff_user):
        update_client(
            client_identified,
            staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", "nicht-am-typ": "Schmuggelware"},
        )

        client_identified.refresh_from_db()
        assert client_identified.data_json == {"wohnsituation": "WG"}

    def test_type_switch_drops_slugs_of_the_old_type(self, facility, doc_type_client_profile, staff_user, fernet_key):
        """E11-Merge-Policy: nach dem Typwechsel bleiben nur Slugs des NEUEN Typs."""
        from core.models import DocumentTypeField, FieldTemplate

        other_type = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.ADMIN,
            entity_type=DocumentType.EntityType.CLIENT,
            name="Personenprofil (anderes)",
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Zielvereinbarung",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=other_type, field_template=ft, sort_order=0)

        with override_settings(ENCRYPTION_KEY=fernet_key):
            client = create_client(
                facility,
                staff_user,
                pseudonym="P4b-White-03",
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "WG"},
            )
            update_client(client, staff_user, document_type=other_type)

        client.refresh_from_db()
        assert client.data_json == {}, "Slugs des alten Typs muessen beim Typwechsel wegfallen"


@pytest.mark.django_db
class TestClientDynamicDataAnonymization:
    """Anonymisierung loescht Dynamikdaten und loest den PROTECT-Verweis (E7)."""

    def test_anonymize_wipes_data_json_and_document_type(
        self, facility, doc_type_client_profile, staff_user, fernet_key
    ):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            client = create_client(
                facility,
                staff_user,
                pseudonym="P4b-Anon-01",
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "Bei Familie Musterfrau", "diagnose": "Sensibel"},
            )

            anonymize_client(client, user=staff_user)

        client.refresh_from_db()
        assert client.data_json == {}
        assert client.document_type_id is None
        assert raw_data_json(client) == {}

    def test_k_anonymize_wipes_data_json_and_document_type(
        self, facility, doc_type_client_profile, staff_user, fernet_key
    ):
        """``k_anonymize_client`` redigiert die Client-Zeile eigenstaendig — die
        Dynamikdaten muessen dort genauso fallen wie im Hard-Pfad."""
        with override_settings(ENCRYPTION_KEY=fernet_key):
            client = create_client(
                facility,
                staff_user,
                pseudonym="P4b-Anon-02",
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "Bei Familie Musterfrau"},
            )

            k_anonymize_client(client)

        client.refresh_from_db()
        assert client.data_json == {}
        assert client.document_type_id is None

    def test_anonymized_client_no_longer_blocks_type_deletion(self, facility, doc_type_client_profile, staff_user):
        client = create_client(
            facility,
            staff_user,
            pseudonym="P4b-Anon-03",
            document_type=doc_type_client_profile,
        )
        anonymize_client(client, user=staff_user)

        doc_type_client_profile.delete()
        assert not DocumentType.objects.filter(pk=doc_type_client_profile.pk).exists()


@pytest.mark.django_db
class TestClientDynamicDataAudit:
    """``update_client`` meldet die Feldnamen, niemals die Werte."""

    def test_changed_fields_contain_dynamic_fields_without_values(
        self, client_identified, doc_type_client_profile, staff_user
    ):
        update_client(
            client_identified,
            staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "Streng geheime Adresse"},
        )

        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_UPDATE,
            target_id=str(client_identified.pk),
        ).latest("timestamp")
        assert set(entry.detail["changed_fields"]) == {"document_type", "data_json"}
        assert "Streng geheime Adresse" not in str(entry.detail)
        assert set(entry.detail) == {"changed_fields"}


@pytest.mark.django_db
class TestClientDocumentTypeProtect:
    """``on_delete=PROTECT``: ein benutzter Typ ist nicht loeschbar (UI-Block: P9)."""

    def test_type_with_client_reference_cannot_be_deleted(self, facility, doc_type_client_profile, staff_user):
        create_client(
            facility,
            staff_user,
            pseudonym="P4b-Protect-01",
            document_type=doc_type_client_profile,
        )

        with pytest.raises(ProtectedError):
            doc_type_client_profile.delete()

        assert DocumentType.objects.filter(pk=doc_type_client_profile.pk).exists()
