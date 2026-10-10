"""Vertikale AuthZ-Matrix + Vollständigkeits-Gate (Refs #1055).

Soll-Quelle: src/tests/_authz_expectations.py — jeder benannte URL-Pattern
MUSS dort deklariert sein. Das Gate failt in beide Richtungen (Pattern ohne
Eintrag / Eintrag ohne Pattern), damit neue Endpoints eine bewusste
AuthZ-Deklaration erzwingen.
"""

import uuid

import pytest
from django.conf import settings
from django.db import DatabaseError, connection
from django.urls import get_resolver, reverse
from django.urls.resolvers import URLPattern, URLResolver

from tests._authz_expectations import EXPECTATIONS, ROLES
from tests._rbac_helpers import resolve_fixture_kwargs
from tests.e2e import _authz_audit_helpers as helpers

# Django-Admin-Site (obfuskierter Pfad, eigene Permission-Maschinerie,
# abgedeckt durch test_admin_site_permissions.py) und Debug-Toolbar sind
# bewusst nicht Teil der Matrix.
EXCLUDED_NAMESPACES = {"admin", "djdt"}
# Django-i18n-Include (URLResolver auf i18n/); der eigene set_language-Wrapper
# auf i18n/setlang/ ist ein benannter URLPattern und separat deklariert.
EXCLUDED_ROUTE_PREFIXES = ("i18n/",)
# Permanente Redirects ohne eigene AuthZ-Logik (RedirectView, Refs core/urls.py).
UNNAMED_ALLOWLIST = {"aktivitaetslog/", "timeline/"}

# Frueher stand hier ``LIVE_AUDIT_UNHARVESTED`` — eine benannte Allowlist mit
# den zehn Konfigurations-Fixture-Namen (Dokumentationstyp, Feldvorlage,
# Feldgruppe, Feldbedingung + foreign-Pendants), deren Endpoints der Live-Audit
# (``AUTHZ_AUDIT=1``) nie probte. Mit Refs #1783 sind sie erntbar
# (``_authz_audit_helpers``: ``_CONFIG_DOC_TYPE`` + die drei Kind-Queries,
# ``_ENSURE_CONFIG_CONDITION``), und die Allowlist ist ersatzlos entfallen:
# ``test_every_fixture_reference_is_live_harvestable`` kennt damit KEINE
# Ausnahme mehr. Ein neuer Fixture-Name in der Erwartungs-Tabelle braucht
# seither zwingend einen HARVEST_SQL- oder FOREIGN_ALIASES-Eintrag.


def _referenced_fixture_names():
    """Fixture-Namen aus ``url_kwargs``/``idor`` aller Erwartungen.

    Disambiguierung wie in ``_authz_expectations`` dokumentiert: ein Wert MIT
    Punkt ist ein Fixture-Attributpfad, ein Wert ohne Punkt ein URL-Literal.
    """
    return {
        ref.split(".", 1)[0]
        for exp in EXPECTATIONS
        for pairs in (exp.url_kwargs, exp.idor)
        for _kwarg, ref in pairs
        if "." in ref
    }


def _collect_patterns():
    """Alle URL-Patterns: benannte (Set + Liste, namespace-qualifiziert) + unbenannte Routen."""
    named, unnamed, named_list = set(), set(), []

    def walk(resolver, namespace, prefix):
        for p in resolver.url_patterns:
            route = prefix + str(p.pattern)
            if isinstance(p, URLPattern):
                if p.name:
                    qualified = f"{namespace}:{p.name}" if namespace else p.name
                    named.add(qualified)
                    named_list.append(qualified)
                else:
                    unnamed.add(route)
            elif isinstance(p, URLResolver):
                ns = p.namespace
                if ns in EXCLUDED_NAMESPACES:
                    continue
                if any(route.startswith(x) for x in EXCLUDED_ROUTE_PREFIXES):
                    continue
                child_ns = f"{namespace}:{ns}" if namespace and ns else (ns or namespace)
                walk(p, child_ns, route)

    walk(get_resolver(), None, "")
    return named, unnamed, named_list


