"""Pin-Tests der Prio-1-Survivor-Triage zu ``core.services.file_vault.storage``.

Quelle: ``src/core/services/file_vault/storage.py`` (Vault-Choke-Point,
Refs #1345/#1738/#1960/#2128/#2129 / ADR-039). Gepinnt wird das Prio-1-Set
des Mutations-Deltas v0.96.0: 26 bestaetigte Testluecken in drei Familien.

* **Attributions-Weitergabe der Pre-Encrypt-Pipeline** (21 Mutanten) —
  ``prepare_encrypted_upload`` reicht ``facility``, ``owner`` und ``user`` an
  acht Policy-Gates durch; ``policy.py`` selbst nutzt ``owner``/``user``
  ausschliesslich fuer die Audit-Attribution des Rejects (Modul-Docstring
  ``policy.py``). Faellt eines dieser Argumente auf ``None``, greift der Check
  weiter, aber die ``SECURITY_VIOLATION``- bzw.
  ``ATTACHMENT_QUOTA_EXCEEDED``-Zeile verliert ihren Akteur
  (``AuditLog.user``), ihre Einrichtung (``AuditLog.facility``) oder ihren
  Bezug (``target_id``/``owner_type``/``owner_id``, ADR-039 §5). Die Zeile ist
  prune-exempt und damit der dauerhafte forensische Beleg — die Attribution
  ist Audit-Pflicht. Gepinnt wird deshalb die **Argumentliste**, mit der die
  Pipeline ihre Gates aufruft: das ist genau der mutierte Vertrag, und er ist
  fuer jedes Gate gleich pruefbar, ohne acht verschiedene Reject-Gruende
  konstruieren zu muessen. Bewusst KEIN Mock der Gates: die Spione delegieren
  an die echten Policy-Funktionen, es laeuft die volle echte Pipeline
  (Whitelist, Groesse, Quota, Scan, libmagic, Pixel-Cap, Archiv-Guard,
  Metadaten-Strip, Fernet).
* **``detected_mime``-Durchreichung** (4 der 21 Mutanten, gesondert begruendet)
  — ``enforce_image_limits``/``strip_image_metadata`` bekommen den verifizierten
  libmagic-MIME als fuenftes Argument (#2128, Fix-Runde 1/N-1). Faellt es weg
  oder auf ``None``, entscheidet wieder allein die Dateiendung: der
  Decompression-Bomb-Pixelcap und der EXIF/GPS-Strip greifen fuer Inhalte, deren
  Endungsfamilie nicht passt, nicht mehr. Gepinnt wird daher zusaetzlich die
  **Stelligkeit** des Aufrufs und der uebergebene MIME-Wert.
* **``store_encrypted_file``** (5 Mutanten) — der fail-closed Audit-Default
  (``audit=True``, M4-Entscheid zu #1960), die Owner-Weitergabe in die
  Pre-Encrypt-Pipeline und der Akteur des DB-/Audit-Writes.

Konventionen wie ``src/tests/test_mutation_pins_094_attachments_crud.py``: eine
Testklasse je Funktion; jeder Test nennt seine Mutanten in der Docstring samt
dem Wert, der beim Mutanten abweicht; Zusicherungen vergleichen EXAKT (``==``
bzw. ``is``), nie per Teilstring; keine Fixture-Faken — es laufen echte
Model-Objekte durch den echten File-Vault. Sprachabhaengige Texte kommen nicht
vor (die Datei pinnt Datenfluss, keine Wortlaute).
"""

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

# ``prepare_encrypted_upload`` prueft Magic-Bytes per libmagic; ohne die
# Shared-Library (Host ohne ``libmagic1``) ist die Pipeline nicht lauffaehig.
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffaehig ({_libmagic_exc}) — Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from core.models import AuditLog, FieldTemplate  # noqa: E402
from core.models.attachment import Attachment  # noqa: E402
from core.services.file_vault import prepare_encrypted_upload, store_encrypted_file  # noqa: E402
from core.services.file_vault import storage as vault_storage  # noqa: E402

