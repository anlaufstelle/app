"""Tests for the setup_facility management command."""

import io
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse

from core.models import DocumentType, Facility, FieldTemplate, Organization, Settings, User


@pytest.mark.django_db
class TestSetupFacilitySuccess:
    """Happy-path tests for interactive facility setup."""

    def _run_with_inputs(self, inputs, passwords):
        """Helper: call setup_facility with mocked input() and getpass()."""
        with (
            patch("builtins.input", side_effect=inputs),
            patch("getpass.getpass", side_effect=passwords),
        ):
            call_command("setup_facility")

    def test_creates_org_facility_and_admin(self):
        """Successful run creates Organization, Facility, Settings, and admin User."""
        self._run_with_inputs(
            inputs=["Testorg", "Teststelle", "myadmin"],
            passwords=["secret123", "secret123"],
        )

        assert Organization.objects.filter(name="Testorg").exists()
        assert Facility.objects.filter(name="Teststelle").exists()

        facility = Facility.objects.get(name="Teststelle")
        assert facility.organization.name == "Testorg"

        assert Settings.objects.filter(facility=facility).exists()
        settings = Settings.objects.get(facility=facility)
        assert settings.facility_full_name == "Testorg – Teststelle"

        user = User.objects.get(username="myadmin")
        assert user.role == User.Role.FACILITY_ADMIN
        # Refs #1271: facility_admin ist kein Django-superuser (Least-Privilege).
        assert user.is_superuser is False
        assert user.is_staff is True
        assert user.facility == facility
        assert user.check_password("secret123")

    def test_default_username_is_admin(self):
        """Pressing Enter at the username prompt defaults to 'admin'."""
        self._run_with_inputs(
            inputs=["Org", "Stelle", ""],  # empty -> default "admin"
            passwords=["pw1234", "pw1234"],
        )

        assert User.objects.filter(username="admin").exists()

    def test_get_or_create_is_idempotent_for_org_and_facility(self):
        """Running setup twice with the same org/facility names reuses existing records."""
        Organization.objects.create(name="Existing Org")
        self._run_with_inputs(
            inputs=["Existing Org", "New Facility", "admin1"],
            passwords=["pass1234", "pass1234"],
        )

        assert Organization.objects.filter(name="Existing Org").count() == 1
        assert Facility.objects.filter(name="New Facility").count() == 1

    def test_display_name_is_titlecased_username(self):
        """The admin's display_name is the titlecased version of the username."""
        self._run_with_inputs(
            inputs=["Org", "Stelle", "johndoe"],
            passwords=["secret", "secret"],
        )

        user = User.objects.get(username="johndoe")
        assert user.display_name == "Johndoe"


