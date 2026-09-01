"""Tests for PostgreSQL Row-Level-Security setup (#542).

Echtes RLS-Filtering kann nur als Non-Superuser-Rolle getestet werden.
Im Test-Setup ist der DB-User Superuser (bypassed RLS per Postgres-Default).
Diese Tests prüfen daher die Migration-Korrektheit — dass Policies und
``FORCE ROW LEVEL SECURITY`` auf allen erwarteten Tabellen existieren.

Das eigentliche RLS-Verhalten greift in Produktion, wo der Django-DB-User
explizit kein Superuser sein darf (siehe docs/coolify-deployment.md).
"""

import pytest
from django.apps import apps
from django.db import connection, models
from django.db.models import ForeignKey, OneToOneField
from django.test.utils import isolate_apps

# Importiere Fixtures aus test_rls_functional.py, sodass
# ``TestSuperAdminRLSBypass`` unten die Fixtures wiederverwenden kann.
# pytest-Fixtures sind modul-lokal — ein einfacher Import ohne
# Re-Export wuerde nicht reichen, der Import macht sie hier sichtbar.
# Ruff-F811-Warnung an den Test-Method-Args ist ein bekanntes
# pytest-Pattern; mit Inline-Suppression an den Tests behandelt.
from tests.conftest import xdist_role_suffix
from tests.test_rls_functional import (  # noqa: F401
    facility_a_with_data,
    facility_b_with_data,
    rls_test_role,
)

# Refs #1096: ``EXPECTED_TABLES`` wird ableitungsbasiert statt hartkodiert —
# damit verschwindet die historische Drift-Quelle (0063/0091 waren beide
# nachtraegliche Korrekturen fuer von frueheren Migrationen uebersehene
# Tabellen). DIRECT-Tabellen leiten sich by-construction aus der Model-Registry
# ab; nur die transitiv (JOIN) gescopten Tabellen bleiben von Hand kuriert.

# Transitiv facility-gescopt (KEIN direkter facility-FK), RLS via JOIN-Policy in
# Migration 0047/0063 — aus Model-Metadaten nicht ableitbar, daher kuriert. Dies
# ist der einzige von Hand zu pflegende Eintrag bei einer neuen JOIN-Tabelle.
JOIN_SCOPED_TABLES = frozenset(
    {
        "core_eventhistory",
        # core_attachment: bis M24 (Refs #1738) join-gescopt via core_event;
        # seit Migration 0130/0131 direkter facility-FK → auto-erkannt.
        "core_episode",
        "core_outcomegoal",
        "core_milestone",
        "core_documenttypefield",
        # FieldGroup (M0, Refs #1056) hat keinen eigenen facility-FK — gescopt
        # transitiv ueber document_type.facility_id (Migration 0111).
        "core_fieldgroup",
        # FieldCondition (M1, Refs #1057) hat keinen eigenen facility-FK —
        # gescopt doppelt-transitiv ueber
        # target_field.document_type.facility_id (Migration 0114).
        "core_fieldcondition",
    }
)
# facility-FK, aber bewusst AUSSERHALB von RLS (Auth-Grenze: super_admin hat
# keine Facility, Auth geht dem Facility-Scoping voraus). Allowlist-Muster wie
# ``_OBJECTS_ALL_WHITELIST_PREFIXES`` in test_architecture_guards_models.py.
NOT_RLS_SCOPED = frozenset({"core_user"})


def _has_direct_facility_fk(model) -> bool:
    """True, wenn ``model`` eine direkte FK/O2O auf ``core_facility`` traegt."""
    try:
        field = model._meta.get_field("facility")
    except Exception:  # FieldDoesNotExist
        return False
    return isinstance(field, (ForeignKey, OneToOneField)) and field.related_model._meta.db_table == "core_facility"


def _direct_scoped_tables(model_registry=None) -> set[str]:
    """Alle Tabellen mit direktem facility-FK, ohne die Auth-Grenzen-Allowlist."""
    registered_models = apps.get_models() if model_registry is None else model_registry
    return {
        model._meta.db_table for model in registered_models if not model._meta.proxy and _has_direct_facility_fk(model)
    } - NOT_RLS_SCOPED


def _join_scoped_candidates(model_registry=None) -> dict[str, set[str]]:
    """Nicht-DIRECT-Tabellen mit FK/O2O auf mindestens eine DIRECT-Tabelle.

    ``model_registry`` ist absichtlich injizierbar: Der Gegenstands-Test nutzt
    eine isolierte Django-App-Registry und registriert keine Probe im echten
    Modellbaum. Im Guard selbst bleibt die globale App-Registry die SSOT.
    """
    registered_models = tuple(apps.get_models() if model_registry is None else model_registry)
    direct_tables = _direct_scoped_tables(registered_models)
    candidates: dict[str, set[str]] = {}
    for model in registered_models:
        table = model._meta.db_table
        if model._meta.proxy or table in direct_tables or table in NOT_RLS_SCOPED:
            continue
        referenced_direct_tables = {
            field.related_model._meta.db_table
            for field in model._meta.fields
            if isinstance(field, (ForeignKey, OneToOneField))
            and field.related_model is not None
            and field.related_model._meta.db_table in direct_tables
        }
        if referenced_direct_tables:
            candidates[table] = referenced_direct_tables
    return candidates


