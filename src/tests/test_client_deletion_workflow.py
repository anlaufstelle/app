"""Vier-Augen-Lösch-Workflow für Personen — Unit-Tests (Refs #626).

Testet die Service-Funktionen: request, approve, reject, restore, retention-
getriggerte Anonymisierung nach Ablauf von ``client_trash_days``.
"""

import threading
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog, Client, DocumentType, Event, EventHistory, LegalHold
from core.models.workitem import DeletionRequest
from core.services.client import (
    anonymize_eligible_soft_deleted_clients,
    approve_client_deletion,
    reject_client_deletion,
    request_client_deletion,
    restore_client,
)

pytestmark = pytest.mark.django_db


def _make_client(facility, pseudonym="P-Workflow", **kwargs):
    return Client.objects.create(facility=facility, pseudonym=pseudonym, **kwargs)


class TestRequestClientDeletion:
    def test_creates_pending_request(self, facility, staff_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Widerruf der Einwilligung")
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.target_type == "Client"
        assert dr.target_id == client.pk
        assert dr.requested_by == staff_user
        assert dr.reason == "Widerruf der Einwilligung"

    def test_idempotent_returns_existing_pending(self, facility, staff_user):
        client = _make_client(facility)
        dr1 = request_client_deletion(client, staff_user, "Grund 1")
        dr2 = request_client_deletion(client, staff_user, "Grund 2 (ignoriert)")
        assert dr1.pk == dr2.pk
        assert DeletionRequest.objects.filter(target_id=client.pk).count() == 1

    def test_request_against_a_merge_tombstone_is_refused(self, facility, staff_user):
        """Refs #2017: ein Merge-Tombstone ist kein Antragsziel.

        Die leere Huelle liegt bereits im Papierkorb und traegt den Verweis auf
        ihr Ziel; ein Loeschantrag auf sie waere ein zweiter Weg in denselben
        Papierkorb — mit dem Nebeneffekt, dass die Genehmigung ``deleted_at``
        neu setzt und damit die Papierkorb-Frist des Tombstones zurueckdreht.
        """
        target = _make_client(facility, "P-Ziel")
        tombstone = _make_client(facility, "P-Quelle")
        tombstone.merged_into = target
        tombstone.save(update_fields=["merged_into"])
        tombstone.soft_delete(user=staff_user)

        with pytest.raises(ValidationError) as exc:
            request_client_deletion(tombstone, staff_user, "Grund")
        assert "zusammengeführt" in " ".join(exc.value.messages)
        assert not DeletionRequest.objects.filter(target_id=tombstone.pk).exists()

    def test_request_against_a_trashed_client_is_refused(self, facility, staff_user):
        """Refs #2017 (Cross-Review): auch ohne Merge kein zweiter Weg in den
        Papierkorb — die Genehmigung setzte sonst ``deleted_at`` neu."""
        trashed = _make_client(facility, "P-Papierkorb")
        trashed.soft_delete(user=staff_user)

        with pytest.raises(ValidationError) as exc:
            request_client_deletion(trashed, staff_user, "Grund")
        assert "Papierkorb" in " ".join(exc.value.messages)
        assert not DeletionRequest.objects.filter(target_id=trashed.pk).exists()

    def test_request_sees_a_merge_that_committed_after_the_view_lookup(self, facility, staff_user):
        """Refs #2017 (Cross-Review): der Guard laeuft auf der frisch
        gesperrten Zeile, nicht auf der uebergebenen Instanz.

        Genau die Race, die der View-Filter ``is_deleted=False`` nicht
        abdeckt: die View hat die Person als lebend gelesen, danach committet
        eine Zusammenfuehrung, und erst dann laeuft der Service.
        """
        target = _make_client(facility, "P-Ziel-Stale")
        source = _make_client(facility, "P-Quelle-Stale")
        stale = Client.objects.get(pk=source.pk)  # Sicht der View, vor dem Merge

        Client.objects.filter(pk=source.pk).update(merged_into=target, is_deleted=True, deleted_at=timezone.now())
        assert stale.merged_into_id is None and stale.is_deleted is False  # veraltete Instanz

        with pytest.raises(ValidationError):
            request_client_deletion(stale, staff_user, "Grund")
        assert not DeletionRequest.objects.filter(target_id=source.pk).exists()


class TestApproveClientDeletion:
    def test_approve_sets_soft_delete_and_audit(self, facility, staff_user, lead_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        approve_client_deletion(dr, lead_user)

        client.refresh_from_db()
        dr.refresh_from_db()

        assert client.is_deleted is True
        assert client.deleted_by_id == lead_user.pk
        assert client.deleted_at is not None
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by == lead_user

        audit = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_SOFT_DELETED,
            target_id=str(client.pk),
        ).first()
        assert audit is not None
        # Refs #1093: detail traegt nur noch die DeletionRequest-PK — kein
        # Klienten-Pseudonym/reason, kein Antragsteller-Username (Forensik via
        # DR-Join: requested_by/reason leben in der DeletionRequest-Zeile).
        assert audit.detail == {"deletion_request": str(dr.pk)}

    def test_self_review_blocked(self, facility, staff_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        with pytest.raises(ValidationError):
            approve_client_deletion(dr, staff_user)

    def test_double_approve_rejected_after_status_change(self, facility, staff_user, lead_user, admin_user):
        """Refs #1376 (I9): TOCTOU-Race, analog services.events.approve_deletion.
        Beide Reviewer haben den Antrag im PENDING-Zustand gelesen (dasselbe
        Python-Objekt, kein ``refresh_from_db()`` zwischen den Aufrufen). Der
        zweite Aufruf muss den bereits fortgeschrittenen Status sehen und
        sauber ``ValidationError`` werfen, statt die Person ein zweites Mal
        zu verarbeiten."""
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")

        approve_client_deletion(dr, lead_user)
        with pytest.raises(ValidationError):
            approve_client_deletion(dr, admin_user)

        # Kein Doppel-Approve-Nebeneffekt: genau ein CLIENT_SOFT_DELETED-Eintrag.
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.CLIENT_SOFT_DELETED,
                target_id=str(client.pk),
            ).count()
            == 1
        )

    def test_approve_refuses_a_target_that_became_a_merge_tombstone(self, facility, staff_user, lead_user):
        """Refs #2017: Zielzustand wird unter dem Lock neu validiert.

        Zwischen Antrag und Genehmigung kann die Person Quelle einer
        Zusammenfuehrung geworden sein. Die Genehmigung wuerde die leere Huelle
        ein zweites Mal in den Papierkorb legen und dabei ``deleted_at``
        ueberschreiben — die Papierkorb-Frist des Tombstones liefe neu an, und
        das Undo-Fenster der Zusammenfuehrung verschoebe sich still.
        """
        target = _make_client(facility, "P-Ziel")
        source = _make_client(facility, "P-Quelle")
        dr = request_client_deletion(source, staff_user, "Grund")
        # Rennen: die Zusammenfuehrung kommt zwischen Antrag und Genehmigung durch.
        source.merged_into = target
        source.save(update_fields=["merged_into"])
        source.soft_delete(user=staff_user)
        deleted_at_before = Client.objects.get(pk=source.pk).deleted_at

        with pytest.raises(ValidationError) as exc:
            approve_client_deletion(dr, lead_user)
        assert "zusammengeführt" in " ".join(exc.value.messages)

        dr.refresh_from_db()
        source.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert source.deleted_at == deleted_at_before

    def test_approve_refuses_a_target_already_in_the_trash(self, facility, staff_user, lead_user):
        """Refs #2017: kein zweiter Soft-Delete auf denselben Datensatz.

        Parität zum Fall-Pfad (``_move_case_to_trash``): ein erneuter
        Soft-Delete setzt ``deleted_at`` neu und verlaengert damit die
        Aufbewahrung im Papierkorb um eine volle Frist.
        """
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        client.soft_delete(user=staff_user)
        deleted_at_before = Client.objects.get(pk=client.pk).deleted_at

        with pytest.raises(ValidationError) as exc:
            approve_client_deletion(dr, lead_user)
        assert "Papierkorb" in " ".join(exc.value.messages)

        dr.refresh_from_db()
        client.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert client.deleted_at == deleted_at_before


