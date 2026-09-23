"""Reencrypt-Fields-Command: Rotation auf Client/Case-``data_json`` (M2 P6, Refs #1058).

Schliesst die Rotationsluecke aus P4b/P5: ``Client``/``Case`` haben seit
``DynamicDataEncryptionMixin`` (P4a) dieselbe Feldverschluesselung wie
``Event`` — ohne diese Erweiterung wuerde ``reencrypt_fields`` sie bei einer
Key-Rotation stillschweigend uebergehen. Struktur/Fixtures spiegeln die
bestehenden Event-Proben in ``test_encryption.py``
(``test_reencrypt_command_key_rotation`` u.a.) sowie die Krypto-Proben aus
``test_client_dynamic_data.py``/``test_case_dynamic_data.py``.

Abgedeckt:

* **Token-Rotation** — Client/Case: encrypted-Slug-Ciphertext aendert sich,
  Klartext bleibt ueber ``safe_decrypt`` stabil.
* **Plaintext-Heal** — ein unverschluesselt in die DB geschriebener
  encrypted-Slug wird nachverschluesselt (nur mit ``document_type``).
* **document_type=None** — ``data_json`` bleibt unangetastet, kein Crash.
* **E17** — ein soft-deleted Client wird MIT rotiert (``FacilityScopedManager``
  filtert ``is_deleted`` nicht) — und seit #1724 auch ein soft-deleted Event
  (der bisherige ``is_deleted=False``-Filter in ``_reencrypt_events`` ist
  entfernt).
* **--dry-run** — zaehlt, schreibt nichts.
* **Ausgabezeile** — neue Zaehler ("X clients, Y cases").
"""

import json
import threading
from datetime import datetime, timedelta
from datetime import time as dt_time
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import CommandError, call_command
from django.db import connection, connections, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Case, Client, Event, EventHistory, RectificationRequest, ShiftHandoverNote
from core.models.attachment import Attachment
from core.services.file_vault import encrypt_field, generate_key, is_encrypted_value, safe_decrypt
from core.services.file_vault.rotation import rotate_all


def _handover_window():
    """Pflicht-Fenstergrenzen einer Schichtnotiz (Refs #2042, E-01).

    Fuer die Rotation ohne Belang — sie fasst nur die ``note``-Spalte an —,
    aber inzwischen nicht mehr nullbar.
    """
    heute = timezone.localdate()
    return {
        "window_start": timezone.make_aware(datetime.combine(heute, dt_time.min)),
        "window_end": timezone.make_aware(datetime.combine(heute, dt_time.max)),
        "editable_until": timezone.make_aware(datetime.combine(heute + timedelta(days=1), dt_time.max)),
    }


_JOIN_TIMEOUT = 30


def raw_client_data_json(client) -> dict:
    """``core_client.data_json`` direkt aus der DB-Zeile (ohne ORM-Cache)."""
    with connection.cursor() as cur:
        cur.execute("SELECT data_json FROM core_client WHERE id = %s", [str(client.pk)])
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


def raw_case_data_json(case) -> dict:
    """``core_case.data_json`` direkt aus der DB-Zeile (ohne ORM-Cache)."""
    with connection.cursor() as cur:
        cur.execute("SELECT data_json FROM core_case WHERE id = %s", [str(case.pk)])
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


def raw_event_data_json(event) -> dict:
    """``core_event.data_json`` direkt aus der DB-Zeile (ohne ORM-Cache)."""
    with connection.cursor() as cur:
        cur.execute("SELECT data_json FROM core_event WHERE id = %s", [str(event.pk)])
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


