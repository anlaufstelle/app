"""Refs #2314: Jeder Sicherheits-Header kommt aus GENAU EINER Quelle.

Setzen Anwendung und Reverse-Proxy denselben Header, erreicht er den Client
doppelt. Fachlich unschädlich, solange die Werte gleich sind — aber wer die
Konfiguration ändert (z. B. die HSTS-Dauer), sieht seine Änderung nicht
wirken und sucht an der falschen Stelle.

Maßgeblich ist die **Anwendung** (Djangos ``SecurityMiddleware`` /
``XFrameOptionsMiddleware``, Werte in ``settings/prod.py``): so gilt der
Header auch hinter einem fremden Proxy und bei direktem Zugriff ohne Caddy.
Die Caddyfiles setzen nur noch, was Django nicht setzt (z. B. ``X-Robots-Tag``).

Der Guard prüft beide Konfigurationen gegeneinander: die tatsächlich von der
Anwendung ausgelieferten Header gegen die in jedem Caddyfile gesetzten.
"""

import re
from pathlib import Path

import pytest
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]

CADDYFILES = ("Caddyfile", "Caddyfile.dev", "Caddyfile.demo", "Caddyfile.staging")

#: Werte aus ``settings/prod.py`` — der Guard stellt die Produktionslage her,
#: ohne das Settings-Modul zu importieren (es verlangt Umgebungsvariablen).
PROD_SECURITY_SETTINGS = {
    "SECURE_HSTS_SECONDS": 31536000,
    "SECURE_HSTS_INCLUDE_SUBDOMAINS": True,
    "SECURE_HSTS_PRELOAD": True,
    "SECURE_CONTENT_TYPE_NOSNIFF": True,
    "SECURE_REFERRER_POLICY": "strict-origin-when-cross-origin",
    "X_FRAME_OPTIONS": "DENY",
    "SECURE_SSL_REDIRECT": False,
}

#: Header, die Django selbst setzt und die deshalb in keinem Caddyfile
#: auftauchen dürfen.
APP_OWNED_HEADERS = frozenset(
    {
        "strict-transport-security",
        "x-content-type-options",
        "referrer-policy",
        "x-frame-options",
    }
)

_HEADER_BLOCK_RE = re.compile(r"^\s*header\s*\{", re.MULTILINE)


def caddy_header_names(source: str) -> set[str]:
    """Header-Feldnamen, die ein Caddyfile in seinen ``header {}``-Bloecken setzt.

    Bewusst simpel und konservativ: jede nicht leere, nicht kommentierte Zeile
    innerhalb eines Blocks beginnt mit dem Feldnamen. Praefixe wie ``+``/``-``/
    ``?`` (Caddy-Operatoren) werden abgeschnitten.
    """
    names: set[str] = set()
    for match in _HEADER_BLOCK_RE.finditer(source):
        depth = 1
        for line in source[match.end() :].split("\n"):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if stripped.startswith("}"):
                depth -= 1
                if depth == 0:
                    break
                continue
            if stripped.endswith("{"):
                depth += 1
                continue
            names.add(stripped.split()[0].lstrip("+-?").lower())
    return names


@pytest.mark.parametrize("filename", CADDYFILES)
def test_caddyfile_does_not_repeat_application_headers(filename):
    path = REPO_ROOT / filename
    if not path.exists():
        # Der Release-Build strippt die Nicht-Produktions-Konfigurationen
        # (Caddyfile.dev, Caddyfile.demo, Caddyfile.staging) aus dem oeffentlichen
        # Snapshot — dort ist der Guard gegenstandslos (Refs #1137, #1047).
        pytest.skip("Caddyfile.dev/Caddyfile.demo/Caddyfile.staging nicht im Public-Snapshot")
    source = path.read_text(encoding="utf-8")
    duplicated = sorted(caddy_header_names(source) & APP_OWNED_HEADERS)
    assert not duplicated, (
        f"{filename} setzt Header, die bereits die Anwendung setzt: {duplicated}. "
        "Sie kaemen beim Client doppelt an (Refs #2314). Massgeblich ist die "
        "Anwendung (settings/prod.py) — im Caddyfile gehoert nur hinein, was "
        "Django nicht setzt."
    )


@pytest.mark.django_db
@override_settings(**PROD_SECURITY_SETTINGS)
def test_application_delivers_each_security_header_exactly_once(client):
    response = client.get(reverse("login"), secure=True)
    delivered = [name.lower() for name, _value in response.items()]
    for header in sorted(APP_OWNED_HEADERS):
        assert delivered.count(header) == 1, (
            f"{header} kommt {delivered.count(header)}x aus der Anwendung (erwartet: genau 1)."
        )


@pytest.mark.django_db
@override_settings(**PROD_SECURITY_SETTINGS)
def test_application_is_the_source_of_the_four_headers(client):
    """Gegen-Check zum Caddy-Guard: wer im Proxy streicht, muss sicher sein,
    dass die Anwendung den Header wirklich liefert."""
    response = client.get(reverse("login"), secure=True)
    assert response["Strict-Transport-Security"].startswith("max-age=31536000")
    assert "includeSubDomains" in response["Strict-Transport-Security"]
    assert "preload" in response["Strict-Transport-Security"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response["X-Frame-Options"] == "DENY"
