"""Forms for WorkItem management."""

from datetime import date

from django import forms
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from core.forms.widgets import INPUT_CSS
from core.models import User, WorkItem
from core.models.client import Client


def max_workitem_date() -> date:
    """Obere Schranke für ``due_date``/``remind_at``: 31.12. des Folgejahrs.

    Refs #708: Aufgaben sollen nicht beliebig weit in die Zukunft datierbar
    sein (z. B. 05.05.3345 verschwindet praktisch vom Radar). Wiederkehrende
    Aufgaben decken längere Zeiträume bereits über ``recurrence`` ab.
    """
    return date(date.today().year + 1, 12, 31)


def min_workitem_date() -> date:
    """Untere Schranke für ``due_date``/``remind_at``: heute.

    Refs #711: Aufgaben mit Fälligkeit in der Vergangenheit anlegen ergibt
    keinen Sinn — sie wären sofort überfällig. Beim Edit eines bereits
    überfälligen Items prüfen wir nur, wenn das Datum tatsächlich geändert
    wurde (vgl. ``Form.changed_data``).
    """
    return date.today()


class WorkItemForm(forms.ModelForm):
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
            "item_type": forms.Select(attrs={"class": INPUT_CSS}),
            "title": forms.TextInput(attrs={"class": INPUT_CSS}),
            "description": forms.Textarea(
                attrs={
                    "class": INPUT_CSS,
                    "rows": 4,
                }
            ),
            "priority": forms.Select(attrs={"class": INPUT_CSS}),
            # Explizites ISO-Format (Refs #619): HTML5 `<input type="date">`
            # akzeptiert nur `YYYY-MM-DD`; ohne `format=` nimmt Django den
            # ersten Eintrag aus DATE_INPUT_FORMATS — unter LANGUAGE_CODE=de-DE
            # ist das `%d.%m.%Y`, wodurch der Prefill beim Edit leer bliebe.
            "due_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "type": "date",
                    "class": INPUT_CSS,
                    "min": min_workitem_date().isoformat(),
                    "max": max_workitem_date().isoformat(),
                },
            ),
            "remind_at": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "type": "date",
                    "class": INPUT_CSS,
                    "min": min_workitem_date().isoformat(),
                    "max": max_workitem_date().isoformat(),
                },
            ),
            "recurrence": forms.Select(attrs={"class": INPUT_CSS}),
            "assigned_to": forms.Select(attrs={"class": INPUT_CSS}),
        }

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        if facility:
            # Refs #1125: ASSISTANT ist zuweisbar. Assistenzkräfte ziehen offene
            # Teamaufgaben ohnehin per "Annehmen" auf sich (Auto-Assign auf
            # IN_PROGRESS) und sind damit faktisch ``assigned_to``; eine normale,
            # nicht-private Aufgabe (private Aufgaben aus #607 existieren noch
            # nicht) muss einer aktiven Assistenz derselben Facility direkt
            # zuweisbar sein. Korrigiert die frühere #867-Annahme.
            self.fields["assigned_to"].queryset = User.objects.filter(  # type: ignore[attr-defined]
                facility=facility,
                is_active=True,
                role__in=[
                    User.Role.FACILITY_ADMIN,
                    User.Role.LEAD,
                    User.Role.STAFF,
                    User.Role.ASSISTANT,
                ],
            ).order_by("username")
        self.fields["assigned_to"].required = False
        self.fields["description"].required = False
        # Recurrence has a DB default (NONE) — allow omitting it in POST.
        self.fields["recurrence"].required = False

        # Refs #1131: Beim Edit eines bereits überfälligen Items das HTML5-
        # ``min`` auf den Bestandswert absenken. ``clean()`` erlaubt den
        # unveränderten Vergangenheitswert (``changed_data``-Check), aber das
        # statische ``min=heute`` der Widget-Definition würde die Browser-
        # Native-Validation den Prefill verwerfen lassen — das Item ließe sich
        # in der Oberfläche nicht speichern. Mit ``min == Bestandsdatum`` nimmt
        # der Browser den unveränderten Wert an und blockiert weiterhin ein
        # *noch früheres* Datum; das aktive Verschieben auf ein anderes
        # Vergangenheits-Datum fängt serverseitig ``clean()`` ab.
        min_date = min_workitem_date()
        for field_name in ("due_date", "remind_at"):
            existing = getattr(self.instance, field_name, None)
            if existing and existing < min_date:
                self.fields[field_name].widget.attrs["min"] = existing.isoformat()

        # Refs #710: Browser-native HTML5-Validation-Tooltips folgen der
        # Browser-Sprache. Wir reichen lokalisierte Custom-Messages als
        # data-Attribute durch, ein DOMContentLoaded-Listener in
        # ``src/static/js/date-input-i18n.js`` (vor #911: ``alpine-components.js``)
        # ruft damit ``setCustomValidity`` auf.
        max_str = date_format(max_workitem_date(), "DATE_FORMAT")
        too_late = _("Das Datum darf höchstens am %(max)s liegen.") % {"max": max_str}
        too_early = _("Das Datum darf nicht in der Vergangenheit liegen.")
        for field_name in ("due_date", "remind_at"):
            attrs = self.fields[field_name].widget.attrs
            attrs["data-msg-too-early"] = too_early
            attrs["data-msg-too-late"] = too_late

    def clean_client(self):
        client_id = self.cleaned_data.get("client")
        if not client_id:
            return None
        try:
            return Client.objects.filter(
                pk=client_id,
                facility=self.facility,
            ).get()
        except Client.DoesNotExist as exc:
            raise forms.ValidationError(_("Ungültige Person-ID")) from exc  # pragma: no mutate

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