def raw_history_payloads(history) -> tuple[dict | None, dict | None]:
    """``core_eventhistory.data_before``/``data_after`` direkt aus der DB-Zeile."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT data_before, data_after FROM core_eventhistory WHERE id = %s",
            [str(history.pk)],
        )
        before, after = cur.fetchone()
    return (
        json.loads(before) if isinstance(before, str) else before,
        json.loads(after) if isinstance(after, str) else after,
    )


def decryptable_values(payload) -> list[str]:
    """Die aus *payload* noch entschluesselbaren Klartexte (Refs #2177).

    Das ist der Schadensmassstab des Rennens: Steht nach dem Lauf wieder
    Klartext in einer redigierten Spalte, ist eine Loeschung nach Art. 17
    still rueckgaengig gemacht worden.
    """
    if not isinstance(payload, dict):
        return []
    found = []
    for value in payload.values():
        if is_encrypted_value(value):
            plain = safe_decrypt(value)
            if plain != "[verschlüsselt]":
                found.append(plain)
    return found


@pytest.mark.django_db
def test_reencrypt_client_token_rotates_under_key_rotation(facility, doc_type_client_profile, staff_user):
    """``Client.data_json['diagnose']`` wird unter dem neuen Primaerschluessel neu verschluesselt."""
    old_key = generate_key()
    new_key = generate_key()

    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
        client = Client.objects.create(
            facility=facility,
            pseudonym="P6-Rotation-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", "diagnose": "Rotationstest"},
        )
    token_before = raw_client_data_json(client)["diagnose"]["value"]

    keys = f"{new_key},{old_key}"
    with override_settings(ENCRYPTION_KEYS=keys, ENCRYPTION_KEY=""):
        call_command("reencrypt_fields", stdout=StringIO())

    client.refresh_from_db()
    stored = raw_client_data_json(client)["diagnose"]
    assert is_encrypted_value(stored), "Feld muss weiterhin ein Verschluesselungsmarker sein"
    assert stored["value"] != token_before, "Ciphertext muss sich nach der Rotation aendern"

    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=new_key):
        assert safe_decrypt(stored) == "Rotationstest"


@pytest.mark.django_db
def test_reencrypt_case_token_rotates_under_key_rotation(facility, client_identified, doc_type_case_form, staff_user):
    """``Case.data_json['verlauf']`` wird unter dem neuen Primaerschluessel neu verschluesselt (analog Client)."""
    old_key = generate_key()
    new_key = generate_key()

    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P6-Rotation-Fall",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden", "verlauf": "Rotationstest"},
        )
    token_before = raw_case_data_json(case)["verlauf"]["value"]

    keys = f"{new_key},{old_key}"
    with override_settings(ENCRYPTION_KEYS=keys, ENCRYPTION_KEY=""):
        call_command("reencrypt_fields", stdout=StringIO())

    case.refresh_from_db()
    stored = raw_case_data_json(case)["verlauf"]
    assert is_encrypted_value(stored)
    assert stored["value"] != token_before

    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=new_key):
        assert safe_decrypt(stored) == "Rotationstest"


@pytest.mark.django_db
def test_reencrypt_heals_plaintext_client_diagnose(facility, doc_type_client_profile, staff_user):
    """Wie bei Event/A4.4: ein unverschluesselt in die DB geschriebener encrypted-Slug
    wird vom Heal-Lauf nachverschluesselt — Vorbedingung ist ein gesetzter ``document_type``.
    """
    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        client = Client.objects.create(
            facility=facility,
            pseudonym="P6-Heal-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", "diagnose": "egal"},
        )
        # Altbestand simulieren: Klartext direkt in die DB schreiben (umgeht die
        # save()-Verschluesselung).
        Client.objects.filter(pk=client.pk).update(data_json={"wohnsituation": "WG", "diagnose": "klartext-diagnose"})

        call_command("reencrypt_fields", stdout=StringIO())

    client.refresh_from_db()
    stored = raw_client_data_json(client)["diagnose"]
    assert is_encrypted_value(stored), "Plaintext-Diagnose muss nach dem Heal-Lauf verschluesselt sein"
    with override_settings(ENCRYPTION_KEY=key):
        assert safe_decrypt(stored) == "klartext-diagnose"


@pytest.mark.django_db
def test_reencrypt_client_without_document_type_is_untouched(facility, staff_user):
    """Ohne ``document_type`` gibt es keine Slug-Whitelist — ``data_json`` bleibt
    unangetastet, der Lauf darf nicht crashen (kein ``document_type.fields``-Zugriff auf None).
    """
    client = Client.objects.create(
        facility=facility,
        pseudonym="P6-NoType-01",
        created_by=staff_user,
        data_json={"irgendwas": "Klartext"},
    )
    before = raw_client_data_json(client)

    call_command("reencrypt_fields", stdout=StringIO())

    client.refresh_from_db()
    assert raw_client_data_json(client) == before


@pytest.mark.django_db
def test_reencrypt_soft_deleted_client_is_rotated(facility, doc_type_client_profile, staff_user):
    """E17: Client/Case rotieren ueber ALLE Rows inkl. soft-deleted — anders als bei
    Events (``is_deleted=False``-Filter bleibt dort bewusst bestehen, bekannte Luecke).
    ``FacilityScopedManager`` filtert ``is_deleted`` nicht, ``Client.objects`` deckt daher
    schon ohne Extra-Filter alle Rows ab.
    """
    old_key = generate_key()
    new_key = generate_key()

    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
        client = Client.objects.create(
            facility=facility,
            pseudonym="P6-SoftDel-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"diagnose": "Geloescht aber rotiert"},
        )
        client.soft_delete(user=staff_user)

    assert Client.objects.get(pk=client.pk).is_deleted is True

    keys = f"{new_key},{old_key}"
    with override_settings(ENCRYPTION_KEYS=keys, ENCRYPTION_KEY=""):
        call_command("reencrypt_fields", stdout=StringIO())

    client.refresh_from_db()
    assert client.is_deleted is True, "Soft-Delete darf durch die Rotation nicht rueckgaengig gemacht werden"
    stored = raw_client_data_json(client)["diagnose"]
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=new_key):
        assert safe_decrypt(stored) == "Geloescht aber rotiert"


@pytest.mark.django_db
def test_reencrypt_soft_deleted_event_is_rotated(facility, client_identified, doc_type_crisis, staff_user):
    """E17 (Refs #1724): ein soft-deleted Event mit noch vorhandenem ``data_json``
    (Altbestand von vor der Retention-Leerung, oder direkt in der DB soft-deleted)
    wird von ``reencrypt_fields`` MIT rotiert.

    Der reguläre Soft-Delete-Pfad (``soft_delete_event``) leert ``data_json``
    heute mit — hier stellen wir den Altbestands-Zustand direkt her
    (``is_deleted=True`` UND unveraendertes ``data_json``), den der reguläre
    Pfad so nicht mehr erzeugt, der aber in der DB liegen bleiben kann
    (bereits soft-deleted VOR Einfuehrung dieser Leerung, oder Restore-Faelle).
    """
    old_key = generate_key()
    new_key = generate_key()

    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Geloescht aber rotiert"},
            created_by=staff_user,
        )
        # Altbestand simulieren: soft-deleted, aber ``data_json`` bleibt (anders
        # als der heutige ``soft_delete_event``, der es leert) — genau die
        # Konstellation, die die E17-Luecke betrifft.
        Event.objects.filter(pk=event.pk).update(is_deleted=True)

    assert Event.objects.get(pk=event.pk).is_deleted is True

    keys = f"{new_key},{old_key}"
    with override_settings(ENCRYPTION_KEYS=keys, ENCRYPTION_KEY=""):
        call_command("reencrypt_fields", stdout=StringIO())

    event.refresh_from_db()
    assert event.is_deleted is True, "Soft-Delete darf durch die Rotation nicht rueckgaengig gemacht werden"
    stored = raw_event_data_json(event)["notiz-krise"]
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=new_key):
        assert safe_decrypt(stored) == "Geloescht aber rotiert"


@pytest.mark.django_db
def test_reencrypt_dry_run_counts_but_does_not_write(facility, doc_type_client_profile, staff_user):
    """``--dry-run`` meldet den Client in der Zaehlung, schreibt aber nichts in die DB."""
    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        client = Client.objects.create(
            facility=facility,
            pseudonym="P6-DryRun-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"diagnose": "Unveraendert"},
        )
    token_before = raw_client_data_json(client)["diagnose"]["value"]

    with override_settings(ENCRYPTION_KEY=key):
        out = StringIO()
        call_command("reencrypt_fields", "--dry-run", stdout=out)

    output = out.getvalue()
    assert "Would re-encrypt" in output
    assert "1 clients" in output

    client.refresh_from_db()
    assert raw_client_data_json(client)["diagnose"]["value"] == token_before, "dry-run darf nichts persistieren"


@pytest.mark.django_db
def test_reencrypt_output_line_includes_client_and_case_counts(
    facility, client_identified, doc_type_client_profile, doc_type_case_form, staff_user
):
    """Die Ausgabezeile des Commands nennt die neuen Client-/Case-Zaehler."""
    key = generate_key()
    with override_settings(ENCRYPTION_KEY=key):
        client = Client.objects.create(
            facility=facility,
            pseudonym="P6-Output-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"diagnose": "Zaehler-Client"},
        )
        Case.objects.create(
            facility=facility,
            client=client_identified,
            title="P6-Output-Fall",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"verlauf": "Zaehler-Case"},
        )
        # Client-Row auf Plaintext zuruecksetzen, damit der Heal-Pfad sie sicher zaehlt
        # (unabhaengig davon, ob der Case-Save allein schon einen Re-Encrypt-Diff erzeugt).
        Client.objects.filter(pk=client.pk).update(data_json={"diagnose": "klartext"})

        out = StringIO()
        call_command("reencrypt_fields", stdout=out)

    output = out.getvalue()
    assert "1 clients" in output
    assert "1 cases" in output


@pytest.mark.django_db
class TestRotateAll:
    """``rotate_all()`` (Refs #1835) — die Business-Logik hinter dem Command.

    Der Command bleibt Thin-Wrapper (Argument-Parsing, RLS-Guard, Ausgabe);
    diese Tests decken die Service-Funktion direkt ab, ohne den Command-Pfad
    zu durchlaufen.
    """

    def test_dict_shape_has_all_nine_counters(self, facility):
        """Acht Pfade plus Kontention landen unter stabilen Schluesseln.

        Refs #2042: ``shift_handover_notes`` ist der achte Pfad — der Pin ist
        bewusst nachgezogen (Schluessel ergaenzt, keine Erwartung gelockert).
        """
        counts = rotate_all(dry_run=True)
        assert counts == {
            "events": 0,
            "event_history": 0,
            "attachments": 0,
            "totp_devices": 0,
            "clients": 0,
            "cases": 0,
            "rectification_requests": 0,
            "shift_handover_notes": 0,
            "contended": 0,
        }

    def test_dry_run_writes_nothing(self, facility, doc_type_client_profile, staff_user):
        """``dry_run=True`` zaehlt den Client, schreibt aber nichts in die DB."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEY=key):
            client = Client.objects.create(
                facility=facility,
                pseudonym="P6-RotateAll-DryRun-01",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"diagnose": "Unveraendert"},
            )
        token_before = raw_client_data_json(client)["diagnose"]["value"]

        with override_settings(ENCRYPTION_KEY=key):
            counts = rotate_all(dry_run=True)

        assert counts["clients"] == 1
        client.refresh_from_db()
        assert raw_client_data_json(client)["diagnose"]["value"] == token_before

    @pytest.mark.django_db(transaction=True)
    def test_all_mutable_paths_bind_cas_and_count_contention(
        self,
        facility,
        client_identified,
        doc_type_crisis,
        doc_type_client_profile,
        doc_type_case_form,
        staff_user,
        monkeypatch,
    ):
        """Alle sieben veraenderlichen Pfade nutzen denselben CAS-Vertrag.

        Refs #2177: ``EventHistory`` ist seit B-12 der sechste Pfad an dieser
        Stelle — vorher schrieb er als einziger unbedingt und tauchte deshalb
        weder in ``calls_by_model`` noch im ``contended``-Zaehler auf.
        Refs #2042: ``ShiftHandoverNote`` kommt als siebter dazu — die
        Erwartung ist additiv verschaerft, keine bestehende Zusage gelockert.
        """
        from core.services.file_vault import rotation as rotation_module

        key = generate_key()
        with override_settings(ENCRYPTION_KEY=key):
            event = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={"notiz-krise": "Event-CAS"},
                created_by=staff_user,
            )
            client = Client.objects.create(
                facility=facility,
                pseudonym="CAS-Client",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"diagnose": "Client-CAS"},
            )
            case = Case.objects.create(
                facility=facility,
                client=client_identified,
                title="CAS-Case",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"verlauf": "Case-CAS"},
            )
            attachment = Attachment.objects.create(
                facility=facility,
                event=event,
                field_template=doc_type_crisis.fields.first().field_template,
                storage_filename="cas-test.enc",
                original_filename_encrypted=encrypt_field("cas-test.pdf"),
                file_size=42,
                mime_type="application/pdf",
                created_by=staff_user,
            )
            rectification = RectificationRequest.objects.create(
                facility=facility,
                target_client=client_identified,
                field_path="notes",
                old_value=encrypt_field('"Alt"'),
                new_value=encrypt_field('"Neu"'),
                reason=encrypt_field("CAS-Grund"),
                review_note=encrypt_field("CAS-Prüfvermerk"),
                requested_by=staff_user,
            )
            history = EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"notiz-krise": encrypt_field("History-alt")},
                data_after={"notiz-krise": encrypt_field("History-neu")},
            )
            handover_note = ShiftHandoverNote.objects.create(
                facility=facility,
                shift_date=timezone.localdate(),
                time_filter=None,
                note=encrypt_field("CAS-Schichtnotiz"),
                author=staff_user,
                updated_by=staff_user,
                **_handover_window(),
            )

            dry_counts = rotate_all(dry_run=True)

        assert dry_counts == {
            "events": 1,
            "event_history": 1,
            "attachments": 1,
            "totp_devices": 0,
            "clients": 1,
            "cases": 1,
            "rectification_requests": 1,
            "shift_handover_notes": 1,
            "contended": 0,
        }

        calls = []

        def reject_cas(model, pk, *, expected, updates):
            calls.append((model, pk, expected, updates))
            return False

        monkeypatch.setattr(rotation_module, "_locked_compare_and_swap", reject_cas)
        with override_settings(ENCRYPTION_KEY=key):
            counts = rotate_all(dry_run=False)

        assert counts == {
            "events": 0,
            "event_history": 0,
            "attachments": 0,
            "totp_devices": 0,
            "clients": 0,
            "cases": 0,
            "rectification_requests": 0,
            "shift_handover_notes": 0,
            "contended": 7,
        }
        calls_by_model = {model: (pk, expected, updates) for model, pk, expected, updates in calls}
        assert set(calls_by_model) == {
            Event,
            Client,
            Case,
            Attachment,
            RectificationRequest,
            EventHistory,
            ShiftHandoverNote,
        }
        assert set(calls_by_model[Event][1]) == {"updated_at", "data_json"}
        assert set(calls_by_model[Event][2]) == {"data_json", "updated_at"}
        assert set(calls_by_model[Client][1]) == {"updated_at", "data_json"}
        assert set(calls_by_model[Case][1]) == {"updated_at", "data_json"}
        assert set(calls_by_model[Attachment][1]) == {"original_filename_encrypted"}
        assert set(calls_by_model[RectificationRequest][1]) == {
            "old_value",
            "new_value",
            "reason",
            "review_note",
        }
        assert set(calls_by_model[EventHistory][1]) == {"data_before", "data_after"}
        assert set(calls_by_model[EventHistory][2]) == {"data_before", "data_after"}
        # Refs #2042: die Schichtnotiz bindet NUR ihre Chiffrat-Spalte — kein
        # ``updated_at``, weil die Retention-Frist an genau diesem Zeitstempel
        # haengt; eine Rotation darf die Aufbewahrungsuhr nicht zurueckdrehen.
        assert set(calls_by_model[ShiftHandoverNote][1]) == {"note"}
        assert set(calls_by_model[ShiftHandoverNote][2]) == {"note"}

        event.refresh_from_db()
        client.refresh_from_db()
        case.refresh_from_db()
        attachment.refresh_from_db()
        rectification.refresh_from_db()
        history_before, history_after = raw_history_payloads(history)
        with override_settings(ENCRYPTION_KEY=key):
            assert safe_decrypt(raw_event_data_json(event)["notiz-krise"]) == "Event-CAS"
            assert safe_decrypt(raw_client_data_json(client)["diagnose"]) == "Client-CAS"
            assert safe_decrypt(raw_case_data_json(case)["verlauf"]) == "Case-CAS"
            assert safe_decrypt(attachment.original_filename_encrypted) == "cas-test.pdf"
            assert safe_decrypt(rectification.review_note) == "CAS-Prüfvermerk"
            assert safe_decrypt(history_before["notiz-krise"]) == "History-alt"
            assert safe_decrypt(history_after["notiz-krise"]) == "History-neu"
            handover_note.refresh_from_db()
            assert safe_decrypt(handover_note.note) == "CAS-Schichtnotiz"

    def test_locked_cas_reports_a_row_deleted_before_lock_as_contention(self):
        from core.services.file_vault.rotation import _locked_compare_and_swap

        assert (
            _locked_compare_and_swap(
                Event,
                uuid4(),
                expected={"updated_at": timezone.now()},
                updates={"data_json": {}, "updated_at": timezone.now()},
            )
            is False
        )

    def test_locked_cas_uses_jsonb_equality_once_without_retry(
        self, facility, client_identified, doc_type_crisis, staff_user
    ):
        from core.services.file_vault.rotation import _locked_compare_and_swap

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        with CaptureQueriesContext(connection) as queries:
            swapped = _locked_compare_and_swap(
                Event,
                event.pk,
                expected={"data_json": {"stale": True}},
                updates={"data_json": {}},
            )

        lock_queries = [query["sql"] for query in queries if "FOR UPDATE" in query["sql"]]
        update_queries = [query["sql"] for query in queries if query["sql"].startswith('UPDATE "core_event"')]
        assert swapped is False
        assert len(lock_queries) == 1
        assert len(update_queries) == 1
        assert '"data_json" = ' in update_queries[0]

    def test_unrotatable_values_are_skipped_without_writes(
        self, facility, client_identified, doc_type_crisis, staff_user
    ):
        from core.services.file_vault.rotation import _rewrap_one_totp_device

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        field_template = doc_type_crisis.fields.first().field_template
        Attachment.objects.create(
            facility=facility,
            event=event,
            field_template=field_template,
            storage_filename="plain-marker.enc",
            original_filename_encrypted={"legacy": "plain"},
            file_size=1,
            mime_type="application/pdf",
            created_by=staff_user,
        )
        Attachment.objects.create(
            facility=facility,
            event=event,
            field_template=field_template,
            storage_filename="corrupt-marker.enc",
            original_filename_encrypted={"__encrypted__": True, "value": "corrupt"},
            file_size=1,
            mime_type="application/pdf",
            created_by=staff_user,
        )
        RectificationRequest.objects.create(
            facility=facility,
            target_client=client_identified,
            field_path="notes",
            requested_by=staff_user,
        )
        RectificationRequest.objects.create(
            facility=facility,
            target_client=client_identified,
            field_path="pseudonym",
            old_value={"__encrypted__": True, "value": "corrupt"},
            requested_by=staff_user,
        )
        EventHistory.objects.create(
            event=event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
            data_before={},
            data_after={},
        )

        counts = rotate_all(dry_run=False)

        assert counts == {
            "events": 0,
            "event_history": 0,
            "attachments": 0,
            "totp_devices": 0,
            "clients": 0,
            "cases": 0,
            "rectification_requests": 0,
            "shift_handover_notes": 0,
            "contended": 0,
        }
        assert _rewrap_one_totp_device(2_147_483_647, lambda value: value, lambda value: value) == "empty"

    def test_history_rotation_rewraps_both_columns_without_contention(
        self, facility, client_identified, doc_type_crisis, staff_user
    ):
        """Refs #2177: der ungestoerte Historienpfad rotiert weiterhin beide Spalten.

        Der Gegenbeweis zum Rennen-Test: das Compare-and-Swap darf den
        Normalfall nicht ausbremsen — beide Chiffrate wechseln, der Klartext
        bleibt, und niemand wird als Kontention gemeldet.
        """
        old_key = generate_key()
        new_key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
            event = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
            history = EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"notiz-krise": encrypt_field("Vorher-Wortlaut")},
                data_after={"notiz-krise": encrypt_field("Nachher-Wortlaut")},
            )
        before_token, after_token = raw_history_payloads(history)

        with override_settings(ENCRYPTION_KEYS=f"{new_key},{old_key}", ENCRYPTION_KEY=""):
            counts = rotate_all(dry_run=False)
            rotated_before, rotated_after = raw_history_payloads(history)

            assert safe_decrypt(rotated_before["notiz-krise"]) == "Vorher-Wortlaut"
            assert safe_decrypt(rotated_after["notiz-krise"]) == "Nachher-Wortlaut"

        assert counts["event_history"] == 1
        assert counts["contended"] == 0
        assert rotated_before["notiz-krise"]["value"] != before_token["notiz-krise"]["value"]
        assert rotated_after["notiz-krise"]["value"] != after_token["notiz-krise"]["value"]

    def test_history_row_deleted_during_the_run_is_skipped_silently(
        self, facility, client_identified, doc_type_crisis, staff_user, monkeypatch
    ):
        """Refs #2177: eine waehrend des Laufs geloeschte Zeile ist keine Kontention.

        Die zweite Stufe des Aufbewahrungslaufs
        (``delete_redacted_event_history_rows``) entfernt redigierte Zeilen
        nach Fristablauf — sie kann also zwischen der pk-Sammlung und dem
        Lesen einer Zeile committen. Dann gibt es nichts mehr zu rotieren und
        auch nichts zu melden: der Lauf ueberspringt die pk still.
        """
        from core.services.file_vault import rotation as rotation_module
        from core.services.system import bypass_replication_triggers

        key = generate_key()
        with override_settings(ENCRYPTION_KEY=key):
            event = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
            EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"notiz-krise": encrypt_field("Erste Fassung")},
                data_after=None,
            )
            EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"notiz-krise": encrypt_field("Zweite Fassung")},
                data_after=None,
            )

            original_reencrypt = rotation_module._reencrypt_data_json
            state = {"deleted": False}

            def delete_the_other_row(payload):
                # Beim Lesen der ersten Historienzeile verschwindet die andere —
                # unabhaengig davon, welche der beiden zuerst drankommt.
                if not state["deleted"] and isinstance(payload, dict) and "notiz-krise" in payload:
                    state["deleted"] = True
                    with bypass_replication_triggers(tables=("core_eventhistory",)):
                        EventHistory.objects.exclude(data_before=payload).delete()
                return original_reencrypt(payload)

            monkeypatch.setattr(rotation_module, "_reencrypt_data_json", delete_the_other_row)

            counts = rotate_all(dry_run=False)

        assert state["deleted"]
        assert EventHistory.objects.count() == 1
        assert counts["event_history"] == 1
        assert counts["contended"] == 0

    def test_history_row_redacted_before_the_run_is_left_untouched(
        self, facility, client_identified, doc_type_crisis, staff_user
    ):
        """Refs #2177: eine vor dem Lauf redigierte Zeile ist kein Rotationskandidat.

        Der Marker traegt keine Chiffrate mehr — ``_reencrypt_data_json``
        meldet ``changed = False``, die Zeile faellt vor jedem Schreibversuch
        aus der Schleife. Die Redaktion gewinnt hier also ohne Sonderlogik.
        """
        from core.retention.eventhistory import redact_event_history_rows

        key = generate_key()
        with override_settings(ENCRYPTION_KEY=key):
            event = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
            history = EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"notiz-krise": encrypt_field("Vorher-Wortlaut")},
                data_after={"notiz-krise": encrypt_field("Nachher-Wortlaut")},
            )
            redact_event_history_rows(facility, [history.pk], category="test-2177")
            redacted_before, redacted_after = raw_history_payloads(history)

            counts = rotate_all(dry_run=False)

            assert decryptable_values(redacted_before) == []
            assert decryptable_values(redacted_after) == []

        history.refresh_from_db()
        assert history.redacted_at is not None
        assert counts["event_history"] == 0
        assert counts["contended"] == 0
        assert raw_history_payloads(history) == (redacted_before, redacted_after)

    def test_on_totp_contended_callback_not_invoked_without_contention(self, facility):
        """Der optionale Kontentions-Callback bleibt bei einem sauberen Lauf ungerufen."""
        calls = []
        rotate_all(dry_run=True, on_totp_contended=calls.append)
        assert calls == []


