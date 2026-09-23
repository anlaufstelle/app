"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.attachments.crud``
und ``core.services.attachments.context``, Batch ``attachments-1``.

Quelle: Vollmutation #2174, Batch ``attachments-1``
(``welle-l-0.99/reports/m-scratch/batch-attachments-1.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-attachments-1/triage-099-attachments-1.json``).
Der Batch hat 55 Survivor-Mutanten in der Datei-Loeschkette/Versionierung der
Anhang-Domaene: ``crud.py`` (Staging, REMOVE/REPLACE/ADD, ersatzloses Loeschen
einer Kette, Typwechsel-Verwurf) und ``context.py`` (read-only Anzeige-Kontexte,
Vorversionen-Kette, Download-URL-Aufloesung). 42 sind Testluecken (``test``),
13 sind ``equivalent`` (Begruendung jeweils im Test-Docstring bzw. in der
Triage-Datei — kein Fix, kein Pin).

Gruppierung der gepinnten Tests (Funktion -> Mutanten):

* ``finalize_staged_files`` — ``or``/``and``-Verwechslung im Leer-Guard
  (1 Mutante, ``TestFinalizeStagedFilesEmptyListSkipsMarker``).
* ``stage_attachment_uploads``/``apply_attachment_changes`` — drei
  unabhaengige ``unlink(missing_ok=True)``-Aufraeumstellen verlieren ihr
  ``missing_ok`` (6 Mutanten, ueber ``monkeypatch`` auf ``pathlib.Path.unlink``
  als Spion nachgewiesen statt ueber eine echte Race).
* ``_apply_replace`` — der persistierte Marker-Schluessel ``"sort"`` wird
  umbenannt (2 Mutanten, Datenintegritaet des gespeicherten ``data_json``).
* ``_apply_add`` — ``sort_order`` beim Persistieren einer neu hinzugefuegten
  Datei geht verloren (2 Mutanten, kollidierende ``sort_order``-Werte).
* ``_drop_purged_marker_entries`` — Rueckgabewert-Identitaet ``False`` (2
  Mutanten).
* ``_lock_owner_row`` — der Client-Kurzschluss verliert seinen exakten
  Klassennamen-Vergleich (3 Mutanten, per Abfragezahl nachgewiesen).
* ``purge_attachment_chain`` — Meldungsverlust/-Wortlaut an ZWEI Guards (Art.
  18 DSGVO, Legal Hold; 9 Mutanten, je EIN Exact-Match-Pin je Meldung).
* ``build_prior_versions`` — Zyklus-Waechter (1 Mutante, mit Deadline-Pin),
  Ketten-Abbruch nach der ersten Vorversion (2 Mutanten), Anzeige-Schluessel
  (4 Mutanten).
* ``resolve_file_entries``/``build_attachment_context`` — ``continue`` wird zu
  ``break`` an drei bzw. zwei Fehlerpfaden (5 Mutanten, je ein
  Zwei-Eintraege-Szenario: kaputter/fremder/geloeschter Eintrag VOR einem
  gueltigen), Anzeige-Schluessel (4 Mutanten).
* ``attachment_download_url`` — Meldungsverlust (1 Mutante).

``_encryption_key`` liegt modulweit an: der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``; ohne die Fixture waeren die Pins dort aus dem FALSCHEN
Grund rot (Original-Dateiname/-Attribute werden verschluesselt gespeichert).

Fuenf ``continue``->``break``- und drei ``missing_ok``-Mutanten je Gruppe
teilen sich EINEN Pin (siehe Docstrings der einzelnen Tests fuer die exakte
Mutanten-Liste je Test).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, LegalHold
from core.models.attachment import Attachment
from core.services.attachments import (
    apply_attachment_changes,
    build_attachment_context,
    build_prior_versions,
    finalize_staged_files,
    purge_attachment_chain,
    resolve_file_entries,
    stage_attachment_uploads,
)
from core.services.attachments.context import attachment_download_url
from core.services.attachments.crud import (
    _apply_add,  # noqa: F401 -- fuer Lesbarkeit im Docstring referenziert
    _drop_purged_marker_entries,
    _lock_owner_row,
    replace_upload_key,
)
from core.services.file_vault import commit_staged_upload, prepare_encrypted_upload
from core.utils.formatting import format_file_size

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

