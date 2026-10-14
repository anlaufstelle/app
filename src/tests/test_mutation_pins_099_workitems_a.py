"""Pin-Tests der Welle L (0.99) — Survivor-Triage in ``core.services.case.workitems``.

Refs #2174,
#2193.

Quelle: atomarer Vollmutations-Messlauf (Batch ``workitems-a``), Funktion
``build_inbox_sections`` — der groesste Einzel-Batch der Welle (121
Survivoren). Die Funktion baut Querysets, Sektionen und Filterkontext der
WorkItem-Inbox; sie gibt selbst KEIN HTML aus, jede Abweichung ist an der
Reihenfolge der zurueckgegebenen Listen, an einzelnen Dict-Werten oder an
der Anzahl ausgefuehrter Datenbank-Queries beobachtbar.

Gepinnt werden neun Familien:

* **``select_related``** — fehlende Relation erzeugt eine zusaetzliche
  SELECT-Query beim Zugriff auf ``client``/``created_by``/``assigned_to``
  (klassisches N+1).
* **Sortier-Kette** (``order_by``) — jeder der vier Schluessel
  (``due_date_bucket``, ``due_date``, ``priority_order``, ``-created_at``)
  entscheidet in eigens dafuer konstruierten Konstellationen ueber die
  Reihenfolge.
* **``priority_order``-Annotation** — dringend/wichtig/normal muessen auf
  0/1/2 abbilden; eine Kollision oder ein NULL-Ergebnis fuer "wichtig"
  aendert die beobachtete Reihenfolge (dass "normal" bereits die letzte
  Stufe ist und ein NULL-Ergebnis dort UNBEOBACHTBAR bleibt, ist unten bei
  den ``equivalent``-Urteilen begruendet).
* **``due_date_bucket``-Annotation** — ueberfaellig/heute/zukuenftig/ohne
  Frist (0/1/2/9) plus der Default-Bucket 5 fuer erledigte, aber
  ueberfaellige Items (nur erreichbar, weil die erste Bedingung zusaetzlich
  ``status__in=[OPEN, IN_PROGRESS]`` verlangt).
* **Sieben-Tage-Fenster** der Sektion "Kuerzlich erledigt".
* **``done_full_has_more``-Initialwert**, wenn der Status-Filter nicht
  ``done`` ist.
* **``done_period_label``** — Anzeigetext des gewaehlten Zeitraums.
* **``has_active_filters``** — Erkennung eines gesetzten Faelligkeitsfilters.
* **``facility_users``-Sortierung** sowie **Rueckgabe-Schluessel/-Werte**
  (Umbenennung bzw. falscher Query-Param-Schluessel fuer
  ``selected_item_type``/``selected_priority``/``selected_due``).

Konventionen wie ``test_mutation_pins_097_workitems.py``: eine Testklasse je
Aspektgruppe; jeder Test nennt seine Mutanten in der Docstring samt dem Wert,
der beim Mutanten abweicht; Zusicherungen vergleichen EXAKT (``==``/``is``/
Listenreihenfolge), nie per Teilstring. Keine Fakes — echte Model-Objekte
durchlaufen den echten Service. Eine Ausnahme: der letzte Sortier-Schluessel
(``-created_at``) wird NICHT ueber die Zeilenreihenfolge geprueft, sondern
ueber die tatsaechlich ausgefuehrte SQL (``CaptureQueriesContext``) — fehlen
allen vorangehenden Schluesseln echte Gleichstaende, ist die Zeilenreihenfolge
ohne den letzten Schluessel laut SQL-Standard undefiniert und haengt vom
physischen Scan-Zustand der Tabelle ab (an diesem Mutanten empirisch als
instabil beobachtet).

``_encryption_key`` liegt modulweit an: der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``; ohne die Fixture waeren die Pins dort aus dem FALSCHEN
Grund rot und der Kill-Nachweis wertlos.

Mehrere Survivoren dieses Batches sind ``equivalent`` (kein Pin, siehe
Batch-Bericht fuer die vollstaendige Begruendungsliste): u. a. jede
Mutation, die eine ohnehin bereits "letzte" Sortierstufe (die Prioritaet
NORMAL, der Bucket "ohne Frist") auf NULL setzt — NULL sortiert unter
Postgres' Standard (NULLS LAST bei aufsteigender Sortierung) ebenfalls ans
Ende, die Position aendert sich nicht; ebenso jede Aenderung eines
``output_field``-Arguments auf ``None`` (das IST der Django-Default, Django
leitet den Feldtyp aus den ``Value``-Ergebnissen automatisch ab — per
SQL-Vergleich verifiziert, siehe Batch-Bericht).
"""

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import User, WorkItem
from core.services.case.workitems import build_inbox_sections

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

