"""Pin-Tests zum Meilenstein release-0.96 — Prio-1-Survivors in ``core.services.client.merge``.

Quelle: Mutations-Delta v0.96.0, Pin-Auftrag ``pin-task-merge`` (23 Prio-1-
Survivors der Funktionen :func:`~core.services.client.merge.approve_client_merge`,
:func:`~core.services.client.merge.reject_client_merge`,
:func:`~core.services.client.merge.create_or_get_client_merge_request` und
:func:`~core.services.client.merge.unmerge_client`). Vier Themen:

* **Der Primaerschluessel des TOCTOU-Reloads** (``approve``/``reject``): der
  Antrag wird unter ``select_for_update()`` neu geladen — ohne ``pk`` trifft
  die Sperre einen beliebigen Antrag DERSELBEN Einrichtung. Alle
  Bestandstests legen nur EINEN Antrag an, deshalb ueberleben die Mutanten.
* **Die Mandantengrenze der drei Roh-Lookups**: ``DeletionRequest.target_id``
  ist ein FK-loses Feld, ``DeletionRequest.merge_into`` und
  ``Client.merged_into`` sind FKs OHNE Facility-Bindung im Modell — der
  ``facility``-Filter ist jeweils die einzige Grenze. Faellt er weg,
  verarbeitet der Merge Personen einer FREMDEN Einrichtung.
* **Die Inventar-Grenzen des Undo** (``unmerge_client``): Faelle, Ereignisse,
  Aufgaben und Anhaenge wandern anhand des ``CLIENT_MERGED``-Inventars
  zurueck. Ohne ``pk__in``-Filter wandert der GESAMTE Bestand des Ziels mit;
  ohne ``client=target`` reisst ein veraltetes Inventar auch Zeilen Dritter
  mit. Beides bricht das Undo-Versprechen „nur was wanderte, wandert zurueck".
* **Die Rechenschaftsspur**: Antragsteller, Genehmigungs-Zeitstempel,
  Ablehnungs-Akteur und -Einrichtung, Akteur der Herabstufung und der
  ``CLIENT_RESTORED``-Zeile. ``AuditLog.user``/``facility`` sind ``null=True``
  und ``audit_event`` akzeptiert ``None`` ausdruecklich — die Zeilen entstehen
  also klaglos ohne handelnde Person bzw. ohne Mandantenbindung.

Konventionen wie ``test_mutation_pins_094_merge_1.py``: exakte Vergleiche
(``==``) statt Teilstrings, Fixtures aus ``src/tests/conftest.py`` bzw. echte
Model-Objekte, kein Nachbau von Produktionslogik. Jeder Test nennt in seiner
Docstring die gepinnten Mutanten und den Wert, der beim Mutanten abweicht.
"""

import pytest
from django.utils import timezone

from core.models import AuditLog, Case, Client, DocumentTypeField, Event
from core.models.workitem import DeletionRequest, WorkItem
from core.services.client.merge import (
    approve_client_merge,
    merge_clients,
    reject_client_merge,
    request_client_merge,
    unmerge_client,
)
from tests.conftest import _make_attachment

pytestmark = pytest.mark.django_db


def _identified(facility, user, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=user,
    )


@pytest.fixture
def source(facility, staff_user):
    return _identified(facility, staff_user, "Pin096-Merge-Quelle")


@pytest.fixture
def target(facility, staff_user):
    return _identified(facility, staff_user, "Pin096-Merge-Ziel")


@pytest.fixture
def stranger(facility, staff_user):
    return _identified(facility, staff_user, "Pin096-Merge-Dritte")


def _event(facility, client, doc_type, user, case=None):
    return Event.objects.create(
        facility=facility,
        client=client,
        case=case,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=user,
    )