#: Minimales, gueltiges PDF — ``prepare_encrypted_upload`` prueft Magic-Bytes.
PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _upload(name="beleg.pdf"):
    return SimpleUploadedFile(name, PDF_HEADER, content_type="application/pdf")


def _attach(owner, ft, user, *, name="beleg.pdf", sort_order=None):
    staged = prepare_encrypted_upload(owner.facility, _upload(name), user, owner=owner)
    return commit_staged_upload(owner, ft, staged, user, sort_order=sort_order)


class _FakeStaged:
    """``StagedUpload``-Double mit fester Groesse (Muster ``test_mutation_pins_096_attachments_crud.py``).

    ``output_path`` zeigt bewusst auf eine NIEMALS geschriebene Datei — die
    ``missing_ok``-Pins pruefen nur, welcher Wert an ``unlink`` uebergeben
    wird (per Spion), nicht das tatsaechliche Aufraeum-Ergebnis auf der
    Platte.
    """

    FILE_SIZE = 1000

    def __init__(self):
        self.file_size = self.FILE_SIZE
        self.output_path = Path("/nicht-vorhanden-pins-attachments-1.enc")
        self.storage_name = "nicht-verwendet.enc"
        self.detected_mime = "application/pdf"
        self.content_type = "application/pdf"
        self.original_name = "nicht-verwendet.pdf"


def _unlink_spy(monkeypatch):
    """Ersetzt ``pathlib.Path.unlink`` durch einen Rekorder, der NICHT
    durchreicht (die Fake-Pfade existieren ohnehin nicht) und die
    ``missing_ok``-Werte in Aufrufreihenfolge sammelt."""
    calls: list = []

    def spy(self, missing_ok=False):
        calls.append(missing_ok)

    monkeypatch.setattr(Path, "unlink", spy)
    return calls


