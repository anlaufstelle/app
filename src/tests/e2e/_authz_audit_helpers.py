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


def elevate_sudo(session, base_url):
    """Sudo-Fenster der Session öffnen (POST /sudo/ mit Seed-Passwort).

    Nötig für IDOR-Probes auf sudo-geschützte Views: RequireSudoModeMixin
    redirected sonst VOR jedem Objekt-Lookup nach /sudo/ (302, pk-unabhängig)
    — die Probe erreicht den Scoping-Code nie. Im Unit-IDOR-Test stellt sich
    die Frage nicht (SUDO_MODE_ENABLED=False in settings/test.py); der
    E2E-Server läuft mit aktivem Sudo-Gate. TTL 900s > Audit-Laufzeit.
    """
    session.get(f"{base_url}/sudo/?next=/start/", timeout=10)  # csrftoken sicherstellen
    response = session.post(
        f"{base_url}/sudo/",
        data={"password": PASSWORD, "next": "/start/"},
        headers={"Referer": f"{base_url}/sudo/", "X-CSRFToken": session.cookies.get("csrftoken", "")},
        timeout=10,
        allow_redirects=False,
    )
    assert response.status_code == 302, f"Sudo-Elevation fehlgeschlagen: {response.status_code}"


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
    ids = []
    for username in ("admin", FOREIGN_ADMIN):
        cur.execute("SELECT facility_id FROM core_user WHERE username = %s", (username,))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(
                f"Seed-User {username!r} fehlt — medium-Seed gelaufen? "
                "(.venv/bin/python src/manage.py seed --flush --scale medium)"
            )
        ids.append(row[0])
    return tuple(ids)


# Subquery: erster lead-User der Facility (fac2-Leads heißen thomas_1 etc.,
# daher facility-bewusst per role statt hartem Username).
_LEAD_OF_FACILITY = (
    "(SELECT u.id FROM core_user u WHERE u.role = 'lead' AND u.facility_id = %(fac)s ORDER BY u.id LIMIT 1)"
)

# Vertragsbedingungen für Matrix-Events (Unit-Fixture-Vertrag, Refs #1055):
# NORMAL-Sensitivität (ROLE_MAX_SENSITIVITY blendet ELEVATED/HIGH für
# assistant/staff per 404 aus — services/compliance/sensitivity.py) und kein
# QUALIFIED-Client (EventDeleteView.post stellt für qualified statt zu löschen
# einen Löschantrag — Vier-Augen-Prinzip, views/events.py).
_EVENT_CONTRACT_WHERE = (
    "e.is_deleted = false AND dt.sensitivity = 'normal' AND (e.client_id IS NULL OR cl.contact_stage != 'qualified')"
)

# Erstes vertragskonformes Event (außer Owner) — Events MIT aktuellem
# Attachment bevorzugt, damit sample_event und authz_attachment auf dasselbe
# Event zeigen (attachment_download nutzt beide Kwargs zusammen).
_CONFORMABLE_EVENT = f"""
    SELECT e.id FROM core_event e
    JOIN core_documenttype dt ON dt.id = e.document_type_id
    LEFT JOIN core_client cl ON cl.id = e.client_id
    WHERE e.facility_id = %(fac)s AND {_EVENT_CONTRACT_WHERE}
    ORDER BY EXISTS (
        SELECT 1 FROM core_eventattachment a WHERE a.event_id = e.id AND a.is_current
    ) DESC, e.id
    LIMIT 1
"""  # noqa: S608 — nur statische Fragmente, keine Userdaten.


