"""Schreibpfade der Anhang-Domaene — owner-generisch (M24/AP2, Refs #1738).

Umgezogen aus :mod:`core.services.events.crud`: die Attachment-Mutationen
waren nie event-spezifisch, sie brauchten nur einen Owner mit
``data_json``-Feld und dem Reverse-Accessor ``attachments``. Seit ADR-039
sind das Ereignis, Person und Fall.

``events/crud.py`` behaelt duenne, delegierende Aliasse (``finalize_staged_files``,
``apply_attachment_changes``) — Bestands-Importe (``core.services.events``) und
der Event-View-Pfad bleiben dadurch unveraendert gueltig.

**Patch-Vertrag:** ``prepare_encrypted_upload``/``commit_staged_upload``/
``soft_delete_attachment_chain`` werden weiterhin **funktionslokal aus dem
Paket** ``core.services.file_vault`` geholt (nicht aus dem Submodul). Tests
patchen genau diesen Paket-Alias (z. B.
``patch("core.services.file_vault.commit_staged_upload")``); ein Modul-Level-
Import oder ein Submodul-Import wuerde den Mock zur Aufrufzeit umgehen.

**Lock-Vertrag (Refs #1956):** Scan + Verschluesselung laufen ausschliesslich in
:func:`stage_attachment_uploads` — die gehoert VOR den ``transaction.atomic``
des Aufrufers. Im Transaktionsblock faellt nur noch der schnelle DB-Write
(``commit_staged_upload``) an. Sonst haelt der per-Facility
``pg_advisory_xact_lock`` des AuditLog-Inserts (Hashkette) bis zu
``CLAMAV_TIMEOUT`` je Datei und serialisiert jede audit-schreibende Aktion
derselben Einrichtung.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import FieldTemplate
from core.services.dynamic_data import build_field_template_lookup, is_file_marker, normalize_file_marker
from core.services.fk_locks import fk_lock_targets, lock_fk_targets


@contextmanager
def quota_backstop_audit_boundary() -> Iterator[None]:
    """Der aeusserste Transaktionsrand fuer die Kontingent-Rechenschaft (#2178).

    **Gehoert UM den ``transaction.atomic()``-Block**, nicht hinein::

        with quota_backstop_audit_boundary():
            with transaction.atomic():
                obj = create_x(...)
                finalize_staged_files(obj, request.user, staged.by_slug, doc_type)
            staged.commit()

    Warum das noetig ist: Reisst das Speicherkontingent erst im Commit
    (``storage._commit_staged_upload``), rollt der ``ValidationError`` die
    gesamte Aufrufer-Transaktion zurueck. Eine Audit-Zeile, die INNERHALB dieser
    Transaktion geschrieben wird, verschwindet mit ihr — und der Vorab-Check
    ersetzt sie nicht: im abgefangenen Rennen hat er erfolgreich geprueft und
    ist ohne Audit zurueckgekehrt. Ohne diesen Rand bliebe genau die neu
    abgefangene Ablehnung unprotokolliert.

    Die Ausnahme traegt die Nutzdaten (``StorageQuotaBackstopError``); hier —
    nach dem Rollback, aber noch auf derselben Verbindung — wird daraus die
    dauerhafte ``ATTACHMENT_QUOTA_EXCEEDED``-Zeile. Eine zweite Verbindung
    waere falsch: sie liefe in den Advisory-Lock der Hashkette, den die
    Aufrufer-Transaktion selbst haelt.

    Anschliessend wird dieselbe Ausnahme weitergereicht — sie IST der
    ``ValidationError`` mit dem Text des Vorab-Checks, Views und Formulare
    reagieren also unveraendert. ``write_audit`` ist idempotent; ein
    verschachtelter zweiter Rand schreibt nichts doppelt.
    """
    from core.services.file_vault.policy import StorageQuotaBackstopError  # noqa: PLC0415 — Zyklus vermeiden

    try:
        yield
    except StorageQuotaBackstopError as exc:
        exc.write_audit()
        raise


class AttachmentPurgeBlockedError(PermissionDenied):
    """Ein physisches Loeschen ist gesperrt (Refs #1874).

    Erbt von :class:`~django.core.exceptions.PermissionDenied`, damit ein
    kuenftiger Aufrufer, der die Ausnahme NICHT behandelt, mit 403 endet statt
    mit einem 500er — die sichere Richtung fuer einen Loeschpfad, der nicht
    stattfinden darf. Der View faengt sie ab und macht daraus eine lesbare
    Meldung.
    """


class StagedUploadBatch:
    """Ergebnis von :func:`stage_uploads` — die gestagten Uploads je Slug.

    ``by_slug`` ist das ``{slug: [StagedUpload, ...]}``-Dict in Upload-
    Reihenfolge, wie :func:`finalize_staged_files` es erwartet. ``commit()``
    markiert den erfolgreichen Commit der Aufrufer-Transaktion; ohne ihn
    räumt der Context-Manager beim Verlassen alle ``.enc`` weg.
    """

    def __init__(self, by_slug: dict[str, list]):
        self.by_slug = by_slug
        self._committed = False

    def commit(self) -> None:
        """Markiert den Commit der Aufrufer-Transaktion als erfolgt — direkt
        nach dem ``transaction.atomic()``-Block aufrufen (#1345)."""
        self._committed = True


@contextmanager
def stage_uploads(facility, user, file_fields) -> Iterator[StagedUploadBatch]:
    """Pre-Transaction-Staging der Create-Views (Event/Client/Case, Refs #1831).

    Refs #1345: ClamAV-Scan + Fernet-Verschluesselung laufen VOR der Transaktion
    — der Owner-Create schreibt eine AuditLog-Zeile, deren INSERT den
    per-Facility ``pg_advisory_xact_lock`` der Hashkette bis zum Commit hält;
    läge die (bis ``CLAMAV_TIMEOUT`` dauernde) Datei-Verarbeitung im selben
    ``atomic``, blockierte sie jede andere audit-schreibende Aktion derselben
    Einrichtung. ``owner=None``: das Ziel-Objekt existiert noch nicht.

    Cleanup-Garantie: JEDE Nicht-Commit-Ausgang — Rollback, Duplikat-Replay-
    Redirect, unerwartete Exception, ``ValidationError`` beim Stagen (Policy-/
    Virus-Reject; der zugehörige SECURITY_VIOLATION-Audit ist bereits durable
    geschrieben und überlebt bewusst) — lässt die vorab verschlüsselten
    ``.enc`` als Disk-Waisen zurück; der Context-Manager entfernt sie restlos.
    Nach erfolgreichem Commit ``batch.commit()`` rufen (direkt nach dem
    ``transaction.atomic()``-Block, minimalstes Fenster).

    Nutzung::

        with stage_uploads(facility, request.user, file_fields) as staged:
            with transaction.atomic():
                obj = create_x(...)
                finalize_staged_files(obj, request.user, staged.by_slug, doc_type)
            staged.commit()

    ``ValidationError`` bleibt beim Aufrufer (jede View formuliert ihre eigene
    Antwort); ``file_fields`` ist das ``{slug: [UploadedFile, ...]}``-Dict aus
    ``split_file_and_text_data``.
    """
    from core.services.file_vault import prepare_encrypted_upload

    staged_all: list = []
    staged_by_slug: dict[str, list] = {}
    batch: StagedUploadBatch | None = None
    # Refs #2129 (R-L7a-5): laufende Summe der bereits gestagten
    # Dateien. Sie existieren noch als KEINE ``Attachment``-Zeile — ohne diesen
    # Offset saehe jede Datei desselben Submits denselben alten DB-Verbrauch,
    # und ein einzelnes Formular koennte das Kontingent mit bis zu
    # ``FILE_VAULT_MAX_UPLOAD_FILES`` Dateien deterministisch reissen.
    # Gezaehlt wird ``staged.file_size`` (nach der Metadaten-Bereinigung) —
    # genau der Wert, der spaeter in der Zeile steht.
    pending_bytes = 0
    try:
        for slug, uploaded_list in file_fields.items():
            prepared = []
            for uploaded_file in uploaded_list or []:
                staged = prepare_encrypted_upload(
                    facility, uploaded_file, user, owner=None, pending_bytes=pending_bytes
                )
                pending_bytes += staged.file_size
                staged_all.append(staged)
                prepared.append(staged)
            if prepared:
                staged_by_slug[slug] = prepared
        batch = StagedUploadBatch(staged_by_slug)
        yield batch
    finally:
        if batch is None or not batch._committed:
            for staged in staged_all:
                staged.output_path.unlink(missing_ok=True)


def finalize_staged_files(owner, user, staged_by_slug, document_type):
    """Persistiere vor-verschluesselte Uploads an einen frisch erzeugten Owner.

    Refs #1345: Der teure ClamAV-Scan + Fernet-Encrypt laeuft bereits VOR der
    Transaktion (``prepare_encrypted_upload``); hier wird pro Slug nur noch der
    schnelle DB-Insert (``commit_staged_upload``) plus der Stufe-B-Marker
    (``__files__``) geschrieben. Dadurch haelt der AuditLog-Hashchain-Advisory-
    Lock (aus dem Owner-Create) nicht mehr ueber den Scan. Der Aufrufer ist fuer
    den umliegenden ``transaction.atomic``-Block zustaendig (Refs #584).

    ``staged_by_slug`` ist ein ``{slug: [StagedUpload, ...]}``-Dict in
    Upload-Reihenfolge (siehe ``EventCreateView.post``). ``owner`` ist ein
    ``Event``, ``Client`` oder ``Case`` (Refs #1738).

    Reiner ADD-Batch: Dieser Pfad haengt ausschliesslich neue Ketten an einen
    frisch erzeugten Owner und fasst **keine** Bestandszeile an. Die
    Batch-Sperrmenge des Vertrags aus :func:`lock_batch_attachment_rows` ist
    hier also leer — es gibt nichts vorzusperren (Refs #2178).

    Sperr-Reihenfolge (Refs #2260): Jeder ``commit_staged_upload`` nimmt seine
    Owner-Stufe selbst. Der Owner ist hier in derselben Transaktion frisch
    erzeugt — seine Zeile kann deshalb gar nicht bei einem anderen Vorgang
    liegen, und die Personenzeile hat der Create-Service bereits vor seiner
    Audit-Zeile gesperrt.
    """
    from core.services.file_vault import commit_staged_upload

    if not staged_by_slug:
        return
    field_templates = build_field_template_lookup(document_type)
    for slug, staged_list in staged_by_slug.items():
        ft = field_templates.get(slug)
        if not ft or not staged_list:
            continue
        entries = []
        for idx, staged in enumerate(staged_list):
            attachment = commit_staged_upload(owner, ft, staged, user, sort_order=idx)
            entries.append({"id": str(attachment.pk), "sort": idx})
        owner.data_json[slug] = {"__files__": True, "entries": entries}
    owner.save(update_fields=["data_json"])


def replace_upload_key(slug, entry_id):
    """Der ``request.FILES``-Schluessel des per-Entry-Replace-Inputs.

    Eine Stelle fuer beide Seiten: :func:`stage_attachment_uploads` staged
    darunter, :func:`_apply_replace` holt darunter wieder ab. Auseinanderlaufen
    hiesse: verschluesselte ``.enc`` ohne DB-Zeile.
    """
    return f"{slug}__replace__{entry_id}"


def _iter_file_fields(owner, user, document_type, data_json=None):
    """Gemeinsame Basis von Staging und Anwendung.

    Liefert je FILE-Feld des Typs, das die Rolle sehen darf, ein Tupel
    ``(slug, field_template, current_entries, attachments_by_pk)``.

    Warum gemeinsam (Refs #1956): Das Sensitivity-Gate (R11, Refs #1476/#1666)
    lag frueher NUR in :func:`apply_attachment_changes`. Zieht man Scan +
    Verschluesselung vor die Transaktion, ohne das Gate mitzuziehen, wuerden
    Dateien fuer Felder gescannt und verschluesselt, die die Rolle gar nicht
    aendern darf — als ``.enc``-Waise auf der Disk und als Policy-Umgehung.
    Ebenso muss die REPLACE-Aufloesung auf beiden Seiten identisch sein: der
    Staging-Pfad darf **nur** fuer tatsaechlich vorhandene Entries scannen,
    sonst genuegten frei erfundene ``<slug>__replace__<uuid>``-Schluessel, um
    beliebig viele Virenscans und ``.enc``-Waisen zu erzwingen.

    ``data_json`` erlaubt dem Aufrufer, die **kuenftige** Markerlage
    vorzugeben (der Update-Pfad hat sie als ``merged`` schon berechnet, aber
    noch nicht gespeichert). ``None`` = Markerlage des Owners lesen.
    """
    from core.services.compliance import user_can_see_field

    field_templates = build_field_template_lookup(document_type)
    doc_sensitivity = document_type.sensitivity
    marker_source = owner.data_json if data_json is None else data_json
    for slug, ft in field_templates.items():
        if ft.field_type != FieldTemplate.FieldType.FILE:
            continue

        # R11 (Refs #1476/#1666): Sensitivity-Gate auch auf Anhang-Mutation.
        # Der Update-Pfad entfernt Text-Felder oberhalb der Rollen-Sicht
        # per ``remove_restricted_fields`` aus dem Form; die Anhang-Mutation
        # liest REMOVE/REPLACE/ADD aber direkt aus ``request.POST``/``FILES``
        # und umging dieses Gate. Ein User, der den Owner sieht (Doc-Type-Gate),
        # aber dieses FILE-Feld wegen eines feldbezogenen Sensitivity-Overrides
        # NICHT sehen darf, darf dessen Anhaenge nicht loeschen/ersetzen/
        # ergaenzen — der Marker bleibt dann unveraendert. (``user is None`` =
        # System-/Service-Kontext ohne Rolle → kein Gate.)
        if user is not None and not user_can_see_field(user, doc_sensitivity, ft.sensitivity):
            continue

        current_entries = normalize_file_marker((marker_source or {}).get(slug))

        # Refs #782 (C-17): Bulk-Load aller Attachments des aktuellen Slugs
        # in einer Query — vorher lud der REMOVE/REPLACE-Pfad jeden Entry
        # einzeln (``filter(pk=entry["id"]).first()``) und produzierte N+1.
        entry_ids = [entry["id"] for entry in current_entries]
        attachments_by_pk = (
            {str(att.pk): att for att in owner.attachments.filter(pk__in=entry_ids)} if entry_ids else {}
        )

        yield slug, ft, current_entries, attachments_by_pk


def stage_attachment_uploads(owner, user, request_files, file_fields, document_type, *, data_json=None):
    """Scannen + verschluesseln aller REPLACE-/ADD-Uploads — OHNE DB-Write.

    Refs #1956: Gehoert VOR den ``transaction.atomic``-Block des Aufrufers (das
    Pendant zum Create-Pfad, der seit #1345 so arbeitet). Liefert
    ``(staged_by_key, staged_all)``:

    * ``staged_by_key`` — ``{schluessel: [StagedUpload, ...]}``. Der Schluessel
      ist fuer ADD der Feld-Slug (Reihenfolge = Upload-Reihenfolge), fuer
      REPLACE der Formular-Schluessel aus :func:`replace_upload_key`.
    * ``staged_all`` — flache Liste aller gestagten Dateien, damit der Aufrufer
      sie im ``finally`` wieder entfernen kann, wenn seine Transaktion NICHT
      committet (Cleanup-Garantie aus #662).

    Scheitert eine Datei an der Policy (Virus, Magic-Bytes, Groesse), werden
    die bereits gestagten ``.enc`` hier entfernt und die ``ValidationError``
    weitergereicht — der Aufrufer sieht dann eine leere Staging-Menge.
    """
    from core.services.file_vault import prepare_encrypted_upload

    # Ohne jede hochgeladene Datei gibt es nichts zu stagen. Der reine
    # Text-Edit — der haeufigste Fall — spart sich damit die Bestands-Queries
    # der Marker-Aufloesung komplett.
    if not request_files and not any(file_fields.values()):
        return {}, []

    # ADR-039 §2: die Facility kommt aus dem Owner, nie aus dem Request.
    facility = owner.facility
    staged_by_key: dict[str, list] = {}
    staged_all: list = []
    # Refs #2129 (R-L7a-5): eine laufende Summe fuer den GANZEN
    # Request — REPLACE- und ADD-Uploads eines Submits zaehlen gemeinsam gegen
    # das Kontingent (siehe ``stage_uploads``). Ersetzte Vorversionen bleiben
    # bis zur physischen Tilgung auf der Platte und zaehlen ohnehin weiter.
    pending_bytes = 0
    try:
        for slug, _ft, current_entries, attachments_by_pk in _iter_file_fields(owner, user, document_type, data_json):
            for entry in current_entries:
                att = attachments_by_pk.get(str(entry["id"]))
                if att is None:
                    continue
                key = replace_upload_key(slug, att.entry_id)
                replace_file = request_files.get(key)
                if replace_file is None:
                    continue
                staged = prepare_encrypted_upload(
                    facility, replace_file, user, owner=owner, pending_bytes=pending_bytes
                )
                pending_bytes += staged.file_size
                staged_all.append(staged)
                staged_by_key[key] = [staged]

            prepared = []
            for uploaded_file in file_fields.get(slug) or []:
                staged = prepare_encrypted_upload(
                    facility, uploaded_file, user, owner=owner, pending_bytes=pending_bytes
                )
                pending_bytes += staged.file_size
                staged_all.append(staged)
                prepared.append(staged)
            if prepared:
                staged_by_key[slug] = prepared
    except Exception:
        # Kein halber Staging-Stand auf der Disk (#662): der Aufrufer bekommt
        # die Exception, aber keine Waisen.
        for staged in staged_all:
            staged.output_path.unlink(missing_ok=True)
        raise

    return staged_by_key, staged_all


def _apply_remove(slug, current_entries, attachments_by_pk, request_post, owner, user):
    """REMOVE-Schritt: hidden CSV ``<slug>__remove`` listet zu loeschende
    ``entry_id``-Werte -> ``soft_delete_attachment_chain``. Liefert die um die
    geloeschten Entries bereinigte Entry-Liste.

    Der Einzelaufruf nimmt seine Sperren selbst (Refs #2260); die gebuendelte
    Vorsperre des Batches liegt davor, seine Abfrage findet die Zeilen also
    bereits gehalten vor und wartet nicht."""
    from core.services.file_vault import soft_delete_attachment_chain

    remove_raw = request_post.get(f"{slug}__remove", "")
    remove_ids = {x.strip() for x in remove_raw.split(",") if x.strip()}
    if not remove_ids:
        return current_entries
    filtered = []
    for entry in current_entries:
        att = attachments_by_pk.get(str(entry["id"]))
        if att and str(att.entry_id) in remove_ids:
            soft_delete_attachment_chain(owner, att.entry_id, user, field=slug)
            continue
        filtered.append(entry)
    return filtered


def _apply_replace(slug, current_entries, attachments_by_pk, staged_by_key, ft, owner, user):
    """REPLACE-Schritt: pro bestehendem Entry eine vorab gestagte Datei unter
    :func:`replace_upload_key` -> ``commit_staged_upload(supersedes=...)``.
    Liefert die (ggf. ersetzte) Entry-Liste. Reiner DB-Write (Refs #1956).

    Verbrauchte Schluessel werden aus ``staged_by_key`` entfernt (``pop``) —
    was am Ende uebrig bleibt, hat keine DB-Zeile bekommen und raeumt
    :func:`apply_attachment_changes` weg."""
    from core.services.file_vault import commit_staged_upload

    updated_entries = []
    for entry in current_entries:
        att = attachments_by_pk.get(str(entry["id"]))
        if not att:
            continue
        staged_list = staged_by_key.pop(replace_upload_key(slug, att.entry_id), None)
        if staged_list:
            new_att = commit_staged_upload(owner, ft, staged_list[0], user, supersedes=att)
            updated_entries.append({"id": str(new_att.pk), "sort": att.sort_order})
        else:
            updated_entries.append(entry)
    return updated_entries


def _apply_add(slug, updated_entries, staged_by_key, ft, owner, user):
    """ADD-Schritt: alle unter dem Slug gestagten Dateien werden mit frischen
    ``entry_id`` angehaengt. Liefert die erweiterte Liste. Reiner DB-Write.
    Verbraucht den Slug-Schluessel aus ``staged_by_key`` (``pop``)."""
    from core.services.file_vault import commit_staged_upload

    base_sort = (max((e.get("sort", 0) for e in updated_entries), default=-1)) + 1
    for idx, staged in enumerate(staged_by_key.pop(slug, None) or []):
        new_att = commit_staged_upload(owner, ft, staged, user, sort_order=base_sort + idx)
        updated_entries.append({"id": str(new_att.pk), "sort": base_sort + idx})
    return updated_entries


def _write_file_marker(owner, slug, updated_entries):
    """Schreibt bzw. entfernt den Stufe-B-Marker im ``owner.data_json``."""
    if updated_entries:
        owner.data_json[slug] = {"__files__": True, "entries": updated_entries}
    elif slug in owner.data_json:
        del owner.data_json[slug]


def lock_batch_attachment_rows(owner, entry_ids, *, creates_rows=False, user=None, field_templates=()) -> None:
    """Die Vorsperre eines Anhangs-Batches: Stufe 1 und Stufe 2 der Ordnung (#2178).

    Sperrt die Owner-/Personenzeilen und anschliessend ALLE Bestandszeilen der
    Batch-Menge in EINER Abfrage. ``creates_rows=True`` sagt, dass der Batch
    (mindestens) eine NEUE Zeile anlegt: die traegt den Owner-Fremdschluessel,
    dessen aufgeschobene Pruefung sonst erst zum COMMIT — hinter dem Ketten-Lock
    — auf der Owner-Zeile landete. Ein Batch, der weder eine Bestandszeile
    anfasst noch eine neue anlegt, sperrt weiterhin nichts.

    **Vertrag fuer Aufrufer mit eigener Transaktion:** Wer in derselben
    ``transaction.atomic()`` MEHRERE Anhangs-Schreibvorgaenge absetzt, sperrt
    vorher die Zeilen aller Ketten, die dieser Vorgang ersetzt oder loescht.

    Warum das noetig ist: ``commit_staged_upload`` sperrt im Replace-Modus nur
    die eine Zeile, die es selbst abloest — und zwar unmittelbar bevor es den
    Advisory-Lock der Einrichtung nimmt. Der haelt bis zum Ende der
    **aeusseren** Transaktion. Der zweite Commit desselben Batches sperrt seine
    Zeile also NACH einem bereits gehaltenen Advisory-Lock, waehrend
    ``soft_delete_attachment_chain`` die Zeile vor seiner Audit-Zeile nimmt:
    gegenlaeufige Reihenfolge, PostgreSQL loest den Zyklus mit SQLSTATE
    ``40P01`` auf. Eine einzige vorgezogene Sperrabfrage zieht saemtliche
    Zeilensperren des Batches vor den ersten Advisory-Lock.

    ``ORDER BY pk`` gibt der Sperrabfrage eine feste Reihenfolge, damit zwei
    gleichzeitige Batches derselben Einrichtung sich nicht gegenseitig
    verschraenken. Leere Menge = keine Abfrage (der reine ADD-Batch zahlt
    nichts).

    **Nur mit offener Aufrufer-Transaktion** (Refs #2178): Ohne
    ``atomic()`` des Aufrufers gibt es nichts vorzusperren — dann oeffnet jede
    Storage-Operation ihre eigene kurze Transaktion, schuetzt ihre Mutation
    selbst und gibt Zeilensperre wie Advisory-Lock sofort wieder frei; ueber
    mehrere Operationen hinweg haelt niemand einen Lock, und die verdrehte
    Reihenfolge kann gar nicht entstehen. Eine ``select_for_update``-Abfrage
    waere dort nicht nur wirkungslos: Django lehnt sie im Autocommit-Modus mit
    ``TransactionManagementError`` ab und braeche damit den ausdruecklich
    zugesicherten transaktionslosen Service-Einstieg.

    Die Sperre laeuft ueber den Reverse-Accessor des Owners (ADR-039 §2) und
    greift ganze Ketten (``entry_id``): Ersetzen fasst die aktuelle Zeile an,
    Soft-Delete alle Zeilen der Kette.

    **FK-Ziele der neuen Zeilen** (Refs #2337), im Anschluss an Stufe 1 und 2
    und noch vor jedem Ketten-Lock: Legt der Batch
    Zeilen an (``creates_rows`` oder ``field_templates`` nicht leer — auch
    Ersetzen legt eine neue Version an), nimmt er zusaetzlich ``FOR KEY
    SHARE`` auf JEDES Fremdschluessel-Ziel, das der Insert traegt: Einrichtung,
    Owner, Feldvorlage, anlegende Person. Die Ziele leitet
    :mod:`core.services.fk_locks` aus dem Modell ab — derselbe Helfer wie im
    Importpfad (``_lock_job_fk_rows``); ein spaeter hinzukommender
    Fremdschluessel ist damit in beiden Pfaden dabei. Ohne diese Stufe nahm
    erst die aufgeschobene FK-Pruefung beim COMMIT diese Sperren — hinter dem
    Ketten-Lock. ``field_templates`` sind die Vorlagen der Felder, die eine
    Zeile bekommen; ``user`` ist die anlegende Person.
    """
    field_templates = list(field_templates)
    creates_rows = creates_rows or bool(field_templates)
    if not entry_ids and not creates_rows:
        return
    lock_attachment_rows_in_facility_order(owner, entry_ids, lock_owner_rows=True)
    if creates_rows and connection.in_atomic_block:
        lock_fk_targets(_new_row_fk_targets(owner, user, field_templates))


def _new_row_fk_targets(owner, user, field_templates) -> list:
    """Die FK-Ziele aller Anhangszeilen, die ein Batch anlegen wird (Refs #2337).

    ``_new_row_fk_values`` benennt die Werte, die der Insert ausdruecklich
    setzt; alle uebrigen Fremdschluessel des Modells traegt er mit ihrem
    Default, und genau so leitet :func:`~core.services.fk_locks.fk_lock_targets`
    sie ab. Die Einzelmengen werden vereinigt und neu sortiert, damit die
    gemeinsame Menge in EINER Reihenfolge (Tabelle, dann PK) gesperrt wird.
    """
    from core.models.attachment import Attachment  # noqa: PLC0415 — Zyklus vermeiden

    targets: set = set()
    for field_template in field_templates or [None]:
        targets.update(fk_lock_targets(Attachment, _new_row_fk_values(owner, user, field_template)))
    return sorted(targets, key=lambda entry: (entry[0], str(entry[2])))


def _new_row_fk_values(owner, user, field_template) -> dict:
    """Die Fremdschluesselwerte, die ``commit_staged_upload`` einer neuen Zeile gibt.

    Einrichtung aus dem Owner (ADR-039 §2), der EINE Owner-Fremdschluessel,
    Feldvorlage und anlegende Person. Die beiden anderen Owner-Spalten und
    ``superseded_by`` bleiben beim Insert leer. Welche Fremdschluessel hier
    stehen muessen, haelt ``test_fk_lock_targets_guard`` gegen das Modell fest.
    """
    from core.models.attachment import owner_field_name  # noqa: PLC0415 — Zyklus vermeiden

    return {
        "facility_id": owner.facility_id,
        f"{owner_field_name(owner)}_id": owner.pk,
        "field_template_id": getattr(field_template, "pk", None),
        "created_by_id": getattr(user, "pk", None),
    }


def lock_attachment_rows_in_facility_order(owner, entry_ids, *, lock_owner_rows=False, deletes_rows=False) -> list:
    """Die EINE Umsetzung der einrichtungsweiten Sperr-Reihenfolge (Refs #2260).

    Verbindliche Ordnung fuer jeden anhangsmutierenden Pfad einer Einrichtung:

    1. Owner-/Personen-/Ereignis-Zeilen,
    2. Attachment-Zeilen in aufsteigender ``pk``-Reihenfolge,
    3. Ketten-Advisory-Lock des AuditLog (Audit-Schreiben) zuletzt.

    Sie dehnt den Batch-Vertrag aus :func:`lock_batch_attachment_rows` auf die
    uebrigen anhangsmutierenden Pfade aus. Jeder Pfad, der beide Sperrsorten
    nimmt, nimmt sie damit in derselben Richtung; die Gegenrichtung ist der
    Zyklus, den PostgreSQL mit SQLSTATE ``40P01`` aufloest.

    Die Haltedauer des Ketten-Locks steigt dadurch nicht: er wird weiterhin
    erst vom Audit-Write genommen und nicht ueber eine Kaskade vorgezogen —
    diese Funktion nimmt ihn gar nicht, sie zieht nur die Zeilensperren VOR
    ihn.

    ``entry_ids`` ist die Menge der Ketten, die der Vorgang anfasst; ``None``
    meint ALLE Zeilen des Owners (physischer Cleanup einer Loesch- oder
    Anonymisierungskaskade). Eine leere Menge fragt die Anhangszeilen nicht ab
    — der reine ADD-Batch braucht nur Stufe 1.

    ``lock_owner_rows=True`` nimmt Stufe 1 gleich mit. Das tut JEDER Pfad, der
    den Owner mittelbar oder unmittelbar schreibt; ausgenommen ist allein der
    physische Cleanup, der die Anhangszeilen einer bereits laufenden Owner-
    Kaskade abraeumt und deren Owner-Zeilen deshalb bereits gesperrt vorfindet.

    **Idempotent:** Eine in dieser Transaktion bereits gehaltene Zeile erneut
    zu sperren kostet eine Abfrage, aber kein Warten — deshalb nimmt jeder
    Einstieg seine Sperren bedingungslos selbst, statt sich auf eine
    Behauptung des Aufrufers zu verlassen.

    **Nur mit offener Aufrufer-Transaktion** (Vertrag aus Refs #2178): ohne
    ``atomic()`` gibt es nichts vorzusperren, jede Storage-Operation schuetzt
    ihre Mutation dann selbst, und ``select_for_update`` waere im
    Autocommit-Modus ein ``TransactionManagementError``.

    ``deletes_rows=True`` sagt: dieser Vorgang LOESCHT die Zeilen. Dann gehoert
    in die Vorsperre nicht nur die eigene Loeschmenge, sondern auch jede Zeile,
    deren ``superseded_by`` in sie zeigt — auch die eines FREMDEN Owners.

    Warum es die gibt: Ein Zusammenfuehren verschiebt nur die im FILE-Marker
    referenzierten Zeilen; die ersetzte Vorgeschichte bleibt bewusst beim
    abgebenden Datensatz (Marker-Invariante I-M1). Ein Versionsstrang kann
    danach ueber zwei Owner laufen. ``superseded_by`` ist ein
    ``SET_NULL``-Selbstbezug: Djangos Loeschsammler setzt ihn beim ``delete()``
    per ``UPDATE`` auf ``NULL`` — ohne Owner-Filter. Diese Zeile wuerde also
    erstmals gesperrt, NACHDEM der Vorgang den Ketten-Advisory-Lock haelt
    (beim endgueltigen Loeschen steht das Protokoll fachlich vor der Tilgung);
    ein Pfad, der jene Zeile haelt und danach den Ketten-Lock will
    (Anonymisierung des abgebenden Datensatzes), schliesst damit den Zyklus.
    Beim physischen Cleanup kommt hinzu, dass die erstmals gesperrte
    Anhangsmenge nicht mehr aufsteigend ist, wenn die fremde Zeile die
    kleinere PK hat.

    EIN Schritt genuegt: die referenzierenden Zeilen werden nur AKTUALISIERT,
    nicht geloescht — sie loesen ihrerseits keine Kaskade aus. Beide Mengen
    werden in EINER gemeinsam nach ``pk`` sortierten Abfrage gesperrt, ueber
    die Einrichtung des Owners gescopt und bewusst owner-uebergreifend. Die
    Owner-/Personenzeile des fremden Datensatzes bleibt ungesperrt: die
    Reihenfolge zweier Personenzeilen ist ein eigener Vorgang.

    Liefert die gesperrten Attachment-PKs der EIGENEN Menge in
    Sperr-Reihenfolge — bei ``deletes_rows=True`` also ohne die nur
    mitgesperrten Fremdzeilen, damit Zaehlwerte des Aufrufers (etwa die
    Versionszahl im Protokoll) weiterhin den eigenen Vorgang beschreiben.
    """
    if not connection.in_atomic_block:
        return []
    ids = None if entry_ids is None else sorted({str(entry_id) for entry_id in entry_ids if entry_id})
    if lock_owner_rows:
        lock_owner_rows_in_facility_order(owner)
    if ids is not None and not ids:
        # Leere Mutationsmenge: keine Abfrage auf den Anhangszeilen. Wer die
        # Ordnung ueberhaupt nicht braucht, ruft gar nicht erst auf — die
        # Entscheidung, ob ein Vorgang Stufe 1 braucht, faellt beim Aufrufer
        # (``lock_batch_attachment_rows``), nicht hier.
        return []
    rows = owner.attachments.all()
    if ids is not None:
        rows = rows.filter(entry_id__in=ids)
    if not deletes_rows:
        return list(rows.select_for_update().order_by("pk").values_list("pk", flat=True))

    from core.models.attachment import Attachment  # noqa: PLC0415 — Zyklus vermeiden

    eigene = set(rows.values_list("pk", flat=True))
    if not eigene:
        return []
    gesperrt = list(
        Attachment.objects.filter(facility_id=owner.facility_id)
        .filter(Q(pk__in=eigene) | Q(superseded_by_id__in=eigene))
        .select_for_update()
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    # Nur die eigene Menge zurueck — und nur, soweit die Sperrabfrage sie nach
    # dem Warten noch gesehen hat (``FOR UPDATE`` prueft die Sichtbarkeit
    # erneut). Ein nebenlaeufig bereits vernichteter Strang faellt damit wie
    # bisher auf 0.
    return [pk for pk in gesperrt if pk in eigene]


def lock_owner_rows_in_facility_order(owner) -> None:
    """Stufe 1 der Ordnung: Personenzeile, dann die Owner-Zeile (ADR-050 D5.5).

    Dieselbe Richtung, die :func:`_lock_owner_row` fuer den Vernichtungspfad
    nimmt und die der manuelle Ereignis-Loeschweg, die Hold-Anlage und die
    Tilgungskaskaden bereits fahren (``core_client`` -> ``core_case``/
    ``core_event``). Ein Owner ohne Personenbezug (anonymes Ereignis) hat
    schlicht keine erste Stufe.

    Die Person eines Fall- oder Ereignis-Owners kommt nicht aus der
    uebergebenen Instanz, sondern aus der Owner-Zeile selbst (Refs #2379):
    :func:`_lock_current_owner_person` liest die Bindung frisch, sperrt diese
    Person und zieht ``owner.client_id`` nach.

    Doppeltes Sperren derselben Zeile in derselben Transaktion ist folgenlos —
    die Pfade, die diese Zeilen ohnehin spaeter sperren (etwa das Art.-18-Gate
    oder der Optimistic-Locking-Reload), behalten ihre Sperre unveraendert und
    finden sie hier nur bereits gehalten vor.

    Ohne offene Transaktion gibt es nichts vorzusperren (derselbe Vertrag wie
    in :func:`lock_attachment_rows_in_facility_order`); ``select_for_update``
    waere im Autocommit-Modus ein ``TransactionManagementError``.
    """
    from core.models.client import Client

    if not connection.in_atomic_block:
        return
    facility_id = owner.facility_id
    if owner.__class__.__name__ == "Client":
        Client.objects.for_facility(facility_id).filter(pk=owner.pk).select_for_update().only("pk").first()
        return
    if getattr(owner, "client_id", None):
        _lock_current_owner_person(owner, facility_id)
    type(owner).objects.filter(pk=owner.pk, facility=facility_id).select_for_update().only("pk").first()


#: Obergrenze fuer das Nachlesen der Personenbindung in
#: :func:`_lock_current_owner_person`. Jede weitere Runde braucht eine
#: Zusammenfuehrung, die genau zwischen Lesen und Sperren abschliesst.
_PERSON_BINDING_ROUNDS = 3


def _lock_current_owner_person(owner, facility_id) -> None:
    """Sperrt die Person, an der die Owner-Zeile JETZT haengt, und zieht die Instanz nach.

    Die Owner-Instanz stammt aus einem Lesevorgang vor der Transaktion. Eine
    zwischenzeitlich abgeschlossene Zusammenfuehrung P -> Q setzt Fall und
    Ereignis per Queryset-Update auf Q um, die Instanz zeigt aber weiter auf
    P. Wer dann P sperrt, sperrt die abgegebene Person; Q fiele beim naechsten
    Schritt desselben Vorgangs (Art.-18-Gate im ``update_*``-Service,
    :func:`apply_attachment_changes`) hinter die Owner-Zeile — gegen die
    Ordnung ``core_client`` -> ``core_case``/``core_event`` (ADR-050 D5.5).

    Deshalb: Bindung frisch und einrichtungsgescopt lesen, diese Person
    sperren, Bindung erneut lesen. Laeuft eine Zusammenfuehrung gerade, haelt
    sie die Personenzeile bis zum Commit; die Sperre wartet, und erst die
    naechste Lesung sieht die neue Bindung. Stimmt sie mit der gesperrten
    Person ueberein, steht die Bindung — sie kann sich ohne diese
    Personenzeile nicht mehr aendern. Alle Personensperren fallen damit VOR
    die Owner-Zeile.

    Die Instanz bekommt die gesperrte Person, damit die folgenden Schritte
    dieselbe Zeile treffen. Ist die Owner-Zeile nicht (mehr) sichtbar, bleibt
    es bei der Person der Instanz; das Verschwinden meldet der spaetere
    Schreibschritt selbst.
    """
    from core.models.client import Client

    gesperrt = None
    for _runde in range(_PERSON_BINDING_ROUNDS):
        zeile = type(owner).objects.filter(pk=owner.pk, facility=facility_id).values_list("client_id").first()
        aktuell = owner.client_id if zeile is None else zeile[0]
        if aktuell == gesperrt:
            break
        if aktuell:
            Client.objects.for_facility(facility_id).filter(pk=aktuell).select_for_update().only("pk").first()
        gesperrt = aktuell
    if gesperrt and gesperrt != owner.client_id:
        owner.client_id = gesperrt


def open_chain_entry_ids(owner) -> list:
    """Die ``entry_id``-Werte aller noch offenen Ketten eines Owners.

    Eine Stelle fuer beide Seiten des Typwechsels: die Vorsperre des aeusseren
    Einstiegs und :func:`discard_type_switch_file_chains`, das genau diese
    Ketten soft-loescht. Auseinanderlaufen hiesse: eine verworfene Zeile ohne
    Vorsperre.

    ``Attachment.Meta.ordering`` sortiert nach ``-created_at``; ohne
    ``order_by()`` zoege PostgreSQL diese Spalte ins DISTINCT und die Ketten
    kaemen mehrfach zurueck (falsche Zaehlung).
    """
    return list(
        owner.attachments.filter(deleted_at__isnull=True).order_by().values_list("entry_id", flat=True).distinct()
    )


def lock_attachment_batch_before_audit(
    owner, user, request_post, staged_by_key, document_type, data_json=None, *, discards_existing_chains=False
) -> None:
    """Vorsperre fuer Aufrufer, die VOR dem Anhangs-Batch auditieren (Refs #2260).

    Die drei Owner-Update-Pfade (Ereignis, Person, Fall) schreiben ihre
    Audit-Zeile im ``update_*``-Service und lassen ``apply_attachment_changes``
    erst danach laufen — die Zeilensperren des Batches fielen damit hinter den
    Ketten-Advisory-Lock. Dieser Einstieg zieht genau die Sperrmenge des
    spaeteren Batches vor den ``update_*``-Aufruf; die Sperre in
    :func:`apply_attachment_changes` bleibt bestehen und findet die Zeilen dann
    bereits gehalten vor (sie traegt weiterhin die Aufrufer ohne diesen Weg).

    ``data_json`` gibt die kuenftige Markerlage vor — derselbe Wert, den der
    Aufrufer an :func:`stage_attachment_uploads` gibt, damit Sperrmenge und
    Schreibmenge deckungsgleich bleiben.

    **Typwechsel** (``discards_existing_chains=True``, auch beim Entfernen des
    Typs mit ``document_type is None``): Der Vorgang verwirft ueber
    :func:`discard_type_switch_file_chains` ALLE offenen Ketten des Owners und
    schreibt dafuer eine Bulk-Audit-Zeile, also den Ketten-Lock — noch VOR dem
    ``update_*``. Ab da liegt jede weitere Erstsperre hinter dem Ketten-Lock.
    Dieser aeussere Einstieg nimmt deshalb die Sperrmenge des GESAMTEN Vorgangs
    vorweg: die Owner-/Personenzeile UND die alte, zu verwerfende Anhangsmenge
    — unabhaengig davon, was der neue Typ mitbringt. Der Weg ueber einen neuen
    konkreten Typ ohne Upload hat einen leeren Batch und faende sonst gar keine
    Vorsperre.
    """
    entry_ids: set = set()
    creates_rows = False
    new_row_templates: list = []
    if document_type is not None:
        file_field_states = list(_iter_file_fields(owner, user, document_type, data_json=data_json))
        entry_ids = _batch_entry_ids(file_field_states, request_post, staged_by_key)
        creates_rows = _batch_legt_zeilen_an(file_field_states, staged_by_key)
        new_row_templates = _batch_new_row_templates(file_field_states, staged_by_key)
    if discards_existing_chains:
        # Die zu verwerfende Menge steht nicht im kuenftigen Typ — sie kommt
        # aus dem Bestand. Hat der Owner gar keine offene Kette, verwirft der
        # Discard nichts und schreibt auch keine Bulk-Audit-Zeile: dann nimmt
        # der ``update_*``-Schritt seine Owner-Zeile wie immer selbst vor
        # seinem eigenen Audit-Write, und hier ist nichts vorzuziehen.
        entry_ids = set(entry_ids) | set(open_chain_entry_ids(owner))
    # Reines Text-Update: keine Anhangsmutation, keine Sperre. Nur ein Batch,
    # der Zeilen anfasst ODER anlegt, braucht die Ordnung.
    lock_batch_attachment_rows(
        owner, entry_ids, creates_rows=creates_rows, user=user, field_templates=new_row_templates
    )


def _batch_entry_ids(file_field_states, request_post, staged_by_key) -> set:
    """Die ``entry_id``-Werte, die dieser Batch ersetzt oder loescht.

    Liest dieselben Quellen wie :func:`_apply_remove` und :func:`_apply_replace`
    — die hidden-CSV ``<slug>__remove`` und die Staging-Schluessel aus
    :func:`replace_upload_key` —, damit die Sperrmenge exakt der spaeteren
    Schreibmenge entspricht. Reine ADD-Schluessel bleiben draussen: sie fassen
    keine Bestandszeile an.
    """
    ids: set = set()
    for slug, _ft, current_entries, attachments_by_pk in file_field_states:
        remove_raw = request_post.get(f"{slug}__remove", "")
        remove_ids = {x.strip() for x in remove_raw.split(",") if x.strip()}
        for entry in current_entries:
            att = attachments_by_pk.get(str(entry["id"]))
            if att is None:
                continue
            if str(att.entry_id) in remove_ids or replace_upload_key(slug, att.entry_id) in staged_by_key:
                ids.add(att.entry_id)
    return ids


def _batch_legt_zeilen_an(file_field_states, staged_by_key) -> bool:
    """Legt dieser Batch eine NEUE Anhangszeile an (reiner ADD)?

    Ein ADD fasst keine Bestandszeile an und taucht deshalb nicht in
    :func:`_batch_entry_ids` auf — leer ist er trotzdem nicht: die neue Zeile
    traegt den Owner-Fremdschluessel, und dessen (aufgeschobene) Pruefung nimmt
    eine Sperre auf der Owner-/Personenzeile. Ohne Stufe 1 fiele die hinter den
    Ketten-Lock des ``update_*`` (Refs #2260).

    Die Schluessel eines Zugangs sind die reinen Feld-Slugs; Ersetzen legt
    seine Schluessel unter :func:`replace_upload_key` ab.
    """
    return any(slug in staged_by_key for slug, *_rest in file_field_states)


def _batch_new_row_templates(file_field_states, staged_by_key) -> list:
    """Die Feldvorlagen der Felder, die in diesem Batch eine NEUE Zeile bekommen.

    Zugang (reiner Slug) wie Ersetzen (:func:`replace_upload_key`) legen eine
    Zeile an — beide tragen die Feldvorlage als Fremdschluessel (Refs #2337).
    """
    templates = []
    for slug, field_template, _entries, attachments_by_pk in file_field_states:
        replaces = any(replace_upload_key(slug, att.entry_id) in staged_by_key for att in attachments_by_pk.values())
        if slug in staged_by_key or replaces:
            templates.append(field_template)
    return templates


def apply_attachment_changes(owner, user, request_post, request_files, file_fields, document_type, staged_by_key=None):
    """Applizieren von REMOVE/REPLACE/ADD auf die FILE-Felder eines Owners.

    - **REMOVE**: hidden CSV ``<slug>__remove`` listet zu loeschende
      ``entry_id``-Werte -> ``soft_delete_attachment_chain``.
    - **REPLACE**: die unter :func:`replace_upload_key` gestagte Datei ersetzt
      den bestehenden Entry (``commit_staged_upload(supersedes=...)``).
    - **ADD**: alle unter dem Slug gestagten Dateien werden mit frischen
      ``entry_id`` angehaengt.

    Aktualisiert anschliessend den Marker im ``owner.data_json``. ``owner`` ist
    ein ``Event``, ``Client`` oder ``Case`` (Refs #1738).

    Sperr-Reihenfolge (Refs #2178): Haelt der Aufrufer eine eigene Transaktion,
    sperrt :func:`lock_batch_attachment_rows` vor dem ersten Schreibvorgang alle
    Ketten, die dieser Vorgang ersetzt oder loescht — erst danach laufen
    REMOVE/REPLACE/ADD und damit der erste Advisory-Lock der Einrichtung. **Ohne
    aeussere Transaktion entfaellt die Vorsperre**: dort schuetzt
    jede Storage-Operation ihre Mutation in ihrer eigenen kurzen Transaktion,
    niemand haelt einen Lock ueber mehrere Operationen, und
    ``select_for_update`` waere im Autocommit-Modus ein Abbruch.

    ``staged_by_key`` (Refs #1956) ist das Ergebnis von
    :func:`stage_attachment_uploads`; gestagte Dateien, die hier keine DB-Zeile
    bekommen, werden am Ende von der Disk entfernt. **Jeder Aufrufer mit eigener Transaktion
    muss es mitgeben** — dann faellt hier nur noch der schnelle DB-Write an und
    der AuditLog-Advisory-Lock der Einrichtung steht nicht ueber dem ClamAV-
    Scan. ``None`` ist der Bequemlichkeits-Modus fuer transaktionslose
    Service-/Test-Aufrufer: dann staged diese Funktion selbst (inklusive
    Cleanup bei Fehlern) und ``file_fields`` bleibt die ADD-Quelle. Mit
    ``staged_by_key`` wird ``file_fields`` nicht mehr gelesen.
    """
    own_staging = staged_by_key is None
    staged_all: list = []
    if own_staging:
        staged_by_key, staged_all = stage_attachment_uploads(owner, user, request_files, file_fields, document_type)

    # Arbeitskopie: die Schritte unten ``pop``en, was sie verbrauchen. Der
    # Aufrufer soll seine Staging-Liste unveraendert behalten (sein ``finally``
    # raeumt bei ausbleibendem Commit alles weg).
    staged_pending = dict(staged_by_key)
    try:
        # Einmal materialisieren: dieselbe Feldliste traegt die Sperrmenge und
        # die Anwendung. Jedes Feld liest und schreibt nur seinen eigenen Slug,
        # die Vorab-Materialisierung aendert daran nichts.
        file_field_states = list(_iter_file_fields(owner, user, document_type))
        # Refs #2178/#2260: Batch-Vertrag — die Owner-/Personenzeile und ALLE
        # Zeilen, die dieser Vorgang ersetzt oder loescht, VOR dem ersten
        # Schreibvorgang sperren. Sonst faellt die Zeilensperre des zweiten
        # Commits hinter den Advisory-Lock des ersten, und die Owner-Zeile
        # faellt (als aufgeschobene Fremdschluesselpruefung oder als
        # ``owner.save`` am Ende) noch weiter dahinter. Dieser Einstieg nimmt
        # die Stufen selbst und bedingungslos — ein Aufrufer, der sie bereits
        # genommen hat, wird hier nur bestaetigt und nicht aufgehalten.
        lock_batch_attachment_rows(
            owner,
            _batch_entry_ids(file_field_states, request_post, staged_pending),
            creates_rows=_batch_legt_zeilen_an(file_field_states, staged_pending),
            user=user,
            field_templates=_batch_new_row_templates(file_field_states, staged_pending),
        )
        for slug, ft, current_entries, attachments_by_pk in file_field_states:
            current_entries = _apply_remove(slug, current_entries, attachments_by_pk, request_post, owner, user)
            updated_entries = _apply_replace(slug, current_entries, attachments_by_pk, staged_pending, ft, owner, user)
            updated_entries = _apply_add(slug, updated_entries, staged_pending, ft, owner, user)

            _write_file_marker(owner, slug, updated_entries)

        owner.save(update_fields=["data_json"])

        # Refs #1956: Zwischen Staging und Anwendung kann die Markerlage
        # abweichen (nebenlaeufige Aenderung, Feldzuordnung entfernt). Eine
        # gestagte Datei ohne DB-Zeile waere dann eine verschluesselte Waise
        # auf der Disk, die kein Loeschpfad je erfasst — sofort entfernen
        # statt auf den naechtlichen Orphan-Cleanup zu warten (#662).
        for leftover in staged_pending.values():
            for staged in leftover:
                staged.output_path.unlink(missing_ok=True)
    except Exception:
        # Nur was diese Funktion selbst gestagt hat, raeumt sie auch selbst weg
        # (#662). Beim Aufrufer-Staging haengt das Aufraeumen an dessen
        # ``finally`` — nur der weiss, ob seine Transaktion committet hat.
        if own_staging:
            for staged in staged_all:
                staged.output_path.unlink(missing_ok=True)
        raise


def _drop_purged_marker_entries(owner, purged_pks):
    """Entfernt die Marker-Eintraege der geloeschten Zeilen aus ``owner.data_json``.

    Invariante I-M1 (ADR-039 §7): ein **physischer** Loeschpfad MUSS im selben
    ``transaction.atomic`` die ``__files__``-Marker bereinigen — sonst zeigt der
    Marker auf eine Zeile, die es nicht mehr gibt, und der Owner rendert ein
    Anhang-Feld mit unaufloesbarem Eintrag.

    Bewusst ueber **alle** Marker-Slugs des ``data_json`` statt ueber die
    FILE-Felder des aktuellen Dokumentationstyps: nach einem Typwechsel kann ein
    Marker zu einem Slug gehoeren, den der heutige Typ nicht mehr kennt
    (:func:`discard_type_switch_file_chains` laesst genau solche Ketten
    zurueck). Der Aufloesungsweg des Feldsets wuerde den Marker dann nicht
    finden und stehen lassen.
    """
    data_json = owner.data_json or {}
    changed = False
    for slug in list(data_json):
        marker = data_json.get(slug)
        if not is_file_marker(marker):
            continue
        current = normalize_file_marker(marker)
        entries = [entry for entry in current if entry["id"] not in purged_pks]
        if len(entries) == len(current):
            continue
        changed = True
        if entries:
            data_json[slug] = {"__files__": True, "entries": entries}
        else:
            del data_json[slug]
    if changed:
        owner.data_json = data_json
        owner.save(update_fields=["data_json"])
    return changed


def _lock_owner_row(owner):
    """Sperrt die Zeile des Anhang-Owners — zweite Stufe der kanonischen Ordnung.

    Refs #2123 (Befund B-1): ``assert_processing_allowed(...,
    lock=True)`` sperrt ausschliesslich ``core_client``. Die **direkte**
    Ereignis-Hold-Anlage (``_lock_event_and_check_redaction``) sperrt aber nur
    ``core_event`` und fasst die Personenzeile nie an — zwischen Purge und
    Ereignis-Hold gab es also gar kein gemeinsames Sperrobjekt. Ein Hold, der
    zwischen der Hold-Lesung und dem Commit des Purge entstand, kam zu spaet,
    und der ``on_commit``-Unlink vernichtete die Datei trotz aktiver Anordnung.
    Fuer ein anonymes Ereignis (``client_id is None``) kehrt ``_restricted_flag``
    sogar VOR jeder Sperre zurueck: dann gab es ueberhaupt keinen Anker.

    Reihenfolge: ``core_client`` (schon gehalten) -> ``core_event`` bzw.
    ``core_case`` — genau die kanonische Ordnung aus ADR-050 D5.5. Ein
    Inversions-Risiko entsteht nicht: die Ereignis-Hold-Anlage nimmt inzwischen
    dieselbe Richtung (``core_client`` -> ``core_event``), die
    Fall-Hold-Anlage ebenso (``core_client`` -> ``core_case`` -> ``core_event``).

    Fuer einen **Personen**-Owner ist nichts zu tun: seine Zeile haelt das
    Art.-18-Gate bereits. Dass ``owner_has_active_hold`` dort zusaetzlich jeden
    Ereignis-Hold der Person mitzaehlt, deckt seit Refs #2144/#2123 auch
    die Hold-Anlage selbst ab: ``_lock_event_and_check_redaction``
    sperrt jetzt ebenfalls zuerst die Personenzeile. Dieser Helfer muss deshalb
    keine fremden Ereigniszeilen nachsperren — das waere eine unbegrenzte
    Sperrmenge.
    """
    if owner.__class__.__name__ == "Client":
        return
    type(owner).objects.filter(pk=owner.pk, facility=owner.facility_id).select_for_update().only("pk").first()


def purge_attachment_chain(owner, entry_id):
    """Loesche eine Versionskette **ersatzlos**: Zeilen, Dateien und Marker (Refs #1874).

    Abgrenzung zum Bestand: Das UI-Entfernen an den Owner-Formularen
    (``<slug>__remove`` -> :func:`_apply_remove` ->
    ``soft_delete_attachment_chain``) ist seit #622 ein **Soft**-Delete — es
    setzt ``deleted_at`` und laesst die verschluesselte ``.enc`` liegen; der
    naechtliche Orphan-Sweep verschont sie ausdruecklich, weil die Zeile noch
    existiert. Eine irrtuemlich hochgeladene Datei (falsche Person, Klarname im
    Inhalt) blieb damit bis zur Anonymisierung der ganzen Person im Vault.
    Diese Funktion ist der Gegenpart: sie erfasst die **ganze** Kette, auch
    bereits soft-geloeschte und ersetzte Glieder.

    **Legal Hold ist ein harter Blocker** (:func:`owner_has_active_hold`). Der
    Soft-Delete-Pfad kommt ohne diese Pruefung aus, weil er nichts vernichtet;
    hier waere sie das Loeschen von Beweismitteln.

    **Reihenfolge — erst DB, dann Disk.** Ein Prozessabbruch zwischen beiden
    Schritten hinterlaesst so eine referenzlose ``.enc``, die
    :func:`~core.services.file_vault.cleanup.cleanup_orphan_storage_files`
    einsammelt. Umgekehrt (wie in :func:`delete_attachments_for`, dessen
    Aufrufer den Owner ohnehin komplett abraeumt) bliebe eine DB-Zeile ohne
    Datei zurueck — die raeumt kein Sweep je weg, und der Download antwortet
    dauerhaft mit 404. Beide Schritte gehoeren in **eine** Transaktion des
    Aufrufers; ein Rollback nach dem Unlink waere in beiden Reihenfolgen
    derselbe Verlust.

    :param owner: Ereignis, Person oder Fall — der Zugriff laeuft ueber den
        geteilten Reverse-Accessor ``owner.attachments`` und ist damit
        owner-gescopt.
    :param entry_id: Kennung der Versionskette (nicht die Attachment-PK).
    **Art.-18-Sperrvermerk** (Refs #2045) blockiert genauso — seit dem
    Re-Review IM Service statt ungesperrt in der View davor. Bewusst derselbe
    Fehlertyp wie beim Legal Hold: die View hat dafuer laengst einen Zweig, der
    die Meldung ausgibt und die Bestaetigungsseite verlaesst; ein zweiter
    Fehlerpfad haette dasselbe Ergebnis mit mehr Code erzeugt. Fachlich sind es
    zwei verschiedene Gruende — der Hold schuetzt Beweismittel, die
    Einschraenkung untersagt das Ausduennen einer bestrittenen Akte — deshalb
    zwei getrennte Meldungen.

    :raises AttachmentPurgeBlockedError: bei aktivem Legal Hold oder gesetztem
        Art.-18-Sperrvermerk.
    :return: Zahl der physisch entfernten Versionen (0 = Kette existiert nicht).
    """
    from core.retention.legal_holds import owner_has_active_hold
    from core.services.client.restriction import assert_processing_allowed
    from core.services.file_vault import delete_attachment_file

    chain = list(owner.attachments.filter(entry_id=entry_id))
    if not chain:
        return 0
    owner_client_id = owner.pk if owner.__class__.__name__ == "Client" else getattr(owner, "client_id", None)

    # Art.-18-Gate unter der Client-ZEILENSPERRE, in derselben Transaktion wie
    # die Loeschung (Re-Review 2): das ungesperrte Anzeigepraedikat liess ein
    # TOCTOU-Fenster zwischen Pruefung und ``delete()``. ``assert_processing_allowed``
    # ist die eine Autoritaet; ihr ``ValidationError`` wird hier in den
    # etablierten Blocked-Fehlertyp uebersetzt, damit die View ihren
    # bestehenden Zweig behaelt (Muster Legal Hold).
    #
    # Die eigene ``atomic``-Klammer macht den DIREKTEN Service-Aufruf genauso
    # sicher wie den View-Weg: ``select_for_update`` braucht eine Transaktion,
    # und der Lock muss bis nach dem ``delete()`` halten. Ruft die View schon
    # in einem ``atomic``, ist dies ein Savepoint — kein zweiter Commit.
    with transaction.atomic():
        try:
            assert_processing_allowed(owner_client_id, lock=True)
        except ValidationError as exc:
            raise AttachmentPurgeBlockedError(
                _(
                    "Für diese Person ist die Verarbeitung nach Art. 18 DSGVO eingeschränkt — "
                    "die Datei darf nicht einzeln vernichtet werden."
                )
            ) from exc

        # Refs #2123: Die Hold-Pruefung lag frueher VOR dieser ``atomic``-Klammer
        # und damit vor jeder Sperre — ein zwischen Pruefung und ``delete()``
        # angeordneter Hold kam zu spaet, die Datei war physisch weg (nicht
        # rollback-bar). Sie liegt jetzt HINTER beiden Sperren:
        # ``assert_processing_allowed(..., lock=True)`` sperrt ueber
        # ``_restricted_flag`` die ``core_client``-Zeile — den Anker der
        # Personen- und (als erste Stufe) der Fall-Hold-Anlage —, und
        # :func:`_lock_owner_row` die Zeile des Owners selbst. Die zweite Stufe
        # ist noetig, weil ein Hold auf den Owner SELBST (Ereignis, Fall) sonst
        # kein gemeinsames Sperrobjekt mit dem Purge haette (Befund B-1).
        #
        # „Kein Hold" ist an dieser Stelle stabil, weil JEDE Hold-Anlage
        # (Ereignis, Person, Fall, Aktivitaet) die Personenzeile als erstes
        # Glied sperrt — seit Refs #2144/#2123 auch die direkte
        # Ereignis-Hold-Anlage. Das ist der Grund, warum die transitive Menge
        # zaehlt, die ``owner_has_active_hold`` bei Personen- und Fall-Ownern
        # zusaetzlich prueft (Ereignis- und Fall-Holds der Person): sie alle
        # warten auf dieselbe ``core_client``-Zeile, die dieser Pfad haelt.
        #
        # Die Reihenfolge der beiden Gates ist umgekehrt zu vorher; fachlich
        # sind sie unabhaengig, und beide antworten mit demselben Fehlertyp an
        # dieselbe View-Flaeche.
        _lock_owner_row(owner)

        if owner_has_active_hold(owner):
            raise AttachmentPurgeBlockedError(
                _("Für diesen Datensatz besteht ein aktiver Legal Hold — die Datei darf nicht vernichtet werden.")
            )

        # Refs #2260: die vollstaendige Schreibmenge dieses
        # Loeschens — die Kette selbst UND jede Zeile, deren ``superseded_by``
        # in sie zeigt — in EINER nach ``pk`` geordneten Abfrage sperren, bevor
        # das ``delete()`` seine ``SET_NULL``-Kaskade faehrt. Der View-Weg hat
        # diese Zeilen ueber dieselbe Funktion bereits vor seinem Audit-Write
        # gesperrt und findet sie hier gehalten vor; der DIREKTE Service-Aufruf
        # bekommt die Ordnung so ebenfalls. Stufe 1 liegt bereits hinter uns
        # (``assert_processing_allowed(lock=True)`` und
        # :func:`_lock_owner_row`), deshalb ``lock_owner_rows=False``.
        lock_attachment_rows_in_facility_order(owner, [entry_id], deletes_rows=True)

        _drop_purged_marker_entries(owner, {str(attachment.pk) for attachment in chain})
        owner.attachments.filter(entry_id=entry_id).delete()

        # Der ``.enc``-Unlink laeuft NACH dem aeussersten Commit
        # (Cross-Review-Runde 4). Vorher lag er in der Transaktion und war
        # damit nicht rollback-bar: rollte eine aeussere Transaktion zurueck
        # oder scheiterte der Commit, kehrten Marker und Attachment-Zeilen
        # zurueck — die Dateien nicht. Die Zeile zeigte dann dauerhaft ins
        # Leere und der Download antwortete 404.
        #
        # ``on_commit`` dreht die verbleibende Fehlerrichtung in die
        # harmlosere: Bricht der Prozess zwischen Commit und Unlink ab, bleibt
        # eine referenzlose ``.enc`` liegen — und die raeumt
        # :func:`~core.services.file_vault.cleanup.cleanup_orphan_storage_files`
        # ab. Eine Zeile ohne Datei raeumt dagegen kein Sweep je weg.
        #
        # Erste Verwendung von ``on_commit`` in diesem Projekt: der Bestand
        # (``delete_attachments_for``) unlinkt weiterhin innerhalb der
        # Transaktion. Das bleibt bewusst so — jener Helfer laeuft nur in
        # Owner-Loesch-/Anonymisierungskaskaden, in denen der ganze Owner
        # ohnehin verschwindet; ihn umzustellen waere ein eigener Eingriff in
        # fremde Pfade. Der Purge ist der einzige Weg, der eine EINZELNE Datei
        # aus einem sonst unveraenderten Datensatz nimmt — hier zaehlt die
        # Rollback-Konsistenz.
        transaction.on_commit(lambda rows=tuple(chain): [delete_attachment_file(row) for row in rows])
    return len(chain)


def preserve_file_markers(owner, merged_data, previous_data, file_field_slugs):
    """Bestehende FILE-Marker in eine frisch gemergte ``data_json`` zurueckholen.

    Owner-generisches Pendant zum FILE-Zweig von
    :func:`core.services.events.crud.merge_update_payload` (Refs #1160).

    Warum das noetig ist: der Update-Pfad baut ``data_json`` komplett aus dem
    **Formular** des aktuellen Typs neu auf (E11-Merge-Policy,
    :func:`~core.services.dynamic_data.merge_dynamic_data`). Ein FILE-Feld
    traegt aber gar keinen Formularwert — sein Wert IST der Marker auf die
    ``Attachment``-Zeilen. Ohne diese Re-Injektion wuerde bereits der erste
    reine Text-Edit still **alle** Bestandsanhaenge des Owners aus dem
    ``data_json`` werfen (die Zeilen blieben als UI-unsichtbare Waisen liegen).
    :func:`apply_attachment_changes` passt den Marker danach an.

    Mutiert ``merged_data`` in-place und gibt es zurueck (Signatur-Muster wie
    ``merge_update_payload``).

    :param owner: Ereignis/Person/Fall — dokumentiert die Zugehoerigkeit; der
        Helfer selbst liest den Owner nicht (die Marker liegen in
        ``previous_data``). Bewusst im Signatur-Kopf, damit Aufrufstellen nicht
        versehentlich die ``data_json`` einer anderen Entitaet mergen.
    :param merged_data: Ergebnis des Form-Merges (wird ergaenzt).
    :param previous_data: bisheriges ``data_json`` des Owners.
    :param file_field_slugs: Slugs der FILE-Felder des **aktuellen** Typs. Nur
        sie werden nachgezogen — bei einem Typwechsel gehoert ein Alt-Slug nicht
        mehr zum Feldset und darf nicht wieder auftauchen.
    """
    previous = previous_data or {}
    for slug in file_field_slugs:
        marker = previous.get(slug)
        # Dieselbe Marker-Erkennung wie ``validate_data_json``: was dort
        # durchfaellt, wuerde ohnehin verworfen — kein Grund, es einzusetzen.
        if is_file_marker(marker):
            merged_data[slug] = marker
    return merged_data


def discard_type_switch_file_chains(owner, user):
    """Alle Versionsketten eines Owners soft-loeschen (Typwechsel, ADR-039 §7).

    Wechselt der Dokumentationstyp einer Person/eines Falls, verwirft die
    E11-Merge-Policy das komplette ``data_json`` des alten Typs — samt der
    FILE-Marker. Die ``Attachment``-Zeilen blieben ohne diesen Aufruf als
    UI-unsichtbare Waisen zurueck (weder in der Datei-Uebersicht noch in der
    Auskunft auffindbar, aber weiterhin auf Disk).

    Bewusst **soft-delete** (``deleted_at``), nicht physisch: die Ketten sollen
    fuer Auskunft/Forensik nachvollziehbar bleiben und faellig erst mit dem
    Owner-Lebenszyklus (Klienten-Anonymisierung). Damit ist dieser Pfad **kein**
    physischer Loeschpfad — Invariante I-M1 (Marker-Bereinigung im selben
    ``atomic``) ist nicht beruehrt; der Typwechsel verwirft das ``data_json``
    ohnehin.

    Idempotent: bereits soft-geloeschte Ketten werden nicht erneut angefasst.

    Returns: Anzahl der betroffenen **Ketten** (nicht der Einzelversionen) —
    genau die Zahl, die der Bestaetigungsdialog nennt (Refs #1733).
    """
    from core.services.file_vault import soft_delete_attachment_chain
    from core.services.file_vault.audit import audit_attachment_bulk_delete

    with transaction.atomic():
        entry_ids = open_chain_entry_ids(owner)
        # Refs #2260: einrichtungsweite Sperr-Reihenfolge. Ohne diese eine
        # vorgezogene Abfrage legte die Reihenfolge der ``entry_ids`` fest, in
        # welcher Folge die Schleife die Zeilen sperrt — zwei gleichzeitige
        # Typwechsel desselben Owners koennten sich darueber verschraenken.
        # Gebuendelt und nach ``pk`` geordnet liegt die ganze Menge vor dem
        # Audit-Write unten; Stufe 1 gehoert davor, weil der Vorgang (und der
        # aufrufende ``update_*``-Schritt) die Owner-Zeile schreibt.
        lock_batch_attachment_rows(owner, entry_ids)
        for entry_id in entry_ids:
            soft_delete_attachment_chain(owner, entry_id, user, audit=False)
        if entry_ids:
            audit_attachment_bulk_delete(
                owner,
                user,
                chains=len(entry_ids),
                reason="document_type_switch",
            )
        return len(entry_ids)
