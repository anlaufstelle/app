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
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from core.models import Case, Client, Event
from core.services.file_vault import generate_key, is_encrypted_value, safe_decrypt
from core.services.file_vault.rotation import rotate_all


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

    def test_dict_shape_has_all_seven_counters(self, facility):
        """Die sieben Rotationspfade landen unter stabilen, benannten Schluesseln."""
        counts = rotate_all(dry_run=True)
        assert counts == {
            "events": 0,
            "event_history": 0,
            "attachments": 0,
            "totp_devices": 0,
            "clients": 0,
            "cases": 0,
            "rectification_requests": 0,
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

    def test_on_totp_contended_callback_not_invoked_without_contention(self, facility):
        """Der optionale Kontentions-Callback bleibt bei einem sauberen Lauf ungerufen."""
        calls = []
        rotate_all(dry_run=True, on_totp_contended=calls.append)
        assert calls == []


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
