"""Forms for WorkItem management."""

from datetime import date
from typing import cast

from django import forms
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from core.forms.mixins import PinnedClientMixin, TailwindFormMixin
from core.models import User, WorkItem
from core.services.case.workitems import assignable_workitem_users
from core.utils.date_input import AppDateInput


def max_workitem_date() -> date:
    """Obere Schranke für ``due_date``/``remind_at``: 31.12. des Folgejahrs.

    Refs #708: Aufgaben sollen nicht beliebig weit in die Zukunft datierbar
    sein (z. B. 05.05.3345 verschwindet praktisch vom Radar). Wiederkehrende
    Aufgaben decken längere Zeiträume bereits über ``recurrence`` ab.
    """
    return date(timezone.localdate().year + 1, 12, 31)


def min_workitem_date() -> date:
    """Untere Schranke für ``due_date``/``remind_at``: heute.

    Refs #711: Aufgaben mit Fälligkeit in der Vergangenheit anlegen ergibt
    keinen Sinn — sie wären sofort überfällig. Beim Edit eines bereits
    überfälligen Items prüfen wir nur, wenn das Datum tatsächlich geändert
    wurde (vgl. ``Form.changed_data``).
    """
    return timezone.localdate()


class WorkItemForm(TailwindFormMixin, PinnedClientMixin, forms.ModelForm):
    """Form for creating and editing WorkItems."""

    client = forms.UUIDField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = WorkItem
        fields = [
            "item_type",
            "title",
            "description",
            "priority",
            "due_date",
            "remind_at",
            "recurrence",
            "assigned_to",
        ]
        widgets = {
            "description": forms.Textarea(
                attrs={
                    "rows": 4,
                }
            ),
            # Explizites ISO-Format (Refs #619): HTML5 `<input type="date">`
            # akzeptiert nur `YYYY-MM-DD`; ohne `format=` nimmt Django den
            # ersten Eintrag aus DATE_INPUT_FORMATS — unter LANGUAGE_CODE=de-DE
            # ist das `%d.%m.%Y`, wodurch der Prefill beim Edit leer bliebe.
            # The ``min``/``max`` bounds are set per instance in ``__init__``;
            # values computed here would freeze at import time (Refs #2380).
            "due_date": AppDateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "remind_at": AppDateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        }

    def __init__(self, *args, facility=None, pinned_client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        # Ursprungs-Pinning (Refs #1786): nur beim Bearbeiten einer Aufgabe,
        # die bereits eine Person trägt. Eine personenlose Aufgabe bekommt
        # ``pinned_client=None`` und behält die freie Auswahl (Attach-Fall,
        # Maintainer-Entscheid 2026-08-05).
        self.pinned_client = pinned_client
        if facility:
            assignee_field = cast(forms.ModelChoiceField, self.fields["assigned_to"])
            recipients = assignable_workitem_users(facility)
            if not self.instance._state.adding and self.instance.facility_id == facility.pk:
                # Refs #2544: retain only this task's current facility member.
                # New assignments remain subject to the service's locked check.
                current_id = self.instance.assigned_to_id
                recipients = recipients | User.objects.filter(facility=facility, pk=current_id)
                current_label = _("aktuell")
                assignee_field.label_from_instance = lambda obj: (  # type: ignore[method-assign]
                    f"{obj} ({current_label})" if obj.pk == current_id else str(obj)
                )
            assignee_field.queryset = recipients
        self.fields["assigned_to"].required = False
        # Refs #2381: Django 6.1 faellt ohne eigenes ``empty_label`` auf den
        # unuebersetzten Blank-Choice-Text ("- Select an option -") zurueck.
        self.fields["assigned_to"].empty_label = _("— Nicht zugewiesen —")  # type: ignore[attr-defined]
        self.fields["description"].required = False
        # Recurrence has a DB default (NONE) — allow omitting it in POST.
        self.fields["recurrence"].required = False

        # Refs #2380: the HTML5 bounds are computed when the form is built, so
        # a long-running server process always offers the range of the current
        # day instead of the day the module was imported.
        # Refs #1131: when editing an item that is already overdue, ``min``
        # drops to the stored date. ``clean()`` accepts the unchanged past
        # value (``changed_data`` check); with ``min == stored date`` the
        # browser accepts the prefill and still blocks an even earlier date,
        # while moving it to another past date is rejected by ``clean()``.
        min_date = min_workitem_date()
        max_date = max_workitem_date()
        for field_name in ("due_date", "remind_at"):
            existing = getattr(self.instance, field_name, None)
            field_min = existing if existing and existing < min_date else min_date
            attrs = self.fields[field_name].widget.attrs
            attrs["min"] = field_min.isoformat()
            attrs["max"] = max_date.isoformat()

        # Refs #710: Browser-native HTML5-Validation-Tooltips folgen der
        # Browser-Sprache. Wir reichen lokalisierte Custom-Messages als
        # data-Attribute durch, ein DOMContentLoaded-Listener in
        # ``src/static/js/date-input-i18n.js`` (vor #911: ``alpine-components.js``)
        # ruft damit ``setCustomValidity`` auf.
        max_str = date_format(max_date, "DATE_FORMAT")
        too_late = _("Das Datum darf höchstens am %(max)s liegen.") % {"max": max_str}
        too_early = _("Das Datum darf nicht in der Vergangenheit liegen.")
        for field_name in ("due_date", "remind_at"):
            attrs = self.fields[field_name].widget.attrs
            attrs["data-msg-too-early"] = too_early
            attrs["data-msg-too-late"] = too_late

    def clean_client(self):
        # Pin-Guard und Widerrufs-Gate liegen in
        # :class:`~core.forms.mixins.PinnedClientMixin`; eigen ist hier, dass
        # eine personenlose Aufgabe erlaubt bleibt (Attach-Fall,
        # Maintainer-Entscheid 2026-08-05).
        #
        # Refs #1771: Der WorkItem-Pfad ist der heikelste Bestand am Gate:
        # ``title`` und ``description`` sind Freitext, und es gibt KEINEN
        # nachgelagerten Prune — ``_redact_workitems`` laeuft ausschliesslich
        # aus den beiden Anonymisierungs-Kaskaden. Ein Replay nach der
        # Anonymisierung bliebe also unbefristet als Klartext an einer
        # redigierten Person stehen (docs/security-notes.md § Offline-Widerruf).
        client_id = self.apply_client_pin(self.cleaned_data.get("client"))
        if not client_id:
            return None
        return self.resolve_client(
            client_id,
            missing_error=_("Ungültige Person-ID"),  # pragma: no mutate
        )

    def clean(self):
        cleaned = super().clean() or {}
        remind_at = cleaned.get("remind_at")
        due_date = cleaned.get("due_date")
        if remind_at and due_date and remind_at > due_date:
            raise forms.ValidationError({"remind_at": _("Die Erinnerung muss vor oder am Fälligkeitstag liegen.")})  # pragma: no mutate  # noqa: E501  # fmt: skip

        max_date = max_workitem_date()
        max_str = date_format(max_date, "DATE_FORMAT")
        if due_date and due_date > max_date:
            raise forms.ValidationError(
                {"due_date": _("Das Fälligkeitsdatum darf höchstens am %(max)s liegen.") % {"max": max_str}}
            )
        if remind_at and remind_at > max_date:
            raise forms.ValidationError(
                {"remind_at": _("Die Erinnerung darf höchstens am %(max)s liegen.") % {"max": max_str}}
            )

        # Refs #711: Vergangene Daten nur prüfen, wenn das Feld tatsächlich
        # geändert wurde — sonst würde der Edit-Save eines bereits überfälligen
        # Items immer fehlschlagen.
        min_date = min_workitem_date()
        if due_date and due_date < min_date and "due_date" in self.changed_data:
            raise forms.ValidationError({"due_date": _("Das Fälligkeitsdatum darf nicht in der Vergangenheit liegen.")})  # pragma: no mutate  # noqa: E501  # fmt: skip
        if remind_at and remind_at < min_date and "remind_at" in self.changed_data:
            raise forms.ValidationError({"remind_at": _("Die Erinnerung darf nicht in der Vergangenheit liegen.")})  # pragma: no mutate  # noqa: E501  # fmt: skip
        return cleaned
