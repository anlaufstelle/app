"""Gemeinsame Nav-Badge-Semantik + Badge-Integration (Refs #1984, ADR-046).

Das Partial ``partials/_nav_badge.html`` traegt die gemeinsame Semantik aller
Navigations-Zaehler: 0/fehlend rendert NICHTS, die Anzeige ist auf ``99+``
gekappt, der volle zugaengliche Name (inkl. echter Zahl) steht als
``sr-only``-Text daneben (die Zahl selbst ist ``aria-hidden``). Die
Integrationstests pinnen, dass Sidebar-Hub, Datenschutz-Reiterzeile und der
gehaertete Aufgaben-Badge das Partial tatsaechlich speisen.
"""

import uuid

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from core.models import Client, RectificationRequest
from core.models.workitem import DeletionRequest

pytestmark = pytest.mark.django_db


def _render_badge(**ctx):
    return render_to_string("partials/_nav_badge.html", ctx)


class TestNavBadgePartial:
    def test_zero_renders_nothing(self):
        assert _render_badge(count=0, kind="deletion").strip() == ""

    def test_missing_count_renders_nothing(self):
        assert _render_badge(kind="deletion").strip() == ""

    def test_small_count_shows_number_and_accessible_name(self):
        html = _render_badge(count=5, kind="deletion")
        assert ">5<" in html.replace("\n", "")
        assert 'aria-hidden="true"' in html
        assert "sr-only" in html
        assert "5 offene" in html  # voller zugaenglicher Name inkl. Zahl

    def test_large_count_capped_but_accessible_name_keeps_full_value(self):
        html = _render_badge(count=150, kind="rectification")
        assert "99+" in html
        assert "150" in html  # sr-only traegt den echten Wert

    def test_singular_plural(self):
        singular = _render_badge(count=1, kind="deletion")
        assert "1 offener Löschantrag" in singular
        plural = _render_badge(count=2, kind="deletion")
        assert "2 offene Löschanträge" in plural

    def test_english_translation_active(self):
        """EN-Katalog traegt die Badge-Wordings (kompilierte .mo, Refs #1984)."""
        from django.utils.translation import override

        with override("en"):
            html = _render_badge(count=2, kind="deletion")

        assert "2 open deletion requests" in html

    def test_alert_variant_and_suffix(self):
        html = _render_badge(count=3, kind="workitems_overdue", variant="alert", suffix="!")
        assert "bg-red-500" in html
        assert "3!" in html.replace("\n", "").replace(" ", "")


def _pending_deletion(facility, user):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=uuid.uuid4(),
        reason="Badge-Test",
        requested_by=user,
        status=DeletionRequest.Status.PENDING,
    )


def _pending_rectification(facility, client_obj, user, field_path="pseudonym"):
    return RectificationRequest.objects.create(
        facility=facility,
        target_client=client_obj,
        field_path=field_path,
        old_value="Alt",
        new_value="Neu",
        reason="Badge-Test",
        requested_by=user,
        status=RectificationRequest.Status.PENDING,
    )


