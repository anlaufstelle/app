"""Bedingte Feld-zu-Feld-Sichtbarkeit innerhalb eines Dokumentationstyps (M1, Refs #1057).

``FieldCondition`` verknuepft zwei :class:`~core.models.document_type.DocumentTypeField`
desselben :class:`~core.models.document_type.DocumentType`: ``target_field`` wird ein-
oder ausgeblendet, je nachdem, ob ``depends_on_field`` (die Quelle) den ``operator``
gegen ``value`` erfuellt. Die eigentliche Auswertungssemantik (Operator x Typ-Matrix,
Leer-Definition, Kaskade) lebt zentral in ``core.services.conditions`` (ADR-035) —
dieses Modell validiert in ``clean()`` gegen genau dieselben Konstanten, damit
Modell- und Evaluator-Regeln nicht auseinanderlaufen.

Kein eigener ``facility``-FK — der Scope ergibt sich transitiv ueber
``target_field.document_type.facility`` (wie bei :class:`FieldGroup`), RLS via
doppelt-transitive JOIN-Policy (Migration 0114, P3). Kein ``full_clean()``-Zwang in
``save()``: die Validierung laeuft ueber den Config-Service (P6) und die Tests hier —
direkte ``.save()``-Aufrufe (z.B. Fixtures) umgehen ``clean()`` bewusst, die
DB-``CheckConstraint`` gegen Selbstreferenz bleibt als harte Grenze bestehen.
``FieldCondition`` wird NICHT im Django-Admin registriert (Pflege ausschliesslich
ueber die Config-UI, Abschnitt 9 des M1-Plans).
"""

from __future__ import annotations

import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.document_type import DocumentTypeField

# Schichtregel (Architektur-Guard): Models importieren ``core.services`` nur
# funktionslokal in den clean()-Helfern, nie auf Modul-Ebene.