@pytest.mark.django_db
class TestSetupFacilityValidation:
    """Validation / error-path tests."""

    def test_empty_org_name_aborts(self):
        """Empty organization name aborts without creating anything."""
        with (
            patch("builtins.input", side_effect=["", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["pw", "pw"]),
        ):
            call_command("setup_facility")

        assert Organization.objects.count() == 0
        assert Facility.objects.count() == 0
        assert User.objects.count() == 0

    def test_empty_facility_name_aborts(self):
        """Empty facility name aborts after org prompt but before creating records."""
        with (
            patch("builtins.input", side_effect=["Org", "", "admin"]),
            patch("getpass.getpass", side_effect=["pw", "pw"]),
        ):
            call_command("setup_facility")

        assert Organization.objects.count() == 0
        assert Facility.objects.count() == 0

    def test_empty_password_aborts(self):
        """Empty password aborts before creating anything."""
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["", ""]),
        ):
            call_command("setup_facility")

        # Password check happens before get_or_create — nothing is created
        assert not Organization.objects.filter(name="Org").exists()
        assert not Facility.objects.filter(name="Stelle").exists()
        assert User.objects.count() == 0

    def test_password_mismatch_retries(self):
        """Mismatched passwords trigger a retry; matching on second attempt succeeds."""
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "admin"]),
            patch(
                "getpass.getpass",
                side_effect=[
                    "first_pw",
                    "different_pw",  # first attempt: mismatch
                    "correct_pw",
                    "correct_pw",  # second attempt: match
                ],
            ),
        ):
            call_command("setup_facility")

        user = User.objects.get(username="admin")
        assert user.check_password("correct_pw")

    def test_existing_username_warns_and_skips_creation(self):
        """If the username already exists, setup warns but still completes."""
        # Pre-create org, facility, and user
        org = Organization.objects.create(name="Org")
        facility = Facility.objects.create(organization=org, name="Stelle")
        User.objects.create_user(
            username="admin",
            role=User.Role.STAFF,
            facility=facility,
        )

        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["newpass", "newpass"]),
        ):
            call_command("setup_facility")

        # User was not recreated, role unchanged
        user = User.objects.get(username="admin")
        assert user.role == User.Role.STAFF
        # Password was NOT updated (get_or_create returned existing)
        assert not user.check_password("newpass")

    def test_whitespace_only_org_name_treated_as_empty(self):
        """Whitespace-only org name is stripped and treated as empty."""
        with (
            patch("builtins.input", side_effect=["   ", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["pw", "pw"]),
        ):
            call_command("setup_facility")

        assert Organization.objects.count() == 0


@pytest.mark.django_db
class TestSetupFacilityDefaultPackage:
    """Kaltstart-Minimum (Refs #1883): nach dem Lauf ist dokumentierbar."""

    def _run(self, *, no_default_package=False, username="admin"):
        out = io.StringIO()
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", username]),
            patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
        ):
            call_command("setup_facility", no_default_package=no_default_package, stdout=out)
        return out.getvalue()

    def test_default_package_is_applied(self):
        """Ohne Ereignistyp ist die Typ-Auswahl leer und keine Dokumentation moeglich."""
        self._run()

        facility = Facility.objects.get(name="Stelle")
        assert DocumentType.objects.filter(
            facility=facility, entity_type=DocumentType.EntityType.EVENT, is_active=True
        ).exists()
        assert FieldTemplate.objects.filter(facility=facility).exists()

    def test_opt_out_leaves_the_facility_empty(self):
        self._run(no_default_package=True)

        facility = Facility.objects.get(name="Stelle")
        assert not DocumentType.objects.filter(facility=facility).exists()
        # Organisation, Einrichtung, Einstellungen und Admin entstehen trotzdem.
        assert Settings.objects.filter(facility=facility).exists()
        assert User.objects.filter(username="admin").exists()

    def test_second_run_keeps_the_maintained_catalog_untouched(self):
        """Ein Wiederholungslauf darf im Admin gepflegte Katalog-Werte NICHT ueberschreiben.

        ``setup_facility`` ist Ersteinrichtung, kein Katalog-Update: der Command
        bietet weder ``--dry-run`` noch einen Report, mit dem der Betreiber ein
        Ueberschreiben vorher sehen koennte. ``load_package --default`` ist der
        explizite Weg, den Auslieferungsstand nachzuziehen (dokumentierte
        Ruecksync-Semantik, ``docs/packages.md``). Der frueher hier stehende
        Zaehler-Vergleich (``DocumentType.objects.count()`` vorher/nachher) war
        auch ohne diese Zusicherung gruen — er pinnte nur den idempotenten
        Upsert des Loaders, nicht den Schutz gepflegter Werte.
        """
        self._run()
        facility = Facility.objects.get(name="Stelle")
        doc_type = DocumentType.objects.get(facility=facility, slug="kontakt")
        assert doc_type.name == "Kontakt", "Testannahme: der Katalog liefert diesen Namen"
        doc_type.name = "Beratungskontakt"
        doc_type.sort_order = 99
        doc_type.save()
        before = DocumentType.objects.count()

        output = self._run()

        doc_type.refresh_from_db()
        assert doc_type.name == "Beratungskontakt"
        assert doc_type.sort_order == 99
        assert DocumentType.objects.count() == before
        assert "load_package --default" in output, "Der Betreiber muss den expliziten Weg genannt bekommen"

    def test_existing_catalog_with_a_conflicting_slug_does_not_kill_the_run(self):
        """Bestands-Einrichtung mit einem kollidierenden Katalog-Slug: der Lauf bleibt heil.

        Ein Bestands-Typ ``kontakt`` mit abweichendem Entitaetstyp ist fuer den
        Loader ein Unveraenderlichkeits-Konflikt (``entity_type`` ist nach dem
        Anlegen fix, ``core/packages/loader.py``). Wuerde die Ersteinrichtung den
        Katalog blind einspielen, stuerbe der Lauf daran — und weil er eine
        einzige Transaktion ist, verschwaende der gerade angelegte
        Admin-Benutzer gleich mit.
        """
        org = Organization.objects.create(name="Org")
        facility = Facility.objects.create(organization=org, name="Stelle")
        DocumentType.objects.create(
            facility=facility,
            name="Kontakt",
            slug="kontakt",
            entity_type=DocumentType.EntityType.CLIENT,
        )

        output = self._run(username="neuer-admin")

        assert User.objects.filter(username="neuer-admin").exists()
        assert DocumentType.objects.get(facility=facility, slug="kontakt").entity_type == (
            DocumentType.EntityType.CLIENT
        )
        assert DocumentType.objects.filter(facility=facility).count() == 1
        assert "load_package --default" in output


@pytest.mark.django_db
class TestSetupFacilityRlsGuard:
    """Refs #1883: der Command schreibt in FORCE-RLS-Tabellen (``core_settings``,
    ``core_documenttype``, …). Als reine App-Rolle scheitert schon der
    ``Settings``-INSERT an der USING-Policy (fuer eine ``ALL``-Policy ohne
    eigenes ``WITH CHECK`` prueft PostgreSQL die USING-Klausel auch beim
    INSERT) — der Command muss das vorab melden, statt den Operator erst ein
    Passwort tippen zu lassen und dann an einem rohen ``ProgrammingError`` zu
    sterben.
    """

    def test_aborts_before_prompting_when_role_cannot_bypass_rls(self, monkeypatch):
        from core.management.commands import setup_facility as command_module

        monkeypatch.setattr(command_module, "_has_rls_bypass_context", lambda: False)

        def _fail(*args, **kwargs):  # pragma: no cover - darf nie laufen
            raise AssertionError("Der Guard muss VOR der ersten Eingabeaufforderung greifen.")

        monkeypatch.setattr("builtins.input", _fail)

        with pytest.raises(CommandError, match="POSTGRES_ADMIN_USER"):
            call_command("setup_facility")

        assert Organization.objects.count() == 0
        assert Facility.objects.count() == 0


