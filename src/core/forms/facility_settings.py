"""Formular fuer einrichtungsweite Einstellungen im Verwaltungs-Hub (#1885)."""

from __future__ import annotations

from typing import cast

from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.forms.mixins import TailwindFormMixin
from core.models import DocumentType, Settings

_FIELD_GROUPS = (
    (
        _("Allgemein und Komfort"),
        ("facility_full_name", "default_document_type", "search_trigram_threshold"),
    ),
    (
        _("Sicherheit"),
        ("session_timeout_minutes", "session_absolute_hours", "mfa_enforced_facility_wide"),
    ),
    (
        _("Datenschutz und Fristen"),
        (
            "retention_anonymous_days",
            "retention_identified_days",
            "retention_qualified_days",
            "retention_activities_days",
            # Refs #2042: direkt hinter der Aktivitaeten-Frist — beide regeln
            # die Aufbewahrung von Betriebsspuren neben der Fallakte.
            "retention_handover_notes_days",
            "client_trash_days",
            "auditlog_retention_months",
            # ADR-050 D3/D8 (Refs #2065): direkt hinter der Audit-Frist — beide
            # beantworten dieselbe Frage („wie lange bleibt Rechenschaft
            # lesbar") und teilen sich den Vorgabewert 24.
            "eventhistory_detail_months",
            "eventhistory_chronicle_months",
            "retention_auto_approve_after_defer",
            "retention_max_defer_count",
            "retention_use_k_anonymization",
            "k_anonymity_threshold",
        ),
    ),
    # Refs #2136 (N-2): eigene Gruppe statt Anhaengen an "Sicherheit". Die
    # Sicherheits-Gruppe regelt, wie lange eine Sitzung lebt und wer sich wie
    # anmeldet; diese beiden Felder regeln, was jemand SIEHT, der neben der
    # Fachkraft steht. Verschiedene Fragen, verschiedene Zielgruppen (Frauenhaus/
    # Awareness) — und die Wahl der Fluchtseite ist eine Entscheidung, die eine
    # Einrichtung bewusst treffen soll, nicht eine, die zwischen zwei
    # Sitzungs-Timeouts untergeht.
    (_("Mitschauen-Schutz"), ("quick_exit_url", "privacy_screen_on_blur")),
    (_("Dateien"), ("allowed_file_types", "max_file_size_mb", "max_storage_mb")),
)


class FacilitySettingsForm(TailwindFormMixin, forms.ModelForm):
    """Alle fachlich editierbaren ``Settings``-Felder, ohne Facility-Scope."""

    FIELD_GROUPS = _FIELD_GROUPS

    class Meta:
        model = Settings
        fields = tuple(field for _title, fields in _FIELD_GROUPS for field in fields)

    def __init__(self, *args, facility, **kwargs):
        super().__init__(*args, **kwargs)
        current_id = getattr(self.instance, "default_document_type_id", None)
        queryset = DocumentType.objects.for_facility(facility).filter(
            Q(is_active=True) | Q(pk=current_id),
            entity_type=DocumentType.EntityType.EVENT,
        )
        default_field = cast(forms.ModelChoiceField, self.fields["default_document_type"])
        default_field.queryset = queryset.order_by("sort_order", "name")
        default_field.empty_label = _("Kein Standard-Dokumentationstyp")

    @property
    def grouped_fields(self):
        """Gruppen samt gebundenen Feldern fuer das semantische Fieldset-Markup."""
        return tuple((title, tuple(self[name] for name in names)) for title, names in self.FIELD_GROUPS)