@pytest.mark.django_db
class TestEventHistoryRotationTriggerSelection:
    def test_rotation_laesst_das_auditlog_geschlossen(
        self, facility, client_identified, doc_type_crisis, staff_user, monkeypatch
    ):
        """Refs #2184: Die Rotation oeffnet nur die EventHistory-Trigger."""
        from core.services.system import _db_admin

        old_key = generate_key()
        new_key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
            event = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
            EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"notiz-krise": encrypt_field("Vorher-Wortlaut")},
                data_after={"notiz-krise": encrypt_field("Nachher-Wortlaut")},
            )

        opened: list[tuple[str, str]] = []
        original = _db_admin.bypass_replication_triggers

        def _recording(tables=None):
            opened.extend(_db_admin._selected_triggers(tables))
            return original(tables=tables)

        monkeypatch.setattr("core.services.system.bypass_replication_triggers", _recording)

        with override_settings(ENCRYPTION_KEYS=f"{new_key},{old_key}", ENCRYPTION_KEY=""):
            counts = rotate_all(dry_run=False)

        assert counts["event_history"] == 1
        assert opened
        assert {table for table, _trigger in opened} == {"core_eventhistory"}


@pytest.mark.django_db(transaction=True)
class TestRotationCompareAndSwapRace:
    """Refs #2131: parallele Fachänderungen gewinnen gegen die Rotation."""

    def _run_stampless_redaction_race(
        self,
        *,
        target,
        model,
        payload_slug,
        redact,
        raw_data_json,
        counter,
        old_key,
        new_key,
        monkeypatch,
    ):
        from core.services.file_vault import rotation as rotation_module

        user_holds_lock = threading.Event()
        rotation_has_read = threading.Event()
        user_committed = threading.Event()
        errors = {}
        result = {}
        original_reencrypt = rotation_module._reencrypt_data_json

        def synchronized_reencrypt(payload):
            healed = original_reencrypt(payload)
            if isinstance(payload, dict) and payload_slug in payload and not rotation_has_read.is_set():
                rotation_has_read.set()
                if not user_committed.wait(timeout=_JOIN_TIMEOUT):
                    errors["sync"] = RuntimeError("Stempellose Redaktion wurde nicht rechtzeitig committed")
            return healed

        monkeypatch.setattr(rotation_module, "_reencrypt_data_json", synchronized_reencrypt)

        def save_redaction_without_timestamp():
            connection.close()
            try:
                with transaction.atomic():
                    current = model.objects.select_for_update().get(pk=target.pk)
                    user_holds_lock.set()
                    if not rotation_has_read.wait(timeout=_JOIN_TIMEOUT):
                        errors["redaction"] = RuntimeError("Rotation hat die Zielzeile nicht gelesen")
                    else:
                        redact(current)
            except Exception as exc:  # noqa: BLE001
                errors["redaction"] = exc
            finally:
                user_committed.set()
                connections.close_all()

        def run_rotation():
            connection.close()
            try:
                if not user_holds_lock.wait(timeout=_JOIN_TIMEOUT):
                    errors["rotation"] = RuntimeError("Redaktions-Faden bekam die Zeilensperre nicht")
                    return
                result["counts"] = rotation_module.rotate_all(dry_run=False)
            except Exception as exc:  # noqa: BLE001
                errors["rotation"] = exc
            finally:
                connections.close_all()

        keys = f"{new_key},{old_key}"
        with override_settings(ENCRYPTION_KEYS=keys, ENCRYPTION_KEY=""):
            redaction_thread = threading.Thread(
                target=save_redaction_without_timestamp,
                name=f"rotation-{counter}-redaction",
            )
            rotation_thread = threading.Thread(target=run_rotation, name=f"rotation-{counter}-command")
            redaction_thread.start()
            if not user_holds_lock.wait(timeout=_JOIN_TIMEOUT):
                errors["main"] = RuntimeError("Redaktions-Faden bekam die Zeilensperre nicht")
            rotation_thread.start()

            redaction_thread.join(timeout=_JOIN_TIMEOUT)
            rotation_thread.join(timeout=_JOIN_TIMEOUT)

        assert not redaction_thread.is_alive() and not rotation_thread.is_alive(), "Verklemmung im Redaktionsrennen"
        assert not errors, errors
        target.refresh_from_db()
        assert raw_data_json(target) == {}
        assert target.document_type_id is None
        assert result["counts"][counter] == 0
        assert result["counts"]["contended"] == 1

    def test_client_anonymization_without_timestamp_wins(
        self, facility, doc_type_client_profile, staff_user, monkeypatch
    ):
        from core.services.client.main import _redact_client_identity

        old_key = generate_key()
        new_key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
            client = Client.objects.create(
                facility=facility,
                pseudonym="CAS-Client-Redaktion",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"diagnose": "Diese PII muss getilgt bleiben"},
            )

        self._run_stampless_redaction_race(
            target=client,
            model=Client,
            payload_slug="diagnose",
            redact=_redact_client_identity,
            raw_data_json=raw_client_data_json,
            counter="clients",
            old_key=old_key,
            new_key=new_key,
            monkeypatch=monkeypatch,
        )

    def test_case_redaction_without_timestamp_wins(
        self, facility, client_identified, doc_type_case_form, staff_user, monkeypatch
    ):
        from core.services.case.deletion import redact_case_content

        old_key = generate_key()
        new_key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
            case = Case.objects.create(
                facility=facility,
                client=client_identified,
                title="CAS-Fall-Redaktion",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"verlauf": "Diese Fall-PII muss getilgt bleiben"},
            )

        self._run_stampless_redaction_race(
            target=case,
            model=Case,
            payload_slug="verlauf",
            redact=redact_case_content,
            raw_data_json=raw_case_data_json,
            counter="cases",
            old_key=old_key,
            new_key=new_key,
            monkeypatch=monkeypatch,
        )

    def test_user_save_between_rotation_read_and_write_wins(
        self, facility, client_identified, doc_type_crisis, staff_user, monkeypatch
    ):
        from core.services.file_vault import rotation as rotation_module

        old_key = generate_key()
        new_key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
            event = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={"notiz-krise": "Stand vor dem Rennen"},
                created_by=staff_user,
            )

        user_holds_lock = threading.Event()
        rotation_has_read = threading.Event()
        user_committed = threading.Event()
        errors = {}
        result = {}
        original_reencrypt = rotation_module._reencrypt_data_json

        def synchronized_reencrypt(payload):
            healed = original_reencrypt(payload)
            if isinstance(payload, dict) and "notiz-krise" in payload and not rotation_has_read.is_set():
                rotation_has_read.set()
                if not user_committed.wait(timeout=_JOIN_TIMEOUT):
                    errors["sync"] = RuntimeError("Nutzer-Save wurde nicht rechtzeitig committed")
            return healed

        monkeypatch.setattr(rotation_module, "_reencrypt_data_json", synchronized_reencrypt)

        def save_user_change():
            connection.close()
            try:
                with transaction.atomic():
                    current = Event.objects.select_for_update().get(pk=event.pk)
                    user_holds_lock.set()
                    if not rotation_has_read.wait(timeout=_JOIN_TIMEOUT):
                        errors["user"] = RuntimeError("Rotation hat die Event-Zeile nicht gelesen")
                    else:
                        current.data_json = {"notiz-krise": "Nutzerstand gewinnt"}
                        current.save(update_fields=["data_json", "updated_at"])
            except Exception as exc:  # noqa: BLE001
                errors["user"] = exc
            finally:
                user_committed.set()
                connections.close_all()

        def run_rotation():
            connection.close()
            try:
                if not user_holds_lock.wait(timeout=_JOIN_TIMEOUT):
                    errors["rotation"] = RuntimeError("Nutzer-Faden bekam die Zeilensperre nicht")
                    return
                result["counts"] = rotation_module.rotate_all(dry_run=False)
            except Exception as exc:  # noqa: BLE001
                errors["rotation"] = exc
            finally:
                connections.close_all()

        keys = f"{new_key},{old_key}"
        with override_settings(ENCRYPTION_KEYS=keys, ENCRYPTION_KEY=""):
            user_thread = threading.Thread(target=save_user_change, name="rotation-user-save")
            rotation_thread = threading.Thread(target=run_rotation, name="rotation-command")
            user_thread.start()
            assert user_holds_lock.wait(timeout=_JOIN_TIMEOUT), "Nutzer-Faden bekam die Zeilensperre nicht"
            rotation_thread.start()

            user_thread.join(timeout=_JOIN_TIMEOUT)
            rotation_thread.join(timeout=_JOIN_TIMEOUT)
            assert not user_thread.is_alive() and not rotation_thread.is_alive(), "Verklemmung im Rotationsrennen"
            assert not errors, errors

            event.refresh_from_db()
            user_token = raw_event_data_json(event)["notiz-krise"]
            assert safe_decrypt(user_token) == "Nutzerstand gewinnt"
            assert result["counts"]["events"] == 0
            assert result["counts"]["contended"] == 1

            monkeypatch.setattr(rotation_module, "_reencrypt_data_json", original_reencrypt)
            rerun = rotation_module.rotate_all(dry_run=False)
            event.refresh_from_db()
            rerun_token = raw_event_data_json(event)["notiz-krise"]

            assert rerun["events"] == 1
            assert rerun["contended"] == 0
            assert rerun_token["value"] != user_token["value"]
            assert safe_decrypt(rerun_token) == "Nutzerstand gewinnt"

    def test_history_redaction_between_read_and_write_wins(
        self, facility, client_identified, doc_type_crisis, staff_user, monkeypatch
    ):
        """Refs #2177 (Red-Team B-12): der Retentionssweep gewinnt gegen die Rotation.

        Beobachtend gebaut: der Redaktions-Sweep committet auf einer eigenen
        Verbindung genau zwischen Lesen und Schreiben der Rotation; geprueft
        wird NACH dem Rennen, im gesperrten Fenster steht kein ``assert``.

        Ohne Row-Lock und Compare-and-Swap schrieb die Rotation ihre Kopie des
        alten Payloads unbedingt zurueck: der redigierte Wortlaut war wieder
        entschluesselbar, ``redacted_at`` blieb gesetzt — die Zeile fiel damit
        dauerhaft aus dem Idempotenz-Filter des Sweeps
        (``redacted_at__isnull=True``) und hatte keinen Heilungspfad mehr.
        """
        from core.retention.eventhistory import redact_event_history_rows
        from core.services.events.history_diff import is_redaction_marker
        from core.services.file_vault import rotation as rotation_module

        old_key = generate_key()
        new_key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
            event = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
            target = EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"notiz-krise": encrypt_field("Vorher-Wortlaut-Art17")},
                data_after={"notiz-krise": encrypt_field("Nachher-Wortlaut-Art17")},
            )
            # Nachbarzeile ohne den Sync-Slug: sie belegt, dass die Rotation nur
            # die umkaempfte Zeile ueberspringt und nicht den ganzen Lauf.
            untouched = EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"unbeteiligt": encrypt_field("Nachbarzeile")},
                data_after=None,
            )

        rotation_has_read = threading.Event()
        redaction_committed = threading.Event()
        errors = {}
        result = {}
        original_reencrypt = rotation_module._reencrypt_data_json

        def synchronized_reencrypt(payload):
            healed = original_reencrypt(payload)
            if isinstance(payload, dict) and "notiz-krise" in payload and not rotation_has_read.is_set():
                rotation_has_read.set()
                if not redaction_committed.wait(timeout=_JOIN_TIMEOUT):
                    errors["sync"] = RuntimeError("Redaktion wurde nicht rechtzeitig committed")
            return healed

        monkeypatch.setattr(rotation_module, "_reencrypt_data_json", synchronized_reencrypt)

        def commit_redaction():
            connection.close()
            try:
                if not rotation_has_read.wait(timeout=_JOIN_TIMEOUT):
                    errors["redaction"] = RuntimeError("Rotation hat die Historienzeile nicht gelesen")
                    return
                redact_event_history_rows(facility, [target.pk], category="test-2177")
            except Exception as exc:  # noqa: BLE001
                errors["redaction"] = exc
            finally:
                redaction_committed.set()
                connections.close_all()

        def run_rotation():
            connection.close()
            try:
                result["counts"] = rotation_module.rotate_all(dry_run=False)
            except Exception as exc:  # noqa: BLE001
                errors["rotation"] = exc
            finally:
                connections.close_all()

        with override_settings(ENCRYPTION_KEYS=f"{new_key},{old_key}", ENCRYPTION_KEY=""):
            redaction_thread = threading.Thread(target=commit_redaction, name="rotation-history-redaction")
            rotation_thread = threading.Thread(target=run_rotation, name="rotation-history-command")
            redaction_thread.start()
            rotation_thread.start()

            redaction_thread.join(timeout=_JOIN_TIMEOUT)
            rotation_thread.join(timeout=_JOIN_TIMEOUT)

            assert not redaction_thread.is_alive() and not rotation_thread.is_alive(), "Verklemmung im Historienrennen"
            assert not errors, errors

            target.refresh_from_db()
            redacted_before, redacted_after = raw_history_payloads(target)

            assert is_redaction_marker(redacted_before)
            assert is_redaction_marker(redacted_after)
            assert decryptable_values(redacted_before) == []
            assert decryptable_values(redacted_after) == []
            assert target.redacted_at is not None
            assert result["counts"]["event_history"] == 1
            assert result["counts"]["contended"] == 1

            monkeypatch.setattr(rotation_module, "_reencrypt_data_json", original_reencrypt)
            rerun = rotation_module.rotate_all(dry_run=False)

            assert rerun["contended"] == 0
            assert rerun["event_history"] == 1
            assert raw_history_payloads(target) == (redacted_before, redacted_after)
            assert safe_decrypt(raw_history_payloads(untouched)[0]["unbeteiligt"]) == "Nachbarzeile"


