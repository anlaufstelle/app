"""Pin-Tests Welle L (release-0.99) — Survivors in ``core.services.compliance.mfa``.

Ticket-Auftrag #2174/#2193 (Batch **compliance-4**): 35 Survivors aus
dem Vollmutations-Lauf (Prüfstand 25685845, Basis main e3c125e6). Liste:
``/work/wellen/welle-l-0.99/reports/m-scratch/batch-compliance-4.json``.
Quelle der Mutanten-Rümpfe: Mutantenbaum
``mutants/src/core/services/compliance/mfa.py``.

``_mfa_checks()`` hat vier Antwortpfade: UNKNOWN (``privileged_total == 0``),
OK (``percent >= 100``), WARNING (``80 <= percent < 100``), CRITICAL
(``percent < 80``). Die Bestandsdeckung (``test_compliance_service.py::
TestMfaChecks``, ``test_mfa_quota_denominator.py``) prüft ausschließlich
``status`` an UNKNOWN/CRITICAL — ``key``/``label``/``category``/``message``/
``detail``/``action_hint`` blieben an allen vier Pfaden ungepinnt, ebenso die
Zähler-Query (``TOTPDevice.objects.filter(confirmed=True,
user__role__in=enforced_roles, user__is_active=True)``) und die Rundungs-/
Multiplikationskonstanten der Prozent-Berechnung
(``round(... * 100, 1)``). Genau dort liegen die 35 Survivors dieses
Batches.

Konvention wie ``test_mutation_pins_098_compliance_cron.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt zu jedem
Text-Literal die Variante ``"XX<text>XX"``, die ein ``in``-Vergleich
übersieht). Jeder Test nennt in der Docstring die Mutanten
(``x__mfa_checks__mutmut_<n>``), die er tötet.

Nicht gepinnt (äquivalent, siehe Bericht ``m-pins-compliance-4-report.md``):
``x__mfa_checks__mutmut_35`` (``Count("id")`` -> ``Count(None)`` im
``.values("user_id").annotate(n=Count(...))``-Zwischenschritt; das
anschließende ``.count()`` zählt für eine ``values()+annotate()``-Query
IMMER die Anzahl der GROUP-BY-Gruppen über ein äußeres ``COUNT(*)`` auf die
Subquery — der Wert der ``n``-Annotation selbst wird an keiner Stelle
gelesen. SQL-Verifikation (``qs.query.get_count(...)``) zeigt für beide
Varianten dieselbe ``GROUP BY``-Struktur und denselben Count-Pfad; kein
Aufrufer kann den Unterschied beobachten).
"""

from __future__ import annotations

import pytest

from core.models import EncryptedTOTPDevice
from core.models.user import User, mfa_enforced_roles
from core.services.compliance import ComplianceStatus
from core.services.compliance.mfa import _mfa_checks

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _make_user(facility, role, username, *, is_active=True):
    return User.objects.create_user(
        username=username,
        password="anlaufstelle2026",
        facility=facility,
        role=role,
        is_active=is_active,
    )


def _confirmed_totp(user, name):
    return EncryptedTOTPDevice.objects.create(user=user, name=name, confirmed=True)


class TestUnknownBranchFullWording:
    """``privileged_total == 0`` — frühe Rückgabe vor der Prozent-Berechnung."""

    def test_unknown_check_key_and_label(self, facility):
        """Pins ``x__mfa_checks__mutmut_11/20/21``.

        ``_11`` setzt ``label=None``, ``_20``/``_21`` verstümmeln den
        ``key`` (``"XXmfa_privileged_quoteXX"`` / ``"MFA_PRIVILEGED_QUOTE"``).
        Kein Bestandstest liest ``key``/``label`` des UNKNOWN-Zweigs.
        """
        User.objects.filter(role__in=mfa_enforced_roles()).delete()

        checks = _mfa_checks()

        assert checks[0].key == "mfa_privileged_quote"
        assert checks[0].label == "MFA-Quote (MFA-pflichtige Rollen)"
        assert checks[0].category == "MFA"
        assert checks[0].status == ComplianceStatus.UNKNOWN
        assert checks[0].message == "Keine MFA-pflichtigen User vorhanden."