@pytest.mark.django_db
class TestSetupFacilityAtomicity:
    """Refs #1883: ein Abbruch im Katalog-Schritt darf keine halb angelegte
    Einrichtung hinterlassen."""

    def test_failure_while_applying_the_catalog_rolls_everything_back(self, monkeypatch):
        from core.management.commands import setup_facility as command_module

        def _boom(_facility):
            raise RuntimeError("Katalog kaputt")

        monkeypatch.setattr(command_module, "ensure_default_package", _boom)

        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
            pytest.raises(RuntimeError),
        ):
            call_command("setup_facility")

        assert Organization.objects.count() == 0
        assert Facility.objects.count() == 0
        assert Settings.objects.count() == 0
        assert User.objects.filter(username="admin").count() == 0


@pytest.mark.django_db
class TestColdStartCanDocument:
    """Refs #1883, Akzeptanzkriterium „Kaltstart → erste Dokumentation ohne CLI-Zusatzschritt".

    Der Beweis liegt nicht in der Zeilenzahl der Tabelle, sondern an der
    Oberflaeche: die Auswahl „Dokumentationstyp" in ``/events/new/`` muss
    gefuellt sein. War sie leer, liess sich kein einziger Kontakt erfassen.
    """

    def _setup(self, *, no_default_package=False):
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "kaltstart-admin"]),
            patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
        ):
            call_command("setup_facility", no_default_package=no_default_package)
        return User.objects.get(username="kaltstart-admin")

    def test_type_selector_is_filled_right_after_setup(self, client):
        user = self._setup()
        client.force_login(user)

        response = client.get(reverse("core:event_create"))

        assert response.status_code == 200
        choices = response.context["meta_form"].fields["document_type"].queryset
        assert choices.exists(), "Kaltstart ohne waehlbaren Dokumentationstyp — nichts dokumentierbar"
        assert all(dt.entity_type == DocumentType.EntityType.EVENT for dt in choices)

    def test_opt_out_leaves_the_selector_empty(self, client):
        """Falsifikator: ohne Katalog ist die Auswahl leer — genau der Zustand vor #1883."""
        user = self._setup(no_default_package=True)
        client.force_login(user)

        response = client.get(reverse("core:event_create"))

        assert response.status_code == 200
        assert not response.context["meta_form"].fields["document_type"].queryset.exists()


@pytest.mark.django_db
class TestSetupFacilityCatalogFailure:
    """Refs #1883: ein Katalog-Fehler wird als CommandError gemeldet, nicht als Traceback.

    Muster des Schwester-Commands ``load_package`` (Report ausgeben, dann
    ``CommandError(...) from exc``) — in ``docs/packages.md`` als Exit-Verhalten
    festgeschrieben. Der Fall ist nach der Bestands-Pruefung selten (eine
    Einrichtung mit Katalog wird gar nicht mehr angefasst), bleibt aber
    moeglich, z.B. bei einem defekten ausgelieferten Katalog.
    """

    def _run(self):
        out = io.StringIO()
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
        ):
            call_command("setup_facility", stdout=out)
        return out.getvalue()

    def test_package_load_error_becomes_a_command_error(self, monkeypatch):
        from core.management.commands import setup_facility as command_module
        from core.packages.loader import PackageLoadError, PackageReport

        report = PackageReport()
        report.errors.append(("kontakt", "Entitaetstyp ist nach dem Anlegen unveraenderlich"))

        def _boom(_facility):
            raise PackageLoadError(report)

        monkeypatch.setattr(command_module, "ensure_default_package", _boom)

        with pytest.raises(CommandError, match="Standardkatalog"):
            self._run()

        # Alles-oder-nichts: der Lauf ist eine Transaktion, also bleibt nichts
        # halb Angelegtes zurueck — der Betreiber wiederholt den Befehl.
        assert Organization.objects.count() == 0
        assert User.objects.filter(username="admin").count() == 0

    def test_invalid_shipped_catalog_becomes_a_command_error(self, monkeypatch):
        from core.management.commands import setup_facility as command_module
        from core.packages.schema import PackageValidationError

        def _boom(_facility):
            raise PackageValidationError([("document_types[0].slug", "fehlt")])

        monkeypatch.setattr(command_module, "ensure_default_package", _boom)

        with pytest.raises(CommandError, match="Standardkatalog"):
            self._run()

        assert Organization.objects.count() == 0
