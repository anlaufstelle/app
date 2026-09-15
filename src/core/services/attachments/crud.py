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
from django.utils.translation import gettext_lazy as _

from core.models import FieldTemplate
from core.services.dynamic_data import build_field_template_lookup, is_file_marker, normalize_file_marker


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
    # Refs #2129 (Fix-Runde 1, R-L7a-5): laufende Summe der bereits gestagten
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
    # Refs #2129 (Fix-Runde 1, R-L7a-5): eine laufende Summe fuer den GANZEN
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
    geloeschten Entries bereinigte Entry-Liste."""
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


def lock_batch_attachment_rows(owner, entry_ids) -> None:
    """Sperrt ALLE Bestandszeilen einer Batch-Menge in EINER Abfrage (#2178).

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

    **Nur mit offener Aufrufer-Transaktion** (Refs #2178, Fix-Runde 3): Ohne
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
    """
    if not connection.in_atomic_block:
        return
    ids = sorted({str(entry_id) for entry_id in entry_ids if entry_id})
    if not ids:
        return
    list(owner.attachments.select_for_update().filter(entry_id__in=ids).order_by("pk").values_list("pk", flat=True))


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
    aeussere Transaktion entfaellt die Vorsperre** (Fix-Runde 3): dort schuetzt
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
        # Refs #2178 (Fix-Runde 2): Batch-Vertrag — ALLE Zeilen, die dieser
        # Vorgang ersetzt oder loescht, VOR dem ersten Commit sperren. Sonst
        # faellt die Zeilensperre des zweiten Commits hinter den Advisory-Lock
        # des ersten (siehe ``lock_batch_attachment_rows``).
        lock_batch_attachment_rows(owner, _batch_entry_ids(file_field_states, request_post, staged_pending))
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

    Refs #2123 (kimi-k3 B-1, Fix-Runde 1): ``assert_processing_allowed(...,
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
    Inversions-Risiko entsteht nicht: die Ereignis-Hold-Anlage nimmt seit
    Fix-Runde 2 dieselbe Richtung (``core_client`` -> ``core_event``), die
    Fall-Hold-Anlage ebenso (``core_client`` -> ``core_case`` -> ``core_event``).

    Fuer einen **Personen**-Owner ist nichts zu tun: seine Zeile haelt das
    Art.-18-Gate bereits. Dass ``owner_has_active_hold`` dort zusaetzlich jeden
    Ereignis-Hold der Person mitzaehlt, deckt seit Refs #2144/#2123
    (Fix-Runde 2) die Hold-Anlage selbst ab: ``_lock_event_and_check_redaction``
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
        # kein gemeinsames Sperrobjekt mit dem Purge haette (kimi-k3 B-1,
        # Fix-Runde 1).
        #
        # „Kein Hold" ist an dieser Stelle stabil, weil JEDE Hold-Anlage
        # (Ereignis, Person, Fall, Aktivitaet) die Personenzeile als erstes
        # Glied sperrt — seit Refs #2144/#2123 (Fix-Runde 2) auch die direkte
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
        entry_ids = list(
            owner.attachments.filter(deleted_at__isnull=True)
            # ``Attachment.Meta.ordering`` sortiert nach ``-created_at``; ohne
            # ``order_by()`` zoege PostgreSQL diese Spalte ins DISTINCT und die
            # Ketten kaemen mehrfach zurueck (falsche Zaehlung).
            .order_by()
            .values_list("entry_id", flat=True)
            .distinct()
        )
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
