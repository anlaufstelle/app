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
        """Gesamtkontakte auf Statistik-Seite bleiben nach Retention stabil.

        Refs #1788 — Warnkommentar statt Umbau: Dieser Test führt den
        ECHTEN ``enforce_retention``-Command aus (kein ``--dry-run``), und
        der läuft installationsweit auf der geteilten E2E-DB — nicht auf
        eigens angelegten Wegwerf-Daten (Leitplanke des Tickets:
        "Zielobjekte selbst erzeugen, nie Seed-/Fremdobjekte"). Verifiziert
        gegen Seed + Command vor dem Schreiben dieses Kommentars:
        - Die Standard-E2E-Seed-Skala ("small", ``conftest.py`` seedet ohne
          ``--scale``) erzeugt Events nur bis 80 Tage in die Vergangenheit
          (``core/seed/events.py``); alle Retention-Fenster liegen bei
          Seed-Defaults (90/365/3650 Tage, ``core/seed/settings_seed.py``)
          gleich oder darüber — für DIESE Skala ist der Lauf gegen die
          Seed-Daten selbst ein No-op.
        - ABER: ``enforce_retention`` reaktiviert/löscht auch
          RetentionProposals und Legal Holds anderer Dateien, wenn diese
          nicht sauber isoliert sind — genau das ist bereits einmal real
          passiert (Refs #1787, siehe Docstring in
          ``test_retention_dashboard.py::_ensure_proposals``: ein in
          diesem Suite-Durchgang aufgehobener Legal Hold gab ein fremdes
          Event für einen ``enforce_retention``-Lauf frei). Der Schutz
          davor ist NICHT strukturell (kein Fixture-Ownership-Scoping im
          Command selbst), sondern reine Test-Disziplin: jede E2E-Datei,
          die PENDING/APPROVED-Proposals oder Legal Holds anlegt, MUSS das
          auf eigenen Wegwerf-Objekten tun (wie ``test_retention_dashboard.py``
          es seit #1787 tut). Dieser Test bleibt also ein bewusst
          geduldetes, offenes Restrisiko der Lebenszyklus-Klasse — nicht
          eliminiert, nur (Stand heute) durch Disziplin in den anderen
          Dateien nicht ausgelöst.
        """
        page = lead_page

        # Snapshots für alle vorhandenen Monate erstellen
        _run_management_command(e2e_env, "create_statistics_snapshots", "--backfill")

        # Statistik-Seite mit Jahres-Ansicht laden
        page.goto(
            f"{base_url}/statistics/?zeitraum=jahr",
            wait_until="domcontentloaded",
        )
        page.wait_for_load_state("domcontentloaded")

        # Gesamtkontakte aus KPI-Karte auslesen
        kpi_card = page.locator("div.font-mono.font-bold").first
        kpi_card.wait_for(state="visible", timeout=5000)
        total_before = kpi_card.inner_text().strip()

        # enforce_retention ausführen (erstellt Snapshots + löscht Events)
        result = _run_management_command(e2e_env, "enforce_retention")
        assert result.returncode == 0, f"enforce_retention failed: {result.stderr}"

        # Seite neu laden
        page.goto(
            f"{base_url}/statistics/?zeitraum=jahr",
            wait_until="domcontentloaded",
        )
        page.wait_for_load_state("domcontentloaded")

        # Gesamtkontakte erneut auslesen
        kpi_card_after = page.locator("div.font-mono.font-bold").first
        kpi_card_after.wait_for(state="visible", timeout=5000)
        total_after = kpi_card_after.inner_text().strip()

        assert total_before == total_after, f"Gesamtkontakte nach Retention verändert: {total_before} → {total_after}"
