"""E2E: ersatzloses (physisches) Löschen eines Dateianhangs (Refs #1874).

Der erste **nutzer-getriggerte Hard-Delete** der Anwendung — alle bisherigen
physischen Löschungen hingen an Cron, Anonymisierung oder dem
Vier-Augen-Antragsweg. Ein Fehlklick ist hier nicht wiederherstellbar; genau
deshalb gehören die Journeys hierher und nicht nur in die Unit-Ebene:

* Löschweg als **lead** von der Datei-Übersicht bis zur verschwundenen ``.enc``,
* Abweisung als **staff** (kein Einstieg im UI, 403 auf der Direkt-Adresse),
* **Bestätigungsseite benennt den Datensatz** — zwei gleichnamige Dateien an
  zwei Personen dürfen keine identischen Seiten erzeugen, sonst trägt der
  Verzicht auf den Vier-Augen-Antragsweg nicht,
* **Legal Hold** blockiert, und der abgewiesene Versuch hinterlässt eine Spur,
* eine zuvor nur **entfernte** (soft-gelöschte) Kette bleibt löschbar,
* Einstieg **am Owner-Objekt**, nicht nur in der Übersicht.

Konventionen wie ``test_attachments_client_case.py``: Wegwerf-Namen mit Präfix
``E2E-PURGE-`` + Cleanup im Teardown (geteilte E2E-DB), Datenaufbau per
``manage.py shell``. Anhänge hängen hier an **Personen** — der Upload-Weg über
das Bearbeiten-Formular ist der kürzeste; die Owner-Generik der Route (Ereignis,
Person, Fall) deckt ``src/tests/test_attachment_purge.py`` ab.
"""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e

_PREFIX = "E2E-PURGE-"
_FIELD_SLUG = "e2e-purge-nachweis"

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
from core.models import Client, DocumentType, User
from core.models.document_type_field import DocumentTypeField
from core.models.field_template import FieldTemplate
u = User.objects.get(username="miriam"); fac = u.facility
ft, _ = FieldTemplate.objects.get_or_create(
    facility=fac, name="E2E-PURGE-Nachweis", defaults={"field_type": "file"}
)
dt, _ = DocumentType.objects.get_or_create(
    facility=fac, name="E2E-PURGE-Akte",
    defaults={"entity_type": "client", "sensitivity": "normal"},
)
DocumentTypeField.objects.get_or_create(document_type=dt, field_template=ft, defaults={"sort_order": 0})
pks = []
for pseudonym in ("E2E-PURGE-Person-A", "E2E-PURGE-Person-B"):
    cli, _ = Client.objects.get_or_create(
        facility=fac, pseudonym=pseudonym,
        defaults={"contact_stage": "identified", "created_by": u, "document_type": dt},
    )
    pks.append(str(cli.pk))
print(" ".join(pks))
"""

_CLEANUP = """
from core.models import Client, DocumentType, LegalHold
from core.models.field_template import FieldTemplate
from core.services.file_vault import delete_attachments_for
for cli in Client.objects.filter(pseudonym__startswith="E2E-PURGE-"):
    LegalHold.objects.filter(target_id=cli.pk).delete()
    delete_attachments_for(cli)
