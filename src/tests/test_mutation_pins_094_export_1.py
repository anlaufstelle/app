"""Pin-Tests zum Meilenstein release-0.94 — Survivors in ``core.services.client.export``.

Quelle: Survivor-Triage des Moduls ``src/core/services/client/export.py``
(Mutantenbaum ``mutants/src/core/services/client/export.py``). Gepinnt wird der
Modul-Batch ``export_1`` aus der Survivor-Triage release-0.94 (Ledger des Meilensteins)
(60 triagierte, killbare Mutanten). Schwerpunkte:

* die **Reihenfolge jeder Auskunfts-Kategorie** — sowohl das primaere
  Sortierkriterium (``-occurred_at``/``-timestamp``/``-created_at``/
  ``-started_at``) als auch der ``pk``-Tiebreak. Beide waren bislang nie
  gepinnt: kein Bestandstest legte zwei Zeilen mit gleichem Zeitstempel an,
  und die betroffenen Modelle tragen **Zufalls-UUIDs** als PK — ohne
  Primaerkriterium ist die Auskunft schlicht unsortiert;
* die **N+1-Klammern** (``select_related``/``prefetch_related``): sie aendern
  den Rueckgabewert nicht und sind deshalb ausschliesslich ueber die
  tatsaechlich abgesetzten Queries pinnbar. Im Modul existierte genau EIN
  Query-Zaehl-Test (``core_attachment``); jede andere Join-Klammer war frei;
* der **FILE-Marker-Zweig der dynamischen Felder** — am aktuellen Typ UND bei
  den Waisen (``dynamic_data_orphaned``): Schluesselsatz des Eintrags und die
  Tatsache, dass die Schleife **weiterlaeuft** (``continue``, nicht ``break``).
  Ein ``break`` schnitte die gesamte Auskunft hinter dem ersten Datei-Feld ab;
* die **Aufloesung der Waisen-Werte**: Facility-Klammer des
  ``FieldTemplate``-Lookups (Slug-Kollision zwischen zwei Einrichtungen),
  Entschluesselung des Rohwerts und die typgerechte Anzeige-Formatierung;
* die **Block-Reihenfolge** der dynamischen Felder (``ordered=True``) — ohne
  sie zeigt die Auskunft die rohe ``sort_order``-Folge, in der Innen-Indizes
  gruppierter Felder mit globalen Blockpositionen kollidieren;
* die **Zielmenge des Zugriffsprotokolls**: die Antrags-PKs event-gezielter
  Loeschantraege und die beiden Alt-Schema-Zweige, die bewusst NUR auf
  ``DOWNLOAD`` verengt sind (sonst zieht die Auskunft fremde Audit-Zeilen);
* die **Schluessel** der Episoden- und Aktivitaets-Eintraege samt des
  ``ended_at``-Werts (eine abgeschlossene Episode galt sonst als offen).

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); Sprachkontext
wird bei Texten per ``translation.override("de")`` festgenagelt; KEINE Imports
aus ``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py`` bzw.
werden als echte Model-Objekte angelegt (keine Fakes). Jeder Test nennt seine
Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.

**Zur Determiniertheit der Tiebreak-Pins.** Ein entfernter ``"pk"``-Tiebreak
laesst die Reihenfolge bei Zeitstempel-Gleichstand offen. Die Tests hier
zwingen sie trotzdem auseinander: die Zeilen bekommen **explizite** UUID-PKs,
und zwar in der Einfuegereihenfolge ``B, A, C`` — einer Rotation der
PK-Reihenfolge ``A, B, C``. Gepinnt wird ausschliesslich die vom Original
garantierte Ordnung ``A, B, C``; welche Folge der Mutant liefert, ist kein
SQL-Vertrag und haengt vom Ausfuehrungsplan ab (im geprueften Lauf kam mit
Index-Scan ueber den Zeitstempel-Index die Einfuegefolge ``B, A, C``). Jede
von ``A, B, C`` abweichende Folge toetet den Mutanten; eine zufaellig
richtige Folge waere bei drei rotierten Zeilen der einzige Ueberlebensweg
und ist in den geprueften Plaenen nicht aufgetreten.
"""

import uuid
from datetime import date, timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.models.fields import timezone as fields_timezone
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation

from core.models import (
    Activity,
    AuditLog,
    Client,
    DeletionRequest,
    DocumentType,
    DocumentTypeField,
    Episode,
    Event,
    EventHistory,
    FieldGroup,
    FieldTemplate,
    LegalHold,
)
from core.models import Case as CaseModel
from core.services.client import (
    _gather_activities,
    _gather_audit_trail,
    _gather_cases,
    _gather_deletion_requests,
    _gather_entity_dynamic_data,
    _gather_entity_orphan_data,
    _gather_episodes,
    _gather_event_history,
    _gather_events,
    _gather_legal_holds,
)
from core.services.client.export import ATTACHMENT_FIELD_PLACEHOLDER

#: Drei feste PKs in aufsteigender Ordnung.
PK_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
PK_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
PK_C = uuid.UUID("33333333-3333-4333-8333-333333333333")
ASCENDING_PKS = (PK_A, PK_B, PK_C)
#: Einfuegereihenfolge der Tiebreak-Pins — eine Rotation der PK-Reihenfolge,
#: damit weder die physische Folge noch ihre Umkehrung ``A, B, C`` ergibt
#: (siehe Modul-Docstring).
TIEBREAK_INSERT_PKS = (PK_B, PK_A, PK_C)
#: Werte in derselben Einfuegereihenfolge; die Pins erwarten ``["A", "B", "C"]``.
TIEBREAK_INSERT_LABELS = ("B", "A", "C")


def _queries_on(captured, table):
    """SQL der Abfragen, die ``FROM "<table>"`` enthalten.

    Bewusst mit Anfuehrungszeichen und ``FROM``: ``core_documenttype`` waere
    sonst auch in ``core_documenttypefield`` enthalten, und ein Join auf eine
    Tabelle ist etwas anderes als eine eigene Nachlade-Abfrage auf sie.
    """
    needle = f'FROM "{table}"'
    return [q["sql"] for q in captured.captured_queries if needle in q["sql"]]


