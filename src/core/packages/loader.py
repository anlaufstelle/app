"""Idempotenter Paket-Loader mit Report, Savepoints und Dry-Run (M2 P10, Refs #1058).

:func:`load_package` spielt ein validiertes
:class:`~core.packages.schema.PackageV1` gegen **eine** Einrichtung ein. Der
Loader ist bewusst als *Upsert von Hand* gebaut, nicht über
``update_or_create``:

* Djangos ``update_or_create`` schreibt mit ``save(update_fields=…)`` — das
  kollidiert mit der Sonderlogik in :meth:`FieldTemplate.save`
  (FILE/HIGH erzwingen ``is_encrypted``) und würde erzwungene Felder aus dem
  Update ausschließen (E12).
* Wir wollen vor dem Schreiben einen **Diff** (created/updated/unchanged für
  den Report) und ein ``full_clean()`` — letzteres verwandelt Unique-Konflikte
  (z. B. ``(facility, name)``) in eine lesbare ``ValidationError``,
  bevor daraus ein ``IntegrityError`` wird.

Transaktions-Bauform: **ein** äußeres ``transaction.atomic`` über den ganzen
Lauf (Alles-oder-nichts) plus ein **Savepoint je Objekt**. Ohne den inneren
Savepoint würde ein durchgerutschter ``IntegrityError`` die Postgres-
Transaktion vergiften ("current transaction is aborted") und jede weitere
Abfrage im selben Lauf mitreißen — wir wollen aber alle Fehler eines Laufs
sammeln und erst am Ende gebündelt melden.

Semantik-Entscheide (verbindlich, E13–E15):

* **Unveränderlich** (Abweichung ⇒ Fehler, nie stiller Wechsel): ``slug``
  (der Lookup-Key selbst), ``entity_type``, ``system_type`` (Ausnahme:
  ``None`` → Wert ist erlaubt, deckt Bestandsdaten ohne Systemtyp),
  ``field_type``, ``is_encrypted``.
* **Bedingungen** sind deklarativ *wenn vorhanden*: Schlüssel fehlt ⇒
  unangetastet (UI-gepflegte Regeln überleben), ``[]`` ⇒ leeren, sonst
  Wholesale-Replace. Gruppen und Feldzuordnungen sind dagegen additiv —
  v1 entfernt nie etwas, was das Paket nicht mehr erwähnt.
* **Dry-Run** nimmt exakt denselben Code-Pfad und rollt am Ende zurück.

Bewusst NICHT enthalten: Audit-Logging (konsistent mit dem Seed — der Loader
ist ein Betriebswerkzeug ohne Request-Kontext) und das Setzen eines
RLS-Kontexts. Der Aufrufer MUSS eine Rolle mitbringen, die RLS umgehen darf
(``POSTGRES_ADMIN_USER``/BYPASSRLS, siehe ``docs/ops-runbook.md`` § 9.5);
:class:`core.management.commands.load_package.Command` prüft das vorab
fail-loud (Final-Review A, Refs #1058).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils.translation import gettext_lazy as _

from core.models.document_type import DocumentType
from core.models.document_type_field import DocumentTypeField
from core.models.field_condition import FieldCondition
from core.models.field_group import FieldGroup
from core.models.field_template import FieldTemplate
from core.models.organization import Facility
from core.packages.schema import ConditionDef, DocumentTypeDef, FieldTemplateDef, PackageV1

if TYPE_CHECKING:
    # Refs #1058 (Review-Haertung, mypy-Schaerfe): nur fuer Typannotationen —
    # ``_StrOrPromise`` existiert zur Laufzeit nicht in ``django.utils.functional``
    # (Stub-only-Alias), ``from __future__ import annotations`` macht den Import
    # ohnehin lazy.
    from django.utils.functional import _StrOrPromise

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class ObjectReport:
    """Was mit den Objekten **einer** Art passiert ist — Bezeichner je Bucket."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.created) + len(self.updated) + len(self.unchanged)


