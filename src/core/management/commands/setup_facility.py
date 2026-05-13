"""Interactive first-time setup for a new facility."""

import getpass

from django.core.management.base import BaseCommand

from core.models import Facility, Organization, Settings, User


class Command(BaseCommand):
    help = "Interactive setup of a new facility"

    def handle(self, *args, **options):
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

        org, _ = Organization.objects.get_or_create(name=org_name)
        facility, _ = Facility.objects.get_or_create(
            organization=org,
            name=facility_name,
        )
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
                "is_superuser": True,
                "display_name": admin_username.title(),
            },
        )
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Admin-Benutzer '{admin_username}' erstellt."))
        else:
            self.stdout.write(self.style.WARNING(f"Benutzer '{admin_username}' existiert bereits."))

        self.stdout.write(self.style.SUCCESS(f"\nEinrichtung '{facility_name}' ({org_name}) erfolgreich eingerichtet."))
