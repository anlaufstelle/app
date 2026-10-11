"""E2E: owner-generische Dateianhänge an Person und Fall (M24, Refs #1738).

Journeys aus #1738/AP6: Upload an Person und Fall, Assistenz-Negativprobe,
Sensitivity HIGH-FILE, Übersicht (Owner-Spalte + Personen-Filter),
Typwechsel-Confirm mit Datei-Zählung, Ersetzen mit Vorversion.

Konventionen: Wegwerf-Namen mit Präfix ``E2E-M24-`` + Cleanup im Teardown
(geteilte E2E-DB); Datenaufbau per ``manage.py shell`` (Muster
``_select_qualified_client`` in test_file_vault.py). Ereignis-Anhänge deckt
test_file_vault.py als Regression weiter ab.
"""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e

_PREFIX = "E2E-M24-"

_VALID_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _run_shell(code, e2e_env):
    python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable
    result = subprocess.run(
        [python, "src/manage.py", "shell", "--no-imports", "-c", code],
        capture_output=True,
        text=True,
        env=e2e_env,
    )
    assert result.returncode == 0, f"shell-Setup fehlgeschlagen: {result.stderr[-800:]}"
    return result.stdout.strip()


_SETUP = """
from core.models import Case, Client, DocumentType, User
from core.models.document_type_field import DocumentTypeField
from core.models.field_template import FieldTemplate
u = User.objects.get(username="miriam"); fac = u.facility
ft, _ = FieldTemplate.objects.get_or_create(facility=fac, name="E2E-M24-Nachweis", defaults={"field_type": "file"})
# High types reject inheriting fields; a file field is no report field, so it needs "high".
ft_high, _ = FieldTemplate.objects.get_or_create(
    facility=fac, name="E2E-M24-Nachweis-Hoch", defaults={"field_type": "file", "sensitivity": "high"},
)
dts = {}
for key, (name, entity, sens) in {
    "p": ("E2E-M24-P-Akte", "client", "normal"),
    "p2": ("E2E-M24-P2-Akte", "client", "normal"),
    "f": ("E2E-M24-F-Akte", "case", "normal"),
    "h": ("E2E-M24-H-Akte", "client", "high"),
}.items():
    dt, _ = DocumentType.objects.get_or_create(
        facility=fac, name=name,
        defaults={"entity_type": entity, "sensitivity": sens},
    )
    if key != "p2":
        template = ft_high if key == "h" else ft
        DocumentTypeField.objects.get_or_create(document_type=dt, field_template=template, defaults={"sort_order": 0})
    dts[key] = dt
cli, _ = Client.objects.get_or_create(
    facility=fac, pseudonym="E2E-M24-Person",
    defaults={"contact_stage": "identified", "created_by": u, "document_type": dts["p"]},
)
cli_h, _ = Client.objects.get_or_create(
    facility=fac, pseudonym="E2E-M24-Person-H",
    defaults={"contact_stage": "identified", "created_by": u, "document_type": dts["h"]},
)
case, _ = Case.objects.get_or_create(
    facility=fac, client=cli, title="E2E-M24-Fall",
    defaults={"created_by": u, "document_type": dts["f"]},
)
print(cli.pk, cli_h.pk, case.pk)
"""

_CLEANUP = """
from core.models import Case, Client, DocumentType
from core.models.field_template import FieldTemplate
from core.services.file_vault import delete_attachments_for
for cli in Client.objects.filter(pseudonym__startswith="E2E-M24-"):
    for case in Case.objects.filter(client=cli):
        delete_attachments_for(case)
    for ev in cli.events.all():
        delete_attachments_for(ev)
    delete_attachments_for(cli)
Case.objects.filter(title__startswith="E2E-M24-").delete()
Client.objects.filter(pseudonym__startswith="E2E-M24-").delete()
DocumentType.objects.filter(name__startswith="E2E-M24-").delete()
FieldTemplate.objects.filter(name__startswith="E2E-M24-").delete()
print("ok")
"""


@pytest.fixture
def m24_data(e2e_env):
    """Wegwerf-Konfiguration + Personen/Fall; Cleanup auch bei Testfehlern."""
    out = _run_shell(_SETUP, e2e_env).split()
    yield {"client_pk": out[0], "client_h_pk": out[1], "case_pk": out[2]}
    _run_shell(_CLEANUP, e2e_env)


@pytest.fixture
def _test_pdf(tmp_path):
    path = tmp_path / "m24-nachweis.pdf"
    path.write_bytes(_VALID_PDF_BYTES)
    return str(path)


