"""Follow-Up-Tests für Mutation-Survivors in ``core.services.case.handover``.

Refs #930. Ziel: Mutationen in den Branch-Grenzen von
``_collect_highlights`` (33 Survivors) gezielt killen.

Die Funktion kombiniert ORM-Filter (priority/status/system_type),
explizite Slice-Limits (``[:10]``) und Sortierreihenfolgen — also genau
die Stellen, an denen Mutmut gerne ``<=`` ↔ ``<``, ``in`` ↔ ``not in``,
``-foo`` ↔ ``foo`` und Konstanten-Off-by-One mutiert.

Refs #1388: zweite Welle — die verhaltensrelevanten Survivors aus
``_collect_stats`` (Facility-/Zeitfenster-/Status-/system_type-Filter,
Rueckgabe-Dict-Keys) und ``build_handover_summary`` (``is_deleted``-Filter,
Facility-Argument, User-Argument fuer die Preview-Anreicherung). Bewusst
ausgelassen (aequivalent): reine strftime-/Display-Mutationen in
``_build_shift_metadata``, ``select_related``-Drops (nur Query-Optimierung),
der ``document_type__color``-``values()``-Drop (kein Konsument) und die
``occurred_at``-Key-Umbenennung in der lokalen ``event_highlights``-Liste
(``enrich_events_with_preview`` liest den Key nie).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from core.models import Client, DocumentType, DocumentTypeField, Event, FieldTemplate, WorkItem
from core.services.case import _collect_highlights, _collect_stats, build_handover_summary

# ---------------------------------------------------------------------------
# Helper-Factories — bewusst klein, damit Tests nicht von komplexen
# Fixture-Bäumen abhängen. Bestehende ``facility``-/``*_user``-Fixtures
# kommen aus ``conftest.py``.
# ---------------------------------------------------------------------------


def _make_doc_type(facility, *, system_type: str | None = None, name: str = "Doc") -> DocumentType:
    kwargs = dict(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        name=name,
    )
    if system_type:
        kwargs["system_type"] = system_type
    return DocumentType.objects.create(**kwargs)


def _make_event(
    facility,
    doc_type,
    *,
    client=None,
    user=None,
    offset_minutes: int = 0,
) -> Event:
    """Event mit ``occurred_at = now + offset_minutes`` (oft negativ)."""
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now() + timedelta(minutes=offset_minutes),
        created_by=user,
    )


def _make_workitem(
    facility,
    user,
    *,
    title: str = "WI",
    priority: str = WorkItem.Priority.NORMAL,
    status: str = WorkItem.Status.OPEN,
    assigned_to=None,
    due_date=None,
    created_offset_minutes: int = 0,
) -> WorkItem:
    wi = WorkItem.objects.create(
        facility=facility,
        created_by=user,
        assigned_to=assigned_to,
        title=title,
        priority=priority,
        status=status,
        due_date=due_date,
    )
    if created_offset_minutes:
        # ``created_at`` ist auto_now_add; einmaliger Bulk-Update fuer Tests OK.
        new_ts = timezone.now() + timedelta(minutes=created_offset_minutes)
        WorkItem.objects.filter(pk=wi.pk).update(created_at=new_ts)
        wi.refresh_from_db()
    return wi


def _wide_time_range():
    now = timezone.now()
    return (now - timedelta(days=1), now + timedelta(days=1))


def _make_client(facility, user, *, pseudonym: str = "C", created_offset_minutes: int = 0) -> Client:
    """Client mit optional nach hinten verschobenem ``created_at`` (auto_now_add)."""
    c = Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=user,
    )
    if created_offset_minutes:
        new_ts = timezone.now() + timedelta(minutes=created_offset_minutes)
        Client.objects.filter(pk=c.pk).update(created_at=new_ts)
        c.refresh_from_db()
    return c


def _range_now(hours: int = 2):
    """(now, time_range) mit einem Fenster ``now ± hours``.

    Frisch erzeugte Objekte (``auto_now``/``auto_now_add`` ≈ now) liegen im
    Fenster; per Offset weit nach aussen geschobene bewusst nicht.
    """
    now = timezone.now()
    return now, (now - timedelta(hours=hours), now + timedelta(hours=hours))


def _visible_events(user, facility, time_range):
    """Wie ``build_handover_summary`` die ``visible_events`` baut."""
    return Event.objects.visible_to(user).filter(
        facility=facility,
        is_deleted=False,
        occurred_at__range=time_range,
    )


# ---------------------------------------------------------------------------
# _collect_highlights
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCollectHighlights:
    """Refs `_collect_highlights`.

    Funktionsverhalten:
    - Sammelt bis zu 10 Crisis-Events, 10 Ban-Events, 10 Urgent/Important-
      Tasks (jeweils im Zeitfenster für Tasks).
    - Mischt sie und sortiert nach Zeit DESC.
    - Anreicherung von event-typ-Einträgen über ``enrich_events_with_preview``.
    """

    def test_returns_empty_when_no_events_and_no_tasks(self, facility, staff_user):
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        assert result == []

    def test_crisis_event_listed_with_type_crisis(self, facility, staff_user):
        dt_crisis = _make_doc_type(facility, system_type=DocumentType.SystemType.CRISIS, name="Krise")
        _make_event(facility, dt_crisis, user=staff_user)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        types = [h["type"] for h in result]
        assert types == ["crisis"]

    def test_ban_event_listed_with_type_ban(self, facility, staff_user):
        dt_ban = _make_doc_type(facility, system_type=DocumentType.SystemType.BAN, name="Ban")
        _make_event(facility, dt_ban, user=staff_user)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        assert [h["type"] for h in result] == ["ban"]

    def test_non_crisis_non_ban_event_excluded(self, facility, staff_user):
        """Boundary: ``system_type="crisis"`` / ``"ban"`` als exakte Filter.

        Ein Event ohne system_type darf nicht in Highlights landen.
        """
        dt_plain = _make_doc_type(facility, name="Kontakt")
        _make_event(facility, dt_plain, user=staff_user)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        assert result == []

    def test_urgent_task_in_range_listed_as_task(self, facility, lead_user):
        _make_workitem(facility, lead_user, priority=WorkItem.Priority.URGENT, title="urgent task")
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        assert [h["type"] for h in result] == ["task"]

    def test_important_task_in_range_listed_as_task(self, facility, lead_user):
        """Boundary: ``priority__in=["urgent", "important"]`` — beide Werte erlaubt."""
        _make_workitem(facility, lead_user, priority=WorkItem.Priority.IMPORTANT)
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        assert len(result) == 1
        assert result[0]["type"] == "task"

    def test_normal_priority_task_excluded(self, facility, lead_user):
        """Boundary: ``priority__in`` schliesst ``normal`` aus."""
        _make_workitem(facility, lead_user, priority=WorkItem.Priority.NORMAL)
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        assert result == []

    def test_task_outside_time_range_excluded(self, facility, lead_user):
        """Boundary: ``created_at__range=time_range`` filtert ausserhalb liegende Tasks."""
        _make_workitem(facility, lead_user, priority=WorkItem.Priority.URGENT, created_offset_minutes=-3 * 24 * 60)
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        assert result == []

    def test_crisis_events_cap_at_10(self, facility, staff_user):
        """Boundary: ``[:10]``-Slice. 11 Crisis-Events → genau 10 in Highlights."""
        dt_crisis = _make_doc_type(facility, system_type=DocumentType.SystemType.CRISIS, name="Krise")
        for i in range(11):
            _make_event(facility, dt_crisis, user=staff_user, offset_minutes=-i)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        assert sum(1 for h in result if h["type"] == "crisis") == 10

    def test_ban_events_cap_at_10(self, facility, staff_user):
        dt_ban = _make_doc_type(facility, system_type=DocumentType.SystemType.BAN, name="Ban")
        for i in range(11):
            _make_event(facility, dt_ban, user=staff_user, offset_minutes=-i)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        assert sum(1 for h in result if h["type"] == "ban") == 10

    def test_urgent_tasks_cap_at_10(self, facility, lead_user):
        for i in range(11):
            _make_workitem(
                facility,
                lead_user,
                priority=WorkItem.Priority.URGENT,
                title=f"u{i}",
                created_offset_minutes=-i,
            )
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        assert sum(1 for h in result if h["type"] == "task") == 10

    def test_sort_is_descending_by_time(self, facility, staff_user, lead_user):
        """``highlights.sort(key=lambda h: h["time"], reverse=True)``.

        Mutmut könnte ``reverse=True`` → ``reverse=False`` flippen.
        """
        dt_crisis = _make_doc_type(facility, system_type=DocumentType.SystemType.CRISIS)
        # Drei Events in unterschiedlicher Reihenfolge erzeugen, aber
        # ``occurred_at`` zeitlich gestaffelt: oldest → middle → newest.
        e_old = _make_event(facility, dt_crisis, user=staff_user, offset_minutes=-60)
        e_new = _make_event(facility, dt_crisis, user=staff_user, offset_minutes=-1)
        e_mid = _make_event(facility, dt_crisis, user=staff_user, offset_minutes=-30)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        times = [h["time"] for h in result]
        assert times == [e_new.occurred_at, e_mid.occurred_at, e_old.occurred_at]

    def test_task_highlight_uses_object_key(self, facility, lead_user):
        """Refs #1388 — killt ``x__collect_highlights__mutmut_90``.

        Der Task-Highlight-Eintrag muss den Key ``"object"`` tragen (das
        Template rendert ``h.object.pk``/``h.object.title``). Die Mutation
        ``"object"`` → ``"XXobjectXX"`` bricht den Zugriff.
        """
        wi = _make_workitem(facility, lead_user, priority=WorkItem.Priority.URGENT, title="urgent")
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        task_h = next(h for h in result if h["type"] == "task")
        assert "object" in task_h
        assert task_h["object"].pk == wi.pk

    def test_ban_highlight_object_gets_preview_fields(self, facility, staff_user):
        """Refs #1388 — killt ``x__collect_highlights__mutmut_100``.

        ``event_highlights = None`` würde den ``enrich_events_with_preview``-
        Aufruf überspringen (``if event_highlights:`` ist dann falsy). Ohne
        Anreicherung fehlt dem Ban-Event-Objekt das Attribut ``preview_fields``,
        das ``_highlights.html`` rendert.
        """
        dt_ban = _make_doc_type(facility, system_type=DocumentType.SystemType.BAN, name="Ban")
        _make_event(facility, dt_ban, user=staff_user)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        ban_h = next(h for h in result if h["type"] == "ban")
        assert hasattr(ban_h["object"], "preview_fields")


# ---------------------------------------------------------------------------
# _collect_stats — Zaehler-/Filter-Logik + Rueckgabe-Dict-Keys (Refs #1388)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCollectStats:
    """Refs `_collect_stats`.

    Zielt auf die verhaltensrelevanten Survivors: Facility-Scoping,
    Zeitfenster-Filter, Status-/``system_type``-Werte und die Zaehler-Existenz.
    Der Zugriff auf jeden Zaehler über seinen *korrekten* Key killt zugleich
    die Key-Rename-Mutationen (``"foo"`` → ``"XXfooXX"``/``"FOO"``) per KeyError.
    """

    def test_workitems_new_scoping(self, facility, second_facility, staff_user, second_facility_user):
        """killt mutmut_19 (=None), _20 (facility=None), _22 (Facility-Filter
        entfernt), _23 (created_at-Range entfernt), _46/_47 (Key ``workitems_new``).
        """
        now, time_range = _range_now()
        _make_workitem(facility, staff_user, title="w1")  # this fac, in range -> Ist-Treffer
        _make_workitem(second_facility, second_facility_user, title="w2")  # andere Facility -> _22
        _make_workitem(facility, staff_user, title="w3", created_offset_minutes=-300)  # ausserhalb -> _23
        stats = _collect_stats(facility, _visible_events(staff_user, facility, time_range), time_range)
        assert stats["workitems_new"] == 1

    def test_workitems_completed_scoping(self, facility, second_facility, staff_user, second_facility_user):
        """killt mutmut_24 (=None), _25 (facility=None), _26 (status=None),
        _28 (Facility-Filter entfernt), _29 (status-Filter entfernt),
        _30 (updated_at-Range entfernt), _31 (``XXdoneXX``), _32 (``DONE``),
        _48/_49 (Key ``workitems_completed``).
        """
        now, time_range = _range_now()
        # WC1: this fac, done, updated in range -> Ist-Treffer
        _make_workitem(facility, staff_user, title="wc1", status=WorkItem.Status.DONE)
        # WC2: andere Facility, done, updated in range -> _28
        _make_workitem(second_facility, second_facility_user, title="wc2", status=WorkItem.Status.DONE)
        # WC3: this fac, done, updated ausserhalb -> _30
        wc3 = _make_workitem(facility, staff_user, title="wc3", status=WorkItem.Status.DONE)
        WorkItem.objects.filter(pk=wc3.pk).update(updated_at=now - timedelta(hours=5))
        # WC4: this fac, OFFEN, updated in range -> _29
        _make_workitem(facility, staff_user, title="wc4", status=WorkItem.Status.OPEN)
        stats = _collect_stats(facility, _visible_events(staff_user, facility, time_range), time_range)
        assert stats["workitems_completed"] == 1

    def test_bans_new_counts_only_ban_system_type(self, facility, staff_user):
        """killt mutmut_33 (=None), _34 (system_type=None), _35 (``XXbanXX``),
        _36 (``BAN``), _50/_51 (Key ``bans_new``).

        Zwei Plain-Events (``system_type=NULL``) dienen als Diskriminator: die
        Mutation ``system_type=None`` (IS NULL) zählte sie statt des Ban-Events.
        """
        now, time_range = _range_now()
        dt_ban = _make_doc_type(facility, system_type=DocumentType.SystemType.BAN, name="Ban")
        dt_plain = _make_doc_type(facility, name="Kontakt")
        _make_event(facility, dt_ban, user=staff_user)
        _make_event(facility, dt_plain, user=staff_user)
        _make_event(facility, dt_plain, user=staff_user)
        stats = _collect_stats(facility, _visible_events(staff_user, facility, time_range), time_range)
        assert stats["bans_new"] == 1

    def test_clients_new_scoping(self, facility, second_facility, staff_user, second_facility_user):
        """killt mutmut_37 (=None), _38 (facility=None), _40 (Facility-Filter
        entfernt), _41 (created_at-Range entfernt), _52/_53 (Key ``clients_new``).
        """
        now, time_range = _range_now()
        _make_client(facility, staff_user, pseudonym="c1")  # this fac, in range -> Ist-Treffer
        _make_client(second_facility, second_facility_user, pseudonym="c2")  # andere Facility -> _40
        _make_client(facility, staff_user, pseudonym="c3", created_offset_minutes=-300)  # ausserhalb -> _41
        stats = _collect_stats(facility, _visible_events(staff_user, facility, time_range), time_range)
        assert stats["clients_new"] == 1


# ---------------------------------------------------------------------------
# build_handover_summary — Filter/Delegation (Refs #1388)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildHandoverSummaryMutations:
    """Refs `build_handover_summary`."""

    def test_soft_deleted_events_excluded(self, facility, staff_user, client_identified, doc_type_contact):
        """killt mutmut_19 (``is_deleted=False``-Filter entfernt).

        ``visible_to`` filtert nur nach Sensitivität, nicht nach Soft-Delete —
        ohne den expliziten ``is_deleted=False`` würde ein gelöschtes Event in
        die Übergabe gezählt.
        """
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
            is_deleted=True,
        )
        result = build_handover_summary(facility, today, None, staff_user)
        assert result["stats"]["events_total"] == 1

    def test_stats_use_facility_scope(self, facility, staff_user, client_identified):
        """killt mutmut_24 (``_collect_stats(None, ...)``).

        Mit ``facility=None`` fielen die facility-gescopten Zähler auf 0.
        """
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
            title="Neu",
        )
        result = build_handover_summary(facility, timezone.localdate(), None, staff_user)
        assert result["stats"]["workitems_new"] == 1

    def test_highlights_receive_user_for_enrichment(self, facility, staff_user, client_identified):
        """killt mutmut_34 (``_collect_highlights(..., None)``).

        Der User fließt in ``enrich_events_with_preview`` →
        ``user_can_see_field(user, ...)``, das ``user.role`` ohne None-Guard
        liest. Ein Ban-Event mit mindestens einem Feld-Template löst mit
        ``user=None`` daher ``AttributeError`` aus — die Übergabe würde crashen.
        """
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))
        dt_ban = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.ADMIN,
            name="Hausverbot",
            system_type="ban",
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Grund",
            field_type=FieldTemplate.FieldType.NUMBER,
        )
        DocumentTypeField.objects.create(document_type=dt_ban, field_template=ft, sort_order=0)
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt_ban,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        result = build_handover_summary(facility, today, None, staff_user)
        ban_highlights = [h for h in result["highlights"] if h["type"] == "ban"]
        assert len(ban_highlights) == 1