@pytest.fixture
def medienordner(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def ft_file(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Beleg (Pins 0.99 attachments-1)",
        field_type=FieldTemplate.FieldType.FILE,
    )


@pytest.fixture
def dt_file(facility, ft_file):
    dt = DocumentType.objects.create(
        facility=facility,
        name="Pins 0.99 attachments-1 — Ereignistyp",
        entity_type=DocumentType.EntityType.EVENT,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    return dt


@pytest.fixture
def file_event(facility, client_identified, dt_file, staff_user):
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=dt_file,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )


# ---------------------------------------------------------------------------
# finalize_staged_files
# ---------------------------------------------------------------------------


class TestFinalizeStagedFilesEmptyListSkipsMarker:
    def test_leerer_staged_file_zweig_schreibt_keinen_geister_marker(self, staff_user, file_event, ft_file):
        """Pin: ``x_finalize_staged_files__mutmut_6`` (``if not ft or not
        staged_list`` -> ``and``). Bei vorhandenem FieldTemplate, aber leerer
        Upload-Liste ueberspringt das Original den Slug komplett; die Mutante
        faellt durch und schreibt einen Marker mit ``entries: []`` ohne
        zugehoerige Attachment-Zeile."""
        finalize_staged_files(file_event, staff_user, {ft_file.slug: []}, file_event.document_type)

        file_event.refresh_from_db()
        assert ft_file.slug not in (file_event.data_json or {})


# ---------------------------------------------------------------------------
# stage_attachment_uploads / apply_attachment_changes — missing_ok=True-Pins
# ---------------------------------------------------------------------------


class TestStageAttachmentUploadsCleanupUsesMissingOkTrue:
    def test_scan_fehler_raeumt_bereits_gestagte_dateien_mit_missing_ok_true_auf(
        self, monkeypatch, staff_user, file_event, ft_file, dt_file, medienordner
    ):
        """Pins ``x_stage_attachment_uploads__mutmut_68`` (``missing_ok=None``)
        und ``…_69`` (``missing_ok=False``) im except-Cleanup. Nach einem
        Scan-/Encrypt-Fehler des ZWEITEN Uploads raeumt das Original die
        bereits gestagte erste Datei mit ``missing_ok=True`` auf — nur so
        ueberlebt der Aufraeum-Schritt eine (aus welchem Grund auch immer)
        bereits fehlende ``.enc``-Datei, ohne die urspruengliche Exception zu
        verdecken."""
        calls = _unlink_spy(monkeypatch)
        erster = _FakeStaged()

        class _Boom(Exception):
            pass

        with patch("core.services.file_vault.prepare_encrypted_upload") as vorbereiten:
            vorbereiten.side_effect = [erster, _Boom("zweite Datei kaputt")]
            with pytest.raises(_Boom):
                stage_attachment_uploads(
                    file_event,
                    staff_user,
                    {},
                    {ft_file.slug: [_upload("a.pdf"), _upload("b.pdf")]},
                    dt_file,
                )

        assert calls == [True]


class TestApplyAttachmentChangesLeftoverCleanupUsesMissingOkTrue:
    def test_ungenutzter_gestagter_upload_ueberlebt_nach_erfolgreichem_apply(
        self, monkeypatch, staff_user, file_event, dt_file, medienordner
    ):
        """Pins ``x_apply_attachment_changes__mutmut_73`` (``missing_ok=None``)
        und ``…_74`` (``missing_ok=False``) im Erfolgspfad-Cleanup ungenutzter
        Uploads. Ein ``staged_by_key``-Eintrag zu einem Slug, den der aktuelle
        Dokumentationstyp gar nicht kennt, bleibt nach REMOVE/REPLACE/ADD
        unverbraucht liegen — das Original raeumt ihn NACH dem erfolgreichen
        ``owner.save()`` mit ``missing_ok=True`` auf."""
        calls = _unlink_spy(monkeypatch)
        verwaist = _FakeStaged()

        apply_attachment_changes(
            file_event,
            staff_user,
            request_post={},
            request_files={},
            file_fields={},
            document_type=dt_file,
            staged_by_key={"kein-feld-dieses-typs": [verwaist]},
        )

        assert calls == [True]


class TestApplyAttachmentChangesExceptionCleanupUsesMissingOkTrue:
    def test_fehler_waehrend_eigenem_staging_raeumt_mit_missing_ok_true_auf(
        self, monkeypatch, staff_user, file_event, ft_file, dt_file, medienordner
    ):
        """Pins ``x_apply_attachment_changes__mutmut_75`` (``missing_ok=None``)
        und ``…_76`` (``missing_ok=False``) im except-Cleanup bei
        ``staged_by_key=None`` (die Funktion stagt selbst). Scheitert
        ``commit_staged_upload`` innerhalb von ``_apply_add``, raeumt das
        Original die selbst gestagte Datei mit ``missing_ok=True`` auf, bevor
        die urspruengliche Exception weitergereicht wird."""
        calls = _unlink_spy(monkeypatch)
        gestagt = _FakeStaged()

        class _Boom(RuntimeError):
            pass

        with patch("core.services.attachments.crud.stage_attachment_uploads") as staging:
            staging.return_value = ({ft_file.slug: [gestagt]}, [gestagt])
            with patch("core.services.file_vault.commit_staged_upload", side_effect=_Boom("db kaputt")):
                with pytest.raises(_Boom):
                    apply_attachment_changes(
                        file_event,
                        staff_user,
                        request_post={},
                        request_files={},
                        file_fields={ft_file.slug: [_upload("neu.pdf")]},
                        document_type=dt_file,
                        staged_by_key=None,
                    )

        assert calls == [True]


# ---------------------------------------------------------------------------
# _apply_replace — Marker-Schluessel "sort"
# ---------------------------------------------------------------------------


class TestApplyReplaceKeepsTheSortKey:
    def test_replace_schreibt_den_marker_mit_dem_schluessel_sort(
        self, staff_user, file_event, ft_file, dt_file, medienordner
    ):
        """Pins ``x__apply_replace__mutmut_33`` (Schluessel ``"XXsortXX"``) und
        ``…_34`` (``"SORT"``). Der persistierte Marker-Eintrag muss exakt den
        Schluessel ``"sort"`` tragen — eine Umbenennung beschaedigt die in
        ``owner.data_json`` gespeicherte JSON-Struktur direkt beobachtbar."""
        alt = _attach(file_event, ft_file, staff_user, name="alt.pdf", sort_order=0)
        file_event.data_json = {ft_file.slug: {"__files__": True, "entries": [{"id": str(alt.pk), "sort": 0}]}}
        file_event.save(update_fields=["data_json"])

        schluessel = replace_upload_key(ft_file.slug, alt.entry_id)
        apply_attachment_changes(
            file_event,
            staff_user,
            request_post={},
            request_files={schluessel: _upload("neu.pdf")},
            file_fields={},
            document_type=dt_file,
        )

        file_event.refresh_from_db()
        entries = file_event.data_json[ft_file.slug]["entries"]
        assert len(entries) == 1
        neue_pk = entries[0]["id"]
        assert neue_pk != str(alt.pk)
        assert entries == [{"id": neue_pk, "sort": 0}]


# ---------------------------------------------------------------------------
# _apply_add — sort_order beim Add-Commit
# ---------------------------------------------------------------------------


class TestApplyAddAssignsIncreasingSortOrder:
    def test_zwei_neue_dateien_bekommen_fortlaufende_sort_order(
        self, staff_user, file_event, ft_file, dt_file, medienordner
    ):
        """Pins ``x__apply_add__mutmut_27`` (``sort_order=None``) und ``…_32``
        (Kwarg komplett entfernt — ``commit_staged_upload`` hat selbst
        ``sort_order=None`` als Default, also identische Wirkung).
        ``_commit_staged_upload`` faellt im Add-Modus bei ``sort_order=None``
        IMMER auf ``0`` zurueck (``storage.py``); zwei gleichzeitig
        hinzugefuegte Dateien wuerden dann beide ``sort_order=0`` bekommen
        statt fortlaufend ``1``, ``2``."""
        bestand = _attach(file_event, ft_file, staff_user, name="bestand.pdf", sort_order=0)
        file_event.data_json = {ft_file.slug: {"__files__": True, "entries": [{"id": str(bestand.pk), "sort": 0}]}}
        file_event.save(update_fields=["data_json"])

        apply_attachment_changes(
            file_event,
            staff_user,
            request_post={},
            request_files={},
            file_fields={ft_file.slug: [_upload("neu1.pdf"), _upload("neu2.pdf")]},
            document_type=dt_file,
        )

        file_event.refresh_from_db()
        entries = file_event.data_json[ft_file.slug]["entries"]
        neue_pks = [e["id"] for e in entries if e["id"] != str(bestand.pk)]
        assert len(neue_pks) == 2
        sort_orders = sorted(Attachment.objects.filter(pk__in=neue_pks).values_list("sort_order", flat=True))
        assert sort_orders == [1, 2]


# ---------------------------------------------------------------------------
# _drop_purged_marker_entries — Rueckgabewert
# ---------------------------------------------------------------------------


class TestDropPurgedMarkerEntriesReturnsFalseWhenNothingChanges:
    def test_ohne_treffer_bleibt_der_rueckgabewert_false(self, file_event):
        """Pins ``x__drop_purged_marker_entries__mutmut_3`` (``changed=None``
        statt ``False``) und ``…_4`` (``changed=True`` statt ``False``). Ohne
        einen zu ``purged_pks`` passenden Marker liefert das Original exakt
        ``False`` zurueck und laesst ``owner.save()`` unausgefuehrt — ``None``
        und ``True`` sind beide per ``is`` von ``False`` unterscheidbar."""
        file_event.data_json = {}
        file_event.save(update_fields=["data_json"])

        result = _drop_purged_marker_entries(file_event, set())

        assert result is False


# ---------------------------------------------------------------------------
# _lock_owner_row — Client-Kurzschluss
# ---------------------------------------------------------------------------


class TestLockOwnerRowSkipsAnExtraQueryForClients:
    def test_fuer_einen_client_owner_laeuft_keine_zusaetzliche_abfrage(self, client_identified):
        """Pins ``x__lock_owner_row__mutmut_2`` (``"XXClientXX"``), ``…_3``
        (``"client"``) und ``…_4`` (``"CLIENT"``). Fuer einen ``Client``-Owner
        haelt dessen eigene Zeile das Art.-18-Gate bereits — das Original
        kehrt ohne zusaetzliche Abfrage zurueck; keine der drei
        String-Varianten matcht den echten Klassennamen ``"Client"``, sodass
        die Mutanten eine ZUSAETZLICHE ``SELECT ... FOR UPDATE``-Abfrage
        absetzen (Abfragezahl ist laut Schreib-Scope-Regel ein explizit
        beobachtbares Kriterium)."""
        with transaction.atomic():
            with CaptureQueriesContext(connection) as ctx:
                _lock_owner_row(client_identified)

        assert len(ctx.captured_queries) == 0


# ---------------------------------------------------------------------------
# purge_attachment_chain — exakte Guard-Meldungen
# ---------------------------------------------------------------------------


class TestPurgeAttachmentChainArticle18MessageIsExact:
    def test_artikel_18_sperre_meldet_den_exakten_wortlaut(
        self, staff_user, file_event, ft_file, client_identified, medienordner
    ):
        """Pins ``x_purge_attachment_chain__mutmut_23`` (``AttachmentPurgeBlockedError(None)``),
        ``…_24`` (``_(None)``, Stringify bricht mit ``AttributeError``),
        ``…_25``/``…_26``/``…_27`` (XX-Marker/klein/gross des ERSTEN
        Textsegments) und ``…_28``/``…_29``/``…_30`` (dieselben Varianten des
        ZWEITEN Textsegments) — alle acht betreffen dieselbe zusammengesetzte
        Meldung, EIN exakter String-Vergleich toetet sie alle."""
        from core.models import Client as ClientModel
        from core.services.client.restriction import restrict_processing

        att = _attach(file_event, ft_file, staff_user, name="beleg.pdf")
        file_event.data_json = {ft_file.slug: {"__files__": True, "entries": [{"id": str(att.pk), "sort": 0}]}}
        file_event.save(update_fields=["data_json"])

        restrict_processing(client_identified, staff_user, reason=ClientModel.RestrictionReason.ACCURACY_DISPUTED)

        with pytest.raises(PermissionDenied) as exc:
            purge_attachment_chain(file_event, att.entry_id)

        assert str(exc.value) == (
            "Für diese Person ist die Verarbeitung nach Art. 18 DSGVO eingeschränkt — "
            "die Datei darf nicht einzeln vernichtet werden."
        )


class TestPurgeAttachmentChainLegalHoldMessageIsExact:
    def test_legal_hold_meldet_den_exakten_wortlaut(
        self, staff_user, lead_user, file_event, ft_file, facility, medienordner
    ):
        """Pin: ``x_purge_attachment_chain__mutmut_35`` (XX-Marker-Variante der
        Legal-Hold-Meldung — zweiter, unabhaengiger Guard desselben Pfads)."""
        att = _attach(file_event, ft_file, staff_user, name="beleg.pdf")
        file_event.data_json = {ft_file.slug: {"__files__": True, "entries": [{"id": str(att.pk), "sort": 0}]}}
        file_event.save(update_fields=["data_json"])

        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=file_event.pk,
            reason="Pin099-attachments-1",
            created_by=lead_user,
        )

        with pytest.raises(PermissionDenied) as exc:
            purge_attachment_chain(file_event, att.entry_id)

        assert str(exc.value) == (
            "Für diesen Datensatz besteht ein aktiver Legal Hold — die Datei darf nicht vernichtet werden."
        )


