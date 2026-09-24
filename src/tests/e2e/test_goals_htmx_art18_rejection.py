"""E2E: fachliche Absage bei Wirkungszielen bleibt ein Hinweis (Refs #2358).

Bei einer Person mit eingeschraenkter Verarbeitung (Art. 18 DSGVO) lehnt der
Server das Anlegen eines Meilensteins mit HTTP 409 und der
Einschraenkungs-Meldung ab. Seit der globalen 4xx-Swap-Regel (Refs #2232)
setzte htmx diesen Klartext an die Stelle des ganzen Kastens
„Wirkungsziele“ — Ziel und vorhandene Meilensteine waren bis zum Neuladen
verschwunden. Die Absage traegt jetzt ``HX-Reswap: none``: der Kasten bleibt
stehen, der globale Fehler-Toast (``#htmx-error-toast``) zeigt die Meldung.

Gegenprobe: ohne Einschraenkung wird derselbe Meilenstein angelegt und
eingewechselt.
"""

from __future__ import annotations

import re
import uuid

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

_CLIENT_DETAIL_RE = re.compile(r"/clients/([0-9a-f-]+)/$")
_CASE_DETAIL_RE = re.compile(r"/cases/[0-9a-f-]+/$")
_SUBMIT = "#main-content button[type='submit']"
_MELDUNG_ANFANG = "Für diese Person ist die Verarbeitung nach Art. 18 DSGVO eingeschränkt"


def _person_mit_fall_ziel_und_meilenstein(page, base_url):
    """Wegwerf-Person, Fall, Ziel und ein Meilenstein — nie Seed-Personen einschraenken."""
    kennung = uuid.uuid4().hex[:6]
    page.goto(f"{base_url}/clients/new/", wait_until="domcontentloaded")
    page.fill('input[name="pseudonym"]', f"E2E-Art18-{kennung}")
    page.locator(_SUBMIT).click()
    page.wait_for_url(_CLIENT_DETAIL_RE)
    client_url = page.url
    client_id = _CLIENT_DETAIL_RE.search(client_url).group(1)

    page.goto(f"{base_url}/cases/new/?client={client_id}", wait_until="domcontentloaded")
    page.fill('input[name="title"]', f"E2E-Art18-Fall-{kennung}")
    page.locator(_SUBMIT).click()
    page.wait_for_url(_CASE_DETAIL_RE)
    case_url = page.url

    goal = f"E2E-Art18-Ziel-{kennung}"
    section = page.locator("#goals-section")
    section.locator("input[name='title']").last.fill(goal)
    section.locator("button:has-text('Hinzufügen')").click()
    expect(page.locator(f"#goals-section h3:text-is('{goal}')")).to_be_visible(timeout=5000)

    meilenstein = f"E2E-Art18-MS-{kennung}"
    _meilenstein_absenden(page, meilenstein)
    expect(page.locator(f"#goals-section :text-is('{meilenstein}')").first).to_be_visible(timeout=5000)
    return client_url, case_url, goal, meilenstein


def _meilenstein_absenden(page, titel):
    form = page.locator("form[hx-post*='/milestones/'][hx-post$='/']:has(input[placeholder='Neuer Meilenstein'])").first
    # Ein vorheriger Swap muss abgeschlossen sein, sonst ist das neue Formular
    # noch nicht von htmx verarbeitet und sendet nativ.
    page.wait_for_function("() => !document.querySelector('.htmx-swapping, .htmx-settling, .htmx-request')")
    form.locator("input[placeholder='Neuer Meilenstein']").fill(titel)
    with page.expect_response(lambda r: "/milestones/new/" in r.url) as info:
        form.locator("button[type='submit']").click()
    return info.value


def _einschraenken(page, client_url):
    page.goto(client_url, wait_until="domcontentloaded")
    page.locator("[data-testid='client-processing-restrict-submit']").click()
    expect(page.locator("[data-testid='client-processing-restricted-badge']")).to_be_visible(timeout=5000)


class TestMeilensteinBeiEinschraenkung:
    def test_absage_laesst_kasten_stehen_und_zeigt_hinweis(self, staff_page, base_url):
        page = staff_page
        client_url, case_url, goal, meilenstein = _person_mit_fall_ziel_und_meilenstein(page, base_url)
        _einschraenken(page, client_url)

        page.goto(case_url, wait_until="domcontentloaded")
        url_vorher = page.url
        abgelehnt = f"E2E-Art18-abgelehnt-{uuid.uuid4().hex[:6]}"
        response = _meilenstein_absenden(page, abgelehnt)

        assert response.status == 409, response.status
        assert response.headers.get("hx-reswap") == "none"

        # Der Kasten ist nicht durch den Meldungstext ersetzt worden.
        section = page.locator("#goals-section")
        expect(section).to_be_visible(timeout=5000)
        expect(page.locator(f"#goals-section h3:text-is('{goal}')")).to_be_visible()
        expect(page.locator(f"#goals-section :text-is('{meilenstein}')").first).to_be_visible()
        expect(section.locator("input[placeholder='Neuer Meilenstein']").first).to_be_visible()
        assert _MELDUNG_ANFANG not in section.inner_text()
        assert page.locator(f"#goals-section :text-is('{abgelehnt}')").count() == 0
        assert page.url == url_vorher

        # Nicht eingewechselt, also zeigt der globale Fehler-Toast die Meldung.
        toast = page.locator("#htmx-error-toast")
        expect(toast).to_be_visible(timeout=5000)
        expect(toast).to_contain_text(_MELDUNG_ANFANG)

        # Nach dem Neuladen: unveraendert, der abgelehnte Meilenstein existiert nicht.
        page.reload(wait_until="domcontentloaded")
        expect(page.locator(f"#goals-section :text-is('{meilenstein}')").first).to_be_visible()
        assert page.locator(f"#goals-section :text-is('{abgelehnt}')").count() == 0

    def test_gegenprobe_ohne_einschraenkung(self, staff_page, base_url):
        page = staff_page
        _client_url, _case_url, goal, meilenstein = _person_mit_fall_ziel_und_meilenstein(page, base_url)

        zweiter = f"E2E-Art18-frei-{uuid.uuid4().hex[:6]}"
        response = _meilenstein_absenden(page, zweiter)

        assert response.status == 200, response.status
        expect(page.locator(f"#goals-section :text-is('{zweiter}')").first).to_be_visible(timeout=5000)
        expect(page.locator(f"#goals-section :text-is('{meilenstein}')").first).to_be_visible()
        expect(page.locator(f"#goals-section h3:text-is('{goal}')")).to_be_visible()
        assert page.locator("#htmx-error-toast").count() == 0
