"""Paketformat v1 — strikte, DB-freie Validierung (M2 P10, Refs #1058).

:func:`parse_package` nimmt ein bereits geparstes ``dict`` (YAML/JSON) und
liefert einen validierten, unveränderlichen :class:`PackageV1`-Baum. Fremder
Input wird **strikt** behandelt: unbekannte Schlüssel auf jeder Ebene sind ein
Fehler (Tippfehler wie ``sort_oder`` sollen auffallen statt still zu
verpuffen), Enum-Werte werden gegen die **echten** Modell-Choices geprüft, und
alle Verstöße werden gesammelt und mit ihrem **Pfad** gemeldet
(``document_types[2].field_refs[0].template_slug``) — eine CLI-Ausgabe soll
direkt auf die Zeile im YAML zeigen.

Bewusst ohne ``jsonschema``-Dependency: die Regeln hier sind zur Hälfte
Cross-Referenzen (Slug-Verweise zwischen den Abschnitten) und Fach-Invarianten
(E8: Datei-Felder nur an Ereignis-Typen), die ein JSON-Schema ohnehin nicht
ausdrücken könnte.

Pflicht sind nur Identität und die Werte ohne sinnvollen Modell-Default:
``slug``/``name`` (bzw. ``template_slug``, ``target``/``depends_on``/
``operator``). Alles andere ist optional und fällt auf den **Modell-Default**
zurück — ein Paket bleibt damit klein und lesbar.

Referenzformat siehe ``docs``/Brief; Kurzfassung:

.. code-block:: yaml

    schema_version: 1
    field_templates:
      - {slug: dauer, name: Dauer, field_type: number}
    document_types:
      - slug: kontakt
        name: Kontakt
        entity_type: event
        groups: [{slug: basisdaten, name: Basisdaten, sort_order: 0}]
        field_refs: [{template_slug: dauer, group_slug: basisdaten, sort_order: 0}]
        conditions: [{target: notiz, depends_on: dauer, operator: greater_than, value: 10}]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.constants import CONTACT_STAGE_CHOICES
from core.models.document_type import _SLUG_RE, DocumentType, FieldTemplate
from core.models.field_condition import FieldCondition

SCHEMA_VERSION = 1

# --- Erlaubte Werte: direkt aus den Modell-Choices, nie handgepflegt ---------
_FIELD_TYPES: frozenset[str] = frozenset(FieldTemplate.FieldType.values)
_CATEGORIES: frozenset[str] = frozenset(DocumentType.Category.values)
_SENSITIVITIES: frozenset[str] = frozenset(DocumentType.Sensitivity.values)
# FieldTemplate.sensitivity ist ``blank=True`` ("" = erbt vom Dokumentationstyp),
# DocumentType.sensitivity nicht — daher zwei Mengen.
_TEMPLATE_SENSITIVITIES: frozenset[str] = _SENSITIVITIES | {""}
_SYSTEM_TYPES: frozenset[str] = frozenset(DocumentType.SystemType.values)
_ENTITY_TYPES: frozenset[str] = frozenset(DocumentType.EntityType.values)
_OPERATORS: frozenset[str] = frozenset(FieldCondition.Operator.values)
_CONTACT_STAGES: frozenset[str] = frozenset(value for value, _label in CONTACT_STAGE_CHOICES)
# Nur diese Feldtypen tragen Auswahloptionen.
_OPTION_FIELD_TYPES: frozenset[str] = frozenset(
    {FieldTemplate.FieldType.SELECT.value, FieldTemplate.FieldType.MULTI_SELECT.value}
)
# E8: Datei-Felder gibt es in v1 nur an Ereignis-Typen (Person/Fall haben keine
# Upload-Pipeline) — Schicht 1 der Absicherung, Schicht 2 liegt im Config-Service.
_NON_FILE_ENTITY_TYPES: frozenset[str] = frozenset(
    {DocumentType.EntityType.CLIENT.value, DocumentType.EntityType.CASE.value}
)

_PACKAGE_KEYS = frozenset({"schema_version", "field_templates", "document_types"})
_FIELD_TEMPLATE_KEYS = frozenset(
    {
        "slug",
        "name",
        "field_type",
        "is_required",
        "is_encrypted",
        "sensitivity",
        "options",
        "help_text",
        "default_value",
    }
)
_OPTION_KEYS = frozenset({"slug", "label", "is_active"})
_DOCUMENT_TYPE_KEYS = frozenset(
    {
        "slug",
        "name",
        "entity_type",
        "category",
        "system_type",
        "sensitivity",
        "min_contact_stage",
        "icon",
        "color",
        "sort_order",
        "groups",
        "field_refs",
        "conditions",
    }
)
_GROUP_KEYS = frozenset({"slug", "name", "sort_order", "is_collapsible"})
_FIELD_REF_KEYS = frozenset({"template_slug", "group_slug", "sort_order"})
_CONDITION_KEYS = frozenset({"target", "depends_on", "operator", "value", "group_index"})


# ---------------------------------------------------------------------------
# Fehler
# ---------------------------------------------------------------------------


class PackageValidationError(Exception):
    """Sammelfehler der Schema-Validierung.

    ``errors`` ist eine Liste aus ``(Pfad, Meldung)`` in Fundreihenfolge; die
    Exception-Message ist dieselbe Liste zeilenweise formatiert (direkt
    CLI-tauglich).
    """

    def __init__(self, errors: list[tuple[str, str]]) -> None:
        self.errors: list[tuple[str, str]] = list(errors)
        super().__init__("\n".join(f"{path}: {message}" for path, message in self.errors))


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldTemplateDef:
    """Eine Feldvorlage. Lookup-Key beim Laden ist ``(facility, slug)``.

    ``options`` spiegelt exakt die ``FieldTemplate.options_json``-Struktur
    (``{"slug": …, "label": …, "is_active": …}``) — der Loader kann sie 1:1
    schreiben. Die inneren Dicts sind normalisiert (``is_active`` immer
    gesetzt) und werden vom Loader kopiert, nie geteilt.
    """

    slug: str
    name: str
    field_type: str = FieldTemplate.FieldType.TEXT.value
    is_required: bool = False
    is_encrypted: bool = False
    sensitivity: str = ""
    options: tuple[dict[str, Any], ...] = ()
    help_text: str = ""
    default_value: str = ""


@dataclass(frozen=True)
class GroupDef:
    """Eine Feldgruppe. Lookup-Key beim Laden ist ``(document_type, slug)``.

    ``sort_order`` ist die Position des Gruppen-Blocks in der **globalen**
    Block-Sequenz des Dokumentationstyps (ADR-036/#1713).
    """

    slug: str
    name: str
    sort_order: int = 0
    is_collapsible: bool = False


@dataclass(frozen=True)
class FieldRefDef:
    """Zuordnung einer Feldvorlage zu einem Dokumentationstyp.

    Lookup-Key beim Laden ist ``(document_type, field_template)``.
    ``sort_order`` hat die Dual-Semantik aus ADR-036/#1713: mit
    ``group_slug`` die Reihenfolge **innerhalb** der Gruppe, ohne
    ``group_slug`` die eigene Position in der globalen Block-Sequenz.
    """

    template_slug: str
    group_slug: str | None = None
    sort_order: int = 0


@dataclass(frozen=True)
class ConditionDef:
    """Eine Feldbedingung ("zeige ``target``, wenn ``depends_on`` <op> ``value``").

    ``target``/``depends_on`` sind **Feldvorlagen-Slugs** (nicht Feld-IDs) —
    :class:`~core.models.field_condition.FieldCondition` hat keinen Natural
    Key, deshalb ersetzt der Loader Bedingungen immer im Ganzen (E14).
    ``value`` ist JSON-typisiert (Zahl/Bool/String), nicht String-serialisiert.
    """

    target: str
    depends_on: str
    operator: str
    value: Any = None
    group_index: int = 0


@dataclass(frozen=True)
class DocumentTypeDef:
    """Ein Dokumentationstyp. Lookup-Key beim Laden ist ``(facility, slug)``.

    ``conditions is None`` bedeutet: der Schlüssel fehlte im Paket — der
    Loader lässt vorhandene Bedingungen dann **unangetastet** (E14). Eine
    leere Liste (``()``) ist dagegen die explizite Ansage "keine Bedingungen".
    """

    slug: str
    name: str
    entity_type: str = DocumentType.EntityType.EVENT.value
    category: str = DocumentType.Category.CONTACT.value
    system_type: str | None = None
    sensitivity: str = DocumentType.Sensitivity.NORMAL.value
    min_contact_stage: str | None = None
    icon: str = ""
    color: str = ""
    sort_order: int = 0
    groups: tuple[GroupDef, ...] = ()
    field_refs: tuple[FieldRefDef, ...] = ()
    conditions: tuple[ConditionDef, ...] | None = None


@dataclass(frozen=True)
class PackageV1:
    """Validiertes Paket der Schemaversion 1."""

    schema_version: int = SCHEMA_VERSION
    field_templates: tuple[FieldTemplateDef, ...] = ()
    document_types: tuple[DocumentTypeDef, ...] = ()


# ---------------------------------------------------------------------------
# Primitive Prüfhelfer — alle sammeln in ``errors`` statt zu werfen
# ---------------------------------------------------------------------------

Errors = list[tuple[str, str]]


def _check_keys(
    data: dict[str, Any], path: str, allowed: frozenset[str], required: frozenset[str], errors: Errors
) -> None:
    """Unbekannte Schlüssel und fehlende Pflichtschlüssel melden."""
    for key in data:
        if key not in allowed:
            errors.append((_join(path, str(key)), "Unbekannter Schlüssel."))
    for key in sorted(required - set(data)):
        errors.append((_join(path, key), "Pflichtangabe fehlt."))


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _mapping(value: Any, path: str, errors: Errors) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append((path, "Erwartet wird ein Mapping."))
        return None
    return value


def _sequence(value: Any, path: str, errors: Errors) -> list[Any] | None:
    # ``str`` ist zwar iterierbar, aber hier nie gemeint.
    if not isinstance(value, list):
        errors.append((path, "Erwartet wird eine Liste."))
        return None
    return value


def _str_value(data: dict[str, Any], key: str, path: str, errors: Errors, *, default: str = "") -> str:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, str):
        errors.append((_join(path, key), "Erwartet wird eine Zeichenkette."))
        return default
    return value


def _required_name(data: dict[str, Any], key: str, path: str, errors: Errors) -> str:
    """Pflicht-Freitext (Name/Label): Zeichenkette mit sichtbarem Inhalt."""
    if key not in data:
        return ""
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        errors.append((_join(path, key), "Erwartet wird eine nicht-leere Zeichenkette."))
        return ""
    return value


def _bool_value(data: dict[str, Any], key: str, path: str, errors: Errors, *, default: bool = False) -> bool:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, bool):
        errors.append((_join(path, key), "Erwartet wird ein Wahrheitswert (true/false)."))
        return default
    return value


def _int_value(
    data: dict[str, Any], key: str, path: str, errors: Errors, *, default: int = 0, minimum: int | None = None
) -> int:
    if key not in data:
        return default
    value = data[key]
    # ``bool`` ist in Python ein ``int`` — hier nie gemeint.
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append((_join(path, key), "Erwartet wird eine ganze Zahl."))
        return default
    if minimum is not None and value < minimum:
        errors.append((_join(path, key), f"Wert muss >= {minimum} sein."))
        return default
    return value


def _enum_value(
    data: dict[str, Any], key: str, path: str, errors: Errors, allowed: frozenset[str], *, default: str
) -> str:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, str) or value not in allowed:
        errors.append((_join(path, key), f"Unzulässiger Wert — erlaubt: {_allowed(allowed)}."))
        return default
    return value


def _optional_enum_value(
    data: dict[str, Any], key: str, path: str, errors: Errors, allowed: frozenset[str]
) -> str | None:
    """Wie :func:`_enum_value`, aber ``null``/fehlend ist zulässig."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        errors.append((_join(path, key), f"Unzulässiger Wert — erlaubt: null, {_allowed(allowed)}."))
        return None
    return value


def _allowed(allowed: frozenset[str]) -> str:
    return ", ".join(repr(value) for value in sorted(allowed))


def _slug_value(data: dict[str, Any], key: str, path: str, errors: Errors) -> str | None:
    """Pflicht-Slug: Zeichenkette im ``_SLUG_RE``-Format. ``None`` = unbrauchbar."""
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, str) or not _SLUG_RE.fullmatch(value):
        errors.append((_join(path, key), "Ungültiger Slug — erlaubt sind a-z, 0-9 und Bindestriche."))
        return None
    return value


