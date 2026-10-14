"""Interactive first-time setup for a new facility.

Rollen-Voraussetzung (Refs #1883): der Command schreibt in FORCE-RLS-Tabellen
— ``core_settings`` immer, mit dem Standardkatalog zusaetzlich
``core_documenttype``/``core_fieldtemplate``/``core_documenttypefield``/
``core_fieldgroup``/``core_fieldcondition``. Die Policy aus Migration 0047 ist
eine ``ALL``-Policy **ohne** eigenes ``WITH CHECK``; PostgreSQL prueft dann die
``USING``-Klausel auch beim INSERT. Als reine App-Rolle (NOBYPASSRLS, ohne
``app.current_facility_id`` — genau die Rolle, mit der der ``web``-Container
laeuft) scheitert deshalb schon der ``Settings``-INSERT mit
``new row violates row-level security policy``. Der Command muss darum mit der
Admin-Rolle laufen (``POSTGRES_ADMIN_USER``/BYPASSRLS, siehe
``docs/ops-runbook.md`` § 9); :func:`has_rls_bypass_context` prueft das vorab
und bricht fail-loud ab — Muster der Schwester-Commands (``load_package``
Refs #1058, ``enforce_retention`` Refs #1016).

Kaltstart-Minimum (Refs #1883): nach dem Lauf ist die Einrichtung
**dokumentierbar**. Ohne mindestens einen Dokumentationstyp mit
``entity_type: event`` bliebe die Typ-Auswahl in ``EventCreateView`` leer;
deshalb spielt der Command den mitgelieferten Standardkatalog ein
(``--no-default-package`` fuer Einrichtungen, die leer starten wollen).

Refs #1970: derselbe Aufruf legt Schichtfenster und Schnellvorlagen mit an —
:func:`core.services.facility_bootstrap.ensure_facility_baseline` ist der EINE
Einstieg, den auch der Django-Admin benutzt, damit beide Anlagepfade dasselbe
Ergebnis liefern. ``--no-default-package`` laesst nur den Katalog weg; die
Schichtfenster haengen an keinem Katalog und entstehen trotzdem, die
Schnellvorlagen haengen an Katalog-Slugs und bleiben ohne Katalog leer.

Der Opt-out haelt: der Admin zieht den Einstieg nur beim **Anlegen**
(``change=False``, siehe ``core/admin/organization.py``). Ein spaeteres
Speichern der so angelegten Einrichtung holt den abgewaehlten Katalog also
nicht still nach. Ein erneuter Lauf dieses Commands mit denselben Namen ist
dagegen eine ausdrueckliche Operator-Handlung und stattet aus, was zu diesem
Zeitpunkt leer ist — er sagt in seiner Ausgabe je Bestand, was er getan hat.

Der Katalog-Schritt ist **Ausstatten, kein Nachziehen**: bringt die
Einrichtung schon Dokumentationstypen oder Feldvorlagen mit (Wiederholungslauf,
im Django-Admin angelegte Einrichtung), bleibt sie unangetastet — sonst
schriebe der deklarative Loader im Admin gepflegte Katalog-Eigenschaften still
auf den Auslieferungsstand zurueck. Begruendung und Abgrenzung zu
``load_package --default``: :mod:`core.services.facility_bootstrap`.
"""

import getpass
from uuid import uuid4

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import AuditLog, Facility, Organization, Settings, User
from core.packages.catalog import PACKAGE_INFO, UnknownPackageError, list_packages
from core.packages.loader import PackageLoadError
from core.packages.schema import PackageValidationError
from core.services.audit import audit_event
from core.services.dynamic_protection import lock_dynamic_protection_gate
from core.services.facility_bootstrap import ensure_facility_baseline

# Refs #1016/#1058/#1883: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


