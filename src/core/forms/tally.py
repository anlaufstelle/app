"""Forms for tally events that need required numeric values (Refs #1906)."""

from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms.dynamic import _build_dynamic_field
from core.models import FieldTemplate
from core.services.config import block_ordered_dtf_queryset


class TallyNumberDataForm(forms.Form):
    """Validate the required number fields of one tally document type.

    Optional fields deliberately stay out of the compact tally workflow. A
    required field of any other type invalidates the whole form so a crafted
    POST cannot bypass the runtime eligibility filter in the view/service.
    """

    def __init__(self, *args, document_type, **kwargs):
        kwargs.setdefault("auto_id", f"id_tally_{document_type.pk}_%s")
        self.document_type = document_type
        self._has_unsupported_required_fields = False
        super().__init__(*args, **kwargs)

        for document_type_field in block_ordered_dtf_queryset(document_type):
            field_template = document_type_field.field_template
            if not field_template.is_required:
                continue
            if field_template.field_type != FieldTemplate.FieldType.NUMBER:
                self._has_unsupported_required_fields = True
                continue
            field = _build_dynamic_field(field_template, initial_data=None)
            # Der Server bleibt für Ganzzahlen maßgeblich. Ohne ``step=any``
            # wird ein zurückgerendertes ``value=1.5`` zur HTML-Step-Basis und
            # der Browser blockiert danach korrigierte Ganzzahlen wie ``10``.
            field.widget.attrs["step"] = "any"
            self.fields[field_template.slug] = field

        if self.fields:
            first_field = next(iter(self.fields.values()))
            first_field.widget.attrs.setdefault("autofocus", True)

    def clean(self):
        cleaned_data = super().clean()
        if self._has_unsupported_required_fields:
            raise forms.ValidationError(
                _(
                    "Inline-Erfassung ist nur für Dokumentationstypen mit "
                    "ausschließlich Zahlen-Pflichtfeldern verfügbar."
                )
            )
        return cleaned_data
