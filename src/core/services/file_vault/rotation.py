"""Feldverschluesselungs-Rotation — Business-Logik des ``reencrypt_fields``-Command.

Verschoben aus ``core/management/commands/reencrypt_fields.py`` (Refs #1835):
das Thin-Wrapper-Muster gilt fuer Commands repoweit ("all business logic
lives in the service module", siehe ``enforce_retention.py``) — vorher lagen
sieben Rotationspfade samt RLS-Guard-Kontext im Command selbst. Der Command
behaelt Argument-Parsing, den RLS-Bypass-Guard und die Ausgabe; hier lebt nur
noch, WAS rotiert wird.

M4 Strang C (Refs #1060): ``RectificationRequest`` ist der siebte Rotationspfad —
seine vier verschluesselten Spalten (Wertpaar, Begruendung, Pruefvermerk) sind
nach ADR-041 dauerhafte Rechenschaftsbelege und muessen daher mitrotieren, sonst
liesse sich ein ausrangierter Schluessel nie entfernen.

M24 (Refs #1738): ``Attachment`` ist seit ADR-039 owner-generisch (Ereignis,
Person, Fall) — die Rotation erfasst weiterhin ALLE Zeilen ohne Owner-Filter
(kein Verhaltensunterschied, aber jetzt ausdruecklich dokumentiert).

A4.4 (Refs #1024): Für ``Event.data_json`` werden zusätzlich Plaintext-PII-Felder
(laut document_type ``is_encrypted=True``) nachverschlüsselt — Heal von Altbestand
vor Einführung der Feldverschlüsselung. M2/P6 (Refs #1058): dasselbe Heal-Muster
gilt fuer ``Client.data_json``/``Case.data_json`` (``DynamicDataEncryptionMixin``,
P4a/P4b/P5) — sonst entstuende eine Rotationsluecke fuer die neuen Modellgruppen.

E17-Nachzug (Refs #1724): Event/Client/Case rotieren ALLE Rows inkl.
soft-deleted (kein ``is_deleted``-Filter) — der fruehere ``is_deleted=False``-
Filter bei Events liess soft-deleted Events mit noch vorhandenem ``data_json``
dauerhaft unter einem ausrangierten Schluessel liegen.
"""

from __future__ import annotations

from collections.abc import Callable

from django.db import transaction
from django.utils import timezone

from core.models import Case, Client, Event, EventHistory, RectificationRequest
from core.models.attachment import Attachment
from core.services.file_vault.encryption import encrypt_dynamic_data, encrypt_field, is_encrypted_value, safe_decrypt


def _reencrypt_data_json(payload):
    """Re-encrypt every encrypted value in a ``data_json``-style dict.

    Returns ``(new_payload_or_None_if_unchanged, changed_flag)``. Operates on
    a copy, so the caller decides whether to persist.
    """
    if not payload or not isinstance(payload, dict):
        return payload, False
    changed = False
    new_payload = dict(payload)
    for key, value in payload.items():
        if is_encrypted_value(value):
            decrypted = safe_decrypt(value)
            if decrypted != "[verschlüsselt]":
                new_payload[key] = encrypt_field(decrypted)
                changed = True
    return new_payload, changed


def _locked_compare_and_swap(model, pk, *, expected: dict, updates: dict) -> bool:
    """Lock one row and apply *updates* only while *expected* still matches.

    The candidate values are deliberately read and transformed before this
    short transaction. A writer that commits in that window therefore wins:
    the lock waits for it, the compare-and-swap sees the changed version/value,
    and the rotation skips its now-stale payload instead of overwriting it.
    There is deliberately no retry with the stale candidate: a zero-row update
    is counted as contention and the operator repeats the whole rotation run.
    The TOTP path remains separate because it re-reads its candidate under the
    row lock before each bounded attempt.
    """
    with transaction.atomic():
        locked = model.objects.select_for_update().filter(pk=pk).only("pk").first()
        if locked is None:
            return False
        return bool(model.objects.filter(pk=pk, **expected).update(**updates))