def _assert_join_scoped_tables_declared(
    *,
    model_registry=None,
    join_scoped_tables: frozenset[str] = JOIN_SCOPED_TABLES,
) -> None:
    candidates = _join_scoped_candidates(model_registry)
    missing = sorted(set(candidates) - join_scoped_tables)
    details = ", ".join(f"{table} (FK auf {', '.join(sorted(candidates[table]))})" for table in missing)
    assert not missing, (
        f"JOIN-gescopte Modelle ohne RLS-Deklaration: {details}. "
        "Neue RLS-Migration nach dem Muster src/core/migrations/0047_postgres_rls_setup.py anlegen "
        "und die Tabelle in JOIN_SCOPED_TABLES aufnehmen."
    )


# Reine Registry-Introspektion (kein DB-Zugriff) — importierbar ohne Postgres;
# ``_residue_expectations.py`` importiert ``EXPECTED_TABLES`` als SSOT. Gewollte
# Nebenwirkung: ein neues DIRECT-Model landet hier automatisch und triggert
# damit auch das Completeness-Gate des PII-Residue-Sweeps.
EXPECTED_TABLES = sorted(_direct_scoped_tables() | JOIN_SCOPED_TABLES)


@pytest.mark.architecture
class TestRLSJoinScopeArchitectureGuard:
    """Refs #2001: JOIN-Kandidaten duerfen nicht aus der RLS-SSOT fallen."""

    def test_all_models_referencing_direct_scope_are_declared(self):
        _assert_join_scoped_tables_declared()

    @isolate_apps()
    def test_fixture_model_without_join_declaration_is_reported(self):
        class FixtureFacility(models.Model):
            class Meta:
                app_label = "rls_guard_fixture"
                db_table = "core_facility"

        class FixtureDirect(models.Model):
            facility = models.ForeignKey(FixtureFacility, on_delete=models.CASCADE)

            class Meta:
                app_label = "rls_guard_fixture"
                db_table = "fixture_direct"

        class FixtureJoin(models.Model):
            parent = models.ForeignKey(FixtureDirect, on_delete=models.CASCADE)

            class Meta:
                app_label = "rls_guard_fixture"
                db_table = "fixture_join"

        with pytest.raises(AssertionError, match=r"fixture_join.*0047_postgres_rls_setup\.py"):
            _assert_join_scoped_tables_declared(
                model_registry=(FixtureFacility, FixtureDirect, FixtureJoin),
                join_scoped_tables=frozenset(),
            )


