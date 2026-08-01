#!/usr/bin/env python3
"""Laedt das supercronic-Binary versions- und sha256-gepinnt herunter (Refs #794).

Wird ausschliesslich im Docker-**Builder** ausgefuehrt (siehe ``Dockerfile``),
nicht zur Laufzeit. supercronic ist der Cron-Daemon des ``cron``-Sidecars in
``docker-compose.prod.yml``: ein statisch gelinktes Go-Binary, das eine
crontab-Datei im Vordergrund abarbeitet, nach stdout/stderr loggt und auf
SIGTERM sauber beendet — im Gegensatz zu ``cron``/``crond`` also
container-tauglich (kein Syslog, kein Daemonize, keine setuid-Bits).

Warum ein Python-Skript statt ``curl | sha256sum -c`` im ``RUN``-Layer:
``python:3.14-slim`` (das Builder-Base-Image) bringt **weder curl noch wget**
mit. Ein ``apt-get install curl`` nur fuer diesen Download waere ein
zusaetzlicher Paket-Layer plus Netzwerkzugriff auf die Debian-Mirrors; die
Standardbibliothek kann denselben Job. Nebeneffekt: die Pins stehen an
**einer** greppbaren Stelle und sind per Unit-Test pruefbar
(``src/tests/test_ops_cron_sidecar.py``).

Sicherheitsvertrag (analog ``pip wheel --require-hashes`` im selben Builder):
Das heruntergeladene Artefakt wird gegen einen fest verdrahteten sha256
geprueft, **bevor** es das Executable-Bit bekommt. Ein umgehaengtes Release-Tag,
ein kompromittierter Release-Bucket oder ein MITM auf der Leitung bricht den
Build fail-loud ab, statt ein fremdes Binary in das Runtime-Image zu legen.

Aufruf::

    python deploy/fetch_supercronic.py --arch amd64 --output /build/supercronic

Pins aktualisieren (Version + beide Architekturen):

.. code-block:: bash

    V=v0.2.49
    for A in amd64 arm64; do
        curl -sSL "https://github.com/aptible/supercronic/releases/download/$V/supercronic-linux-$A" \\
            | sha256sum | cut -d' ' -f1
    done
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

# --- Pins -------------------------------------------------------------------
# Upstream: https://github.com/aptible/supercronic (MIT).
# Die Release-Artefakte sind unsignierte Binaries ohne Checksummen-Datei; die
# sha256 unten wurden am 2026-08-01 aus den offiziellen Release-Assets
# berechnet (Reproduktion siehe Modul-Docstring).
SUPERCRONIC_VERSION = "v0.2.48"
SUPERCRONIC_SHA256 = {
    "amd64": "88c1b66b94c486f972fdd1a4d1f901e3e75ff04f749cddd60c5db573e3a33c6c",
    "arm64": "50ae8755e04fa72812d0a1bc47a112a856811cc91cce7b6c875c378a850788bc",
}
DOWNLOAD_URL_TEMPLATE = "https://github.com/aptible/supercronic/releases/download/{version}/supercronic-linux-{arch}"

# Buildx setzt TARGETARCH auf die Go-Schreibweise; das Release-Asset traegt
# denselben Namen. Nur die im Release-Workflow gebauten Plattformen
# (linux/amd64, linux/arm64) sind gepinnt — alles andere bricht bewusst ab,
# statt still ein ungepruefte Architektur zu ziehen.


def download_url(arch: str) -> str:
    return DOWNLOAD_URL_TEMPLATE.format(version=SUPERCRONIC_VERSION, arch=arch)


def fetch(arch: str, output: Path, *, opener=urllib.request.urlopen) -> Path:
    """Laedt supercronic fuer ``arch``, verifiziert sha256 und schreibt ``output``."""
    try:
        expected = SUPERCRONIC_SHA256[arch]
    except KeyError:
        raise SystemExit(
            f"FEHLER: keine supercronic-Checksumme fuer Architektur '{arch}' gepinnt "
            f"(bekannt: {', '.join(sorted(SUPERCRONIC_SHA256))}). "
            "Neue Zielplattform? Dann Pin in deploy/fetch_supercronic.py ergaenzen."
        ) from None

    url = download_url(arch)
    with opener(url) as response:
        payload = response.read()

    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise SystemExit(
            f"FEHLER: sha256-Mismatch fuer {url}\n"
            f"  erwartet: {expected}\n"
            f"  erhalten: {actual}\n"
            "Build abgebrochen — das Release-Artefakt entspricht nicht dem Pin."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    output.chmod(0o755)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="supercronic gepinnt herunterladen")
    parser.add_argument("--arch", required=True, help="Zielarchitektur (Docker TARGETARCH), z.B. amd64/arm64")
    parser.add_argument("--output", required=True, type=Path, help="Zielpfad des Binaries")
    args = parser.parse_args(argv)

    path = fetch(args.arch, args.output)
    print(f"supercronic {SUPERCRONIC_VERSION} ({args.arch}) verifiziert -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