# ---------------------------------------------------------------------------
# _gather_activities
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherActivities:
    """Zeitstrom der Auskunft — Reihenfolge, Join-Klammern, Schluesselsatz."""

    def _activity(self, facility, client, user, *, occurred_at, pk, verb=Activity.Verb.CREATED):
        return Activity.objects.create(
            id=pk,
            facility=facility,
            actor=user,
            verb=verb,
            target_type=ContentType.objects.get_for_model(Client),
            target_id=client.pk,
            context={"action": Activity.Action.CLIENT_CREATED.value},
            occurred_at=occurred_at,
        )

    def test_activities_are_ordered_newest_first_and_carry_their_timestamp(
        self, facility, client_identified, staff_user
    ):
        """Pin: ``x__gather_activities__mutmut_9`` — ``.order_by("-occurred_at", "pk")``
        -> ``.order_by("pk")``; ``__mutmut_36``/``__mutmut_37`` — Dict-Key
        ``"occurred_at"`` -> ``"XXoccurred_atXX"``/``"OCCURRED_AT"``.

        Die drei Aktivitaeten werden **PK-aufsteigend** in Zeit-aufsteigender
        Folge angelegt. Original: neueste zuerst, also
        ``[t3, t2, t1]``. Mutant 9 sortiert nach ``pk`` und liefert exakt die
        Gegenrichtung ``[t1, t2, t3]``. Mutant 36/37 laesst den Key
        ``occurred_at`` verschwinden — der Zugriff bricht mit ``KeyError`` ab.
        """
        now = timezone.now()
        stamps = [now - timedelta(days=2), now - timedelta(days=1), now]
        for pk, occurred_at in zip(ASCENDING_PKS, stamps, strict=True):
            self._activity(facility, client_identified, staff_user, occurred_at=occurred_at, pk=pk)

        with translation.override("de"):
            rows = _gather_activities(facility, client_identified, [])

        assert [row["occurred_at"] for row in rows] == [
            stamps[2].isoformat(),
            stamps[1].isoformat(),
            stamps[0].isoformat(),
        ]

    def test_activities_fall_back_to_pk_order_on_timestamp_ties(self, facility, client_identified, staff_user):
        """Pin: ``x__gather_activities__mutmut_10`` — ``.order_by("-occurred_at", "pk")``
        -> ``.order_by("-occurred_at")`` (PK-Tiebreak entfernt).

        Drei Aktivitaeten mit **identischem** ``occurred_at``, eingefuegt in der
        Rotation ``B, A, C`` der PK-Reihenfolge. Original: PK-aufsteigend, also
        ``["erstellt", "aktualisiert", "qualifiziert"]``. Mutant: physische
        Reihenfolge (``["aktualisiert", "erstellt", "qualifiziert"]``) bzw.
        deren Umkehrung — beides ist von der gepinnten Folge verschieden.
        """
        occurred_at = timezone.now()
        verbs = (Activity.Verb.UPDATED, Activity.Verb.CREATED, Activity.Verb.QUALIFIED)
        for pk, verb in zip(TIEBREAK_INSERT_PKS, verbs, strict=True):
            self._activity(facility, client_identified, staff_user, occurred_at=occurred_at, pk=pk, verb=verb)

        with translation.override("de"):
            rows = _gather_activities(facility, client_identified, [])

        assert [row["verb"] for row in rows] == ["erstellt", "aktualisiert", "qualifiziert"]

    def test_activities_are_gathered_without_n_plus_one(self, facility, client_identified, staff_user):
        """Pin: ``x__gather_activities__mutmut_11`` (``prefetch_related(None)``),
        ``__mutmut_14`` (``select_related`` ohne ``"actor"``) und ``__mutmut_15``
        (``select_related`` ohne ``"target_type"``).

        Alle drei aendern den Rueckgabewert nicht — nur die Query-Zahl. Bei
        vier Aktivitaeten gilt im Original: **eine** gebuendelte Abfrage auf
        ``core_client`` (GenericPrefetch), **keine** auf ``core_user``
        (``_username(activity.actor)`` kommt aus dem Join) und **keine** auf
        ``django_content_type`` (``activity.target_type.name`` ebenso).
        Mutant 11 laedt das GenericFK-Ziel je Zeile nach (vier
        ``core_client``-Abfragen), Mutant 14 den Actor je Zeile (vier
        ``core_user``-Abfragen), Mutant 15 den ContentType je Zeile.
        """
        now = timezone.now()
        for index in range(4):
            self._activity(
                facility,
                client_identified,
                staff_user,
                occurred_at=now - timedelta(minutes=index),
                pk=uuid.uuid4(),
            )

        # Aufwaermen: ContentType-Cache und Verbindungs-Setup zaehlen sonst mit.
        with translation.override("de"):
            _gather_activities(facility, client_identified, [])

        with translation.override("de"), CaptureQueriesContext(connection) as captured:
            rows = _gather_activities(facility, client_identified, [])

        assert len(rows) == 4
        assert len(_queries_on(captured, "core_client")) == 1
        assert _queries_on(captured, "core_user") == []
        assert _queries_on(captured, "django_content_type") == []