def _file_slug_with_attachment(facility, owner, doc_type, user, *, ft_name="Pin096-Nachweis"):
    """FILE-Feld am ``doc_type`` plus Anhang und Stufe-B-Marker am ``owner``.

    Wortgleich zum Helfer in ``test_client_merge_workflow.py`` (Entry-Schema
    ``{"id", "sort"}``, ``services/dynamic_data.py``); ``ft_name`` ist frei,
    weil ``FieldTemplate.name`` je Einrichtung eindeutig ist.
    """
    attachment = _make_attachment(facility, owner, user, ft_name=ft_name)
    field_template = attachment.field_template
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field_template, sort_order=2)
    owner.document_type = doc_type
    owner.data_json = {field_template.slug: {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}}
    owner.save()
    return field_template.slug, attachment


def _rejected_rows(deletion_request):
    return list(
        AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_REJECTED,
            target_type="DeletionRequest",
            target_id=str(deletion_request.pk),
        )
    )


class TestFourEyesReloadKeepsItsPrimaryKey:
    """Der Re-Check unter ``select_for_update()`` muss GENAU diesen Antrag laden.

    ``approve``/``reject`` laden den Antrag unter Sperre neu, um das
    TOCTOU-Fenster zu schliessen. Ohne ``pk`` trifft die Sperre einen
    beliebigen Antrag der Einrichtung: es wuerde ein FREMDER Vorgang
    entschieden (bzw. bei mehreren Zeilen ``MultipleObjectsReturned``),
    obwohl der Vier-Augen-Guard darueber den urspruenglichen geprueft hat.
    """

    def test_approve_decides_only_the_named_request(self, facility, source, target, staff_user, admin_user):
        """Pin: ``x_approve_client_merge__mutmut_16`` — ``select_for_update().get(
        pk=deletion_request.pk, facility=...)`` -> ``get(facility=...)`` (Zeile 264).

        Zwei PENDING-Merge-Antraege derselben Einrichtung. Original:
        genehmigt wird ausschliesslich der uebergebene Antrag, der zweite
        bleibt PENDING und dessen Quelle unberuehrt. Mutant: der Lookup
        findet beide Zeilen (``MultipleObjectsReturned``) — er haette ohne
        die zweite Zeile ebenso gut den falschen Vorgang genehmigt.
        """
        other_source = _identified(facility, staff_user, "Pin096-Zweitantrag-Quelle")
        other_target = _identified(facility, staff_user, "Pin096-Zweitantrag-Ziel")
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")
        other_dr = request_client_merge(other_source, other_target, staff_user, reason="Ebenfalls doppelt")

        approve_client_merge(dr, admin_user)

        dr.refresh_from_db()
        other_dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert other_dr.status == DeletionRequest.Status.PENDING
        other_source.refresh_from_db()
        assert other_source.merged_into_id is None
        assert other_source.is_deleted is False

    def test_reject_decides_only_the_named_request(self, facility, source, target, staff_user, admin_user):
        """Pin: ``x_reject_client_merge__mutmut_10`` — ``select_for_update().get(
        pk=deletion_request.pk, facility=...)`` -> ``get(facility=...)`` (Zeile 297).

        Zwei PENDING-Merge-Antraege derselben Einrichtung. Original: nur der
        uebergebene Antrag wird REJECTED, und es entsteht genau EINE
        ``DELETION_REJECTED``-Zeile zu genau dieser Antrags-PK. Mutant: der
        Lookup findet beide Zeilen (``MultipleObjectsReturned``) — er drehte
        sonst einen laufenden Fremdvorgang ab und protokollierte den
        Widerspruch append-only.
        """
        other_source = _identified(facility, staff_user, "Pin096-Zweitantrag-Quelle")
        other_target = _identified(facility, staff_user, "Pin096-Zweitantrag-Ziel")
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")
        other_dr = request_client_merge(other_source, other_target, staff_user, reason="Ebenfalls doppelt")

        reject_client_merge(dr, admin_user)

        dr.refresh_from_db()
        other_dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED
        assert other_dr.status == DeletionRequest.Status.PENDING
        assert len(_rejected_rows(dr)) == 1
        assert _rejected_rows(other_dr) == []


