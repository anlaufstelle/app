"""Seed data for development and demo.

This command is a thin dispatcher: all the actual seeding logic lives in
``core.seed.*`` modules, grouped by domain. Each helper is a pure function
with explicit parameters so it can be reused in tests or ad-hoc scripts.
"""

import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Case, Client, User
from core.seed.activities import seed_activities
from core.seed.attachments import attach_files_to_client_and_case, attach_files_to_counseling_events
from core.seed.cases import assign_events_to_cases, seed_cases, seed_episodes, seed_goals
from core.seed.clients import seed_clients_bulk, seed_clients_small
from core.seed.deletions import seed_deletion_requests, seed_retention_proposals
from core.seed.doc_types import seed_document_types
from core.seed.events import seed_events_bulk, seed_events_small
from core.seed.flush import flush_seed_data
from core.seed.organization import seed_facility, seed_organization
from core.seed.quick_templates import seed_quick_templates
from core.seed.scale import FACILITY_NAMES, FACILITY_PACKAGES, SCALE_CONFIG
from core.seed.settings_seed import seed_settings, seed_time_filters
from core.seed.users import seed_super_admin, seed_users
from core.seed.workitems import seed_work_items
from core.services.facility_bootstrap import PACKAGE_COMFORT, ensure_quick_templates, ensure_time_filters