class Command(BaseCommand):
    help = "Interactive setup of a new facility"

    def add_arguments(self, parser):
        parser.add_argument(
            "--package",
            help="Mitgeliefertes Katalogpaket einspielen (Standard: default).",
        )
        parser.add_argument(
            "--no-default-package",
            action="store_true",
            help=(
                "Den mitgelieferten Standardkatalog NICHT einspielen. Die Einrichtung "
                "startet dann ohne Dokumentationstypen und ist erst dokumentierbar, "
                "wenn ein Paket geladen wurde (manage.py load_package). Schichtfenster "
                "entstehen trotzdem, Schnellvorlagen mangels Katalog nicht. Der Entscheid "
                "hält: ein späteres Speichern der Einrichtung im Django-Admin zieht den "
                "abgewählten Katalog nicht nach."
            ),
        )

    def handle(self, *args, **options):
        # Vor der ersten Eingabeaufforderung: sonst tippt der Operator erst
        # Organisation, Einrichtung und zweimal ein Passwort und stirbt danach
        # am rohen ProgrammingError des ersten Schreibzugriffs.
        if not _has_rls_bypass_context():
            raise CommandError(
                "Ersteinrichtung läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — "
                "sonst scheiterte schon das Anlegen der Einstellungen an der Row-Level-Security. "
                "Command im Dienst maintenance (Admin-Rolle) aufrufen: "
                "docker compose -f docker-compose.prod.yml run --rm maintenance setup_facility "
                "(siehe docs/ops-runbook.md § 9)."
            )

        requested_package = options.get("package")
        if options["no_default_package"] and requested_package is not None:
            raise CommandError("--package und --no-default-package schließen sich aus.")
        package_name = (
            None if options["no_default_package"] else ("default" if requested_package is None else requested_package)
        )
        available_packages = list_packages()
        if package_name is not None and package_name not in available_packages:
            raise CommandError(str(UnknownPackageError(package_name, available_packages)))

        self.stdout.write(self.style.MIGRATE_HEADING("Anlaufstelle – Einrichtung einrichten"))
        self.stdout.write("")

        org_name = input("Name der Organisation: ").strip()
        if not org_name:
            self.stderr.write(self.style.ERROR("Organisationsname darf nicht leer sein."))
            return

        facility_name = input("Name der Einrichtung: ").strip()
        if not facility_name:
            self.stderr.write(self.style.ERROR("Einrichtungsname darf nicht leer sein."))
            return

        admin_username = input("Admin-Benutzername [admin]: ").strip() or "admin"

        while True:
            password = getpass.getpass("Admin-Passwort: ")
            password_confirm = getpass.getpass("Admin-Passwort (Bestätigung): ")
            if password == password_confirm:
                break
            self.stderr.write(self.style.ERROR("Passwörter stimmen nicht überein."))

        if not password:
            self.stderr.write(self.style.ERROR("Passwort darf nicht leer sein."))
            return

        # Keep setup atomic (Refs #1883) and gate every write (Refs #2525).
        # A new UUID needs no existing row; catalog setup requires exclusive
        # from the start, while the opt-out writes only shared-gated data.
        while True:
            with transaction.atomic():
                facility_id = (
                    Facility.objects.filter(name=facility_name).values_list("pk", flat=True).first() or uuid4()
                )
                lock_dynamic_protection_gate(facility_id, exclusive=package_name is not None)
                org, _ = Organization.objects.get_or_create(name=org_name)
                facility, _ = Facility.objects.get_or_create(
                    organization=org,
                    name=facility_name,
                    defaults={"id": facility_id},
                )
                if facility.pk != facility_id:
                    # Another transaction created the name after our initial read.
                    # Retry without carrying writes or the reserved UUID's gate
                    # into the transaction for the now-existing facility.
                    transaction.set_rollback(True)
                    continue
                Settings.objects.get_or_create(
                    facility=facility,
                    defaults={"facility_full_name": f"{org_name} – {facility_name}"},
                )

                user, created = User.objects.get_or_create(
                    username=admin_username,
                    defaults={
                        "role": User.Role.FACILITY_ADMIN,
                        "facility": facility,
                        "is_staff": True,
                        # Refs #1271: facility_admin is scoped to one facility;
                        # authorization uses the role, never is_superuser.
                        "display_name": admin_username.title(),
                        # The first account approves deletion requests from the start;
                        # without it no request of the new facility has an approver.
                        "can_confirm_deletion": True,
                    },
                )
                if created:
                    user.set_password(password)
                    user.save()
                    # The command runs without a logged-in actor, so the creation
                    # signal stays silent; the system entry records the initial grant.
                    audit_event(
                        AuditLog.Action.USER_CREATED,
                        user=user,
                        facility=facility,
                        target_type="User",
                        target_id=str(user.pk),
                        detail={
                            "new_role": user.role,
                            "username": user.username,
                            "can_confirm_deletion": user.can_confirm_deletion,
                            "system": True,
                            "command": "setup_facility",
                        },
                    )

                baseline = self._ensure_baseline(facility, package=package_name)

                break

        if created:
            self.stdout.write(self.style.SUCCESS(f"Admin-Benutzer '{admin_username}' erstellt."))
        else:
            self.stdout.write(self.style.WARNING(f"Benutzer '{admin_username}' existiert bereits."))

        self._write_package_summary(facility, baseline.package_report, package_name=baseline.package)
        self._write_baseline_summary(baseline)

        self.stdout.write(self.style.SUCCESS(f"\nEinrichtung '{facility_name}' ({org_name}) erfolgreich eingerichtet."))

    def _ensure_baseline(self, facility, *, package):
        """Grundausstattung einspielen und Katalog-Fehler in eine lesbare Meldung uebersetzen.

        Muster des Schwester-Commands ``load_package`` (dort ``handle()``):
        erst den (verworfenen) Report ausgeben, dann ``CommandError … from exc``.
        Ohne diese Klammer saehe der Operator einen rohen Traceback. Der
        ``raise`` liegt INNERHALB der Transaktionsklammer des Laufs — der
        Abbruch nimmt deshalb auch Organisation, Einrichtung, Einstellungen und
        Admin-Benutzer wieder mit; das sagt die Meldung ausdruecklich, damit
        niemand einen halb angelegten Zustand vermutet.
        """
        catalog_label = "Standardkatalog" if package == "default" else f"Katalogpaket {package!r}"
        try:
            return ensure_facility_baseline(facility, package=package)
        except PackageLoadError as exc:
            for identifier, message in exc.report.errors:
                self.stderr.write(self.style.ERROR(f"  {identifier}: {message}"))
            raise CommandError(
                f"{catalog_label} konnte nicht eingespielt werden — {len(exc.report.errors)} Fehler. "
                "Der ganze Lauf ist eine Transaktion: es wurde NICHTS angelegt. Konflikte im Bestand "
                "bereinigen und den Befehl wiederholen."
            ) from exc
        except PackageValidationError as exc:
            raise CommandError(
                f"{catalog_label} ist ungültig — das ist ein Fehler im "
                f"ausgelieferten Stand, nicht in Ihrer Eingabe:\n{exc}\n"
                "Es wurde nichts angelegt. Mit --no-default-package lässt sich die Einrichtung "
                "vorläufig ohne Katalog anlegen."
            ) from exc
        except yaml.YAMLError as exc:
            package_path = f"core/packages/{package}.yaml"
            raise CommandError(
                f"YAML-Fehler in {package_path!r}:\n{exc}\n"
                "Der ganze Lauf ist eine Transaktion: es wurde NICHTS angelegt."
            ) from exc

    def _write_package_summary(self, facility, report, *, package_name):
        """Eine Zeile zum Katalog — der Operator soll sehen, ob dokumentiert werden kann."""
        if package_name is None:
            self.stdout.write(
                self.style.WARNING(
                    "Standardkatalog übersprungen (--no-default-package) — dokumentieren lässt sich "
                    "erst, wenn die Einrichtung Dokumentationstypen hat: 'manage.py load_package'."
                )
            )
            return
        package_info = PACKAGE_INFO.get(package_name)
        display_name = package_info.display_name if package_info is not None else package_name
        if report is None:
            # Bestands-Einrichtung: der Command fasst einen vorhandenen Katalog
            # NICHT an (siehe ``core/services/facility_bootstrap.py``).
            if package_name != "default":
                self.stdout.write(
                    self.style.WARNING(
                        "Einrichtung trägt bereits einen Katalog; Domänen-Pakete sind Ersatz, kein Zusatz — "
                        "für ein anderes Paket eine neue Einrichtung anlegen; den Standardkatalog nachziehen: "
                        f"manage.py load_package --default --facility {facility.id} --dry-run"
                    )
                )
                return
            self.stdout.write(
                self.style.WARNING(
                    f"{display_name} nicht eingespielt — die Einrichtung bringt bereits "
                    "Dokumentationstypen oder Feldvorlagen mit; gepflegte Werte bleiben unverändert. "
                    "Auslieferungsstand bewusst nachziehen (mit Vorschau): "
                    f"manage.py load_package --default --facility {facility.id} --dry-run"
                )
            )
            return
        types = report.document_types
        self.stdout.write(
            self.style.SUCCESS(
                f"{display_name} eingespielt: {len(types.created)} Dokumentationstypen angelegt, "
                f"{len(types.updated)} aktualisiert, {len(types.unchanged)} unverändert."
            )
        )
        for identifier, message in report.warnings:
            self.stdout.write(self.style.WARNING(f"  {identifier}: {message}"))

    def _write_baseline_summary(self, baseline):
        """Je eine Zeile zu Schichtfenstern und Schnellvorlagen (Refs #1970).

        Beide blockieren nichts — ohne Zeitfenster faellt die Auswertung auf
        den vollen Tag zurueck, ohne Schnellvorlagen wird eben von Hand
        ausgefuellt. Der Operator soll trotzdem sehen, was er bekommen hat und
        was nicht, statt es spaeter in der Oberflaeche zu vermissen.
        """
        if baseline.time_filters is None:
            self.stdout.write(
                self.style.WARNING(
                    "Schichtfenster nicht angelegt — die Einrichtung bringt bereits eigene mit; "
                    "sie bleiben unverändert."
                )
            )
        else:
            labels = ", ".join(item.label for item in baseline.time_filters)
            self.stdout.write(self.style.SUCCESS(f"Schichtfenster angelegt: {labels}."))

        if baseline.quick_templates is None:
            self.stdout.write(
                self.style.WARNING(
                    "Schnellvorlagen nicht angelegt — die Einrichtung bringt bereits eigene mit; "
                    "sie bleiben unverändert."
                )
            )
        elif not baseline.quick_templates:
            if baseline.package is None:
                reason = "ohne Katalog gibt es keinen passenden Dokumentationstyp"
            else:
                reason = f"für das Paket {baseline.package!r} sind keine passenden Vorlagen vorkonfiguriert"
            self.stdout.write(
                self.style.WARNING(
                    f"Schnellvorlagen nicht angelegt — {reason}. Nachträglich anlegen: "
                    "im Admin unter Core → Quick-Templates."
                )
            )
        else:
            names = ", ".join(item.name for item in baseline.quick_templates)
            self.stdout.write(self.style.SUCCESS(f"Schnellvorlagen angelegt: {names}."))