class TestOkBranchFullWording:
    """``percent >= 100`` — 1 von 1 erzwungenem User hat MFA aktiv."""

    def test_ok_check_full_wording(self, settings, facility):
        """Pins ``x__mfa_checks__mutmut_47/51/52/53/54/55`` sowie die im OK-
        Zweig unterscheidbaren gemeinsamen Feld-Mutanten ``_70/_71/_74/_75/
        _82/_84/_85``.

        ``_47`` setzt das gemeinsame ``detail`` auf ``None`` (vor der
        Verzweigung berechnet); ``_51``-``_54`` verstümmeln die
        OK-``message`` (``None``/``XX``-Präfix/lower/upper); ``_55`` setzt
        den in diesem Zweig korrekt ``None``-wertigen ``hint`` auf ``""``.
        ``_70/_84/_85`` verstümmeln den ``key`` des finalen
        ``ComplianceCheck``, ``_71`` das ``label``, ``_74`` das ``message``-
        Feld, ``_75``/``_82`` das ``detail``-Feld (Wert bzw. Kwarg entfernt)
        — alle im OK-Zweig beobachtbar, weil ``detail`` hier nicht-``None``
        ist.
        """
        settings.MFA_ENFORCED_ROLES_IMMEDIATE = ()
        settings.MFA_ENFORCED_ROLES_GRACE = ("staff",)
        User.objects.filter(role__in=mfa_enforced_roles()).delete()

        user = _make_user(facility, User.Role.STAFF, "staff_ok")
        _confirmed_totp(user, "ok-device")

        checks = _mfa_checks()

        assert checks[0].key == "mfa_privileged_quote"
        assert checks[0].label == "MFA-Quote (MFA-pflichtige Rollen)"
        assert checks[0].category == "MFA"
        assert checks[0].status == ComplianceStatus.OK
        assert checks[0].message == "100 % der MFA-pflichtigen Rollen haben MFA aktiv."
        assert checks[0].detail == "1 von 1 MFA-pflichtigen Usern haben MFA aktiv."
        assert checks[0].action_hint is None


class TestWarningBranchFullWording:
    """``80 <= percent < 100`` — 4 von 5 erzwungenen Usern haben MFA aktiv."""

    def test_warning_check_full_wording(self, settings, facility):
        """Pins ``x__mfa_checks__mutmut_59/60/61/62/63`` sowie die im
        WARNING-Zweig unterscheidbaren gemeinsamen Feld-Mutanten
        ``_70/_71/_74/_75/_76/_82/_83/_84/_85``.

        ``_59`` setzt die WARNING-``message`` auf ``None``; ``_60``-``_63``
        verstümmeln den ``hint`` (``None``/``XX``-Präfix/lower/upper).
        ``_76``/``_83`` setzen bzw. entfernen ``action_hint`` — nur hier
        beobachtbar, weil der echte ``hint`` in diesem Zweig nicht-``None``
        ist (im OK-Zweig ist er es bereits von Natur aus, daher dort
        ununterscheidbar).
        """
        settings.MFA_ENFORCED_ROLES_IMMEDIATE = ()
        settings.MFA_ENFORCED_ROLES_GRACE = ("staff",)
        User.objects.filter(role__in=mfa_enforced_roles()).delete()

        users = [_make_user(facility, User.Role.STAFF, f"staff_warn_{i}") for i in range(5)]
        for user in users[:4]:
            _confirmed_totp(user, f"warn-device-{user.username}")

        checks = _mfa_checks()

        assert checks[0].key == "mfa_privileged_quote"
        assert checks[0].label == "MFA-Quote (MFA-pflichtige Rollen)"
        assert checks[0].category == "MFA"
        assert checks[0].status == ComplianceStatus.WARNING
        assert checks[0].message == "80 % MFA-Quote — Lücke schliessen."
        assert checks[0].detail == "4 von 5 MFA-pflichtigen Usern haben MFA aktiv."
        assert checks[0].action_hint == "Verbleibende User per Admin-UI zur MFA-Aktivierung anhalten."


