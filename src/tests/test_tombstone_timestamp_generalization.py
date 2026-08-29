"""Zeitliche Generalisierung der Tombstone-Ereignisse (Refs #2047).

Nach der Art.-17-Tilgung blieb je Tombstone die vollstaendige, personen-
verknuepfte Timeline aus exakten ``occurred_at``-Werten stehen. Die
k-Anonymitaets-Zusicherung deckt sie nicht ab: sie attestiert
``(age_cluster, contact_stage)``, nicht das zeitlich hochdimensionale Muster.
Mit Aussenwissen („Krisenintake am 03.05. gegen halb drei") bleibt ein
Single-out bei Einrichtungsgroessen plausibel — der Datensatz waere damit
pseudonymisiert, nicht anonym.

**Entscheid: Monatsbucket.** ``occurred_at`` wird auf den Monatsersten 00:00
im lokalen Kalender generalisiert. Die Statistik arbeitet auf Monatsebene, der
Verlust ist dort also null; gegenueber Tagesgenauigkeit ist der Monatsbucket
deutlich robuster gegen Single-out.

Die Bucket-Grenze liegt bewusst im **lokalen** Kalender (``TIME_ZONE``), nicht
in UTC: ein Ereignis am 1. Maerz um 00:30 Berliner Zeit liegt in UTC noch im
Februar. Ein UTC-Bucket schoebe es in den Vormonat und verfaelschte damit
genau die Monatsstatistik, deren Erhalt die Abwaegung traegt.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from core.models import Client, Event
from core.services.client import anonymize_client, anonymize_client_now, export_client_data

BERLIN = ZoneInfo("Europe/Berlin")


def _aware(year, month, day, hour=0, minute=0):
    """Ein Zeitpunkt im lokalen Kalender (Europe/Berlin), aware."""
    return datetime(year, month, day, hour, minute, tzinfo=BERLIN)


def _make_event(facility, client_obj, doc_type, user, occurred_at, *, is_deleted=False):
    event = Event.objects.create(
        facility=facility,
        client=client_obj,
        document_type=doc_type,
        occurred_at=occurred_at,
        created_by=user,
        data_json={"notiz": "Krisenintake"},
    )
    if is_deleted:
        # ``Event`` traegt nur ``is_deleted`` (kein ``deleted_at`` wie die
        # ``SoftDeletableModel``-Aggregate) — die Retention haengt am Ereignis
        # ueber ``occurred_at``, nicht ueber einen eigenen Loeschzeitpunkt.
        event.is_deleted = True
        event.save(update_fields=["is_deleted"])
    return event


def _local(dt):
    return timezone.localtime(dt)


def _assert_month_bucket(event, year, month):
    local = _local(Event.objects.get(pk=event.pk).occurred_at)
    assert (local.year, local.month) == (year, month)
    assert (local.day, local.hour, local.minute, local.second, local.microsecond) == (1, 0, 0, 0, 0)


# ---------------------------------------------------------------------------
# Hard-Pfad
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHardPath:
    def test_live_and_trashed_events_are_bucketed(self, facility, client_identified, doc_type_contact, staff_user):
        live = _make_event(facility, client_identified, doc_type_contact, staff_user, _aware(2026, 5, 3, 14, 32))
        trashed = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            _aware(2026, 4, 17, 9, 5),
            is_deleted=True,
        )
        anonymize_client(client_identified, user=staff_user)
        _assert_month_bucket(live, 2026, 5)
        # Auch das soft-geloeschte Ereignis: sein ``occurred_at`` ueberlebt den
        # Soft-Delete und traegt dieselbe Timeline-Information.
        _assert_month_bucket(trashed, 2026, 4)

    def test_month_survives_local_midnight_boundary(self, facility, client_identified, doc_type_contact, staff_user):
        """1. März 00:30 Berlin ist in UTC noch der 28. Februar.

        Der Bucket muss im lokalen Kalender rechnen — sonst wandert das
        Ereignis in den Vormonat und verfaelscht die Monatsstatistik.
        """
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, _aware(2026, 3, 1, 0, 30))
        anonymize_client(client_identified, user=staff_user)
        _assert_month_bucket(event, 2026, 3)

    def test_no_event_keeps_an_exact_time(self, facility, client_identified, doc_type_contact, staff_user):
        for day, hour in ((2, 8), (14, 23), (28, 16)):
            _make_event(facility, client_identified, doc_type_contact, staff_user, _aware(2026, 6, day, hour, 41))
        anonymize_client(client_identified, user=staff_user)
        for event in Event.objects.filter(client=client_identified):
            local = _local(event.occurred_at)
            assert (local.day, local.hour, local.minute) == (1, 0, 0)

    def test_is_idempotent(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, _aware(2026, 5, 3, 14, 32))
        anonymize_client(client_identified, user=staff_user)
        first = Event.objects.get(pk=event.pk).occurred_at
        anonymize_client(client_identified, user=staff_user)
        assert Event.objects.get(pk=event.pk).occurred_at == first

    def test_contact_stage_is_reset_like_age_cluster(self, client_qualified, staff_user):
        """Symmetrie-Befund: der Hard-Pfad zerstoerte ``age_cluster``, liess
        ``contact_stage`` aber stehen."""
        anonymize_client(client_qualified, user=staff_user)
        client_qualified.refresh_from_db()
        assert client_qualified.age_cluster == Client.AgeCluster.UNKNOWN
        assert client_qualified.contact_stage == Client.ContactStage.IDENTIFIED

    def test_events_stay_linked_to_the_client(self, facility, client_identified, doc_type_contact, staff_user):
        """Die Generalisierung ersetzt keine Löschung — die Zeile bleibt
        Statistik-Aggregat der Person."""
        _make_event(facility, client_identified, doc_type_contact, staff_user, _aware(2026, 5, 3, 14, 32))
        anonymize_client(client_identified, user=staff_user)
        assert Event.objects.filter(client=client_identified).count() == 1


# ---------------------------------------------------------------------------
# Einstieg „Sofort anonymisieren" (Papierkorb)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnonymizeNow:
    def test_manual_entry_generalizes(self, facility, client_identified, doc_type_contact, staff_user, admin_user):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, _aware(2026, 5, 3, 14, 32))
        client_identified.is_deleted = True
        client_identified.deleted_at = timezone.now()
        client_identified.save(update_fields=["is_deleted", "deleted_at"])
        anonymize_client_now(client_identified, admin_user)
        _assert_month_bucket(event, 2026, 5)


# ---------------------------------------------------------------------------
# K-Anon-Bridge
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestKAnonBridge:
    def test_bridge_generalizes_and_keeps_both_quasi_identifiers(
        self, facility, client_qualified, doc_type_contact, staff_user, settings_obj
    ):
        from core.retention.anonymization import anonymize_clients

        settings_obj.retention_use_k_anonymization = True
        settings_obj.k_anonymity_threshold = 1
        settings_obj.save()
        # Unterscheidbarer Wert statt des Fixture-Defaults ``UNKNOWN`` — sonst
        # pruefte die Erhaltungs-Assertion unten nichts.
        client_qualified.age_cluster = Client.AgeCluster.AGE_18_26
        client_qualified.save(update_fields=["age_cluster"])

        event = _make_event(
            facility,
            client_qualified,
            doc_type_contact,
            staff_user,
            _aware(2026, 5, 3, 14, 32),
            is_deleted=True,
        )
        anonymize_clients(facility, dry_run=False)

        client_qualified.refresh_from_db()
        assert client_qualified.k_anonymized is True
        _assert_month_bucket(event, 2026, 5)
        # Die k-Anonymitaet ruht auf beiden Quasi-Identifikatoren — der
        # K-Anon-Pfad darf sie (anders als der Hard-Pfad) NICHT zerstoeren.
        assert client_qualified.age_cluster == Client.AgeCluster.AGE_18_26
        assert client_qualified.contact_stage == Client.ContactStage.QUALIFIED


# ---------------------------------------------------------------------------
# Statistik-Invariante
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMonthlyAggregateUnchanged:
    def test_month_counts_survive_anonymization(self, facility, client_identified, doc_type_contact, staff_user):
        from django.db.models import Count
        from django.db.models.functions import TruncMonth

        for day in (2, 14, 28):
            _make_event(facility, client_identified, doc_type_contact, staff_user, _aware(2026, 6, day, 11, 0))
        _make_event(facility, client_identified, doc_type_contact, staff_user, _aware(2026, 7, 9, 11, 0))

        def by_month():
            return {
                (row["m"].year, row["m"].month): row["n"]
                for row in Event.objects.filter(client=client_identified)
                .annotate(m=TruncMonth("occurred_at"))
                .values("m")
                .annotate(n=Count("pk"))
            }

        before = by_month()
        anonymize_client(client_identified, user=staff_user)
        assert by_month() == before


# ---------------------------------------------------------------------------
# Auskunft (Art. 15) — Transparenz
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExportTransparency:
    def test_export_flags_the_generalization(self, facility, client_identified, doc_type_contact, admin_user):
        _make_event(facility, client_identified, doc_type_contact, admin_user, _aware(2026, 5, 3, 14, 32))
        anonymize_client(client_identified, user=admin_user)
        data = export_client_data(client_identified, facility, admin_user)
        assert data["client"]["event_timestamps_generalized"] is True

    def test_untouched_client_keeps_the_legacy_shape(self, facility, client_identified, admin_user):
        data = export_client_data(client_identified, facility, admin_user)
        assert "event_timestamps_generalized" not in data["client"]

    def test_pdf_names_the_generalization(self, facility, client_identified, doc_type_contact, admin_user):
        """Cross-Review-Befund 6: der Transparenz-Hinweis muss tatsaechlich im
        Dokument stehen — ``%PDF`` allein sagt darueber nichts."""
        from core.services.client import export_client_data_pdf, render_client_export_html

        _make_event(facility, client_identified, doc_type_contact, admin_user, _aware(2026, 5, 3, 14, 32))
        anonymize_client(client_identified, user=admin_user)

        html = render_client_export_html(client_identified, facility, admin_user)
        assert "Zeitpunkte der Ereignisse" in html
        assert "Auf den Monat generalisiert" in html

        pdf = export_client_data_pdf(client_identified, facility, admin_user)
        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 1000

    def test_untouched_client_pdf_has_no_generalization_note(self, facility, client_identified, admin_user):
        """Gegenprobe: die Zeile darf nur am anonymisierten Datensatz stehen."""
        from core.services.client import render_client_export_html

        html = render_client_export_html(client_identified, facility, admin_user)
        assert "Auf den Monat generalisiert" not in html


# ---------------------------------------------------------------------------
# Paritaets-Nachzug: der Helfer muss in BEIDEN Pfaden laufen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNoResidualExactTimestamp:
    def test_sweep_over_both_paths(self, facility, client_qualified, doc_type_contact, staff_user, settings_obj):
        """PII-Residue-Sweep fuer die Zeitachse — beide Einstiege, eine Aussage."""
        from core.retention.anonymization import anonymize_clients

        settings_obj.retention_use_k_anonymization = True
        settings_obj.k_anonymity_threshold = 1
        settings_obj.save()
        _make_event(
            facility,
            client_qualified,
            doc_type_contact,
            staff_user,
            timezone.localtime(timezone.now()) - timedelta(days=40),
            is_deleted=True,
        )
        anonymize_clients(facility, dry_run=False)
        for event in Event.objects.filter(client=client_qualified):
            local = _local(event.occurred_at)
            assert (local.day, local.hour, local.minute, local.second) == (1, 0, 0, 0)
