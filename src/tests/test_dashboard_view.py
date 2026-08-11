"""Rollen-Einstieg ``/start/`` (Refs #920, Regel N3 aus #1801).

``/start/`` ist keine Seite mit vier Gesichtern mehr, sondern **ein**
Einstieg, der weiterleitet, wo ein Bereich die bessere Heimat ist. Das Muster
gab es schon (Fachkraft/Assistenz → Zeitstrom, Refs #1124); Refs #1813
vervollständigt es: Anwendungsbetreuung → ``/verwaltung/``,
Systemadministration → ``/system/``. Übrig bleibt eine eigene
Arbeitszentrale — die der Leitung.

Die Tests prüfen deshalb zwei Dinge: dass jede Rolle **irgendwo** landet (kein
403, keine leere Seite) und dass das Ziel die Inhalte trägt, die vorher auf
der Rollen-Seite standen.
"""

from __future__ import annotations

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestRoleDashboardView:
    def _login(self, client, user):
        client.force_login(user)

    def test_staff_redirected_to_zeitstrom(self, client, staff_user):
        self._login(client, staff_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 302
        assert response.url == reverse("core:zeitstrom")

    def test_lead_sees_lead_template(self, client, lead_user):
        """Die einzige Rolle mit eigener Arbeitszentrale."""
        self._login(client, lead_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 200
        assert b"lead-dashboard-cards" in response.content
        # "Löschanträge" UTF-8-kodiert
        assert "Löschanträge".encode() in response.content

    def test_facility_admin_redirected_to_verwaltung(self, client, admin_user):
        """Regel N3: die Verwaltungs-Übersicht ersetzt die fast leere Rollen-Seite."""
        self._login(client, admin_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 302
        assert response.url == reverse("core:verwaltung_uebersicht")

    def test_facility_admin_target_still_shows_the_role_cards(self, client, admin_user):
        """Kein Informationsverlust: die beiden Karten der alten Seite sind dort."""
        self._login(client, admin_user)
        content = client.get(reverse("core:dashboard"), follow=True).content.decode()
        assert 'data-testid="card-users-without-mfa"' in content
        assert 'data-testid="card-settings-warnings"' in content

    def test_super_admin_redirected_to_system(self, client, super_admin_user):
        """F5: der Systembereich IST der Einstieg dieser Rolle."""
        self._login(client, super_admin_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 302
        assert response.url == reverse("core:system_dashboard")

    def test_assistant_redirected_to_zeitstrom(self, client, assistant_user):
        self._login(client, assistant_user)
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 302
        assert response.url == reverse("core:zeitstrom")

    def test_unauthenticated_redirects_to_login(self, client):
        response = client.get(reverse("core:dashboard"))
        # Mixin sollte 302 zur Login-Page liefern
        assert response.status_code in (302, 403)


@pytest.mark.django_db
class TestSuperAdminDashboardAuditLinks:
    """Refs #1048: super_admin hat ``facility=None`` — ``core:audit_log``
    (``/audit/``) ist ueber ``FacilityAdminRequiredMixin`` gescopt und
    liefert fuer super_admin 403. Die Audit-Karten muessen deshalb auf die
    installationsweite View ``core:system_audit_list`` (``/system/audit/``)
    zeigen — die Zaehler im Kontext sind bereits installationsweit.

    Refs #1813: die Karten stehen seit dem Wegfall der System-Arbeitszentrale
    auf ``/system/`` (dorthin leitet ``/start/`` jetzt weiter). Die Aussage
    ist unveraendert, nur ihr Ort hat gewechselt.
    """

    def test_audit_cards_link_to_system_audit(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="card-audit-events"' in content
        assert 'data-testid="card-critical-events"' in content
        assert reverse("core:system_audit_list") in content
        # Exakter href-Match: "/audit/" ist Substring von "/system/audit/".
        assert f'href="{reverse("core:audit_log")}"' not in content

    def test_audit_card_target_returns_200_for_super_admin(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_list"))
        assert response.status_code == 200

    def test_users_card_shows_active_count(self, client, super_admin_user, staff_user):
        """Verify-Befund #1813: Die alte System-Arbeitszentrale trug die Karte
        „Benutzer aktiv (über alle Mandanten)" — beim Umzug nach ``/system/``
        darf die Aktiv-Zahl nicht verloren gehen. ``total_users`` zählt alle
        Konten inkl. deaktivierter; die Karte weist beide Werte aus."""
        staff_user.is_active = False
        staff_user.save(update_fields=["is_active"])
        client.force_login(super_admin_user)
        content = client.get(reverse("core:system_dashboard")).content.decode()
        assert 'data-testid="card-users-active"' in content
        from core.models import User

        active = User.objects.filter(is_active=True).count()
        total = User.objects.count()
        assert total == active + 1
        assert f"davon {active} aktiv" in content
