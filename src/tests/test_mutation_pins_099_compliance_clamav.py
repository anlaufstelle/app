"""Pin-Tests Welle L (release-0.99) — Vollmutation #2174/#2193, Batch **compliance-2**.

Modul ``core.services.compliance.clamav``, Funktion ``_clamav_checks``. Liste
der 37 Survivors: ``/work/wellen/welle-l-0.99/reports/m-scratch/
batch-compliance-2.json``. Quelle der Mutanten-Rümpfe: Mutantenbaum
``mutants/src/core/services/compliance/clamav.py``.

``_clamav_checks`` liefert zwei ``ComplianceCheck``-Objekte: die
Erreichbarkeit des ClamAV-Daemons (``clamav_reachable``, OK/CRITICAL nach
``virus_scan.ping()``) und das Alter der Signaturen (``clamav_signature``,
UNKNOWN/OK/WARNING nach ``virus_scan.signature_info()``). Die
Bestandsdeckung (``test_compliance_service.py::TestClamavChecks``) prueft
nur ``status`` je Zweig — ``label``/``category``/``message``/``detail``/
``action_hint`` sind an keiner Stelle exakt zugesichert. Genau dort liegen
die 37 Survivors dieses Batches.

Konvention wie ``test_mutation_pins_099_compliance_backup_b.py``:
Zusicherungen vergleichen EXAKT (``==``), nie per Teilstring (mutmut
erzeugt zu jedem Text-Literal zusaetzlich die Variante ``"XX<text>XX"``,
die ein ``in``-Vergleich ueberleben wuerde). Je Zustandskombination EIN
Test, der alle Felder BEIDER zurueckgegebener Checks exakt prueft — toetet
damit mehrere Feld-Survivors in einem Lauf.

Prio-1-Begruendung (reachability-CRITICAL-Zweig, ``ping_ok=False``): ein
nicht erreichbarer ClamAV-Daemon ist eine ausgefallene Sicherheitskontrolle
(Datei-Uploads werden nicht mehr auf Malware geprueft, Refs #919). Die
Meldung + Handlungsempfehlung dieses Zweigs sind der einzige Hinweis im
Dashboard, WARUM und WIE der Ausfall zu beheben ist — ein verstuemmelter
Wortlaut (inkl. der Env-Variable ``CLAMAV_ENABLED`` in mutmut_24)
verzoegert die Wiederherstellung des Virenscanners.

Prio-2-Begruendung: ``category`` fliesst in
``core.views.system.compliance.SystemComplianceView.get_context_data``
(``grouped[check.category].append(check)`` + ``sorted(grouped)`` fuer
unbekannte Kategorien) — ein ``None``-Schluessel gefaehrdet die feste
Kategorie-Reihenfolge bzw. die Sortierung. Die WARNING-Zweig-Felder
(veraltete Signatur, echtes Malware-Risiko nach Refs #919) tragen die
konkrete Altersangabe und die freshclam-Handlungsempfehlung — fachliches
Verhalten, kein reiner Zierrat.

Nicht gepinnt: keine (alle 37 Survivors sind Klasse ``test``, siehe
Triage-Datei ``reports/m-scratch/pins-compliance-2/
triage-099-compliance-2.json``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.services import compliance
from core.services.compliance import ComplianceStatus

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _patch_virus_scan(*, ping: bool, signature_info: dict | None):
    """Doppel-Patch wie ``test_compliance_service.py::TestClamavChecks``."""
    return (
        patch("core.services.file_vault.virus_scan.ping", return_value=ping),
        patch("core.services.file_vault.virus_scan.signature_info", return_value=signature_info),
    )


class TestReachableOkSignatureUnknownPingOk:
    """``ping_ok=True`` + ``signature_info() -> None`` (Signaturdaten trotz
    erreichbarem Daemon nicht abrufbar)."""

    def test_reachable_ok_signature_unknown_ping_ok_full_wording(self):
        """Pin: ``mutmut_4`` (reachability ``label`` -> ``None``),
        ``mutmut_7`` (reachability ``message``-Ternary komplett entfernt ->
        ``None`` in beiden Zweigen, hier ueber den OK-Zweig gefangen),
        ``mutmut_17``/``_18``/``_19`` (reachability ``message`` OK-Zweig
        Wortlaut mutiert), ``mutmut_30`` (signature ``label`` -> ``None``
        im sig-None-Zweig), ``mutmut_33`` (signature ``message`` -> ``None``
        im sig-None-Zweig), ``mutmut_34``/``_40`` (signature ``action_hint``
        im sig-None-Zweig bei ``ping_ok=True`` -> ``None``, Ternary bzw.
        Keyword entfernt), ``mutmut_43`` (dieselbe ``action_hint``-Bedingung
        invertiert — bei ``ping_ok=True`` liefert das Original den Text,
        die Mutante ``None``), ``mutmut_44``/``_45``/``_46`` (``action_hint``
        Wortlaut mutiert).
        """
        p_ping, p_sig = _patch_virus_scan(ping=True, signature_info=None)
        with p_ping, p_sig:
            checks = compliance._clamav_checks()

        assert len(checks) == 2
        reachability, signature = checks

        assert reachability.key == "clamav_reachable"
        assert reachability.label == "ClamAV erreichbar"
        assert reachability.category == "Virus-Scan"
        assert reachability.status == ComplianceStatus.OK
        assert reachability.message == "Daemon antwortet."
        assert reachability.action_hint is None

        assert signature.key == "clamav_signature"
        assert signature.label == "ClamAV-Signatur"
        assert signature.category == "Virus-Scan"
        assert signature.status == ComplianceStatus.UNKNOWN
        assert signature.message == "Signatur-Daten nicht abrufbar."
        assert signature.action_hint == "ClamAV-Container neu starten, dann erneut pruefen."


class TestReachableCriticalSignatureUnknownPingDown:
    """``ping_ok=False`` + ``signature_info() -> None`` (Daemon ausgefallen,
    Sicherheitskontrolle Virenscan nicht aktiv, Refs #919)."""

    def test_reachable_critical_signature_unknown_ping_down_full_wording(self):
        """Pin (Prio 1): ``mutmut_7`` (reachability ``message``-Ternary
        komplett entfernt, hier ueber den CRITICAL-Zweig gefangen),
        ``mutmut_8``/``_14`` (reachability ``action_hint``-Ternary bzw.
        -Keyword im CRITICAL-Zweig entfernt -> ``None`` statt
        Wiederherstellungsanweisung), ``mutmut_20``/``_21``/``_22``
        (reachability ``message`` CRITICAL-Zweig Wortlaut mutiert),
        ``mutmut_23``/``_24``/``_25`` (reachability ``action_hint``
        CRITICAL-Zweig Wortlaut mutiert, ``_24`` schreibt die Env-Variable
        ``CLAMAV_ENABLED`` klein — die kopierbare Anweisung waere kaputt).
        """
        p_ping, p_sig = _patch_virus_scan(ping=False, signature_info=None)
        with p_ping, p_sig:
            checks = compliance._clamav_checks()

        assert len(checks) == 2
        reachability, signature = checks

        assert reachability.key == "clamav_reachable"
        assert reachability.label == "ClamAV erreichbar"
        assert reachability.category == "Virus-Scan"
        assert reachability.status == ComplianceStatus.CRITICAL
        assert reachability.message == "Daemon nicht erreichbar oder deaktiviert."
        assert reachability.action_hint == "docker compose ps clamav; CLAMAV_ENABLED in .env pruefen."

        assert signature.key == "clamav_signature"
        assert signature.status == ComplianceStatus.UNKNOWN
        assert signature.action_hint is None


class TestSignatureDateUnknown:
    """Signaturdaten vorhanden, aber ``age_days`` fehlt (UNKNOWN)."""

    def test_signature_date_unknown_full_wording(self):
        """Pin: ``mutmut_61`` (``label`` -> ``None``), ``mutmut_62``
        (``category`` -> ``None`` — bricht die Kategorie-Gruppierung in
        ``SystemComplianceView``), ``mutmut_64`` (``message`` -> ``None``)."""
        p_ping, p_sig = _patch_virus_scan(ping=True, signature_info={"age_days": None, "version": None})
        with p_ping, p_sig:
            checks = compliance._clamav_checks()

        signature = checks[1]
        assert signature.key == "clamav_signature"
        assert signature.label == "ClamAV-Signatur"
        assert signature.category == "Virus-Scan"
        assert signature.status == ComplianceStatus.UNKNOWN
        assert signature.message == "Signatur-Datum nicht ermittelbar."
        assert signature.detail == "Version: unbekannt"


class TestSignatureFresh:
    """Signatur jung genug (``age_days <= 7`` -> OK)."""

    def test_signature_fresh_full_wording(self):
        """Pin: ``mutmut_56`` (Versions-Fallback ``"unbekannt"`` ->
        mutmut-Marker, hier ueber fehlenden ``version``-Key gefangen),
        ``mutmut_78`` (``label`` -> ``None``), ``mutmut_79`` (``category``
        -> ``None``), ``mutmut_81`` (``message`` -> ``None``),
        ``mutmut_82``/``_88`` (``detail`` -> ``None``, Ternary-Wert bzw.
        Keyword entfernt)."""
        p_ping, p_sig = _patch_virus_scan(ping=True, signature_info={"age_days": 3})
        with p_ping, p_sig:
            checks = compliance._clamav_checks()

        signature = checks[1]
        assert signature.key == "clamav_signature"
        assert signature.label == "ClamAV-Signatur"
        assert signature.category == "Virus-Scan"
        assert signature.status == ComplianceStatus.OK
        assert signature.message == "Signatur ist 3 Tag(e) alt."
        assert signature.detail == "Version: unbekannt"


class TestSignatureStale:
    """Signatur veraltet (``age_days > 7`` -> WARNING, echtes
    Malware-Risiko, Refs #919)."""

    def test_signature_stale_full_wording(self):
        """Pin: ``mutmut_93`` (``label`` -> ``None``), ``mutmut_94``
        (``category`` -> ``None`` — bricht die Kategorie-Gruppierung),
        ``mutmut_96`` (``message`` -> ``None``, Admin verliert die
        konkrete Altersangabe gegen den Schwellwert), ``mutmut_97``/``_104``
        (``detail`` -> ``None``), ``mutmut_98``/``_105`` (``action_hint``
        -> ``None`` — die freshclam-Handlungsempfehlung fuer eine veraltete
        Signatur fehlt)."""
        p_ping, p_sig = _patch_virus_scan(ping=True, signature_info={"age_days": 30, "version": "1.4.2"})
        with p_ping, p_sig:
            checks = compliance._clamav_checks()

        signature = checks[1]
        assert signature.key == "clamav_signature"
        assert signature.label == "ClamAV-Signatur"
        assert signature.category == "Virus-Scan"
        assert signature.status == ComplianceStatus.WARNING
        assert signature.message == "Signatur ist 30 Tage alt (Schwelle: 7)."
        assert signature.detail == "Version: 1.4.2"
        assert signature.action_hint == "freshclam-Cron im ClamAV-Container pruefen."