def _optional_slug_value(data: dict[str, Any], key: str, path: str, errors: Errors) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not _SLUG_RE.fullmatch(value):
        errors.append((_join(path, key), "Ungültiger Slug — erlaubt sind a-z, 0-9 und Bindestriche."))
        return None
    return value


# ---------------------------------------------------------------------------
# Abschnitts-Parser — liefern ``None`` für unbrauchbare Einträge
# ---------------------------------------------------------------------------


def _parse_option(raw: Any, path: str, errors: Errors) -> dict[str, Any] | None:
    data = _mapping(raw, path, errors)
    if data is None:
        return None
    _check_keys(data, path, _OPTION_KEYS, frozenset({"slug", "label"}), errors)
    slug = _slug_value(data, "slug", path, errors)
    label = _required_name(data, "label", path, errors)
    is_active = _bool_value(data, "is_active", path, errors, default=True)
    if slug is None:
        return None
    return {"slug": slug, "label": label, "is_active": is_active}


def _parse_field_template(raw: Any, path: str, errors: Errors) -> FieldTemplateDef | None:
    data = _mapping(raw, path, errors)
    if data is None:
        return None
    _check_keys(data, path, _FIELD_TEMPLATE_KEYS, frozenset({"slug", "name"}), errors)
    slug = _slug_value(data, "slug", path, errors)
    name = _required_name(data, "name", path, errors)
    field_type = _enum_value(data, "field_type", path, errors, _FIELD_TYPES, default=FieldTemplate.FieldType.TEXT.value)
    is_required = _bool_value(data, "is_required", path, errors)
    is_encrypted = _bool_value(data, "is_encrypted", path, errors)
    sensitivity = _enum_value(data, "sensitivity", path, errors, _TEMPLATE_SENSITIVITIES, default="")
    help_text = _str_value(data, "help_text", path, errors)
    default_value = _str_value(data, "default_value", path, errors)

    options: list[dict[str, Any]] = []
    raw_options = data.get("options")
    if raw_options is not None:
        entries = _sequence(raw_options, _join(path, "options"), errors)
        if entries is not None:
            seen: set[str] = set()
            for index, raw_option in enumerate(entries):
                option_path = f"{_join(path, 'options')}[{index}]"
                option = _parse_option(raw_option, option_path, errors)
                if option is None:
                    continue
                if option["slug"] in seen:
                    errors.append((_join(option_path, "slug"), f"Doppelter Options-Slug {option['slug']!r}."))
                    continue
                seen.add(option["slug"])
                options.append(option)
            if entries and field_type not in _OPTION_FIELD_TYPES:
                errors.append(
                    (
                        _join(path, "options"),
                        "Optionen sind nur für die Feldtypen 'select' und 'multi_select' zulässig.",
                    )
                )

    # Spiegelt ``FieldTemplate._validate_high_sensitivity_requires_encryption``
    # früh: HIGH-Felder ohne Verschlüsselung würden beim Laden ohnehin am
    # Modell-``clean()`` scheitern — als Schema-Fehler ist die Meldung
    # pfadgenau statt als Sammelfehler des Loaders.
    if sensitivity == DocumentType.Sensitivity.HIGH.value and not is_encrypted:
        errors.append((_join(path, "is_encrypted"), "Felder mit Sensibilität 'high' müssen verschlüsselt sein."))

    # (a) Haertung Refs #1058: FILE-Felder sind immer verschluesselt
    # (``FieldTemplate.save()`` erzwingt das). Ein FEHLENDER Key wird vom
    # Loader stillschweigend nach oben normalisiert (Kompatibilitaet), aber
    # ein EXPLIZITES ``is_encrypted: false`` ist ein Widerspruch im Paket
    # selbst und soll hier pfadgenau auffallen statt erst beim zweiten Lauf
    # als Immutable-Konflikt gegen die eigene erste Anwendung.
    if field_type == FieldTemplate.FieldType.FILE.value and data.get("is_encrypted") is False:
        errors.append(
            (_join(path, "is_encrypted"), "Felder vom Typ 'file' sind immer verschlüsselt — 'false' ist unzulässig.")
        )

    if slug is None:
        return None
    return FieldTemplateDef(
        slug=slug,
        name=name,
        field_type=field_type,
        is_required=is_required,
        is_encrypted=is_encrypted,
        sensitivity=sensitivity,
        options=tuple(options),
        help_text=help_text,
        default_value=default_value,
    )


