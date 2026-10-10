"""Sichere Live-Invalidierung der Hausverbotswarnungen im Zeitstrom (Refs #1917).

Kernvertrag: eine facility-, rollen- und sensitivity-gebundene, opake Revision
(``BanRevision``) macht Banner-Änderungen im bestehenden Watch-Poll (#1916)
sichtbar — ohne O(N)-Scan, ohne Entschlüsselung im Poll-Pfad und ohne
Seitenkanal: Änderungen an für die Rolle unsichtbaren Hausverbots-Daten
(Grund eines ELEVATED/HIGH-Typs) dürfen für niedrigere Rollen keinen
beobachtbaren Unterschied erzeugen (weder Validator noch ETag noch Refresh).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import BanRevision, DocumentType, DocumentTypeField, FieldTemplate
from core.services.client.main import anonymize_client, update_client
from core.services.events import create_event, soft_delete_event, update_event
from core.services.system.ban_revision import (
    _banner_projection,
    ban_revision_projection,
    bump_ban_revision,
    bump_ban_revision_for_client_change,
    bump_ban_revision_for_document_type_change,
    bump_ban_revision_for_event_change,
)
from core.services.zeitstrom_filters import resolve_zeitstrom_state
from core.services.zeitstrom_watch import build_zeitstrom_component_validator
from tests.test_zeitstrom_watch import _scope_for_client, _watch

FUTURE = (date.today() + timedelta(days=30)).isoformat()


def _revisions(facility):
    row = BanRevision.objects.filter(facility=facility).first()
    if row is None:
        return (0, 0, 0)
    return (row.revision_normal, row.revision_elevated, row.revision_high)


def _make_ban_doc_type(facility, *, sensitivity=DocumentType.Sensitivity.ELEVATED, name="Hausverbot"):
    doc_type = DocumentType.objects.create(
        facility=facility,
        name=name,
        sensitivity=sensitivity,
        system_type=DocumentType.SystemType.BAN,
    )
    for field_name, field_type, required in (
        ("Grund", FieldTemplate.FieldType.TEXTAREA, True),
        ("Bis", FieldTemplate.FieldType.DATE, False),
        ("Aktiv", FieldTemplate.FieldType.BOOLEAN, False),
    ):
        ft = FieldTemplate.objects.create(
            facility=facility,
            name=field_name,
            field_type=field_type,
            is_required=required,
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft)
    return doc_type


def _force_sensitivity(document_type, value):
    """``choices`` sind Formular-Validierung, keine DB-Beschraenkung."""
    DocumentType.objects.filter(pk=document_type.pk).update(sensitivity=value)
    document_type.refresh_from_db()
    return document_type


@pytest.fixture
def ban_doc_type(facility):
    return _make_ban_doc_type(facility)


@pytest.fixture
def high_ban_doc_type(facility):
    return _make_ban_doc_type(facility, sensitivity=DocumentType.Sensitivity.HIGH, name="Hausverbot (hoch)")


# ---------------------------------------------------------------------------
# Bump-Semantik des Event-Deltas (Write-Boundary-Kern)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBanRevisionEventDelta:
    def test_new_active_ban_bumps_normal_stratum(self, facility, ban_doc_type):
        bump_ban_revision_for_event_change(
            facility,
            ban_doc_type,
            {},
            {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True},
        )

        # Existenz-Delta → Stratum 0; der neue Grund ist ein zusätzliches
        # Delta im ELEVATED-Stratum des Dokumenttyps.
        assert _revisions(facility) == (1, 1, 0)

    def test_new_inactive_ban_bumps_nothing(self, facility, ban_doc_type):
        bump_ban_revision_for_event_change(
            facility,
            ban_doc_type,
            {},
            {"grund": "Schlafend", "bis": "", "aktiv": False},
        )

        assert _revisions(facility) == (0, 0, 0)

    def test_grund_only_change_on_elevated_ban_bumps_only_elevated(self, facility, ban_doc_type):
        before = {"grund": "Alter Grund", "bis": FUTURE, "aktiv": True}
        after = {"grund": "Neuer Grund", "bis": FUTURE, "aktiv": True}

        bump_ban_revision_for_event_change(facility, ban_doc_type, before, after)

        assert _revisions(facility) == (0, 1, 0)

    def test_grund_only_change_on_high_ban_bumps_only_high(self, facility, high_ban_doc_type):
        before = {"grund": "Alter Grund", "bis": FUTURE, "aktiv": True}
        after = {"grund": "Neuer Grund", "bis": FUTURE, "aktiv": True}

        bump_ban_revision_for_event_change(facility, high_ban_doc_type, before, after)

        assert _revisions(facility) == (0, 0, 1)

    def test_deactivation_bumps_normal_stratum(self, facility, high_ban_doc_type):
        before = {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True}
        after = {"grund": "Bedrohung", "bis": FUTURE, "aktiv": False}

        bump_ban_revision_for_event_change(facility, high_ban_doc_type, before, after)

        normal, _elevated, _high = _revisions(facility)
        assert normal == 1

    def test_bis_change_on_active_ban_bumps_normal_stratum(self, facility, high_ban_doc_type):
        later = (date.today() + timedelta(days=60)).isoformat()
        before = {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True}
        after = {"grund": "Bedrohung", "bis": later, "aktiv": True}

        bump_ban_revision_for_event_change(facility, high_ban_doc_type, before, after)

        assert _revisions(facility)[0] == 1

    def test_dormant_ban_edit_stays_invisible_for_everyone(self, facility, high_ban_doc_type):
        """Schlafendes HIGH-Hausverbot: Datums-/Grund-Edits erzeugen KEIN Signal."""
        later = (date.today() + timedelta(days=60)).isoformat()
        before = {"grund": "Alt", "bis": FUTURE, "aktiv": False}
        after = {"grund": "Neu", "bis": later, "aktiv": False}

        bump_ban_revision_for_event_change(facility, high_ban_doc_type, before, after)

        assert _revisions(facility) == (0, 0, 0)

    def test_expired_active_ban_projects_inactive_and_grund_edit_bumps_nothing(
        self,
        facility,
        high_ban_doc_type,
    ):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        before = {"grund": "Alter Grund", "bis": yesterday, "aktiv": True}
        after = {"grund": "Neuer Grund", "bis": yesterday, "aktiv": True}

        assert _banner_projection(before) == (False, None, "")

        bump_ban_revision_for_event_change(facility, high_ban_doc_type, before, after)

        assert _revisions(facility) == (0, 0, 0)

    def test_banner_foreign_field_change_bumps_nothing(self, facility, ban_doc_type):
        before = {"grund": "Gleich", "bis": FUTURE, "aktiv": True, "notiz": "alt"}
        after = {"grund": "Gleich", "bis": FUTURE, "aktiv": True, "notiz": "neu"}

        bump_ban_revision_for_event_change(facility, ban_doc_type, before, after)

        assert _revisions(facility) == (0, 0, 0)

    def test_non_ban_doc_type_never_bumps(self, facility, doc_type_contact):
        bump_ban_revision_for_event_change(
            facility,
            doc_type_contact,
            {},
            {"grund": "irrelevant", "aktiv": True},
        )

        assert _revisions(facility) == (0, 0, 0)

    @pytest.mark.usefixtures("_encryption_key")
    def test_encrypted_stored_values_are_compared_as_plaintext(self, facility, ban_doc_type):
        """Fernet-Token alt vs. identischer Klartext neu ⇒ kein Delta, kein Bump."""
        from core.services.file_vault import encrypt_field

        before = {"grund": encrypt_field("Bedrohung"), "bis": FUTURE, "aktiv": encrypt_field(True)}
        after = {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True}

        bump_ban_revision_for_event_change(facility, ban_doc_type, before, after)

        assert _revisions(facility) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Validator-Stratifizierung (Seitenkanal-Kern)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBanRevisionPersistence:
    def test_integrity_error_retries_update_for_same_facility(self, facility, monkeypatch):
        revision = BanRevision.objects.create(facility=facility, revision_normal=7)
        queryset_type = type(BanRevision.objects.for_facility(facility))
        real_update = queryset_type.update
        update_calls = 0

        def first_update_misses(queryset, **increments):
            nonlocal update_calls
            if queryset.model is BanRevision:
                update_calls += 1
                if update_calls == 1:
                    return 0
            return real_update(queryset, **increments)

        def create_loses_race(**_kwargs):
            raise IntegrityError("simulierter verlorener INSERT-Wettlauf")

        monkeypatch.setattr(queryset_type, "update", first_update_misses)
        monkeypatch.setattr(BanRevision.objects, "create", create_loses_race)

        bump_ban_revision(facility, [0])

        revision.refresh_from_db()
        assert update_calls == 2
        assert revision.revision_normal == 8


@pytest.mark.django_db
class TestBanValidatorStratification:
    @staticmethod
    def _validators(facility, *users):
        state = resolve_zeitstrom_state({}, facility)
        return [build_zeitstrom_component_validator(user, facility, state, "bans") for user in users]

    def test_high_stratum_bump_is_invisible_below_lead(self, facility, assistant_user, staff_user, lead_user):
        before = self._validators(facility, assistant_user, staff_user, lead_user)

        bump_ban_revision(facility, [2])

        after = self._validators(facility, assistant_user, staff_user, lead_user)
        assert after[0] == before[0]
        assert after[1] == before[1]
        assert after[2] != before[2]

    def test_elevated_stratum_bump_is_invisible_for_assistant(self, facility, assistant_user, staff_user, lead_user):
        before = self._validators(facility, assistant_user, staff_user, lead_user)

        bump_ban_revision(facility, [1])

        after = self._validators(facility, assistant_user, staff_user, lead_user)
        assert after[0] == before[0]
        assert after[1] != before[1]
        assert after[2] != before[2]

    def test_normal_stratum_bump_is_visible_for_all_roles(self, facility, assistant_user, staff_user, lead_user):
        before = self._validators(facility, assistant_user, staff_user, lead_user)

        bump_ban_revision(facility, [0])

        after = self._validators(facility, assistant_user, staff_user, lead_user)
        assert all(a != b for a, b in zip(after, before, strict=True))

    def test_projection_reads_single_fixed_row_without_event_access(self, facility, assistant_user):
        bump_ban_revision(facility, [0])

        with CaptureQueriesContext(connection) as ctx:
            ban_revision_projection(facility, assistant_user)

        assert len(ctx) == 1
        assert "core_event" not in ctx.captured_queries[0]["sql"]

    def test_projection_contains_localdate_for_midnight_expiry(self, facility, assistant_user):
        with patch("core.services.system.ban_revision.timezone") as tz:
            tz.localdate.return_value = date(2026, 8, 17)
            first = ban_revision_projection(facility, assistant_user)
            tz.localdate.return_value = date(2026, 8, 18)
            second = ban_revision_projection(facility, assistant_user)

        assert first != second

    def test_projection_for_unlisted_role_uses_rank_zero_prefix(self, facility, super_admin_user):
        BanRevision.objects.create(
            facility=facility,
            revision_normal=3,
            revision_elevated=4,
            revision_high=5,
        )

        rank, prefix, _localdate = ban_revision_projection(facility, super_admin_user)

        assert rank == 0
        assert prefix == (3,)


# ---------------------------------------------------------------------------
# Write-Boundaries: Event-Services
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEventServiceWriteBoundaries:
    def test_create_event_with_active_ban_bumps(self, facility, staff_user, client_identified, ban_doc_type):
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )

        assert _revisions(facility)[0] == 1

    def test_update_event_grund_only_bumps_elevated_only(self, facility, staff_user, client_identified, ban_doc_type):
        event = create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Alter Grund", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        baseline = _revisions(facility)

        update_event(event, staff_user, {"grund": "Neuer Grund", "bis": FUTURE, "aktiv": True})

        normal, elevated, high = _revisions(facility)
        assert normal == baseline[0]
        assert elevated == baseline[1] + 1
        assert high == baseline[2]

    def test_soft_delete_active_ban_bumps_normal(self, facility, staff_user, client_identified, ban_doc_type):
        event = create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        baseline = _revisions(facility)

        soft_delete_event(event, staff_user)

        assert _revisions(facility)[0] == baseline[0] + 1

    def test_soft_delete_inactive_ban_bumps_nothing(self, facility, staff_user, client_identified, ban_doc_type):
        event = create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Schlafend", "bis": "", "aktiv": False},
            client=client_identified,
        )
        baseline = _revisions(facility)

        soft_delete_event(event, staff_user)

        assert _revisions(facility) == baseline


# ---------------------------------------------------------------------------
# Write-Boundaries: DocumentType-Konfiguration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocTypeWriteBoundaries:
    def test_is_active_change_bumps_normal(self, facility, ban_doc_type):
        ban_doc_type.is_active = False

        bump_ban_revision_for_document_type_change(
            ban_doc_type,
            ["is_active"],
            previous_sensitivity=ban_doc_type.sensitivity,
        )

        assert _revisions(facility)[0] == 1

    def test_sensitivity_flip_bumps_lowest_affected_stratum(self, facility, high_ban_doc_type):
        high_ban_doc_type.sensitivity = DocumentType.Sensitivity.ELEVATED

        bump_ban_revision_for_document_type_change(
            high_ban_doc_type,
            ["sensitivity"],
            previous_sensitivity=DocumentType.Sensitivity.HIGH,
        )

        assert _revisions(facility) == (0, 1, 0)

    def test_empty_previous_sensitivity_bumps_normal_stratum(self, facility, ban_doc_type):
        bump_ban_revision_for_document_type_change(
            ban_doc_type,
            ["sensitivity"],
            previous_sensitivity="",
        )

        assert _revisions(facility) == (1, 0, 0)

    def test_unknown_new_sensitivity_bumps_normal_stratum(self, facility, ban_doc_type):
        _force_sensitivity(ban_doc_type, "kritisch")

        bump_ban_revision_for_document_type_change(
            ban_doc_type,
            ["sensitivity"],
            previous_sensitivity=DocumentType.Sensitivity.ELEVATED,
        )

        assert _revisions(facility) == (1, 0, 0)

    def test_non_ban_doc_type_change_bumps_nothing(self, facility, doc_type_contact):
        bump_ban_revision_for_document_type_change(
            doc_type_contact,
            ["is_active", "sensitivity"],
            previous_sensitivity=DocumentType.Sensitivity.HIGH,
        )

        assert _revisions(facility) == (0, 0, 0)

    def test_config_update_view_bumps_on_is_active_toggle(self, client, admin_user, facility, ban_doc_type):
        client.force_login(admin_user)

        response = client.post(
            reverse("core:config_doctype_update", kwargs={"pk": ban_doc_type.pk}),
            {"name": ban_doc_type.name, "sensitivity": ban_doc_type.sensitivity},
        )

        assert response.status_code == 302
        assert _revisions(facility)[0] == 1


# ---------------------------------------------------------------------------
# Write-Boundaries: Personen-Pfade
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClientWriteBoundaries:
    def test_pseudonym_change_with_ban_bumps_normal(self, facility, staff_user, client_identified, ban_doc_type):
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        baseline = _revisions(facility)

        update_client(client_identified, staff_user, pseudonym="Neues Pseudonym")

        assert _revisions(facility)[0] == baseline[0] + 1

    def test_pseudonym_change_without_ban_bumps_nothing(self, facility, staff_user, client_identified):
        update_client(client_identified, staff_user, pseudonym="Neues Pseudonym")

        assert _revisions(facility) == (0, 0, 0)

    def test_unrelated_client_change_with_ban_bumps_nothing(
        self, facility, staff_user, client_identified, ban_doc_type
    ):
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        baseline = _revisions(facility)

        update_client(client_identified, staff_user, notes="Nur eine Notiz")

        assert _revisions(facility) == baseline

    def test_anonymize_client_with_ban_bumps_normal(self, facility, staff_user, client_identified, ban_doc_type):
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        baseline = _revisions(facility)

        anonymize_client(client_identified, user=staff_user)

        assert _revisions(facility)[0] == baseline[0] + 1

    def test_client_change_helper_without_ban_events_bumps_nothing(self, facility, client_identified):
        bump_ban_revision_for_client_change(client_identified)

        assert _revisions(facility) == (0, 0, 0)

    def _create_dormant_bans(self, facility, staff_user, client_identified, ban_doc_type):
        """Ein schlafendes und ein abgelaufenes Hausverbot — beide ohne Banner-Wirkung."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Schlafend", "bis": "", "aktiv": False},
            client=client_identified,
        )
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Abgelaufen", "bis": yesterday, "aktiv": True},
            client=client_identified,
        )

    def test_pseudonym_change_with_only_dormant_bans_bumps_nothing(
        self, facility, staff_user, client_identified, ban_doc_type
    ):
        """Trigger-Seitenkanal: Umbenennung einer Person, deren Bans alle
        schlafend/abgelaufen sind, ändert das Banner für keine Rolle — ein
        Bump wäre ein beobachtbarer No-op-Fetch, aus dem sich „Person hat
        verborgene Ban-Events" ablesen ließe."""
        self._create_dormant_bans(facility, staff_user, client_identified, ban_doc_type)
        baseline = _revisions(facility)

        update_client(client_identified, staff_user, pseudonym="Neues Pseudonym")

        assert _revisions(facility) == baseline

    def test_pseudonym_change_with_deactivated_ban_type_bumps_nothing(
        self, facility, staff_user, client_identified, ban_doc_type
    ):
        """Deaktivierter Ban-Typ fällt aus der Banner-Query — kein Signal."""
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        DocumentType.objects.filter(pk=ban_doc_type.pk).update(is_active=False)
        baseline = _revisions(facility)

        update_client(client_identified, staff_user, pseudonym="Neues Pseudonym")

        assert _revisions(facility) == baseline

    def test_anonymize_client_with_only_dormant_bans_bumps_nothing(
        self, facility, staff_user, client_identified, ban_doc_type
    ):
        self._create_dormant_bans(facility, staff_user, client_identified, ban_doc_type)
        baseline = _revisions(facility)

        anonymize_client(client_identified, user=staff_user)

        assert _revisions(facility) == baseline


