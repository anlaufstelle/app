"""Pin-Tests der Welle K (0.98) — Prio-2-Survivors in ``core.services.client.merge``.

Quelle: Füllauftrag #2193,
Batch ``merge-crud`` (``triage-096-merge-1.json``/``triage-096-merge-2-misc.json``).
Der Batch benennt 26 Mutanten in ``reject_client_merge`` und ``unmerge_client``.

**Befund vor dem Pinnen (bindend fuer die Klassifikation unten):** die
Zeilen-/Begruendungstexte des Batches fuer ``unmerge_client`` stammen aus der
0.96-Triage und referenzieren eine Fassung VOR dem Hold-Guard aus Refs #2200
(36 zusaetzliche Zeilen). Der Mutantenbaum bei 448a6d66 nummeriert ab dort
neu — jeder Mutant wurde deshalb einzeln gegen seinen TATSAECHLICHEN
Funktionskoerper verifiziert (nicht gegen den Batch-Text). Ergebnis: 13 der 15
``unmerge_client``-Survivors sind nach Verifikation bereits von bestehenden
Tests getoetet (siehe Bericht), nur zwei sind echte Luecken:

* ``x_unmerge_client__mutmut_136`` — ``Attachment.objects.filter(pk__in=
  moved_attachments, client=target)`` verliert das Kwarg ``client=target``.
  Kein Bestandstest deckt einen nach dem Merge an eine DRITTE Person
  umgehaengten Anhang ab (nur der Fall-Fall ist in
  ``test_mutation_pins_096_merge.py`` gepinnt).
* ``x_unmerge_client__mutmut_178`` — ``bump_ban_revision_for_client_change(
  target, had_banner_entries=had_banner_entries)`` verliert das Kwarg; der
  Default ``None`` laesst die Funktion den Banner-Zustand NACH der
  Ereignis-Ruecknahme neu ermitteln (immer ``False``) statt den vorher
  gemessenen Wert zu nehmen — die Stratum-0-Revision der Einrichtung wird
  nach dem Aufheben eines Merges mit aktivem Hausverbot nicht mehr erhoeht.

``reject_client_merge`` ist von der Drift nicht betroffen (Funktion vor dem
Hold-Guard, Zeilen 304/305/311/313-315 stimmen 1:1 mit dem Batch); alle elf
Survivors betreffen ausschliesslich die Rechenschaftsfelder der Ablehnung und
werden mit einer einzigen Zusicherungsgruppe gepinnt.

Konventionen wie ``test_mutation_pins_096_merge.py``: exakte Vergleiche
(``==``) statt Teilstrings, Fixtures aus ``src/tests/conftest.py``, kein
Nachbau von Produktionslogik. Jeder Test nennt in seiner Docstring die
gepinnten Mutanten und den beim Mutanten abweichenden Wert.
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from core.models import AuditLog, BanRevision, Client, DocumentType, DocumentTypeField, FieldTemplate
from core.models.attachment import Attachment
from core.models.workitem import DeletionRequest
from core.services.client.merge import (
    merge_clients,
    reject_client_merge,
    request_client_merge,
    unmerge_client,
)
from core.services.events import create_event
from tests.conftest import _make_attachment

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

FUTURE = (date.today() + timedelta(days=30)).isoformat()


def _identified(facility, user, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=user,
    )


@pytest.fixture
def source(facility, staff_user):
    return _identified(facility, staff_user, "Pin098-Merge-Quelle")


@pytest.fixture
def target(facility, staff_user):
    return _identified(facility, staff_user, "Pin098-Merge-Ziel")


@pytest.fixture
def stranger(facility, staff_user):
    return _identified(facility, staff_user, "Pin098-Merge-Dritte")


def _file_slug_with_attachment(facility, owner, doc_type, user, *, ft_name="Pin098-Nachweis"):
    """FILE-Feld am ``doc_type`` plus Anhang und Stufe-B-Marker am ``owner``.

    Wortgleich zum Helfer in ``test_mutation_pins_096_merge.py``.
    """
    attachment = _make_attachment(facility, owner, user, ft_name=ft_name)
    field_template = attachment.field_template
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field_template, sort_order=2)
    owner.document_type = doc_type
    owner.data_json = {field_template.slug: {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}}
    owner.save()
    return field_template.slug, attachment


def _make_ban_doc_type(facility, *, name="Pin098-Hausverbot"):
    """Dokumentationstyp mit ``SystemType.BAN`` — Muster ``test_zeitstrom_ban_watch.py``."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        name=name,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
    )
    for field_name, field_type, required in (
        ("Grund", FieldTemplate.FieldType.TEXTAREA, True),
        ("Bis", FieldTemplate.FieldType.DATE, False),
        ("Aktiv", FieldTemplate.FieldType.BOOLEAN, False),
    ):
        ft = FieldTemplate.objects.create(
            facility=facility, name=field_name, field_type=field_type, is_required=required
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft)
    return doc_type


def _stratum0(facility):
    row = BanRevision.objects.filter(facility=facility).first()
    return row.revision_normal if row else 0


