"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — ``core.services.hubs``,
Batch ``hubs-1`` (Vollmutation-Survivor-Triage, 37 Survivors in
``datenschutz_overview``/``verwaltung_metrics``/``verwaltung_overview``).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-hubs-1.json`` (Triage
``reports/m-scratch/pins-hubs-1/triage-099-hubs-1.json``).

Alle 36 gepinnten Survivors dieses Batches mutieren woertliche Konstanten:
``testid``-Selektoren (27 Mutanten, je Zeile ``None``/XX-Marker/GROSSSCHREIBUNG)
und die beiden lokalisierten ``_date_line``-Argumente von Audit-/DSGVO-Zeile
(9 Mutanten, davon ein echter Meldungsverlust ``-> None`` und acht
XX-Marker-/Case-Varianten desselben Wortlauts). Vier Tests reichen, weil
jeder Test alle Varianten EINER Codezeile per exakter Gleichheit killt: eine
falsche/None-Kennung besteht die Gleichheitspruefung so wenig wie ein
XX-markierter oder klein-/grossgeschriebener Wortlaut.

Der 37. Survivor (``x_verwaltung_overview__mutmut_4``, ``facility_admin_
dashboard_context(user, facility)`` -> ``facility_admin_dashboard_context
(None, facility)``) ist laut Triage AEQUIVALENT und hier bewusst NICHT
gepinnt: ``facility_admin_dashboard_context`` (``core/services/dashboard/
main.py``) liest ihren ``user``-Parameter im gesamten Funktionskoerper nie.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone, translation
from django.utils.formats import date_format

from core.models import AuditLog
from core.services.hubs import datenschutz_overview, verwaltung_metrics

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _metric(metrics, key):
    found = [m for m in metrics if m.key == key]
    assert found, f"Kennzahl {key!r} fehlt (vorhanden: {[m.key for m in metrics]})"
    return found[0]


# ---------------------------------------------------------------------------
# datenschutz_overview: testid-Selektoren aller fuenf Zeilen
# ---------------------------------------------------------------------------


def test_datenschutz_overview_testids_match_the_documented_selectors(facility, admin_user):
    """Pins core.services.hubs.x_datenschutz_overview__mutmut_11/32/33 (deletion-
    requests), _52/72/73 (rectification-requests), _85/106/107 (retention),
    _127/156/157 (audit) und _183/210/211 (dsgvo) — je ``testid="hub-metric-…"``
    -> ``None``/``"XXhub-metric-…XX"``/``"HUB-METRIC-…"``.

    ``testid`` ist der ``data-testid``-Anker aus ``_metric_row.html``
    (Wert-/Detail-Span UND „Öffnen“-Link) — eine falsche Kennung reisst jeden
    Playwright-Lokator dieser Zeile ab, ohne Zahl oder Rolle zu veraendern.
    """
    metrics = datenschutz_overview(facility, admin_user)

    assert _metric(metrics, "deletion-requests").testid == "hub-metric-deletion-requests"
    assert _metric(metrics, "rectification-requests").testid == "hub-metric-rectification-requests"
    assert _metric(metrics, "retention").testid == "hub-metric-retention"
    assert _metric(metrics, "audit").testid == "hub-metric-audit"
    assert _metric(metrics, "dsgvo").testid == "hub-metric-dsgvo"


# ---------------------------------------------------------------------------
# datenschutz_overview: Audit-Zeile — beide _date_line-Zweige woertlich
# ---------------------------------------------------------------------------


def test_datenschutz_overview_audit_line_wording_for_both_branches(facility, admin_user):
    """Pins x_datenschutz_overview__mutmut_142 (Meldungsverlust: ``_("noch
    keine Einträge")`` -> ``None``) sowie _147/148 (Present-Zweig ``_("letzter
    Eintrag am %(date)s")`` als XX-Marker/Kleinschreibung) und _151/152/153
    (Absent-Zweig als XX-Marker/Klein-/GROSSSCHREIBUNG).

    Ohne Audit-Eintrag liefert die Zeile den Absent-Text; mit einem Eintrag
    den Present-Text samt formatiertem Datum — beide Zweige exakt geprueft
    killt None-Verlust UND jede Marker-/Case-Variante der jeweiligen Zeile.
    """
    with translation.override("de"):
        absent = _metric(datenschutz_overview(facility, admin_user), "audit")
    assert absent.detail == "noch keine Einträge"

    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
        target_type="Sonstiges",
        target_id="pin-hubs-1-audit",
        timestamp=timezone.now() - timedelta(days=3),
    )
    expected_date = date_format(timezone.localtime(entry.timestamp), "SHORT_DATE_FORMAT")
    expected = f"letzter Eintrag am {expected_date}"
    with translation.override("de"):
        present = _metric(datenschutz_overview(facility, admin_user), "audit")
    assert present.detail == expected


# ---------------------------------------------------------------------------
# datenschutz_overview: DSGVO-Zeile — beide _date_line-Zweige woertlich
# ---------------------------------------------------------------------------


def test_datenschutz_overview_dsgvo_line_wording_for_both_branches(facility, admin_user):
    """Pins x_datenschutz_overview__mutmut_203 (Present-Zweig ``_("zuletzt
    heruntergeladen am %(date)s")`` als XX-Marker) sowie _206/207 (Absent-
    Zweig ``_("noch nie heruntergeladen")`` als XX-Marker/GROSSSCHREIBUNG).

    Wie bei der Audit-Zeile: Absent- und Present-Text je exakt geprueft,
    diesmal mit einem EXPORT-Eintrag auf ``target_type="DSGVO-Dokument"``.
    """
    with translation.override("de"):
        absent = _metric(datenschutz_overview(facility, admin_user), "dsgvo")
    assert absent.detail == "noch nie heruntergeladen"

    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.EXPORT,
        target_type="DSGVO-Dokument",
        target_id="pin-hubs-1-dsgvo",
        timestamp=timezone.now() - timedelta(days=2),
    )
    expected_date = date_format(timezone.localtime(entry.timestamp), "SHORT_DATE_FORMAT")
    expected = f"zuletzt heruntergeladen am {expected_date}"
    with translation.override("de"):
        present = _metric(datenschutz_overview(facility, admin_user), "dsgvo")
    assert present.detail == expected


# ---------------------------------------------------------------------------
# verwaltung_metrics: testid-Selektoren aller vier Zeilen
# ---------------------------------------------------------------------------


def test_verwaltung_metrics_testids_match_the_documented_selectors(facility):
    """Pins core.services.hubs.x_verwaltung_metrics__mutmut_30/55/56 (config),
    _62/80/81 (config-fieldtemplates), _87/108/109 (import) und _117/135/136
    (storage) — je ``testid="hub-metric-…"`` -> ``None``/XX-Marker/GROSS."""
    metrics = verwaltung_metrics(facility)

    assert _metric(metrics, "config").testid == "hub-metric-config"
    assert _metric(metrics, "config-fieldtemplates").testid == "hub-metric-config-fieldtemplates"
    assert _metric(metrics, "import").testid == "hub-metric-import"
    assert _metric(metrics, "storage").testid == "hub-metric-storage"
