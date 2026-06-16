"""Follow-Up-Tests für Mutation-Survivors in ``core.forms`` (Refs #943).

Voll-Run zeigte Bucket-Killrate ``core.forms`` brutto 76.79 %,
adjustiert 80 %. Ziel ist ≥ 85 %.

Survivor-Konzentration nach Funktion (Top-Cluster):

- ``forms.events.DynamicEventDataForm.clean`` — 23 surv
- ``forms.workitems.WorkItemForm.__init__`` — 20 surv  (Hauptansatz dieses Files)
- ``forms.workitems.WorkItemForm.clean`` — 13 surv
- ``forms.episodes.EpisodeForm.__init__`` — 10 surv

Pragma-Pass (commit [`<pending>`]) hat die UI-Label-Strings adressiert.
Verbleibende Survivors sind primär:

1. **`required = None/True`** statt False — Logic-Mutations in __init__-Phase
2. **Queryset-Filter-Werte** mutiert (`is_active=True` → False, Role-Liste)
3. **`order_by(...)`-Argument** mutiert (Strings)
4. **Boundary-Werte** in clean()-Methoden

Dieses File schließt die ``required is False``-Lücke explizit
(Identitäts-Check ``is False``, nicht ``not required`` — fängt
``required = None`` als Mutation).
"""

from __future__ import annotations

import pytest

from core.forms.episodes import EpisodeForm
from core.forms.workitems import WorkItemForm


@pytest.mark.django_db
class TestEpisodeFormFieldRequired:
    """Identitäts-Checks für ``required is False`` in EpisodeForm.__init__.

    Bestehender Test ``test_ended_at_and_description_optional`` (in
    test_forms_cases.py) prüft VERHALTEN (Form ist valide ohne diese
    Felder) — aber ``required = None`` würde Django auch akzeptieren,
    weil Django ``None`` als falsy auswertet. Daher hier ein expliziter
    ``is False``-Check.
    """

    def test_ended_at_required_is_explicitly_false(self):
        form = EpisodeForm()
        # ``is False`` (nicht ``== False`` oder Truthiness) fängt mutmut-
        # Mutationen wie ``required = None`` (None is False → False).
        assert form.fields["ended_at"].required is False

    def test_description_required_is_explicitly_false(self):
        form = EpisodeForm()
        assert form.fields["description"].required is False


@pytest.mark.django_db
class TestWorkItemFormFieldRequired:
    """Identitäts-Checks für ``required is False`` in WorkItemForm.__init__."""

    def test_assigned_to_required_is_explicitly_false(self, facility):
        form = WorkItemForm(facility=facility)
        assert form.fields["assigned_to"].required is False

    def test_description_required_is_explicitly_false(self, facility):
        form = WorkItemForm(facility=facility)
        assert form.fields["description"].required is False

    def test_recurrence_required_is_explicitly_false(self, facility):
        form = WorkItemForm(facility=facility)
        assert form.fields["recurrence"].required is False


@pytest.mark.django_db
class TestWorkItemFormAssignedQueryset:
    """Branch-Tests für ``assigned_to``-Queryset-Filter (facility, role, is_active).

    Mutmut mutiert ``is_active=True`` → ``False``, Rollen-Liste-Elemente,
    ``order_by("username")`` → andere Strings. Tests stellen sicher, dass
    das Queryset das erwartete Set liefert.
    """

    def test_queryset_includes_all_active_facility_roles(
        self, facility, admin_user, lead_user, staff_user, assistant_user
    ):
        form = WorkItemForm(facility=facility)
        qs = form.fields["assigned_to"].queryset
        assert admin_user in qs
        assert lead_user in qs
        assert staff_user in qs
        # Refs #1125: ASSISTANT ist jetzt zuweisbar (korrigiert #867-Annahme).
        assert assistant_user in qs

    def test_queryset_excludes_inactive_assistant(self, facility, assistant_user):
        """Refs #1125: Eine *deaktivierte* Assistenz bleibt aus dem Queryset.

        Hält den ``is_active=True``-Branch unter Mutationsdruck, jetzt da die
        ASSISTANT-Rolle selbst nicht mehr ausschließt.
        """
        assistant_user.is_active = False
        assistant_user.save(update_fields=["is_active"])
        form = WorkItemForm(facility=facility)
        qs = form.fields["assigned_to"].queryset
        assert assistant_user not in qs

    def test_queryset_excludes_inactive_users(self, facility, lead_user):
        lead_user.is_active = False
        lead_user.save()
        form = WorkItemForm(facility=facility)
        qs = form.fields["assigned_to"].queryset
        assert lead_user not in qs

    def test_queryset_excludes_other_facility(self, facility, second_facility):
        from core.models.user import User

        outsider = User.objects.create_user(
            username="outsider_lead",
            password="anlaufstelle2026",
            facility=second_facility,
            role=User.Role.LEAD,
        )
        form = WorkItemForm(facility=facility)
        qs = form.fields["assigned_to"].queryset
        assert outsider not in qs

    def test_queryset_ordered_by_username(self, facility):
        """``order_by("username")``-Mutation killing: explizite Reihenfolge
        bestätigen. Drei User mit aufsteigenden Usernames werden alphabetisch
        sortiert zurückgegeben.
        """
        from core.models.user import User

        # Saubere Reihenfolge: a, b, c
        User.objects.create_user(
            username="zzz_user", password="anlaufstelle2026", facility=facility, role=User.Role.LEAD
        )
        User.objects.create_user(
            username="aaa_user", password="anlaufstelle2026", facility=facility, role=User.Role.LEAD
        )
        User.objects.create_user(
            username="mmm_user", password="anlaufstelle2026", facility=facility, role=User.Role.LEAD
        )
        form = WorkItemForm(facility=facility)
        usernames = list(form.fields["assigned_to"].queryset.values_list("username", flat=True))
        # Aufsteigende Ordnung: aaa < mmm < zzz
        assert usernames.index("aaa_user") < usernames.index("mmm_user") < usernames.index("zzz_user")