@pytest.mark.django_db
@pytest.mark.postgres
class TestRLSSetup:
    def test_rls_enabled_on_all_facility_scoped_tables(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname FROM pg_class WHERE relrowsecurity AND relname = ANY(%s)",
                [EXPECTED_TABLES],
            )
            enabled = {row[0] for row in cursor.fetchall()}
        missing = set(EXPECTED_TABLES) - enabled
        assert not missing, f"RLS missing on: {missing}"

    def test_force_rls_enabled_on_all_tables(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname FROM pg_class WHERE relforcerowsecurity AND relname = ANY(%s)",
                [EXPECTED_TABLES],
            )
            forced = {row[0] for row in cursor.fetchall()}
        missing = set(EXPECTED_TABLES) - forced
        assert not missing, f"FORCE RLS missing on: {missing}"

    def test_facility_isolation_policy_exists_on_all_tables(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_policies WHERE policyname = 'facility_isolation' AND tablename = ANY(%s)",
                [EXPECTED_TABLES],
            )
            covered = {row[0] for row in cursor.fetchall()}
        missing = set(EXPECTED_TABLES) - covered
        assert not missing, f"Policy 'facility_isolation' missing on: {missing}"

    def test_super_admin_bypass_branch_on_all_policies(self):
        """Refs #1016 A1.3: JEDE facility_isolation-Policy muss den
        ``app.is_super_admin``-Bypass-Branch in USING enthalten — sonst ist die
        Tabelle fuer den super_admin cross-facility unsichtbar (0085 + 0091)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename, qual FROM pg_policies "
                "WHERE policyname = 'facility_isolation' AND tablename = ANY(%s)",
                [EXPECTED_TABLES],
            )
            quals = {row[0]: row[1] or "" for row in cursor.fetchall()}
        missing_bypass = sorted(t for t in EXPECTED_TABLES if "is_super_admin" not in quals.get(t, ""))
        assert not missing_bypass, f"super_admin-Bypass fehlt in USING auf: {missing_bypass} (Refs #1016 A1.3)"

    def test_set_config_does_not_raise(self):
        """Smoke-Test for the set_config call used by FacilityScopeMiddleware."""
        import uuid

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_facility_id', %s, false)",
                [str(uuid.uuid4())],
            )
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            assert cursor.fetchone()[0]

    def test_middleware_set_config_persists_across_cursors(self, facility, staff_user):
        """Regression #586: set_config(..., is_local=false) darf nicht mit der
        Middleware-eigenen Statement-Transaktion ablaufen — sonst liefert die
        RLS-Policy NULL fuer alle nachfolgenden ORM-Queries dieses Requests.

        Simuliert durch: Middleware aufrufen, dann in separatem Cursor lesen.
        """
        from django.test import RequestFactory

        from core.middleware.facility_scope import FacilityScopeMiddleware

        rf = RequestFactory()
        request = rf.get("/")
        request.user = staff_user  # fixture: facility = facility

        FacilityScopeMiddleware(lambda r: r)(request)

        # Separater Cursor, separater Kontext — Variable muss ueberleben.
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            value = cursor.fetchone()[0]
        assert value == str(facility.pk)

    def test_set_config_round_trip_for_facility_spoof(self, facility, second_facility):
        """Functional-Check der Session-Variable als RLS-Defense-in-Depth.

        Im Test-Setup ist der DB-User Superuser → echtes RLS-Filter bypasst.
        Daher prüft dieser Test den Round-Trip: ``set_config`` auf Facility B's
        ID, gefolgt von ``current_setting(...)``, muss exakt Facility B's ID
        zurückgeben. Damit ist sichergestellt, dass die Middleware-Variable
        in Produktion (Non-Superuser) korrekt als Policy-Input greift.

        Refs #542 / #591 WP1.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_facility_id', %s, false)",
                [str(second_facility.pk)],
            )
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            value = cursor.fetchone()[0]
        assert value == str(second_facility.pk)
        # Stellt zusätzlich sicher, dass Facility A (andere UUID) nicht
        # versehentlich ausgegeben wird.
        assert value != str(facility.pk)

    def test_middleware_clears_setting_for_facility_less_user(self, staff_user):
        """Ein authentifizierter User ohne Facility (Edge-Case) muss die
        Variable auf leer zuruecksetzen — sonst koennte eine wiederverwendete
        Connection den Wert aus einer frueheren Request leaken (Refs #586).

        Anonyme Requests sind hier nicht erfasst: sie greifen nicht auf
        facility-scoped Tabellen zu und vermeiden deshalb bewusst den
        zusaetzlichen DB-Hit der Middleware.
        """
        from django.test import RequestFactory

        from core.middleware.facility_scope import FacilityScopeMiddleware

        # Erst setzen, um den Leak-Vektor zu simulieren.
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_facility_id', 'leak-id', false)")

        staff_user.facility = None
        staff_user.save(update_fields=["facility"])

        rf = RequestFactory()
        request = rf.get("/")
        request.user = staff_user
        FacilityScopeMiddleware(lambda r: r)(request)

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            assert cursor.fetchone()[0] == ""

    def test_auditlog_policy_has_with_check_for_null_facility(self):
        """Migration 0083: WITH CHECK erlaubt NULL-Facility-Inserts (Pre-Auth)."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT qual, with_check FROM pg_policies "
                "WHERE policyname='facility_isolation' AND tablename='core_auditlog'"
            )
            row = cursor.fetchone()
        assert row is not None, "Policy facility_isolation auf core_auditlog fehlt"
        qual, with_check = row
        # USING bleibt strikt — kein NULL-Match.
        assert "current_setting" in (qual or "").lower(), f"USING-Klausel fehlt set_config-Check: {qual}"
        # WITH CHECK muss NULL erlauben (Pre-Auth-Audits).
        assert with_check is not None, "WITH CHECK fehlt — Migration 0083 nicht angewendet?"
        assert "is null" in with_check.lower(), f"WITH CHECK erlaubt NULL nicht: {with_check}"
        assert "current_setting" in with_check.lower(), f"WITH CHECK fehlt scope-match: {with_check}"


@pytest.mark.django_db
@pytest.mark.postgres
class TestRLSCoverageGuard:
    """Refs #1096: Reverse-Guard zur ableitungsbasierten ``EXPECTED_TABLES``.

    Die vier ``TestRLSSetup``-Tests decken die Richtung ``EXPECTED ⊆ DB`` ab
    (jede erwartete Tabelle hat RLS/Policy/Bypass). Dieser Test schliesst die
    Gegenrichtung ``DB ⊆ EXPECTED``: Traegt die DB RLS auf einer
    ``core_``-Tabelle, die nicht (mehr) in ``EXPECTED_TABLES`` steht — etwa ein
    neues facility-Model, dessen JOIN-Tabelle in ``JOIN_SCOPED_TABLES`` fehlt,
    oder eine verwaiste RLS-Migration — wird der Test rot. Zusammen erzwingen
    beide Richtungen Gleichheit, sodass die Abdeckung vollstaendig-by-
    construction bleibt.
    """

    def test_expected_tables_equals_live_db_rls_set(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT relname FROM pg_class WHERE relrowsecurity AND relname LIKE %s",
                ["core_%"],
            )
            db_rls = {row[0] for row in cur.fetchall()}
        assert db_rls == set(EXPECTED_TABLES), (
            f"RLS-Drift — nur in DB: {db_rls - set(EXPECTED_TABLES)}; "
            f"nur in EXPECTED_TABLES: {set(EXPECTED_TABLES) - db_rls}. "
            "Neues facility-Model ohne RLS-Migration? Oder JOIN_SCOPED_TABLES veraltet?"
        )


@pytest.mark.django_db
@pytest.mark.postgres
class TestRLSFunctional:
    """Funktionale Regression-Guards für die FacilityScopeMiddleware.

    Echte RLS-Policies können im Test-Setup nicht greifen (DB-User ist
    Superuser und bypasst RLS). Diese Klasse testet daher das Verhalten der
    Session-Variable direkt — was die Policies in Produktion nutzen.
    """

    def test_anonymous_request_does_not_set_facility_variable(self):
        """Regression ffb5666: Ein anonymer Request darf keinen DB-Cursor
        öffnen, um die Facility-Variable zu setzen. Andernfalls würden
        Anonymous-Routes (Login, Health, Static) unnötige SET-Statements
        absetzen, und stehengebliebene Werte aus früheren Requests würden
        versehentlich überschrieben oder geleakt (Refs #591, WP1).
        """
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from core.middleware.facility_scope import FacilityScopeMiddleware

        # Stelle sicher, dass zu Beginn kein Wert gesetzt ist.
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_facility_id', '', false)")

        rf = RequestFactory()
        request = rf.get("/login/")
        request.user = AnonymousUser()
        FacilityScopeMiddleware(lambda r: r)(request)

        # Nach dem Middleware-Lauf muss die Variable weiterhin leer sein —
        # also weder auf eine Facility-ID noch auf einen sonstigen Wert
        # gesetzt.
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            value = cursor.fetchone()[0]
        assert value == "", "Anonymous Request darf die facility-Variable nicht setzen."

    def test_anonymous_request_clears_stale_facility_id(self):
        """Defense-in-Depth (Refs #733): Ein anonymer
        Request muss eine aus einer fruehren authentifizierten Request
        stehengebliebene ``app.current_facility_id`` explizit auf '' leeren,
        damit Connection-Pooling den Wert nicht in eine RLS-Anfrage
        eines anderen Tenants leakt.

        Pruefung: Wir setzen vor dem Request eine Marker-UUID als
        ``app.current_facility_id`` (simuliert Connection-Reuse aus einer
        fruehren Request). Nach dem Middleware-Lauf muss der Wert leer
        sein.
        """
        import uuid

        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from core.middleware.facility_scope import FacilityScopeMiddleware

        marker = str(uuid.uuid4())
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_facility_id', %s, false)", [marker])

        rf = RequestFactory()
        request = rf.get("/health/")
        request.user = AnonymousUser()
        FacilityScopeMiddleware(lambda r: r)(request)

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            value = cursor.fetchone()[0]
        assert value == "", (
            f"Anonyme Request muss app.current_facility_id leeren — sonst Connection-Pool-Leak. "
            f"Erwartet: '', erhalten: {value!r}"
        )


# ---------------------------------------------------------------------------
# Super-Admin RLS-Bypass (Refs #867)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestAppendOnlyTriggerOwnership:
    """Refs #1026 (A1.4): Nur der Table-Owner darf die Append-Only-Trigger per
    ``ALTER TABLE ... DISABLE TRIGGER`` abschalten. Die Runtime-App-Rolle ist in
    Prod NOSUPERUSER/NOBYPASSRLS und NICHT Owner (Runtime-/Owner-Trennung,
    ``01-app-role.sh``) — sie kann den Audit-/EventHistory-Immutability-Schutz
    daher nicht aushebeln. Fruehere Umgehungsversuche liefen ueber
    ``session_replication_role = replica``; dieser Vektor ist mit ``ENABLE
    ALWAYS`` + Owner-DDL abgeloest (siehe ``services/system/_db_admin.py``).

    Dieser Test simuliert die Prod-Runtime-Rolle ueber ``SET ROLE`` auf eine
    frisch angelegte NOSUPERUSER/NOBYPASSRLS-Nicht-Owner-Rolle: DML ist erlaubt,
    ``DISABLE TRIGGER`` scheitert mit ``must be owner``.

    ``transaction=True``: CREATE/DROP ROLE + SET ROLE brauchen echte Sessions.
    """

    # Refs #2065 (ADR-050 D5.5): dieselbe Ordnung wie in ``_APPEND_ONLY_TRIGGERS``
    # — ``core_eventhistory`` vor ``core_auditlog``. Die Kopie hier ist bewusst
    # eine Kopie (der Test soll nicht dieselbe Konstante pruefen, die er
    # absichert); ihre Reihenfolge wird von
    # ``test_append_only_trigger_reihenfolge_ist_eventhistory_vor_auditlog``
    # gegen das Original gepinnt.
    _APPEND_ONLY_TRIGGERS = (
        ("core_eventhistory", "eventhistory_no_update"),
        ("core_eventhistory", "eventhistory_no_delete"),
        ("core_auditlog", "auditlog_immutable"),
    )

    # Refs #1479 (TST-03): Worker-Suffix — Postgres-Rollen sind cluster-global,
    # ohne Suffix kollidieren zwei xdist-Worker beim CREATE/DROP.
    @property
    def _runtime_role(self):
        return f"a1_4_runtime{xdist_role_suffix()}"

    def _drop_role(self):
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")
            # DROP OWNED widerruft auch die an die Rolle erteilten GRANTs auf
            # fremd-owned Tabellen, sonst blockt eine Abhaengigkeit den DROP.
            cur.execute(
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{self._runtime_role}') THEN "
                f"  EXECUTE format('DROP OWNED BY %I', '{self._runtime_role}'); "
                f"  EXECUTE format('DROP ROLE %I', '{self._runtime_role}'); "
                "END IF; END $$;"
            )

    def test_non_owner_runtime_role_cannot_disable_append_only_triggers(self):
        from django.db import Error as DjangoDBError
        from django.db import transaction

        role = self._runtime_role
        self._drop_role()
        try:
            with connection.cursor() as cur:
                cur.execute(f"CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS")
                # Prod-analog: die App-Rolle hat DML, aber KEINE Ownership.
                cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON core_auditlog TO {role}")
                cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON core_eventhistory TO {role}")

            for table, trigger in self._APPEND_ONLY_TRIGGERS:
                # atomic() kapselt den Transaktions-Abort (und rollt SET ROLE
                # zurueck), damit die Connection danach weiterverwendbar bleibt.
                with pytest.raises(DjangoDBError) as exc, transaction.atomic():
                    with connection.cursor() as cur:
                        cur.execute(f"SET ROLE {role}")
                        cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
                assert "must be owner" in str(exc.value).lower(), (
                    f"Nicht-Owner-Rolle konnte {trigger} auf {table} abschalten — A1.4-Regression!"
                )
                with connection.cursor() as cur:
                    cur.execute("RESET ROLE")
        finally:
            self._drop_role()

    def test_append_only_triggers_are_enable_always_at_rest(self):
        """Refs #2065 (ADR-050 D5.4): der Ruhezustand ist ``'A'`` = ENABLE ALWAYS.

        Ergaenzung zum Ownership-Test oben: der prueft, dass eine fremde Rolle
        die Trigger nicht abschalten KANN — dieser, dass die legitimen
        Wartungspfade sie exakt so zuruecklassen, wie sie sie vorgefunden
        haben. Seit dem naechtlichen EventHistory-Sweep laeuft
        ``bypass_replication_triggers`` planmaessig und nicht mehr nur bei
        seltenen Verwaltungsvorgaengen; ein einziger Pfad, der statt des
        gelesenen ``tgenabled`` ein pauschales ``ENABLE TRIGGER`` schriebe,
        degradierte den seit Migration 0107 gehaerteten Zustand still auf
        Origin und oeffnete den ``replica``-Vektor aus Refs #1026 wieder — ohne
        dass irgendetwas fehlschluege.
        """
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")
            for table, trigger in self._APPEND_ONLY_TRIGGERS:
                cur.execute(
                    "SELECT tgenabled FROM pg_trigger WHERE tgname = %s AND tgrelid = %s::regclass",
                    [trigger, table],
                )
                row = cur.fetchone()
                assert row is not None, f"{trigger} auf {table} fehlt"
                assert row[0] == "A", (
                    f"{trigger} auf {table} steht auf tgenabled={row[0]!r} statt 'A' — "
                    "ENABLE ALWAYS wurde still auf Origin degradiert (Refs #1026/#2065)."
                )


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestSlugImmutableTriggerOwnership:
    """Refs #1709, #1058: analog ``TestAppendOnlyTriggerOwnership`` — die Slug-
    Immutable-Trigger (Migration 0116 FieldGroup/FieldTemplate, Migration 0117
    DocumentType) sind bewusst NICHT in ``_APPEND_ONLY_TRIGGERS``/
    ``bypass_replication_triggers`` aufgenommen (die Slug-Immutabilitaet soll
    auch fuer Wartungspfade gelten). Damit bleibt ``ENABLE ALWAYS`` +
    Owner-DDL die EINZIGE Verteidigungslinie gegen ein Abschalten der
    Trigger — dieser Test pinnt, dass die Runtime-App-Rolle
    (NOSUPERUSER/NOBYPASSRLS, kein Table-Owner) sie nicht deaktivieren kann.
    """

    _SLUG_IMMUTABLE_TRIGGERS = (
        ("core_fieldgroup", "fieldgroup_slug_immutable"),
        ("core_fieldtemplate", "fieldtemplate_slug_immutable"),
        ("core_documenttype", "documenttype_slug_immutable"),
    )

    @property
    def _runtime_role(self):
        return f"m2_slug_runtime{xdist_role_suffix()}"

    def _drop_role(self):
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")
            cur.execute(
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{self._runtime_role}') THEN "
                f"  EXECUTE format('DROP OWNED BY %I', '{self._runtime_role}'); "
                f"  EXECUTE format('DROP ROLE %I', '{self._runtime_role}'); "
                "END IF; END $$;"
            )

    def test_non_owner_runtime_role_cannot_disable_slug_immutable_triggers(self):
        from django.db import Error as DjangoDBError
        from django.db import transaction

        role = self._runtime_role
        self._drop_role()
        try:
            with connection.cursor() as cur:
                cur.execute(f"CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS")
                cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON core_fieldgroup TO {role}")
                cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON core_fieldtemplate TO {role}")
                cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON core_documenttype TO {role}")

            for table, trigger in self._SLUG_IMMUTABLE_TRIGGERS:
                with pytest.raises(DjangoDBError) as exc, transaction.atomic():
                    with connection.cursor() as cur:
                        cur.execute(f"SET ROLE {role}")
                        cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
                assert "must be owner" in str(exc.value).lower(), (
                    f"Nicht-Owner-Rolle konnte {trigger} auf {table} abschalten — Slug-Immutabilitaet umgehbar!"
                )
                with connection.cursor() as cur:
                    cur.execute("RESET ROLE")
        finally:
            self._drop_role()


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestSuperAdminRLSBypass:
    """Refs #867: ``app.is_super_admin='true'`` bypasst die facility_isolation-
    Policy ueber alle facility-gescopten Tabellen.

    Tests laufen unter der Non-Superuser-Rolle ``rls_test_role`` (siehe
    ``test_rls_functional.py``). Der DB-Test-User ist ansonsten Superuser
    und bypasst RLS per Postgres-Default — dann waeren diese Tests
    aussagelos.

    ``transaction=True`` ist Pflicht: ``SET ROLE`` muss session-weit aktiv
    bleiben, damit die Policies tatsaechlich greifen.
    """

    def test_super_admin_session_var_bypasses_rls_policy(
        self,
        rls_test_role,  # noqa: F811
        facility_a_with_data,  # noqa: F811
        facility_b_with_data,  # noqa: F811
    ):
        """Mit ``app.is_super_admin='true'`` (und leerer
        current_facility_id) gibt die Policy *alle* core_client-Zeilen
        beider Facilities frei — der RLS-Bypass-OR-Branch greift.
        """
        from tests.test_rls_functional import as_rls_role

        with as_rls_role(rls_test_role, facility_id="") as cur:
            # Super-Admin-Bypass aktivieren (vor SET ROLE eine zweite
            # set_config-Sitzungs-Var; das Yield des Context-Managers
            # ist bereits in der gewuenschten Rolle).
            cur.execute("SELECT set_config('app.is_super_admin', 'true', false)")
            cur.execute("SELECT pseudonym FROM core_client ORDER BY pseudonym")
            rows = [r[0] for r in cur.fetchall()]
            # Aufraeumen, sonst leakt der Bypass in nachfolgende Tests
            # innerhalb derselben Session.
            cur.execute("SELECT set_config('app.is_super_admin', '', false)")

        assert "A-Client" in rows, "Super-Admin sieht eigene Facility nicht — Bypass kaputt."
        assert "B-Client" in rows, (
            f"Super-Admin sieht zweite Facility nicht — RLS-Bypass-OR-Branch greift nicht. Rows: {rows}"
        )

    def test_facility_admin_only_sees_own_facility(
        self,
        rls_test_role,  # noqa: F811
        facility_a_with_data,  # noqa: F811
        facility_b_with_data,  # noqa: F811
    ):
        """Komplement zum Super-Admin-Test: ``app.is_super_admin=''`` und
        ``current_facility_id=<facility_a>`` -> nur A-Client sichtbar.

        Stellt sicher, dass die Bypass-Erweiterung aus 0085 die
        facility-spezifische Isolation NICHT aufweicht, wenn der Bypass
        nicht aktiv ist.
        """
        from tests.test_rls_functional import as_rls_role

        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute("SELECT set_config('app.is_super_admin', '', false)")
            cur.execute("SELECT pseudonym FROM core_client ORDER BY pseudonym")
            rows = [r[0] for r in cur.fetchall()]

        assert "A-Client" in rows, "Facility-Admin sieht eigene Facility nicht."
        assert "B-Client" not in rows, f"Facility-Admin sieht fremde Facility — RLS-Isolation versagt. Rows: {rows}"

    def test_null_facility_audit_visible_only_to_super_admin(
        self,
        rls_test_role,  # noqa: F811
        facility,
        admin_user,
    ):
        """Refs #863 + #867: AuditLog mit ``facility=NULL`` (z.B. Pre-Auth-
        LOGIN_FAILED) ist nur fuer super_admin sichtbar.

        - Super-Admin (Bypass-Branch ``is_super_admin='true'``): liefert
          den NULL-Eintrag.
        - Facility-Admin (USING-Branch ``facility_id::text =
          current_setting('app.current_facility_id', true)``): NULL =/=
          irgend-eine Facility, also 0 Rows.

        Der INSERT mit NULL-Facility ist unkritisch im Test-Setup
        (DB-User=Superuser bypasst alle Policies); die Aussage des
        Tests liegt im SELECT unter Non-Superuser-Rolle.
        """
        import uuid

        from tests.test_rls_functional import as_rls_role

        # Vor dem Test einen NULL-Facility-Audit anlegen. Wir nutzen Raw-SQL,
        # weil das Manager-API nicht-Null erwartet/setzt und der Test direkt
        # die Datenbank-Schicht prueft.
        marker_target_id = "rls-null-audit-" + uuid.uuid4().hex[:8]
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO core_auditlog (id, facility_id, user_id, action, "
                "target_type, target_id, detail, ip_address, timestamp) "
                "VALUES (%s, NULL, %s, %s, '', %s, '{}', NULL, NOW())",
                [uuid.uuid4(), admin_user.pk, "login_failed", marker_target_id],
            )

        # Probe 1: super_admin -> NULL-Audit ist sichtbar.
        with as_rls_role(rls_test_role, facility_id="") as cur:
            cur.execute("SELECT set_config('app.is_super_admin', 'true', false)")
            cur.execute(
                "SELECT COUNT(*) FROM core_auditlog WHERE target_id = %s",
                [marker_target_id],
            )
            count_super = cur.fetchone()[0]
            cur.execute("SELECT set_config('app.is_super_admin', '', false)")

        assert count_super == 1, (
            f"Super-Admin sieht NULL-Facility-AuditLog nicht. Erwartet: 1, erhalten: {count_super}. "
            "Pruefe Migration 0085 (is_super_admin-Branch in USING)."
        )

        # Probe 2: facility-admin (irgendeine Facility, kein Bypass) -> NULL-
        # Audit ist NICHT sichtbar.
        with as_rls_role(rls_test_role, facility_id=facility.pk) as cur:
            cur.execute("SELECT set_config('app.is_super_admin', '', false)")
            cur.execute(
                "SELECT COUNT(*) FROM core_auditlog WHERE target_id = %s",
                [marker_target_id],
            )
            count_fac = cur.fetchone()[0]

        assert count_fac == 0, (
            f"Facility-Admin sieht NULL-Facility-AuditLog. Erwartet: 0, erhalten: {count_fac}. "
            "USING-Klausel muesste NULL-facility ausschliessen, da NULL-Vergleich != current_setting."
        )

    def test_session_var_pool_leak_protection(self, super_admin_user):
        """Refs #867: nach einem super_admin-Request darf
        ``app.is_super_admin`` in der naechsten Anonymous-Request explizit
        auf '' geleert sein — Connection-Pool-Reuse darf den Bypass nicht
        an einen anderen User vererben.

        Strategie:
        1. Echter super_admin-Request mit ``is_super_admin``-Set.
        2. Direkt anschliessend Anonymous-Request — Middleware muss
           ``app.is_super_admin`` auf '' resetten.
        3. Der gleiche Test prueft auch, dass nach einem zweiten
           non-super_admin-Login die Variable wieder leer ist.
        """
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from core.middleware.facility_scope import FacilityScopeMiddleware

        rf = RequestFactory()

        # 1. Super-Admin-Request -> Middleware setzt is_super_admin='true'
        request_super = rf.get("/system/")
        request_super.user = super_admin_user
        FacilityScopeMiddleware(lambda r: r)(request_super)

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.is_super_admin', true)")
            value_after_super = cursor.fetchone()[0]
        assert value_after_super == "true", (
            f"Super-Admin-Middleware-Set hat nicht gewirkt. Erwartet 'true', erhalten {value_after_super!r}."
        )

        # 2. Anonymer Request danach -> Middleware muss explicit zuruecksetzen.
        request_anon = rf.get("/login/")
        request_anon.user = AnonymousUser()
        FacilityScopeMiddleware(lambda r: r)(request_anon)

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.is_super_admin', true)")
            value_after_anon = cursor.fetchone()[0]
        assert value_after_anon == "", (
            f"Anonymer Request hat ``app.is_super_admin`` nicht geleert. "
            f"Erwartet '', erhalten {value_after_anon!r}. "
            "Connection-Pool-Reuse koennte den Bypass an einen anderen Tenant vererben."
        )


# ---------------------------------------------------------------------------
# DeletionRequest cross-tenant isolation (Refs Matrix DEV-SEC-RLS-07)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestDeletionRequestRLSIsolation:
    """Refs Matrix DEV-SEC-RLS-07: ``core_deletionrequest`` ist
    facility-gescopt (siehe ``EXPECTED_TABLES`` oben + Migration
    ``0047_postgres_rls_setup.py``). Loeschantraege duerfen Tenant-Grenzen
    nicht ueberschreiten — sonst koennte Facility B sehen, dass in
    Facility A ein Vier-Augen-Loeschantrag laeuft (Metadaten-Leak ueber
    Pseudonyme + ``target_id``).

    Im Unterschied zu den ``TestRLSSetup``-Tests (Policy-Existenz-Checks)
    laeuft dieser Test als NOSUPERUSER-Rolle ``rls_test_role``, sodass
    die ``facility_isolation``-Policy tatsaechlich greift. ``transaction
    =True`` ist Pflicht — siehe Klassendoc in ``TestRLSCrossTenantIsolation``.
    """

    def test_deletion_request_invisible_across_facility(
        self,
        rls_test_role,  # noqa: F811
        facility,
        second_facility,
        admin_user,
        second_facility_user,
    ):
        """Refs Matrix DEV-SEC-RLS-07: Je ein DeletionRequest in
        Facility A und Facility B; unter ``app.current_facility_id=A``
        ist nur A's Antrag sichtbar, unter B nur B's.
        """
        import uuid

        from core.models import Client
        from core.models.workitem import DeletionRequest
        from tests.test_rls_functional import as_rls_role

        # Je 1 Client + 1 DeletionRequest pro Facility. Client.create
        # benoetigen wir, um eine realistische ``target_id`` einzutragen
        # — die UUID ist im Test sonst beliebig.
        client_a = Client.objects.create(
            facility=facility,
            pseudonym="A-Client-Del",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=admin_user,
        )
        client_b = Client.objects.create(
            facility=second_facility,
            pseudonym="B-Client-Del",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=second_facility_user,
        )

        dr_a = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_a.pk,
            requested_by=admin_user,
            reason="A-Loeschantrag (Test)",
        )
        dr_b = DeletionRequest.objects.create(
            facility=second_facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_b.pk,
            requested_by=second_facility_user,
            reason="B-Loeschantrag (Test)",
        )

        # ---- Probe 1: Facility A sieht NUR A's DeletionRequest. ----
        with as_rls_role(rls_test_role, facility_id=facility.pk) as cur:
            cur.execute("SELECT id::text FROM core_deletionrequest ORDER BY created_at")
            ids_a = {uuid.UUID(row[0]) for row in cur.fetchall()}

        assert dr_a.pk in ids_a, (
            "Facility A sieht den eigenen DeletionRequest nicht — Policy "
            "ist zu strikt oder Daten fehlen (false negative)."
        )
        assert dr_b.pk not in ids_a, (
            f"Cross-Tenant-Leak: Facility A sieht DeletionRequest aus "
            f"Facility B. Sichtbare IDs: {ids_a}. Pruefe Migration "
            "0047 (core_deletionrequest in DIRECT_TABLES) und FORCE RLS."
        )

        # ---- Probe 2: Facility B sieht NUR B's DeletionRequest. ----
        with as_rls_role(rls_test_role, facility_id=second_facility.pk) as cur:
            cur.execute("SELECT id::text FROM core_deletionrequest ORDER BY created_at")
            ids_b = {uuid.UUID(row[0]) for row in cur.fetchall()}

        assert dr_b.pk in ids_b, "Facility B sieht den eigenen DeletionRequest nicht — false negative im Test-Setup."
        assert dr_a.pk not in ids_b, (
            f"Cross-Tenant-Leak: Facility B sieht DeletionRequest aus Facility A. Sichtbare IDs: {ids_b}."
        )


@pytest.mark.architecture
class TestSecurityDefinerInventory:
    """Refs #2085: im Repo gibt es KEINE ``SECURITY DEFINER``-Funktion.

    ADR-033 § „Alternatives considered" und ADR-050 M-8 lehnen sie beide ab.
    Fuer den Art.-17-Sofortpfad wurde die Ausnahme in #2085 gebaut, gegen
    echtes PostgreSQL missbraucht und daraufhin **verworfen**. Der Preis war
    hoeher als gedacht, und beide Wege liessen sich aus dem Funktionsrumpf
    heraus prinzipiell nicht schliessen:

    1. **Die Facility-Pruefung ist vom Grantee selbst faelschbar.** Der Rumpf
       las ``app.current_facility_id``; genau die Rolle, die ``EXECUTE``
       bekam, kann diese Custom-GUC per ``set_config(..., false)`` beliebig
       setzen. Damit war die Historie **jeder** Einrichtung tilgbar. Im
       Definer-Kontext gilt zudem das ``rolbypassrls`` des Owners — RLS haette
       den Fehlgriff nicht abgefangen.
    2. **Aktive Legal Holds wurden umgangen.** ``anonymize_client_now``
       verbietet die Anonymisierung bei Hold ausdruecklich als Spoliation; die
       DB-Primitive kannte diesen Schutz nicht und tilgte Beweisinhalte trotz
       Sperre.

    Entscheidend ist nicht, dass eine GUC faelschbar ist (RLS nutzt dieselbe),
    sondern dass die App-Rolle die **append-only**-Historie ueberhaupt
    zerstoeren konnte — das verhindert die Ownership-Trennung sonst
    vollstaendig. Der Sofortpfad laeuft deshalb nachgelagert ueber den
    owner-seitigen Sweep (ADR-050), und die App-Rolle bekommt **kein** neues
    Recht.

    Dateizugriff bewusst IN der Testmethode (nicht auf Modulebene): ein
    Import-Zeit-Lesen risse ``test_architecture_guards_mutation_isolation``.
    """

    def test_keine_security_definer_funktion_in_den_migrationen(self):
        from pathlib import Path

        treffer = sorted(
            path.name
            for path in Path("src/core/migrations").glob("*.py")
            if "SECURITY DEFINER" in path.read_text(encoding="utf-8")
        )
        assert treffer == [], (
            "Eine SECURITY-DEFINER-Funktion umgeht die Runtime-/Owner-Trennung aus "
            f"ADR-033. Gefunden: {treffer}. In #2085 wurde genau das gebaut und nach "
            "zwei reproduzierten Missbrauchswegen (faelschbare Facility-GUC, "
            "umgangener Legal Hold) wieder verworfen — ein neuer Anlauf braucht "
            "einen eigenen ADR-Entscheid, keinen stillen Commit."
        )

    def test_die_app_rolle_bekommt_kein_execute_recht_im_init_skript(self):
        """Gegenstueck zum Guard oben: ohne Definer-Funktion darf auch die
        Sammelzuweisung ``GRANT EXECUTE ON FUNCTIONS`` nicht wieder auftauchen.
        Sie war der Weg, auf dem eine spaetere zweite Definer-Funktion
        automatisch fuer die Runtime aufrufbar geworden waere."""
        from pathlib import Path

        skript = Path("deploy/postgres-init/01-app-role.sh").read_text(encoding="utf-8")
        assert "ON FUNCTIONS" not in skript, (
            "01-app-role.sh grantet der App-Rolle Ausfuehrungsrechte auf Funktionen. "
            "Das ist seit #2085 ausdruecklich nicht mehr vorgesehen."
        )
