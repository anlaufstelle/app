"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.audit.detail_policy``.

Quelle: Vollmutation #2174, Batch ``audit-2``
(``welle-l-0.99/reports/m-scratch/batch-audit-2.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-audit-2/triage-099-audit-2.json``).

Der Verstoss-Meldungstext von ``validate_audit_detail`` ist Teil des
Datenminimierungs-Nachweises (Refs #1944): im Strict-Modus (Tests/CI,
``settings.AUDIT_DETAIL_STRICT = True``) landet er woertlich in der
geworfenen ``UnknownAuditDetailKeyError``. Ein ``pytest.raises(match=...)``
mit einem kurzen Teilstring uebersieht Gross-/Kleinschreibungs- und
mutmut-Marker-Aenderungen (``XX…XX``) ausserhalb des gesuchten Teilstrings —
deshalb vergleicht dieser Pin den KOMPLETTEN Meldungstext exakt.

Killt in einer einzigen Assertion alle zehn Wortlaut-Mutanten von
``x_validate_audit_detail``: ``__mutmut_8``, ``__mutmut_9``, ``__mutmut_10``,
``__mutmut_12``, ``__mutmut_13``, ``__mutmut_14``, ``__mutmut_15``,
``__mutmut_16``, ``__mutmut_17``, ``__mutmut_18`` — jede veraendert einen
anderen Abschnitt derselben zusammengesetzten f-String-Meldung (mutmut-Marker
``XX…XX`` bzw. vollstaendige Gross-/Kleinschreibung); ein exakter Vergleich
der vollstaendigen Meldung trifft daher jede von ihnen.

``x__strict__mutmut_4`` (``getattr(settings, "AUDIT_DETAIL_STRICT", None)``
statt ``..., False)``) ist NICHT gepinnt — Begruendung in der Triage-JSON
(Klasse ``equivalent``: ``bool(None) == bool(False)``, der Default wirkt sich
nur aus, wenn das Settings-Attribut fehlt, und liefert in beiden Faellen
``False``).
"""

from __future__ import annotations

import pytest

from core.services.audit.detail_policy import UnknownAuditDetailKeyError, validate_audit_detail

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_validate_audit_detail_meldungstext_ist_zeichengenau(settings):
    """Pins zehn Wortlaut-Mutanten von ``x_validate_audit_detail`` (siehe
    Moduldocstring dieser Datei): im Strict-Modus muss die Fehlermeldung
    ZEICHENGENAU dem dokumentierten Wortlaut entsprechen. Ein
    ``pytest.raises(match=...)`` mit einem Teilstring wuerde
    Gross-/Kleinschreibungs- und mutmut-Marker-Varianten ausserhalb des
    gesuchten Teilstrings uebersehen (Fehlerklasse: Teilstring-Suche statt
    Exaktvergleich)."""
    settings.AUDIT_DETAIL_STRICT = True
    unbekannt = ["boese_schluessel_pin099"]

    with pytest.raises(UnknownAuditDetailKeyError) as exc:
        validate_audit_detail({"boese_schluessel_pin099": "wert"}, source="pin099-audit2")

    erwartet = (
        f"Nicht deklarierte AuditLog-detail-Schluessel aus pin099-audit2: {unbekannt}. "
        "Jeder detail-Schluessel muss in core.services.audit.detail_policy.AUDIT_DETAIL_KEYS "
        "mit Begruendung stehen (Datenminimierung im append-only Log, Refs #1944). "
        "Klienten-Freitext gehoert nicht hinein — statt Klartext einen HMAC "
        "(hmac_hash_value) oder die target_id verwenden."
    )
    assert str(exc.value) == erwartet
