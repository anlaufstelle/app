"""Pin-Tests zum Meilenstein release-0.94 — Survivors in ``core.services.client.merge``.

Quelle: Survivor-Triage des Moduls ``src/core/services/client/merge.py``
(Mutantenbaum ``mutants/src/core/services/client/merge.py``). Gepinnt wird
der Modul-Batch ``merge_1`` aus der Survivor-Triage release-0.94 (Ledger des
Meilensteins) (60 triagierte Mutanten; 59 davon werden hier gepinnt, ``x__adopt_missing_data__mutmut_72``
ist als aequivalent dokumentiert und nicht gepinnt). Schwerpunkte:

* die **Fehlertexte der Vorbedingungen** (``_validate_merge``) und des
  ``KeyError``-Zweigs in :func:`~core.services.client.merge._locked_clients` —
  die bestehenden Tests pruefen nur „irgendeine ``ValidationError``";
* der **Abschluss-Check der Invariante** ``Event.client == Event.case.client``
  (:func:`~core.services.client.merge._assert_event_case_consistency`): sein
  Meldungstext UND die bislang ungepruefte ZIEL-Seite des Aufrufs;
* die **Dokumentationstyp-Uebernahme** (ADR-045, Entscheid 3) samt ihres
  Inventar-Flags ``document_type_adopted`` in beiden Richtungen, der
  Entity-Type-Fehlermeldung von ``clean_dynamic_data`` und der praezisen,
  auf die Marker-Eintraege UND die Quelle beschraenkten Attachment-Wanderung
  (Marker-Invariante I-M1, ADR-039 §7);
* die **Provenienz der Rechenschaft**: Taeter der ``CLIENT_MERGED``-Zeile,
  Antrags-Verweis im ``detail``, Akteur der ``Activity``-Zeile, alte/neue
  Stufe und Taeter der ``STAGE_CHANGE``-Zeile, sowie die strukturierten
  Felder des ``client_merged``-Logs;
* die **Teil-Saves** (``update_fields``): der Stufen-Save darf die
  ``data_json`` des Ziels nicht mit-schreiben, der ``merged_into``-Save nicht
  den ``updated_at``-Stempel der Quelle;
* die **Banner-Wirkung vor der Wanderung** (``had_banner_entries``) — nach
  dem Merge findet ``client_has_ban_banner_entries`` die Ban-Ereignisse der
  Quelle nicht mehr;
* der **Dedup der Besuchs-Zeilen** — die Schleife muss ALLE Zeilen abarbeiten.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); Sprachkontext
wird bei Texten per ``translation.override("de")`` festgenagelt; KEINE Imports
aus ``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py`` bzw.
werden als echte Model-Objekte angelegt (keine Fakes). Jeder Test nennt seine
Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.
"""

import logging
import uuid
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone, translation

from core.models import (
    Activity,
    AuditLog,
    BanRevision,
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldTemplate,
    RecentClientVisit,
)
from core.services.client.merge import (
    approve_client_merge,
    merge_clients,
    request_client_merge,
    unmerge_client,
)
from tests.conftest import _make_attachment

pytestmark = pytest.mark.django_db

LOGGER_NAME = "core.services.client.merge"


@pytest.fixture
def source(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin-Merge-Quelle",
        created_by=staff_user,
    )


@pytest.fixture
def target(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin-Merge-Ziel",
        created_by=staff_user,
    )


@pytest.fixture
def stranger(facility, staff_user):
    """Dritte Person — Traeger der Invarianz-Verletzung bzw. fremder Anhaenge."""
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin-Merge-Dritte",
        created_by=staff_user,
    )


def _merged_row(client):
    return AuditLog.objects.get(
        action=AuditLog.Action.CLIENT_MERGED,
        target_type="Client",
        target_id=str(client.pk),
    )


# ============================================================================
# _validate_merge — die Fehlertexte der Vorbedingungen (13 Mutanten)
# ============================================================================


