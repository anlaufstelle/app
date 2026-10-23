"""Validated online review filters and mark/revoke input."""

from typing import cast

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import User
from core.services.free_text_review import TARGET_LABELS
from core.services.sensitive_patterns import PATTERN_LABELS


class ReviewFilterForm(forms.Form):
    model = forms.ChoiceField(label=_("Modell"))
    changed_before = forms.DateField(
        label=_("Geändert bis"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("Bei Episoden zählt das Erfassungsdatum, da kein Änderungsdatum vorliegt."),
    )
    editor = forms.ChoiceField(label=_("Erfasst oder geprüft von"), required=False)
    risk = forms.ChoiceField(
        label=_("Musterhinweis"),
        required=False,
        choices=[("", _("Alle")), *PATTERN_LABELS.items()],
    )

    def __init__(self, *args, facility, user, entries=(), **kwargs):
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["model"]).choices = [
            (key, label)
            for key, label in TARGET_LABELS.items()
            if user.is_staff_or_above or key in {"WorkItem", "Event"}
        ]
        editor_ids = {
            user_id
            for entry in entries
            for user_id in (entry.author_id, entry.review.reviewed_by_id if entry.review else None)
            if user_id is not None
        }
        cast(forms.ChoiceField, self.fields["editor"]).choices = [
            ("", _("Alle")),
            *(
                (str(person.pk), person.display_name or person.username)
                for person in User.objects.filter(facility=facility, pk__in=editor_ids).order_by("username")
            ),
        ]
        for field in self.fields.values():
            field.widget.attrs["class"] = "rounded border border-ink-muted bg-surface p-2 text-ink"


class FreeTextReviewForm(forms.Form):
    action = forms.ChoiceField(choices=[("mark", _("Als geprüft markieren")), ("revoke", _("Prüfung zurücknehmen"))])
    reviewed_until = forms.DateField(
        label=_("Geprüft bis"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    fingerprint = forms.CharField(required=False, max_length=64, widget=forms.HiddenInput)

    def clean(self):
        data = super().clean() or {}
        if data.get("action") == "mark":
            until = data.get("reviewed_until")
            if until is None or until < timezone.localdate():
                self.add_error("reviewed_until", _("Bitte wählen Sie heute oder ein zukünftiges Datum."))
            if not data.get("fingerprint"):
                self.add_error("fingerprint", _("Bitte laden Sie die Liste neu und prüfen Sie den Text erneut."))
        return data