def _parse_group(raw: Any, path: str, errors: Errors) -> GroupDef | None:
    data = _mapping(raw, path, errors)
    if data is None:
        return None
    _check_keys(data, path, _GROUP_KEYS, frozenset({"slug", "name"}), errors)
    slug = _slug_value(data, "slug", path, errors)
    name = _required_name(data, "name", path, errors)
    sort_order = _int_value(data, "sort_order", path, errors)
    is_collapsible = _bool_value(data, "is_collapsible", path, errors)
    if slug is None:
        return None
    return GroupDef(slug=slug, name=name, sort_order=sort_order, is_collapsible=is_collapsible)


def _parse_field_ref(raw: Any, path: str, errors: Errors) -> FieldRefDef | None:
    data = _mapping(raw, path, errors)
    if data is None:
        return None
    _check_keys(data, path, _FIELD_REF_KEYS, frozenset({"template_slug"}), errors)
    template_slug = _slug_value(data, "template_slug", path, errors)
    group_slug = _optional_slug_value(data, "group_slug", path, errors)
    sort_order = _int_value(data, "sort_order", path, errors)
    if template_slug is None:
        return None
    return FieldRefDef(template_slug=template_slug, group_slug=group_slug, sort_order=sort_order)


def _parse_condition(raw: Any, path: str, errors: Errors) -> ConditionDef | None:
    data = _mapping(raw, path, errors)
    if data is None:
        return None
    _check_keys(data, path, _CONDITION_KEYS, frozenset({"target", "depends_on", "operator"}), errors)
    target = _slug_value(data, "target", path, errors)
    depends_on = _slug_value(data, "depends_on", path, errors)
    operator = _enum_value(data, "operator", path, errors, _OPERATORS, default="")
    group_index = _int_value(data, "group_index", path, errors, minimum=0)
    if target is None or depends_on is None:
        return None
    if target == depends_on:
        errors.append((_join(path, "depends_on"), "Ziel- und Quellfeld müssen unterschiedlich sein."))
        return None
    return ConditionDef(
        target=target,
        depends_on=depends_on,
        operator=operator,
        value=data.get("value"),
        group_index=group_index,
    )


