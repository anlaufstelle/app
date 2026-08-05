"""Helfer für den Live-AuthZ-Audit (Refs #1055): Logins, ID-Harvesting, Report."""

import os
from pathlib import Path

import psycopg
import requests

PASSWORD = "anlaufstelle2026"
REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = REPO_ROOT / "docs" / "archive" / "audits" / "2026-06-12-a1-laufzeit-authz-matrix.md"

# Seed-Logins (CLAUDE.md): Facility 1 ohne Suffix, Facility 2 mit _1.
ACTOR_LOGINS = {
    "facility_admin": "admin",
    "lead": "emma",
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


# Subquery: erster lead-User der Facility (fac2-Leads heißen emma_1 etc.,
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
        SELECT 1 FROM core_attachment a WHERE a.event_id = e.id AND a.is_current
    ) DESC, e.id
    LIMIT 1
"""  # noqa: S608 — nur statische Fragmente, keine Userdaten.

# Owner-Unterabfragen der beiden neuen Download-Routen (M24, Refs #1738).
# EINE Quelle für Harvesting UND Nachbesserung: die Erwartungs-Tabelle baut
# ``client_attachment_download`` aus client_identified.pk + Anhang-PK (bzw.
# ``case_attachment_download`` aus case_open.pk + Anhang-PK) zusammen — zeigen
# Owner-Query und Anhang-Query auf verschiedene Owner, antwortet die Route für
# JEDEN Akteur 404 und die Zeile wäre eine stille Falschmeldung.
_FIRST_CLIENT = "SELECT id FROM core_client WHERE facility_id = %(fac)s AND is_deleted = false ORDER BY id LIMIT 1"

# Episode/Goal/Milestone-URLs kombinieren case_open.pk mit den Kind-PKs — die
# Views lookup-en strikt get_object_or_404(..., case=case) (case_episodes.py,
# case_goals.py). Daher: offenen Fall bevorzugen, der Episode UND Milestone
# (impliziert Goal) hat; die Kind-Queries pinnen sich per %(case_open)s auf
# genau diesen Fall (harvest_ids).
_CASE_OPEN = """
    SELECT c.id FROM core_case c
    WHERE c.facility_id = %(fac)s AND c.status = 'open' AND c.is_deleted = false
    ORDER BY (
        EXISTS (SELECT 1 FROM core_episode ep WHERE ep.case_id = c.id AND ep.is_deleted = false)
        AND EXISTS (
            SELECT 1 FROM core_milestone m JOIN core_outcomegoal g ON g.id = m.goal_id WHERE g.case_id = c.id
        )
    ) DESC, c.id
    LIMIT 1
"""


# Anker der ~20 Konfigurations-Endpoints (Refs #1783). Alle URLs dort setzen
# ``pk`` = Dokumentationstyp und kombinieren ihn mit einem KIND desselben Typs
# (``dtf_pk``/``group_pk``/``condition_pk``, Views lookup-en strikt
# ``get_object_or_404(..., document_type=doc_type)``). Deshalb wird der Typ
# zuerst geerntet und die Kind-Queries pinnen sich per %(doc_type_contact)s.
#
# Die vier Bedingungen sind NICHT kosmetisch:
#
# * ``core_fieldgroup``/mindestens 3 Feld-Zuordnungen — sonst haetten
#   ``field_group`` bzw. ``doc_type_field`` kein Objekt (zwei DTFs traegt schon
#   die Bedingung, die dritte ist die frei konsumierbare Zuordnung).
# * mindestens ein Ereignis — ``config_doctype_delete`` POST laeuft in der
#   Matrix als ERLAUBTE Zelle (facility_admin steht in ``ROLES`` an erster
#   Stelle). Nur mit Bestandsreferenz greift der PROTECT-Blocker
#   (``config_service.delete_document_type``) und der Anker ueberlebt seine
#   eigene Loeschzelle; sonst faenden ALLE nachfolgenden config-Zellen 404 und
#   der Report waere voller Falschmeldungen.
# * mindestens ein zulaessiges Quellfeld — Voraussetzung fuer die Bedingung,
#   die ``prepare_audit_objects`` anlegt (der Seed-Katalog kennt keine).
_CONFIG_DOC_TYPE = """
    SELECT dt.id FROM core_documenttype dt
    WHERE dt.facility_id = %(fac)s
      AND EXISTS (SELECT 1 FROM core_fieldgroup g WHERE g.document_type_id = dt.id)
      AND EXISTS (SELECT 1 FROM core_event e WHERE e.document_type_id = dt.id)
      AND (SELECT count(*) FROM core_documenttypefield f WHERE f.document_type_id = dt.id) >= 3
      AND EXISTS (
          SELECT 1 FROM core_documenttypefield f
          JOIN core_fieldtemplate t ON t.id = f.field_template_id
          WHERE f.document_type_id = dt.id AND t.field_type <> 'file' AND NOT t.is_encrypted
      )
    ORDER BY dt.id LIMIT 1
