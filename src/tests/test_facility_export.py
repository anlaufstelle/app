"""Tests fuer den versionierten JSON-Facility-Export (M4 Strang B, Refs #1060).

Deckt :mod:`core.services.system.facility_export` (Schema v1, Entschluesselung,
Attachment-SHA-256, Nur-Aktive-Filter, Facility-Isolation) sowie das
Management-Command ``export_facility`` (Fail-Loud-RLS-Guard, UUID-/Namens-
Aufloesung, Audit, Datei-Schreiben) ab.
"""

import hashlib
import json
import uuid
from io import StringIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import connection
from django.utils import timezone

from core.models import AuditLog, Case, Client, Event, FieldTemplate, WorkItem
from core.services.file_vault import EncryptionKeyMissing, store_encrypted_file
from core.services.system.facility_export import build_facility_export

PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _make_attachment(facility, owner, user, *, ft_name="Anhang (Export-Test)"):
    ft = FieldTemplate.objects.create(facility=facility, name=ft_name, field_type=FieldTemplate.FieldType.FILE)
    upload = SimpleUploadedFile("export-test.pdf", PDF_MINIMAL, content_type="application/pdf")
    return store_encrypted_file(facility, upload, ft, owner, user)


def _raw_data_json(table: str, pk) -> dict:
    """``data_json`` direkt aus der DB-Zeile, an der ORM-Instanz-Cache vorbei."""
    with connection.cursor() as cur:
        cur.execute(f"SELECT data_json FROM {table} WHERE id = %s", [str(pk)])  # noqa: S608 — Tabellenname konstant
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.fixture(autouse=True)
def _bypass_rls(monkeypatch):
    """Tests rufen den Service direkt und/oder den Command mit gepatchtem Bypass.

    ``build_facility_export`` selbst kennt keinen RLS-Bypass (Service-Layer-
    Prinzip, s. Docstring) — die Fail-Loud-Pruefung sitzt ausschliesslich im
    Command. Der Bypass wird hier zentral auf ``True`` gesetzt; die eine
    Fail-Loud-Testklasse ueberschreibt ihn gezielt wieder auf ``False``.
    """
    from core.management.commands import export_facility as cmd

    monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: True)


@pytest.mark.django_db
class TestSchemaShape:
    def test_top_level_keys_and_schema_version(self, facility):
        export = build_facility_export(facility)
        assert export["schema_version"] == 1
        assert set(export.keys()) == {"schema_version", "exported_at", "facility", "configuration", "entities"}

    def test_facility_block(self, facility):
        export = build_facility_export(facility)
        assert export["facility"] == {
            "id": str(facility.pk),
            "name": facility.name,
            "organization": {"id": str(facility.organization_id), "name": facility.organization.name},
        }

    def test_configuration_keys(self, facility):
        export = build_facility_export(facility)
        assert set(export["configuration"].keys()) == {
            "document_types",
            "field_templates",
            "field_groups",
            "field_conditions",
        }

    def test_entities_keys(self, facility):
        export = build_facility_export(facility)
        assert set(export["entities"].keys()) == {"clients", "events", "cases", "episodes", "goals", "work_items"}

    def test_event_transport_metadata_is_not_exported(self, facility, sample_event):
        sample_event.idempotency_key = "facility-export-internal-key"
        sample_event.idempotency_payload_hash = "b" * 64
        sample_event.save(update_fields=["idempotency_key", "idempotency_payload_hash"])

        export = build_facility_export(facility)
        entry = next(event for event in export["entities"]["events"] if event["id"] == str(sample_event.pk))

        assert "idempotency_key" not in entry
        assert "idempotency_payload_hash" not in entry