@dataclass
class PackageReport:
    """Ergebnis eines Ladelaufs.

    Bezeichner sind sprechend statt technisch (keine UUIDs), damit die
    CLI-Ausgabe direkt auf das Paket zeigt:

    * ``field_templates`` / ``document_types`` → Slug (``"dauer"``)
    * ``groups`` / ``field_refs`` → ``"<typ-slug>/<gruppen- bzw. vorlagen-slug>"``
    * ``conditions`` → ``"<typ-slug>/<ziel><-<quelle>"``

    ``errors``/``warnings`` sind global (nicht je Objektart) und tragen denselben
    Bezeichner als ersten Tupel-Eintrag.
    """

    field_templates: ObjectReport = field(default_factory=ObjectReport)
    document_types: ObjectReport = field(default_factory=ObjectReport)
    groups: ObjectReport = field(default_factory=ObjectReport)
    field_refs: ObjectReport = field(default_factory=ObjectReport)
    conditions: ObjectReport = field(default_factory=ObjectReport)
    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = False

    def sections(self) -> list[tuple[str, ObjectReport]]:
        """Alle Objektarten in Anwendungsreihenfolge — für die CLI-Ausgabe."""
        return [
            ("field_templates", self.field_templates),
            ("document_types", self.document_types),
            ("groups", self.groups),
            ("field_refs", self.field_refs),
            ("conditions", self.conditions),
        ]


class PackageLoadError(Exception):
    """Mindestens ein Objekt konnte nicht angewendet werden — alles rollte zurück.

    ``report`` trägt den vollständigen Report des (verworfenen) Laufs, inklusive
    dem, was ohne die Fehler angelegt/aktualisiert worden wäre.
    """

    def __init__(self, report: PackageReport) -> None:
        self.report = report
        super().__init__("\n".join(f"{identifier}: {message}" for identifier, message in report.errors))


class _DryRunRollbackError(Exception):
    """Interner Sentinel: beendet den Lauf nach dem Report ohne zu schreiben (E15)."""


# ---------------------------------------------------------------------------
# Öffentliche API
# ---------------------------------------------------------------------------


def load_package(facility: Facility, package: PackageV1, *, dry_run: bool = False) -> PackageReport:
    """Spielt ``package`` idempotent in ``facility`` ein.

    :param facility: Ziel-Einrichtung; alle Slugs werden relativ zu ihr aufgelöst.
    :param package: bereits validiertes Paket (siehe
        :func:`core.packages.schema.parse_package`).
    :param dry_run: identischer Code-Pfad, am Ende erzwungener Rollback — der
        Report zeigt exakt das, was ein Echtlauf getan hätte.
    :returns: :class:`PackageReport` mit created/updated/unchanged je Objektart.
    :raises PackageLoadError: sobald **ein** Objekt fehlschlägt; der gesamte
        Lauf wird zurückgerollt (Alles-oder-nichts), der Report hängt an der
        Exception.
    """
    report = PackageReport(dry_run=dry_run)
    try:
        with transaction.atomic():
            _apply_package(facility, package, report)
            if report.errors:
                # Raise INNERHALB des atomic-Blocks — erst dadurch rollt der
                # Block zurück; ein Raise danach käme zu spät (schon committet).
                raise PackageLoadError(report)
            if dry_run:
                raise _DryRunRollbackError
    except _DryRunRollbackError:
        pass
    return report


# ---------------------------------------------------------------------------
# Anwendung je Objektart
# ---------------------------------------------------------------------------


def _apply_package(facility: Facility, package: PackageV1, report: PackageReport) -> None:
    templates = _apply_field_templates(facility, package, report)
    for doc_def in package.document_types:
        doc_type = _apply_document_type(facility, doc_def, report)
        if doc_type is None:
            # Der Typ selbst ist gescheitert — seine Kinder zu verarbeiten
            # brächte nur Folgefehler in den Report (alles rollt ohnehin zurück).
            continue
        groups = _apply_groups(doc_type, doc_def, report)
        fields = _apply_field_refs(doc_type, doc_def, templates, groups, report)
        _apply_conditions(doc_type, doc_def, fields, report)


