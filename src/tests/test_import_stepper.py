"""Fortschrittsanzeige des Import-Wizards: Stepper-Zustaende je Schritt (Refs #1813, §F.1).

Vier Schritte (Datei -> Zuordnung -> Probelauf -> Uebernahme). Schritt 4 hat
keine eigene Seite — „Uebernehmen" ist ein reiner POST ohne GET-Template —
deshalb zeigt ``core/import/detail.html`` den Stepper dauerhaft mit Schritt 4
aktiv; sein Zustand (abgeschlossen/abgebrochen) leitet sich aus ``job.status``
ab. Rein praesentational: kein neuer View-Kontext, ``core/import/partials/_stepper.html``
liest ``step`` und (fuer Schritt 4) ``job`` direkt aus dem vorhandenen Kontext.
"""

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import ImportJob
from core.services import import_engine

CSV = "Pseudonym;Kontaktstufe;Notizen\nSTEP-01;identified;Erste\n"


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


def _aria_current_step_count(html):
    return len(re.findall(r'aria-current="step"', html))


def _block_between(html, start_testid, end_testid=None):
    """Isoliert den Ausschnitt eines Stepper-Schritts anhand seiner testids —
    so laesst sich ``aria-current`` genau dem betroffenen ``<li>`` zuordnen."""
    after = html.split(f'data-testid="{start_testid}"', 1)[1]
    if end_testid is None:
        return after
    return after.split(f'data-testid="{end_testid}"', 1)[0]


@pytest.mark.django_db
class TestStepperRendersPerPage:
    def test_upload_page_shows_stepper_with_step_1_active(self, admin_client_logged_in):
        content = admin_client_logged_in.get(reverse("core:import_create")).content.decode()
        assert 'data-testid="import-stepper"' in content
        assert 'data-testid="import-step-1"' in content
        assert 'data-testid="import-step-2"' in content
        assert 'data-testid="import-step-3"' in content
        assert 'data-testid="import-step-4"' in content
        assert _aria_current_step_count(content) == 1
        assert 'aria-current="step"' in _block_between(content, "import-step-1", "import-step-2")
        assert "von 4" not in content

    def test_mapping_page_shows_stepper_with_step_2_active(self, admin_client_logged_in, uploaded_job):
        content = admin_client_logged_in.get(
            reverse("core:import_mapping", kwargs={"pk": uploaded_job.pk})
        ).content.decode()
        assert _aria_current_step_count(content) == 1
        assert 'aria-current="step"' in _block_between(content, "import-step-2", "import-step-3")
        assert "Schritt 2 von 4" not in content

    def test_mapping_page_keeps_the_row_count_sentence(self, admin_client_logged_in, uploaded_job):
        """Die dynamische Zeilenzahl-Info darf nicht verloren gehen — nur der
        „Schritt n von 4"-Rahmen entfaellt, der Satz selbst bleibt als
        Zusatztext unter dem Stepper stehen."""
        content = admin_client_logged_in.get(
            reverse("core:import_mapping", kwargs={"pk": uploaded_job.pk})
        ).content.decode()
        assert "Datenzeile" in content
        assert "Nicht zugeordnete Spalten werden ignoriert" in content

    def test_preview_page_shows_stepper_with_step_3_active(self, admin_client_logged_in, uploaded_job):
        admin_client_logged_in.post(
            reverse("core:import_mapping", kwargs={"pk": uploaded_job.pk}),
            {"column-0": "pseudonym", "column-1": "contact_stage", "column-2": "notes"},
        )
        content = admin_client_logged_in.get(
            reverse("core:import_preview", kwargs={"pk": uploaded_job.pk})
        ).content.decode()
        assert _aria_current_step_count(content) == 1
        assert 'aria-current="step"' in _block_between(content, "import-step-3", "import-step-4")
        assert "Schritt 3 von 4" not in content


@pytest.mark.django_db
class TestDetailStepFourState:
    def test_detail_page_shows_stepper_with_step_4_active(self, admin_client_logged_in, import_job):
        content = admin_client_logged_in.get(
            reverse("core:import_detail", kwargs={"pk": import_job.pk})
        ).content.decode()
        assert 'data-testid="import-stepper"' in content
        assert _aria_current_step_count(content) == 1
        assert 'aria-current="step"' in _block_between(content, "import-step-4")

    def test_not_final_job_has_no_state_suffix(self, admin_client_logged_in, import_job):
        """Ein noch laufender Import (UPLOADED) ist weder abgeschlossen noch
        abgebrochen — der Stepper darf hier keinen der beiden Endzustaende
        behaupten."""
        content = admin_client_logged_in.get(
            reverse("core:import_detail", kwargs={"pk": import_job.pk})
        ).content.decode()
        assert 'data-testid="import-step-4-state"' not in content

    def test_committed_job_shows_the_completed_state(self, admin_client_logged_in, uploaded_job):
        admin_client_logged_in.post(
            reverse("core:import_mapping", kwargs={"pk": uploaded_job.pk}),
            {"column-0": "pseudonym", "column-1": "contact_stage", "column-2": "notes"},
        )
        admin_client_logged_in.post(reverse("core:import_commit", kwargs={"pk": uploaded_job.pk}))
        content = admin_client_logged_in.get(
            reverse("core:import_detail", kwargs={"pk": uploaded_job.pk})
        ).content.decode()
        assert 'data-testid="import-step-4-state"' in content
        assert "abgeschlossen" in _block_between(content, "import-step-4-state")

    def test_aborted_job_shows_the_aborted_state(self, admin_client_logged_in, uploaded_job):
        admin_client_logged_in.post(reverse("core:import_abort", kwargs={"pk": uploaded_job.pk}))
        content = admin_client_logged_in.get(
            reverse("core:import_detail", kwargs={"pk": uploaded_job.pk})
        ).content.decode()
        assert 'data-testid="import-step-4-state"' in content
        assert "abgebrochen" in _block_between(content, "import-step-4-state")