@pytest.mark.django_db
class TestConfiguration:
    def test_document_type_has_entity_type_and_no_category(self, facility, doc_type_client_profile):
        export = build_facility_export(facility)
        want_id = str(doc_type_client_profile.pk)
        entry = next(dt for dt in export["configuration"]["document_types"] if dt["id"] == want_id)
        assert entry["entity_type"] == "client"
        # ADR-040: category wurde entfernt — darf im Export nicht wiederauftauchen.
        assert "category" not in entry

    def test_document_type_fields_carry_field_group_id(self, facility, doc_type_contact, field_group, doc_type_field):
        doc_type_field.field_group = field_group
        doc_type_field.save(update_fields=["field_group"])
        export = build_facility_export(facility)
        entry = next(dt for dt in export["configuration"]["document_types"] if dt["id"] == str(doc_type_contact.pk))
        field_entry = next(f for f in entry["fields"] if f["id"] == str(doc_type_field.pk))
        assert field_entry["field_group_id"] == str(field_group.pk)
        assert field_entry["field_template_id"] == str(doc_type_field.field_template_id)

    def test_field_template_full_definition(self, facility, doc_type_client_profile):
        export = build_facility_export(facility)
        slugs = {ft["slug"] for ft in export["configuration"]["field_templates"]}
        assert "diagnose" in slugs
        diagnose = next(ft for ft in export["configuration"]["field_templates"] if ft["slug"] == "diagnose")
        assert diagnose["is_encrypted"] is True
        assert diagnose["field_type"] == "textarea"

    def test_field_template_includes_optional_number_ranges(self, facility):
        field_template = FieldTemplate.objects.create(
            facility=facility,
            name="Stückzahl Export",
            field_type=FieldTemplate.FieldType.NUMBER,
            min_value=0,
            max_value=20,
        )

        export = build_facility_export(facility)
        entry = next(ft for ft in export["configuration"]["field_templates"] if ft["id"] == str(field_template.pk))

        assert entry["min_value"] == 0
        assert entry["max_value"] == 20

    def test_report_key_ersetzt_statistics_category(self, facility, doc_type_contact, field_template):
        """E3 (Refs #1849): der Berichtsschluessel steht an BEIDEN Traegern im
        Export; das folgenlose ``statistics_category`` ist ersatzlos entfallen.
        """
        doc_type_contact.report_key = "contact"
        doc_type_contact.save()
        field_template.report_key = "dauer_minuten"
        field_template.save()

        export = build_facility_export(facility)
        typ = next(dt for dt in export["configuration"]["document_types"] if dt["id"] == str(doc_type_contact.pk))
        vorlage = next(ft for ft in export["configuration"]["field_templates"] if ft["id"] == str(field_template.pk))

        assert typ["report_key"] == "contact"
        assert vorlage["report_key"] == "dauer_minuten"
        assert "statistics_category" not in vorlage

    def test_field_group_present(self, facility, field_group):
        export = build_facility_export(facility)
        ids = {fg["id"] for fg in export["configuration"]["field_groups"]}
        assert str(field_group.pk) in ids

    def test_field_condition_present(self, facility, field_condition):
        export = build_facility_export(facility)
        entry = next(fc for fc in export["configuration"]["field_conditions"] if fc["id"] == str(field_condition.pk))
        assert entry["target_field_id"] == str(field_condition.target_field_id)
        assert entry["depends_on_field_id"] == str(field_condition.depends_on_field_id)
        assert entry["operator"] == field_condition.operator


@pytest.mark.django_db
class TestDecryption:
    def test_client_encrypted_field_appears_as_plaintext(self, facility, doc_type_client_profile, staff_user):
        client = Client.objects.create(
            facility=facility,
            pseudonym="Export-Krypto-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", "diagnose": "Klartext-Diagnose"},
        )
        # DB traegt weiterhin einen Fernet-Marker (Kontrollgruppe).
        raw = _raw_data_json("core_client", client.pk)
        assert raw["diagnose"]["__encrypted__"] is True

        export = build_facility_export(facility)
        entry = next(c for c in export["entities"]["clients"] if c["id"] == str(client.pk))
        assert entry["data_json"]["diagnose"] == "Klartext-Diagnose"
        assert entry["data_json"]["wohnsituation"] == "WG"

    def test_event_encrypted_field_appears_as_plaintext(self, facility, doc_type_crisis, client_identified, staff_user):
        from django.utils import timezone

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Vertrauliche Krisen-Notiz"},
            created_by=staff_user,
        )
        raw = _raw_data_json("core_event", event.pk)
        assert raw["notiz-krise"]["__encrypted__"] is True

        export = build_facility_export(facility)
        entry = next(e for e in export["entities"]["events"] if e["id"] == str(event.pk))
        assert entry["data_json"]["notiz-krise"] == "Vertrauliche Krisen-Notiz"


