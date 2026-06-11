"""Helfer für den Live-AuthZ-Audit (Refs #1055): Logins, ID-Harvesting, Report."""

import os
from pathlib import Path

import psycopg
import requests

PASSWORD = "anlaufstelle2026"
REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = REPO_ROOT / "docs" / "archive" / "audits" / "2026-06-11-a1-laufzeit-authz-matrix.md"

# Seed-Logins (CLAUDE.md): Facility 1 ohne Suffix, Facility 2 mit _1.
ACTOR_LOGINS = {
    "facility_admin": "admin",
    "lead": "thomas",
    "staff": "miriam",
    "assistant": "lena",
    "super_admin": "superadmin",
}
FOREIGN_ADMIN = "admin_1"


def login(base_url, username):
    """Echter Form-Login; Rückgabe ``(session, login_response)``.

    Aufrufer müssen die Session schließen (``with session:`` bzw.
    ``session.close()``); schlägt der Login fehl, wird sie HIER geschlossen
    (kein Session-Leak bei Assertion-Fail). Zusätzlich ``Connection: close``:
    Der gunicorn-gthread-Worker blockiert nach einem POST über eine
    Keep-Alive-Verbindung den graceful Shutdown um volle 30s
    (graceful_timeout) — auch wenn der Client die Verbindung längst
    geschlossen hat. Das ließ den Server-Teardown der base_url-Fixture
    (proc.wait(timeout=10)) reproduzierbar in TimeoutExpired laufen.
    """
    session = requests.Session()
    session.headers["Connection"] = "close"
    try:
        login_page = session.get(f"{base_url}/login/", timeout=10)
        assert login_page.status_code == 200, f"Login-Seite antwortete {login_page.status_code}"
        token = session.cookies.get("csrftoken")
        response = session.post(
            f"{base_url}/login/",
            data={"username": username, "password": PASSWORD, "csrfmiddlewaretoken": token},
            headers={"Referer": f"{base_url}/login/"},
            timeout=10,
            allow_redirects=False,
        )
        assert response.status_code == 302, f"Login {username} fehlgeschlagen: {response.status_code}"
    except BaseException:
        session.close()
        raise
    return session, response


def session_cookie_header(login_response):
    """Roher ``Set-Cookie``-Header des Session-Cookies — oder ``""``.

    ``response.headers["Set-Cookie"]`` fasst mehrere Set-Cookie-Header
    kommasepariert zusammen; ``raw.headers.getlist`` liefert sie einzeln.
    Das macht das Parsing unabhängig von der Header-Reihenfolge
    (csrftoken vs. sessionid) und verhindert False-Positives durch
    Attribute des jeweils anderen Cookies.
    """
    matches = [h for h in login_response.raw.headers.getlist("Set-Cookie") if h.startswith("sessionid=")]
    return matches[0] if matches else ""


def request_cell(session_or_none, base_url, method, path, data=None):
    """Eine Matrix-Zelle abfragen; POST mit CSRF-Header, ohne Redirect-Folgen.

    ``session_or_none=None`` = anonymer Akteur (Wegwerf-Session, wird
    geschlossen). ``data`` erlaubt IDOR-POST-Payloads, die nötig sind,
    damit die Probe den Scoping-Code überhaupt erreicht (IDOR_POST_DATA
    in test_authz_idor.py).
    """
    if session_or_none is None:
        with requests.Session() as session:
            session.headers["Connection"] = "close"
            return _request(session, base_url, method, path, data)
    return _request(session_or_none, base_url, method, path, data)


def _request(session, base_url, method, path, data):
    url = f"{base_url}{path}"
    if method == "GET":
        return session.get(url, timeout=15, allow_redirects=False)
    session.get(f"{base_url}/login/", timeout=10)  # csrftoken-Cookie sicherstellen
    return session.post(
        url,
        data=data or {},
        headers={
            "Referer": url,
            "X-CSRFToken": session.cookies.get("csrftoken", ""),
        },
        timeout=15,
        allow_redirects=False,
    )