"""

# Quell-/Zielfeld der Audit-Bedingung: das Quellfeld muss ein zulaessiger
# Bedingungs-Quelltyp und unverschluesselt sein (core/services/conditions.py:
# ALLOWED_SOURCE_FIELD_TYPES = alles ausser ``file``), das Zielfeld irgendein
# anderes Feld desselben Typs (CheckConstraint fieldcondition_no_self_reference).
_CONDITION_SOURCE_FIELD = f"""
    SELECT f.id FROM core_documenttypefield f
    JOIN core_fieldtemplate t ON t.id = f.field_template_id
    WHERE f.document_type_id = ({_CONFIG_DOC_TYPE})
      AND t.field_type <> 'file' AND NOT t.is_encrypted
    ORDER BY f.id LIMIT 1
"""  # noqa: S608 — nur statische Fragmente, keine Userdaten.

_CONDITION_TARGET_FIELD = f"""
    SELECT f.id FROM core_documenttypefield f
    WHERE f.document_type_id = ({_CONFIG_DOC_TYPE})
      AND f.id <> ({_CONDITION_SOURCE_FIELD})
    ORDER BY f.id LIMIT 1
"""  # noqa: S608 — nur statische Fragmente, keine Userdaten.

# Der Seed-Katalog (core/packages/default.yaml) deklariert KEINE ``conditions``
# — ohne diesen Schritt gaebe es fuer ``field_condition`` nie ein Objekt.
# ``is_not_empty`` ist bewusst gewaehlt: der Operator ist fuer JEDEN zulaessigen
# Quelltyp erlaubt und braucht keinen ``value`` (VALUELESS_OPERATORS), die
# Bedingung ist damit unabhaengig vom konkreten Katalog fachlich gueltig.
# Idempotent ueber NOT EXISTS auf denselben Dokumentationstyp.
_ENSURE_CONFIG_CONDITION = f"""
    INSERT INTO core_fieldcondition (id, target_field_id, depends_on_field_id, operator, value, group_index)
    SELECT gen_random_uuid(), tgt.id, src.id, 'is_not_empty', NULL, 0
    FROM ({_CONDITION_SOURCE_FIELD}) AS src(id), ({_CONDITION_TARGET_FIELD}) AS tgt(id)
    WHERE NOT EXISTS (
        SELECT 1 FROM core_fieldcondition c
        JOIN core_documenttypefield f ON f.id = c.target_field_id
        WHERE f.document_type_id = ({_CONFIG_DOC_TYPE})
    )
"""  # noqa: S608 — nur statische Fragmente, keine Userdaten.


# M4 (Refs #1060): Der Seed kennt keine Berichtigungsantraege — ohne diesen
# Schritt bliebe ``rectification_review`` dauerhaft unerntbar (SETUP-ERROR-Zeile
# im Report). Die vier verschluesselten Spalten bleiben bewusst NULL: dieses
# Modul spricht reines SQL ohne Django-Krypto, und die Ansicht kommt mit
# leeren Werten aus (``—``). Antragsteller ist die Fachkraft, damit Leitung und
# Anwendungsbetreuung nicht am Selbst-Review-Guard haengenbleiben.
_ENSURE_RECTIFICATION_REQUEST = """
    INSERT INTO core_rectificationrequest
        (id, facility_id, target_client_id, field_path, old_value, new_value, reason,
         review_note, status, requested_by_id, reviewed_by_id, created_at, reviewed_at)
    SELECT gen_random_uuid(), %(fac)s, c.id, 'notes', NULL, NULL, NULL,
           NULL, 'pending', u.id, NULL, now(), NULL
    FROM (
        SELECT id FROM core_client
        WHERE facility_id = %(fac)s AND is_deleted = false AND is_active = true
        ORDER BY id LIMIT 1
    ) AS c(id),
    (
        SELECT id FROM core_user
        WHERE facility_id = %(fac)s AND role = 'staff'
        ORDER BY id LIMIT 1
    ) AS u(id)
    WHERE NOT EXISTS (
        SELECT 1 FROM core_rectificationrequest
        WHERE facility_id = %(fac)s AND status = 'pending'
    )
