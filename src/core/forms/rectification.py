"""Formular fuer den Art.-16-Berichtigungsantrag (ADR-041, Refs #1060)."""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms.mixins import TailwindFormMixin
from core.services.client.rectification import DATA_JSON_PREFIX, rectifiable_fields


class RectificationRequestForm(TailwindFormMixin, forms.Form):
    """Antragsformular: Zielfeld, Wunschwert, fachliche Begruendung.

    Die Feldauswahl kommt aus :func:`~core.services.client.rectification.rectifiable_fields`
    — derselben Allowlist, gegen die auch der Service prueft. Das Formular ist
    damit bequem, nicht autoritativ: ein manipuliertes ``field_path`` scheitert
    hier UND im Service.

    Der Wunschwert wird als Text erfasst und fuer dynamische Felder ueber
    :func:`~core.services.system.field_types.get_spec` in den Typ des Feldes
    gewandelt (``parse_default``) — dieselbe String-Konvention wie bei
    ``FieldTemplate.default_value``, inklusive Komma-Trennung fuer
    Mehrfachauswahl. Ohne diese Wandlung landete ein String in einem Slug, der
    eine Liste oder eine Zahl erwartet, und die Anzeige der Person braeche.
    """

    field_path = forms.ChoiceField(
        label=_("Zu berichtigendes Feld"),
    )
    new_value = forms.CharField(
        label=_("Richtiger Wert"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_(
            "Leer lassen, wenn der Eintrag ersatzlos entfallen soll. Mehrfachauswahl: Werte mit Komma trennen."
        ),
    )
    reason = forms.CharField(
        label=_("Fachliche Begründung"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Woraus ergibt sich, dass der bisherige Eintrag unrichtig ist? Wird verschlüsselt gespeichert."),
    )

    def __init__(self, *args: Any, client: Any = None, user: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.client = client
        self.user = user
        choices = rectifiable_fields(client, user) if client is not None and user is not None else []
        self.fields["field_path"].choices = choices  # type: ignore[attr-defined]

    def clean(self) -> dict[str, Any]:
        # ``Form.clean()`` ist als ``dict | None`` typisiert (ein Override darf
        # nichts zurueckgeben); ``self.cleaned_data`` ist die verlaessliche
        # Quelle und immer ein Dict.
        super().clean()
        cleaned: dict[str, Any] = self.cleaned_data
        field_path = cleaned.get("field_path")
        if not field_path or not field_path.startswith(DATA_JSON_PREFIX):
            return cleaned
        cleaned["new_value"] = self._coerce_dynamic_value(field_path, cleaned.get("new_value") or "")
        return cleaned

    def _coerce_dynamic_value(self, field_path: str, raw: str) -> Any:
        """String → Feldtyp des ``data_json``-Slugs (Registry-Konvention)."""
        from core.services.dynamic_data import build_field_template_lookup
        from core.services.system.field_types import get_spec

        document_type = getattr(self.client, "document_type", None)
        if document_type is None:
            return raw
        slug = field_path[len(DATA_JSON_PREFIX) :]
        field_template = build_field_template_lookup(document_type).get(slug)
        if field_template is None:
            return raw
        # ``parse_default`` liefert bei leerem/nicht parsbarem Input ``None`` —
        # genau die richtige Semantik fuer „Eintrag entfaellt".
        return get_spec(field_template.field_type).parse_default(raw.strip())