def _reencrypt_events(dry_run: bool, *, on_contended: Callable[[], None] | None = None) -> int:
    """M2/P6-Nachzug (Refs #1724, Entscheid E17): rotiert ALLE ``Event``-Rows
    inkl. soft-deleted.

    Der bisherige ``is_deleted=False``-Filter war eine bewusst offen gelassene
    Rotations-Luecke (dokumentiert als "bekannte Luecke" in
    ``docs/admin-guide.md``): ein soft-deleted Event mit noch vorhandenem
    ``data_json`` (z.B. Altbestand von vor Einfuehrung der ``data_json``-
    Leerung in ``soft_delete_event``, oder Restore-Faelle) blieb dauerhaft
    unter einem ausrangierten Schluessel liegen. ``EventManager``/
    ``FacilityScopedManager`` filtert ``is_deleted`` nicht, ``Event.objects``
    deckt damit ohne Extra-Filter bereits den vollen Bestand ab — analog
    :func:`_rotate_dynamic_json_model`.

    Bleibt bewusst eigenstaendig statt in :func:`_rotate_dynamic_json_model`
    aufzugehen (Refs #1835): ``Event.document_type`` ist — anders als bei
    ``Client``/``Case`` — ein Pflichtfeld (kein ``null=True``), das nullable-
    Narrowing aus Refs #1730 (``document_type = obj.document_type if
    obj.document_type_id else None``) gilt hier also nicht.

    Refs #2131: Das fruehere ``save(update_fields=["data_json",
    "updated_at"])`` rief zwar das einzige Event-``pre_save``-Signal auf, nahm
    dessen ``search_text`` aber nicht in ``update_fields`` auf. Der Signalwert
    wurde daher schon vorher nie persistiert; das CAS-``update()`` verliert
    keine wirksame Nebenwirkung.
    """
    updated = 0
    pks = list(Event.objects.values_list("pk", flat=True))
    for pk in pks:
        event = Event.objects.select_related("document_type").filter(pk=pk).first()
        if event is None:
            continue
        new_data, changed = _reencrypt_data_json(event.data_json)
        # A4.4 (Refs #1024): zusätzlich Plaintext-PII-Felder (laut
        # document_type ``is_encrypted=True``) nachverschlüsseln — heilt
        # Altbestand, der vor Einführung der Feldverschlüsselung als Klartext
        # gespeichert wurde. ``encrypt_dynamic_data`` lässt bereits verschlüsselte
        # Werte unangetastet.
        healed = encrypt_dynamic_data(event.document_type, new_data) if event.document_type_id else new_data
        if changed or healed != event.data_json:
            if dry_run:
                updated += 1
                continue
            if _locked_compare_and_swap(
                Event,
                pk,
                expected={"updated_at": event.updated_at, "data_json": event.data_json},
                updates={"data_json": healed, "updated_at": timezone.now()},
            ):
                updated += 1
            elif on_contended is not None:
                on_contended()
    return updated


def _rotate_dynamic_json_model(model, *, dry_run: bool, on_contended: Callable[[], None] | None = None) -> int:
    """Gemeinsamer Rotationspfad fuer ``Client``/``Case`` (Refs #1835).

    ``_reencrypt_clients``/``_reencrypt_cases`` waren bis aufs Modell
    identisch (M2/P6, Refs #1058, Entscheid E17: rotiert ALLE Rows inkl.
    soft-deleted — ``FacilityScopedManager`` filtert ``is_deleted`` nicht,
    ``<Model>.objects`` deckt damit ohne Extra-Filter bereits den vollen
    Bestand ab). Beide Modelle bieten dieselbe Form (``data_json``,
    nullable ``document_type``/``document_type_id``,
    ``DynamicDataEncryptionMixin``) — die Rotation ist deshalb
    modellunabhaengig ausdrueckbar.

    Refs #1730: ``document_type`` ist bei beiden Modellen nullable — ueber
    die lokale Variable auf ``is not None`` narrowen (statt auf
    ``document_type_id`` zu prüfen), damit mypy den ``DocumentType | None``-
    Typ real ausschliesst. Verhaltensgleich: der FK-Dereferenz passiert
    weiterhin nur, wenn eine Zuordnung existiert (Kurzschluss dank
    ``if``-Ausdruck).
    """
    updated = 0
    pks = list(model.objects.values_list("pk", flat=True))
    for pk in pks:
        obj = model.objects.select_related("document_type").filter(pk=pk).first()
        if obj is None:
            continue
        new_data, changed = _reencrypt_data_json(obj.data_json)
        document_type = obj.document_type if obj.document_type_id else None
        healed = encrypt_dynamic_data(document_type, new_data) if document_type is not None else new_data
        if changed or healed != obj.data_json:
            if dry_run:
                updated += 1
                continue
            if _locked_compare_and_swap(
                model,
                pk,
                expected={"updated_at": obj.updated_at, "data_json": obj.data_json},
                updates={"data_json": healed, "updated_at": timezone.now()},
            ):
                updated += 1
            elif on_contended is not None:
                on_contended()
    return updated