#: Letzter Sortier-Schluessel der Basis-Queryset in ``build_inbox_sections``
#: (``-created_at``) direkt in der ausgefuehrten SQL geprueft — robuster als
#: ein Zeilenreihenfolge-Vergleich, wenn alle VORIGEN Sortier-Schluessel
#: absichtlich gleichstehen (siehe ``TestBuildInboxSectionsOrderBy``).
_CREATED_AT_DESC_TAIL = re.compile(r'"core_workitem"\."created_at" DESC')


def _make_workitem(facility, user, **overrides):
    """Aufgabe mit sprechenden Defaults; Titel eindeutig genug fuer Debug-Ausgaben."""
    defaults = {
        "facility": facility,
        "created_by": user,
        "item_type": WorkItem.ItemType.TASK,
        "status": WorkItem.Status.OPEN,
        "title": "Pin-Aufgabe",
    }
    defaults.update(overrides)
    return WorkItem.objects.create(**defaults)


def _set_updated_at(workitem, *, updated_at):
    """Setzt ``updated_at`` DIREKT in der DB (bypass ``auto_now`` bei ``save()``)."""
    WorkItem.objects.filter(pk=workitem.pk).update(updated_at=updated_at)
    workitem.refresh_from_db()
    return workitem


# ---------------------------------------------------------------------------
# select_related("client", "created_by", "assigned_to") — 3 Mutanten
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsSelectRelated:
    """``.select_related("client", "created_by", "assigned_to")`` (3 Mutanten)."""

    def test_verknuepfte_objekte_ohne_zusatzquery_erreichbar(self, facility, staff_user, client_identified):
        """Pin: ``x_build_inbox_sections__mutmut_18/19/20`` — je eine der drei
        ``select_related``-Relationen entfernt (``client``/``created_by``/
        ``assigned_to``).

        Ohne die jeweilige Relation loest der NACHGELAGERTE Zugriff auf das
        Attribut eine zusaetzliche SELECT-Query aus (klassisches N+1); das
        erfasste Query-Fenster bleibt bei ALLEN drei Relationen vorhanden bei
        exakt null zusaetzlichen Queries.
        """
        item = _make_workitem(
            facility,
            staff_user,
            client=client_identified,
            created_by=staff_user,
            assigned_to=staff_user,
        )
        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        fetched = result["open_items"][0]
        assert fetched.pk == item.pk

        with CaptureQueriesContext(connection) as ctx:
            _ = fetched.client.pseudonym
            _ = fetched.created_by.username
            _ = fetched.assigned_to.username
        assert len(ctx.captured_queries) == 0


