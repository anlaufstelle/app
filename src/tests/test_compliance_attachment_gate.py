"""Sichtbarkeits-Gate fuer Anhaenge an beliebigen Ownern (M24/AP2, Refs #1738).

``get_visible_attachment_for_owner_or_404`` ist die owner-generische Stufe 3+4
des fuenfstufigen Download-Gates aus ADR-039 §12: Der Aufrufer hat den Owner
bereits autorisiert geladen (Stufen 1+2 — facility-Scope, Soft-Delete,
Doc-Type-Sensitivity → 404); dieser Helfer prueft nur noch, dass der Anhang zu
GENAU diesem Owner gehoert und nicht soft-geloescht ist (→ 404), und gated die
Feld-Sensitivity (→ 403).

Die Antwortkategorien sind bewusst verschieden: 404 verrät nichts ueber die
Existenz, 403 sagt „gibt es, darfst du nicht" — das ist an dieser Stelle
zulaessig, weil die Zusatzfelder-Sektion selbst schon durch das
Doc-Type-Gate geschuetzt ist.
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404

try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffähig ({_libmagic_exc}) — Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from django.utils import timezone  # noqa: E402

from core.models import DocumentType, FieldTemplate  # noqa: E402
from core.services.compliance.sensitivity import (  # noqa: E402
    get_visible_attachment_for_owner_or_404,
    get_visible_attachment_or_404,
)
from core.services.file_vault import commit_staged_upload, prepare_encrypted_upload  # noqa: E402

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _file_template(facility, *, sensitivity="", name="Beleg (Gate)"):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.FILE,
        sensitivity=sensitivity,
    )


def _attach(owner, ft, user):
    upload = SimpleUploadedFile("gate.pdf", PDF_HEADER, content_type="application/pdf")
    staged = prepare_encrypted_upload(owner.facility, upload, user, owner=owner)
    return commit_staged_upload(owner, ft, staged, user)


@pytest.fixture
def client_with_type(client_identified, doc_type_client_profile):
    client_identified.document_type = doc_type_client_profile
    client_identified.save(update_fields=["document_type"])
    return client_identified


@pytest.fixture
def case_with_type(case_open, doc_type_case_form):
    case_open.document_type = doc_type_case_form
    case_open.save(update_fields=["document_type"])
    return case_open


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestOwnerGenerischesGate:
    def test_personen_anhang_wird_geliefert(self, facility, staff_user, client_with_type):
        ft = _file_template(facility)
        attachment = _attach(client_with_type, ft, staff_user)
        assert get_visible_attachment_for_owner_or_404(staff_user, client_with_type, attachment.pk) == attachment

    def test_fall_anhang_wird_geliefert(self, facility, staff_user, case_with_type):
        ft = _file_template(facility)
        attachment = _attach(case_with_type, ft, staff_user)
        assert get_visible_attachment_for_owner_or_404(staff_user, case_with_type, attachment.pk) == attachment

    def test_soft_geloeschter_anhang_ist_404(self, facility, staff_user, client_with_type):
        ft = _file_template(facility)
        attachment = _attach(client_with_type, ft, staff_user)
        attachment.deleted_at = timezone.now()
        attachment.save(update_fields=["deleted_at"])
        with pytest.raises(Http404):
            get_visible_attachment_for_owner_or_404(staff_user, client_with_type, attachment.pk)

    def test_anhang_eines_fremden_owners_ist_404(self, facility, staff_user, client_with_type, case_with_type):
        """IDOR-Probe: eine gueltige Anhang-PK am falschen Owner darf nichts liefern."""
        ft = _file_template(facility)
        attachment = _attach(client_with_type, ft, staff_user)
        with pytest.raises(Http404):
            get_visible_attachment_for_owner_or_404(staff_user, case_with_type, attachment.pk)

    def test_feld_sensitivity_fuehrt_zu_403(self, facility, assistant_user, lead_user, staff_user, client_with_type):
        ft = _file_template(facility, sensitivity=DocumentType.Sensitivity.HIGH)
        attachment = _attach(client_with_type, ft, staff_user)
        for blocked in (assistant_user, staff_user):
            with pytest.raises(PermissionDenied):
                get_visible_attachment_for_owner_or_404(blocked, client_with_type, attachment.pk)
        # Leitung darf HIGH sehen — dasselbe Objekt kommt zurueck.
        assert get_visible_attachment_for_owner_or_404(lead_user, client_with_type, attachment.pk) == attachment

    def test_owner_ohne_dokumentationstyp_ist_404(self, facility, staff_user, client_identified):
        """Fail-closed (ADR-039 §12): ohne Typ gibt es keine Bezugs-Sensitivity —
        dann darf der Anhang gar nicht erst ausgeliefert werden."""
        ft = _file_template(facility)
        attachment = _attach(client_identified, ft, staff_user)
        assert client_identified.document_type_id is None
        with pytest.raises(Http404):
            get_visible_attachment_for_owner_or_404(staff_user, client_identified, attachment.pk)


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestBestandsfunktionDelegiert:
    """``get_visible_attachment_or_404`` (Event-Pfad) delegiert nach dem
    Event-Lookup an den neuen Helfer — wirkungsgleich fuer den Download-View."""

    def test_liefert_weiterhin_das_paar_event_anhang(self, facility, staff_user, sample_event):
        ft = _file_template(facility)
        attachment = _attach(sample_event, ft, staff_user)
        found_event, found_attachment = get_visible_attachment_or_404(
            staff_user, facility, sample_event.pk, attachment.pk
        )
        assert found_event == sample_event
        assert found_attachment == attachment

    def test_feld_sensitivity_wird_jetzt_zentral_gegatet(self, facility, assistant_user, staff_user, sample_event):
        """Vorher prueften Helfer und View die Feld-Sensitivity getrennt (der
        View hob den 403). Jetzt haelt der Helfer das Gate — die HTTP-Antwort
        des Download-Views bleibt unveraendert 403."""
        ft = _file_template(facility, sensitivity=DocumentType.Sensitivity.HIGH)
        attachment = _attach(sample_event, ft, staff_user)
        with pytest.raises(PermissionDenied):
            get_visible_attachment_or_404(assistant_user, facility, sample_event.pk, attachment.pk)


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestUnbekannteSensitivityFailClosed:
    """Unbekannte Sensitivity-Werte verstecken auf BEIDEN Wahrheiten (Refs #1991).

    Fuer Anhaenge gibt es zwei Sichtbarkeits-Wahrheiten: die Uebersichts-Query
    (:func:`visible_attachments_for_user`) und das Download-/Detail-Gate ueber
    :func:`effective_sensitivity`. Die Query war seit ADR-039 §12 fail-closed
    (der Sentinel-Wert kommt in KEINER ``allowed``-Liste vor, die Zeile faellt
    fuer jede Rolle heraus), das Gate rechnete dagegen mit
    ``SENSITIVITY_RANK.get(value, 0)`` — ein unbekannter Wert landete auf Rang
    0 und war damit wie ``normal`` fuer alle sichtbar. Ein neuer, per Migration
    oder Paket eingefuehrter Wert (oder eine korrupte Zeile) haette die Datei
    aus der Liste genommen, per Direkt-URL aber ausgeliefert.

    Ein unbekannter Wert ist hier jeder Wert ausserhalb von
    ``DocumentType.Sensitivity`` — ``""`` NICHT: der leere Feld-Wert heisst
    „erbt vom Dokumentationstyp" und behaelt diese Bedeutung.
    """

    UNBEKANNT = "kritisch"

    @staticmethod
    def _force_sensitivity(instance, value):
        """Setzt einen Wert ausserhalb der ``choices`` an der DB vorbei am Formular.

        ``choices`` sind eine Formular-/Validierungs-Regel, keine
        DB-Beschraenkung — genau deshalb ist der Fall ueberhaupt erreichbar.
        """
        type(instance).objects.filter(pk=instance.pk).update(sensitivity=value)
        instance.refresh_from_db()
        return instance

    def _visible_pks(self, facility, user):
        from core.services.compliance.sensitivity import visible_attachments_for_user

        return set(visible_attachments_for_user(facility, user).values_list("pk", flat=True))

    def test_unbekannte_doctype_sensitivity_versteckt_in_liste_und_im_download(
        self, facility, lead_user, staff_user, client_with_type
    ):
        ft = _file_template(facility)
        attachment = _attach(client_with_type, ft, staff_user)
        self._force_sensitivity(client_with_type.document_type, self.UNBEKANNT)

        assert attachment.pk not in self._visible_pks(facility, lead_user)
        with pytest.raises(PermissionDenied):
            get_visible_attachment_for_owner_or_404(lead_user, client_with_type, attachment.pk)

    def test_unbekannte_feld_sensitivity_versteckt_in_liste_und_im_download(
        self, facility, lead_user, staff_user, client_with_type
    ):
        ft = _file_template(facility)
        attachment = _attach(client_with_type, ft, staff_user)
        self._force_sensitivity(ft, self.UNBEKANNT)

        assert attachment.pk not in self._visible_pks(facility, lead_user)
        with pytest.raises(PermissionDenied):
            get_visible_attachment_for_owner_or_404(lead_user, client_with_type, attachment.pk)

    def test_leere_feld_sensitivity_bleibt_vererbung_vom_dokumentationstyp(
        self, facility, assistant_user, lead_user, staff_user, client_with_type
    ):
        """Regressionsprobe zur Gegenrichtung: ``""`` ist KEIN unbekannter Wert.

        Der leere Feld-Wert erbt die Sensitivity des Dokumentationstyps — die
        Datei bleibt fuer jede Rolle sichtbar, die den Typ sehen darf, und
        verschwindet mit ihm, wenn der Typ hochgestuft wird.
        """
        ft = _file_template(facility, sensitivity="")
        attachment = _attach(client_with_type, ft, staff_user)
        assert client_with_type.document_type.sensitivity == DocumentType.Sensitivity.NORMAL

        assert attachment.pk in self._visible_pks(facility, assistant_user)
        assert get_visible_attachment_for_owner_or_404(assistant_user, client_with_type, attachment.pk) == attachment

        self._force_sensitivity(client_with_type.document_type, DocumentType.Sensitivity.HIGH)
        assert attachment.pk not in self._visible_pks(facility, assistant_user)
        with pytest.raises(PermissionDenied):
            get_visible_attachment_for_owner_or_404(assistant_user, client_with_type, attachment.pk)
        assert attachment.pk in self._visible_pks(facility, lead_user)
        assert get_visible_attachment_for_owner_or_404(lead_user, client_with_type, attachment.pk) == attachment