Client.objects.filter(pseudonym__startswith="E2E-PURGE-").delete()
DocumentType.objects.filter(name__startswith="E2E-PURGE-").delete()
FieldTemplate.objects.filter(name__startswith="E2E-PURGE-").delete()
print("ok")
"""

#: Speicherpfade der Kette einer Person + Zahl der DB-Zeilen. Der Purge ist erst
#: bewiesen, wenn BEIDES weg ist — eine Zeile ohne Datei repariert kein Sweep,
#: eine Datei ohne Zeile bliebe unentdeckt im Vault liegen.
_CHAIN_STATE = """
from core.models import Client
from core.models.attachment import Attachment
from core.services.file_vault import get_attachment_path
cli = Client.objects.get(pseudonym="{pseudonym}")
rows = list(Attachment.objects.filter(client=cli))
print(len(rows), sum(1 for a in rows if get_attachment_path(a).exists()))
"""


def _chain_state(e2e_env, pseudonym):
    """``(anzahl_db_zeilen, anzahl_dateien_auf_disk)`` der Kette einer Person."""
    rows, files = _run_shell(_CHAIN_STATE.format(pseudonym=pseudonym), e2e_env).split()
    return int(rows), int(files)


@pytest.fixture
def purge_data(e2e_env):
    """Wegwerf-Konfiguration + zwei Personen; Cleanup auch bei Testfehlern."""
    out = _run_shell(_SETUP, e2e_env).split()
    yield {"client_a": out[0], "client_b": out[1]}
    _run_shell(_CLEANUP, e2e_env)


@pytest.fixture
def _test_pdf(tmp_path):
    path = tmp_path / "e2e-purge-bescheid.pdf"
    path.write_bytes(_VALID_PDF_BYTES)
    return str(path)


def _upload(page, base_url, client_pk, pdf_path):
    """Datei im Bearbeiten-Formular der Person hochladen und speichern."""
    page.goto(f"{base_url}/clients/{client_pk}/edit/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector(f'input[name="{_FIELD_SLUG}"]', timeout=30000)
    page.set_input_files(f'input[name="{_FIELD_SLUG}"]', pdf_path)
    page.click('button:has-text("Speichern")')
    page.wait_for_url(lambda url: "/edit/" not in url, timeout=30000)


def _replace(page, base_url, client_pk, pdf_path):
    """Zweite Version derselben Kette — der Purge muss beide mitnehmen."""
    page.goto(f"{base_url}/clients/{client_pk}/edit/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("[data-testid^='attachment-replace-']", timeout=30000)
    page.set_input_files("[data-testid^='attachment-replace-']", pdf_path)
    page.click('button:has-text("Speichern")')
    page.wait_for_url(lambda url: "/edit/" not in url, timeout=30000)


def _remove(page, base_url, client_pk):
    """Datei über die Checkbox „Entfernen" aus der Dokumentation nehmen (Soft-Delete)."""
    page.goto(f"{base_url}/clients/{client_pk}/edit/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("[data-testid^='attachment-remove-']", timeout=30000)
    page.check("[data-testid^='attachment-remove-']")
    page.click('button:has-text("Speichern")')
    page.wait_for_url(lambda url: "/edit/" not in url, timeout=30000)


def _open_overview(page, base_url, pseudonym, *, removed=False):
    """Datei-Übersicht, auf die Person gefiltert (Anker-First)."""
    suffix = "&removed=1" if removed else ""
    page.goto(f"{base_url}/attachments/?client={pseudonym}{suffix}")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("#attachment-table", timeout=30000)


def _delete_url(page, base_url, pseudonym):
    """Adresse der Bestätigungsseite aus der gefilterten Übersicht ziehen."""
    _open_overview(page, base_url, pseudonym)
    link = page.locator("[data-testid='attachment-delete-link']").first
    link.wait_for(timeout=30000)
    return base_url + link.get_attribute("href")


class TestLeadPurgeJourney:
    @pytest.mark.smoke
    def test_lead_loescht_kette_samt_dateien_von_der_uebersicht(
        self, staff_page, lead_page, base_url, e2e_env, purge_data, _test_pdf, tmp_path
    ):
        _upload(staff_page, base_url, purge_data["client_a"], _test_pdf)
        replacement = tmp_path / "e2e-purge-bescheid-neu.pdf"
        replacement.write_bytes(_VALID_PDF_BYTES)
        _replace(staff_page, base_url, purge_data["client_a"], str(replacement))
        assert _chain_state(e2e_env, "E2E-PURGE-Person-A") == (2, 2), "Kette mit zwei Versionen erwartet"

        page = lead_page
        page.goto(_delete_url(page, base_url, "E2E-PURGE-Person-A"))
        page.wait_for_selector("[data-testid='attachment-purge-confirm']", timeout=30000)

        # Die Seite muss den DATENSATZ benennen, nicht nur die Datei.
        assert page.locator("[data-testid='attachment-purge-filename']").inner_text() == "e2e-purge-bescheid-neu.pdf"
        assert "E2E-PURGE-Person-A" in page.locator("[data-testid='attachment-purge-owner']").inner_text()
        assert page.locator("[data-testid='attachment-purge-doc-type']").inner_text() == "E2E-PURGE-Akte"
        assert page.locator("[data-testid='attachment-purge-versions']").inner_text() == "2"
        assert page.locator("[data-testid='attachment-purge-uploaded-at']").count() == 1
        # Unwiderruflichkeit „in der Anwendung" + Einschränkung für Backups.
        assert "Anwendung" in page.locator("[data-testid='attachment-purge-warning']").inner_text()
        assert page.locator("[data-testid='attachment-purge-backup-hint']").count() == 1

        page.click("[data-testid='attachment-purge-confirm']")
        page.wait_for_url(lambda url: "/delete/" not in url, timeout=30000)
        assert "endgültig gelöscht" in page.locator("body").inner_text()

        # Zeilen UND Dateien sind weg — beide Versionen.
        assert _chain_state(e2e_env, "E2E-PURGE-Person-A") == (0, 0)
        # Kein verwaister Marker: das Bearbeiten-Formular zeigt keinen Eintrag mehr.
        staff_page.goto(f"{base_url}/clients/{purge_data['client_a']}/edit/")
        staff_page.wait_for_load_state("domcontentloaded")
        assert staff_page.locator("[data-testid^='attachment-remove-']").count() == 0


class TestStaffIsRejected:
    def test_staff_sieht_keinen_einstieg_und_bekommt_403(
        self, staff_page, lead_page, base_url, e2e_env, purge_data, _test_pdf
    ):
        _upload(staff_page, base_url, purge_data["client_a"], _test_pdf)
        url = _delete_url(lead_page, base_url, "E2E-PURGE-Person-A")

        _open_overview(staff_page, base_url, "E2E-PURGE-Person-A")
        assert staff_page.locator("[data-testid='attachment-delete-link']").count() == 0
        assert staff_page.locator("[data-testid='attachment-removed-toggle']").count() == 0

        assert staff_page.request.get(url).status == 403
        # Mit gültigem CSRF-Token, damit das 403 die AuthZ belegt und nicht den
        # CSRF-Guard davor: Django akzeptiert den Cookie-Wert als POST-Token.
        cookies = {cookie["name"]: cookie["value"] for cookie in staff_page.context.cookies()}
        response = staff_page.request.post(url, form={"csrfmiddlewaretoken": cookies.get("csrftoken", "")})
        assert response.status == 403
        assert _chain_state(e2e_env, "E2E-PURGE-Person-A") == (1, 1)


class TestConfirmationNamesTheRecord:
    def test_zwei_gleichnamige_dateien_ergeben_unterscheidbare_seiten(
        self, staff_page, lead_page, base_url, e2e_env, purge_data, _test_pdf
    ):
        """Der Fehlklick-Fall: dieselbe ``Bescheid.pdf`` an zwei Personen."""
        _upload(staff_page, base_url, purge_data["client_a"], _test_pdf)
        _upload(staff_page, base_url, purge_data["client_b"], _test_pdf)

        page = lead_page
        texts = {}
        for pseudonym in ("E2E-PURGE-Person-A", "E2E-PURGE-Person-B"):
            page.goto(_delete_url(page, base_url, pseudonym))
            page.wait_for_selector("[data-testid='attachment-purge-owner']", timeout=30000)
            texts[pseudonym] = page.locator("[data-testid='attachment-purge-owner']").inner_text()

        assert "E2E-PURGE-Person-A" in texts["E2E-PURGE-Person-A"]
        assert "E2E-PURGE-Person-B" not in texts["E2E-PURGE-Person-A"]
        assert "E2E-PURGE-Person-B" in texts["E2E-PURGE-Person-B"]
        assert "E2E-PURGE-Person-A" not in texts["E2E-PURGE-Person-B"]


class TestLegalHoldBlocks:
    def test_hold_sperrt_die_schaltflaeche_und_den_post(
        self, staff_page, lead_page, base_url, e2e_env, purge_data, _test_pdf
    ):
        _upload(staff_page, base_url, purge_data["client_a"], _test_pdf)
        _run_shell(
            "from core.models import Client, LegalHold, User\n"
            'u = User.objects.get(username="emma")\n'
            'cli = Client.objects.get(pseudonym="E2E-PURGE-Person-A")\n'
            'LegalHold.objects.create(facility=cli.facility, target_type="Client", '
            'target_id=cli.pk, reason="E2E-PURGE-Beweissicherung", created_by=u)\n'
            'print("ok")',
            e2e_env,
        )

        page = lead_page
        url = _delete_url(page, base_url, "E2E-PURGE-Person-A")
        page.goto(url)
        page.wait_for_selector("[data-testid='attachment-purge-blocker-hold']", timeout=30000)
        assert page.locator("[data-testid='attachment-purge-confirm']").is_disabled()

        # Der Blocker sitzt im Service, nicht nur im Template: ein trotzdem
        # abgesendeter POST muss abgewiesen werden.
        token = page.locator("input[name='csrfmiddlewaretoken']").first.get_attribute("value")
        response = page.request.post(url, form={"csrfmiddlewaretoken": token})
        assert response.status in (200, 302)
        assert _chain_state(e2e_env, "E2E-PURGE-Person-A") == (1, 1), "Datei unter Hold darf nicht fallen"

        # Der abgewiesene Versuch hinterlässt eine Spur.
        blocked = _run_shell(
            "from core.models import AuditLog\n"
            "print(AuditLog.objects.filter(action=AuditLog.Action.LEGAL_HOLD, "
            'detail__category="legal_hold_blocked_purge").count())',
            e2e_env,
        )
        assert int(blocked) >= 1, "Gesperrter Loeschversuch muss protokolliert sein"


class TestRemovedChainStaysPurgeable:
    def test_entfernte_datei_bleibt_ueber_die_entfernt_ansicht_loeschbar(
        self, staff_page, lead_page, base_url, e2e_env, purge_data, _test_pdf
    ):
        """Der Anlassfall des Tickets darf sich nicht selbst blockieren.

        Wer die irrtümlich hochgeladene Datei zuerst über „Entfernen" aus der
        Dokumentation nimmt, muss sie danach noch endgültig löschen können —
        sonst bleibt die ``.enc`` bis zur Anonymisierung der Person liegen.
        """
        _upload(staff_page, base_url, purge_data["client_a"], _test_pdf)
        _remove(staff_page, base_url, purge_data["client_a"])
        assert _chain_state(e2e_env, "E2E-PURGE-Person-A") == (1, 1), "Entfernen ist nur ein Soft-Delete"

        page = lead_page
        # Aktive Ansicht zeigt die Zeile nicht mehr …
        _open_overview(page, base_url, "E2E-PURGE-Person-A")
        assert page.locator("[data-testid='attachment-delete-link']").count() == 0
        # … die Entfernt-Ansicht schon, ohne Download-Einstieg.
        page.click("[data-testid='attachment-removed-toggle']")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_selector("[data-testid='attachment-removed-badge']", timeout=30000)
        _open_overview(page, base_url, "E2E-PURGE-Person-A", removed=True)
        link = page.locator("[data-testid='attachment-delete-link']").first
        link.wait_for(timeout=30000)
        assert page.locator("[data-testid='attachment-removed-at']").count() >= 1

        page.goto(base_url + link.get_attribute("href"))
        page.wait_for_selector("[data-testid='attachment-purge-removed-at']", timeout=30000)
        page.click("[data-testid='attachment-purge-confirm']")
        page.wait_for_url(lambda url: "/delete/" not in url, timeout=30000)
        assert _chain_state(e2e_env, "E2E-PURGE-Person-A") == (0, 0)


class TestEntryPointAtTheOwner:
    def test_personendetail_bietet_den_loeschlink_nur_der_leitung(
        self, staff_page, lead_page, base_url, e2e_env, purge_data, _test_pdf
    ):
        """Akzeptanzkriterium #1874: Einstieg an der Übersicht UND am Owner."""
        _upload(staff_page, base_url, purge_data["client_a"], _test_pdf)
        detail = f"{base_url}/clients/{purge_data['client_a']}/"

        lead_page.goto(detail)
        lead_page.wait_for_load_state("domcontentloaded")
        lead_page.wait_for_selector("[data-testid='dynamic-attachment-list']", timeout=30000)
        assert lead_page.locator("[data-testid='owner-attachment-purge-link']").count() == 1

        staff_page.goto(detail)
        staff_page.wait_for_load_state("domcontentloaded")
        staff_page.wait_for_selector("[data-testid='dynamic-attachment-list']", timeout=30000)
        assert staff_page.locator("[data-testid='owner-attachment-purge-link']").count() == 0
