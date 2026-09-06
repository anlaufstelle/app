"""Gemeinsame Kontext-Builder der Config-Views.

Die Funktionen in diesem Modul bereiten ausschließlich Template-Kontext auf.
Sie bleiben damit unabhängig von Request-/Response-Choreografie und können von
den einzelnen Config-View-Modulen ohne lokale Zyklus-Imports genutzt werden.
"""

import uuid

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.forms.config import DocumentTypeFieldAddForm, FieldConditionForm, FieldGroupForm
from core.models import DocumentTypeField, FieldCondition
from core.services import config as config_service
from core.services.conditions import OPERATORS_BY_FIELD_TYPE, VALUELESS_OPERATORS
from core.services.dynamic_data import option_label_map

CONDITION_CREATE_ID_PREFIX = "condition-create"


def condition_id_prefix(raw: str | None = None, *, condition=None) -> str:
    """Liefert einen sicheren, slot-eindeutigen ID-Praefix fuer Regel-Formulare."""
    if condition is not None:
        return f"condition-{condition.pk}"
    if raw == CONDITION_CREATE_ID_PREFIX:
        return raw
    if raw and raw.startswith("condition-"):
        try:
            return f"condition-{uuid.UUID(raw.removeprefix('condition-'))}"
        except (ValueError, TypeError):
            pass
    return CONDITION_CREATE_ID_PREFIX


def prepare_condition_form(form, prefix: str) -> None:
    """Setzt eindeutige Django-Feld-IDs, ohne POST-``name``-Attribute zu aendern."""
    form.auto_id = f"{prefix}-%s"


def _condition_display_value(condition):
    """Menschenlesbarer Vergleichswert einer Bedingung fürs Board (#1714).

    Löst den Roh-``value`` je **Quelltyp** auf, statt ihn roh anzuzeigen:

    * ``boolean`` → ``Ja``/``Nein`` (übersetzbar, Muster
      ``services/events/context.py:_format_field_display_value``);
    * ``select``/``multi_select`` → Options-Label über eine ``label_map`` DIREKT
      aus ``options_json`` (NICHT ``FieldTemplate.choices`` — das filtert
      ``is_active`` und würde Bestandswerte mit inaktivem Slug nicht auflösen);
      unbekannter Slug → Roh-Slug als Fallback;
    * ``VALUELESS_OPERATORS`` (``is_empty``/``is_not_empty``) → ``None`` (kein Wert);
    * sonst → ``str(value)``.

    Rückgabe ``None`` bedeutet „kein Wert anzeigen" (das Template guardet mit
    ``is not None``, damit auch ``0``/``False`` als Wert erscheinen).
    """
    if condition.operator in VALUELESS_OPERATORS:
        return None
    value = condition.value
    if value is None:
        return None
    template = condition.depends_on_field.field_template
    field_type = template.field_type
    types = template.FieldType
    if field_type == types.BOOLEAN:
        return _("Ja") if value else _("Nein")
    if field_type in (types.SELECT, types.MULTI_SELECT) and isinstance(value, str):
        return option_label_map(template.options_json).get(value, value)
    return str(value)


