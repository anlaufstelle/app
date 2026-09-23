"""Refs #2314: Jeder Sicherheits-Header kommt aus GENAU EINER Quelle — und aus
der Anwendung auf JEDER Antwort.

Setzen Anwendung und Reverse-Proxy denselben Header, erreicht er den Client
doppelt. Fachlich unschädlich, solange die Werte gleich sind — aber wer die
Konfiguration ändert (z. B. die HSTS-Dauer), sieht seine Änderung nicht wirken
und sucht an der falschen Stelle.

Maßgeblich ist die **Anwendung** (Djangos ``SecurityMiddleware`` /
``XFrameOptionsMiddleware``, Werte in ``settings/prod.py``): so gilt der Header
auch hinter einem fremden Proxy und bei direktem Zugriff ohne Caddy. Die
Caddyfiles setzen nur noch, was Django nicht setzt (z. B. ``X-Robots-Tag``).

Zwei Zusagen, beide hier geprüft:

1. **Genau eine Quelle:** kein Caddyfile setzt einen Header, den die Anwendung
   setzt. Die Prod-Werte liest der Guard per Subprozess aus ``settings.prod``,
   statt sie zu kopieren — sonst fiele eine Drift dort nirgends auf.
2. **Auf jeder Antwort:** nicht nur die gerenderte Seite, sondern auch die von
   WhiteNoise direkt ausgelieferte Static-Datei und die Wartungs-503 tragen
   alle vier Header. Beide Antworten kürzen die Middleware-Kette ab; solange
   Caddy den Header für jede Antwort des Site-Blocks setzte, fiel das nicht
   auf. Deshalb steht ``XFrameOptionsMiddleware`` seit Refs #2314 direkt hinter
   ``SecurityMiddleware`` statt am Ende der Liste.

Dazu kommt die ``Content-Security-Policy`` (Refs #2339): auch sie gehoert auf
die Wartungs-503, deshalb steht die CSP-Kette ebenfalls vor der
Wartungs-Middleware.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import Client, override_settings
from django.urls import reverse

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

#: Die Produktions-Proxy-Konfiguration ist im Public-Snapshot enthalten, die
#: drei anderen strippt der Release-Build.
PROD_CADDYFILE = "Caddyfile"
NON_PROD_CADDYFILES = ("Caddyfile.dev", "Caddyfile.demo", "Caddyfile.staging")
CADDYFILES = (PROD_CADDYFILE, *NON_PROD_CADDYFILES)

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

_PROD_ENV = {
    "PATH": os.environ.get("PATH", ""),
    "PYTHONPATH": str(SRC_DIR),
    "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.prod",
    "DJANGO_LOAD_DOTENV": "false",
    "DATABASE_URL": "postgres://u:p@localhost:5432/x",
    "POSTGRES_DB": "x",
    "POSTGRES_USER": "u",
    "POSTGRES_PASSWORD": "p" * 40,
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "DJANGO_SECRET_KEY": "x" * 50,
    "ALLOWED_HOSTS": "example.org",
    "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
    "ENCRYPTION_KEYS": "",
    "DJANGO_AUDIT_HASH_KEY": "y" * 50,
}

#: Die Namen der Settings, die die vier Header speisen. Der Guard liest die
#: WERTE aus ``prod.py`` (Subprozess-Import wie ``test_prod_settings.py``) —
#: eine Kopie hier wäre genau das Netz, das bei einer Drift reisst.
_PROD_SETTING_NAMES = (
    "SECURE_HSTS_SECONDS",
    "SECURE_HSTS_INCLUDE_SUBDOMAINS",
    "SECURE_HSTS_PRELOAD",
    "SECURE_CONTENT_TYPE_NOSNIFF",
    "SECURE_REFERRER_POLICY",
    "X_FRAME_OPTIONS",
)


def _prod_security_settings():
    """Die sicherheitsrelevanten Header-Settings, wie ``prod.py`` sie auflöst."""
    fields = ", ".join(f"'{name}': p.{name}" for name in _PROD_SETTING_NAMES)
    code = f"import json, anlaufstelle.settings.prod as p; print(json.dumps({{{fields}}}))"
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=_PROD_ENV,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert result.returncode == 0, f"prod-Import fehlgeschlagen: {result.stderr}"
    values = json.loads(result.stdout.strip().splitlines()[-1])
    # ``SECURE_SSL_REDIRECT`` bleibt aus: der Testclient spricht kein echtes
    # TLS, und der Redirect ist nicht Gegenstand dieses Guards.
    return {**values, "SECURE_SSL_REDIRECT": False}


@pytest.fixture(scope="module")
def prod_security_settings():
    return _prod_security_settings()


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


def _assert_no_duplicates(filename):
    source = (REPO_ROOT / filename).read_text(encoding="utf-8")
    duplicated = sorted(caddy_header_names(source) & APP_OWNED_HEADERS)
    assert not duplicated, (
        f"{filename} setzt Header, die bereits die Anwendung setzt: {duplicated}. "
        "Sie kaemen beim Client doppelt an (Refs #2314). Massgeblich ist die "
        "Anwendung (settings/prod.py) — im Caddyfile gehoert nur hinein, was "
        "Django nicht setzt."
    )


def test_production_caddyfile_does_not_repeat_application_headers():
    """Die Produktionsdatei ist im Public-Snapshot enthalten — ohne Skip-Gate:
    verschwindet sie, soll der Kern-Guard nicht still wegfallen."""
    _assert_no_duplicates(PROD_CADDYFILE)


@pytest.mark.parametrize("filename", NON_PROD_CADDYFILES)
def test_non_production_caddyfiles_do_not_repeat_application_headers(filename):
    if not (REPO_ROOT / filename).is_file():
        # Der Release-Build strippt die Nicht-Produktions-Konfigurationen
        # (Refs #1137, #1047) — dort ist der Guard gegenstandslos.
        pytest.skip("Caddyfile.dev/Caddyfile.demo/Caddyfile.staging nicht im Public-Snapshot")
    _assert_no_duplicates(filename)


def _assert_all_four_headers(response, prod, label):
    delivered = [name.lower() for name, _value in response.items()]
    for header in sorted(APP_OWNED_HEADERS):
        assert delivered.count(header) == 1, (
            f"{label}: {header} kommt {delivered.count(header)}x aus der Anwendung (erwartet: genau 1). "
            "Seit Refs #2314 setzt der Reverse-Proxy ihn nicht mehr — eine Antwort ohne ihn ist eine Luecke."
        )
    hsts = response["Strict-Transport-Security"]
    assert hsts.startswith(f"max-age={prod['SECURE_HSTS_SECONDS']}"), f"{label}: {hsts}"
    if prod["SECURE_HSTS_INCLUDE_SUBDOMAINS"]:
        assert "includeSubDomains" in hsts, label
    if prod["SECURE_HSTS_PRELOAD"]:
        assert "preload" in hsts, label
    assert response["X-Content-Type-Options"] == "nosniff", label
    assert response["Referrer-Policy"] == prod["SECURE_REFERRER_POLICY"], label
    assert response["X-Frame-Options"] == prod["X_FRAME_OPTIONS"], label


@pytest.mark.django_db
def test_rendered_page_carries_all_four_headers(client, prod_security_settings):
    with override_settings(**prod_security_settings):
        response = client.get(reverse("login"), secure=True)
    _assert_all_four_headers(response, prod_security_settings, "Login-Seite")


@pytest.mark.django_db
def test_static_file_carries_all_four_headers(tmp_path, prod_security_settings):
    """WhiteNoise liefert die Datei direkt aus und kuerzt die Kette ab.

    Bis Refs #2314 war ``XFrameOptionsMiddleware`` der LETZTE Eintrag und sah
    diese Antwort nie; Caddy setzte den Header damals noch selbst.
    """
    (tmp_path / "pruefsonde.css").write_text("body{}", encoding="utf-8")
    with override_settings(STATIC_ROOT=str(tmp_path), STATIC_URL="/static/", **prod_security_settings):
        response = Client().get("/static/pruefsonde.css", secure=True)
    try:
        assert response.status_code == 200, "Static-Sonde wurde nicht von WhiteNoise beantwortet"
        _assert_all_four_headers(response, prod_security_settings, "Static-Datei")
    finally:
        # WhiteNoise antwortet mit einer offenen Datei; ohne close() meldet der
        # Interpreter beim Aufraeumen des tmp-Verzeichnisses einen nicht
        # geschlossenen Handle (pytest wertet das als Fehler).
        if hasattr(response, "streaming_content"):
            b"".join(response.streaming_content)
        response.close()


@pytest.mark.django_db
def test_maintenance_503_carries_all_four_headers(tmp_path, prod_security_settings):
    """Die Wartungsantwort schliesst die Kette direkt nach SecurityMiddleware kurz.

    Sie ist die Antwort, die ein Betreiber waehrend eines Deploys ausliefert —
    ausgerechnet dort fehlte der Clickjacking-Schutz, wenn ihn nur der Proxy
    setzte.
    """
    from core.middleware.maintenance import MaintenanceModeMiddleware

    flag_file = tmp_path / "maintenance.flag"
    flag_file.write_text("", encoding="utf-8")
    MaintenanceModeMiddleware._cache = None
    try:
        with override_settings(MAINTENANCE_FLAG_FILE=str(flag_file), **prod_security_settings):
            response = Client().get("/clients/", secure=True)
        assert response.status_code == 503, "Wartungsmodus war nicht aktiv"
        _assert_all_four_headers(response, prod_security_settings, "Wartungs-503")
    finally:
        MaintenanceModeMiddleware._cache = None


@pytest.mark.django_db
def test_maintenance_503_carries_the_content_security_policy(tmp_path, prod_security_settings):
    """Die Wartungs-503 traegt dieselbe Richtlinie wie eine regulaere Seite.

    Die CSP-Middleware stand hinter der Wartungs-Middleware; deren 503 kehrte
    zurueck, bevor sie lief — die Wartungsseite kam ohne CSP beim Client an.
    Verglichen wird mit der Login-Seite: gleiche Richtlinie, kein Sonderweg.
    """
    from core.middleware.maintenance import MaintenanceModeMiddleware

    with override_settings(**prod_security_settings):
        reference = Client().get(reverse("login"), secure=True)
    expected = reference.get("Content-Security-Policy")
    assert expected, "Referenzseite ohne Content-Security-Policy — Guard waere gegenstandslos"

    flag_file = tmp_path / "maintenance.flag"
    flag_file.write_text("", encoding="utf-8")
    MaintenanceModeMiddleware._cache = None
    try:
        with override_settings(MAINTENANCE_FLAG_FILE=str(flag_file), **prod_security_settings):
            response = Client().get("/clients/", secure=True)
        assert response.status_code == 503, "Wartungsmodus war nicht aktiv"
        delivered = [name.lower() for name, _value in response.items()]
        assert delivered.count("content-security-policy") == 1, (
            "Wartungs-503: Content-Security-Policy kommt "
            f"{delivered.count('content-security-policy')}x (erwartet: genau 1)."
        )
        assert response["Content-Security-Policy"] == expected
    finally:
        MaintenanceModeMiddleware._cache = None


def test_csp_middlewares_run_before_the_maintenance_middleware():
    """Strukturelle Absicherung: die CSP-Kette steht vor der Wartungs-Middleware.

    Die Relax-Middlewares bleiben dabei VOR ``CSPMiddleware`` (sie schreiben
    deren Header ausgangsseitig um).
    """
    from django.conf import settings

    order = list(settings.MIDDLEWARE)
    maintenance = order.index("core.middleware.maintenance.MaintenanceModeMiddleware")
    csp = order.index("csp.middleware.CSPMiddleware")
    for relax in (
        "core.middleware.admin_csp_relax.AdminCSPRelaxMiddleware",
        "core.middleware.demo_umami_csp_relax.DemoUmamiCSPRelaxMiddleware",
    ):
        assert order.index(relax) < csp, f"{relax} muss vor CSPMiddleware stehen."
    assert csp < maintenance, (
        "CSPMiddleware muss VOR MaintenanceModeMiddleware stehen, sonst traegt die Wartungs-503 keine CSP."
    )


def test_clickjacking_middleware_runs_before_the_short_circuiting_ones():
    """Strukturelle Absicherung der Reihenfolge (Refs #2314).

    Die beiden Funktionstests oben pruefen das Ergebnis; dieser Test nennt den
    Grund und macht eine Umsortierung sichtbar, auch wenn sie (noch) keine der
    beiden Antworten betrifft.
    """
    from django.conf import settings

    order = list(settings.MIDDLEWARE)
    clickjacking = order.index("django.middleware.clickjacking.XFrameOptionsMiddleware")
    for short_circuiting in (
        "core.middleware.maintenance.MaintenanceModeMiddleware",
        "whitenoise.middleware.WhiteNoiseMiddleware",
    ):
        assert clickjacking < order.index(short_circuiting), (
            "XFrameOptionsMiddleware muss VOR den kurzschliessenden Middlewares stehen, "
            f"sonst erreicht deren Antwort sie nie: {short_circuiting} (Refs #2314)."
        )