class TestCompletenessGate:
    def test_every_named_pattern_is_declared(self):
        named, _, _ = _collect_patterns()
        declared = {e.url_name for e in EXPECTATIONS}
        undeclared = named - declared
        assert not undeclared, f"URL-Patterns ohne AuthZ-Deklaration in _authz_expectations.py: {sorted(undeclared)}"

    def test_every_declaration_has_a_pattern(self):
        named, _, _ = _collect_patterns()
        declared = {e.url_name for e in EXPECTATIONS}
        stale = declared - named
        assert not stale, f"Deklarationen ohne URL-Pattern (Drift): {sorted(stale)}"

    def test_unnamed_patterns_are_allowlisted(self):
        _, unnamed, _ = _collect_patterns()
        unexpected = unnamed - UNNAMED_ALLOWLIST
        assert not unexpected, f"Unbenannte URL-Patterns außerhalb der Allowlist: {sorted(unexpected)}"

    def test_no_duplicate_declarations(self):
        names = [e.url_name for e in EXPECTATIONS]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"Doppelte Deklarationen: {sorted(dupes)}"

    def test_gate_detects_missing_declaration(self):
        """Selbsttest: Ein fehlender Eintrag MUSS auffallen (Schutz vor Gate-Bugs)."""
        named, _, _ = _collect_patterns()
        declared = {e.url_name for e in EXPECTATIONS} - {"core:client_detail"}
        assert "core:client_detail" in named - declared, "Gate hat den künstlich entfernten Eintrag nicht bemerkt"

    def test_no_duplicate_url_names_in_urlconf(self):
        """Doppelte URL-Namen kollabieren im Set — reverse() träfe nur eines."""
        _, _, named_list = _collect_patterns()
        dupes = {n for n in named_list if named_list.count(n) > 1}
        assert not dupes, f"Doppelte URL-Namen im URLconf: {sorted(dupes)}"

    def test_every_pk_endpoint_has_idor_probe_or_exemption(self):
        """Jeder Endpoint mit URL-Kwargs braucht eine IDOR-Probe oder begründete Ausnahme."""
        missing = [e.url_name for e in EXPECTATIONS if e.url_kwargs and not e.idor and not e.idor_exempt]
        assert not missing, f"pk-Endpoints ohne IDOR-Probe und ohne begründete Ausnahme: {sorted(missing)}"

    def test_idor_declarations_are_consistent(self):
        """idor und idor_exempt schließen sich aus; idor setzt url_kwargs voraus."""
        both = [e.url_name for e in EXPECTATIONS if e.idor and e.idor_exempt]
        assert not both, f"Einträge mit idor UND idor_exempt (widersprüchlich): {sorted(both)}"
        no_kwargs = [e.url_name for e in EXPECTATIONS if e.idor and not e.url_kwargs]
        assert not no_kwargs, f"Einträge mit idor ohne url_kwargs: {sorted(no_kwargs)}"

    def test_every_fixture_reference_is_live_harvestable(self):
        """Kein Fixture-Name der Erwartungs-Tabelle darf im Live-Audit ins Leere zeigen.

        Refs #1738 (Integrations-Review): ``client_attachment_download`` und
        ``case_attachment_download`` kamen mit vier neuen Fixture-Namen, fuer die
        es in ``tests/e2e/_authz_audit_helpers.py`` weder eine ``HARVEST_SQL``-
        Query noch einen ``FOREIGN_ALIASES``-Eintrag gab. ``_path_for`` liefert
        fuer einen unbekannten Namen ``None``; der Live-Audit schreibt daraufhin
        still eine SETUP-ERROR-Zelle. Die bleibt unter der 10%-Schwelle des
        Struktur-Asserts — die Route war damit **ungeprueft**, ohne dass
        irgendetwas rot wurde.

        Dieser Guard schliesst die Blindstellen-Klasse: jeder Fixture-Name aus
        ``url_kwargs``/``idor`` (Literale ohne Punkt sind keine Fixture-Refs)
        muss entweder direkt geerntet werden oder ueber einen Foreign-Alias
        aufloesbar sein.

        Refs #1783: **ohne Ausnahmeliste.** Die frueher hier abgezogene
        Bestands-Allowlist (zehn Konfigurations-Namen) ist geschlossen; ein
        neuer Name kann sich seither nicht mehr an diesem Guard vorbeimelden.
        """
        referenced = _referenced_fixture_names()
        resolvable = set(helpers.HARVEST_SQL) | set(helpers.FOREIGN_ALIASES)
        missing = referenced - resolvable
        assert not missing, (
            "Fixture-Namen ohne HARVEST_SQL/FOREIGN_ALIASES-Eintrag — der Live-Audit "
            f"wuerde diese Endpoints still als SETUP-ERROR ueberspringen: {sorted(missing)}"
        )

    def test_no_stale_harvest_entries(self):
        """Gegenrichtung: geerntet wird nur, was die Erwartungs-Tabelle nutzt.

        Refs #1783 — an die Stelle der abgeschafften Allowlist tritt der
        Drift-Guard in die andere Richtung: eine ``HARVEST_SQL``-Query ohne
        Abnehmer laeuft in jedem Audit-Lauf mit und verrottet still, wenn ihr
        Endpoint aus der Tabelle faellt.
        """
        referenced = _referenced_fixture_names()
        harvested = set(helpers.HARVEST_SQL) | set(helpers.FOREIGN_ALIASES)
        stale = harvested - referenced
        assert not stale, f"HARVEST_SQL/FOREIGN_ALIASES-Eintraege ohne Abnehmer in EXPECTATIONS: {sorted(stale)}"


