"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.zeitstrom_watch`` (Batch ``zeitstrom_watch-b``).

Refs https://github.com/anlaufstelle/app/issues/2174.
Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Vollmutations-Lauf auf main@a5c78e5e (frischer Mutantenbaum), Batch
``zeitstrom_watch-b`` (54 Survivor-Mutanten, ausschliesslich
``_focus_box_projection``, die private Helferfunktion hinter dem
``focus_box``-Watch-Baustein — den offenen/dringenden Aufgaben der
Einrichtung, die als Kachel angezeigt werden). Andere Funktionen desselben
Moduls (``_current_shift_projection`` in Batch a, ``_feed_projection``,
``_tally_counts_projection``, ``_tally_topology_projection``,
``_bans_projection`` u. a. in den Schwester-Batches c/d/e) gehören fremden
Testdateien — bewusst nicht gelesen, nicht angefasst.

``_focus_box_projection`` baut aus offenen/laufenden ``WorkItem``-Zeilen der
Einrichtung eine nach ``due_date_bucket``/``due_date``/``priority_order``/
``-created_at`` sortierte Top-8-Auswahl und gruppiert sie in vier Buckets
(``overdue``/``today``/``priority``/``in_progress``). mutmut mutiert dabei
drei Achsen:

* **Eligibility-Filter** (``Q(due_date__lte=today) | Q(priority__in=[...])
  | Q(status=IN_PROGRESS)``) — Operator-Verknüpfung geändert bzw. eine
  Klausel auf ``None`` gedreht (Mutanten ``_13``/``_17``): eine Aufgabe, die
  nur über die betroffene Klausel eligible ist, verschwindet komplett aus
  der Fokusbox.
* **Sortier-Hilfsspalten** (``priority_order``/``due_date_bucket``, je ein
  ``Case``/``When``/``Value``-Konstrukt) — Literale/Feldtypen der
  ``When``-/``Value``-/``default``-/``output_field``-Argumente geändert
  oder ganze Zeilen gestrichen (Mutanten ``_20`` bis ``_69``, 40 Stück):
  gefangen über EINEN Spy auf die ``Case``/``When``/``Value``-Konstruktion
  (kein DB-Datum nötig — Django baut diese Ausdrucksobjekte bereits beim
  Aufbau der ``annotate()``-Argumente, nicht erst bei der Ausführung; ein
  Objekt ohne Aufgaben reicht als Fixture). Das ist die robuste,
  deterministische Variante der Sortier-/Tiebreak-Regel aus den
  Vorwellen (per Spy, nie über beobachtete Ergebnisreihenfolge, die bei
  Gleichstand nicht garantiert ist).
* **``order_by``-Feldliste und Top-N-Limit** (Mutanten ``_76``–``_79``
  Felder gestrichen, ``_88`` ``[:8]`` -> ``[:9]``) — die Feldliste wird
  über einen Spy auf ``QuerySet.order_by`` gepinnt (gleiches Prinzip), das
  Limit über die direkt zurückgegebene Zeilenzahl bei neun eligible
  Aufgaben.
* **Bucket-Klassifikation** (``<``/``==``/``in`` im Schleifenkörper sowie
  der ``"today"``-Schlüssel selbst, Mutanten ``_104``/``_110``–``_113``/
  ``_116``) — eine heute fällige bzw. eine dringende/wichtige, nicht
  heute/überfällige Aufgabe landet im falschen Bucket; die
  Schlüssel-Mutationen (``None``/``"XXtodayXX"``/``"TODAY"``) werfen sogar
  ein ``KeyError`` (kein Assertion-Mismatch nötig, ein Absturz ist ein
  gültiger Kill-Nachweis).
* **"Mehr vorhanden"-Flag** (``total_open > len(rows)`` -> ``>=``, Mutant
  ``_136``) — bei exaktem Gleichstand (alle offenen Aufgaben werden
  gezeigt) liefert das Original ``False``, die Mutante ``True``.

Konventionen wie ``test_mutation_pins_099_zeitstrom_watch_a.py``:
Zusicherungen vergleichen EXAKT (``==``/``is``), nie per Teilstring (mutmut
erzeugt ``"XX<text>XX"``-Varianten, die ein Substring-Match übersehen
würde). Fixtures kommen aus ``src/tests/conftest.py``. ``_encryption_key``
liegt modulweit an (Lehre 112) — der Mutantenbaum hat keine ``.env`` und
damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db.models import IntegerField
from django.db.models.query import QuerySet
from django.utils import timezone

