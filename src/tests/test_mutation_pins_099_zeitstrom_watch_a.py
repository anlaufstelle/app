"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.zeitstrom_watch`` (Batch ``zeitstrom_watch-a``).

Refs https://github.com/anlaufstelle/app/issues/2174.
Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Vollmutations-Lauf auf main@5ceb7f3a (frischer Mutantenbaum), Batch
``zeitstrom_watch-a`` (67 Survivor-Mutanten, ausschliesslich
``x__current_shift_projection``, die private Helferfunktion hinter dem
``current_shift``-Watch-Baustein). Die Funktion baut aus vier Bausteinen ein
Dict:

* ``stats`` — Aggregat-Zaehler ueber ``visible_events``/``WorkItem``/
  ``Client`` der laufenden Schicht (Einrichtung + nicht geloescht +
  Zeitfenster).
* ``highlights`` — eine zeit-sortierte Liste aus Krisen-/Hausverbots-
  Ereignissen (je hoechstens 10, neueste zuerst) und dringenden/wichtigen
  Aufgaben (hoechstens 10) der laufenden Schicht.
* ``shift``/``date``/``window`` — die Schicht-Identifikation selbst.

mutmut mutiert dabei drei Achsen, die sich mit EINEM reich besetzten
Szenario gemeinsam fangen lassen:

* **Scope-Bruch** (``facility=None``/Filter gestrichen, ``is_deleted``
  auf ``None``/``True`` gedreht, ``occurred_at__range`` gestrichen,
  ``visible_to(None)`` statt ``visible_to(user)``, ``system_type=None``
  statt des Schleifenwerts, Prioritaets-/Zeitfenster-Filter der
  Aufgaben-Highlights gestrichen) — ein Distraktor-Objekt je Scope-Achse
  (falsche Einrichtung, geloescht, ausserhalb des Fensters, falsche
  Prioritaet) darf NICHT in ``stats``/``highlights`` auftauchen; bleibt der
  Filter aus, taucht er auf oder verdraengt das echte Objekt.
* **Schluessel-/Wert-Vertauschung** (``"XX<key>XX"``/``"<KEY>"`` statt des
  echten Dict-Schluessels, ``Count(None)`` statt ``Count("id")``,
  ``str(None)`` statt ``str(live_filter.pk)``, vertauschte/fehlende
  Argumente an ``_event_projection``) — bricht entweder die exakte
  ``set(...)``-Gleichheit der Dict-Schluessel oder wirft beim Zugriff auf
  den (unveraenderten) Original-Schluessel ein ``KeyError``/``TypeError``,
  das den Test ebenso rot macht (kein Assertion-Mismatch noetig, ein
  Absturz ist ein gueltiger Kill-Nachweis).
* **Sortier-/Limit-Bruch** (``.sort(key=None)``/``reverse=None``/kein
  ``key`` mehr/kein ``reverse`` mehr, ``[:10]`` -> ``[:11]``) — drei
  Highlights mit eindeutigem, absichtlich NICHT der Einfuegereihenfolge
  entsprechendem Zeitstempel machen jede Sortier-Abweichung sichtbar; die
  beiden ``[:10]``-Limits brauchen je 11 gleichartige Zeilen und stehen
  deshalb in eigenen Tests.

Konventionen wie ``test_mutation_pins_098_client_export.py``/
``test_mutation_pins_099_facility_export_a.py``: Zusicherungen vergleichen
EXAKT (``==``) bzw. per Mengen-Vergleich (wo die DB-Reihenfolge bei
Gleichstand nicht garantiert ist), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die ein Substring-Match uebersehen wuerde).
Fixtures kommen aus ``src/tests/conftest.py``; Dokumenttypen/Aufgaben mit
eigenen, unverwechselbaren Werten werden je Test frisch angelegt.
``_encryption_key`` liegt modulweit an (Lehre 112) — der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, TimeFilter, WorkItem
from core.services.zeitstrom_filters import resolve_zeitstrom_state
from core.services.zeitstrom_watch import _current_shift_projection

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _full_day_filter(facility, *, label="Ganztag Pin"):
    return TimeFilter.objects.create(facility=facility, label=label, start_time="00:00", end_time="23:59")


# ---------------------------------------------------------------------------
# Scope, Aggregat-Schluessel, Highlight-Inhalt und -Reihenfolge — 63 Survivors
# ---------------------------------------------------------------------------


