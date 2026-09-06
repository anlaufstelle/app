"""Pin-Tests zum Meilenstein release-0.94 — Survivor-Batch ``export_2``.

Quelle: ``src/core/services/client/export.py`` (Auskunfts-Export nach DSGVO
Art. 15/20). Modul-Batch ``export_2`` der Survivor-Triage; Vorgabe ist
die Survivor-Triage release-0.94 (Ledger des Meilensteins) (60 Mutanten, Herkunft
``welle-d``/``welle-f``, Mutantenbaum ``mutants/src/core/services/client/export.py``).

Der Batch faellt in vier Familien, die hier je Funktion gebuendelt gepinnt
werden:

* **Feld-Vollstaendigkeit der Auskunft** — Dict-Schluessel und ihre Werte in
  :func:`~core.services.client.export._gather_legal_holds`,
  :func:`~core.services.client.export._gather_outcome_goals` und
  :func:`~core.services.client.export._gather_retention_proposals`. mutmut
  benennt Schluessel um (``"XXexpires_atXX"``/``"EXPIRES_AT"``) oder ersetzt
  den Wert durch ``_isoformat(None)``/``_username(None)``. Beides trifft ein
  **Vergleich des ganzen Eintrags** (``entry == {...}``) — deshalb steht je
  Kategorie ein „Voll-Eintrag"-Test mit gesetzten Optionalfeldern (Ablauf,
  Aufhebung, Zurueckstellung, Erreichung), denn nur gesetzte Werte
  unterscheiden ``_isoformat(x)`` von ``_isoformat(None)``.

* **Reihenfolge** — ``order_by``-Mutanten, die das fachliche Kriterium
  streichen (``.order_by("-created_at", "pk")`` -> ``.order_by("pk")``). Die
  PKs sind UUIDv4; die Tests vergeben deshalb **explizite** UUIDs, sodass die
  PK-Reihenfolge nachweislich von der fachlichen abweicht.

* **Stabiler ``pk``-Tiebreak** — Mutanten, die nur das zweite Sortierfeld
  streichen. Gepinnt mit Datensaetzen auf **identischem** Sortierschluessel und
  einer PK-Reihenfolge, die der Einfuegereihenfolge entgegenlaeuft; ohne
  Tiebreak liefert Postgres die Gruppe in Heap-/Einfuegereihenfolge. Mehrere
  Zeilen je Gruppe druecken die Rest-Zufallswahrscheinlichkeit gegen null.

* **N+1 / ``select_related``** — Mutanten, die ein Join-Feld aus
  ``select_related``/``prefetch_related`` streichen. Ergebnis bitgleich, Kosten
  linear. Gepinnt in der Hausform als **Skalierungs-Zusicherung**: dieselbe
  Auskunft mit wenigen und mit vielen Datensaetzen kostet gleich viele Queries
  (``CaptureQueriesContext``). Keine feste Query-Zahl — die waere ein
  Implementierungs-Pin.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); Sprachkontext
wird bei uebersetzten Texten (``get_status_display``) per
``translation.override("de")`` festgenagelt; Fixtures kommen aus
``src/tests/conftest.py`` bzw. sind echte Model-Objekte (keine Fakes); KEIN
Import aus ``test_rls_functional``. Jeder Test nennt seine Mutanten in der
Docstring samt dem Wert, der beim Mutanten abweicht.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation

from core.models import (
    Attachment,
    Client,
    FieldTemplate,
    LegalHold,
    Milestone,
    OutcomeGoal,
    RecentClientVisit,
    RectificationRequest,
    RetentionProposal,
    WorkItem,
)
from core.services.client.export import (
    _gather_attachments,
    _gather_legal_holds,
    _gather_merged_sources,
    _gather_outcome_goals,
    _gather_recent_visits,
    _gather_rectification_requests,
    _gather_retention_proposals,
    _gather_workitems,
)


def _pk(n: int) -> uuid.UUID:
    """Deterministische UUID mit **bekannter** Sortierlage.

    Die Modelle dieses Moduls tragen ``UUIDField(primary_key=True,
    default=uuid.uuid4)``. Zufaellige PKs machen jede Aussage ueber „nach ``pk``
    sortiert" unpruefbar; ``id`` ist aber schreibbar. ``_pk(1) < _pk(2) < …``
    gilt sowohl fuer Postgres' ``uuid``-Typ (bytewise) als auch fuer den
    String-Vergleich sonstiger Backends.
    """
    return uuid.UUID(int=n)


def _count_queries(call) -> int:
    """Query-Zahl eines Aufrufs — Skalierungs-Pins gegen N+1."""
    with CaptureQueriesContext(connection) as ctx:
        call()
    return len(ctx.captured_queries)


# ============================================================================
# _ordered_attachments — der ``field_template``-Join (1 Mutant)
# ============================================================================


@pytest.mark.django_db
class TestOrderedAttachmentsFieldTemplateJoin:
    """Die Feld-Vorlage wird mitgeladen, nicht je Anhang nachgeladen.

    ``_ordered_attachments`` ist die EINE Stelle, an der Sortierung und Joins
    der Anhang-Serialisierung festgelegt sind; ``_gather_attachments`` liest
    aus jeder Zeile ``field_template.name`` UND ``field_template.slug``. Ohne
    ``select_related("field_template")`` ist das Ergebnis bitgleich, kostet
    aber eine Query je Anhang — bei einer Person mit langer Historie skaliert
    die Auskunft linear (G4-Befund, Refs #1738).
    """

    @staticmethod
    def _add_attachment(facility, owner, field_template, user, index: int) -> Attachment:
        return Attachment.objects.create(
            facility=facility,
            client=owner,
            field_template=field_template,
            storage_filename=f"vault-{index}.bin",
            original_filename_encrypted=f"anhang-{index}.pdf",
            file_size=1024 + index,
            mime_type="application/pdf",
            created_by=user,
        )

    def test_field_template_is_joined_not_reloaded_per_attachment(self, facility, client_identified, admin_user):
        """Pin: ``x__ordered_attachments__mutmut_11`` — ``select_related("field_template", "created_by")``
        -> ``select_related("created_by")``.

        Original: die Query-Zahl von ``_gather_attachments`` ist unabhaengig
        von der Zahl der Anhaenge (ein einziges SELECT mit Joins).
        Mutant: ``attachment.field_template`` laedt je Zeile nach — bei einem
        Anhang eine Query mehr, bei fuenf Anhaengen fuenf. Die beiden
        gemessenen Zahlen laufen damit auseinander (Mutant: 2 vs. 7).
        """
        field_template = FieldTemplate.objects.create(
            facility=facility,
            name="Nachweis",
            field_type=FieldTemplate.FieldType.FILE,
        )
        self._add_attachment(facility, client_identified, field_template, admin_user, 1)
        # Aufwaermen: erster Aufruf traegt einmalige Nebenkosten (Modell-Cache).
        _gather_attachments(client_identified)
        with_one = _count_queries(lambda: _gather_attachments(client_identified))

        for index in range(2, 7):
            self._add_attachment(facility, client_identified, field_template, admin_user, index)
        with_six = _count_queries(lambda: _gather_attachments(client_identified))

        assert len(_gather_attachments(client_identified)) == 6
        assert with_six == with_one


# ============================================================================
# _gather_legal_holds — Feldwahrheit + der ``dismissed_by``-Join (12 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestGatherLegalHolds:
    """Art.-18-Kategorie: warum Daten trotz abgelaufener Frist noch existieren.

    Der aufgehobene Hold ist der aussagekraeftigste Fall: er traegt als
    einziger ``dismissed_at``/``dismissed_by`` UND (hier) ein Ablaufdatum —
    also genau die Optionalfelder, deren Wert mutmut durch ``_isoformat(None)``
    bzw. ``_username(None)`` ersetzt. Ein Hold ohne diese Werte wuerde die
    Mutanten nicht unterscheiden.
    """

    @staticmethod
    def _hold(facility, client, creator, dismisser, *, reason: str, pk: int) -> LegalHold:
        return LegalHold.objects.create(
            id=_pk(pk),
            facility=facility,
            target_type="Client",
            target_id=client.pk,
            reason=reason,
            expires_at=date(2027, 3, 1),
            created_by=creator,
            dismissed_at=timezone.now(),
            dismissed_by=dismisser,
        )

    def test_dismissed_hold_entry_carries_every_key_with_its_own_value(
        self, facility, client_identified, admin_user, lead_user
    ):
        """Pin: ``x__gather_legal_holds__mutmut_30/31`` (Schluessel ``"expires_at"``
        -> ``"XXexpires_atXX"``/``"EXPIRES_AT"``),
        ``mutmut_32`` (``_isoformat(hold.expires_at)`` -> ``_isoformat(None)``),
        ``mutmut_33/34`` (``"created_at"`` -> ``"XXcreated_atXX"``/``"CREATED_AT"``),
        ``mutmut_38/39`` (``"dismissed_at"`` -> ``"XXdismissed_atXX"``/``"DISMISSED_AT"``),
        ``mutmut_40`` (``_isoformat(hold.dismissed_at)`` -> ``_isoformat(None)``),
        ``mutmut_41/42`` (``"dismissed_by"`` -> ``"XXdismissed_byXX"``/``"DISMISSED_BY"``),
        ``mutmut_43`` (``_username(hold.dismissed_by)`` -> ``_username(None)``).

        Original: der Eintrag ist exakt das erwartete Acht-Schluessel-Dict.
        Mutant (Schluessel): der erwartete Name fehlt, stattdessen steht der
        verhunzte da — der Dict-Vergleich schlaegt fehl.
        Mutant (``None``-Wert): ``expires_at``/``dismissed_at`` sind ``None``
        statt ``"2027-03-01"`` bzw. dem ISO-Zeitstempel, ``dismissed_by`` ist
        ``None`` statt ``"testlead"``.
        """
        hold = self._hold(
            facility,
            client_identified,
            admin_user,
            lead_user,
            reason="Aufsichtsverfahren, Az. 12/26",
            pk=1,
        )

        (entry,) = _gather_legal_holds(facility, client_identified, [])

        assert entry == {
            "target_type": "Client",
            "reason": "Aufsichtsverfahren, Az. 12/26",
            "is_active": False,
            "expires_at": "2027-03-01",
            "created_at": hold.created_at.isoformat(),
            "created_by": "testadmin",
            "dismissed_at": hold.dismissed_at.isoformat(),
            "dismissed_by": "testlead",
        }

    def test_dismissed_by_is_joined_not_reloaded_per_hold(self, facility, client_identified, admin_user, lead_user):
        """Pin: ``x__gather_legal_holds__mutmut_9`` — ``select_related("created_by", "dismissed_by")``
        -> ``select_related("created_by", )``.

        Aufgehobene Holds bleiben in der Auskunft (``is_active=False``), und
        ``_username(hold.dismissed_by)`` liest je Zeile den FK.

        Original: die Query-Zahl ist unabhaengig von der Zahl der Holds.
        Mutant: je aufgehobenem Hold eine Query mehr — die Messung mit einem
        Hold und die mit sechs laufen auseinander (Mutant: +1 vs. +6).
        """
        self._hold(facility, client_identified, admin_user, lead_user, reason="Hold 1", pk=11)
        # Aufwaermen: ``client_legal_hold_targets_q`` loest ContentTypes auf,
        # die danach im Prozess-Cache liegen — sonst zaehlt der erste Aufruf
        # Nebenkosten mit, die der zweite nicht mehr hat.
        _gather_legal_holds(facility, client_identified, [])
        with_one = _count_queries(lambda: _gather_legal_holds(facility, client_identified, []))

        for index in range(2, 7):
            self._hold(
                facility,
                client_identified,
                admin_user,
                lead_user,
                reason=f"Hold {index}",
                pk=10 + index,
            )
        with_six = _count_queries(lambda: _gather_legal_holds(facility, client_identified, []))

        assert len(_gather_legal_holds(facility, client_identified, [])) == 6
        assert with_six == with_one


# ============================================================================
# _gather_merged_sources — Herkunft nach Duplikat-Merge (2 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestGatherMergedSources:
    """ADR-045 §7: je Tombstone Pseudonym, Zeitpunkt und Tilgungsstatus.

    Sortiert wird nach ``deleted_at`` (= ``merged_at``, Soft-Delete und
    ``merged_into``-Verweis entstehen in derselben Transaktion) mit ``pk`` als
    Tiebreak. Chronologie ist hier die Aussage: „diese Akten wurden in dieser
    Reihenfolge zusammengefuehrt".
    """

    @staticmethod
    def _tombstone(facility, target, creator, *, pseudonym: str, pk: int, merged_at) -> Client:
        return Client.objects.create(
            id=_pk(pk),
            facility=facility,
            pseudonym=pseudonym,
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=creator,
            merged_into=target,
            is_deleted=True,
            deleted_at=merged_at,
            deleted_by=creator,
        )

    def test_sources_are_ordered_by_merge_time_not_by_pk(self, facility, client_identified, admin_user):
        """Pin: ``x__gather_merged_sources__mutmut_11`` — ``.order_by("deleted_at", "pk")`` -> ``.order_by("pk")``.

        Die PKs sind bewusst so vergeben, dass ihre Reihenfolge (63, 61, 62)
        der Merge-Chronologie widerspricht.

        Original: ``["Merge-Quelle-1", "Merge-Quelle-2", "Merge-Quelle-3"]``
        (aeltester Merge zuerst).
        Mutant: ``["Merge-Quelle-2", "Merge-Quelle-3", "Merge-Quelle-1"]`` —
        die PK-Reihenfolge, ohne jeden Bezug zum Zeitpunkt.
        """
        base = timezone.now() - timedelta(days=10)
        self._tombstone(facility, client_identified, admin_user, pseudonym="Merge-Quelle-1", pk=63, merged_at=base)
        self._tombstone(
            facility,
            client_identified,
            admin_user,
            pseudonym="Merge-Quelle-2",
            pk=61,
            merged_at=base + timedelta(days=1),
        )
        self._tombstone(
            facility,
            client_identified,
            admin_user,
            pseudonym="Merge-Quelle-3",
            pk=62,
            merged_at=base + timedelta(days=2),
        )

        entries = _gather_merged_sources(client_identified)

        assert [entry["pseudonym"] for entry in entries] == [
            "Merge-Quelle-1",
            "Merge-Quelle-2",
            "Merge-Quelle-3",
        ]

    def test_sources_merged_at_the_same_moment_keep_a_stable_pk_order(self, facility, client_identified, admin_user):
        """Pin: ``x__gather_merged_sources__mutmut_12`` — ``.order_by("deleted_at", "pk")``
        -> ``.order_by("deleted_at", )``.

        Zwei Quellen koennen denselben ``deleted_at`` tragen (ein Merge-Lauf,
        derselbe Transaktionszeitpunkt). Ohne Tiebreak ist die Ausgabe dann
        nicht mehr reproduzierbar — dieselbe Auskunft zweimal erzeugt liefert
        potenziell verschiedene Dokumente.

        Die PKs laufen der Einfuegereihenfolge entgegen (73, 72, 71).

        Original: ``["Gleichzeitig-C", "Gleichzeitig-B", "Gleichzeitig-A"]``
        (nach ``pk`` aufsteigend).
        Mutant: Postgres liefert die gleichrangige Gruppe in Heap-/Einfuege-
        reihenfolge, also ``["Gleichzeitig-A", "Gleichzeitig-B",
        "Gleichzeitig-C"]``.
        """
        merged_at = timezone.now() - timedelta(days=3)
        self._tombstone(facility, client_identified, admin_user, pseudonym="Gleichzeitig-A", pk=73, merged_at=merged_at)
        self._tombstone(facility, client_identified, admin_user, pseudonym="Gleichzeitig-B", pk=72, merged_at=merged_at)
        self._tombstone(facility, client_identified, admin_user, pseudonym="Gleichzeitig-C", pk=71, merged_at=merged_at)

        entries = _gather_merged_sources(client_identified)

        assert [entry["pseudonym"] for entry in entries] == [
            "Gleichzeitig-C",
            "Gleichzeitig-B",
            "Gleichzeitig-A",
        ]


# ============================================================================
# _gather_outcome_goals — Wirkungsziele + Meilensteine (24 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestGatherOutcomeGoals:
    """Ein Ziel ohne seine Schritte waere eine halbe Auskunft (Refs #1752)."""

    def test_goal_entry_carries_every_key_with_its_own_value(self, case_open, client_identified, admin_user):
        """Pin: ``x__gather_outcome_goals__mutmut_42/43`` (Schluessel ``"achieved_at"``),
        ``mutmut_44`` (``_isoformat(goal.achieved_at)`` -> ``_isoformat(None)``),
        ``mutmut_45/46`` (Schluessel ``"created_at"`` des Ziels),
        ``mutmut_47/48`` (Schluessel ``"created_by"``),
        ``mutmut_49`` (``_username(goal.created_by)`` -> ``_username(None)``),
        ``mutmut_56/57`` (Schluessel ``"completed_at"`` des Meilensteins),
        ``mutmut_58`` (``_isoformat(milestone.completed_at)`` -> ``_isoformat(None)``),
        ``mutmut_59/60`` (Schluessel ``"sort_order"``),
        ``mutmut_61/62`` (Schluessel ``"created_at"`` des Meilensteins).

        Ziel UND Meilenstein tragen hier gesetzte Optionalfelder
        (``achieved_at``, ``completed_at``) — nur so unterscheidet sich
        ``_isoformat(x)`` von ``_isoformat(None)``.

        Original: der Eintrag ist exakt das erwartete Dict.
        Mutant (Schluessel): der erwartete Name fehlt.
        Mutant (``None``-Wert): ``achieved_at``/``completed_at`` sind ``None``
        statt ``"2026-05-04"``/``"2026-04-02"``, ``created_by`` ist ``None``
        statt ``"testadmin"``.
        """
        goal = OutcomeGoal.objects.create(
            id=_pk(101),
            case=case_open,
            title="Stabile Wohnsituation",
            description="Eigener Mietvertrag bis Jahresende.",
            is_achieved=True,
            achieved_at=date(2026, 5, 4),
            created_by=admin_user,
        )
        milestone = Milestone.objects.create(
            id=_pk(102),
            goal=goal,
            title="Wohnungsangebot geprueft",
            is_completed=True,
            completed_at=date(2026, 4, 2),
            sort_order=7,
        )

        (entry,) = _gather_outcome_goals(client_identified)

        assert entry == {
            "case": "Offener Fall",
            "title": "Stabile Wohnsituation",
            "description": "Eigener Mietvertrag bis Jahresende.",
            "is_achieved": True,
            "achieved_at": "2026-05-04",
            "created_at": goal.created_at.isoformat(),
            "created_by": "testadmin",
            "milestones": [
                {
                    "title": "Wohnungsangebot geprueft",
                    "is_completed": True,
                    "completed_at": "2026-04-02",
                    "sort_order": 7,
                    "created_at": milestone.created_at.isoformat(),
                }
            ],
        }

    def test_goals_are_ordered_newest_first_not_by_pk(self, case_open, client_identified, admin_user):
        """Pin: ``x__gather_outcome_goals__mutmut_4`` — ``.order_by("-created_at", "pk")`` -> ``.order_by("pk")``.

        ``created_at`` ist ``auto_now_add``; die drei Zeitstempel werden
        deshalb per Queryset-``update`` gesetzt. Die PKs steigen mit der
        Einfuegereihenfolge, die Chronologie ebenfalls — „neueste zuerst" ist
        damit garantiert die Umkehrung der PK-Reihenfolge.

        Original: ``["Ziel-neu", "Ziel-mittel", "Ziel-alt"]``.
        Mutant: ``["Ziel-alt", "Ziel-mittel", "Ziel-neu"]``.
        """
        base = timezone.now() - timedelta(days=30)
        for index, (title, offset) in enumerate([("Ziel-alt", 0), ("Ziel-mittel", 1), ("Ziel-neu", 2)], start=1):
            goal = OutcomeGoal.objects.create(id=_pk(110 + index), case=case_open, title=title, created_by=admin_user)
            OutcomeGoal.objects.filter(pk=goal.pk).update(created_at=base + timedelta(days=offset))

        entries = _gather_outcome_goals(client_identified)

        assert [entry["title"] for entry in entries] == ["Ziel-neu", "Ziel-mittel", "Ziel-alt"]

    def test_goals_created_at_the_same_moment_keep_a_stable_pk_order(self, case_open, client_identified, admin_user):
        """Pin: ``x__gather_outcome_goals__mutmut_5`` — ``.order_by("-created_at", "pk")``
        -> ``.order_by("-created_at", )``.

        Ziele einer Sitzung entstehen im selben Moment; ``created_at`` hat
        Mikrosekunden-Aufloesung, ist aber nicht kollisionsfrei — hier per
        ``update`` bewusst gleichgesetzt. Ohne Tiebreak haengt die Auskunft
        vom Ausfuehrungsplan ab.

        Die PKs laufen der Einfuegereihenfolge entgegen (123, 122, 121).

        Original: ``["Gleichzeitig-C", "Gleichzeitig-B", "Gleichzeitig-A"]``.
        Mutant: die gleichrangige Gruppe in Heap-/Einfuegereihenfolge, also
        ``["Gleichzeitig-A", "Gleichzeitig-B", "Gleichzeitig-C"]``.
        """
        created_at = timezone.now() - timedelta(days=5)
        pks = []
        for title, pk in [("Gleichzeitig-A", 123), ("Gleichzeitig-B", 122), ("Gleichzeitig-C", 121)]:
            goal = OutcomeGoal.objects.create(id=_pk(pk), case=case_open, title=title, created_by=admin_user)
            pks.append(goal.pk)
        OutcomeGoal.objects.filter(pk__in=pks).update(created_at=created_at)

        entries = _gather_outcome_goals(client_identified)

        assert [entry["title"] for entry in entries] == [
            "Gleichzeitig-C",
            "Gleichzeitig-B",
            "Gleichzeitig-A",
        ]

    def test_milestones_are_ordered_by_sort_order_then_pk(self, case_open, client_identified, admin_user):
        """Pin: ``x__gather_outcome_goals__mutmut_24`` (``Milestone.objects.order_by("sort_order", "pk")``
        -> ``order_by("pk")``),
        ``mutmut_25`` (-> ``order_by("sort_order", )``),
        ``mutmut_17`` (``Prefetch("milestones", queryset=None)``),
        ``mutmut_19`` (``Prefetch("milestones", )`` — ohne ``queryset``).

        Die sechs Meilensteine sind so angelegt, dass **drei** Reihenfolgen
        auseinanderfallen: die fachliche (``sort_order``, dann ``pk``), die
        reine PK-Reihenfolge und die Einfuegereihenfolge. ``Milestone.Meta``
        sortiert nur nach ``sort_order`` — deshalb treffen ``queryset=None``
        und das ganz entfernte ``queryset``-Argument dieselbe Luecke wie der
        gestrichene ``pk``-Tiebreak.

        Original: ``["M-F", "M-E", "M-D", "M-C", "M-A", "M-B"]``.
        Mutant ``24``: PK-Reihenfolge ``["M-F", "M-A", "M-D", "M-E", "M-B", "M-C"]``.
        Mutanten ``17``/``19``/``25``: ``sort_order``-Gruppen in Einfuege-
        reihenfolge, also ``["M-E", "M-F", "M-C", "M-D", "M-B", "M-A"]``.
        """
        goal = OutcomeGoal.objects.create(id=_pk(130), case=case_open, title="Reihenfolge-Ziel", created_by=admin_user)
        # (Titel, sort_order, pk) — Einfuegereihenfolge != PK-Reihenfolge,
        # und die PK-Reihenfolge schneidet quer durch die sort_order-Gruppen.
        for title, sort_order, pk in [
            ("M-E", 0, 134),
            ("M-F", 0, 131),
            ("M-C", 1, 136),
            ("M-D", 1, 133),
            ("M-B", 2, 135),
            ("M-A", 2, 132),
        ]:
            Milestone.objects.create(id=_pk(pk), goal=goal, title=title, sort_order=sort_order)

        (entry,) = _gather_outcome_goals(client_identified)

        assert [milestone["title"] for milestone in entry["milestones"]] == [
            "M-F",
            "M-E",
            "M-D",
            "M-C",
            "M-A",
            "M-B",
        ]

    def test_case_creator_and_milestones_are_bundled_not_reloaded_per_goal(
        self, case_open, client_identified, admin_user
    ):
        """Pin: ``x__gather_outcome_goals__mutmut_9`` (``select_related("case", "created_by")``
        -> ``select_related("created_by")``),
        ``mutmut_10`` (-> ``select_related("case", )``),
        ``mutmut_6`` (``prefetch_related(Prefetch("milestones", …))`` -> ``prefetch_related(None)``).

        Alle drei Mutanten sind ergebnisgleich und kosten eine Query je Ziel:
        ``goal.case.title``, ``_username(goal.created_by)`` und
        ``goal.milestones.all()`` laufen dann einzeln nach.

        Original: die Query-Zahl ist unabhaengig von der Zahl der Ziele.
        Mutant: die Messung mit einem Ziel und die mit sechs laufen
        auseinander (je Mutant +1 vs. +6 Queries).
        """
        first = OutcomeGoal.objects.create(id=_pk(141), case=case_open, title="Skalierung-1", created_by=admin_user)
        Milestone.objects.create(id=_pk(151), goal=first, title="Schritt-1")
        _gather_outcome_goals(client_identified)
        with_one = _count_queries(lambda: _gather_outcome_goals(client_identified))

        for index in range(2, 7):
            goal = OutcomeGoal.objects.create(
                id=_pk(140 + index), case=case_open, title=f"Skalierung-{index}", created_by=admin_user
            )
            Milestone.objects.create(id=_pk(150 + index), goal=goal, title=f"Schritt-{index}")
        with_six = _count_queries(lambda: _gather_outcome_goals(client_identified))

        assert len(_gather_outcome_goals(client_identified)) == 6
        assert with_six == with_one


# ============================================================================
# _gather_recent_visits — Merklisten-Marker (2 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestGatherRecentVisits:
    """„Ihre Akte steht auf N Merklisten, zuletzt am …" — ohne Beschaeftigte.

    Der Eintrag traegt bewusst nur ``visited_at`` und ``is_favorite`` (kein
    ``user``, Produktentscheid 2026-08-03). Genau deshalb ist die
    **Reihenfolge** die einzige verbleibende Aussage ueber die Zuordnung der
    Zeilen — sie muss reproduzierbar sein.
    """

    @staticmethod
    def _visit(facility, client, user, *, pk: int, visited_at, is_favorite: bool) -> RecentClientVisit:
        return RecentClientVisit.objects.create(
            id=_pk(pk),
            facility=facility,
            client=client,
            user=user,
            visited_at=visited_at,
            is_favorite=is_favorite,
        )

    def test_visits_are_ordered_newest_first_not_by_pk(
        self, facility, client_identified, admin_user, lead_user, staff_user
    ):
        """Pin: ``x__gather_recent_visits__mutmut_4`` — ``.order_by("-visited_at", "pk")`` -> ``.order_by("pk")``.

        Die PKs steigen mit der Einfuegereihenfolge, die Besuchszeitpunkte
        ebenfalls — „zuletzt zuerst" ist damit die Umkehrung der
        PK-Reihenfolge.

        Original: ``[t+2, t+1, t]`` (neuester Besuch zuerst).
        Mutant: ``[t, t+1, t+2]``.
        """
        base = (timezone.now() - timedelta(days=9)).replace(microsecond=0)
        for index, user in enumerate([admin_user, lead_user, staff_user]):
            self._visit(
                facility,
                client_identified,
                user,
                pk=161 + index,
                visited_at=base + timedelta(days=index),
                is_favorite=False,
            )

        entries = _gather_recent_visits(client_identified)

        assert [entry["visited_at"] for entry in entries] == [
            (base + timedelta(days=2)).isoformat(),
            (base + timedelta(days=1)).isoformat(),
            base.isoformat(),
        ]

    def test_visits_at_the_same_moment_keep_a_stable_pk_order(
        self, facility, client_identified, admin_user, lead_user, staff_user, assistant_user
    ):
        """Pin: ``x__gather_recent_visits__mutmut_5`` — ``.order_by("-visited_at", "pk")``
        -> ``.order_by("-visited_at", )``.

        Vier Merklisten-Zeilen mit identischem ``visited_at`` (etwa nach einem
        Merge, der ``visited_at`` per Queryset-Update gleichsetzt). Die PKs
        laufen der Einfuegereihenfolge entgegen (174, 173, 172, 171), die
        ``is_favorite``-Marker unterscheiden die Zeilen im Ergebnis.

        Original: ``[False, False, True, True]`` (nach ``pk`` aufsteigend).
        Mutant: die gleichrangige Gruppe in Heap-/Einfuegereihenfolge, also
        ``[True, True, False, False]``.
        """
        visited_at = (timezone.now() - timedelta(days=2)).replace(microsecond=0)
        rows = [
            (admin_user, 174, True),
            (lead_user, 173, True),
            (staff_user, 172, False),
            (assistant_user, 171, False),
        ]
        for user, pk, is_favorite in rows:
            self._visit(
                facility,
                client_identified,
                user,
                pk=pk,
                visited_at=visited_at,
                is_favorite=is_favorite,
            )

        entries = _gather_recent_visits(client_identified)

        assert [entry["is_favorite"] for entry in entries] == [False, False, True, True]


# ============================================================================
# _gather_rectification_requests — Art.-16-Antraege (4 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestGatherRectificationRequests:
    """Refs #1796/ADR-041: der Berichtigungsantrag ist selbst Auskunftsstoff."""

    @staticmethod
    def _request(facility, client, requester, reviewer, *, field_path: str, pk: int) -> RectificationRequest:
        return RectificationRequest.objects.create(
            id=_pk(pk),
            facility=facility,
            target_client=client,
            field_path=field_path,
            status=RectificationRequest.Status.APPROVED,
            requested_by=requester,
            reviewed_by=reviewer,
            reviewed_at=timezone.now(),
        )

    def test_requests_are_ordered_newest_first_not_by_pk(self, facility, client_identified, admin_user, lead_user):
        """Pin: ``x__gather_rectification_requests__mutmut_5`` — ``.order_by("-created_at", "pk")``
        -> ``.order_by("pk")``.

        ``created_at`` ist ``auto_now_add`` und wird per Queryset-``update``
        gesetzt; PKs und Chronologie steigen beide mit der
        Einfuegereihenfolge.

        Original: ``["notes", "age_cluster", "pseudonym"]`` (neuester zuerst).
        Mutant: ``["pseudonym", "age_cluster", "notes"]``.
        """
        base = timezone.now() - timedelta(days=20)
        for index, field_path in enumerate(["pseudonym", "age_cluster", "notes"]):
            request = self._request(
                facility,
                client_identified,
                admin_user,
                lead_user,
                field_path=field_path,
                pk=181 + index,
            )
            RectificationRequest.objects.filter(pk=request.pk).update(created_at=base + timedelta(days=index))

        entries = _gather_rectification_requests(facility, client_identified)

        assert [entry["field_path"] for entry in entries] == ["notes", "age_cluster", "pseudonym"]

    def test_requests_created_at_the_same_moment_keep_a_stable_pk_order(
        self, facility, client_identified, admin_user, lead_user
    ):
        """Pin: ``x__gather_rectification_requests__mutmut_6`` — ``.order_by("-created_at", "pk")``
        -> ``.order_by("-created_at", )``.

        Drei Antraege mit gleichgesetztem ``created_at`` (ein Formular, mehrere
        Felder). Die PKs laufen der Einfuegereihenfolge entgegen (193, 192, 191).

        Original: ``["notes", "age_cluster", "pseudonym"]`` (nach ``pk``
        aufsteigend).
        Mutant: die gleichrangige Gruppe in Heap-/Einfuegereihenfolge, also
        ``["pseudonym", "age_cluster", "notes"]``.
        """
        created_at = timezone.now() - timedelta(days=4)
        pks = []
        for field_path, pk in [("pseudonym", 193), ("age_cluster", 192), ("notes", 191)]:
            request = self._request(facility, client_identified, admin_user, lead_user, field_path=field_path, pk=pk)
            pks.append(request.pk)
        RectificationRequest.objects.filter(pk__in=pks).update(created_at=created_at)

        entries = _gather_rectification_requests(facility, client_identified)

        assert [entry["field_path"] for entry in entries] == ["notes", "age_cluster", "pseudonym"]

    def test_requester_and_reviewer_are_joined_not_reloaded_per_request(
        self, facility, client_identified, admin_user, lead_user
    ):
        """Pin: ``x__gather_rectification_requests__mutmut_9`` (``select_related("requested_by", "reviewed_by")``
        -> ``select_related("reviewed_by")``),
        ``mutmut_10`` (-> ``select_related("requested_by", )``).

        Beide Antragsteller-Spalten sind hier gesetzt (Status GENEHMIGT), also
        traegt jeder der beiden gestrichenen Joins ein eigenes Nachladen:
        ``_username(rr.requested_by)`` bzw. ``_username(rr.reviewed_by)``.

        Original: die Query-Zahl ist unabhaengig von der Zahl der Antraege.
        Mutant: die Messung mit einem Antrag und die mit sechs laufen
        auseinander (je Mutant +1 vs. +6 Queries).

        ``client_identified`` traegt keinen ``document_type`` — der
        Feld-Vorlagen-Zweig bleibt damit aus und die Zahl konstant.
        """
        self._request(facility, client_identified, admin_user, lead_user, field_path="notes", pk=201)
        _gather_rectification_requests(facility, client_identified)
        with_one = _count_queries(lambda: _gather_rectification_requests(facility, client_identified))

        for index in range(2, 7):
            self._request(
                facility,
                client_identified,
                admin_user,
                lead_user,
                field_path=f"notes-{index}",
                pk=200 + index,
            )
        with_six = _count_queries(lambda: _gather_rectification_requests(facility, client_identified))

        assert len(_gather_rectification_requests(facility, client_identified)) == 6
        assert with_six == with_one


# ============================================================================
# _gather_retention_proposals — Loeschvorschlaege (11 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestGatherRetentionProposals:
    """Gegenstueck zum Legal Hold: anstehende Loeschung samt Frist (Refs #1752)."""

    @staticmethod
    def _proposal(facility, target_id, *, pk: int, due, category: str, **overrides) -> RetentionProposal:
        fields = {
            "id": _pk(pk),
            "facility": facility,
            "target_type": RetentionProposal.TargetType.EVENT,
            "target_id": target_id,
            "deletion_due_at": due,
            "retention_category": category,
        }
        fields.update(overrides)
        return RetentionProposal.objects.create(**fields)

    def test_deferred_proposal_entry_carries_every_key_with_its_own_value(
        self, facility, client_identified, sample_event
    ):
        """Pin: ``x__gather_retention_proposals__mutmut_16/17`` (Schluessel ``"target_type"``),
        ``mutmut_22/23`` (Schluessel ``"proposed_at"``),
        ``mutmut_26/27`` (Schluessel ``"deferred_until"``),
        ``mutmut_28`` (``_isoformat(proposal.deferred_until)`` -> ``_isoformat(None)``),
        ``mutmut_29/30`` (Schluessel ``"defer_count"``).

        Der **zurueckgestellte** Vorschlag ist der aussagekraeftigste Fall: nur
        er traegt ``deferred_until`` und ``defer_count > 0``.
        ``translation.override("de")`` nagelt ``get_status_display()`` fest —
        sonst haengt der Vergleich an der aktiven Sprache.

        Original: der Eintrag ist exakt das erwartete Acht-Schluessel-Dict.
        Mutant (Schluessel): der erwartete Name fehlt.
        Mutant ``28``: ``deferred_until`` ist ``None`` statt ``"2027-02-11"``.
        """
        proposal = self._proposal(
            facility,
            sample_event.pk,
            pk=211,
            due=date(2026, 12, 24),
            category="identified",
            status=RetentionProposal.Status.DEFERRED,
            deferred_until=date(2027, 2, 11),
            defer_count=3,
            details={"pseudonym": "Test-ID-01"},
        )

        with translation.override("de"):
            (entry,) = _gather_retention_proposals(facility, client_identified, [sample_event.pk])

        assert entry == {
            "target_type": "Event",
            "status": "Zurückgestellt",
            "retention_category": "identified",
            "proposed_at": proposal.proposed_at.isoformat(),
            "deletion_due_at": "2026-12-24",
            "deferred_until": "2027-02-11",
            "defer_count": 3,
            "details": {"pseudonym": "Test-ID-01"},
        }

    def test_proposals_are_ordered_by_due_date_not_by_pk(self, facility, client_identified, sample_event):
        """Pin: ``x__gather_retention_proposals__mutmut_4`` — ``.order_by("deletion_due_at", "pk")``
        -> ``.order_by("pk")``.

        Die Frist ist die Aussage der Kategorie („was faellt zuerst"). Die PKs
        (223, 221, 222) widersprechen der Fristenfolge bewusst.

        Original: ``["kat-1", "kat-2", "kat-3"]`` (fruehestes Datum zuerst).
        Mutant: ``["kat-2", "kat-3", "kat-1"]`` — die PK-Reihenfolge.

        Die Vorschlaege haengen an drei verschiedenen Ziel-IDs, weil
        ``unique_active_retention_proposal`` je Ziel nur einen aktiven
        Vorschlag zulaesst.
        """
        target_ids = [sample_event.pk, uuid.uuid4(), uuid.uuid4()]
        for (pk, offset, category), target_id in zip(
            [(223, 0, "kat-1"), (221, 1, "kat-2"), (222, 2, "kat-3")], target_ids, strict=True
        ):
            self._proposal(
                facility,
                target_id,
                pk=pk,
                due=date(2026, 11, 1) + timedelta(days=offset),
                category=category,
            )

        entries = _gather_retention_proposals(facility, client_identified, target_ids)

        assert [entry["retention_category"] for entry in entries] == ["kat-1", "kat-2", "kat-3"]

    def test_proposals_due_on_the_same_day_keep_a_stable_pk_order(self, facility, client_identified, sample_event):
        """Pin: ``x__gather_retention_proposals__mutmut_5`` — ``.order_by("deletion_due_at", "pk")``
        -> ``.order_by("deletion_due_at", )``.

        ``deletion_due_at`` ist ein ``DateField`` — Gleichstand ist der
        Regelfall, nicht die Ausnahme (ein Retention-Lauf setzt fuer eine
        ganze Kohorte dieselbe Frist). Ohne Tiebreak haengt die Auskunft vom
        Ausfuehrungsplan ab.

        Die PKs laufen der Einfuegereihenfolge entgegen (233, 232, 231).

        Original: ``["kat-C", "kat-B", "kat-A"]`` (nach ``pk`` aufsteigend).
        Mutant: die gleichrangige Gruppe in Heap-/Einfuegereihenfolge, also
        ``["kat-A", "kat-B", "kat-C"]``.
        """
        target_ids = [sample_event.pk, uuid.uuid4(), uuid.uuid4()]
        for (pk, category), target_id in zip([(233, "kat-A"), (232, "kat-B"), (231, "kat-C")], target_ids, strict=True):
            self._proposal(facility, target_id, pk=pk, due=date(2026, 10, 5), category=category)

        entries = _gather_retention_proposals(facility, client_identified, target_ids)

        assert [entry["retention_category"] for entry in entries] == ["kat-C", "kat-B", "kat-A"]


# ============================================================================
# _gather_workitems — Aufgaben der Person (4 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestGatherWorkItems:
    """Aufgaben tragen Freitext und Papierkorb-Metadaten (Refs #1752)."""

    @staticmethod
    def _workitem(facility, client, creator, assignee, deleter, *, title: str, pk: int) -> WorkItem:
        return WorkItem.objects.create(
            id=_pk(pk),
            facility=facility,
            client=client,
            title=title,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            created_by=creator,
            assigned_to=assignee,
            is_deleted=True,
            deleted_at=timezone.now(),
            deleted_by=deleter,
        )

    def test_workitems_created_at_the_same_moment_keep_a_stable_pk_order(
        self, facility, client_identified, admin_user, lead_user, staff_user
    ):
        """Pin: ``x__gather_workitems__mutmut_5`` — ``.order_by("-created_at", "pk")``
        -> ``.order_by("-created_at", )``.

        Aufgaben aus einer wiederkehrenden Serie entstehen im selben Moment;
        ``created_at`` ist ``auto_now_add`` und wird hier per Queryset-Update
        gleichgesetzt. Die PKs laufen der Einfuegereihenfolge entgegen
        (244, 243, 242, 241).

        Original: ``["Aufgabe-D", "Aufgabe-C", "Aufgabe-B", "Aufgabe-A"]``
        (nach ``pk`` aufsteigend).
        Mutant: die gleichrangige Gruppe in Heap-/Einfuegereihenfolge, also
        ``["Aufgabe-A", "Aufgabe-B", "Aufgabe-C", "Aufgabe-D"]``.
        """
        created_at = timezone.now() - timedelta(days=6)
        pks = []
        for title, pk in [
            ("Aufgabe-A", 244),
            ("Aufgabe-B", 243),
            ("Aufgabe-C", 242),
            ("Aufgabe-D", 241),
        ]:
            workitem = self._workitem(
                facility, client_identified, admin_user, lead_user, staff_user, title=title, pk=pk
            )
            pks.append(workitem.pk)
        WorkItem.objects.filter(pk__in=pks).update(created_at=created_at)

        entries = _gather_workitems(client_identified)

        assert [entry["title"] for entry in entries] == [
            "Aufgabe-D",
            "Aufgabe-C",
            "Aufgabe-B",
            "Aufgabe-A",
        ]

    def test_actor_columns_are_joined_not_reloaded_per_workitem(
        self, facility, client_identified, admin_user, lead_user, staff_user
    ):
        """Pin: ``x__gather_workitems__mutmut_9`` (``select_related("created_by", "assigned_to", "deleted_by")``
        -> ``select_related("assigned_to", "deleted_by")``),
        ``mutmut_10`` (-> ``select_related("created_by", "deleted_by")``),
        ``mutmut_11`` (-> ``select_related("created_by", "assigned_to", )``).

        Alle drei Nutzer-Spalten sind hier gesetzt (Ersteller, Zustaendige,
        Papierkorb-Loeschende), also traegt jeder gestrichene Join ein eigenes
        Nachladen ueber ``_username(...)`` bzw. ``_soft_delete_meta``.

        Zugleich Positiv-Kontrolle im selben Test: die drei Namen stehen
        wirklich im Ergebnis (der Join darf nicht durch einen leeren Wert
        „konstant" werden).

        Original: die Query-Zahl ist unabhaengig von der Zahl der Aufgaben.
        Mutant: die Messung mit einer Aufgabe und die mit sechs laufen
        auseinander (je Mutant +1 vs. +6 Queries).
        """
        self._workitem(facility, client_identified, admin_user, lead_user, staff_user, title="Skalierung-1", pk=251)
        _gather_workitems(client_identified)
        with_one = _count_queries(lambda: _gather_workitems(client_identified))

        for index in range(2, 7):
            self._workitem(
                facility,
                client_identified,
                admin_user,
                lead_user,
                staff_user,
                title=f"Skalierung-{index}",
                pk=250 + index,
            )
        with_six = _count_queries(lambda: _gather_workitems(client_identified))

        entries = _gather_workitems(client_identified)
        assert len(entries) == 6
        assert entries[0]["created_by"] == "testadmin"
        assert entries[0]["assigned_to"] == "testlead"
        assert entries[0]["deleted_by"] == "teststaff"
        assert with_six == with_one
