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
from django.urls import reverse

from tests._authz_expectations import EXPECTATIONS, ROLES
from tests.e2e import _authz_audit_helpers as helpers

# Bewusste Wiederverwendung der Unit-IDOR-Sonderfälle (DRY, Refs #1055):
# NOT_PROBEABLE-GET-Zellen (pauschales 405 vor jedem Lookup) werden auch im
# Audit übersprungen — der 405-Drift-Guard läuft in test_authz_idor.py.
from tests.test_authz_idor import IDOR_POST_DATA, NOT_PROBEABLE

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.authz_audit,
    pytest.mark.skipif(os.environ.get("AUTHZ_AUDIT") != "1", reason="Nur mit AUTHZ_AUDIT=1"),
]


@pytest.fixture(scope="module")
def medium_seed(base_url):
    """Reseed mit ``--scale medium``: der Audit braucht 2 Facilities (IDOR).

    ``base_url`` hat bereits small geseedet und den Server gestartet —
    hier wird die laufende E2E-DB unter dem Server neu befüllt.
    """
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

    yield {"sessions": sessions, "ids_fac1": ids_fac1, "ids_fac2": ids_fac2}

    for session in sessions.values():
        session.close()


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
        ok = (status not in (403, 404) or status in exp.extra_ok) and not login_redirect
        return "allow", "OK" if ok else "ABWEICHUNG"
    return "deny", "OK" if status in (403, 404) else "ABWEICHUNG"


def _row(exp, method, actor, expected, status, verdict):
    return {
        "url_name": exp.url_name,
        "category": exp.category,
        "method": method,
        "actor": actor,
        "expected": expected,
        "status": status,
        "verdict": verdict,
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
                if exp.url_name == "logout" and method == "POST" and actor != "anonymous":
                    # Logout zerstört die Session — Wegwerf-Login statt der
                    # geteilten Matrix-Session, sonst kippen alle Folgezellen
                    # dieses Akteurs in Login-Redirects.
                    fresh_session, _ = helpers.login(base_url, helpers.ACTOR_LOGINS[actor])
                    with fresh_session:
                        response = helpers.request_cell(fresh_session, base_url, method, path)
                else:
                    response = helpers.request_cell(sessions.get(actor), base_url, method, path)
                expected, verdict = _judge(
                    exp, actor, allowed, response.status_code, response.headers.get("Location", "")
                )
                rows.append(_row(exp, method, actor, expected, response.status_code, verdict))

    # 2. IDOR: fremde Objekt-IDs (Facility 2), stärkste erlaubte Facility-Rolle.
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
            response = helpers.request_cell(sessions[actor], base_url, method, path, data=data)
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
    print(f"\nReport: {report_path}")
    print(f"Zellen: {len(rows)} — OK {ok_count} · Abweichungen {deviation_count} · Setup-Fehler {setup_error_count}")

    assert len(rows) > 500, f"Nur {len(rows)} Zellen — Matrix unvollständig ausgeführt"
    assert setup_error_count < len(rows) * 0.1, (
        f"{setup_error_count} Setup-Fehler bei {len(rows)} Zellen — Seed/Harvesting prüfen"
    )