def _apply_field_templates(facility: Facility, package: PackageV1, report: PackageReport) -> dict[str, FieldTemplate]:
    """Feldvorlagen upserten; liefert die verwendbaren Vorlagen je Slug."""
    templates: dict[str, FieldTemplate] = {}
    for template_def in package.field_templates:
        identifier = template_def.slug
        desired = _desired_template_values(template_def)
        existing = FieldTemplate.objects.filter(facility=facility, slug=template_def.slug).first()

        if existing is None:
            template = FieldTemplate(facility=facility, slug=template_def.slug, **desired)
            if _save(template, report, identifier):
                report.field_templates.created.append(identifier)
                templates[template_def.slug] = template
            continue

        _warn_on_disappearing_options(existing, desired["options_json"], report, identifier)
        _warn_on_field_sensitivity_downgrade(existing, desired["sensitivity"], report, identifier)
        conflicts = _immutable_conflicts(existing, desired, _TEMPLATE_IMMUTABLES)
        if conflicts:
            # Haertung (Refs #1058, Review-Punkt 3): NICHT in die Erfolgs-Map
            # eintragen — DocumentType-Muster (None + Kinder-Skip) gespiegelt.
            # Abhaengige field_refs sehen ``templates.get(slug) is None`` und
            # melden sich selbst als uebersprungen, statt faelschlich als
            # "created" in den Report zu wandern (das rollt ohnehin zurueck,
            # aber der Report soll nur zeigen, was ein Re-Lauf wirklich taete).
            report.errors.extend((identifier, message) for message in conflicts)
            continue
        templates[template_def.slug] = existing
        _apply_diff(existing, desired, report.field_templates, report, identifier)
    return templates


def _apply_document_type(facility: Facility, doc_def: DocumentTypeDef, report: PackageReport) -> DocumentType | None:
    identifier = doc_def.slug
    _warn_on_ignored_keys(doc_def, report, identifier)
    desired: dict[str, Any] = {
        "name": doc_def.name,
        "entity_type": doc_def.entity_type,
        "system_type": doc_def.system_type,
        "sensitivity": doc_def.sensitivity,
        "min_contact_stage": doc_def.min_contact_stage,
        "icon": doc_def.icon,
        "color": doc_def.color,
        "sort_order": doc_def.sort_order,
    }
    existing = DocumentType.objects.filter(facility=facility, slug=doc_def.slug).first()

    if existing is None:
        doc_type = DocumentType(facility=facility, slug=doc_def.slug, **desired)
        if not _save(doc_type, report, identifier):
            return None
        report.document_types.created.append(identifier)
        return doc_type

    conflicts = _immutable_conflicts(existing, desired, _DOCUMENT_TYPE_IMMUTABLES)
    # ``system_type`` ist die Ausnahme von E13: ein Bestandstyp ohne Systemtyp
    # darf einen bekommen (Kompatibilität mit vor-M2-Daten), ein gesetzter darf
    # weder wechseln noch entfernt werden — genau wie ``DocumentType.save()``
    # es selbst erzwingt.
    if existing.system_type and existing.system_type != doc_def.system_type:
        conflicts.append(_immutable_message(_("Systemtyp"), existing.system_type, doc_def.system_type))
    if conflicts:
        report.errors.extend((identifier, message) for message in conflicts)
        return None

    _warn_on_doctype_sensitivity_downgrade(existing, desired["sensitivity"], report, identifier)
    _apply_diff(existing, desired, report.document_types, report, identifier)
    return existing


def _apply_groups(doc_type: DocumentType, doc_def: DocumentTypeDef, report: PackageReport) -> dict[str, FieldGroup]:
    """Feldgruppen upserten (additiv — nicht erwähnte Gruppen bleiben stehen).

    ``sort_order`` wird 1:1 aus dem Paket übernommen: das Format ist deklarativ,
    und der Wert ist nach ADR-036/#1713 die Position des Gruppen-Blocks in der
    **globalen** Block-Sequenz des Typs (die sich Gruppen und ungruppierte
    Felder teilen).
    """
    groups: dict[str, FieldGroup] = {}
    for group_def in doc_def.groups:
        identifier = f"{doc_def.slug}/{group_def.slug}"
        desired: dict[str, Any] = {
            "name": group_def.name,
            "sort_order": group_def.sort_order,
            "is_collapsible": group_def.is_collapsible,
        }
        existing = FieldGroup.objects.filter(document_type=doc_type, slug=group_def.slug).first()
        if existing is None:
            group = FieldGroup(document_type=doc_type, slug=group_def.slug, **desired)
            if _save(group, report, identifier):
                report.groups.created.append(identifier)
                groups[group_def.slug] = group
            continue
        groups[group_def.slug] = existing
        _apply_diff(existing, desired, report.groups, report, identifier)
    return groups