@pytest.mark.django_db
def test_command_warns_about_contended_rows_and_exits_successfully(monkeypatch):
    from core.management.commands import reencrypt_fields as cmd

    monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: True)
    monkeypatch.setattr(
        cmd,
        "rotate_all",
        lambda **_kwargs: {
            "events": 0,
            "event_history": 0,
            "attachments": 0,
            "totp_devices": 0,
            "clients": 0,
            "cases": 0,
            "rectification_requests": 0,
            "shift_handover_notes": 0,
            "contended": 2,
        },
    )
    stderr = StringIO()

    call_command("reencrypt_fields", stdout=StringIO(), stderr=stderr)

    assert "2 Zeilen wegen paralleler Änderung übersprungen — Lauf wiederholen" in stderr.getvalue()


@pytest.mark.django_db
class TestReencryptFieldsRlsBypassGuard:
    """Final-Review A/Split (Refs #1058): ``reencrypt_fields`` iteriert
    ``Event``/``EventHistory``/``Attachment``/``Client``/``Case`` — alles
    FORCE-RLS-Tabellen. Als App-Rolle (NOBYPASSRLS, kein
    ``app.current_facility_id``) sähe der Lauf 0 Zeilen, meldete aber Erfolg;
    die Rotations-Prozedur im Admin-Guide würde daraufhin den Alt-Schlüssel
    entfernen. Fail-loud wie bei den Schwester-Commands."""

    def test_aborts_loud_without_bypass_context(self, monkeypatch):
        from core.management.commands import reencrypt_fields as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: False)
        with pytest.raises(CommandError, match="RLS"):
            call_command("reencrypt_fields", stdout=StringIO())

    def test_runs_through_with_bypass_context(self, monkeypatch):
        from core.management.commands import reencrypt_fields as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: True)
        out = StringIO()
        call_command("reencrypt_fields", stdout=out)
        assert "Re-encrypted" in out.getvalue()