@pytest.mark.django_db
class TestLiveAuditHarvestQueriesRunAgainstTheSchema:
    """Die Harvest-Queries laufen sonst NUR im manuellen Live-Audit (Refs #1783).

    Ein Tippfehler in einem Tabellen-/Spaltennamen oder eine Schema-Drift
    (umbenannte Spalte, entfallenes Feld) faellt dort erst Monate spaeter auf —
    und dann als **stille** SETUP-ERROR-Zelle unter der 10%-Schwelle des
    Struktur-Asserts, also genau in der Blindstellen-Klasse, die
    ``test_every_fixture_reference_is_live_harvestable`` schliessen soll. Hier
    laufen sie gegen das echte Testschema; das Ergebnis interessiert nicht, nur
    dass Postgres sie annimmt.

    Die Eltern-Parameter stehen bewusst auf ``None`` (kein Fixture-Setup): eine
    ``= NULL``-Vergleichsklausel ist syntaktisch vollwertig und liefert leer.
    """

    _PARAMS = {"fac": uuid.UUID(int=0), **dict.fromkeys(helpers._CHAINED_PARENTS)}

    def test_every_harvest_query_is_valid_sql(self):
        for name, sql in helpers.HARVEST_SQL.items():
            with connection.cursor() as cursor:
                try:
                    cursor.execute(sql, self._PARAMS)
                    cursor.fetchall()
                except DatabaseError as exc:  # pragma: no cover - Fehlerpfad
                    # Fail-fast: die Transaktion ist nach dem ersten Fehler
                    # ohnehin vergiftet, weitere Queries saehen nur Folgefehler.
                    pytest.fail(f"HARVEST_SQL[{name!r}] laeuft nicht gegen das Schema: {exc}")

    def test_ensure_config_condition_is_valid_sql(self):
        """Der einzige INSERT in ``prepare_audit_objects`` (Refs #1783)."""
        with connection.cursor() as cursor:
            cursor.execute(helpers._ENSURE_CONFIG_CONDITION, self._PARAMS)
            assert cursor.rowcount == 0, "Platzhalter-Facility duerfte keine Bedingung erzeugen"

class TestAuditReportPath:
    """Der Audit darf keinen versionierten Bericht überschreiben."""

    def test_default_is_dated_next_to_the_archived_audits(self, monkeypatch, tmp_path):
        import datetime

        monkeypatch.delenv(helpers.REPORT_PATH_ENV, raising=False)
        monkeypatch.setattr(helpers, "REPORT_DIR", tmp_path)

        path = helpers.report_path(datetime.date(2026, 10, 10))

        assert path == tmp_path / "2026-10-10-live-authz-matrix.md"

    def test_existing_report_is_never_reused(self, monkeypatch, tmp_path):
        import datetime

        monkeypatch.delenv(helpers.REPORT_PATH_ENV, raising=False)
        monkeypatch.setattr(helpers, "REPORT_DIR", tmp_path)
        day = datetime.date(2026, 10, 10)
        (tmp_path / "2026-10-10-live-authz-matrix.md").write_text("kept", encoding="utf-8")

        first = helpers.report_path(day)
        first.write_text("also kept", encoding="utf-8")
        second = helpers.report_path(day)

        assert first == tmp_path / "2026-10-10-live-authz-matrix-2.md"
        assert second == tmp_path / "2026-10-10-live-authz-matrix-3.md"

    def test_environment_variable_sets_the_target(self, monkeypatch, tmp_path):
        target = tmp_path / "custom.md"
        monkeypatch.setenv(helpers.REPORT_PATH_ENV, str(target))

        assert helpers.report_path() == target


