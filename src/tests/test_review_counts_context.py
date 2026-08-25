"""Nav-Badges für offene Prüf-/Arbeitsvorgänge — ``review_counts`` (Refs #1984).

Der Context-Processor liefert die datensparsamen Zähler für die
Navigations-Badges (ADR-046: In-App-Zähler statt Notification-Modell) und
MUSS exakt die Sichtmengen der Zielansichten spiegeln:

- Löschanträge: ``DeletionRequestListView``-Menge — Leitung/Genehmiger:innen
  sehen alle PENDING der Einrichtung, reine Antragsteller:innen nur die
  eigenen, alle anderen bekommen keinen Zähler (kein Existenz-Leak).
- Berichtigungen: die ``pending``-Definition der ``RectificationListView``
  (Leitung/Anwendungsbetreuung; widerrufene/inaktive Zielpersonen zählen
  nicht — sie sind kein prüfbarer Vorgang mehr).
"""

import uuid

import pytest
from django.test import RequestFactory

from core.context_processors import review_counts
from core.models import Client, RectificationRequest
from core.models.workitem import DeletionRequest

pytestmark = pytest.mark.django_db


def _make_request(user, facility, *, hx=False):
    headers = {"HTTP_HX_REQUEST": "true"} if hx else {}
    request = RequestFactory().get("/", **headers)
    request.user = user
    request.current_facility = facility
    return request


def _pending_deletion(facility, user):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=uuid.uuid4(),
        reason="Badge-Test",
        requested_by=user,
        status=DeletionRequest.Status.PENDING,
    )


def _pending_rectification(facility, client, user, **overrides):
    defaults = {
        "facility": facility,
        "target_client": client,
        "field_path": "pseudonym",
        "old_value": "Alt",
        "new_value": "Neu",
        "reason": "Badge-Test",
        "requested_by": user,
        "status": RectificationRequest.Status.PENDING,
    }
    defaults.update(overrides)
    return RectificationRequest.objects.create(**defaults)


class TestDeletionRequestCount:
    def test_lead_sees_full_pending_count(self, facility, lead_user, staff_user):
        _pending_deletion(facility, staff_user)
        _pending_deletion(facility, lead_user)

        ctx = review_counts(_make_request(lead_user, facility))

        assert ctx["open_deletion_requests_count"] == 2

    def test_requester_only_sees_own_pending(self, facility, staff_user, second_facility_user, lead_user):
        """Reine Antragsteller:innen (Zugang nur via eigenem Antrag) zählen
        ausschließlich die eigenen offenen Anträge — fremde Anträge dürfen
        nicht einmal als Zahl durchschimmern (Refs #1736 B)."""
        _pending_deletion(facility, staff_user)
        _pending_deletion(facility, lead_user)

        ctx = review_counts(_make_request(staff_user, facility))

        assert ctx["open_deletion_requests_count"] == 1

    def test_decided_requests_do_not_count(self, facility, lead_user, staff_user):
        _pending_deletion(facility, staff_user)
        approved = _pending_deletion(facility, staff_user)
        approved.status = DeletionRequest.Status.APPROVED
        approved.save(update_fields=["status"])

        ctx = review_counts(_make_request(lead_user, facility))

        assert ctx["open_deletion_requests_count"] == 1

    def test_user_without_access_gets_no_deletion_key(self, facility, staff_user, lead_user):
        """Fachkraft ohne eigenen Antrag hat keinen Listen-Zugang — der
        Zähler existiert für sie nicht (kein Existenz-Leak fremder Anträge)."""
        _pending_deletion(facility, lead_user)

        ctx = review_counts(_make_request(staff_user, facility))

        assert "open_deletion_requests_count" not in ctx

    def test_foreign_facility_requests_do_not_count(self, facility, other_facility, lead_user, second_facility_user):
        _pending_deletion(other_facility, second_facility_user)

        ctx = review_counts(_make_request(lead_user, facility))

        assert ctx.get("open_deletion_requests_count", 0) == 0