import core.services.zeitstrom_watch as zeitstrom_watch
from core.models import WorkItem
from core.services.zeitstrom_watch import _focus_box_projection

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestFocusBoxProjectionAnnotateLiterals:
    def test_focus_box_projection_case_when_literals_and_field_types_are_exact(self, monkeypatch, facility):
        """Ein Spy auf ``zeitstrom_watch.When``/``Value``/``DBCase`` fängt
        alle 40 Literal-/Feldtyp-Mutanten der beiden ``annotate()``-Bausteine
        ``priority_order`` und ``due_date_bucket``:

        ``x__focus_box_projection__mutmut_20`` (``default=Value(2)`` ->
        ``None``), ``_21`` (``output_field=IntegerField()`` -> ``None``),
        ``_22``/``_23`` (``When(priority=URGENT/IMPORTANT, ...)`` komplett
        gestrichen), ``_24`` (``default=Value(2)`` komplett gestrichen),
        ``_25`` (``output_field=IntegerField()`` komplett gestrichen),
        ``_26``/``_32`` (``priority=URGENT``/``IMPORTANT`` -> ``None``),
        ``_27``/``_33`` (``then=Value(0)``/``Value(1)`` -> ``None``),
        ``_29``/``_35`` (``then=``-Argument komplett gestrichen),
        ``_30``/``_36`` (``then=Value(0)``/``Value(1)`` -> ``Value(None)``),
        ``_31`` (``then=Value(0)`` -> ``Value(1)``), ``_37`` (``then=Value(1)``
        -> ``Value(2)``), ``_38`` (``default=Value(2)`` -> ``Value(None)``),
        ``_39`` (``default=Value(2)`` -> ``Value(3)``), ``_43``
        (``default=Value(9)`` -> ``None``), ``_44`` (``output_field`` ->
        ``None``), ``_45``/``_46``/``_47`` (``When(due_date__lt/=/__gt=today,
        ...)`` komplett gestrichen), ``_48`` (``default=Value(9)`` komplett
        gestrichen), ``_49`` (``output_field`` komplett gestrichen), ``_51``
        (``then=Value(0)`` -> ``None``), ``_53`` (``then=``-Argument
        gestrichen), ``_54`` (``then=Value(0)`` -> ``Value(None)``), ``_55``
        (``then=Value(0)`` -> ``Value(1)``), ``_56`` (``due_date=today`` ->
        ``None``), ``_57`` (``then=Value(1)`` -> ``None``), ``_59``
        (``then=``-Argument gestrichen), ``_60`` (``then=Value(1)`` ->
        ``Value(None)``), ``_61`` (``then=Value(1)`` -> ``Value(2)``), ``_63``
        (``then=Value(2)`` -> ``None``), ``_65`` (``then=``-Argument
        gestrichen), ``_66`` (``then=Value(2)`` -> ``Value(None)``), ``_67``
        (``then=Value(2)`` -> ``Value(3)``), ``_68`` (``default=Value(9)`` ->
        ``Value(None)``), ``_69`` (``default=Value(9)`` -> ``Value(10)``).

        Kein WorkItem nötig: Django konstruiert ``Case``/``When``/``Value``
        bereits beim Aufbau der ``annotate()``-Keyword-Argumente in Python,
        bevor die Queryset je ausgewertet wird — der Spy sieht die exakten
        Literale unabhängig vom DB-Inhalt der Einrichtung.
        """
        value_records: list[tuple[tuple, object]] = []
        when_records: list[tuple[dict, object]] = []
        case_records: list[tuple[tuple, dict]] = []

        real_value = zeitstrom_watch.Value
        real_when = zeitstrom_watch.When
        real_case = zeitstrom_watch.DBCase

        def spy_value(*args, **kwargs):
            obj = real_value(*args, **kwargs)
            value_records.append((args, obj))
            return obj

        def spy_when(*args, **kwargs):
            obj = real_when(*args, **kwargs)
            when_records.append((kwargs, obj))
            return obj

        def spy_case(*args, **kwargs):
            obj = real_case(*args, **kwargs)
            case_records.append((args, kwargs))
            return obj

        monkeypatch.setattr(zeitstrom_watch, "Value", spy_value)
        monkeypatch.setattr(zeitstrom_watch, "When", spy_when)
        monkeypatch.setattr(zeitstrom_watch, "DBCase", spy_case)

        _focus_box_projection(facility)

        # Sieben Value(...)-Aufrufe in exakter Reihenfolge: priority_order
        # (URGENT=0, IMPORTANT=1, default=2), due_date_bucket (lt=0, eq=1,
        # gt=2, default=9). Jede gestrichene/genullte/vertauschte Literalzahl
        # bricht diese Liste (Länge ODER Position).
        assert [args for args, _obj in value_records] == [
            (0,),
            (1,),
            (2,),
            (0,),
            (1,),
            (2,),
            (9,),
        ]

        today = timezone.localdate()

        # priority_order: zwei When-Aufrufe mit den echten Lookup-Werten und
        # dem jeweils zugehörigen (identischen) Value-Objekt.
        assert when_records[0][0] == {"priority": WorkItem.Priority.URGENT, "then": value_records[0][1]}
        assert when_records[1][0] == {"priority": WorkItem.Priority.IMPORTANT, "then": value_records[1][1]}

        # due_date_bucket: drei When-Aufrufe mit den echten Lookup-Werten.
        assert when_records[2][0] == {"due_date__lt": today, "then": value_records[3][1]}
        assert when_records[3][0] == {"due_date": today, "then": value_records[4][1]}
        assert when_records[4][0] == {"due_date__gt": today, "then": value_records[5][1]}

        assert len(case_records) == 2

        priority_order_args, priority_order_kwargs = case_records[0]
        assert priority_order_args == (when_records[0][1], when_records[1][1])
        assert priority_order_kwargs.get("default") is value_records[2][1]
        assert isinstance(priority_order_kwargs.get("output_field"), IntegerField)

        due_date_bucket_args, due_date_bucket_kwargs = case_records[1]
        assert due_date_bucket_args == (when_records[2][1], when_records[3][1], when_records[4][1])
        assert due_date_bucket_kwargs.get("default") is value_records[6][1]
        assert isinstance(due_date_bucket_kwargs.get("output_field"), IntegerField)


