"""Purge-Command für verwaiste ``data_json``-Keys (Refs #1725(a)).

``purge_orphan_field_data`` entfernt bei Personen, Fällen und Ereignissen jene
``data_json``-Schlüssel, die nicht (mehr) zur aktuellen Feldzuordnung ihres
Dokumentationstyps gehören — zugeordnete Werte (inkl. verschlüsselter) bleiben
unangetastet. Struktur/Fixtures spiegeln die Schwester-Commands
(``test_reencrypt_fields.py``: RLS-Guard + raw-DB-Proben).

Abgedeckt:

* **RLS-Guard** — ohne Bypass-Kontext fail-loud (``CommandError``), sonst sähe
  der Lauf als App-Rolle 0 Zeilen und meldete fälschlich Erfolg.
* **Dry-Run** — zählt, schreibt aber nichts.
* **Echter Lauf** — entfernt nur Waisen-Keys; zugeordnete (auch verschlüsselte)
  Werte bleiben.
* **Entität ohne Typ** — bleibt unangetastet (nur gezählt).
"""

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings

from core.models import Case, Client, Event
from core.services.file_vault import generate_key, is_encrypted_value, safe_decrypt


def raw_data_json(table, pk) -> dict:
    """``<table>.data_json`` direkt aus der DB-Zeile (ohne ORM-Cache)."""
    with connection.cursor() as cur:
        cur.execute(f"SELECT data_json FROM {table} WHERE id = %s", [str(pk)])  # noqa: S608 — table ist Konstante
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.mark.django_db
class TestPurgeOrphanFieldDataRlsGuard:
    """Orphan-Cleanup-RLS-Falle (Refs #1554/#1016): Client/Case/Event stehen unter
    FORCE RLS. Als App-Rolle ohne Bypass-Kontext sähe der Lauf 0 Zeilen und
    meldete trotzdem Erfolg — die Reste blieben liegen. Fail-loud."""

    def test_aborts_loud_without_bypass_context(self, monkeypatch):
        from core.management.commands import purge_orphan_field_data as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: False)
        with pytest.raises(CommandError, match="RLS"):
            call_command("purge_orphan_field_data", stdout=StringIO())

    def test_runs_through_with_bypass_context(self, monkeypatch):
        from core.management.commands import purge_orphan_field_data as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: True)
        out = StringIO()
        call_command("purge_orphan_field_data", stdout=out)
        assert "Personen" in out.getvalue()


