"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Nacharbeits-Batch
``dashboard-1n`` für ``core.services.dashboard.focus_box``.

Quelle: ``reports/m-scratch/batch-dashboard-1n.json`` (ein Mutant:
``x_build_focus_box__mutmut_10`` — das letzte Tupel-Element ``"-created_at"``
fällt aus dem finalen ``.order_by("due_date_bucket", "due_date",
"priority_order", "-created_at")`` heraus).

Dieser Mutant hatte bereits einen Pin in
``test_mutation_pins_099_dashboard_focus_box.py``
(``TestCreatedAtBreaksFullTies::test_newer_item_sorts_first_when_bucket_due_date_and_priority_are_all_equal``).
Im Kill-Nachweis-Rerun überlebte die Mutante jedoch — der bestehende Pin ist
unter der Mutante NICHT deterministisch rot. Diese Datei ersetzt ihn NICHT
(bestehende Pin-Dateien bleiben laut Briefing unangetastet), sondern ergänzt
zwei unabhängige, tatsächlich deterministische Pins.

**Warum der alte Pin flackert (empirisch verifiziert, Lehre 137, Scratch-
Explorer nicht Teil dieses Commits):** ``EXPLAIN`` der Query ohne
``-created_at`` zeigt, dass der Planer für die Filterung
(``facility_id = … AND status IN (…)``) den zusammengesetzten Index
``workitem_facility_status_idx`` auf ``(facility, status, -created_at)``
per Index-Scan nutzt (Refs #638 — der Index wurde genau für dieses Sortier-
Suffix angelegt). Für zwei bei ``due_date_bucket``/``due_date``/
``priority_order`` gleichrangige Zeilen liefert dieser Index-Scan die Zeilen
bereits in ``-created_at``-Reihenfolge — der nachgelagerte ``Sort``-Knoten
über die drei verbliebenen ``order_by``-Spalten lässt diese bereits passende
Eingabereihenfolge bei Gleichstand unverändert. Das ist reines
Planner-/Index-Zufallsglück dieser Umgebung (Datensatzgröße, Statistik,
Postgres-Version) — kein SQL-Vertrag: ohne explizites ``-created_at`` im
``order_by()`` GARANTIERT nichts diese Reihenfolge, und genau das behauptet
die Mutante fälschlich als äquivalent.

**Pin A** (``TestCreatedAtTiebreakIsNotAnIncidentalIndexArtifact``) macht den
tatsächlichen Vertragsbruch sichtbar, indem er den Index für die Dauer der
Prüfung per ``SET LOCAL enable_indexscan/enable_bitmapscan/
enable_indexonlyscan = off`` aus dem Plan nimmt (Postgres rollt das beim
Transaktions-Ende des Tests automatisch zurück, keine Nebenwirkung auf andere
Tests). Unter dem dann erzwungenen Seq-Scan-Plan entscheidet bei Gleichstand
die physische Tupel-Reihenfolge — empirisch: die UMGEKEHRTE Einfüge-
reihenfolge. Acht gleichrangige Aufgaben werden angelegt und ihr
``created_at`` per ``update()`` (umgeht ``auto_now_add``) auf eine
Permutation gesetzt, die WEDER der Einfüge- noch der umgekehrten
Einfügereihenfolge entspricht — die beiden einzigen unter der Mutante
beobachteten Fallback-Ordnungen können die erwartete Reihenfolge damit nicht
zufällig treffen (verifiziert über 5 Wiederholungen, siehe Kill-Nachweis im
Bericht). Ohne den Index-Scan-Ausschluss würde dieser Pin — wie der alte —
unter der Mutante fälschlich grün bleiben; siehe Absatz oben.

**Pin B** (``TestOrderByCallCarriesTheCreatedAtTiebreak``) prüft den Vertrag
zusätzlich unmittelbar am Aufruf: ein Spy auf
``django.db.models.query.QuerySet.order_by`` zeichnet die tatsächlich
übergebenen Argumente auf und prüft, dass ``"-created_at"`` darunter ist —
unabhängig von jeder Planer-/Index-Entscheidung.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.db.models.query import QuerySet
from django.utils import timezone

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