def _parse_document_type(raw: Any, path: str, errors: Errors) -> DocumentTypeDef | None:
    data = _mapping(raw, path, errors)
    if data is None:
        return None
    _check_keys(data, path, _DOCUMENT_TYPE_KEYS, frozenset({"slug", "name"}), errors)
    slug = _slug_value(data, "slug", path, errors)
    name = _required_name(data, "name", path, errors)
    entity_type = _enum_value(
        data, "entity_type", path, errors, _ENTITY_TYPES, default=DocumentType.EntityType.EVENT.value
    )
    category = _enum_value(data, "category", path, errors, _CATEGORIES, default=DocumentType.Category.CONTACT.value)
    sensitivity = _enum_value(
        data, "sensitivity", path, errors, _SENSITIVITIES, default=DocumentType.Sensitivity.NORMAL.value
    )
    system_type = _optional_enum_value(data, "system_type", path, errors, _SYSTEM_TYPES)
    min_contact_stage = _optional_enum_value(data, "min_contact_stage", path, errors, _CONTACT_STAGES)
    icon = _str_value(data, "icon", path, errors)
    color = _str_value(data, "color", path, errors)
    sort_order = _int_value(data, "sort_order", path, errors)

    # ``system_type`` steuert Bann-Logik/Krisen-Highlight/Jugendamt-Export —
    # alles Ereignis-Semantik. An Person-/Fall-Typen wäre er bedeutungslos.
    if system_type is not None and entity_type != DocumentType.EntityType.EVENT.value:
        errors.append(
            (
                _join(path, "system_type"),
                "Ein Systemtyp ist nur für Dokumentationstypen mit entity_type 'event' zulässig.",
            )
        )

    groups = _parse_group_list(data, path, errors)
    field_refs = _parse_field_ref_list(data, path, errors)
    conditions = _parse_condition_list(data, path, errors)

    if slug is None:
        return None
    return DocumentTypeDef(
        slug=slug,
        name=name,
        entity_type=entity_type,
        category=category,
        system_type=system_type,
        sensitivity=sensitivity,
        min_contact_stage=min_contact_stage,
        icon=icon,
        color=color,
        sort_order=sort_order,
        groups=groups,
        field_refs=field_refs,
        conditions=conditions,
    )


