"""Art.-18-Sperrvermerk — Einschraenkung der Verarbeitung (Refs #2045).

Art. 18 DSGVO ist das **Gegenteil** des Legal Hold: der Hold blockiert die
Loeschung (Art. 17 Abs. 3), die Einschraenkung blockiert die *Nutzung*. Die
Grenze ist deshalb ein Schreib-, kein Loeschgate — Speichern bleibt erlaubt
(Art. 18 Abs. 2: „duerfen … nur gespeichert werden"), inhaltliche Aenderung
nicht. Einzige Ausnahme ist der Berichtigungsworkflow (ADR-041): er ist genau
der Weg, ueber den die bestrittene Richtigkeit geklaert wird.

Die Tests spiegeln bewusst das Muster von ``resolve_writable_client``
(Refs #1771): dieselbe Doppelung Service-Guard + Formular-Resolver, damit auch
Nicht-Formular-Aufrufer (Offline-Replay, Import, kuenftige Commands) die Grenze
passieren muessen.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import AuditLog, Client
from core.services.client import resolve_writable_client, update_client
from core.services.client.restriction import (
    PROCESSING_RESTRICTED_MESSAGE,
    lift_processing_restriction,
    restrict_processing,
)


def _login(client, user):
    client.force_login(user)
    return client


def _restrict(client_obj, user, reason=None):
    return restrict_processing(
        client_obj,
        user,
        reason=reason or Client.RestrictionReason.ACCURACY_DISPUTED,
    )


# ---------------------------------------------------------------------------
# Setzen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRestrictProcessing:
    def test_sets_flag_reason_and_provenance(self, client_identified, staff_user):
        _restrict(client_identified, staff_user)
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is True
        assert client_identified.processing_restriction_reason == Client.RestrictionReason.ACCURACY_DISPUTED
        assert client_identified.processing_restricted_by == staff_user
        assert client_identified.processing_restricted_at is not None

    def test_writes_audit_row(self, client_identified, staff_user):
        _restrict(client_identified, staff_user)
        entry = AuditLog.objects.get(
            action=AuditLog.Action.PROCESSING_RESTRICTED,
            target_id=str(client_identified.pk),
        )
        assert entry.user == staff_user
        assert entry.detail["reason"] == Client.RestrictionReason.ACCURACY_DISPUTED

    def test_is_idempotent_without_second_audit_row(self, client_identified, staff_user):
        _restrict(client_identified, staff_user)
        _restrict(client_identified, staff_user)
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.PROCESSING_RESTRICTED,
                target_id=str(client_identified.pk),
            ).count()
            == 1
        )

    def test_assistant_may_not_restrict(self, client_identified, assistant_user):
        with pytest.raises(ValidationError):
            _restrict(client_identified, assistant_user)
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is False

    def test_invalid_reason_rejected(self, client_identified, staff_user):
        with pytest.raises(ValidationError):
            restrict_processing(client_identified, staff_user, reason="weil-ich-es-kann")


# ---------------------------------------------------------------------------
# Write-Gate (Spiegel von ``resolve_writable_client``)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWriteGate:
    def test_update_client_blocked(self, client_identified, staff_user):
        _restrict(client_identified, staff_user)
        with pytest.raises(ValidationError) as exc:
            update_client(client_identified, staff_user, notes="Neue Notiz")
        assert str(PROCESSING_RESTRICTED_MESSAGE) in "; ".join(exc.value.messages)
        client_identified.refresh_from_db()
        assert client_identified.notes != "Neue Notiz"

    def test_update_client_allowed_for_rectification_workflow(self, client_identified, staff_user):
        """Der Ausstieg ist seit dem Cross-Review (Befund 4) ein modul-privater
        Kontext statt eines ``allow_restricted``-Arguments — die ANFORDERUNG hat
        sich geaendert, nicht das erwartete Verhalten: die Berichtigung schreibt
        weiterhin, jeder andere Aufrufer weiterhin nicht."""
        from core.services.client.restriction import _rectification_write

        _restrict(client_identified, staff_user)
        with _rectification_write():
            update_client(client_identified, staff_user, notes="Berichtigt")
        client_identified.refresh_from_db()
        assert client_identified.notes == "Berichtigt"

    def test_resolve_writable_client_blocked(self, facility, client_identified, staff_user):
        _restrict(client_identified, staff_user)
        with pytest.raises(ValidationError):
            resolve_writable_client(facility, client_identified.pk, missing_error="fehlt")

    def test_resolve_writable_client_passes_when_unrestricted(self, facility, client_identified):
        assert resolve_writable_client(facility, client_identified.pk, missing_error="fehlt") == client_identified

    def test_event_form_rejects_restricted_client(self, facility, client_identified, staff_user, doc_type_contact):
        """Der Formular-Resolver ist derselbe Pfad — Ereignisse an einer
        eingeschraenkten Person sind neue inhaltliche Verarbeitung."""
        from core.forms.events import EventMetaForm

        _restrict(client_identified, staff_user)
        form = EventMetaForm(
            data={"client": str(client_identified.pk), "occurred_at": "2026-05-03 14:32"},
            facility=facility,
            user=staff_user,
        )
        form.is_valid()
        assert "client" in form.errors


# ---------------------------------------------------------------------------
# Aufhebung — vier-aeugig ODER durch die Leitung
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLiftRestriction:
    def test_setter_may_not_lift_alone(self, client_identified, staff_user):
        _restrict(client_identified, staff_user)
        with pytest.raises(ValidationError):
            lift_processing_restriction(client_identified, staff_user)
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is True

    def test_second_staff_may_lift(self, facility, client_identified, staff_user):
        from core.models import User

        other = User.objects.create_user(
            username=f"staff2-{facility.pk.hex[:8]}",
            password="x",
            facility=facility,
            role=User.Role.STAFF,
        )
        _restrict(client_identified, staff_user)
        lift_processing_restriction(client_identified, other)
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is False
        assert client_identified.processing_restriction_reason == ""
        assert client_identified.processing_restricted_by is None
        assert client_identified.processing_restricted_at is None

    def test_lead_may_lift_even_as_setter(self, client_identified, lead_user):
        _restrict(client_identified, lead_user)
        lift_processing_restriction(client_identified, lead_user)
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is False

    def test_lift_writes_audit_row(self, client_identified, lead_user):
        _restrict(client_identified, lead_user)
        lift_processing_restriction(client_identified, lead_user)
        assert AuditLog.objects.filter(
            action=AuditLog.Action.PROCESSING_RESTRICTION_LIFTED,
            target_id=str(client_identified.pk),
        ).exists()

    def test_lift_without_restriction_rejected(self, client_identified, lead_user):
        with pytest.raises(ValidationError):
            lift_processing_restriction(client_identified, lead_user)

    def test_assistant_may_not_lift(self, client_identified, staff_user, assistant_user):
        _restrict(client_identified, staff_user)
        with pytest.raises(ValidationError):
            lift_processing_restriction(client_identified, assistant_user)


# ---------------------------------------------------------------------------
# Berichtigungsworkflow — die eine Ausnahme
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRectificationException:
    def test_approve_applies_despite_restriction(self, client_identified, staff_user, lead_user):
        from core.services.client.rectification import approve_rectification, request_rectification

        rr = request_rectification(client_identified, staff_user, "notes", "Richtig", "bestritten")
        _restrict(client_identified, staff_user)
        approve_rectification(rr, lead_user)
        client_identified.refresh_from_db()
        assert client_identified.notes == "Richtig"


# ---------------------------------------------------------------------------
# UI — dauerhaftes Banner, Listen-Kennzeichnung, Setzen/Aufheben
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestViews:
    def test_detail_shows_permanent_banner(self, client, client_identified, staff_user):
        _restrict(client_identified, staff_user)
        _login(client, staff_user)
        response = client.get(reverse("core:client_detail", args=[client_identified.pk]))
        assert response.status_code == 200
        assert b"client-processing-restricted-banner" in response.content

    def test_detail_without_restriction_has_no_banner(self, client, client_identified, staff_user):
        _login(client, staff_user)
        response = client.get(reverse("core:client_detail", args=[client_identified.pk]))
        assert b"client-processing-restricted-banner" not in response.content

    def test_list_marks_restricted_client(self, client, client_identified, staff_user):
        _restrict(client_identified, staff_user)
        _login(client, staff_user)
        response = client.get(reverse("core:client_list"))
        assert b"client-row-processing-restricted-badge" in response.content

    def test_post_restrict_sets_flag(self, client, client_identified, staff_user):
        _login(client, staff_user)
        response = client.post(
            reverse("core:client_restrict_processing", args=[client_identified.pk]),
            {"reason": Client.RestrictionReason.ACCURACY_DISPUTED},
        )
        assert response.status_code == 302
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is True

    def test_post_lift_by_lead(self, client, client_identified, staff_user, lead_user):
        _restrict(client_identified, staff_user)
        _login(client, lead_user)
        response = client.post(reverse("core:client_lift_processing_restriction", args=[client_identified.pk]))
        assert response.status_code == 302
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is False

    def test_post_lift_by_setter_is_rejected(self, client, client_identified, staff_user):
        _restrict(client_identified, staff_user)
        _login(client, staff_user)
        client.post(reverse("core:client_lift_processing_restriction", args=[client_identified.pk]))
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is True

    def test_assistant_may_not_reach_restrict_endpoint(self, client, client_identified, assistant_user):
        _login(client, assistant_user)
        response = client.post(
            reverse("core:client_restrict_processing", args=[client_identified.pk]),
            {"reason": Client.RestrictionReason.ACCURACY_DISPUTED},
        )
        assert response.status_code in (302, 403)
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is False


# ---------------------------------------------------------------------------
# Auto-Vorschlag am Berichtigungsantrag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRectificationOptIn:
    def test_opt_in_restricts_processing(self, client, client_identified, staff_user):
        _login(client, staff_user)
        response = client.post(
            reverse("core:rectification_create", args=[client_identified.pk]),
            {
                "field_path": "notes",
                "new_value": "Korrigiert",
                "reason": "Falsch protokolliert",
                "restrict_processing": "on",
            },
        )
        assert response.status_code == 302
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is True
        assert client_identified.processing_restriction_reason == Client.RestrictionReason.ACCURACY_DISPUTED

    def test_without_opt_in_nothing_is_restricted(self, client, client_identified, staff_user):
        _login(client, staff_user)
        client.post(
            reverse("core:rectification_create", args=[client_identified.pk]),
            {"field_path": "notes", "new_value": "Korrigiert", "reason": "Falsch protokolliert"},
        )
        client_identified.refresh_from_db()
        assert client_identified.processing_restricted is False

    def test_form_offers_the_opt_in(self, client, client_identified, staff_user):
        _login(client, staff_user)
        response = client.get(reverse("core:rectification_create", args=[client_identified.pk]))
        assert "restrict_processing" in response.context["form"].fields
