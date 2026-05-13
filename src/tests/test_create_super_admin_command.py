"""Tests for the ``create_super_admin`` management command (Refs #867).

Setzt die Argumente, mockt ``getpass.getpass`` und ``input`` und prueft den
End-Zustand in der Datenbank. Der Command bricht bewusst auch ohne TTY ab,
wenn das Passwort die Django-validate-Pipeline nicht besteht — die Tests
verifizieren beide Zweige (Happy-Path + Validation-Fehler).
"""

from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command

from core.models import User


@pytest.mark.django_db
class TestCreateSuperAdminCommand:
    """Refs #867: ``manage.py create_super_admin`` legt einen super_admin an.

    Der Command erwartet entweder Args (``--username``, ``--email``) oder
    ruft fuer fehlende Werte ``input()`` auf. Das Passwort wird *immer*
    interaktiv via ``getpass.getpass`` abgefragt — nie als Arg.
    """

    def test_creates_super_admin_with_args(self):
        """Mit ``--username`` + ``--email`` als Args wird die Bestandsfrage
        umgangen; getpass liefert das Passwort.

        End-Zustand: User existiert mit ``role=SUPER_ADMIN``,
        ``facility=None``, ``is_active=True`` und korrektem Hash.
        """
        with patch("getpass.getpass", side_effect=["StrongPass!2026", "StrongPass!2026"]):
            call_command(
                "create_super_admin",
                "--username=jonas",
                "--email=jonas@example.org",
            )

        user = User.objects.get(username="jonas")
        assert user.role == User.Role.SUPER_ADMIN
        assert user.facility is None
        assert user.email == "jonas@example.org"
        assert user.is_active is True
        # ``must_change_password`` wird beim Bootstrap auf False gesetzt —
        # super_admin soll sich direkt einloggen koennen.
        assert user.must_change_password is False
        # Passwort wurde gehasht abgelegt.
        assert user.check_password("StrongPass!2026")

    def test_force_overrides_existing_super_admin_check(self):
        """Wenn bereits ein super_admin existiert, blockt der Command —
        ``--force`` ueberschreibt diese Bestandspruefung und legt einen
        weiteren super_admin an.
        """
        # Vorhandener super_admin
        User.objects.create_user(
            username="erster",
            email="erster@example.org",
            role=User.Role.SUPER_ADMIN,
            password="vorhanden123",
        )

        # Ohne --force: CommandError
        with patch("getpass.getpass", side_effect=["irrelevant", "irrelevant"]):
            with pytest.raises(CommandError) as exc:
                call_command(
                    "create_super_admin",
                    "--username=zweiter",
                    "--email=zweiter@example.org",
                )
        assert "erster" in str(exc.value), "Fehlermeldung soll den Username des vorhandenen super_admin nennen."

        # Mit --force: erfolgreich
        with patch("getpass.getpass", side_effect=["AnotherStrongPass!2026", "AnotherStrongPass!2026"]):
            call_command(
                "create_super_admin",
                "--username=zweiter",
                "--email=zweiter@example.org",
                "--force",
            )

        # Beide super_admins existieren jetzt.
        super_admins = User.objects.filter(role=User.Role.SUPER_ADMIN).order_by("username")
        assert list(super_admins.values_list("username", flat=True)) == ["erster", "zweiter"]

    def test_existing_username_aborts_even_with_args(self):
        """Wenn der angegebene Username bereits vergeben ist, bricht der
        Command mit CommandError ab — auch ohne --force-Frage. Schutz vor
        unabsichtlichem Ueberschreiben eines vorhandenen Users.
        """
        User.objects.create_user(
            username="vorhandener",
            role=User.Role.STAFF,
            password="anything",
        )

        with patch("getpass.getpass", side_effect=["StrongPass!2026", "StrongPass!2026"]):
            with pytest.raises(CommandError) as exc:
                call_command(
                    "create_super_admin",
                    "--username=vorhandener",
                    "--email=vorhandener@example.org",
                )
        assert "vorhandener" in str(exc.value)

    def test_short_password_is_rejected_then_long_one_accepted(self):
        """Refs #867: ``validate_password`` lehnt zu kurze/zu schwache Passworte
        ab. Der Command druckt den Fehler und fragt erneut — die zweite
        Eingabe wird akzeptiert, sofern sie der Validierung standhaelt.

        getpass-Sequenz:
          1. Erstversuch (zu kurz) -> Fehler
          2. Bestaetigung des Erstversuchs (matcht) -> validate_password
             schlaegt fehl, Schleife
          3. Zweitversuch (gueltig)
          4. Bestaetigung des Zweitversuchs
        """
        with patch(
            "getpass.getpass",
            side_effect=[
                "abc",
                "abc",
                "GoodLongPass!2026",
                "GoodLongPass!2026",
            ],
        ):
            call_command(
                "create_super_admin",
                "--username=jonas2",
                "--email=jonas2@example.org",
            )

        user = User.objects.get(username="jonas2")
        assert user.role == User.Role.SUPER_ADMIN
        assert user.check_password("GoodLongPass!2026")
        # Defensiv: das schwache Passwort darf nicht akzeptiert worden sein.
        assert not user.check_password("abc")

    def test_password_mismatch_then_match(self):
        """Bestaetigung != Erstversuch -> Schleife; nach Match wird angelegt."""
        with patch(
            "getpass.getpass",
            side_effect=[
                "GoodPass!2026A",
                "GoodPass!2026B",  # Mismatch
                "GoodPass!2026C",
                "GoodPass!2026C",  # Match
            ],
        ):
            call_command(
                "create_super_admin",
                "--username=jonas3",
                "--email=jonas3@example.org",
            )

        user = User.objects.get(username="jonas3")
        assert user.check_password("GoodPass!2026C")
        # Sicherheits-Probe: nicht das erste Passwort.
        assert not user.check_password("GoodPass!2026A")

    def test_interactive_username_prompt_when_arg_missing(self):
        """Ohne ``--username`` als Arg fragt der Command via ``input()`` ab.

        Wir wrappen ``Command._prompt`` (das ist die Test-Hook fuer ``input()``);
        damit ist das Test-Zusammenspiel mit getpass sauber gekoppelt.
        """
        # ``Command._prompt`` liefert username und email der Reihenfolge nach.
        with (
            patch(
                "core.management.commands.create_super_admin.Command._prompt",
                side_effect=["jonas4", "jonas4@example.org"],
            ),
            patch(
                "getpass.getpass",
                side_effect=["GoodPass!2026", "GoodPass!2026"],
            ),
        ):
            call_command("create_super_admin")

        user = User.objects.get(username="jonas4")
        assert user.role == User.Role.SUPER_ADMIN
        assert user.email == "jonas4@example.org"