@pytest.mark.django_db
class TestPurgeOrphanFieldData:
    def test_removes_only_orphan_keys_keeps_mapped_and_encrypted(self, facility, doc_type_client_profile, staff_user):
        """Nur ``alt-feld`` (nicht am Typ) fällt; ``wohnsituation`` (zugeordnet,
        Klartext) und ``diagnose`` (zugeordnet, verschlüsselt) bleiben."""
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=generate_key()):
            client = Client.objects.create(
                facility=facility,
                pseudonym="purge-01",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"wohnsituation": "WG", "diagnose": "Sensibel", "alt-feld": "Verwaist"},
            )
            token_before = raw_data_json("core_client", client.pk)["diagnose"]

            call_command("purge_orphan_field_data", stdout=StringIO())

            client.refresh_from_db()
            assert "alt-feld" not in client.data_json
            assert client.data_json["wohnsituation"] == "WG"
            stored = raw_data_json("core_client", client.pk)
            assert "alt-feld" not in stored
            assert is_encrypted_value(stored["diagnose"]), "zugeordnetes verschlüsseltes Feld bleibt verschlüsselt"
            assert stored["diagnose"] == token_before, "Ciphertext des zugeordneten Felds bleibt unverändert"
            assert safe_decrypt(stored["diagnose"]) == "Sensibel"

    def test_dry_run_changes_nothing(self, facility, doc_type_client_profile, staff_user):
        client = Client.objects.create(
            facility=facility,
            pseudonym="purge-dry",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", "alt-feld": "Verwaist"},
        )
        out = StringIO()
        call_command("purge_orphan_field_data", "--dry-run", stdout=out)
        client.refresh_from_db()
        assert client.data_json == {"wohnsituation": "WG", "alt-feld": "Verwaist"}
        # Dry-Run meldet trotzdem den geplanten Umfang.
        assert "1" in out.getvalue()

    def test_entity_without_document_type_is_untouched(self, facility, staff_user):
        """Ohne Typ gibt es keine Whitelist — der Datensatz wird nur gezählt,
        nicht angefasst (kein Rätselraten über die richtige Feldmenge)."""
        client = Client.objects.create(
            facility=facility,
            pseudonym="purge-typeless",
            created_by=staff_user,
            document_type=None,
            data_json={"irgendwas": "bleibt"},
        )
        call_command("purge_orphan_field_data", stdout=StringIO())
        client.refresh_from_db()
        assert client.data_json == {"irgendwas": "bleibt"}

    def test_file_orphan_marker_is_skipped_not_purged(self, facility, client_identified, doc_type_contact, staff_user):
        """Refs #1725: Verwaiste FILE-Marker werden NICHT geloescht, sondern
        uebersprungen und separat gezaehlt.

        Der Marker ist die einzige Bruecke zur verschluesselten
        Attachment-Zeile: entfernte man ihn, verschonte
        cleanup_orphan_storage_files die Datei (die Attachment-Zeile existiert
        weiter) und sie waere UI-unsichtbar/unloeschbar — schlechter minimiert
        statt besser. Ein verwaister Klartext-Key faellt weiterhin.
        """
        from django.utils import timezone

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            created_by=staff_user,
            data_json={
                "dauer": 15,
                "alt-datei": {
                    "__file__": True,
                    "attachment_id": "11111111-1111-1111-1111-111111111111",
                    "name": "akte.pdf",
                },
                "alt-text": "Rest",
            },
        )
        out = StringIO()
        call_command("purge_orphan_field_data", stdout=out)
        event.refresh_from_db()
        # FILE-Waise bleibt erhalten (uebersprungen), Text-Waise faellt,
        # zugeordnetes Feld bleibt.
        assert "alt-datei" in event.data_json, "FILE-Waise darf nicht geloescht werden"
        assert event.data_json["alt-datei"]["__file__"] is True
        assert "alt-text" not in event.data_json
        assert event.data_json["dauer"] == 15
        # Report weist die uebersprungene Datei-Waise aus.
        assert "übersprungen" in out.getvalue()

    def test_file_orphan_marker_is_skipped_for_client_owner(
        self, facility, client_identified, doc_type_client_profile, staff_user
    ):
        """M24 (Refs #1738, ADR-039 §7): Die Skip-Regel gilt owner-generisch.

        Bis M24 konnten nur Ereignisse FILE-Marker tragen (E8-Sperre). Seit der
        Owner-Generik koennen auch Personen und Faelle verwaiste Datei-Marker
        haben — sie duerfen genauso wenig geloescht werden, sonst waere die
        zugehoerige ``Attachment``-Zeile UI-unsichtbar und nur noch ueber die
        Klient-Hartloeschung erreichbar.
        """
        client_identified.document_type = doc_type_client_profile
        client_identified.data_json = {
            "wohnsituation": "WG",
            "alt-datei": {"__files__": True, "entries": [{"id": "22222222-2222-2222-2222-222222222222", "sort": 0}]},
            "alt-text": "Rest",
        }
        client_identified.save(update_fields=["document_type", "data_json"])

        out = StringIO()
        call_command("purge_orphan_field_data", stdout=out)
        client_identified.refresh_from_db()

        assert "alt-datei" in client_identified.data_json, "FILE-Waise an der Person darf nicht geloescht werden"
        assert client_identified.data_json["alt-datei"]["__files__"] is True
        assert "alt-text" not in client_identified.data_json
        assert client_identified.data_json["wohnsituation"] == "WG"
        assert "übersprungen" in out.getvalue()

    def test_soft_deleted_entity_orphan_is_purged(self, facility, doc_type_client_profile, staff_user):
        """Refs #1725: Der Purge raeumt bewusst AUCH soft-geloeschte Datensaetze
        (anders als reencrypt_fields' is_deleted=False-Luecke) — sonst
        ueberdauern verwaiste Reste im Papierkorb. Bislang nur im Docstring
        behauptet, hier gegen eine kuenftige is_deleted=False-Regression
        abgesichert (Schwester zu test_reencrypt_soft_deleted_event_is_rotated).
        """
        client = Client.objects.create(
            facility=facility,
            pseudonym="purge-soft",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", "alt-feld": "Verwaist"},
        )
        # Soft-Delete NACH dem Anlegen (update umgeht die save()-Encryption-Kette).
        Client.objects.filter(pk=client.pk).update(is_deleted=True)

        call_command("purge_orphan_field_data", stdout=StringIO())

        client.refresh_from_db()
        assert "alt-feld" not in client.data_json, "verwaister Key auch im Papierkorb bereinigt"
        assert client.data_json["wohnsituation"] == "WG"
        assert client.is_deleted is True, "Soft-Delete-Status bleibt unangetastet"

    def test_case_and_event_orphans_are_purged_too(
        self, facility, client_identified, doc_type_case_form, doc_type_contact, staff_user
    ):
        from django.utils import timezone

        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="purge-case",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Miete", "alt-fallfeld": "Rest"},
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            created_by=staff_user,
            data_json={"dauer": 15, "alt-eventfeld": "Rest"},
        )
        call_command("purge_orphan_field_data", stdout=StringIO())
        case.refresh_from_db()
        event.refresh_from_db()
        assert case.data_json == {"anlass": "Miete"}
        assert event.data_json == {"dauer": 15}
