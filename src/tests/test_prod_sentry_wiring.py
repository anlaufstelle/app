"""Guard für die Sentry-Verdrahtung in den Produktionseinstellungen (Refs #2263).

``src/anlaufstelle/settings/_sentry.py`` ist gut getestet — die Unit-Guards in
``test_sentry_scrubber.py`` und der Integrationsnachweis in
``test_sentry_integration.py`` rufen die Hooks direkt bzw. über ein selbst
initialisiertes SDK auf. Was bisher **kein** Test geprüft hat: dass
``prod.py`` diese Hooks auch tatsächlich an ``sentry_sdk.init`` übergibt.
``before_breadcrumb`` hängt an genau einer Zeile; fiele sie einem Refactoring
zum Opfer, bliebe die Suite grün und die Zusage aus ``docs/security-notes.md``
(Log-Breadcrumbs tragen keine Werte, nur Templates) wäre stillschweigend weg.

Geprüft wird die **echte** Verdrahtung, nicht der Quelltext: Das
Produktionssettings-Modul wird in einem Subprozess unter kontrollierter
Umgebung geladen, wobei ``sentry_sdk.init`` vorher durch einen Sammler ersetzt
ist. Der Test sieht damit exakt die kwargs, die das SDK bekommen hätte —
inklusive der Frage, ob die übergebenen Hooks die Scrubber aus ``_sentry`` sind
und was sie mit einem Breadcrumb voller Klarnamen tun.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

# Fremder Prozess ⇒ Timeout von Anfang an (der Import zieht Django hoch).
_SUBPROCESS_TIMEOUT = 180

# Klarnamen, die ein musterbasierter Scrubber NICHT erkennt — genau der Grund
# für ``before_breadcrumb``. Sie dürfen im Ergebnis nirgends mehr auftauchen.
KLARNAME = "Erika Mustermann"
EINRICHTUNGSNAME = "Haus Sonnenschein"

# Der Code, der im Subprozess läuft: ``sentry_sdk.init`` abfangen, danach die
# Produktionseinstellungen importieren und berichten, was der Aufruf mitbekam.
# ``prod.py`` ruft ``sentry_sdk.init`` über das Modulattribut auf, die Ersetzung
# greift also zur Aufrufzeit.
_CAPTURE_CODE = """
import json

import sentry_sdk

_calls = []


def _sammeln(*args, **kwargs):
    _calls.append({"args": len(args), "kwargs": kwargs})


sentry_sdk.init = _sammeln

import anlaufstelle.settings.prod  # noqa: E402,F401
from anlaufstelle.settings import _sentry  # noqa: E402

ergebnis = {"init_aufrufe": len(_calls)}
if _calls:
    kwargs = _calls[0]["kwargs"]
    ergebnis["positional_args"] = _calls[0]["args"]
    ergebnis["kwarg_namen"] = sorted(kwargs)
    ergebnis["send_default_pii"] = kwargs.get("send_default_pii")
    ergebnis["include_local_variables"] = kwargs.get("include_local_variables")
    for name in ("before_send", "before_send_transaction", "before_breadcrumb"):
        hook = kwargs.get(name)
        ergebnis[name + "_ist_scrubber"] = hook is getattr(_sentry, name)

    class _Record:
        msg = "Klient*in %s in %s angelegt"
        name = "core.clients"
        levelname = "INFO"

    crumb = {
        "type": "log",
        "level": "info",
        "message": "Klient*in __KLARNAME__ in __EINRICHTUNG__ angelegt",
        "data": {"category": "__EINRICHTUNG__", "logger": "__KLARNAME__"},
    }
    breadcrumb_hook = kwargs.get("before_breadcrumb")
    ergebnis["breadcrumb"] = (
        breadcrumb_hook(crumb, {"log_record": _Record()}) if callable(breadcrumb_hook) else None
    )

