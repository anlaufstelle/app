"""Regression: per-IP rate-limit buckets key on the real client IP, not the proxy IP.

Security N1 (vormals H2): ``django-ratelimit`` ``key="ip"`` liest ohne
``RATELIMIT_IP_META_KEY`` ``REMOTE_ADDR`` — hinter dem Caddy-Reverse-Proxy
immer Caddys Container-IP, also EIN gemeinsamer Eimer fürs ganze Internet
(globaler Login-/Reset-DoS, zugleich keine echte Brute-Force-Isolierung pro
IP). Der Fix setzt ``RATELIMIT_IP_META_KEY`` auf einen Resolver, der die echte
Client-IP über ``get_client_ip`` (respektiert ``TRUSTED_PROXY_HOPS``) ableitet.
"""

import pytest
from django.test import RequestFactory, override_settings

pytestmark = pytest.mark.architecture


@override_settings(TRUSTED_PROXY_HOPS=1)
def test_resolver_returns_forwarded_client_not_proxy_ip():
    """Behind a single trusted proxy, the resolver returns the X-Forwarded-For client."""
    from core.signals.audit import client_ip_for_ratelimit

    req = RequestFactory().post(
        "/login/",
        REMOTE_ADDR="172.18.0.5",  # Caddy container IP
        HTTP_X_FORWARDED_FOR="203.0.113.10",
    )
    assert client_ip_for_ratelimit(req) == "203.0.113.10"


@override_settings(TRUSTED_PROXY_HOPS=1)
def test_resolver_falls_back_to_remote_addr_without_forwarded_header():
    """No X-Forwarded-For → REMOTE_ADDR (never an empty value that would break masking)."""
    from core.signals.audit import client_ip_for_ratelimit

    req = RequestFactory().post("/login/", REMOTE_ADDR="172.18.0.5")
    assert client_ip_for_ratelimit(req) == "172.18.0.5"


@override_settings(
    RATELIMIT_IP_META_KEY="core.signals.audit.client_ip_for_ratelimit",
    TRUSTED_PROXY_HOPS=1,
)
def test_two_clients_behind_same_proxy_get_distinct_ratelimit_ips():
    """Two clients sharing the proxy (same REMOTE_ADDR) land in DIFFERENT rate-limit buckets."""
    # django-ratelimit's own IP resolution — the exact code path key="ip" uses.
    from django_ratelimit.core import _get_ip

    rf = RequestFactory()
    r1 = rf.post("/login/", REMOTE_ADDR="172.18.0.5", HTTP_X_FORWARDED_FOR="203.0.113.10")
    r2 = rf.post("/login/", REMOTE_ADDR="172.18.0.5", HTTP_X_FORWARDED_FOR="203.0.113.20")

    ip1, ip2 = _get_ip(r1), _get_ip(r2)
    assert ip1 == "203.0.113.10"
    assert ip1 != ip2, "clients behind the same proxy must not share one rate-limit bucket"


def test_base_settings_wire_ratelimit_ip_meta_key():
    """Prod/dev/demo/staging (all behind Caddy) must configure the IP meta key."""
    from django.conf import settings

    assert getattr(settings, "RATELIMIT_IP_META_KEY", None), (
        "RATELIMIT_IP_META_KEY muss in den Basis-Settings gesetzt sein — sonst "
        "kollabiert django-ratelimit key='ip' auf die Reverse-Proxy-IP (N1)."
    )
