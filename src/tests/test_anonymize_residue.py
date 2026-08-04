"""DSGVO-Restdaten-Matrix-Test fuer Client.anonymize() (Refs #715).

Verifiziert: nach ``Client.anonymize()`` sind in **allen** abhaengigen
Tabellen keine PII-Spuren des Klienten mehr — Pseudonym, Notes, Event-
data_json-Werte, Anhang-Dateinamen, DeletionRequest-Begruendung.

Der Test deckt die als „Restdaten == 0"-Matrix
geforderten Tabellen ab:
- core_client (Stamm)
- core_case + core_episode + core_workitem (linked)
- core_eventhistory (history) — neu mit #715
- core_attachment (Anhaenge) — neu mit #715
- core_deletionrequest (4-Augen-Antraege) — neu mit #715
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import (
    Case,
    Client,
    DeletionRequest,
    DocumentType,
    Episode,
    Event,
    EventHistory,
    WorkItem,
)


@pytest.fixture
def normal_doc_type(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
        category=DocumentType.Category.CONTACT,
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )


@pytest.fixture
def client_with_full_history(facility, normal_doc_type, doc_type_client_profile, doc_type_case_form, admin_user):
    """Klient mit Event + EventHistory + DeletionRequest + WorkItem + Case + Episode.

    M2 P4b (Refs #1058): zusaetzlich mit Personen-``document_type`` +
    ``data_json``-Probe. Der Needle liegt bewusst im **unverschluesselten**
    Slug (``wohnsituation``) — nur so ist er ueberhaupt als Klartext-Residue
    beobachtbar; der encrypted-Slug wuerde die Probe hinter Fernet verstecken.

    M2 P5 (Refs #1058): der Fall bekommt analog Fall-``document_type`` +
    ``data_json`` (unverschluesselter Slug ``anlass``).
    """
    cli = Client.objects.create(
        facility=facility,
        pseudonym="Maria-eindeutig",
        notes="Klient hat psychische Krise, Hausarzt Dr. Mueller",
        contact_stage=Client.ContactStage.IDENTIFIED,
        document_type=doc_type_client_profile,
        data_json={"wohnsituation": "Wohnt bei Dr. Mueller, Hauptstraße 5"},
    )
    case = Case.objects.create(
        facility=facility,
        client=cli,
        title="Mietproblem 2026",
        description="Konkrete Adresse: Hauptstraße 5",
        created_by=admin_user,
        # M2 P5 (Refs #1058): Fall-``document_type`` + ``data_json``-Probe,
        # analog zur Client-Probe oben. Needle im unverschluesselten Slug
        # (``anlass``), sonst versteckt Fernet die Probe.
        document_type=doc_type_case_form,
        data_json={"anlass": "Mahnung von Vermieter Mueller"},
    )
    Episode.objects.create(
        case=case,
        title="Erstberatung",
        description="Klient kam mit Mahnung",
        started_at=timezone.now() - timedelta(days=20),
    )
    event = Event.objects.create(
        facility=facility,
        client=cli,
        document_type=normal_doc_type,
        occurred_at=timezone.now() - timedelta(days=10),
        data_json={
            "freitext": "Maria sehr aufgeloest, weinte",
            "intervention": "Krisengespraech 45 min",
        },
        created_by=admin_user,
    )
    # EventHistory (UPDATE) mit Klartext im data_before/data_after
    EventHistory.objects.create(
        event=event,
        changed_by=admin_user,
        action=EventHistory.Action.UPDATE,
        data_before={"freitext": "Maria sehr aufgeloest"},
        data_after={"freitext": "Maria sehr aufgeloest, weinte"},
    )
    DeletionRequest.objects.create(
        facility=facility,
        target_type="Event",
        target_id=event.pk,
        reason="Klient Maria moechte Vermerk zu Krise loeschen lassen",
        requested_by=admin_user,
    )
    WorkItem.objects.create(
        facility=facility,
        client=cli,
        created_by=admin_user,
        title=f"Termin mit {cli.pseudonym} koordinieren",
        description="Adresse: Hauptstraße 5, telefonisch",
    )
    return cli, case, event


@pytest.mark.django_db
class TestAnonymizeResidueMatrix:
    """Nach Client.anonymize() sind alle PII-Spuren weg — alle Tabellen."""

    PII_VALUES = (
        "Maria",
        "Mueller",
        "psychische Krise",
        "Mahnung",
        "Hauptstraße 5",
        "weinte",
        "Krisengespraech",
        "aufgeloest",
    )

    def _has_residue(self, value, haystack):
        return any(needle in str(haystack) for needle in (value,))

    def test_client_master_data_redacted(self, client_with_full_history):
        cli, _, _ = client_with_full_history
        cli.anonymize()
        cli.refresh_from_db()
        assert cli.pseudonym.startswith("Gelöscht-")
        assert cli.notes == ""
        assert cli.is_active is False

    def test_client_dynamic_data_redacted(self, client_with_full_history):
        """M2 P4b (Refs #1058): ``data_json`` leer, ``document_type`` geloest.

        Der PROTECT-Verweis muss fallen — sonst kettet ein anonymisierter
        Bestand den Konfigurationstyp dauerhaft fest, obwohl seine Daten weg
        sind.
        """
        cli, _, _ = client_with_full_history
        assert cli.data_json, "Positiv-Kontrolle: die Probe muss vorher befuellt sein"
        cli.anonymize()
        cli.refresh_from_db()
        assert cli.data_json == {}
        assert cli.document_type_id is None

    def test_case_and_episode_redacted(self, client_with_full_history):
        cli, case, _ = client_with_full_history
        cli.anonymize()
        case.refresh_from_db()
        assert "Mietproblem" not in case.title
        assert "Hauptstraße" not in case.description
        episode = Episode.objects.get(case=case)
        assert episode.title == "Episode (anonymisiert)"
        assert episode.description == ""

    def test_case_dynamic_data_redacted(self, client_with_full_history):
        """M2 P5 (Refs #1058): ``data_json`` leer, ``document_type`` geloest.

        Der PROTECT-Verweis muss fallen — sonst kettet ein anonymisierter
        Fall den Konfigurationstyp dauerhaft fest, obwohl seine Daten weg
        sind. Muster ``test_client_dynamic_data_redacted``.
        """
        cli, case, _ = client_with_full_history
        assert case.data_json, "Positiv-Kontrolle: die Probe muss vorher befuellt sein"
        cli.anonymize()
        case.refresh_from_db()
        assert case.data_json == {}
        assert case.document_type_id is None

    def test_workitem_redacted(self, client_with_full_history):
        cli, _, _ = client_with_full_history
        cli.anonymize()
        wi = WorkItem.objects.get(client=cli)
        assert wi.title == "Aufgabe (anonymisiert)"
        assert wi.description == ""

    def test_event_history_redacted(self, client_with_full_history):
        cli, _, event = client_with_full_history
        cli.anonymize()
        history = EventHistory.objects.filter(event=event)
        for entry in history:
            assert entry.data_before == {"_redacted": True, "anonymized": True}
            assert entry.data_after == {"_redacted": True, "anonymized": True}
            # Klartext-Werte duerfen NICHT in irgendeiner Spalte mehr stehen.
            for pii in self.PII_VALUES:
                assert pii not in str(entry.data_before)
                assert pii not in str(entry.data_after)

    def test_event_attachments_deleted(self, client_with_full_history, facility, admin_user):
        from core.models import Attachment, FieldTemplate

        cli, _, event = client_with_full_history
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Anlage",
            field_type=FieldTemplate.FieldType.FILE,
        )
        Attachment.objects.create(
            facility=event.facility,
            event=event,
            field_template=ft,
            storage_filename="abc.enc",
            original_filename_encrypted={"ct": "x", "iv": "y"},
            file_size=42,
            mime_type="text/plain",
            created_by=admin_user,
        )

        cli.anonymize()
        # Disk-Loeschen kann in Tests fehlschlagen (kein MEDIA_ROOT-File),
        # DB-Cleanup muss aber durchgehen.
        assert Attachment.objects.filter(event=event).count() == 0

    def test_owner_attachments_deleted(self, client_with_full_history, facility, admin_user):
        """M24 (Refs #1738, ADR-039 §7): personen- UND falleigene Anhaenge fallen mit.

        Seit dem Owner-Umbau haengen Anhaenge nicht mehr nur am Ereignis. Ohne
        einen eigenen Loeschpfad ueberlebten die verschluesselten Dateien einer
        anonymisierten Person als ``.enc`` samt DB-Zeile — eine Art.-17-Luecke,
        die kein Bestandstest gesehen haette (der Event-Pfad bleibt gruen).
        """
        from core.models import Attachment, FieldTemplate

        cli, case, _ = client_with_full_history
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Anlage-Owner",
            field_type=FieldTemplate.FieldType.FILE,
        )
        for owner_kwargs in ({"client": cli}, {"case": case}):
            Attachment.objects.create(
                facility=facility,
                field_template=ft,
                storage_filename="owner.enc",
                original_filename_encrypted={"ct": "x", "iv": "y"},
                file_size=42,
                mime_type="text/plain",
                created_by=admin_user,
                **owner_kwargs,
            )
        assert Attachment.objects.filter(client=cli).count() == 1  # Positiv-Kontrolle
        assert Attachment.objects.filter(case=case).count() == 1

        cli.anonymize()

        assert Attachment.objects.filter(client=cli).count() == 0
        assert Attachment.objects.filter(case=case).count() == 0

    def test_file_markers_gone_from_owner_data_json(self, client_with_full_history):
        """Invariante I-M1 (ADR-039 §7): kein ``__files__``-Marker ueberlebt.

        Der physische Loeschpfad und die Marker-Bereinigung muessen in
        derselben Transaktion liegen — sonst zeigen Marker in ``data_json``
        auf Attachment-Zeilen, die es nicht mehr gibt (Detail-Anzeige und
        Auskunft wuerden sie weiter als vorhandene Datei ausweisen).
        """
        cli, case, _ = client_with_full_history
        marker = {"__files__": True, "entries": [{"id": "11111111-1111-1111-1111-111111111111", "sort": 0}]}
        cli.data_json = {**cli.data_json, "anlage": marker}
        cli.save(update_fields=["data_json"])
        case.data_json = {**case.data_json, "anlage": marker}
        case.save(update_fields=["data_json"])

        cli.anonymize()

        cli.refresh_from_db()
        case.refresh_from_db()
        for haystack in (cli.data_json, case.data_json):
            assert "__files__" not in str(haystack)
            assert "__file__" not in str(haystack)

    def test_deletion_request_reason_redacted(self, client_with_full_history):
        cli, _, event = client_with_full_history
        cli.anonymize()
        dr = DeletionRequest.objects.get(target_id=event.pk)
        assert dr.reason == "[Anonymisiert]"
        # Antrag-Meta bleibt fuer Audit-Trail erhalten.
        assert dr.requested_by is not None

    def test_no_pii_residue_anywhere(self, client_with_full_history):
        """Aggregat-Check: keine PII-Werte in irgendeiner abhaengigen Tabelle."""
        cli, case, event = client_with_full_history
        cli.anonymize()
        cli.refresh_from_db()
        case.refresh_from_db()

        haystacks = []
        haystacks.append(str(cli.__dict__))
        haystacks.append(str(case.__dict__))
        for ep in Episode.objects.filter(case=case):
            haystacks.append(str(ep.__dict__))
        for wi in WorkItem.objects.filter(client=cli):
            haystacks.append(str(wi.__dict__))
        for h in EventHistory.objects.filter(event=event):
            haystacks.append(str(h.__dict__))
        for dr in DeletionRequest.objects.filter(target_id=event.pk):
            haystacks.append(str(dr.__dict__))

        full_haystack = "\n".join(haystacks)
        leaked = [pii for pii in self.PII_VALUES if pii in full_haystack]
        assert not leaked, (
            f"PII-Reste in abhaengigen Tabellen nach anonymize(): {leaked}\n"
            f"Mindestens eine Tabelle ist unanonymisiert geblieben."
        )
