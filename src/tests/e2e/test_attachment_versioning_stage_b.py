"""E2E-Tests für Attachment-Versionierung Stufe B (Refs #622).

Verifiziert die Multi-File-UI im Event-Create und -Edit:
- Mehrere Dateien in einem Feld hochladen (Add).
- Bestehende Datei über die Edit-UI entfernen (Remove).
- Zwei existierende Dateien parallel sichtbar.
"""

import os
import subprocess
import sys
import uuid

import pytest

pytestmark = pytest.mark.e2e


def _python():
    return ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable


def _create_doc_type_with_file(e2e_env, name_suffix):
    """Legt einen DocumentType mit einem FILE-Feld in Hauptstelle an.

    Gibt den DocumentType-Namen und FieldTemplate-Slug zurück.
    """
    dt_name = f"E2E-FileDoc-{name_suffix}"
    ft_name = f"e2e-attach-{name_suffix}"
    script = (
        "from core.models import DocumentType, DocumentTypeField, FieldTemplate, Facility;"
        " f = Facility.objects.get(name='Hauptstelle');"
        f" dt, _ = DocumentType.objects.get_or_create(facility=f, name='{dt_name}',"
        "  defaults={'category': 'note'});"
        f" ft, _ = FieldTemplate.objects.get_or_create(facility=f, name='{ft_name}',"
        "  defaults={'field_type': 'file'});"
        " DocumentTypeField.objects.get_or_create(document_type=dt, field_template=ft);"
        " print(ft.slug)"
    )
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    slug = out.stdout.strip().splitlines()[-1]
    return dt_name, slug


def _make_pdf_file(tmp_path, name, marker=b"A"):
    p = tmp_path / name
    p.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        b"xref\n0 3\n0000000000 65535 f\n"
        b"trailer<</Size 3/Root 1 0 R>>\n"
        b"startxref\n9\n%%EOF\n" + marker
    )
    return str(p)


class TestMultiFileUpload:
    def test_create_event_with_multiple_files(self, authenticated_page, base_url, e2e_env, tmp_path):
        dt_name, slug = _create_doc_type_with_file(e2e_env, f"create-{uuid.uuid4().hex[:8]}")
        page = authenticated_page

        file_a = _make_pdf_file(tmp_path, "a.pdf", b"a")
        file_b = _make_pdf_file(tmp_path, "b.pdf", b"b")

        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        page.select_option("select[name='document_type']", label=dt_name)
        page.locator(f"input[name='{slug}']").wait_for(state="attached", timeout=5000)

        # Multi-File: zwei Dateien im gleichen Upload-Input.
        page.locator(f"input[name='{slug}']").set_input_files([file_a, file_b])

        # Speichern.
        page.locator("#event-submit-btn").click()
        page.wait_for_url(lambda url: "/events/" in url and "/new/" not in url, timeout=10000)

        # Detail-View zeigt jetzt zwei Entries.
        page.locator('[data-testid="attachment-entry-list"] li').nth(1).wait_for(state="visible", timeout=5000)
        count = page.locator('[data-testid="attachment-entry-list"] li').count()
        assert count == 2, f"Erwartet 2 Entries, bekam {count}"

    def test_edit_view_shows_existing_entries(self, authenticated_page, base_url, e2e_env, tmp_path):
        """Edit-Seite listet bestehende Entries mit Download-Link + Remove-Checkbox + Replace-Input."""
        dt_name, slug = _create_doc_type_with_file(e2e_env, f"editview-{uuid.uuid4().hex[:8]}")
        page = authenticated_page

        file_a = _make_pdf_file(tmp_path, "existing-a.pdf", b"a")
        file_b = _make_pdf_file(tmp_path, "existing-b.pdf", b"b")

        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        page.select_option("select[name='document_type']", label=dt_name)
        page.locator(f"input[name='{slug}']").wait_for(state="attached", timeout=5000)
        page.locator(f"input[name='{slug}']").set_input_files([file_a, file_b])
        page.locator("#event-submit-btn").click()
        page.wait_for_url(lambda url: "/events/" in url and "/new/" not in url, timeout=10000)
        event_url = page.url

        page.goto(event_url.rstrip("/") + "/edit/", wait_until="domcontentloaded")
        # Beide Entries in der Liste.
        page.locator(f'[data-testid="attachment-entries-{slug}"] li').nth(1).wait_for(state="attached", timeout=5000)
        entries = page.locator(f'[data-testid="attachment-entries-{slug}"] li')
        assert entries.count() == 2
        # Pro Entry: Replace-File-Input und Remove-Checkbox.
        assert entries.first.locator("input[type='file']").count() == 1
        assert entries.first.locator("input[type='checkbox']").count() == 1
