"""Der Ersatz-Upload lehnt einen Vorgaenger eines fremden Owners ab (Refs #2307).

``commit_staged_upload(owner, …, supersedes=<Attachment>)`` sperrt die
abzuloesende Zeile owner-gescopt (``owner.attachments.select_for_update()``).
Gehoert der uebergebene Vorgaenger einem ANDEREN Owner — derselben oder einer
anderen Einrichtung —, trifft diese Vorsperre keine Zeile. Frueher lief der
Upload trotzdem weiter, nahm den Ketten-Advisory-Lock der Einrichtung und
schrieb ``is_current``/``superseded_by``/``superseded_at`` auf das FREMDE
Objekt: erstmalige Zeilensperre unter dem Ketten-Lock (Gegenrichtung zur
verbindlichen Sperr-Reihenfolge) und eine Versionskette ueber Owner-Grenzen.

Die Zugehoerigkeit haengt deshalb an der TATSAECHLICH gesperrten Zeile: greift
die Vorsperre ins Leere, bricht der Upload VOR dem Ketten-Lock kontrolliert ab
— ohne DB-Schreibvorgang, ohne Datei, ohne Rechenschaftszeile.

Kein View uebergibt heute ein fremdes ``supersedes``-Objekt (die Views laden
den Vorgaenger ueber den Owner); die Zusage gilt dem Service-Layer und seinen
direkten Aufrufern.
"""

import contextlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

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

from core.models import AuditLog, Client, FieldTemplate  # noqa: E402
from core.models.attachment import Attachment  # noqa: E402
from core.services.file_vault.storage import commit_staged_upload, prepare_encrypted_upload  # noqa: E402

#: Der Guard laeuft ueber die Meldung und ueber ``ValueError`` — nicht ueber
#: einen Import der konkreten Klasse: sonst waere der Rot-Lauf vor dem Fix ein
#: Importfehler des Moduls statt des ausbleibenden Abbruchs, den er belegen soll.
ABLEHNUNG = "gehoert nicht zum Owner"

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
        name="Beleg (Ersatz-Guard)",
        field_type=FieldTemplate.FieldType.FILE,
    )


@pytest.fixture
def ft_file_zweite(second_facility):
    return FieldTemplate.objects.create(
        facility=second_facility,
        name="Beleg (Ersatz-Guard, zweite Stelle)",
        field_type=FieldTemplate.FieldType.FILE,
    )


@pytest.fixture
def client_zweite_stelle(second_facility, second_facility_user):
    return Client.objects.create(
        facility=second_facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Zweite-ID-01",
        created_by=second_facility_user,
    )


def _stage(owner, user, name="beleg.pdf"):
    """Eine verschluesselte, noch nicht persistierte Datei des Owners erzeugen."""
    upload = SimpleUploadedFile(name, PDF_HEADER, content_type="application/pdf")
    return prepare_encrypted_upload(owner.facility, upload, user, owner=owner)


def _anhang(owner, field_template, user, name="erste.pdf"):
    """Eine erste Version am Owner persistieren (Add-Modus)."""
    return commit_staged_upload(owner, field_template, _stage(owner, user, name), user)


@contextlib.contextmanager
def _ketten_lock_mitschnitt():
    """Protokolliert jeden ``pg_advisory_xact_lock``-Aufruf der Verbindung.

    Der Ketten-Advisory-Lock der Einrichtung (Kontingent-Backstop = Audit-
    Hashkette, EIN Key) ist die Sperre, hinter die keine erstmalige
    Zeilensperre fallen darf. Der Mitschnitt haengt sich vor jede Anweisung
    und ist damit unabhaengig davon, welche Funktion den Lock nimmt.
    """
    aufrufe: list[str] = []

    def wrapper(execute, sql, params, many, context):
        if "pg_advisory_xact_lock" in sql:
            aufrufe.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(wrapper):
        yield aufrufe