# ---------------------------------------------------------------------------
# order_by("due_date_bucket", "due_date", "priority_order", "-created_at")
# — 4 Mutanten
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsOrderBy:
    """Sortier-Kette der Basis-Queryset (4 Mutanten)."""

    def test_faellt_due_date_bucket_weg_vertauscht_kuerzlich_erledigt(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_7`` — ``due_date_bucket`` aus
        der Sortierung entfernt.

        In "Kuerzlich erledigt" (Status DONE) faellt ein laengst
        ueberfaelliges, aber erledigtes Item auf den Default-Bucket 5, ein
        noch NICHT faelliges erledigtes Item auf Bucket 2 — der Bucket
        schlaegt das reine Faelligkeitsdatum. Ohne den Bucket-Schluessel
        sortiert reines ``due_date`` das laengst ueberfaellige Item VOR das
        knapp erledigte, obwohl beide erledigt sind.
        """
        today = timezone.localdate()
        # Recently done is windowed by completed_at since #2587.
        now = timezone.now()
        overdue_done = _make_workitem(
            facility, staff_user, status=WorkItem.Status.DONE, due_date=today - timedelta(days=30), completed_at=now
        )
        future_done = _make_workitem(
            facility, staff_user, status=WorkItem.Status.DONE, due_date=today + timedelta(days=5), completed_at=now
        )
        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        ids = [wi.pk for wi in result["done_items"]]
        assert ids == [future_done.pk, overdue_done.pk]

    def test_faellt_due_date_weg_vertauscht_faellige_reihenfolge(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_8`` — ``due_date`` aus der
        Sortierung entfernt.

        Zwei offene Items mit gleichem Bucket ("Naechste 7 Tage") und
        gleicher Prioritaet unterscheiden sich nur im Faelligkeitsdatum; das
        FRUEHER faellige wurde ABSICHTLICH ZUERST angelegt (aelterer
        ``created_at``). Ohne den ``due_date``-Schluessel entscheidet nur
        noch ``-created_at`` — das juengere (spaeter faellige) Item rutscht
        faelschlich nach vorn.
        """
        today = timezone.localdate()
        sooner_due = _make_workitem(facility, staff_user, due_date=today + timedelta(days=2))
        later_due = _make_workitem(facility, staff_user, due_date=today + timedelta(days=6))
        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        ids = [wi.pk for wi in result["open_items"]]
        assert ids == [sooner_due.pk, later_due.pk]

    def test_faellt_priority_order_weg_vertauscht_prioritaet(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_9`` — ``priority_order`` aus
        der Sortierung entfernt.

        Zwei offene Items mit identischem Faelligkeitsdatum unterscheiden
        sich nur in der Prioritaet; das DRINGENDE wurde ABSICHTLICH ZUERST
        angelegt (aelterer ``created_at``). Ohne ``priority_order``
        entscheidet nur noch ``-created_at`` — das juengere normale Item
        rutscht vor das dringende.
        """
        today = timezone.localdate()
        urgent_item = _make_workitem(
            facility, staff_user, due_date=today + timedelta(days=3), priority=WorkItem.Priority.URGENT
        )
        normal_item = _make_workitem(
            facility, staff_user, due_date=today + timedelta(days=3), priority=WorkItem.Priority.NORMAL
        )
        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        ids = [wi.pk for wi in result["open_items"]]
        assert ids == [urgent_item.pk, normal_item.pk]

    def test_faellt_created_at_als_letzter_sortier_schluessel_weg(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_10`` — ``-created_at`` (der
        letzte Sortier-Schluessel) aus ``.order_by(...)`` entfernt.

        Ein Zeilenreihenfolge-Vergleich waere hier INSTABIL: fehlt der letzte
        Sortier-Schluessel und binden alle vorherigen Schluessel nicht (echte
        Gleichstaende), ist die Reihenfolge laut SQL-Standard undefiniert —
        Postgres liefert dann die physische Scan-Reihenfolge, die vom
        Tabellen-/Autovacuum-Zustand abhaengt und sich zwischen Laeufen
        AENDERN kann (an diesem Mutanten empirisch beobachtet). Stattdessen
        wird die tatsaechlich ausgefuehrte SQL direkt geprueft: die
        ORDER-BY-Klausel MUSS mit ``"core_workitem"."created_at" DESC``
        enden — das ist unabhaengig von Tabelleninhalt und Scan-Reihenfolge.
        """
        with CaptureQueriesContext(connection) as ctx:
            build_inbox_sections(facility, staff_user, QueryDict(""))
        order_by_snippets = [
            q["sql"][q["sql"].find("ORDER BY") :]
            for q in ctx.captured_queries
            if '"core_workitem"' in q["sql"] and "ORDER BY" in q["sql"]
        ]
        assert order_by_snippets, "keine core_workitem-Abfrage mit ORDER BY erfasst"
        for snippet in order_by_snippets:
            assert _CREATED_AT_DESC_TAIL.search(snippet), snippet


# ---------------------------------------------------------------------------
# priority_order-Annotation: URGENT/IMPORTANT/NORMAL -> 0/1/2 — 7 Mutanten
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsPriorityOrder:
    """``priority_order``-Annotation (7 Mutanten)."""

    def test_dringend_vor_wichtig_vor_normal_bei_gleicher_faelligkeit(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_33/41/42/43/45/46/47``.

        Drei offene Items mit IDENTISCHEM Faelligkeitsdatum (gleicher
        Bucket, gleiches Datum) unterscheiden sich nur in der Prioritaet;
        Anlage-Reihenfolge dringend -> wichtig -> normal, GEGEN die
        Prioritaets-Reihenfolge. Jede Kollision ("wichtig" kollidiert mit
        "dringend" oder "normal") oder jedes NULL-Ergebnis fuer "wichtig"
        kippt die erwartete Reihenfolge, weil der einzige verbleibende
        Tiebreaker (``-created_at``) die Anlage-Reihenfolge widerspiegelt.
        """
        today = timezone.localdate()
        urgent_item = _make_workitem(
            facility, staff_user, due_date=today + timedelta(days=4), priority=WorkItem.Priority.URGENT
        )
        important_item = _make_workitem(
            facility,
            staff_user,
            due_date=today + timedelta(days=4),
            priority=WorkItem.Priority.IMPORTANT,
        )
        normal_item = _make_workitem(
            facility, staff_user, due_date=today + timedelta(days=4), priority=WorkItem.Priority.NORMAL
        )
        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        ids = [wi.pk for wi in result["open_items"]]
        assert ids == [urgent_item.pk, important_item.pk, normal_item.pk]


# ---------------------------------------------------------------------------
# due_date_bucket-Annotation — 18 Mutanten
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsDueDateBucket:
    """``due_date_bucket``-Annotation: ueberfaellig/heute/zukuenftig/ohne Frist (18 Mutanten)."""

    def test_offene_items_ueberfaellig_vor_heute_vor_zukunft_vor_ohne_frist(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_60/61/68/71/72/74/75/77/78/81/83/84``.

        Vier offene Items decken alle vier "echten" Buckets ab (0
        ueberfaellig, 1 heute, 2 zukuenftig, 9 ohne Frist) bei GLEICHER
        Prioritaet — jede kaputte Bucket-Bedingung oder jeder auf NULL
        fallende Bucket-Wert verschiebt genau eines der vier Items aus
        seiner erwarteten Position.
        """
        today = timezone.localdate()
        overdue = _make_workitem(facility, staff_user, due_date=today - timedelta(days=3))
        due_today = _make_workitem(facility, staff_user, due_date=today)
        future = _make_workitem(facility, staff_user, due_date=today + timedelta(days=3))
        no_due = _make_workitem(facility, staff_user, due_date=None)

        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        ids = [wi.pk for wi in result["open_items"]]
        assert ids == [overdue.pk, due_today.pk, future.pk, no_due.pk]

    def test_kuerzlich_erledigt_default_bucket_vs_echte_buckets(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_58/62/64/70/90/93``.

        Vier erledigte Items: eines laengst ueberfaellig (faellt NUR bei
        Status DONE/DISMISSED auf den Default-Bucket 5, weil die erste
        ``When``-Bedingung zusaetzlich ``status__in=[OPEN, IN_PROGRESS]``
        verlangt), eines heute, eines erst in der Zukunft faellig, eines
        ohne Frist. Jede Verschiebung des Default-Buckets (5 -> NULL) oder
        eine Kollision des Zukunfts- mit dem Default-Bucket (2 -> 5)
        aendert die Reihenfolge.
        """
        today = timezone.localdate()
        # Recently done is windowed by completed_at since #2587.
        now = timezone.now()
        overdue_done = _make_workitem(
            facility, staff_user, status=WorkItem.Status.DONE, due_date=today - timedelta(days=20), completed_at=now
        )
        today_done = _make_workitem(facility, staff_user, status=WorkItem.Status.DONE, due_date=today, completed_at=now)
        future_done = _make_workitem(
            facility, staff_user, status=WorkItem.Status.DONE, due_date=today + timedelta(days=3), completed_at=now
        )
        no_due_done = _make_workitem(facility, staff_user, status=WorkItem.Status.DONE, due_date=None, completed_at=now)

        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        ids = [wi.pk for wi in result["done_items"]]
        assert ids == [today_done.pk, future_done.pk, overdue_done.pk, no_due_done.pk]


# ---------------------------------------------------------------------------
# Sieben-Tage-Fenster der Sektion "Kuerzlich erledigt" — 2 Mutanten
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsDoneWindow:
    """``seven_days_ago``-Fenster (2 Mutanten)."""

    def test_acht_tage_alte_erledigung_faellt_aus_dem_sieben_tage_fenster(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_218`` — ``timedelta(days=7)``
        -> ``timedelta(days=8)``.

        Ein vor 7 Tagen und 12 Stunden aktualisiertes erledigtes Item liegt
        AUSSERHALB des Sieben-Tage-Fensters und darf in "Kuerzlich erledigt"
        NICHT erscheinen. Mit einem Acht-Tage-Fenster waere es noch drin.
        """
        today = timezone.localdate()
        stale_done = _make_workitem(facility, staff_user, status=WorkItem.Status.DONE, due_date=today)
        _set_updated_at(stale_done, updated_at=timezone.now() - timedelta(days=7, hours=12))

        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        ids = [wi.pk for wi in result["done_items"]]
        assert stale_done.pk not in ids

    def test_lange_zurueckliegende_erledigung_bleibt_ausserhalb_ohne_zeitfilter(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_228`` — der Filter
        ``updated_at__gte=seven_days_ago`` komplett aus der Abfrage entfernt.

        Ein vor 90 Tagen aktualisiertes erledigtes Item gehoert NICHT in
        "Kuerzlich erledigt" (7-Tage-Fenster). Ohne den Zeitfilter wuerde
        die Sektion JEDE jemals erledigte Aufgabe zeigen.
        """
        today = timezone.localdate()
        old_done = _make_workitem(facility, staff_user, status=WorkItem.Status.DONE, due_date=today)
        _set_updated_at(old_done, updated_at=timezone.now() - timedelta(days=90))

        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        ids = [wi.pk for wi in result["done_items"]]
        assert old_done.pk not in ids


# ---------------------------------------------------------------------------
# done_full_has_more-Initialwert — 2 Mutanten
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsDoneFullHasMoreDefault:
    """``done_full_has_more = False``, solange ``status != "done"`` (2 Mutanten)."""

    def test_initialwert_bleibt_false_ohne_status_filter_done(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_232/233`` —
        ``done_full_has_more = False`` -> ``None``/``True``.

        Ohne ``status=done`` im Query-String bleibt ``show_done_full``
        False, die ``if``-Verzweigung wird nie betreten und der
        Initialwert landet unveraendert im Rueckgabe-Dict.
        """
        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        assert result["done_full_has_more"] is False


# ---------------------------------------------------------------------------
# done_period_label — 3 Mutanten
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsDonePeriodLabel:
    """``done_period_label`` = Anzeigetext des gewaehlten Zeitraums (3 Mutanten)."""

    def test_label_entspricht_dem_gewaehlten_zeitraum(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_251/252/254``.

        Mit ``done_period=30`` MUSS das Label "Letzte 30 Tage" lauten. 251
        ersetzt die ganze Zuweisung durch ``None``; 252 sucht mit dem
        FALSCHEN Schluessel ``None`` statt ``done_period`` (liefert immer
        den leeren Default); 254 sucht mit dem Schluessel ``""`` statt
        ``done_period`` (liefert immer ``None``, da ``.get("")`` ohne
        zweites Argument aufgerufen wird).
        """
        params = QueryDict("done_period=30")
        result = build_inbox_sections(facility, staff_user, params)
        assert result["done_period_label"] == "Letzte 30 Tage"


# ---------------------------------------------------------------------------
# has_active_filters — 4 Mutanten
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsHasActiveFiltersDueKey:
    """``has_active_filters`` erkennt einen gesetzten Faelligkeitsfilter (4 Mutanten)."""

    def test_gesetzter_faelligkeitsfilter_setzt_has_active_filters(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_278/280/282/283``.

        Mit AUSSCHLIESSLICH ``due=today`` gesetzt (sonst keine Filter,
        Default-Scope) MUSS ``has_active_filters`` True sein. Die vier
        Mutanten lesen den Faelligkeitsfilter unter dem FALSCHEN Schluessel
        (``None``, ``""``, ``"XXdueXX"``, ``"DUE"``) und liefern immer den
        leeren Default — ``has_active_filters`` bliebe faelschlich False.
        """
        params = QueryDict("due=today")
        result = build_inbox_sections(facility, staff_user, params)
        assert result["has_active_filters"] is True


# ---------------------------------------------------------------------------
# facility_users-Sortierung — 2 Mutanten
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsFacilityUsersOrder:
    """``facility_users``-Sortierung: last_name, first_name, username (2 Mutanten)."""

    def test_sortierung_nach_nachname_dann_vorname_dann_benutzername(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_290/291``.

        Drei Konten: zwei mit gleichem Nachnamen (unterschiedlichem
        Vornamen), eines mit anderem Nachnamen; der Benutzername ist
        bewusst GEGENLAEUFIG zu Vor-/Nachnamen vergeben, damit ein
        fehlender Sortier-Schluessel eine ANDERE Reihenfolge erzwingt statt
        zufaellig dieselbe zu treffen.
        """
        user_a = User.objects.create_user(
            username="aaa_a",
            role=User.Role.STAFF,
            facility=facility,
            is_staff=True,
            last_name="Adler",
            first_name="Zoe",
        )
        user_b = User.objects.create_user(
            username="zzz_b",
            role=User.Role.STAFF,
            facility=facility,
            is_staff=True,
            last_name="Adler",
            first_name="Anna",
        )
        user_c = User.objects.create_user(
            username="ccc_c",
            role=User.Role.STAFF,
            facility=facility,
            is_staff=True,
            last_name="Berg",
            first_name="Anna",
        )
        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        ids = [u.pk for u in result["facility_users"] if u.pk != staff_user.pk]
        assert ids == [user_b.pk, user_a.pk, user_c.pk]


# ---------------------------------------------------------------------------
# Rueckgabe-Schluessel vollstaendig — 16 Mutanten (Schluessel umbenannt)
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsOutputKeys:
    """Vollstaendigkeit der Rueckgabe-Schluessel (16 Mutanten)."""

    _EXPECTED_KEYS = {
        "open_items",
        "open_has_more",
        "in_progress_items",
        "in_progress_has_more",
        "done_items",
        "done_has_more",
        "done_items_preview",
        "done_items_extra",
        "recent_done_preview",
        "done_full_items",
        "done_full_has_more",
        "done_period_label",
        "show_open",
        "show_in_progress",
        "show_done_widget",
        "show_done_full",
        "inbox_list_limit",
        "item_type_choices",
        "priority_choices",
        "status_choices",
        "due_filter_choices",
        "status_filter_choices",
        "done_period_choices",
        "facility_users",
        "assignable_users",
        "selected_item_type",
        "selected_priority",
        "selected_assigned_to",
        "default_assigned_scope",
        "selected_due",
        "selected_status",
        "selected_done_period",
        "has_active_filters",
        "forbidden_ids",
    }

    def test_alle_kontext_schluessel_vollstaendig_und_unveraendert(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_329/330/341/342/343/344/345/
        346/349/350/351/352/355/356/364/365``.

        Jede dieser Mutanten benennt GENAU EINEN Rueckgabe-Schluessel um
        (z. B. ``done_period_label`` -> ``XXdone_period_labelXX`` bzw.
        ``DONE_PERIOD_LABEL``). Der ORIGINAL-Schluessel fehlt dann im Dict
        und der umbenannte taucht zusaetzlich auf — die exakte
        Schluesselmenge deckt jede einzelne dieser Umbenennungen auf.
        """
        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        assert set(result.keys()) == self._EXPECTED_KEYS


# ---------------------------------------------------------------------------
# selected_item_type / selected_priority / selected_due — 17 Mutanten
# ---------------------------------------------------------------------------


class TestBuildInboxSectionsSelectedFilterValues:
    """``selected_item_type``/``selected_priority``/``selected_due`` spiegeln
    die Query-Parameter (17 Mutanten)."""

    def test_gesetzte_werte_werden_unveraendert_durchgereicht(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_357/359/361/362/366/368/370/371``.

        Mit ``item_type=task`` und ``priority=urgent`` gesetzt MUESSEN
        ``selected_item_type``/``selected_priority`` genau diese Werte
        tragen. Die genannten Mutanten lesen unter einem FALSCHEN
        Schluessel (``None``, ``""``, Gross-/Kleinschreibung vertauscht,
        Praefix ``XX…XX``) und liefern immer den leeren Default.
        """
        params = QueryDict("item_type=task&priority=urgent")
        result = build_inbox_sections(facility, staff_user, params)
        assert result["selected_item_type"] == "task"
        assert result["selected_priority"] == "urgent"

    def test_fehlende_werte_bleiben_leerer_string_nicht_none_oder_platzhalter(self, facility, staff_user):
        """Pin: ``x_build_inbox_sections__mutmut_358/360/363/367/369/372/381/383/386``.

        OHNE ``item_type``/``priority``/``due`` im Query-String MUESSEN
        ``selected_item_type``/``selected_priority``/``selected_due`` exakt
        der leere String sein — nicht ``None`` und nicht ein
        Platzhalter-Text (``"XXXX"``).
        """
        result = build_inbox_sections(facility, staff_user, QueryDict(""))
        assert result["selected_item_type"] == ""
        assert result["selected_priority"] == ""
        assert result["selected_due"] == ""