# ---------------------------------------------------------------------------
# attachment_download_url — exakte Meldung
# ---------------------------------------------------------------------------


class TestAttachmentDownloadUrlMessageIsExact:
    def test_anhang_ohne_owner_meldet_den_exakten_wortlaut(self):
        """Pin: ``x_attachment_download_url__mutmut_5`` (``ValueError(None)``).
        Ein ungespeichertes ``Attachment()`` ohne gesetzten Owner-FK hat
        ``owner_type is None`` (dokumentiertes Verhalten, laut Docstring "nur
        waehrend der Konstruktion moeglich") — genau der Fall, den das Original
        mit einer diagnostischen ``ValueError`` quittiert."""
        unsaved = Attachment()

        with pytest.raises(ValueError) as exc:
            attachment_download_url(unsaved)

        assert str(exc.value) == f"Anhang ohne aufloesbaren Owner-Typ: {unsaved.pk!r} (owner_type=None)"


# ---------------------------------------------------------------------------
# build_prior_versions
# ---------------------------------------------------------------------------


class TestBuildPriorVersionsDictKeysAreExact:
    def test_vorversion_traegt_die_erwarteten_schluessel(self, staff_user, file_event, ft_file, medienordner):
        """Pins ``x_build_prior_versions__mutmut_17``/``…_18`` (Schluessel
        ``"file_size_display"`` -> ``"XXfile_size_displayXX"``/``"FILE_SIZE_DISPLAY"``)
        und ``…_20``/``…_21`` (``"superseded_at"`` -> selbe Varianten). EIN
        Exact-Dict-Vergleich der zurueckgegebenen Vorversion toetet alle vier."""
        alt = _attach(file_event, ft_file, staff_user, name="alt.pdf")
        neu = commit_staged_upload(
            file_event,
            ft_file,
            prepare_encrypted_upload(file_event.facility, _upload("neu.pdf"), staff_user, owner=file_event),
            staff_user,
            supersedes=alt,
        )
        alt.refresh_from_db()

        result = build_prior_versions(neu, {neu.pk: alt})

        assert len(result) == 1
        entry = result[0]
        assert set(entry.keys()) == {
            "attachment_id",
            "original_filename",
            "file_size_display",
            "superseded_at",
            "download_url",
        }
        assert entry["attachment_id"] == str(alt.pk)
        assert entry["file_size_display"] == format_file_size(alt.file_size)
        assert entry["superseded_at"] == alt.superseded_at