#: Die vier feldverschluesselten Spalten von ``RectificationRequest`` (M4,
#: Refs #1060). Als Konstante, damit ein spaeter ergaenztes Feld hier
#: sichtbar fehlt statt still aus der Rotation zu fallen.
_RECTIFICATION_COLUMNS = ("old_value", "new_value", "reason", "review_note")


def _reencrypt_rectification_requests(dry_run: bool, *, on_contended: Callable[[], None] | None = None) -> int:
    """Siebter Rotationspfad: die Wertpaare der Art.-16-Antraege (Refs #1060).

    ``old_value``/``new_value``/``reason``/``review_note`` sind
    ``encrypt_field``-Marker-Dicts (ADR-041) — mechanisch derselbe Fall wie
    ``Attachment.original_filename_encrypted``, nur vier Spalten pro Zeile.
    Die JSON-Stufe **innerhalb** des Klartexts bleibt dabei unangetastet:
    entschluesselt wird zum JSON-String, der unveraendert wieder eingewickelt
    wird — hier wird rotiert, nicht interpretiert.

    Es wird ueber ALLE Zeilen iteriert, auch ueber abgeschlossene Antraege:
    genehmigte/abgelehnte Antraege sind der Rechenschaftsnachweis und
    duerfen nicht unter einem ausrangierten Schluessel liegenbleiben (Muster
    :func:`_rotate_dynamic_json_model`, Refs #1724/E17).
    """
    updated = 0
    pks = list(RectificationRequest.objects.values_list("pk", flat=True))
    for pk in pks:
        rr = RectificationRequest.objects.filter(pk=pk).first()
        if rr is None:
            continue
        expected = {}
        updates = {}
        for column in _RECTIFICATION_COLUMNS:
            value = getattr(rr, column)
            if not is_encrypted_value(value):
                continue
            decrypted = safe_decrypt(value)
            if decrypted == "[verschlüsselt]":
                continue
            expected[column] = value
            updates[column] = encrypt_field(decrypted)
        if not updates:
            continue
        if dry_run:
            updated += 1
            continue
        if _locked_compare_and_swap(RectificationRequest, pk, expected=expected, updates=updates):
            updated += 1
        elif on_contended is not None:
            on_contended()
    return updated


def _reencrypt_event_history(dry_run: bool) -> int:
    """Refs #783 — EventHistory ist append-only (Trigger ``prevent_eventhistory_update``).

    UPDATE wird per ``bypass_replication_triggers`` umgangen — derselbe
    Pfad, den ``services/clients.anonymize_client`` fuer redaktierte
    History-Eintraege nutzt.

    Lazy-Import (Refs #1835, wie beim TOTP-Rewrap unten): ``core.services.system``
    importiert transitiv wieder ``core.services.file_vault`` (ueber
    ``compliance`` → ``clamav`` → ``file_vault.virus_scan``) — ein Modul-Import
    an dieser Stelle liefe beim Package-Import von ``file_vault`` selbst in
    einen echten Zirkel (``file_vault`` re-exportiert ``rotate_all``).
    """
    from core.services.system import bypass_replication_triggers

    updated = 0
    for hist in EventHistory.objects.iterator(chunk_size=500):
        before, before_changed = _reencrypt_data_json(hist.data_before)
        after, after_changed = _reencrypt_data_json(hist.data_after)
        if not (before_changed or after_changed):
            continue
        updated += 1
        if dry_run:
            continue
        with transaction.atomic(), bypass_replication_triggers():
            EventHistory.objects.filter(pk=hist.pk).update(
                data_before=before,
                data_after=after,
            )
    return updated