# ---------------------------------------------------------------------------
# _gather_audit_trail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherAuditTrail:
    """Zugriffsprotokoll — Zielmenge und Reihenfolge."""

    def _row(
        self,
        facility,
        user,
        *,
        target_id,
        timestamp,
        pk,
        action=AuditLog.Action.EXPORT,
        target_type="Client",
        detail=None,
    ):
        return AuditLog.objects.create(
            id=pk,
            facility=facility,
            user=user,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            detail=detail if detail is not None else {},
            timestamp=timestamp,
        )

    def test_audit_trail_is_ordered_newest_first(self, facility, client_identified, staff_user):
        """Pin: ``x__gather_audit_trail__mutmut_91`` — ``.order_by("-timestamp", "pk")``
        -> ``.order_by("pk")``.

        Drei Zeilen zur selben Person, PK-aufsteigend in Zeit-aufsteigender
        Folge. Original: ``[t3, t2, t1]``. Mutant: ``[t1, t2, t3]``.
        """
        now = timezone.now()
        stamps = [now - timedelta(hours=2), now - timedelta(hours=1), now]
        written = [
            self._row(facility, staff_user, target_id=client_identified.pk, timestamp=timestamp, pk=pk)
            for pk, timestamp in zip(ASCENDING_PKS, stamps, strict=True)
        ]

        with translation.override("de"):
            rows = _gather_audit_trail(facility, client_identified, [])

        assert [row["timestamp"] for row in rows] == [
            written[2].timestamp.isoformat(),
            written[1].timestamp.isoformat(),
            written[0].timestamp.isoformat(),
        ]

    def test_audit_trail_falls_back_to_pk_order_on_timestamp_ties(self, facility, client_identified, staff_user):
        """Pin: ``x__gather_audit_trail__mutmut_92`` — ``.order_by("-timestamp", "pk")``
        -> ``.order_by("-timestamp")`` (PK-Tiebreak entfernt).

        Die Annahme, dieser Mutant sei aequivalent, war falsch: ``AuditLog.save()``
        (``assign_chain_fields``, ``core/services/audit/chain.py``) hebt zwar bei
        einem Gleichstand ueber ``save()`` den Zeitstempel um eine Mikrosekunde an,
        aber ``bulk_create`` ruft ``save()`` gar nicht erst auf — genau das Muster,
        das ``test_audit_chain.py`` bereits fuer einen gefaelschten Checkpoint
        nutzt, um das Ketten-Sealing zu umgehen. Der DB-Trigger aus
        ``0024_auditlog_immutable_trigger`` blockt nur ``UPDATE``/``DELETE``,
        kein ``INSERT`` — ein Gleichstand ist ueber einen Raw-INSERT also
        weiterhin erzeugbar.

        Drei Zeilen mit **identischem** ``timestamp``, eingefuegt in der Rotation
        ``B, A, C`` (``PK_B``->EXPORT, ``PK_A``->CLIENT_UPDATE, ``PK_C``->DOWNLOAD).
        Original: PK-aufsteigend, also ``["Person aktualisiert", "Export",
        "Download"]``. Mutant: die physische Insert-Folge ``["Export", "Person
        aktualisiert", "Download"]`` (nachgemessen).
        """
        now = timezone.now()
        action_by_pk = {
            PK_A: AuditLog.Action.CLIENT_UPDATE,
            PK_B: AuditLog.Action.EXPORT,
            PK_C: AuditLog.Action.DOWNLOAD,
        }
        rows = [
            AuditLog(
                id=pk,
                facility=facility,
                user=staff_user,
                action=action_by_pk[pk],
                target_type="Client",
                target_id=str(client_identified.pk),
                detail={},
                timestamp=now,
                # The database only accepts rows with a chain hash (#2449).
                entry_hash="0" * 64,
                prev_hash="",
            )
            for pk in TIEBREAK_INSERT_PKS
        ]
        AuditLog.objects.bulk_create(rows)

        with translation.override("de"):
            gathered = _gather_audit_trail(facility, client_identified, [])

        assert [row["action"] for row in gathered] == ["Person aktualisiert", "Export", "Download"]

    def test_audit_rows_of_event_deletion_requests_are_included(
        self, facility, client_identified, sample_event, staff_user
    ):
        """Pin: ``x__gather_audit_trail__mutmut_40`` —
        ``client_and_case_targets_q(client, event_ids)`` ->
        ``client_and_case_targets_q(client, None)``.

        Ein Loeschantrag mit ``target_type="Event"`` steuert seine ANTRAGS-PK
        zur Zielmenge des Protokolls bei; die zugehoerige
        ``DELETION_REQUESTED``-Zeile traegt genau diese PK als ``target_id``.
        Mit ``None`` faellt der Ereignis-Zweig aus dem Ziel-Builder, der Antrag
        wird nicht mehr gefunden — die Auditzeile verschwindet. Original:
        ``[timestamp]``. Mutant: ``[]``.
        """
        request = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="Betroffene widerspricht der Speicherung.",
            requested_by=staff_user,
        )
        written = self._row(
            facility,
            staff_user,
            target_id=request.pk,
            timestamp=timezone.now(),
            pk=PK_A,
            action=AuditLog.Action.DELETION_REQUESTED,
            target_type="DeletionRequest",
        )

        with translation.override("de"):
            rows = _gather_audit_trail(facility, client_identified, [sample_event.pk])

        assert [row["timestamp"] for row in rows] == [written.timestamp.isoformat()]

    def test_legacy_detail_branches_only_match_downloads(self, facility, client_identified, sample_event, staff_user):
        """Pin: ``x__gather_audit_trail__mutmut_80`` (``action``-Filter im
        ``detail__event_id``-Zweig entfernt), ``__mutmut_81`` (``detail``-Filter
        in demselben Zweig entfernt) und ``__mutmut_86`` (``action``-Filter im
        ``detail__owner_id``-Zweig entfernt).

        Vier Anhang-Zeilen mit bewusst FREMDER ``target_id`` (weder Personen-,
        Fall-, Ereignis- noch Antrags-PK) — nur die erste ist ein echter
        ``DOWNLOAD`` mit passendem ``detail.event_id``:

        * ``t_ok``   DOWNLOAD + ``detail.event_id`` -> gehoert dazu;
        * ``t_80``   EXPORT   + ``detail.event_id`` -> nur Mutant 80 zieht sie;
        * ``t_81``   DOWNLOAD ohne passendes ``detail`` -> nur Mutant 81;
        * ``t_86``   EXPORT   + ``detail.owner_id``  -> nur Mutant 86.

        Original: ``[t_ok]``. Jeder der drei Mutanten liefert zwei Eintraege.
        """
        now = timezone.now()
        t_ok, t_80, t_81, t_86 = (now - timedelta(minutes=i) for i in range(4))
        foreign_target = uuid.uuid4()
        written_ok = self._row(
            facility,
            staff_user,
            target_id=foreign_target,
            timestamp=t_ok,
            pk=uuid.uuid4(),
            action=AuditLog.Action.DOWNLOAD,
            target_type="Attachment",
            detail={"event_id": str(sample_event.pk), "field": "datei"},
        )
        self._row(
            facility,
            staff_user,
            target_id=foreign_target,
            timestamp=t_80,
            pk=uuid.uuid4(),
            action=AuditLog.Action.EXPORT,
            target_type="Attachment",
            detail={"event_id": str(sample_event.pk), "field": "datei"},
        )
        self._row(
            facility,
            staff_user,
            target_id=foreign_target,
            timestamp=t_81,
            pk=uuid.uuid4(),
            action=AuditLog.Action.DOWNLOAD,
            target_type="Attachment",
            detail={"field": "datei"},
        )
        self._row(
            facility,
            staff_user,
            target_id=foreign_target,
            timestamp=t_86,
            pk=uuid.uuid4(),
            action=AuditLog.Action.EXPORT,
            target_type="Attachment",
            detail={"owner_id": str(client_identified.pk), "field": "datei"},
        )

        with translation.override("de"):
            rows = _gather_audit_trail(facility, client_identified, [sample_event.pk])

        assert [row["timestamp"] for row in rows] == [written_ok.timestamp.isoformat()]


