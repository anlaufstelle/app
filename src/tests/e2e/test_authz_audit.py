"""Live-AuthZ-Audit: volle Matrix am gunicorn-E2E-Server (Refs #1055, #1042/A1).

Manuell ausgeführt (nicht Teil normaler Läufe):

    AUTHZ_AUDIT=1 .venv/bin/python -m pytest src/tests/e2e/test_authz_audit.py -m authz_audit -v

Schreibt den Report nach docs/archive/audits/2026-06-11-a1-laufzeit-authz-matrix.md.
Mutationen durch erlaubte POSTs sind akzeptiert — die E2E-DB ist wegwerfbar.

Der Test failt NICHT bei Abweichungen (Befunde gehören in Report + Issues;
Regressionsschutz ist die Unit-Matrix test_authz_matrix.py) — nur bei
strukturellem Versagen (zu wenige Zellen, zu viele Setup-Fehler).
"""

import datetime
import os
import subprocess
import sys

import pytest
import requests
from django.urls import reverse

from tests._authz_expectations import EXPECTATIONS, ROLES
from tests.e2e import _authz_audit_helpers as helpers

# Bewusste Wiederverwendung der Unit-IDOR-Sonderfälle (DRY, Refs #1055):
# NOT_PROBEABLE-GET-Zellen (pauschales 405 vor jedem Lookup) werden auch im
# Audit übersprungen — der 405-Drift-Guard läuft in test_authz_idor.py.
from tests.test_authz_idor import IDOR_POST_DATA, NOT_PROBEABLE

# M6: bekannte, als Issue dokumentierte Lücken der Unit-Matrix — Abweichungen
# dieser Zellen werden im Report als „Bekannt" annotiert (kein neuer Befund).
from tests.test_authz_matrix import KNOWN_GAPS

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.authz_audit,
    pytest.mark.skipif(os.environ.get("AUTHZ_AUDIT") != "1", reason="Nur mit AUTHZ_AUDIT=1"),
]