class TestNavBadgeIntegration:
    def test_sidebar_hub_shows_datenschutz_sum(self, client, facility, lead_user, staff_user, client_identified):
        _pending_deletion(facility, staff_user)
        _pending_rectification(facility, client_identified, staff_user)
        client.force_login(lead_user)

        html = client.get("/").content.decode()

        # Desktop- und Mobil-Navigation liegen GLEICHZEITIG im DOM (nur per
        # CSS umgeschaltet) — die testids muessen je Standort eindeutig sein
        # (Konvention testid/mobile_testid, test_nav_registry).
        assert html.count('data-testid="nav-badge-datenschutz"') == 1
        assert html.count('data-testid="mobile-nav-badge-datenschutz"') == 1
        assert "2 offene Prüfvorgänge" in html

    def test_subnav_tabs_show_individual_counts(self, client, facility, lead_user, staff_user, client_identified):
        _pending_deletion(facility, staff_user)
        _pending_rectification(facility, client_identified, staff_user)
        client.force_login(lead_user)

        html = client.get("/deletion-requests/").content.decode()

        assert 'data-testid="nav-badge-deletion"' in html
        assert "1 offener Löschantrag" in html
        assert 'data-testid="nav-badge-rectification"' in html
        assert "1 offener Berichtigungsantrag" in html

    def test_staff_without_access_sees_no_badges(self, client, facility, staff_user, lead_user, client_identified):
        """Kein Existenz-Leak: Fachkraft ohne Antrag sieht weder Hub- noch
        Reiter-Badge (der Datenschutz-Hub ist fuer sie ohnehin unsichtbar)."""
        _pending_deletion(facility, lead_user)
        _pending_rectification(facility, client_identified, lead_user)
        client.force_login(staff_user)

        html = client.get("/").content.decode()

        assert "nav-badge-datenschutz" not in html
        assert "nav-badge-deletion" not in html

    def test_workitem_badge_uses_shared_semantics(self, client, facility, staff_user):
        """Haertung des bestehenden Aufgaben-Badges: 99+-Kappung + voller
        zugaenglicher Name (vorher nackte Zahl ohne aria-Anbindung)."""
        from core.models import WorkItem

        for i in range(101):
            WorkItem.objects.create(
                facility=facility,
                title=f"Badge-Aufgabe {i}",
                status=WorkItem.Status.OPEN,
                assigned_to=staff_user,
            )
        client.force_login(staff_user)

        html = client.get("/").content.decode()

        assert "99+" in html
        assert "101 offene Aufgaben" in html
        # Sidebar (Desktop) und Bottom-Bar (mobil) tragen standort-eigene
        # testids — keine Selektor-Mehrdeutigkeit (Playwright strict mode).
        assert html.count('data-testid="nav-badge-workitems_open"') == 1


class TestBadgeMatchesListContext:
    """Integrator-Auflage (#1984): Zaehler und Zielansicht PAARWEISE pinnen —
    der Badge darf nie eine andere Zahl tragen, als die gerenderte Liste
    Zeilen zeigt. Beide Werte kommen aus demselben Response-Kontext (Liste
    aus der View, Zaehler aus dem review_counts-Context-Processor)."""

    def test_deletion_badge_equals_list_for_lead(self, client, facility, lead_user, staff_user):
        _pending_deletion(facility, staff_user)
        _pending_deletion(facility, lead_user)
        client.force_login(lead_user)

        response = client.get(reverse("core:deletion_request_list"))

        assert len(response.context["pending_requests"]) == 2
        assert response.context["open_deletion_requests_count"] == 2

    def test_deletion_badge_equals_list_for_requester(self, client, facility, staff_user, lead_user):
        """Der Antragsteller-Fall erstmals durch die GERENDERTE Seite: eigene
        Zeile + eigener Zaehler, der fremde Antrag erscheint in keinem von
        beiden (Refs #1736 B)."""
        _pending_deletion(facility, staff_user)
        _pending_deletion(facility, lead_user)
        client.force_login(staff_user)

        response = client.get(reverse("core:deletion_request_list"))

        assert len(response.context["pending_requests"]) == 1
        assert response.context["open_deletion_requests_count"] == 1

    def test_deletion_badge_equals_list_for_confirmer(self, client, facility, staff_user, lead_user):
        """#1053-Genehmiger-Pool auch am Paar: volle Liste, voller Zaehler."""
        staff_user.can_confirm_deletion = True
        staff_user.save(update_fields=["can_confirm_deletion"])
        _pending_deletion(facility, lead_user)
        client.force_login(staff_user)

        response = client.get(reverse("core:deletion_request_list"))

        assert len(response.context["pending_requests"]) == 1
        assert response.context["open_deletion_requests_count"] == 1

    def test_rectification_badge_equals_list_for_lead(self, client, facility, lead_user, staff_user, client_identified):
        """Auch die Ausschluss-Faelle (widerrufene Zielperson) laufen durchs
        Paar — Liste und Zaehler teilen dieselbe pending-Definition."""
        _pending_rectification(facility, client_identified, staff_user)
        revoked = Client.objects.create(facility=facility, pseudonym="Widerrufen-02", is_deleted=True)
        _pending_rectification(facility, revoked, staff_user)
        client.force_login(lead_user)

        response = client.get(reverse("core:rectification_list"))

        assert len(response.context["pending_requests"]) == 1
        assert response.context["open_rectification_requests_count"] == 1