class TestRejectClientDeletion:
    def test_reject_sets_status_only(self, facility, staff_user, lead_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        reject_client_deletion(dr, lead_user)

        client.refresh_from_db()
        dr.refresh_from_db()

        assert client.is_deleted is False  # not deleted
        assert dr.status == DeletionRequest.Status.REJECTED
        assert dr.reviewed_by == lead_user

    def test_self_review_blocked(self, facility, staff_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        with pytest.raises(ValidationError):
            reject_client_deletion(dr, staff_user)

    def test_reject_of_an_already_approved_request_is_refused(self, facility, staff_user, lead_user, admin_user):
        """Refs #2012: kein Zurueckdrehen auf REJECTED nach der Genehmigung.

        Ohne ``select_for_update``/PENDING-Recheck ueberschreibt ein
        zeitgleicher Reject den APPROVED-Status, waehrend die Person im
        Papierkorb liegen bleibt. Anders als im Fall-/Merge-Pfad schreibt
        dieser Weg keine ``DELETION_REJECTED``-Zeile — der Schaden ist eine
        stille Statuskorruption ganz ohne Spur.
        """
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        approve_client_deletion(dr, lead_user)

        with pytest.raises(ValidationError) as exc:
            reject_client_deletion(dr, admin_user)
        assert "bereits bearbeitet" in " ".join(exc.value.messages)

        dr.refresh_from_db()
        client.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by == lead_user
        assert client.is_deleted is True


class TestRestoreClient:
    def test_restore_clears_soft_delete(self, facility, admin_user, lead_user, staff_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        approve_client_deletion(dr, lead_user)
        client.refresh_from_db()

        restore_client(client, admin_user)

        client.refresh_from_db()
        assert client.is_deleted is False
        assert client.deleted_at is None
        assert client.deleted_by is None

        audit = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_RESTORED,
            target_id=str(client.pk),
        ).first()
        assert audit is not None

    def test_restore_rejects_non_deleted(self, facility, admin_user):
        client = _make_client(facility)
        with pytest.raises(ValidationError):
            restore_client(client, admin_user)


class TestRetentionTrashAnonymization:
    """anonymize_eligible_soft_deleted_clients — Retention-Pfad."""

    def _make_settings(self, facility, trash_days=30):
        from core.models.settings import Settings

        settings_obj, _ = Settings.objects.get_or_create(
            facility=facility,
            defaults={"client_trash_days": trash_days},
        )
        if settings_obj.client_trash_days != trash_days:
            settings_obj.client_trash_days = trash_days
            settings_obj.save()
        return settings_obj

    def test_anonymizes_only_expired_soft_deleted(self, facility, lead_user, staff_user):
        settings_obj = self._make_settings(facility, trash_days=30)

        # Aktiver Klient — bleibt unangetastet
        active = _make_client(facility, pseudonym="P-Active")

        # Frisch soft-deleter Klient (nicht abgelaufen)
        recent = _make_client(facility, pseudonym="P-Recent")
        dr1 = request_client_deletion(recent, staff_user, "test")
        approve_client_deletion(dr1, lead_user)

        # Soft-deleter Klient mit abgelaufener Frist
        expired = _make_client(facility, pseudonym="P-Expired")
        dr2 = request_client_deletion(expired, staff_user, "test")
        approve_client_deletion(dr2, lead_user)
        # deleted_at zurueckdatieren auf 31 Tage
        Client.objects.filter(pk=expired.pk).update(deleted_at=timezone.now() - timedelta(days=31))

        count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        active.refresh_from_db()
        recent.refresh_from_db()
        expired.refresh_from_db()

        assert count == 1
        assert active.pseudonym == "P-Active"
        assert recent.pseudonym == "P-Recent"
        assert expired.pseudonym.startswith("Gelöscht-")

        audit = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_ANONYMIZED,
            target_id=str(expired.pk),
        ).first()
        assert audit is not None
        assert audit.detail["trigger"] == "trash_days_expired"

    def test_second_run_finds_nothing(self, facility, lead_user, staff_user):
        """Refs #2013: Terminal-Marker gegen die naechtliche Tombstone-Reselektion.

        ``anonymize_client`` aendert ``deleted_at`` nicht — der Tombstone
        erfuellt ``is_deleted=True, deleted_at<cutoff`` also weiter. Ohne
        ``anonymized_at`` selektierte JEDER Nachtlauf alle jemals
        Anonymisierten erneut: pro Tombstone und Nacht eine neue,
        HMAC-verkettete ``CLIENT_ANONYMIZED``-Zeile im append-only AuditLog
        (unbegrenztes Wachstum), ein dauerhaft von Null verschiedenes und damit
        wertloses „Anonymisiert N"-Ops-Signal und Ban-Revision-Churn.
        Gegenstueck: ``anonymize_eligible_soft_deleted_cases``.
        """
        settings_obj = self._make_settings(facility, trash_days=30)
        expired = self._make_expired_trash_client(facility, staff_user, lead_user, "P-Zweitlauf")

        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 1
        expired.refresh_from_db()
        stamped_at = expired.anonymized_at
        assert stamped_at is not None

        # Zweiter (und dritter) Lauf: nichts mehr zu tun — auch nicht im Dry-Run,
        # der dieselbe Menge zaehlt und deshalb denselben Filter braucht.
        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj, dry_run=True) == 0
        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 0

        expired.refresh_from_db()
        # Der Stempel datiert die ERSTE Tilgung und wird nicht fortgeschrieben.
        assert expired.anonymized_at == stamped_at
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.CLIENT_ANONYMIZED,
                target_id=str(expired.pk),
            ).count()
            == 1
        )

    def test_dry_run_returns_count_without_changes(self, facility, lead_user, staff_user):
        settings_obj = self._make_settings(facility, trash_days=30)
        client = _make_client(facility, pseudonym="P-DryRun")
        dr = request_client_deletion(client, staff_user, "test")
        approve_client_deletion(dr, lead_user)
        Client.objects.filter(pk=client.pk).update(deleted_at=timezone.now() - timedelta(days=31))

        count = anonymize_eligible_soft_deleted_clients(facility, settings_obj, dry_run=True)
        client.refresh_from_db()

        assert count == 1
        assert not client.pseudonym.startswith("Gelöscht-")

    # -- Legal-Hold-Ausschluss (Refs #1066) ---------------------------------

    def _make_expired_trash_client(self, facility, staff_user, lead_user, pseudonym):
        """Soft-deleter Klient mit abgelaufener Papierkorb-Frist."""
        c = _make_client(facility, pseudonym=pseudonym)
        dr = request_client_deletion(c, staff_user, "test")
        approve_client_deletion(dr, lead_user)
        Client.objects.filter(pk=c.pk).update(deleted_at=timezone.now() - timedelta(days=31))
        return c

    def _make_event_with_history(self, facility, client_obj, user):
        """Event + Klartext-EventHistory für einen Klienten."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            name=f"DT-{client_obj.pseudonym}",
        )
        event = Event.objects.create(
            facility=facility,
            client=client_obj,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"notiz": "klartext"},
            created_by=user,
        )
        history = EventHistory.objects.create(
            event=event,
            changed_by=user,
            action=EventHistory.Action.CREATE,
            data_after={"notiz": "klartext"},
        )
        return event, history

    def test_client_with_held_event_survives_trash_expiry(self, facility, lead_user, staff_user):
        """Refs #1066: Aktiver Legal Hold auf einem Event blockiert die
        Trash-Anonymisierung des Eltern-Klienten (Spoliation-Schutz) —
        ungehaltene Klienten derselben Facility werden weiter anonymisiert."""
        settings_obj = self._make_settings(facility, trash_days=30)

        held_client = self._make_expired_trash_client(facility, staff_user, lead_user, "P-Held")
        event, history = self._make_event_with_history(facility, held_client, staff_user)
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Litigation",
            created_by=lead_user,
        )
        plain_client = self._make_expired_trash_client(facility, staff_user, lead_user, "P-Plain")

        count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        held_client.refresh_from_db()
        plain_client.refresh_from_db()
        history.refresh_from_db()

        assert count == 1
        assert held_client.pseudonym == "P-Held"
        assert plain_client.pseudonym.startswith("Gelöscht-")
        assert history.data_after == {"notiz": "klartext"}

    def test_dry_run_count_excludes_clients_with_held_events(self, facility, lead_user, staff_user):
        """Refs #1066: dry_run zählt gehaltene Klienten nicht mit."""
        settings_obj = self._make_settings(facility, trash_days=30)
        held_client = self._make_expired_trash_client(facility, staff_user, lead_user, "P-DryHeld")
        event, _ = self._make_event_with_history(facility, held_client, staff_user)
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Litigation",
            created_by=lead_user,
        )

        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj, dry_run=True) == 0

    def test_directly_held_client_is_excluded(self, facility, lead_user, staff_user):
        """Refs #1066: Auch ein direkt gehaltener Klient (target_type="Client")
        wird vom Trash-Ablauf ausgenommen."""
        settings_obj = self._make_settings(facility, trash_days=30)
        held_client = self._make_expired_trash_client(facility, staff_user, lead_user, "P-ClientHold")
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=held_client.pk,
            reason="Audit",
            created_by=lead_user,
        )

        count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        held_client.refresh_from_db()
        assert count == 0
        assert held_client.pseudonym == "P-ClientHold"

    def test_dismissed_hold_does_not_block_anonymization(self, facility, lead_user, staff_user):
        """Refs #1066: Nur AKTIVE Holds schieben auf — nach Aufhebung muss
        die DSGVO-Anonymisierung im nächsten Lauf nachgeholt werden."""
        settings_obj = self._make_settings(facility, trash_days=30)
        former_held = self._make_expired_trash_client(facility, staff_user, lead_user, "P-Dismissed")
        event, _ = self._make_event_with_history(facility, former_held, staff_user)
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Litigation beendet",
            created_by=lead_user,
            dismissed_at=timezone.now(),
            dismissed_by=lead_user,
        )

        count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        former_held.refresh_from_db()
        assert count == 1
        assert former_held.pseudonym.startswith("Gelöscht-")


