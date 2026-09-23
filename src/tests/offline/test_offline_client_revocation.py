"""Personen-genaue Invalidierung ausgelieferter Offline-Bundles (Refs #1760).

Ausgangslage (#1476/R20, #1493): Ein bereits auf ein Geraet uebertragenes
Offline-Bundle traegt bis zu ``BUNDLE_TTL_SECONDS`` (48 h) Klartext — Pseudonym,
Notizen, Fall-Titel, Ereignisdaten. Wird die Person serverseitig anonymisiert,
invalidierte bis #1760 **kein** Server-Signal diesen Bestand:

* Die bestehende Revalidierung (``revalidateCachedClient``) purgt nur bei
  401/404/410 — eine anonymisierte Person existiert aber weiter und liefert
  **200** mit anonymisiertem Bundle.
* Und selbst dieses 200 ueberschreibt den lokalen Klartext NICHT, wenn zu der
  Person ungesyncte Aenderungen offen sind (``"skipped"``-Zweig).

Der Fix ist eine server-gefuehrte **Revoke-Liste**: Jede Anonymisierung
schreibt einen personen-genauen, klartext-freien Eintrag
(``OfflineClientRevocation``: Facility + ehemalige Client-UUID + Zeitstempel),
den der Client bei jedem Sync und beim Boot abholt und gegen seine IndexedDB
anwendet.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Client, OfflineClientRevocation, Settings
from core.services.system import (
    OFFLINE_REVOCATION_RETENTION_SECONDS,
    prune_offline_client_revocations,
    revoke_offline_client_bundles,
)


def _revoked_pks(facility):
    return set(OfflineClientRevocation.objects.filter(facility=facility).values_list("client_pk", flat=True))


@pytest.mark.django_db
class TestRevocationEntryIsWritten:
    """Jeder Anonymisierungs-Einstieg hinterlaesst genau einen Revoke-Eintrag."""

    def test_hard_path_writes_revocation(self, facility, staff_user, client_identified):
        from core.services.client import anonymize_client

        anonymize_client(client_identified, user=staff_user)

        assert _revoked_pks(facility) == {client_identified.pk}

    def test_trash_anonymize_now_writes_revocation(self, facility, admin_user, client_identified):
        from core.services.client import anonymize_client_now

        client_identified.soft_delete(user=admin_user)
        anonymize_client_now(client_identified, admin_user)

        assert _revoked_pks(facility) == {client_identified.pk}

    def test_k_anon_bridge_writes_revocation(self, facility, client_identified):
        """Der K-Anon-Retention-Pfad darf nicht hinter dem Hard-Pfad zurueckbleiben."""
        from core.retention.anonymization import _k_anonymize_with_cascade

        _k_anonymize_with_cascade(client_identified)

        assert _revoked_pks(facility) == {client_identified.pk}

    def test_repeated_anonymization_keeps_single_row(self, facility, staff_user, client_identified):
        """Upsert statt Duplikat — der Zeitstempel wird aufgefrischt."""
        from core.services.client import anonymize_client

        anonymize_client(client_identified, user=staff_user)
        first = OfflineClientRevocation.objects.get(facility=facility, client_pk=client_identified.pk)
        OfflineClientRevocation.objects.filter(pk=first.pk).update(revoked_at=timezone.now() - timedelta(days=1))

        anonymize_client(client_identified, user=staff_user)

        assert OfflineClientRevocation.objects.filter(facility=facility).count() == 1
        refreshed = OfflineClientRevocation.objects.get(pk=first.pk)
        assert refreshed.revoked_at > timezone.now() - timedelta(minutes=1)

    def test_row_carries_no_plaintext(self, facility, staff_user, client_identified):
        """Der Eintrag traegt NUR Facility-Scope, UUID und Zeitstempel — kein Klartext.

        Strukturell geprueft: gaebe es je ein Text-/JSON-Feld, waere der Eintrag
        selbst eine neue PII-Flaeche, die die Anonymisierung ueberlebt.
        """
        from core.services.client import anonymize_client

        anonymize_client(client_identified, user=staff_user)

        concrete = {f.name for f in OfflineClientRevocation._meta.get_fields() if f.concrete}
        assert concrete == {"id", "facility", "client_pk", "revoked_at"}

    def test_revocation_survives_client_row(self, facility, staff_user, client_identified):
        """Kein FK auf ``Client`` — der Eintrag ist an die *ehemalige* UUID gebunden."""
        from core.services.client import anonymize_client

        anonymize_client(client_identified, user=staff_user)
        Client.objects.filter(pk=client_identified.pk).delete()

        assert _revoked_pks(facility) == {client_identified.pk}

    def test_no_extra_audit_action_needed(self, facility, admin_user, client_identified):
        """Die Revoke-Erzeugung haengt am bestehenden Anonymisierungs-Audit.

        Der Papierkorb-Pfad schreibt weiterhin genau EIN ``CLIENT_ANONYMIZED`` —
        kein zusaetzliches Audit-Event fuer die Revoke-Zeile (Refs #1760).
        """
        from core.services.client import anonymize_client_now

        client_identified.soft_delete(user=admin_user)
        anonymize_client_now(client_identified, admin_user)

        assert AuditLog.objects.filter(action=AuditLog.Action.CLIENT_ANONYMIZED).count() == 1


@pytest.mark.django_db
class TestRevocationService:
    def test_revoke_is_idempotent(self, facility, client_identified):
        revoke_offline_client_bundles(client_identified)
        revoke_offline_client_bundles(client_identified)

        assert OfflineClientRevocation.objects.filter(facility=facility).count() == 1

    def test_retention_window_is_twice_the_bundle_ttl(self):
        from core.services.system import BUNDLE_TTL_SECONDS

        assert OFFLINE_REVOCATION_RETENTION_SECONDS == 2 * BUNDLE_TTL_SECONDS

    def test_prune_removes_expired_keeps_fresh(self, facility, staff_user):
        fresh = Client.objects.create(facility=facility, pseudonym="Frisch", created_by=staff_user)
        stale = Client.objects.create(facility=facility, pseudonym="Alt", created_by=staff_user)
        revoke_offline_client_bundles(fresh)
        revoke_offline_client_bundles(stale)
        OfflineClientRevocation.objects.filter(client_pk=stale.pk).update(
            revoked_at=timezone.now() - timedelta(seconds=OFFLINE_REVOCATION_RETENTION_SECONDS + 60)
        )

        removed = prune_offline_client_revocations(facility)

        assert removed == 1
        assert _revoked_pks(facility) == {fresh.pk}

    def test_prune_keeps_entry_just_inside_window(self, facility, staff_user):
        cli = Client.objects.create(facility=facility, pseudonym="Grenzfall", created_by=staff_user)
        revoke_offline_client_bundles(cli)
        OfflineClientRevocation.objects.filter(client_pk=cli.pk).update(
            revoked_at=timezone.now() - timedelta(seconds=OFFLINE_REVOCATION_RETENTION_SECONDS - 60)
        )

        assert prune_offline_client_revocations(facility) == 0
        assert _revoked_pks(facility) == {cli.pk}

    def test_prune_is_facility_scoped(self, facility, other_facility, staff_user):
        mine = Client.objects.create(facility=facility, pseudonym="Meine", created_by=staff_user)
        theirs = Client.objects.create(facility=other_facility, pseudonym="Fremde", created_by=staff_user)
        revoke_offline_client_bundles(mine)
        revoke_offline_client_bundles(theirs)
        OfflineClientRevocation.objects.all().update(
            revoked_at=timezone.now() - timedelta(seconds=OFFLINE_REVOCATION_RETENTION_SECONDS + 60)
        )

        assert prune_offline_client_revocations(facility) == 1
        assert _revoked_pks(other_facility) == {theirs.pk}


@pytest.mark.django_db
class TestRevocationEndpoint:
    url = None

    def setup_method(self):
        self.url = reverse("core:offline_revocations")

    def test_requires_login(self, client):
        response = client.get(self.url)
        assert response.status_code == 302

    def test_assistant_may_read(self, client, assistant_user, facility, client_identified):
        revoke_offline_client_bundles(client_identified)
        client.force_login(assistant_user)

        response = client.get(self.url)

        assert response.status_code == 200
        payload = json.loads(response.content)
        assert payload["revocations"] == [str(client_identified.pk)]

    def test_payload_carries_server_time_and_retention(self, client, staff_user, facility):
        client.force_login(staff_user)

        payload = json.loads(client.get(self.url).content)

        assert payload["revocations"] == []
        assert payload["retention_seconds"] == OFFLINE_REVOCATION_RETENTION_SECONDS
        assert payload["server_time"]

    def test_is_facility_scoped(self, client, staff_user, facility, other_facility, client_identified):
        foreign = Client.objects.create(facility=other_facility, pseudonym="Fremde", created_by=staff_user)
        revoke_offline_client_bundles(client_identified)
        revoke_offline_client_bundles(foreign)
        client.force_login(staff_user)

        payload = json.loads(client.get(self.url).content)

        assert payload["revocations"] == [str(client_identified.pk)]

    def test_expired_entries_are_not_served(self, client, staff_user, facility, client_identified):
        """Auch ohne gelaufenen Prune liefert der Endpoint nur das TTL-Fenster."""
        revoke_offline_client_bundles(client_identified)
        OfflineClientRevocation.objects.all().update(
            revoked_at=timezone.now() - timedelta(seconds=OFFLINE_REVOCATION_RETENTION_SECONDS + 60)
        )
        client.force_login(staff_user)

        payload = json.loads(client.get(self.url).content)

        assert payload["revocations"] == []

    def test_response_is_not_cacheable(self, client, staff_user, facility):
        client.force_login(staff_user)

        response = client.get(self.url)

        assert "no-store" in response["Cache-Control"]

    def test_read_writes_no_audit_trail(self, client, staff_user, facility, client_identified):
        """Der Poll laeuft bei jedem Sync — ein Audit-Eintrag je Abruf wuerde das
        Log fluten und traegt keine Information (die Liste ist PII-frei)."""
        revoke_offline_client_bundles(client_identified)
        client.force_login(staff_user)
        # NACH dem Login zaehlen — der Login schreibt selbst einen Audit-Eintrag.
        before = AuditLog.objects.count()

        client.get(self.url)

        assert AuditLog.objects.count() == before

    def test_anonymized_client_appears_end_to_end(self, client, staff_user, facility, client_identified):
        from core.services.client import anonymize_client

        client.force_login(staff_user)
        assert json.loads(client.get(self.url).content)["revocations"] == []

        anonymize_client(client_identified, user=staff_user)

        assert json.loads(client.get(self.url).content)["revocations"] == [str(client_identified.pk)]


def _per_hour(rate: str) -> int:
    """``"600/h"`` -> 600. Nur die in diesem Projekt genutzten ``/h``-Raten."""
    count, _, period = rate.partition("/")
    assert period == "h", f"Unerwartete Rate-Periode in {rate!r}"
    return int(count)


class TestRevocationEndpointRateLimit:
    """Refs #1760 (Befund 2b): eigenes, grosszuegiges Limit.

    Der Widerrufs-Poll laeuft pro Seitenaufbau (die App nutzt kein hx-boost),
    client-seitig gedrosselt auf einen Lauf pro Minute — also bis zu 60/h allein
    aus dem Boot-Check, dazu der Sync-Pfad. Das an die Bundle-Fetches angelehnte
    ``RATELIMIT_OFFLINE_BUNDLE`` (120/h) war dafuer zu knapp bemessen. Ein
    eigenes Constant sorgt zudem dafuer, dass ein spaeteres Nachschaerfen des
    Bundle-Limits den Sicherheitskanal nicht als Seiteneffekt mitdrosselt.
    """

    def test_has_its_own_more_generous_constant(self):
        from core.constants import RATELIMIT_OFFLINE_BUNDLE, RATELIMIT_OFFLINE_REVOCATIONS

        assert RATELIMIT_OFFLINE_REVOCATIONS != RATELIMIT_OFFLINE_BUNDLE
        assert _per_hour(RATELIMIT_OFFLINE_REVOCATIONS) > _per_hour(RATELIMIT_OFFLINE_BUNDLE)

    def test_budget_covers_the_throttled_boot_poll_with_headroom(self):
        from core.constants import RATELIMIT_OFFLINE_REVOCATIONS

        # 60/h Boot-Poll (1/min-Drossel) + Sync-Pfad; mit deutlichem Puffer.
        assert _per_hour(RATELIMIT_OFFLINE_REVOCATIONS) >= 300

    def test_view_does_not_hang_on_the_bundle_budget(self):
        import inspect

        from core.views.offline import OfflineClientRevocationsView

        source = inspect.getsource(OfflineClientRevocationsView)
        assert "RATELIMIT_OFFLINE_REVOCATIONS" in source
        assert "RATELIMIT_OFFLINE_BUNDLE" not in source

    @pytest.mark.django_db
    def test_das_limit_greift_wirklich(self, client, staff_user, ratelimit_enabled):
        """Refs #1775: Wirk-Test statt ``hasattr(get, "__wrapped__")``.

        Das ``__wrapped__``-Attribut belegte nur, dass ueberhaupt ein Dekorator
        auf ``get`` sitzt. Belegt per Mutationsprobe: ``block=True`` auf
        ``block=False`` zu drehen — das Limit zaehlt dann zwar noch, weist aber
        nichts mehr ab — liess den alten Guard gruen. Genau das ist der Zustand,
        vor dem er schuetzen soll.

        Muster wie ``test_offline_bundle_api.test_rate_limited_after_120_requests``:
        django-ratelimit mit ``block=True`` wirft ``Ratelimited``
        (``PermissionDenied``-Subklasse), die der eigene ``handler403`` auf 429
        abbildet — nicht auf 403, weil der Offline-Client 403 als Rechteentzug
        deutet und lokale Bundles purgen wuerde (Refs #1354).

        ``ratelimit_enabled`` (conftest) nagelt das Zaehlfenster fest — bei 600
        Requests je Lauf ist das die laengste Schleife der Suite und damit die
        groesste Angriffsflaeche fuer einen Fensterwechsel mittendrin
        (Refs #1900).
        """
        from core.constants import RATELIMIT_OFFLINE_REVOCATIONS

        budget = _per_hour(RATELIMIT_OFFLINE_REVOCATIONS)
        client.force_login(staff_user)
        url = reverse("core:offline_revocations")
        for i in range(budget):
            assert client.get(url).status_code == 200, f"Request {i + 1}/{budget} wurde zu frueh abgewiesen"
        assert client.get(url).status_code == 429, f"Request {budget + 1} haette abgewiesen werden muessen"


@pytest.mark.django_db
class TestRetentionRunPrunes:
    """Der naechtliche Lauf raeumt abgelaufene Revoke-Eintraege ab."""

    @pytest.fixture
    def facility_with_settings(self, facility):
        Settings.objects.get_or_create(facility=facility)
        return facility

    def test_enforce_retention_prunes_expired(self, facility_with_settings, staff_user):
        cli = Client.objects.create(facility=facility_with_settings, pseudonym="Alt", created_by=staff_user)
        revoke_offline_client_bundles(cli)
        OfflineClientRevocation.objects.all().update(
            revoked_at=timezone.now() - timedelta(seconds=OFFLINE_REVOCATION_RETENTION_SECONDS + 60)
        )

        call_command("enforce_retention")

        assert not OfflineClientRevocation.objects.exists()

    def test_dry_run_does_not_prune(self, facility_with_settings, staff_user):
        cli = Client.objects.create(facility=facility_with_settings, pseudonym="Alt", created_by=staff_user)
        revoke_offline_client_bundles(cli)
        OfflineClientRevocation.objects.all().update(
            revoked_at=timezone.now() - timedelta(seconds=OFFLINE_REVOCATION_RETENTION_SECONDS + 60)
        )

        call_command("enforce_retention", "--dry-run")

        assert OfflineClientRevocation.objects.count() == 1

    def test_run_marker_reports_pruned_count(self, facility_with_settings, staff_user):
        cli = Client.objects.create(facility=facility_with_settings, pseudonym="Alt", created_by=staff_user)
        revoke_offline_client_bundles(cli)
        OfflineClientRevocation.objects.all().update(
            revoked_at=timezone.now() - timedelta(seconds=OFFLINE_REVOCATION_RETENTION_SECONDS + 60)
        )

        call_command("enforce_retention")

        entry = AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).latest("timestamp")
        assert entry.detail["pruned_offline_revocations"] == 1


@pytest.mark.django_db
def test_model_is_registered_for_rls_derivation():
    """Direkter ``facility``-FK ⇒ das ableitungsbasierte ``EXPECTED_TABLES``
    (``tests/test_rls.py``) erfasst die Tabelle automatisch — der Guard verlangt
    dann eine RLS-Migration. Hier nur die Vorbedingung gepinnt."""
    from tests.test_rls import EXPECTED_TABLES

    assert OfflineClientRevocation._meta.db_table in EXPECTED_TABLES