def _parse_group_list(data: dict[str, Any], path: str, errors: Errors) -> tuple[GroupDef, ...]:
    raw_groups = data.get("groups")
    if raw_groups is None:
        return ()
    entries = _sequence(raw_groups, _join(path, "groups"), errors)
    if entries is None:
        return ()
    groups: list[GroupDef] = []
    seen: set[str] = set()
    for index, raw_group in enumerate(entries):
        group_path = f"{_join(path, 'groups')}[{index}]"
        group = _parse_group(raw_group, group_path, errors)
        if group is None:
            continue
        if group.slug in seen:
            errors.append((_join(group_path, "slug"), f"Doppelter Gruppen-Slug {group.slug!r}."))
            continue
        seen.add(group.slug)
        groups.append(group)
    return tuple(groups)


def _parse_field_ref_list(data: dict[str, Any], path: str, errors: Errors) -> tuple[FieldRefDef, ...]:
    raw_refs = data.get("field_refs")
    if raw_refs is None:
        return ()
    entries = _sequence(raw_refs, _join(path, "field_refs"), errors)
    if entries is None:
        return ()
    refs: list[FieldRefDef] = []
    seen: set[str] = set()
    for index, raw_ref in enumerate(entries):
        ref_path = f"{_join(path, 'field_refs')}[{index}]"
        ref = _parse_field_ref(raw_ref, ref_path, errors)
        if ref is None:
            continue
        if ref.template_slug in seen:
            errors.append(
                (_join(ref_path, "template_slug"), f"Feldvorlage {ref.template_slug!r} ist mehrfach zugeordnet.")
            )
            continue
        seen.add(ref.template_slug)
        refs.append(ref)
    return tuple(refs)