class TestBuildPriorVersionsTerminatesOnACycle:
    @pytest.mark.timeout(5)
    def test_ein_zyklus_hinter_dem_startpunkt_terminiert(self, staff_user, file_event, ft_file, medienordner):
        """Pin: ``x_build_prior_versions__mutmut_9`` (``seen.add(predecessor.pk)``
        -> ``seen.add(None)``). ``seen`` bewacht die Schleife vor einem Zyklus
        im ``predecessor_index``; ohne den ECHTEN Eintrag waechst ``seen`` nie
        ueber den Startpunkt hinaus. Ein (kuenstlich konstruierter) Zyklus
        HINTER dem Startpunkt (``a -> b -> c -> b``) wird vom Original nach
        der zweiten Runde erkannt und beendet — die Mutante erkennt ihn nicht
        und laeuft endlos (``pytest.mark.timeout`` als Deadline-Pin, Lehre
        gemaess der Regel fuer Endlosschleifen-Mutanten)."""
        a = _attach(file_event, ft_file, staff_user, name="a.pdf")
        b = _attach(file_event, ft_file, staff_user, name="b.pdf")
        c = _attach(file_event, ft_file, staff_user, name="c.pdf")
        predecessor_index = {a.pk: b, b.pk: c, c.pk: b}

        result = build_prior_versions(a, predecessor_index)

        assert [entry["attachment_id"] for entry in result] == [str(b.pk), str(c.pk)]


