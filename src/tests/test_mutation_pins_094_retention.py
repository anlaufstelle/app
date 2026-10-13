"""Pin-Tests zum Meilenstein release-0.94 — Survivors in ``core.services.compliance.retention``.

Quelle: Originalmodul ``src/core/services/compliance/retention.py``, Mutanten-
ruempfe aus dem Mutantenbaum ``mutants/src/core/services/compliance/retention.py``.
Herkunft der Auswahl: die triagierte Survivor-Liste des Modul-Batches
``retention`` (Survivor-Triage release-0.94 (Ledger des Meilensteins), 15 killbare
Mutanten aus dem 0.92er Delta-Lauf). Zwei Bloecke:

* :func:`~core.services.compliance.retention._retention_config_checks` — die
  aggregierte Warnung vor unbegrenzter AuditLog-Aufbewahrung (Refs #2053):
  Trennzeichen der Namensliste (erst ab ZWEI unbegrenzten Einrichtungen
  sichtbar), der wortwoertliche Handlungshinweis und das Anzeige-``label``;
* :func:`~core.services.compliance.retention._retention_checks` — der
  Cron-LastRun-Check (Refs #919/#958-M3/#1872): der bislang voellig
  ungepinnte UNKNOWN-Zweig (Label, Meldung, Massnahme), die Zeitbasis der
  Altersrechnung (``/ 3600.0``), das ``detail``-Feld mit dem Zeitstempel des
  letzten Laufs, die OK-Meldung samt bewusst leerer Massnahme und das
  ``label`` des OK/WARNING/CRITICAL-Checks sowie das ``.only()``-Ladebudget
  der Abfrage.

Die Bestandsdeckung (``test_compliance_service.py``,
``test_mutation_followup_compliance.py``, ``test_mutation_pins_089.py``)
prueft an diesen Stellen nur ``status``/``key``/``category`` bzw. vergleicht
Texte per Teilstring — genau deshalb haben die hier gepinnten Mutanten
ueberlebt.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt zu jedem Literal die Variante
``"XX<text>XX"``, die ein ``in``-Vergleich ueberlebt); Fixtures kommen aus
``src/tests/conftest.py`` bzw. sind echte Model-Objekte (keine Fakes); KEIN
Import aus ``test_rls_functional``. ``translation.override("de")`` ist hier
nicht noetig und waere irrefuehrend: das Modul verwendet ausschliesslich
nicht-uebersetzte Python-Literale (kein ``gettext``/``gettext_lazy``), die
Sprache der Suite hat auf die geprueften Texte keinen Einfluss. Jeder Test
nennt in der Docstring seine Mutanten und den Wert, der beim Mutanten
abweicht.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import AuditLog, Facility, Settings
from core.services import ops_thresholds
from core.services.compliance import _retention_checks, _retention_config_checks
from core.services.compliance._types import ComplianceStatus
from tests._job_run_helpers import SUCCESSFUL_JOB_RUN

pytestmark = pytest.mark.django_db


def _patch_compliance_now(fixed):
    """``core.services.compliance._clock.now`` -> ``fixed`` (Refs #958-M3).

    Dieselbe Technik wie ``test_mutation_followup_compliance.py``:
    AuditLog-Zeilen sind per DB-Trigger immutable, das Alter wird also
    ueber die Zeitquelle gestellt, nicht ueber den Zeitstempel.
    """
    return patch("core.services.compliance._clock.now", return_value=fixed)


def _retention_marker(facility=None) -> AuditLog:
    """Frischer ``RETENTION_RUN_COMPLETED``-Marker, sonst keiner in der DB."""
    AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).delete()
    entry = AuditLog.objects.create(
        action=AuditLog.Action.RETENTION_RUN_COMPLETED,
        facility=None,
        target_type="RetentionRun",
        detail={"job_run": SUCCESSFUL_JOB_RUN, "event_count": 0},
    )
    # Der Check liest die Zeile aus der DB; fuer Zeit-/Formatvergleiche
    # zaehlt der gespeicherte Wert, nicht das In-Memory-Objekt.
    return AuditLog.objects.get(pk=entry.pk)


def _no_retention_marker() -> None:
    """UNKNOWN-Vorbedingung: kein einziger Marker in der Installation."""
    AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).delete()


# ============================================================================
# _retention_config_checks — Warnung "unbegrenzte AuditLog-Aufbewahrung"
# ============================================================================


class TestRetentionConfigChecksNameEveryUnlimitedFacility:
    """Die Warnung ist eine Arbeitsliste: welche Einrichtungen sind gemeint?

    Der Super-Admin sieht nur diese eine Zeile im Compliance-Dashboard und
    muss daraus ableiten, wo er die Aufbewahrungsfrist nachziehen muss.
    """

    def test_names_are_joined_with_a_comma_and_a_space(self, organization, facility, settings_obj):
        """Pin: ``x__retention_config_checks__mutmut_22`` — ``", ".join`` -> ``"XX, XX".join``.

        Der Separator wird erst ab ZWEI unbegrenzten Einrichtungen sichtbar:
        die Bestandstests haben entweder nur eine Einrichtung
        (``test_zero_months_warns_with_facility_name``) oder zwei
        Einrichtungen mit nur EINER unbegrenzten
        (``test_mixed_facilities_are_aggregated``) — ``join`` laeuft dort nie
        ueber mehr als ein Element, der Mutant ueberlebt.

        Original: ``... 0 Monate): Teststelle, Zzz-Zweitstelle``.
        Mutant:   ``... 0 Monate): TeststelleXX, XXZzz-Zweitstelle``.

        Der zweite Name sortiert bewusst hinter den ersten, damit die
        ``order_by("name")``-Reihenfolge der Quelle mitgepinnt ist.
        """
        settings_obj.auditlog_retention_months = 0
        settings_obj.save(update_fields=["auditlog_retention_months"])
        second = Facility.objects.create(organization=organization, name="Zzz-Zweitstelle")
        Settings.objects.create(facility=second, auditlog_retention_months=0)

        checks = _retention_config_checks()

        assert len(checks) == 1
        assert checks[0].message == (
            f"2 von 2 Einrichtungen speichern das Audit-Log unbegrenzt "
            f"(Aufbewahrung 0 Monate): {facility.name}, {second.name}"
        )


class TestRetentionConfigChecksCarriesLabelAndRemedy:
    """Titel und Handlungsanweisung der Dashboard-Zeile, wortwoertlich."""

    def test_label_and_action_hint_are_verbatim(self, facility, settings_obj):
        """Pin: ``x__retention_config_checks__mutmut_29`` — ``label=None``;
        ``x__retention_config_checks__mutmut_25`` — ``action_hint`` als
        ``"XXCore → Einstellungen ...XX"``.

        ``test_zero_months_warns_with_facility_name`` prueft den Hinweis nur
        per Teilstring (``"Einstellungen" in ...``); beide Teilstrings
        ueberleben die ``XX``-Umklammerung. ``label`` prueft dort gar
        niemand.

        Original: ``label == "Audit-Log-Aufbewahrung"`` und der Hinweis ohne
        ``XX``-Klammern. Mutanten: ``label is None`` (Template Zeile 61
        rendert ``{{ check.label }}`` ungeschuetzt, die Zeile hiesse
        woertlich "None") bzw. ein Hinweis mit sichtbarem ``XX``-Muell.
        """
        settings_obj.auditlog_retention_months = 0
        settings_obj.save(update_fields=["auditlog_retention_months"])

        checks = _retention_config_checks()

        assert len(checks) == 1
        assert checks[0].status == ComplianceStatus.WARNING
        assert checks[0].label == "Audit-Log-Aufbewahrung"
        assert checks[0].action_hint == (
            "Verwaltung → Einstellungen öffnen und unter Datenschutz und Fristen "
            "eine begrenzte AuditLog-Aufbewahrung festlegen."
        )


# ============================================================================
# _retention_checks — UNKNOWN-Zweig (kein RETENTION_RUN_COMPLETED-Eintrag)
# ============================================================================


class TestRetentionCheckUnknownBranchIsSelfExplaining:
    """Der graue Check ist der Zustand "Cron lief vielleicht NIE".

    An ``enforce_retention`` haengt laut Modul-Docstring die DSFA-Zusage zu
    Loeschfristen. Ein grauer Check ohne Text und ohne Massnahme sagt der
    Leitung nicht, dass sie genau hier nachsehen muss.
    """

    def test_unknown_check_carries_label_message_and_action_hint(self):
        """Pin: ``x__retention_checks__mutmut_16`` (``label=None``),
        ``x__retention_checks__mutmut_19`` (``message=None``),
        ``x__retention_checks__mutmut_20`` (``action_hint=None``) und
        ``x__retention_checks__mutmut_26`` (Keyword ``action_hint`` ganz
        gestrichen, faellt auf den Default ``None``).

        Bestandsdeckung des UNKNOWN-Zweigs (``test_compliance_service.py``,
        ``test_mutation_followup_compliance.py``) ist ausschliesslich
        ``status == UNKNOWN`` — die drei Anzeigefelder sieht kein Test.

        Original: ``label == "Retention-Lauf"``, ``message ==
        "Noch kein RETENTION_RUN_COMPLETED-Eintrag — Cron lief vielleicht
        nie."``, ``action_hint == "enforce_retention-Cron pruefen
        (Ops-Runbook §6)."``. Bei allen vier Mutanten ist das jeweilige Feld
        ``None``: das Dashboard zeigt woertlich "None" (label/message) bzw.
        blendet den Empfehlungsblock still aus (action_hint, Template
        Zeile 74).
        """
        _no_retention_marker()

        checks = _retention_checks()

        assert len(checks) == 1
        check = checks[0]
        assert check.status == ComplianceStatus.UNKNOWN
        assert check.label == "Retention-Lauf"
        assert check.message == "Noch kein RETENTION_RUN_COMPLETED-Eintrag — Cron lief vielleicht nie."
        assert check.action_hint == "enforce_retention-Cron prüfen (Ops-Runbook §6)."


# ============================================================================
# _retention_checks — Zeitbasis, detail, OK-Zweig, Ladebudget
# ============================================================================


class TestRetentionCheckAgeUsesFullHours:
    """Die Ampelgrenzen haengen an der Umrechnung Sekunden -> Stunden."""

    def test_one_hour_is_exactly_3600_seconds(self, facility):
        """Pin: ``x__retention_checks__mutmut_32`` — ``/ 3600.0`` -> ``/ 3601.0``.

        Der Mutant staucht das Alter um Faktor 3600/3601 (~0,028 %) und
        verschiebt damit BEIDE Ampelgrenzen um rund eine Sekunde nach oben.
        Alle Boundary-Tests im Bestand liegen auf vollen Stunden (36/37/48/
        49/40/60 h) und ueberleben diese Stauchung.

        Hier ist der Marker exakt ``RETENTION_WARN_HOURS`` + 2 s alt:
        Original ``age_hours == 36,000555... > 36`` -> WARNING; Mutant
        ``age_hours == 35,9906... <= 36`` -> OK. Genau dieser Unterschied
        haengt ueber ``cron_job_checks`` an der ``/health/``-Degradierung.
        """
        entry = _retention_marker(facility)
        just_past_warn = entry.timestamp + timedelta(hours=ops_thresholds.RETENTION_WARN_HOURS, seconds=2)

        with _patch_compliance_now(just_past_warn):
            checks = _retention_checks()

        assert checks[0].status == ComplianceStatus.WARNING


class TestRetentionCheckNamesTheLastRunTimestamp:
    """Das ``detail``-Feld ist die einzige Stelle mit dem exakten Zeitpunkt."""

    def test_detail_states_the_stored_timestamp(self, facility):
        """Pin: ``x__retention_checks__mutmut_33`` (``detail = None`` statt
        f-String), ``x__retention_checks__mutmut_56`` (``detail=None`` am
        ``ComplianceCheck``) und ``x__retention_checks__mutmut_63``
        (Keyword ``detail=detail`` ganz gestrichen).

        Kein Test im Baum liest bislang ``.detail`` des Retention-Checks —
        deshalb ueberleben alle drei Varianten derselben Wurzel.

        Original: ``detail == "Letzter Lauf: <YYYY-MM-DD HH:MM>"`` des
        gespeicherten Markers. Bei allen drei Mutanten ist ``detail is
        None``; das Template blendet die Zeile per ``{% if check.detail %}``
        still aus, der Zeitpunkt des letzten Laufs verschwindet aus der
        Betriebssicht.
        """
        entry = _retention_marker(facility)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=40)):
            checks = _retention_checks()

        assert checks[0].status == ComplianceStatus.WARNING
        assert checks[0].detail == f"Letzter Lauf: {entry.timestamp:%Y-%m-%d %H:%M}"


class TestRetentionCheckOkBranchIsComplete:
    """Auch der gruene Check hat einen Vertrag: Titel, Meldung, KEINE Massnahme."""

    def test_ok_check_states_label_and_age_and_has_no_hint(self, facility):
        """Pin: ``x__retention_checks__mutmut_36`` (``message = None`` im
        OK-Zweig), ``x__retention_checks__mutmut_37`` (``hint = None`` ->
        ``hint = ""``) und ``x__retention_checks__mutmut_52``
        (``label=None`` am OK/WARNING/CRITICAL-Check).

        ``test_mutation_pins_089.py`` pinnt fuer diesen ``ComplianceCheck``
        ``key`` und ``category``, ``test_mutation_followup_compliance.py``
        den ``status`` — ``label``, die OK-Meldung und der bewusst leere
        Hinweis blieben offen.

        Marker ist 12 h alt (unter ``RETENTION_WARN_HOURS``). Original:
        ``label == "Retention-Lauf"``, ``message == "Letzter Lauf vor 12h."``,
        ``action_hint is None``. Mutanten: ``label is None`` bzw.
        ``message is None`` (das Dashboard zeigt woertlich "None") bzw.
        ``action_hint == ""`` — ein leerer statt gar kein Hinweis, ein
        Bruch des ``ComplianceCheck``-Vertrags (``action_hint: str | None``,
        oeffentliches Feld in ``compliance.__all__``).
        """
        entry = _retention_marker(facility)

        with _patch_compliance_now(entry.timestamp + timedelta(hours=12)):
            checks = _retention_checks()

        check = checks[0]
        assert check.status == ComplianceStatus.OK
        assert check.label == "Retention-Lauf"
        assert check.message == "Letzter Lauf vor 12h."
        assert check.action_hint is None


class TestRetentionCheckLoadsWhatItReads:
    """``.only()`` muss die Felder nennen, die der Check danach anfasst."""

    def test_single_query_on_the_auditlog_table(self, facility):
        """Pin: ``x__retention_checks__mutmut_4`` — ``.only("timestamp",
        "detail")`` -> ``.only("detail")``.

        Fachlich identisch (Django laedt ein zurueckgestelltes Feld lazy
        nach), aber nicht wirkungsgleich: ``entry.timestamp`` wird sowohl
        fuer ``age_hours`` als auch fuer ``detail`` gelesen und loest beim
        Mutanten pro Aufruf einen zweiten ``SELECT`` auf die AuditLog-Zeile
        aus. Das Compliance-Dashboard ruft den Check bei jedem Aufruf.

        Original: genau 1 Query auf ``core_auditlog``. Mutant: 2 (Basis-
        Query + Deferred-Load von ``timestamp``).
        """
        _retention_marker(facility)

        with CaptureQueriesContext(connection) as ctx:
            checks = _retention_checks()

        assert checks[0].status == ComplianceStatus.OK
        audit_queries = [q for q in ctx.captured_queries if AuditLog._meta.db_table in q["sql"]]
        assert len(audit_queries) == 1