def prepare_audit_objects(conn, fac_id):
    """Idempotente Seed-Nachbesserungen je Facility (Refs #1055):

    1. 1 Trash-Client (client_trashed).
    2. Vertragskonformes sample_event: Der Seed vergibt Owner und
       DocumentType-Sensitivität zufällig; die Erwartungen für
       core:event_update (STAFF_PLUS) / core:event_delete (LEAD_PLUS) gelten
       aber nur, wenn der Matrix-Akteur NIE Owner ist und die Sensitivität
       NORMAL ist (Unit-Fixture-Vertrag). Daher: Owner des ersten
       NORMAL-Events (Attachment bevorzugt, kein QUALIFIED-Client) auf den
       lead der Facility setzen. Unbedingt (ohne NOT-EXISTS-Guard), damit
       garantiert das Attachment-präferierte Event konform ist —
       deterministisches Ziel + fester Wert = idempotent.
    3. Vertragskonformes authz_attachment: der Seed hängt Attachments NUR an
       counseling-Events, und counseling ist ELEVATED (core/seed/attachments.py,
       core/seed/doc_types.py) — ohne Nachbesserung gäbe es nie ein Attachment
       auf einem NORMAL-Event. Daher: erstes aktuelles Attachment der Facility
       auf das vertragskonforme Event aus Schritt 2 umhängen (rein relational;
       Download entschlüsselt die Datei unabhängig vom Parent-Event).
    4. Vertragskonformes case_event: falls kein NORMAL-Event an einem Fall
       hängt, erst einem NORMAL-Event einen Fall zuweisen; dann Owner des
       ersten NORMAL-Case-Events auf lead setzen.
    5. Vertragskonformes sample_workitem (core:workitem_update LEAD_PLUS):
       erstes WorkItem auf created_by = lead, assigned_to = NULL setzen —
       weder created_by noch assigned_to dürfen staff/assistant sein.
    """
    cur = _superuser_cursor(conn)
    params = {"fac": fac_id}
    # 1. Trash-Client.
    cur.execute(
        """
        UPDATE core_client SET is_deleted = true, deleted_at = now()
        WHERE id = (
            SELECT id FROM core_client
            WHERE facility_id = %(fac)s AND is_deleted = false
            ORDER BY id LIMIT 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM core_client WHERE facility_id = %(fac)s AND is_deleted = true
        )
        """,
        params,
    )
    # 2. sample_event: Owner → lead.
    cur.execute(
        f"UPDATE core_event SET created_by_id = {_LEAD_OF_FACILITY} WHERE id = ({_CONFORMABLE_EVENT})",  # noqa: S608
        params,
    )
    # 3. authz_attachment: erstes Attachment auf das konforme Event umhängen
    #    (nur falls noch kein Attachment an einem konformen Event hängt —
    #    der Guard prüft inkl. lead-Owner, der in Schritt 2 gesetzt wurde).
    cur.execute(
        f"""
        UPDATE core_eventattachment SET event_id = ({_CONFORMABLE_EVENT})
        WHERE id = (
            SELECT a.id FROM core_eventattachment a
            JOIN core_event ae ON ae.id = a.event_id
            WHERE ae.facility_id = %(fac)s AND a.is_current
            ORDER BY a.id LIMIT 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM core_eventattachment a2
            JOIN core_event e ON e.id = a2.event_id
            JOIN core_documenttype dt ON dt.id = e.document_type_id
            LEFT JOIN core_client cl ON cl.id = e.client_id
            WHERE a2.is_current AND e.facility_id = %(fac)s AND {_EVENT_CONTRACT_WHERE}
              AND e.created_by_id IN (
                  SELECT u.id FROM core_user u WHERE u.role = 'lead' AND u.facility_id = %(fac)s
              )
        )
        """,  # noqa: S608
        params,
    )
    # 4a. Falls kein NORMAL-Case-Event existiert: erstem fall-losen
    #     NORMAL-Event den ersten Fall der Facility zuweisen.
    cur.execute(
        f"""
        UPDATE core_event SET case_id = (
            SELECT c.id FROM core_case c
            WHERE c.facility_id = %(fac)s AND c.is_deleted = false
            ORDER BY c.id LIMIT 1
        )
        WHERE id = (
            SELECT e.id FROM core_event e
            JOIN core_documenttype dt ON dt.id = e.document_type_id
            LEFT JOIN core_client cl ON cl.id = e.client_id
            WHERE e.facility_id = %(fac)s AND e.case_id IS NULL AND {_EVENT_CONTRACT_WHERE}
            ORDER BY e.id LIMIT 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM core_event e
            JOIN core_case c ON c.id = e.case_id
            JOIN core_documenttype dt ON dt.id = e.document_type_id
            LEFT JOIN core_client cl ON cl.id = e.client_id
            WHERE c.facility_id = %(fac)s AND {_EVENT_CONTRACT_WHERE}
        )
        """,  # noqa: S608
        params,
    )
    # 4b. case_event: Owner → lead.
    cur.execute(
        f"""
        UPDATE core_event SET created_by_id = {_LEAD_OF_FACILITY}
        WHERE id = (
            SELECT e.id FROM core_event e
            JOIN core_case c ON c.id = e.case_id
            JOIN core_documenttype dt ON dt.id = e.document_type_id
            LEFT JOIN core_client cl ON cl.id = e.client_id
            WHERE c.facility_id = %(fac)s AND {_EVENT_CONTRACT_WHERE}
            ORDER BY e.id LIMIT 1
        )
        """,  # noqa: S608
        params,
    )
    # 5. sample_workitem: created_by → lead, assigned_to → NULL.
    cur.execute(
        f"""
        UPDATE core_workitem SET created_by_id = {_LEAD_OF_FACILITY}, assigned_to_id = NULL
        WHERE id = (
            SELECT id FROM core_workitem
            WHERE facility_id = %(fac)s AND is_deleted = false
            ORDER BY id LIMIT 1
        )
        """,  # noqa: S608
        params,
    )
    # 6. Feld-Sensitivität des authz_attachment neutralisieren: der Seed kennt
    #    nur HIGH-File-FieldTemplates (alle Attachments hängen an „high"-Feldern)
    #    — user_can_see_field nimmt max(doc, feld), also 403 für staff/assistant
    #    trotz NORMAL-Event (views/attachments.py). Der Unit-Fixture-Vertrag
    #    nutzt ein frisches Template MIT leerer Sensitivität (= vom DocumentType
    #    erben, conftest._make_attachment) — daher hier '' setzen, nur für
    #    Templates der Attachments am konformen Event. Statuscode-neutral für
    #    alle anderen Zellen (Sensitivität steuert nur Sichtbarkeit/Download).
    cur.execute(
        f"""
        UPDATE core_fieldtemplate SET sensitivity = ''
        WHERE id IN (
            SELECT a.field_template_id FROM core_eventattachment a
            WHERE a.is_current AND a.event_id = ({_CONFORMABLE_EVENT})
        )
        """,  # noqa: S608
        params,
    )


