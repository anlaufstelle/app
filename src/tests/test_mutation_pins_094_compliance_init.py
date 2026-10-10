"""Pin-Tests zum Meilenstein release-0.94 — Survivors in ``core.services.compliance.__init__``.

Quelle: Survivor-Triage des Moduls ``src/core/services/compliance/__init__.py``
(Mutantenbaum ``mutants/src/core/services/compliance/__init__.py``). Gepinnt wird
das triagierte Set aus der Survivor-Triage release-0.94 (Ledger des Meilensteins)
(17 Mutanten, Herkunft 092). Alle 17 sitzen in der EINEN defensiven Klammer
:func:`core.services.compliance._run_helpers` — dem Stueck Code, das den
Kernvertrag "ein Single-Failure kippt das Compliance-Dashboard nicht" traegt.

Warum sie ueberlebt haben (Systembefund, gilt fuer alle drei Bloecke):

* Die beiden Bestandstests des Fehlerpfads (``test_compliance_service.py`` —
  ``TestAggregateChecks.test_single_helper_failure_does_not_crash_aggregator``
  und ``TestCronJobChecks.test_single_helper_failure_does_not_crash``) ersetzen
  den Helper per ``patch.object(..., side_effect=...)`` durch einen
  ``MagicMock``. Ein ``MagicMock`` hat selbst kein ``__name__`` — schon das
  ORIGINAL faellt damit auf ``"unknown_helper"`` zurueck. Der Fixture-Fake
  maskiert jede Mutation an ``getattr(helper, "__name__", "unknown_helper")``
  vollstaendig (Mutanten 3, 9, 10).
* Beide Tests asserten nur ``key.startswith("_internal_")``, ``status`` und
  ``"boom" in detail`` — ``key`` (exakt), ``label``, ``category``, ``message``
  und ``action_hint`` des ``_internal_``-Checks waren komplett ungetestet
  (Mutanten 2, 5, 11, 12, 23, 24, 26, 28, 35).
* Logs dieses Moduls fing kein Test ab (Mutanten 15–19), obwohl der
  ``action_hint`` des Checks ("Server-Log einsehen.") das Log ausdruecklich
  zum vorgesehenen Diagnosepfad erklaert.
* Die ``# pragma: no mutate``-Marker an ``category``/``message``/
  ``action_hint`` haben die Mutanten 24/26/28 NICHT verhindert — der
  Mutantenbaum enthaelt sie samt Marker (pragma-Luecke; die Marker sind hier
  wirkungslos und ersetzen keinen Test).

Die Pins arbeiten deshalb bewusst mit ECHTEN Callables statt mit Mocks: einer
benannten Modulfunktion (``boom``) fuer den Normalfall und einem echten
Callable-Objekt ohne ``__name__`` (``_NamelessHelper``) fuer den
Fallback-Pfad. Dazu ein Regressionstest auf View-Ebene, der den Vertrag
"Dashboard kippt nicht" erstmals MIT geworfenem Helper rendert.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten) — hier meist
als Gleichheit des GANZEN ``ComplianceCheck``-Dataclass-Werts bzw. der
vollstaendig gerenderten Log-Zeile; KEINE Imports aus ``test_rls_functional``;
keine Fixture-Fakes (echte Funktionen/Objekte, echte Model-Objekte). Ein
``translation.override("de")`` ist hier NICHT noetig und waere irrefuehrend:
alle gepinnten Texte sind hartkodierte Strings ohne ``gettext``, der
Sprachkontext hat auf sie keinen Einfluss. ``pytest.mark.django_db`` steht nur
am View-Regressionstest — ``_run_helpers`` selbst braucht keine Datenbank.
Jeder Test nennt seine Mutanten in der Docstring samt dem Wert, der beim
Mutanten abweicht.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.urls import reverse

from core.services import compliance
from core.services.compliance import ComplianceCheck, ComplianceStatus
from core.services.compliance._types import ComplianceCategory

LOGGER_NAME = "core.services.compliance"

BOOM_DETAIL = "kaputt: pg_roles nicht lesbar"


def boom():
    """Echter, benannter Helper, der wirft — ``__name__`` ist ``"boom"``.

    Bewusst KEIN ``MagicMock``: nur mit einem echten ``__name__`` wird
    sichtbar, ob ``_run_helpers`` den Helper-Namen wirklich durchreicht.
    """
    raise RuntimeError(BOOM_DETAIL)


class _NamelessHelper:
    """Echtes Callable OHNE ``__name__`` — erreicht den ``getattr``-Fallback.

    Instanzen einer Klasse tragen kein ``__name__`` (das ist ein Deskriptor
    auf der Metaklasse ``type``), also greift ``getattr(helper, "__name__",
    "unknown_helper")`` auf den Default zu. Genau dieser Pfad wird von den
    Bestandstests zwar durchlaufen (MagicMock), aber nie asserted.
    """

    def __call__(self):
        raise RuntimeError("kaputt ohne Namen")


def _db_role_checks_boom():
    """Ersatz fuer ``_db_role_checks`` im View-Regressionstest (echte Funktion)."""
    raise RuntimeError(BOOM_DETAIL)


# ============================================================================
# 1) Der ``_internal_``-Check: JEDES Feld ist Vertrag (key/label/category/
#    message/action_hint), nicht nur das ``_internal_``-Praefix.
# ============================================================================


class TestRunHelpersFehlerCheck:
    """``_run_helpers`` baut aus einer Helper-Exception einen vollstaendigen Check."""

    def test_der_fehler_check_traegt_helper_namen_und_alle_pflichttexte(self):
        """Pin: x__run_helpers__mutmut_2 — ``name = getattr(...)`` → ``name = None``.

        Pin: x__run_helpers__mutmut_3 — ``getattr(helper, ...)`` → ``getattr(None, ...)``.
        Pin: x__run_helpers__mutmut_9 — Attributname → ``"XX__name__XX"``.
        Pin: x__run_helpers__mutmut_10 — Attributname → ``"__NAME__"``.
        Pin: x__run_helpers__mutmut_23 — ``label=f"Interner Fehler: {name}"`` → ``label=None``.
        Pin: x__run_helpers__mutmut_24 — ``category="System"`` → ``category=None``.
        Pin: x__run_helpers__mutmut_26 — ``message="Check konnte nicht ausgefuehrt werden."`` → ``None``.
        Pin: x__run_helpers__mutmut_28 — ``action_hint="Server-Log einsehen."`` → ``None``.
        Pin: x__run_helpers__mutmut_35 — ``action_hint``-Argument entfernt (Default ``None``).

        Eine einzige Gleichheits-Zusicherung auf den GANZEN ``ComplianceCheck``
        toetet alle neun. Abweichende Werte beim Mutanten:

        * 2: ``key="_internal_None"``, ``label="Interner Fehler: None"`` — der
          Name faellt aus dem maschinenlesbaren ``key``; bei zwei
          fehlgeschlagenen Helpern kollidieren beide auf denselben ``key``.
        * 3/9/10: ``key="_internal_unknown_helper"``,
          ``label="Interner Fehler: unknown_helper"`` — der Attributzugriff
          geht ins Leere, der echte Helper-Name verschwindet.
        * 23: ``label=None`` (Dashboard zeigt "None" statt des Helfers).
        * 24: ``category=None`` (siehe zusaetzlich den View-Regressionstest —
          das kippt das Dashboard mit ``TypeError``).
        * 26: ``message=None``. 28/35: ``action_hint=None`` (der
          Empfehlungsblock ``{% if check.action_hint %}`` faellt weg).
        """
        checks = compliance._run_helpers((boom,))

        assert checks == [
            ComplianceCheck(
                key="_internal_boom",
                label="Interner Fehler: boom",
                category=ComplianceCategory.SYSTEM,
                status=ComplianceStatus.UNKNOWN,
                message="Check konnte nicht ausgeführt werden.",
                detail=BOOM_DETAIL,
                action_hint="Server-Log einsehen.",
            )
        ]

    def test_callable_ohne_name_faellt_exakt_auf_unknown_helper_zurueck(self):
        """Pin: x__run_helpers__mutmut_5 — Fallback ``"unknown_helper"`` → ``None``.

        Pin: x__run_helpers__mutmut_11 — Fallback → ``"XXunknown_helperXX"``.
        Pin: x__run_helpers__mutmut_12 — Fallback → ``"UNKNOWN_HELPER"``.

        Der Fallback ist kein reiner Logtext, sondern Teil des ``key``-Vertrags.
        Abweichende Werte beim Mutanten: ``key="_internal_None"`` (5),
        ``key="_internal_XXunknown_helperXX"`` (11),
        ``key="_internal_UNKNOWN_HELPER"`` (12) — jeweils mit passend
        verunstaltetem ``label``. Alle drei erfuellen weiterhin das von den
        Bestandstests geprueffte ``startswith("_internal_")``.
        """
        checks = compliance._run_helpers((_NamelessHelper(),))

        assert checks == [
            ComplianceCheck(
                key="_internal_unknown_helper",
                label="Interner Fehler: unknown_helper",
                category=ComplianceCategory.SYSTEM,
                status=ComplianceStatus.UNKNOWN,
                message="Check konnte nicht ausgeführt werden.",
                detail="kaputt ohne Namen",
                action_hint="Server-Log einsehen.",
            )
        ]


# ============================================================================
# 2) Die Log-Zeile: der ``action_hint`` verweist auf sie ("Server-Log
#    einsehen."), also ist ihr Wortlaut samt Helper-Name Vertrag.
# ============================================================================


class TestRunHelpersLogZeile:
    """Der Fehlerpfad protokolliert Meldung UND Helper-Namen im vollen Wortlaut."""

    def test_log_zeile_nennt_helper_namen_im_exakten_wortlaut(self, caplog):
        """Pin: x__run_helpers__mutmut_15 — ``logger.exception(msg, name)`` → ``(msg, None)``.

        Pin: x__run_helpers__mutmut_16 — ``logger.exception(msg, name)`` → ``logger.exception(name)``.
        Pin: x__run_helpers__mutmut_17 — Argument entfernt: ``logger.exception(msg)``.
        Pin: x__run_helpers__mutmut_18 — Meldung → ``"XXCompliance-Helper %s fehlgeschlagenXX"``.
        Pin: x__run_helpers__mutmut_19 — Meldung → ``"compliance-helper %s fehlgeschlagen"``.

        Verglichen wird die GESAMTE gerenderte Meldung (``record.getMessage()``),
        nie ein Teilstring. Abweichende Werte beim Mutanten:

        * 15: ``"Compliance-Helper None fehlgeschlagen"`` — der Name fehlt.
        * 16: ``"boom"`` — die Meldung fehlt ganz (``record.msg`` traegt nur
          noch den Namen, ``record.args`` ist leer).
        * 17: ``"Compliance-Helper %s fehlgeschlagen"`` — unformatiert, weil
          ``args`` leer ist; der Platzhalter bleibt literal stehen.
        * 18: ``"XXCompliance-Helper boom fehlgeschlagenXX"`` — Muellmarker
          landen im Server- und JSON-Log.
        * 19: ``"compliance-helper boom fehlgeschlagen"`` — Kleinschreibung.

        Zusaetzlich festgenagelt: Level ERROR und ein angehaengter Traceback
        (``exc_info``) — das ist der Unterschied zwischen ``logger.exception``
        und einem blossen ``logger.error``.
        """
        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            checks = compliance._run_helpers((boom,))

        records = [record for record in caplog.records if record.name == LOGGER_NAME]
        assert len(records) == 1
        assert records[0].getMessage() == "Compliance-Helper boom fehlgeschlagen"
        assert records[0].levelno == logging.ERROR
        assert records[0].exc_info is not None
        # Der Check selbst bleibt vom Logging unberuehrt.
        assert checks[0].key == "_internal_boom"


# ============================================================================
# 3) View-Regression: das Dashboard rendert MIT geworfenem Helper — bislang
#    lief jeder View-Test nur auf dem Erfolgspfad.
# ============================================================================


@pytest.mark.django_db
class TestComplianceDashboardUeberlebtHelperFehler:
    """Der Kernvertrag von ``_run_helpers`` endet nicht in der Service-Schicht."""

    def test_dashboard_rendert_trotz_geworfenem_helper(self, client, super_admin_user):
        """Pin: x__run_helpers__mutmut_24 — ``category="System"`` → ``category=None``.

        ``SystemComplianceView.get_context_data`` gruppiert nach
        ``check.category`` und sortiert die Kategorien, die nicht in
        ``_CATEGORY_ORDER`` stehen, per ``sorted(grouped)``. "Berechtigungen"
        (aus ``_app_superuser_checks``) steht dort NICHT drin — kommt ein
        ``None``-Key dazu, wirft ``sorted()`` einen ``TypeError`` ('<' not
        supported between instances of 'str' and 'NoneType'). Beim Mutanten
        kippt das Dashboard also ausgerechnet in dem Fehlerfall, den
        ``_run_helpers`` abfedern soll: statt ``status_code == 200`` schlaegt
        der Request mit ``TypeError`` fehl.

        Gepatcht wird mit einer ECHTEN Funktion statt mit ``MagicMock``, damit
        der Helper-Name real durchgereicht wird (ein Mock ohne ``__name__``
        wuerde den ``getattr``-Pfad wieder maskieren).
        """
        client.force_login(super_admin_user)
        url = reverse("core:system_compliance")

        with patch.object(compliance, "_db_role_checks", _db_role_checks_boom):
            response = client.get(url)

        assert response.status_code == 200
        groups = response.context["groups"]
        # Die Voraussetzung des Mutanten-Kills: eine Kategorie ausserhalb von
        # _CATEGORY_ORDER ist tatsaechlich vorhanden, der sorted()-Zweig laeuft.
        assert ComplianceCategory.PERMISSIONS in groups
        assert None not in groups
        internal = [
            check for check in groups[ComplianceCategory.SYSTEM] if check.key == "_internal__db_role_checks_boom"
        ]
        assert len(internal) == 1
        assert internal[0].category == ComplianceCategory.SYSTEM
        assert internal[0].status == ComplianceStatus.UNKNOWN