# ---------------------------------------------------------------------------
# _gather_cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherCases:
    """Faelle — Tiebreak der Reihenfolge und die vier Join-Klammern."""

    def _case(self, facility, client, user, *, title, pk, document_type=None):
        return CaseModel.objects.create(
            id=pk,
            facility=facility,
            client=client,
            title=title,
            status=CaseModel.Status.OPEN,
            created_by=user,
            lead_user=user,
            deleted_by=user,
            document_type=document_type,
        )

    def test_cases_fall_back_to_pk_order_on_created_at_ties(self, facility, client_identified, staff_user):
        """Pin: ``x__gather_cases__mutmut_6`` — ``.order_by("-created_at", "pk")``
        -> ``.order_by("-created_at")`` (PK-Tiebreak entfernt).

        Drei Faelle mit **identischem** ``created_at``, eingefuegt in der Rotation
        ``B, A, C``. Original: PK-aufsteigend, also ``["A", "B", "C"]``.
        Mutant: die physische Folge ``["B", "A", "C"]`` (nachgemessen) bzw. bei
        einem Index-Plan deren Umkehrung — beides verschieden.
        """
        for pk, title in zip(TIEBREAK_INSERT_PKS, TIEBREAK_INSERT_LABELS, strict=True):
            self._case(facility, client_identified, staff_user, title=title, pk=pk)
        CaseModel.objects.filter(pk__in=ASCENDING_PKS).update(created_at=timezone.now())

        with translation.override("de"):
            rows = _gather_cases(client_identified, {})

        assert [row["title"] for row in rows] == ["A", "B", "C"]

    def test_cases_are_gathered_without_n_plus_one(self, facility, client_identified, staff_user, doc_type_case_form):
        """Pin: ``x__gather_cases__mutmut_11``/``_12``/``_13``/``_14`` — je ein
        Glied aus ``.select_related("created_by", "lead_user", "document_type",
        "deleted_by")`` entfernt.

        Drei Faelle, bei denen **alle vier** Beziehungen belegt sind. Original:
        keine einzige Nachlade-Abfrage auf ``core_user`` (``created_by``,
        ``lead_user``, ``deleted_by``) und keine auf ``core_documenttype``
        (``case.document_type.name``). Jeder der vier Mutanten erzeugt drei
        zusaetzliche Abfragen auf genau einer dieser beiden Tabellen.
        """
        for index in range(3):
            self._case(
                facility,
                client_identified,
                staff_user,
                title=f"Fall {index}",
                pk=uuid.uuid4(),
                document_type=doc_type_case_form,
            )

        with translation.override("de"):
            _gather_cases(client_identified, {})

        with translation.override("de"), CaptureQueriesContext(connection) as captured:
            rows = _gather_cases(client_identified, {})

        assert len(rows) == 3
        assert _queries_on(captured, "core_user") == []
        assert _queries_on(captured, "core_documenttype") == []


# ---------------------------------------------------------------------------
# _gather_deletion_requests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherDeletionRequests:
    """Loeschantraege — Reihenfolge und die beiden Nutzer-Joins."""

    def _request(self, facility, *, target_type, target_id, reason, pk, requested_by, reviewed_by=None):
        return DeletionRequest.objects.create(
            id=pk,
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            requested_by=requested_by,
            reviewed_by=reviewed_by,
        )

    def _three_targets(self, client, case, event):
        return (
            (DeletionRequest.TargetType.CLIENT, client.pk),
            (DeletionRequest.TargetType.CASE, case.pk),
            (DeletionRequest.TargetType.EVENT, event.pk),
        )

    def test_deletion_requests_are_ordered_newest_first(
        self, facility, client_identified, case_open, sample_event, staff_user
    ):
        """Pin: ``x__gather_deletion_requests__mutmut_4`` —
        ``.order_by("-created_at", "pk")`` -> ``.order_by("pk")``.

        Drei Antraege, PK-aufsteigend in Zeit-aufsteigender Folge. Original:
        juengster zuerst (``["dritter", "zweiter", "erster"]``). Mutant:
        PK-Reihenfolge, also die Gegenrichtung.
        """
        reasons = ("erster", "zweiter", "dritter")
        targets = self._three_targets(client_identified, case_open, sample_event)
        for pk, reason, (target_type, target_id) in zip(ASCENDING_PKS, reasons, targets, strict=True):
            self._request(
                facility,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
                pk=pk,
                requested_by=staff_user,
            )
        now = timezone.now()
        for offset, pk in enumerate(ASCENDING_PKS):
            DeletionRequest.objects.filter(pk=pk).update(created_at=now - timedelta(hours=2 - offset))

        with translation.override("de"):
            rows = _gather_deletion_requests(facility, client_identified, [sample_event.pk])

        assert [row["reason"] for row in rows] == ["dritter", "zweiter", "erster"]

    def test_deletion_requests_fall_back_to_pk_order_on_created_at_ties(
        self, facility, client_identified, case_open, sample_event, staff_user
    ):
        """Pin: ``x__gather_deletion_requests__mutmut_5`` — PK-Tiebreak entfernt.

        Drei Antraege mit **identischem** ``created_at``, eingefuegt in der
        Rotation ``B, A, C``. Original: PK-aufsteigend (``["A", "B", "C"]``).
        Mutant: die physische Folge bzw. deren Umkehrung — beides verschieden.
        """
        targets = self._three_targets(client_identified, case_open, sample_event)
        for pk, reason, (target_type, target_id) in zip(
            TIEBREAK_INSERT_PKS, TIEBREAK_INSERT_LABELS, targets, strict=True
        ):
            self._request(
                facility,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
                pk=pk,
                requested_by=staff_user,
            )
        DeletionRequest.objects.filter(pk__in=ASCENDING_PKS).update(created_at=timezone.now())

        with translation.override("de"):
            rows = _gather_deletion_requests(facility, client_identified, [sample_event.pk])

        assert [row["reason"] for row in rows] == ["A", "B", "C"]

    def test_deletion_requests_are_gathered_without_n_plus_one(
        self, facility, client_identified, case_open, sample_event, staff_user, lead_user
    ):
        """Pin: ``x__gather_deletion_requests__mutmut_8``/``_9`` — je ein Glied
        aus ``.select_related("requested_by", "reviewed_by")`` entfernt.

        Drei Antraege mit **beiden** belegten Nutzer-FKs. Original: keine
        Nachlade-Abfrage auf ``core_user``. Mutant 8 laedt ``requested_by`` je
        Zeile nach, Mutant 9 ``reviewed_by`` — je drei zusaetzliche Abfragen.
        """
        targets = self._three_targets(client_identified, case_open, sample_event)
        for target_type, target_id in targets:
            self._request(
                facility,
                target_type=target_type,
                target_id=target_id,
                reason="Pruefung abgeschlossen",
                pk=uuid.uuid4(),
                requested_by=staff_user,
                reviewed_by=lead_user,
            )

        with translation.override("de"):
            _gather_deletion_requests(facility, client_identified, [sample_event.pk])

        with translation.override("de"), CaptureQueriesContext(connection) as captured:
            rows = _gather_deletion_requests(facility, client_identified, [sample_event.pk])

        assert len(rows) == 3
        assert _queries_on(captured, "core_user") == []


