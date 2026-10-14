"""E2E: Datei-Upload — TIFF-Seitengrenze (ATT #2582).

Ein kleines TIFF mit 101 Seiten wird mit einer verständlichen Bildgrenzen-Meldung
abgewiesen; ein kleines dreiseitiges TIFF lässt sich hochladen und mit allen Seiten
und ohne Aufnahme-Metadaten herunterladen.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .test_attachments_client_case import _CLEANUP, _SETUP, _run_shell

pytestmark = pytest.mark.e2e

FIELD_SLUG = "e2e-m24-nachweis"
_ENABLE_TIFF = """
from core.models import User
s = User.objects.get(username="miriam").facility.settings
print("prev=" + s.allowed_file_types)
s.allowed_file_types = s.allowed_file_types + ",tif,tiff"
s.save(update_fields=["allowed_file_types"])
"""
_RESTORE = """
from core.models import User
s = User.objects.get(username="miriam").facility.settings
s.allowed_file_types = {previous!r}
s.save(update_fields=["allowed_file_types"])
print("ok")
"""


@pytest.fixture
def tiff_scene(e2e_env):
    out = _run_shell(_SETUP, e2e_env).split()
    enable = _run_shell(_ENABLE_TIFF, e2e_env)
    previous = next(line for line in enable.splitlines() if line.startswith("prev=")).removeprefix("prev=")
    yield {"client_pk": out[0]}
    _run_shell(_RESTORE.format(previous=previous), e2e_env)
    _run_shell(_CLEANUP, e2e_env)


def _make_tiff(path, pages: int) -> str:
    """A tiny multi-page TIFF carrying EXIF capture metadata (camera make) on every page."""
    from PIL import Image

    frames = [Image.new("RGB", (8, 8), (index * 2 % 255, 40, 80)) for index in range(pages)]
    exif = Image.Exif()
    exif[0x010F] = "QAB-CameraMaker"  # Make
    exif[0x0110] = "QAB-CameraModel"  # Model
    frames[0].save(
        path,
        format="TIFF",
        save_all=True,
        append_images=frames[1:],
        exif=exif,
    )
    return str(path)


def _upload(page, base_url, client_pk, tiff_path):
    page.goto(f"{base_url}/clients/{client_pk}/edit/", wait_until="domcontentloaded")
    page.wait_for_selector(f'input[name="{FIELD_SLUG}"]', timeout=10000)
    page.set_input_files(f'input[name="{FIELD_SLUG}"]', tiff_path)
    page.click('button:has-text("Speichern")')


class TestTiffPageLimit:
    def test_101_pages_are_rejected_with_a_clear_message(self, staff_page, base_url, tiff_scene, tmp_path):
        page = staff_page
        _upload(page, base_url, tiff_scene["client_pk"], _make_tiff(tmp_path / "zu-viele.tiff", 101))
        page.wait_for_load_state("domcontentloaded")
        assert "/edit/" in page.url, "the over-limit TIFF must not be accepted"
        expect(page.locator("#main-content")).to_contain_text(
            re.compile(r"Bild enthält zu viele Frames oder Seiten\. Maximum: 100\."), timeout=10000
        )
        assert page.locator("text=Internal Server Error").count() == 0

    def test_three_pages_upload_and_download_with_all_pages_and_no_capture_metadata(
        self, staff_page, base_url, tiff_scene, tmp_path
    ):
        from PIL import Image

        page = staff_page
        source = _make_tiff(tmp_path / "drei-seiten.tiff", 3)
        # Precondition: the uploaded file really carries the capture metadata the download must not.
        assert b"QAB-CameraMaker" in Path(source).read_bytes(), "test file lacks the capture metadata"
        _upload(page, base_url, tiff_scene["client_pk"], source)
        page.wait_for_url(lambda url: "/edit/" not in url, timeout=15000)

        page.goto(f"{base_url}/clients/{tiff_scene['client_pk']}/", wait_until="domcontentloaded")
        link = page.locator("a:has-text('drei-seiten.tiff')").first
        expect(link).to_be_visible(timeout=10000)
        with page.expect_download() as info:
            link.click()
        data = Path(info.value.path()).read_bytes()
        image = Image.open(io.BytesIO(data))
        assert getattr(image, "n_frames", 1) == 3, f"expected 3 pages, got {getattr(image, 'n_frames', 1)}"
        assert b"QAB-CameraMaker" not in data and b"QAB-CameraModel" not in data, "capture metadata survived"
