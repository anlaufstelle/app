"""Verkettete HMAC-Integritaet fuer den AuditLog (Refs #1070).

Macht den append-only ``AuditLog`` *tamper-evident*: jede Zeile traegt einen
``entry_hash = HMAC(key, prev_hash || canonical(row))``, wobei ``prev_hash``
der ``entry_hash`` der vorherigen Zeile derselben Facility-Kette ist. Wer eine
mittlere Zeile lautlos veraendert oder entfernt — direkt in der DB, am
``save()``-/``delete()``-Guard und am Immutability-Trigger vorbei
(``session_replication_role = replica`` / Owner-Zugriff) — bricht damit den
Hash bzw. die Verkettung, ohne den geheimen ``AUDIT_HASH_KEY`` zu kennen.

Geltungsbereich der Kette ist die **Facility** (Zeilen mit ``facility=NULL``
bilden eine gemeinsame „System"-Kette). Beim Insert serialisiert ein
Postgres-Advisory-Xact-Lock pro Facility die Schreiber, sodass ``prev_hash``
luecken- und kollisionsfrei vergeben wird.

Schluessel: wiederverwendet ``_get_audit_hash_key()`` aus :mod:`.hash`
(separat von ``SECRET_KEY``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from dataclasses import dataclass
from datetime import UTC, timedelta

from django.db import connection

from core.services.audit.hash import _get_audit_hash_key

# R15 (Refs #1476/#1665): Reentrancy-Guard fuer das kurze Fenster, in dem der
# System-Ketten-Read die RLS-Sicht transient auf ``app.is_super_admin='true'``
# anhebt. Das angehobene Fenster umschliesst bewusst NUR einen einzigen
# Raw-Cursor-SELECT (kein ORM). Der Guard macht diese Invariante fail-loud
# durchsetzbar: schoebe ein kuenftiges Refactor je eine weitere (ORM-)Query in
# das Fenster, die selbst wieder einen System-Ketten-Read ausloeste, wuerde die
# geschachtelte Elevation hier hart abbrechen — statt lautlos volle Zeilen
# unter Super-Admin-Sicht zu lesen.
_system_read_active = threading.local()


def _canonical_timestamp(ts) -> str:
    """UTC-normalisierter ISO-8601-Zeitstempel fuer den Kettenhash.

    Migration 0097 stellte ``timestamp`` von ``auto_now_add`` auf
    ``default=timezone.now`` um — ein Aufrufer kann also einen naiven oder
    nicht-UTC-aware Wert uebergeben. Beim Schreiben lebt dieser In-Memory-Wert in
    seiner Original-Zone, beim Verify liefert Postgres ihn UTC-aware zurueck.
    Ohne Vereinheitlichung haetten beide unterschiedliche ``isoformat()``-Strings
    und damit verschiedene Hashes — ein falscher „Tamper". Wir normalisieren
    daher konsistent auf UTC (naiv → als UTC interpretiert). Refs #1070.
    """
    if not ts:
        return ""
    if ts.utcoffset() is None:  # naiv → als UTC interpretieren
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def _canonical_payload(row) -> str:
    """Stabile, sortierte JSON-Serialisierung der semantischen Zeilenfelder.

    ``default=str`` serialisiert UUIDs/Datetimes; ``sort_keys`` + kompakte
    Separatoren machen die Ausgabe deterministisch und unabhaengig von der
    Dict-Einfuegereihenfolge. Identisch beim Schreiben (In-Memory-Instanz) und
    beim Verify (aus der DB geladene Zeile).
    """
    payload = {
        "timestamp": _canonical_timestamp(row.timestamp),
        "action": str(row.action),
        "user_id": row.user_id,
        "facility_id": row.facility_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        # ``GenericIPAddressField`` hat ``empty_strings_allowed=False`` →
        # die DB persistiert "" als NULL. Auf NULL normalisieren, damit der
        # Write-Hash (In-Memory "") und der Verify-Hash (DB NULL) gleich sind.
        "ip_address": row.ip_address or None,
        "detail": row.detail,
    }
    # Refs #1369: der handelnde Admin (``actor``) geht nur dann in den
    # Kettenhash ein, wenn er gesetzt ist. Bestandszeilen (vor Migration 0101)
    # und system-getriebene Eintraege haben ``actor_id IS NULL`` → der Schluessel
    # bleibt WEG und ihr Canonical-Payload (und damit ihr gespeicherter
    # ``entry_hash``) ist bit-identisch zu vor der Migration; die bestehende
    # Kette bleibt verifizierbar. Neu attribuierte Eintraege binden den Actor
    # dagegen tamper-evident in die Kette ein. ``getattr`` haelt die Funktion
    # robust gegen In-Memory-Rows ohne geladenes ``actor``-Attribut.
    actor_id = getattr(row, "actor_id", None)
    if actor_id is not None:
        payload["actor_id"] = actor_id
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def compute_entry_hash(row, prev_hash: str) -> str:
    """``HMAC_SHA256(key, prev_hash || canonical(row))`` als Hex-Digest."""
    message = ((prev_hash or "") + _canonical_payload(row)).encode("utf-8")
    return hmac.new(_get_audit_hash_key(), message, hashlib.sha256).hexdigest()


def _system_chain_latest_link_raw():
    """``(timestamp, entry_hash)`` der juengsten System-Ketten-Zeile
    (``facility=NULL``) via **einzelnem Raw-Cursor-SELECT** unter transient
    angehobener RLS-Sicht (R15, Refs #1476/#1665).

    Only hashed rows count as the chain head (Refs #2449): a row without
    ``entry_hash`` must never become the predecessor of a new entry.

    NULL-facility-Zeilen sind unter der RLS-USING-Policy nur fuer
    ``app.is_super_admin='true'`` sichtbar (Migration 0085). Ein nicht-super
    Schreiber unter der NOBYPASSRLS-App-Rolle (z.B. LOGIN_FAILED fuer einen
    unbekannten User) saehe den Ketten-Tail sonst NICHT und wuerde faelschlich
    ``prev_hash=""`` setzen — die System-Kette risse.

    Die GUC wird transient (``SET LOCAL`` / ``is_local=true``, Transaktions-
    scoped) angehoben und im ``finally`` exakt auf den Vorwert zurueckgesetzt
    (kein Leak auf gepoolter Connection). **Das angehobene Fenster umschliesst
    ausschliesslich den einen ``SELECT``** — bewusst KEINE ORM-Query (die die
    volle Instanz unter Super-Admin-Sicht materialisieren wuerde); gelesen
    werden nur Hash-/Ordnungs-Metadaten der eigenen Kette, keine PII. Ein
    Reentrancy-Guard bricht eine geschachtelte Elevation fail-loud ab.
    """
    from core.models import AuditLog

    if getattr(_system_read_active, "flag", False):
        raise RuntimeError(
            "geschachtelte System-Ketten-Elevation (R15): das angehobene "
            "app.is_super_admin-Fenster darf ausschliesslich den einen "
            "Raw-Cursor-Read umschliessen — keine weitere (ORM-)Query."
        )

    table = connection.ops.quote_name(AuditLog._meta.db_table)
    ts_col = connection.ops.quote_name(AuditLog._meta.get_field("timestamp").column)
    hash_col = connection.ops.quote_name(AuditLog._meta.get_field("entry_hash").column)
    id_col = connection.ops.quote_name(AuditLog._meta.get_field("id").column)
    fac_col = connection.ops.quote_name(AuditLog._meta.get_field("facility").column)

    _system_read_active.flag = True
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.is_super_admin', true)")
            previous = cur.fetchone()[0] or ""
            cur.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            try:
                # Alle Identifier stammen aus ``_meta`` + ``quote_name`` (konstant,
                # kein User-Input); die WHERE-Bedingung ist literal.
                sql = (
                    f"SELECT {ts_col}, {hash_col} FROM {table} "  # noqa: S608 — konstante, gequotete Identifier
                    f"WHERE {fac_col} IS NULL AND {hash_col} <> '' "
                    f"ORDER BY {ts_col} DESC, {id_col} DESC LIMIT 1"
                )
                cur.execute(sql)
                return cur.fetchone()
            finally:
                cur.execute("SELECT set_config('app.is_super_admin', %s, true)", [previous])
    finally:
        _system_read_active.flag = False


def _latest_link(facility_id):
    """``(timestamp, entry_hash)`` of the newest *hashed* row of the chain.

    Rows without ``entry_hash`` are ignored (Refs #2449). Otherwise an unhashed
    row at the chain end -- the database refuses to INSERT one, so it can only
    come from before the chain existed or from an owner-level write -- would
    become the predecessor, and the next legitimate entry would restart the
    chain behind it with ``prev_hash=""``.
    """
    from django.db.models import Q

    from core.models import AuditLog

    # System-Kette (facility=NULL) auf PostgreSQL: Raw-Read unter eng
    # gescopeter RLS-Elevation (R15). Facility-Ketten sind fuer den eigenen
    # Schreiber ohnehin RLS-sichtbar; SQLite-Tests kennen keine RLS/GUC.
    if facility_id is None and connection.vendor == "postgresql":
        return _system_chain_latest_link_raw()

    return (
        AuditLog.objects.filter(Q(facility_id=facility_id), ~Q(entry_hash=""), entry_hash__isnull=False)
        .order_by("-timestamp", "-id")
        .values_list("timestamp", "entry_hash")
        .first()
    )


def advisory_lock_name(facility_id) -> str:
    """Der Ressourcen-NAME, aus dem der Advisory-Lock-Key abgeleitet wird.

    Einzige Quelle dieser Zeichenkette — :func:`assign_chain_fields` und jeder
    Aufrufer, der die Lock-Reihenfolge planen muss, gehen hier durch. Eine
    zweite, kopierte Formatierung waere genau die Art stiller Drift, die einen
    Deadlock-Vertrag unbemerkt bricht.
    """
    return f"audit_chain_{facility_id if facility_id is not None else 0}"


def advisory_lock_id(facility_id) -> int | None:
    """Der **tatsaechliche** Advisory-Lock-Key einer Facility (``hashtext``-Wert).

    Refs #2019/#2023 (Re-Review): Wer mehrere Facility-Locks in
    einer Transaktion haelt, muss sie in global gleicher Reihenfolge erwerben.
    Nach ``facility_id`` zu ordnen genuegt dafuer **nicht**: gesperrt wird
    ``hashtext(name)``, und ``hashtext`` liefert nur 32 Bit, bildet UUIDs also
    **nicht injektiv** ab. Bei einer Kollision ``h(A) = h(C) = X``, ``h(B) = Y``
    mit ``A < B < C`` nimmt ein Lauf ueber ``{A, B}`` die Keys ``X -> Y``, ein
    Lauf ueber ``{B, C}`` dagegen ``Y -> X`` — der Deadlock-Zyklus bliebe trotz
    sortierter facility_ids offen. Geordnet werden muss deshalb nach diesem
    Wert.

    Die Berechnung laeuft bewusst in Postgres statt als Python-Nachbau: nur so
    ist garantiert, dass hier derselbe Wert entsteht, den
    ``pg_advisory_xact_lock`` spaeter tatsaechlich sperrt.

    Gibt ``None`` auf Nicht-PostgreSQL zurueck — dort nimmt
    :func:`assign_chain_fields` gar keinen Lock, es gibt also auch keine
    Reihenfolge zu planen.
    """
    if connection.vendor != "postgresql":
        return None
    with connection.cursor() as cur:
        cur.execute("SELECT hashtext(%s)", [advisory_lock_name(facility_id)])
        return cur.fetchone()[0]


def assign_chain_fields(instance) -> None:
    """Setzt ``prev_hash``/``entry_hash`` (und sichert streng monoton steigende
    Zeitstempel pro Facility) fuer eine neue ``AuditLog``-Zeile.

    MUSS innerhalb derselben ``transaction.atomic()`` laufen, die den INSERT
    ausfuehrt — so wird der Advisory-Xact-Lock ueber Read+Write gehalten und
    konkurrierende Schreiber derselben Kette serialisieren. Auf Nicht-
    PostgreSQL (SQLite-Tests) entfaellt nur der Lock; die Kette wird trotzdem
    deterministisch gebildet.
    """
    if connection.vendor == "postgresql":
        # Name-Ableitung ausschliesslich ueber ``advisory_lock_name`` — dieselbe
        # Funktion, ueber die Aufrufer ihre Lock-Reihenfolge planen (#2019/#2023).
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [advisory_lock_name(instance.facility_id)])

    latest = _latest_link(instance.facility_id)
    prev_hash = (latest[1] if latest else "") or ""

    # Strenge Monotonie pro Facility: kollidiert der Zeitstempel mit dem
    # juengsten Bestand (gleiche Mikrosekunde), um 1 us anheben. Damit ist
    # ``(timestamp, id)`` eine totale Ordnung, die der Insert-Reihenfolge
    # entspricht — Verify liest in derselben Reihenfolge.
    # The reference is the newest hashed row (see ``_latest_link``); an
    # unhashed row may sort after it and is reported by ``verify_chain``.
    if latest and latest[0] and instance.timestamp and instance.timestamp <= latest[0]:
        instance.timestamp = latest[0] + timedelta(microseconds=1)

    instance.prev_hash = prev_hash
    instance.entry_hash = compute_entry_hash(instance, prev_hash)


@dataclass
class ChainResult:
    """Ergebnis von :func:`verify_chain` fuer eine Facility-Kette."""

    ok: bool
    rows_checked: int
    first_break_id: str | None = None
    reason: str = ""
    # R16 (Refs #1476/#1665): ``entry_hash`` der juengsten ueberlebenden Zeile
    # (Tail) — der Anker, den der externe High-Water-Mark fortschreibt. ``None``
    # bei leerer Kette. Rein informativ; aeltere Aufrufer ignorieren das Feld.
    last_entry_hash: str | None = None


def _checkpoint_boundaries(rows) -> set[str]:
    """Sammelt die legitimen Prune-Grenzen (``boundary_hash`` +
    ``boundary_hashes``) — aber NUR aus Checkpoint-Zeilen, die selbst
    AUTHENTIFIZIERT sind.

    Der Immutability-Trigger (Migration 0024) schuetzt nur UPDATE/DELETE. Ein
    DB-Angreifer ohne ``AUDIT_HASH_KEY`` koennte sonst eine mittlere Zeile
    loeschen und einen GEFAELSCHTEN Checkpoint einschieben (``entry_hash`` NULL
    oder Schrott, ``detail.boundary_hashes=[<hash der geloeschten Zeile>]``), der
    den dann danglenden ``prev_hash`` des Nachfolgers als legitime Prune-Grenze
    tarnt — und damit die Tamper-Evidenz aushebelt. Da er den Schluessel nicht
    kennt, kann er fuer den Checkpoint keinen gueltigen ``entry_hash`` berechnen.
    Wir vertrauen den Grenzen einer Checkpoint-Zeile daher nur, wenn ihr
    ``entry_hash`` vorhanden ist UND ``compute_entry_hash(cp, cp.prev_hash)`` via
    :func:`hmac.compare_digest` exakt reproduziert. Refs #1070.
    """
    from core.models import AuditLog

    boundaries: set[str] = set()
    for row in rows:
        if row.action != AuditLog.Action.AUDIT_PRUNE_CHECKPOINT:
            continue
        # Nur authentifizierte Checkpoints legitimieren Luecken — ein gefaelschter
        # (NULL/falscher entry_hash) wird ignoriert, die maskierte Loeschung faellt
        # dann beim Verkettungs-Check auf.
        if not row.entry_hash:
            continue
        expected = compute_entry_hash(row, row.prev_hash or "")
        if not hmac.compare_digest(expected, row.entry_hash):
            continue
        detail = row.detail or {}
        single = detail.get("boundary_hash")
        if single:
            boundaries.add(single)
        for bh in detail.get("boundary_hashes") or []:
            if bh:
                boundaries.add(bh)
    return boundaries


def verify_chain(facility, *, hwm=None) -> ChainResult:
    """Verifiziert die HMAC-Kette einer Facility und meldet den ersten Bruch.

    Pro ueberlebender Zeile (in ``(timestamp, id)``-Reihenfolge):

    1. **Integritaet** — ``entry_hash`` wird aus dem GESPEICHERTEN ``prev_hash``
       + Canonical neu berechnet und verglichen. Erkennt In-Place-Manipulation
       beliebiger semantischer Felder (auch wenn die Verkettung intakt bleibt).
    2. **Verkettung** — ``prev_hash`` muss auf den ``entry_hash`` der
       vorherigen ueberlebenden Zeile zeigen (Kettenstart: ``""``). Eine
       Diskontinuitaet ist nur legitim, wenn ``prev_hash`` eine von einem
       *authentifizierten* Checkpoint protokollierte Prune-Grenze ist (siehe
       :func:`_checkpoint_boundaries`) — sonst wurde eine Zeile
       geloescht/eingeschoben.
    3. **Completeness** -- a row without ``entry_hash`` is only accepted before
       the first hashed row of the chain (written before the chain existed).
       After the chain start it is a break of its own (Refs #2449): the
       database refuses such an INSERT, so the row was slipped in past the
       regular write path.

    ``facility`` darf eine ``Facility``-Instanz, ein PK oder ``None`` (System-
    Kette) sein. Vergleich via :func:`hmac.compare_digest`.

    **Tail-Truncation / whole-chain wipe (R16, Refs #1476/#1665).** Die
    Zeilen-gegen-Zeilen-Pruefung allein erkennt In-Place-Manipulation und
    MITTIGE Loeschung/Einschuebe, aber NICHT das Abschneiden der *juengsten*
    Zeile(n) am Ketten-Ende bzw. eine komplett geleerte Kette — die Restkette
    bleibt in sich konsistent. Wird ein
    :class:`~core.services.audit.hwm.HighWaterMarkStore` als ``hwm`` uebergeben,
    prueft ``verify_chain`` zusaetzlich, ob der zuletzt extern verankerte
    Tail-``entry_hash`` noch physisch in der Kette liegt; fehlt er, wird der
    Abschnitt/die Leerung als Bruch gemeldet. Ohne ``hwm`` bleibt diese Grenze
    bestehen (Verhalten unveraendert).
    """
    from core.models import AuditLog, Facility

    facility_id = facility.pk if isinstance(facility, Facility) else facility
    rows = list(AuditLog.objects.filter(facility_id=facility_id).order_by("timestamp", "id"))
    boundaries = _checkpoint_boundaries(rows)
    # R16: Menge aller physisch vorhandenen entry_hashes — der HWM-Anker muss
    # darin liegen, sonst wurde der Tail abgeschnitten / die Kette geleert.
    present_hashes = {r.entry_hash for r in rows if r.entry_hash}

    prev_entry: str | None = None
    checked = 0
    for row in rows:
        if not row.entry_hash:
            if checked:
                return ChainResult(
                    False,
                    checked,
                    str(row.pk),
                    "unhashed row after chain start (inserted without hash)",
                    last_entry_hash=prev_entry,
                )
            # Row from before the chain existed (not yet sealed by
            # ``backfill_audit_chain``) -- skip; the chain starts at the first
            # hashed row with ``prev_hash=""``.
            continue
        checked += 1
        expected = compute_entry_hash(row, row.prev_hash or "")
        if not hmac.compare_digest(expected, row.entry_hash):
            return ChainResult(False, checked, str(row.pk), "entry_hash mismatch (in-place tampering)")

        pv = row.prev_hash or ""
        linked = pv == prev_entry if prev_entry is not None else pv == ""
        if not linked and pv not in boundaries:
            return ChainResult(False, checked, str(row.pk), "prev_hash linkage break (row deleted/inserted)")
        prev_entry = row.entry_hash

    if hwm is not None:
        reason = hwm.check(facility_id, present_hashes)
        if reason:
            return ChainResult(False, checked, None, reason, last_entry_hash=prev_entry)

    return ChainResult(True, checked, last_entry_hash=prev_entry)