def _unveraendert(vorgaenger, *, vorher_aktuell=True):
    """Der Vorgaenger traegt keine Ersetzungs-Spuren."""
    vorgaenger.refresh_from_db()
    assert vorgaenger.is_current is vorher_aktuell
    assert vorgaenger.superseded_by_id is None
    assert vorgaenger.superseded_at is None


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFremderVorgaengerWirdAbgelehnt:
    """Ein ``supersedes``-Objekt, das dem Owner nicht gehoert, wird abgewiesen."""

    def test_vorgaenger_eines_anderen_owners_derselben_einrichtung(
        self, staff_user, client_identified, client_qualified, ft_file
    ):
        """Gleiche Einrichtung, anderer Owner — die Vorsperre trifft nichts."""
        fremder = _anhang(client_qualified, ft_file, staff_user)
        anhaenge_vorher = Attachment.objects.count()
        audit_vorher = AuditLog.objects.count()
        staged = _stage(client_identified, staff_user, "zweite.pdf")

        with pytest.raises(ValueError, match=ABLEHNUNG):
            commit_staged_upload(client_identified, ft_file, staged, staff_user, supersedes=fremder)

        _unveraendert(fremder)
        assert Attachment.objects.count() == anhaenge_vorher
        assert AuditLog.objects.count() == audit_vorher
        assert not staged.output_path.exists()

    def test_vorgaenger_einer_anderen_einrichtung(
        self,
        staff_user,
        second_facility_user,
        client_identified,
        client_zweite_stelle,
        ft_file,
        ft_file_zweite,
    ):
        """Andere Einrichtung — die Kette darf die Einrichtungsgrenze nicht queren."""
        fremder = _anhang(client_zweite_stelle, ft_file_zweite, second_facility_user)
        anhaenge_vorher = Attachment.objects.count()
        audit_vorher = AuditLog.objects.count()
        staged = _stage(client_identified, staff_user, "zweite.pdf")

        with pytest.raises(ValueError, match=ABLEHNUNG):
            commit_staged_upload(client_identified, ft_file, staged, staff_user, supersedes=fremder)

        _unveraendert(fremder)
        assert Attachment.objects.count() == anhaenge_vorher
        assert AuditLog.objects.count() == audit_vorher
        assert not staged.output_path.exists()

    def test_die_ausnahme_traegt_eine_eigene_klasse(self, staff_user, client_identified, client_qualified, ft_file):
        """Eigene Klasse fuer die gezielte Behandlung, ``ValueError`` als Basis —
        Bestandsaufrufer, die den fail-loud-``ValueError`` des Owner-Dispatchs
        abfangen, sehen die Ablehnung damit unveraendert."""
        from core.services.file_vault.storage import SupersedesOwnerMismatchError

        assert issubclass(SupersedesOwnerMismatchError, ValueError)

        fremder = _anhang(client_qualified, ft_file, staff_user)
        staged = _stage(client_identified, staff_user, "zweite.pdf")

        with pytest.raises(SupersedesOwnerMismatchError):
            commit_staged_upload(client_identified, ft_file, staged, staff_user, supersedes=fremder)


@pytest.mark.postgres
@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestSperrReihenfolgeBeiDerAblehnung:
    """Die Ablehnung faellt VOR den Ketten-Advisory-Lock der Einrichtung."""

    def test_ablehnung_nimmt_keinen_ketten_lock(self, staff_user, client_identified, client_qualified, ft_file):
        fremder = _anhang(client_qualified, ft_file, staff_user)
        staged = _stage(client_identified, staff_user, "zweite.pdf")

        with _ketten_lock_mitschnitt() as aufrufe:
            with pytest.raises(ValueError, match=ABLEHNUNG):
                commit_staged_upload(client_identified, ft_file, staged, staff_user, supersedes=fremder)

        assert aufrufe == []

    def test_der_regulaere_ersatz_nimmt_den_ketten_lock(self, staff_user, client_identified, ft_file):
        """Gegenprobe: der Mitschnitt sieht den Lock, wenn er genommen wird —
        der leere Mitschnitt oben ist also kein blinder Beobachter."""
        eigener = _anhang(client_identified, ft_file, staff_user)
        staged = _stage(client_identified, staff_user, "zweite.pdf")

        with _ketten_lock_mitschnitt() as aufrufe:
            commit_staged_upload(client_identified, ft_file, staged, staff_user, supersedes=eigener)

        assert aufrufe != []


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestEigenerVorgaengerBleibtUnveraendert:
    """Positivfall: der Replace-Pfad des Owners verhaelt sich wie bisher."""

    def test_eigener_vorgaenger_wird_ersetzt(self, staff_user, client_identified, ft_file):
        eigener = _anhang(client_identified, ft_file, staff_user)

        neu = commit_staged_upload(
            client_identified,
            ft_file,
            _stage(client_identified, staff_user, "zweite.pdf"),
            staff_user,
            supersedes=eigener,
        )

        eigener.refresh_from_db()
        assert eigener.is_current is False
        assert eigener.superseded_by_id == neu.pk
        assert eigener.superseded_at is not None
        assert neu.is_current is True
        assert neu.entry_id == eigener.entry_id
        assert neu.sort_order == eigener.sort_order
        assert neu.owner_id == client_identified.pk
        assert AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_UPLOAD).count() == 2
