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
from urllib.parse import urlsplit

from locust import HttpUser, between, task

# Seed-Default aus core/management/commands/seed.py — bleibt mit der Doku
# konsistent (siehe CONTRIBUTING.md "Seed-Daten laden").
STAFF_USERNAME = "miriam"
LEAD_USERNAME = "emma"
SEED_PASSWORD = "anlaufstelle2026"  # noqa: S105 — public Seed-Passwort


CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


def login_user(user: HttpUser, username: str, password: str) -> None:
    """Log in and fail loudly when the response is not an authenticated page."""
    login_page = user.client.get("/login/")
    match = CSRF_RE.search(login_page.text)
    if not match:
        raise RuntimeError("CSRF token missing from login page")

    response = user.client.post(
        "/login/",
        data={
            "csrfmiddlewaretoken": match.group(1),
            "username": username,
            "password": password,
        },
        headers={"Referer": f"{user.host}/login/"},
    )
    if urlsplit(response.url).path.rstrip("/") == "/login":
        raise RuntimeError(f"login failed for {username}")


class AuthenticatedUser(HttpUser):
    """Shared login and response validation for authenticated profiles."""

    abstract = True

    def _get_authenticated(self, path: str, **kwargs: object) -> None:
        with self.client.get(path, catch_response=True, **kwargs) as response:
            if urlsplit(response.url).path.rstrip("/") == "/login":
                response.failure("authenticated request was redirected to login")


class StaffUser(AuthenticatedUser):
    """Realistic staff user: login, dashboard, list views, search."""

    wait_time = between(1, 3)

    def on_start(self):
        login_user(self, STAFF_USERNAME, SEED_PASSWORD)

    # Locust prefixt das HTTP-Verb automatisch — ``name=`` darf nur den
    # Pfad enthalten, sonst entsteht "GET GET /xyz" im Stats-CSV.
    @task(5)
    def zeitstrom(self):
        self._get_authenticated("/", name="/zeitstrom")

    @task(3)
    def client_list(self):
        self._get_authenticated("/clients/", name="/clients/")

    @task(3)
    def case_list(self):
        self._get_authenticated("/cases/", name="/cases/")

    @task(2)
    def workitem_inbox(self):
        self._get_authenticated("/workitems/", name="/workitems/")

    @task(2)
    def search(self):
        self._get_authenticated("/search/?q=Kontakt", name="/search?q=Kontakt")


class HeavyExportUser(AuthenticatedUser):
    """Lower-frequency user that hits the slow paths (PDF export, Sachbericht)."""

    wait_time = between(10, 30)

    def on_start(self):
        login_user(self, LEAD_USERNAME, SEED_PASSWORD)

    @task
    def pdf_export(self):
        # date_from/date_to als heutiges Halbjahr (siehe statistics-View).
        self._get_authenticated(
            "/statistics/export/pdf/?date_from=2026-01-01&date_to=2026-06-30",
            name="/statistics/export/pdf",
        )

    @task
    def csv_export(self):
        self._get_authenticated(
            "/statistics/export/csv/?date_from=2026-01-01&date_to=2026-06-30",
            name="/statistics/export/csv",
        )

    @task
    def statistics(self):
        self._get_authenticated("/statistics/?zeitraum=letzte30", name="/statistics")


def _read_budgets(path: str = "docs/performance-budgets.json") -> dict[str, float]:
    """Helper fuer Nightly-CI — liest die Budgets aus der JSON-Tabelle."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
