"""Pin-Tests Welle L (release-0.99) — Vollmutation #2174/#2193, Batch **compliance-3**.

Modul ``core.services.compliance.db_roles`` (36 Survivors, gesamtes Batch).
Liste: ``/work/wellen/welle-l-0.99/reports/m-scratch/batch-compliance-3.json``.
Quelle der Mutanten-Rümpfe: Mutantenbaum
``mutants/src/core/services/compliance/db_roles.py``. Vollständige Triage
(alle 36 Mutanten, inkl. ``equivalent``-Begründungen):
``reports/m-scratch/pins-compliance-3/triage-099-compliance-3.json``.

Dieses Modul traegt den Sicherheitsvertrag der DB-Rollen-Compliance-Checks
(App-Runtime-Rolle NOSUPERUSER + NOBYPASSRLS, Admin-Rolle NOSUPERUSER +
BYPASSRLS, Owner-Trennung der Append-Only-Tabellen im Drei-Rollen-Modell,
Refs #919/#1026/#1835). Die bestehende Abdeckung (``test_check_db_roles.py``,
``test_mutation_followup_db_roles.py``) mockt ``_query_role``/
``_query_db_owner``/``_query_table_owner`` fast durchgehend und prueft Meldungen
nur per ``in``-Substring — die 36 Survivors liegen an vier Stellen, die dort
NICHT abgedeckt sind:

1. **``x__query_role__mutmut_11``**: ``_query_role`` liefert ``bool(row[1]),
   bool(row[1])`` statt ``bool(row[0]), bool(row[1])`` — ``rolsuper`` wird
   durch den ``rolbypassrls``-Wert ueberschrieben. Da JEDER bestehende Test
   ``_query_role`` komplett wegmockt, laeuft die reale Katalogabfrage nie —
   dieser Pin ruft sie echt gegen eine frisch angelegte Test-Rolle auf.
2. **``x_check_db_ownership__mutmut_11``/``_13``** und
   **``x_check_db_roles__mutmut_10``/``_12``**: der Fallback
   ``settings.DATABASES.get("default", {})`` wird zu ``.get("default", None)``
   bzw. verliert den Default-Parameter ganz (faellt ebenfalls auf ``None``).
   Fehlt der ``"default"``-Alias in ``settings.DATABASES`` komplett (nicht nur
   der ``"USER"``-Key — das deckt die Bestandssuite ab), crasht der Mutant mit
   ``AttributeError`` auf ``None.get("USER")`` statt geordnet mit leerem
   Ergebnis/Konfigurationsfehler zurueckzukehren.
3. **Meldungstexte in ``check_db_ownership``** (Append-Only-Tabelle gehoert
   App-Rolle / gehoert weder App- noch Admin-Rolle / Abschluss-Zeile
   "Ownership-Umzug ...") und **in ``check_db_roles``** (Config-Fehler "App-
   Rolle nicht bestimmbar" / "POSTGRES_ADMIN_USER ist nicht gesetzt ..."):
   XX-Marker-, Gross-/Kleinschreib- und (bei ``check_db_roles``) ein echter
   Meldungs-VERLUST-Mutant (``None`` statt Text). Die Bestandstests pruefen
   diese Meldungen nur per Teilstring auf variable Werte (Rollen-/Tabellen-
   namen), nie auf den vollstaendigen englischen/deutschen Wortlaut — die
   Pins hier assertieren die komplette Problemliste/Fehlerliste exakt
   (``==``) und toeten damit alle Text-Varianten in einem Aufwasch.
4. **``x__db_role_checks__mutmut_20``/``_21``/``_22``/``_36``/``_37``/``_38``**:
   die UI-Labels ``"App-DB-Rolle NOSUPERUSER"``/``"App-DB-Rolle kein
   BYPASSRLS"`` der beiden aus der App-Rolle abgeleiteten Checks — kein
   Bestandstest liest ``ComplianceCheck.label`` fuer diese beiden Checks.

Die vier als ``equivalent`` eingestuften Survivors (``x__query_role__mutmut_6``,
``x__query_db_owner__mutmut_3``/``_4``, ``x__query_table_owner__mutmut_6``)
sind reine SQL-Keyword-/Identifier-Gross-Kleinschreib-Varianten — unter
PostgreSQL aequivalent (unquoted Identifier/Keywords werden auf
Kleinschreibung gefaltet) und je einmal live gegen die Test-DB verifiziert
(gleiches Ergebnis wie das Original, siehe Triage-Begruendung); dafuer gibt
es bewusst KEINEN Pin.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest
from django.db import connection

from core.services.compliance import db_roles
from tests.conftest import xdist_role_suffix

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

_PROBE_ROLE = f"pin_cdb3_query_role_probe{xdist_role_suffix()}"


@pytest.mark.django_db(transaction=True)
def test_query_role_verwechselt_rolsuper_nicht_mit_rolbypassrls():
    """Pin ``x__query_role__mutmut_11``: die Mutante liefert
    ``bool(row[1]), bool(row[1])`` statt ``bool(row[0]), bool(row[1])`` —
    ``rolsuper`` wird durch den ``rolbypassrls``-Wert ersetzt. Echte
    Katalogabfrage gegen eine frisch angelegte Rolle mit
    ``NOSUPERUSER BYPASSRLS`` (rolsuper=False, rolbypassrls=True — die
    beiden Werte muessen sich unterscheiden, sonst waere die Vertauschung
    nicht beobachtbar): das Original liefert ``(False, True)``, die Mutante
    liefert ``(True, True)``.
    """
    with connection.cursor() as cur:
        cur.execute(f"DROP ROLE IF EXISTS {_PROBE_ROLE}")
        cur.execute(f"CREATE ROLE {_PROBE_ROLE} NOSUPERUSER BYPASSRLS")
    try:
        result = db_roles._query_role(_PROBE_ROLE)
    finally:
        with connection.cursor() as cur:
            cur.execute(f"DROP ROLE IF EXISTS {_PROBE_ROLE}")
    assert result == (False, True)


def test_check_db_ownership_ohne_default_alias_bricht_nicht_ab(monkeypatch, settings):
    """Pins ``x_check_db_ownership__mutmut_11``/``_13``: fehlt der
    ``"default"``-Alias in ``settings.DATABASES`` komplett (nicht nur der
    ``"USER"``-Key), liefert das Original per Default-Parameter ``{}`` ein
    geordnetes ``check_db_ownership() == []`` (App-Rolle unbestimmbar ->
    frueher Ausstieg). Die Mutanten ersetzen den Default durch ``None`` bzw.
    lassen ihn ganz weg (faellt ebenfalls auf ``None``) -> ``None.get("USER")``
    crasht mit ``AttributeError``, bevor die Pruefung ueberhaupt beginnt.
    """
    monkeypatch.delitem(settings.DATABASES, "default")
    monkeypatch.delenv("POSTGRES_APP_USER", raising=False)

    assert db_roles.check_db_ownership() == []


def test_check_db_roles_ohne_default_alias_liefert_konfigurationsfehler_statt_crash(monkeypatch, settings):
    """Pins ``x_check_db_roles__mutmut_10``/``_12`` (Default-Fallback ``None``
    statt ``{}`` -> Crash statt Konfigurationsfehler) sowie die Meldungstexte
    ``x_check_db_roles__mutmut_18``/``_19``/``_20``/``_21`` (App-Rolle nicht
    bestimmbar: Meldungs-VERLUST zu ``None``, XX-Marker, klein-/
    grossgeschrieben) und ``x_check_db_roles__mutmut_50``/``_52``/``_53``/
    ``_54``/``_55`` (POSTGRES_ADMIN_USER-Meldung: XX-Marker/Gross-
    Kleinschreibung in beiden Haelften der verketteten Zeichenkette).

    Fehlen ``"default"``-Alias UND ``POSTGRES_ADMIN_USER`` gleichzeitig,
    muss ``check_db_roles()`` OHNE Exception zwei exakte Konfigurationsfehler
    liefern statt (Mutante) beim ersten Fallback zu crashen bzw. die Meldung
    zu verstuemmeln.
    """
    monkeypatch.delitem(settings.DATABASES, "default")
    monkeypatch.delenv("POSTGRES_APP_USER", raising=False)
    monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)

    checks, config_errors = db_roles.check_db_roles()

    assert checks == []
    assert config_errors == [
        "App-Rolle nicht bestimmbar (weder POSTGRES_APP_USER noch settings.DATABASES['default']['USER'] gesetzt).",
        "POSTGRES_ADMIN_USER ist nicht gesetzt — Admin-/Maintenance-Rolle nicht prüfbar. "
        "Refs #902: Drei-Rollen-Modell in docker-compose.prod.yml.",
    ]


def test_check_db_ownership_app_rolle_besitzt_append_only_tabelle_wird_exakt_gemeldet(monkeypatch):
    """Pins ``x_check_db_ownership__mutmut_48``/``_49``/``_50`` (Meldung
    "... 'ALTER TABLE ... DISABLE TRIGGER' abschalten (A1.4)." mit XX-Marker/
    klein-/grossgeschrieben) sowie die Abschluss-Zeile
    ``x_check_db_ownership__mutmut_56``/``_57``/``_58``/``_59``/``_60``/``_61``
    ("Ownership-Umzug auf die Admin-Rolle noch nicht durchgefuehrt
    (Fail-open verhindert). ..." — XX-Marker/Gross-Kleinschreibung in Praefix
    UND Suffix).

    Szenario: Admin besitzt die DB (kein DB-Owner-Fund), die App-Rolle
    besitzt faelschlich ``core_auditlog`` (Bestands-DB ohne § 9.6-Umzug),
    ``core_eventhistory`` gehoert sauber dem Admin. Exakte Listen-Assertion
    (``==``) auf die vollstaendige Problemliste — jede Text-Abweichung
    (Marker, Case, Wortlaut) schlaegt fehl.
    """
    monkeypatch.setenv("POSTGRES_APP_USER", "cdb3_app_owns_table")
    monkeypatch.setenv("POSTGRES_ADMIN_USER", "cdb3_admin_owns_table")
    monkeypatch.setattr(db_roles, "_query_db_owner", lambda: "cdb3_admin_owns_table")
    monkeypatch.setattr(
        db_roles,
        "_query_table_owner",
        lambda table: {
            "core_auditlog": "cdb3_app_owns_table",
            "core_eventhistory": "cdb3_admin_owns_table",
        }[table],
    )

    problems = db_roles.check_db_ownership()

    assert problems == [
        "Append-Only-Tabelle 'core_auditlog' gehoert der App-Runtime-Rolle "
        "'cdb3_app_owns_table' — als Owner koennte sie die Immutability-Trigger per "
        "'ALTER TABLE ... DISABLE TRIGGER' abschalten (A1.4).",
        "Ownership-Umzug auf die Admin-Rolle noch nicht durchgefuehrt "
        "(Fail-open verhindert). Ablauf: docs/ops-runbook.md § 9.6 (Refs #1026).",
    ]


def test_check_db_ownership_tabelle_gehoert_weder_app_noch_admin_wird_exakt_gemeldet(monkeypatch):
    """Pins ``x_check_db_ownership__mutmut_52``/``_53``/``_54`` (Meldung
    "... weder der App- noch der Admin-Rolle. Owner muss ... kann der
    Wartungspfad die Immutability-Trigger nicht bedienen (A1.4)." mit
    XX-Marker/klein-/grossgeschrieben; Refs #2086).

    Szenario: eine halb gefahrene Bootstrap-Topologie — ``core_auditlog``
    gehoert weiterhin einer dritten (Bootstrap-)Rolle, weder App noch Admin.
    Exakte Listen-Assertion auf die vollstaendige Problemliste.
    """
    monkeypatch.setenv("POSTGRES_APP_USER", "cdb3_app_neither")
    monkeypatch.setenv("POSTGRES_ADMIN_USER", "cdb3_admin_neither")
    monkeypatch.setattr(db_roles, "_query_db_owner", lambda: "cdb3_admin_neither")
    monkeypatch.setattr(
        db_roles,
        "_query_table_owner",
        lambda table: {
            "core_auditlog": "cdb3_bootstrap_legacy",
            "core_eventhistory": "cdb3_admin_neither",
        }[table],
    )

    problems = db_roles.check_db_ownership()

    assert problems == [
        "Append-Only-Tabelle 'core_auditlog' gehoert 'cdb3_bootstrap_legacy' — weder der "
        "App- noch der Admin-Rolle. Owner muss 'cdb3_admin_neither' sein, sonst "
        "kann der Wartungspfad die Immutability-Trigger nicht bedienen (A1.4).",
        "Ownership-Umzug auf die Admin-Rolle noch nicht durchgefuehrt "
        "(Fail-open verhindert). Ablauf: docs/ops-runbook.md § 9.6 (Refs #1026).",
    ]


def test_db_role_checks_labels_sind_exakt(monkeypatch):
    """Pins ``x__db_role_checks__mutmut_20``/``_21``/``_22`` (Label
    "App-DB-Rolle NOSUPERUSER" mit XX-Marker/klein-/grossgeschrieben) sowie
    ``x__db_role_checks__mutmut_36``/``_37``/``_38`` (Label "App-DB-Rolle
    kein BYPASSRLS" mit XX-Marker/klein-/grossgeschrieben).

    ``check_db_roles`` wird gestubbt (kein Postgres-Roundtrip noetig — die
    beiden Labels haengen nur an ``rc.label == "App"``, nicht an den
    Attributwerten), damit der Test unabhaengig vom OK/CRITICAL/UNKNOWN-Zweig
    bleibt. Beide UI-Labels sind die einzige Beobachtungsgroesse des
    Compliance-Dashboards fuer diese beiden Checks.
    """
    app_check = db_roles.RoleCheck(
        role="cdb3_label_app",
        label="App",
        expected_super=False,
        expected_bypassrls=False,
        actual_super=False,
        actual_bypassrls=False,
    )
    monkeypatch.setattr(db_roles, "check_db_roles", lambda: ([app_check], []))

    checks = db_roles._db_role_checks()
    labels = {c.key: c.label for c in checks}

    assert labels["db_role_app_nosuperuser"] == "App-DB-Rolle NOSUPERUSER"
    assert labels["db_role_app_nobypassrls"] == "App-DB-Rolle kein BYPASSRLS"