# ---------------------------------------------------------------------------
# View-Layer Tests (Refs #922 — Coverage-Lift)
# ---------------------------------------------------------------------------

from django.urls import reverse  # noqa: E402


class TestClientDeleteRequestViewLayer:
    def test_get_renders_confirm_template(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        url = reverse("core:client_delete_request", args=[client_identified.pk])
        response = client.get(url)
        assert response.status_code == 200

    def test_post_empty_reason_redirects_with_error(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        url = reverse("core:client_delete_request", args=[client_identified.pk])
        response = client.post(url, {"reason": "   "})
        assert response.status_code == 302
        msgs = [m.message for m in response.wsgi_request._messages]
        assert any("Begruendung" in m or "Begründung" in m for m in msgs)

    def test_post_with_reason_creates_deletion_request(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        before = DeletionRequest.objects.count()
        url = reverse("core:client_delete_request", args=[client_identified.pk])
        response = client.post(url, {"reason": "Inaktiv seit >2 Jahren"})
        assert response.status_code == 302
        assert DeletionRequest.objects.count() == before + 1


class TestClientRestoreViewLayer:
    def test_restore_brings_back_deleted_client(self, client, admin_user, client_identified):
        client_identified.is_deleted = True
        client_identified.save(update_fields=["is_deleted"])
        client.force_login(admin_user)
        url = reverse("core:client_restore", args=[client_identified.pk])
        response = client.post(url)
        assert response.status_code == 302
        client_identified.refresh_from_db()
        assert client_identified.is_deleted is False

    def test_restore_404_for_non_deleted(self, client, admin_user, client_identified):
        client.force_login(admin_user)
        url = reverse("core:client_restore", args=[client_identified.pk])
        response = client.post(url)
        assert response.status_code == 404


class TestClientTrashView:
    def test_lists_deleted_clients(self, client, admin_user, client_identified):
        client_identified.is_deleted = True
        client_identified.deleted_at = timezone.now()
        client_identified.deleted_by = admin_user
        client_identified.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])
        client.force_login(admin_user)
        url = reverse("core:client_trash")
        response = client.get(url)
        assert response.status_code == 200
        assert client_identified in list(response.context["clients"])

    def test_does_not_list_non_deleted(self, client, admin_user, client_identified):
        client.force_login(admin_user)
        url = reverse("core:client_trash")
        response = client.get(url)
        assert client_identified not in list(response.context["clients"])


@pytest.mark.django_db(transaction=True)
class TestRetentionTrashAnonymizationOverlappingRuns:
    """Refs #2013 (Cross-Review): zwei GLEICHZEITIGE Nachtlaeufe.

    Der ``anonymized_at``-Filter allein macht den Lauf nur gegen spaetere
    Laeufe idempotent. Ueberlappen zwei ``enforce_retention``-Prozesse
    (Cron-Ueberlauf, manueller Lauf neben dem naechtlichen), lesen beide
    dieselbe Zeile, bevor der erste committet — ohne Zeilensperre und
    Marker-Recheck redigierten beide und schrieben beide eine
    ``CLIENT_ANONYMIZED``-Zeile. Das Ticket nennt genau diesen Fall
    („Bei ueberlappenden Laeufen duplizieren sich Auditzeilen zusaetzlich").

    Echt nebenlaeufig ueber einen zweiten Thread mit eigener DB-Verbindung
    (Muster ``test_offline_sync_idempotency.py``), deshalb
    ``transaction=True``.
    """

    def _make_settings(self, facility, trash_days=30):
        from core.models.settings import Settings

        settings_obj, _ = Settings.objects.get_or_create(
            facility=facility,
            defaults={"client_trash_days": trash_days},
        )
        if settings_obj.client_trash_days != trash_days:
            settings_obj.client_trash_days = trash_days
            settings_obj.save()
        return settings_obj

    def test_two_parallel_runs_anonymize_each_record_once(self, facility, staff_user):
        from django.db import close_old_connections

        settings_obj = self._make_settings(facility, trash_days=30)
        expired = Client.objects.create(facility=facility, pseudonym="P-Parallel")
        expired.soft_delete(user=staff_user)
        Client.objects.filter(pk=expired.pk).update(deleted_at=timezone.now() - timedelta(days=31))

        results = {}
        barrier = threading.Barrier(2, timeout=10)

        def _run(slot):
            try:
                # Beide Laeufe betreten die Selektion moeglichst zeitgleich.
                barrier.wait()
                results[slot] = anonymize_eligible_soft_deleted_clients(facility, settings_obj)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=_run, args=(slot,)) for slot in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "Deadlock: ein Lauf haengt in der Zeilensperre"

        # Genau einer der beiden hat den Datensatz verarbeitet.
        assert sorted(results.values()) == [0, 1], results
        expired.refresh_from_db()
        assert expired.anonymized_at is not None
        assert expired.pseudonym.startswith("Gelöscht-")
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.CLIENT_ANONYMIZED,
                target_id=str(expired.pk),
            ).count()
            == 1
        )