print(json.dumps(ergebnis, default=repr))
""".replace("__KLARNAME__", KLARNAME).replace("__EINRICHTUNG__", EINRICHTUNGSNAME)

# Eine vollständige, gültige Prod-Konfiguration: die Fail-closed-Guards in
# prod.py müssen passieren, sonst kommt der Import gar nicht bis zu Sentry.
_VALID_ENV = {
    "DJANGO_SECRET_KEY": "x" * 50,
    "ALLOWED_HOSTS": "example.org",
    "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
    "DJANGO_AUDIT_HASH_KEY": "y" * 50,
}


def _prod_sentry_init(sentry_dsn):
    """Lädt prod.py im Subprozess und gibt die abgefangenen init-kwargs zurück.

    Alle Variablen, aus denen das Projekt seine Konfiguration zieht, werden
    vorbelegt (leer, wo sie leer sein sollen) — ``load_dotenv()`` überschreibt
    gesetzte Variablen nicht, eine lokale ``.env`` kann das Ergebnis also nicht
    verfälschen.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_DIR),
        "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.prod",
        "DATABASE_URL": "postgres://u:p@localhost:5432/x",
        "POSTGRES_DB": "x",
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p" * 40,
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_ADMIN_USER": "",
        "POSTGRES_ADMIN_PASSWORD": "",
        "POSTGRES_BOOTSTRAP_PASSWORD": "",
        "REDIS_URL": "",
        "FILE_UPLOAD_TEMP_DIR": "",
        "EXTERNAL_ORIGIN": "",
        "SENTRY_DSN": sentry_dsn,
        **_VALID_ENV,
    }
    result = subprocess.run(
        [sys.executable, "-c", _CAPTURE_CODE],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert result.returncode == 0, f"prod-Import fehlgeschlagen: {result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def init_kwargs():
    """Die abgefangenen ``sentry_sdk.init``-Argumente bei gesetztem DSN."""
    return _prod_sentry_init("https://public@sentry.example.org/42")


class TestProdSentryVerdrahtung:
    """Refs #2263: prod.py muss die Scrubber-Hooks an das SDK übergeben."""

    def test_init_wird_mit_dsn_genau_einmal_aufgerufen(self, init_kwargs):
        """Vorbedingung aller anderen Guards: der Sammler hat wirklich gegriffen."""
        assert init_kwargs["init_aufrufe"] == 1
        # Keine Positional-Args: sonst könnte ein Hook am kwargs-Guard vorbei.
        assert init_kwargs["positional_args"] == 0

    def test_before_breadcrumb_ist_verdrahtet(self, init_kwargs):
        """Der Hook, der Log-Breadcrumbs auf ihr Template reduziert (Refs #2198).

        ``before_send`` scrubt musterbasiert (E-Mail/Token/Passwort/URL) — ein
        nackter Personen- oder Einrichtungsname überlebt dort. Dicht ist die
        Kette erst mit diesem Hook.
        """
        assert "before_breadcrumb" in init_kwargs["kwarg_namen"]
        assert init_kwargs["before_breadcrumb_ist_scrubber"] is True

    def test_before_send_und_transaction_sind_verdrahtet(self, init_kwargs):
        """Error-Events und Performance-Transactions laufen beide über einen Scrubber."""
        assert init_kwargs["before_send_ist_scrubber"] is True
        assert init_kwargs["before_send_transaction_ist_scrubber"] is True

    def test_send_default_pii_bleibt_aus(self, init_kwargs):
        assert init_kwargs["send_default_pii"] is False

    def test_frame_locals_werden_nicht_erfasst(self, init_kwargs):
        """Refs #1275: entschlüsselte Notizen leben in Frame-Locals."""
        assert init_kwargs["include_local_variables"] is False

    def test_verdrahteter_hook_entfernt_klarnamen_aus_dem_breadcrumb(self, init_kwargs):
        """Negativfall: der tatsächlich übergebene Hook, nicht der importierte.

        Der Breadcrumb trägt den Klarnamen in ``message`` und den
        Einrichtungsnamen in ``data`` — beides überlebt einen musterbasierten
        Scrubber. Nach dem Hook darf keines von beiden mehr im Breadcrumb
        stehen; was bleibt, ist das Format-Template und die Logger-Kategorie.
        """
        breadcrumb = init_kwargs["breadcrumb"]
        assert breadcrumb is not None, "kein before_breadcrumb-Hook verdrahtet"
        serialisiert = json.dumps(breadcrumb, ensure_ascii=False)
        assert KLARNAME not in serialisiert
        assert EINRICHTUNGSNAME not in serialisiert
        assert breadcrumb["message"] == "Klient*in %s in %s angelegt"
        assert breadcrumb["data"] == {"logger": "core.clients", "level": "INFO"}

    def test_ohne_dsn_wird_sentry_nicht_initialisiert(self):
        """Gegenprobe: ohne ``SENTRY_DSN`` gibt es keinen init-Aufruf.

        Belegt, dass der Sammler nicht irgendeinen Aufruf sieht, sondern genau
        den aus dem DSN-Zweig von prod.py.
        """
        assert _prod_sentry_init("")["init_aufrufe"] == 0