class TestBuildPriorVersionsWalksTheFullChain:
    def test_drei_versionen_liefern_alle_vorversionen(self, staff_user, file_event, ft_file, medienordner):
        """Pins ``x_build_prior_versions__mutmut_26`` (``predecessor = None``
        statt des naechsten Lookups) und ``…_27``
        (``predecessor_index.get(None)`` statt ``.get(current_attachment.pk)``
        — ``None`` ist nie ein Schluessel, identische Wirkung). Beide brechen
        die Kette nach GENAU einer Vorversion ab, unabhaengig davon, wie viele
        weitere ``predecessor_index`` liefern wuerde."""
        a = _attach(file_event, ft_file, staff_user, name="a.pdf")
        b = _attach(file_event, ft_file, staff_user, name="b.pdf")
        c = _attach(file_event, ft_file, staff_user, name="c.pdf")
        predecessor_index = {a.pk: b, b.pk: c}

        result = build_prior_versions(a, predecessor_index)

        assert [entry["attachment_id"] for entry in result] == [str(b.pk), str(c.pk)]


# ---------------------------------------------------------------------------
# resolve_file_entries — continue statt break an drei Fehlerpfaden
# ---------------------------------------------------------------------------


class TestResolveFileEntriesSkipsBadEntriesInsteadOfAborting:
    def test_ein_nicht_geparster_eintrag_stoppt_nicht_die_ganze_liste(
        self, staff_user, file_event, ft_file, medienordner
    ):
        """Pin: ``x_resolve_file_entries__mutmut_11`` (``except (ValueError,
        TypeError): continue`` -> ``break``). Ein nicht als UUID parsbarer
        Marker-Eintrag VOR einem gueltigen: das Original ueberspringt den
        kaputten und liefert den gueltigen dennoch; die Mutante bricht ab und
        unterschlaegt auch den gueltigen Folgeeintrag."""
        gueltig = _attach(file_event, ft_file, staff_user, name="gueltig.pdf")
        value = {
            "__files__": True,
            "entries": [{"id": "keine-uuid", "sort": 0}, {"id": str(gueltig.pk), "sort": 1}],
        }

        result = resolve_file_entries(value, {gueltig.pk: gueltig}, {})

        assert [entry["attachment_id"] for entry in result] == [str(gueltig.pk)]

    def test_ein_fremder_eintrag_stoppt_nicht_die_ganze_liste(self, staff_user, file_event, ft_file, medienordner):
        """Pin: ``x_resolve_file_entries__mutmut_15`` (``if not att: continue``
        -> ``break``). Ein Marker-Eintrag, dessen Attachment NICHT in
        ``attachments_by_pk`` steht (stale/fremd), VOR einem gueltigen: das
        Original ueberspringt, die Mutante bricht ab."""
        fremde_pk = uuid.uuid4()
        gueltig = _attach(file_event, ft_file, staff_user, name="gueltig.pdf")
        value = {
            "__files__": True,
            "entries": [{"id": str(fremde_pk), "sort": 0}, {"id": str(gueltig.pk), "sort": 1}],
        }

        result = resolve_file_entries(value, {gueltig.pk: gueltig}, {})

        assert [entry["attachment_id"] for entry in result] == [str(gueltig.pk)]

    def test_ein_soft_geloeschter_eintrag_stoppt_nicht_die_ganze_liste(
        self, staff_user, file_event, ft_file, medienordner
    ):
        """Pin: ``x_resolve_file_entries__mutmut_17`` (``if att.deleted_at is
        not None: continue`` -> ``break``). Ein soft-geloeschter Anhang VOR
        einem aktiven: das Original ueberspringt den geloeschten, die Mutante
        bricht ab und unterschlaegt den aktiven Folgeeintrag."""
        geloescht = _attach(file_event, ft_file, staff_user, name="geloescht.pdf")
        Attachment.objects.filter(pk=geloescht.pk).update(deleted_at=timezone.now())
        geloescht.refresh_from_db()
        gueltig = _attach(file_event, ft_file, staff_user, name="gueltig.pdf")
        value = {
            "__files__": True,
            "entries": [{"id": str(geloescht.pk), "sort": 0}, {"id": str(gueltig.pk), "sort": 1}],
        }

        result = resolve_file_entries(value, {geloescht.pk: geloescht, gueltig.pk: gueltig}, {})

        assert [entry["attachment_id"] for entry in result] == [str(gueltig.pk)]