class TestValidateMergeNamesTheReason:
    """Jede Vorbedingung muss ihren EIGENEN Grund nennen.

    Die bestehenden Tests (``test_client_merge_workflow.py::TestMergeValidation``)
    erwarten durchweg nur ``pytest.raises(ValidationError)`` — jeder der vier
    Texte darf also beliebig verrutschen, ohne dass ein Test rot wird. Fuer die
    Leitung ist der Text aber die gesamte Auskunft: „gleiche Einrichtung",
    „Papierkorb", „anonymisiert" und „mit sich selbst" fuehren zu vier
    voellig verschiedenen naechsten Schritten.
    """

    def test_self_merge(self, source, lead_user):
        """Pin: ``x__validate_merge__mutmut_2`` — ``ValidationError(_("Eine Person
        kann nicht mit sich selbst zusammengeführt werden."))`` -> ``ValidationError(None)``.

        Mutant: ``exc.value.messages == ["None"]`` statt des deutschen Satzes.
        """
        with translation.override("de"), pytest.raises(ValidationError) as exc:
            merge_clients(source, source, lead_user)

        assert exc.value.messages == ["Eine Person kann nicht mit sich selbst zusammengeführt werden."]

    def test_cross_facility(self, source, second_facility, lead_user):
        """Pin: ``x__validate_merge__mutmut_10`` (``XX...XX``-Marker), ``_11``
        (komplett kleingeschrieben) und ``_12`` (komplett grossgeschrieben) —
        alle drei mutieren denselben Meldungstext in Zeile 396.

        Mutant: ``messages`` traegt die verstuemmelte Variante (z. B.
        ``"XXBeide Personen müssen zur selben Einrichtung gehören.XX"`` bzw.
        ``"beide personen ..."`` / ``"BEIDE PERSONEN ..."``) statt des Satzes.
        """
        other = Client.objects.create(facility=second_facility, pseudonym="Fremde Einrichtung")

        with translation.override("de"), pytest.raises(ValidationError) as exc:
            merge_clients(source, other, lead_user)

        assert exc.value.messages == ["Beide Personen müssen zur selben Einrichtung gehören."]

    def test_trashed_participant(self, source, target, lead_user):
        """Pin: ``x__validate_merge__mutmut_13`` (``ValidationError(None)``),
        ``_14`` (``_(None)``), ``_15`` (``XX...XX``), ``_16`` (klein), ``_17``
        (gross) — alle fuenf mutieren den Text in Zeile 399.

        Mutant: ``messages`` liefert ``["None"]`` bzw. die verstuemmelte
        Variante statt ``"Personen im Papierkorb können nicht zusammengeführt
        werden."``.
        """
        source.soft_delete(user=lead_user)

        with translation.override("de"), pytest.raises(ValidationError) as exc:
            merge_clients(source, target, lead_user)

        assert exc.value.messages == ["Personen im Papierkorb können nicht zusammengeführt werden."]

    def test_anonymized_participant(self, source, target, lead_user):
        """Pin: ``x__validate_merge__mutmut_19`` (``ValidationError(None)``),
        ``_20`` (``_(None)``), ``_21`` (``XX...XX``), ``_22`` (klein) — alle
        vier mutieren den Text in Zeile 401.

        Mutant: ``messages`` liefert ``["None"]`` bzw. die verstuemmelte
        Variante statt ``"Anonymisierte Personen können nicht zusammengeführt
        werden."``.
        """
        target.is_active = False
        target.save(update_fields=["is_active"])

        with translation.override("de"), pytest.raises(ValidationError) as exc:
            merge_clients(source, target, lead_user)

        assert exc.value.messages == ["Anonymisierte Personen können nicht zusammengeführt werden."]


# ============================================================================
# _locked_clients — der Fehlertext des fehlenden Zeilen-Locks (3 Mutanten)
# ============================================================================


class TestLockedClientsNamesTheMissingRow:
    """Der ``KeyError``-Zweig traegt ``# pragma: no cover`` — aber er ist erreichbar.

    ``_locked_clients`` sperrt beide Personen in EINEM ``select_for_update``
    und greift die beiden Zeilen danach aus dem Ergebnis-Dict. Liefert die
    Query eine der beiden nicht (Alt-/Reparaturzustand, Instanz aus einem
    abgebrochenen Vorgang), muss der Aufruf mit „Person nicht gefunden."
    abbrechen — nicht mit einem rohen ``KeyError`` aus der Service-Schicht.
    """

    def test_vanished_source_row(self, facility, target, lead_user):
        """Pin: ``x__locked_clients__mutmut_9`` (``XXPerson nicht gefunden.XX``),
        ``_10`` (``person nicht gefunden.``) und ``_11`` (``PERSON NICHT
        GEFUNDEN.``) — alle drei mutieren denselben Text in Zeile 377.

        Der Aufruf bekommt eine Instanz, deren PK es in der Tabelle nicht
        gibt; das ``select_for_update`` liefert nur die Ziel-Zeile, der
        Zugriff ``locked[source.pk]`` faellt in den ``except KeyError``.
        Mutant: ``messages`` traegt die verstuemmelte Variante statt
        ``"Person nicht gefunden."``.
        """
        ghost = Client(
            pk=uuid.uuid4(),
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Verschwundene Zeile",
        )

        with translation.override("de"), pytest.raises(ValidationError) as exc:
            merge_clients(ghost, target, lead_user)

        assert exc.value.messages == ["Person nicht gefunden."]