@pytest.fixture(scope="module")
def medium_seed(request, base_url):
    """Reseed mit ``--scale medium``: der Audit braucht 2 Facilities (IDOR).

    ``base_url`` hat bereits small geseedet und den Server gestartet —
    hier wird die laufende E2E-DB unter dem Server neu befüllt.
    """
    # I3: helpers.db_connection() trifft immer die Worker-0-Default-DB —
    # unter xdist (workerinput gesetzt, vgl. _get_worker_info in conftest.py)
    # würden Reseed/Harvesting fremde Worker-DBs/Server verfehlen.
    assert getattr(request.config, "workerinput", None) is None, "Audit nur seriell ausführen (ohne pytest-xdist / -n)"
    python = str(helpers.REPO_ROOT / ".venv" / "bin" / "python")
    if not os.path.exists(python):
        python = sys.executable
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.e2e",
        # Seriell = Worker 0 = Default-DB (muss zu helpers.db_connection passen).
        "E2E_DATABASE_NAME": os.environ.get("E2E_DATABASE_NAME", "anlaufstelle_e2e"),
    }
    result = subprocess.run(
        [python, "src/manage.py", "seed", "--flush", "--scale", "medium"],
        cwd=helpers.REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"seed --scale medium fehlgeschlagen:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="module")
def audit_context(base_url, medium_seed):
    """Sessions aller Akteure + geerntete Objekt-IDs beider Facilities."""
    sessions = {}
    for actor, username in helpers.ACTOR_LOGINS.items():
        session, _ = helpers.login(base_url, username)
        sessions[actor] = session
    # admin_1-Login erzeugt eine AuditLog-Zeile in Facility 2 (foreign_audit_entry).
    foreign_session, _ = helpers.login(base_url, helpers.FOREIGN_ADMIN)
    foreign_session.close()

    with helpers.db_connection() as conn:
        fac1, fac2 = helpers.facility_ids(conn)
        helpers.prepare_audit_objects(conn, fac1)
        helpers.prepare_audit_objects(conn, fac2)
        ids_fac1 = helpers.harvest_ids(conn, fac1)
        ids_fac2 = helpers.harvest_ids(conn, fac2)
        milestone_snapshot = _snapshot_milestone(conn, ids_fac1["milestone"])

    yield {
        "sessions": sessions,
        "ids_fac1": ids_fac1,
        "ids_fac2": ids_fac2,
        "milestone_snapshot": milestone_snapshot,
    }

    for session in sessions.values():
        session.close()


# Spaltenliste am Model verifiziert (core/models/outcome.py: Milestone).
_MILESTONE_COLUMNS = "id, goal_id, title, is_completed, completed_at, sort_order, created_at"


def _snapshot_milestone(conn, milestone_id):
    """Row-Snapshot des Matrix-Milestones für die Hard-Delete-Wiederherstellung."""
    if milestone_id is None:
        return None
    cur = helpers._superuser_cursor(conn)
    cur.execute(f"SELECT {_MILESTONE_COLUMNS} FROM core_milestone WHERE id = %s", (milestone_id,))  # noqa: S608
    return cur.fetchone()


def _reset_event(conn, ctx):
    """Soft-Delete von sample_event rückgängig machen.

    Event hat KEIN deleted_at/deleted_by (eigenes is_deleted-Feld statt
    SoftDeletableModel, core/models/event.py) — is_deleted reicht, damit
    get_visible_event_or_404 das Objekt wieder findet. data_json/Attachments
    werden bewusst nicht restauriert (für die AuthZ-Probe irrelevant; die
    Attachment-Zellen laufen in EXPECTATIONS-Reihenfolge VOR event_delete).
    """
    cur = helpers._superuser_cursor(conn)
    cur.execute("UPDATE core_event SET is_deleted = false WHERE id = %s", (ctx["ids_fac1"]["sample_event"],))


def _reset_milestone(conn, ctx):
    """Hard-Delete von milestone rückgängig machen.

    Gewählte Variante: Row-Snapshot aus audit_context per Re-INSERT mit
    identischer ID — die in den URL-Pfaden eingebaute Milestone-ID bleibt
    dadurch über alle Akteur-Zellen gültig. ON CONFLICT DO NOTHING macht den
    Hook idempotent (abgelehnte Akteure haben nichts gelöscht).
    """
    if ctx["milestone_snapshot"] is None:
        return
    cur = helpers._superuser_cursor(conn)
    cur.execute(
        f"INSERT INTO core_milestone ({_MILESTONE_COLUMNS}) "  # noqa: S608
        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
        ctx["milestone_snapshot"],
    )


# C2: Endpoints, deren erlaubter POST das Matrix-Objekt konsumiert — ohne
# Wiederherstellung bekämen nachfolgende ERLAUBTE Akteure 404 (falsche
# ABWEICHUNG). Hook läuft nach jeder POST-Zelle dieser Endpoints.
DESTRUCTIVE_RESET = {
    "core:event_delete": _reset_event,  # Soft-Delete (events/crud.py: soft_delete_event)
    "core:milestone_delete": _reset_milestone,  # Hard-Delete
}


def _path_for(url_name, ids, kwarg_spec):
    """URL-Pfad aus der Kwarg-Spezifikation bauen; ``None`` = SETUP-ERROR.

    Fixture-Refs (mit Punkt, z. B. ``"client_identified.pk"``) werden über
    FOREIGN_ALIASES normalisiert und in ``ids`` nachgeschlagen; Literale
    werden unverändert eingesetzt. UUIDs aus psycopg sind bereits
    ``uuid.UUID``-Objekte — ``reverse`` kommt damit klar.
    """
    kwargs = {}
    for kwarg, ref in kwarg_spec:
        if "." in ref:
            fixture_name = ref.split(".", 1)[0]
            fixture_name = helpers.FOREIGN_ALIASES.get(fixture_name, fixture_name)
            value = ids.get(fixture_name)
            if value is None:
                return None
            kwargs[kwarg] = value
        else:
            kwargs[kwarg] = ref
    return reverse(url_name, kwargs=kwargs)


def _judge(exp, actor, allowed, status, location):
    """``(expected, verdict)`` für eine vertikale Matrix-Zelle bestimmen."""
    login_redirect = status == 302 and location.startswith("/login/")
    if actor == "anonymous":
        if exp.anonymous_ok:
            return "public", "OK" if status < 500 else "ABWEICHUNG"
        return "login-redirect", "OK" if login_redirect else "ABWEICHUNG"
    if actor in allowed:
        if exp.sudo and status == 302 and location.startswith("/sudo/"):
            return "allow", "OK"  # AuthZ hat durchgelassen — Sudo-Gate aktiv.
        # Nur ?next=-Redirects sind echte Auth-Bounces (redirect_to_login);
        # ein nacktes /login/ ist der legitime Logout-Redirect
        # (LOGOUT_REDIRECT_URL) — vgl. test_authz_matrix.py.
        auth_bounce = status == 302 and location.startswith("/login/?next=")
        # I1: 5xx auf einer Allow-Zelle ist eine Abweichung, kein OK.
        ok = status < 500 and (status not in (403, 404) or status in exp.extra_ok) and not auth_bounce
        return "allow", "OK" if ok else "ABWEICHUNG"
    return "deny", "OK" if status in (403, 404) else "ABWEICHUNG"


def _row(exp, method, actor, expected, status, verdict):
    # M6: bekannte Unit-Matrix-Gaps annotieren (Spalte „Bekannt" im Report).
    gap = KNOWN_GAPS.get((exp.url_name, method, actor), "")
    return {
        "url_name": exp.url_name,
        "category": exp.category,
        "method": method,
        "actor": actor,
        "expected": expected,
        "status": status,
        "verdict": verdict,
        "known": f"✔ {gap}" if gap else "",
    }


def test_full_live_matrix_and_report(base_url, audit_context):
    """Volle vertikale Matrix + IDOR-Probes + Header-Erfassung → Markdown-Report."""
    sessions = audit_context["sessions"]
    ids = audit_context["ids_fac1"]
    ids_foreign = audit_context["ids_fac2"]
    rows = []

    # 1. Vertikale Matrix: jede Expectation × Methode × (ROLES + anonymous).
    for exp in EXPECTATIONS:
        path = _path_for(exp.url_name, ids, exp.url_kwargs)
        for method, allowed in exp.methods:
            if path is None:
                rows.append(_row(exp, method, "alle", "-", "-", "SETUP-ERROR"))
                continue
            for actor in (*ROLES, "anonymous"):
                # I2: Request-Fehler (Timeout, ConnectionError, …) brechen den
                # Lauf nicht ab — die Zelle wird als ERROR-Zeile reportet.
                try:
                    if exp.url_name == "logout" and method == "POST" and actor != "anonymous":
                        # Logout zerstört die Session — Wegwerf-Login statt der
                        # geteilten Matrix-Session, sonst kippen alle Folgezellen
                        # dieses Akteurs in Login-Redirects.
                        fresh_session, _ = helpers.login(base_url, helpers.ACTOR_LOGINS[actor])
                        with fresh_session:
                            response = helpers.request_cell(fresh_session, base_url, method, path)
                    else:
                        response = helpers.request_cell(sessions.get(actor), base_url, method, path)
                except requests.RequestException as exc:
                    rows.append(_row(exp, method, actor, f"Request-Fehler: {type(exc).__name__}", "-", "ERROR"))
                else:
                    expected, verdict = _judge(
                        exp, actor, allowed, response.status_code, response.headers.get("Location", "")
                    )
                    rows.append(_row(exp, method, actor, expected, response.status_code, verdict))
                # C2: konsumiertes Objekt wiederherstellen, bevor der nächste
                # Akteur dieselbe Zelle abfragt (auch nach Request-Fehler —
                # serverseitig kann die Mutation trotzdem passiert sein).
                if method == "POST" and exp.url_name in DESTRUCTIVE_RESET:
                    with helpers.db_connection() as conn:
                        DESTRUCTIVE_RESET[exp.url_name](conn, audit_context)

    # 2. IDOR: fremde Objekt-IDs (Facility 2), stärkste erlaubte Facility-Rolle.
    # Sudo-Elevation NACH der vertikalen Matrix (deren 302→/sudo/-Beobachtungen
    # bleiben echt): sudo-geschützte IDOR-Zellen (client_export_*) erreichen
    # sonst nie den Objekt-Lookup — RequireSudoModeMixin redirected pk-unabhängig
    # vor get_object_or_404 (services/security/sudo_mode.py). Im Unit-IDOR-Test
    # entfällt das Gate (SUDO_MODE_ENABLED=False in settings/test.py).
    for idor_actor in ("facility_admin", "lead"):
        helpers.elevate_sudo(sessions[idor_actor], base_url)
    for exp in EXPECTATIONS:
        if not exp.idor:
            continue
        path = _path_for(exp.url_name, ids_foreign, exp.idor)
        for method, allowed in exp.methods:
            if (exp.url_name, method) in NOT_PROBEABLE:
                continue  # Pauschales 405 vor jedem Lookup — siehe Grenzen-Sektion.
            actor = "facility_admin" if "facility_admin" in allowed else "lead"
            if path is None:
                rows.append(_row(exp, f"{method} (IDOR)", actor, "404", "-", "SETUP-ERROR"))
                continue
            data = IDOR_POST_DATA.get((exp.url_name, method))
            try:
                response = helpers.request_cell(sessions[actor], base_url, method, path, data=data)
            except requests.RequestException as exc:
                rows.append(_row(exp, f"{method} (IDOR)", actor, f"Request-Fehler: {type(exc).__name__}", "-", "ERROR"))
                continue
            verdict = "OK" if response.status_code == 404 else "ABWEICHUNG"
            rows.append(_row(exp, f"{method} (IDOR)", actor, "404", response.status_code, verdict))

    # 3. Cookie-Flags + Security-Header (frische Session, echte Header).
    session, login_response = helpers.login(base_url, "miriam")
    with session:
        start_response = session.get(f"{base_url}/start/", timeout=15)
    cookie = helpers.session_cookie_header(login_response)
    header_findings = [
        f"- `Set-Cookie: sessionid` — HttpOnly: {'ja' if 'HttpOnly' in cookie else 'NEIN'}, "
        f"SameSite=Lax: {'ja' if 'SameSite=Lax' in cookie else 'NEIN'}, "
        f"Secure: {'ja' if 'Secure' in cookie else 'nein (HTTP-E2E-Server; prod-only, config-verifiziert)'}",
        f"- `X-Content-Type-Options` (/start/): `{start_response.headers.get('X-Content-Type-Options', 'FEHLT')}`",
        f"- `Referrer-Policy` (/start/): `{start_response.headers.get('Referrer-Policy', 'FEHLT')}`",
        "- `Content-Security-Policy` (/start/): "
        + ("gesetzt (enforced, kein Report-Only)" if "Content-Security-Policy" in start_response.headers else "FEHLT"),
    ]

    # 4. Report schreiben.
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=helpers.REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    meta = {"date": datetime.date.today().isoformat(), "commit": commit}
    report_path = helpers.write_report(rows, header_findings, meta)

    # 5. Konsole + strukturelle Asserts (Abweichungen failen den Test NICHT).
    ok_count = sum(1 for r in rows if r["verdict"] == "OK")
    deviation_count = sum(1 for r in rows if r["verdict"] == "ABWEICHUNG")
    setup_error_count = sum(1 for r in rows if r["verdict"] == "SETUP-ERROR")
    request_error_count = sum(1 for r in rows if r["verdict"] == "ERROR")
    print(f"\nReport: {report_path}")
    print(
        f"Zellen: {len(rows)} — OK {ok_count} · Abweichungen {deviation_count} · "
        f"Setup-Fehler {setup_error_count} · Request-Fehler {request_error_count}"
    )

    assert len(rows) > 850, f"Nur {len(rows)} Zellen — Matrix unvollständig ausgeführt"
    # I2: ERROR-Zellen zählen in den strukturellen <10%-Assert mit.
    assert setup_error_count + request_error_count < len(rows) * 0.1, (
        f"{setup_error_count} Setup-Fehler + {request_error_count} Request-Fehler "
        f"bei {len(rows)} Zellen — Seed/Harvesting/Server prüfen"
    )
