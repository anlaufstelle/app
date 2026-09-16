"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.forms.workitems``.

Quelle: Vollmutation #2174, Batch ``forms-2``
(``welle-l-0.99/reports/m-scratch/batch-forms-2.json``,
Triage ``welle-l-0.99/reports/m-scratch/pins-forms-2/triage-099-forms-2.json``). Die Batch hat 22
Survivor-Mutanten in ``WorkItemForm`` (``__init__``, ``clean``); 9 davon werden hier mit fünf
Tests gepinnt. 6 Mutanten sind ``equivalent`` (Django-eigene Rückfallregeln bzw. ein Default-Wert,
der bereits mit dem Widget-Default übereinstimmt) und 7 sind ``skip`` (reine
Groß-/Kleinschreibungs- bzw. ``XX``-Markierungs-Varianten von Hinweistexten) — siehe
Triage-Begründungen, keine weitere Testdatei nötig.

Vier Themen:

* **Die Rollen-Allowlist des ``assigned_to``-Querysets** (Prio 1, einziger Prio-1-Fund der
  Batch): ``role__in=[FACILITY_ADMIN, LEAD, STAFF, ASSISTANT]`` entfällt komplett — ohne den
  Filter wäre jede aktive Person der Einrichtung zuweisbar, auch eine installationsweite
  ``SUPER_ADMIN``, die laut Rollenmodell (``core/models/user.py``) nicht Teil des operativen
  Aufgabenteams einer Einrichtung ist.
* **Die JS-Validierungshinweise am Datumswidget** (``data-msg-too-early``/``data-msg-too-late``):
  fünf Mutanten setzen den berechneten Text direkt oder über eine Zwischenvariable auf ``None`` —
  beobachtbar als das Literal ``"None"`` im Attributwert statt eines echten Hinweistexts.
* **Die Fehlermeldung bei überschrittenem Höchstdatum** enthält unter einem Mutanten ebenfalls
  wörtlich ``"None"`` statt des tatsächlichen Datums.
* **Die Grenzwerte der Datumsprüfung in ``clean()``**: zwei Mutanten verschärfen ``>``/``<`` zu
  ``>=``/``<=`` — ein ``remind_at`` GENAU auf der oberen bzw. unteren Schranke würde dann
  fälschlich abgelehnt, obwohl das Original es zulässt.

Konventionen wie ``test_forms_workitems.py``: ``_form_helpers`` für Standard-Assertions,
``facility``-Fixture aus ``conftest.py``, ``_minimal_data`` als lokaler Ersatz für dessen
private ``_base_data``-Hilfsfunktion (Bestandsdatei bleibt unangetastet).
"""

from __future__ import annotations

from datetime import date

import pytest

from core.forms.workitems import WorkItemForm, max_workitem_date, min_workitem_date
from core.models import User, WorkItem
from tests._form_helpers import queryset_pks

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _minimal_data(**overrides) -> dict[str, str]:
    """Minimal gültige POST-Daten für ein neues WorkItem — pro Test überschreibbar."""
    data: dict[str, str] = {
        "item_type": WorkItem.ItemType.TASK,
        "title": "Pin099",
        "priority": WorkItem.Priority.NORMAL,
        "due_date": max_workitem_date().isoformat(),
        "recurrence": WorkItem.Recurrence.NONE,
    }
    data.update(overrides)
    return data


class TestAssignedToRoleAllowlist:
    def test_assigned_to_queryset_excludes_roles_outside_the_allowlist(self, facility, staff_user):
        """Pins ``xǁWorkItemFormǁ__init____mutmut_14``: die ``role__in``-Liste
        (``FACILITY_ADMIN``/``LEAD``/``STAFF``/``ASSISTANT``) entfällt komplett — jede
        aktive Person der Einrichtung wäre zuweisbar, auch eine installationsweite
        ``SUPER_ADMIN``, die NICHT Bestandteil des operativen Aufgabenteams einer
        Einrichtung ist."""
        super_admin = User.objects.create_user(
            username="pin099-super-admin",
            role=User.Role.SUPER_ADMIN,
            facility=facility,
            is_staff=True,
            is_active=True,
        )

        form = WorkItemForm(facility=facility)
        pks = queryset_pks(form, "assigned_to")

        assert super_admin.pk not in pks
        assert staff_user.pk in pks


class TestDateWidgetMessageAttrs:
    def test_date_widget_hinweistexte_sind_kein_literales_none(self, facility):
        """Pins ``xǁWorkItemFormǁ__init____mutmut_46`` (``max_str=None`` färbt
        ``too_late`` mit dem wörtlichen Text ``None``), ``…__mutmut_53``/``…_71``
        (``too_early``/``attrs["data-msg-too-early"]`` direkt auf ``None``) und
        ``…__mutmut_61``/``…_74`` (``too_late``/``attrs["data-msg-too-late"]`` auf
        ``None``): die JS-Validierungshinweise an ``due_date``/``remind_at`` müssen
        echten, übersetzten Text tragen statt der Python-Stringform von ``None``."""
        form = WorkItemForm(facility=facility)

        for field_name in ("due_date", "remind_at"):
            attrs = form.fields[field_name].widget.attrs
            too_early = attrs["data-msg-too-early"]
            too_late = attrs["data-msg-too-late"]

            assert too_early is not None
            assert too_late is not None
            assert "None" not in too_early
            assert "None" not in too_late


class TestCleanMaxDateMessage:
    def test_due_date_ueberschreitung_meldet_kein_literales_none(self, facility):
        """Pins ``xǁWorkItemFormǁclean__mutmut_15``: ``max_str=None`` ließe die
        Fehlermeldung bei überschrittenem Höchstdatum wörtlich ,…höchstens am None
        liegen.' lauten statt das tatsächliche Datum zu nennen."""
        far_future = date(max_workitem_date().year + 5, 1, 1)
        form = WorkItemForm(data=_minimal_data(due_date=far_future.isoformat()), facility=facility)

        assert not form.is_valid()
        message = form.errors["due_date"][0]
        assert "None" not in message


class TestCleanGrenzwerte:
    def test_remind_at_exakt_am_maximaldatum_ist_gueltig(self, facility):
        """Pins ``xǁWorkItemFormǁclean__mutmut_35``: ``>`` wird zu ``>=`` — ein
        ``remind_at`` GENAU am erlaubten Maximum (31.12. des Folgejahrs) wäre dann
        fälschlich abgelehnt statt wie im Original gültig zu sein."""
        data = _minimal_data(remind_at=max_workitem_date().isoformat())
        del data["due_date"]
        form = WorkItemForm(data=data, facility=facility)

        assert form.is_valid(), dict(form.errors)

    def test_remind_at_exakt_heute_bei_neuer_aufgabe_ist_gueltig(self, facility):
        """Pins ``xǁWorkItemFormǁclean__mutmut_55``: ``<`` wird zu ``<=`` — ein
        ``remind_at`` GENAU auf der unteren Schranke (heute) wäre bei einer NEUEN
        Aufgabe (das Feld ist zwangsläufig in ``changed_data``) fälschlich als
        Vergangenheitsdatum abgelehnt."""
        data = _minimal_data(remind_at=min_workitem_date().isoformat())
        del data["due_date"]
        form = WorkItemForm(data=data, facility=facility)

        assert form.is_valid(), dict(form.errors)