# ============================================================================
# _assert_event_case_consistency — Abschluss-Check beider Seiten (6 Mutanten)
# ============================================================================


class TestEventCaseConsistencyGuard:
    """Der Abschluss-Check prueft BEIDE Personen — und nennt seinen Grund.

    Die Invariante ``Event.client == Event.case.client`` (``services/events/
    crud.py``) haelt Fall und Ereignis zusammen. Der Merge kann sie auf der
    ZIEL-Seite verletzt vorfinden (Alt-/Reparaturzustand: ein Ereignis haengt
    am Fall des Ziels, aber an einer dritten Person). Faellt die Ziel-Seite aus
    dem Check, committet der Merge eine halb zugeordnete Akte — und die
    ``atomic``-Klammer, die genau das verhindern soll, greift nie.
    """

    def test_broken_target_invariant_aborts_and_rolls_back(
        self, facility, source, target, stranger, doc_type_contact, staff_user, lead_user
    ):
        """Pin: ``x__assert_event_case_consistency__mutmut_3`` (Text ->
        ``None``), ``_4`` (``_(None)``), ``_5`` (``XX...XX``), ``_6`` (klein),
        ``_7`` (gross) — alle fuenf mutieren den Meldungstext in Zeile 444 —
        sowie ``x__execute_merge__mutmut_46``:
        ``_assert_event_case_consistency(source, target)`` ->
        ``_assert_event_case_consistency(source, )``.

        Aufbau: ein Fall des ZIELS traegt ein Ereignis, das einer dritten
        Person zugeordnet ist. Original: der Check schlaegt an, die Meldung
        lautet exakt „Interner Fehler: …", und ``atomic`` rollt den kompletten
        Merge zurueck. Mutanten 3–7: ``messages`` liefert ``["None"]`` bzw. die
        verstuemmelte Variante. Mutant 46: die Quell-Seite allein ist nach der
        Wanderung konsistent — der Merge laeuft DURCH, ``pytest.raises`` sieht
        keine Ausnahme.
        """
        case = Case.objects.create(facility=facility, client=target, title="Fall Ziel", created_by=staff_user)
        Event.objects.create(
            facility=facility,
            client=stranger,
            case=case,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        with translation.override("de"), pytest.raises(ValidationError) as exc:
            merge_clients(source, target, lead_user)

        assert exc.value.messages == [
            "Interner Fehler: Ereignis- und Fall-Zuordnung sind inkonsistent — Zusammenführung abgebrochen."
        ]
        source.refresh_from_db()
        assert source.is_deleted is False
        assert source.merged_into_id is None
        assert not AuditLog.objects.filter(action=AuditLog.Action.CLIENT_MERGED).exists()


# ============================================================================
# _merge_recent_visits — die Schleife muss ALLE Besuchs-Zeilen abarbeiten
# ============================================================================


class TestRecentVisitMigrationVisitsEveryRow:
    """Nach einer kollisionsfreien Zeile geht es weiter, nicht raus.

    ``_merge_recent_visits`` haengt kollisionsfreie Zeilen um und dedupliziert
    kollidierende. Der bestehende Test legt die kollisionsfreie Zeile so an,
    dass sie bei ``ordering = ["-visited_at"]`` ZULETZT kommt — ein ``break``
    statt ``continue`` faellt dort nicht auf. Hier steht sie vorn.
    """

    def test_collision_row_after_a_free_row_still_migrates(self, facility, source, target, staff_user, lead_user):
        """Pin: ``x__merge_recent_visits__mutmut_9`` — ``continue`` -> ``break``
        (Zeile 463).

        ``lead_user`` hat nur die Quelle besucht (kollisionsfrei, juengster
        Stempel -> erste Schleifen-Runde), ``staff_user`` beide (Kollision).
        Original: beide Runden laufen, die Quelle behaelt keine Besuchs-Zeile,
        und die Ziel-Zeile des ``staff_user`` traegt ``is_favorite=True``
        (OR) und den juengeren Stempel. Mutant: die Schleife bricht nach der
        ersten Runde ab — die ``staff_user``-Zeile bleibt an der Quelle
        haengen (``exists()`` ist ``True``) und das Ziel behaelt
        ``is_favorite=False``.
        """
        now = timezone.now()
        sv = RecentClientVisit.objects.create(user=staff_user, client=source, facility=facility, is_favorite=True)
        RecentClientVisit.objects.filter(pk=sv.pk).update(visited_at=now)
        tv = RecentClientVisit.objects.create(user=staff_user, client=target, facility=facility, is_favorite=False)
        RecentClientVisit.objects.filter(pk=tv.pk).update(visited_at=now - timedelta(days=3))
        lv = RecentClientVisit.objects.create(user=lead_user, client=source, facility=facility)
        RecentClientVisit.objects.filter(pk=lv.pk).update(visited_at=now + timedelta(hours=1))

        merge_clients(source, target, lead_user)

        assert RecentClientVisit.objects.filter(client=source).exists() is False
        merged = RecentClientVisit.objects.get(user=staff_user, client=target)
        assert merged.is_favorite is True
        assert merged.visited_at == now
        assert RecentClientVisit.objects.filter(user=lead_user, client=target).count() == 1


# ============================================================================
# _adopt_missing_data — Dokumentationstyp, Flag und Attachment-Wanderung
# ============================================================================


class TestDocumentTypeAdoption:
    """Der Typ der Quelle fuellt einen leeren Typ-Slot des Ziels — und nur dann.

    ADR-045, Entscheid 3: „derselbe Leere-Felder-Grundsatz, auf den Typ
    angewandt". Das Flag ``document_type_adopted`` ist zugleich der
    Undo-Schalter: ``unmerge_client`` setzt den Typ des Ziels NUR dann zurueck
    (und leert dessen ``data_json``), wenn der Merge ihn wirklich gebracht hat.
    Ein falsch berechnetes Flag zerstoert also entweder die Uebernahme oder das
    Ziel beim Aufheben.
    """

    def test_type_is_adopted_even_without_new_data_slugs(self, source, target, doc_type_client_profile, lead_user):
        """Pin: ``x__adopt_missing_data__mutmut_3`` (Flag hart ``None``),
        ``_6`` (``source.document_type_id is not None`` -> ``is None``) und
        ``_22`` (``if not candidates and document_type_adopted:``) — alle drei
        lassen den Early-Return in Zeile 511f. greifen.

        Die Quelle traegt einen Typ, aber KEINE eigenen Daten; das Ziel hat
        noch keinen Typ. Original: das Ziel bekommt den Typ, das Inventar
        vermerkt ``document_type_adopted = True``. Alle drei Mutanten kehren
        vorzeitig zurueck — ``target.document_type_id`` bleibt ``None`` und
        das Inventar-Flag ist ``False``.
        """
        source.document_type = doc_type_client_profile
        source.data_json = {}
        source.save()

        merge_clients(source, target, lead_user)

        target.refresh_from_db()
        assert target.document_type_id == doc_type_client_profile.pk
        assert _merged_row(source).detail["document_type_adopted"] is True

    def test_flag_stays_false_when_the_target_already_has_the_type(
        self, source, target, doc_type_client_profile, lead_user
    ):
        """Pin: ``x__adopt_missing_data__mutmut_4`` (``and`` -> ``or``), ``_5``
        (``target.document_type_id is None`` -> ``is not None``) und ``_23``
        (Early-Return liefert ``True`` statt ``False``).

        Beide Seiten tragen denselben Typ, das Ziel hat Daten, die Quelle
        nicht — uebernommen wird nichts. Original: ``document_type_adopted``
        ist ``False``, und das Aufheben laesst Typ und Daten des Ziels
        deshalb in Ruhe. Alle drei Mutanten schreiben ``True`` ins Inventar;
        ``unmerge_client`` loescht daraufhin Typ UND ``data_json`` des Ziels
        (``target.document_type_id`` wird ``None``).
        """
        source.document_type = doc_type_client_profile
        source.data_json = {}
        source.save()
        target.document_type = doc_type_client_profile
        target.data_json = {"wohnsituation": "eigene Wohnung"}
        target.save()

        merge_clients(source, target, lead_user)

        assert _merged_row(source).detail["document_type_adopted"] is False

        source.refresh_from_db()
        unmerge_client(source, lead_user)

        target.refresh_from_db()
        assert target.document_type_id == doc_type_client_profile.pk
        assert target.data_json == {"wohnsituation": "eigene Wohnung"}

    def test_wrong_entity_type_names_the_reason(self, source, target, doc_type_contact, lead_user):
        """Pin: ``x__adopt_missing_data__mutmut_29`` (``entity_type_error=None``),
        ``_35`` (``_(None)``), ``_36`` (``XX...XX``), ``_37`` (klein), ``_38``
        (gross) — alle fuenf mutieren denselben Kwarg in Zeile 518.

        Die Quelle traegt einen EREIGNIS-Dokumentationstyp; das Ziel hat
        keinen. ``_adopt_missing_data`` wuerde ihn uebernehmen und reicht ihn
        deshalb durch ``clean_dynamic_data`` — dort schlaegt der Entity-Type-
        Guard mit GENAU diesem Text an. Mutanten: ``messages`` liefert
        ``["None"]`` bzw. die verstuemmelte Variante statt „Der
        Dokumentationstyp ist nicht für Personen vorgesehen.".
        """
        source.document_type = doc_type_contact
        source.save()

        with translation.override("de"), pytest.raises(ValidationError) as exc:
            merge_clients(source, target, lead_user)

        assert exc.value.messages == ["Der Dokumentationstyp ist nicht für Personen vorgesehen."]

    def test_only_referenced_attachments_of_the_source_move(
        self, facility, source, target, doc_type_client_profile, staff_user, lead_user
    ):
        """Pin: ``x__adopt_missing_data__mutmut_66`` — ``Attachment.objects.filter(
        pk__in=entry_ids, client=source)`` -> ``.filter(client=source)``
        (Zeile 527).

        Die Quelle traegt zwei Anhaenge: einen, den der uebernommene
        FILE-Marker referenziert, und einen verwaisten (kein Slug zeigt auf
        ihn). Original: nur der referenzierte wandert; der verwaiste bleibt
        bei der Quelle und faellt mit deren Redaktion („Ziel behaelt seine
        Werte", auf Dateien angewandt). Mutant: der Filter verliert die
        Entry-Bindung und zieht ALLE Anhaenge der Quelle mit —
        ``orphan.client_id`` ist dann ``target.pk``.
        """
        orphan = _make_attachment(facility, source, staff_user, ft_name="Verwaistes Dokument")
        referenced = _make_attachment(facility, source, staff_user, ft_name="Nachweis")
        field_template = referenced.field_template
        DocumentTypeField.objects.create(
            document_type=doc_type_client_profile, field_template=field_template, sort_order=5
        )
        source.document_type = doc_type_client_profile
        source.data_json = {
            field_template.slug: {"__files__": True, "entries": [{"id": str(referenced.pk), "sort": 0}]}
        }
        source.save()
        target.document_type = doc_type_client_profile
        target.save()

        merge_clients(source, target, lead_user)

        referenced.refresh_from_db()
        orphan.refresh_from_db()
        assert referenced.client_id == target.pk
        assert orphan.client_id == source.pk

    def test_foreign_attachments_are_never_pulled_in(
        self, facility, source, target, stranger, doc_type_client_profile, staff_user, lead_user
    ):
        """Pin: ``x__adopt_missing_data__mutmut_67`` — ``Attachment.objects.filter(
        pk__in=entry_ids, client=source)`` -> ``.filter(pk__in=entry_ids)``
        (Zeile 527).

        Alt-/Reparaturzustand: der FILE-Marker der Quelle referenziert eine
        Anhang-Zeile, die einer DRITTEN Person gehoert. Original: der
        ``client=source``-Filter deckt das ab — die fremde Zeile bleibt
        unberuehrt (``client_id == stranger.pk``). Mutant: der Merge reisst
        den fremden Anhang an das Ziel (``client_id == target.pk``) — eine
        Zuordnungs-Verletzung quer durch die Akten.
        """
        foreign = _make_attachment(facility, stranger, staff_user, ft_name="Fremder Nachweis")
        field_template = foreign.field_template
        DocumentTypeField.objects.create(
            document_type=doc_type_client_profile, field_template=field_template, sort_order=6
        )
        source.document_type = doc_type_client_profile
        source.data_json = {field_template.slug: {"__files__": True, "entries": [{"id": str(foreign.pk), "sort": 0}]}}
        source.save()
        target.document_type = doc_type_client_profile
        target.save()

        merge_clients(source, target, lead_user)

        foreign.refresh_from_db()
        assert foreign.client_id == stranger.pk


# ============================================================================
# _execute_merge — Rechenschaft, Teil-Saves, Banner-Wirkung, Log
# ============================================================================


class TestMergeAuditProvenance:
    """Die ``CLIENT_MERGED``-Zeile traegt Taeter, Inventar-Flag und Antrag.

    Das append-only AuditLog ist hier zugleich der Undo-Speicher
    (``_merge_inventory``): was im ``detail`` fehlt oder falsch steht, ist
    nach dem Merge weder rekonstruierbar noch umkehrbar. Die bestehenden
    Tests pruefen nur ``merged_into`` und ``adopted_slugs``.
    """

    def test_row_names_the_acting_user_and_a_false_type_flag(self, source, target, lead_user):
        """Pin: ``x__execute_merge__mutmut_92`` (``audit_client_event(source,
        None, ...)``), ``_100`` (``document_type_adopted=None``), ``_111``
        (Kwarg ``document_type_adopted`` ganz entfernt) und
        ``x__adopt_missing_data__mutmut_8`` (Early-Return bei
        ``effective_type is None`` liefert ``True`` statt ``False``).

        Keine der beiden Personen traegt einen Dokumentationstyp — korrekt ist
        ``document_type_adopted is False``. Mutant 92: ``merged_row.user_id``
        ist ``None`` statt der Leitung. Mutant 100: das Flag ist ``None``.
        Mutant 111: der Schluessel fehlt (``KeyError``). Mutant
        ``_adopt_missing_data__mutmut_8``: das Flag ist ``True`` — und
        ``unmerge_client`` wuerde spaeter Typ und Daten des Ziels loeschen.
        """
        merge_clients(source, target, lead_user)

        merged_row = _merged_row(source)
        assert merged_row.user_id == lead_user.pk
        assert merged_row.detail["document_type_adopted"] is False

    def test_activity_names_the_acting_user(self, source, target, lead_user):
        """Pin: ``x__execute_merge__mutmut_116`` — ``log_activity(..., actor=user, ...)``
        -> ``actor=None`` (Zeile 609).

        Original: die ``CLIENT_MERGED``-Zeile im Zeitstrom traegt die
        handelnde Leitung. Mutant: ``activity.actor_id`` ist ``None`` — der
        Zeitstrom zeigt eine Zusammenfuehrung ohne Urheber.
        """
        merge_clients(source, target, lead_user)

        activity = Activity.objects.get(context__action=Activity.Action.CLIENT_MERGED.value)
        assert activity.actor_id == lead_user.pk

    def test_four_eyes_row_carries_request_and_stage_change(self, client_qualified, target, staff_user, admin_user):
        """Pin: ``x__execute_merge__mutmut_89`` (``str(deletion_request.pk)`` ->
        ``str(None)``), ``_113`` (``**extra`` im ``audit_client_event``-Aufruf
        entfernt), ``_69`` (``update_client_stage(target, None, ...)``), ``_70``
        (``..., None, user)`` als neue Stufe) und ``_71`` (``..., None)`` als
        Taeter).

        Der Antragsweg ist die Vier-Augen-Evidenz: der Merge schreibt bewusst
        KEINE ``DELETION_APPROVED``-Zeile, der Verweis auf den genehmigten
        Antrag lebt allein im ``detail``. Mutant 89: dort steht ``"None"``.
        Mutant 113: der Schluessel fehlt ganz (``KeyError``). Die Hochstufung
        des Ziels wird als ``STAGE_CHANGE`` protokolliert — Mutant 69:
        ``old_stage`` ist ``None``; Mutant 70: ``new_stage`` ist ``None``;
        Mutant 71: die Zeile hat keinen Taeter.
        """
        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt angelegt")

        approve_client_merge(dr, admin_user)

        assert _merged_row(client_qualified).detail["deletion_request"] == str(dr.pk)
        stage_row = AuditLog.objects.get(action=AuditLog.Action.STAGE_CHANGE, target_id=str(target.pk))
        assert stage_row.detail["old_stage"] == Client.ContactStage.IDENTIFIED
        assert stage_row.detail["new_stage"] == Client.ContactStage.QUALIFIED
        assert stage_row.user_id == admin_user.pk


class TestMergePartialSaves:
    """Beide Teil-Saves des Merges schreiben genau ihre eigenen Spalten.

    ``update_fields`` ist hier kein Performance-Detail: der Stufen-Save
    liefe sonst ueber ``data_json`` (und verschluesselte damit im Vorbeigehen
    Bestandswerte um), der ``merged_into``-Save ueber ``updated_at`` (und
    faelschte den Aenderungsstempel der Quelle, die inhaltlich unberuehrt
    bleibt).
    """

    def test_stage_save_leaves_the_targets_dynamic_data_untouched(
        self, client_qualified, target, doc_type_client_profile, staff_user, admin_user
    ):
        """Pin: ``x__execute_merge__mutmut_63`` — ``target.save(update_fields=
        ["contact_stage", "updated_at"])`` -> ``update_fields=None`` (Zeile 574).

        Das Ziel traegt einen Bestandswert im verschluesselten Slug
        ``diagnose``, der als KLARTEXT in der Spalte liegt (per
        ``QuerySet.update()`` gesetzt — genau der Zustand vor einer
        nachtraeglichen Sensitivitaets-Erhoehung). Uebernommen wird nichts
        (die Quelle hat keine Daten, das Ziel schon einen Typ), also fasst
        ``_adopt_missing_data`` das Feld nicht an. Original: der Stufen-Save
        schreibt nur ``contact_stage``/``updated_at``, die Spalte behaelt
        ``{"diagnose": "F32"}``. Mutant: der Voll-Save laeuft durch
        ``_encrypt_sensitive_fields`` und legt einen Fernet-Token ab — der
        Vergleich mit ``{"diagnose": "F32"}`` schlaegt fehl.
        """
        Client.objects.filter(pk=target.pk).update(document_type=doc_type_client_profile, data_json={"diagnose": "F32"})
        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt angelegt")

        approve_client_merge(dr, admin_user)

        stored = Client.objects.filter(pk=target.pk).values_list("data_json", flat=True).first()
        assert stored == {"diagnose": "F32"}
        target.refresh_from_db()
        assert target.contact_stage == Client.ContactStage.QUALIFIED

    def test_merged_into_save_keeps_the_sources_update_stamp(self, source, target, lead_user):
        """Pin: ``x__execute_merge__mutmut_77`` — ``source.save(update_fields=
        ["merged_into"])`` -> ``update_fields=None`` (Zeile 578).

        ``updated_at`` steht bewusst NICHT in der Feldliste: die Quelle wird
        zum Tombstone, ihr inhaltlicher Aenderungsstempel bleibt stehen (der
        folgende ``soft_delete`` schreibt ebenfalls nur seine drei Spalten).
        Original: der Stempel ist nach dem Merge unveraendert. Mutant: der
        Voll-Save loest ``auto_now`` aus und setzt ihn auf die Merge-Zeit.
        """
        before = Client.objects.filter(pk=source.pk).values_list("updated_at", flat=True).first()

        merge_clients(source, target, lead_user)

        after = Client.objects.filter(pk=source.pk).values_list("updated_at", flat=True).first()
        assert after == before
        source.refresh_from_db()
        assert source.merged_into_id == target.pk


class TestBanBannerEffectMeasuredBeforeTheMove:
    """Die Banner-Wirkung wird VOR der Wanderung gemessen und weitergereicht.

    Nach dem Merge haengen die Ban-Ereignisse der Quelle am Ziel —
    ``client_has_ban_banner_entries(source)`` findet dann nichts mehr. Wer den
    Zustand erst hinterher (oder gar nicht) uebergibt, unterlaesst den Bump
    der Hausverbots-Revision: die Zeitstrom-Clients behalten ihren alten
    Validator und zeigen das Banner der aufgeloesten Person weiter.
    """

    @pytest.fixture
    def doc_type_ban(self, facility):
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Pin-Hausverbot",
            sensitivity=DocumentType.Sensitivity.ELEVATED,
            system_type=DocumentType.SystemType.BAN,
        )
        for name, field_type, order in (
            ("Pin-Grund", FieldTemplate.FieldType.TEXTAREA, 0),
            ("Pin-Bis", FieldTemplate.FieldType.DATE, 1),
            ("Pin-Aktiv", FieldTemplate.FieldType.BOOLEAN, 2),
        ):
            template = FieldTemplate.objects.create(facility=facility, name=name, field_type=field_type)
            DocumentTypeField.objects.create(document_type=doc_type, field_template=template, sort_order=order)
        return doc_type

    def test_active_ban_on_the_source_bumps_the_revision(
        self, facility, source, target, doc_type_ban, staff_user, lead_user
    ):
        """Pin: ``x__execute_merge__mutmut_7`` (``had_banner_entries =
        client_has_ban_banner_entries(source)`` -> ``= None``), ``_82``
        (``bump_ban_revision_for_client_change(None, ...)``), ``_83``
        (``had_banner_entries=None``) und ``_85`` (Kwarg ganz entfernt) —
        alle vier betreffen dieselbe Klammer (Zeilen 555/587).

        Die Quelle steht mit einem aktiven Hausverbot im Banner. Original:
        Stratum 0 wird gebumpt, die Einrichtung hat danach
        ``revision_normal == 1``. Mutanten 7/83/85: ``had_banner_entries``
        ist ``None``, ``bump_ban_revision_for_client_change`` rechnet den Wert
        NACH der Wanderung neu — die Ban-Ereignisse liegen dann beim Ziel,
        das Ergebnis ist ``False``, es entsteht ueberhaupt keine
        ``BanRevision``-Zeile. Mutant 82: ``None.facility`` beendet den Merge
        mit ``AttributeError``.
        """
        # Die Slugs des Ban-Dokumenttyps traegt die Banner-Projektion fest:
        # aktiv/bis/grund. Die Feldvorlagen oben liefern nur die Whitelist —
        # gelesen wird das Ereignis ueber diese drei Schluessel.
        Event.objects.create(
            facility=facility,
            client=source,
            document_type=doc_type_ban,
            occurred_at=timezone.now(),
            data_json={"grund": "Bedrohung", "bis": (date.today() + timedelta(days=30)).isoformat(), "aktiv": True},
            created_by=staff_user,
        )
        assert BanRevision.objects.filter(facility=facility).exists() is False

        merge_clients(source, target, lead_user)

        assert BanRevision.objects.filter(facility=facility).values_list("revision_normal", flat=True).first() == 1