class Command(BaseCommand):
    help = "Create demo data (idempotent)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--scale",
            choices=["small", "medium", "large", "solo"],
            default="small",
            help="Data volume: small (default), medium, large",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            default=False,
            help="Delete existing seed data before generating",
        )
        parser.add_argument(
            "--only-if-empty",
            action="store_true",
            default=False,
            help=(
                "Nur seeden, wenn die Datenbank noch kein einziges Benutzerkonto hat. "
                "Traegt die Idempotenz des lokalen One-Command-Bootstraps (Refs #1062)."
            ),
        )

    def handle(self, *args, **options):
        # Fail-closed: Demo-Logins/--flush sind nur in explizit
        # freigeschalteten Umgebungen zulaessig. Refs #1040 (S1).
        # Bewusst VOR jeder anderen Auswertung: ``--only-if-empty`` darf den
        # Guard nicht in ein stilles "nichts zu tun" verwandeln (Refs #1062).
        if not getattr(settings, "SEED_ALLOWED", False):
            raise CommandError(
                "'seed' ist in dieser Umgebung gesperrt (SEED_ALLOWED=False). "
                "Demo-Logins und --flush sind auf Produktionssystemen nicht "
                "erlaubt — Ersteinrichtung über 'manage.py create_super_admin'."
            )

        if options["only_if_empty"]:
            if options["flush"]:
                raise CommandError(
                    "'--only-if-empty' und '--flush' schliessen sich aus: der eine "
                    "Schalter schont den Bestand, der andere loescht ihn."
                )
            # "Leer" = noch kein einziges Benutzerkonto. Bewusst eine Tabellen-
            # Probe statt eines Migrations-Markers: ein migriertes, aber
            # ungefuelltes Schema ist genau der Zustand, den der lokale
            # Bootstrap befuellen soll. ``core_user`` steht nicht unter RLS
            # (Migration 0047) — die Probe sieht auch ohne Bypass alle Zeilen.
            if User.objects.exists():
                self.stdout.write("Datenbank ist bereits befüllt — Seed übersprungen (--only-if-empty).")
                return

        scale = options["scale"]
        cfg = SCALE_CONFIG[scale]
        random.seed(42)

        if options["flush"]:
            self.stdout.write("Deleting existing data...")
            flush_seed_data()

        org = seed_organization()
        seed_super_admin()

        # Refs #1730: ``SCALE_CONFIG``-Werte joinen ueber "small"/"medium"/"large"
        # (mit z.B. ``attachment_ratio`` als float) zu ``int | float`` — "facilities"
        # ist immer ein int, ``int()`` ist daher verhaltensgleich und macht das
        # fuer ``range()`` explizit.
        for idx in range(int(cfg["facilities"])):
            facility_name = FACILITY_NAMES[idx]
            # Refs #2148 (R-L5c-1): ein mitgeliefertes Paket je Facility-Index
            # statt fuer jede Einrichtung derselbe Standardkatalog. Faellt
            # ``seed_document_types`` fail-loud auf ein noch fehlendes
            # Domaenen-Paket (``UnknownPackageError``), bricht der Lauf hier
            # bewusst ab — kein stiller Fallback auf ``default``.
            package_name = FACILITY_PACKAGES[idx]
            facility = seed_facility(org, facility_name)
            seed_document_types(facility, package=package_name)
            if package_name == "default":
                # Hauptstelle: eigene, reichhaltigere Demo-Vorlagen/-Schichten
                # (siehe core/seed/quick_templates.py-Docstring) statt des
                # schmalen Produktions-Startbestands aus PACKAGE_COMFORT.
                seed_quick_templates(facility)
                seed_time_filters(facility)
            else:
                # Andere Pakete: Komfortobjekte kommen unveraendert aus dem
                # Produktions-Startbestand (PACKAGE_COMFORT), damit sie nicht
                # zweimal gepflegt werden muessen (R-L5c-1 "nichts doppelt
                # anlegen"). ``seed_document_types`` hat den Katalog bereits
                # angewendet — hier folgen nur noch die beiden Komfortlisten.
                comfort = PACKAGE_COMFORT[package_name]
                ensure_quick_templates(facility, comfort.quick_templates)
                ensure_time_filters(facility, comfort.time_filters)
            seed_settings(facility)
            users = seed_users(facility, idx)

            if scale == "small":
                seed_clients_small(facility, users)
                seed_events_small(facility)
                clients = list(Client.objects.filter(facility=facility))
                seed_cases(facility, users, clients, cfg)
                seed_work_items(facility, users, clients, cfg)
            else:
                clients = seed_clients_bulk(facility, users, cfg)
                created_events = seed_events_bulk(facility, users, clients, cfg, package=package_name)
                if created_events:
                    self.stdout.write(f"  {created_events} Events für {facility.name} erstellt.")
                created_cases = seed_cases(facility, users, clients, cfg)
                if created_cases:
                    self.stdout.write(f"  {created_cases} Cases für {facility.name} erstellt.")
                created_work_items = seed_work_items(facility, users, clients, cfg)
                if created_work_items:
                    self.stdout.write(f"  {created_work_items} WorkItems für {facility.name} erstellt.")
                created_deletions = seed_deletion_requests(facility, users, cfg)
                if created_deletions:
                    self.stdout.write(f"  {created_deletions} DeletionRequests für {facility.name} erstellt.")

            created_attachments = attach_files_to_counseling_events(facility, users, cfg)
            if created_attachments:
                self.stdout.write(f"  {created_attachments} Dateianhänge für {facility.name} erstellt.")

            # M24 (Refs #1738): owner-generische Beispielanhänge. Muss NACH
            # ``seed_cases`` laufen (beide Zweige oben legen die Fälle an) —
            # sonst gäbe es keinen Fall, an dem eine Datei hängen könnte.
            created_owner_attachments = attach_files_to_client_and_case(facility, users)
            if created_owner_attachments:
                self.stdout.write(
                    f"  {created_owner_attachments} Personen-/Fall-Dateianhänge für {facility.name} erstellt."
                )

            proposals, holds = seed_retention_proposals(facility, users, cfg)
            if proposals or holds:
                self.stdout.write(
                    f"  {proposals} RetentionProposals + {holds} LegalHolds für {facility.name} erstellt."
                )

            cases = list(Case.objects.filter(facility=facility))
            created_episodes = seed_episodes(facility, users, cases, cfg)
            if created_episodes:
                self.stdout.write(f"  {created_episodes} Episoden für {facility.name} erstellt.")
            goals_created, milestones_created = seed_goals(facility, users, cases, cfg)
            if goals_created:
                self.stdout.write(
                    f"  {goals_created} Wirkungsziele und "
                    f"{milestones_created} Meilensteine für {facility.name} erstellt."
                )
            assigned_events = assign_events_to_cases(facility, cases, cfg)
            if assigned_events:
                self.stdout.write(f"  {assigned_events} Events Cases zugeordnet ({facility.name}).")

            created_activities = seed_activities(facility, users, cfg)
            if created_activities:
                self.stdout.write(f"  {created_activities} Activities für {facility.name} erstellt.")

        self.stdout.write(self.style.SUCCESS(f"Seed-Daten erfolgreich erstellt. (scale={scale})"))