"""


def _owner_attachment_source(owner_column):
    """Erster owner-eigener Anhang der Facility (``client``/``case``)."""
    return f"""
        SELECT a.id FROM core_attachment a
        WHERE a.facility_id = %(fac)s AND a.is_current AND a.deleted_at IS NULL
          AND a.{owner_column} IS NOT NULL
        ORDER BY a.id LIMIT 1
    """  # noqa: S608 — owner_column ist ein Literal aus diesem Modul.


def _owner_doc_type_source(owner_table, owner_column):
    """``document_type_id`` des Owners, an dem der Seed-Anhang aktuell hängt."""
    return f"""
        SELECT src.document_type_id FROM {owner_table} src
        JOIN core_attachment a ON a.{owner_column} = src.id
        WHERE a.facility_id = %(fac)s AND a.is_current AND a.deleted_at IS NULL
        ORDER BY a.id LIMIT 1
    """  # noqa: S608 — beide Argumente sind Literale aus diesem Modul.


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
    7./8. Vertragskonforme authz_client_attachment/authz_case_attachment
       (M24, Refs #1738): Der Seed hängt seine beiden owner-generischen
       Beispielanhänge an den ERSTEN Client bzw. Fall der Facility
       (core/seed/attachments.py) — das ist nicht zwingend derselbe Owner, den
       die Erwartungs-Tabelle in die URL setzt (Schritt 1 schiebt genau den
       ersten Client in den Papierkorb; case_open bevorzugt einen Fall mit
       Episode + Milestone). Deshalb je Owner-Art: erst den Dokumentationstyp
       auf den Ziel-Owner übertragen (ohne Typ antwortet das Download-Gate
       fail-closed 404, ADR-039 §12 Stufe 2), dann den Anhang umhängen —
       Muster von Schritt 3. Idempotent: nach dem ersten Lauf sind Quelle und
       Ziel identisch, beide UPDATEs werden zu No-ops.
    9. Eine ``FieldCondition`` am Konfigurations-Anker (M24-Folge, Refs #1783):
       der Seed-Katalog deklariert keine ``conditions``, die vier
       Regel-Editor-Endpoints wären ohne diesen Schritt dauerhaft
       unerntbar. Siehe ``_ENSURE_CONFIG_CONDITION``.
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
        UPDATE core_attachment SET event_id = ({_CONFORMABLE_EVENT})
        WHERE id = (
            SELECT a.id FROM core_attachment a
            JOIN core_event ae ON ae.id = a.event_id
            WHERE ae.facility_id = %(fac)s AND a.is_current
            ORDER BY a.id LIMIT 1
        )
        AND NOT EXISTS (
            SELECT 1 FROM core_attachment a2
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
            SELECT a.field_template_id FROM core_attachment a
            WHERE a.is_current AND a.event_id = ({_CONFORMABLE_EVENT})
        )
        """,  # noqa: S608
        params,
    )
    # 7a./8a. Dokumentationstyp des Seed-Owners auf den Ziel-Owner übertragen.
    for owner_table, owner_column, target_sql in (
        ("core_client", "client_id", _FIRST_CLIENT),
        ("core_case", "case_id", _CASE_OPEN),
    ):
        source_sql = _owner_doc_type_source(owner_table, owner_column)
        cur.execute(
            f"""
            UPDATE {owner_table} SET document_type_id = ({source_sql})
            WHERE id = ({target_sql}) AND EXISTS ({source_sql})
            """,  # noqa: S608
            params,
        )
        # 7b./8b. Anhang auf den Ziel-Owner umhängen.
        cur.execute(
            f"""
            UPDATE core_attachment SET {owner_column} = ({target_sql})
            WHERE id = ({_owner_attachment_source(owner_column)})
            """,  # noqa: S608
            params,
        )
    # 9. Feldbedingung am Konfigurations-Anker (Refs #1783).
    cur.execute(_ENSURE_CONFIG_CONDITION, params)
    # 10. Offener Berichtigungsantrag (M4, Refs #1060).
    cur.execute(_ENSURE_RECTIFICATION_REQUEST, params)


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
    # Geteilt mit prepare_audit_objects (Schritte 7/8) — siehe dort.
    "client_identified": _FIRST_CLIENT,
    "client_trashed": (
        "SELECT id FROM core_client WHERE facility_id = %(fac)s AND is_deleted = true ORDER BY id LIMIT 1"
    ),
    "case_open": _CASE_OPEN,
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
            SELECT 1 FROM core_attachment a WHERE a.event_id = e.id AND a.is_current
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
        SELECT a.id FROM core_attachment a
        WHERE a.is_current AND a.event_id = (
            SELECT e.id FROM core_event e
            JOIN core_documenttype dt ON dt.id = e.document_type_id
            LEFT JOIN core_client cl ON cl.id = e.client_id
            WHERE e.facility_id = %(fac)s AND {_EVENT_CONTRACT_WHERE}
              AND e.created_by_id IN (SELECT u.id FROM core_user u WHERE u.role = 'lead' AND u.facility_id = %(fac)s)
            ORDER BY EXISTS (
                SELECT 1 FROM core_attachment a2 WHERE a2.event_id = e.id AND a2.is_current
            ) DESC, e.id
            LIMIT 1
        )
        ORDER BY a.id LIMIT 1
    """,  # noqa: S608
    # M24 (Refs #1738): personen-/fall-eigene Anhaenge. Beide Downloads
    # kombinieren Owner-PK + Anhang-PK, die Query muss also den Anhang GENAU
    # des Owners liefern, den die Erwartungs-Tabelle in die URL setzt:
    # client_identified bzw. case_open (identische Unterabfragen wie oben).
    # ``prepare_audit_objects`` haengt die Seed-Anhaenge vorher auf genau diese
    # beiden Owner um (Schritte 7/8) — analog zu Schritt 3 fuer Ereignisse.
    "authz_client_attachment": """
        SELECT a.id FROM core_attachment a
        WHERE a.facility_id = %(fac)s AND a.is_current AND a.deleted_at IS NULL
          AND a.client_id = (
              SELECT id FROM core_client
              WHERE facility_id = %(fac)s AND is_deleted = false ORDER BY id LIMIT 1
          )
        ORDER BY a.id LIMIT 1
    """,
    # %(case_open)s: an den bereits geernteten case_open gepinnt (case_open
    # steht in dieser Dict-Reihenfolge davor, siehe _HARVEST_ORDER-Guard).
    "authz_case_attachment": """
        SELECT a.id FROM core_attachment a
        WHERE a.facility_id = %(fac)s AND a.is_current AND a.deleted_at IS NULL
          AND a.case_id = %(case_open)s
        ORDER BY a.id LIMIT 1
    """,
    # ---- Konfigurations-Endpoints (Refs #1783) ----------------------------
    # Bis dahin die einzige benannte Blindstelle des Live-Audits
    # (``LIVE_AUDIT_UNHARVESTED``): ~20 Endpoints liefen als SETUP-ERROR, also
    # ungeprüft, unter der 10%-Schwelle des Struktur-Asserts.
    # ``doc_type_contact`` MUSS vor seinen drei Kind-Queries stehen (siehe
    # ``_HARVEST_ORDER``-Guard) — sie pinnen sich per %(doc_type_contact)s auf
    # genau diesen Typ, weil die Views das Kind strikt am Elternteil suchen.
    "doc_type_contact": _CONFIG_DOC_TYPE,
    # Feld-Zuordnung, die KEINE Bedingung trägt: ``config_doctype_field_remove``
    # POST löscht sie (erlaubte Zelle), und ein CASCADE über
    # ``target_field``/``depends_on_field`` würde die Bedingung mitreißen — die
    # ``config_condition_*``-Zellen stehen in EXPECTATIONS danach und fänden
    # dann 404 (Falschmeldung auf einer Allow-Zelle).
    "doc_type_field": """
        SELECT f.id FROM core_documenttypefield f
        WHERE f.document_type_id = %(doc_type_contact)s
          AND NOT EXISTS (
              SELECT 1 FROM core_fieldcondition c
              WHERE c.target_field_id = f.id OR c.depends_on_field_id = f.id
          )
        ORDER BY f.id LIMIT 1
    """,
    "field_group": "SELECT id FROM core_fieldgroup WHERE document_type_id = %(doc_type_contact)s ORDER BY id LIMIT 1",
    "field_condition": """
        SELECT c.id FROM core_fieldcondition c
        JOIN core_documenttypefield f ON f.id = c.target_field_id
        WHERE f.document_type_id = %(doc_type_contact)s
        ORDER BY c.id LIMIT 1
    """,
    # Feldvorlage MIT Zuordnung: ``config_fieldtemplate_delete`` POST ist eine
    # erlaubte Zelle und läuft VOR ``config_fieldtemplate_toggle``. Nur mit
    # mindestens einer DocumentTypeField-Zuordnung greift der Lösch-Blocker
    # (``config_service.field_template_delete_blockers``) und die Vorlage
    # überlebt bis zur Toggle-Zelle.
    "field_template": """
        SELECT t.id FROM core_fieldtemplate t
        WHERE t.facility_id = %(fac)s
          AND EXISTS (SELECT 1 FROM core_documenttypefield f WHERE f.field_template_id = t.id)
        ORDER BY t.id LIMIT 1
    """,
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
    # --- M4 Import (Refs #1060) -------------------------------------------
    # Ein noch nicht abgeschlossener Lauf; der Seed legt keinen an, die Query
    # liefert dann ``None`` (SETUP-ERROR-Zelle im Live-Audit) — der Eintrag ist
    # trotzdem Pflicht, sonst zeigen die sieben import_*-Erwartungen ins Leere
    # (``test_every_fixture_reference_is_live_harvestable``).
    "import_job": (
        "SELECT id FROM core_importjob WHERE facility_id = %(fac)s "
        "AND status NOT IN ('committed', 'aborted') ORDER BY id LIMIT 1"
    ),
    # M4 (Refs #1060): garantiert durch ``prepare_audit_objects`` Schritt 10.
    "rectification_request": (
        "SELECT id FROM core_rectificationrequest WHERE facility_id = %(fac)s AND status = 'pending' "
        "ORDER BY id LIMIT 1"
    ),
}

# Eltern-Namen, deren geerntete ID den NACHFOLGENDEN Queries als
# gleichnamiger Parameter zur Verfügung steht (``harvest_ids`` füllt sie
# on-the-fly). Werte: die Kind-Queries, die den Parameter referenzieren — der
# Drift-Guard darunter pinnt die Reihenfolge im Dict.
_CHAINED_PARENTS = {
    "case_open": ("episode", "outcome_goal", "milestone", "authz_case_attachment"),
    # Refs #1783: die drei Kinder des Konfigurations-Ankers.
    "doc_type_contact": ("doc_type_field", "field_group", "field_condition"),
}

_HARVEST_ORDER = list(HARVEST_SQL)
for _parent, _children in _CHAINED_PARENTS.items():
    assert all(_HARVEST_ORDER.index(_parent) < _HARVEST_ORDER.index(_child) for _child in _children), (
        f"HARVEST_SQL: {_parent} muss vor {', '.join(_children)} stehen"
    )

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
    "foreign_client_attachment": "authz_client_attachment",
    "foreign_case_attachment": "authz_case_attachment",
    "foreign_audit_entry": "audit_entry",
    "foreign_retention_proposal": "retention_proposal",
    "foreign_legal_hold": "legal_hold",
    "foreign_deletion_request": "deletion_request",
    "foreign_rectification_request": "rectification_request",
    # Konfigurations-Endpoints (Refs #1783). Der Unit-Fixture-Name des fremden
    # Dokumentationstyps heisst ``foreign_doc_type`` (ohne ``_contact``) — die
    # Alias-Tabelle ist genau fuer solche Namensbrueche da.
    "foreign_doc_type": "doc_type_contact",
    "foreign_doc_type_field": "doc_type_field",
    "foreign_field_group": "field_group",
    "foreign_field_condition": "field_condition",
    "foreign_field_template": "field_template",
    # M4 Import (Refs #1060).
    "foreign_import_job": "import_job",
}


def harvest_ids(conn, fac_id):
    """``fixture_name → id`` (oder ``None`` = SETUP-ERROR-Zelle im Report).

    Mehrphasig: sobald ein Eltern-Name aus ``_CHAINED_PARENTS`` geerntet ist,
    steht seine ID den nachfolgenden Queries als gleichnamiger Parameter zur
    Verfügung — Episode/Goal/Milestone müssen im SELBEN Fall liegen,
    Feld-Zuordnung/-Gruppe/-Bedingung am SELBEN Dokumentationstyp (die Views
    suchen das Kind strikt am Elternteil). Die Dict-Reihenfolge von
    ``HARVEST_SQL`` stellt sicher, dass die Eltern vorher laufen
    (``_HARVEST_ORDER``-Guard); psycopg ignoriert überzählige Mapping-Keys.
    """
    cur = _superuser_cursor(conn)
    out = {}
    params = {"fac": fac_id, **dict.fromkeys(_CHAINED_PARENTS)}
    for name, sql in HARVEST_SQL.items():
        cur.execute(sql, params)
        row = cur.fetchone()
        out[name] = row[0] if row else None
        if name in _CHAINED_PARENTS:
            params[name] = out[name]
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
