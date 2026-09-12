"""Pin-Tests zum Meilenstein release-0.98 (#2193) — Modul-Batch ``file_vault.rotation``.

Quelle: ``src/core/services/file_vault/rotation.py`` (Business-Logik des
``reencrypt_fields``-Command). Vorgabe ist die Prio-2-Survivor-Triage aus
``reports/c-scratch/batch-vault.json`` (Quelle ``triage-096-vault-rot-storage.json``):
26 Ueberlebende, verteilt auf sechs Rotationspfade (Attachments,
EventHistory, Events, RectificationRequests, TOTP-Geraete, Client/Case ueber
``_rotate_dynamic_json_model``). Zwei wiederkehrende Muster:

* **Ein ``continue``-vs-``break`` bei einer waehrend des Laufs verschwundenen
  Zeile.** ``test_history_row_deleted_during_the_run_is_skipped_silently``
  (``test_reencrypt_fields.py``) deckt dieses Muster fuer EventHistory schon
  ab, aber mit nur ZWEI Zeilen, bei denen die verschwundene Zeile immer die
  LETZTE der Iteration ist — dort ist ein ``break`` von einem ``continue``
  nicht zu unterscheiden, weil nach der geloeschten Zeile ohnehin nichts
  mehr folgt. Die Pins hier nutzen daher DREI Zeilen (bzw. drei Clients) und
  loeschen die MITTLERE, damit ein ``break`` sichtbar eine dritte,
  eigentlich noch faellige Zeile verschluckt.
* **``updated += 1`` gegen ``= 1``/``-= 1``/``+= 2``, sowohl im
  ``--dry-run``-Zweig als auch im echten Schreibpfad.** Alle bestehenden
  Zaehl-Tests arbeiten mit genau EINER rotierbaren Zeile je Modell, wodurch
  jede Zaehler-Mutation unbemerkt bleibt; die Pins hier legen jeweils ZWEI
  rotierbare Zeilen an.

Reihenfolge-Kontrolle: ``Attachment``/``RectificationRequest`` ordnen nach
``-created_at``, ``EventHistory`` nach ``-changed_at`` (alle absteigend,
neueste zuerst) und ``Client`` nach ``pseudonym`` (aufsteigend). ``Event``
ordnet nach ``-occurred_at``, das direkt gesetzt werden kann. Fuer die
Auto-``now_add``-Zeitstempel wird nach dem Anlegen explizit per
``.update(...)`` zurueckdatiert (bei ``EventHistory`` innerhalb von
``bypass_replication_triggers``, da die Tabelle sonst append-only ist).

Konventionen wie ``test_mutation_pins_096_rotation.py``: exakte Vergleiche
(``==``), echte Model-Objekte, Fixtures aus ``src/tests/conftest.py``,
Roh-Helfer aus ``src/tests/test_reencrypt_fields.py``. Jeder Test nennt seine
gepinnten Mutanten in der Docstring. Es wird bewusst NUR EIN aktiver
Verschluesselungsschluessel (Fixture ``_encryption_key``) benutzt, keine
Alt-/Neu-Rotation: Fernet ist nicht deterministisch, jede
Neuverschluesselung ist daher sichtbar, auch ohne echten Schluesselwechsel.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.utils import timezone

from core.models import Client, EncryptedTOTPDevice, Event, EventHistory, RectificationRequest
from core.models.attachment import Attachment
from core.services.file_vault import EncryptionError, encrypt_field, safe_decrypt
from core.services.file_vault import rotation as rotation_module
from core.services.file_vault.rotation import (
    _reencrypt_attachments,
    _reencrypt_event_history,
    _reencrypt_events,
    _reencrypt_rectification_requests,
    _reencrypt_totp_devices,
    _rotate_dynamic_json_model,
)
from core.services.system import bypass_replication_triggers
from tests.test_reencrypt_fields import raw_client_data_json, raw_history_payloads

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# _reencrypt_attachments
# ---------------------------------------------------------------------------


def test_attachment_row_deleted_between_pk_collection_and_reread_skips_only_that_pk(
    facility, sample_event, field_template, staff_user, monkeypatch
):
    """Pins x__reencrypt_attachments__mutmut_15 (``if att is None: continue`` -> ``break``).

    Drei Attachments; die ZWEITE Zeile verschwindet, waehrend die erste
    verarbeitet wird (Owner-Delete/Anonymisierung laeuft parallel). Der
    Original-Code ueberspringt nur die verschwundene pk und rotiert die
    DRITTE Zeile noch (``counts == 2``); der Mutant bricht die gesamte
    Rotation ab, sobald eine Zeile fehlt (``counts == 1``).
    """
    attachments = [
        Attachment.objects.create(
            facility=facility,
            event=sample_event,
            field_template=field_template,
            storage_filename=f"pin-{i}.enc",
            original_filename_encrypted=encrypt_field(f"pin-{i}.pdf"),
            file_size=1,
            mime_type="application/pdf",
            created_by=staff_user,
        )
        for i in range(3)
    ]
    now = timezone.now()
    for i, attachment in enumerate(attachments):
        Attachment.objects.filter(pk=attachment.pk).update(created_at=now - timedelta(seconds=i))
    # ordering = ["-created_at"] => Iterationsreihenfolge attachments[0..2]
    to_delete = attachments[1].pk
    original = rotation_module.is_encrypted_value
    state = {"fired": False}

    def delete_second_then_check(value):
        if not state["fired"]:
            state["fired"] = True
            Attachment.objects.filter(pk=to_delete).delete()
        return original(value)

    monkeypatch.setattr(rotation_module, "is_encrypted_value", delete_second_then_check)

    updated = rotation_module._reencrypt_attachments(dry_run=False)

    assert state["fired"]
    assert not Attachment.objects.filter(pk=to_delete).exists()
    assert updated == 2


def test_attachment_dry_run_counts_every_rotatable_row_not_just_the_first(
    facility, sample_event, field_template, staff_user
):
    """Pins x__reencrypt_attachments__mutmut_26 (``updated += 1`` -> ``= 1``) und
    __mutmut_29 (``continue`` -> ``break`` im Dry-Run-Zweig).

    Zwei rotierbare Attachments im Dry-Run: der Original-Code zaehlt beide
    (``2``); beide Mutanten liefern stattdessen ``1``.
    """
    attachments = [
        Attachment.objects.create(
            facility=facility,
            event=sample_event,
            field_template=field_template,
            storage_filename=f"dry-{i}.enc",
            original_filename_encrypted=encrypt_field(f"dry-{i}.pdf"),
            file_size=1,
            mime_type="application/pdf",
            created_by=staff_user,
        )
        for i in range(2)
    ]

    updated = _reencrypt_attachments(dry_run=True)

    assert updated == 2
    for i, attachment in enumerate(attachments):
        attachment.refresh_from_db()
        assert safe_decrypt(attachment.original_filename_encrypted) == f"dry-{i}.pdf"


def test_attachment_real_rotation_counts_every_updated_row(facility, sample_event, field_template, staff_user):
    """Pins x__reencrypt_attachments__mutmut_43/44/45 (Zaehler ``= 1``/``-= 1``/``+= 2``
    statt ``+= 1`` im echten Schreibpfad).

    Zwei ECHTE Attachments werden rotiert (kein CAS-Monkeypatch) — der
    Original-Zaehler ist ``2``, alle drei Mutanten weichen davon ab.
    """
    attachments = [
        Attachment.objects.create(
            facility=facility,
            event=sample_event,
            field_template=field_template,
            storage_filename=f"real-{i}.enc",
            original_filename_encrypted=encrypt_field(f"real-{i}.pdf"),
            file_size=1,
            mime_type="application/pdf",
            created_by=staff_user,
        )
        for i in range(2)
    ]

    updated = _reencrypt_attachments(dry_run=False)

    assert updated == 2
    for i, attachment in enumerate(attachments):
        attachment.refresh_from_db()
        assert safe_decrypt(attachment.original_filename_encrypted) == f"real-{i}.pdf"


# ---------------------------------------------------------------------------
# _reencrypt_event_history
# ---------------------------------------------------------------------------


def test_history_row_deleted_between_pk_collection_and_reread_skips_only_that_pk(
    facility, client_identified, doc_type_crisis, staff_user, monkeypatch
):
    """Pins x__reencrypt_event_history__mutmut_15 (``if hist is None: continue`` -> ``break``).

    DREI EventHistory-Zeilen; die verschwindende Zeile liegt in der Mitte
    (nicht wie im Bestandstest
    ``test_history_row_deleted_during_the_run_is_skipped_silently`` als
    LETZTE) — dort ist ein ``break`` von einem ``continue`` nicht zu
    unterscheiden, weil danach ohnehin nichts mehr folgt.
    """
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_crisis,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    histories = [
        EventHistory.objects.create(
            event=event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
            data_before={"notiz-krise": encrypt_field(f"Fassung-{i}")},
            data_after=None,
        )
        for i in range(3)
    ]
    now = timezone.now()
    with bypass_replication_triggers(tables=("core_eventhistory",)):
        for i, history in enumerate(histories):
            EventHistory.objects.filter(pk=history.pk).update(changed_at=now - timedelta(seconds=i))
    # ordering = ["-changed_at"] => Iterationsreihenfolge histories[0..2]
    to_delete = histories[1].pk
    original = rotation_module._reencrypt_data_json
    state = {"fired": False}

    def delete_second_then_check(payload):
        if not state["fired"] and isinstance(payload, dict) and "notiz-krise" in payload:
            state["fired"] = True
            with bypass_replication_triggers(tables=("core_eventhistory",)):
                EventHistory.objects.filter(pk=to_delete).delete()
        return original(payload)

    monkeypatch.setattr(rotation_module, "_reencrypt_data_json", delete_second_then_check)

    updated = rotation_module._reencrypt_event_history(dry_run=False)

    assert state["fired"]
    assert EventHistory.objects.count() == 2
    assert updated == 2


def test_history_dry_run_counts_every_rotatable_row_not_just_the_first(
    facility, client_identified, doc_type_crisis, staff_user
):
    """Pins x__reencrypt_event_history__mutmut_23 (``updated += 1`` -> ``= 1``) und
    __mutmut_26 (``continue`` -> ``break`` im Dry-Run-Zweig)."""
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_crisis,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    for i in range(2):
        EventHistory.objects.create(
            event=event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
            data_before={"notiz-krise": encrypt_field(f"Fassung-{i}")},
            data_after=None,
        )

    updated = _reencrypt_event_history(dry_run=True)

    assert updated == 2


def test_history_real_rotation_counts_every_updated_row(facility, client_identified, doc_type_crisis, staff_user):
    """Pins x__reencrypt_event_history__mutmut_50 (Zaehler ``= 1`` statt ``+= 1``)."""
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_crisis,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    histories = [
        EventHistory.objects.create(
            event=event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
            data_before={"notiz-krise": encrypt_field(f"Vorher-{i}")},
            data_after={"notiz-krise": encrypt_field(f"Nachher-{i}")},
        )
        for i in range(2)
    ]

    updated = _reencrypt_event_history(dry_run=False)

    assert updated == 2
    for i, history in enumerate(histories):
        before, after = raw_history_payloads(history)
        assert safe_decrypt(before["notiz-krise"]) == f"Vorher-{i}"
        assert safe_decrypt(after["notiz-krise"]) == f"Nachher-{i}"


# ---------------------------------------------------------------------------
# _reencrypt_events
# ---------------------------------------------------------------------------


def test_event_row_deleted_between_pk_collection_and_reread_skips_only_that_pk(
    facility, client_identified, doc_type_crisis, staff_user, monkeypatch
):
    """Pins x__reencrypt_events__mutmut_18 (``if event is None: continue`` -> ``break``).

    Analog zum EventHistory-Pin: DREI Events, die MITTLERE Zeile
    verschwindet waehrend die erste verarbeitet wird.
    """
    now = timezone.now()
    events = [
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=now - timedelta(seconds=i),
            data_json={"notiz-krise": f"Text-{i}"},
            created_by=staff_user,
        )
        for i in range(3)
    ]
    # ordering = ["-occurred_at"] => Iterationsreihenfolge events[0..2]
    to_delete = events[1].pk
    original = rotation_module._reencrypt_data_json
    state = {"fired": False}

    def delete_second_then_check(payload):
        if not state["fired"] and isinstance(payload, dict) and "notiz-krise" in payload:
            state["fired"] = True
            Event.objects.filter(pk=to_delete).delete()
        return original(payload)

    monkeypatch.setattr(rotation_module, "_reencrypt_data_json", delete_second_then_check)

    updated = rotation_module._reencrypt_events(dry_run=False)

    assert state["fired"]
    assert Event.objects.count() == 2
    assert updated == 2


def test_event_dry_run_counts_every_rotatable_row_not_just_the_first(
    facility, client_identified, doc_type_crisis, staff_user
):
    """Pins x__reencrypt_events__mutmut_28 (``updated += 1`` -> ``= 1``) und
    __mutmut_31 (``continue`` -> ``break`` im Dry-Run-Zweig)."""
    for i in range(2):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": f"Text-{i}"},
            created_by=staff_user,
        )

    updated = _reencrypt_events(dry_run=True)

    assert updated == 2


# ---------------------------------------------------------------------------
# _reencrypt_rectification_requests
# ---------------------------------------------------------------------------


def test_rectification_row_deleted_between_pk_collection_and_reread_skips_only_that_pk(
    facility, client_identified, staff_user, monkeypatch
):
    """Pins x__reencrypt_rectification_requests__mutmut_15 (``if rr is None: continue`` ->
    ``break``).

    DREI Antraege (nur ``reason`` verschluesselt, ``old_value``/``new_value``/
    ``review_note`` bleiben ``None``); die MITTLERE Zeile verschwindet beim
    Lesen der ersten Spalte der ersten Zeile.
    """
    requests = [
        RectificationRequest.objects.create(
            facility=facility,
            target_client=client_identified,
            field_path=f"notes-{i}",
            reason=encrypt_field(f"Grund-{i}"),
            requested_by=staff_user,
        )
        for i in range(3)
    ]
    now = timezone.now()
    for i, rr in enumerate(requests):
        RectificationRequest.objects.filter(pk=rr.pk).update(created_at=now - timedelta(seconds=i))
    # ordering = ["-created_at"] => Iterationsreihenfolge requests[0..2]
    to_delete = requests[1].pk
    original = rotation_module.is_encrypted_value
    state = {"fired": False}

    def delete_second_on_first_call(value):
        if not state["fired"]:
            state["fired"] = True
            RectificationRequest.objects.filter(pk=to_delete).delete()
        return original(value)

    monkeypatch.setattr(rotation_module, "is_encrypted_value", delete_second_on_first_call)

    updated = rotation_module._reencrypt_rectification_requests(dry_run=False)

    assert state["fired"]
    assert not RectificationRequest.objects.filter(pk=to_delete).exists()
    assert updated == 2


def test_rectification_dry_run_counts_every_rotatable_row_not_just_the_first(facility, client_identified, staff_user):
    """Pins x__reencrypt_rectification_requests__mutmut_37 (``updated += 1`` -> ``= 1``)
    und __mutmut_40 (``continue`` -> ``break`` im Dry-Run-Zweig)."""
    for i in range(2):
        RectificationRequest.objects.create(
            facility=facility,
            target_client=client_identified,
            field_path=f"notes-{i}",
            reason=encrypt_field(f"Grund-{i}"),
            requested_by=staff_user,
        )

    updated = _reencrypt_rectification_requests(dry_run=True)

    assert updated == 2


def test_rectification_real_rotation_counts_every_updated_row(facility, client_identified, staff_user):
    """Pins x__reencrypt_rectification_requests__mutmut_49/50/51 (Zaehler ``= 1``/``-= 1``/
    ``+= 2`` statt ``+= 1``)."""
    requests = [
        RectificationRequest.objects.create(
            facility=facility,
            target_client=client_identified,
            field_path=f"notes-{i}",
            reason=encrypt_field(f"Grund-{i}"),
            requested_by=staff_user,
        )
        for i in range(2)
    ]

    updated = _reencrypt_rectification_requests(dry_run=False)

    assert updated == 2
    for i, rr in enumerate(requests):
        rr.refresh_from_db()
        assert safe_decrypt(rr.reason) == f"Grund-{i}"


# ---------------------------------------------------------------------------
# _reencrypt_totp_devices
# ---------------------------------------------------------------------------


def test_totp_dry_run_skips_only_the_empty_key_device_not_the_rest(staff_user):
    """Pins x__reencrypt_totp_devices__mutmut_23 (``if not key: continue`` -> ``break``).

    Ein unbestaetigtes Geraet ohne Secret (``key=""``) VOR einem echten
    Geraet: der Original-Code ueberspringt nur das leere und zaehlt das
    echte (``1``); der Mutant bricht beim leeren Geraet komplett ab (``0``).
    """
    EncryptedTOTPDevice.objects.create(user=staff_user, name="leer", confirmed=False, key="")
    EncryptedTOTPDevice.objects.create(user=staff_user, name="echt", confirmed=True)

    updated, contended = _reencrypt_totp_devices(dry_run=True)

    assert updated == 1
    assert contended == []


def test_totp_dry_run_validates_decryptability_of_every_device_not_a_hardcoded_none(staff_user):
    """Pins x__reencrypt_totp_devices__mutmut_24 (``decrypt_totp_key(key)`` ->
    ``decrypt_totp_key(None)``).

    Ein Geraet mit einem unter einem FREMDEN Schluessel erzeugten Token: der
    Original-Code deckt das im Dry-Run laut auf (``EncryptionError``); der
    Mutant entschluesselt ``None`` (No-op ueber den fruehen Rueckgabezweig in
    ``decrypt_totp_key``) und laeuft still durch.
    """
    foreign_token = Fernet(Fernet.generate_key()).encrypt(b"0" * 40).decode("ascii")
    EncryptedTOTPDevice.objects.create(user=staff_user, name="fremd", confirmed=True, key=foreign_token)

    with pytest.raises(EncryptionError):
        _reencrypt_totp_devices(dry_run=True)


# ---------------------------------------------------------------------------
# _rotate_dynamic_json_model (ueber Client)
# ---------------------------------------------------------------------------


def test_client_row_deleted_between_pk_collection_and_reread_skips_only_that_pk(
    facility, doc_type_client_profile, staff_user, monkeypatch
):
    """Pins x__rotate_dynamic_json_model__mutmut_18 (``if obj is None: continue`` -> ``break``).

    DREI Clients (Reihenfolge ueber ``pseudonym`` A/B/C, da ``Client``
    alphabetisch ordnet); die MITTLERE Zeile (B) verschwindet waehrend die
    erste (A) verarbeitet wird.
    """
    clients = [
        Client.objects.create(
            facility=facility,
            pseudonym=f"{letter}-Pin098",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"diagnose": f"Diagnose-{letter}"},
        )
        for letter in ("A", "B", "C")
    ]
    # ordering = ["pseudonym"] => Iterationsreihenfolge A, B, C
    to_delete = clients[1].pk
    original = rotation_module._reencrypt_data_json
    state = {"fired": False}

    def delete_second_then_check(payload):
        if not state["fired"] and isinstance(payload, dict) and "diagnose" in payload:
            state["fired"] = True
            Client.objects.filter(pk=to_delete).delete()
        return original(payload)

    monkeypatch.setattr(rotation_module, "_reencrypt_data_json", delete_second_then_check)

    updated = rotation_module._rotate_dynamic_json_model(Client, dry_run=False)

    assert state["fired"]
    assert Client.objects.filter(pk__in=[c.pk for c in clients]).count() == 2
    assert updated == 2


def test_client_dry_run_counts_every_rotatable_row_not_just_the_first(facility, doc_type_client_profile, staff_user):
    """Pins x__rotate_dynamic_json_model__mutmut_30 (``updated += 1`` -> ``= 1``) und
    __mutmut_33 (``continue`` -> ``break`` im Dry-Run-Zweig)."""
    for i in range(2):
        Client.objects.create(
            facility=facility,
            pseudonym=f"DryRun098-{i}",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"diagnose": f"Diagnose-{i}"},
        )

    updated = _rotate_dynamic_json_model(Client, dry_run=True)

    assert updated == 2


def test_client_real_rotation_counts_every_updated_row(facility, doc_type_client_profile, staff_user):
    """Pins x__rotate_dynamic_json_model__mutmut_50/51 (Zaehler ``= 1``/``-= 1`` statt
    ``+= 1``)."""
    clients = [
        Client.objects.create(
            facility=facility,
            pseudonym=f"Real098-{i}",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"diagnose": f"Diagnose-{i}"},
        )
        for i in range(2)
    ]

    updated = _rotate_dynamic_json_model(Client, dry_run=False)

    assert updated == 2
    for i, client in enumerate(clients):
        client.refresh_from_db()
        assert safe_decrypt(raw_client_data_json(client)["diagnose"]) == f"Diagnose-{i}"
