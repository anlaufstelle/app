"""Pin-Tests Welle L (release-0.99) — Vollmutation #2174/#2193, Batch
**compliance-5**.

Module: ``core.services.compliance.system_info`` (``_migration_checks``,
``_version_checks``), ``core.services.compliance.app_superuser``
(``_app_superuser_checks``), ``core.services.compliance.k_anonymization``
(``_pseudonym_hash``, ``k_anonymize_client``). Liste der 54 Survivors:
``/work/wellen/welle-l-0.99/reports/m-scratch/batch-compliance-5.json``.
Triage: ``reports/m-scratch/pins-compliance-5/triage-099-compliance-5.json``.
Quelle der Mutanten-Rümpfe: Mutantenbaum
``mutants/src/core/services/compliance/{system_info,app_superuser,
k_anonymization}.py``.

``_migration_checks``/``_app_superuser_checks`` teilen dieselbe Struktur
(OK-Zweig ohne Treffer, CRITICAL-Zweig mit Top-5-Liste + „(+ n weitere)“-
Suffix ab dem 6. Treffer). Die Bestandsdeckung (``test_compliance_service.py``)
prüft dort ausschließlich ``status`` — Wortlaut (``key``/``label``/
``category``/``message``/``detail``/``action_hint``) sowie die exakten
Top-5-/Suffix-Grenzen (``> 5`` vs. ``>= 5``/``> 6``, Slice ``[:5]`` vs.
``[:6]``, Arithmetik ``- 5`` vs. ``+ 5``/``- 6``) bleiben ungepinnt. Genau
dort liegen die Survivors dieser beiden Funktionen.

Konvention wie ``test_mutation_pins_099_compliance_clamav.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt zu jedem
Text-Literal zusätzlich die Variante ``"XX<text>XX"``, die ein ``in``-
Vergleich überleben würde). Je Zustandskombination EIN Test, der alle
Felder exakt prüft — tötet damit mehrere Feld-Survivors in einem Lauf.

Prio-1-Begründung (``_app_superuser_checks``): friert die mit #1271/#1297
etablierte Invariante ein, dass kein App-``User`` Djangos ``is_superuser``-
Flag trägt — Autorisierung läuft ausschließlich über die Rolle. Ein
verstümmelter oder unvollständiger CRITICAL-Befund (fehlende Namen, falsche
Trefferzahl, fehlende Remediation-Anweisung) verzögert die Behebung eines
aktiven Sicherheitsbefunds.

Prio-2/3-Begründung (``_migration_checks``): ausstehende Migrationen sind
ein Betriebszustand (kein Sicherheitsbefund im engeren Sinn), aber die
korrekte Liste/Anzahl ist fachlich relevant für den Betreiber (welche/wie
viele Migrationen fehlen). ``_version_checks`` ist eine reine Info-Karte
ohne CRITICAL-Zweig — Prio 3.

Nicht gepinnt (äquivalent, siehe Triage-Datei):

* ``x__pseudonym_hash__mutmut_6`` (``.encode("utf-8")`` ->
  ``.encode("UTF-8")``) — Python-Codec-Namen sind case-insensitiv;
  ``"x".encode("utf-8") == "x".encode("UTF-8")`` liefert byte-identische
  Ausgabe (empirisch verifiziert), der SHA-256-Digest ist ununterscheidbar.
* ``x_k_anonymize_client__mutmut_1`` (Default ``k: int = 5`` ->
  ``k: int = 6``) — nicht mehr erzeugbar (Refs #2265): der Parameter ``k``
  wurde entfernt, weil der Funktionskörper ihn kein einziges Mal las; die
  Schwellenprüfung lag und liegt bei ``is_k_anonymous``, nicht bei
  ``k_anonymize_client``. Historische Begründung des ursprünglichen
  Nicht-Pinnens (vor der Entfernung): der einzige Produktionscaller übergab
  ``k`` stets explizit, kein Aufrufer konnte den Default-Wert beobachten.
* ``x__latest__mutmut_4`` (``core.services.compliance.cron``,
  ``.only("timestamp", "detail")`` -> ``.only("timestamp", )``) — keiner
  der vier Last-Run-Checks liest je ``entry.detail``, nur
  ``entry.timestamp`` bleibt im Ladebudget; bereits in
  ``test_mutation_pins_098_compliance_cron.py`` für exakt diese Mutante so
  dokumentiert.
* ``x__retention_checks__mutmut_5`` (``core.services.compliance.retention``,
  identische ``.only()``-Verkürzung) — dieselbe Argumentation: das im
  Return verwendete ``detail`` ist ein lokal berechneter String, nie
  ``entry.detail`` (Modellfeld).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.models.user import User
from core.services import compliance
from core.services.compliance import ComplianceStatus
from core.services.compliance._types import ComplianceCategory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _pending(n: int) -> list[tuple[str, str]]:
    """``n`` deterministisch benannte, ausstehende Migrationen (fuer ``_migration_checks``)."""
    return [("core", f"02{i:02d}_test") for i in range(1, n + 1)]


def _patch_pending(pending: list[tuple[str, str]]):
    return patch("core.services.system.health.pending_migrations", return_value=pending)


def _make_superusers(n: int) -> list[str]:
    """``n`` App-User mit ``is_superuser=True`` und alphabetisch sortierbaren Namen."""
    names = [f"sup{i:02d}" for i in range(1, n + 1)]
    for name in names:
        user = User.objects.create_user(username=name, password="x", role=User.Role.STAFF)
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
    return names


# ============================================================================
# _migration_checks
# ============================================================================


class TestMigrationChecksOkNoPending:
    """OK-Zweig (keine ausstehenden Migrationen)."""

    def test_migration_checks_ok_no_pending(self):
        """Pin: ``mutmut_4`` (``label`` -> ``None``), ``mutmut_7`` (``message``
        -> ``None``), ``mutmut_13``/``_14`` (``key`` -> XX-Marker bzw.
        Grossschreibung — ``key`` ist laut ``_types.ComplianceCheck``-Docstring
        stabil/maschinenlesbar, Tests/Templates verlassen sich darauf)."""
        with _patch_pending([]):
            checks = compliance._migration_checks()

        assert len(checks) == 1
        check = checks[0]
        assert check.key == "migrations_pending"
        assert check.label == "Migrationen"
        assert check.category == ComplianceCategory.SYSTEM
        assert check.status == ComplianceStatus.OK
        assert check.message == "Alle Migrationen angewendet."
        assert check.detail is None
        assert check.action_hint is None


class TestMigrationChecksCriticalFewPending:
    """CRITICAL-Zweig, <= 5 ausstehende Migrationen (kein Suffix)."""

    def test_migration_checks_critical_few_pending(self):
        """Pin: ``mutmut_15`` (``listing`` -> ``None``), ``mutmut_17``
        (Separator ``", "`` -> ``"XX, XX"``), ``mutmut_25`` (``key`` ->
        ``None``), ``mutmut_26`` (``label`` -> ``None``), ``mutmut_27``
        (``category`` -> ``None`` — bricht die Kategorie-Gruppierung in
        ``SystemComplianceView``), ``mutmut_29`` (``message`` -> ``None``),
        ``mutmut_30``/``_37`` (``detail`` -> ``None``, Wertersetzung bzw.
        Kwarg-Entfernung), ``mutmut_31``/``_38`` (``action_hint`` -> ``None``
        — verliert die Migrationsanweisung), ``mutmut_39``/``_40`` (``key``
        -> XX-Marker bzw. Grossschreibung, hier im CRITICAL-Zweig)."""
        pending = _pending(3)
        with _patch_pending(pending):
            checks = compliance._migration_checks()

        assert len(checks) == 1
        check = checks[0]
        assert check.key == "migrations_pending"
        assert check.label == "Migrationen"
        assert check.category == ComplianceCategory.SYSTEM
        assert check.status == ComplianceStatus.CRITICAL
        assert check.message == "3 ausstehende Migration(en)."
        assert check.detail == "core.0201_test, core.0202_test, core.0203_test"
        assert check.action_hint == "docker compose exec web python manage.py migrate ausfuehren."


class TestMigrationChecksBoundaryFivePending:
    """Grenzfall: genau 5 ausstehende Migrationen -> kein Suffix."""

    def test_migration_checks_critical_boundary_five_pending(self):
        """Pin: ``mutmut_19`` (``len(pending) > 5`` -> ``>= 5``) — bei
        genau 5 haengt der Mutant faelschlich ``" (+ 0 weitere)"`` an."""
        pending = _pending(5)
        with _patch_pending(pending):
            checks = compliance._migration_checks()

        check = checks[0]
        assert check.message == "5 ausstehende Migration(en)."
        assert check.detail == "core.0201_test, core.0202_test, core.0203_test, core.0204_test, core.0205_test"


class TestMigrationChecksBoundarySixPending:
    """Grenzfall: genau 6 ausstehende Migrationen -> Suffix noetig."""

    def test_migration_checks_critical_boundary_six_pending(self):
        """Pin: ``mutmut_20`` (``len(pending) > 5`` -> ``> 6``) — bei genau
        6 unterschlaegt der Mutant den Suffix ``" (+ 1 weitere)"``."""
        pending = _pending(6)
        with _patch_pending(pending):
            checks = compliance._migration_checks()

        check = checks[0]
        assert check.message == "6 ausstehende Migration(en)."
        assert check.detail == (
            "core.0201_test, core.0202_test, core.0203_test, core.0204_test, core.0205_test (+ 1 weitere)"
        )


class TestMigrationChecksManyPending:
    """> 6 ausstehende Migrationen: Slice/Operator/Arithmetik des Suffix."""

    def test_migration_checks_critical_many_pending(self):
        """Pin: ``mutmut_18`` (``pending[:5]`` -> ``pending[:6]`` — ein
        zusaetzliches Item in der Liste statt der dokumentierten Top-5),
        ``mutmut_21`` (``listing += ...`` -> ``listing = ...`` — die
        Migrationsnamen verschwinden, nur der Suffix bleibt), ``mutmut_22``
        (``listing += ...`` -> ``listing -= ...`` — ``str -= str`` ist
        undefiniert, die Funktion wirft ``TypeError`` statt eine Liste
        zurueckzugeben), ``mutmut_23`` (``- 5`` -> ``+ 5`` im Suffix,
        falsche Anzahl), ``mutmut_24`` (``- 5`` -> ``- 6`` im Suffix, Anzahl
        um eins zu klein)."""
        pending = _pending(8)
        with _patch_pending(pending):
            checks = compliance._migration_checks()

        check = checks[0]
        assert check.message == "8 ausstehende Migration(en)."
        assert check.detail == (
            "core.0201_test, core.0202_test, core.0203_test, core.0204_test, core.0205_test (+ 3 weitere)"
        )


# ============================================================================
# _version_checks
# ============================================================================


class TestVersionChecksFullWording:
    def test_version_checks_full_wording(self):
        """Pin: ``mutmut_10`` (``label`` -> ``None``), ``mutmut_19``/``_20``
        (``key`` -> XX-Marker bzw. Grossschreibung)."""
        checks = compliance._version_checks()

        assert len(checks) == 1
        check = checks[0]
        assert check.key == "versions"
        assert check.label == "Versionen"
        assert check.category == ComplianceCategory.SYSTEM
        assert check.status == ComplianceStatus.OK
        assert "Django" in check.message
        assert "Python" in check.message


# ============================================================================
# _app_superuser_checks (Refs #1271/#1297, Prio 1)
# ============================================================================


class TestAppSuperuserChecksOkNoOffenders:
    """OK-Zweig (kein App-User traegt is_superuser=True)."""

    def test_app_superuser_checks_ok_no_offenders(self):
        """Pin: ``mutmut_17`` (``label`` -> ``None``), ``mutmut_20``
        (``message`` -> ``None``)."""
        checks = compliance._app_superuser_checks()

        assert len(checks) == 1
        check = checks[0]
        assert check.key == "app_user_no_django_superuser"
        assert check.label == "App-User kein Django-Superuser"
        assert check.category == ComplianceCategory.PERMISSIONS
        assert check.status == ComplianceStatus.OK
        assert check.message == "Kein App-User traegt Djangos is_superuser-Flag."
        assert check.detail is None
        assert check.action_hint is None


class TestAppSuperuserChecksCriticalFewOffenders:
    """CRITICAL-Zweig, <= 5 betroffene User (kein Suffix)."""

    def test_app_superuser_checks_critical_few_offenders(self):
        """Pin: ``mutmut_30`` (Separator ``", "`` -> ``"XX, XX"``),
        ``mutmut_38`` (``key`` -> ``None``), ``mutmut_39`` (``label`` ->
        ``None``), ``mutmut_40`` (``category`` -> ``None`` — bricht die
        Kategorie-Gruppierung), ``mutmut_42`` (``message`` -> ``None``),
        ``mutmut_44``/``_51`` (``action_hint`` -> ``None``, Wertersetzung
        bzw. Kwarg-Entfernung — verliert die Remediation-Anweisung eines
        aktiven Sicherheitsbefunds), ``mutmut_52``/``_53`` (``key`` ->
        XX-Marker bzw. Grossschreibung), ``mutmut_54``/``_55``/``_56``
        (``action_hint`` 1. Haelfte: XX-Marker/lowercase/UPPERCASE),
        ``mutmut_57``/``_58``/``_59`` (``action_hint`` 2. Haelfte:
        XX-Marker/lowercase/UPPERCASE)."""
        offenders = _make_superusers(3)
        checks = compliance._app_superuser_checks()

        assert len(checks) == 1
        check = checks[0]
        assert check.key == "app_user_no_django_superuser"
        assert check.label == "App-User kein Django-Superuser"
        assert check.category == ComplianceCategory.PERMISSIONS
        assert check.status == ComplianceStatus.CRITICAL
        assert check.message == "3 App-User mit is_superuser=True."
        assert check.detail == ", ".join(offenders)
        assert check.action_hint == (
            "is_superuser dieser User auf False setzen — Autorisierung laeuft "
            "ueber die Rolle, nie ueber das Django-Flag (Refs #1271)."
        )


class TestAppSuperuserChecksBoundaryFiveOffenders:
    """Grenzfall: genau 5 betroffene User -> kein Suffix."""

    def test_app_superuser_checks_critical_boundary_five_offenders(self):
        """Pin: ``mutmut_32`` (``len(offenders) > 5`` -> ``>= 5``) — bei
        genau 5 haengt der Mutant faelschlich einen Suffix an."""
        offenders = _make_superusers(5)
        checks = compliance._app_superuser_checks()

        check = checks[0]
        assert check.message == "5 App-User mit is_superuser=True."
        assert check.detail == ", ".join(offenders)


class TestAppSuperuserChecksBoundarySixOffenders:
    """Grenzfall: genau 6 betroffene User -> Suffix noetig."""

    def test_app_superuser_checks_critical_boundary_six_offenders(self):
        """Pin: ``mutmut_33`` (``len(offenders) > 5`` -> ``> 6``) — bei
        genau 6 unterschlaegt der Mutant den Suffix, ein betroffener
        Django-Superuser bleibt in der Anzeige unsichtbar."""
        offenders = _make_superusers(6)
        checks = compliance._app_superuser_checks()

        check = checks[0]
        assert check.message == "6 App-User mit is_superuser=True."
        assert check.detail == ", ".join(offenders[:5]) + " (+ 1 weitere)"


class TestAppSuperuserChecksManyOffenders:
    """> 6 betroffene User: Slice/Operator/Arithmetik des Suffix."""

    def test_app_superuser_checks_critical_many_offenders(self):
        """Pin: ``mutmut_31`` (``offenders[:5]`` -> ``offenders[:6]`` — ein
        zusaetzliches Item in der Trefferliste eines Sicherheitsbefunds),
        ``mutmut_34`` (``listing += ...`` -> ``listing = ...`` — die
        betroffenen Usernamen verschwinden, nur der Suffix bleibt: WER
        betroffen ist, geht verloren), ``mutmut_35`` (``listing += ...``
        -> ``listing -= ...`` — ``str -= str`` ist undefiniert, der Check
        wirft ``TypeError`` statt den Befund zu melden), ``mutmut_36``
        (``- 5`` -> ``+ 5`` im Suffix, falsche Anzahl), ``mutmut_37``
        (``- 5`` -> ``- 6`` im Suffix, Anzahl um eins zu klein)."""
        offenders = _make_superusers(8)
        checks = compliance._app_superuser_checks()

        check = checks[0]
        assert check.message == "8 App-User mit is_superuser=True."
        assert check.detail == ", ".join(offenders[:5]) + " (+ 3 weitere)"