@pytest.mark.django_db
class TestKAnonymizedAndAnonymizedClients:
    def test_k_anonymized_client_included_and_marked(self, facility, staff_user):
        client = Client.objects.create(
            facility=facility,
            pseudonym="Export-KAnon-01",
            created_by=staff_user,
            k_anonymized=True,
        )
        export = build_facility_export(facility)
        entry = next(c for c in export["entities"]["clients"] if c["id"] == str(client.pk))
        assert entry["k_anonymized"] is True

    def test_fully_anonymized_client_marked_is_anonymized(self, facility, staff_user):
        client = Client.objects.create(
            facility=facility,
            pseudonym="Gelöscht-abcd1234",
            created_by=staff_user,
            is_active=False,
        )
        export = build_facility_export(facility)
        entry = next(c for c in export["entities"]["clients"] if c["id"] == str(client.pk))
        assert entry["is_anonymized"] is True

    def test_active_client_not_marked_anonymized(self, client_identified, facility):
        export = build_facility_export(facility)
        entry = next(c for c in export["entities"]["clients"] if c["id"] == str(client_identified.pk))
        assert entry["is_anonymized"] is False


@pytest.mark.django_db
class TestAttachments:
    def test_known_content_yields_known_sha256(self, facility, sample_event, staff_user):
        attachment = _make_attachment(facility, sample_event, staff_user)
        expected = hashlib.sha256(PDF_MINIMAL).hexdigest()

        export = build_facility_export(facility)
        entry = next(e for e in export["entities"]["events"] if e["id"] == str(sample_event.pk))
        att_entry = next(a for a in entry["attachments"] if a["id"] == str(attachment.pk))
        assert att_entry["sha256"] == expected
        assert att_entry["size"] == attachment.file_size
        assert att_entry["owner"] == "event"
        assert att_entry["original_filename"] == "export-test.pdf"

    def test_missing_file_yields_null_sha256_not_crash(self, facility, sample_event, staff_user):
        from core.services.file_vault.storage import get_attachment_path

        attachment = _make_attachment(facility, sample_event, staff_user)
        get_attachment_path(attachment).unlink()

        export = build_facility_export(facility)
        entry = next(e for e in export["entities"]["events"] if e["id"] == str(sample_event.pk))
        att_entry = next(a for a in entry["attachments"] if a["id"] == str(attachment.pk))
        assert att_entry["sha256"] is None

    def test_corrupted_attachment_yields_null_sha256_not_crash(self, facility, sample_event, staff_user):
        """Befund 4 (Refs #1060): ein defektes Anhang-File darf den Gesamtexport
        nicht abbrechen — ``decrypt_file_stream`` wirft ``EncryptionError``
        (kaputter Format-Header) statt ``OSError``, muss also ebenfalls gefangen
        werden."""
        from core.services.file_vault.storage import get_attachment_path

        attachment = _make_attachment(facility, sample_event, staff_user)
        get_attachment_path(attachment).write_bytes(b"kein-gueltiges-verschluesseltes-format-0123456789")

        export = build_facility_export(facility)
        entry = next(e for e in export["entities"]["events"] if e["id"] == str(sample_event.pk))
        att_entry = next(a for a in entry["attachments"] if a["id"] == str(attachment.pk))
        assert att_entry["sha256"] is None

    def test_truncated_attachment_after_version_byte_yields_null_sha256(self, facility, sample_event, staff_user):
        """S5-Restluecke (Refs #1060): eine hinter dem gueltigen Versionsbyte
        auf 1-4 Bytes abgeschnittene Datei (halber Restore, volle Platte) laesst
        ``decrypt_file_stream`` beim ungeprueften ``struct.unpack`` des
        Chunk-Zaehlers ``struct.error`` werfen — weder ``OSError`` noch
        ``EncryptionError``; muss ebenfalls als ``sha256: null`` enden."""
        from core.services.file_vault.storage import get_attachment_path

        attachment = _make_attachment(facility, sample_event, staff_user)
        path = get_attachment_path(attachment)
        path.write_bytes(path.read_bytes()[:3])

        export = build_facility_export(facility)
        entry = next(e for e in export["entities"]["events"] if e["id"] == str(sample_event.pk))
        att_entry = next(a for a in entry["attachments"] if a["id"] == str(attachment.pk))
        assert att_entry["sha256"] is None

    def test_encryption_key_missing_aborts_export(self, facility, sample_event, staff_user, monkeypatch):
        """Befund 4 (Refs #1060): ``EncryptionKeyMissing`` (Fehlkonfiguration) muss
        weiter-raisen statt als ``sha256: null`` maskiert zu werden
        (``safe_decrypt``-Praezedenz). Patch sitzt auf der Modul-Referenz in
        ``facility_export`` — niemals auf Proxies/Connection-Objekten."""
        from core.services.system import facility_export as svc

        _make_attachment(facility, sample_event, staff_user)

        def _boom(_attachment):
            raise EncryptionKeyMissing("kein Schluessel konfiguriert")

        monkeypatch.setattr(svc, "get_decrypted_file_stream", _boom)

        with pytest.raises(EncryptionKeyMissing):
            build_facility_export(facility)

    def test_no_file_hashes_flag_skips_hashing_entirely(self, facility, sample_event, staff_user, monkeypatch):
        from core.services.system import facility_export as svc

        _make_attachment(facility, sample_event, staff_user)

        def _boom(_attachment):
            raise AssertionError("get_decrypted_file_stream darf bei with_file_hashes=False nicht aufgerufen werden")

        monkeypatch.setattr(svc, "get_decrypted_file_stream", _boom)

        export = build_facility_export(facility, with_file_hashes=False)
        entry = next(e for e in export["entities"]["events"] if e["id"] == str(sample_event.pk))
        assert entry["attachments"][0]["sha256"] is None

    def test_client_and_case_owned_attachments_present(
        self, facility, client_identified, doc_type_client_profile, case_open, doc_type_case_form, staff_user
    ):
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type"])
        case_open.document_type = doc_type_case_form
        case_open.save(update_fields=["document_type"])

        client_att = _make_attachment(facility, client_identified, staff_user, ft_name="Anhang Person")
        case_att = _make_attachment(facility, case_open, staff_user, ft_name="Anhang Fall")

        export = build_facility_export(facility)
        client_entry = next(c for c in export["entities"]["clients"] if c["id"] == str(client_identified.pk))
        case_entry = next(c for c in export["entities"]["cases"] if c["id"] == str(case_open.pk))
        assert {a["id"] for a in client_entry["attachments"]} == {str(client_att.pk)}
        assert {a["id"] for a in case_entry["attachments"]} == {str(case_att.pk)}

    def test_superseded_attachment_not_included(self, facility, sample_event, staff_user):
        first = _make_attachment(facility, sample_event, staff_user)
        ft = first.field_template
        upload = SimpleUploadedFile("v2.pdf", PDF_MINIMAL, content_type="application/pdf")
        store_encrypted_file(facility, upload, ft, sample_event, staff_user, supersedes=first)

        export = build_facility_export(facility)
        entry = next(e for e in export["entities"]["events"] if e["id"] == str(sample_event.pk))
        att_ids = {a["id"] for a in entry["attachments"]}
        assert str(first.pk) not in att_ids