# ---------------------------------------------------------------------------
# _gather_entity_dynamic_data
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEntityDynamicData:
    """Dynamische Felder am aktuellen Typ — FILE-Zweig und Block-Reihenfolge."""

    def test_file_field_entry_is_complete_and_does_not_end_the_loop(self, facility, client_identified, staff_user):
        """Pin: ``x__gather_entity_dynamic_data__mutmut_18``/``_19`` (Key
        ``"label"`` -> ``"XXlabelXX"``/``"LABEL"``) und ``__mutmut_22``
        (``continue`` -> ``break`` nach dem FILE-Marker-Eintrag).

        Ein Personen-Dokumentationstyp mit einem **Datei-Feld vor** einem
        Textfeld. Original: beide Eintraege, der Datei-Eintrag mit vollem
        Schluesselsatz ``{"slug", "label", "value"}``. Mutant 18/19 liefert
        statt ``label`` einen verstuemmelten Key (die PDF-Vorlage liest
        ``entry.label`` und rendert still leer); Mutant 22 bricht die Schleife
        nach dem Datei-Feld ab — der Text-Eintrag fehlt komplett.
        """
        doc_type = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.CLIENT,
            name="Profil mit Datei",
        )
        ft_file = FieldTemplate.objects.create(
            facility=facility,
            name="Ausweiskopie",
            field_type=FieldTemplate.FieldType.FILE,
        )
        ft_text = FieldTemplate.objects.create(
            facility=facility,
            name="Anmerkung",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_file, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_text, sort_order=1)

        client = Client.objects.create(
            facility=facility,
            pseudonym="dyn-file-01",
            created_by=staff_user,
            document_type=doc_type,
            data_json={
                ft_file.slug: {
                    "__files__": True,
                    "entries": [{"id": "44444444-4444-4444-4444-444444444444", "sort": 0}],
                },
                ft_text.slug: "Notiz zur Person",
            },
        )

        with translation.override("de"):
            entries = _gather_entity_dynamic_data(client)

        assert entries == [
            {"slug": ft_file.slug, "label": "Ausweiskopie", "value": ATTACHMENT_FIELD_PLACEHOLDER},
            {"slug": ft_text.slug, "label": "Anmerkung", "value": "Notiz zur Person"},
        ]

    def test_dynamic_fields_follow_the_global_block_order(self, facility, client_identified, staff_user):
        """Pin: ``x__gather_entity_dynamic_data__mutmut_6`` (``ordered=None``),
        ``__mutmut_8`` (``ordered``-Kwarg entfernt) und ``__mutmut_9``
        (``ordered=False``) — alle drei sind falsy und schalten damit von der
        Block-Reihenfolge auf die rohe ``sort_order``-Folge um.

        Aufbau mit genau der Kollision, gegen die ``block_ordered_dtf_queryset``
        gebaut ist: eine Gruppe auf Blockposition 0 mit zwei Innen-Indizes
        10/11 und ein freies Feld auf Blockposition 1. Original (Block):
        ``[in-gruppe-a, in-gruppe-b, freies-feld]``. Mutant (roh nach
        ``sort_order`` 1/10/11): ``[freies-feld, in-gruppe-a, in-gruppe-b]``.
        """
        doc_type = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.CLIENT,
            name="Profil mit Gruppe",
        )
        group = FieldGroup.objects.create(document_type=doc_type, name="Erstkontakt", sort_order=0)
        ft_a = FieldTemplate.objects.create(facility=facility, name="Gruppenfeld A")
        ft_b = FieldTemplate.objects.create(facility=facility, name="Gruppenfeld B")
        ft_free = FieldTemplate.objects.create(facility=facility, name="Freies Feld")
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_a, field_group=group, sort_order=10)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_b, field_group=group, sort_order=11)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_free, sort_order=1)

        client = Client.objects.create(
            facility=facility,
            pseudonym="dyn-block-01",
            created_by=staff_user,
            document_type=doc_type,
            data_json={ft_a.slug: "A", ft_b.slug: "B", ft_free.slug: "F"},
        )

        with translation.override("de"):
            entries = _gather_entity_dynamic_data(client)

        assert [entry["slug"] for entry in entries] == [ft_a.slug, ft_b.slug, ft_free.slug]


