"""Owner-Generik des File-Vaults (AP2, Refs #1738).

Seit ADR-039 haengt ein ``Attachment`` an genau EINEM Owner (Ereignis, Person
oder Fall) und traegt eine eigene ``facility``. Der Sicherheitskontrakt aus
ADR-039 §2 lautet: die ``facility`` wird IMMER aus ``owner.facility``
abgeleitet, nie als freier Parameter uebernommen. Diese Datei nagelt genau das
fest — fuer alle drei Owner-Typen — plus den Speicherpfad, der fuer
Ereignis-Anhaenge bitgleich bleiben MUSS (keine Datei-Moves beim Umbau).
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

# ``prepare_encrypted_upload`` ruft seit #610 libmagic fuer die
# Magic-Bytes-Pruefung auf — ohne die Shared-Library nicht lauffaehig.
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffähig ({_libmagic_exc}) — Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from core.models import Event, FieldTemplate  # noqa: E402
from core.models.attachment import Attachment  # noqa: E402
from core.services.file_vault import (  # noqa: E402
    commit_staged_upload,
    delete_attachments_for,
    get_attachment_path,
    get_current_entries_for_field,
    prepare_encrypted_upload,
    soft_delete_attachment_chain,
    store_encrypted_file,
)
from core.services.file_vault.storage import _facility_dir  # noqa: E402

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


@pytest.fixture
def ft_file(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Beleg (owner-generisch)",
        field_type=FieldTemplate.FieldType.FILE,
    )


def _stage(facility, user, name="beleg.pdf"):
    """Eine verschluesselte, noch nicht persistierte Datei erzeugen."""
    upload = SimpleUploadedFile(name, PDF_HEADER, content_type="application/pdf")
    return prepare_encrypted_upload(facility, upload, user)


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFacilityAbleitungAusDemOwner:
    """ADR-039 §2: ``facility`` kommt aus dem Owner — fuer jeden Owner-Typ."""

    def test_ereignis_owner_erbt_die_facility(self, facility, staff_user, sample_event, ft_file):
        staged = _stage(facility, staff_user)
        attachment = commit_staged_upload(sample_event, ft_file, staged, staff_user)
        assert attachment.facility_id == sample_event.facility_id
        assert attachment.owner_type == "event"
        assert attachment.owner_id == sample_event.pk

    def test_personen_owner_erbt_die_facility(self, facility, staff_user, client_identified, ft_file):
        staged = _stage(facility, staff_user)
        attachment = commit_staged_upload(client_identified, ft_file, staged, staff_user)
        assert attachment.facility_id == client_identified.facility_id
        assert attachment.owner_type == "client"
        assert attachment.owner_id == client_identified.pk
        assert attachment.event_id is None

    def test_fall_owner_erbt_die_facility(self, facility, staff_user, case_open, ft_file):
        staged = _stage(facility, staff_user)
        attachment = commit_staged_upload(case_open, ft_file, staged, staff_user)
        assert attachment.facility_id == case_open.facility_id
        assert attachment.owner_type == "case"
        assert attachment.owner_id == case_open.pk
        assert attachment.event_id is None

    def test_fremder_owner_typ_wird_abgewiesen(self, facility, staff_user, ft_file, settings_obj):
        """``owner_field_name`` ist fail-loud — kein stilles Fehlscoping."""
        staged = _stage(facility, staff_user)
        with pytest.raises(ValueError, match="Kein zulaessiger Attachment-Owner"):
            commit_staged_upload(settings_obj, ft_file, staged, staff_user)


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFacilityMismatchGuard:
    """``store_encrypted_file`` bekommt ``facility`` UND ``owner`` — beide
    muessen zusammenpassen, sonst laege der Anhang in einem fremden
    Facility-Verzeichnis (Pfad) waehrend die Zeile zur Owner-Facility gehoert
    (RLS). Fail-loud statt stiller Divergenz."""

    def test_fremde_facility_wirft_value_error(self, facility, second_facility, staff_user, sample_event, ft_file):
        upload = SimpleUploadedFile("fremd.pdf", PDF_HEADER, content_type="application/pdf")
        with pytest.raises(ValueError, match="Facility"):
            store_encrypted_file(second_facility, upload, ft_file, sample_event, staff_user)
        assert Attachment.objects.filter(event=sample_event).count() == 0

    def test_passende_facility_bleibt_erlaubt(self, facility, staff_user, sample_event, ft_file):
        upload = SimpleUploadedFile("passend.pdf", PDF_HEADER, content_type="application/pdf")
        attachment = store_encrypted_file(facility, upload, ft_file, sample_event, staff_user)
        assert attachment.facility_id == facility.pk


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestSpeicherpfad:
    """G2-Befund: ``get_attachment_path`` lief ueber ``attachment.event.facility``
    und waere fuer personen-/falleigene Zeilen ein ``AttributeError`` gewesen."""

    def test_pfad_bleibt_bitgleich_fuer_ereignis_anhaenge(self, facility, staff_user, sample_event, ft_file):
        staged = _stage(facility, staff_user)
        attachment = commit_staged_upload(sample_event, ft_file, staged, staff_user)
        # Bitgleich zum Bestandsverhalten: Verzeichnis = Facility des Ereignisses.
        assert get_attachment_path(attachment).parent == _facility_dir(attachment.event.facility)
        assert get_attachment_path(attachment).name == attachment.storage_filename

    def test_pfad_funktioniert_ohne_ereignis(self, facility, staff_user, client_identified, ft_file):
        staged = _stage(facility, staff_user)
        attachment = commit_staged_upload(client_identified, ft_file, staged, staff_user)
        assert attachment.event_id is None
        assert get_attachment_path(attachment).parent == _facility_dir(facility)
        assert get_attachment_path(attachment).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestOwnerGenerischeKettenHelfer:
    """``soft_delete_attachment_chain``/``get_current_entries_for_field``/
    ``delete_attachments_for`` laufen ueber den geteilten Reverse-Accessor
    ``owner.attachments`` und funktionieren damit fuer alle drei Owner."""

    def test_aktuelle_eintraege_einer_person(self, facility, staff_user, client_identified, ft_file):
        staged_first = _stage(facility, staff_user)
        staged_second = _stage(facility, staff_user)
        first = commit_staged_upload(client_identified, ft_file, staged_first, staff_user, sort_order=0)
        second = commit_staged_upload(client_identified, ft_file, staged_second, staff_user, sort_order=1)
        entries = get_current_entries_for_field(client_identified, ft_file)
        assert [a.pk for a in entries] == [first.pk, second.pk]

    def test_soft_delete_der_kette_einer_person(self, facility, staff_user, client_identified, ft_file):
        attachment = commit_staged_upload(client_identified, ft_file, _stage(facility, staff_user), staff_user)
        updated = soft_delete_attachment_chain(client_identified, attachment.entry_id, staff_user)
        assert updated == 1
        attachment.refresh_from_db()
        assert attachment.deleted_at is not None
        assert get_current_entries_for_field(client_identified, ft_file) == []

    def test_delete_attachments_for_loescht_personen_anhaenge(self, facility, staff_user, client_identified, ft_file):
        attachment = commit_staged_upload(client_identified, ft_file, _stage(facility, staff_user), staff_user)
        path = get_attachment_path(attachment)
        assert path.exists()
        delete_attachments_for(client_identified)
        assert not path.exists()
        assert Attachment.objects.filter(client=client_identified).count() == 0

    def test_delete_attachments_for_loescht_ereignis_anhaenge(self, facility, staff_user, sample_event, ft_file):
        attachment = commit_staged_upload(sample_event, ft_file, _stage(facility, staff_user), staff_user)
        path = get_attachment_path(attachment)
        delete_attachments_for(sample_event)
        assert not path.exists()
        assert Attachment.objects.filter(event=sample_event).count() == 0

    def test_ketten_helfer_scopen_auf_den_eigenen_owner(
        self, facility, staff_user, client_identified, case_open, ft_file
    ):
        """Ein Anhang der Person taucht nicht bei ihrem Fall auf (und umgekehrt)."""
        commit_staged_upload(client_identified, ft_file, _stage(facility, staff_user), staff_user)
        assert get_current_entries_for_field(case_open, ft_file) == []
        assert len(get_current_entries_for_field(client_identified, ft_file)) == 1


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestAuditAttributionDesOwners:
    """``log_attachment_violation`` traegt den Owner im Detail-Schema
    (``owner_type``/``owner_id`` statt ``event_id``, ADR-039 §5)."""

    def test_detail_traegt_owner_typ_und_id(self, facility, staff_user, client_identified):
        from core.services.file_vault.audit import log_attachment_violation

        entry = log_attachment_violation(
            facility,
            staff_user,
            client_identified,
            reason="extension_not_allowed",
            filename="schaden.exe",
        )
        assert entry.target_type == "Attachment"
        assert entry.target_id == str(client_identified.pk)
        assert entry.detail["owner_type"] == "client"
        assert entry.detail["owner_id"] == str(client_identified.pk)

    def test_detail_bleibt_ohne_owner_stabil(self, facility, staff_user):
        from core.services.file_vault.audit import log_attachment_violation

        entry = log_attachment_violation(
            facility,
            staff_user,
            None,
            reason="virus_detected",
            filename="virus.pdf",
        )
        assert entry.target_type == "Attachment"
        assert entry.target_id in ("", None)
        assert entry.detail["owner_type"] is None
        assert entry.detail["owner_id"] is None

    def test_ereignis_owner_wird_als_event_ausgewiesen(self, facility, staff_user, sample_event):
        from core.services.file_vault.audit import log_attachment_violation

        entry = log_attachment_violation(
            facility,
            staff_user,
            sample_event,
            reason="mime_mismatch",
            filename="bericht.pdf",
        )
        assert entry.detail["owner_type"] == "event"
        assert entry.detail["owner_id"] == str(sample_event.pk)
        assert Event.objects.filter(pk=sample_event.pk).exists()