class TestCriticalBranchFullWording:
    """``percent < 80`` — 2 von 3 erzwungenen Usern haben MFA aktiv (66,7 %)."""

    def test_critical_check_full_wording(self, settings, facility):
        """Pins ``x__mfa_checks__mutmut_65/66/67/68/69`` (CRITICAL-Wortlaut)
        sowie die Rundungs-/Formel-Mutanten ``_40/_42/_45/_46``.

        2 von 3 Usern ergeben exakt ``2 / 3 * 100 == 66.66666...``.
        Original: ``round(..., 1) == 66.7``. ``_40``/``_42``
        (``round(..., None)`` bzw. ``round(..., )``) runden auf die nächste
        Ganzzahl -> ``67`` (andere Anzeige, ``f"{67:g}"`` ohne
        Nachkommastelle). ``_45`` (``* 101`` statt ``* 100``) ergibt
        ``round(67.333..., 1) == 67.3``. ``_46`` (``round(..., 2)`` statt
        ``round(..., 1)``) ergibt ``66.67``. Alle vier weichen von ``66.7``
        in der ``message``/``detail`` ab.
        """
        settings.MFA_ENFORCED_ROLES_IMMEDIATE = ()
        settings.MFA_ENFORCED_ROLES_GRACE = ("staff",)
        User.objects.filter(role__in=mfa_enforced_roles()).delete()

        users = [_make_user(facility, User.Role.STAFF, f"staff_crit_{i}") for i in range(3)]
        for user in users[:2]:
            _confirmed_totp(user, f"crit-device-{user.username}")

        checks = _mfa_checks()

        assert checks[0].key == "mfa_privileged_quote"
        assert checks[0].label == "MFA-Quote (MFA-pflichtige Rollen)"
        assert checks[0].category == "MFA"
        assert checks[0].status == ComplianceStatus.CRITICAL
        assert checks[0].message == "Nur 66.7 % der MFA-pflichtigen Rollen haben MFA — Risiko."
        assert checks[0].detail == "2 von 3 MFA-pflichtigen Usern haben MFA aktiv."
        assert checks[0].action_hint == "mfa_enforced_facility_wide aktivieren oder MFA pro User erzwingen."


class TestNumeratorFilterIntegrity:
    """Zähler-Query muss Rolle UND Aktiv-Status filtern — nicht nur eins von beiden."""

    def test_numerator_ignores_role_and_active_filters_only_in_mutants(self, settings, facility):
        """Pins ``x__mfa_checks__mutmut_29/30``.

        ``_29`` entfernt ``user__role__in=enforced_roles`` aus der
        Zähler-Query, ``_30`` entfernt ``user__is_active=True``. Erzwungen
        ist hier ausschließlich STAFF. Ein STAFF-User ohne Gerät bildet den
        Nenner (1); ein ASSISTANT-User (nicht erzwungen, aber mit
        bestätigtem Gerät) und ein INAKTIVER STAFF-User (aus dem Nenner
        ausgeschlossen, aber mit bestätigtem Gerät) dürfen den Zähler NICHT
        erhöhen. Original: 0 von 1 -> CRITICAL. ``_29`` zählt den
        ASSISTANT mit (Rollenfilter fehlt), ``_30`` zählt den inaktiven
        STAFF mit (Aktiv-Filter fehlt) — beide landen bei 1 von 1 -> OK.
        """
        settings.MFA_ENFORCED_ROLES_IMMEDIATE = ()
        settings.MFA_ENFORCED_ROLES_GRACE = ("staff",)
        User.objects.filter(role__in=mfa_enforced_roles()).delete()
        User.objects.filter(role=User.Role.ASSISTANT).delete()

        _make_user(facility, User.Role.STAFF, "staff_no_device")
        unenforced_role_user = _make_user(facility, User.Role.ASSISTANT, "assistant_with_device")
        _confirmed_totp(unenforced_role_user, "assistant-device")
        inactive_user = _make_user(facility, User.Role.STAFF, "staff_inactive_device", is_active=False)
        _confirmed_totp(inactive_user, "inactive-device")

        checks = _mfa_checks()

        assert checks[0].status == ComplianceStatus.CRITICAL
        assert checks[0].detail == "0 von 1 MFA-pflichtigen Usern haben MFA aktiv."
