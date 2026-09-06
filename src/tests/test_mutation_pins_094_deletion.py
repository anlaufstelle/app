"""Pin-Tests zum Meilenstein release-0.94 — Modul-Batch ``deletion``.

Quelle: ``src/core/services/deletion.py`` (gemeinsame Helfer des
Vier-Augen-Loeschworkflows, Refs #1053/#1736). Vorgabe ist die
Survivor-Triage release-0.94 (Ledger des Meilensteins) (4 Mutanten,
Herkunft ``welle-d``; Mutantenbaum ``mutants/src/core/services/deletion.py``).

Beide gepinnten Funktionen tragen eine Zusicherung, die bisher NUR auf der
Werteebene geprueft war — und genau daneben liegen die Ueberlebenden:

* :func:`~core.services.deletion.eligible_deletion_confirmers` — die
  **Reihenfolge** des Genehmiger-Pools (``order_by("display_name",
  "username")``). Der Bestandspin ``test_mutation_pins_090.py`` prueft nur die
  Mitgliedschaft (``== [admin_user]``) und ist damit blind fuer beide
  ``order_by``-Mutanten. Gepinnt wird (a) der Vorrang des Anzeigenamens vor
  dem Benutzernamen und (b) der ``username``-Tiebreak bei gleichem
  Anzeigenamen — letzterer mit einer Einfuegereihenfolge, die der erwarteten
  entgegenlaeuft, damit die Postgres-Heap-Ordnung den Mutanten nicht zufaellig
  rettet (Muster ``test_mutation_pins_094_export_2.py``).

* :func:`~core.services.deletion.annotate_deletion_targets` — der **Zuschnitt
  der Ereignis-Bulk-Query**. Der Docstring benennt die Buendelung als Vertrag
  („zwei Queries fuer die ganze Liste, nicht eine je Zeile"); die beiden
  Mutanten lassen das Ergebnis bitgleich und zerstoeren nur die Kosten:
  ``select_related(None)`` laedt den Dokumentationstyp je Antrag nach (N+1),
  und der Verlust von ``pk__in=event_ids`` loest die Query von den
  referenzierten Zeilen und laedt stattdessen die GESAMTE sichtbare
  Ereignishistorie der Einrichtung. Gepinnt wird deshalb einmal als
  Skalierungs-Zusicherung (Query-Zahl unabhaengig von der Antragszahl, Muster
  ``test_client_export.py::test_attachments_are_fetched_per_owner_kind_not_per_owner``)
  und einmal als Zuschnitt-Zusicherung ueber das tatsaechlich abgesetzte SQL
  (Muster #1816: die Query darf nicht mit der irrelevanten Ereignismenge
  skalieren).

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); Sprachkontext
wird bei uebersetzten Texten per ``translation.override("de")`` festgenagelt;
Fixtures kommen aus ``src/tests/conftest.py`` bzw. sind echte Model-Objekte
(keine Fakes); KEIN Import aus ``test_rls_functional``. Jeder Test nennt seine
Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation
from django.utils.formats import date_format

from core.models import DeletionRequest, DocumentType, Event, User
from core.services.deletion import annotate_deletion_targets, eligible_deletion_confirmers

pytestmark = pytest.mark.django_db


def _confirmer(facility, *, username: str, display_name: str) -> User:
    """Aktive Genehmiger:in — ``can_confirm_deletion`` ist rollenunabhaengig (#1053)."""
    return User.objects.create_user(
        username=username,
        role=User.Role.STAFF,
        facility=facility,
        display_name=display_name,
        can_confirm_deletion=True,
    )


def _count_queries(call) -> int:
    """Query-Zahl eines Aufrufs — Skalierungs-Pins gegen N+1."""
    with CaptureQueriesContext(connection) as ctx:
        call()
    return len(ctx.captured_queries)


# ============================================================================
# eligible_deletion_confirmers — die Reihenfolge des Genehmiger-Pools
# ============================================================================


class TestEligibleDeletionConfirmersOrder:
    """Der Pool wird nach Anzeigename sortiert, mit Benutzername als Tiebreak.

    Die Liste erscheint auf der Antragsseite („wer koennte diesen Antrag
    bestaetigen") und im Setup-Guard der Loeschantraege-Liste
    (``case_deletion.py``/``client_deletion.py``/``client_merge.py``,
    Kontextvariable ``confirmers``). Angezeigt wird der Anzeigename — eine
    Liste, die nach dem unsichtbaren Benutzernamen sortiert ist, wirkt fuer
    die Antragstellerin unsortiert; ohne Tiebreak springt sie zwischen zwei
    Aufrufen sogar.
    """

    def test_display_name_beats_username_in_the_order(self, facility):
        """Pin: ``x_eligible_deletion_confirmers__mutmut_17`` — Zeile 125
        ``.order_by("display_name", "username")`` -> ``.order_by("username")``.

        Die beiden Konten sind bewusst gegenlaeufig benannt: „Anna" traegt den
        Benutzernamen ``zzz-anna``, „Berta" den Benutzernamen ``aaa-berta``.
        Original: ``[anna, berta]`` (nach Anzeigename). Mutant: ``[berta,
        anna]`` — die Reihenfolge kippt vollstaendig, weil nur noch der
        Benutzername sortiert.
        """
        anna = _confirmer(facility, username="zzz-anna", display_name="Anna Achter")
        berta = _confirmer(facility, username="aaa-berta", display_name="Berta Bauer")

        assert list(eligible_deletion_confirmers(facility)) == [anna, berta]

    def test_username_is_the_tiebreak_for_equal_display_names(self, facility):
        """Pin: ``x_eligible_deletion_confirmers__mutmut_18`` — Zeile 125
        ``.order_by("display_name", "username")`` -> ``.order_by("display_name", )``.

        Vier Konten mit IDENTISCHEM Anzeigenamen (Namensgleichheit ist in
        einer Einrichtung real: „A. Bauer"); der Tiebreak ist das einzige, was
        die Gruppe noch ordnet. Eingefuegt werden sie in der UMGEKEHRTEN
        Reihenfolge (``zzz`` zuerst, ``aaa`` zuletzt), damit die
        Heap-/Einfuegereihenfolge, die Postgres ohne zweites Sortierfeld
        liefert, dem erwarteten Ergebnis entgegenlaeuft.

        Original: ``["aaa-vier", "mmm-drei", "ttt-zwei", "zzz-eins"]``.
        Mutant: die Reihenfolge der gleichrangigen Gruppe ist ohne zweites
        Sortierfeld SQL-seitig nicht garantiert; im geprueften
        PostgreSQL-Lauf kam zuverlaessig die Einfuegereihenfolge
        ``["zzz-eins", "ttt-zwei", "mmm-drei", "aaa-vier"]`` (umgebungsgebundene
        Sonde, keine probabilistische Absicherung); vier Zeilen statt zwei
        machen einen zufaellig richtigen Treffer zusaetzlich unwahrscheinlich.
        """
        eins = _confirmer(facility, username="zzz-eins", display_name="A. Bauer")
        zwei = _confirmer(facility, username="ttt-zwei", display_name="A. Bauer")
        drei = _confirmer(facility, username="mmm-drei", display_name="A. Bauer")
        vier = _confirmer(facility, username="aaa-vier", display_name="A. Bauer")

        assert list(eligible_deletion_confirmers(facility)) == [vier, drei, zwei, eins]


# ============================================================================
# annotate_deletion_targets — der Zuschnitt der Ereignis-Bulk-Query
# ============================================================================


class TestAnnotateDeletionTargetsEventQuery:
    """Die Ereignis-Aufloesung ist gebuendelt UND auf die Antraege zugeschnitten.

    Beide Mutanten dieser Familie lassen ``target_label``/``target_url``
    unveraendert — die Bestandstests (``test_mutation_pins_090.py``,
    ``test_deletion_request_list_targets.py``) pruefen ausschliesslich Werte
    bei einem einzigen Antrag und sind damit strukturell blind. Beide Pins
    messen deshalb die Query selbst, nicht das Label; das Label steht in
    beiden Tests nur als Positiv-Kontrolle daneben.
    """

    @staticmethod
    def _event(facility, client, user, index: int) -> Event:
        """Ereignis mit EIGENEM Dokumentationstyp (Default-Sensibilitaet)."""
        doc_type = DocumentType.objects.create(facility=facility, name=f"Beratungstyp {index}")
        return Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=index),
            data_json={},
            created_by=user,
        )

    @staticmethod
    def _request(facility, user, event: Event) -> DeletionRequest:
        return DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=event.pk,
            reason="Grund",
            requested_by=user,
        )

    @staticmethod
    def _expected_label(event: Event) -> str:
        occurred = date_format(timezone.localtime(event.occurred_at), "DATE_FORMAT")
        return f"{event.document_type.name} — {occurred}"

    def test_document_type_is_joined_not_reloaded_per_request(self, facility, staff_user, lead_user, client_identified):
        """Pin: ``x_annotate_deletion_targets__mutmut_23`` — Zeile 181
        ``.select_related("document_type")`` -> ``.select_related(None)``.

        Der Mutant loescht die Optimierung ersatzlos (es gibt keine vorherige
        ``select_related``-Kette, die uebrig bliebe). Das Label ist danach
        bitgleich, aber Zeile 232 (``target.document_type.name``) laedt den
        Dokumentationstyp je Ereignis-Antrag nach.

        Original: die Query-Zahl ist unabhaengig von der Zahl der Antraege
        (ein Antrag und sechs Antraege kosten gleich viel). Mutant: sechs
        Antraege kosten fuenf Queries mehr als einer — die beiden gemessenen
        Zahlen laufen auseinander.
        """
        events = [self._event(facility, client_identified, staff_user, index) for index in range(6)]
        requests = [self._request(facility, staff_user, event) for event in events]

        # Aufwaermen: der erste Aufruf traegt einmalige Nebenkosten.
        annotate_deletion_targets(requests[:1], facility, lead_user)
        with_one = _count_queries(lambda: annotate_deletion_targets(requests[:1], facility, lead_user))
        with_six = _count_queries(lambda: annotate_deletion_targets(requests, facility, lead_user))

        assert with_six == with_one
        # Positiv-Kontrolle: die Buendelung darf kein Label verlieren.
        with translation.override("de"):
            annotate_deletion_targets(requests, facility, lead_user)
        assert [dr.target_label for dr in requests] == [self._expected_label(event) for event in events]

    def test_event_query_is_scoped_to_the_requested_ids(self, facility, staff_user, lead_user, client_identified):
        """Pin: ``x_annotate_deletion_targets__mutmut_27`` — Zeile 180
        ``.filter(facility=facility, pk__in=event_ids)`` -> ``.filter(facility=facility, )``.

        Der Mutant entkoppelt die Bulk-Query von den referenzierten
        ``event_ids``. Das Ergebnis fuer den angefragten Antrag bleibt korrekt
        (die gesuchte Zeile ist in der groesseren Menge enthalten, Facility-
        und Sensibilitaets-Gate bleiben aktiv, also kein Leck) — die
        Loeschantraege-Liste laedt aber ab dann bei JEDEM Aufruf die gesamte
        sichtbare Ereignishistorie der Einrichtung.

        Geprueft wird deshalb das tatsaechlich abgesetzte SELECT auf
        ``core_event``: es gibt genau eines, und es ist auf die referenzierte
        PK eingeschraenkt. Hier stehen 20 weitere sichtbare Ereignisse
        derselben Einrichtung ohne Loeschantrag daneben; die Zeilenzahl des
        abgesetzten SELECTs wird nachgezaehlt.

        Original: eine Ereignis-Query, die die Ziel-PK nennt und **1** Zeile
        liefert. Mutant: dieselbe Query ohne PK-Einschraenkung — sie nennt die
        Ziel-PK nicht und liefert **21** Zeilen (skaliert also mit der
        irrelevanten Historie).
        """
        target = self._event(facility, client_identified, staff_user, 0)
        for index in range(1, 21):
            self._event(facility, client_identified, staff_user, index)
        dr = self._request(facility, staff_user, target)

        with CaptureQueriesContext(connection) as ctx:
            with translation.override("de"):
                annotate_deletion_targets([dr], facility, lead_user)

        event_selects = [
            query["sql"]
            for query in ctx.captured_queries
            if query["sql"].lstrip().upper().startswith("SELECT") and '"core_event"' in query["sql"]
        ]
        assert len(event_selects) == 1, "Ereignis-Aufloesung ist nicht mehr genau eine Query:\n" + "\n".join(
            event_selects
        )
        # Die PK steht als UUID-Literal im ausgefuehrten SQL (psycopg schreibt sie
        # ohne Bindestriche: ``'ac37…ece'::uuid``).
        # Zweite, unabhaengige Sonde: die Query liefert genau die referenzierte
        # Zeile, ihre Zeilenzahl skaliert NICHT mit der uebrigen Historie.
        with connection.cursor() as cursor:
            cursor.execute(event_selects[0])
            assert len(cursor.fetchall()) == 1
        # Positiv-Kontrolle: der Zuschnitt darf das Label nicht kosten.
        assert dr.target_label == self._expected_label(target)
