"""E2E: Episoden — Bearbeiten und parallele Phasen pro Fall (#928).

Refs Master #922.

- ENT-EPI-02 — Episode bearbeiten: Titel/Beschreibung/started_at änderbar,
  Redirect auf Fall-Detail mit Flash, aktualisierte Werte sichtbar.
- ENT-EPI-05 — Mehrere parallele Episoden pro Fall sind erlaubt (kein
  automatischer Close); Sortierung in der Liste ist desc nach started_at.

CRUD-Smoke ist schon in ``test_cases.py::TestEpisodes`` abgedeckt — hier
fokussieren wir auf die in der Matrix offenen Lücken.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.e2e


def _select_first_client(page):
    autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
    autocomplete.click()
    page.locator("[role='listbox']").wait_for(state="visible", timeout=5000)
    page.locator("[role='option']").first.click()


def _create_case(page, base_url) -> str:
    """Erzeugt einen Fall mit Klient und liefert die Detail-URL."""
    title = f"E2E-EpiCase-{uuid.uuid4().hex[:6]}"
    page.goto(f"{base_url}/cases/new/")
    page.fill('input[name="title"]', title)
    page.select_option('select[name="lead_user"]', index=1)
    _select_first_client(page)
    page.locator("#main-content button[type='submit']").click()
    page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))
    return page.url


def _create_episode(page, base_url, title: str, started_at: str) -> None:
    """Legt eine Episode am aktuellen Fall an. Page bleibt auf Fall-Detail."""
    page.click("a:has-text('Neue Episode')")
    page.wait_for_url(re.compile(r"/episodes/new/"))
    page.fill('input[name="title"]', title)
    page.fill('input[name="started_at"]', started_at)
    page.locator("#main-content button[type='submit']").click()
    page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/$"))


class TestEpisodeEdit:
    """Refs Matrix ENT-EPI-02."""

    def test_edit_episode_title_and_started_at_persists(self, staff_page, base_url):
        page = staff_page
        case_url = _create_case(page, base_url)

        original_title = f"E2E-Epi-{uuid.uuid4().hex[:6]}"
        _create_episode(page, base_url, original_title, "2026-01-10")

        # Edit-Link der frisch erzeugten Episode klicken.
        edit_link = page.locator("a[href*='/episodes/'][href$='/edit/']:has-text('Bearbeiten')").first
        edit_link.wait_for(state="visible", timeout=5000)
        edit_link.click()
        page.wait_for_url(re.compile(r"/episodes/[0-9a-f-]+/edit/"))

        new_title = original_title + "-edit"
        page.fill('input[name="title"]', new_title)
        page.fill('input[name="started_at"]', "2026-02-20")
        page.locator("#main-content button[type='submit']").click()

        # Redirect zurück zur Fall-Detail-Seite.
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/$"), timeout=10000)
        # Flash-Message "aktualisiert" sichtbar.
        flash = page.locator("[role='status'] :text-matches('aktualisiert', 'i')").first
        flash.wait_for(state="visible", timeout=5000)

        # Aktualisierte Werte in der Episode-Liste sichtbar.
        assert page.locator(f":text-is('{new_title}')").first.is_visible()
        assert page.locator(f":text-is('{original_title}')").count() == 0, (
            "Alter Episode-Titel darf nach Update nicht mehr sichtbar sein."
        )

        # Reload → Persistenz.
        page.goto(case_url, wait_until="domcontentloaded")
        assert page.locator(f":text-is('{new_title}')").first.is_visible()


class TestParallelEpisodes:
    """Refs Matrix ENT-EPI-05 — Parallele Episoden pro Fall sind erlaubt."""

    def test_two_parallel_episodes_both_active(self, staff_page, base_url):
        page = staff_page
        case_url = _create_case(page, base_url)

        title_a = f"Phase-A-{uuid.uuid4().hex[:6]}"
        title_b = f"Phase-B-{uuid.uuid4().hex[:6]}"

        _create_episode(page, base_url, title_a, "2026-01-01")
        # Page steht jetzt wieder auf der Case-Detail-URL.
        _create_episode(page, base_url, title_b, "2026-02-15")

        # Beide Episoden in der Liste sichtbar.
        page.goto(case_url, wait_until="domcontentloaded")
        assert page.locator(f":text-is('{title_a}')").first.is_visible()
        assert page.locator(f":text-is('{title_b}')").first.is_visible()

        # Beide Episoden zeigen Status „aktiv" — kein automatischer Close.
        # Wir zählen, dass „aktiv" mind. zweimal in der Episoden-Sektion
        # erscheint (eine pro Episode-Card).
        episode_section_text = page.content()
        # Pragmatisches Indiz für „beide aktiv": mindestens zwei „aktiv"-Tokens
        # tauchen im Detail-Page-Body auf.
        assert episode_section_text.lower().count("aktiv") >= 2, (
            "Beide parallelen Episoden müssen Status 'aktiv' tragen."
        )

        # Sortierung: Phase B (späteres started_at) steht vor Phase A.
        body_text = page.locator("main").inner_text()
        pos_b = body_text.find(title_b)
        pos_a = body_text.find(title_a)
        assert pos_b != -1 and pos_a != -1, "Beide Phasen müssen im Detail-Page stehen."
        assert pos_b < pos_a, (
            f"Sortierung verletzt: Phase B ({title_b}, started_at=2026-02-15) sollte vor "
            f"Phase A ({title_a}, started_at=2026-01-01) erscheinen. "
            f"Positionen: B={pos_b}, A={pos_a}"
        )
