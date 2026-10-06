"""Tests for the setup_facility management command."""

import io
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
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

    def _run(self, *, no_default_package=False, package=None, username="admin"):
        out = io.StringIO()
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", username]),
            patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
        ):
            call_command(
                "setup_facility",
                no_default_package=no_default_package,
                package=package,
                stdout=out,
            )
        return out.getvalue()

    def test_default_package_is_applied(self):
        """Ohne Ereignistyp ist die Typ-Auswahl leer und keine Dokumentation moeglich."""
        self._run()

        facility = Facility.objects.get(name="Stelle")
        assert DocumentType.objects.filter(
            facility=facility, entity_type=DocumentType.EntityType.EVENT, is_active=True
        ).exists()
        assert FieldTemplate.objects.filter(facility=facility).exists()

    def test_explicit_default_package_is_applied(self):
        self._run(package="default")

        facility = Facility.objects.get(name="Stelle")
        assert DocumentType.objects.filter(facility=facility, slug="kontakt").exists()

    def test_package_and_opt_out_are_mutually_exclusive_before_prompting(self, monkeypatch):
        def _fail(*args, **kwargs):  # pragma: no cover - darf nie laufen
            raise AssertionError("Die Optionsprüfung muss vor der ersten Eingabe greifen.")

        monkeypatch.setattr("builtins.input", _fail)

        with pytest.raises(CommandError, match="--package und --no-default-package"):
            call_command("setup_facility", package="default", no_default_package=True)

    def test_unknown_package_reports_available_names_before_prompting(self, monkeypatch):
        def _fail(*args, **kwargs):  # pragma: no cover - darf nie laufen
            raise AssertionError("Die Paketprüfung muss vor der ersten Eingabe greifen.")

        monkeypatch.setattr("builtins.input", _fail)

        with pytest.raises(CommandError, match="Verfügbare Pakete: default"):
            call_command("setup_facility", package="missing")

    def test_empty_package_name_is_unknown_before_prompting(self, monkeypatch):
        def _fail(*args, **kwargs):  # pragma: no cover - darf nie laufen
            raise AssertionError("Die Paketprüfung muss vor der ersten Eingabe greifen.")

        monkeypatch.setattr("builtins.input", _fail)

        with pytest.raises(CommandError, match="Verfügbare Pakete: default") as excinfo:
            call_command("setup_facility", package="")

        assert "Unbekanntes Paket ''." in str(excinfo.value)

    def test_synthetic_package_creates_only_its_catalog(self, tmp_path: Path, monkeypatch):
        from core.packages import catalog
        from core.services import facility_bootstrap

        raw = {
            "schema_version": 1,
            "field_templates": [{"slug": "probe-feld", "name": "Probe-Feld"}],
            "document_types": [
                {
                    "slug": "probe-typ",
                    "name": "Probe-Typ",
                    "entity_type": "event",
                    "field_refs": [{"template_slug": "probe-feld"}],
                }
            ],
        }
        (tmp_path / "probe.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
        monkeypatch.setattr(catalog, "_package_root", lambda: tmp_path)
        monkeypatch.setitem(
            facility_bootstrap.PACKAGE_COMFORT,
            "probe",
            facility_bootstrap.ComfortDefs((), ()),
        )
        catalog._package_data_cache.clear()
        try:
            output = self._run(package="probe")

            facility = Facility.objects.get(name="Stelle")
            assert set(DocumentType.objects.for_facility(facility).values_list("slug", flat=True)) == {"probe-typ"}
            assert set(FieldTemplate.objects.for_facility(facility).values_list("slug", flat=True)) == {"probe-feld"}
            assert "probe eingespielt" in output
        finally:
            catalog._package_data_cache.clear()

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

    def test_existing_catalog_with_domain_package_explains_replacement(self, tmp_path: Path, monkeypatch):
        from core.packages import catalog
        from core.services import facility_bootstrap

        raw = {
            "schema_version": 1,
            "field_templates": [{"slug": "probe-feld", "name": "Probe-Feld"}],
            "document_types": [
                {
                    "slug": "probe-typ",
                    "name": "Probe-Typ",
                    "entity_type": "event",
                    "field_refs": [{"template_slug": "probe-feld"}],
                }
            ],
        }
        (tmp_path / "probe.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
        monkeypatch.setattr(catalog, "_package_root", lambda: tmp_path)
        monkeypatch.setitem(facility_bootstrap.PACKAGE_COMFORT, "probe", facility_bootstrap.ComfortDefs((), ()))
        org = Organization.objects.create(name="Org")
        facility = Facility.objects.create(organization=org, name="Stelle")
        DocumentType.objects.create(facility=facility, slug="bestand", name="Bestand")
        catalog._package_data_cache.clear()
        try:
            output = self._run(package="probe", username="neuer-admin")
        finally:
            catalog._package_data_cache.clear()

        assert "Domänen-Pakete sind Ersatz, kein Zusatz" in output
        assert "für ein anderes Paket eine neue Einrichtung anlegen" in output
        assert f"load_package --default --facility {facility.id} --dry-run" in output
        assert "load_package --package probe" not in output


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

        with pytest.raises(CommandError) as excinfo:
            call_command("setup_facility")

        # The hint names the maintenance role, not an ``exec web`` call that runs as the app role.
        message = str(excinfo.value)
        assert "docker compose -f docker-compose.prod.yml run --rm maintenance setup_facility" in message
        assert "exec" not in message

        assert Organization.objects.count() == 0
        assert Facility.objects.count() == 0

    def test_hint_command_selects_a_stack_that_defines_the_service(self, monkeypatch):
        import re
        import shlex

        from core.management.commands import setup_facility as command_module

        monkeypatch.setattr(command_module, "_has_rls_bypass_context", lambda: False)
        with pytest.raises(CommandError) as excinfo:
            call_command("setup_facility")

        command = re.search(r"docker compose .*? setup_facility", str(excinfo.value)).group()
        tokens = shlex.split(command)
        files = [tokens[i + 1] for i, token in enumerate(tokens) if token == "-f"] or ["docker-compose.yml"]
        service = next(t for t in tokens[tokens.index("run") + 1 :] if not t.startswith("-"))
        root = Path(__file__).resolve().parents[2]
        services = set()
        for name in files:
            services |= set(yaml.safe_load((root / name).read_text())["services"])
        assert service in services, (command, files)


@pytest.mark.django_db
class TestSetupFacilityAtomicity:
    """Refs #1883: ein Abbruch im Katalog-Schritt darf keine halb angelegte
    Einrichtung hinterlassen."""

    def test_failure_while_applying_the_catalog_rolls_everything_back(self, monkeypatch):
        from core.management.commands import setup_facility as command_module

        # Refs #1970: der Command ruft die Grundausstattung als Ganzes auf
        # (Katalog + Schichtfenster + Schnellvorlagen); der Katalog-Schritt
        # liegt seither eine Ebene tiefer in ``facility_bootstrap``.
        def _boom(_facility, **_kwargs):
            raise RuntimeError("Katalog kaputt")

        monkeypatch.setattr(command_module, "ensure_facility_baseline", _boom)

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

    def _run(self, *, package=None):
        out = io.StringIO()
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", "admin"]),
            patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
        ):
            call_command("setup_facility", package=package, stdout=out)
        return out.getvalue()

    def test_package_load_error_becomes_a_command_error(self, monkeypatch):
        from core.management.commands import setup_facility as command_module
        from core.packages.loader import PackageLoadError, PackageReport

        report = PackageReport()
        report.errors.append(("kontakt", "Entitaetstyp ist nach dem Anlegen unveraenderlich"))

        def _boom(_facility, **_kwargs):
            raise PackageLoadError(report)

        monkeypatch.setattr(command_module, "ensure_facility_baseline", _boom)

        with pytest.raises(CommandError, match="Standardkatalog"):
            self._run()

        # Alles-oder-nichts: der Lauf ist eine Transaktion, also bleibt nichts
        # halb Angelegtes zurueck — der Betreiber wiederholt den Befehl.
        assert Organization.objects.count() == 0
        assert User.objects.filter(username="admin").count() == 0

    def test_invalid_shipped_catalog_becomes_a_command_error(self, monkeypatch):
        from core.management.commands import setup_facility as command_module
        from core.packages.schema import PackageValidationError

        def _boom(_facility, **_kwargs):
            raise PackageValidationError([("document_types[0].slug", "fehlt")])

        monkeypatch.setattr(command_module, "ensure_facility_baseline", _boom)

        with pytest.raises(CommandError, match="Standardkatalog"):
            self._run()

        assert Organization.objects.count() == 0

    def test_invalid_catalog_yaml_becomes_a_command_error(self, tmp_path: Path, monkeypatch):
        from core.packages import catalog
        from core.services import facility_bootstrap

        (tmp_path / "probe.yaml").write_text(
            "field_templates: [{slug: notiz, name: Notiz\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(catalog, "_package_root", lambda: tmp_path)
        monkeypatch.setitem(facility_bootstrap.PACKAGE_COMFORT, "probe", facility_bootstrap.ComfortDefs((), ()))
        catalog._package_data_cache.clear()
        try:
            with pytest.raises(CommandError) as excinfo:
                self._run(package="probe")
        finally:
            catalog._package_data_cache.clear()

        assert "core/packages/probe.yaml" in str(excinfo.value)
        assert "while parsing" in str(excinfo.value)
        assert Organization.objects.count() == 0


@pytest.mark.django_db
class TestSetupFacilityBaseline:
    """Refs #1970: Katalog, Schichtfenster und Schnellvorlagen aus EINEM Einstieg.

    Der Admin-Anlagepfad (``core/admin/organization.py``) ruft dieselbe
    Funktion — ein zweiter Weg mit abweichendem Ergebnis war genau die Falle
    aus Refs #1884.
    """

    def _run(self, *, no_default_package=False, username="admin"):
        out = io.StringIO()
        with (
            patch("builtins.input", side_effect=["Org", "Stelle", username]),
            patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
        ):
            call_command("setup_facility", no_default_package=no_default_package, stdout=out)
        return out.getvalue()

    def test_run_creates_shifts_and_quick_templates(self):
        from core.models import QuickTemplate, TimeFilter

        self._run()

        facility = Facility.objects.get(name="Stelle")
        assert TimeFilter.objects.filter(facility=facility).exists()
        assert QuickTemplate.objects.filter(facility=facility).exists()

    def test_opt_out_skips_the_catalog_but_not_the_shifts(self):
        from core.models import QuickTemplate, TimeFilter

        self._run(no_default_package=True)

        facility = Facility.objects.get(name="Stelle")
        assert not DocumentType.objects.filter(facility=facility).exists()
        assert TimeFilter.objects.filter(facility=facility).exists()
        # Ohne Katalog gibt es keinen Typ, an dem eine Vorlage haengen koennte.
        assert not QuickTemplate.objects.filter(facility=facility).exists()

    def test_second_run_leaves_maintained_shifts_alone(self):
        from core.models import TimeFilter

        self._run()
        facility = Facility.objects.get(name="Stelle")
        TimeFilter.objects.filter(facility=facility).exclude(label="Frühdienst").delete()
        renamed = TimeFilter.objects.get(facility=facility)
        renamed.label = "Tagdienst"
        renamed.save()

        self._run()

        assert list(TimeFilter.objects.filter(facility=facility).values_list("label", flat=True)) == ["Tagdienst"]

    def test_admin_save_does_not_undo_the_opt_out(self, rf, super_admin_user):
        """Der ``--no-default-package``-Entscheid ueberlebt jedes spaetere Speichern im Admin.

        Beide Anlagepfade teilen sich einen Einstieg — der Admin darf ihn
        deshalb nur beim **Anlegen** ziehen. Sonst holte das erste Umbenennen
        einer bewusst leer gehaltenen Einrichtung den kompletten
        Standardkatalog herein, gegen den Hilfetext des Flags.
        """
        from django.contrib.messages.storage.fallback import FallbackStorage

        from core.admin.organization import FacilityAdmin
        from core.admin_site import anlaufstelle_admin_site
        from core.models import QuickTemplate

        self._run(no_default_package=True)
        facility = Facility.objects.get(name="Stelle")
        assert not DocumentType.objects.filter(facility=facility).exists()

        request = rf.get("/")
        request.user = super_admin_user
        request.current_facility = getattr(super_admin_user, "facility", None)
        request.session = {}
        request._messages = FallbackStorage(request)
        facility.name = "Stelle (umbenannt)"
        FacilityAdmin(Facility, anlaufstelle_admin_site).save_model(request, facility, form=None, change=True)

        assert not DocumentType.objects.filter(facility=facility).exists()
        assert not FieldTemplate.objects.filter(facility=facility).exists()
        assert not QuickTemplate.objects.filter(facility=facility).exists()

    def test_admin_path_and_command_agree(self, rf, super_admin_user, organization):
        """Akzeptanzkriterium: beide Anlagepfade liefern dasselbe Ergebnis."""
        from django.contrib.messages.storage.fallback import FallbackStorage

        from core.admin.organization import FacilityAdmin
        from core.admin_site import anlaufstelle_admin_site
        from core.models import QuickTemplate, TimeFilter

        self._run(username="cli-admin")
        via_command = Facility.objects.get(name="Stelle")

        request = rf.get("/")
        request.user = super_admin_user
        request.current_facility = getattr(super_admin_user, "facility", None)
        request.session = {}
        request._messages = FallbackStorage(request)
        via_admin = Facility(organization=organization, name="Adminstelle")
        FacilityAdmin(Facility, anlaufstelle_admin_site).save_model(request, via_admin, form=None, change=False)

        def _fingerprint(facility):
            return (
                sorted(DocumentType.objects.filter(facility=facility).values_list("slug", flat=True)),
                sorted(FieldTemplate.objects.filter(facility=facility).values_list("slug", flat=True)),
                sorted(TimeFilter.objects.filter(facility=facility).values_list("label", flat=True)),
                sorted(QuickTemplate.objects.filter(facility=facility).values_list("name", flat=True)),
            )

        assert _fingerprint(via_admin) == _fingerprint(via_command)
