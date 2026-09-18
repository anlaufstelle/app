"""Wire-Up: K-Anonymisierung im Retention-Pfad (Refs #776, #780).

Vier Cases:

1. ``k=5`` mit ≥5 Klienten in derselben Aequivalenzklasse → ``is_k_anonymous``
   liefert True.
2. ``k=5`` mit <5 Klienten → ``is_k_anonymous`` liefert False.
3. ``Settings.retention_use_k_anonymization=False`` (Default) → Pipeline ruft
   ``client.anonymize()`` (Hard-Anonymize), Pseudonym beginnt mit ``Gelöscht-``.
4. ``Settings.retention_use_k_anonymization=True`` → Pipeline prueft zuerst
   ``is_k_anonymous(client, k=facility.settings.k_anonymity_threshold)`` und
   ruft dann ``k_anonymize_client(client)`` (Refs #2265: der Parameter ``k``
   wurde dort entfernt, ungenutzt — die Schwelle wirkt ausschliesslich ueber
   ``is_k_anonymous``), Pseudonym beginnt mit ``anon-`` und
   ``k_anonymized=True``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import AuditLog, Client, Event, LegalHold, Settings
from core.retention.anonymization import anonymize_clients
from core.services.compliance import is_k_anonymous


def _make_client(
    facility, *, pseudonym, age_cluster=Client.AgeCluster.AGE_27_PLUS, stage=Client.ContactStage.IDENTIFIED
):
    return Client.objects.create(
        facility=facility,
        contact_stage=stage,
        age_cluster=age_cluster,
        pseudonym=pseudonym,
    )


@pytest.mark.django_db
class TestBucketExcludesTombstones:
    """Security (Refs #1442): ``count_clients_in_bucket`` darf hart-anonymisierte
    ``Gelöscht-``-Tombstones NICHT mitzaehlen. Sie tragen keine echten
    Quasi-Identifikatoren mehr (age_cluster auf UNKNOWN zurueckgesetzt) und sind
    am Pseudonym-Praefix erkennbar — ein Angreifer mit DB-Read filtert sie weg,
    das effektive k faellt unter die Schwelle. Bereits k-anonymisierte Zeilen
    (``k_anonymized=True``, ``anon-``-Pseudonym) bleiben dagegen gezaehlt: sie
    erhalten echte Quasi-Identifikatoren und vergroessern die echte
    Anonymitaetsmenge ehrlich."""

    def test_geloescht_tombstones_do_not_pad_bucket(self, facility):
        # Ziel + 4 Gelöscht-Tombstones in derselben Aequivalenzklasse. Vor dem
        # Fix ergibt count=5 >= k -> faelschlich k-anonym (der Bug); nach dem
        # Fix zaehlt nur das Ziel -> 1 < 5 -> False.
        target = _make_client(
            facility,
            pseudonym="P-target",
            age_cluster=Client.AgeCluster.UNKNOWN,
            stage=Client.ContactStage.IDENTIFIED,
        )
        for i in range(4):
            _make_client(
                facility,
                pseudonym=f"Gelöscht-{i:08d}",
                age_cluster=Client.AgeCluster.UNKNOWN,
                stage=Client.ContactStage.IDENTIFIED,
            )
        assert is_k_anonymous(target, k=5) is False

    def test_k_anonymized_peers_still_count(self, facility):
        # Ziel + 4 echte Genossen, zwei davon bereits k-anonymisiert
        # (k_anonymized=True, anon-Pseudonym). Diese bleiben Teil der echten
        # Anonymitaetsmenge -> bucket=5 >= k -> True.
        target = _make_client(
            facility,
            pseudonym="P-target",
            age_cluster=Client.AgeCluster.AGE_27_PLUS,
            stage=Client.ContactStage.IDENTIFIED,
        )
        for i in range(4):
            peer = _make_client(
                facility,
                pseudonym=f"anon-{i:012d}",
                age_cluster=Client.AgeCluster.AGE_27_PLUS,
                stage=Client.ContactStage.IDENTIFIED,
            )
            if i < 2:
                peer.k_anonymized = True
                peer.save(update_fields=["k_anonymized"])
        assert is_k_anonymous(target, k=5) is True


@pytest.mark.django_db
class TestKAnonymity:
    def test_pos_bucket_at_or_above_k_returns_true(self, facility):
        # 5 Klienten mit denselben (age_cluster, contact_stage) → bucket=5
        for i in range(5):
            _make_client(facility, pseudonym=f"P-{i}")
        target = _make_client(facility, pseudonym="P-target")
        # bucket umfasst alle 6 Klienten in dieser Aequivalenzklasse
        assert is_k_anonymous(target, k=5) is True

    def test_neg_bucket_below_k_returns_false(self, facility):
        # Nur 2 Klienten (target + 1) → bucket=2 < k=5
        _make_client(facility, pseudonym="P-1")
        target = _make_client(facility, pseudonym="P-target")
        assert is_k_anonymous(target, k=5) is False

    def test_model_default_is_hard_delete(self, facility):
        """#1311: Client-Level-Retention-k-Anon bleibt per Default AUS.

        Hard-Delete ist die staerkere, fail-safe Datenschutz-Voreinstellung (der
        Datensatz wird zerstoert statt nur generalisiert). K-Anon im Retention-Pfad
        erhaelt generalisierte Datensaetze und ist damit eine bewusste
        Aufbewahrungs-Abwaegung, die eine Einrichtung aktiv opt-in setzen muss;
        ein Default-``AN`` waere ein Compliance-Regress. Entscheidung dokumentiert
        in ``docs/security-notes.md`` (§ K-Anonymitaet …) und
        ``docs/adr/023-k-anonymization-statistik.md`` (Update 2026-07-11).
        """
        settings_obj = Settings.objects.create(facility=facility)
        assert settings_obj.retention_use_k_anonymization is False

    def test_setting_disabled_uses_hard_anonymize(self, facility, staff_user, doc_type_contact, client_identified):
        """Setting=False (Default) → ``client.anonymize()``-Pfad (Refs #780)."""
        Settings.objects.create(
            facility=facility,
            retention_use_k_anonymization=False,
            k_anonymity_threshold=5,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            is_deleted=True,
            created_by=staff_user,
        )

        anonymize_clients(facility, dry_run=False)

        client_identified.refresh_from_db()
        assert client_identified.pseudonym.startswith("Gelöscht-")
        assert not client_identified.pseudonym.startswith("anon-")
        assert client_identified.k_anonymized is False

    def test_setting_enabled_uses_k_anonymize(self, facility, staff_user, doc_type_contact, client_identified):
        """Setting=True → ``k_anonymize_client()``-Pfad mit Schwelle aus
        ``k_anonymity_threshold`` (Refs #780, Pfad A im Issue-Body)."""
        Settings.objects.create(
            facility=facility,
            retention_use_k_anonymization=True,
            k_anonymity_threshold=5,
        )
        # Security N5: k wird jetzt erzwungen — der Ziel-Client braucht eine
        # ehrlich besetzte Aequivalenzklasse (>= k). ``client_identified`` hat
        # age_cluster=UNKNOWN + contact_stage=IDENTIFIED; die 4 Genossen matchen
        # diesen Bucket (ohne geloeschte Events, daher keine Kandidaten).
        for i in range(4):
            _make_client(
                facility,
                pseudonym=f"Peer-{i}",
                age_cluster=Client.AgeCluster.UNKNOWN,
                stage=Client.ContactStage.IDENTIFIED,
            )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            is_deleted=True,
            created_by=staff_user,
        )

        anonymize_clients(facility, dry_run=False)

        client_identified.refresh_from_db()
        assert client_identified.pseudonym.startswith("anon-"), "K-Anon-Pfad muss greifen, sobald Setting aktiv ist."
        assert not client_identified.pseudonym.startswith("Gelöscht-")
        assert client_identified.k_anonymized is True
        assert client_identified.notes == ""
        assert client_identified.is_active is False

    def test_enforce_retention_command_uses_k_anon_when_enabled(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        """End-to-End: ``call_command('enforce_retention')`` wendet den
        K-Anon-Pfad an, wenn das Facility-Setting es verlangt (Refs #780)."""
        Settings.objects.create(
            facility=facility,
            retention_use_k_anonymization=True,
            k_anonymity_threshold=5,
        )
        # Security N5: siehe test_setting_enabled_uses_k_anonymize — die
        # Aequivalenzklasse muss ehrlich >= k besetzt sein, damit der K-Anon-
        # Pfad greift. 4 Genossen im Bucket (UNKNOWN, IDENTIFIED).
        for i in range(4):
            _make_client(
                facility,
                pseudonym=f"Peer-{i}",
                age_cluster=Client.AgeCluster.UNKNOWN,
                stage=Client.ContactStage.IDENTIFIED,
            )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            is_deleted=True,
            created_by=staff_user,
        )

        call_command("enforce_retention")

        client_identified.refresh_from_db()
        assert client_identified.pseudonym.startswith("anon-")
        assert client_identified.k_anonymized is True


@pytest.mark.django_db
class TestKAnonEnforcement:
    """Security N5: k wird jetzt erzwungen — unterbesetzte Buckets (< k)
    fallen fail-safe auf Hard-Anonymize zurueck statt faelschlich als
    k-anonymisiert markiert zu werden."""

    def _settings(self, facility, k=5):
        return Settings.objects.create(facility=facility, retention_use_k_anonymization=True, k_anonymity_threshold=k)

    def _make_candidate(self, facility, staff_user, doc_type, pseudonym, **kwargs):
        c = _make_client(facility, pseudonym=pseudonym, **kwargs)
        Event.objects.create(
            facility=facility,
            client=c,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            is_deleted=True,
            created_by=staff_user,
        )
        return c

    def test_singleton_bucket_falls_back_to_hard_anonymize(self, facility, staff_user, doc_type_contact):
        self._settings(facility, k=5)
        target = self._make_candidate(
            facility,
            staff_user,
            doc_type_contact,
            "Solo-01",
            age_cluster=Client.AgeCluster.U18,
        )
        result = anonymize_clients(facility, dry_run=False)
        target.refresh_from_db()
        assert result["count"] == 1
        assert target.k_anonymized is False, "Singleton-Bucket darf NICHT als k-anonym gelten (N5)"
        assert target.pseudonym.startswith("Gelöscht-"), "Fallback muss Hard-Anonymize sein"

    def test_full_bucket_is_k_anonymized(self, facility, staff_user, doc_type_contact):
        self._settings(facility, k=5)
        target = self._make_candidate(
            facility,
            staff_user,
            doc_type_contact,
            "Bucket-00",
            age_cluster=Client.AgeCluster.AGE_27_PLUS,
        )
        # Refs #1935: Retention-Festschreibung — der K-Anon-Bridge-Pfad ruft
        # _redact_live_events NICHT auf (Eintrittsbedingung active_events == 0),
        # darf idempotency_key/idempotency_payload_hash also nicht anfassen.
        event = Event.objects.get(client=target)
        event.idempotency_key = "residueprobe-kanon-key"
        event.idempotency_payload_hash = "e" * 64
        event.save(update_fields=["idempotency_key", "idempotency_payload_hash"])
        # 4 Bucket-Genossen OHNE geloeschte Events — zaehlen fuer die
        # Aequivalenzklasse, sind aber keine Anonymisierungs-Kandidaten.
        for i in range(1, 5):
            _make_client(
                facility,
                pseudonym=f"Bucket-{i:02d}",
                age_cluster=Client.AgeCluster.AGE_27_PLUS,
            )
        anonymize_clients(facility, dry_run=False)
        target.refresh_from_db()
        assert target.k_anonymized is True
        assert target.pseudonym.startswith("anon-")
        event.refresh_from_db()
        assert event.idempotency_key == "residueprobe-kanon-key"
        assert event.idempotency_payload_hash == "e" * 64

    def test_mixed_buckets_audit_both_categories(self, facility, staff_user, doc_type_contact):
        self._settings(facility, k=5)
        self._make_candidate(
            facility,
            staff_user,
            doc_type_contact,
            "Solo-02",
            age_cluster=Client.AgeCluster.U18,
        )
        self._make_candidate(
            facility,
            staff_user,
            doc_type_contact,
            "Voll-00",
            age_cluster=Client.AgeCluster.AGE_27_PLUS,
        )
        for i in range(1, 5):
            _make_client(
                facility,
                pseudonym=f"Voll-{i:02d}",
                age_cluster=Client.AgeCluster.AGE_27_PLUS,
            )
        anonymize_clients(facility, dry_run=False)
        categories = set(
            AuditLog.objects.filter(action=AuditLog.Action.DELETE).values_list("detail__category", flat=True)
        )
        assert {"client_k_anonymized", "client_anonymized"} <= categories


@pytest.mark.django_db
class TestLegalHoldBlocksBridge:
    """Refs #1756: Ein aktiver Legal Hold blockiert auch die K-Anon-Bridge.

    ``anonymize_eligible_soft_deleted_clients`` (Trash-Expiry) nimmt Klienten
    mit aktivem Hold — auf der Person ODER einem ihrer Events — seit #1066
    aus (Spoliations-Schutz). ``anonymize_clients`` tat das nicht: der
    Retention-Pfad anonymisierte den Klienten trotz laufender
    Aufbewahrungspflicht und redigierte seit #1751 zusaetzlich die
    ``LegalHold.reason`` des **noch aktiven** Holds.

    Fail-safe-Richtung ist hier die **Aufbewahrung**: die Retention-Zusage
    wird aufgeschoben (ein spaeterer Lauf holt sie nach, sobald der Hold
    aufgehoben/abgelaufen ist), waehrend der unterbesetzte Bucket aus
    Security-Review N5 in die andere Richtung faellt (staerker loeschen =
    Hard-Anonymize). Beide Male gewinnt der schwerer reparierbare Schaden.
    """

    HOLD_REASON = "Ermittlungsverfahren 42 Js 1/26"

    def _settings(self, facility, *, k_anon=True, k=5):
        return Settings.objects.create(facility=facility, retention_use_k_anonymization=k_anon, k_anonymity_threshold=k)

    def _make_candidate(self, facility, staff_user, doc_type, pseudonym, *, events=1, **kwargs):
        """Klient mit ausschliesslich soft-geloeschten Events = Bridge-Kandidat.

        Gibt ``(client, erstes_event)`` zurueck; ``events > 1`` legt weitere,
        ungehaltene Events an (Mehr-Event-Fall des Ausschlusses).
        """
        c = _make_client(facility, pseudonym=pseudonym, **kwargs)
        created = [
            Event.objects.create(
                facility=facility,
                client=c,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={"dauer": 1},
                is_deleted=True,
                created_by=staff_user,
            )
            for _ in range(events)
        ]
        return c, created[0]

    def _fill_bucket(self, facility, n=4, prefix="Peer"):
        """Bucket-Genossen ohne Events — zaehlen fuer k, sind keine Kandidaten."""
        for i in range(n):
            _make_client(facility, pseudonym=f"{prefix}-{i}")

    def _hold(self, facility, user, target_type, target_id, **kwargs):
        return LegalHold.objects.create(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            reason=self.HOLD_REASON,
            created_by=user,
            **kwargs,
        )

    def test_client_hold_blocks_bridge(self, facility, staff_user, lead_user, doc_type_contact):
        """Direkter Hold auf der Person (``target_type="Client"``)."""
        self._settings(facility)
        self._fill_bucket(facility)
        held, _ = self._make_candidate(facility, staff_user, doc_type_contact, "P-ClientHold")
        plain, _ = self._make_candidate(facility, staff_user, doc_type_contact, "P-Plain")
        hold = self._hold(facility, lead_user, "Client", held.pk)

        result = anonymize_clients(facility, dry_run=False)

        held.refresh_from_db()
        plain.refresh_from_db()
        hold.refresh_from_db()
        assert result["count"] == 1, "Nur der ungehaltene Klient darf gezaehlt werden"
        assert held.pseudonym == "P-ClientHold"
        assert held.k_anonymized is False
        assert hold.reason == self.HOLD_REASON, "Begruendung eines AKTIVEN Holds darf nicht redigiert werden"
        assert plain.k_anonymized is True, "Positiv-Kontrolle: der Lauf agiert real"

    def test_event_hold_blocks_bridge(self, facility, staff_user, lead_user, doc_type_contact):
        """Hold auf EINEM Event der Person — der Eltern-Klient bleibt stehen.

        Der gehaltene Klient hat bewusst drei Events, nur eines davon unter
        Hold: der Ausschluss muss „mindestens ein gehaltener Event" bedeuten,
        nicht „alle" (SQL: ``NOT EXISTS``-Subquery, kein Join — die
        ``Count("events")``-Annotation der Kandidatenabfrage bleibt exakt).
        """
        self._settings(facility)
        self._fill_bucket(facility)
        held, held_event = self._make_candidate(facility, staff_user, doc_type_contact, "P-EventHold", events=3)
        plain, _ = self._make_candidate(facility, staff_user, doc_type_contact, "P-Plain")
        hold = self._hold(facility, lead_user, "Event", held_event.pk)

        result = anonymize_clients(facility, dry_run=False)

        held.refresh_from_db()
        plain.refresh_from_db()
        hold.refresh_from_db()
        assert result["count"] == 1
        assert held.pseudonym == "P-EventHold"
        assert held.k_anonymized is False
        assert hold.reason == self.HOLD_REASON
        assert plain.k_anonymized is True

    def test_hard_path_also_excludes_held_clients(self, facility, staff_user, lead_user, doc_type_contact):
        """Der Ausschluss greift VOR der Pfadwahl — auch ohne K-Anon-Setting."""
        self._settings(facility, k_anon=False)
        held, held_event = self._make_candidate(facility, staff_user, doc_type_contact, "P-HardHold")
        plain, _ = self._make_candidate(facility, staff_user, doc_type_contact, "P-HardPlain")
        self._hold(facility, lead_user, "Event", held_event.pk)

        result = anonymize_clients(facility, dry_run=False)

        held.refresh_from_db()
        plain.refresh_from_db()
        assert result["count"] == 1
        assert held.pseudonym == "P-HardHold"
        assert plain.pseudonym.startswith("Gelöscht-")

    def test_dry_run_count_excludes_held_clients(self, facility, staff_user, lead_user, doc_type_contact):
        """dry_run meldet dieselbe Menge wie der echte Lauf (Muster aus #1066)."""
        self._settings(facility)
        held, held_event = self._make_candidate(facility, staff_user, doc_type_contact, "P-DryHeld")
        self._hold(facility, lead_user, "Event", held_event.pk)

        assert anonymize_clients(facility, dry_run=True)["count"] == 0
        held.refresh_from_db()
        assert held.pseudonym == "P-DryHeld"

    def test_dismissed_and_expired_holds_do_not_block(self, facility, staff_user, lead_user, doc_type_contact):
        """Nur AKTIVE Holds schieben auf — sonst waere die Loeschung dauerhaft ausgesetzt.

        Aufgehobene und abgelaufene Holds muessen weiterhin durchlaufen; ihre
        Begruendung wird dabei mitredigiert (#1751), sonst blieben genau die
        Alt-Zeilen mit Klartext-Begruendung fuer immer stehen.
        """
        self._settings(facility)
        self._fill_bucket(facility)
        dismissed_client, dismissed_event = self._make_candidate(facility, staff_user, doc_type_contact, "P-Dismissed")
        dismissed = self._hold(
            facility,
            lead_user,
            "Event",
            dismissed_event.pk,
            dismissed_at=timezone.now(),
            dismissed_by=lead_user,
        )
        expired_client, _ = self._make_candidate(facility, staff_user, doc_type_contact, "P-Expired")
        expired = self._hold(
            facility,
            lead_user,
            "Client",
            expired_client.pk,
            expires_at=timezone.localdate() - timedelta(days=1),
        )

        result = anonymize_clients(facility, dry_run=False)

        dismissed_client.refresh_from_db()
        expired_client.refresh_from_db()
        dismissed.refresh_from_db()
        expired.refresh_from_db()
        assert result["count"] == 2
        assert dismissed_client.k_anonymized is True
        assert expired_client.k_anonymized is True
        assert dismissed.reason == "[Anonymisiert]"
        assert expired.reason == "[Anonymisiert]"

    def test_held_client_still_counts_towards_bucket(self, facility, staff_user, lead_user, doc_type_contact):
        """Bucket-Entscheidung: ein uebersprungener Klient bleibt Bucket-Mitglied.

        Der Hold **verschiebt** die Anonymisierung nur — die Zeile bleibt mit
        echten Quasi-Identifikatoren (``age_cluster``, ``contact_stage``) in der
        DB und gehoert damit ehrlich zur Aequivalenzklasse, genau wie jeder
        andere noch nicht anonymisierte Klient (dieselbe Begruendung wie fuer
        die mitgezaehlten ``k_anonymized``-Zeilen in ``count_clients_in_bucket``,
        Refs #1442). Ein Abzug waere die falsche Richtung: er wuerde das k
        kuenstlich druecken und Mit-Klienten grundlos in den Hard-Pfad kippen —
        obwohl die Zeile real existiert und niemanden weniger schuetzt.

        Setup: Ziel + gehaltener Klient + 3 Genossen = Bucket 5 = k. Zaehlte der
        gehaltene Klient nicht mit, faende sich das Ziel bei 4 < k wieder und
        fiele fail-safe auf Hard-Anonymize zurueck.
        """
        self._settings(facility, k=5)
        self._fill_bucket(facility, n=3)
        target, _ = self._make_candidate(facility, staff_user, doc_type_contact, "P-Target")
        held, held_event = self._make_candidate(facility, staff_user, doc_type_contact, "P-Held")
        self._hold(facility, lead_user, "Event", held_event.pk)

        result = anonymize_clients(facility, dry_run=False)

        target.refresh_from_db()
        held.refresh_from_db()
        assert result["count"] == 1
        assert target.k_anonymized is True, "Der gehaltene Klient muss als Bucket-Genosse zaehlen"
        assert held.k_anonymized is False