class TestMergeLookupsStayInsideTheFacility:
    """Die drei Roh-Lookups tragen die einzige Mandantengrenze des Moduls.

    ``DeletionRequest.target_id`` ist FK-los, ``DeletionRequest.merge_into``
    und ``Client.merged_into`` sind FKs ohne Facility-Bindung im Modell. Ohne
    den ``facility``-Filter laedt der Service eine Person einer FREMDEN
    Einrichtung, sperrt sie und schreibt Faelle, Ereignisse, Aufgaben und
    Anhaenge ueber die Mandantengrenze hinweg um. Erwartet ist deshalb
    ``Client.DoesNotExist`` — nicht die spaetere ``ValidationError`` aus
    ``_validate_merge``, die den Fund bereits voraussetzt.
    """

    def test_approve_refuses_a_source_of_another_facility(
        self, facility, second_facility, target, staff_user, admin_user
    ):
        """Pin: ``x_approve_client_merge__mutmut_34`` — ``Client.objects.get(
        pk=deletion_request.target_id, facility=...)`` -> ``get(pk=...)`` (Zeile 272).

        Der Antrag der Einrichtung A nennt als Quelle eine Person der
        Einrichtung B. Original: der Lookup findet sie nicht
        (``Client.DoesNotExist``). Mutant: er findet sie, und erst
        ``_validate_merge`` bricht mit einer ``ValidationError`` ab — die
        fremde Zeile wurde da bereits geladen und gesperrt.
        """
        foreign_source = _identified(second_facility, None, "Pin096-Fremde-Quelle")
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=foreign_source.pk,
            merge_into=target,
            reason="Antrag mit mandantenfremder Quelle",
            requested_by=staff_user,
        )

        with pytest.raises(Client.DoesNotExist):
            approve_client_merge(dr, admin_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        foreign_source.refresh_from_db()
        assert foreign_source.merged_into_id is None
        assert foreign_source.is_deleted is False

    def test_approve_refuses_a_target_of_another_facility(
        self, facility, second_facility, source, staff_user, admin_user
    ):
        """Pin: ``x_approve_client_merge__mutmut_39`` — ``Client.objects.get(
        pk=deletion_request.merge_into_id, facility=...)`` -> ``get(pk=...)`` (Zeile 273).

        Spiegelbild fuer die ZIELakte: der Antrag der Einrichtung A zeigt auf
        eine Person der Einrichtung B. Original: ``Client.DoesNotExist``.
        Mutant: die fremde Zielakte wird geladen und gesperrt, der Abbruch
        kommt erst aus ``_validate_merge``.
        """
        foreign_target = _identified(second_facility, None, "Pin096-Fremdes-Ziel")
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=source.pk,
            merge_into=foreign_target,
            reason="Antrag mit mandantenfremdem Ziel",
            requested_by=staff_user,
        )

        with pytest.raises(Client.DoesNotExist):
            approve_client_merge(dr, admin_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        source.refresh_from_db()
        assert source.merged_into_id is None
        assert source.is_deleted is False

    def test_unmerge_refuses_a_target_of_another_facility(self, second_facility, source, lead_user):
        """Pin: ``x_unmerge_client__mutmut_17`` — ``Client.objects.get(
        pk=source.merged_into_id, facility=source.facility)`` -> ``get(pk=...)`` (Zeile 685).

        Alt-/Reparaturzustand: der ``merged_into``-Verweis zeigt ueber die
        Mandantengrenze. Original: ``Client.DoesNotExist``. Mutant: die fremde
        Person wird geladen und mit ``_locked_clients`` gesperrt — der Abbruch
        kaeme erst aus dem fehlenden Audit-Nachweis, und mit passendem
        Inventar wanderten Zeilen ueber die Einrichtungsgrenze.
        """
        foreign_target = _identified(second_facility, None, "Pin096-Fremdes-Undo-Ziel")
        Client.objects.filter(pk=source.pk).update(
            merged_into=foreign_target, is_deleted=True, deleted_at=timezone.now()
        )
        source.refresh_from_db()

        with pytest.raises(Client.DoesNotExist):
            unmerge_client(source, lead_user)

        source.refresh_from_db()
        assert source.merged_into_id == foreign_target.pk
        assert source.is_deleted is True


class TestFourEyesAccountability:
    """Antragsteller, Genehmigungs-Zeitpunkt, Ablehnungs-Akteur und -Einrichtung.

    Der Merge-Pfad schreibt bewusst KEINE ``DELETION_APPROVED``-Zeile
    (``detect_mass_client_destruction`` zaehlt sie) — die ``DeletionRequest``-
    Zeile und die beiden Antrags-/Ablehnungs-Audits sind damit die gesamte
    Vier-Augen-Evidenz. ``AuditLog.user``/``facility`` sind ``null=True``:
    ohne diese Zusicherungen entstehen die Zeilen klaglos ohne Person bzw.
    ohne Mandantenbindung (Art. 5 Abs. 2).
    """

    def test_request_row_names_the_requesting_user(self, source, target, staff_user):
        """Pin: ``x_create_or_get_client_merge_request__mutmut_36`` —
        ``audit_event(..., user=user, ...)`` -> ``user=None`` (Zeile 231).

        Original: die ``DELETION_REQUESTED``-Zeile traegt die antragstellende
        Person. Mutant: ``row.user_id`` ist ``None`` — wer beantragt hat, ist
        aus dem append-only Protokoll nicht mehr rekonstruierbar.
        """
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")

        row = AuditLog.objects.get(
            action=AuditLog.Action.DELETION_REQUESTED,
            target_type="DeletionRequest",
            target_id=str(dr.pk),
        )
        assert row.user_id == staff_user.pk

    def test_approval_carries_a_review_timestamp(self, source, target, staff_user, admin_user):
        """Pin: ``x_approve_client_merge__mutmut_56`` —
        ``deletion_request.reviewed_at = timezone.now()`` -> ``= None`` (Zeile 279).

        Original: ``reviewed_at`` liegt im Fenster zwischen Aufruf und
        Rueckkehr. Mutant: ``reviewed_at`` ist ``None`` — wann die
        Zweitperson entschieden hat, ist nicht mehr belegbar, obwohl der
        Antrag die einzige Evidenz der Zusammenfuehrung ist.
        """
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")
        before = timezone.now()

        approve_client_merge(dr, admin_user)

        after = timezone.now()
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_at is not None
        assert before <= dr.reviewed_at <= after

    def test_rejection_row_names_actor_and_facility(self, facility, source, target, staff_user, admin_user):
        """Pin: ``x_reject_client_merge__mutmut_28`` (``user=reviewer`` ->
        ``user=None``, Zeile 309) und ``x_reject_client_merge__mutmut_29``
        (``facility=deletion_request.facility`` -> ``facility=None``, Zeile 310).

        Original: die ``DELETION_REJECTED``-Zeile traegt die entscheidende
        Person und die Einrichtung. Mutant 28: ``user_id`` ist ``None``.
        Mutant 29: ``facility_id`` ist ``None`` — die Zeile faellt aus jeder
        facility-gescopten Audit-Flaeche heraus, der Vorgang ist fuer die
        Einrichtung unsichtbar protokolliert.
        """
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")

        reject_client_merge(dr, admin_user)

        row = AuditLog.objects.get(
            action=AuditLog.Action.DELETION_REJECTED,
            target_type="DeletionRequest",
            target_id=str(dr.pk),
        )
        assert row.user_id == admin_user.pk
        assert row.facility_id == facility.pk


class TestUnmergeMovesOnlyTheInventory:
    """Zurueck wandert, was das ``CLIENT_MERGED``-Inventar nennt — nichts sonst.

    Ohne den ``pk__in``-Filter zieht das Undo den GESAMTEN Bestand des Ziels
    zur wiederhergestellten Quelle: eigene Alt-Zeilen und alles, was nach dem
    Merge am Ziel entstanden ist. Das ist stiller Datenabfluss aus einer
    aktiven Akte in einen Tombstone, dessen Redaktion nach Fristablauf genau
    diese Zeilen erfasst. Die Bestandstests uebersehen es, weil das Ziel dort
    keine eigenen Zeilen der jeweiligen Art besitzt.
    """

    def test_own_cases_of_the_target_stay(self, facility, source, target, staff_user, lead_user):
        """Pin: ``x_unmerge_client__mutmut_98`` — ``Case.objects.filter(
        pk__in=moved_cases, client=target)`` -> ``filter(client=target)`` (Zeile 733).

        Original: nur der beim Merge gewanderte Fall kehrt zurueck, der
        eigene Fall des Ziels bleibt dort. Mutant: ``eigener.client_id`` ist
        die Quelle — das Undo nimmt eine fremde Fallakte mit.
        """
        moved_case = Case.objects.create(facility=facility, client=source, title="Fall Quelle", created_by=staff_user)
        own_case = Case.objects.create(facility=facility, client=target, title="Fall Ziel", created_by=staff_user)
        merge_clients(source, target, lead_user)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        moved_case.refresh_from_db()
        own_case.refresh_from_db()
        assert moved_case.client_id == source.pk
        assert own_case.client_id == target.pk

    def test_own_and_post_merge_events_of_the_target_stay(
        self, facility, source, target, doc_type_contact, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_105`` — ``Q(pk__in=moved_events,
        case__isnull=True)`` -> ``Q(case__isnull=True)`` (Zeile 736).

        Das Ziel traegt ein eigenes fallfreies Ereignis von VOR dem Merge und
        eines von DANACH; die Quelle bringt genau eines mit. Original: nur
        das gewanderte kehrt zurueck. Mutant: beide Ziel-Ereignisse haengen
        anschliessend an der Quelle.
        """
        own_event = _event(facility, target, doc_type_contact, staff_user)
        moved_event = _event(facility, source, doc_type_contact, staff_user)
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        post_merge_event = _event(facility, target, doc_type_contact, staff_user)

        unmerge_client(source, lead_user)

        for row in (own_event, moved_event, post_merge_event):
            row.refresh_from_db()
        assert moved_event.client_id == source.pk
        assert own_event.client_id == target.pk
        assert post_merge_event.client_id == target.pk

    def test_own_workitems_of_the_target_stay(self, facility, source, target, staff_user, lead_user):
        """Pin: ``x_unmerge_client__mutmut_121`` — ``WorkItem.objects.filter(
        pk__in=moved_workitems, client=target)`` -> ``filter(client=target)`` (Zeile 743).

        Original: nur die gewanderte Aufgabe kehrt zurueck. Mutant: auch die
        eigene Aufgabe des Ziels haengt danach an der Quelle — Freitext zur
        Person an der falschen Akte.
        """
        moved_item = WorkItem.objects.create(
            facility=facility, client=source, title="Aufgabe Quelle", created_by=staff_user
        )
        own_item = WorkItem.objects.create(
            facility=facility, client=target, title="Aufgabe Ziel", created_by=staff_user
        )
        merge_clients(source, target, lead_user)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        moved_item.refresh_from_db()
        own_item.refresh_from_db()
        assert moved_item.client_id == source.pk
        assert own_item.client_id == target.pk

    def test_own_attachments_of_the_target_stay(
        self, facility, source, target, doc_type_client_profile, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_126`` — ``Attachment.objects.filter(
        pk__in=moved_attachments, client=target)`` -> ``filter(client=target)`` (Zeile 745).

        Original: nur die uebernommene Datei-Zeile kehrt zurueck. Mutant: der
        eigene Anhang des Ziels wandert mit an den Tombstone — waehrend der
        ``__files__``-Marker am Ziel stehen bleibt (Marker-Invariante I-M1,
        ADR-039 §7) und die Redaktion der Quelle die Datei nach Fristablauf
        tilgt.
        """
        _slug, moved_attachment = _file_slug_with_attachment(facility, source, doc_type_client_profile, staff_user)
        own_attachment = _make_attachment(facility, target, staff_user, ft_name="Pin096-Ziel-Nachweis")
        target.document_type = doc_type_client_profile
        target.save()
        merge_clients(source, target, lead_user)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        moved_attachment.refresh_from_db()
        own_attachment.refresh_from_db()
        assert moved_attachment.client_id == source.pk
        assert own_attachment.client_id == target.pk

    def test_a_case_moved_away_after_the_merge_is_left_alone(
        self, facility, source, target, stranger, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_99`` — ``Case.objects.filter(
        pk__in=moved_cases, client=target)`` -> ``filter(pk__in=moved_cases)`` (Zeile 733).

        Reparatur-/Altzustand: ein im Inventar genannter Fall liegt
        inzwischen bei einer DRITTEN Person. Original: der Filter
        ``client=target`` trifft 0 Zeilen, der Fall bleibt bei ihr. Mutant:
        das veraltete Inventar reisst ihn zur wiederhergestellten Quelle —
        ein Fallwechsel quer ueber Personendatensaetze.
        """
        moved_case = Case.objects.create(facility=facility, client=source, title="Fall Quelle", created_by=staff_user)
        merge_clients(source, target, lead_user)
        Case.objects.filter(pk=moved_case.pk).update(client=stranger)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        moved_case.refresh_from_db()
        assert moved_case.client_id == stranger.pk


class TestUnmergeRevertsAdoptedFieldsPrecisely:
    """Das Undo entfernt am Ziel genau die uebernommenen Angaben.

    Der uebernommene Dokumentationstyp faellt auf ``None`` zurueck (ADR-045,
    Entscheid 2) — und NUR in diesem Fall wird ``data_json`` geleert. Wurde
    kein Typ uebernommen, bleiben die eigenen Zusatzangaben des Ziels stehen.
    """

    def test_adopted_document_type_falls_back_to_none(
        self, source, target, doc_type_client_profile, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_81`` (``document_type_adopted = None``),
        ``_82`` (``inventory.get(None, False)``), ``_84`` (``inventory.get(False)``),
        ``_86`` (Schluessel ``"XXdocument_type_adoptedXX"``) und ``_87``
        (Schluessel ``"DOCUMENT_TYPE_ADOPTED"``) — alle Zeile 722.

        Das Ziel hat KEINEN Dokumentationstyp, die Quelle bringt Typ und
        Zusatzangaben mit: der Merge uebernimmt beides
        (``document_type_adopted is True``). Original: nach dem Aufheben
        traegt das Ziel wieder ``document_type_id is None`` und ein leeres
        ``data_json``. Bei allen fuenf Mutanten ist das Flag falsy, der
        Ruecknahme-Zweig laeuft nie — das Ziel behaelt dauerhaft den
        Dokumentationstyp aus dem Datensatz einer anderen Person.
        """
        source.document_type = doc_type_client_profile
        source.data_json = {"wohnsituation": "Notunterkunft"}
        source.save()
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        target.refresh_from_db()
        assert target.document_type_id == doc_type_client_profile.pk

        unmerge_client(source, lead_user)

        target.refresh_from_db()
        assert target.document_type_id is None
        assert target.data_json == {}

    def test_own_dynamic_data_of_the_target_survives(
        self, source, target, doc_type_client_profile, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_131`` — ``dict(target.data_json or {})``
        -> ``dict(target.data_json and {})`` (Zeile 748).

        Das Ziel traegt schon vor dem Merge einen eigenen Slug und denselben
        Dokumentationstyp (``document_type_adopted is False``), die Quelle
        steuert nur ``diagnose`` bei. Original: das Undo entfernt genau den
        uebernommenen Slug, der eigene bleibt. Mutant: aus ``or`` wird
        ``and``, der Ausdruck ergibt ``{}`` — das Ziel verliert saemtliche
        Zusatzangaben, nicht nur die uebernommenen.
        """
        source.document_type = doc_type_client_profile
        source.data_json = {"diagnose": "F32"}
        source.save()
        target.document_type = doc_type_client_profile
        target.data_json = {"wohnsituation": "eigene Wohnung"}
        target.save()
        merge_clients(source, target, lead_user)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        target.refresh_from_db()
        assert target.document_type_id == doc_type_client_profile.pk
        assert target.data_json == {"wohnsituation": "eigene Wohnung"}


class TestUnmergeStageRollback:
    """Die Kontaktstufe faellt NUR zurueck, wenn die Hochstufung vom Merge kam.

    Andernfalls waere es genau die verdeckte Herabstufung, die
    ``update_client`` fuer Nicht-Leitung verbietet (Security N2) — die Person
    verloere ihr hoeheres Schutzniveau ohne fachlichen Grund. Und wenn
    herabgestuft wird, ist die ``STAGE_CHANGE``-Zeile die einzige Zuordnung
    dieses Leitungs-Eingriffs.
    """

    def test_a_pre_existing_qualified_target_is_not_downgraded(
        self, facility, client_qualified, staff_user, admin_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_145`` — ``target_stage_before ==
        IDENTIFIED and target.contact_stage == QUALIFIED`` -> ``... or ...`` (Zeile 757).

        Beide Personen stehen schon vor dem Merge auf ``QUALIFIED``
        (``target_stage_before == QUALIFIED``), der Merge stuft also nichts
        hoch. Original: das Undo laesst die Stufe des Ziels stehen und
        schreibt keine ``STAGE_CHANGE``-Zeile. Mutant: die Oder-Bedingung
        greift ueber die zweite Haelfte und stuft das Ziel auf
        ``IDENTIFIED`` HERAB.
        """
        qualified_target = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.QUALIFIED,
            pseudonym="Pin096-Ziel-Qualifiziert",
            created_by=staff_user,
        )
        dr = request_client_merge(client_qualified, qualified_target, staff_user, reason="Doppelt angelegt")
        approve_client_merge(dr, admin_user)
        client_qualified.refresh_from_db()

        unmerge_client(client_qualified, lead_user)

        qualified_target.refresh_from_db()
        assert qualified_target.contact_stage == Client.ContactStage.QUALIFIED
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.STAGE_CHANGE,
            target_type="Client",
            target_id=str(qualified_target.pk),
        ).exists()

    def test_the_downgrade_names_the_acting_lead(self, client_qualified, target, staff_user, admin_user, lead_user):
        """Pin: ``x_unmerge_client__mutmut_157`` — ``update_client_stage(target,
        QUALIFIED, IDENTIFIED, user)`` -> ``..., None)`` (Zeile 760).

        Der Merge stuft das Ziel auf ``QUALIFIED`` hoch, das Undo nimmt das
        zurueck. Original: die Herabstufungs-Zeile traegt die handelnde
        Leitung. Mutant: ``user_id`` ist ``None`` — gerade die
        sicherheitsrelevante Stufenaenderung (nur Leitung darf sie ausloesen,
        N2) waere nicht mehr zurechenbar.
        """
        dr = request_client_merge(client_qualified, target, staff_user, reason="Doppelt angelegt")
        approve_client_merge(dr, admin_user)
        client_qualified.refresh_from_db()

        unmerge_client(client_qualified, lead_user)

        target.refresh_from_db()
        assert target.contact_stage == Client.ContactStage.IDENTIFIED
        rows = [
            row
            for row in AuditLog.objects.filter(
                action=AuditLog.Action.STAGE_CHANGE,
                target_type="Client",
                target_id=str(target.pk),
            )
            if row.detail.get("new_stage") == Client.ContactStage.IDENTIFIED
        ]
        assert len(rows) == 1
        assert rows[0].user_id == lead_user.pk

    def test_restore_row_names_the_acting_lead(self, source, target, lead_user):
        """Pin: ``x_unmerge_client__mutmut_171`` — ``audit_client_event(source,
        user, CLIENT_RESTORED, ...)`` -> ``..., None, ...)`` (Zeile 773).

        Original: die ``CLIENT_RESTORED``-Zeile traegt die handelnde Leitung.
        Mutant: ``user_id`` ist ``None`` — das Aufheben einer
        Zusammenfuehrung ist ein Leitungs-Vorbehalt und die einzige Stelle,
        an der ein Merge-Tombstone wieder aktiv wird; ohne Akteur ist der
        Eingriff nicht zurechenbar.
        """
        merge_clients(source, target, lead_user)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        row = AuditLog.objects.get(
            action=AuditLog.Action.CLIENT_RESTORED,
            target_type="Client",
            target_id=str(source.pk),
        )
        assert row.user_id == lead_user.pk
        assert row.detail["unmerged_from"] == str(target.pk)
