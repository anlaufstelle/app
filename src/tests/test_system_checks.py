"""L8 (Refs #1375) — System-Check für ``TRUSTED_PROXY_HOPS``.

``TRUSTED_PROXY_HOPS`` steuert, welcher Eintrag aus ``X-Forwarded-For`` als echte
Client-IP gilt (``signals.audit.get_client_ip``). Ist der Wert HÖHER als die
tatsächlich vorgeschalteten, vertrauenswürdigen Proxy-Hops, kann ein Client den
XFF-Header spoofen (er hängt eigene Fake-Hops an, die dann als „vertrauenswürdig"
gezählt werden) — das unterläuft IP-basierte Ratelimits, Lockout-Achse und
Audit-Client-IP. Ein Django-System-Check warnt bei Über-Konfiguration und
verhindert offensichtlich ungültige Werte.
"""

from __future__ import annotations

from django.test import override_settings

from core.checks import check_trusted_proxy_hops


def _ids(messages):
    return {m.id for m in messages}


class TestTrustedProxyHopsCheck:
    @override_settings(TRUSTED_PROXY_HOPS=1)
    def test_caddy_only_default_is_clean(self):
        assert check_trusted_proxy_hops(None) == []

    @override_settings(TRUSTED_PROXY_HOPS=0)
    def test_zero_direct_remote_addr_is_clean(self):
        assert check_trusted_proxy_hops(None) == []

    @override_settings(TRUSTED_PROXY_HOPS=2)
    def test_cdn_plus_caddy_is_clean(self):
        assert check_trusted_proxy_hops(None) == []

    @override_settings(TRUSTED_PROXY_HOPS=3)
    def test_high_value_warns(self):
        messages = check_trusted_proxy_hops(None)
        assert "core.W001" in _ids(messages)

    @override_settings(TRUSTED_PROXY_HOPS=-1)
    def test_negative_is_error(self):
        messages = check_trusted_proxy_hops(None)
        assert "core.E001" in _ids(messages)
