"""Refs #1219, #1221: Pluralisierung der Offline-Banner-Zähler.

Die Zähler (``cachedClients``/``queueCount``/``conflictCount``) werden erst im
Client gesetzt, daher greift ``{% blocktrans count %}`` nicht. Singular- und
Plural-Form werden als getrennte ``{% trans %}``-Strings bereitgestellt und per
CSP-kompatiblem Alpine-Getter (``@alpinejs/csp``) nach Zählerwert umgeschaltet.

Diese Tests sichern:
1. die EN-Übersetzungen beider Formen (Regression-Lock analog #1144), und
2. dass base.html beide Formen je Zähler rendert (Template-Verdrahtung).

Bewusst nicht behandelt: der Unsync-Indikator „· N nicht synchronisiert"
(adjektivische Phrase ohne Nomen, bei 1 wie bei N korrekt — siehe #1221).
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils.translation import gettext, override


class TestOfflineBannerTranslationsEN:
    """EN-msgstr für Singular und Plural je Offline-Banner-Zähler."""

    # Personen-Zähler (cachedClients) — #1219
    def test_cached_clients_singular(self):
        with override("en"):
            assert gettext("Person lokal verfügbar") == "person available locally"

    def test_cached_clients_plural(self):
        with override("en"):
            assert gettext("Personen lokal verfügbar") == "people available locally"

    # Sync-Banner (queueCount) — #1221, inkl. Verb-Kongruenz wird/werden
    def test_sync_singular(self):
        with override("en"):
            assert gettext("Änderung wird synchronisiert...") == "change is being synced..."

    def test_sync_plural(self):
        with override("en"):
            assert gettext("Änderungen werden synchronisiert...") == "changes are being synced..."

    # Konflikt-Banner (conflictCount) — #1221
    def test_conflict_singular(self):
        with override("en"):
            assert gettext("Konflikt — bitte auflösen") == "conflict — please resolve"

    def test_conflict_plural(self):
        with override("en"):
            assert gettext("Konflikte — bitte auflösen") == "conflicts — please resolve"

    # Aria-Label des Personenlisten-Links (cachedClients) — #1535
    def test_cached_clients_link_aria_label(self):
        with override("en"):
            assert gettext("Zur Personenliste") == "Go to person list"


@pytest.mark.django_db
class TestOfflineBannerRendersBothForms:
    """base.html rendert je Zähler einen Singular- und einen Plural-Span."""

    def _banner_html(self, client, user) -> str:
        client.force_login(user)
        return client.get(reverse("core:dashboard")).content.decode()

    def test_cached_count_singular_and_plural(self, client, lead_user):
        html = self._banner_html(client, lead_user)
        # CSP-Getter-Verdrahtung (keine Ausdrücke in x-show erlaubt)
        assert 'x-show="isSingleCachedClient"' in html
        assert 'x-show="hasMultipleCachedClients"' in html
        # Beide Nomen-Formen vorhanden (DE-Quelltext)
        assert "Person lokal verfügbar" in html
        assert "Personen lokal verfügbar" in html

    def test_sync_banner_singular_and_plural(self, client, lead_user):
        html = self._banner_html(client, lead_user)
        assert "Änderung wird synchronisiert..." in html
        assert "Änderungen werden synchronisiert..." in html

    def test_conflict_banner_singular_and_plural(self, client, lead_user):
        html = self._banner_html(client, lead_user)
        assert "Konflikt — bitte auflösen" in html
        assert "Konflikte — bitte auflösen" in html


@pytest.mark.django_db
class TestOfflineBannerCachedClientsLink:
    """Refs #1535 (#1499, SI-8): der "N Personen lokal verfügbar"-Link

    fuehrt auf die kanonische Personenliste (``core:client_list``, /clients/),
    die im Offline-Zustand in-place die gecachten Personen rendert (SI-3/SI-4),
    statt auf die generische ``offline_fallback``-Seite (/offline/).
    """

    def _banner_html(self, client, user) -> str:
        client.force_login(user)
        return client.get(reverse("core:dashboard")).content.decode()

    def test_link_points_to_client_list(self, client, lead_user):
        html = self._banner_html(client, lead_user)
        client_list_url = reverse("core:client_list")
        assert f'href="{client_list_url}"' in html
        assert 'data-testid="offline-cached-count"' in html

    def test_link_no_longer_points_to_offline_fallback(self, client, lead_user):
        html = self._banner_html(client, lead_user)
        offline_fallback_url = reverse("offline_fallback")
        # offline_fallback bleibt als eigenstaendige Route (SW-Navigations-
        # Fallback) bestehen, darf aber nicht mehr das Ziel des Banner-Links
        # sein.
        assert f'href="{offline_fallback_url}"' not in html

    def test_link_has_descriptive_aria_label(self, client, lead_user):
        html = self._banner_html(client, lead_user)
        assert 'aria-label="Zur Personenliste"' in html