def _apply_field_refs(
    doc_type: DocumentType,
    doc_def: DocumentTypeDef,
    templates: dict[str, FieldTemplate],
    groups: dict[str, FieldGroup],
    report: PackageReport,
) -> dict[str, DocumentTypeField]:
    """Feldzuordnungen upserten; liefert die Zuordnungen je Feldvorlagen-Slug.

    ``sort_order`` ist dual (ADR-036/#1713): innerhalb einer Gruppe die
    Reihenfolge im Block, ohne Gruppe die eigene Position in der globalen
    Block-Sequenz. Der Loader schreibt den deklarierten Wert unverändert.
    """
    # Reserved-Slug-Guard (Refs #1726): hier ist KEIN eigener Guard nötig. Eine
    # ``field_ref`` kann nur eine Vorlage aus ``templates`` zuordnen, und
    # ``templates`` stammt ausschliesslich aus ``package.field_templates`` (via
    # ``_apply_field_templates``); das Paket-Schema (``_parse_field_template``)
    # lehnt reservierte Slugs bereits beim Parsen ab. Ein ``field_ref`` auf einen
    # reservierten Slug fände die Vorlage also nie in ``templates`` und würde als
    # „Feldvorlage konnte nicht angelegt werden" gemeldet — die Zuordnung einer
    # BESTEHENDEN Reserved-Slug-DB-Vorlage ist über diesen Pfad nicht erreichbar.
    fields: dict[str, DocumentTypeField] = {}
    for ref_def in doc_def.field_refs:
        identifier = f"{doc_def.slug}/{ref_def.template_slug}"
        template = templates.get(ref_def.template_slug)
        if template is None:
            report.errors.append(
                (identifier, str(_("Feldvorlage konnte nicht angelegt werden — Zuordnung übersprungen.")))
            )
            continue
        group = None
        if ref_def.group_slug is not None:
            group = groups.get(ref_def.group_slug)
            if group is None:
                report.errors.append(
                    (identifier, str(_("Feldgruppe konnte nicht angelegt werden — Zuordnung übersprungen.")))
                )
                continue

        desired: dict[str, Any] = {"field_group": group, "sort_order": ref_def.sort_order}
        existing = DocumentTypeField.objects.filter(document_type=doc_type, field_template=template).first()
        if existing is None:
            doc_type_field = DocumentTypeField(document_type=doc_type, field_template=template, **desired)
            if _save(doc_type_field, report, identifier):
                report.field_refs.created.append(identifier)
                fields[ref_def.template_slug] = doc_type_field
            continue
        fields[ref_def.template_slug] = existing
        _apply_diff(existing, desired, report.field_refs, report, identifier)
    return fields


def _apply_conditions(
    doc_type: DocumentType,
    doc_def: DocumentTypeDef,
    fields: dict[str, DocumentTypeField],
    report: PackageReport,
) -> None:
    """Feldbedingungen deklarativ-wenn-vorhanden anwenden (E14).

    ``FieldCondition`` hat keinen Natural Key — ein zeilenweiser Upsert wäre
    nicht definierbar. Deshalb: Soll- und Ist-Menge normalisiert vergleichen;
    bei Gleichheit passiert **nichts** (sonst wäre kein Lauf je ``unchanged``),
    sonst alles löschen und neu anlegen.
    """
    if doc_def.conditions is None:
        return

    planned: list[tuple[ConditionDef, DocumentTypeField, DocumentTypeField]] = []
    for condition_def in doc_def.conditions:
        identifier = _condition_identifier(doc_def.slug, condition_def.target, condition_def.depends_on)
        target = fields.get(condition_def.target)
        depends_on = fields.get(condition_def.depends_on)
        if target is None or depends_on is None:
            report.errors.append(
                (identifier, str(_("Ziel- oder Quellfeld konnte nicht zugeordnet werden — Bedingung übersprungen.")))
            )
            continue
        planned.append((condition_def, target, depends_on))

    existing = list(
        FieldCondition.objects.filter(target_field__document_type=doc_type).select_related(
            "target_field__field_template", "depends_on_field__field_template"
        )
    )
    existing_keys = sorted(_existing_condition_key(condition) for condition in existing)
    planned_keys = sorted(_planned_condition_key(condition_def) for condition_def, _t, _d in planned)
    if existing_keys == planned_keys:
        report.conditions.unchanged.extend(
            _condition_identifier(doc_def.slug, condition_def.target, condition_def.depends_on)
            for condition_def, _t, _d in planned
        )
        return

    planned_key_set = set(planned_keys)
    for condition in existing:
        if _existing_condition_key(condition) in planned_key_set:
            continue
        report.warnings.append(
            (
                _condition_identifier(
                    doc_def.slug,
                    condition.target_field.field_template.slug,
                    condition.depends_on_field.field_template.slug,
                ),
                str(_("Bestehende Bedingung wurde durch die Paketangabe ersetzt bzw. entfernt.")),
            )
        )
    FieldCondition.objects.filter(target_field__document_type=doc_type).delete()

    for condition_def, target, depends_on in planned:
        identifier = _condition_identifier(doc_def.slug, condition_def.target, condition_def.depends_on)
        condition = FieldCondition(
            target_field=target,
            depends_on_field=depends_on,
            operator=condition_def.operator,
            value=condition_def.value,
            group_index=condition_def.group_index,
        )
        if _save(condition, report, identifier):
            report.conditions.created.append(identifier)


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------