# ---- Vertikale Matrix: Endpoint × Methode × Akteur (Test-Client) ----------

LOGIN_PATH = settings.LOGIN_URL
ACTORS = (*ROLES, "anonymous")

# Bekannte, als Issue dokumentierte Lücken: Zelle → Begründung mit Issue-Ref.
# Format: ("core:foo", "POST", "assistant"): "200 statt 403 — Issue #XXXX"
KNOWN_GAPS: dict[tuple[str, str, str], str] = {}


def test_known_gaps_reference_existing_cells():
    """Tippfehler in KNOWN_GAPS-Keys dürfen nicht lautlos ins Leere zeigen."""
    valid = {(exp.url_name, method, actor) for exp in EXPECTATIONS for method, _ in exp.methods for actor in ACTORS}
    stray = set(KNOWN_GAPS) - valid
    assert not stray, f"KNOWN_GAPS-Einträge ohne Matrix-Zelle: {sorted(stray)}"


def _cells():
    """Generiert je Tabellen-Zeile × Methode × Akteur einen Test-Parameter."""
    for exp in EXPECTATIONS:
        for method, allowed in exp.methods:
            for actor in ACTORS:
                marks = []
                if (exp.url_name, method, actor) in KNOWN_GAPS:
                    marks.append(pytest.mark.xfail(reason=KNOWN_GAPS[(exp.url_name, method, actor)], strict=True))
                yield pytest.param(
                    exp,
                    method,
                    allowed,
                    actor,
                    id=f"{exp.url_name}-{method}-{actor}",
                    marks=marks,
                )


@pytest.fixture
def matrix_users(db, facility):
    """Fünf Akteure analog Seed (Refs #867/#1053): confirm-Flag für admin+lead."""
    from core.models import User

    def make(username, role, fac, confirm=False):
        # Kein Passwort nötig: force_login umgeht die Authentifizierung;
        # create_user reicht can_confirm_deletion als Kwarg ans Model durch.
        return User.objects.create_user(
            username=username, role=role, facility=fac, is_staff=True, can_confirm_deletion=confirm
        )

    return {
        "facility_admin": make("mx_admin", User.Role.FACILITY_ADMIN, facility, confirm=True),
        "lead": make("mx_lead", User.Role.LEAD, facility, confirm=True),
        "staff": make("mx_staff", User.Role.STAFF, facility),
        "assistant": make("mx_assistant", User.Role.ASSISTANT, facility),
        "super_admin": make("mx_super", User.Role.SUPER_ADMIN, None),
    }


@pytest.mark.django_db
@pytest.mark.parametrize("exp,method,allowed,actor", list(_cells()))
def test_authz_cell(client, exp, method, allowed, actor, request):
    """Eine Matrix-Zelle: erlaubt = kein 403/404/Login-Redirect, verboten = 403/404."""
    kwargs = resolve_fixture_kwargs(exp.url_kwargs, request)
    url = reverse(exp.url_name, kwargs=kwargs or None)
    if actor != "anonymous":
        # Lazy: anonyme Zellen brauchen keine fünf User-Objekte.
        users = request.getfixturevalue("matrix_users")
        client.force_login(users[actor])

    response = getattr(client, method.lower())(url)
    status = response.status_code
    location = getattr(response, "url", "")

    if actor == "anonymous":
        if exp.anonymous_ok:
            assert status < 500, f"{url}: anonym (anonymous_ok) erwartet <500, bekam {status}"
        else:
            assert status == 302 and location.startswith(LOGIN_PATH), (
                f"{url}: anonym erwartet Login-Redirect, bekam {status} {location}"
            )
        return

    if actor in allowed:
        allowed_here = (status not in (403, 404)) or (status in exp.extra_ok)
        assert allowed_here, f"{url}: {actor} erwartet Zugriff, bekam {status}"
        assert status < 500, f"{url}: {actor} erhielt Serverfehler {status}"
        if status == 302:
            # Ein echter Auth-Bounce (redirect_to_login) trägt immer ?next=…;
            # ein nacktes /login/ ist z. B. der legitime Logout-Redirect
            # (LOGOUT_REDIRECT_URL) und kein Berechtigungsproblem.
            assert not location.startswith(f"{LOGIN_PATH}?next="), f"{url}: {actor} wurde zum Login umgeleitet"
    else:
        assert status in (403, 404), f"{url}: {actor} erwartet 403/404, bekam {status} {location}"
