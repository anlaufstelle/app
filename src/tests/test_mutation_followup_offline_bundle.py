"""Mutation-Followup-Tests für ``core.services.offline`` — Bundle-Envelope.

Refs #930. Sub-File aus ``test_mutation_followup_offline``;
enthält die Test-Klassen ``TestBundleEnvelope``, ``TestBundleClientFields``
und ``TestNotesVisibilityGate`` — also Schema-Version/TTL, Client-Felder
und das Notes-Visibility-Gate.
"""

from __future__ import annotations

import pytest
from django.http import Http404

from core.services.system import (
    BUNDLE_SCHEMA_VERSION,
    BUNDLE_TTL_SECONDS,
    build_client_offline_bundle,
)
from tests._standard_text import set_record_text

# ---------------------------------------------------------------------------
# build_client_offline_bundle — Top-Level-Aggregat (33 Survivors)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBundleEnvelope:
    """Schema-Version, TTL, generated_at/expires_at — Konstanten-Mutationen."""

    def test_schema_version_constant_is_set(self, facility, client_identified, staff_user):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        # Refs #1529/#1499: Schema-Bump 1->2 (Purge der Alt-v1-Bundles).
        # Mutation BUNDLE_SCHEMA_VERSION 2->x oder Verschmelzung mit anderem Key.
        assert bundle["schema_version"] == BUNDLE_SCHEMA_VERSION == 3

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
    """The person envelope exposes exactly the declared metadata and typed fields."""

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
            "data_fields",
            "field_metadata",
            "is_active",
            # Refs #1529/#1499: Letzter-Kontakt-Spaltenwert fuer die
            # Offline-Personenliste (Max(events__occurred_at), wie ClientListView).
            "last_contact",
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
        assert bundle["client"]["contact_stage_display"] == "Mit Zusatzangaben"
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

    def test_bundle_requires_active_client(self, facility, client_identified, staff_user):
        assert build_client_offline_bundle(staff_user, facility, client_identified)["client"]["is_active"] is True
        client_identified.is_active = False
        client_identified.save(update_fields=["is_active"])
        with pytest.raises(Http404):
            build_client_offline_bundle(staff_user, facility, client_identified)


@pytest.mark.django_db
class TestNotesVisibilityGate:
    """Typed person values in the offline bundle are filtered per field and type
    sensitivity for the user's role.

    Mutations of the negation, the property name or the empty fallback are
    caught.
    """

    def test_assistant_sees_empty_notes(self, facility, client_identified, assistant_user):
        set_record_text(client_identified, "geheim")
        client_identified.save(update_fields=["document_type", "data_json"])
        bundle = build_client_offline_bundle(assistant_user, facility, client_identified)
        # Mutation ``"" → "geheim"`` würde Klartext leaken.
        assert bundle["client"]["data_fields"].get("client-notes", "") == ""

    def test_staff_sees_full_notes(self, facility, client_identified, staff_user):
        set_record_text(client_identified, "Aktennotiz")
        client_identified.save(update_fields=["document_type", "data_json"])
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["client"]["data_fields"].get("client-notes", "") == "Aktennotiz"

    def test_lead_sees_notes(self, facility, client_identified, lead_user):
        set_record_text(client_identified, "Lead-Sicht")
        client_identified.save(update_fields=["document_type", "data_json"])
        bundle = build_client_offline_bundle(lead_user, facility, client_identified)
        assert bundle["client"]["data_fields"].get("client-notes", "") == "Lead-Sicht"

    def test_facility_admin_sees_notes(self, facility, client_identified, admin_user):
        set_record_text(client_identified, "Admin-Sicht")
        client_identified.save(update_fields=["document_type", "data_json"])
        bundle = build_client_offline_bundle(admin_user, facility, client_identified)
        assert bundle["client"]["data_fields"].get("client-notes", "") == "Admin-Sicht"

    def test_user_without_property_falls_back_to_empty(self, facility, client_identified):
        """Mutation ``hasattr(user, "is_staff_or_above")`` → ``True`` oder
        Removal des Fallbacks ``False`` würde den Branch killen."""

        class _StubUser:
            is_authenticated = True
            role = "assistant"
            pk = 999

        set_record_text(client_identified, "wuerde-leaken")
        client_identified.save(update_fields=["document_type", "data_json"])
        bundle = build_client_offline_bundle(_StubUser(), facility, client_identified)
        # Ohne is_staff_or_above-Property → notes_visible False → leerer String.
        assert bundle["client"]["data_fields"].get("client-notes", "") == ""