# ---------------------------------------------------------------------------
# build_attachment_context — continue statt break, plus Anzeige-Schluessel
# ---------------------------------------------------------------------------


class TestBuildAttachmentContextSkipsBadEntriesInsteadOfAborting:
    def test_ein_nicht_geparster_eintrag_stoppt_nicht_den_slug(self, staff_user, file_event, ft_file, medienordner):
        """Pin: ``x_build_attachment_context__mutmut_16`` (``except
        (ValueError, TypeError): continue`` -> ``break``)."""
        gueltig = _attach(file_event, ft_file, staff_user, name="gueltig.pdf")
        file_event.data_json = {
            ft_file.slug: {
                "__files__": True,
                "entries": [{"id": "keine-uuid", "sort": 0}, {"id": str(gueltig.pk), "sort": 1}],
            }
        }
        file_event.save(update_fields=["data_json"])

        result = build_attachment_context(file_event)

        assert [entry["attachment_id"] for entry in result[ft_file.slug]] == [str(gueltig.pk)]

    def test_ein_fremder_eintrag_stoppt_nicht_den_slug(
        self, staff_user, file_event, ft_file, facility, client_identified, medienordner
    ):
        """Pin: ``x_build_attachment_context__mutmut_22`` (``if not att or
        att.deleted_at is not None: continue`` -> ``break``). Der Marker
        verweist auf ein Attachment EINES ANDEREN Owners (nicht in
        ``owner.attachments.all()``) — dieselbe Wirkung wie ``not att`` in
        ``resolve_file_entries``, hier aber in der kombinierten Bedingung."""
        anderes_event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=file_event.document_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        fremd = _attach(anderes_event, ft_file, staff_user, name="fremd.pdf")
        gueltig = _attach(file_event, ft_file, staff_user, name="gueltig.pdf")
        file_event.data_json = {
            ft_file.slug: {
                "__files__": True,
                "entries": [{"id": str(fremd.pk), "sort": 0}, {"id": str(gueltig.pk), "sort": 1}],
            }
        }
        file_event.save(update_fields=["data_json"])

        result = build_attachment_context(file_event)

        assert [entry["attachment_id"] for entry in result[ft_file.slug]] == [str(gueltig.pk)]


class TestBuildAttachmentContextDictKeysAreExact:
    def test_eintrag_traegt_die_erwarteten_schluessel(self, staff_user, file_event, ft_file, medienordner):
        """Pins ``x_build_attachment_context__mutmut_33``/``…_34`` (Schluessel
        ``"size"`` -> ``"XXsizeXX"``/``"SIZE"``) und ``…_36``/``…_37``
        (``"sort_order"`` -> selbe Varianten)."""
        att = _attach(file_event, ft_file, staff_user, name="beleg.pdf", sort_order=0)
        file_event.data_json = {ft_file.slug: {"__files__": True, "entries": [{"id": str(att.pk), "sort": 0}]}}
        file_event.save(update_fields=["data_json"])

        result = build_attachment_context(file_event)

        assert len(result[ft_file.slug]) == 1
        entry = result[ft_file.slug][0]
        assert set(entry.keys()) == {"entry_id", "attachment_id", "filename", "size", "sort_order", "download_url"}
        assert entry["size"] == format_file_size(att.file_size)
        assert entry["sort_order"] == att.sort_order