def _reencrypt_totp_devices(dry_run: bool) -> tuple[int, list[int]]:
    """Wickelt jedes TOTP-Secret unter den aktuellen Primaerschluessel neu ein.

    Refs #1362: Das Secret liegt at rest Fernet-verschluesselt in
    ``otp_totp_totpdevice.key``. Bei einer Key-Rotation (MultiFernet) bleiben
    Alt-Tokens dank Mehr-Key-Decrypt zwar lesbar — dieser Lauf wickelt sie
    aber aktiv unter den neuen Primaerschluessel um, sodass ein
    ausrangierter Schluessel wirklich entfernt werden kann. Ein
    unmigrierter Klartext-Key wird dabei mitgeheilt.

    Rewrap-Race (Refs #1362, Review-Befund): Der eigentliche Verursacher —
    ``django-otp.verify_token`` schreibt beim Verify ein Voll-``save()`` inkl.
    (evtl. Alt-Key-)``key`` — ist an der Quelle geschlossen: der Verify-Pfad
    laesst ``key`` nun unangetastet (siehe :class:`core.models.mfa.
    EncryptedTOTPDevice`). Ein Row-Lock im Command allein koennte diese Race
    NICHT schliessen, weil der Verify keinen Lock nimmt und einen vor dem
    Lock gelesenen Wert **nach** unserem Commit zurueckschreiben koennte.

    Zusaetzlich haerten wir hier den Rewrap gegen jeden **anderen** parallelen
    Schreiber der ``key``-Spalte ab (kuenftige Re-Provisionierung, zweiter
    Rewrap-Lauf): je Geraet eine kurze ``transaction.atomic()`` mit
    ``select_for_update()`` (Row-Lock) + wertgebundenem Compare-and-Swap-
    ``update()``. Aendert sich die Zeile zwischen unserem gelockten Lesen und
    dem Schreiben doch (0 betroffene Zeilen), wird bis zu einer Obergrenze
    erneut versucht und andernfalls die pk laut gemeldet — damit der Operator
    den Alt-Schluessel erst nach einem sauberen Lauf entfernt (idempotent).

    Rueckgabe: ``(updated, contended_pks)`` — die Formatierung der
    Kontentions-Warnung (``self.style``) bleibt Command-Sache, hier wird nur
    erkannt und retried.
    """
    from core.models import EncryptedTOTPDevice
    from core.services.security.totp import decrypt_totp_key, encrypt_totp_key

    updated = 0
    contended: list[int] = []
    # pks vorab einsammeln statt ``select_for_update()`` ueber einen offenen
    # ``iterator()``-Cursor zu spannen — je Geraet eine eigene kurze,
    # gelockte Transaktion (minimaler Blast-Radius, keine Tabellen-weiten Locks).
    pks = list(EncryptedTOTPDevice.objects.values_list("pk", flat=True))
    for pk in pks:
        if dry_run:
            key = EncryptedTOTPDevice.objects.filter(pk=pk).values_list("key", flat=True).first()
            if not key:
                continue
            decrypt_totp_key(key)  # validiert Entschluesselbarkeit vorab (deckt Alt-Key-Reste auf)
            updated += 1
            continue
        outcome = _rewrap_one_totp_device(pk, decrypt_totp_key, encrypt_totp_key)
        if outcome == "rewrapped":
            updated += 1
        elif outcome == "contended":
            contended.append(pk)
    return updated, contended


def _rewrap_one_totp_device(pk, decrypt_totp_key, encrypt_totp_key, *, attempts: int = 3) -> str:
    """Rewrap eines einzelnen TOTP-Geraets unter Row-Lock + Compare-and-Swap.

    Liest ``key`` unter ``select_for_update()`` (also den *aktuell*
    committeten Wert — ein unmittelbar zuvor committeter Verify wird
    mitgenommen) und schreibt wertgebunden zurueck
    (``filter(pk=pk, key=current).update(...)``). Geschrieben wird per
    ``update()`` bewusst am Proxy-``save`` vorbei (das ein bereits
    verschluesseltes Secret unangetastet liesse) und der Wert-Filter macht
    die Schreiboperation zur atomaren CAS. Kollidiert ausnahmsweise dennoch
    etwas (0 betroffene Zeilen), wird bis ``attempts`` erneut versucht.

    Rueckgabe: ``"rewrapped"`` (Secret frisch umgewickelt), ``"empty"``
    (kein Secret, uebersprungen) oder ``"contended"`` (nach ``attempts``
    weiter Kontention → Melde-Anker fuer den Operator).
    """
    from core.models import EncryptedTOTPDevice

    for _ in range(attempts):
        with transaction.atomic():
            current = (
                EncryptedTOTPDevice.objects.select_for_update().filter(pk=pk).values_list("key", flat=True).first()
            )
            if not current:
                return "empty"
            new_value = encrypt_totp_key(decrypt_totp_key(current))
            affected = EncryptedTOTPDevice.objects.filter(pk=pk, key=current).update(key=new_value)
        if affected:
            return "rewrapped"
    return "contended"


