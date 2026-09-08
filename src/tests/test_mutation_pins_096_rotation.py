"""Pin-Tests zum Meilenstein release-0.96 — Modul-Batch ``file_vault.rotation``.

Quelle: ``src/core/services/file_vault/rotation.py`` (Business-Logik des
``reencrypt_fields``-Command, Refs #1835 / #1362 / #2131 / #2177). Vorgabe ist
die Prio-1-Survivor-Triage des Mutations-Deltas v0.96.0 (17 Ueberlebende).

Die Ueberlebenden verteilen sich auf vier Zusicherungen, die der Bestand
(``src/tests/test_reencrypt_fields.py``, ``src/tests/test_totp_secret_at_rest.py``)
zwar formuliert, aber nicht wertgenau pinnt:

* **``--dry-run`` zaehlt und schreibt nichts.** ``TestRotateAll::
  test_dry_run_writes_nothing`` legt genau EINE ``Client``-Zeile an; die sechs
  uebrigen Pfade (Event, Ereignis-Historie, Anhang, TOTP-Geraet, Fall,
  Berichtigungsantrag) bekommen ihr ``dry_run``-Argument daher nie geprueft.
  ``None`` an dieser Stelle ist falsy — die Vorab-Pruefung wuerde real
  schreiben. Gepinnt je Pfad ueber die BYTE-Identitaet des Rohwerts in der DB
  (Fernet ist nicht deterministisch: jede Neuverschluesselung desselben
  Klartexts erzeugt ein anderes Chiffrat, ein Schreiben ist also sichtbar).

* **Die Kontentions-Summe ist additiv.** ``contended`` ist laut Docstring die
  Gesamtzahl ueber alle sieben Pfade und im Command das einzige Kriterium fuer
  "Alt-Schluessel noch nicht entfernen".

* **Compare-and-Swap ist an GENAU EINE Zeile gebunden.** ``filter(pk=pk, …)``
  ist die Zeilenbindung; ohne sie trifft das ``UPDATE`` jede Zeile mit
  passenden Erwartungswerten — der Lauf laeuft per Definition im
  RLS-Bypass-Kontext, also einrichtungsuebergreifend.

* **Ein nicht rotierbarer Wert wird UEBERSPRUNGEN, nicht zum Abbruch.** Alle
  ``continue`` der Rotationsschleifen sind gegen ``break`` mutierbar; ein
  ``break`` schneidet den Lauf still ab, ohne Kontentionszaehler und mit
  korrekt aussehendem Erfolgstext. Weil alle drei betroffenen Modelle
  ``ordering = ["-created_at"]`` bzw. ``["-changed_at"]`` tragen, wird die
  Reihenfolge hier explizit gestellt (der Stopper steht VORNE), statt sich auf
  die Heap-Reihenfolge zu verlassen.

Konventionen wie ``test_mutation_pins_094_deletion.py``: exakte Vergleiche
(``==``, nie Teilstring — mutmut erzeugt ``"XX…XX"``-Varianten), echte
Model-Objekte statt Fakes, Fixtures aus ``src/tests/conftest.py``, Roh-Helfer
aus ``src/tests/test_reencrypt_fields.py``. Jeder Test nennt seine Mutanten in
der Docstring samt dem Wert, der beim Mutanten abweicht.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from core.models import (
    Case,
    DocumentType,
    EncryptedTOTPDevice,
    Event,
    EventHistory,
    RectificationRequest,
)
from core.models.attachment import Attachment
from core.services.file_vault import encrypt_field, generate_key, safe_decrypt
from core.services.file_vault.rotation import (
    _locked_compare_and_swap,
    _reencrypt_data_json,
    _rewrap_one_totp_device,
    rotate_all,
)
from tests.test_reencrypt_fields import (
    raw_case_data_json,
    raw_event_data_json,
    raw_history_payloads,
)

#: Ein Marker-Dict, dessen Chiffrat unter KEINEM konfigurierten Schluessel
#: aufgeht — ``safe_decrypt`` faellt darauf auf ``"[verschlüsselt]"`` zurueck.
#: Identisches Muster wie ``TestRotateAll::test_unrotatable_values_are_skipped_without_writes``.
UNDECRYPTABLE = {"__encrypted__": True, "value": "corrupt"}


def raw_totp_key(device_id) -> str:
    """``otp_totp_totpdevice.key`` roh aus der DB (umgeht die Proxy-Entschluesselung)."""
    with connection.cursor() as cur:
        cur.execute("SELECT key FROM otp_totp_totpdevice WHERE id = %s", [device_id])
        return cur.fetchone()[0]


def _make_event(facility, doc_type, staff_user, *, data_json):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json=data_json,
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestDryRunWritesNothingPerPath:
    """Je Rotationspfad: ``rotate_all(dry_run=True)`` laesst den Rohwert byte-identisch.

    Die Mutanten ersetzen das durchgereichte ``dry_run`` durch ``None``
    (falsy) — der jeweilige Pfad schreibt dann bei einer Vorab-Pruefung real.
    Der Zaehler bleibt dabei unauffaellig, weil auch der Schreibzweig
    ``updated += 1`` zaehlt; nur der Rohwert verraet den Mutanten.
    """

    def test_dry_run_leaves_event_data_json_byte_identical(self, facility, doc_type_crisis, staff_user):
        """Mutant ``x_rotate_all__mutmut_7`` (``_reencrypt_events(None, …)``):
        der Ereignispfad wickelt ``data_json`` bei ``--dry-run`` real neu ein."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            event = _make_event(facility, doc_type_crisis, staff_user, data_json={"notiz-krise": "Dry-Run-Ereignis"})
            token_before = raw_event_data_json(event)["notiz-krise"]["value"]

            counts = rotate_all(dry_run=True)

            assert counts["events"] == 1
            assert raw_event_data_json(event)["notiz-krise"]["value"] == token_before

    def test_dry_run_leaves_event_history_byte_identical(self, facility, doc_type_crisis, staff_user):
        """Mutant ``x_rotate_all__mutmut_12`` (``_reencrypt_event_history(None, …)``):
        der Historienpfad schreibt bei ``--dry-run`` real (per Trigger-Bypass!)."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            event = _make_event(facility, doc_type_crisis, staff_user, data_json={})
            history = EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"notiz-krise": encrypt_field("Vorher-Wortlaut")},
                data_after={"notiz-krise": encrypt_field("Nachher-Wortlaut")},
            )
            before_token, after_token = raw_history_payloads(history)

            counts = rotate_all(dry_run=True)

            assert counts["event_history"] == 1
            assert raw_history_payloads(history) == (before_token, after_token)

    def test_dry_run_leaves_attachment_filename_byte_identical(self, facility, doc_type_crisis, staff_user):
        """Mutant ``x_rotate_all__mutmut_17`` (``_reencrypt_attachments(None, …)``):
        der Anhangspfad wickelt ``original_filename_encrypted`` bei ``--dry-run`` real neu ein."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            event = _make_event(facility, doc_type_crisis, staff_user, data_json={})
            attachment = Attachment.objects.create(
                facility=facility,
                event=event,
                field_template=doc_type_crisis.fields.first().field_template,
                storage_filename="dry-run.enc",
                original_filename_encrypted=encrypt_field("dry-run.pdf"),
                file_size=17,
                mime_type="application/pdf",
                created_by=staff_user,
            )
            token_before = attachment.original_filename_encrypted["value"]

            counts = rotate_all(dry_run=True)

            attachment.refresh_from_db()
            assert counts["attachments"] == 1
            assert attachment.original_filename_encrypted["value"] == token_before

    def test_dry_run_leaves_totp_key_byte_identical(self, facility, staff_user):
        """Mutant ``x_rotate_all__mutmut_22`` (``_reencrypt_totp_devices(None)``):
        das TOTP-Secret wird bei ``--dry-run`` real umgewickelt statt nur validiert."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            device = EncryptedTOTPDevice.objects.create(user=staff_user, name="dry-run", confirmed=True)
            token_before = raw_totp_key(device.id)

            counts = rotate_all(dry_run=True)

            assert counts["totp_devices"] == 1
            assert raw_totp_key(device.id) == token_before

    def test_dry_run_leaves_case_data_json_byte_identical(
        self, facility, client_identified, doc_type_case_form, staff_user
    ):
        """Mutant ``x_rotate_all__mutmut_37`` (``_rotate_dynamic_json_model(Case, dry_run=None, …)``):
        der Fallpfad wickelt ``data_json`` bei ``--dry-run`` real neu ein."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            case = Case.objects.create(
                facility=facility,
                client=client_identified,
                title="Dry-Run-Fall",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"verlauf": "Dry-Run-Verlauf"},
            )
            token_before = raw_case_data_json(case)["verlauf"]["value"]

            counts = rotate_all(dry_run=True)

            assert counts["cases"] == 1
            assert raw_case_data_json(case)["verlauf"]["value"] == token_before

    def test_dry_run_leaves_rectification_columns_byte_identical(self, facility, client_identified, staff_user):
        """Mutant ``x_rotate_all__mutmut_43`` (``_reencrypt_rectification_requests(None, …)``):
        die vier Belegspalten des Art.-16-Antrags werden bei ``--dry-run`` real neu eingewickelt."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            request = RectificationRequest.objects.create(
                facility=facility,
                target_client=client_identified,
                field_path="notes",
                old_value=encrypt_field('"Alt"'),
                new_value=encrypt_field('"Neu"'),
                reason=encrypt_field("Dry-Run-Grund"),
                review_note=encrypt_field("Dry-Run-Prüfvermerk"),
                requested_by=staff_user,
            )
            tokens_before = (
                request.old_value["value"],
                request.new_value["value"],
                request.reason["value"],
                request.review_note["value"],
            )

            counts = rotate_all(dry_run=True)

            request.refresh_from_db()
            assert counts["rectification_requests"] == 1
            assert (
                request.old_value["value"],
                request.new_value["value"],
                request.reason["value"],
                request.review_note["value"],
            ) == tokens_before


@pytest.mark.django_db
class TestContendedIsAdditive:
    """``contended`` summiert die TOTP-Kontention auf, statt sie abzuziehen."""

    def test_totp_contended_pks_are_added_to_the_total(self, facility, monkeypatch):
        """Mutant ``x_rotate_all__mutmut_27`` (``contended -= len(totp_contended)``):
        liefert ``-2`` statt ``2`` — ein Aufrufer ohne ``on_totp_contended``-Callback
        bekaeme damit gar kein Kontentionssignal mehr."""
        from core.services.file_vault import rotation as rotation_module

        monkeypatch.setattr(rotation_module, "_reencrypt_totp_devices", lambda dry_run: (0, [11, 22]))

        counts = rotate_all(dry_run=False)

        assert counts["contended"] == 2
        assert counts["totp_devices"] == 0

    def test_totp_contended_pks_reach_the_callback_and_the_total(self, facility, monkeypatch):
        """Gegenprobe zu ``x_rotate_all__mutmut_27``: der Callback sieht dieselben
        pks, die die Summe erhoehen (Callback ist optional, die Summe nicht)."""
        from core.services.file_vault import rotation as rotation_module

        monkeypatch.setattr(rotation_module, "_reencrypt_totp_devices", lambda dry_run: (0, [11, 22]))
        seen: list[list[int]] = []

        counts = rotate_all(dry_run=False, on_totp_contended=seen.append)

        assert seen == [[11, 22]]
        assert counts["contended"] == 2


@pytest.mark.django_db
class TestCompareAndSwapIsBoundToOneRow:
    """Beide CAS-Pfade schreiben ausschliesslich die adressierte Zeile."""

    def test_locked_cas_updates_only_the_addressed_row(self, facility, second_facility, doc_type_crisis, staff_user):
        """Mutant ``x__locked_compare_and_swap__mutmut_10``
        (``filter(pk=pk, **expected)`` → ``filter(**expected)``): das ``UPDATE``
        verliert seine Zeilenbindung und traegt den Payload der EINEN Zeile in
        JEDE Zeile mit gleichen Erwartungswerten ein — hier in die Zeile einer
        FREMDEN Einrichtung (der Lauf laeuft im RLS-Bypass-Kontext)."""
        doc_type_second = DocumentType.objects.create(
            facility=second_facility,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
            name="Krisengespräch (zweite Stelle)",
        )
        event_own = _make_event(facility, doc_type_crisis, staff_user, data_json={})
        event_foreign = _make_event(second_facility, doc_type_second, staff_user, data_json={})

        swapped = _locked_compare_and_swap(
            Event,
            event_own.pk,
            expected={"data_json": {}},
            updates={"data_json": {"gepinnt": True}},
        )

        assert swapped is True
        assert raw_event_data_json(event_own) == {"gepinnt": True}
        assert raw_event_data_json(event_foreign) == {}

    def test_totp_rewrap_updates_only_the_addressed_device(self, facility, staff_user, lead_user):
        """Mutant ``x__rewrap_one_totp_device__mutmut_22``
        (``filter(pk=pk, key=current)`` → ``filter(key=current)``): zwei Geraete
        mit demselben ``key``-Wert (unmigrierter Klartext-Key bzw. kopiertes
        Token) werden beide beschrieben — nutzer- und einrichtungsuebergreifend."""
        from core.services.security.totp import decrypt_totp_key, encrypt_totp_key

        old_key = generate_key()
        new_key = generate_key()
        with override_settings(ENCRYPTION_KEYS=old_key, ENCRYPTION_KEY=""):
            target = EncryptedTOTPDevice.objects.create(user=staff_user, name="ziel", confirmed=True)
            bystander = EncryptedTOTPDevice.objects.create(user=lead_user, name="fremd", confirmed=True)
            shared = raw_totp_key(target.id)
            # Gleicher ``key``-Wert in beiden Zeilen — genau der Zustand, den
            # ein doppelt eingespieltes Secret bzw. ein unmigrierter
            # Klartext-Key erzeugt.
            EncryptedTOTPDevice.objects.filter(pk=bystander.pk).update(key=shared)
            assert raw_totp_key(bystander.id) == shared

        with override_settings(ENCRYPTION_KEYS=f"{new_key},{old_key}", ENCRYPTION_KEY=""):
            outcome = _rewrap_one_totp_device(target.pk, decrypt_totp_key, encrypt_totp_key)

        assert outcome == "rewrapped"
        assert raw_totp_key(target.id) != shared
        assert raw_totp_key(bystander.id) == shared


@pytest.mark.django_db
class TestUndecryptableValuesAreNeverOverwritten:
    """Der Platzhalter-Vergleich schuetzt das Chiffrat vor sich selbst."""

    def test_undecryptable_value_is_never_re_encrypted(self):
        """Mutanten ``x__reencrypt_data_json__mutmut_13`` (``"XX[verschlüsselt]XX"``)
        und ``…__mutmut_14`` (``"[VERSCHLÜSSELT]"``): die Bedingung wird immer wahr,
        der Platzhalter selbst wird verschluesselt und ueberschreibt das
        Original-Chiffrat — irreversibler Datenverlust genau in dem Szenario,
        fuer das die Rotation existiert."""
        key = generate_key()
        payload = {"notiz-krise": UNDECRYPTABLE}

        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            assert safe_decrypt(UNDECRYPTABLE) == "[verschlüsselt]"
            new_payload, changed = _reencrypt_data_json(payload)

        assert changed is False
        assert new_payload == {"notiz-krise": UNDECRYPTABLE}

    def test_rotation_leaves_undecryptable_event_ciphertext_byte_identical(self, facility, doc_type_crisis, staff_user):
        """Gegenprobe zu ``x__reencrypt_data_json__mutmut_13``/``…_14`` am
        echten Rotationslauf: ein Ereignis mit nicht mehr entschluesselbarem
        ``data_json``-Wert bleibt unangetastet (der Mutant schreibt
        ``encrypt_field("[verschlüsselt]")`` darueber)."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            event = _make_event(facility, doc_type_crisis, staff_user, data_json={})
            Event.objects.filter(pk=event.pk).update(data_json={"notiz-krise": UNDECRYPTABLE})

            counts = rotate_all(dry_run=False)

            assert counts["events"] == 0
            assert counts["contended"] == 0
            assert raw_event_data_json(event) == {"notiz-krise": UNDECRYPTABLE}


