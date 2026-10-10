"""Conditional field visibility within one document type (Refs #1057).

The facility scope follows target_field.document_type, with a transitive RLS
policy. Config services run full_clean(); direct save() only validates selection
references under the shared configuration lock (Refs #2519). Operator, cycle and
other clean() checks remain the service's responsibility. Conditions are managed
through the configuration UI and are not registered in the Django admin.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from core.models.document_type_field import DocumentTypeField

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

    def save(self, *args, **kwargs):
        """Recheck selection references while configuration changes cannot commit."""
        from core.services.dynamic_protection import lock_dynamic_protection

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = kwargs["update_fields"] = frozenset(update_fields)
            if not update_fields:
                return super().save(*args, **kwargs)
        with transaction.atomic():
            lock_dynamic_protection(self.target_field.document_type.facility_id)
            previous = (
                FieldCondition.objects.filter(pk=self.pk)
                .select_for_update(of=("self",))
                .values("depends_on_field_id", "operator", "value")
                .first()
            )
            self._validate_option_reference(previous, update_fields)
            return super().save(*args, **kwargs)

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
        from core.services.conditions import BOOLEAN, MULTI_SELECT, SELECT, VALUELESS_OPERATORS

        source_template = self.depends_on_field.field_template
        if self.operator in VALUELESS_OPERATORS:
            if self.value is not None:
                raise ValidationError({"value": _("Für diesen Operator darf kein Vergleichswert gesetzt sein.")})
            return
        if self.value is None:
            raise ValidationError({"value": _("Für diesen Operator ist ein Vergleichswert erforderlich.")})
        if source_template.field_type == BOOLEAN and self.value not in (True, False):
            raise ValidationError({"value": _("Der Vergleichswert muss „wahr“ oder „falsch“ sein.")})
        if source_template.field_type in (SELECT, MULTI_SELECT):
            previous = (
                FieldCondition.objects.filter(pk=self.pk).values("depends_on_field_id", "operator", "value").first()
            )
            self._validate_option_reference(previous)

    def _validate_option_reference(self, previous, update_fields=None) -> None:
        """Validate the persisted candidate against fresh options, preserving old references."""
        from core.services.conditions import MULTI_SELECT, SELECT, VALUELESS_OPERATORS

        source_id, operator, value = self.depends_on_field_id, self.operator, self.value
        if previous is not None and update_fields is not None:
            if not {"depends_on_field", "depends_on_field_id"}.intersection(update_fields):
                source_id = previous["depends_on_field_id"]
            if "operator" not in update_fields:
                operator = previous["operator"]
            if "value" not in update_fields:
                value = previous["value"]
        if operator in VALUELESS_OPERATORS or value is None:
            return
        source_template = DocumentTypeField.objects.select_related("field_template").get(pk=source_id).field_template
        if source_template.field_type not in (SELECT, MULTI_SELECT):
            return
        if (
            previous is not None
            and previous["depends_on_field_id"] == source_id
            and previous["operator"] not in VALUELESS_OPERATORS
            and previous["value"] == value
        ):
            return
        self._validate_option_slug_value(source_template.options_json, value)

    @staticmethod
    def _validate_option_slug_value(options_json: list[dict[str, Any]], value: Any) -> None:
        # Inactive options remain valid references; only slug existence matters.
        known_slugs = {option.get("slug") for option in options_json}
        if not isinstance(value, str) or value not in known_slugs:
            raise ValidationError({"value": _("Der Vergleichswert muss ein gültiger Options-Slug sein.")})

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
