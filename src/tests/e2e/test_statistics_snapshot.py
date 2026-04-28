"""E2E-Test: Statistik-Snapshots bewahren Zahlen nach Retention.

Ablauf:
1. Statistik-Seite aufrufen → Gesamtkontakte notieren
2. enforce_retention ausführen (erzeugt Snapshots + löscht alte Events)
3. Statistik-Seite erneut laden → Zahlen identisch
"""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


def _run_management_command(env, *args):
    """Run a Django management command in the worker-aware E2E environment."""
    result = subprocess.run(
        [sys.executable, "src/manage.py", *args],
        capture_output=True,
        text=True,
        env=env,
    )
    return result


class TestStatisticsSnapshotPreservation:
    """Statistik-Zahlen bleiben nach enforce_retention erhalten."""

    def test_statistics_preserved_after_retention(self, lead_page, base_url, e2e_env):
        """Gesamtkontakte auf Statistik-Seite bleiben nach Retention stabil."""
        page = lead_page

        # Snapshots für alle vorhandenen Monate erstellen
        _run_management_command(e2e_env, "create_statistics_snapshots", "--backfill")

        # Statistik-Seite mit Jahres-Ansicht laden
        page.goto(
            f"{base_url}/statistics/?period=year",
            wait_until="domcontentloaded",
        )
        page.wait_for_load_state("domcontentloaded")

        # Gesamtkontakte aus KPI-Karte auslesen
        kpi_card = page.locator("p.text-3xl.font-bold").first
        kpi_card.wait_for(state="visible", timeout=5000)
        total_before = kpi_card.inner_text().strip()

        # enforce_retention ausführen (erstellt Snapshots + löscht Events)
        result = _run_management_command(e2e_env, "enforce_retention")
        assert result.returncode == 0, f"enforce_retention failed: {result.stderr}"

        # Seite neu laden
        page.goto(
            f"{base_url}/statistics/?period=year",
            wait_until="domcontentloaded",
        )
        page.wait_for_load_state("domcontentloaded")

        # Gesamtkontakte erneut auslesen
        kpi_card_after = page.locator("p.text-3xl.font-bold").first
        kpi_card_after.wait_for(state="visible", timeout=5000)
        total_after = kpi_card_after.inner_text().strip()

        assert total_before == total_after, f"Gesamtkontakte nach Retention verändert: {total_before} → {total_after}"