@pytest.mark.django_db
class TestShiftHandoverNoteRotation:
    """Achter Rotationspfad: die manuelle Schichtnotiz (Refs #2042).

    Ohne ihn liesse sich ein ausrangierter Schluessel nie entfernen — die
    Notiz haelt Freitext ueber Personen bis zum Ablauf ihrer Frist.
    """

    @staticmethod
    def _note(facility, staff_user, text):
        return ShiftHandoverNote.objects.create(
            facility=facility,
            shift_date=timezone.localdate(),
            time_filter=None,
            note=encrypt_field(text),
            author=staff_user,
            updated_by=staff_user,
            **_handover_window(),
        )

    def test_rotation_wechselt_das_chiffrat_und_haelt_den_klartext(self, facility, staff_user):
        key_alt = generate_key()
        key_neu = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key_alt):
            note = self._note(facility, staff_user, "Schichtnotiz-Rotation")
            token_vorher = note.note["value"]

        with override_settings(ENCRYPTION_KEYS=f"{key_neu},{key_alt}", ENCRYPTION_KEY=""):
            counts = rotate_all(dry_run=False)
            assert counts["shift_handover_notes"] == 1
            note.refresh_from_db()
            assert note.note["value"] != token_vorher
            assert safe_decrypt(note.note) == "Schichtnotiz-Rotation"

    def test_dry_run_zaehlt_ohne_zu_schreiben(self, facility, staff_user):
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            note = self._note(facility, staff_user, "Schichtnotiz-DryRun")
            token_vorher = note.note["value"]
            assert rotate_all(dry_run=True)["shift_handover_notes"] == 1
            note.refresh_from_db()
            assert note.note["value"] == token_vorher

    def test_leere_notiz_wird_uebersprungen(self, facility, staff_user):
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            ShiftHandoverNote.objects.create(
                facility=facility,
                shift_date=timezone.localdate(),
                time_filter=None,
                note=None,
                author=staff_user,
                **_handover_window(),
            )
            assert rotate_all(dry_run=False)["shift_handover_notes"] == 0

    def test_zeile_zwischen_lesen_und_schreiben_geloescht_zaehlt_als_kontention(
        self, facility, staff_user, monkeypatch
    ):
        """Beobachtender Race-Guard: die Zeile verschwindet vor dem CAS."""
        from core.services.file_vault import rotation as rotation_module

        key_alt = generate_key()
        key_neu = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key_alt):
            note = self._note(facility, staff_user, "Schichtnotiz-Race")

        echtes_cas = rotation_module._locked_compare_and_swap

        def loescht_dann_cas(model, pk, *, expected, updates):
            if model is ShiftHandoverNote:
                ShiftHandoverNote.objects.filter(pk=pk).delete()
            return echtes_cas(model, pk, expected=expected, updates=updates)

        monkeypatch.setattr(rotation_module, "_locked_compare_and_swap", loescht_dann_cas)
        with override_settings(ENCRYPTION_KEYS=f"{key_neu},{key_alt}", ENCRYPTION_KEY=""):
            counts = rotate_all(dry_run=False)

        assert counts["shift_handover_notes"] == 0
        assert counts["contended"] == 1
        assert not ShiftHandoverNote.objects.filter(pk=note.pk).exists()

    def test_ausgabezeile_des_commands_nennt_die_schichtnotizen(self, facility, staff_user, monkeypatch):
        from core.management.commands import reencrypt_fields as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: True)
        monkeypatch.setattr(
            cmd,
            "rotate_all",
            lambda **_kwargs: {
                "events": 0,
                "event_history": 0,
                "attachments": 0,
                "totp_devices": 0,
                "clients": 0,
                "cases": 0,
                "rectification_requests": 0,
                "shift_handover_notes": 3,
                "contended": 0,
            },
        )
        out = StringIO()
        call_command("reencrypt_fields", stdout=out)
        assert "3 handover notes" in out.getvalue()
