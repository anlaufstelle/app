"""Import-Wizard-Views: Rollen-Gate, Statusmaschine, IDOR, Happy-Path (Refs #1060).

Die vertikale Rollenmatrix und die IDOR-Probe laufen zusaetzlich generisch ueber
``_authz_expectations.py`` (test_authz_matrix / test_authz_idor). Hier stehen die
wizard-spezifischen Invarianten: der vollstaendige Durchlauf, die
Statusuebergaenge und die Zusicherung, dass ein Lauf ohne Quelldatei zu einer
Nutzermeldung fuehrt statt zu einem 500er.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as DjangoClient
from django.urls import reverse

from core.models import Client, FieldTemplate, ImportJob
from core.services import import_engine

CSV = "Pseudonym;Kontaktstufe;Notizen\nVIEW-01;identified;Erste\nVIEW-02;qualified;Zweite\n"


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def admin_client_logged_in(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def uploaded_job(facility, admin_user, media_root):
    return import_engine.store_import_upload(
        facility,
        admin_user,
        SimpleUploadedFile("import.csv", CSV.encode(), content_type="text/csv"),
        target_type=ImportJob.TargetType.CLIENT,
    )


@pytest.mark.django_db
class TestRoleGate:
    """Nur die Anwendungsbetreuung darf importieren (Ergaenzung zur Matrix)."""

    @pytest.mark.parametrize("user_fixture", ["lead_user", "staff_user", "assistant_user"])
    def test_other_roles_get_403_on_the_list(self, client, request, user_fixture):
        client.force_login(request.getfixturevalue(user_fixture))
        assert client.get(reverse("core:import_list")).status_code == 403

    def test_admin_sees_the_list(self, admin_client_logged_in):
        assert admin_client_logged_in.get(reverse("core:import_list")).status_code == 200


@pytest.mark.django_db
class TestFacilityScoping:
    def test_list_shows_only_own_jobs(self, admin_client_logged_in, import_job, foreign_import_job):
        response = admin_client_logged_in.get(reverse("core:import_list"))
        assert str(import_job.pk) in response.content.decode()
        assert str(foreign_import_job.pk) not in response.content.decode()

    def test_foreign_job_detail_is_404(self, admin_client_logged_in, foreign_import_job):
        response = admin_client_logged_in.get(reverse("core:import_detail", kwargs={"pk": foreign_import_job.pk}))
        assert response.status_code == 404

    def test_foreign_job_abort_is_404(self, admin_client_logged_in, foreign_import_job):
        response = admin_client_logged_in.post(reverse("core:import_abort", kwargs={"pk": foreign_import_job.pk}))
        assert response.status_code == 404
        foreign_import_job.refresh_from_db()
        assert foreign_import_job.status == ImportJob.Status.UPLOADED


@pytest.mark.django_db
class TestWizardRoundtrip:
    def test_upload_creates_job_and_redirects_to_mapping(self, admin_client_logged_in, facility, media_root):
        response = admin_client_logged_in.post(
            reverse("core:import_create"),
            {
                "target_type": ImportJob.TargetType.CLIENT,
                "source_file": SimpleUploadedFile("import.csv", CSV.encode(), content_type="text/csv"),
                "delimiter": "",
                "encoding": "",
            },
        )
        job = ImportJob.objects.for_facility(facility).get()
        assert response.status_code == 302
        assert response.url == reverse("core:import_mapping", kwargs={"pk": job.pk})
        assert job.status == ImportJob.Status.UPLOADED

    def test_full_roundtrip_upload_mapping_preview_commit(self, admin_client_logged_in, facility, media_root):
        admin_client_logged_in.post(
            reverse("core:import_create"),
            {
                "target_type": ImportJob.TargetType.CLIENT,
                "source_file": SimpleUploadedFile("import.csv", CSV.encode(), content_type="text/csv"),
                "delimiter": "",
                "encoding": "",
            },
        )
        job = ImportJob.objects.for_facility(facility).get()

        mapping_page = admin_client_logged_in.get(reverse("core:import_mapping", kwargs={"pk": job.pk}))
        assert mapping_page.status_code == 200
        assert "Pseudonym" in mapping_page.content.decode()

        mapped = admin_client_logged_in.post(
            reverse("core:import_mapping", kwargs={"pk": job.pk}),
            {"column-0": "pseudonym", "column-1": "contact_stage", "column-2": "notes"},
        )
        assert mapped.url == reverse("core:import_preview", kwargs={"pk": job.pk})
        job.refresh_from_db()
        assert job.status == ImportJob.Status.VALIDATED
        assert job.result_json["summary"]["created"] == 2
        assert not Client.objects.filter(pseudonym__startswith="VIEW-").exists()

        preview = admin_client_logged_in.get(reverse("core:import_preview", kwargs={"pk": job.pk}))
        assert preview.status_code == 200

        committed = admin_client_logged_in.post(reverse("core:import_commit", kwargs={"pk": job.pk}))
        assert committed.url == reverse("core:import_detail", kwargs={"pk": job.pk})
        job.refresh_from_db()
        assert job.status == ImportJob.Status.COMMITTED
        assert Client.objects.for_facility(facility).filter(pseudonym__startswith="VIEW-").count() == 2
        assert not import_engine.import_file_path(job).exists() or job.storage_filename == ""

    def test_mapping_page_shows_sample_values_per_column(self, admin_client_logged_in, uploaded_job):
        """Ohne Beispielwerte ist die Zuordnung Ratespiel — die Spaltennamen
        allein sagen nicht, was in der Datei steht."""
        content = admin_client_logged_in.get(
            reverse("core:import_mapping", kwargs={"pk": uploaded_job.pk})
        ).content.decode()
        assert "VIEW-01" in content
        assert "VIEW-02" in content

    def test_mapping_page_keeps_the_previous_selection(self, admin_client_logged_in, uploaded_job):
        admin_client_logged_in.post(
            reverse("core:import_mapping", kwargs={"pk": uploaded_job.pk}),
            {"column-0": "pseudonym", "column-1": "contact_stage", "column-2": ""},
        )
        content = admin_client_logged_in.get(
            reverse("core:import_mapping", kwargs={"pk": uploaded_job.pk})
        ).content.decode()
        assert '<option value="pseudonym" selected>' in content

    def test_invalid_mapping_is_rendered_with_errors(self, admin_client_logged_in, uploaded_job):
        response = admin_client_logged_in.post(
            reverse("core:import_mapping", kwargs={"pk": uploaded_job.pk}),
            {"column-0": "notes", "column-1": "", "column-2": ""},
        )
        assert response.status_code == 400
        assert b"import-mapping-errors" in response.content
        uploaded_job.refresh_from_db()
        assert uploaded_job.status == ImportJob.Status.UPLOADED

    def test_commit_with_inactive_mapped_template_redirects_to_mapping_message(
        self,
        admin_client_logged_in,
        facility,
        admin_user,
        doc_type_client_profile,
        media_root,
    ):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            SimpleUploadedFile(
                "import.csv",
                b"Pseudonym;Wohnform\nVIEW-TPL;Wohnung\n",
                content_type="text/csv",
            ),
            target_type=ImportJob.TargetType.CLIENT,
            document_type=doc_type_client_profile,
        )
        admin_client_logged_in.post(
            reverse("core:import_mapping", kwargs={"pk": job.pk}),
            {"column-0": "pseudonym", "column-1": "data_json.wohnsituation"},
        )
        template = FieldTemplate.objects.get(facility=facility, slug="wohnsituation")
        template.is_active = False
        template.save(update_fields=["is_active"])

        response = admin_client_logged_in.post(reverse("core:import_commit", kwargs={"pk": job.pk}))

        assert response.status_code == 302
        assert response.url == reverse("core:import_detail", kwargs={"pk": job.pk})
        detail = admin_client_logged_in.get(response.url)
        assert "Zuordnungsschritt" in detail.content.decode()
        job.refresh_from_db()
        assert job.status == ImportJob.Status.VALIDATED
        assert not Client.objects.filter(facility=facility, pseudonym="VIEW-TPL").exists()

    def test_commit_request_uses_mapping_confirmed_by_second_session(
        self, facility, admin_user, media_root, monkeypatch
    ):
        first_client = DjangoClient()
        second_client = DjangoClient()
        first_client.force_login(admin_user)
        second_client.force_login(admin_user)
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            SimpleUploadedFile(
                "import.csv",
                b"Pseudonym;Wert\nVIEW-LOCK;identified\n",
                content_type="text/csv",
            ),
            target_type=ImportJob.TargetType.CLIENT,
        )
        mapping_url = reverse("core:import_mapping", kwargs={"pk": job.pk})
        first_client.post(mapping_url, {"column-0": "pseudonym", "column-1": "notes"})
        real_commit_import = import_engine.commit_import
        second_responses = []

        def _commit_after_second_session_mapping(stale_job, user):
            second_responses.append(
                second_client.post(mapping_url, {"column-0": "pseudonym", "column-1": "contact_stage"})
            )
            return real_commit_import(stale_job, user)

        monkeypatch.setattr(import_engine, "commit_import", _commit_after_second_session_mapping)

        response = first_client.post(reverse("core:import_commit", kwargs={"pk": job.pk}))

        assert response.status_code == 302
        assert second_responses[0].url == reverse("core:import_preview", kwargs={"pk": job.pk})
        job.refresh_from_db()
        imported = Client.objects.get(facility=facility, pseudonym="VIEW-LOCK")
        assert job.mapping_json["columns"] == {"Pseudonym": "pseudonym", "Wert": "contact_stage"}
        assert imported.contact_stage == Client.ContactStage.IDENTIFIED
        assert imported.notes == ""

    def test_upload_rejects_an_event_job_without_document_type(self, admin_client_logged_in, facility, media_root):
        response = admin_client_logged_in.post(
            reverse("core:import_create"),
            {
                "target_type": ImportJob.TargetType.EVENT,
                "source_file": SimpleUploadedFile("import.csv", CSV.encode(), content_type="text/csv"),
                "delimiter": "",
                "encoding": "",
            },
        )
        assert response.status_code == 200
        assert ImportJob.objects.for_facility(facility).count() == 0

    def test_upload_renders_malformed_csv_as_form_error(self, admin_client_logged_in, facility, media_root):
        response = admin_client_logged_in.post(
            reverse("core:import_create"),
            {
                "target_type": ImportJob.TargetType.CLIENT,
                "source_file": SimpleUploadedFile(
                    "import.csv",
                    b"Pseudonym\r\na\rb\r\n",
                    content_type="text/csv",
                ),
                "delimiter": "",
                "encoding": "",
            },
        )

        assert response.status_code == 200
        assert "Die Datei konnte nicht als CSV gelesen werden" in response.content.decode()
        assert ImportJob.objects.for_facility(facility).count() == 0

    def test_upload_rejects_a_foreign_document_type(
        self, admin_client_logged_in, facility, foreign_doc_type, media_root
    ):
        response = admin_client_logged_in.post(
            reverse("core:import_create"),
            {
                "target_type": ImportJob.TargetType.EVENT,
                "document_type": str(foreign_doc_type.pk),
                "source_file": SimpleUploadedFile("import.csv", CSV.encode(), content_type="text/csv"),
                "delimiter": "",
                "encoding": "",
            },
        )
        assert response.status_code == 200
        assert ImportJob.objects.for_facility(facility).count() == 0


@pytest.mark.django_db
class TestStateMachineGuards:
    def test_commit_without_dry_run_redirects_with_message(self, admin_client_logged_in, uploaded_job):
        response = admin_client_logged_in.post(reverse("core:import_commit", kwargs={"pk": uploaded_job.pk}))
        assert response.url == reverse("core:import_detail", kwargs={"pk": uploaded_job.pk})
        uploaded_job.refresh_from_db()
        assert uploaded_job.status == ImportJob.Status.UPLOADED

    def test_preview_without_dry_run_redirects(self, admin_client_logged_in, uploaded_job):
        response = admin_client_logged_in.get(reverse("core:import_preview", kwargs={"pk": uploaded_job.pk}))
        assert response.url == reverse("core:import_detail", kwargs={"pk": uploaded_job.pk})

    def test_mapping_of_a_finished_job_redirects(self, admin_client_logged_in, uploaded_job, admin_user):
        import_engine.abort_import(uploaded_job, admin_user)
        response = admin_client_logged_in.get(reverse("core:import_mapping", kwargs={"pk": uploaded_job.pk}))
        assert response.url == reverse("core:import_detail", kwargs={"pk": uploaded_job.pk})

    def test_abort_deletes_the_upload(self, admin_client_logged_in, uploaded_job):
        path = import_engine.import_file_path(uploaded_job)
        assert path.exists()
        admin_client_logged_in.post(reverse("core:import_abort", kwargs={"pk": uploaded_job.pk}))
        uploaded_job.refresh_from_db()
        assert uploaded_job.status == ImportJob.Status.ABORTED
        assert not path.exists()

    def test_second_abort_redirects_instead_of_failing(self, admin_client_logged_in, uploaded_job):
        admin_client_logged_in.post(reverse("core:import_abort", kwargs={"pk": uploaded_job.pk}))
        response = admin_client_logged_in.post(reverse("core:import_abort", kwargs={"pk": uploaded_job.pk}))
        assert response.status_code == 302


@pytest.mark.django_db
class TestDetailCountLabel:
    """Die Detailseite ist das Ziel ALLER Fehler-Redirects und des Abbruchs —
    sie darf die hypothetische Zeilenzahl eines Probelaufs nie als vollendete
    Tatsache beschriften (Refs #1060).

    ``summary.created`` zaehlt die Zeilen, die INNERHALB der Transaktion durch
    ``create_client`` liefen; im Probelauf wird genau diese Transaktion
    zurueckgerollt. ``summary.committed`` unterscheidet beide Faelle.
    """

    def _dry_run(self, client, job):
        client.post(
            reverse("core:import_mapping", kwargs={"pk": job.pk}),
            {"column-0": "pseudonym", "column-1": "contact_stage", "column-2": "notes"},
        )

    def test_dry_run_count_is_labelled_as_hypothetical(self, admin_client_logged_in, uploaded_job):
        self._dry_run(admin_client_logged_in, uploaded_job)

        content = admin_client_logged_in.get(
            reverse("core:import_detail", kwargs={"pk": uploaded_job.pk})
        ).content.decode()

        assert "Würden angelegt (Probelauf)" in content
        assert "Angelegte Datensätze" not in content

    def test_committed_count_keeps_the_factual_label(self, admin_client_logged_in, uploaded_job):
        self._dry_run(admin_client_logged_in, uploaded_job)
        admin_client_logged_in.post(reverse("core:import_commit", kwargs={"pk": uploaded_job.pk}))

        content = admin_client_logged_in.get(
            reverse("core:import_detail", kwargs={"pk": uploaded_job.pk})
        ).content.decode()

        assert "Angelegte Datensätze" in content
        assert "Würden angelegt (Probelauf)" not in content


@pytest.mark.django_db
class TestMissingSourceFileIsAUserError:
    """Die Matrix probt mit einem Lauf ohne Datei — das darf nie 5xx werden."""

    def test_mapping_get_redirects(self, admin_client_logged_in, import_job):
        response = admin_client_logged_in.get(reverse("core:import_mapping", kwargs={"pk": import_job.pk}))
        assert response.status_code == 302

    def test_mapping_post_redirects(self, admin_client_logged_in, import_job):
        response = admin_client_logged_in.post(
            reverse("core:import_mapping", kwargs={"pk": import_job.pk}), {"column-0": "pseudonym"}
        )
        assert response.status_code == 302


@pytest.mark.django_db
@pytest.mark.parametrize("row", ["SECRET;note;EXTRA", "SECRET", 'SECRET;"OPEN'])
def test_upload_displays_structure_error_without_cell_contents(admin_client_logged_in, facility, media_root, row):
    response = admin_client_logged_in.post(
        reverse("core:import_create"),
        {
            "target_type": ImportJob.TargetType.CLIENT,
            "source_file": SimpleUploadedFile("import.csv", ("Pseudonym;Notizen\n" + row + "\n").encode()),
            "delimiter": "semicolon",
            "encoding": "",
        },
    )
    assert response.status_code == 200
    assert "Zeile 2" in response.content.decode()
    assert "SECRET" not in response.content.decode()
    assert not ImportJob.objects.for_facility(facility).exists()