@pytest.mark.django_db
class TestOnlyActiveEntities:
    def test_soft_deleted_client_excluded(self, client_trashed, facility):
        export = build_facility_export(facility)
        ids = {c["id"] for c in export["entities"]["clients"]}
        assert str(client_trashed.pk) not in ids

    def test_soft_deleted_event_excluded(self, sample_event, facility):
        sample_event.is_deleted = True
        sample_event.save(update_fields=["is_deleted"])
        export = build_facility_export(facility)
        ids = {e["id"] for e in export["entities"]["events"]}
        assert str(sample_event.pk) not in ids

    def test_soft_deleted_case_excluded(self, case_open, client_identified, facility, staff_user):
        """Befund 1+2 (Refs #1060): der einzige Produktionspfad loescht die
        PERSON (``client.soft_delete()``, ``approve_client_deletion``) — kein
        Aufrufer setzt ``Case.is_deleted`` direkt. Diese Grenze wird geprueft,
        nicht der (in Produktion tote) Case-eigene Soft-Delete-Zustand."""
        client_identified.soft_delete(user=staff_user)
        export = build_facility_export(facility)
        ids = {c["id"] for c in export["entities"]["cases"]}
        assert str(case_open.pk) not in ids

    def test_soft_deleted_episode_excluded(self, episode, client_identified, facility, staff_user):
        """Befund 1+2 (Refs #1060): siehe test_soft_deleted_case_excluded —
        Episode.soft_delete() hat keinen Produktionsaufrufer, die wirksame
        Grenze verlaeuft ueber den Client des Case."""
        client_identified.soft_delete(user=staff_user)
        export = build_facility_export(facility)
        ids = {e["id"] for e in export["entities"]["episodes"]}
        assert str(episode.pk) not in ids

    def test_soft_deleted_workitem_excluded(self, sample_workitem, client_identified, facility, staff_user):
        """Befund 1+2 (Refs #1060): siehe test_soft_deleted_case_excluded —
        WorkItem.soft_delete() hat keinen Produktionsaufrufer, die wirksame
        Grenze verlaeuft ueber den (nullable) Client."""
        client_identified.soft_delete(user=staff_user)
        export = build_facility_export(facility)
        ids = {wi["id"] for wi in export["entities"]["work_items"]}
        assert str(sample_workitem.pk) not in ids

    def test_own_is_deleted_case_still_filters(self, case_open, facility):
        """Defense-in-Depth-Pin (S5 Refs #1060): ``Case.is_deleted`` hat heute
        keinen Produktionssetzer — der Eigen-Filter in ``_build_entities``
        bleibt trotzdem stehen und wird hier gegen Entfernen gepinnt."""
        case_open.is_deleted = True
        case_open.save(update_fields=["is_deleted"])
        export = build_facility_export(facility)
        assert str(case_open.pk) not in {c["id"] for c in export["entities"]["cases"]}

    def test_own_is_deleted_episode_still_filters(self, episode, facility):
        """Defense-in-Depth-Pin (S5 Refs #1060), analog Case."""
        episode.is_deleted = True
        episode.save(update_fields=["is_deleted"])
        export = build_facility_export(facility)
        assert str(episode.pk) not in {e["id"] for e in export["entities"]["episodes"]}

    def test_own_is_deleted_workitem_still_filters(self, sample_workitem, facility):
        """Defense-in-Depth-Pin (S5 Refs #1060), analog Case."""
        sample_workitem.is_deleted = True
        sample_workitem.save(update_fields=["is_deleted"])
        export = build_facility_export(facility)
        assert str(sample_workitem.pk) not in {wi["id"] for wi in export["entities"]["work_items"]}

    def test_active_entities_all_included(
        self, facility, client_identified, sample_event, case_open, episode, outcome_goal, milestone, sample_workitem
    ):
        export = build_facility_export(facility)
        assert str(client_identified.pk) in {c["id"] for c in export["entities"]["clients"]}
        assert str(sample_event.pk) in {e["id"] for e in export["entities"]["events"]}
        assert str(case_open.pk) in {c["id"] for c in export["entities"]["cases"]}
        assert str(episode.pk) in {e["id"] for e in export["entities"]["episodes"]}
        goal_entry = next(g for g in export["entities"]["goals"] if g["id"] == str(outcome_goal.pk))
        assert str(milestone.pk) in {m["id"] for m in goal_entry["milestones"]}
        assert str(sample_workitem.pk) in {wi["id"] for wi in export["entities"]["work_items"]}