class TestCurrentShiftProjectionScopeAndContent:
    def test_stats_and_highlights_reflect_only_the_current_shifts_own_visible_rows(
        self, facility, staff_user, foreign_event
    ):
        """Mutanten ``x__current_shift_projection__mutmut_6``/``_9``
        (``facility=facility`` im ``visible_events``-Filter -> ``None``/
        gestrichen), ``_7``/``_10``/``_13`` (``is_deleted=False`` ->
        ``None``/gestrichen/``True``), ``_11`` (``occurred_at__range``
        gestrichen), ``_12`` (``visible_to(user)`` -> ``visible_to(None)``,
        liefert ``.none()``), ``_20``/``_21`` (``.values("document_type__name",
        "document_type__color")`` verliert je eine Spalte), ``_26``
        (``Count("id")`` -> ``Count(None)``, zaehlt in jeder Gruppe 0 statt
        der echten Zeilen), ``_32``/``_33`` (``"events_total"`` ->
        ``"XXevents_totalXX"``/``"EVENTS_TOTAL"``), ``_35``/``_36``
        (``item["count"]`` in der Summe -> ``item["XXcountXX"]``/
        ``item["COUNT"]``), ``_37``/``_38`` (``"events_by_type"`` ->
        ``"XXevents_by_typeXX"``/``"EVENTS_BY_TYPE"``), ``_39``/``_40``
        (``"workitems_new"`` -> ``"XXworkitems_newXX"``/``"WORKITEMS_NEW"``),
        ``_42`` (``WorkItem.objects.for_facility(facility)`` ->
        ``for_facility(None)``, liefert 0 statt der echten Aufgaben),
        ``_43``/``_44`` (``"clients_new"`` -> ``"XXclients_newXX"``/
        ``"CLIENTS_NEW"``), ``_48``/``_49`` (``highlight_type`` des
        Krisen-Paars -> ``"XXcrisisXX"``/``"CRISIS"``, falscher Typ-Tag),
        ``_50``/``_51`` (``system_type``-Filterwert des Krisen-Paars ->
        ``"XXcrisisXX"``/``"CRISIS"``, Krisen-Highlight bleibt leer),
        ``_52``/``_53`` (``highlight_type`` des Hausverbots-Paars ->
        ``"XXbanXX"``/``"BAN"``, falscher Typ-Tag), ``_54``/``_55``
        (``system_type``-Filterwert des Hausverbots-Paars ->
        ``"XXbanXX"``/``"BAN"``, Hausverbots-Highlight bleibt leer),
        ``_59`` (``document_type__system_type=system_type`` ->
        ``=None`` in BEIDEN Schleifendurchlaeufen), ``_64``/``_65``
        (``"type"`` beim Aufbau des Krisen-/Hausverbots-Eintrags ->
        ``"XXtypeXX"``/``"TYPE"``), ``_66``/``_67`` (``"time"`` ->
        ``"XXtimeXX"``/``"TIME"``), ``_68``/``_69`` (``row["occurred_at"]``
        -> ``row["XXoccurred_atXX"]``/``row["OCCURRED_AT"]``), ``_70``/``_71``
        (``"row"`` -> ``"XXrowXX"``/``"ROW"``), ``_77`` (``priority__in=[...]``
        im Aufgaben-Highlight-Filter gestrichen), ``_78``
        (``created_at__range`` im Aufgaben-Highlight-Filter gestrichen),
        ``_94`` (``highlights.sort(key=None, ...)``), ``_95``/``_101``
        (``reverse=None``/``reverse=False`` statt ``True``), ``_96``
        (``key``-Argument komplett gestrichen), ``_97`` (``reverse``-Argument
        komplett gestrichen), ``_98`` (``key=lambda item: None``), ``_103``/
        ``_104`` (``item["row"]`` beim Aufbau von ``event_rows`` ->
        ``item["XXrowXX"]``/``item["ROW"]``), ``_110`` (``field_configs =
        None`` statt des echten Aufrufs), ``_112`` (``_visible_field_configs``
        mit ``user=None``), ``_126``/``_127``/``_128``/``_129`` (Argumente an
        ``_event_projection`` vertauscht/fehlend: ``row`` -> ``None``,
        ``field_configs`` -> ``None``, ``row`` fehlt, ``field_configs``
        fehlt), ``_135``/``_136`` (``"shift"`` -> ``"XXshiftXX"``/``"SHIFT"``),
        ``_137`` (``str(live_filter.pk)`` -> ``str(None)``), ``_138``/``_139``
        (``"date"`` -> ``"XXdateXX"``/``"DATE"``), ``_140``/``_141``
        (``"window"`` -> ``"XXwindowXX"``/``"WINDOW"``), ``_142``/``_143``
        (``"stats"`` -> ``"XXstatsXX"``/``"STATS"``), ``_144``/``_145``
        (``"highlights"`` -> ``"XXhighlightsXX"``/``"HIGHLIGHTS"``).

        Vier Distraktor-Ereignisse duerfen NICHT in ``events_by_type``
        auftauchen bzw. dessen Zaehler nicht veraendern: ``foreign_event``
        (fremde Einrichtung), ``deleted_event`` (geloescht, gleicher
        Dokumenttyp wie das echte Krisen-Highlight — eine ausbleibende
        ``is_deleted``-Filterung wuerde seine Gruppe auf 2 verdoppeln oder
        an seine Stelle setzen), ``time_out_event`` (ausserhalb des
        24h-Schichtfensters, gleicher Dokumenttyp wie ``own_active_event`` —
        ein gestrichener Zeitfilter wuerde dessen Gruppe auf 2 verdoppeln).
        ``own_active_event``/``crisis_event``/``ban_event`` sind die drei
        einzigen echten Treffer (je ein eigener Dokumenttyp, je ``count=1``);
        ``normal_task`` zaehlt zu ``workitems_new`` mit, aber NICHT zu den
        Highlights (siehe Testklasse unten).
        """
        _full_day_filter(facility)
        now = timezone.localtime()

        contact_type = DocumentType.objects.create(facility=facility, name="Kontakt Pin", color="#111111")
        crisis_type = DocumentType.objects.create(
            facility=facility,
            name="Krise Pin",
            system_type=DocumentType.SystemType.CRISIS,
            color="#ff0000",
        )
        ban_type = DocumentType.objects.create(
            facility=facility,
            name="Hausverbot Pin",
            system_type=DocumentType.SystemType.BAN,
            color="#0000ff",
        )
        # Mutant _112: mindestens eine Feldzuordnung ist Pflicht, sonst
        # bleibt die ``user_can_see_field``-Schleife leer und ein
        # ``user=None`` faellt nie auf.
        crisis_field = FieldTemplate.objects.create(
            facility=facility, name="Diagnosehinweis", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=crisis_type, field_template=crisis_field, sort_order=0)

        Event.objects.create(
            facility=facility,
            document_type=contact_type,
            occurred_at=now,
            created_by=staff_user,
        )
        crisis_event = Event.objects.create(
            facility=facility,
            document_type=crisis_type,
            occurred_at=now - timedelta(minutes=3),
            created_by=staff_user,
        )
        ban_event = Event.objects.create(
            facility=facility,
            document_type=ban_type,
            occurred_at=now - timedelta(minutes=5),
            created_by=staff_user,
        )
        deleted_event = Event.objects.create(
            facility=facility,
            document_type=crisis_type,
            occurred_at=now - timedelta(minutes=2),
            is_deleted=True,
            created_by=staff_user,
        )
        time_out_event = Event.objects.create(
            facility=facility,
            document_type=contact_type,
            occurred_at=now - timedelta(days=2),
            created_by=staff_user,
        )
        assert deleted_event.is_deleted is True
        assert time_out_event.pk is not None

        urgent_task = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Dringende Diensthinweis-Aufgabe",
            priority=WorkItem.Priority.URGENT,
        )
        WorkItem.objects.filter(pk=urgent_task.pk).update(created_at=now - timedelta(minutes=1))
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Normale Aufgabe",
        )
        old_urgent_task = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Alte dringende Aufgabe ausserhalb des Fensters",
            priority=WorkItem.Priority.URGENT,
        )
        WorkItem.objects.filter(pk=old_urgent_task.pk).update(created_at=now - timedelta(days=2))

        state = resolve_zeitstrom_state({}, facility, now=now)
        result = _current_shift_projection(facility, staff_user, state)

        # -- stats: exakte Schluessel + exakte Aggregat-Werte --------------
        assert set(result["stats"].keys()) == {"events_total", "events_by_type", "workitems_new", "clients_new"}
        assert result["stats"]["events_total"] == 3
        assert {
            (row["document_type__name"], row["document_type__color"], row["count"])
            for row in result["stats"]["events_by_type"]
        } == {
            (contact_type.name, contact_type.color, 1),
            (crisis_type.name, crisis_type.color, 1),
            (ban_type.name, ban_type.color, 1),
        }
        # workitems_new zaehlt urgent_task + normal_task, NICHT old_urgent_task.
        assert result["stats"]["workitems_new"] == 2

        # -- highlights: exakte Typ-/Identitaets-/Reihenfolge-Kette --------
        highlights = result["highlights"]
        assert [entry[0] for entry in highlights] == ["task", "crisis", "ban"]
        task_type, task_time, task_value = highlights[0]
        crisis_type_label, crisis_time, crisis_value = highlights[1]
        ban_type_label, ban_time, ban_value = highlights[2]
        assert task_value[0] == urgent_task.pk
        assert crisis_value[0] == str(crisis_event.pk)
        assert ban_value[0] == str(ban_event.pk)
        assert task_time > crisis_time > ban_time

        # -- Top-level-Dict: exakte Schluessel + Schicht-Identitaet --------
        assert set(result.keys()) == {"shift", "date", "window", "stats", "highlights"}
        assert result["shift"][0] == str(state.live_context.time_filter.pk)