def _upload_on_edit(page, base_url, edit_path, pdf_path, field_slug="e2e-m24-nachweis"):
    """Datei im Bearbeiten-Formular des Owners hochladen und speichern."""
    page.goto(f"{base_url}{edit_path}")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector(f'input[name="{field_slug}"]', timeout=10000)
    page.set_input_files(f'input[name="{field_slug}"]', pdf_path)
    page.click('button:has-text("Speichern")')
    page.wait_for_url(lambda url: "/edit/" not in url, timeout=10000)


class TestClientAttachmentJourney:
    @pytest.mark.smoke
    def test_upload_ersetzen_vorversion_download_an_person(
        self, staff_page, base_url, e2e_env, m24_data, _test_pdf, tmp_path
    ):
        page = staff_page
        client_pk = m24_data["client_pk"]

        # Upload im Personen-Formular
        _upload_on_edit(page, base_url, f"/clients/{client_pk}/edit/", _test_pdf)

        # Detailseite zeigt die Datei mit Download-Link
        page.goto(f"{base_url}/clients/{client_pk}/")
        page.wait_for_selector("text=m24-nachweis.pdf", timeout=10000)
        with page.expect_download() as download_info:
            page.locator("a:has-text('m24-nachweis.pdf')").click()
        assert download_info.value.suggested_filename == "m24-nachweis.pdf"

        # Ersetzen: Vorversion bleibt erreichbar
        replacement = tmp_path / "m24-ersatz.pdf"
        replacement.write_bytes(_VALID_PDF_BYTES)
        page.goto(f"{base_url}/clients/{client_pk}/edit/")
        page.wait_for_selector("[data-testid^='attachment-replace-']", timeout=10000)
        page.set_input_files("[data-testid^='attachment-replace-']", str(replacement))
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/edit/" not in url, timeout=10000)

        page.goto(f"{base_url}/clients/{client_pk}/")
        page.wait_for_selector("text=m24-ersatz.pdf", timeout=10000)
        assert page.locator("text=m24-nachweis.pdf").count() >= 1, "Vorversion muss sichtbar bleiben"

    def test_typwechsel_confirm_nennt_dateizahl(self, staff_page, base_url, e2e_env, m24_data, _test_pdf):
        page = staff_page
        client_pk = m24_data["client_pk"]
        _upload_on_edit(page, base_url, f"/clients/{client_pk}/edit/", _test_pdf)

        page.goto(f"{base_url}/clients/{client_pk}/edit/")
        page.wait_for_load_state("domcontentloaded")
        # Typwechsel -> HTMX tauscht das Feldset, beim Speichern kommt der
        # window.confirm aus #1733 (jetzt mit Datei-Zählung).
        # Refs #1855: Statt einer festen Sekunde auf die Antwort des
        # Feld-Partials warten UND auf den frisch eingeswappten Knoten. Der
        # ``data-pre-swap``-Stempel ist noetig, weil ``#dynamic-fields`` schon
        # server-seitig gerendert ist — ein nacktes ``wait_for_selector`` wuerde
        # sonst den VOR-Swap-Knoten matchen (Muster aus
        # ``test_file_vault.select_doc_type_and_wait``, Refs #1797).
        page.evaluate(
            "() => document.querySelectorAll('#dynamic-fields > *')"
            ".forEach((el) => el.setAttribute('data-pre-swap', '1'))"
        )
        with page.expect_response(lambda r: "/partials/clients/fields/" in r.url and r.ok, timeout=15000):
            page.select_option('select[name="document_type"]', label="E2E-M24-P2-Akte")
        # ``state="attached"``: der frisch eingeswappte Wurzel-Knoten des Partials
        # (``x-data="conditionEvaluator"``) hat bei diesem Typ keine sichtbare
        # Ausdehnung — geprueft wird der SWAP, nicht die Sichtbarkeit.
        page.wait_for_selector("#dynamic-fields > :not([data-pre-swap])", state="attached", timeout=10000)

        dialog_messages = []
        page.once("dialog", lambda dialog: (dialog_messages.append(dialog.message), dialog.accept()))
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/edit/" not in url, timeout=10000)

        assert dialog_messages, "Typwechsel mit Datei muss den Confirm ausloesen"
        assert "Datei" in dialog_messages[0], f"Confirm nennt die Dateien nicht: {dialog_messages[0]!r}"