@pytest.mark.django_db
class TestTrashBoundaryIsThePerson:
    """Befund 1+2 (Refs #1060, high): die Papierkorb-Grenze verlaeuft ueber die
    Person (``client.soft_delete()`` — der einzige Produktionsloeschpfad,
    ``approve_client_deletion``/``core/services/client/main.py:938``), nicht
    ueber die eigenen ``is_deleted``-Felder von Case/Event/Episode/WorkItem.
    """

    def test_trashed_client_children_all_excluded_production_real(
        self,
        facility,
        client_identified,
        case_open,
        sample_event,
        episode,
        outcome_goal,
        milestone,
        sample_workitem,
        staff_user,
    ):
        """Produktionsrealer Papierkorb-Weg: nur der Client wird soft-deleted —
        Case/Event/Episode/Goal/WorkItem behalten ihr eigenes ``is_deleted=False``.
        Vor dem Fix waren alle fuenf trotzdem im Export (Kern-Defekt)."""
        client_identified.soft_delete(user=staff_user)

        export = build_facility_export(facility)

        assert str(client_identified.pk) not in {c["id"] for c in export["entities"]["clients"]}
        assert str(case_open.pk) not in {c["id"] for c in export["entities"]["cases"]}
        assert str(sample_event.pk) not in {e["id"] for e in export["entities"]["events"]}
        assert str(episode.pk) not in {e["id"] for e in export["entities"]["episodes"]}
        assert str(outcome_goal.pk) not in {g["id"] for g in export["entities"]["goals"]}
        assert str(sample_workitem.pk) not in {wi["id"] for wi in export["entities"]["work_items"]}

    def test_no_dangling_foreign_keys_after_client_trash(
        self,
        facility,
        client_identified,
        client_qualified,
        case_open,
        sample_event,
        episode,
        outcome_goal,
        milestone,
        sample_workitem,
        staff_user,
    ):
        """Genereller Dangling-FK-Guard: jede ``client_id``/``case_id``/
        ``episode_id`` in ``entities.*`` muss in der jeweiligen Zielliste
        aufloesbar sein — ausser bei Events/WorkItems mit ``client_id=None``
        (SET_NULL-Waisen, per Design erlaubt).

        Gemischtes Szenario (S5 Refs #1060): NEBEN dem Papierkorb-Client
        existiert ein lebender Client mit vollem Objektgraph — sonst sind nach
        dem Trash alle Listen leer und der Guard laeuft vakuum-gruen."""
        from core.models import Episode, Event, OutcomeGoal

        live_case = Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Lebender Fall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        live_episode = Episode.objects.create(
            case=live_case, title="Lebende Episode", started_at=timezone.now().date(), created_by=staff_user
        )
        live_event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=sample_event.document_type,
            occurred_at=timezone.now(),
            case=live_case,
            episode=live_episode,
            created_by=staff_user,
        )
        OutcomeGoal.objects.create(case=live_case, title="Lebendes Ziel", created_by=staff_user)
        live_workitem = WorkItem.objects.create(
            facility=facility,
            client=client_qualified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Lebende Aufgabe",
        )

        client_identified.soft_delete(user=staff_user)

        export = build_facility_export(facility)
        client_ids = {c["id"] for c in export["entities"]["clients"]}
        case_ids = {c["id"] for c in export["entities"]["cases"]}
        episode_ids = {e["id"] for e in export["entities"]["episodes"]}

        # Kein Vakuum: der lebende Graph muss vollstaendig drin sein.
        assert str(live_case.pk) in case_ids
        assert str(live_episode.pk) in episode_ids
        assert str(live_event.pk) in {e["id"] for e in export["entities"]["events"]}
        assert str(live_workitem.pk) in {wi["id"] for wi in export["entities"]["work_items"]}
        assert export["entities"]["goals"]

        for event in export["entities"]["events"]:
            assert event["client_id"] is None or event["client_id"] in client_ids
            assert event["case_id"] is None or event["case_id"] in case_ids
            assert event["episode_id"] is None or event["episode_id"] in episode_ids
        for work_item in export["entities"]["work_items"]:
            assert work_item["client_id"] is None or work_item["client_id"] in client_ids
        for case in export["entities"]["cases"]:
            assert case["client_id"] in client_ids
        for ep in export["entities"]["episodes"]:
            assert ep["case_id"] in case_ids
        for goal in export["entities"]["goals"]:
            assert goal["case_id"] in case_ids

    def test_anonymized_client_and_its_case_stay_in_export(self, facility, staff_user):
        """``is_active=False`` (Art.-17-Anonymisierung, NICHT im Papierkorb) ist
        eine andere Grenze als ``is_deleted`` — die Person bleibt (redigiert,
        ``is_anonymized: true``) im Export, ihr Fall ebenfalls (bewusste
        Abweichung von ``cases_with_live_client``, s. Kommentar in
        ``_build_entities``)."""
        client = Client.objects.create(
            facility=facility,
            pseudonym="Export-Anon-Fall-01",
            created_by=staff_user,
            is_active=False,
        )
        case = Case.objects.create(
            facility=facility,
            client=client,
            title="Alt-Fall einer anonymisierten Person",
            status=Case.Status.CLOSED,
            created_by=staff_user,
        )

        export = build_facility_export(facility)

        client_entry = next(c for c in export["entities"]["clients"] if c["id"] == str(client.pk))
        assert client_entry["is_anonymized"] is True
        assert str(case.pk) in {c["id"] for c in export["entities"]["cases"]}

    def test_anonymous_event_and_unassigned_workitem_remain(self, facility, doc_type_crisis, staff_user):
        """Event.client/WorkItem.client sind SET_NULL — anonyme Ereignisse und
        nicht zugeordnete Arbeitsauftraege haben nie eine Person und muessen
        trotz ``live_client``-Filter im Export bleiben."""
        event = Event.objects.create(
            facility=facility,
            client=None,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        work_item = WorkItem.objects.create(
            facility=facility,
            client=None,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Nicht zugeordnete Aufgabe",
        )

        export = build_facility_export(facility)

        assert str(event.pk) in {e["id"] for e in export["entities"]["events"]}
        assert str(work_item.pk) in {wi["id"] for wi in export["entities"]["work_items"]}


@pytest.mark.django_db
class TestForeignKeysAsStrings:
    def test_event_fks_are_strings(self, facility, sample_event):
        export = build_facility_export(facility)
        entry = next(e for e in export["entities"]["events"] if e["id"] == str(sample_event.pk))
        assert isinstance(entry["client_id"], str)
        assert isinstance(entry["document_type_id"], str)
        assert isinstance(entry["created_by_id"], str)
        assert entry["case_id"] is None or isinstance(entry["case_id"], str)

    def test_client_without_document_type_has_none_not_missing_key(self, client_identified, facility):
        export = build_facility_export(facility)
        entry = next(c for c in export["entities"]["clients"] if c["id"] == str(client_identified.pk))
        assert entry["document_type_id"] is None


@pytest.mark.django_db
class TestJSONRoundtrip:
    def test_full_export_is_json_serializable(
        self, facility, client_identified, sample_event, case_open, episode, outcome_goal, milestone, sample_workitem
    ):
        export = build_facility_export(facility)
        dumped = json.dumps(export)
        reloaded = json.loads(dumped)
        assert reloaded["schema_version"] == 1


@pytest.mark.django_db
class TestCrossFacilityIsolation:
    def test_facility_a_export_excludes_facility_b_entities(
        self,
        facility,
        client_identified,
        second_facility,
        foreign_client,
        foreign_event,
        foreign_case,
        foreign_episode,
        foreign_goal,
        foreign_milestone,
        foreign_workitem,
    ):
        export = build_facility_export(facility)
        client_ids = {c["id"] for c in export["entities"]["clients"]}
        event_ids = {e["id"] for e in export["entities"]["events"]}
        case_ids = {c["id"] for c in export["entities"]["cases"]}
        episode_ids = {e["id"] for e in export["entities"]["episodes"]}
        workitem_ids = {wi["id"] for wi in export["entities"]["work_items"]}
        goal_ids = {g["id"] for g in export["entities"]["goals"]}

        assert str(foreign_client.pk) not in client_ids
        assert str(foreign_event.pk) not in event_ids
        assert str(foreign_case.pk) not in case_ids
        assert str(foreign_episode.pk) not in episode_ids
        assert str(foreign_workitem.pk) not in workitem_ids
        assert str(foreign_goal.pk) not in goal_ids
        assert str(client_identified.pk) in client_ids

    def test_facility_a_config_excludes_facility_b_config(self, facility, foreign_field_template, foreign_field_group):
        export = build_facility_export(facility)
        ft_ids = {ft["id"] for ft in export["configuration"]["field_templates"]}
        fg_ids = {fg["id"] for fg in export["configuration"]["field_groups"]}
        assert str(foreign_field_template.pk) not in ft_ids
        assert str(foreign_field_group.pk) not in fg_ids

    def test_facility_a_attachments_exclude_facility_b(self, facility, sample_event, staff_user, foreign_attachment):
        _make_attachment(facility, sample_event, staff_user)
        export = build_facility_export(facility)
        all_att_ids = {
            a["id"]
            for entity_list in export["entities"].values()
            if isinstance(entity_list, list)
            for entity in entity_list
            if isinstance(entity, dict)
            for a in entity.get("attachments", [])
        }
        assert str(foreign_attachment.pk) not in all_att_ids


@pytest.mark.django_db
class TestCommandEndToEnd:
    def test_command_writes_file_and_matches_schema(self, facility, client_identified, tmp_path):
        output = tmp_path / "facility.json"
        call_command("export_facility", facility=str(facility.pk), output=str(output), stdout=StringIO())
        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert str(client_identified.pk) in {c["id"] for c in data["entities"]["clients"]}

    def test_command_resolves_by_name(self, facility, tmp_path):
        output = tmp_path / "facility.json"
        call_command("export_facility", facility=facility.name, output=str(output), stdout=StringIO())
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["facility"]["id"] == str(facility.pk)

    def test_command_indent_option_pretty_prints(self, facility, tmp_path):
        output = tmp_path / "facility.json"
        call_command("export_facility", facility=str(facility.pk), output=str(output), indent=2, stdout=StringIO())
        content = output.read_text(encoding="utf-8")
        assert content.startswith("{\n  ")

    def test_command_no_file_hashes_option(self, facility, sample_event, staff_user, tmp_path):
        _make_attachment(facility, sample_event, staff_user)
        output = tmp_path / "facility.json"
        call_command(
            "export_facility",
            facility=str(facility.pk),
            output=str(output),
            **{"no_file_hashes": True},
            stdout=StringIO(),
        )
        data = json.loads(output.read_text(encoding="utf-8"))
        entry = next(e for e in data["entities"]["events"] if e["id"] == str(sample_event.pk))
        assert entry["attachments"][0]["sha256"] is None

    def test_unknown_facility_uuid_raises_command_error(self, tmp_path):
        output = tmp_path / "facility.json"
        with pytest.raises(CommandError):
            call_command("export_facility", facility=str(uuid.uuid4()), output=str(output), stdout=StringIO())

    def test_unknown_facility_name_raises_command_error(self, tmp_path, db):
        output = tmp_path / "facility.json"
        with pytest.raises(CommandError):
            call_command("export_facility", facility="Nichtvorhandene Stelle", output=str(output), stdout=StringIO())

    def test_ambiguous_facility_name_raises_command_error(self, organization, tmp_path):
        from core.models import Facility

        Facility.objects.create(organization=organization, name="Doppelt")
        Facility.objects.create(organization=organization, name="Doppelt")
        output = tmp_path / "facility.json"
        with pytest.raises(CommandError, match="mehrdeutig"):
            call_command("export_facility", facility="Doppelt", output=str(output), stdout=StringIO())


@pytest.mark.django_db
class TestFailLoudWithoutBypass:
    def test_command_aborts_without_bypass_context(self, monkeypatch, facility, tmp_path):
        from core.management.commands import export_facility as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: False)
        output = tmp_path / "facility.json"
        with pytest.raises(CommandError, match="RLS"):
            call_command("export_facility", facility=str(facility.pk), output=str(output), stdout=StringIO())
        assert not output.exists()

    def test_command_runs_with_bypass_context(self, monkeypatch, facility, tmp_path):
        from core.management.commands import export_facility as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: True)
        output = tmp_path / "facility.json"
        call_command("export_facility", facility=str(facility.pk), output=str(output), stdout=StringIO())
        assert output.exists()