@pytest.mark.django_db
class TestUnrotatableRowsAreSkippedNotFatal:
    """Ein nicht rotierbarer Wert ueberspringt seine Zeile/Spalte — er beendet den Lauf nicht.

    Die Reihenfolge wird bewusst gestellt: alle drei Modelle ordnen absteigend
    nach Zeitstempel (``ordering = ["-created_at"]`` bzw. ``["-changed_at"]``),
    der Stopper bekommt also den JUENGSTEN Zeitstempel und steht damit VORNE.
    """

    def test_attachment_without_marker_does_not_stop_the_run(self, facility, doc_type_crisis, staff_user):
        """Mutant ``x__reencrypt_attachments__mutmut_19`` (``continue`` → ``break``):
        eine einzige Legacy-Zeile ohne ``__encrypted__``-Marker beendet die
        gesamte Anhangs-Rotation — ``attachments`` faellt von 1 auf 0."""
        key = generate_key()
        field_template = doc_type_crisis.fields.first().field_template
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            event = _make_event(facility, doc_type_crisis, staff_user, data_json={})
            rotatable = Attachment.objects.create(
                facility=facility,
                event=event,
                field_template=field_template,
                storage_filename="rotierbar.enc",
                original_filename_encrypted=encrypt_field("rotierbar.pdf"),
                file_size=1,
                mime_type="application/pdf",
                created_by=staff_user,
            )
            stopper = Attachment.objects.create(
                facility=facility,
                event=event,
                field_template=field_template,
                storage_filename="legacy.enc",
                original_filename_encrypted={"legacy": "plain"},
                file_size=1,
                mime_type="application/pdf",
                created_by=staff_user,
            )
            self._order_attachments(stopper, rotatable)
            token_before = rotatable.original_filename_encrypted["value"]

            counts = rotate_all(dry_run=False)

            rotatable.refresh_from_db()
            assert counts["attachments"] == 1
            assert counts["contended"] == 0
            assert rotatable.original_filename_encrypted["value"] != token_before
            assert safe_decrypt(rotatable.original_filename_encrypted) == "rotierbar.pdf"

    def test_undecryptable_attachment_does_not_stop_the_run(self, facility, doc_type_crisis, staff_user):
        """Mutant ``x__reencrypt_attachments__mutmut_25`` (``continue`` → ``break``):
        ein Anhang, dessen Token unter keinem konfigurierten Schluessel mehr
        aufgeht — der Anlass jeder Rotation —, beendet den Lauf statt
        uebersprungen zu werden."""
        key = generate_key()
        field_template = doc_type_crisis.fields.first().field_template
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            event = _make_event(facility, doc_type_crisis, staff_user, data_json={})
            rotatable = Attachment.objects.create(
                facility=facility,
                event=event,
                field_template=field_template,
                storage_filename="rotierbar.enc",
                original_filename_encrypted=encrypt_field("rotierbar.pdf"),
                file_size=1,
                mime_type="application/pdf",
                created_by=staff_user,
            )
            stopper = Attachment.objects.create(
                facility=facility,
                event=event,
                field_template=field_template,
                storage_filename="fremdschluessel.enc",
                original_filename_encrypted=UNDECRYPTABLE,
                file_size=1,
                mime_type="application/pdf",
                created_by=staff_user,
            )
            self._order_attachments(stopper, rotatable)
            token_before = rotatable.original_filename_encrypted["value"]

            counts = rotate_all(dry_run=False)

            rotatable.refresh_from_db()
            stopper.refresh_from_db()
            assert counts["attachments"] == 1
            assert rotatable.original_filename_encrypted["value"] != token_before
            assert stopper.original_filename_encrypted == UNDECRYPTABLE

    def test_history_row_without_ciphertext_does_not_stop_the_run(self, facility, doc_type_crisis, staff_user):
        """Mutant ``x__reencrypt_event_history__mutmut_22`` (``continue`` → ``break``):
        eine redigierte Historienzeile (ADR-050-Sweep / Art.-17-Kaskade, Marker
        ohne Chiffrate) schneidet die Historien-Rotation ab — ``event_history``
        faellt von 1 auf 0, alles Nachfolgende bleibt unter dem Alt-Schluessel."""
        from core.services.system import bypass_replication_triggers

        old_key = generate_key()
        new_key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
            event = _make_event(facility, doc_type_crisis, staff_user, data_json={})
            rotatable = EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"notiz-krise": encrypt_field("Vorher-Wortlaut")},
                data_after=None,
            )
            stopper = EventHistory.objects.create(
                event=event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
                data_before={"_redacted": True, "_category": "pin-096"},
                data_after={"_redacted": True, "_category": "pin-096"},
            )
        now = timezone.now()
        with bypass_replication_triggers(tables=("core_eventhistory",)):
            EventHistory.objects.filter(pk=stopper.pk).update(changed_at=now)
            EventHistory.objects.filter(pk=rotatable.pk).update(changed_at=now - timedelta(minutes=5))
        before_token, _ = raw_history_payloads(rotatable)

        with override_settings(ENCRYPTION_KEYS=f"{new_key},{old_key}", ENCRYPTION_KEY=""):
            counts = rotate_all(dry_run=False)
            rotated_before, _ = raw_history_payloads(rotatable)
            assert safe_decrypt(rotated_before["notiz-krise"]) == "Vorher-Wortlaut"

        assert counts["event_history"] == 1
        assert counts["contended"] == 0
        assert rotated_before["notiz-krise"]["value"] != before_token["notiz-krise"]["value"]
        assert raw_history_payloads(stopper) == (
            {"_redacted": True, "_category": "pin-096"},
            {"_redacted": True, "_category": "pin-096"},
        )

    def test_empty_first_column_does_not_stop_the_column_loop(self, facility, client_identified, staff_user):
        """Mutant ``x__reencrypt_rectification_requests__mutmut_25`` (``continue`` → ``break``):
        ``_RECTIFICATION_COLUMNS`` beginnt mit ``old_value`` — ist die Spalte leer
        (Regelfall bei Pseudonym-Berichtigungen), bricht die Spaltenschleife ab
        und ``reason``/``review_note`` rotieren NIE mit."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            request = RectificationRequest.objects.create(
                facility=facility,
                target_client=client_identified,
                field_path="notes",
                old_value=None,
                new_value=None,
                reason=encrypt_field("Spalten-Grund"),
                review_note=encrypt_field("Spalten-Prüfvermerk"),
                requested_by=staff_user,
            )
            reason_before = request.reason["value"]
            note_before = request.review_note["value"]

            counts = rotate_all(dry_run=False)

            request.refresh_from_db()
            assert counts["rectification_requests"] == 1
            assert counts["contended"] == 0
            assert request.reason["value"] != reason_before
            assert request.review_note["value"] != note_before
            assert safe_decrypt(request.reason) == "Spalten-Grund"
            assert safe_decrypt(request.review_note) == "Spalten-Prüfvermerk"

    def test_undecryptable_first_column_does_not_stop_the_column_loop(self, facility, client_identified, staff_user):
        """Mutant ``x__reencrypt_rectification_requests__mutmut_31`` (``continue`` → ``break``):
        eine einzige nicht mehr entschluesselbare Spalte laesst alle
        nachfolgenden Spalten desselben Antrags still unter dem ausrangierten
        Schluessel liegen."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            request = RectificationRequest.objects.create(
                facility=facility,
                target_client=client_identified,
                field_path="notes",
                old_value=UNDECRYPTABLE,
                new_value=encrypt_field('"Neu"'),
                reason=encrypt_field("Fremdschlüssel-Grund"),
                review_note=encrypt_field("Fremdschlüssel-Prüfvermerk"),
                requested_by=staff_user,
            )
            new_before = request.new_value["value"]
            reason_before = request.reason["value"]
            note_before = request.review_note["value"]

            counts = rotate_all(dry_run=False)

            request.refresh_from_db()
            assert counts["rectification_requests"] == 1
            assert counts["contended"] == 0
            assert request.old_value == UNDECRYPTABLE
            assert request.new_value["value"] != new_before
            assert request.reason["value"] != reason_before
            assert request.review_note["value"] != note_before

    def test_request_without_rotatable_column_does_not_stop_the_run(self, facility, client_identified, staff_user):
        """Mutant ``x__reencrypt_rectification_requests__mutmut_36`` (``continue`` → ``break``):
        ein einziger Antrag ohne rotierbare Spalte (abgelehnt, ohne Pruefvermerk)
        beendet die Rotation ALLER weiteren Antraege."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            rotatable = RectificationRequest.objects.create(
                facility=facility,
                target_client=client_identified,
                field_path="notes",
                old_value=encrypt_field('"Alt"'),
                new_value=encrypt_field('"Neu"'),
                reason=encrypt_field("Reihenfolge-Grund"),
                review_note=encrypt_field("Reihenfolge-Prüfvermerk"),
                requested_by=staff_user,
            )
            stopper = RectificationRequest.objects.create(
                facility=facility,
                target_client=client_identified,
                field_path="pseudonym",
                requested_by=staff_user,
            )
            now = timezone.now()
            RectificationRequest.objects.filter(pk=stopper.pk).update(created_at=now)
            RectificationRequest.objects.filter(pk=rotatable.pk).update(created_at=now - timedelta(minutes=5))
            reason_before = rotatable.reason["value"]

            counts = rotate_all(dry_run=False)

            rotatable.refresh_from_db()
            assert counts["rectification_requests"] == 1
            assert counts["contended"] == 0
            assert rotatable.reason["value"] != reason_before
            assert safe_decrypt(rotatable.reason) == "Reihenfolge-Grund"

    @staticmethod
    def _order_attachments(first, second):
        """Stellt die Iterationsreihenfolge (``ordering = ["-created_at"]``) explizit."""
        now = timezone.now()
        Attachment.objects.filter(pk=first.pk).update(created_at=now)
        Attachment.objects.filter(pk=second.pk).update(created_at=now - timedelta(minutes=5))
