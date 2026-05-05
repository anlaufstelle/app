"""Last-Test-Profile fuer Anlaufstelle (Refs #825 / C-58).

Benutzung lokal:

    .venv/bin/pip install locust
    .venv/bin/python src/manage.py seed --scale large
    .venv/bin/python src/manage.py runserver 0.0.0.0:8000
    .venv/bin/locust -f locustfile.py --host=http://localhost:8000

In der Web-UI (http://localhost:8089) Anzahl Users + Spawn-Rate setzen
und das Szenario starten. Im Nightly-Lauf wird Locust headless mit
``--users``, ``--spawn-rate``, ``--run-time`` und ``--csv``-Output
aufgerufen — siehe ``.github/workflows/perf-nightly.yml``.

Performance-Budgets (95-Perzentil) sind in
:file:`docs/performance-budgets.md` dokumentiert; der Nightly-Workflow
parst die Locust-CSV und scheitert bei Overrun.
"""

from __future__ import annotations

import json
import re

from locust import HttpUser, between, task

# Seed-Default aus core/management/commands/seed.py — bleibt mit der Doku
# konsistent (siehe CONTRIBUTING.md "Seed-Daten laden").
SEED_USERNAME = "fachkraft1"
SEED_PASSWORD = "anlaufstelle2026"  # noqa: S105 — public Seed-Passwort


CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


class StaffUser(HttpUser):
    """Realistic staff user: login, dashboard, list views, search."""

    wait_time = between(1, 3)

    def on_start(self):
        # GET login page to get the CSRF token, then POST credentials.
        login_page = self.client.get("/login/")
        match = CSRF_RE.search(login_page.text)
        token = match.group(1) if match else ""
        self.client.post(
            "/login/",
            data={
                "csrfmiddlewaretoken": token,
                "username": SEED_USERNAME,
                "password": SEED_PASSWORD,
            },
            headers={"Referer": f"{self.host}/login/"},
        )

    @task(5)
    def zeitstrom(self):
        self.client.get("/", name="GET /zeitstrom")

    @task(3)
    def client_list(self):
        self.client.get("/clients/", name="GET /clients/")

    @task(3)
    def case_list(self):
        self.client.get("/cases/", name="GET /cases/")

    @task(2)
    def workitem_inbox(self):
        self.client.get("/workitems/", name="GET /workitems/")

    @task(2)
    def search(self):
        self.client.get("/search/?q=Kontakt", name="GET /search?q=Kontakt")

    @task(1)
    def statistics(self):
        self.client.get("/statistik/?period=month", name="GET /statistik")


class HeavyExportUser(HttpUser):
    """Lower-frequency user that hits the slow paths (PDF export, Jugendamt)."""

    wait_time = between(10, 30)

    def on_start(self):
        login_page = self.client.get("/login/")
        match = CSRF_RE.search(login_page.text)
        token = match.group(1) if match else ""
        self.client.post(
            "/login/",
            data={
                "csrfmiddlewaretoken": token,
                "username": "leitung1",
                "password": SEED_PASSWORD,
            },
            headers={"Referer": f"{self.host}/login/"},
        )

    @task
    def pdf_export(self):
        # date_from/date_to als heutiges Halbjahr (siehe statistics-View).
        self.client.get(
            "/statistik/exports/pdf/?date_from=2026-01-01&date_to=2026-06-30",
            name="GET /statistik/exports/pdf",
        )

    @task
    def csv_export(self):
        self.client.get(
            "/statistik/exports/csv/?date_from=2026-01-01&date_to=2026-06-30",
            name="GET /statistik/exports/csv",
        )


def _read_budgets(path: str = "docs/performance-budgets.json") -> dict[str, float]:
    """Helper fuer Nightly-CI — liest die Budgets aus der JSON-Tabelle."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