#: Minimales, gueltiges PDF — die Pipeline prueft Magic-Bytes gegen den
#: deklarierten ``content_type``.
PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)

#: Der von libmagic fuer :data:`PDF_HEADER` verifizierte MIME — genau der Wert,
#: den ``enforce_magic_bytes`` zurueckgibt und den die beiden nachgelagerten
#: Bild-Gates als fuenftes Argument sehen muessen (#2128).
PDF_MIME = "application/pdf"

#: PE-Executable — von der Default-Whitelist abgelehnt (``extension_not_allowed``).
PE_EXECUTABLE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + b"\x00" * 128

#: Die acht Policy-Gates in genau der Reihenfolge, in der
#: ``prepare_encrypted_upload`` sie aufruft (die Reihenfolge selbst ist ueber
#: #1268 zugesichert und hier nicht Gegenstand des Pins).
PIPELINE_GATES = (
    "enforce_allowed_file_types",
    "enforce_upload_size",
    "enforce_storage_quota",
    "run_virus_scan",
    "enforce_magic_bytes",
    "enforce_image_limits",
    "enforce_archive_limits",
    "strip_image_metadata",
)


def _upload(name="beleg.pdf", *, data=PDF_HEADER, content_type="application/pdf"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def _exe_upload():
    return SimpleUploadedFile("trojaner.exe", PE_EXECUTABLE_BYTES, content_type="application/octet-stream")


def _expected_extra(gate):
    """Erwartete Zusatz-Argumente eines Gates hinter ``(facility, file, owner, user)``.

    Rueckgabe ``(positionale Zusatzargumente, Schluesselwortargumente)``.
    """
    if gate == "enforce_storage_quota":
        return (), {"pending_bytes": 0}
    if gate in ("enforce_image_limits", "strip_image_metadata"):
        return (PDF_MIME,), {}
    return (), {}


def _spy_on_gates(monkeypatch):
    """Ersetzt jedes Policy-Gate im ``storage``-Namensraum durch einen Spion.

    Der Spion protokolliert die Argumentliste und delegiert an die echte
    Policy-Funktion — die Pipeline laeuft also unveraendert durch (echter
    libmagic, echter Scan-Pfad, echter Fernet-Write). Gepatcht wird das
    Modul-Attribut von ``core.services.file_vault.storage``, weil genau ueber
    diesen Namen die mutierte Aufrufstelle aufloest.
    """
    calls = {}

    def _make(gate_name, original):
        def spy(*args, **kwargs):
            calls[gate_name] = (args, kwargs)
            return original(*args, **kwargs)

        return spy

    for gate in PIPELINE_GATES:
        monkeypatch.setattr(vault_storage, gate, _make(gate, getattr(vault_storage, gate)))
    return calls


@pytest.fixture
def ft_file(facility):
    """FILE-Feldvorlage fuer den Commit-Pfad."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Beleg (Pins 0.96)",
        field_type=FieldTemplate.FieldType.FILE,
    )


@pytest.fixture
def medienordner(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# prepare_encrypted_upload — Attribution der Pre-Encrypt-Pipeline (21 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key", "medienordner")
class TestPrepareEncryptedUploadAttribution:
    """Jedes Policy-Gate bekommt die VOLLE Attribution ``(facility, owner, user)``.

    ``policy.py`` liest ``owner``/``user`` nie fachlich, sondern reicht sie an
    ``log_attachment_violation``/``audit_attachment_quota_exceeded`` durch. Ein
    auf ``None`` gefallenes Argument aendert deshalb kein Enforcement-Ergebnis
    und faellt keinem Verhaltenstest auf — es entwertet nur die Audit-Zeile
    (ADR-039 §5). Gepinnt wird darum die Aufrufstelle selbst.
    """

    def test_pipeline_gates_werden_in_der_dokumentierten_reihenfolge_aufgerufen(
        self, facility, staff_user, sample_event, monkeypatch
    ):
        """Vorbedingung der Attributions-Pins: alle acht Gates laufen wirklich.

        Ohne diese Zusicherung koennte ein spaeter uebersprungenes Gate die
        parametrisierten Attributions-Pins still gruen halten (das Gate fehlte
        dann schlicht in ``calls``).
        """
        calls = _spy_on_gates(monkeypatch)

        staged = prepare_encrypted_upload(facility, _upload(), staff_user, owner=sample_event)

        assert tuple(calls) == PIPELINE_GATES
        assert staged.detected_mime == PDF_MIME

    @pytest.mark.parametrize("gate", PIPELINE_GATES)
    def test_gate_bekommt_facility_owner_und_user_vollstaendig(
        self, gate, facility, staff_user, sample_event, monkeypatch
    ):
        """Pin: ``x_prepare_encrypted_upload__mutmut_{4,5,12,13,20,21,30,39,40,45,47,48,55,57,58,66,67}``.

        Jeder dieser Mutanten ersetzt an genau einem Gate ``facility``, ``owner``
        oder ``user`` durch ``None``. Erwartet ist die unveraenderte
        Weitergabe der drei Aufrufer-Objekte an JEDES Gate — beim Mutanten steht
        an der jeweiligen Position ``None``.
        """
        calls = _spy_on_gates(monkeypatch)
        upload = _upload()

        prepare_encrypted_upload(facility, upload, staff_user, owner=sample_event)

        args, kwargs = calls[gate]
        assert args[0] is facility
        assert args[1] is upload
        assert args[2] is sample_event
        assert args[3] is staff_user
        assert kwargs == _expected_extra(gate)[1]

    @pytest.mark.parametrize("gate", ("enforce_image_limits", "strip_image_metadata"))
    def test_bildgate_bekommt_den_verifizierten_mime_als_fuenftes_argument(
        self, gate, facility, staff_user, sample_event, monkeypatch
    ):
        """Pin: ``x_prepare_encrypted_upload__mutmut_{49,54,68,73}``.

        ``49``/``68`` ersetzen ``detected_mime`` durch ``None``, ``54``/``73``
        lassen das Argument ganz weg (Signatur-Default). Beides faellt hinter
        den #2128-Fix zurueck: dann entscheidet wieder allein die Dateiendung,
        ob Pixel-Cap und EXIF/GPS-Strip greifen. Erwartet sind fuenf
        positionale Argumente mit dem verifizierten libmagic-MIME an Position 5;
        beim Mutanten sind es vier Argumente bzw. ``None``.
        """
        calls = _spy_on_gates(monkeypatch)

        prepare_encrypted_upload(facility, _upload(), staff_user, owner=sample_event)

        args, _kwargs = calls[gate]
        assert len(args) == 5
        assert args[4] == PDF_MIME

    def test_quota_gate_bekommt_pending_bytes_als_schluesselwort(self, facility, staff_user, sample_event, monkeypatch):
        """Flankierend zu ``mutmut_20``/``mutmut_21``: die Quota-Signatur ist
        die einzige der acht mit Schluesselwortargument (#2129); der Pin haelt
        fest, dass ``pending_bytes`` dort und nur dort ankommt."""
        calls = _spy_on_gates(monkeypatch)

        prepare_encrypted_upload(facility, _upload(), staff_user, owner=sample_event)

        for gate in PIPELINE_GATES:
            assert calls[gate][1] == _expected_extra(gate)[1]


# ---------------------------------------------------------------------------
# store_encrypted_file — Audit-Default, Owner- und Akteurs-Weitergabe (5 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key", "medienordner")
class TestStoreEncryptedFile:
    """Der duenne Wrapper haelt drei Zusagen: fail-closed Audit-Default,
    Owner in der Pre-Encrypt-Pipeline, Akteur am DB-/Audit-Write."""

    def test_default_aufruf_schreibt_die_upload_audit_zeile(self, facility, staff_user, sample_event, ft_file):
        """Pin: ``x_store_encrypted_file__mutmut_1`` (Default ``audit=True`` ->
        ``audit=False``), ``x_store_encrypted_file__mutmut_38``
        (``audit=audit`` -> ``audit=None``, immer falsy) und
        ``x_store_encrypted_file__mutmut_35`` (``user`` -> ``None`` beim Commit).

        Ohne explizites ``audit``-Argument — dem produktiven Aufrufmuster —
        entsteht genau eine ``ATTACHMENT_UPLOAD``-Zeile mit Akteur, Einrichtung
        und Bezug; der Anhang traegt seinen Urheber. Beim Mutanten fehlt die
        Zeile ganz (1/38) bzw. ist ``created_by``/``user`` ``None`` (35).
        """
        attachment = store_encrypted_file(facility, _upload(), ft_file, sample_event, staff_user)

        assert attachment.created_by == staff_user
        logs = AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_UPLOAD)
        assert logs.count() == 1
        log = logs.get()
        assert log.user == staff_user
        assert log.facility == facility
        assert log.target_id == str(sample_event.pk)
        assert log.detail["attachment_id"] == str(attachment.pk)
        assert log.detail["owner_type"] == "event"
        assert log.detail["owner_id"] == str(sample_event.pk)

    def test_explizites_audit_true_schreibt_die_upload_audit_zeile(self, facility, staff_user, sample_event, ft_file):
        """Pin: ``x_store_encrypted_file__mutmut_38`` — ``audit=audit`` ->
        ``audit=None``.

        ``None`` ist falsy: der Schalter stuende dauerhaft auf "aus", auch fuer
        einen Aufrufer, der ``audit=True`` ausdruecklich verlangt. Genau dieser
        Aufruf muss die Zeile erzeugen.
        """
        attachment = store_encrypted_file(facility, _upload(), ft_file, sample_event, staff_user, audit=True)

        logs = AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_UPLOAD)
        assert logs.count() == 1
        assert logs.get().detail["attachment_id"] == str(attachment.pk)

    def test_audit_false_bleibt_der_stille_seed_pfad(self, facility, staff_user, sample_event, ft_file):
        """Gegenprobe zum Default-Pin: ``audit=False`` ist der dokumentierte
        dev/e2e-Seed-Pfad (M4-Entscheid zu #1960) und schreibt bewusst keine
        Zeile. Ohne diese Gegenprobe pinnte der Default-Test nur "irgendwo wird
        auditiert", nicht den Schalter selbst."""
        store_encrypted_file(facility, _upload(), ft_file, sample_event, staff_user, audit=False)

        assert AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_UPLOAD).count() == 0

    def test_owner_erreicht_die_pre_encrypt_pipeline(self, facility, staff_user, sample_event, ft_file):
        """Pin: ``x_store_encrypted_file__mutmut_27`` (``owner=owner`` ->
        ``owner=None``) und ``x_store_encrypted_file__mutmut_31``
        (``owner``-Argument entfaellt, Signatur-Default ``None``).

        Ein abgewiesener Upload laeuft hier durch die ECHTE Pipeline (nicht
        durch den Direktaufruf der Policy-Funktion): die
        ``SECURITY_VIOLATION``-Zeile muss den Bezug tragen, an dem der Upload
        versucht wurde (ADR-039 §5). Beim Mutanten sind ``target_id``,
        ``owner_type`` und ``owner_id`` leer bzw. ``None``.
        """
        with pytest.raises(ValidationError):
            store_encrypted_file(facility, _exe_upload(), ft_file, sample_event, staff_user)

        assert Attachment.objects.filter(event=sample_event).count() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "extension_not_allowed"
        assert log.user == staff_user
        assert log.facility == facility
        assert log.target_id == str(sample_event.pk)
        assert log.detail["owner_type"] == "event"
        assert log.detail["owner_id"] == str(sample_event.pk)