class TestRectificationRequestCount:
    def test_lead_sees_pending_count(self, facility, lead_user, staff_user, client_identified):
        # Pro (Person, Feld) erlaubt die DB nur EINEN offenen Antrag
        # (unique_pending_rectification_request) — zweiter Antrag anderes Feld.
        _pending_rectification(facility, client_identified, staff_user)
        _pending_rectification(facility, client_identified, staff_user, field_path="notes")

        ctx = review_counts(_make_request(lead_user, facility))

        assert ctx["open_rectification_requests_count"] == 2

    def test_admin_sees_pending_count(self, facility, admin_user, staff_user, client_identified):
        _pending_rectification(facility, client_identified, staff_user)

        ctx = review_counts(_make_request(admin_user, facility))

        assert ctx["open_rectification_requests_count"] == 1

    def test_staff_gets_no_rectification_key(self, facility, staff_user, client_identified):
        """Berichtigungen prüfen ist Leitungsarbeit — Fachkraft bekommt den
        Zähler nicht (Gate identisch zur ``RectificationListView``)."""
        _pending_rectification(facility, client_identified, staff_user)

        ctx = review_counts(_make_request(staff_user, facility))

        assert "open_rectification_requests_count" not in ctx

    def test_revoked_or_inactive_target_does_not_count(self, facility, lead_user, staff_user, client_identified):
        """Widerrufene/inaktive Zielpersonen sind kein prüfbarer Vorgang mehr
        (``RectificationReviewView._pending``) — exakt die pending-Definition
        der Liste, sonst zeigt der Badge dauerhaft eine Zahl ohne Zeile."""
        _pending_rectification(facility, client_identified, staff_user)
        revoked = Client.objects.create(facility=facility, pseudonym="Widerrufen-01", is_deleted=True)
        _pending_rectification(facility, revoked, staff_user)
        inactive = Client.objects.create(facility=facility, pseudonym="Inaktiv-01", is_active=False)
        _pending_rectification(facility, inactive, staff_user)

        ctx = review_counts(_make_request(lead_user, facility))

        assert ctx["open_rectification_requests_count"] == 1

    def test_decided_requests_do_not_count(self, facility, lead_user, staff_user, client_identified):
        _pending_rectification(facility, client_identified, staff_user)
        _pending_rectification(facility, client_identified, staff_user, status=RectificationRequest.Status.REJECTED)

        ctx = review_counts(_make_request(lead_user, facility))

        assert ctx["open_rectification_requests_count"] == 1


class TestProcessorPlumbing:
    def test_datenschutz_sum_key(self, facility, lead_user, staff_user, client_identified):
        """Der Hub-Anker zeigt die Summe beider Zähler (ein Key, im
        Processor berechnet — kein Template-Addieren über fehlende Keys)."""
        _pending_deletion(facility, staff_user)
        _pending_rectification(facility, client_identified, staff_user)

        ctx = review_counts(_make_request(lead_user, facility))

        assert ctx["datenschutz_review_count"] == 2

    def test_hx_request_skips_queries(self, facility, lead_user, django_assert_num_queries):
        """HTMX-Partials rendern die Navigation nie — keine Badge-Queries
        (Muster ``workitem_counts``)."""
        with django_assert_num_queries(0):
            ctx = review_counts(_make_request(lead_user, facility, hx=True))

        assert ctx == {}

    def test_anonymous_and_missing_facility_are_noops(self, facility, lead_user):
        from django.contrib.auth.models import AnonymousUser

        anon_request = RequestFactory().get("/")
        anon_request.user = AnonymousUser()
        assert review_counts(anon_request) == {}

        request = RequestFactory().get("/")
        request.user = lead_user
        request.current_facility = None
        assert review_counts(request) == {}

    def test_lead_costs_exactly_two_queries(self, facility, lead_user, django_assert_num_queries):
        """Query-Budget: einmal pro Request (Context-Processor), zwei COUNTs
        für Leitung — die Nav-Gates selbst kosten hier nichts
        (Rollen-Kurzschluss bzw. geteilte ``cached_property``, Refs #1755)."""
        request = _make_request(lead_user, facility)

        with django_assert_num_queries(2):
            review_counts(request)
