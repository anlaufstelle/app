"""Seed data for development and demo.

This command is a thin dispatcher: all the actual seeding logic lives in
``core.seed.*`` modules, grouped by domain. Each helper is a pure function
with explicit parameters so it can be reused in tests or ad-hoc scripts.
"""

import random

from django.core.management.base import BaseCommand

from core.models import Case, Client
from core.seed.activities import seed_activities
from core.seed.attachments import attach_files_to_counseling_events
from core.seed.cases import assign_events_to_cases, seed_cases, seed_episodes, seed_goals
from core.seed.clients import seed_clients_bulk, seed_clients_small
from core.seed.deletions import seed_deletion_requests, seed_retention_proposals
from core.seed.doc_types import seed_document_types
from core.seed.events import seed_events_bulk, seed_events_small
from core.seed.flush import flush_seed_data
from core.seed.organization import seed_facility, seed_organization
from core.seed.scale import FACILITY_NAMES, SCALE_CONFIG
from core.seed.settings_seed import seed_settings, seed_time_filters
from core.seed.users import seed_users
from core.seed.workitems import seed_work_items


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

    def handle(self, *args, **options):
        scale = options["scale"]
        cfg = SCALE_CONFIG[scale]
        random.seed(42)

        if options["flush"]:
            self.stdout.write("Deleting existing data...")
            flush_seed_data()

        org = seed_organization()

        for idx in range(cfg["facilities"]):
            facility_name = FACILITY_NAMES[idx]
            facility = seed_facility(org, facility_name)
            seed_document_types(facility)
            seed_settings(facility)
            seed_time_filters(facility)
            users = seed_users(facility, idx)

            if scale == "small":
                seed_clients_small(facility, users)
                seed_events_small(facility)
                clients = list(Client.objects.filter(facility=facility))
                seed_cases(facility, users, clients, cfg)
                seed_work_items(facility, users, clients, cfg)
            else:
                clients = seed_clients_bulk(facility, users, cfg)
                created_events = seed_events_bulk(facility, users, clients, cfg)
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
