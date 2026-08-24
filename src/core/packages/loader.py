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
* **``min_value``/``max_value``** sind echtes Tri-State (Refs #1934, Muster
  ``tally_enabled``): Schlüssel fehlt ⇒ unangetastet (UI-gepflegte Grenze
  überlebt), ``null`` ⇒ bewusst gelöscht, Wert ⇒ gesetzt. **Das ist ein
  dokumentierter Semantikwechsel, kein Bugfix an undokumentiertem Verhalten**
  — ``docs/packages.md`` führte ``min_value``/``max_value`` bis dahin
  ausdrücklich in der Rücksync-Tabelle als "wird zurückgesetzt": vorher zog
  ein erneuter Lauf eine weggelassene Grenze **absichtlich** auf ``NULL``
  zurück, jetzt lässt Weglassen den Bestand unangetastet und nur ein
  explizites ``null`` löscht. Gilt trotzdem **sofort** für jedes bestehende
  v1-Paket, ohne ``schema_version``-Bump, weil der Effekt nachweislich
  einseitig schützend ist: die neue Semantik löscht in **keinem** Fall mehr,
  als die alte gelöscht hätte (Weglassen löscht jetzt nie, wo es vorher immer
  löschte; ein explizites ``null`` löscht unverändert in beiden Fassungen).
  ``default.yaml`` selbst zeigt den Praxis-Impact — ``dauer``/``ausgabe``/
  ``rueckgabe`` führen mindestens eine der beiden Grenzen nicht mit
  (Kompatibilitätsentscheid, Task M1-B2/#1934, Review-Runde 1).
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
from core.packages.schema import KEY_MISSING, ConditionDef, DocumentTypeDef, FieldTemplateDef, PackageV1
from core.services.config import conflicting_report_key_field

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

    ``errors``/``warnings``/``preserved`` sind global (nicht je Objektart) und
    tragen denselben Bezeichner als ersten Tupel-Eintrag.

    ``preserved`` (Refs #1934, Controller-Ruling Review-Runde 1) ist **keine**
    Warnung — bewusst ein eigener Bucket statt eines weiteren Eintrags in
    ``warnings``. Eine Warnung bei jedem fehlenden Tri-State-Schlüssel würde
    jeden gewöhnlichen Lauf zuspammen, bei dem ein Paket Grenzen einfach nicht
    verwaltet (der Normalfall für die meisten Zahlenfelder); eine Warnung "nur
    beim ersten Auftreten" bräuchte eine persistente Erstlauf-Markierung, die
    hier bewusst NICHT eingeführt wird. ``preserved`` listet daher — jeden Lauf
    neu und zustandslos berechnet, ohne Alarm-Charakter — genau die Fälle, in
    denen ein fehlender Schlüssel tatsächlich einen vorhandenen Bestandswert
    erhält, und macht sie damit von "unverändert, weil paket- und
    Bestandswert gleich sind" unterscheidbar, ohne Rauschen für den
    überwiegenden Regelfall (kein Bestandswert vorhanden, nichts zu erhalten).
    """

    field_templates: ObjectReport = field(default_factory=ObjectReport)
    document_types: ObjectReport = field(default_factory=ObjectReport)
    groups: ObjectReport = field(default_factory=ObjectReport)
    field_refs: ObjectReport = field(default_factory=ObjectReport)
    conditions: ObjectReport = field(default_factory=ObjectReport)
    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)
    preserved: list[tuple[str, str]] = field(default_factory=list)
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
        _warn_on_normalized_report_keys(template_def, report, identifier)
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
        _note_preserved_number_range(existing, template_def, report, identifier)
        conflicts = _immutable_conflicts(existing, desired, _TEMPLATE_IMMUTABLES)
        conflicts += _report_key_conflicts(existing, template_def.report_key)
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
    _warn_on_removed_system_types(doc_def, report, identifier)
    _warn_on_normalized_report_keys(doc_def, report, identifier)
    desired: dict[str, Any] = {
        "name": doc_def.name,
        "entity_type": doc_def.entity_type,
        "system_type": doc_def.system_type,
        "sensitivity": doc_def.sensitivity,
        "min_contact_stage": doc_def.min_contact_stage,
    }
    # Refs #1886, E13: Aeltere Pakete ohne Darstellungs-Schluessel duerfen
    # lokale Werte aus der Fach-UI nicht bei jedem Ruecksync auf Modell-
    # Defaults zuruecksetzen. Explizites "" bzw. 0 bleibt dagegen ein Wert.
    if doc_def.icon is not KEY_MISSING:
        desired["icon"] = doc_def.icon
    if doc_def.color is not KEY_MISSING:
        desired["color"] = doc_def.color
    if doc_def.sort_order is not KEY_MISSING:
        desired["sort_order"] = doc_def.sort_order
    # Refs #1637, E13: Fehlt der Schlüssel, bleibt ein im Admin gepflegtes Opt-in erhalten.
    if doc_def.tally_enabled is not None:
        desired["tally_enabled"] = doc_def.tally_enabled
    # E3 (Refs #1849): NUR aufnehmen, wenn das Paket etwas sagt — siehe
    # ``_report_key_conflicts``.
    if doc_def.report_key is not None:
        desired["report_key"] = doc_def.report_key
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
    conflicts += _report_key_conflicts(existing, doc_def.report_key)
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

        # E-J (Refs #1849): Feld-Kardinalitaet je (Typ, Berichtsschluessel) —
        # dritte Schicht neben Formular und Service. Der Paketlauf ist der
        # wahrscheinlichste Entstehungsweg einer Doppelbindung: er ordnet
        # mehrere Vorlagen in einem Zug zu, ohne dass jemand hinsieht.
        konflikt = conflicting_report_key_field(doc_type, template)
        if konflikt is not None:
            report.errors.append(
                (
                    identifier,
                    str(
                        _(
                            "Berichtsschlüssel %(key)r ist an diesem Dokumentationstyp bereits durch die "
                            "Feldvorlage %(bestand)r belegt — je Typ ist höchstens eine Feldvorlage je "
                            "Berichtsschlüssel zulässig."
                        )
                        % {"key": template.report_key, "bestand": konflikt.slug}
                    ),
                )
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
    desired: dict[str, Any] = {
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
    # Refs #1934, Tri-State analog ``tally_enabled`` (E13): NUR aufnehmen, wenn
    # das Paket den Schlüssel überhaupt trägt (``KEY_MISSING`` = fehlt, Bestand
    # bleibt unangetastet). Ein aufgenommenes ``None`` löscht eine bestehende
    # Grenze bewusst — bei der Neuanlage bleibt das Modellfeld dann einfach auf
    # seinem Konstruktor-Default (ebenfalls ``None``), das Verhalten ist also
    # für Neuanlagen deckungsgleich, unabhängig davon, ob der Schlüssel fehlt
    # oder explizit ``null`` ist.
    if template_def.min_value is not KEY_MISSING:
        desired["min_value"] = template_def.min_value
    if template_def.max_value is not KEY_MISSING:
        desired["max_value"] = template_def.max_value
    # E3 (Refs #1849): NUR aufnehmen, wenn das Paket etwas sagt — siehe
    # ``_report_key_conflicts``.
    if template_def.report_key is not None:
        desired["report_key"] = template_def.report_key
    return desired


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


def _report_key_conflicts(instance: models.Model, gewuenscht: str | None) -> list[str]:
    """E13 fuer den Berichtsschluessel (E3, Refs #1849, Konzept #1847 §2.1).

    **leer→gesetzt erlaubt, gesetzt→anders Fehler.** Dieselbe Regel wie am
    Modell-Guard, nur mit lesbarer Report-Meldung statt ``ValidationError``.

    Ein FEHLENDER (bzw. ``null``) Schluessel im Paket heisst „keine Angabe" und
    laesst den Bestand unangetastet — bewusst anders als bei ``system_type``,
    wo ein fehlender Schluessel gegen einen gesetzten Bestandswert ein Fehler
    ist. Der Grund ist der Zeitpunkt: ``system_type`` gibt es seit Schema v1,
    ``report_key`` kommt mit E3 dazu, und der Backfill vergibt ihn an
    **Bestandszeilen**. Waere das Weglassen ein Fehler, machte E3 jedes bereits
    veroeffentlichte Fremdpaket gegen eine aktualisierte Instanz unladbar —
    ohne dass das Paket etwas falsch macht. Ein Entfernen laesst sich damit
    nicht ausdruecken; das ist kein Verlust, weil es ohnehin verboten ist.

    ``getattr`` mit Default: die Funktion laeuft gegen beide Traeger.
    """
    bestand = getattr(instance, "report_key", None)
    if gewuenscht is None or not bestand or bestand == gewuenscht:
        return []
    return [_immutable_message(_("Berichtsschlüssel"), bestand, gewuenscht)]


def _warn_on_removed_system_types(doc_def: DocumentTypeDef, report: PackageReport, identifier: str) -> None:
    """Warnen, wenn ein entfallener ``system_type`` weggelassen wurde (E4, Refs #1850).

    Kein Fehler — das Alt-Paket bleibt ladbar (Werte-Ebenen-Toleranz, Konzept
    #1847 §E3.8 auf der Systemtyp-Achse). Aber auch kein stiller Wegfall: wer
    ``system_type: medical`` pflegt, soll im Report lesen, dass der Wert nicht
    angekommen ist und wo die Zuordnung heute steht. Dieselbe Linie wie
    :func:`_warn_on_ignored_keys` und
    :func:`_warn_on_normalized_report_keys`.
    """
    for alt in doc_def.removed_system_types:
        report.warnings.append(
            (
                identifier,
                str(
                    _(
                        "Der Systemtyp %(alt)r ist entfallen und wurde nicht übernommen — die "
                        "Berichtszuordnung trägt seither der Berichtsschlüssel (report_key)."
                    )
                    % {"alt": alt}
                ),
            )
        )


def _warn_on_normalized_report_keys(definition, report: PackageReport, identifier: str) -> None:
    """Warnen, wenn ein „ehemals gueltiger" Berichtswert uebersetzt wurde (Refs #1849).

    Kein Fehler — das Paket bleibt ladbar (Werte-Ebenen-Toleranz, Konzept #1847
    §E3.8). Aber auch kein Silent-Rewrite: wer ``report_key: note`` pflegt,
    soll im Report sehen, dass daraus der Sentinel geworden ist. Dieselbe Linie
    wie :func:`_warn_on_ignored_keys`.
    """
    for alt, neu in definition.normalized_report_keys:
        report.warnings.append(
            (
                identifier,
                str(
                    _(
                        "Der Berichtsschlüssel %(alt)r ist ein ehemals gültiger Wert und wurde auf "
                        "%(neu)r normalisiert. Im Paket direkt %(neu)r eintragen."
                    )
                    % {"alt": alt, "neu": neu}
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


def _note_preserved_number_range(
    existing: FieldTemplate, template_def: FieldTemplateDef, report: PackageReport, identifier: str
) -> None:
    """Betreiber-Sichtbarkeit für den Tri-State (Refs #1934, Controller-Ruling
    Review-Runde 1) — bewusst **keine** Warnung.

    Eine Warnung "Schlüssel fehlt" würde jeden Lauf zuspammen, bei dem ein
    Paket Grenzen einfach nicht verwaltet — der Normalfall für die meisten
    Zahlenfelder, kein Missstand. Diese Funktion trägt deshalb in
    :attr:`PackageReport.preserved` ein, **nur** wenn dadurch tatsächlich ein
    vorhandener Bestandswert erhalten bleibt (Schlüssel fehlt im Paket UND der
    Bestand trägt bereits eine Grenze) — kein Eintrag, wenn ohnehin nichts zu
    erhalten ist (Bestand schon ``None``) oder das Paket den Schlüssel führt
    (dann ist es ein normaler ``unverändert``/``aktualisiert``-Fall, kein
    "unangetastet, weil unerwähnt").
    """
    preserved_bits = []
    if template_def.min_value is KEY_MISSING and existing.min_value is not None:
        preserved_bits.append(f"min_value={existing.min_value!r}")
    if template_def.max_value is KEY_MISSING and existing.max_value is not None:
        preserved_bits.append(f"max_value={existing.max_value!r}")
    if not preserved_bits:
        return
    report.preserved.append(
        (
            identifier,
            str(
                _("Unverändert, weil das Paket den Schlüssel nicht führt — Bestand bleibt: %(werte)s.")
                % {"werte": ", ".join(preserved_bits)}
            ),
        )
    )


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
