"""Settings-Guard: friert die MFA-Rollen-Enforcement-Posture ein (A3.1, Refs #1019).

In Produktion MUSS ``MFA_ENFORCE_PRIVILEGED_ROLES`` an sein (Default in
``base.py`` → von ``prod.py`` geerbt); dev/test/e2e MÜSSEN es aus haben
(``dev.py`` → kaskadiert über Vererbung nach ``test.py`` und ``e2e.py``).

Dieser Guard verhindert, dass ein versehentliches Flippen entweder das
Prod-Enforcement still abschaltet (Sicherheitsregression) oder die Test-/Dev-
Suite ungewollt MFA-pflichtig macht (~100+ Tests würden auf /mfa/setup/
umgeleitet). Stil analog zu den Prod-Settings-Guards in
``test_architecture_guards_views.py``.
"""

from django.conf import settings


def test_base_settings_enforce_privileged_roles_by_default():
    """``base.py`` (→ prod) erzwingt MFA für privilegierte Rollen ab Werk."""
    import anlaufstelle.settings.base as base_settings

    assert base_settings.MFA_ENFORCE_PRIVILEGED_ROLES is True


def test_running_dev_test_e2e_settings_disable_role_enforcement():
    """Die laufende Test-Config erbt ``dev.py`` → bewusst MFA-frei.

    Beweist zugleich, dass die dev-Abschaltung über Vererbung greift (test.py
    importiert dev.py; e2e.py ebenso, dort zusätzlich env-gegatet).
    """
    assert settings.MFA_ENFORCE_PRIVILEGED_ROLES is False