def db_connection():
    """Direkte psycopg-Verbindung zur E2E-Datenbank (autocommit)."""
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ.get("POSTGRES_USER", "anlaufstelle"),
        password=os.environ.get("POSTGRES_PASSWORD", "anlaufstelle"),
        dbname=os.environ.get("E2E_DATABASE_NAME", "anlaufstelle_e2e"),
        autocommit=True,
    )


def _superuser_cursor(conn):
    """RLS: FORCE ROW LEVEL SECURITY gilt auch für den Owner — ohne
    Session-Variablen liefern Queries 0 Zeilen (Migration 0047)."""
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.is_super_admin', 'true', false)")
    return cur


def facility_ids(conn):
    """``(facility_1_id, facility_2_id)`` über die Seed-Usernamen."""
    cur = _superuser_cursor(conn)
    cur.execute("SELECT facility_id FROM core_user WHERE username = 'admin'")
    fac1 = cur.fetchone()[0]
    cur.execute("SELECT facility_id FROM core_user WHERE username = %s", (FOREIGN_ADMIN,))
    fac2 = cur.fetchone()[0]
    return fac1, fac2


def prepare_audit_objects(conn, fac_id):
    """Idempotente Seed-Nachbesserungen: 1 Trash-Client, 1 Case-Event."""
    cur = _superuser_cursor(conn)
    cur.execute(
        """
        UPDATE core_client SET is_deleted = true, deleted_at = now()
        WHERE id = (
            SELECT id FROM core_client
            WHERE facility_id = %s AND is_deleted = false
            ORDER BY id LIMIT 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM core_client WHERE facility_id = %s AND is_deleted = true
        )
        """,
        (fac_id, fac_id),
    )
    cur.execute(
        """
        UPDATE core_event SET case_id = (
            SELECT c.id FROM core_case c
            WHERE c.facility_id = %s ORDER BY c.id LIMIT 1
        )
        WHERE id = (
            SELECT e.id FROM core_event e
            WHERE e.facility_id = %s AND e.case_id IS NULL
            ORDER BY e.id LIMIT 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM core_event WHERE facility_id = %s AND case_id IS NOT NULL
        )
        """,
        (fac_id, fac_id, fac_id),
    )


# SQL pro Fixture-Name aus der Erwartungs-Tabelle. %(fac)s = Facility-ID.
# Tabellennamen sind db_table-Defaults (core_<modelname klein>, keine Overrides
# in src/core/models/); Status-Literale gegen die TextChoices verifiziert
# (Case.Status, RetentionProposal.Status, DeletionRequest.Status).
# Soft-deletable Models (Client, Case, Event, Episode, WorkItem) werden auf
# is_deleted = false gefiltert, damit die Matrix keine Papierkorb-Objekte trifft.
HARVEST_SQL = {
    "client_identified": "SELECT id FROM core_client WHERE facility_id = %(fac)s AND is_deleted = false LIMIT 1",
    "client_trashed": "SELECT id FROM core_client WHERE facility_id = %(fac)s AND is_deleted = true LIMIT 1",
    "case_open": (
        "SELECT id FROM core_case WHERE facility_id = %(fac)s AND status = 'open' AND is_deleted = false LIMIT 1"
    ),
    "case_closed": (
        "SELECT id FROM core_case WHERE facility_id = %(fac)s AND status = 'closed' AND is_deleted = false LIMIT 1"
    ),
    "case_event": (
        "SELECT e.id FROM core_event e JOIN core_case c ON c.id = e.case_id "
        "WHERE c.facility_id = %(fac)s AND e.is_deleted = false LIMIT 1"
    ),
    "sample_event": "SELECT id FROM core_event WHERE facility_id = %(fac)s AND is_deleted = false LIMIT 1",
    "episode": (
        "SELECT ep.id FROM core_episode ep JOIN core_case c ON c.id = ep.case_id "
        "WHERE c.facility_id = %(fac)s AND ep.is_deleted = false LIMIT 1"
    ),
    "outcome_goal": (
        "SELECT g.id FROM core_outcomegoal g JOIN core_case c ON c.id = g.case_id WHERE c.facility_id = %(fac)s LIMIT 1"
    ),
    "milestone": (
        "SELECT m.id FROM core_milestone m JOIN core_outcomegoal g ON g.id = m.goal_id "
        "JOIN core_case c ON c.id = g.case_id WHERE c.facility_id = %(fac)s LIMIT 1"
    ),
    "sample_workitem": "SELECT id FROM core_workitem WHERE facility_id = %(fac)s AND is_deleted = false LIMIT 1",
    "authz_attachment": (
        "SELECT a.id FROM core_eventattachment a JOIN core_event e ON e.id = a.event_id "
        "WHERE e.facility_id = %(fac)s AND a.is_current LIMIT 1"
    ),
    "audit_entry": "SELECT id FROM core_auditlog WHERE facility_id = %(fac)s LIMIT 1",
    "retention_proposal": (
        "SELECT id FROM core_retentionproposal WHERE facility_id = %(fac)s AND status = 'pending' LIMIT 1"
    ),
    "legal_hold": "SELECT id FROM core_legalhold WHERE facility_id = %(fac)s AND dismissed_at IS NULL LIMIT 1",
    "deletion_request": (
        "SELECT id FROM core_deletionrequest WHERE facility_id = %(fac)s AND status = 'pending' LIMIT 1"
    ),
}

