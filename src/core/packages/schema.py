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
(z. B. Systemtypen nur an Ereignis-Typen), die ein JSON-Schema ohnehin nicht
ausdrücken könnte.

M24/AP2 (Refs #1738, ADR-039 §9): Die frühere E8-Invariante („Datei-Felder nur
an Ereignis-Typen", Refs #1058) ist **ersatzlos entfallen** — Anhänge hängen
seit ADR-039 an Ereignis, Person oder Fall. **Kein ``schema_version``-Bump:**
eine gelockerte Restriktion ist abwärtskompatibel, jedes bisher gültige Paket
bleibt gültig.

Refs #1749 (ADR-040): ``category`` an einem Dokumentationstyp wird weiterhin
**angenommen**, aber nicht mehr angewendet — das Modellfeld gibt es nicht mehr.
Ebenfalls **kein ``schema_version``-Bump**: ein Cutover auf v2 machte jedes
Bestands-Paket (und jede ältere Kopie von ``default.yaml``) unladbar, obwohl
sich an allem anderen nichts ändert. Damit daraus kein Silent-Drop wird — was
dem Strict-Design widerspräche —, vermerkt der Parser den Schlüssel in
``DocumentTypeDef.ignored_keys`` und der Loader macht daraus eine sichtbare
Warnung im Report.

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

import re
from dataclasses import dataclass
from typing import Any

from core.constants import CONTACT_STAGE_CHOICES
from core.models.document_type import DocumentType
from core.models.field_condition import FieldCondition
from core.models.field_template import FieldTemplate
from core.models.report_key import EXCLUDED
from core.models.slug import _RESERVED_SLUGS, _SLUG_RE

SCHEMA_VERSION = 1

# --- Erlaubte Werte: direkt aus den Modell-Choices, nie handgepflegt ---------
_FIELD_TYPES: frozenset[str] = frozenset(FieldTemplate.FieldType.values)
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
_PACKAGE_KEYS = frozenset({"schema_version", "field_templates", "document_types"})
_FIELD_TEMPLATE_KEYS = frozenset(
    {
        "slug",
        "name",
        "field_type",
        "min_value",
        "max_value",
        "is_required",
        "is_encrypted",
        "sensitivity",
        "options",
        "help_text",
        "default_value",
        "report_key",
    }
)

# Formatregel der Berichtsschluessel (E3, Refs #1849). Bewusst NICHT gegen eine
# Werteliste geprueft: der Werteraum kommt aus den Backend-Deklarationen, und
# ein Fremdpaket mit eigenem Vokabular muss ladbar bleiben (Konzept #1847 §2,
# Entscheid E-H) — die Sichtbarkeit unbekannter Schluessel uebernimmt der
# Vollstaendigkeitshinweis, nicht der Parser. Geprueft wird nur das FORMAT: ein
# Leerzeichen oder Grossbuchstabe ist ein Tippfehler, keine fremde Konvention.
_REPORT_KEY_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")

# Werte-Ebenen-Toleranz (Konzept #1847 §E3.8) — das Gegenstueck zu
# :data:`_IGNORED_DOCUMENT_TYPE_KEYS`, aber auf der WERT-Ebene: „ehemals
# gueltige Berichtswerte" werden normalisiert statt abgelehnt. Ohne den
# Mechanismus machte ein einziger Altwert das GANZE Paket unladbar
# (``parse_package`` wirft, bevor ein ``PackageV1`` existiert).
#
# Die sieben zaehlenden Systemtypen (``contact``, ``crisis``, …) brauchen keinen
# Eintrag: sie heissen als Berichtsschluessel genauso und laufen ohnehin
# unveraendert durch. Uebrig bleiben die beiden Ausschluss-Werte, fuer die der
# Backfill denselben Sentinel setzt.
_FORMER_REPORT_KEY_VALUES: dict[str, str | None] = {"note": EXCLUDED, "ban": EXCLUDED}

# Dieselbe Mechanik auf der ``system_type``-Achse (E4, Refs #1850): die sieben
# reinen Berichtswerte sind aus dem Enum entfernt. Ziel ``None`` heisst hier
# **entfaellt** — anders als beim Berichtsschluessel gibt es nichts zu
# uebersetzen: die Zuordnung, die diese Werte trugen, steht seit E3 im
# ``report_key``, und die Datenmigration ``0147`` nullt den Bestand. Ohne die
# Toleranz waere jedes fremde Paket mit ``system_type: medical`` schlagartig
# unladbar — der Grund, warum dieser Mechanismus Vorbedingung von E4 war und
# nicht bloss Bequemlichkeit.
_FORMER_SYSTEM_TYPE_VALUES: dict[str, str | None] = dict.fromkeys(
    ("contact", "medical", "needle_exchange", "accompaniment", "counseling", "referral", "note")
)
_OPTION_KEYS = frozenset({"slug", "label", "is_active"})
# Refs #1749 (ADR-040): Schluessel, die v1 weiterhin ANNIMMT, aber nicht mehr
# anwendet. ``category`` ist mit dem Wegfall von ``DocumentType.category``
# bedeutungslos geworden; Bestands-Pakete (inkl. aelterer Kopien von
# ``default.yaml``) bleiben trotzdem ladbar — statt eines schema_version-2-
# Cutovers gibt es einen ignorierten Schluessel MIT sichtbarer Warnung im
# Load-Report (kein Silent-Drop). Der Wert wird bewusst nicht mehr gegen eine
# Werteliste geprueft: es gibt keine mehr.
_IGNORED_DOCUMENT_TYPE_KEYS: frozenset[str] = frozenset({"category"})
_DOCUMENT_TYPE_KEYS = frozenset(
    {
        "slug",
        "name",
        "entity_type",
        *_IGNORED_DOCUMENT_TYPE_KEYS,
        "system_type",
        "tally_enabled",
        "sensitivity",
        "min_contact_stage",
        "report_key",
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


class _KeyMissing:
    """Sentinel-Typ: der Schlüssel fehlte im Paket (Refs #1934).

    ``min_value``/``max_value`` können legitim ``None`` tragen (bewusstes
    Löschen der Grenze) — anders als bei ``conditions`` (E14) taugt ``None``
    hier also selbst nicht als Sentinel für "Schlüssel fehlt". Ein eigener,
    einelementiger Typ statt eines ``Any``-Sentinels (Muster
    ``core.services.audit.helpers._UNSET``) hält die Feld-Annotation im
    streng typisierten ``core.packages``-Modul exakt (``int | None |
    _KeyMissing`` statt ``Any``) — genau die Typdisziplin, die der
    Modul-Docstring oben für Fremd-Input einfordert.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - nur Debug-Ausgabe
        return "KEY_MISSING"


KEY_MISSING = _KeyMissing()


@dataclass(frozen=True)
class FieldTemplateDef:
    """Eine Feldvorlage. Lookup-Key beim Laden ist ``(facility, slug)``.

    ``options`` spiegelt exakt die ``FieldTemplate.options_json``-Struktur
    (``{"slug": …, "label": …, "is_active": …}``) — der Loader kann sie 1:1
    schreiben. Die inneren Dicts sind normalisiert (``is_active`` immer
    gesetzt) und werden vom Loader kopiert, nie geteilt.

    ``min_value``/``max_value`` sind echtes Tri-State (Refs #1934, Muster
    ``tally_enabled``/E13): :data:`KEY_MISSING` heißt "Schlüssel fehlte im
    Paket" — der Loader lässt eine bestehende Grenze dann unangetastet.
    ``None`` heißt "Schlüssel da, Wert ``null``" — die Grenze wird bewusst
    gelöscht. Eine ``int`` heißt "Schlüssel da, Wert gesetzt" — die Grenze
    wird geschrieben. Vorher normalisierte der Parser fehlenden Schlüssel und
    explizites ``null`` beide auf ``None``; der Loader konnte die Fälle nicht
    unterscheiden und überschrieb beide Grenzen bei **jedem** Lauf auf NULL.
    """

    slug: str
    name: str
    field_type: str = FieldTemplate.FieldType.TEXT.value
    min_value: int | None | _KeyMissing = KEY_MISSING
    max_value: int | None | _KeyMissing = KEY_MISSING
    is_required: bool = False
    is_encrypted: bool = False
    sensitivity: str = ""
    options: tuple[dict[str, Any], ...] = ()
    help_text: str = ""
    default_value: str = ""
    report_key: str | None = None
    normalized_report_keys: tuple[tuple[str, str], ...] = ()


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

    ``ignored_keys`` trägt die im Paket gesetzten, aber nicht mehr angewendeten
    Schlüssel (Refs #1749) — der Loader macht daraus je Typ eine Warnung im
    Report, damit ein Bestands-Paket zwar weiter lädt, sein toter Schlüssel
    aber nicht stillschweigend verschwindet. ``removed_system_types`` ist das
    Gegenstück auf der **Wert**-Ebene (E4, Refs #1850): ein ehemals gültiger
    ``system_type``, der weggefallen ist.
    """

    slug: str
    name: str
    entity_type: str = DocumentType.EntityType.EVENT.value
    system_type: str | None = None
    # Refs #1637: ``None`` bedeutet E13-konform: Schlüssel fehlt, Bestand unangetastet.
    tally_enabled: bool | None = None
    sensitivity: str = DocumentType.Sensitivity.NORMAL.value
    min_contact_stage: str | None = None
    report_key: str | None = None
    # Refs #1886: fehlende Darstellungs-Schluessel in aelteren Paketen lassen
    # lokale Fach-UI-Anpassungen unangetastet. Leere Strings bzw. 0 bleiben
    # ausdrueckliche Werte und koennen den Bestand bewusst zuruecksetzen.
    icon: str | _KeyMissing = KEY_MISSING
    color: str | _KeyMissing = KEY_MISSING
    sort_order: int | _KeyMissing = KEY_MISSING
    groups: tuple[GroupDef, ...] = ()
    field_refs: tuple[FieldRefDef, ...] = ()
    conditions: tuple[ConditionDef, ...] | None = None
    ignored_keys: tuple[str, ...] = ()
    normalized_report_keys: tuple[tuple[str, str], ...] = ()
    removed_system_types: tuple[str, ...] = ()


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


def _key_missing_str_value(data: dict[str, Any], key: str, path: str, errors: Errors) -> str | _KeyMissing:
    """Zeichenkette mit ``KEY_MISSING`` fuer E13-konformen Ruecksync (#1886)."""
    if key not in data:
        return KEY_MISSING
    return _str_value(data, key, path, errors)


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


def _key_missing_int_value(data: dict[str, Any], key: str, path: str, errors: Errors) -> int | _KeyMissing:
    """Ganzzahl mit ``KEY_MISSING`` fuer E13-konformen Ruecksync (#1886)."""
    if key not in data:
        return KEY_MISSING
    return _int_value(data, key, path, errors)


def _tri_state_int_value(data: dict[str, Any], key: str, path: str, errors: Errors) -> int | None | _KeyMissing:
    """Ganzzahliges Tri-State-Feld (Refs #1934) — löst :func:`_optional_int_value` ab.

    Drei Ausgänge statt zwei: Schlüssel fehlt ⇒ :data:`KEY_MISSING`
    (unangetastet lassen); Schlüssel da, Wert ``null`` ⇒ ``None`` (bewusst
    löschen); Schlüssel da, gültige Zahl ⇒ die Zahl (setzen). Ein ungültiger
    Wert (falscher Typ) fällt auf :data:`KEY_MISSING` zurück wie "fehlt" —
    der Parser sammelt ohnehin einen Fehler und ``parse_package`` wirft, bevor
    der Rückgabewert irgendwo ankommt.
    """
    if key not in data:
        return KEY_MISSING
    value = data[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append((_join(path, key), "Erwartet wird eine ganze Zahl oder null."))
        return KEY_MISSING
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


def _tolerant_key_value(
    data: dict[str, Any],
    key: str,
    path: str,
    errors: Errors,
    *,
    normalisierung: dict[str, str | None],
) -> tuple[str | None, tuple[tuple[str, str | None], ...]]:
    """Toleranter Schluesselwert mit Normalisierung statt Werteliste (Refs #1849).

    Anders als :func:`_optional_enum_value`: ein **unbekannter** Wert ist KEIN
    Fehler. Der Werteraum der Berichtsschluessel kommt aus den
    Backend-Deklarationen und kann in einem Fremdpaket ein anderer sein; ein
    harter Parserfehler machte das ganze Paket unladbar, obwohl nur eine Zeile
    fremd ist (Konzept #1847 §2, Entscheid E-H).

    Geprueft wird deshalb nur der **Typ** (Zeichenkette) und das **Format**;
    Werte aus ``normalisierung`` werden uebersetzt und als ``(alt, neu)``-Paar
    zurueckgegeben, damit der Loader daraus eine sichtbare Warnung macht (kein
    Silent-Rewrite — dieselbe Linie wie bei ``ignored_keys``).

    **``neu is None`` heisst „entfaellt"** (E4, Refs #1850): der Altwert wird
    nicht uebersetzt, sondern weggelassen — der Fall des reduzierten
    ``system_type``, dessen Werte keinen Nachfolger auf derselben Achse haben.
    Deshalb entscheidet die **Mitgliedschaft** im Dict, nicht ein truthy Ziel.

    Fehlender Schluessel und ``null`` sind gleichbedeutend „keine Angabe": das
    Format kennt keinen Weg, einen gesetzten Schluessel zu ENTFERNEN — das
    verbietet die Aenderbarkeits-Regel ohnehin (Konzept §2.1).
    """
    value = data.get(key)
    if value is None:
        return None, ()
    if not isinstance(value, str) or not _REPORT_KEY_RE.fullmatch(value):
        errors.append(
            (
                _join(path, key),
                "Ungültiger Schlüssel — erlaubt sind a-z, 0-9 und Unterstriche (oder null).",
            )
        )
        return None, ()
    if value not in normalisierung:
        return value, ()
    ersatz = normalisierung[value]
    return ersatz, ((value, ersatz),)


def _optional_report_key_value(
    data: dict[str, Any], key: str, path: str, errors: Errors
) -> tuple[str | None, tuple[tuple[str, str], ...]]:
    """:func:`_tolerant_key_value` mit der Berichtsschluessel-Normalisierung."""
    wert, paare = _tolerant_key_value(data, key, path, errors, normalisierung=_FORMER_REPORT_KEY_VALUES)
    # ``_FORMER_REPORT_KEY_VALUES`` kennt kein „entfaellt" — auf dieser Achse
    # hat jeder Altwert einen Nachfolger. Das Filtern ist die Typ-Verengung
    # dieser Zusage, kein Fallback.
    return wert, tuple((alt, neu) for alt, neu in paare if neu is not None)


def _optional_system_type_value(
    data: dict[str, Any], key: str, path: str, errors: Errors
) -> tuple[str | None, tuple[str, ...]]:
    """``system_type`` mit der E4-Toleranz (Refs #1850) — Altwerte entfallen.

    Anders als der Berichtsschluessel bleibt der Systemtyp gegen eine
    **Werteliste** geprueft: er ist ein eingefrorenes Kern-Datenmodell-Enum
    (RFC #1748), ein
    unbekannter Wert ist dort ein Tippfehler und keine fremde Konvention. Neu
    ist allein die Toleranz fuer die sieben Werte, die es bis E4 gab — sie
    fallen weg statt das Paket zu sprengen. Zurueck kommen sie als
    Altwert-Liste, damit der Loader daraus eine sichtbare Warnung macht.
    """
    vorher = len(errors)
    wert, paare = _tolerant_key_value(data, key, path, errors, normalisierung=_FORMER_SYSTEM_TYPE_VALUES)
    if len(errors) > vorher:
        # Die Format-Meldung des Berichtsschluessels suggeriert einen freien
        # Schluesselraum, den es fuer das eingefrorene Enum nicht gibt — fuer
        # Paketautoren zaehlt die Werteliste (z. B. beim Tippfehler
        # ``needle-exchange`` fuer einen entfallenen Altwert).
        errors[vorher:] = [(_join(path, key), f"Unzulässiger Wert — erlaubt: null, {_allowed(_SYSTEM_TYPES)}.")]
        return None, ()
    if wert is not None and wert not in _SYSTEM_TYPES:
        errors.append((_join(path, key), f"Unzulässiger Wert — erlaubt: null, {_allowed(_SYSTEM_TYPES)}."))
        return None, ()
    return wert, tuple(alt for alt, _neu in paare)


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


def _parse_option_list(data: dict[str, Any], path: str, errors: Errors, field_type: str) -> tuple[dict[str, Any], ...]:
    """``options``-Liste einer Feldvorlage (Refs #1827).

    Der einzige verbliebene Inline-Listen-Parser neben den drei ausgelagerten
    Schwester-Parsern (:func:`_parse_group_list`, :func:`_parse_field_ref_list`,
    :func:`_parse_condition_list`) — dedupliziert per Slug (Duplikate werden
    als Fehler gemeldet und übersprungen) und meldet nicht-leere ``options`` an
    Feldtypen außerhalb :data:`_OPTION_FIELD_TYPES` als Fehler (Optionen
    ergeben nur bei select/multi_select Sinn).
    """
    raw_options = data.get("options")
    if raw_options is None:
        return ()
    entries = _sequence(raw_options, _join(path, "options"), errors)
    if entries is None:
        return ()
    options: list[dict[str, Any]] = []
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
    return tuple(options)


def _parse_field_template(raw: Any, path: str, errors: Errors) -> FieldTemplateDef | None:
    data = _mapping(raw, path, errors)
    if data is None:
        return None
    _check_keys(data, path, _FIELD_TEMPLATE_KEYS, frozenset({"slug", "name"}), errors)
    slug = _slug_value(data, "slug", path, errors)
    # Reserved-Slug-Guard (Refs #1726): dynamische Felder werden nackt unter
    # ihrem Slug im Formular gerendert — ein Slug, der einen festen
    # Formularfeld-Namen trifft, ist ein Schema-Fehler (dieselbe importierte
    # ``_RESERVED_SLUGS`` wie Form und Modell).
    if slug is not None and slug in _RESERVED_SLUGS:
        errors.append(
            (_join(path, "slug"), f"Reservierter Slug {slug!r} — er kollidiert mit einem festen Formularfeld.")
        )
    name = _required_name(data, "name", path, errors)
    field_type = _enum_value(data, "field_type", path, errors, _FIELD_TYPES, default=FieldTemplate.FieldType.TEXT.value)
    min_value = _tri_state_int_value(data, "min_value", path, errors)
    max_value = _tri_state_int_value(data, "max_value", path, errors)
    is_required = _bool_value(data, "is_required", path, errors)
    is_encrypted = _bool_value(data, "is_encrypted", path, errors)
    sensitivity = _enum_value(data, "sensitivity", path, errors, _TEMPLATE_SENSITIVITIES, default="")
    help_text = _str_value(data, "help_text", path, errors)
    default_value = _str_value(data, "default_value", path, errors)
    report_key, normalized_report_keys = _optional_report_key_value(data, "report_key", path, errors)

    options = _parse_option_list(data, path, errors, field_type)

    # Nur eine ECHTE Zahl (weder ``KEY_MISSING`` noch das bewusste Loesch-``None``)
    # ist am Feldtyp ueberhaupt zu beanstanden bzw. fuer den Min/Max-Vergleich
    # heranzuziehen — beide Nicht-Zahl-Zustaende bedeuten "keine Grenze".
    if field_type != FieldTemplate.FieldType.NUMBER.value:
        if isinstance(min_value, int):
            errors.append((_join(path, "min_value"), "Minimal- und Maximalwert sind nur für Zahlenfelder zulässig."))
        if isinstance(max_value, int):
            errors.append((_join(path, "max_value"), "Minimal- und Maximalwert sind nur für Zahlenfelder zulässig."))
    elif isinstance(min_value, int) and isinstance(max_value, int) and min_value > max_value:
        errors.append((_join(path, "min_value"), "Wert darf nicht größer als max_value sein."))
        errors.append((_join(path, "max_value"), "Wert darf nicht kleiner als min_value sein."))

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
        min_value=min_value,
        max_value=max_value,
        is_required=is_required,
        is_encrypted=is_encrypted,
        sensitivity=sensitivity,
        options=options,
        help_text=help_text,
        default_value=default_value,
        report_key=report_key,
        normalized_report_keys=normalized_report_keys,
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
    # Refs #1749: angenommen, aber nicht angewendet — der Wert wird nicht
    # einmal mehr validiert (es gibt keine Werteliste), nur vermerkt.
    ignored_keys = tuple(sorted(key for key in _IGNORED_DOCUMENT_TYPE_KEYS if key in data))
    sensitivity = _enum_value(
        data, "sensitivity", path, errors, _SENSITIVITIES, default=DocumentType.Sensitivity.NORMAL.value
    )
    system_type, removed_system_types = _optional_system_type_value(data, "system_type", path, errors)
    tally_enabled = _bool_value(data, "tally_enabled", path, errors) if "tally_enabled" in data else None
    min_contact_stage = _optional_enum_value(data, "min_contact_stage", path, errors, _CONTACT_STAGES)
    report_key, normalized_report_keys = _optional_report_key_value(data, "report_key", path, errors)
    icon = _key_missing_str_value(data, "icon", path, errors)
    color = _key_missing_str_value(data, "color", path, errors)
    sort_order = _key_missing_int_value(data, "sort_order", path, errors)

    # ``system_type`` steuert Bann-Logik und Krisen-Highlight (E4, Refs #1850:
    # die Berichts-Zuordnung ist raus) — beides Ereignis-Semantik. An
    # Person-/Fall-Typen wäre er bedeutungslos. Ein **entfallener** Altwert
    # loest hier nichts aus: er ist zu diesem Zeitpunkt bereits ``None``.
    if system_type is not None and entity_type != DocumentType.EntityType.EVENT.value:
        errors.append(
            (
                _join(path, "system_type"),
                "Ein Systemtyp ist nur für Dokumentationstypen mit entity_type 'event' zulässig.",
            )
        )

    groups = _parse_group_list(data, path, errors)
    field_refs = _parse_field_ref_list(data, path, errors)
    _check_block_positions(data, groups, field_refs, path, errors)
    conditions = _parse_condition_list(data, path, errors)

    if slug is None:
        return None
    return DocumentTypeDef(
        slug=slug,
        name=name,
        entity_type=entity_type,
        system_type=system_type,
        tally_enabled=tally_enabled,
        sensitivity=sensitivity,
        min_contact_stage=min_contact_stage,
        report_key=report_key,
        icon=icon,
        color=color,
        sort_order=sort_order,
        groups=groups,
        field_refs=field_refs,
        conditions=conditions,
        ignored_keys=ignored_keys,
        normalized_report_keys=normalized_report_keys,
        removed_system_types=removed_system_types,
    )


def _raw_index_by_key(raw: Any, key: str) -> dict[str, int]:
    """Erst-Vorkommen-Index je ``key``-Wert in einer rohen Eintragsliste.

    Dient nur dazu, die Blockpositions-Fehler pfadgenau auf den ORIGINAL-Index
    im Paket zeigen zu lassen (``field_refs[k]``/``groups[j]``) — die geparsten
    Defs tragen ihren Roh-Index nicht.
    """
    result: dict[str, int] = {}
    if isinstance(raw, list):
        for index, entry in enumerate(raw):
            if isinstance(entry, dict):
                value = entry.get(key)
                if isinstance(value, str) and value not in result:
                    result[value] = index
    return result


def _check_block_positions(
    data: dict[str, Any],
    groups: tuple[GroupDef, ...],
    field_refs: tuple[FieldRefDef, ...],
    path: str,
    errors: Errors,
) -> None:
    """Haertung Refs #1727: Blockpositions-Eindeutigkeit (ADR-036).

    ``groups[].sort_order`` und die ``sort_order`` der ``field_refs`` OHNE
    ``group_slug`` teilen sich EINE globale Block-Sequenz je Dokumentationstyp —
    ein Positions-Duplikat darin ist ein Fehler: zwei Bloecke beanspruchten
    denselben Slot, die Reihenfolge waere nicht definiert (der bisherige stille
    Tie waere layout-abhaengig von der DB-Zeilenordnung). ``field_refs`` MIT
    ``group_slug`` tragen dagegen einen Innen-Index je Gruppe (Dual-Semantik,
    ADR-036, ``docs/packages.md`` § 2) — dort gilt Eindeutigkeit JE GRUPPE.

    Beide Ebenen sind unabhaengig: ein Innen-Index 0 darf gleich einer globalen
    Blockposition 0 sein, und derselbe Innen-Index in zwei verschiedenen Gruppen
    ist erlaubt. Reihenfolge der Meldung: Gruppen vor freien Feldern (bestimmt
    nur, welcher der beiden Eintraege als "belegt" gilt und welcher als
    kollidierend gemeldet wird).
    """
    group_raw_index = _raw_index_by_key(data.get("groups"), "slug")
    ref_raw_index = _raw_index_by_key(data.get("field_refs"), "template_slug")

    def _duplicate_block_message(position: int, occupant: str) -> str:
        return (
            f"Blockposition {position} ist bereits belegt (durch {occupant}) — jede Position der "
            f"globalen Block-Sequenz muss eindeutig sein."
        )

    # Globale Block-Sequenz: Gruppen zuerst, dann freie Felder.
    global_positions: dict[int, str] = {}
    for group in groups:
        occupant = global_positions.get(group.sort_order)
        if occupant is not None:
            gpath = f"{path}.groups[{group_raw_index.get(group.slug, 0)}].sort_order"
            errors.append((gpath, _duplicate_block_message(group.sort_order, occupant)))
        else:
            global_positions[group.sort_order] = f"Gruppe {group.slug!r}"
    for ref in field_refs:
        if ref.group_slug is not None:
            continue
        occupant = global_positions.get(ref.sort_order)
        rpath = f"{path}.field_refs[{ref_raw_index.get(ref.template_slug, 0)}].sort_order"
        if occupant is not None:
            errors.append((rpath, _duplicate_block_message(ref.sort_order, occupant)))
        else:
            global_positions[ref.sort_order] = f"freies Feld {ref.template_slug!r}"

    # Innen-Positionen je Gruppe (Dual-Semantik): eindeutig innerhalb der Gruppe.
    inner_positions: dict[str, dict[int, str]] = {}
    for ref in field_refs:
        if ref.group_slug is None:
            continue
        seen = inner_positions.setdefault(ref.group_slug, {})
        occupant = seen.get(ref.sort_order)
        if occupant is not None:
            rpath = f"{path}.field_refs[{ref_raw_index.get(ref.template_slug, 0)}].sort_order"
            errors.append(
                (
                    rpath,
                    f"Innen-Position {ref.sort_order} ist in der Gruppe {ref.group_slug!r} bereits belegt "
                    f"(durch Feld {occupant!r}) — die Positionen innerhalb einer Gruppe müssen eindeutig sein.",
                )
            )
        else:
            seen[ref.sort_order] = ref.template_slug


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
    # (c) Dokumentationstypen sind je Einrichtung ueber ``name`` eindeutig —
    # die Pruefung nimmt die DB-Constraint ``unique_facility_documenttype_name``
    # vorweg (Loader-Docstring), damit ein Paket mit zwei gleichnamigen Typen
    # schon beim Validieren auffaellt statt erst am IntegrityError. Refs #1749
    # (ADR-040): bis dahin lief die Regel ueber ``(name, category)``.
    seen_names: set[str] = set()
    for index, doc_type in enumerate(doc_types):
        if doc_type is None:
            continue
        path = f"document_types[{index}]"
        if doc_type.slug in seen_doc_types:
            errors.append((f"{path}.slug", f"Doppelter Slug {doc_type.slug!r}."))
            continue
        seen_doc_types.add(doc_type.slug)
        if doc_type.name in seen_names:
            errors.append((f"{path}.name", f"Doppelter Name {doc_type.name!r}."))
        else:
            seen_names.add(doc_type.name)
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