class TestRejectAccountabilityAndAttribution:
    """Die ``DELETION_REJECTED``-Zeile ist die einzige Evidenz einer Ablehnung.

    ``merge_clients``/``approve_client_merge`` schreiben bewusst KEINE eigene
    Zeile fuer eine Ablehnung — Antragstellung, Ablehnung und deren
    Rechenschaftsfelder sind die gesamte Vier-Augen-Spur. ``AuditLog.detail``
    ist ``null=True`` und ``audit_event`` akzeptiert ``None``/fehlende Kwargs
    ausdruecklich: alle elf Mutanten laufen deshalb klaglos durch und aendern
    nur ein Rechenschaftsfeld, nie Status oder Personendaten (die bleiben laut
    ``test_client_merge_workflow.py::test_reject_leaves_clients_untouched``
    ohnehin unberuehrt).
    """

    def test_rejection_carries_full_accountability(self, facility, source, target, staff_user, admin_user):
        """Pin: ``x_reject_client_merge__mutmut_25`` (``reviewed_by`` ->
        ``None``), ``_26`` (``reviewed_at`` -> ``None``), ``_30``/``_36``/
        ``_39``/``_40``/``_41`` (``target_type`` -> ``None``/entfernt/
        ``"XXDeletionRequestXX"``/``"deletionrequest"``/``"DELETIONREQUEST"``)
        und ``_32``/``_38``/``_45``/``_48`` (``detail`` -> ``None``/entfernt/
        ``target_client``bzw. ``merge_into`` -> ``str(None)``).

        Original: der Antrag traegt Reviewer und Zeitstempel, die
        ``DELETION_REJECTED``-Zeile exakt ``target_type == "DeletionRequest"``
        und ``detail`` mit beiden UUIDs als Strings. Abweichung je Mutant: genau
        eines dieser vier Felder ist falsch oder fehlt.
        """
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")

        reject_client_merge(dr, admin_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED
        assert dr.reviewed_by_id == admin_user.pk
        assert dr.reviewed_at is not None

        row = AuditLog.objects.get(
            action=AuditLog.Action.DELETION_REJECTED,
            target_id=str(dr.pk),
        )
        assert row.target_type == "DeletionRequest"
        assert row.detail == {
            "target_client": str(source.pk),
            "merge_into": str(target.pk),
        }


class TestUnmergeAttachmentInventoryDiscipline:
    """Zurueck wandert nur, was NOCH beim Ziel liegt — Attachment-Spiegel zu
    ``test_a_case_moved_away_after_the_merge_is_left_alone`` (096-Pins, dort
    nur fuer ``Case`` gepinnt)."""

    def test_an_attachment_moved_away_after_the_merge_is_left_alone(
        self, facility, source, target, stranger, doc_type_client_profile, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_136`` — ``Attachment.objects.filter(
        pk__in=moved_attachments, client=target)`` verliert das Kwarg
        ``client=target`` (nur ``pk__in`` bleibt).

        Reparatur-/Altzustand: ein im Inventar genannter Anhang liegt
        inzwischen bei einer DRITTEN Person. Original: der Filter
        ``client=target`` trifft 0 Zeilen, der Anhang bleibt bei ihr. Mutant:
        das veraltete Inventar reisst ihn zur wiederhergestellten Quelle.
        """
        _slug, moved_attachment = _file_slug_with_attachment(facility, source, doc_type_client_profile, staff_user)
        target.document_type = doc_type_client_profile
        target.save()

        merge_clients(source, target, lead_user)
        Attachment.objects.filter(pk=moved_attachment.pk).update(client=stranger)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        moved_attachment.refresh_from_db()
        assert moved_attachment.client_id == stranger.pk


class TestUnmergeBanBannerRevision:
    """Die Stratum-0-Revision muss die VOR der Ruecknahme gemessene
    Banner-Wirkung bumpen — nicht die danach neu ermittelte."""

    def test_undo_bumps_the_facility_ban_revision_when_the_banner_moves_back(
        self, facility, source, target, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_178`` — ``bump_ban_revision_for_
        client_change(target, had_banner_entries=had_banner_entries)`` verliert
        das Kwarg (Default ``None``).

        Die Quelle traegt ein AKTIVES Hausverbot; der Merge nimmt das Ereignis
        mit, das Ziel steht danach im Banner. Original: ``had_banner_entries``
        wird VOR der Ruecknahme (Zeile 730 a.F.) als ``True`` gemessen und an
        ``bump_ban_revision_for_client_change`` durchgereicht — die
        Stratum-0-Revision der Einrichtung steigt, weil das Ziel nach dem Undo
        keinen Banner mehr traegt. Mutant: der Default ``None`` laesst die
        Funktion den Zustand NACH der Ruecknahme neu ermitteln
        (``client_has_ban_banner_entries(target)`` ist dann ``False``, wie VOR
        dem Merge) — kein Bump, die Banner-Projektion bleibt veraltet.
        """
        ban_doc_type = _make_ban_doc_type(facility)
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True},
            client=source,
        )
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        before = _stratum0(facility)

        unmerge_client(source, lead_user)

        assert _stratum0(facility) == before + 1