# Audit-Pendants der Unit-Fixture-Namen für IDOR (foreign_X in Facility 2 = X-SQL dort).
FOREIGN_ALIASES = {
    "foreign_client": "client_identified",
    "foreign_client_trashed": "client_trashed",
    "foreign_case": "case_open",
    "foreign_case_closed": "case_closed",
    "foreign_case_event": "case_event",
    "foreign_event": "sample_event",
    "foreign_episode": "episode",
    "foreign_goal": "outcome_goal",
    "foreign_milestone": "milestone",
    "foreign_workitem": "sample_workitem",
    "foreign_attachment": "authz_attachment",
    "foreign_audit_entry": "audit_entry",
    "foreign_retention_proposal": "retention_proposal",
    "foreign_legal_hold": "legal_hold",
    "foreign_deletion_request": "deletion_request",
}


def harvest_ids(conn, fac_id):
    """``fixture_name → id`` (oder ``None`` = SETUP-ERROR-Zelle im Report)."""
    cur = _superuser_cursor(conn)
    out = {}
    for name, sql in HARVEST_SQL.items():
        cur.execute(sql, {"fac": fac_id})
        row = cur.fetchone()
        out[name] = row[0] if row else None
    return out


def write_report(rows, header_findings, meta):
    """Markdown-Report nach REPORT_PATH schreiben; gibt den Pfad zurück.

    ``rows``: Dicts mit url_name, category, method, actor, expected, status,
    verdict ("OK"/"ABWEICHUNG"/"SETUP-ERROR"). ``header_findings``: fertige
    Markdown-Bullet-Zeilen. ``meta``: Dict mit ``date`` und ``commit``.
    """
    ok_count = sum(1 for r in rows if r["verdict"] == "OK")
    deviations = [r for r in rows if r["verdict"] == "ABWEICHUNG"]
    setup_errors = [r for r in rows if r["verdict"] == "SETUP-ERROR"]

    # Aggregation Kategorie × Akteur (Insertion-Order = EXPECTATIONS-Reihenfolge).
    agg = {}
    for r in rows:
        bucket = agg.setdefault((r["category"], r["actor"]), {"OK": 0, "ABWEICHUNG": 0, "SETUP-ERROR": 0})
        bucket[r["verdict"]] += 1

    lines = [
        "# A1 — Laufzeit-Autorisierungs-Matrix & Mandantentrennung",
        "",
        f"**Datum:** {meta['date']} · **Server:** gunicorn mit `anlaufstelle.settings.e2e`, "
        f"Seed `medium` (2 Facilities) · **Commit:** `{meta['commit']}`",
        "",
        "Methodik: Soll-Zustand ist die deklarative Erwartungs-Tabelle",
        "[`_authz_expectations.py`](../../../src/tests/_authz_expectations.py)",
        "(einzige Soll-Quelle; Refs #1055, #1042/A1). Jede Zelle wurde als echter",
        "HTTP-Request gegen den laufenden gunicorn-E2E-Server ausgeführt: echte",
        "Form-Logins je Rolle, `requests` ohne Redirect-Folgen, POST mit",
        "CSRF-Header. Horizontale Mandantentrennung (IDOR) mit geernteten",
        "Objekt-IDs der zweiten Facility — erwartet wird strikt 404 (kein",
        "Existenz-Leak). Sudo-geschützte Views gelten für erlaubte Akteure als",
        "OK, wenn die Antwort 302 → `/sudo/` ist (AuthZ-Schicht hat",
        "durchgelassen, Sudo-Gate aktiv).",
        "",
        "## Ergebnis",
        "",
        f"**{ok_count} OK · {len(deviations)} Abweichungen · {len(setup_errors)} Setup-Fehler** "
        f"({len(rows)} Zellen gesamt)",
        "",
        "## Aggregat: Kategorie × Akteur",
        "",
        "| Kategorie | Akteur | OK | Abweichung | Setup-Error |",
        "|---|---|---:|---:|---:|",
    ]
    for (category, actor), bucket in agg.items():
        lines.append(f"| {category} | {actor} | {bucket['OK']} | {bucket['ABWEICHUNG']} | {bucket['SETUP-ERROR']} |")

    lines += ["", "## Abweichungen", ""]
    if deviations:
        lines += [
            "| Endpoint | Methode | Akteur | Erwartet | Ist |",
            "|---|---|---|---|---|",
        ]
        for r in deviations:
            lines.append(f"| `{r['url_name']}` | {r['method']} | {r['actor']} | {r['expected']} | {r['status']} |")
    else:
        lines.append("Keine. ✅")

    lines += ["", "## Setup-Fehler", ""]
    if setup_errors:
        for r in setup_errors:
            lines.append(f"- `{r['url_name']}` ({r['method']}, {r['actor']}): benötigte Objekt-ID nicht erntbar")
    else:
        lines.append("Keine. ✅")

    lines += ["", "## Session-Cookies & Security-Header", ""]
    lines += list(header_findings)

    lines += [
        "",
        "## Grenzen",
        "",
        "- `Secure`-Flag und HSTS sind prod-only (`settings/prod.py`, TLS terminiert"
        " Caddy) — am HTTP-E2E-Server nicht beobachtbar, config-verifiziert.",
        "- Django-Admin (`/admin-mgmt/`) und i18n-URLs sind nicht Teil der deklarierten Matrix.",
        "- IDOR-GET-Zellen von `core:retention_approve`, `core:retention_hold` und"
        " `core:retention_dismiss_hold` werden übersprungen: GET antwortet pauschal"
        " 405 vor jedem Objekt-Lookup (NOT_PROBEABLE inkl. Drift-Guard in"
        " `test_authz_idor.py`).",
        "- Mutationen durch erlaubte POSTs sind akzeptiert — die E2E-Datenbank ist"
        " wegwerfbar; Folgezellen können dadurch einzelne Statuswechsel sehen.",
        "",
        "## Wiederholung",
        "",
        "```bash",
        "AUTHZ_AUDIT=1 .venv/bin/python -m pytest src/tests/e2e/test_authz_audit.py -m authz_audit -v",
        "```",
        "",
        "Vom Repo-Root, seriell (kein xdist).",
    ]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return REPORT_PATH
