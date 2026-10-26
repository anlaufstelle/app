"""Dynamic details shared by episode and task forms."""

from typing import cast

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.forms.dynamic import DynamicDataForm
from core.services.compliance import allowed_sensitivities_for_user, user_can_see_document_type
from core.services.dynamic_data import decrypt_dynamic_text_data, merge_dynamic_data, remove_restricted_fields
from core.services.entity_details import visible_stored_details


class EntityDetailsMixin(forms.ModelForm):
    """Bind role-filtered dynamic fields without changing the metadata form contract."""

    details_original_keys: set[str]

    def configure_details(self, facility, user, entity_type):
        self.details_old_data = dict(self.instance.data_json or {})
        self.details_old_type_id = self.instance.document_type_id
        self.details_visible_old_data = visible_stored_details(self.instance, user)
        field = cast(forms.ModelChoiceField, self.fields["document_type"])
        if field.queryset is None:
            raise ValueError("The document type field requires a scoped queryset.")
        if user is not None:
            field.queryset = field.queryset.filter(sensitivity__in=allowed_sensitivities_for_user(user))
        url = reverse(f"core:{entity_type}_fields_partial")
        if not self.instance._state.adding:
            url += f"?record={self.instance.pk}"
        field.widget.attrs.update(
            {
                "hx-get": url,
                "hx-target": "#dynamic-fields",
                "hx-trigger": "change",
                "hx-include": "[name='document_type']",
            }
        )
        selected = (
            self.data.get(self.add_prefix("document_type")) if self.is_bound else self.initial.get("document_type")
        )
        try:
            self.details_type = field.queryset.filter(pk=selected).first() if selected else None
        except (ValidationError, ValueError):
            self.details_type = None
        slugs = self.details_type.fields.values_list("field_template__slug", flat=True) if self.details_type else []
        self.details_preserve = (
            self.is_bound
            and self.add_prefix("document_type") not in self.details_original_keys
            and not any(slug in self.data for slug in slugs)
        )
        initial = decrypt_dynamic_text_data(self.details_visible_old_data)
        data = self.data if self.is_bound and not self.details_preserve else None
        self.data_form = DynamicDataForm(data, document_type=self.details_type, initial_data=initial, facility=facility)
        self.details_restricted = []
        if user is not None and self.details_type is not None:
            if not user_can_see_document_type(user, self.details_type):
                self.data_form.fields.clear()
            else:
                self.details_restricted = remove_restricted_fields(user, self.details_type, self.data_form)
        self.fields["confirm_data_loss"] = forms.BooleanField(required=False, widget=forms.HiddenInput())

    def clean(self):
        cleaned = super().clean()
        if self._changes_hidden_source_values():
            self.add_error("document_type", _("Keine Berechtigung für diesen Dokumentationstyp."))
        if self.details_preserve:
            return cleaned
        if not self.data_form.is_valid():
            self.add_error(None, _("Bitte prüfen Sie die zusätzlichen Angaben."))
        return cleaned

    def _changes_hidden_source_values(self):
        """Reject type changes when any stored value is outside the actor's source scope."""
        selected_id = self.details_type.pk if self.details_type else None
        return self.details_old_type_id != selected_id and bool(
            self.details_old_data.keys() - self.details_visible_old_data.keys()
        )

    def dynamic_payload(self):
        """Keep absent offline details unchanged; merge only validated online input."""
        if self._changes_hidden_source_values():
            raise ValidationError(_("Keine Berechtigung für diesen Dokumentationstyp."))
        if self.details_preserve:
            return {}
        if self.details_type is None:
            return {"data_json": {}}
        values = dict(self.data_form.cleaned_data)
        if self.details_old_type_id != self.details_type.pk:
            for slug in self.data_form.fields:
                if slug not in self.data and slug in self.details_visible_old_data:
                    values[slug] = self.details_visible_old_data[slug]
        return {
            "data_json": merge_dynamic_data(
                self.details_type,
                values,
                old_data=self.details_old_data,
                restricted_slugs=self.details_restricted,
            )
        }
