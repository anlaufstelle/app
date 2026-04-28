"""Tests für das TTL-/Lease-Konzept des Offline-Read-Cache (WP5, Refs #574).

Das Offline-Bundle enthält drei zusammenhängende Metadatenfelder:

* ``generated_at`` — ISO-Zeitstempel der Server-Serialisierung.
* ``ttl``           — Gültigkeitsdauer in Sekunden (``BUNDLE_TTL_SECONDS``).
* ``expires_at``    — ISO-Zeitstempel, ab dem der Client den Cache verwerfen soll.

Der Server lehnt keine Submits aufgrund eines abgelaufenen Leases ab (die
Zurückweisung passiert über ``expected_updated_at``/Optimistic Locking). Die
hier getesteten Invarianten decken daher:

1. Die Metadaten werden konsistent berechnet (``expires_at = generated_at + ttl``).
2. Ein erneuter Bundle-Fetch nach Ablauf liefert ein *neues* Lease
   (nicht das alte) — Grundvoraussetzung dafür, dass der Client nach
   Ablauf frisch laden kann.
3. Zwischen zwei Fetches wächst ``generated_at`` monoton mit der
   Serverzeit (Regressionsguard gegen Caching auf Server-Seite).

Zusätzlich wird im ``TestSubmitWithStaleLease``-Block dokumentiert, dass der
Server beim Submit eines Events keine Lease-Prüfung macht; die Kollisions-
behandlung erfolgt ausschliesslich per Optimistic Locking. Das ist bewusst
so und wird als Contract festgeschrieben, damit spätere Änderungen sichtbar
werden.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.services.offline import (
    BUNDLE_TTL_SECONDS,
    build_client_offline_bundle,
)


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp as emitted by ``datetime.isoformat()``."""
    return datetime.fromisoformat(value)


@pytest.mark.django_db
class TestBundleLeaseMetadata:
    """Lease-Konsistenz direkt am Service-Layer (ohne HTTP)."""

    def test_bundle_carries_ttl_and_expires_at(self, facility, client_identified, staff_user):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert bundle["ttl"] == BUNDLE_TTL_SECONDS
        assert "generated_at" in bundle
        assert "expires_at" in bundle

    def test_expires_at_equals_generated_at_plus_ttl(self, facility, client_identified, staff_user):
        """``expires_at`` muss exakt ``generated_at + ttl`` sein — sonst
        laufen Server- und Client-Uhr auseinander."""
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        gen = _parse_iso(bundle["generated_at"])
        exp = _parse_iso(bundle["expires_at"])
        delta = exp - gen
        assert delta == timedelta(seconds=BUNDLE_TTL_SECONDS)

    def test_expires_at_is_in_the_future_for_fresh_bundle(self, facility, client_identified, staff_user):
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
        assert _parse_iso(bundle["expires_at"]) > timezone.now()

    def test_refetch_returns_fresh_lease(self, facility, client_identified, staff_user):
        """Nach künstlichem „Ablauf" (simuliert via Zeit-Patch) muss ein
        zweiter Fetch einen neuen ``generated_at`` liefern — der Server cached
        das Bundle nicht über die TTL hinaus."""
        first_time = timezone.now() - timedelta(seconds=BUNDLE_TTL_SECONDS + 60)
        second_time = timezone.now()

        with patch("core.services.offline.timezone.now", return_value=first_time):
            old = build_client_offline_bundle(staff_user, facility, client_identified)
        old_exp = _parse_iso(old["expires_at"])
        # Das „alte" Bundle ist laut seinem eigenen expires_at bereits abgelaufen.
        assert old_exp < timezone.now()

        with patch("core.services.offline.timezone.now", return_value=second_time):
            new = build_client_offline_bundle(staff_user, facility, client_identified)
        new_gen = _parse_iso(new["generated_at"])
        new_exp = _parse_iso(new["expires_at"])

        # Neuer generated_at > alter generated_at — monotone Frische.
        assert new_gen > _parse_iso(old["generated_at"])
        # Neues expires_at liegt in der Zukunft.
        assert new_exp > timezone.now()
        # Neues Lease hat wieder volle TTL.
        assert (new_exp - new_gen) == timedelta(seconds=BUNDLE_TTL_SECONDS)


@pytest.mark.django_db
class TestBundleLeaseHttp:
    """Lease-Metadaten durch den HTTP-Endpoint hindurch."""

    def _url(self, client_pk):
        return reverse("core:offline_bundle", kwargs={"pk": client_pk})

    def test_http_response_carries_lease_fields(self, client, client_identified, staff_user):
        client.force_login(staff_user)
        response = client.get(self._url(client_identified.pk))
        assert response.status_code == 200
        payload = response.json()
        assert payload["ttl"] == BUNDLE_TTL_SECONDS
        gen = _parse_iso(payload["generated_at"])
        exp = _parse_iso(payload["expires_at"])
        assert (exp - gen) == timedelta(seconds=BUNDLE_TTL_SECONDS)
        assert exp > timezone.now()

    def test_two_consecutive_fetches_produce_distinct_leases(self, client, client_identified, staff_user):
        """Aufeinanderfolgende Fetches liefern monoton wachsende ``generated_at``.
        Damit kann der Client „newest wins" entscheiden."""
        client.force_login(staff_user)
        r1 = client.get(self._url(client_identified.pk))
        # Minimaler Zeit-Patch im zweiten Fetch, damit auch bei sehr schnellem
        # Test-Lauf ein Unterschied entsteht.
        later = timezone.now() + timedelta(seconds=5)
        with patch("core.services.offline.timezone.now", return_value=later):
            r2 = client.get(self._url(client_identified.pk))
        gen1 = _parse_iso(r1.json()["generated_at"])
        gen2 = _parse_iso(r2.json()["generated_at"])
        assert gen2 > gen1


@pytest.mark.django_db
class TestSubmitWithStaleLease:
    """Dokumentiert den Server-Contract: Lease-Ablauf sperrt keinen Submit.

    Kollisionsbehandlung erfolgt über ``expected_updated_at`` (Optimistic
    Locking, siehe ``test_offline_edit_conflict.py``). Ein abgelaufenes Lease
    allein hat *keine* serverseitige Wirkung. Der Test fixiert dieses
    Verhalten, damit eine spätere Lease-Prüfung nicht unbemerkt eingeführt
    werden kann.
    """

    def test_event_submit_ignores_bundle_expires_at(self, client, staff_user, sample_event):
        """Ein regulärer Event-Update POST (ohne ``expected_updated_at``) wird
        normal verarbeitet — selbst wenn der Client aus einem „abgelaufenen"
        Offline-Bundle gepostet hätte. Der Server kennt das Bundle-Lease
        an dieser Stelle nicht.
        """
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "42", "notiz": "from-offline-cache"},
            HTTP_ACCEPT="application/json",
        )
        # 302 = regulärer Erfolg (Redirect auf Detail). Der Lease-Ablauf
        # wird vom Server nicht als Ablehnungsgrund genutzt.
        assert response.status_code == 302
