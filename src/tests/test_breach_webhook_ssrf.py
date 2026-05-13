"""RF-T07: SSRF-Validator fuer Breach-Notification-Webhook (Refs #772).

Die Webhook-URL kommt aus der operatorseitig gesetzten Env-Var
``BREACH_NOTIFICATION_WEBHOOK_URL``. Vor #772
prueftet :func:`core.services.breach_detection._post_webhook` weder Schema
noch Ziel-IP — der Aufruf konnte daher gegen Cloud-Metadata-Adressen
(``169.254.169.254``), interne Hosts (``127.0.0.1``, ``10.0.0.1``,
``192.168.0.1``) oder gegen ``file://``/``gopher://``/``ftp://``-Schemes
laufen.

Diese Tests verankern die Validator-Garantie: nur ``https://``-Schema +
oeffentlich-routbare IP wird akzeptiert.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.services.breach_detection import _validate_webhook_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/hook",
        "http://192.168.0.1/hook",
        "gopher://example.com/hook",
        "ftp://example.com/hook",
        "http://valid.example/hook",  # https-only — http abgelehnt
    ],
)
def test_invalid_webhook_url_rejected(url):
    """Acht parametrische Cases — alle muessen ``ValueError`` werfen."""
    with patch("socket.gethostbyname") as gethost:
        # Private/loopback/link-local-IPs: DNS-Resolver liefert die jeweilige
        # IP zurueck. Wir mocken hier nur den public-Lookup, damit der Test
        # auch in restriktiven Sandboxen ohne Outbound-DNS laeuft.
        gethost.side_effect = lambda host: {
            "127.0.0.1": "127.0.0.1",
            "169.254.169.254": "169.254.169.254",
            "10.0.0.1": "10.0.0.1",
            "192.168.0.1": "192.168.0.1",
            "valid.example": "93.184.216.34",
            "example.com": "93.184.216.34",
        }.get(host, "0.0.0.0")
        with pytest.raises(ValueError):
            _validate_webhook_url(url)


def test_valid_https_public_url_accepted():
    """Sanity: ein https-URL gegen eine public IP geht durch."""
    with patch("socket.gethostbyname", return_value="93.184.216.34"):
        _validate_webhook_url("https://valid.example/hook")