def condition_board_context(doc_type, *, form=None):
    """Baut den Kontext des Bedingungs-Boards.

    ``condition_targets`` gruppiert die Bedingungen je **Zielfeld** und darunter
    je ``group_index`` (OR-Gruppen; AND-Zeilen je Gruppe) — die Sortierung wird
    hier (View/DB) vorbereitet, nicht im Template. ``condition_next_group_index``
    ist ``max(group_index)+1`` (bzw. 0) und belegt das Neu-Formular als frische
    ODER-Gruppe vor (E21/Board-„ODER-Gruppe hinzufügen").

    Jede Condition-Instanz bekommt zusätzlich ``display_value`` (#1714) — den
    aufgelösten Anzeigewert (Label/Ja-Nein) fürs Template.
    """
    conditions = list(
        FieldCondition.objects.filter(target_field__document_type=doc_type)
        .select_related("target_field__field_template", "target_field__field_group", "depends_on_field__field_template")
        .order_by("group_index", "id")
    )

    by_target: dict = {}
    for condition in conditions:
        condition.display_value = _condition_display_value(condition)
        entry = by_target.setdefault(
            condition.target_field_id,
            {"target": condition.target_field, "groups": {}},
        )
        entry["groups"].setdefault(condition.group_index, []).append(condition)

    condition_targets = []
    for entry in by_target.values():
        groups = [{"index": index, "rows": rows} for index, rows in sorted(entry["groups"].items())]
        condition_targets.append({"target": entry["target"], "groups": groups})
    # #1713-Folge: Zielfeld-Blöcke in globaler Block-Reihenfolge (flaches
    # sort_order hätte Positions-Ties zwischen Innen-Indizes und Globalpositionen).
    condition_targets.sort(key=lambda item: config_service.dtf_block_sort_key(item["target"]))

    next_group_index = max((condition.group_index for condition in conditions), default=-1) + 1
    if form is None:
        form = FieldConditionForm(document_type=doc_type, initial={"group_index": next_group_index})
    prefix = condition_id_prefix()
    prepare_condition_form(form, prefix)

    return {
        "doc_type": doc_type,
        "condition_targets": condition_targets,
        "condition_form": form,
        "condition_id_prefix": prefix,
        "condition_next_group_index": next_group_index,
        "condition_field_count": DocumentTypeField.objects.filter(document_type=doc_type).count(),
        # #1715: Operator-Slot des eingebetteten Neu-Formulars (typgefiltert, falls
        # die Quelle im Formularzustand bereits bekannt ist — sonst volle Liste).
        **operator_input_context_for_form(form, doc_type, condition_id_prefix=prefix),
    }


def board_context(doc_type, *, add_form=None, group_form=None):
    """Baut den Kontext des Feld-Boards (globale Block-Sequenz + Formulare, #1713)."""
    # Interleavte Top-Level-Blöcke (Gruppen inkl. Innen-Feldern + freie Felder),
    # sortiert nach globaler Blockposition — gemeinsame Wahrheitsquelle mit
    # Formular/Offline/Feed.
    blocks = config_service.iter_blocks_in_order(doc_type)
    # Refs #1725(b): je Feld die Zahl der Datensätze, die beim Entfernen einen
    # „Nicht mehr zugeordnet"-Wert behielten — speist die hx-confirm-Warnung. Ein
    # Count pro dtf; das Board ist klein.
    for block in blocks:
        fields = block["fields"] if block["kind"] == "group" else [block["field"]]
        for dtf in fields:
            dtf.removal_impact = config_service.count_field_removal_impact(dtf)
    return {
        "doc_type": doc_type,
        "blocks": blocks,
        "add_form": add_form if add_form is not None else DocumentTypeFieldAddForm(doc_type=doc_type),
        # Prefix "group-new": das stets vorhandene Neu-Formular bekommt eindeutige
        # Feld-IDs (id_group-new-name), damit es nicht mit einem gleichzeitig
        # offenen Edit-Formular (Prefix group-<pk>) kollidiert.
        "group_form": group_form if group_form is not None else FieldGroupForm(doc_type=doc_type, prefix="group-new"),
    }


def _value_input_context(
    field_template, operator, current_value, *, condition_id_prefix=CONDITION_CREATE_ID_PREFIX
) -> dict:
    """Kontext für ``condition_value_input.html`` — je Quelltyp+Operator (E19).

    Gemeinsam genutzt vom HTMX-Swap-Endpoint (``ConditionValueInputView``) und vom
    Edit-Formular (``ConditionUpdateView.get``), damit der Prefill im Edit-Flow das
    identische typisierte Input rendert wie der Live-Swap (Bug-Fix Runde 2, Minor 2).
    """
    return {
        "field_type": field_template.field_type,
        "operator": operator,
        "valueless": operator in VALUELESS_OPERATORS,
        "choices": field_template.choices,
        "current_value": current_value,
        "condition_id_prefix": condition_id_prefix,
    }