# ---------------------------------------------------------------------------
# _gather_entity_orphan_data
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEntityOrphanData:
    """Verwaiste dynamische Felder — FILE-Zweig, Facility-Klammer, Werte."""

    def _client_with(self, facility, staff_user, doc_type, data_json, pseudonym):
        return Client.objects.create(
            facility=facility,
            pseudonym=pseudonym,
            created_by=staff_user,
            document_type=doc_type,
            data_json=data_json,
        )

    def test_orphan_file_marker_entry_is_complete_and_does_not_end_the_loop(
        self, facility, doc_type_client_profile, staff_user
    ):
        """Pin: ``x__gather_entity_orphan_data__mutmut_23`` (``is_file_marker(None)``
        -> Zweig unerreichbar), ``__mutmut_24`` (``entries.append(None)``),
        ``__mutmut_25``–``__mutmut_30`` (Keys ``"slug"``/``"label"``/``"value"``
        -> ``XX…XX``/GROSS) und ``__mutmut_31`` (``continue`` -> ``break``).

        Zwei Waisen, alphabetisch sortiert durchlaufen: zuerst ein
        FILE-Marker-Wert, danach ein normaler Textwert. Original: beide
        Eintraege, der Datei-Eintrag exakt
        ``{"slug", "label", "value"=Platzhalter}``. Mutant 23 faellt in den
        Regelzweig und liefert statt des Platzhalters das durchgereichte
        Marker-Dict, Mutant 24 ein ``None``-Element, 25–30 verstuemmelte Keys,
        Mutant 31 schneidet den zweiten Eintrag ab.
        """
        ft_file = FieldTemplate.objects.create(
            facility=facility,
            name="Alt-Datei",
            slug="aaa-datei",
            field_type=FieldTemplate.FieldType.FILE,
        )
        ft_text = FieldTemplate.objects.create(
            facility=facility,
            name="Alt-Text",
            slug="bbb-text",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        client = self._client_with(
            facility,
            staff_user,
            doc_type_client_profile,
            {
                "wohnsituation": "WG",
                ft_file.slug: {
                    "__files__": True,
                    "entries": [{"id": "55555555-5555-5555-5555-555555555555", "sort": 0}],
                },
                ft_text.slug: "Altwert",
            },
            "orphan-file-01",
        )

        with translation.override("de"):
            entries = _gather_entity_orphan_data(client)

        assert entries == [
            {"slug": "aaa-datei", "label": "Alt-Datei", "value": ATTACHMENT_FIELD_PLACEHOLDER},
            {"slug": "bbb-text", "label": "Alt-Text", "value": "Altwert"},
        ]

    def test_orphan_label_comes_from_the_own_facility(
        self, facility, second_facility, doc_type_client_profile, staff_user
    ):
        """Pin: ``x__gather_entity_orphan_data__mutmut_15`` — ``facility_id=...``
        aus dem ``FieldTemplate``-Filter entfernt.

        ``FieldTemplate`` ist nur je (Einrichtung, Slug) eindeutig: derselbe
        Slug darf in zwei Einrichtungen existieren. Ohne die Facility-Klammer
        matchen beide Zeilen; ``FieldTemplate`` sortiert nach ``name``, im
        Dict-Aufbau gewinnt also der **letzte** — hier bewusst die FREMDE
        Vorlage ``"Zzz-Fremdlabel"``. Original: Label ``"Alt-Feld"`` aus der
        eigenen Einrichtung. Mutant: ``"Zzz-Fremdlabel"``.
        """
        FieldTemplate.objects.create(
            facility=facility,
            name="Alt-Feld",
            slug="geteilter-slug",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        FieldTemplate.objects.create(
            facility=second_facility,
            name="Zzz-Fremdlabel",
            slug="geteilter-slug",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        client = self._client_with(
            facility,
            staff_user,
            doc_type_client_profile,
            {"wohnsituation": "WG", "geteilter-slug": "Altwert"},
            "orphan-facility-01",
        )

        with translation.override("de"):
            entries = _gather_entity_orphan_data(client)

        assert entries == [{"slug": "geteilter-slug", "label": "Alt-Feld", "value": "Altwert"}]

    def test_orphan_encrypted_value_is_decrypted(self, facility, doc_type_client_profile, staff_user, _encryption_key):
        """Pin: ``x__gather_entity_orphan_data__mutmut_33`` — ``safe_decrypt(raw)``
        -> ``safe_decrypt(None)``.

        Der einzige Bestandstest fuer diesen Zweig benutzt einen KLARTEXT-Wert;
        dann laeuft ``safe_decrypt`` gar nicht erst an. Hier liegt ein echtes
        Fernet-Dict in ``data_json``. Original: ``"Geheime Altangabe"``.
        Mutant: ``None`` (``is_encrypted_value(None)`` ist falsch, der
        Early-Return gibt ``None`` zurueck).
        """
        from core.services.file_vault import encrypt_field

        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Alt-Geheim",
            slug="alt-geheim",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        client = self._client_with(
            facility,
            staff_user,
            doc_type_client_profile,
            {"wohnsituation": "WG", ft.slug: encrypt_field("Geheime Altangabe")},
            "orphan-crypt-01",
        )

        with translation.override("de"):
            entries = _gather_entity_orphan_data(client)

        assert entries == [{"slug": "alt-geheim", "label": "Alt-Geheim", "value": "Geheime Altangabe"}]

    def test_orphan_value_is_formatted_with_its_field_template(self, facility, doc_type_client_profile, staff_user):
        """Pin: ``x__gather_entity_orphan_data__mutmut_42`` —
        ``format_dynamic_display_value(decrypted, ft)`` ->
        ``format_dynamic_display_value(decrypted, None)``.

        Bei ``ft is None`` gibt die Formatierung den Rohwert unveraendert
        zurueck — bei TEXT (dem einzigen bisher getesteten Typ) faellt das
        nicht auf. Hier ist die Waise ein BOOLEAN-Feld. Original: ``"Ja"``.
        Mutant: der rohe ``True``-Wert.
        """
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Alt-Einverstaendnis",
            slug="alt-einverstaendnis",
            field_type=FieldTemplate.FieldType.BOOLEAN,
        )
        client = self._client_with(
            facility,
            staff_user,
            doc_type_client_profile,
            {"wohnsituation": "WG", ft.slug: True},
            "orphan-bool-01",
        )

        with translation.override("de"):
            entries = _gather_entity_orphan_data(client)

        assert entries == [{"slug": "alt-einverstaendnis", "label": "Alt-Einverstaendnis", "value": "Ja"}]


# ---------------------------------------------------------------------------
# _gather_episodes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEpisodes:
    """Episoden — Reihenfolge, Schluesselsatz und die drei Join-Klammern."""

    def _episode(self, case, user, *, title, started_at, pk, ended_at=None):
        return Episode.objects.create(
            id=pk,
            case=case,
            title=title,
            description="",
            started_at=started_at,
            ended_at=ended_at,
            created_by=user,
            deleted_by=user,
        )

    def test_episodes_are_ordered_newest_first(self, case_open, client_identified, staff_user):
        """Pin: ``x__gather_episodes__mutmut_4`` — ``.order_by("-started_at", "pk")``
        -> ``.order_by("pk")``.

        Drei Episoden, PK-aufsteigend in Datum-aufsteigender Folge. Original:
        ``["dritte", "zweite", "erste"]``. Mutant: PK-Reihenfolge und damit
        exakt die Gegenrichtung — bei Zufalls-UUIDs im Echtbetrieb sogar eine
        voellig willkuerliche Folge.
        """
        base = date(2026, 1, 1)
        titles = ("erste", "zweite", "dritte")
        for offset, (pk, title) in enumerate(zip(ASCENDING_PKS, titles, strict=True)):
            self._episode(case_open, staff_user, title=title, started_at=base + timedelta(days=offset), pk=pk)

        with translation.override("de"):
            rows = _gather_episodes(client_identified)

        assert [row["title"] for row in rows] == ["dritte", "zweite", "erste"]

    def test_episodes_fall_back_to_pk_order_on_started_at_ties(self, case_open, client_identified, staff_user):
        """Pin: ``x__gather_episodes__mutmut_5`` — PK-Tiebreak entfernt.

        ``started_at`` ist ein reines ``DateField``; mehrere Kontakte am selben
        Tag sind der Regelfall. Drei Episoden mit demselben Datum, eingefuegt in
        der Rotation ``B, A, C``. Original: PK-aufsteigend (``["A", "B", "C"]``).
        Mutant: die physische Folge ``["B", "A", "C"]`` (nachgemessen) bzw.
        deren Umkehrung.
        """
        started_at = date(2026, 2, 2)
        for pk, title in zip(TIEBREAK_INSERT_PKS, TIEBREAK_INSERT_LABELS, strict=True):
            self._episode(case_open, staff_user, title=title, started_at=started_at, pk=pk)

        with translation.override("de"):
            rows = _gather_episodes(client_identified)

        assert [row["title"] for row in rows] == ["A", "B", "C"]

    def test_episode_entry_carries_all_three_dates(self, case_open, client_identified, staff_user):
        """Pin: ``x__gather_episodes__mutmut_29``/``_30`` (Key ``"started_at"``),
        ``__mutmut_34``/``_35`` (Key ``"created_at"``) und ``__mutmut_33``
        (``"ended_at": _isoformat(episode.ended_at)`` -> ``_isoformat(None)``).

        Eine **abgeschlossene** Episode. Original: beide Datumsschluessel
        vorhanden, ``ended_at`` traegt den Abschlusstag. Mutant 29/30 bzw.
        34/35 lassen den jeweiligen Schluessel verschwinden (die
        Dict-Gleichheit schlaegt fehl); Mutant 33 meldet ``None`` — eine
        abgeschlossene Episode erschiene in der Auskunft als offen.
        """
        episode = self._episode(
            case_open,
            staff_user,
            title="Krisenphase",
            started_at=date(2026, 3, 1),
            ended_at=date(2026, 3, 31),
            pk=PK_A,
        )
        episode.refresh_from_db()

        with translation.override("de"):
            rows = _gather_episodes(client_identified)

        assert rows == [
            {
                "case": "Offener Fall",
                "title": "Krisenphase",
                "description": "",
                "started_at": "2026-03-01",
                "ended_at": "2026-03-31",
                "created_at": episode.created_at.isoformat(),
                "created_by": staff_user.username,
                "is_deleted": False,
                "deleted_at": None,
                "deleted_by": staff_user.username,
            }
        ]

    def test_episodes_are_gathered_without_n_plus_one(self, case_open, client_identified, staff_user):
        """Pin: ``x__gather_episodes__mutmut_9`` (``"case"`` entfernt),
        ``__mutmut_10`` (``"created_by"`` entfernt) und ``__mutmut_11``
        (``"deleted_by"`` entfernt) aus ``.select_related(...)``.

        Drei Episoden mit belegten ``created_by``/``deleted_by`` an einem Fall.
        Original: keine Nachlade-Abfrage auf ``core_case`` (``episode.case.title``
        kommt aus dem Join) und keine auf ``core_user``. Jeder Mutant erzeugt
        drei zusaetzliche Abfragen auf genau einer der beiden Tabellen.
        """
        base = date(2026, 4, 1)
        for index in range(3):
            self._episode(
                case_open,
                staff_user,
                title=f"Phase {index}",
                started_at=base + timedelta(days=index),
                pk=uuid.uuid4(),
            )

        with translation.override("de"):
            _gather_episodes(client_identified)

        with translation.override("de"), CaptureQueriesContext(connection) as captured:
            rows = _gather_episodes(client_identified)

        assert len(rows) == 3
        assert _queries_on(captured, "core_case") == []
        assert _queries_on(captured, "core_user") == []


# ---------------------------------------------------------------------------
# _gather_event_history
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEventHistory:
    """Ereignis-Historie — Tiebreak und die beiden ungepinnten Joins."""

    def test_event_history_falls_back_to_pk_order_on_changed_at_ties(self, sample_event, staff_user, monkeypatch):
        """Pin: ``x__gather_event_history__mutmut_5`` —
        ``.order_by("-changed_at", "pk")`` -> ``.order_by("-changed_at")``.

        ``changed_at`` ist ``auto_now_add``; bei Batch-Aenderungen kollidieren
        die Stempel. Drei Zeilen mit demselben ``changed_at``, PK-absteigend
        eingefuegt. Original: PK-aufsteigend, also
        ``["Erstellt", "Aktualisiert", "Gelöscht"]``. Mutant:
        Einfuegereihenfolge, also die Gegenrichtung.

        ``EventHistory`` ist append-only — ein DB-Trigger verbietet jedes
        ``UPDATE``. Der Gleichstand entsteht deshalb beim Anlegen: die Uhr, aus
        der ``auto_now_add`` schoepft, wird fuer die drei Inserts festgenagelt.
        """
        fixed_now = timezone.now()
        monkeypatch.setattr(fields_timezone, "now", lambda: fixed_now)
        actions = (EventHistory.Action.UPDATE, EventHistory.Action.CREATE, EventHistory.Action.DELETE)
        for pk, action in zip(TIEBREAK_INSERT_PKS, actions, strict=True):
            EventHistory.objects.create(id=pk, event=sample_event, action=action, changed_by=staff_user)

        with translation.override("de"):
            rows = _gather_event_history([sample_event.pk])

        assert [row["action"] for row in rows] == ["Erstellt", "Aktualisiert", "Gelöscht"]

    def test_event_history_is_gathered_without_n_plus_one(self, sample_event, staff_user):
        """Pin: ``x__gather_event_history__mutmut_9`` (``"changed_by"`` entfernt)
        und ``__mutmut_11`` (``"event__document_type"`` entfernt) aus
        ``.select_related(...)``.

        Drei Historien-Zeilen mit belegtem ``changed_by``. Original: keine
        Nachlade-Abfrage auf ``core_user`` (``_username(entry.changed_by)``) und
        keine auf ``core_documenttype`` (``entry.event.document_type.name``).
        Mutant 9 bzw. 11 erzeugt je drei zusaetzliche Abfragen auf genau einer
        der beiden Tabellen — der Test trennt die Faelle sauber.
        """
        for _ in range(3):
            EventHistory.objects.create(
                event=sample_event,
                action=EventHistory.Action.UPDATE,
                changed_by=staff_user,
            )

        with translation.override("de"):
            _gather_event_history([sample_event.pk])

        with translation.override("de"), CaptureQueriesContext(connection) as captured:
            rows = _gather_event_history([sample_event.pk])

        assert len(rows) == 3
        assert _queries_on(captured, "core_user") == []
        assert _queries_on(captured, "core_documenttype") == []


# ---------------------------------------------------------------------------
# _gather_events
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEvents:
    """Ereignisse — die vier Join-Klammern der Serialisierung."""

    def test_events_are_gathered_without_n_plus_one(
        self, facility, client_identified, doc_type_contact, case_open, episode, admin_user, staff_user
    ):
        """Pin: ``x__gather_events__mutmut_11`` (``"document_type"``),
        ``__mutmut_12`` (``"created_by"``), ``__mutmut_13`` (``"case"``) und
        ``__mutmut_14`` (``"episode"``) — je ein Glied aus
        ``.select_related("document_type", "created_by", "case", "episode")``.

        Drei Ereignisse mit **allen vier** belegten Beziehungen; der
        Anhang-Index wird leer uebergeben, damit ``_gather_attachments`` keine
        eigene Abfrage beisteuert. Original: genau EINE Abfrage insgesamt.
        ``_serialize_event`` liest ``document_type.name``, ``created_by``,
        ``case.title`` und ``episode.title`` — jeder Mutant laedt genau eine
        davon je Ereignis nach (drei zusaetzliche Abfragen).
        """
        now = timezone.now()
        for index in range(3):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=now - timedelta(minutes=index),
                data_json={},
                created_by=staff_user,
                case=case_open,
                episode=episode,
            )

        with translation.override("de"):
            _gather_events(client_identified, admin_user, {})

        with translation.override("de"), CaptureQueriesContext(connection) as captured:
            events_data, event_ids = _gather_events(client_identified, admin_user, {})

        assert len(events_data) == 3
        assert len(event_ids) == 3
        assert len(captured.captured_queries) == 1


# ---------------------------------------------------------------------------
# _gather_legal_holds
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherLegalHolds:
    """Legal Holds — Reihenfolge und der Ersteller-Join."""

    def _hold(self, facility, client, user, *, reason, pk, dismissed_at=None):
        """Ein Hold auf die Person.

        ``unique_active_legal_hold`` laesst je (Einrichtung, Zieltyp, Ziel) nur
        EINEN nicht aufgehobenen Hold zu — die Auskunft weist aber Bestand UND
        Historie aus. Alle Holds bis auf den ersten tragen deshalb ein
        ``dismissed_at``; gepinnt wird ihre Reihenfolge, nicht ihr Zustand.
        """
        return LegalHold.objects.create(
            id=pk,
            facility=facility,
            target_type="Client",
            target_id=client.pk,
            reason=reason,
            created_by=user,
            dismissed_at=dismissed_at,
        )

    def test_legal_holds_are_ordered_newest_first(self, facility, client_identified, lead_user):
        """Pin: ``x__gather_legal_holds__mutmut_4`` — ``.order_by("-created_at", "pk")``
        -> ``.order_by("pk")``.

        Drei Holds, PK-aufsteigend in Zeit-aufsteigender Folge. Original:
        ``["dritter", "zweiter", "erster"]``. Mutant: PK-Reihenfolge und damit
        die Gegenrichtung (``LegalHold.id`` ist im Echtbetrieb eine
        Zufalls-UUID ohne Bezug zur Anlagezeit).
        """
        reasons = ("erster", "zweiter", "dritter")
        dismissed = timezone.now()
        for index, (pk, reason) in enumerate(zip(ASCENDING_PKS, reasons, strict=True)):
            self._hold(
                facility,
                client_identified,
                lead_user,
                reason=reason,
                pk=pk,
                dismissed_at=None if index == 0 else dismissed,
            )
        now = timezone.now()
        for offset, pk in enumerate(ASCENDING_PKS):
            LegalHold.objects.filter(pk=pk).update(created_at=now - timedelta(hours=2 - offset))

        with translation.override("de"):
            rows = _gather_legal_holds(facility, client_identified, [])

        assert [row["reason"] for row in rows] == ["dritter", "zweiter", "erster"]

    def test_legal_holds_fall_back_to_pk_order_on_created_at_ties(self, facility, client_identified, lead_user):
        """Pin: ``x__gather_legal_holds__mutmut_5`` — PK-Tiebreak entfernt.

        Drei gleichzeitig angelegte Holds (``created_at`` ist ``auto_now_add``),
        eingefuegt in der Rotation ``B, A, C``. Original: PK-aufsteigend
        (``["A", "B", "C"]``). Mutant: die physische Folge ``["B", "A", "C"]``
        (nachgemessen) bzw. deren Umkehrung.
        """
        dismissed = timezone.now()
        for index, (pk, reason) in enumerate(zip(TIEBREAK_INSERT_PKS, TIEBREAK_INSERT_LABELS, strict=True)):
            self._hold(
                facility,
                client_identified,
                lead_user,
                reason=reason,
                pk=pk,
                dismissed_at=None if index == 0 else dismissed,
            )
        LegalHold.objects.filter(pk__in=ASCENDING_PKS).update(created_at=timezone.now())

        with translation.override("de"):
            rows = _gather_legal_holds(facility, client_identified, [])

        assert [row["reason"] for row in rows] == ["A", "B", "C"]

    def test_legal_holds_are_gathered_without_n_plus_one(self, facility, client_identified, lead_user):
        """Pin: ``x__gather_legal_holds__mutmut_8`` — ``.select_related("created_by",
        "dismissed_by")`` -> ``.select_related("dismissed_by")``.

        ``LegalHold.created_by`` ist NON-NULL (``PROTECT``): jede Zeile hat
        einen Ersteller, es gibt keinen Null-Escape. Drei Holds. Original:
        keine Nachlade-Abfrage auf ``core_user``. Mutant: drei.
        """
        dismissed = timezone.now()
        for index in range(3):
            self._hold(
                facility,
                client_identified,
                lead_user,
                reason=f"Hold {index}",
                pk=uuid.uuid4(),
                dismissed_at=None if index == 0 else dismissed,
            )

        with translation.override("de"):
            _gather_legal_holds(facility, client_identified, [])

        with translation.override("de"), CaptureQueriesContext(connection) as captured:
            rows = _gather_legal_holds(facility, client_identified, [])

        assert len(rows) == 3
        assert _queries_on(captured, "core_user") == []