# SQL pro Fixture-Name aus der Erwartungs-Tabelle. %(fac)s = Facility-ID.
# Tabellennamen sind db_table-Defaults (core_<modelname klein>, keine Overrides
# in src/core/models/); Status-Literale gegen die TextChoices verifiziert
# (Case.Status, RetentionProposal.Status, DeletionRequest.Status,
# DocumentType.Sensitivity, Client.ContactStage, User.Role).
# Soft-deletable Models (Client, Case, Event, Episode, WorkItem) werden auf
# is_deleted = false gefiltert, damit die Matrix keine Papierkorb-Objekte trifft.
# Determinismus: alle Queries ORDER BY id vor LIMIT 1 (Refs #1055, C1).
# Event-/WorkItem-Queries pinnen zusätzlich den Unit-Fixture-Vertrag
# (Owner = lead, NORMAL-Sensitivität, kein QUALIFIED-Client) — siehe
# prepare_audit_objects, das je ein solches Objekt garantiert.
HARVEST_SQL = {
    "client_identified": (
        "SELECT id FROM core_client WHERE facility_id = %(fac)s AND is_deleted = false ORDER BY id LIMIT 1"
    ),
    "client_trashed": (
        "SELECT id FROM core_client WHERE facility_id = %(fac)s AND is_deleted = true ORDER BY id LIMIT 1"
    ),
    # Episode/Goal/Milestone-URLs kombinieren case_open.pk mit den Kind-PKs —
    # die Views lookup-en strikt get_object_or_404(..., case=case)
    # (case_episodes.py, case_goals.py). Daher: offenen Fall bevorzugen, der
    # Episode UND Milestone (impliziert Goal) hat; die Kind-Queries unten
    # pinnen sich per %(case_open)s auf genau diesen Fall (harvest_ids).
    "case_open": """
        SELECT c.id FROM core_case c
        WHERE c.facility_id = %(fac)s AND c.status = 'open' AND c.is_deleted = false
        ORDER BY (
            EXISTS (SELECT 1 FROM core_episode ep WHERE ep.case_id = c.id AND ep.is_deleted = false)
            AND EXISTS (
                SELECT 1 FROM core_milestone m JOIN core_outcomegoal g ON g.id = m.goal_id WHERE g.case_id = c.id
            )
        ) DESC, c.id
        LIMIT 1
    """,
    "case_closed": (
        "SELECT id FROM core_case WHERE facility_id = %(fac)s AND status = 'closed' AND is_deleted = false "
        "ORDER BY id LIMIT 1"
    ),
    "case_event": f"""
        SELECT e.id FROM core_event e
        JOIN core_case c ON c.id = e.case_id
        JOIN core_documenttype dt ON dt.id = e.document_type_id
        LEFT JOIN core_client cl ON cl.id = e.client_id
        WHERE c.facility_id = %(fac)s AND {_EVENT_CONTRACT_WHERE}
          AND e.created_by_id IN (SELECT u.id FROM core_user u WHERE u.role = 'lead' AND u.facility_id = %(fac)s)
        ORDER BY e.id LIMIT 1
    """,  # noqa: S608
    "sample_event": f"""
        SELECT e.id FROM core_event e
        JOIN core_documenttype dt ON dt.id = e.document_type_id
        LEFT JOIN core_client cl ON cl.id = e.client_id
        WHERE e.facility_id = %(fac)s AND {_EVENT_CONTRACT_WHERE}
          AND e.created_by_id IN (SELECT u.id FROM core_user u WHERE u.role = 'lead' AND u.facility_id = %(fac)s)
        ORDER BY EXISTS (
            SELECT 1 FROM core_eventattachment a WHERE a.event_id = e.id AND a.is_current
        ) DESC, e.id
        LIMIT 1
    """,  # noqa: S608
    # %(case_open)s: an den geernteten case_open gepinnt (siehe oben) — die
    # Erwartungs-Tabelle baut die URLs aus case_open.pk + Kind-PK zusammen.
    "episode": (
        "SELECT ep.id FROM core_episode ep "
        "WHERE ep.case_id = %(case_open)s AND ep.is_deleted = false ORDER BY ep.id LIMIT 1"
    ),
    "outcome_goal": ("SELECT g.id FROM core_outcomegoal g WHERE g.case_id = %(case_open)s ORDER BY g.id LIMIT 1"),
    "milestone": (
        "SELECT m.id FROM core_milestone m JOIN core_outcomegoal g ON g.id = m.goal_id "
        "WHERE g.case_id = %(case_open)s ORDER BY m.id LIMIT 1"
    ),
    "sample_workitem": (
        "SELECT w.id FROM core_workitem w "
        "WHERE w.facility_id = %(fac)s AND w.is_deleted = false "
        "AND w.created_by_id IN (SELECT u.id FROM core_user u WHERE u.role = 'lead' AND u.facility_id = %(fac)s) "
        "AND (w.assigned_to_id IS NULL OR w.assigned_to_id = w.created_by_id) "
        "ORDER BY w.id LIMIT 1"
    ),
    # Attachment am selben Event, das die sample_event-Query liefert —
    # attachment_download kombiniert sample_event.pk + authz_attachment.pk.
    "authz_attachment": f"""
        SELECT a.id FROM core_eventattachment a
        WHERE a.is_current AND a.event_id = (
            SELECT e.id FROM core_event e
            JOIN core_documenttype dt ON dt.id = e.document_type_id
            LEFT JOIN core_client cl ON cl.id = e.client_id
            WHERE e.facility_id = %(fac)s AND {_EVENT_CONTRACT_WHERE}
              AND e.created_by_id IN (SELECT u.id FROM core_user u WHERE u.role = 'lead' AND u.facility_id = %(fac)s)
            ORDER BY EXISTS (
                SELECT 1 FROM core_eventattachment a2 WHERE a2.event_id = e.id AND a2.is_current
            ) DESC, e.id
            LIMIT 1
        )
        ORDER BY a.id LIMIT 1
    """,  # noqa: S608
    "audit_entry": "SELECT id FROM core_auditlog WHERE facility_id = %(fac)s ORDER BY id LIMIT 1",
    "retention_proposal": (
        "SELECT id FROM core_retentionproposal WHERE facility_id = %(fac)s AND status = 'pending' ORDER BY id LIMIT 1"
    ),
    "legal_hold": (
        "SELECT id FROM core_legalhold WHERE facility_id = %(fac)s AND dismissed_at IS NULL ORDER BY id LIMIT 1"
    ),
    "deletion_request": (
        "SELECT id FROM core_deletionrequest WHERE facility_id = %(fac)s AND status = 'pending' ORDER BY id LIMIT 1"
    ),
}