# E13: nach dem Anlegen unveränderlich. ``slug`` fehlt hier bewusst — er ist
# der Lookup-Key und kann per Definition nicht abweichen.
_TEMPLATE_IMMUTABLES: dict[str, _StrOrPromise] = {"field_type": _("Feldtyp"), "is_encrypted": _("Verschlüsselung")}
_DOCUMENT_TYPE_IMMUTABLES: dict[str, _StrOrPromise] = {"entity_type": _("Entitätstyp")}


def _desired_template_values(template_def: FieldTemplateDef) -> dict[str, Any]:
    """Soll-Werte einer Feldvorlage — inklusive der Modell-Zwänge.

    ``FieldTemplate.save()`` erzwingt ``is_encrypted`` für Datei-Felder und für
    Sensibilität ``high``. Ohne dieselbe Normalisierung hier wäre ein Paket mit
    ``field_type: file`` ohne ``is_encrypted: true`` beim **zweiten** Lauf ein
    Immutabilitäts-Konflikt gegen die eigene erste Anwendung.
    """
    is_encrypted = (
        template_def.is_encrypted
        or template_def.field_type == FieldTemplate.FieldType.FILE
        or template_def.sensitivity == DocumentType.Sensitivity.HIGH
    )
    return {
        "name": template_def.name,
        "field_type": template_def.field_type,
        "is_required": template_def.is_required,
        "is_encrypted": is_encrypted,
        "sensitivity": template_def.sensitivity,
        # Kopien: die Dicts im (frozen) Def dürfen nie mit dem ORM geteilt werden.
        "options_json": [dict(option) for option in template_def.options],
        "help_text": template_def.help_text,
        "default_value": template_def.default_value,
    }


def _immutable_conflicts(
    instance: models.Model, desired: dict[str, Any], immutables: dict[str, _StrOrPromise]
) -> list[str]:
    """Meldungen für alle unveränderlichen Felder, die abweichen würden."""
    conflicts: list[str] = []
    for attribute, label in immutables.items():
        current = getattr(instance, attribute)
        wanted = desired[attribute]
        if current != wanted:
            conflicts.append(_immutable_message(label, current, wanted))
    return conflicts


def _immutable_message(label: Any, current: Any, wanted: Any) -> str:
    return str(
        _("%(label)s ist nach dem Anlegen unveränderlich (Bestand: %(current)r, Paket: %(wanted)r).")
        % {"label": label, "current": current, "wanted": wanted}
    )


def _apply_diff(
    instance: models.Model,
    desired: dict[str, Any],
    section: ObjectReport,
    report: PackageReport,
    identifier: str,
) -> None:
    """Nur bei echter Abweichung schreiben — sonst ``unchanged`` melden."""
    changed = {attribute: value for attribute, value in desired.items() if getattr(instance, attribute) != value}
    if not changed:
        section.unchanged.append(identifier)
        return
    for attribute, value in changed.items():
        setattr(instance, attribute, value)
    if _save(instance, report, identifier):
        section.updated.append(identifier)