class TestMergeLogRecord:
    """Der ``client_merged``-Log traegt beide Personen und den Taeter.

    Die strukturierten Felder sind die Betriebs-Spur des Merges: ohne sie
    steht in der Log-Zeile nur noch das Wort ``client_merged``, und die Frage
    „wer hat welche Akte in welche geschoben" ist im Betrieb nicht mehr
    beantwortbar.
    """

    def test_structured_fields_name_source_target_and_user(self, source, target, lead_user, caplog):
        """Pin: ``x__execute_merge__mutmut_126`` (``extra={...}`` -> ``extra=None``),
        ``_128`` (Kwarg ``extra`` ganz entfernt), ``_137`` (Dict-Key
        ``"user_id"`` -> ``"XXuser_idXX"``), ``_138`` (-> ``"USER_ID"``),
        ``_139`` (``getattr(None, "pk", None)``), ``_144``
        (``getattr(user, "XXpkXX", None)``) und ``_145``
        (``getattr(user, "PK", None)``).

        Original: der Datensatz traegt ``source_id``, ``target_id`` und
        ``user_id``. Mutanten 126/128: keines der drei Felder existiert.
        Mutanten 137/138: ``user_id`` fehlt (der Schluessel heisst anders).
        Mutanten 139/144/145: ``user_id`` ist ``None`` statt der PK der
        Leitung.
        """
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            merge_clients(source, target, lead_user)

        records = [r for r in caplog.records if r.name == LOGGER_NAME and r.getMessage() == "client_merged"]
        assert len(records) == 1
        record = records[0]
        assert getattr(record, "source_id", None) == str(source.pk)
        assert getattr(record, "target_id", None) == str(target.pk)
        assert getattr(record, "user_id", None) == lead_user.pk
