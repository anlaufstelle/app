"""Mutation-Pins Welle L (0.99) — ``core.services.system.facility_export``, Batch ``facility_export-b``
(Vollmutation #2174, Refs #2193).

``core.services.system.facility_export`` ist auf vier Batches a/b/c/d verteilt
(andere Funktionen, andere Lanes) — diese Datei deckt ausschließlich die 62
Survivors aus ``facility_export-b`` (Liste: ``/work/wellen/welle-l-0.99/
reports/m-scratch/batch-facility_export-b.json``): ``_serialize_case`` (25),
``_build_entities`` (19), ``_serialize_client`` (18).

**``_serialize_case``/``_serialize_client`` (43 Mutanten):** zwei
Mutations-Familien je Funktion — (a) ein Dict-Schluessel wird umbenannt
(``"XXdocument_type_idXX"``/``"DOCUMENT_TYPE_ID"``), (b) ein Feldwert wird
durch den Null-Fall der Helper-Funktion ersetzt (``_uuid_str(None)``,
``_decrypt_data_json(None)``, ``_isoformat(None)``). Die Bestandsdeckung
(``test_facility_export.py``) prueft nie den VOLLSTAENDIGEN Schluesselsatz
und nie ALLE Werte eines Case-/Client-Eintrags auf einmal — pro Funktion
genuegt daher EIN „Voll-Eintrag"-Test mit durchgaengig befuellten
Optionalfeldern (Dokumentationstyp, Fallverantwortliche/r, Ersteller/in,
Schliessdatum bzw. Alterscluster/Notiz): ``set(entry.keys()) ==
{...}`` toetet jede Schluessel-Umbenennung (falscher Schluessel fehlt/ist
zusaetzlich im Set), die Wertevergleiche daneben toeten die
``_uuid_str(None)``/``_decrypt_data_json(None)``/``_isoformat(None)``-Mutanten
(nur bei tatsaechlich gesetztem Optionalfeld unterscheidbar von ``None``).

**``_build_entities`` (19 Mutanten):** drei Unterfamilien —

* **Sortierkriterium gestrichen** (``.order_by("<feld>", "pk")`` ->
  ``.order_by("pk")``): das fachliche Sortierfeld (Pseudonym/Zeitpunkt/
  Erstellungsdatum/Reihenfolge) faellt komplett weg. Gepinnt mit zwei
  Datensaetzen, deren PK-Reihenfolge der fachlichen Reihenfolge bewusst
  ENTGEGENLAEUFT (UUIDv4 sind zufaellig — die Tests vergeben deshalb ueber
  ``_pk(n)`` explizite, vergleichbare UUIDs).
* **``pk``-Tiebreak gestrichen** (``.order_by("<feld>", "pk")`` ->
  ``.order_by("<feld>", )``): nur bei GLEICHEM fachlichem Sortierwert
  unterscheidbar — dann liefert Postgres die Gruppe ohne Tiebreak in
  Heap-/Einfuegereihenfolge statt aufsteigend nach ``pk``. Gepinnt mit
  mehreren (3) Datensaetzen auf identischem Sortierschluessel, eingefuegt in
  einer PK-Reihenfolge, die der aufsteigenden ``pk``-Ordnung entgegenlaeuft
  (Konvention ``test_mutation_pins_094_export_2.py``); mehrere Zeilen je
  Gruppe druecken die Rest-Zufallswahrscheinlichkeit gegen null.
* **N+1 / ``select_related``/``prefetch_related``** — ``.prefetch_related(None)``
  statt ``Prefetch("milestones", ...)`` macht ``goal.milestones.all()`` je
  Wirkungsziel zu einer eigenen Query (N+1) statt EINER gebuendelten
  Vorabladung; gepinnt als Skalierungs-Zusicherung (``CaptureQueriesContext``,
  1 vs. 6 Wirkungsziele, Query-Zahl bleibt gleich). ``Prefetch("milestones",
  queryset=None)``/``Prefetch("milestones", )`` (Argument fehlt = derselbe
  Default) fallen beide auf ``Milestone``s Modell-Default-Ordering
  (``Meta.ordering = ["sort_order"]``, KEIN ``pk``-Tiebreak) zurueck — exakt
  dieselbe beobachtbare Abweichung wie das Streichen des ``pk``-Tiebreaks im
  expliziten ``order_by("sort_order", "pk")`` und wird von DERSELBEN
  Tiebreak-Probe getoetet.

  ``.select_related("case")`` an ``episodes``/``goals`` ist gegenueber
  ``.select_related(None)`` NACHWEISLICH aequivalent (kein Pin, siehe Bericht
  „skip"/„equivalent"-Abschnitt): weder ``_serialize_episode`` noch
  ``_serialize_goal`` lesen das ``.case``-Relationsobjekt — beide greifen
  ausschliesslich auf die Skalarspalte ``case_id`` zu (kein Join-Zugriff,
  keine Nachlade-Query moeglich). Der JOIN auf ``case`` entsteht in JEDEM Fall
  durch die WHERE-Filter (``case__facility=...`` usw.); ``select_related``
  aendert daran nur die SELECT-Spaltenliste derselben EINEN Query, nie die
  Query-Zahl oder einen Rueckgabewert.

Konventionen wie ``test_mutation_pins_094_export_2.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die eine ``in``-Pruefung uebersieht); PKs sind
UUIDv4, ``_pk(n)`` liefert deterministisch aufsteigende UUIDs
(``_pk(1) < _pk(2) < ...``, gilt fuer Postgres' ``uuid``-Typ bytegleich);
``created_at`` ist bei ``Case``/``OutcomeGoal``/``WorkItem``
``auto_now_add`` und wird deshalb per Queryset-``update()`` nachtraeglich
gesetzt; Fixtures aus ``src/tests/conftest.py``. KEIN Import aus
``test_rls_functional``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Case, Client, Episode, Event, Milestone, OutcomeGoal, WorkItem
from core.services.system.facility_export import build_facility_export

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _pk(n: int) -> uuid.UUID:
    """Deterministische UUID mit **bekannter** Sortierlage.

    Die Modelle dieses Moduls tragen ``UUIDField(primary_key=True,
    default=uuid.uuid4)``. Zufaellige PKs machen jede Aussage ueber „nach
    ``pk`` sortiert" unpruefbar; ``id`` ist aber direkt schreibbar.
    ``_pk(1) < _pk(2) < ...`` gilt fuer Postgres' ``uuid``-Typ (bytewise) wie
    fuer den String-Vergleich sonstiger Backends.
    """
    return uuid.UUID(int=n)


def _count_queries(call) -> int:
    """Query-Zahl eines Aufrufs — Skalierungs-Pin gegen N+1."""
    with CaptureQueriesContext(connection) as ctx:
        call()
    return len(ctx.captured_queries)


# ============================================================================
# _serialize_case — Schluessel + Werte (mutmut_7..31, 25 Mutanten)
# ============================================================================


class TestSerializeCaseFullShapeAndValues:
    """EIN vollstaendig befuellter Fall toetet alle 25 Survivors auf einmal."""

    def test_full_case_entry_matches_populated_case_exactly(
        self, facility, client_identified, doc_type_case_form, staff_user, lead_user
    ):
        """Pins ``x__serialize_case__mutmut_7``..``_31``.

        Schluessel-Umbenennungen (``document_type_id``/``title``/
        ``description``/``status``/``data_json``/``lead_user_id``/
        ``created_by_id``/``created_at``/``updated_at``/``closed_at``, je
        XX-Marker und GROSSSCHREIBUNG) treffen die ``set(entry.keys())``-
        Zusicherung; Wertersetzungen durch ``_uuid_str(None)``/
        ``_decrypt_data_json(None)``/``_isoformat(None)`` (mutmut_9/_18/_21/
        _24/_31) treffen die Werte-Zusicherungen darunter — dafuer sind
        ``document_type``, ``lead_user``, ``data_json`` und ``closed_at``
        hier bewusst NICHT leer/``None``.
        """
        closed_at = timezone.now() - timedelta(days=1)
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_case_form,
            title="Fall-Export-Shape",
            description="Beschreibungstext fuer den Shape-Pin",
            status=Case.Status.CLOSED,
            data_json={"anlass": "Erstkontakt", "verlauf": "Vertraulicher Verlauf"},
            lead_user=lead_user,
            created_by=staff_user,
            closed_at=closed_at,
        )

        export = build_facility_export(facility)
        entry = next(c for c in export["entities"]["cases"] if c["id"] == str(case.pk))

        assert set(entry.keys()) == {
            "id",
            "client_id",
            "document_type_id",
            "title",
            "description",
            "status",
            "data_json",
            "lead_user_id",
            "created_by_id",
            "created_at",
            "updated_at",
            "closed_at",
            "attachments",
        }
        assert entry["id"] == str(case.pk)
        assert entry["client_id"] == str(client_identified.pk)
        assert entry["document_type_id"] == str(doc_type_case_form.pk)
        assert entry["title"] == "Fall-Export-Shape"
        assert entry["description"] == "Beschreibungstext fuer den Shape-Pin"
        assert entry["status"] == Case.Status.CLOSED
        assert entry["data_json"] == {"anlass": "Erstkontakt", "verlauf": "Vertraulicher Verlauf"}
        assert entry["lead_user_id"] == str(lead_user.pk)
        assert entry["created_by_id"] == str(staff_user.pk)
        assert entry["created_at"] == case.created_at.isoformat()
        assert entry["updated_at"] == case.updated_at.isoformat()
        assert entry["closed_at"] == closed_at.isoformat()
        assert entry["attachments"] == []


# ============================================================================
# _serialize_client — Schluessel + Werte (mutmut_4..31, 18 Mutanten)
# ============================================================================


class TestSerializeClientFullShapeAndValues:
    """EIN vollstaendig befuellter Client toetet alle 18 Survivors auf einmal."""

    def test_full_client_entry_matches_populated_client_exactly(self, facility, doc_type_client_profile, staff_user):
        """Pins ``x__serialize_client__mutmut_4``..``_31``, analog
        ``TestSerializeCaseFullShapeAndValues``: ``pseudonym``/
        ``contact_stage``/``age_cluster``/``notes``/``is_active``/
        ``created_at``/``updated_at``/``created_by_id`` als
        Schluessel-Umbenennungen (mutmut_4/_5/_6/_7/_8/_9/_10/_11/_18/_19/
        _25/_26/_27/_28/_29/_30), ``document_type_id``/``created_by_id`` als
        ``_uuid_str(None)``-Wertersetzung (mutmut_14/_31).
        """
        client = Client.objects.create(
            facility=facility,
            pseudonym="Export-Shape-01",
            contact_stage=Client.ContactStage.QUALIFIED,
            age_cluster=Client.AgeCluster.AGE_27_PLUS,
            notes="Interne Randnotiz",
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "Eigene Wohnung", "diagnose": "Klartext-Diagnose"},
            k_anonymized=False,
            created_by=staff_user,
        )

        export = build_facility_export(facility)
        entry = next(c for c in export["entities"]["clients"] if c["id"] == str(client.pk))

        assert set(entry.keys()) == {
            "id",
            "pseudonym",
            "contact_stage",
            "age_cluster",
            "notes",
            "document_type_id",
            "data_json",
            "is_active",
            "k_anonymized",
            "is_anonymized",
            "created_at",
            "updated_at",
            "created_by_id",
            "attachments",
        }
        assert entry["id"] == str(client.pk)
        assert entry["pseudonym"] == "Export-Shape-01"
        assert entry["contact_stage"] == Client.ContactStage.QUALIFIED
        assert entry["age_cluster"] == Client.AgeCluster.AGE_27_PLUS
        assert entry["notes"] == "Interne Randnotiz"
        assert entry["document_type_id"] == str(doc_type_client_profile.pk)
        assert entry["data_json"] == {"wohnsituation": "Eigene Wohnung", "diagnose": "Klartext-Diagnose"}
        assert entry["is_active"] is True
        assert entry["k_anonymized"] is False
        assert entry["is_anonymized"] is False
        assert entry["created_at"] == client.created_at.isoformat()
        assert entry["updated_at"] == client.updated_at.isoformat()
        assert entry["created_by_id"] == str(staff_user.pk)
        assert entry["attachments"] == []


# ============================================================================
# _build_entities — Sortierung/Tiebreak/N+1 (mutmut_10/_11/_22/_23/_35/_36/
# _51/_52/_74/_75/_76/_89/_91/_96/_97/_109/_110, 17 gepinnte Mutanten;
# mutmut_53/_77 = select_related-Aequivalente, siehe Bericht)
# ============================================================================


class TestBuildEntitiesClientOrdering:
    def test_clients_ordered_by_pseudonym_not_only_by_pk(self, facility, staff_user):
        """Pin ``x__build_entities__mutmut_10``: ``.order_by("pseudonym", "pk")``
        -> ``.order_by("pk")``. ``A-Zuerst`` traegt die HOEHERE PK — sortiert
        das Original trotzdem zuerst (Pseudonym massgeblich), faellt der
        Mutant auf reine PK-Ordnung zurueck und vertauscht die beiden."""
        c_a = Client.objects.create(id=_pk(9), facility=facility, pseudonym="A-Zuerst", created_by=staff_user)
        c_b = Client.objects.create(id=_pk(1), facility=facility, pseudonym="B-Danach", created_by=staff_user)

        export = build_facility_export(facility)
        ids = [c["id"] for c in export["entities"]["clients"]]
        assert ids == [str(c_a.pk), str(c_b.pk)]

    # Kein Tiebreak-Pin fuer mutmut_11 (``.order_by("pseudonym", "pk")`` ->
    # ``.order_by("pseudonym", )``): ``unique_facility_pseudonym``
    # (core/models/client.py) verbietet zwei Clients mit identischem
    # Pseudonym in DERSELBEN Einrichtung — der Tiebreak ist empirisch
    # unerreichbar (``IntegrityError`` beim Versuch, siehe Bericht
    # „skip"/„equivalent"-Abschnitt). Aequivalent.


class TestBuildEntitiesEventOrdering:
    def test_events_ordered_by_occurred_at_desc_not_only_by_pk(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Pin ``x__build_entities__mutmut_22``: ``.order_by("-occurred_at", "pk")``
        -> ``.order_by("pk")``. Das juengere Ereignis traegt die HOEHERE PK —
        Original sortiert trotzdem zuerst (juengstes zuerst), Mutant faellt
        auf aufsteigende PK zurueck."""
        now = timezone.now()
        e_recent = Event.objects.create(
            id=_pk(9),
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        e_older = Event.objects.create(
            id=_pk(1),
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now - timedelta(days=3),
            data_json={},
            created_by=staff_user,
        )

        export = build_facility_export(facility)
        ids = [e["id"] for e in export["entities"]["events"]]
        assert ids == [str(e_recent.pk), str(e_older.pk)]

    def test_events_pk_is_tiebreak_when_occurred_at_ties(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Pin ``x__build_entities__mutmut_23``: ``.order_by("-occurred_at", "pk")``
        -> ``.order_by("-occurred_at", )``. Drei Ereignisse mit IDENTISCHEM
        ``occurred_at``, eingefuegt in PK-Reihenfolge 30, 20, 10 (entgegen der
        aufsteigenden PK-Ordnung)."""
        tied = timezone.now()
        e_hi = Event.objects.create(
            id=_pk(30),
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=tied,
            data_json={},
            created_by=staff_user,
        )
        e_mid = Event.objects.create(
            id=_pk(20),
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=tied,
            data_json={},
            created_by=staff_user,
        )
        e_lo = Event.objects.create(
            id=_pk(10),
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=tied,
            data_json={},
            created_by=staff_user,
        )

        export = build_facility_export(facility)
        ids = [e["id"] for e in export["entities"]["events"]]
        assert ids == [str(e_lo.pk), str(e_mid.pk), str(e_hi.pk)]


class TestBuildEntitiesCaseOrdering:
    def test_cases_ordered_by_created_at_desc_not_only_by_pk(self, facility, client_identified, staff_user):
        """Pin ``x__build_entities__mutmut_35``: ``.order_by("-created_at", "pk")``
        -> ``.order_by("pk")``. ``created_at`` ist ``auto_now_add`` — per
        Queryset-``update()`` nachtraeglich gesetzt (Konvention
        ``test_mutation_pins_094_export_2.py``)."""
        case_new = Case.objects.create(id=_pk(9), facility=facility, client=client_identified, created_by=staff_user)
        case_old = Case.objects.create(id=_pk(1), facility=facility, client=client_identified, created_by=staff_user)
        now = timezone.now()
        Case.objects.filter(pk=case_new.pk).update(created_at=now)
        Case.objects.filter(pk=case_old.pk).update(created_at=now - timedelta(days=5))

        export = build_facility_export(facility)
        ids = [c["id"] for c in export["entities"]["cases"]]
        assert ids == [str(case_new.pk), str(case_old.pk)]

    def test_cases_pk_is_tiebreak_when_created_at_ties(self, facility, client_identified, staff_user):
        """Pin ``x__build_entities__mutmut_36``: ``.order_by("-created_at", "pk")``
        -> ``.order_by("-created_at", )``. Drei Faelle mit IDENTISCHEM
        ``created_at``, PK-Einfuegereihenfolge 30, 20, 10."""
        case_hi = Case.objects.create(id=_pk(30), facility=facility, client=client_identified, created_by=staff_user)
        case_mid = Case.objects.create(id=_pk(20), facility=facility, client=client_identified, created_by=staff_user)
        case_lo = Case.objects.create(id=_pk(10), facility=facility, client=client_identified, created_by=staff_user)
        tied = timezone.now()
        Case.objects.filter(pk__in=[case_hi.pk, case_mid.pk, case_lo.pk]).update(created_at=tied)

        export = build_facility_export(facility)
        ids = [c["id"] for c in export["entities"]["cases"]]
        assert ids == [str(case_lo.pk), str(case_mid.pk), str(case_hi.pk)]


class TestBuildEntitiesEpisodeOrdering:
    def test_episodes_ordered_by_started_at_desc_not_only_by_pk(self, case_open, staff_user):
        """Pin ``x__build_entities__mutmut_51``: ``.order_by("-started_at", "pk")``
        -> ``.order_by("pk")``. ``started_at`` ist ein normales ``DateField``
        (nicht ``auto_now_add``) — direkt an ``create()`` setzbar."""
        facility = case_open.facility
        today = timezone.now().date()
        ep_new = Episode.objects.create(
            id=_pk(9), case=case_open, title="Episode neu", started_at=today, created_by=staff_user
        )
        ep_old = Episode.objects.create(
            id=_pk(1),
            case=case_open,
            title="Episode alt",
            started_at=today - timedelta(days=10),
            created_by=staff_user,
        )

        export = build_facility_export(facility)
        ids = [e["id"] for e in export["entities"]["episodes"]]
        assert ids == [str(ep_new.pk), str(ep_old.pk)]

    def test_episodes_pk_is_tiebreak_when_started_at_ties(self, case_open, staff_user):
        """Pin ``x__build_entities__mutmut_52``: ``.order_by("-started_at", "pk")``
        -> ``.order_by("-started_at", )``. Drei Episoden mit IDENTISCHEM
        ``started_at``, PK-Einfuegereihenfolge 30, 20, 10."""
        facility = case_open.facility
        same_day = timezone.now().date()
        ep_hi = Episode.objects.create(
            id=_pk(30), case=case_open, title="C", started_at=same_day, created_by=staff_user
        )
        ep_mid = Episode.objects.create(
            id=_pk(20), case=case_open, title="B", started_at=same_day, created_by=staff_user
        )
        ep_lo = Episode.objects.create(
            id=_pk(10), case=case_open, title="A", started_at=same_day, created_by=staff_user
        )

        export = build_facility_export(facility)
        ids = [e["id"] for e in export["entities"]["episodes"]]
        assert ids == [str(ep_lo.pk), str(ep_mid.pk), str(ep_hi.pk)]


class TestBuildEntitiesGoalOrdering:
    def test_goals_ordered_by_created_at_desc_not_only_by_pk(self, case_open, staff_user):
        """Pin ``x__build_entities__mutmut_74``: ``.order_by("-created_at", "pk")``
        -> ``.order_by("pk")``. ``OutcomeGoal.created_at`` ist ``auto_now_add``."""
        facility = case_open.facility
        goal_new = OutcomeGoal.objects.create(id=_pk(9), case=case_open, title="Ziel neu", created_by=staff_user)
        goal_old = OutcomeGoal.objects.create(id=_pk(1), case=case_open, title="Ziel alt", created_by=staff_user)
        now = timezone.now()
        OutcomeGoal.objects.filter(pk=goal_new.pk).update(created_at=now)
        OutcomeGoal.objects.filter(pk=goal_old.pk).update(created_at=now - timedelta(days=5))

        export = build_facility_export(facility)
        ids = [g["id"] for g in export["entities"]["goals"]]
        assert ids == [str(goal_new.pk), str(goal_old.pk)]

    def test_goals_pk_is_tiebreak_when_created_at_ties(self, case_open, staff_user):
        """Pin ``x__build_entities__mutmut_75``: ``.order_by("-created_at", "pk")``
        -> ``.order_by("-created_at", )``. Drei Ziele mit IDENTISCHEM
        ``created_at``, PK-Einfuegereihenfolge 30, 20, 10."""
        facility = case_open.facility
        goal_hi = OutcomeGoal.objects.create(id=_pk(30), case=case_open, title="C", created_by=staff_user)
        goal_mid = OutcomeGoal.objects.create(id=_pk(20), case=case_open, title="B", created_by=staff_user)
        goal_lo = OutcomeGoal.objects.create(id=_pk(10), case=case_open, title="A", created_by=staff_user)
        tied = timezone.now()
        OutcomeGoal.objects.filter(pk__in=[goal_hi.pk, goal_mid.pk, goal_lo.pk]).update(created_at=tied)

        export = build_facility_export(facility)
        ids = [g["id"] for g in export["entities"]["goals"]]
        assert ids == [str(goal_lo.pk), str(goal_mid.pk), str(goal_hi.pk)]


class TestBuildEntitiesMilestoneOrdering:
    """``Prefetch("milestones", queryset=Milestone.objects.order_by("sort_order", "pk"))``."""

    def test_milestones_ordered_by_sort_order_not_only_by_pk(self, outcome_goal):
        """Pin ``x__build_entities__mutmut_96``: die Prefetch-Queryset-Sortierung
        ``.order_by("sort_order", "pk")`` -> ``.order_by("pk")``."""
        facility = outcome_goal.case.facility
        m_first = Milestone.objects.create(id=_pk(9), goal=outcome_goal, title="Zuerst", sort_order=0)
        m_second = Milestone.objects.create(id=_pk(1), goal=outcome_goal, title="Danach", sort_order=1)

        export = build_facility_export(facility)
        goal_entry = next(g for g in export["entities"]["goals"] if g["id"] == str(outcome_goal.pk))
        ids = [m["id"] for m in goal_entry["milestones"]]
        assert ids == [str(m_first.pk), str(m_second.pk)]

    def test_milestones_pk_is_tiebreak_when_sort_order_ties(self, outcome_goal):
        """Pins ``x__build_entities__mutmut_89``/``_91``/``_97``: alle drei
        entfernen den ``pk``-Tiebreak der Milestone-Prefetch-Queryset —
        ``queryset=None`` bzw. das fehlende ``queryset``-Argument fallen auf
        ``Milestone``s Modell-Default-Ordering (``Meta.ordering =
        ["sort_order"]``, KEIN ``pk``) zurueck, exakt wie
        ``.order_by("sort_order", )``. Drei Meilensteine mit IDENTISCHEM
        ``sort_order``, PK-Einfuegereihenfolge 30, 20, 10."""
        facility = outcome_goal.case.facility
        m_hi = Milestone.objects.create(id=_pk(30), goal=outcome_goal, title="C", sort_order=5)
        m_mid = Milestone.objects.create(id=_pk(20), goal=outcome_goal, title="B", sort_order=5)
        m_lo = Milestone.objects.create(id=_pk(10), goal=outcome_goal, title="A", sort_order=5)

        export = build_facility_export(facility)
        goal_entry = next(g for g in export["entities"]["goals"] if g["id"] == str(outcome_goal.pk))
        ids = [m["id"] for m in goal_entry["milestones"]]
        assert ids == [str(m_lo.pk), str(m_mid.pk), str(m_hi.pk)]


class TestBuildEntitiesGoalMilestonesPrefetch:
    def test_goal_milestones_prefetch_avoids_n_plus_one(self, facility, case_open, staff_user):
        """Pin ``x__build_entities__mutmut_76``: ``.prefetch_related(Prefetch(
        "milestones", ...))`` -> ``.prefetch_related(None)``. Ohne die
        gebuendelte Vorabladung loest ``goal.milestones.all()`` in
        ``_serialize_goal`` je Wirkungsziel eine EIGENE Query aus (N+1); mit
        ihr bleibt die Gesamt-Query-Zahl unabhaengig von der Zahl der Ziele
        (Konvention ``TestOrderedAttachmentsFieldTemplateJoin`` in
        ``test_mutation_pins_094_export_2.py``)."""
        goal_1 = OutcomeGoal.objects.create(case=case_open, title="Ziel 1", created_by=staff_user)
        Milestone.objects.create(goal=goal_1, title="Meilenstein 1")
        # Aufwaermen: erster Aufruf traegt einmalige Nebenkosten (Modell-Cache).
        build_facility_export(facility)
        with_one_goal = _count_queries(lambda: build_facility_export(facility))

        for index in range(2, 7):
            goal = OutcomeGoal.objects.create(case=case_open, title=f"Ziel {index}", created_by=staff_user)
            Milestone.objects.create(goal=goal, title=f"Meilenstein {index}")
        with_six_goals = _count_queries(lambda: build_facility_export(facility))

        export = build_facility_export(facility)
        assert len(export["entities"]["goals"]) == 6
        assert with_six_goals == with_one_goal


class TestBuildEntitiesWorkItemOrdering:
    def test_workitems_ordered_by_created_at_desc_not_only_by_pk(self, facility, client_identified, staff_user):
        """Pin ``x__build_entities__mutmut_109``: ``.order_by("-created_at", "pk")``
        -> ``.order_by("pk")``. ``WorkItem.created_at`` ist ``auto_now_add``."""
        wi_new = WorkItem.objects.create(
            id=_pk(9),
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Neu",
        )
        wi_old = WorkItem.objects.create(
            id=_pk(1),
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Alt",
        )
        now = timezone.now()
        WorkItem.objects.filter(pk=wi_new.pk).update(created_at=now)
        WorkItem.objects.filter(pk=wi_old.pk).update(created_at=now - timedelta(days=5))

        export = build_facility_export(facility)
        ids = [wi["id"] for wi in export["entities"]["work_items"]]
        assert ids == [str(wi_new.pk), str(wi_old.pk)]

    def test_workitems_pk_is_tiebreak_when_created_at_ties(self, facility, client_identified, staff_user):
        """Pin ``x__build_entities__mutmut_110``: ``.order_by("-created_at", "pk")``
        -> ``.order_by("-created_at", )``. Drei Arbeitsauftraege mit
        IDENTISCHEM ``created_at``, PK-Einfuegereihenfolge 30, 20, 10."""
        wi_hi = WorkItem.objects.create(
            id=_pk(30),
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="C",
        )
        wi_mid = WorkItem.objects.create(
            id=_pk(20),
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="B",
        )
        wi_lo = WorkItem.objects.create(
            id=_pk(10),
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="A",
        )
        tied = timezone.now()
        WorkItem.objects.filter(pk__in=[wi_hi.pk, wi_mid.pk, wi_lo.pk]).update(created_at=tied)

        export = build_facility_export(facility)
        ids = [wi["id"] for wi in export["entities"]["work_items"]]
        assert ids == [str(wi_lo.pk), str(wi_mid.pk), str(wi_hi.pk)]