class TestCaseAttachmentJourney:
    def test_upload_und_download_an_fall(self, staff_page, base_url, e2e_env, m24_data, _test_pdf):
        page = staff_page
        case_pk = m24_data["case_pk"]

        _upload_on_edit(page, base_url, f"/cases/{case_pk}/edit/", _test_pdf)

        page.goto(f"{base_url}/cases/{case_pk}/")
        page.wait_for_selector("text=m24-nachweis.pdf", timeout=10000)
        with page.expect_download() as download_info:
            page.locator("a:has-text('m24-nachweis.pdf')").click()
        assert download_info.value.suggested_filename == "m24-nachweis.pdf"


class TestAssistantNegativeProbes:
    def test_assistenz_kein_fall_download_und_keine_fall_zeilen(
        self, staff_page, assistant_page, base_url, e2e_env, m24_data, _test_pdf
    ):
        # Staff laedt die Datei an den Fall
        _upload_on_edit(staff_page, base_url, f"/cases/{m24_data['case_pk']}/edit/", _test_pdf)

        # Download-URL des Fall-Anhangs ermitteln (juengste Fall-Zeile)
        attachment_pk = _run_shell(
            "from core.models import Attachment; "
            "a = Attachment.objects.filter(case__isnull=False).order_by('-created_at').first(); "
            "print(a.pk)",
            e2e_env,
        )
        url = f"{base_url}/cases/{m24_data['case_pk']}/attachments/{attachment_pk}/download/"

        # Assistenz: direkter Zugriff faellt fail-closed (404/403, nie 200)
        response = assistant_page.request.get(url)
        assert response.status in (403, 404), f"Assistenz-Download muss geblockt sein, war {response.status}"

        # Uebersicht zeigt der Assistenz keine fall-eigenen Zeilen
        assistant_page.goto(f"{base_url}/attachments/")
        assistant_page.wait_for_load_state("domcontentloaded")
        assert assistant_page.locator("text=E2E-M24-Fall").count() == 0

    def test_high_file_typ_bleibt_fuer_assistenz_unsichtbar(
        self, lead_page, assistant_page, base_url, e2e_env, m24_data, _test_pdf
    ):
        # Lead laedt an die HIGH-Person (STAFF darf max. ELEVATED sehen,
        # ROLE_MAX_SENSITIVITY - erster Lauf scheiterte genau daran)
        _upload_on_edit(
            lead_page,
            base_url,
            f"/clients/{m24_data['client_h_pk']}/edit/",
            _test_pdf,
            field_slug="e2e-m24-nachweis-hoch",
        )

        attachment_pk = _run_shell(
            "from core.models import Attachment; "
            "a = Attachment.objects.filter(client__isnull=False).order_by('-created_at').first(); "
            "print(a.pk)",
            e2e_env,
        )
        url = f"{base_url}/clients/{m24_data['client_h_pk']}/attachments/{attachment_pk}/download/"
        response = assistant_page.request.get(url)
        assert response.status == 404, f"HIGH-Typ muss fuer Assistenz 404 sein (nicht leckend), war {response.status}"

        # Detailseite: keine Datei-Anzeige fuer die Assistenz
        assistant_page.goto(f"{base_url}/clients/{m24_data['client_h_pk']}/")
        assistant_page.wait_for_load_state("domcontentloaded")
        assert assistant_page.locator("text=m24-nachweis.pdf").count() == 0


class TestAttachmentOverview:
    def test_owner_spalte_und_personen_filter(self, staff_page, base_url, e2e_env, m24_data, _test_pdf):
        page = staff_page
        # Je eine Datei an Person und Fall
        _upload_on_edit(page, base_url, f"/clients/{m24_data['client_pk']}/edit/", _test_pdf)
        _upload_on_edit(page, base_url, f"/cases/{m24_data['case_pk']}/edit/", _test_pdf)

        page.goto(f"{base_url}/attachments/?client={m24_data['client_pk']}")
        page.wait_for_load_state("domcontentloaded")
        # Personen-Filter fasst alle Owner-Quellen der Person zusammen:
        # die personen-eigene UND die fall-eigene Zeile erscheinen.
        assert page.locator("text=m24-nachweis.pdf").count() >= 2
        # Owner-Spalte zeigt bewusst nur die generische Typ-Beschriftung
        # ("Person"/"Fall" als Link, keine Pseudonyme in der Uebersicht)
        assert page.locator("table a:has-text('Person')").count() >= 1
        assert page.locator("table a:has-text('Fall')").count() >= 1