# Drift-Guard für die Zweiphasen-Ernte: case_open muss vor den Queries
# liegen, die %(case_open)s referenzieren (harvest_ids füllt es on-the-fly).
_HARVEST_ORDER = list(HARVEST_SQL)
assert all(
    _HARVEST_ORDER.index("case_open") < _HARVEST_ORDER.index(name) for name in ("episode", "outcome_goal", "milestone")
), "HARVEST_SQL: case_open muss vor episode/outcome_goal/milestone stehen"

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
    """``fixture_name → id`` (oder ``None`` = SETUP-ERROR-Zelle im Report).

    Zweiphasig: sobald ``case_open`` geerntet ist, steht seine ID den
    nachfolgenden Queries als ``%(case_open)s`` zur Verfügung (Episode/Goal/
    Milestone müssen im SELBEN Fall liegen — Views lookup-en case-gebunden).
    Dict-Reihenfolge von HARVEST_SQL stellt sicher, dass case_open vor den
    abhängigen Queries läuft; psycopg ignoriert überzählige Mapping-Keys.
    """
    cur = _superuser_cursor(conn)
    out = {}
    params = {"fac": fac_id, "case_open": None}
    for name, sql in HARVEST_SQL.items():
        cur.execute(sql, params)
        row = cur.fetchone()
        out[name] = row[0] if row else None
        if name == "case_open":
            params["case_open"] = out[name]
    return out


