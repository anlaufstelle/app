"""CSV-Import-Engine (M4 Strang A, Refs #1060).

Der Importer schreibt **nie direkt auf Modelle**, sondern ausschliesslich ueber
:func:`~core.services.client.create_client` und
:func:`~core.services.events.create_event`. Damit kommen Feldverschluesselung,
Kontaktstufen-Gates, ``EventHistory``, Activity-Feed und Audit-Trail
automatisch mit — ein ``bulk_create``-Pfad haette sie alle umgangen.

Kernentscheidungen
------------------

**Ein Codepfad fuer Probelauf und Uebernahme.** :func:`run_import` laeuft in
beiden Faellen identisch; nur am Ende wird bei ``commit=False`` (und bei
Fehlern auch mit ``commit=True``) per ``transaction.set_rollback(True)``
zurueckgerollt. Ohne diese Gleichheit driftet die Validierung des Probelaufs
irgendwann von der echten Uebernahme ab. Weil alles rollt, verschwinden auch
die Audit-/Activity-/History-Zeilen der Einzel-Services wieder — genau das ist
gewollt und in ``test_import_engine.py`` gepinnt.

**Ein Savepoint je Zeile.** Ohne inneres ``transaction.atomic()`` vergiftet der
erste ``IntegrityError`` (Pseudonym-Duplikat) die aeussere Transaktion, und der
Probelauf koennte nur den ersten Fehler melden statt aller.

**Uebernahme ist all-or-nothing.** Ein Lauf mit ``commit=True`` uebernimmt nur,
wenn KEINE Zeile scheitert; sonst rollt er zurueck und bleibt ``VALIDATED``.
Teilimporte waeren in einem Klientendatensatz nicht nachvollziehbar reparierbar.

**PII-Disziplin.** ``result_json`` traegt je Fehler nur Zeilennummer,
Spaltenname, Fehlercode und einen Text aus dem festen Katalog
:data:`ERROR_MESSAGES`. Zusaetzlich filtert :func:`_pii_safe_message` fail-safe
jede Meldung, in der ein Zellwert derselben Zeile auftaucht (relevant fuer
Meldungen, die aus dem Service-Layer hochkommen). Die Klassifikation
``core_importjob.result_json = non_pii`` in ``src/tests/_residue_expectations.py``
haengt an genau dieser Invariante.

**Was v1 NICHT kann** (bewusst): kein Upsert/Update (bestehendes Pseudonym =
Zeilenfehler), kein Teilimport, keine Fall-Zeilen, keine FILE-Felder als Ziel,
keine anonymen Ereignisse (``client_pseudonym`` ist Pflicht) und keine
Hintergrund-Jobs — stattdessen das Zeilen-Limit :data:`MAX_ROWS`.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog, Client, FieldTemplate, ImportJob
from core.services.audit import audit_event
from core.services.client import create_client
from core.services.dynamic_data import build_field_template_lookup
from core.services.events import create_event

# ``_facility_dir`` ist die EINE Definition des Facility-Speicherlayouts
# (``MEDIA_ROOT/<facility-pk>/``); ein zweiter Pfadbau hier waere eine stille
# Drift-Quelle, sobald das Layout sich aendert.
from core.services.file_vault import _facility_dir, decrypt_file_stream, encrypt_field, encrypt_file

logger = logging.getLogger(__name__)

#: Harte Obergrenzen. Der Commit laeuft synchron im Request (kein Queue-Backend
#: im Stack) — das Zeilen-Limit ersetzt die fehlende Hintergrund-Verarbeitung.
MAX_ROWS = 1000
MAX_BYTES = 5 * 1024 * 1024
#: Nach so vielen Tagen raeumt ``enforce_retention`` einen liegengebliebenen
#: Lauf ab (Upload ist PII und darf nicht unbegrenzt herumliegen).
STALE_DAYS = 7

#: Zeilen der Vorschau in Schritt 1/2 des Wizards.
PREVIEW_ROWS = 5

#: Praefix der dynamischen Zuordnungsziele (``data_json.<slug>``).
DATA_JSON_PREFIX = "data_json."

#: Unterstuetzte Encodings (Reihenfolge = Fallback-Kette). Die Auswahlliste des
#: Upload-Formulars leitet sich davon ab (``core/forms/import_data.py``).
SUPPORTED_ENCODINGS = ("utf-8-sig", "cp1252")

_ENCODING_CANDIDATES = SUPPORTED_ENCODINGS
_SNIFF_DELIMITERS = ";,\t|"
_DEFAULT_DELIMITER = ";"

_TRUE_VALUES = frozenset({"ja", "j", "true", "wahr", "1", "x", "yes", "y"})
_FALSE_VALUES = frozenset({"nein", "n", "false", "falsch", "0", "no"})

#: Deutsche Tausendertrennung OHNE Dezimalkomma (``1.500``, ``12.345.678``).
#: Bewusst eng: nur exakte Dreiergruppen gelten als Trennung, alles andere
#: bleibt Dezimalpunkt (siehe :func:`_coerce_number`). Die erste Gruppe darf
#: nicht ``0`` sein — ``0.500`` ist nie eine deutsche Gruppierung, sondern
#: ein Dezimalbruch (S5-Restluecke N4, Refs #1060).
_GROUPED_RE = re.compile(r"^[+-]?[1-9]\d{0,2}(\.\d{3})+$")

_DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y")
_DATETIME_FORMATS = ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y")
_TIME_FORMATS = ("%H:%M", "%H:%M:%S")

#: Fester Meldungskatalog. Bewusst OHNE Interpolation von Zellwerten — der
#: Fehlerbericht landet in ``result_json`` und ist als ``non_pii``
#: klassifiziert. Die Spalte steht separat im Bericht.
ERROR_MESSAGES: dict[str, Any] = {
    "required_missing": _("Pflichtfeld ist leer."),
    "invalid_number": _("Wert ist keine gültige Zahl."),
    "invalid_date": _("Wert ist kein gültiges Datum (erwartet: TT.MM.JJJJ oder JJJJ-MM-TT)."),
    "invalid_datetime": _("Wert ist kein gültiger Zeitpunkt (erwartet: TT.MM.JJJJ [HH:MM])."),
    "invalid_time": _("Wert ist keine gültige Uhrzeit (erwartet: HH:MM)."),
    "invalid_boolean": _("Wert ist kein Ja/Nein-Wert."),
    "invalid_option": _("Wert ist keine zulässige Auswahl für dieses Feld."),
    "unknown_client": _("Zu diesem Pseudonym gibt es in der Einrichtung keine Person."),
    "duplicate": _("Die Zeile verletzt eine Eindeutigkeitsregel (z. B. bereits vergebenes Pseudonym)."),
    "validation": _("Die fachliche Prüfung hat die Zeile abgelehnt."),
    "permission": _("Ihre Rolle darf diese Dokumentation nicht anlegen."),
    "invalid_value": _("Der Zeileninhalt ist für dieses Ziel nicht verwendbar."),
}

_GENERIC_MESSAGE = _("Die Zeile konnte nicht übernommen werden.")


@dataclass(frozen=True)
class ImportTarget:
    """Ein waehlbares Zuordnungsziel im Mapping-Schritt."""

    key: str
    label: str
    required: bool = False


class _CellError(Exception):
    """Interner Zeilenfehler mit Fehlercode und (optional) Spaltenbezug."""

    def __init__(self, code: str, column: str | None = None):
        super().__init__(code)
        self.code = code
        self.column = column


# ---------------------------------------------------------------------------
# Datei-Handling
# ---------------------------------------------------------------------------


def _import_dir(facility) -> Path:
    """``MEDIA_ROOT/<facility-pk>/imports/`` — Ablage der verschluesselten Uploads."""
    return _facility_dir(facility) / "imports"


def import_file_path(job: ImportJob) -> Path:
    """Vollstaendiger Pfad der verschluesselten Quelldatei eines Laufs."""
    return _import_dir(job.facility) / job.storage_filename


def delete_import_file(job: ImportJob) -> None:
    """Loescht die verschluesselte Quelldatei von der Disk (idempotent)."""
    if not job.storage_filename:
        return
    try:
        import_file_path(job).unlink(missing_ok=True)
    except OSError:  # pragma: no cover - Dateisystemfehler
        logger.warning("Import-Datei konnte nicht geloescht werden: job=%s", job.pk)


def _read_upload(uploaded_file) -> bytes:
    """Liest den Upload vollstaendig ein — nach Groessenpruefung.

    Die Groesse wird VOR der Voll-Pufferung geprueft (Memory-DoS-Schutz, wie in
    ``file_vault.policy.enforce_upload_size``). 5 MiB im Speicher sind fuer den
    zweifachen Bedarf (Parsen + Verschluesseln) vertretbar und ersparen einen
    Temp-Datei-Zwischenschritt.
    """
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > MAX_BYTES:
        raise ValidationError(
            _("Die Datei ist zu groß (maximal %(mb)s MB).") % {"mb": MAX_BYTES // (1024 * 1024)},
        )
    raw = uploaded_file.read()
    if len(raw) > MAX_BYTES:
        raise ValidationError(
            _("Die Datei ist zu groß (maximal %(mb)s MB).") % {"mb": MAX_BYTES // (1024 * 1024)},
        )
    return raw


def _decode(raw: bytes, encoding: str) -> tuple[str, str]:
    """Dekodiert die Rohbytes; liefert ``(text, verwendetes_encoding)``.

    Deutsche Excel-Exporte sind der Hauptfall: ``utf-8-sig`` faengt das BOM ab,
    ``cp1252`` die Alt-Exporte. Ein explizit gewaehltes Encoding gewinnt.
    """
    candidates = (encoding,) if encoding else _ENCODING_CANDIDATES
    for candidate in candidates:
        try:
            return raw.decode(candidate), candidate
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValidationError(_("Die Datei konnte weder als UTF-8 noch als Windows-1252 gelesen werden."))


def _sniff_delimiter(text: str) -> str:
    """Delimiter aus einer Textprobe raten; Semikolon als Rueckfallwert.

    ``csv.Sniffer`` ist fehlbar (einspaltige Dateien, Freitext mit Kommata) —
    deshalb gibt es im Upload-Formular den manuellen Override.
    """
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=_SNIFF_DELIMITERS).delimiter
    except csv.Error:
        return _DEFAULT_DELIMITER


def _parse_csv(text: str, delimiter: str) -> tuple[list[str], list[list[str]]]:
    """Kopfzeile + Datenzeilen; leere Zeilen fallen raus, Limits greifen hart."""
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = [column.strip() for column in next(reader)]
    except StopIteration:
        raise ValidationError(_("Die Datei enthält keine Kopfzeile.")) from None
    if not header or all(not column for column in header):
        raise ValidationError(_("Die Datei enthält keine Kopfzeile."))
    if any(not column for column in header):
        raise ValidationError(_("Jede Spalte der Kopfzeile braucht eine Überschrift."))
    if len(set(header)) != len(header):
        raise ValidationError(_("Die Kopfzeile enthält doppelte Spaltenüberschriften."))

    rows: list[list[str]] = []
    for row in reader:
        if not any((cell or "").strip() for cell in row):
            continue
        rows.append(list(row))
        if len(rows) > MAX_ROWS:
            raise ValidationError(_("Die Datei hat mehr als %(max)s Datenzeilen.") % {"max": MAX_ROWS})
    return header, rows


# ---------------------------------------------------------------------------
# Upload + Lesen
# ---------------------------------------------------------------------------


def store_import_upload(
    facility,
    user,
    uploaded_file,
    *,
    target_type: str,
    document_type=None,
    delimiter: str = "",
    encoding: str = "",
) -> ImportJob:
    """Nimmt eine CSV entgegen, legt sie verschluesselt ab und erzeugt den Lauf.

    Reihenfolge bewusst: erst Limits + Parsbarkeit pruefen (sonst entstuende
    eine unbrauchbare ``.enc``-Datei auf der Disk), dann verschluesselt
    schreiben, dann die Zeile persistieren. Scheitert der DB-Write, wird die
    Datei wieder entfernt (Muster ``file_vault.storage.commit_staged_upload``).

    Der Originaldateiname wird NUR verschluesselt persistiert und taucht auch im
    Audit-Detail nicht auf — Dateinamen tragen in der Praxis Klientenbezug.
    """
    raw = _read_upload(uploaded_file)
    text, used_encoding = _decode(raw, encoding)
    used_delimiter = delimiter or _sniff_delimiter(text)
    header, rows = _parse_csv(text, used_delimiter)

    job = ImportJob(
        facility=facility,
        created_by=user,
        target_type=target_type,
        document_type=document_type,
    )
    # Fail-loud VOR jedem Dateischreiben: ein Ereignis-Lauf ohne Typ oder mit
    # fremdem/unpassendem Typ ist nicht ausfuehrbar.
    job.clean()

    storage_filename = f"{uuid.uuid4()}.enc"
    output_path = _import_dir(facility) / storage_filename
    # A4.5-Muster: Chunks an die Storage-ID binden (v2-Format).
    encrypt_file(io.BytesIO(raw), output_path, file_id=storage_filename)
    try:
        job.storage_filename = storage_filename
        job.original_filename_encrypted = encrypt_field(getattr(uploaded_file, "name", "") or "import.csv")
        job.mapping_json = {"columns": {}, "delimiter": used_delimiter, "encoding": used_encoding}
        job.save()
    except Exception:
        output_path.unlink(missing_ok=True)
        raise

    audit_event(
        AuditLog.Action.IMPORT_UPLOADED,
        user=user,
        facility=facility,
        target_obj=job,
        detail={
            "target_type": job.target_type,
            "rows": len(rows),
            "columns": len(header),
        },
    )
    return job


def read_rows(job: ImportJob) -> tuple[list[str], list[list[str]]]:
    """Kopfzeile + alle Datenzeilen der (entschluesselten) Quelldatei."""
    if not job.storage_filename:
        raise ValidationError(_("Zu diesem Importlauf gibt es keine Quelldatei mehr."))
    path = import_file_path(job)
    if not path.exists():
        raise ValidationError(_("Die hochgeladene Datei ist nicht mehr vorhanden."))
    raw = b"".join(decrypt_file_stream(path, file_id=job.storage_filename))
    mapping = job.mapping_json or {}
    text, _used = _decode(raw, mapping.get("encoding") or "")
    delimiter = mapping.get("delimiter") or _sniff_delimiter(text)
    return _parse_csv(text, delimiter)


def parse_preview(job: ImportJob, limit: int = PREVIEW_ROWS) -> dict[str, Any]:
    """Vorschau fuer Schritt 1/2: Spalten, erste ``limit`` Zeilen, Gesamtzahl."""
    header, rows = read_rows(job)
    return {"columns": header, "rows": rows[:limit], "total_rows": len(rows)}


# ---------------------------------------------------------------------------
# Zuordnungsziele
# ---------------------------------------------------------------------------


def _dynamic_templates(job: ImportJob) -> dict[str, FieldTemplate]:
    """``{slug: FieldTemplate}`` der zuordenbaren Zusatzfelder.

    FILE-Felder sind NIE dabei: Anhaenge haengen an einem Owner und laufen ueber
    die Upload-UI (Scan, Magic-Bytes, Versionskette) — ein CSV kann das nicht
    liefern. Inaktive Vorlagen bleiben ebenfalls draussen, wie im Formular.
    """
    document_type = job.document_type
    if document_type is None:
        return {}
    return {
        slug: template
        for slug, template in build_field_template_lookup(document_type, ordered=True).items()
        if template.field_type != FieldTemplate.FieldType.FILE and template.is_active
    }


def available_targets(job: ImportJob) -> list[ImportTarget]:
    """Alle zulaessigen Zuordnungsziele des Laufs (Reihenfolge = Anzeige).

    Dynamische Ziele sind bewusst nie ``required``: auch das Formular erzwingt
    ``FieldTemplate.is_required`` beim Anlegen ueber den Service-Layer nicht —
    eine strengere Mapping-Regel waere ein zweiter, abweichender Vertrag.
    """
    if job.target_type == ImportJob.TargetType.CLIENT:
        targets = [
            ImportTarget("pseudonym", gettext("Pseudonym"), required=True),
            ImportTarget("contact_stage", gettext("Kontaktstufe"), required=False),
            ImportTarget("age_cluster", gettext("Altersgruppe"), required=False),
            ImportTarget("notes", gettext("Notizen"), required=False),
        ]
    else:
        targets = [
            ImportTarget("occurred_at", gettext("Zeitpunkt"), required=True),
            ImportTarget("client_pseudonym", gettext("Pseudonym der Person"), required=True),
        ]
    targets.extend(
        ImportTarget(f"{DATA_JSON_PREFIX}{slug}", template.name, required=False)
        for slug, template in _dynamic_templates(job).items()
    )
    return targets


def apply_mapping(job: ImportJob, mapping: dict[str, str]) -> ImportJob:
    """Validiert und persistiert die Spalten→Ziel-Zuordnung.

    Eigene Validierung statt Vertrauen auf ``validate_data_json``: das droppt
    unbekannte Slugs nur mit einer Log-Warnung — ein falsch gemapptes Feld
    verschwaende sonst kommentarlos und der Import meldete Erfolg.
    """
    if job.is_final:
        raise ValidationError(_("Der Importlauf ist bereits abgeschlossen."))
    header, _rows = read_rows(job)
    known = {target.key: target for target in available_targets(job)}

    columns: dict[str, str] = {}
    assigned: set[str] = set()
    for column, raw_target in (mapping or {}).items():
        target = (raw_target or "").strip()
        if not target:
            continue
        if column not in header:
            raise ValidationError(_("Die Spalte „%(column)s“ gibt es in der Datei nicht.") % {"column": column})
        if target not in known:
            raise ValidationError(_("„%(target)s“ ist kein zulässiges Zielfeld.") % {"target": target})
        if target in assigned:
            raise ValidationError(
                _("Das Zielfeld „%(target)s“ ist mehrfach zugeordnet.") % {"target": known[target].label}
            )
        assigned.add(target)
        columns[column] = target

    missing = [target.label for target in known.values() if target.required and target.key not in assigned]
    if missing:
        raise ValidationError(
            _("Diese Pflichtfelder brauchen eine Spalte: %(fields)s") % {"fields": ", ".join(missing)}
        )

    job.mapping_json = {**(job.mapping_json or {}), "columns": columns}
    job.status = ImportJob.Status.MAPPED
    job.save(update_fields=["mapping_json", "status", "updated_at"])
    return job


# ---------------------------------------------------------------------------
# Wert-Koerzierung
# ---------------------------------------------------------------------------


def _coerce_number(raw: str, column: str | None):
    """Deutsches Dezimalkomma inkl. Tausenderpunkt (``1.234,50``, ``1.500``).

    **Konvention fuer den Punkt ohne Komma** (Refs #1060): als Tausendertrennung
    gilt ausschliesslich die exakte Gruppierungsform :data:`_GROUPED_RE`
    (``1.500``, ``12.345.678``) — dort sind Punkte reine Trenner. Jeder andere
    Punkt bleibt Dezimalpunkt (``1.5`` -> 1,5). Begruendung: Der deutsche Export
    ist der dokumentierte Hauptfall (siehe :func:`_decode`), und ohne diese
    Regel las ``float()`` ``1.500`` still als 1,5 — ein Faktor-1000-Fehler ohne
    Fehlermeldung. Formen, die keine Gruppierung sein KOENNEN, bleiben dagegen
    unveraendert lesbar, damit Dateien aus englischsprachigen Werkzeugen nicht
    brechen. Restrisiko bewusst akzeptiert: ein englisches ``1.500`` (gemeint:
    1,5) wird als 1500 gelesen — deshalb ist die Gruppierungsform eng gefasst.

    Nicht-endliche Werte (``inf``/``nan``) sind ein Zeilenfehler: ``float()``
    nimmt sie klaglos, ``jsonb`` kann sie nicht darstellen — der DB-Fehler
    traefe erst den Schreibpfad.
    """
    text = raw.replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif _GROUPED_RE.match(text):
        text = text.replace(".", "")
    try:
        value = float(text)
    except ValueError:
        raise _CellError("invalid_number", column) from None
    if not math.isfinite(value):
        raise _CellError("invalid_number", column)
    return int(value) if value.is_integer() else value


def _parse_with_formats(raw: str, formats: tuple[str, ...]):
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _coerce_date(raw: str, column: str | None) -> str:
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        parsed = _parse_with_formats(raw, _DATE_FORMATS)
        if parsed is None:
            raise _CellError("invalid_date", column) from None
        return parsed.date().isoformat()


def _coerce_time(raw: str, column: str | None) -> str:
    try:
        return time.fromisoformat(raw).isoformat()
    except ValueError:
        parsed = _parse_with_formats(raw, _TIME_FORMATS)
        if parsed is None:
            raise _CellError("invalid_time", column) from None
        return parsed.time().isoformat()


def _coerce_boolean(raw: str, column: str | None) -> bool:
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise _CellError("invalid_boolean", column)


def _coerce_option(template: FieldTemplate, raw: str, column: str | None) -> str:
    """Slug oder Label einer aktiven Option → Slug (das Wire-Format von ``data_json``)."""
    options = [option for option in (template.options_json or []) if isinstance(option, dict)]
    for option in options:
        if not option.get("is_active", True):
            continue
        if raw == option.get("slug") or raw.casefold() == str(option.get("label", "")).casefold():
            return option["slug"]
    raise _CellError("invalid_option", column)


# Refs #1805: Die Kontaktstufen-Labels wurden lebensweltlich umbenannt
# ("Identifiziert"/"Qualifiziert" → "Nur Pseudonym"/"Mit Zusatzangaben"). Der
# CSV-Export schreibt das ANZEIGE-Label (services/system/export.py:170), der
# Import akzeptiert Wert ODER Label — jede vor der Umbenennung erzeugte Datei
# traegt also die Altbezeichnung. CSV ist ein Datenvertrag mit Bestandsdateien
# der Einrichtungen (Konzept §3.1), darum bleiben die Alt-Labels dauerhaft
# lesbar; geschrieben wird ausschliesslich die neue Form.
_LEGACY_CONTACT_STAGE_LABELS: dict[str, str] = {
    "identifiziert": Client.ContactStage.IDENTIFIED.value,
    "qualifiziert": Client.ContactStage.QUALIFIED.value,
}


def _coerce_choice(raw: str, choices, column: str | None, legacy_labels: dict[str, str] | None = None) -> str:
    """Wert oder Anzeigename einer ``TextChoices``-Konstante → Wert.

    ``legacy_labels`` bildet abgeloeste Anzeigenamen (casefold) auf den Wert
    ab, damit eine Label-Umbenennung keine Bestandsdatei unlesbar macht.
    """
    for value, label in choices.choices:
        if raw == value or raw.casefold() == str(label).casefold():
            return value
    if legacy_labels is not None:
        legacy_value = legacy_labels.get(raw.casefold())
        if legacy_value is not None:
            return legacy_value
    raise _CellError("invalid_option", column)


def _coerce_datetime(raw: str, column: str | None) -> datetime:
    """ISO-8601 oder ``TT.MM.JJJJ [HH:MM]`` → zeitzonenbewusster Zeitpunkt."""
    parsed: datetime | None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = _parse_with_formats(raw, _DATETIME_FORMATS)
    if parsed is None:
        raise _CellError("invalid_datetime", column)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _coerce_field_value(template: FieldTemplate, raw: str, column: str | None):
    field_type = template.field_type
    if field_type == FieldTemplate.FieldType.NUMBER:
        return _coerce_number(raw, column)
    if field_type == FieldTemplate.FieldType.DATE:
        return _coerce_date(raw, column)
    if field_type == FieldTemplate.FieldType.TIME:
        return _coerce_time(raw, column)
    if field_type == FieldTemplate.FieldType.BOOLEAN:
        return _coerce_boolean(raw, column)
    if field_type == FieldTemplate.FieldType.SELECT:
        return _coerce_option(template, raw, column)
    if field_type == FieldTemplate.FieldType.MULTI_SELECT:
        return [_coerce_option(template, part.strip(), column) for part in raw.split(",") if part.strip()]
    return raw


# ---------------------------------------------------------------------------
# Lauf
# ---------------------------------------------------------------------------


def _row_values(header: list[str], row: list[str], columns_map: dict[str, str]) -> tuple[dict, dict]:
    """``(ziel → Zellwert, ziel → Spaltenname)`` fuer eine Zeile."""
    values: dict[str, str] = {}
    columns: dict[str, str] = {}
    for index, column in enumerate(header):
        target = columns_map.get(column)
        if not target:
            continue
        cell = row[index] if index < len(row) else ""
        values[target] = (cell or "").strip()
        columns[target] = column
    return values, columns


def _collect_data_json(job: ImportJob, values: dict, columns: dict) -> dict:
    templates = _dynamic_templates(job)
    data: dict[str, Any] = {}
    for target, raw in values.items():
        if not target.startswith(DATA_JSON_PREFIX):
            continue
        template = templates.get(target[len(DATA_JSON_PREFIX) :])
        if template is None or raw == "":
            continue
        data[template.slug] = _coerce_field_value(template, raw, columns.get(target))
    return data


def _import_client_row(job: ImportJob, user, values: dict, columns: dict) -> None:
    pseudonym = values.get("pseudonym", "")
    if not pseudonym:
        raise _CellError("required_missing", columns.get("pseudonym"))

    payload: dict[str, Any] = {"pseudonym": pseudonym}
    if values.get("contact_stage"):
        payload["contact_stage"] = _coerce_choice(
            values["contact_stage"],
            Client.ContactStage,
            columns.get("contact_stage"),
            legacy_labels=_LEGACY_CONTACT_STAGE_LABELS,
        )
    if values.get("age_cluster"):
        payload["age_cluster"] = _coerce_choice(values["age_cluster"], Client.AgeCluster, columns.get("age_cluster"))
    if values.get("notes"):
        payload["notes"] = values["notes"]
    if job.document_type_id:
        payload["document_type"] = job.document_type
        payload["data_json"] = _collect_data_json(job, values, columns)

    create_client(job.facility, user, **payload)


def _import_event_row(job: ImportJob, user, values: dict, columns: dict) -> None:
    raw_occurred = values.get("occurred_at", "")
    if not raw_occurred:
        raise _CellError("required_missing", columns.get("occurred_at"))
    occurred_at = _coerce_datetime(raw_occurred, columns.get("occurred_at"))

    pseudonym = values.get("client_pseudonym", "")
    if not pseudonym:
        raise _CellError("required_missing", columns.get("client_pseudonym"))
    client = Client.objects.for_facility(job.facility).active().filter(pseudonym=pseudonym).first()
    if client is None:
        raise _CellError("unknown_client", columns.get("client_pseudonym"))

    create_event(
        job.facility,
        user,
        job.document_type,
        occurred_at,
        _collect_data_json(job, values, columns),
        client=client,
    )


def _pii_safe_message(message: str, row: list[str]) -> str:
    """Fail-safe-Filter: Meldungen mit Zellwert werden generisch ersetzt.

    Der feste Katalog :data:`ERROR_MESSAGES` interpoliert nie Werte — aber
    Meldungen aus dem Service-Layer (``ValidationError``) sind fremder Text.
    Ein Treffer ist bewusst grob definiert (jeder nicht-leere Zellwert der
    Zeile, der woertlich in der Meldung vorkommt); ein Fehlalarm kostet nur
    Detailtiefe, ein uebersehener Wert waere ein PII-Leck in ``result_json``.
    """
    for cell in row:
        value = (cell or "").strip()
        if value and value in message:
            return str(_GENERIC_MESSAGE)
    return message


def _error_entry(row_number: int, column: str | None, code: str, row: list[str], message: str | None = None) -> dict:
    text = message if message is not None else str(ERROR_MESSAGES.get(code, _GENERIC_MESSAGE))
    return {
        "row": row_number,
        "column": column or "",
        "code": code,
        "message": _pii_safe_message(text, row),
    }


def run_import(job: ImportJob, user, *, commit: bool = False) -> dict:
    """Fuehrt den Lauf aus — Probelauf (``commit=False``) oder Uebernahme.

    Ergebnis (auch in ``job.result_json``)::

        {"summary": {"rows", "created", "errors", "committed"},
         "errors": [{"row", "column", "code", "message"}]}

    ``row`` zaehlt die Zeile in der Datei inklusive Kopfzeile (erste Datenzeile
    = 2), damit die Nummer zu dem passt, was die Anwenderin in Excel sieht.
    """
    if job.is_final:
        raise ValidationError(_("Der Importlauf ist bereits abgeschlossen."))

    header, rows = read_rows(job)
    columns_map = (job.mapping_json or {}).get("columns") or {}
    if not columns_map:
        raise ValidationError(_("Für diesen Importlauf ist noch keine Spaltenzuordnung hinterlegt."))

    errors: list[dict] = []
    created = 0
    with transaction.atomic():
        for offset, row in enumerate(rows):
            row_number = offset + 2
            values, columns = _row_values(header, row, columns_map)
            try:
                # Savepoint je Zeile: ohne ihn vergiftet der erste
                # IntegrityError die aeussere Transaktion (E3).
                with transaction.atomic():
                    if job.target_type == ImportJob.TargetType.CLIENT:
                        _import_client_row(job, user, values, columns)
                    else:
                        _import_event_row(job, user, values, columns)
            except _CellError as exc:
                errors.append(_error_entry(row_number, exc.column, exc.code, row))
            except IntegrityError:
                # str(exc) traegt die DB-DETAIL-Zeile inkl. Zellwert — nie
                # uebernehmen, immer der feste Katalogtext.
                errors.append(_error_entry(row_number, None, "duplicate", row))
            except DatabaseError:
                # MUSS nach IntegrityError stehen (Subklasse). Auffangnetz fuer
                # alle uebrigen DB-Fehler der Zeile — vor allem DataError
                # (varchar-Ueberlauf, wenn eine Freitext-Spalte auf ein kurzes
                # Ziel wie ``pseudonym`` gemappt wurde): der Schreibpfad ruft
                # ``save()`` ohne ``full_clean()``, die Laenge schlaegt also erst
                # in Postgres auf. Ohne diesen Arm waere das ein 500er und der
                # ganze Lauf braeche ab. Auch hier nie ``str(exc)`` — die
                # DETAIL-Zeile traegt den Zellwert.
                if connection.needs_rollback:
                    # Der Savepoint-Rollback einer Zeile ist gescheitert
                    # (Verbindungsabriss): ab hier lehnt Django jede Query mit
                    # ``TransactionManagementError`` ab — ein ProgrammingError
                    # und damit ebenfalls ``DatabaseError``. Ohne dieses
                    # Durchreichen liefe die Schleife weiter und meldete JEDE
                    # Folgezeile faelschlich als ``invalid_value``, der Lauf
                    # endete mit einem VALIDATED-Verdikt, das nichts geprueft
                    # hat. Die aeussere Klammer rollt sauber zurueck, das
                    # abschliessende ``job.save()`` laeuft nicht mehr — der
                    # Lauf bleibt in seinem alten Status (Refs #1793).
                    raise
                errors.append(_error_entry(row_number, None, "invalid_value", row))
            except ValidationError as exc:
                message = " ".join(str(part) for part in exc.messages) if exc.messages else None
                errors.append(_error_entry(row_number, None, "validation", row, message=message))
            except PermissionDenied as exc:
                errors.append(_error_entry(row_number, None, "permission", row, message=str(exc) or None))
            except ValueError as exc:
                errors.append(_error_entry(row_number, None, "invalid_value", row, message=str(exc) or None))
            else:
                created += 1

        committed = commit and not errors
        if not committed:
            # MUSS die letzte Anweisung im Block sein — danach lehnt Django
            # jede weitere Query in dieser Transaktion ab.
            transaction.set_rollback(True)

    result = {
        "summary": {
            "rows": len(rows),
            "created": created,
            "errors": len(errors),
            "committed": committed,
        },
        "errors": errors,
    }
    job.result_json = result
    job.status = ImportJob.Status.COMMITTED if committed else ImportJob.Status.VALIDATED
    job.save(update_fields=["result_json", "status", "updated_at"])
    return result


def _lock_job(job: ImportJob) -> None:
    """Liest die Job-Zeile unter Zeilen-Lock neu und synchronisiert ``job``.

    Der Wizard laeuft ueber mehrere Requests, und jeder POST arbeitet auf einer
    eigenen, in der View geladenen Instanz. Entschiede der Statusuebergang auf
    diesem In-Memory-Snapshot, importierte ein Doppelklick auf „Uebernehmen" die
    Datei zweimal, und ein spaeter eintreffendes ``abort_import`` schriebe einen
    bereits uebernommenen Lauf auf ``ABORTED`` um. ``probe_import``,
    ``commit_import`` und ``abort_import`` laufen deshalb ueber DENSELBEN Lock:
    der zweite Request blockiert, liest danach den Endzustand und faellt in den
    Guard.

    Muster wie ``core.services.case.workitems`` — Neuladen innerhalb der
    Transaktionsklammer, mit explizitem ``facility_id``-Filter, damit Tenant-
    und RLS-Isolation greifen. Der Aufrufer MUSS in ``transaction.atomic``
    stehen (sonst ist ``select_for_update`` wirkungslos bzw. ein Fehler).
    """
    locked = ImportJob.objects.select_for_update().get(pk=job.pk, facility_id=job.facility_id)
    job.status = locked.status
    job.result_json = locked.result_json
    job.storage_filename = locked.storage_filename


def probe_import(job: ImportJob, user, mapping: dict[str, str]) -> dict:
    """Zuordnung speichern und den Probelauf fahren — unter demselben Lock.

    Der Mapping-Schritt schreibt den Status (``apply_mapping`` -> MAPPED,
    ``run_import`` -> VALIDATED) und gehoert damit in dieselbe Serialisierung
    wie :func:`commit_import`/:func:`abort_import`. Ohne den Lock entschieden
    beide Schritte auf dem In-Memory-Snapshot der View: waehrend ein paralleler
    Commit laeuft, sieht der Mapping-POST noch VALIDATED, laeuft durch und
    schreibt den bereits uebernommenen Lauf auf MAPPED/VALIDATED zurueck — die
    Datei wuerde ein zweites Mal importiert und der Endzustand waere
    widerspruechlich (Refs #1793).

    ``apply_mapping`` und ``run_import`` bleiben selbst lock-frei: die
    Lock-Verantwortung traegt wie bei ``commit_import`` der Aufrufer, ihre
    ``is_final``-Pruefungen sind die zweite Linie.
    """
    with transaction.atomic():
        _lock_job(job)
        apply_mapping(job, mapping)
        return run_import(job, user, commit=False)


def commit_import(job: ImportJob, user) -> ImportJob:
    """Uebernimmt einen fehlerfrei geprueften Lauf und raeumt die Quelldatei ab."""
    with transaction.atomic():
        _lock_job(job)
        if job.status != ImportJob.Status.VALIDATED:
            raise ValidationError(_("Der Importlauf muss zuerst fehlerfrei geprüft werden."))
        if job.error_count:
            raise ValidationError(_("Der Importlauf enthält Fehler und kann nicht übernommen werden."))
        # ``run_import`` klammert sich hier als Savepoint ein; sein
        # ``set_rollback(True)`` im Fehlerfall rollt deshalb nur diesen
        # Savepoint zurueck, nicht den Lock.
        result = run_import(job, user, commit=True)

    if not result["summary"]["committed"]:
        # Zwischen Probelauf und Uebernahme hat sich der Datenbestand geaendert
        # (z. B. Pseudonym inzwischen vergeben) — nichts wurde geschrieben.
        # Bewusst NACH dem atomic-Block: die Daten sind zurueckgerollt, der
        # frische Fehlerbericht in ``result_json`` soll aber erhalten bleiben,
        # weil die Detailseite ihn anzeigt.
        raise ValidationError(_("Beim Übernehmen sind Fehler aufgetreten — es wurde nichts gespeichert."))

    delete_import_file(job)
    job.storage_filename = ""
    job.original_filename_encrypted = {}
    job.save(update_fields=["storage_filename", "original_filename_encrypted", "updated_at"])

    audit_event(
        AuditLog.Action.IMPORT_COMMITTED,
        user=user,
        facility=job.facility,
        target_obj=job,
        detail={
            "target_type": job.target_type,
            "rows": result["summary"]["rows"],
            "created": result["summary"]["created"],
        },
    )
    return job


def abort_import(job: ImportJob, user, *, reason: str = "user") -> ImportJob:
    """Bricht einen Lauf ab: Datei loeschen, Status ``ABORTED``, Audit.

    Ueber denselben Zeilen-Lock wie :func:`commit_import` — sonst koennten sich
    Abbruch und Uebernahme ueberholen (Daten importiert, Lauf trotzdem
    ``ABORTED``).
    """
    with transaction.atomic():
        _lock_job(job)
        if job.is_final:
            raise ValidationError(_("Der Importlauf ist bereits abgeschlossen."))

        delete_import_file(job)
        job.storage_filename = ""
        job.original_filename_encrypted = {}
        job.status = ImportJob.Status.ABORTED
        job.save(update_fields=["storage_filename", "original_filename_encrypted", "status", "updated_at"])

    audit_event(
        AuditLog.Action.IMPORT_ABORTED,
        user=user,
        facility=job.facility,
        target_obj=job,
        detail={"target_type": job.target_type, "reason": reason},
    )
    return job


def cleanup_stale_import_jobs(facility=None, now=None) -> int:
    """Raeumt liegengebliebene Laeufe ab; liefert die Anzahl.

    Ein nicht abgeschlossener Lauf haelt eine verschluesselte Datei mit
    Klientendaten auf der Disk. Nach :data:`STALE_DAYS` gilt er als aufgegeben:
    Datei weg, Status ``ABORTED``, Audit mit ``reason=stale``. Die Job-Zeile
    selbst bleibt als PII-freie Spur stehen (kein Hard-Delete) — Gegenstueck zu
    ``cleanup_stale_proposals``, das echte Waisen loescht, weil dort kein
    Vorgang dokumentiert bleiben muss.
    """
    cutoff = (now or timezone.now()) - timedelta(days=STALE_DAYS)
    queryset = ImportJob.objects.all() if facility is None else ImportJob.objects.for_facility(facility)
    stale = queryset.exclude(status__in=ImportJob.FINAL_STATUSES).filter(created_at__lt=cutoff)

    count = 0
    # Bewusst materialisiert statt ``.iterator()``: die Schleife aendert genau
    # die Spalte, auf die das Queryset filtert — ein offener Cursor waere von
    # den eigenen Updates abhaengig.
    for job in list(stale):
        # ``user=None``: der Cron-Lauf hat keinen Akteur (Muster
        # ``prune_offline_client_revocations``/``audit_event``-Doku).
        abort_import(job, None, reason="stale")
        count += 1
    return count