def _save(instance: models.Model, report: PackageReport, identifier: str) -> bool:
    """``full_clean()`` + ``save()`` in einem eigenen Savepoint.

    Der Savepoint ist Pflicht: ein ``IntegrityError`` würde die umgebende
    Postgres-Transaktion sonst als *aborted* markieren, und jede weitere
    Abfrage dieses Laufs (auch die harmlosen Lookups) liefe auf einen
    ``InternalError`` statt auf einen sammelbaren Fehler.
    """
    try:
        with transaction.atomic():
            instance.full_clean()
            instance.save()
    except ValidationError as error:
        report.errors.append((identifier, _validation_message(error)))
        return False
    except IntegrityError as error:
        report.errors.append((identifier, str(error).strip()))
        return False
    return True


def _validation_message(error: ValidationError) -> str:
    """``ValidationError`` lesbar machen (feldbezogen wie feldlos)."""
    if hasattr(error, "error_dict"):
        return "; ".join(
            f"{field_name}: {' '.join(messages)}" for field_name, messages in sorted(error.message_dict.items())
        )
    return " ".join(error.messages)


def _warn_on_ignored_keys(doc_def: DocumentTypeDef, report: PackageReport, identifier: str) -> None:
    """Warnen, wenn das Paket Schlüssel setzt, die v1 nicht mehr anwendet (Refs #1749).

    Kein Fehler: Bestands-Pakete sollen weiter laden (ADR-040 — bewusst kein
    ``schema_version``-2-Cutover). Aber auch kein Silent-Drop: wer ``category``
    im Paket pflegt, soll im Report sehen, dass der Wert nichts mehr bewirkt
    und aus dem Paket entfernt werden kann.
    """
    if not doc_def.ignored_keys:
        return
    report.warnings.append(
        (
            identifier,
            str(
                _(
                    "Diese Schlüssel gibt es im Datenmodell nicht mehr und sie werden ignoriert: "
                    "%(keys)s. Sie können aus dem Paket entfernt werden."
                )
                % {"keys": ", ".join(doc_def.ignored_keys)}
            ),
        )
    )


def _warn_on_disappearing_options(
    existing: FieldTemplate, desired_options: list[dict[str, Any]], report: PackageReport, identifier: str
) -> None:
    """Warnen, wenn das Paket bestehende Options-Slugs nicht mehr enthält (E14).

    Kein Fehler: das Paket darf Optionen streichen. Aber bereits erfasste
    Ereignisse tragen den Slug weiter in ``data_json`` und würden ihr Label
    verlieren — deshalb die Empfehlung, ``is_active: false`` zu setzen statt zu
    löschen.
    """
    existing_slugs = {option.get("slug") for option in (existing.options_json or []) if isinstance(option, dict)}
    desired_slugs = {option["slug"] for option in desired_options}
    missing = sorted(slug for slug in existing_slugs - desired_slugs if slug)
    if not missing:
        return
    report.warnings.append(
        (
            identifier,
            str(
                _(
                    "Diese Options-Slugs fehlen im Paket und verschwinden aus der Feldvorlage: %(slugs)s. "
                    "Bereits erfasste Werte behalten den Slug — im Paket besser „is_active: false“ setzen "
                    "statt die Option zu entfernen."
                )
                % {"slugs": ", ".join(missing)}
            ),
        )
    )


def _warn_on_field_sensitivity_downgrade(
    existing: FieldTemplate, desired_sensitivity: str, report: PackageReport, identifier: str
) -> None:
    """Warnen, wenn das Paket die EFFEKTIVE Sensibilität einer Vorlage senkt (Refs #1727).

    Absenkung ist per E13 erlaubt, senkt aber rückwirkend die Lese-Schranke
    bereits erfasster Werte. Maßgeblich ist die **effektive** Sensibilität
    ``effective_sensitivity`` = max(Typ, Feld) — nicht der rohe Feldwert: Frei
    (``""``) heißt „erbt vom Typ", nicht Rang 0. Ein Wechsel ``elevated`` →
    ``""`` an einer Vorlage, die nur an ``elevated``-Typen hängt, senkt effektiv
    **nichts** (Final-Review G Refs #1058 warnte hier fälschlich). Deshalb:
    Warnung nur, wenn für MINDESTENS EINEN referenzierenden Dokumentationstyp die
    effektive Sensibilität sinkt. Ohne Referenz (Vorlage noch nirgends
    zugeordnet) bleibt der konservative Roh-Vergleich (``""`` ⇒ Rang 0).
    """
    # Funktionslokal (Schichtregel): der Loader haengt sonst modulweit am
    # Service-Layer, obwohl er nur die Sensibilitaets-Helfer braucht.
    from core.services.compliance.sensitivity import SENSITIVITY_RANK, effective_sensitivity

    old_field = existing.sensitivity
    new_field = desired_sensitivity
    # Referenzierende Typen (DB-Query im Loader ist ok — er laeuft mit DB-Zugriff
    # und Bypass-Kontext). Distinct auf die Typ-Sensibilitaeten reicht.
    type_sensitivities = list(
        DocumentType.objects.filter(fields__field_template=existing).values_list("sensitivity", flat=True).distinct()
    )
    if type_sensitivities:
        downgraded = any(
            effective_sensitivity(ts, new_field) < effective_sensitivity(ts, old_field) for ts in type_sensitivities
        )
    else:
        ranks: dict[str, int] = {str(level): rank for level, rank in SENSITIVITY_RANK.items()}
        downgraded = ranks.get(new_field, 0) < ranks.get(old_field, 0)
    if downgraded:
        _emit_sensitivity_downgrade_warning(report, identifier, old_field, new_field)


