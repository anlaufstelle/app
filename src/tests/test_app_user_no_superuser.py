"""Regression-Guard: kein App-``User`` traegt Djangos ``is_superuser`` (Refs #1297).

Mit #1271 (PR #1296) setzt kein Bootstrap-/Seed-Pfad mehr Djangos
``User.is_superuser=True`` fuer App-Rollen — Autorisierung laeuft
ausschliesslich ueber die Rolle (``is_super_admin``/``is_facility_admin``)
plus Sudo-Mode, das Django-Flag trifft keine Autorisierungsentscheidung mehr.
Dieser Test friert die Invariante ein: schluege ein kuenftiger Seed-/
Migrations-/Management-Command den Footgun wieder ein (wie vor #1271), faellt
hier rot.

Aggregat-Guard ueber alle Bootstrap-Pfade (``seed``, ``create_super_admin``,
``setup_facility``): ``User.objects.filter(is_superuser=True).exists()`` muss
``False`` bleiben.

Abgrenzung: betrifft das Django-``User``-Flag (Anwendungsebene), nicht die
PostgreSQL-Rollen-Attribute (``check_db_roles`` / Refs #902) oder den
Health-Endpoint NOSUPERUSER (#793) — beide eine andere Ebene.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management import call_command

from core.models import User

# Der Seed-Command erzeugt verschluesselte Datei-Uploads via
# ``store_encrypted_file`` und braucht daher libmagic (Refs #610). Fehlt die
# Shared-Library, ist nur der Seed-Pfad nicht lauffaehig — der Guard fuer
# create_super_admin/setup_facility laeuft unabhaengig davon weiter.
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
    _LIBMAGIC_OK = True
except Exception:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    _LIBMAGIC_OK = False


@pytest.mark.django_db
@pytest.mark.skipif(not _LIBMAGIC_OK, reason="libmagic nicht lauffaehig — Seed-Pfad erfordert libmagic1.")
def test_seed_creates_no_django_superuser():
    """Nach ``seed`` traegt KEIN App-User Djangos is_superuser-Flag (Refs #1297)."""
    call_command("seed")

    assert User.objects.filter(is_superuser=True).exists() is False


@pytest.mark.django_db
def test_create_super_admin_creates_no_django_superuser():
    """Der Prod-Bootstrap-super_admin ist kein Django-superuser (Refs #1271/#1297)."""
    with patch("getpass.getpass", side_effect=["StrongPass!2026", "StrongPass!2026"]):
        call_command("create_super_admin", "--username=jonas", "--email=jonas@example.org")

    assert User.objects.filter(is_superuser=True).exists() is False


@pytest.mark.django_db
def test_setup_facility_creates_no_django_superuser():
    """Der per ``setup_facility`` angelegte facility_admin ist kein Django-superuser (Refs #1271/#1297)."""
    with (
        patch("builtins.input", side_effect=["Org", "Stelle", "admin"]),
        patch("getpass.getpass", side_effect=["secret123", "secret123"]),
    ):
        call_command("setup_facility")

    assert User.objects.filter(is_superuser=True).exists() is False