@pytest.mark.django_db
class TestAudit:
    def test_command_writes_audit_entry_with_counters(self, facility, client_identified, sample_event, tmp_path):
        output = tmp_path / "facility.json"
        before = AuditLog.objects.filter(action=AuditLog.Action.EXPORT, target_type="FacilityExport").count()
        call_command("export_facility", facility=str(facility.pk), output=str(output), stdout=StringIO())
        after = AuditLog.objects.filter(action=AuditLog.Action.EXPORT, target_type="FacilityExport")
        assert after.count() == before + 1
        entry = after.latest("timestamp")
        assert entry.detail["clients"] == 1
        assert entry.detail["events"] == 1
        assert entry.facility_id == facility.pk
        assert entry.user is None

    def test_audit_detail_contains_no_pii(self, facility, client_identified, tmp_path):
        output = tmp_path / "facility.json"
        call_command("export_facility", facility=str(facility.pk), output=str(output), stdout=StringIO())
        entry = AuditLog.objects.filter(action=AuditLog.Action.EXPORT, target_type="FacilityExport").latest("timestamp")
        assert client_identified.pseudonym not in str(entry.detail)
        assert set(entry.detail.keys()) == {"clients", "events", "cases", "episodes", "goals", "work_items"}
        assert all(isinstance(v, int) for v in entry.detail.values())