# ---------------------------------------------------------------------------
# Write-Boundaries: Django-Admin-Fallback (Refs #1637)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocTypeAdminWriteBoundary:
    """Der Django-Admin ist der per #1637 erhaltene Produktions-Fallback und
    läuft an ``save_document_type`` vorbei — banner-relevante Änderungen an
    einem Ban-Typ müssen die Revision auch dort bumpen (Muster
    ``test_admin_documenttype_slug.py``: RequestFactory + Admin direkt)."""

    @staticmethod
    def _save_via_admin(admin_user, doc_type, **overrides):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.forms.models import model_to_dict
        from django.test import RequestFactory

        from core.admin.documents import DocumentTypeAdmin
        from core.admin_site import anlaufstelle_admin_site

        request = RequestFactory().post("/")
        request.user = admin_user
        request.current_facility = admin_user.facility
        request.session = {}
        request._messages = FallbackStorage(request)
        model_admin = DocumentTypeAdmin(DocumentType, anlaufstelle_admin_site)
        form_class = model_admin.get_form(request, doc_type, change=True)
        data = {
            key: value
            for key, value in model_to_dict(doc_type, fields=form_class.base_fields.keys()).items()
            if value is not None
        }
        data.update(overrides)
        form = form_class(data, instance=doc_type)
        assert form.is_valid(), form.errors
        model_admin.save_model(request, form.instance, form, change=True)
        return form

    def test_admin_is_active_toggle_on_ban_type_bumps_normal(self, admin_user, facility, ban_doc_type):
        self._save_via_admin(admin_user, ban_doc_type, is_active=False)

        assert _revisions(facility)[0] == 1

    def test_admin_sensitivity_flip_bumps_lowest_affected_stratum(self, admin_user, facility, high_ban_doc_type):
        self._save_via_admin(admin_user, high_ban_doc_type, sensitivity=DocumentType.Sensitivity.ELEVATED)

        assert _revisions(facility) == (0, 1, 0)

    def test_admin_unrelated_change_on_ban_type_bumps_nothing(self, admin_user, facility, ban_doc_type):
        self._save_via_admin(admin_user, ban_doc_type, description="Nur eine Beschreibung")

        assert _revisions(facility) == (0, 0, 0)

    def test_admin_change_on_non_ban_type_bumps_nothing(self, admin_user, facility, doc_type_contact):
        self._save_via_admin(admin_user, doc_type_contact, is_active=False)

        assert _revisions(facility) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Watch-Endpoint: Poll-Vertrag + Seitenkanal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestZeitstromWatchBans:
    def test_watch_reports_bans_component(self, client, staff_user):
        client.force_login(staff_user)

        response = _watch(client)

        assert response.status_code == 200
        payload = response.json()
        assert "bans" in payload["components"]
        assert payload["validators"]["bans"].startswith('"')

    def test_hidden_grund_change_keeps_assistant_etag_and_304(
        self, client, assistant_user, lead_user, facility, client_identified, high_ban_doc_type
    ):
        event = create_event(
            facility,
            lead_user,
            high_ban_doc_type,
            timezone.now(),
            {"grund": "Alter geheimer Grund", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        client.force_login(assistant_user)
        first = _watch(client)

        update_event(event, lead_user, {"grund": "Neuer geheimer Grund", "bis": FUTURE, "aktiv": True})

        response = _watch(client, etag=first["ETag"])
        assert response.status_code == 304
        assert response["ETag"] == first["ETag"]

    def test_hidden_grund_change_reaches_lead_as_bans_only(
        self, client, lead_user, facility, client_identified, high_ban_doc_type
    ):
        event = create_event(
            facility,
            lead_user,
            high_ban_doc_type,
            timezone.now(),
            {"grund": "Alter geheimer Grund", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        client.force_login(lead_user)
        first = _watch(client)

        update_event(event, lead_user, {"grund": "Neuer geheimer Grund", "bis": FUTURE, "aktiv": True})

        response = _watch(client, etag=first["ETag"], validators=first.json()["validators"])
        assert response.status_code == 200
        assert response.json()["components"] == ["bans"]

    def test_ban_lift_reaches_assistant(
        self, client, assistant_user, lead_user, facility, client_identified, high_ban_doc_type
    ):
        event = create_event(
            facility,
            lead_user,
            high_ban_doc_type,
            timezone.now(),
            {"grund": "Geheim", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        client.force_login(assistant_user)
        first = _watch(client)

        update_event(event, lead_user, {"grund": "Geheim", "bis": FUTURE, "aktiv": False})

        response = _watch(client, etag=first["ETag"], validators=first.json()["validators"])
        assert response.status_code == 200
        assert "bans" in response.json()["components"]

    @pytest.mark.parametrize("role_fixture", ["assistant_user", "lead_user"])
    def test_rename_with_only_dormant_bans_keeps_etag_and_304(
        self, request, client, lead_user, facility, client_identified, high_ban_doc_type, role_fixture
    ):
        """Trigger-Seitenkanal (Refs #1917): die sichtbare Umbenennung
        einer Person mit ausschließlich schlafenden/abgelaufenen Bans ändert
        das Banner für KEINE Rolle — der Poll muss byte-identisch bleiben
        (304, gleicher ETag), sonst verrät der No-op-Fetch verborgene
        Ban-Events. Lead ist der strengste Fall (alle Strata im Validator)."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        create_event(
            facility,
            lead_user,
            high_ban_doc_type,
            timezone.now(),
            {"grund": "Schlafend", "bis": "", "aktiv": False},
            client=client_identified,
        )
        create_event(
            facility,
            lead_user,
            high_ban_doc_type,
            timezone.now(),
            {"grund": "Abgelaufen", "bis": yesterday, "aktiv": True},
            client=client_identified,
        )
        watcher = request.getfixturevalue(role_fixture)
        client.force_login(watcher)
        params = {"type": "workitems"}
        first = _watch(client, params=params)

        update_client(client_identified, lead_user, pseudonym="Neues Pseudonym")

        response = _watch(client, params=params, etag=first["ETag"])
        assert response.status_code == 304
        assert response["ETag"] == first["ETag"]

    def test_watch_never_touches_ban_events_or_decryption(
        self, client, staff_user, facility, client_identified, ban_doc_type
    ):
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        client.force_login(staff_user)

        with (
            patch("core.views.zeitstrom.get_active_bans", side_effect=AssertionError("banner builder called")),
            patch("core.services.system.bans.safe_decrypt", side_effect=AssertionError("poll decrypted")),
        ):
            response = _watch(client)

        assert response.status_code == 200

    def test_watch_query_count_constant_across_ban_count(
        self, client, staff_user, facility, client_identified, ban_doc_type
    ):
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Bedrohung", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        client.force_login(staff_user)
        scope = _scope_for_client(client)
        with CaptureQueriesContext(connection) as one:
            assert _watch(client, scope=scope).status_code == 200

        for index in range(10):
            create_event(
                facility,
                staff_user,
                ban_doc_type,
                timezone.now(),
                {"grund": f"Grund {index}", "bis": FUTURE, "aktiv": True},
                client=client_identified,
            )
        with CaptureQueriesContext(connection) as many:
            assert _watch(client, scope=scope).status_code == 200

        assert len(many) <= len(one) + 2
        revision_queries = [q["sql"] for q in many.captured_queries if "core_banrevision" in q["sql"]]
        assert len(revision_queries) == 1


# ---------------------------------------------------------------------------
# Banner-Follow-up-Partial (autoritativ, vollständig, rollenredigiert)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestZeitstromBansPartial:
    @staticmethod
    def _followup(client, scope, validator):
        return client.get(
            reverse("core:zeitstrom_bans_partial"),
            headers={
                "X-Zeitstrom-Watch": "1",
                "X-Zeitstrom-Page-Scope": scope,
                "If-Match": validator,
            },
        )

    def test_partial_returns_full_banner_with_matching_etag(
        self, client, staff_user, facility, client_identified, ban_doc_type
    ):
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Wiederholte Bedrohung", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        client.force_login(staff_user)
        scope = _scope_for_client(client)
        validator = _watch(client, scope=scope).json()["validators"]["bans"]

        response = self._followup(client, scope, validator)

        assert response.status_code == 200
        assert response["ETag"] == validator
        content = response.content.decode()
        assert 'id="zeitstrom-bans-slot"' in content
        assert client_identified.pseudonym in content
        assert "Wiederholte Bedrohung" in content

    def test_partial_redacts_grund_for_assistant(
        self, client, assistant_user, staff_user, facility, client_identified, ban_doc_type
    ):
        create_event(
            facility,
            staff_user,
            ban_doc_type,
            timezone.now(),
            {"grund": "Wiederholte Bedrohung", "bis": FUTURE, "aktiv": True},
            client=client_identified,
        )
        client.force_login(assistant_user)
        scope = _scope_for_client(client)
        validator = _watch(client, scope=scope).json()["validators"]["bans"]

        response = self._followup(client, scope, validator)

        assert response.status_code == 200
        content = response.content.decode()
        assert client_identified.pseudonym in content
        assert "Wiederholte Bedrohung" not in content

    def test_partial_with_stale_validator_is_412(self, client, staff_user):
        client.force_login(staff_user)
        scope = _scope_for_client(client)

        response = self._followup(client, scope, '"veraltet"')

        assert response.status_code == 412

    def test_partial_with_wrong_scope_is_409(self, client, staff_user):
        client.force_login(staff_user)

        response = self._followup(client, "0" * 64, '"egal"')

        assert response.status_code == 409

    def test_page_advertises_bans_url(self, client, staff_user):
        client.force_login(staff_user)

        response = client.get(reverse("core:zeitstrom"))

        content = response.content.decode()
        assert f'data-bans-url="{reverse("core:zeitstrom_bans_partial")}"' in content
