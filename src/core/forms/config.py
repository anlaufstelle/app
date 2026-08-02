"""Forms für die Formular-Konfiguration (M0, Refs #1056).

DocumentType- und FieldTemplate-Formulare für die Config-CRUD-UI unter
``/config/``. Die ``facility`` ist bewusst **kein** Formularfeld — sie kommt
aus dem Request-Scope (``request.current_facility``) und wird beim Speichern
über :mod:`core.services.config` gesetzt. Die Formulare kapseln nur die
UI-Validierung (Widget-CSS, freundliche Unique-Fehler, Immutabilitäten).
"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.forms.widgets import INPUT_CSS
from core.models import DocumentType


class DocumentTypeForm(forms.ModelForm):
    """Anlegen/Bearbeiten eines Dokumentationstyps.

    * ``facility`` kommt aus dem Request-Scope (kein Formularfeld) und wird für
      neue Objekte auf die Instanz gesetzt, damit ``form.save()`` im Service den
      richtigen Mandanten persistiert.
    * ``system_type`` ist nur beim Anlegen editierbar; bei Bearbeitung wird das
      Feld ``disabled`` (Wert stammt aus der Instanz) — spiegelt die
      Model-Immutabilität (``DocumentType.save``) und die Admin-Parität
      (``DocumentTypeAdmin.get_readonly_fields``).
    * Die zusammengesetzte Unique-Regel ``(facility, name, category)`` wird als
      freundlicher Formfehler gemeldet statt als ``IntegrityError`` (facility ist
      kein Formfeld, daher greift Djangos ModelForm-Unique-Check hier nicht).
    """

    class Meta:
        model = DocumentType
        fields = [
            "name",
            "category",
            "sensitivity",
            "system_type",
            "min_contact_stage",
            "retention_days",
            "description",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CSS}),
            "category": forms.Select(attrs={"class": INPUT_CSS}),
            "sensitivity": forms.Select(attrs={"class": INPUT_CSS}),
            "system_type": forms.Select(attrs={"class": INPUT_CSS}),
            "min_contact_stage": forms.Select(attrs={"class": INPUT_CSS}),
            "retention_days": forms.NumberInput(attrs={"class": INPUT_CSS, "min": 1}),
            "description": forms.Textarea(attrs={"class": INPUT_CSS, "rows": 3}),
        }

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        # Neuobjekt: facility aus dem Scope an die Instanz binden, damit
        # form.save() (Service) den richtigen Mandanten schreibt.
        if facility is not None and self.instance.facility_id is None:
            self.instance.facility = facility
        # Bearbeitung: system_type ist unveränderbar (Model erzwingt es beim
        # save()); disabled-Feld hält den Instanzwert und ignoriert POST-Manipulation.
        if self.instance.pk:
            self.fields["system_type"].disabled = True

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get("name")
        category = cleaned.get("category")
        if name and category and self.facility is not None:
            qs = DocumentType.objects.for_facility(self.facility).filter(name=name, category=category)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(
                    _(
                        "Ein Dokumentationstyp mit diesem Namen und dieser Kategorie "
                        "existiert in dieser Einrichtung bereits."
                    )
                )
        return cleaned