def _parse_condition_list(data: dict[str, Any], path: str, errors: Errors) -> tuple[ConditionDef, ...] | None:
    # Fehlender Schlüssel (``None``) heißt "nicht anfassen" — nicht "leeren".
    if "conditions" not in data:
        return None
    entries = _sequence(data["conditions"], _join(path, "conditions"), errors)
    if entries is None:
        return None
    conditions: list[ConditionDef] = []
    for index, raw_condition in enumerate(entries):
        condition_path = f"{_join(path, 'conditions')}[{index}]"
        condition = _parse_condition(raw_condition, condition_path, errors)
        if condition is not None:
            conditions.append(condition)
    return tuple(conditions)


# ---------------------------------------------------------------------------
# Cross-Checks über die Abschnitte hinweg
# ---------------------------------------------------------------------------


def _cross_check(
    templates: list[FieldTemplateDef | None], doc_types: list[DocumentTypeDef | None], errors: Errors
) -> None:
    """Slug-Verweise und typübergreifende Fach-Invarianten prüfen.

    Arbeitet auf den Listen **mit** ``None``-Platzhaltern, damit die
    Fehlerpfade die Original-Indizes des Pakets tragen.
    """
    types_by_slug: dict[str, FieldTemplateDef] = {}
    # (c) Haertung Refs #1058: Namensdubletten sind ein eigener Fehler, unabhaengig
    # vom (per Definition eindeutigen) Slug — zwei Feldvorlagen mit demselben
    # ``name`` waeren in der UI (Dropdown/Config-Liste) nicht unterscheidbar.
    names_by_template: set[str] = set()
    for index, template in enumerate(templates):
        if template is None:
            continue
        if template.slug in types_by_slug:
            errors.append((f"field_templates[{index}].slug", f"Doppelter Slug {template.slug!r}."))
            continue
        types_by_slug[template.slug] = template
        if template.name in names_by_template:
            errors.append((f"field_templates[{index}].name", f"Doppelter Name {template.name!r}."))
        else:
            names_by_template.add(template.name)

    seen_doc_types: set[str] = set()
    # (c) Dokumentationstypen sind ueber (name, category) eindeutig — dieselbe
    # Paarung waere in der Typ-Auswahl (nach Kategorie gruppiert) nicht
    # unterscheidbar, derselbe Name in unterschiedlichen Kategorien ist ok.
    seen_name_category: set[tuple[str, str]] = set()
    for index, doc_type in enumerate(doc_types):
        if doc_type is None:
            continue
        path = f"document_types[{index}]"
        if doc_type.slug in seen_doc_types:
            errors.append((f"{path}.slug", f"Doppelter Slug {doc_type.slug!r}."))
            continue
        seen_doc_types.add(doc_type.slug)
        name_category = (doc_type.name, doc_type.category)
        if name_category in seen_name_category:
            errors.append((f"{path}.name", f"Doppelter Name {doc_type.name!r} in Kategorie {doc_type.category!r}."))
        else:
            seen_name_category.add(name_category)
        _cross_check_document_type(doc_type, path, types_by_slug, errors)


