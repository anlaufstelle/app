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
from core.models import DocumentType, FieldTemplate


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


class FieldTemplateForm(forms.ModelForm):
    """Anlegen/Bearbeiten einer Feldvorlage.

    * ``facility`` kommt aus dem Request-Scope (kein Formularfeld) — der Service
      :func:`core.services.config.save_field_template` setzt sie beim Anlegen.
      Sie dient hier nur dem freundlichen Unique-Namens-Fehler.
    * ``slug`` ist **kein** Formularfeld: beim Anlegen erzeugt das Model ihn
      automatisch, danach ist er unveränderbar (``FieldTemplate.save``); die
      Update-Ansicht zeigt ihn read-only an.
    * Die inhaltlichen Regeln (HIGH-Sensibilität ⇒ verschlüsselt,
      ``default_value``-Registry-Validierung, Slug-Zeichensatz) liegen im Model
      (``FieldTemplate.clean``) und laufen über den ModelForm-``_post_clean``.
    """

    class Meta:
        model = FieldTemplate
        fields = [
            "name",
            "field_type",
            "is_required",
            "is_encrypted",
            "sensitivity",
            "options_json",
            "default_value",
            "statistics_category",
            "help_text",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CSS}),
            "field_type": forms.Select(attrs={"class": INPUT_CSS}),
            "sensitivity": forms.Select(attrs={"class": INPUT_CSS}),
            "options_json": forms.Textarea(attrs={"class": INPUT_CSS, "rows": 3}),
            "default_value": forms.TextInput(attrs={"class": INPUT_CSS}),
            "statistics_category": forms.TextInput(attrs={"class": INPUT_CSS}),
            "help_text": forms.TextInput(attrs={"class": INPUT_CSS}),
        }

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility

    def clean_name(self):
        name = self.cleaned_data["name"]
        if name and self.facility is not None:
            qs = FieldTemplate.objects.for_facility(self.facility).filter(name=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(_("Eine Feldvorlage mit diesem Namen existiert in dieser Einrichtung bereits."))
        return name

    def clean_options_json(self):
        # JSONField liefert bei leerem Textarea ``None``; das Model-Feld ist
        # NOT NULL mit ``default=list`` — leeres Feld also als leere Liste
        # normalisieren, damit kein IntegrityError am Save entsteht.
        value = self.cleaned_data.get("options_json")
        if value in (None, ""):
            return []
        return value