def _value_for_input(value):
    """Stellt einen gespeicherten JSON-``value`` als String fürs Editor-Input dar."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _operator_choices_for(field_template):
    """``(value, label)``-Operatoren gefiltert auf den Quelltyp (#1715).

    In der Reihenfolge von ``FieldCondition.Operator.choices`` (stabile UI-Ordnung).
    ``field_template is None`` (keine Quelle bekannt) → volle Liste (Progressive
    Enhancement; das Non-HTMX-Formular bleibt funktionsfähig, ``clean()``-Matrix
    bleibt Server-Wahrheit).
    """
    if field_template is None:
        return list(FieldCondition.Operator.choices)
    allowed = OPERATORS_BY_FIELD_TYPE.get(field_template.field_type, frozenset())
    return [(value, label) for value, label in FieldCondition.Operator.choices if value in allowed]


def _operator_input_context(
    field_template,
    current_operator,
    doc_type,
    *,
    autoload,
    condition_id_prefix=CONDITION_CREATE_ID_PREFIX,
) -> dict:
    """Kontext für ``condition_operator_input.html`` — je Quelltyp (#1715).

    Gemeinsam genutzt vom HTMX-Swap-Endpoint (``ConditionOperatorInputView``,
    ``autoload=True``) und vom initialen Formular-Render (``autoload=False``). Die
    aktuelle Auswahl bleibt erhalten, wenn sie im zulässigen Set liegt, sonst die
    erste zulässige Option.
    """
    choices = _operator_choices_for(field_template)
    allowed_values = {value for value, _label in choices}
    if current_operator and current_operator in allowed_values:
        selected = current_operator
    else:
        selected = choices[0][0] if choices else ""
    return {
        "operator_choices": choices,
        "selected_operator": selected,
        "doc_type": doc_type,
        "operator_autoload": autoload,
        "condition_id_prefix": condition_id_prefix,
    }


def _form_source_field_template(form):
    """Ermittelt die (ggf.) bereits gewählte Quelle einer ``FieldConditionForm`` (#1715).

    Reihenfolge: gebundene ``data`` (POST/Fehler-Rerender) → ``initial``
    (Edit-Prefill) → ``None``. Die pk wird über das bereits gescopte
    ``depends_on_field``-Queryset aufgelöst (Sicherheit); unauflösbar → ``None``
    (volle Operatorliste als Fallback).
    """
    raw = None
    if form.is_bound:
        raw = form.data.get("depends_on_field")
    if not raw:
        raw = form.initial.get("depends_on_field")
    if not raw:
        return None
    try:
        dtf = form.fields["depends_on_field"].queryset.get(pk=raw)
    except (DocumentTypeField.DoesNotExist, ValidationError, ValueError, TypeError):
        return None
    return dtf.field_template


def operator_input_context_for_form(
    form, doc_type, *, autoload=False, condition_id_prefix=CONDITION_CREATE_ID_PREFIX
) -> dict:
    """Baut den Operator-Slot-Kontext aus dem Formularzustand (#1715).

    Zentral genutzt an allen Formular-Render-Pfaden (Board, Create-GET, Edit-GET,
    Edit-Fehler-Rerender), damit ``condition_operator_input.html`` überall den
    passenden — bei bekannter Quelle typgefilterten — Operator-Slot rendert.
    """
    field_template = _form_source_field_template(form)
    current = ""
    if form.is_bound:
        current = form.data.get("operator", "") or ""
    if not current:
        current = form.initial.get("operator", "") or ""
    return _operator_input_context(
        field_template,
        current,
        doc_type,
        autoload=autoload,
        condition_id_prefix=condition_id_prefix,
    )
