"""Mutation-Followup-Tests für ``core.services.offline`` — Bundle-Envelope.

Refs #930. Sub-File aus ``test_mutation_followup_offline``;
enthält die Test-Klassen ``TestBundleEnvelope``, ``TestBundleClientFields``
und ``TestNotesVisibilityGate`` — also Schema-Version/TTL, Client-Felder
und das Notes-Visibility-Gate.
"""

from __future__ import annotations

import pytest

from core.services.system import (
    BUNDLE_SCHEMA_VERSION,
    BUNDLE_TTL_SECONDS,
    build_client_offline_bundle,
)

# ---------------------------------------------------------------------------
# build_client_offline_bundle — Top-Level-Aggregat (33 Survivors)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBundleEnvelope:
    """Schema-Version, TTL, generated_at/expires_at — Konstanten-Mutationen."""

    def test_schema_version_constant_is_set(self, facility, client_identified, staff_user):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        # Mutation BUNDLE_SCHEMA_VERSION 1→2 oder Verschmelzung mit anderem Key.
        assert bundle["schema_version"] == BUNDLE_SCHEMA_VERSION == 1

    def test_ttl_is_48_hours_in_seconds(self, facility, client_identified, staff_user):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        # Mutation 48*3600 → 24*3600 / Multiplikator weglassen.
        assert bundle["ttl"] == BUNDLE_TTL_SECONDS
        assert bundle["ttl"] == 48 * 3600

    def test_expires_at_is_generated_at_plus_ttl(self, facility, client_identified, staff_user):
        """Mutation ``generated_at + timedelta(seconds=BUNDLE_TTL_SECONDS)``
        → ``-`` oder Vertauschung der Operanden würde den Abstand killen."""
        from datetime import datetime

        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        generated = datetime.fromisoformat(bundle["generated_at"])
        expires = datetime.fromisoformat(bundle["expires_at"])
        delta = (expires - generated).total_seconds()
        # Toleranz fuer datetime-Aufloesung (zwei now()-Aufrufe sind nicht identisch
        # — wir lesen ``generated_at`` aus dem Bundle, das im Service als
        # ``generated_at`` gemerged wurde und sowohl in ``generated_at`` als
        # auch in ``expires_at`` benutzt wird).
        assert abs(delta - BUNDLE_TTL_SECONDS) < 1.0

    def test_top_level_keys_present(self, facility, client_identified, staff_user):
        """Mutation ``"events": [...]`` → key weglassen oder umbenennen."""
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        expected_keys = {
            "schema_version",
            "generated_at",
            "ttl",
            "expires_at",
            "client",
            "cases",
            "workitems",
            # Refs #1398: zuweisbare Nutzer:innen fuer den Offline-Assign-Picker.
            "assignable_users",
            "events",
            "document_types",
        }
        assert set(bundle.keys()) == expected_keys


@pytest.mark.django_db
class TestBundleClientFields:
    """``bundle["client"]`` enthält genau die acht spezifizierten Felder."""

    def test_client_dict_contains_all_required_keys(self, facility, client_identified, staff_user):
        """Mutation eines Feld-Keys (``pseudonym`` → ``pseudo`` etc.) wird
        gefangen, weil jeder Key explizit geprueft wird."""
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert set(bundle["client"].keys()) == {
            "pk",
            "pseudonym",
            "contact_stage",
            "contact_stage_display",
            "age_cluster",
            "age_cluster_display",
            "notes",
            "is_active",
        }

    def test_client_pk_is_stringified_uuid(self, facility, client_identified, staff_user):
        """Mutation ``str(client.pk)`` → ``client.pk`` würde UUID-Objekt liefern."""
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["client"]["pk"] == str(client_identified.pk)
        assert isinstance(bundle["client"]["pk"], str)

    def test_client_pseudonym_matches_source(self, facility, client_identified, staff_user):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["client"]["pseudonym"] == client_identified.pseudonym

    def test_client_contact_stage_and_display_both_set(self, facility, client_qualified, staff_user):
        """Mutation ``get_contact_stage_display()`` → ``contact_stage`` würde
        identische Werte liefern."""
        bundle = build_client_offline_bundle(staff_user, facility, client_qualified)
        assert bundle["client"]["contact_stage"] == "qualified"
        assert bundle["client"]["contact_stage_display"] == "Qualifiziert"
        assert bundle["client"]["contact_stage"] != bundle["client"]["contact_stage_display"]

    def test_client_age_cluster_and_display(self, facility, staff_user):
        from core.models import Client

        c = Client.objects.create(
            facility=facility,
            pseudonym="Age-1",
            age_cluster=Client.AgeCluster.AGE_18_26,
            created_by=staff_user,
        )
        bundle = build_client_offline_bundle(staff_user, facility, c)
        assert bundle["client"]["age_cluster"] == "18_26"
        assert bundle["client"]["age_cluster_display"] == "18–26"

    def test_client_is_active_passes_through(self, facility, client_identified, staff_user):
        client_identified.is_active = False
        client_identified.save(update_fields=["is_active"])
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["client"]["is_active"] is False


@pytest.mark.django_db
class TestNotesVisibilityGate:
    """Notes-Gate: ``is_staff_or_above`` schaltet ``client.notes`` frei.

    Mutation der Negation, des Property-Namens oder des Fallback-``""``
    werden gefangen.
    """

    def test_assistant_sees_empty_notes(self, facility, client_identified, assistant_user):
        client_identified.notes = "geheim"
        client_identified.save(update_fields=["notes"])
        bundle = build_client_offline_bundle(assistant_user, facility, client_identified)
        # Mutation ``"" → "geheim"`` würde Klartext leaken.
        assert bundle["client"]["notes"] == ""

    def test_staff_sees_full_notes(self, facility, client_identified, staff_user):
        client_identified.notes = "Aktennotiz"
        client_identified.save(update_fields=["notes"])
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["client"]["notes"] == "Aktennotiz"

    def test_lead_sees_notes(self, facility, client_identified, lead_user):
        client_identified.notes = "Lead-Sicht"
        client_identified.save(update_fields=["notes"])
        bundle = build_client_offline_bundle(lead_user, facility, client_identified)
        assert bundle["client"]["notes"] == "Lead-Sicht"

    def test_facility_admin_sees_notes(self, facility, client_identified, admin_user):
        client_identified.notes = "Admin-Sicht"
        client_identified.save(update_fields=["notes"])
        bundle = build_client_offline_bundle(admin_user, facility, client_identified)
        assert bundle["client"]["notes"] == "Admin-Sicht"

    def test_user_without_property_falls_back_to_empty(self, facility, client_identified):
        """Mutation ``hasattr(user, "is_staff_or_above")`` → ``True`` oder
        Removal des Fallbacks ``False`` würde den Branch killen."""

        class _StubUser:
            is_authenticated = True
            role = "assistant"
            pk = 999

        client_identified.notes = "wuerde-leaken"
        client_identified.save(update_fields=["notes"])
        bundle = build_client_offline_bundle(_StubUser(), facility, client_identified)
        # Ohne is_staff_or_above-Property → notes_visible False → leerer String.
        assert bundle["client"]["notes"] == ""