def _reencrypt_attachments(dry_run: bool, *, on_contended: Callable[[], None] | None = None) -> int:
    """``Attachment.original_filename_encrypted`` ist ein Fernet-
    verschluesseltes JSON-Marker-Dict. Refs #2131: Der Marker selbst ist der
    Versionsstempel fuer das wertgebundene Compare-and-Swap.

    M24 (Refs #1738, ADR-039): Die Iteration ist **owner-generisch** und
    war es immer — sie laeuft ueber ALLE ``Attachment``-Zeilen, gleich ob
    sie an einem Ereignis, einer Person oder einem Fall haengen, und
    schliesst soft-geloeschte (``deleted_at``) mit ein. Kein Owner-Filter,
    keine Verhaltensaenderung durch die Owner-Generik; die neuen
    personen-/falleigenen Zeilen rotieren automatisch mit.
    """
    updated = 0
    pks = list(Attachment.objects.values_list("pk", flat=True))
    for pk in pks:
        att = Attachment.objects.filter(pk=pk).first()
        if att is None:
            continue
        value = att.original_filename_encrypted
        if not is_encrypted_value(value):
            continue
        decrypted = safe_decrypt(value)
        if decrypted == "[verschlüsselt]":
            continue
        if dry_run:
            updated += 1
            continue
        if _locked_compare_and_swap(
            Attachment,
            pk,
            expected={"original_filename_encrypted": value},
            updates={"original_filename_encrypted": encrypt_field(decrypted)},
        ):
            updated += 1
        elif on_contended is not None:
            on_contended()
    return updated


def rotate_all(*, dry_run: bool, on_totp_contended: Callable[[list[int]], None] | None = None) -> dict[str, int]:
    """Fuehrt alle sieben Rotationspfade aus und meldet Kontention additiv.

    ``on_totp_contended`` ist ein optionaler Callback fuer die TOTP-Rewrap-
    Kontentions-Warnung: Erkennung und Retry-Logik leben hier, die
    Formatierung ueber ``self.style``/``self.stderr`` bleibt Command-Sache.

    ``contended`` ist bewusst eine Gesamtzahl ueber alle sieben Modellpfade:
    Fuer den Betriebsentscheid "Alt-Schluessel noch nicht entfernen, Lauf
    wiederholen" ist jede einzelne uebersprungene Zeile gleich kritisch. Die
    bestehende TOTP-Warnung nennt zusaetzlich die betroffenen Geraete-PKs.
    """
    contended = 0

    def _count_contended() -> None:
        nonlocal contended
        contended += 1

    events_updated = _reencrypt_events(dry_run, on_contended=_count_contended)
    history_updated = _reencrypt_event_history(dry_run)
    attachments_updated = _reencrypt_attachments(dry_run, on_contended=_count_contended)
    totp_updated, totp_contended = _reencrypt_totp_devices(dry_run)
    if totp_contended and on_totp_contended is not None:
        on_totp_contended(totp_contended)
    contended += len(totp_contended)
    clients_updated = _rotate_dynamic_json_model(Client, dry_run=dry_run, on_contended=_count_contended)
    cases_updated = _rotate_dynamic_json_model(Case, dry_run=dry_run, on_contended=_count_contended)
    rectifications_updated = _reencrypt_rectification_requests(
        dry_run,
        on_contended=_count_contended,
    )

    return {
        "events": events_updated,
        "event_history": history_updated,
        "attachments": attachments_updated,
        "totp_devices": totp_updated,
        "clients": clients_updated,
        "cases": cases_updated,
        "rectification_requests": rectifications_updated,
        "contended": contended,
    }