def _cross_check_document_type(
    doc_type: DocumentTypeDef, path: str, templates_by_slug: dict[str, FieldTemplateDef], errors: Errors
) -> None:
    group_slugs = {group.slug for group in doc_type.groups}
    referenced: set[str] = set()

    for index, ref in enumerate(doc_type.field_refs):
        ref_path = f"{path}.field_refs[{index}]"
        template = templates_by_slug.get(ref.template_slug)
        if template is None:
            errors.append(
                (
                    f"{ref_path}.template_slug",
                    f"Unbekannte Feldvorlage {ref.template_slug!r} — sie muss unter 'field_templates' deklariert sein.",
                )
            )
            continue
        referenced.add(ref.template_slug)
        if ref.group_slug is not None and ref.group_slug not in group_slugs:
            errors.append(
                (
                    f"{ref_path}.group_slug",
                    f"Unbekannte Feldgruppe {ref.group_slug!r} — sie muss unter 'groups' dieses Typs deklariert sein.",
                )
            )
        if template.field_type == FieldTemplate.FieldType.FILE.value and doc_type.entity_type in _NON_FILE_ENTITY_TYPES:
            errors.append(
                (
                    f"{ref_path}.template_slug",
                    "Datei-Felder sind nur für Dokumentationstypen mit entity_type 'event' zulässig.",
                )
            )

    for index, condition in enumerate(doc_type.conditions or ()):
        condition_path = f"{path}.conditions[{index}]"
        for key, slug in (("target", condition.target), ("depends_on", condition.depends_on)):
            if slug not in referenced:
                errors.append(
                    (
                        f"{condition_path}.{key}",
                        f"Feld {slug!r} ist diesem Dokumentationstyp nicht zugeordnet (siehe 'field_refs').",
                    )
                )


# ---------------------------------------------------------------------------
# Öffentliche API
# ---------------------------------------------------------------------------


def parse_package(raw: dict[str, Any]) -> PackageV1:
    """Validiert ein Paket-Dict und liefert den ``PackageV1``-Baum.

    :param raw: bereits geparstes Mapping (YAML/JSON) — dieses Modul parst
        selbst **kein** YAML.
    :raises PackageValidationError: bei mindestens einem Verstoß; die
        Exception trägt **alle** gefundenen Verstöße als ``(Pfad, Meldung)``.
    """
    errors: Errors = []
    if not isinstance(raw, dict):
        raise PackageValidationError([("<root>", "Erwartet wird ein Mapping auf oberster Ebene.")])

    _check_keys(raw, "", _PACKAGE_KEYS, frozenset({"schema_version"}), errors)
    version = raw.get("schema_version")
    if "schema_version" in raw and version != SCHEMA_VERSION:
        errors.append(("schema_version", f"Nicht unterstützte Schemaversion {version!r} — erwartet: {SCHEMA_VERSION}."))

    # (b) Haertung Refs #1058: FEHLENDER Schluessel bleibt "kein Eintrag"
    # (Default), aber ein explizites ``null`` ist ein Typfehler — Muster
    # ``_parse_condition_list``: nur "Key fehlt" heisst "nicht anfassen",
    # "Key da, aber kein Wert" ist ein Verstoss gegen "Erwartet wird eine Liste".
    templates: list[FieldTemplateDef | None] = []
    if "field_templates" in raw:
        entries = _sequence(raw["field_templates"], "field_templates", errors)
        for index, raw_template in enumerate(entries or []):
            templates.append(_parse_field_template(raw_template, f"field_templates[{index}]", errors))

    doc_types: list[DocumentTypeDef | None] = []
    if "document_types" in raw:
        entries = _sequence(raw["document_types"], "document_types", errors)
        for index, raw_doc_type in enumerate(entries or []):
            doc_types.append(_parse_document_type(raw_doc_type, f"document_types[{index}]", errors))

    _cross_check(templates, doc_types, errors)

    if errors:
        raise PackageValidationError(errors)
    return PackageV1(
        schema_version=SCHEMA_VERSION,
        field_templates=tuple(template for template in templates if template is not None),
        document_types=tuple(doc_type for doc_type in doc_types if doc_type is not None),
    )
