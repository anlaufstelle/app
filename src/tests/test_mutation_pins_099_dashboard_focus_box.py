"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — ``core.services.dashboard.focus_box``,
Batch ``dashboard-1`` (Vollmutation-Survivor-Triage, 52 Survivors in ``build_focus_box``).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-dashboard-1.json`` (Triage
``reports/m-scratch/pins-dashboard-1/triage-099-dashboard-1.json``).

``build_focus_box()`` sortiert offene/laufende Team-Aufgaben über eine
mehrstufige DB-Query (``order_by("due_date_bucket", "due_date",
"priority_order", "-created_at")``) und begrenzt die Ausgabe auf
``FOCUS_BOX_LIMIT = 8`` Eintraege — welche 8 das sind, entscheidet exakt
diese Sortierung. Alle Survivors dieser Datei mutieren entweder die beiden
``Case()``-Hilfsannotationen (``priority_order``, ``due_date_bucket``), die
``order_by``-Tupel-Elemente selbst, die ``select_related``-Prefetch-Liste,
die Schleifensteuerung beim Einsammeln der Treffer oder den ``"label"``-Key
der Gruppen-Dicts.

**Warum das mehr als Kosmetik ist:** Bei ``FOCUS_BOX_LIMIT = 8`` entscheidet
die Sortierung nicht nur die Reihenfolge, sondern WELCHE Aufgaben überhaupt
gezeigt werden. Ein falsch sortierter Survivor kann eine ueberfaellige oder
heute faellige Aufgabe komplett aus der Box verdraengen — die Team-Fokusbox
soll aber genau auf akuten Handlungsbedarf aufmerksam machen (Refs #1128).
Das ist keine Darstellungsfrage, sondern ein fachlicher Fristen-Fehler
(Prio 1).

Empirisch verifiziert (nicht nur aus dem Diff geraten, Lehre 137): jede
Pin-Idee wurde vor dem Schreiben gegen eine dynamisch aus dem Mutantenbaum
geladene Kopie der jeweiligen ``x_build_focus_box__mutmut_<n>``-Funktion
laufen gelassen (Scratch-Explorer, nicht Teil dieses Commits). Dabei zeigte
sich u.a.: Case()-Mutationen, die eine Teilgruppe auf ``NULL`` setzen,
werden von Postgres unter ASC-Sortierung IMMER ans Ende sortiert
(``NULLS LAST``) — das ist fuer die je LETZTE Stufe einer Case-Kette
(``NORMAL`` bei ``priority_order``, ``due_date__isnull`` bei
``due_date_bucket``) bereits die korrekte Position UND ohne fachliche
Wirkung; siehe ``skip``/``equivalent``-Liste im Bericht.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import WorkItem
from core.services.dashboard import build_focus_box

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _make_item(facility, user, **kwargs):
    defaults = {
        "facility": facility,
        "created_by": user,
        "item_type": WorkItem.ItemType.TASK,
        "status": WorkItem.Status.OPEN,
        "title": "Aufgabe",
    }
    defaults.update(kwargs)
    return WorkItem.objects.create(**defaults)


def _group(box, key):
    return next(g for g in box["groups"] if g["key"] == key)


class TestPriorityOrderWithinSameDueDateTier:
    """``priority_order``-``Case()`` (Zeilen der ``annotate()`` in ``build_focus_box``).

    Drei Aufgaben mit identischem Faelligkeitstag (alle ueberfaellig), aber
    unterschiedlicher Prioritaet: URGENT muss vor IMPORTANT vor NORMAL
    stehen — das ist der einzige Zweck von ``priority_order``.
    """

    def test_urgent_important_normal_stay_strictly_ordered_when_overdue_on_the_same_day(self, facility, staff_user):
        """Killt folgende Survivors ueber EINE Assertion:

        * ``x_build_focus_box__mutmut_9`` — ``priority_order`` faellt komplett
          aus dem ``order_by()``-Tupel, Tie-Break wird ``-created_at``.
        * ``_29``/``_33``/``_34``/``_36``/``_37`` — die URGENT-``When``-Klausel
          verschwindet/matcht nie/liefert ``then=None`` ⇒ URGENT-Zeilen werden
          zu ``NULL`` (sortiert unter ASC ans Ende statt an den Anfang).
        * ``_30``/``_39``/``_40``/``_42``/``_43`` — dieselbe Familie fuer
          IMPORTANT.
        * ``_38`` — URGENT bekommt ``then=Value(1)`` statt ``Value(0)`` und
          kollidiert mit IMPORTANT (Tie-Break durch ``-created_at``, falsche
          Reihenfolge).
        * ``_44`` — IMPORTANT bekommt ``then=Value(2)`` statt ``Value(1)`` und
          kollidiert mit NORMAL.

        Gegenprobe zur Aequivalenz-Klasse: eine NORMAL-Mutation (``_31`` u.a.)
        wuerde NICHT auffallen, weil NORMAL ohnehin die letzte Stufe ist —
        bewusst nicht Teil dieses Pins (siehe Bericht, Abschnitt 3).
        """
        today = timezone.localdate()
        urgent = _make_item(
            facility,
            staff_user,
            title="Dringend",
            due_date=today - timedelta(days=1),
            priority=WorkItem.Priority.URGENT,
        )
        important = _make_item(
            facility,
            staff_user,
            title="Wichtig",
            due_date=today - timedelta(days=1),
            priority=WorkItem.Priority.IMPORTANT,
        )
        normal = _make_item(
            facility, staff_user, title="Normal", due_date=today - timedelta(days=1), priority=WorkItem.Priority.NORMAL
        )

        box = build_focus_box(facility)

        overdue = _group(box, "overdue")
        assert [wi.title for wi in overdue["items"]] == [urgent.title, important.title, normal.title]


class TestCreatedAtBreaksFullTies:
    """``order_by(..., "-created_at")`` als letzter Tie-Break."""

    def test_newer_item_sorts_first_when_bucket_due_date_and_priority_are_all_equal(self, facility, staff_user):
        """Killt ``x_build_focus_box__mutmut_10`` (``-created_at`` faellt aus
        dem ``order_by()``-Tupel). Zwei Aufgaben sind in ``due_date_bucket``,
        ``due_date`` UND ``priority_order`` vollstaendig gleich (beide
        ueberfaellig am selben Tag, beide NORMAL) — ohne den expliziten
        Tie-Break waere die Reihenfolge nicht mehr durch den Code garantiert
        (Postgres liefert dann Scan-Reihenfolge statt „neueste zuerst").
        """
        today = timezone.localdate()
        first = _make_item(facility, staff_user, title="Zuerst angelegt", due_date=today - timedelta(days=1))
        second = _make_item(facility, staff_user, title="Danach angelegt", due_date=today - timedelta(days=1))

        box = build_focus_box(facility)

        overdue = _group(box, "overdue")
        assert [wi.title for wi in overdue["items"]] == [second.title, first.title]


class TestFocusBoxLimitPicksTheRightEightTasks:
    """``due_date_bucket``-``Case()`` UND ``FOCUS_BOX_LIMIT = 8`` zusammen:

    Welche 8 Aufgaben gezeigt werden, haengt direkt an der Sortier-Stufe
    ``due_date_bucket``. Alle drei Tests hier bauen bewusst MEHR als 8
    Kandidaten, damit ein falsch einsortierter Survivor eine tatsaechlich
    dringendere Aufgabe aus der Box verdraengt statt sie nur umzusortieren
    (reine Umsortierung innerhalb der angezeigten Menge waere durch die
    ``FOCUS_BOX_LIMIT``-Kappung u.U. gar nicht sichtbar).
    """

    def test_overdue_tasks_fill_the_limit_before_today_or_future_tasks_appear(self, facility, staff_user):
        """Killt ``_56`` (``due_date__lt``-Klausel entfernt), ``_62``/``_64``/``_65``
        (``then`` von ``_56``s Klausel wird ``None`` per Wert/fehlendem Kwarg) —
        alle vier setzen ueberfaellige Zeilen auf ``due_date_bucket = NULL``
        (nicht die letzte Stufe der Case-Kette!) und schieben sie hinter
        „heute faellig" UND „ohne Faelligkeit" zurueck. Acht ueberfaellige
        Aufgaben plus je eine „heute"- und eine „zukuenftig+dringend"-Aufgabe:
        Original zeigt exakt die acht ueberfaelligen, der Survivor laesst
        stattdessen die beiden anderen Aufgaben hinein und verdraengt eine
        echte Ueberfaellige.
        """
        today = timezone.localdate()
        overdue_titles = []
        for offset in range(1, 9):
            item = _make_item(
                facility, staff_user, title=f"Ueberfaellig-{offset}", due_date=today - timedelta(days=offset)
            )
            overdue_titles.append(item.title)
        _make_item(facility, staff_user, title="Heute", due_date=today)
        _make_item(
            facility,
            staff_user,
            title="Zukunft-dringend",
            due_date=today + timedelta(days=5),
            priority=WorkItem.Priority.URGENT,
        )

        box = build_focus_box(facility)

        assert [g["key"] for g in box["groups"]] == ["overdue"]
        overdue = _group(box, "overdue")
        assert {wi.title for wi in overdue["items"]} == set(overdue_titles)
        assert box["shown_count"] == 8
        assert box["total_open_count"] == 10

    def test_due_today_tasks_fill_the_limit_before_future_tasks_appear(self, facility, staff_user):
        """Killt ``_57`` (``due_date=today``-Klausel entfernt), ``_67``
        (Bedingung wird ``due_date=None`` statt ``due_date=today`` — trifft
        dann faelschlich Aufgaben OHNE Faelligkeit statt die „heute
        faelligen"), ``_68``/``_70``/``_71`` (``then`` der Klausel wird
        ``None``). Alle fuenf setzen „heute faellig" auf
        ``due_date_bucket = NULL`` und schieben diese Aufgaben hinter eine
        zukuenftige. Acht „heute faellig"-Aufgaben plus eine
        zukuenftig+dringende: Original zeigt alle acht „heute"-Aufgaben, der
        Survivor laesst die zukuenftige mit hineinrutschen.
        """
        today = timezone.localdate()
        today_titles = []
        for idx in range(8):
            item = _make_item(facility, staff_user, title=f"Heute-{idx}", due_date=today)
            today_titles.append(item.title)
        _make_item(
            facility,
            staff_user,
            title="Zukunft-dringend",
            due_date=today + timedelta(days=5),
            priority=WorkItem.Priority.URGENT,
        )

        box = build_focus_box(facility)

        assert [g["key"] for g in box["groups"]] == ["today"]
        heute = _group(box, "today")
        assert {wi.title for wi in heute["items"]} == set(today_titles)
        assert box["shown_count"] == 8
        assert box["total_open_count"] == 9

    def test_future_due_priority_task_outranks_dateless_priority_tasks_within_limit(self, facility, staff_user):
        """Killt ``_58`` (``due_date__gt``-Klausel entfernt), ``_74``/``_76``/``_77``
        (``then`` der Klausel wird ``None``). Alle vier setzen „faellig in der
        Zukunft" auf ``due_date_bucket = NULL`` — das ist NICHT die letzte
        Stufe (die ist fuer „keine Faelligkeit" reserviert, Wert 9), die
        zukuenftige Aufgabe landet also faelschlich HINTER den Aufgaben ohne
        jede Faelligkeit. Acht dringende Aufgaben ohne Faelligkeitsdatum plus
        eine dringende mit einem Termin in 5 Tagen: Original zeigt die
        Termin-Aufgabe (frueheste Stufe) und sieben der acht terminlosen: der
        Survivor verdraengt die Termin-Aufgabe komplett.
        """
        today = timezone.localdate()
        dateless_titles = []
        for idx in range(8):
            item = _make_item(facility, staff_user, title=f"Ohne-Termin-{idx}", priority=WorkItem.Priority.URGENT)
            dateless_titles.append(item.title)
        future = _make_item(
            facility,
            staff_user,
            title="Mit-Termin",
            due_date=today + timedelta(days=5),
            priority=WorkItem.Priority.URGENT,
        )

        box = build_focus_box(facility)

        assert [g["key"] for g in box["groups"]] == ["priority"]
        prio = _group(box, "priority")
        shown_titles = [wi.title for wi in prio["items"]]
        assert future.title in shown_titles
        assert box["shown_count"] == 8
        assert box["total_open_count"] == 9


class TestSelectRelatedAvoidsNPlusOne:
    """``.select_related("client", "assigned_to")`` in der Query."""

    def test_client_and_assigned_to_access_cost_no_extra_queries(
        self, facility, staff_user, client_identified, django_assert_num_queries
    ):
        """Killt ``_17`` (``"client"`` fehlt in ``select_related``) und ``_18``
        (``"assigned_to"`` fehlt). Beide aendern weder Datenraum noch
        Reihenfolge — mutmut sieht sie nur ueber die Query-Anzahl. Die
        Konstruktion des Ergebnisses (``build_focus_box()`` selbst, das die
        Haupt-Query inkl. JOIN ausfuehrt) MUSS im Capture-Fenster liegen, sonst
        wird die fehlende Prefetch-Verknuepfung nicht sichtbar: greift man
        erst NACH dem Aufruf auf ``.client``/``.assigned_to`` zu, ist bei
        vorhandenem ``select_related`` die Zeile schon im Cache — der
        Unterschied zeigt sich nur, wenn beides zusammen gezaehlt wird.
        """
        today = timezone.localdate()
        for idx in range(3):
            _make_item(
                facility,
                staff_user,
                title=f"Mit-Bezug-{idx}",
                due_date=today - timedelta(days=1),
                assigned_to=staff_user,
                client=client_identified,
            )

        with django_assert_num_queries(2):
            box = build_focus_box(facility)
            for group in box["groups"]:
                for workitem in group["items"]:
                    # .client/.assigned_to (nicht .client_id/.assigned_to_id!) loesen
                    # bei fehlendem select_related() je eine Extra-Query aus —
                    # die *_id-Spalten liegen dagegen immer schon auf der Zeile.
                    assert workitem.client.id == client_identified.id
                    assert workitem.assigned_to.id == staff_user.id


class TestNonActionableItemIsSkippedNotALoopTerminator:
    """Die Sammel-Schleife in ``build_focus_box()`` (``if not key: continue``)."""

    def test_boring_open_item_without_pressure_does_not_cut_off_later_items(self, facility, staff_user):
        """Killt ``x_build_focus_box__mutmut_106`` (``continue`` → ``break``).

        Eine offene NORMAL-Aufgabe ohne Faelligkeit erzeugt laut Docstring von
        ``build_focus_box()`` bewusst KEINEN Handlungsdruck (``_bucket()``
        liefert ``""``) — sie soll uebersprungen werden, nicht das Einsammeln
        beenden. Reihenfolge hier: die „langweilige" Aufgabe wird NACH der
        laufenden Aufgabe angelegt, damit sie in der Sortierung (Tie-Break
        ``-created_at``) VOR ihr steht. Original sammelt trotzdem beide
        vorherigen/nachfolgenden Gruppen ein; der ``break``-Survivor bricht
        beim „langweiligen" Eintrag ab und verliert die laufende Aufgabe
        komplett.
        """
        today = timezone.localdate()
        _make_item(facility, staff_user, title="Ueberfaellig", due_date=today - timedelta(days=1))
        in_progress = _make_item(facility, staff_user, title="Laeuft", status=WorkItem.Status.IN_PROGRESS)
        _make_item(facility, staff_user, title="Langweilig-ohne-Druck")

        box = build_focus_box(facility)

        assert [g["key"] for g in box["groups"]] == ["overdue", "in_progress"]
        in_progress_group = _group(box, "in_progress")
        assert [wi.title for wi in in_progress_group["items"]] == [in_progress.title]
        assert box["shown_count"] == 2


class TestGroupDictUsesTheDocumentedLabelKey:
    """Der Dict-Comprehension-Key ``"label"`` in ``build_focus_box()``."""

    def test_group_label_key_carries_the_translated_group_heading(self, facility, staff_user):
        """Killt ``x_build_focus_box__mutmut_114`` (Key wird ``"XXlabelXX"``)
        und ``_115`` (Key wird ``"LABEL"``). Ein Template, das
        ``{{ group.label }}`` rendert, bekommt bei beiden Survivors eine
        leere Ueberschrift statt eines Fehlers (Djangos Template-Engine
        schluckt ``VariableDoesNotExist`` still) — deshalb pruefen wir den
        Key hier direkt am Dict, nicht ueber eine Template-Rendering-Probe.
        """
        today = timezone.localdate()
        _make_item(facility, staff_user, title="Ueberfaellig", due_date=today - timedelta(days=1))

        box = build_focus_box(facility)

        overdue = _group(box, "overdue")
        assert "label" in overdue
        assert overdue["label"] == _("Überfällig")