class TestFocusBoxProjectionOrderByAndLimit:
    def test_focus_box_projection_order_by_fields_and_order_are_exact(self, monkeypatch, facility):
        """Ein Spy auf ``QuerySet.order_by`` fängt die vier Feldlisten-
        Mutanten: ``x__focus_box_projection__mutmut_76`` (verliert
        ``"due_date_bucket"``), ``_77`` (verliert ``"due_date"``), ``_78``
        (verliert ``"priority_order"``), ``_79`` (verliert
        ``"-created_at"``). Wie oben genügt ein Objekt ohne Aufgaben, da
        ``order_by(...)`` beim Aufbau der Queryset aufgerufen wird, nicht
        erst bei ihrer Auswertung.
        """
        calls: list[tuple] = []
        real_order_by = QuerySet.order_by

        def spy_order_by(self, *args, **kwargs):
            calls.append(args)
            return real_order_by(self, *args, **kwargs)

        monkeypatch.setattr(QuerySet, "order_by", spy_order_by)

        _focus_box_projection(facility)

        assert calls == [("due_date_bucket", "due_date", "priority_order", "-created_at")]

    def test_focus_box_projection_limits_to_eight_entries(self, facility):
        """Mutant ``x__focus_box_projection__mutmut_88`` (``[:8]`` ->
        ``[:9]``): bei neun offenen, heute fälligen Aufgaben liefert das
        Original genau acht Zeilen und setzt das 'mehr vorhanden'-Flag; die
        Mutante liefert neun.
        """
        today = timezone.localdate()
        for i in range(9):
            WorkItem.objects.create(
                facility=facility,
                title=f"Fokusbox-Limit-Aufgabe {i}",
                due_date=today,
                status=WorkItem.Status.OPEN,
            )

        today_out, _ordered, shown, total_open, more_available = _focus_box_projection(facility)

        assert today_out == today
        assert total_open == 9
        assert shown == 8
        assert more_available is True


