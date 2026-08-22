"""Owner-generische Anhang-Services (M24/AP2, Refs #1738).

Deckt das neue Paket ``core.services.attachments`` ab:

* ``preserve_file_markers`` — ohne diese Re-Injektion wuerde der erste reine
  Text-Edit still ALLE Bestandsanhaenge aus dem ``data_json`` werfen.
* ``discard_type_switch_file_chains`` — Soft-Delete der Ketten beim Typwechsel
  (ADR-039 §7), Rueckgabe = Anzahl der KETTEN.
* ``stage_attachment_uploads``/``apply_attachment_changes`` — die Trennung von
  Scan/Verschluesselung (vor der Transaktion) und DB-Write (in der Transaktion)
  samt Cleanup-Garantie fuer nicht verbrauchte Staging-Dateien (Refs #1956).
* ``attachment_download_url`` — Owner-Dispatch der Download-Route.
* ``build_attachment_context``/``resolve_file_entries`` — Anzeige-Kontexte, die
  ab AP2 die ``download_url`` selbst tragen (die fuenf hartkodierten
  ``{% url 'core:attachment_download' %}``-Stellen loest AP4 darauf ab).
* Alias-Vertrag: die Alt-Namen in ``services/events`` bleiben gueltig.
"""

from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffähig ({_libmagic_exc}) — Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from core.models import FieldTemplate  # noqa: E402
from core.services.attachments import (  # noqa: E402
    apply_attachment_changes,
    attachment_download_url,
    build_attachment_context,
    discard_type_switch_file_chains,
    preserve_file_markers,
    replace_upload_key,
    resolve_file_entries,
    stage_attachment_uploads,
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


@pytest.fixture
def ft_file(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Beleg (AP2)",
        field_type=FieldTemplate.FieldType.FILE,
    )


def _attach(owner, ft, user, *, name="beleg.pdf", supersedes=None, sort_order=None):
    upload = SimpleUploadedFile(name, PDF_HEADER, content_type="application/pdf")
    staged = prepare_encrypted_upload(owner.facility, upload, user, owner=owner)
    return commit_staged_upload(owner, ft, staged, user, supersedes=supersedes, sort_order=sort_order)


def _multi_marker(*attachments):
    return {
        "__files__": True,
        "entries": [{"id": str(a.pk), "sort": i} for i, a in enumerate(attachments)],
    }


class TestPreserveFileMarkers:
    """Reiner Dict-Helfer — kein DB-Zugriff noetig."""

    def test_bestandsmarker_wird_zurueckgeholt(self):
        marker = {"__files__": True, "entries": [{"id": "11111111-1111-1111-1111-111111111111", "sort": 0}]}
        merged = {"notiz": "neuer Text"}
        result = preserve_file_markers(None, merged, {"notiz": "alt", "anhang": marker}, ["anhang"])
        assert result is merged, "mutiert in-place wie merge_update_payload"
        assert merged["anhang"] == marker
        assert merged["notiz"] == "neuer Text", "Textwert des Formulars gewinnt"

    def test_alt_format_stufe_a_wird_ebenfalls_erhalten(self):
        marker = {"__file__": True, "attachment_id": "22222222-2222-2222-2222-222222222222"}
        merged = {}
        preserve_file_markers(None, merged, {"scan": marker}, ["scan"])
        assert merged["scan"] == marker

    def test_slug_ohne_bestandsmarker_bleibt_leer(self):
        merged = {}
        preserve_file_markers(None, merged, {"anhang": None}, ["anhang"])
        assert merged == {}

    def test_kein_marker_dict_wird_nicht_uebernommen(self):
        """Ein Nicht-Marker (z. B. Rest eines Feldtyp-Wechsels) wuerde von
        ``validate_data_json`` ohnehin verworfen — gar nicht erst einsetzen."""
        merged = {}
        preserve_file_markers(None, merged, {"anhang": {"__file__": True}}, ["anhang"])
        assert merged == {}

    def test_slugs_ausserhalb_des_aktuellen_typs_werden_nicht_nachgezogen(self):
        """Typwechsel: ein Alt-Slug gehoert nicht mehr zum Feldset."""
        marker = {"__files__": True, "entries": [{"id": "33333333-3333-3333-3333-333333333333", "sort": 0}]}
        merged = {}
        preserve_file_markers(None, merged, {"alt-anhang": marker}, ["neuer-anhang"])
        assert merged == {}

    def test_leeres_previous_data_ist_none_sicher(self):
        merged = {"a": 1}
        preserve_file_markers(None, merged, None, ["anhang"])
        assert merged == {"a": 1}


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestDiscardTypeSwitchFileChains:
    def test_zaehlt_ketten_nicht_versionen(self, staff_user, sample_event, ft_file):
        first = _attach(sample_event, ft_file, staff_user)
        # Zweite Version derselben Kette (Replace) — zaehlt als EINE Kette.
        _attach(sample_event, ft_file, staff_user, name="neu.pdf", supersedes=first)
        second_chain = _attach(sample_event, ft_file, staff_user, name="zweite.pdf")

        assert discard_type_switch_file_chains(sample_event, staff_user) == 2
        assert sample_event.attachments.filter(deleted_at__isnull=True).count() == 0
        assert sample_event.attachments.filter(deleted_at__isnull=False).count() == 3
        second_chain.refresh_from_db()
        assert second_chain.deleted_at is not None

    def test_person_als_owner(self, staff_user, client_identified, ft_file):
        _attach(client_identified, ft_file, staff_user)
        assert discard_type_switch_file_chains(client_identified, staff_user) == 1
        assert client_identified.attachments.filter(deleted_at__isnull=True).count() == 0

    def test_ist_idempotent(self, staff_user, client_identified, ft_file):
        _attach(client_identified, ft_file, staff_user)
        assert discard_type_switch_file_chains(client_identified, staff_user) == 1
        assert discard_type_switch_file_chains(client_identified, staff_user) == 0

    def test_ohne_anhaenge_null(self, staff_user, case_open):
        assert discard_type_switch_file_chains(case_open, staff_user) == 0

    def test_scopt_auf_den_eigenen_owner(self, staff_user, client_identified, case_open, ft_file):
        _attach(client_identified, ft_file, staff_user)
        assert discard_type_switch_file_chains(case_open, staff_user) == 0
        assert client_identified.attachments.filter(deleted_at__isnull=True).count() == 1


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestAttachmentDownloadUrl:
    def test_ereignis_anhang_loest_die_bestandsroute_auf(self, staff_user, sample_event, ft_file):
        attachment = _attach(sample_event, ft_file, staff_user)
        expected = reverse("core:attachment_download", kwargs={"pk": sample_event.pk, "attachment_pk": attachment.pk})
        assert attachment_download_url(attachment) == expected

    def test_personen_anhang_loest_die_ap4_route_auf(self, staff_user, client_identified, ft_file):
        """AP4 hat die Route angelegt (Refs #1738, ADR-039 §12).

        Bis AP4 pinnte dieser Test bewusst ``NoReverseMatch`` — fail-loud statt
        eines stillen ``None``, das im Template als toter Link gelandet waere.
        Mit ``core:client_attachment_download`` loest der Dispatch jetzt auf.
        """
        attachment = _attach(client_identified, ft_file, staff_user)
        assert attachment_download_url(attachment) == reverse(
            "core:client_attachment_download",
            kwargs={"pk": client_identified.pk, "attachment_pk": attachment.pk},
        )

    def test_fall_anhang_loest_die_ap4_route_auf(self, staff_user, case_open, ft_file):
        attachment = _attach(case_open, ft_file, staff_user)
        assert attachment_download_url(attachment) == reverse(
            "core:case_attachment_download",
            kwargs={"pk": case_open.pk, "attachment_pk": attachment.pk},
        )


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestKontexteTragenDieDownloadUrl:
    """Validator-Befund: die fuenf hartkodierten
    ``{% url 'core:attachment_download' event.pk … %}``-Stellen koennen erst auf
    ``entry.download_url`` umgestellt werden, wenn der Kontext sie traegt."""

    def test_build_attachment_context_traegt_download_url(self, staff_user, sample_event, ft_file):
        attachment = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = _multi_marker(attachment)
        sample_event.save(update_fields=["data_json"])

        context = build_attachment_context(sample_event)
        entry = context[ft_file.slug][0]
        assert entry["attachment_id"] == str(attachment.pk)
        assert entry["download_url"] == reverse(
            "core:attachment_download", kwargs={"pk": sample_event.pk, "attachment_pk": attachment.pk}
        )

    def test_resolve_file_entries_traegt_url_je_eintrag_und_vorversion(self, staff_user, sample_event, ft_file):
        old = _attach(sample_event, ft_file, staff_user)
        new = _attach(sample_event, ft_file, staff_user, name="neu.pdf", supersedes=old)

        all_attachments = list(sample_event.attachments.all())
        attachments_by_pk = {a.pk: a for a in all_attachments}
        predecessor_index = {a.superseded_by_id: a for a in all_attachments if a.superseded_by_id is not None}

        entries = resolve_file_entries(_multi_marker(new), attachments_by_pk, predecessor_index)
        assert len(entries) == 1
        assert entries[0]["download_url"] == reverse(
            "core:attachment_download", kwargs={"pk": sample_event.pk, "attachment_pk": new.pk}
        )
        prior = entries[0]["prior_versions"]
        assert len(prior) == 1
        assert prior[0]["attachment_id"] == str(old.pk)
        assert prior[0]["download_url"] == reverse(
            "core:attachment_download", kwargs={"pk": sample_event.pk, "attachment_pk": old.pk}
        )


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestStagingUndAnwendungGetrennt:
    """Refs #1956: Scan/Verschluesselung stehen VOR der Transaktion, im
    Transaktionsblock faellt nur noch der DB-Write an. Beide Seiten teilen
    Sensitivity-Gate und REPLACE-Aufloesung — laufen sie auseinander, entstehen
    verschluesselte Waisen auf der Disk (#662).
    """

    @pytest.fixture
    def dt_with_file(self, facility, ft_file):
        from core.models import DocumentType, DocumentTypeField

        dt = DocumentType.objects.create(facility=facility, name="Staging-Typ")
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
        return dt

    def test_staging_scannt_nur_vorhandene_replace_entries(
        self, staff_user, sample_event, ft_file, dt_with_file, settings, tmp_path
    ):
        """Ein frei erfundener ``<slug>__replace__<uuid>``-Schluessel darf weder
        einen Virenscan noch eine ``.enc`` erzeugen — sonst genuegte ein
        praeparierter POST, um Scan-Last und Disk-Muell zu erzwingen."""
        settings.MEDIA_ROOT = str(tmp_path)
        import uuid as uuid_mod

        upload = SimpleUploadedFile("fremd.pdf", PDF_HEADER, content_type="application/pdf")
        staged_by_key, staged_all = stage_attachment_uploads(
            sample_event,
            staff_user,
            {replace_upload_key(ft_file.slug, uuid_mod.uuid4()): upload},
            {},
            dt_with_file,
        )
        assert staged_by_key == {}
        assert staged_all == []
        assert list(Path(tmp_path).rglob("*.enc")) == []

    def test_nicht_verbrauchte_staging_datei_wird_entfernt(
        self, staff_user, sample_event, ft_file, dt_with_file, settings, tmp_path
    ):
        """Verliert eine gestagte Datei zwischen Staging und Anwendung ihre
        Zuordnung, bleibt sie sonst als verschluesselte Waise liegen."""
        settings.MEDIA_ROOT = str(tmp_path)
        upload = SimpleUploadedFile("waise.pdf", PDF_HEADER, content_type="application/pdf")
        staged = prepare_encrypted_upload(sample_event.facility, upload, staff_user, owner=sample_event)
        assert staged.output_path.exists()

        apply_attachment_changes(
            sample_event,
            staff_user,
            {},
            {},
            {},
            dt_with_file,
            staged_by_key={"slug-ohne-feld": [staged]},
        )

        assert not staged.output_path.exists()
        assert sample_event.attachments.count() == 0

    def test_eigenes_staging_raeumt_bei_db_fehler_auf(
        self, staff_user, sample_event, ft_file, dt_with_file, settings, tmp_path
    ):
        """Ohne ``staged_by_key`` staged der Service selbst — scheitert danach
        der DB-Write, muss er seine eigenen ``.enc`` wieder entfernen (#662)."""
        settings.MEDIA_ROOT = str(tmp_path)
        from unittest.mock import patch

        upload = SimpleUploadedFile("db-fehler.pdf", PDF_HEADER, content_type="application/pdf")
        with (
            patch("core.services.file_vault.commit_staged_upload", side_effect=RuntimeError("DB down")),
            pytest.raises(RuntimeError, match="DB down"),
        ):
            apply_attachment_changes(
                sample_event,
                staff_user,
                {},
                {},
                {ft_file.slug: [upload]},
                dt_with_file,
            )

        assert list(Path(tmp_path).rglob("*.enc")) == []

    def test_add_nach_remove_persistiert_das_stufe_b_schema(
        self, staff_user, sample_event, ft_file, dt_with_file, settings, tmp_path
    ):
        """Der nach einem ADD geschriebene Marker traegt exakt das Stufe-B-Schema
        ``{"id", "sort"}`` (``dynamic_data.py``) mit ``sort = base_sort + idx``.

        ``data_json`` geht verbatim in Facility-/DSGVO-Exporte; ein fremder
        Entry-Schluessel liesse ``normalize_file_marker`` den Sort beim
        naechsten Lesen positional re-derivieren — nach Remove+Add drifteten
        Marker-Sort und DB-``sort_order`` auseinander und Folge-ADDs vergaeben
        doppelte Sortwerte.
        """
        settings.MEDIA_ROOT = str(tmp_path)
        first = _attach(sample_event, ft_file, staff_user, name="erste.pdf", sort_order=0)
        second = _attach(sample_event, ft_file, staff_user, name="zweite.pdf", sort_order=1)
        sample_event.data_json[ft_file.slug] = _multi_marker(first, second)
        sample_event.save(update_fields=["data_json"])

        uploads = [
            SimpleUploadedFile("dritte.pdf", PDF_HEADER, content_type="application/pdf"),
            SimpleUploadedFile("vierte.pdf", PDF_HEADER, content_type="application/pdf"),
        ]
        apply_attachment_changes(
            sample_event,
            staff_user,
            {f"{ft_file.slug}__remove": str(first.entry_id)},
            {},
            {ft_file.slug: uploads},
            dt_with_file,
        )

        sample_event.refresh_from_db()
        entries = sample_event.data_json[ft_file.slug]["entries"]
        assert [set(entry) for entry in entries] == [{"id", "sort"}] * 3
        added = list(
            sample_event.attachments.filter(deleted_at__isnull=True)
            .exclude(entry_id__in=[first.entry_id, second.entry_id])
            .order_by("sort_order")
        )
        assert entries == [
            {"id": str(second.pk), "sort": 1},
            {"id": str(added[0].pk), "sort": 2},
            {"id": str(added[1].pk), "sort": 3},
        ]


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestAliasVertrag:
    """Der Umzug darf keinen Bestands-Importpfad brechen (Muster P3)."""

    def test_events_paket_reexportiert_die_alt_namen(self):
        from core.services import events as events_pkg
        from core.services.attachments import context as att_context
        from core.services.attachments import crud as att_crud
        from core.services.events import context as events_context

        assert events_context._resolve_file_entries is att_context.resolve_file_entries
        assert events_context._build_prior_versions is att_context.build_prior_versions
        assert events_pkg._build_prior_versions is att_context.build_prior_versions
        # Die beiden Schreiber sind duenne Delegierer (eigene Funktionsobjekte).
        assert events_pkg.finalize_staged_files is not att_crud.finalize_staged_files
        assert events_pkg.apply_attachment_changes is not att_crud.apply_attachment_changes

    def test_alt_alias_baut_denselben_kontext(self, staff_user, sample_event, ft_file):
        from core.services.events import build_attachment_context as legacy_build

        attachment = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = _multi_marker(attachment)
        sample_event.save(update_fields=["data_json"])
        assert legacy_build(sample_event) == build_attachment_context(sample_event)

    def test_marker_helfer_bleiben_ueber_events_fields_importierbar(self):
        from core.services import dynamic_data
        from core.services.events import fields as events_fields

        assert events_fields._is_file_marker is dynamic_data.is_file_marker
        assert events_fields.normalize_file_marker is dynamic_data.normalize_file_marker
        assert events_fields.file_marker_slugs is dynamic_data.file_marker_slugs
        assert events_fields.is_singleton_file_marker is dynamic_data.is_singleton_file_marker
        assert events_fields.is_multi_file_marker is dynamic_data.is_multi_file_marker
