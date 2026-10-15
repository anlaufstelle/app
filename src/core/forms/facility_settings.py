"""Formular fuer einrichtungsweite Einstellungen im Verwaltungs-Hub (#1885)."""

from __future__ import annotations

from typing import cast

from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.forms.mixins import TailwindFormMixin
from core.models import DocumentType, Settings
from core.models.settings import K_ANONYMITY_MIN
from core.services.dashboard.external_report import effective_k_threshold, k_threshold_warning

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
            "deletion_request_retention_days",
            # ADR-050 D3/D8 (Refs #2065): direkt hinter der Audit-Frist — beide
            # beantworten dieselbe Frage („wie lange bleibt Rechenschaft
            # lesbar") und teilen sich den Vorgabewert 24.
            "eventhistory_detail_months",
            "eventhistory_chronicle_months",
            "retention_auto_approve_after_defer",
            "retention_max_defer_count",
            "retention_rejected_retry_days",
            "retention_open_case_inactive_days",
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


# Corrected wording on the form instead of the model, so no help_text migration is needed.
AUTO_APPROVE_AFTER_DEFER_HELP = _(
    "Wenn ausdrücklich aktiviert (standardmäßig aus), werden Löschvorschläge nach Erreichen der maximalen "
    "Zurückstellungszahl und Ablauf der Wiedervorlage ohne zweite Person freigegeben. "
    "Es zählen nur Zurückstellungen durch Personen mit dem Recht „Löschbestätigung“, "
    "wenn kein Aufschub desselben Vorschlags mehr läuft."
)


# Retention periods of the settings the audit log period is compared with:
# field name -> (days per unit, whether 0 means "kept without limit").
_DAYS_PER_MONTH = 30
_RETENTION_PERIODS = {
    "retention_anonymous_days": (1, False),
    "retention_identified_days": (1, False),
    "retention_qualified_days": (1, False),
    "retention_activities_days": (1, False),
    "retention_handover_notes_days": (1, False),
    "client_trash_days": (1, False),
    "deletion_request_retention_days": (1, False),
    "eventhistory_detail_months": (_DAYS_PER_MONTH, True),
    "eventhistory_chronicle_months": (_DAYS_PER_MONTH, True),
}


class FacilitySettingsForm(TailwindFormMixin, forms.ModelForm):
    """Alle fachlich editierbaren ``Settings``-Felder, ohne Facility-Scope."""

    FIELD_GROUPS = _FIELD_GROUPS

    class Meta:
        model = Settings
        fields = tuple(field for _title, fields in _FIELD_GROUPS for field in fields)
        help_texts = {"retention_auto_approve_after_defer": AUTO_APPROVE_AFTER_DEFER_HELP}

    def __init__(self, *args, facility, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        self.fields["retention_max_defer_count"].widget.attrs["min"] = 1
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

    def _current_value(self, name):
        """Value the page is about to save (bound and valid) or the stored value.

        A rejected value is never used: model validation runs after ``construct_instance``
        has written the posted value to the in-memory instance, so the stored value is read
        from ``initial``, which the form built from the instance before validation.
        """
        cleaned = getattr(self, "cleaned_data", None)
        if self.is_bound and cleaned is not None and name in cleaned:
            return cleaned[name]
        if name in self.initial:
            return self.initial[name]
        return getattr(self.instance, name)

    @property
    def setting_warnings(self) -> list:
        """Risk notes for values that are allowed but weaken data protection."""
        warnings = []
        k = self._current_value("k_anonymity_threshold")
        if k is not None and k < K_ANONYMITY_MIN:
            warnings.append(
                _(
                    "Die gespeicherte k-Anonymitäts-Schwelle %(k)s liegt unter dem zulässigen Minimum "
                    "%(minimum)s. Berichte rechnen mit k = %(default)s, bis ein gültiger Wert gespeichert ist."
                )
                % {"k": k, "minimum": K_ANONYMITY_MIN, "default": effective_k_threshold(k)}
            )
        if k is not None and (low_k := k_threshold_warning(effective_k_threshold(k))) is not None:
            warnings.append(low_k)
        audit = self._audit_retention_warning()
        if audit is not None:
            warnings.append(audit)
        return warnings

    def _audit_retention_warning(self):
        """Warn when the audit log is pruned before the longest retention period ends.

        Compared are the periods of the settings and the own period of every event
        document type of the facility (active or not, as retention enforces it).
        ``auditlog_retention_months = 0`` keeps the audit log without limit and never
        warns; a retention period of ``0`` that means "kept without limit" is the
        longest period of all.
        """
        audit_months = self._current_value("auditlog_retention_months")
        if not audit_months:
            return None
        audit_days = audit_months * _DAYS_PER_MONTH
        longest_name: str | None = None
        longest_days: int | None = 0
        for name, (days_per_unit, zero_is_unlimited) in _RETENTION_PERIODS.items():
            value = self._current_value(name)
            if value is None:
                continue
            days = None if (value == 0 and zero_is_unlimited) else value * days_per_unit
            if days is None:
                longest_name, longest_days = name, None
                break
            if days > longest_days:
                longest_name, longest_days = name, days
        longest_label = None
        if longest_days is not None:
            longest_type = (
                DocumentType.objects.filter(
                    facility=self.facility,
                    entity_type=DocumentType.EntityType.EVENT,
                    retention_days__gt=longest_days,
                )
                .order_by("-retention_days", "name")
                .first()
            )
            if longest_type is not None:
                longest_days = longest_type.retention_days
                longest_label = _("Dokumentationstyp %(name)s") % {"name": longest_type.name}
        if longest_label is None and longest_name is not None:
            longest_label = getattr(Settings._meta.get_field(longest_name), "verbose_name", longest_name)
        if longest_label is None or (longest_days is not None and audit_days >= longest_days):
            return None
        period = _("unbegrenzt") if longest_days is None else _("%(days)s Tage") % {"days": longest_days}
        return _(
            "Die Audit-Log-Frist (%(audit_days)s Tage) ist kürzer als die längste Fach-Frist "
            "„%(field)s“ (%(period)s). Audit-Spuren werden gelöscht, während die zugehörigen "
            "Fachdaten noch bestehen können; lückenlos bleibt das Audit-Log nur mit 0."
        ) % {
            "audit_days": audit_days,
            "field": longest_label,
            "period": period,
        }