class TestFocusBoxProjectionEligibilityFilter:
    def test_focus_box_projection_eligibility_is_independent_or_of_due_priority_status(self, facility):
        """Mutanten ``x__focus_box_projection__mutmut_13`` (``Q(priority__in=
        [...]) | Q(status=IN_PROGRESS)`` -> ``... & Q(status=IN_PROGRESS)``,
        die dritte Eligibility-Klausel wird per Operator-Präzedenz an die
        zweite gekettet statt eigenständig per OR anzubieten) und ``_17``
        (``Q(status=IN_PROGRESS)`` -> ``Q(status=None)``, matcht nie einen
        echten Datensatz): drei Aufgaben, von denen jede NUR über eine der
        drei Eligibility-Klauseln (Fälligkeit/Priorität/Status) sichtbar
        wird, müssen alle drei in der Fokusbox auftauchen. Fehlt eine
        Klausel, verschwindet genau die dazugehörige Aufgabe.
        """
        today = timezone.localdate()
        due_soon = WorkItem.objects.create(
            facility=facility,
            title="Nur über Fälligkeit eligible",
            due_date=today - timedelta(days=1),
            priority=WorkItem.Priority.NORMAL,
            status=WorkItem.Status.OPEN,
        )
        urgent_no_due = WorkItem.objects.create(
            facility=facility,
            title="Nur über Priorität eligible",
            due_date=today + timedelta(days=30),
            priority=WorkItem.Priority.URGENT,
            status=WorkItem.Status.OPEN,
        )
        in_progress_no_due = WorkItem.objects.create(
            facility=facility,
            title="Nur über Status eligible",
            due_date=today + timedelta(days=30),
            priority=WorkItem.Priority.NORMAL,
            status=WorkItem.Status.IN_PROGRESS,
        )

        _today_out, ordered, _shown, _total_open, _more = _focus_box_projection(facility)

        ids_present = {row[0] for _key, rows in ordered for row in rows}
        assert ids_present == {due_soon.id, urgent_no_due.id, in_progress_no_due.id}


class TestFocusBoxProjectionBucketClassification:
    def test_focus_box_projection_due_today_item_lands_in_today_bucket(self, facility):
        """Mutanten ``x__focus_box_projection__mutmut_104`` (``row["due_date"]
        < today`` -> ``<= today``, eine heute fällige Aufgabe rutscht in
        ``"overdue"``), ``_110`` (``row["due_date"] == today`` -> ``!=
        today``, die Aufgabe fällt aus dem ``"today"``-Zweig und landet je
        nach Priorität in ``"priority"``/``"in_progress"``), ``_111``/``_112``/
        ``_113`` (``key = "today"`` -> ``None``/``"XXtodayXX"``/``"TODAY"``,
        ``groups[key]`` wirft ``KeyError`` — kein Assertion-Mismatch nötig,
        der Absturz IST der Kill-Nachweis): eine heute fällige, normal
        priorisierte, offene Aufgabe muss exakt und ausschließlich im
        Bucket ``"today"`` landen.
        """
        today = timezone.localdate()
        item = WorkItem.objects.create(
            facility=facility,
            title="Heute fällige Aufgabe",
            due_date=today,
            priority=WorkItem.Priority.NORMAL,
            status=WorkItem.Status.OPEN,
        )

        _today_out, ordered, _shown, _total_open, _more = _focus_box_projection(facility)

        groups = dict(ordered)
        assert set(groups) == {"today"}
        assert [row[0] for row in groups["today"]] == [item.id]

    def test_focus_box_projection_priority_membership_not_inverted(self, facility):
        """Mutant ``x__focus_box_projection__mutmut_116`` (``row["priority"]
        in (URGENT, IMPORTANT)`` -> ``not in (...)``): eine dringende, erst
        in der Zukunft fällige, offene Aufgabe muss im Bucket
        ``"priority"`` landen, nicht in ``"in_progress"``.
        """
        today = timezone.localdate()
        item = WorkItem.objects.create(
            facility=facility,
            title="Dringende, noch nicht fällige Aufgabe",
            due_date=today + timedelta(days=5),
            priority=WorkItem.Priority.URGENT,
            status=WorkItem.Status.OPEN,
        )

        _today_out, ordered, _shown, _total_open, _more = _focus_box_projection(facility)

        groups = dict(ordered)
        assert set(groups) == {"priority"}
        assert [row[0] for row in groups["priority"]] == [item.id]


class TestFocusBoxProjectionMoreAvailableFlag:
    def test_focus_box_projection_more_available_flag_is_strict_greater_than(self, facility):
        """Mutant ``x__focus_box_projection__mutmut_136`` (``total_open >
        len(rows)`` -> ``total_open >= len(rows)``): werden alle offenen
        Aufgaben der Einrichtung bereits angezeigt (Gleichstand, kein
        Abschneiden durch das Acht-Einträge-Limit), muss das
        'mehr vorhanden'-Flag ``False`` sein, nicht ``True``.
        """
        today = timezone.localdate()
        for i in range(2):
            WorkItem.objects.create(
                facility=facility,
                title=f"Vollständig gezeigte Aufgabe {i}",
                due_date=today,
                status=WorkItem.Status.OPEN,
            )

        _today_out, _ordered, shown, total_open, more_available = _focus_box_projection(facility)

        assert total_open == 2
        assert shown == 2
        assert more_available is False