class TestCreatedAtTiebreakIsNotAnIncidentalIndexArtifact:
    """``order_by(..., "-created_at")`` als letzter Tie-Break —
    plangesteuert erzwungener Seq-Scan, damit die Prüfung nicht vom
    zufällig ordnungserhaltenden ``workitem_facility_status_idx``
    profitiert (siehe Klassen-/Moduldocstring)."""

    def test_full_tie_group_sorts_strictly_by_created_at_desc_without_index_assist(self, facility, staff_user):
        """Killt ``x_build_focus_box__mutmut_10`` (``-created_at`` fällt aus
        dem ``order_by()``-Tupel).

        Acht Aufgaben sind in ``due_date_bucket``, ``due_date`` UND
        ``priority_order`` vollständig gleich (alle überfällig am selben
        Tag, alle NORMAL) — der einzige verbleibende Unterschied ist
        ``created_at``, per ``update()`` auf eine Permutation gesetzt, die
        weder der Einfüge- noch der umgekehrten Einfügereihenfolge
        entspricht. Der Index-Scan auf ``workitem_facility_status_idx``
        wird für diese Prüfung per Planer-GUC ausgeschlossen (``SET LOCAL``,
        wirkt nur innerhalb der Testtransaktion), damit der erzwungene
        Seq-Scan-Fallback greift und die Prüfung nicht vom inzidentellen
        Index-Artefakt profitiert, das den alten Pin unzuverlässig machte.
        """
        today = timezone.localdate()
        items = [
            _make_item(facility, staff_user, title=f"Gleichstand-{i}", due_date=today - timedelta(days=1))
            for i in range(8)
        ]

        base = timezone.now()
        # Rang 0 = juengster Zeitstempel (soll zuerst erscheinen) .. Rang 7 =
        # aeltester. Weder auf- noch absteigend zur Einfuegereihenfolge.
        rank_by_insertion_index = [3, 6, 1, 4, 7, 2, 5, 0]
        for item, rank in zip(items, rank_by_insertion_index, strict=True):
            WorkItem.objects.filter(pk=item.pk).update(created_at=base - timedelta(minutes=rank))
        expected_titles = [
            item.title for _, item in sorted(zip(rank_by_insertion_index, items, strict=True), key=lambda pair: pair[0])
        ]
        # Gegenprobe: die Erwartung ist weder die Einfuege- noch die
        # umgekehrte Einfuegereihenfolge (sonst waere sie kein Beleg).
        insertion_titles = [item.title for item in items]
        assert expected_titles != insertion_titles
        assert expected_titles != list(reversed(insertion_titles))

        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_indexscan = off")
            cursor.execute("SET LOCAL enable_bitmapscan = off")
            cursor.execute("SET LOCAL enable_indexonlyscan = off")

            box = build_focus_box(facility)

        overdue = _group(box, "overdue")
        assert [wi.title for wi in overdue["items"]] == expected_titles


class TestOrderByCallCarriesTheCreatedAtTiebreak:
    """Vertragsprüfung unmittelbar am ``order_by()``-Aufruf, unabhängig von
    jeder Planer-/Index-Entscheidung — zweiter, redundanter Pin auf
    ``x_build_focus_box__mutmut_10``."""

    def test_order_by_arguments_include_created_at_descending(self, facility, staff_user, monkeypatch):
        """Killt ``x_build_focus_box__mutmut_10`` unabhängig vom DB-Ausführungsplan:
        ein Spy auf ``QuerySet.order_by`` zeichnet die tatsächlich
        übergebenen Positionsargumente auf; das Original ruft
        ``order_by("due_date_bucket", "due_date", "priority_order",
        "-created_at")`` auf, die Mutante lässt das letzte Argument weg.
        """
        today = timezone.localdate()
        _make_item(facility, staff_user, title="Ueberfaellig", due_date=today - timedelta(days=1))

        captured_calls: list[tuple[object, ...]] = []
        original_order_by = QuerySet.order_by

        def spy_order_by(self, *args, **kwargs):
            captured_calls.append(args)
            return original_order_by(self, *args, **kwargs)

        monkeypatch.setattr(QuerySet, "order_by", spy_order_by)

        build_focus_box(facility)

        assert captured_calls, "build_focus_box() hat order_by() nicht aufgerufen"
        assert any("-created_at" in call for call in captured_calls), captured_calls
