"""Regressionstests fuer SoftDeletableModel-Mixin und Manager-Methoden.

Refs #743 — Foundation fuer einheitliches Soft-Delete-Pattern auf
Client/Case/Episode/WorkItem.
"""

import pytest
from django.utils import timezone

from core.models import Case, Client, WorkItem
from core.models.episode import Episode

pytestmark = pytest.mark.django_db


def _make_client(facility, **kwargs):
    return Client.objects.create(facility=facility, pseudonym="P-Test", **kwargs)


def _make_case(facility, client, **kwargs):
    return Case.objects.create(facility=facility, client=client, title="Fall", **kwargs)


def _make_episode(case, **kwargs):
    return Episode.objects.create(case=case, title="Episode", started_at=timezone.now(), **kwargs)


def _make_workitem(facility, user, **kwargs):
    return WorkItem.objects.create(
        facility=facility,
        title="Aufgabe",
        item_type=WorkItem.ItemType.TASK,
        created_by=user,
        **kwargs,
    )


class TestSoftDeleteFields:
    """Mixin liefert is_deleted/deleted_at/deleted_by auf allen 4 Aggregaten."""

    def test_client_has_soft_delete_fields(self, facility):
        c = _make_client(facility)
        assert c.is_deleted is False
        assert c.deleted_at is None
        assert c.deleted_by is None

    def test_case_has_soft_delete_fields(self, facility):
        c = _make_client(facility)
        case = _make_case(facility, c)
        assert case.is_deleted is False
        assert case.deleted_at is None
        assert case.deleted_by is None

    def test_episode_has_soft_delete_fields(self, facility):
        c = _make_client(facility)
        case = _make_case(facility, c)
        ep = _make_episode(case)
        assert ep.is_deleted is False
        assert ep.deleted_at is None

    def test_workitem_has_soft_delete_fields(self, facility, lead_user):
        wi = _make_workitem(facility, lead_user)
        assert wi.is_deleted is False
        assert wi.deleted_at is None


class TestSoftDeleteRestore:
    """soft_delete() und restore() Methoden."""

    def test_soft_delete_sets_flag_and_timestamp(self, facility, lead_user):
        c = _make_client(facility)
        before = timezone.now()
        c.soft_delete(user=lead_user)
        c.refresh_from_db()
        assert c.is_deleted is True
        assert c.deleted_at is not None
        assert c.deleted_at >= before
        assert c.deleted_by_id == lead_user.pk

    def test_soft_delete_without_user(self, facility):
        c = _make_client(facility)
        c.soft_delete()
        c.refresh_from_db()
        assert c.is_deleted is True
        assert c.deleted_by is None

    def test_restore_clears_flag_and_metadata(self, facility, lead_user):
        c = _make_client(facility)
        c.soft_delete(user=lead_user)
        c.restore()
        c.refresh_from_db()
        assert c.is_deleted is False
        assert c.deleted_at is None
        assert c.deleted_by is None


class TestQuerySetActiveDeleted:
    """active() / deleted() Convenience auf FacilityScopedQuerySet."""

    def test_active_excludes_soft_deleted(self, facility):
        alive = _make_client(facility)
        ghost = Client.objects.create(facility=facility, pseudonym="P-Ghost")
        ghost.soft_delete()
        active_pks = set(Client.objects.for_facility(facility).active().values_list("pk", flat=True))
        assert alive.pk in active_pks
        assert ghost.pk not in active_pks

    def test_deleted_returns_only_soft_deleted(self, facility):
        alive = _make_client(facility)
        ghost = Client.objects.create(facility=facility, pseudonym="P-Ghost")
        ghost.soft_delete()
        deleted_pks = set(Client.objects.for_facility(facility).deleted().values_list("pk", flat=True))
        assert ghost.pk in deleted_pks
        assert alive.pk not in deleted_pks

    def test_default_manager_returns_all_rows(self, facility):
        """Konsistenz zu Event: Default-Manager filtert nicht — Aufrufer
        muessen explizit ``.active()``/``.filter(is_deleted=False)`` setzen."""
        alive = _make_client(facility)
        ghost = Client.objects.create(facility=facility, pseudonym="P-Ghost")
        ghost.soft_delete()
        all_pks = set(Client.objects.for_facility(facility).values_list("pk", flat=True))
        assert {alive.pk, ghost.pk} <= all_pks


class TestAnonymizeClientServiceLayer:
    """services/clients.anonymize_client() — Wrapper Client.anonymize() bleibt
    fachlich identisch zu Refs #715."""

    def test_anonymize_via_model_wrapper_redacts_pseudonym(self, facility):
        c = _make_client(facility, notes="sensible notiz")
        original_pk = c.pk
        c.anonymize()
        c.refresh_from_db()
        assert c.pseudonym.startswith("Gelöscht-")
        assert c.notes == ""
        assert c.is_active is False
        assert c.pk == original_pk  # Datensatz bleibt fuer Statistik

    def test_anonymize_via_service_directly(self, facility, lead_user):
        from core.services.clients import anonymize_client

        c = _make_client(facility, notes="sensible notiz")
        anonymize_client(c, user=lead_user)
        c.refresh_from_db()
        assert c.pseudonym.startswith("Gelöscht-")
        assert c.notes == ""