def _warn_on_doctype_sensitivity_downgrade(
    existing: DocumentType, desired_sensitivity: str, report: PackageReport, identifier: str
) -> None:
    """Warnen, wenn das Paket die effektive Sensibilität eines Typs senkt (Refs #1727).

    Die effektive Sensibilität des Typs SELBST ist sein eigener Rang (kein
    Feld-Override): sinkt er, fällt die Ereignis-Sichtbarkeitsschranke, und für
    jedes zugeordnete Feld, das vom Typ erbt (``sensitivity=""``) oder darunter
    liegt, sinkt zusätzlich die effektive Feld-Schranke max(Typ, Feld). Analog
    zum Feld-Pfad über ``effective_sensitivity`` formuliert; ``DocumentType.
    sensitivity`` ist nie ``""``, daher ist der Typ-Rang hier deckungsgleich mit
    dem bisherigen Roh-Vergleich.
    """
    from core.services.compliance.sensitivity import effective_sensitivity

    old_type = existing.sensitivity
    new_type = desired_sensitivity
    # Typ-Ebene (effektiv = eigener Rang) ODER mindestens ein zugeordnetes Feld,
    # dessen effektive Schranke sinkt. Das ``or`` kurzschliesst die Feld-Query,
    # sobald schon die Typ-Ebene sinkt (bzw. bei Anhebung/Gleichstand pruefen wir
    # die Felder — dort kann dann ohnehin nichts sinken).
    type_drops = effective_sensitivity(new_type) < effective_sensitivity(old_type)
    if type_drops or any(
        effective_sensitivity(new_type, fs) < effective_sensitivity(old_type, fs)
        for fs in existing.fields.values_list("field_template__sensitivity", flat=True)
    ):
        _emit_sensitivity_downgrade_warning(report, identifier, old_type, new_type)


def _emit_sensitivity_downgrade_warning(report: PackageReport, identifier: str, current: str, desired: str) -> None:
    """Gemeinsame Warn-Meldung für Feld-/Typ-Sensibilitäts-Absenkung."""
    report.warnings.append(
        (
            identifier,
            str(
                _(
                    "Zugriffsschranke gesenkt: Sensibilität geht von „%(current)s“ auf „%(desired)s“ "
                    "zurück. Bereits erfasste Werte werden damit für zusätzliche Rollen sichtbar."
                )
                % {"current": current or "normal", "desired": desired or "normal"}
            ),
        )
    )


def _condition_identifier(doc_type_slug: str, target: str, depends_on: str) -> str:
    return f"{doc_type_slug}/{target}<-{depends_on}"


def _planned_condition_key(condition_def: ConditionDef) -> tuple[str, str, str, str, int]:
    return (
        condition_def.target,
        condition_def.depends_on,
        condition_def.operator,
        _json_key(condition_def.value),
        condition_def.group_index,
    )


def _existing_condition_key(condition: FieldCondition) -> tuple[str, str, str, str, int]:
    return (
        condition.target_field.field_template.slug,
        condition.depends_on_field.field_template.slug,
        condition.operator,
        _json_key(condition.value),
        condition.group_index,
    )


def _json_key(value: Any) -> str:
    """Stabile, sortierbare Repräsentation eines JSON-Werts für den Mengenvergleich."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)