class FieldCondition(models.Model):
    """Eine Regel "zeige ``target_field``, wenn ``depends_on_field`` <op> ``value``"."""

    class Operator(models.TextChoices):
        EQUALS = "equals", _("ist gleich")
        NOT_EQUALS = "not_equals", _("ist ungleich")
        CONTAINS = "contains", _("enthält")
        GREATER_THAN = "greater_than", _("ist größer als")
        LESS_THAN = "less_than", _("ist kleiner als")
        IS_EMPTY = "is_empty", _("ist leer")
        IS_NOT_EMPTY = "is_not_empty", _("ist nicht leer")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    target_field = models.ForeignKey(
        DocumentTypeField,
        on_delete=models.CASCADE,
        related_name="conditions",
        verbose_name=_("Zielfeld"),
        help_text=_("Wird ein-/ausgeblendet, je nachdem ob die Bedingung erfüllt ist."),
    )
    depends_on_field = models.ForeignKey(
        DocumentTypeField,
        on_delete=models.CASCADE,
        related_name="dependent_conditions",
        verbose_name=_("Quellfeld"),
        help_text=_("Der Wert dieses Felds wird gegen die Bedingung geprüft."),
    )
    operator = models.CharField(
        max_length=20,
        choices=Operator.choices,
        verbose_name=_("Operator"),
    )
    value = models.JSONField(
        null=True,
        blank=True,
        default=None,
        verbose_name=_("Vergleichswert"),
        help_text=_("Typisierter Vergleichswert (JSON). Leer bei is_empty/is_not_empty."),
    )
    group_index = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Gruppe"),
        help_text=_("AND innerhalb einer Gruppe, OR über Gruppen hinweg."),
    )

    class Meta:
        verbose_name = _("Feldbedingung")
        verbose_name_plural = _("Feldbedingungen")
        ordering = ["group_index", "id"]
        constraints = [
            # Selbstreferenz ist der trivialste Zyklus — DB-Backstop zusaetzlich
            # zur clean()-DFS (find_cycle), greift auch bei direktem .save().
            models.CheckConstraint(
                condition=~models.Q(target_field=models.F("depends_on_field")),
                name="fieldcondition_no_self_reference",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.target_field} ← {self.depends_on_field} {self.operator}"

    def clean(self) -> None:
        super().clean()
        if not self.target_field_id or not self.depends_on_field_id:
            return
        self._validate_same_document_type()
        self._validate_source_field_type()
        self._validate_operator_allowed()
        self._validate_value()
        self._validate_no_cycle()

    def _validate_same_document_type(self) -> None:
        # Cross-Document verboten: Bedingungen gelten nur innerhalb eines
        # DocumentType (Plan-Scope v1, Abschnitt 9).
        if self.target_field.document_type_id != self.depends_on_field.document_type_id:
            raise ValidationError(
                {"depends_on_field": _("Ziel- und Quellfeld müssen zum selben Dokumentationstyp gehören.")}
            )

    def _validate_source_field_type(self) -> None:
        from core.services.conditions import ALLOWED_SOURCE_FIELD_TYPES

        source_template = self.depends_on_field.field_template
        # Reihenfolge (Plan P2): erst Typ-Zugehoerigkeit, dann Verschluesselung —
        # ein FILE-Feld (immer verschluesselt) faellt damit unter die
        # Typ-Meldung, nicht unter die Verschluesselungs-Meldung.
        if source_template.field_type not in ALLOWED_SOURCE_FIELD_TYPES:
            raise ValidationError(
                {"depends_on_field": _("Dieser Feldtyp kann nicht als Quelle für Bedingungen verwendet werden.")}
            )
        if source_template.is_encrypted:
            raise ValidationError({"depends_on_field": _("Verschlüsselte Felder sind als Quelle nicht zulässig.")})

    def _validate_operator_allowed(self) -> None:
        from core.services.conditions import OPERATORS_BY_FIELD_TYPE

        source_template = self.depends_on_field.field_template
        allowed = OPERATORS_BY_FIELD_TYPE.get(source_template.field_type, frozenset())
        if self.operator not in allowed:
            raise ValidationError({"operator": _("Dieser Operator ist für den Feldtyp des Quellfelds nicht zulässig.")})

    def _validate_value(self) -> None:
        from core.services.conditions import VALUELESS_OPERATORS

        source_template = self.depends_on_field.field_template
        if self.operator in VALUELESS_OPERATORS:
            if self.value is not None:
                raise ValidationError({"value": _("Für diesen Operator darf kein Vergleichswert gesetzt sein.")})
            return
        if self.value is None:
            raise ValidationError({"value": _("Für diesen Operator ist ein Vergleichswert erforderlich.")})
        if source_template.field_type == "boolean" and self.value not in (True, False):
            raise ValidationError({"value": _("Der Vergleichswert muss „wahr“ oder „falsch“ sein.")})
        if source_template.field_type in ("select", "multi_select"):
            self._validate_option_slug_value(source_template.options_json, self.value)

    @staticmethod
    def _validate_option_slug_value(options_json: list[dict[str, Any]], value: Any) -> None:
        # Inaktive Slugs sind zulaessig (Bestandsdaten), analog
        # forms/events.py:176-183 — nur die Existenz des Slugs zaehlt,
        # ``is_active`` wird hier bewusst nicht geprueft.
        known_slugs = {option.get("slug") for option in options_json}
        if value not in known_slugs:
            raise ValidationError({"value": _("Der Vergleichswert muss eine gültige Options-Slug sein.")})

    def _validate_no_cycle(self) -> None:
        document_type_id = self.target_field.document_type_id
        edges: list[tuple[str, str]] = []
        existing = FieldCondition.objects.filter(target_field__document_type_id=document_type_id).select_related(
            "target_field__field_template", "depends_on_field__field_template"
        )
        for condition in existing:
            if condition.pk == self.pk:
                continue
            edges.append(
                (
                    condition.depends_on_field.field_template.slug,
                    condition.target_field.field_template.slug,
                )
            )
        edges.append((self.depends_on_field.field_template.slug, self.target_field.field_template.slug))

        from core.services.conditions import find_cycle

        cycle = find_cycle(edges)
        if cycle is not None:
            raise ValidationError(
                {"depends_on_field": _("Diese Bedingung erzeugt einen Zyklus: %(path)s") % {"path": " → ".join(cycle)}}
            )
