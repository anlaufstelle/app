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

* **Sortierkriterium/``pk``-Tiebreak gestrichen** (``.order_by("<feld>",
  "pk")`` -> ``.order_by("pk")`` bzw. -> ``.order_by("<feld>", )``):
  gepinnt **planunabhaengig** ueber eine Spy auf ``QuerySet.order_by``
  (``_capture_order_by``/``_last_order_by`` unten) — geprueft wird das
  tatsaechlich an ``order_by()`` uebergebene Argument-Tupel WAEHREND
  ``build_facility_export`` laeuft, nicht die von Postgres
  zurueckgelieferte Zeilenreihenfolge. **Grund (Nachbesserung nach
  Zweitgutachten, Zwischenstand dieser Datei enthielt zunaechst eine
  zeilenreihenfolge-basierte Fassung nach Konvention
  ``test_mutation_pins_094_export_2.py``):** ``x__build_entities__mutmut_36``
  (Case-``pk``-Tiebreak) erwies sich empirisch als NICHT beobachtbar ueber
  die Zeilenreihenfolge — mehrfacher Lauf unter
  ``MUTANT_UNDER_TEST=…mutmut_36`` blieb GRUEN, obwohl der Tiebreak fehlt;
  Ursache: Postgres liefert bei identischem ``created_at`` (drei per
  ``.filter(pk__in=[...]).update(...)`` gleichgesetzte Zeilen) auch OHNE
  ``pk``-Tiebreak eine Reihenfolge, die zufaellig/haeufig der aufsteigenden
  ``pk``-Ordnung entspricht — die Heap-/Einfuegereihenfolge-Annahme haelt
  (wie im Docstring von ``test_mutation_pins_094_export_2.py`` selbst schon
  vermerkt: „seit 0.96 nicht mehr zuverlaessig") empirisch NICHT verlaesslich,
  und zwar nicht nur nach einem ``update()``: eine Nachpruefung fand
  dieselbe Falsch-Gruen-Symptomatik auch bei
  ``x__build_entities__mutmut_23`` (Event-Tiebreak, Zeilen direkt mit
  gleichem ``occurred_at`` angelegt, KEIN ``update()``) und
  ``x__build_entities__mutmut_110`` (WorkItem-Tiebreak, analog Case). Die
  komplette Ordering-Testfamilie (primaer UND Tiebreak, alle sechs
  Entity-Querysets plus die Milestone-Prefetch-Queryset) wurde deshalb auf
  die planunabhaengige Introspektion umgestellt statt nur die eine
  gemeldete Zeile zu reparieren (dieselbe Fehlerklasse
  einmal gefunden ⇒ repo-/dateiweit sweepen).
* **N+1 / ``select_related``/``prefetch_related``** — ``.prefetch_related(None)``
  statt ``Prefetch("milestones", ...)`` macht ``goal.milestones.all()`` je
  Wirkungsziel zu einer eigenen Query (N+1) statt EINER gebuendelten
  Vorabladung; gepinnt als Skalierungs-Zusicherung (``CaptureQueriesContext``,
  1 vs. 6 Wirkungsziele, Query-Zahl bleibt gleich). ``Prefetch("milestones",
  queryset=None)``/``Prefetch("milestones", )`` (Argument fehlt = derselbe
  Default) rufen ``Milestone.objects.order_by(...)`` gar NICHT auf (die
  Spy sieht fuer ``Milestone`` keinen Aufruf) — dieselbe Introspektion
  toetet deshalb auch diese beiden zusammen mit dem expliziten
  ``order_by("sort_order", "pk")``-Streichen in einem Test.

  ``.select_related("case")`` an ``episodes``/``goals`` ist gegenueber
  ``.select_related(None)`` NACHWEISLICH aequivalent (kein Pin, siehe Bericht
  „skip"/„equivalent"-Abschnitt): weder ``_serialize_episode`` noch
  ``_serialize_goal`` lesen das ``.case``-Relationsobjekt — beide greifen
  ausschliesslich auf die Skalarspalte ``case_id`` zu (kein Join-Zugriff,
  keine Nachlade-Query moeglich). Der JOIN auf ``case`` entsteht in JEDEM Fall
  durch die WHERE-Filter (``case__facility=...`` usw.); ``select_related``
  aendert daran nur die SELECT-Spaltenliste derselben EINEN Query, nie die
  Query-Zahl oder einen Rueckgabewert.

Konventionen wie ``test_mutation_pins_094_export_2.py`` (Zusicherungen
vergleichen EXAKT ``==``, nie per Teilstring — mutmut erzeugt
``"XX<text>XX"``-Varianten, die eine ``in``-Pruefung uebersieht) gelten
weiter fuer ``_serialize_case``/``_serialize_client``; die
Ordering-Introspektion braucht dagegen bewusst KEINE Datensaetze mit
kollidierenden Sortierwerten mehr (das dort noch verwendete
``_pk(n)``-Muster ist mit dieser Nachbesserung entfallen). Fixtures aus
``src/tests/conftest.py``. KEIN Import aus ``test_rls_functional``.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Case, Client, Milestone, OutcomeGoal
from core.services.system.facility_export import build_facility_export

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _capture_order_by(call) -> list[tuple[str, tuple]]:
    """Erfasst jeden ``QuerySet.order_by``-Aufruf waehrend ``call()`` laeuft,
    als ``(Modellname, args)`` in Aufrufreihenfolge.

    Planunabhaengig: prueft die tatsaechlich am Python-Aufruf uebergebenen
    Sortierkriterien, NICHT die von Postgres zurueckgelieferte
    Zeilenreihenfolge. Letztere ist bei Gleichstand ohne entscheidendes
    Kriterium implementierungsabhaengig und — empirisch verifiziert bei der
    Nachbesserung dieser Datei (siehe Modul-Docstring) — NICHT verlaesslich
    beobachtbar, weder nach einem ``.filter(pk__in=...).update(...)`` noch
    bei direkt mit identischem Sortierwert angelegten Zeilen.
    """
    calls: list[tuple[str, tuple]] = []
    original = QuerySet.order_by

    def _spy(self, *args, **kwargs):
        calls.append((self.model.__name__, args))
        return original(self, *args, **kwargs)

    with mock.patch.object(QuerySet, "order_by", _spy):
        call()
    return calls


def _last_order_by(calls: list[tuple[str, tuple]], model_name: str) -> tuple | None:
    """Letztes ``order_by``-Argument-Tupel fuer ``model_name`` aus ``calls``,
    ``None`` wenn kein Aufruf erfasst wurde (z. B. ``Prefetch(..., queryset=None)``
    — dann wird ``Model.objects.order_by(...)`` gar nicht aufgerufen)."""
    matches = [args for name, args in calls if name == model_name]
    return matches[-1] if matches else None


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


class TestBuildEntitiesOrderingCriteria:
    """Plan-unabhaengige Sortierkriterien-Pins fuer alle sechs Entity-Querysets
    plus die Milestone-Prefetch-Queryset in ``_build_entities``. Beobachtet
    das tatsaechlich an ``QuerySet.order_by`` uebergebene Argument-Tupel
    waehrend ``build_facility_export`` laeuft (``_capture_order_by``/
    ``_last_order_by``, siehe Modul-Docstring fuer die Begruendung gegen
    eine zeilenreihenfolge-basierte Fassung) — NICHT die zurueckgelieferte
    Zeilenreihenfolge. Braucht bewusst KEINE angelegten Cases/Events/etc.:
    ``order_by()`` wird beim Aufbau der Querysets in ``_build_entities``
    aufgerufen, unabhaengig davon, ob spaeter Zeilen zurueckkommen — eine
    leere ``facility`` genuegt."""

    def test_clients_order_by_pseudonym_then_pk(self, facility):
        """Pin ``x__build_entities__mutmut_10`` (Sortierkriterium "pseudonym"
        gestrichen, nur noch ``.order_by("pk")``)."""
        calls = _capture_order_by(lambda: build_facility_export(facility))
        assert _last_order_by(calls, "Client") == ("pseudonym", "pk")

    def test_events_order_by_occurred_at_desc_then_pk(self, facility):
        """Pins ``x__build_entities__mutmut_22`` (Sortierkriterium
        "-occurred_at" gestrichen) und ``_23`` (``pk``-Tiebreak gestrichen)."""
        calls = _capture_order_by(lambda: build_facility_export(facility))
        assert _last_order_by(calls, "Event") == ("-occurred_at", "pk")

    def test_cases_order_by_created_at_desc_then_pk(self, facility):
        """Pins ``x__build_entities__mutmut_35`` (Sortierkriterium
        "-created_at" gestrichen) und ``_36`` (``pk``-Tiebreak gestrichen —
        die urspruengliche, ueber Zeilenreihenfolge gepinnte Fassung dieses
        Tests blieb unter ``MUTANT_UNDER_TEST=…mutmut_36`` faelschlich
        GRUEN, siehe Modul-Docstring/Bericht)."""
        calls = _capture_order_by(lambda: build_facility_export(facility))
        assert _last_order_by(calls, "Case") == ("-created_at", "pk")

    def test_episodes_order_by_started_at_desc_then_pk(self, facility):
        """Pins ``x__build_entities__mutmut_51`` (Sortierkriterium
        "-started_at" gestrichen) und ``_52`` (``pk``-Tiebreak gestrichen)."""
        calls = _capture_order_by(lambda: build_facility_export(facility))
        assert _last_order_by(calls, "Episode") == ("-started_at", "pk")

    def test_goals_order_by_created_at_desc_then_pk(self, facility):
        """Pins ``x__build_entities__mutmut_74`` (Sortierkriterium
        "-created_at" gestrichen) und ``_75`` (``pk``-Tiebreak gestrichen)."""
        calls = _capture_order_by(lambda: build_facility_export(facility))
        assert _last_order_by(calls, "OutcomeGoal") == ("-created_at", "pk")

    def test_milestones_prefetch_queryset_order_by_sort_order_then_pk(self, facility):
        """Pins ``x__build_entities__mutmut_96`` (Sortierkriterium
        "sort_order" gestrichen), ``_97`` (``pk``-Tiebreak gestrichen) sowie
        ``_89``/``_91`` (``Prefetch("milestones", queryset=None)`` bzw. ohne
        ``queryset``-Argument — dort wird ``Milestone.objects.order_by(...)``
        gar nicht aufgerufen, ``_last_order_by`` liefert dann ``None``, was
        ebenfalls von der Zusicherung unten als Abweichung erkannt wird)."""
        calls = _capture_order_by(lambda: build_facility_export(facility))
        assert _last_order_by(calls, "Milestone") == ("sort_order", "pk")

    def test_workitems_order_by_created_at_desc_then_pk(self, facility):
        """Pins ``x__build_entities__mutmut_109`` (Sortierkriterium
        "-created_at" gestrichen) und ``_110`` (``pk``-Tiebreak gestrichen —
        dieselbe Falsch-Gruen-Symptomatik wie bei Case-``mutmut_36``
        empirisch bestaetigt, siehe Modul-Docstring)."""
        calls = _capture_order_by(lambda: build_facility_export(facility))
        assert _last_order_by(calls, "WorkItem") == ("-created_at", "pk")


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