def write_report(rows, header_findings, meta):
    """Markdown-Report nach REPORT_PATH schreiben; gibt den Pfad zurück.

    ``rows``: Dicts mit url_name, category, method, actor, expected, status,
    verdict ("OK"/"ABWEICHUNG"/"SETUP-ERROR"/"ERROR") und optional ``known``
    (M6: Annotation bekannter Unit-Matrix-Gaps). ``header_findings``: fertige
    Markdown-Bullet-Zeilen. ``meta``: Dict mit ``date`` und ``commit``.
    """
    ok_count = sum(1 for r in rows if r["verdict"] == "OK")
    deviations = [r for r in rows if r["verdict"] == "ABWEICHUNG"]
    setup_errors = [r for r in rows if r["verdict"] == "SETUP-ERROR"]
    request_errors = [r for r in rows if r["verdict"] == "ERROR"]

    # Aggregation Kategorie × Akteur (Insertion-Order = EXPECTATIONS-Reihenfolge).
    agg = {}
    for r in rows:
        bucket = agg.setdefault((r["category"], r["actor"]), {"OK": 0, "ABWEICHUNG": 0, "SETUP-ERROR": 0, "ERROR": 0})
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
        f"**{ok_count} OK · {len(deviations)} Abweichungen · {len(setup_errors)} Setup-Fehler · "
        f"{len(request_errors)} Request-Fehler** ({len(rows)} Zellen gesamt)",
        "",
        "## Aggregat: Kategorie × Akteur",
        "",
        "| Kategorie | Akteur | OK | Abweichung | Setup-Error | Request-Error |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for (category, actor), bucket in agg.items():
        lines.append(
            f"| {category} | {actor} | {bucket['OK']} | {bucket['ABWEICHUNG']} | "
            f"{bucket['SETUP-ERROR']} | {bucket['ERROR']} |"
        )

    lines += ["", "## Abweichungen", ""]
    if deviations:
        lines += [
            # „Bekannt": ✔ + Begründung, wenn die Zelle in KNOWN_GAPS der
            # Unit-Matrix dokumentiert ist (test_authz_matrix.py, Refs #1055).
            "| Endpoint | Methode | Akteur | Erwartet | Ist | Bekannt |",
            "|---|---|---|---|---|---|",
        ]
        for r in deviations:
            lines.append(
                f"| `{r['url_name']}` | {r['method']} | {r['actor']} | {r['expected']} | {r['status']} | "
                f"{r.get('known', '')} |"
            )
    else:
        lines.append("Keine. ✅")

    lines += ["", "## Setup- & Request-Fehler", ""]
    if setup_errors or request_errors:
        for r in setup_errors:
            lines.append(f"- `{r['url_name']}` ({r['method']}, {r['actor']}): benötigte Objekt-ID nicht erntbar")
        for r in request_errors:
            lines.append(f"- `{r['url_name']}` ({r['method']}, {r['actor']}): {r['expected']}")
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
        "- IDOR-Probes laufen mit zuvor geöffnetem Sudo-Fenster (echter POST auf"
        " `/sudo/`): sudo-geschützte Views (`core:client_export_*`) würden sonst"
        " pk-unabhängig 302 → `/sudo/` antworten, bevor der Objekt-Lookup läuft —"
        " die Probe erreicht so den Scoping-Code. Die vertikale Matrix lief davor,"
        " ihre 302-→-`/sudo/`-Beobachtungen sind unbeeinflusst.",
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