# ---------------------------------------------------------------------------
# Highlight-Limits — 2 Survivors (je 10 gleichartige Zeilen pro Schleife)
# ---------------------------------------------------------------------------


class TestCurrentShiftProjectionHighlightLimits:
    def test_crisis_event_highlights_stay_capped_at_ten(self, facility, staff_user):
        """Mutant ``x__current_shift_projection__mutmut_62``:
        ``.order_by("-occurred_at")[:10]`` -> ``[:11]`` in der Krisen-/
        Hausverbots-Highlight-Schleife. Elf Krisenereignisse im
        Schichtfenster mit eindeutigem ``occurred_at``; die Original-
        Funktion liefert nur die zehn juengsten als ``"crisis"``-Highlights,
        der Mutant alle elf.
        """
        _full_day_filter(facility, label="Ganztag Krisenlimit")
        now = timezone.localtime()
        crisis_type = DocumentType.objects.create(
            facility=facility,
            name="Krise Limit",
            system_type=DocumentType.SystemType.CRISIS,
        )
        events = [
            Event.objects.create(
                facility=facility,
                document_type=crisis_type,
                occurred_at=now - timedelta(minutes=i),
                created_by=staff_user,
            )
            for i in range(11)
        ]

        state = resolve_zeitstrom_state({}, facility, now=now)
        result = _current_shift_projection(facility, staff_user, state)

        crisis_ids = [value[0] for (highlight_type, _time, value) in result["highlights"] if highlight_type == "crisis"]
        assert crisis_ids == [str(event.pk) for event in events[:10]]

    def test_task_highlights_stay_capped_at_ten(self, facility, staff_user):
        """Mutant ``x__current_shift_projection__mutmut_82``:
        ``.order_by("-created_at")[:10]`` -> ``[:11]`` in der dringenden/
        wichtigen Aufgaben-Highlight-Query. Elf dringende Aufgaben im
        Schichtfenster mit eindeutigem ``created_at``; die Original-
        Funktion liefert nur die zehn juengsten als ``"task"``-Highlights,
        der Mutant alle elf.
        """
        _full_day_filter(facility, label="Ganztag Aufgabenlimit")
        now = timezone.localtime()
        tasks = []
        for i in range(11):
            task = WorkItem.objects.create(
                facility=facility,
                created_by=staff_user,
                title=f"Dringende Aufgabe {i}",
                priority=WorkItem.Priority.URGENT,
            )
            WorkItem.objects.filter(pk=task.pk).update(created_at=now - timedelta(minutes=i))
            tasks.append(task)

        state = resolve_zeitstrom_state({}, facility, now=now)
        result = _current_shift_projection(facility, staff_user, state)

        task_ids = [value[0] for (highlight_type, _time, value) in result["highlights"] if highlight_type == "task"]
        assert task_ids == [task.pk for task in tasks[:10]]
