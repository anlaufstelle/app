"""User seeding per facility."""

from core.models import Facility, User
from core.seed.constants import USER_TEMPLATES


def seed_users(facility: Facility, facility_idx: int) -> list[User]:
    """Create the six standard users for a facility.

    For ``facility_idx > 0`` usernames get a ``_{idx}`` suffix to avoid
    collisions across facilities.
    """
    created_users: list[User] = []
    for username_base, first, last, role in USER_TEMPLATES:
        username = username_base if facility_idx == 0 else f"{username_base}_{facility_idx}"
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "first_name": first,
                "last_name": last,
                "role": role,
                "facility": facility,
                "is_staff": True,
                "display_name": f"{first} {last}",
                # Refs #1053: Leitung + Anwendungsbetreuung starten als
                # Genehmiger-Pool (analog Backfill-Migration).
                "can_confirm_deletion": role in (User.Role.FACILITY_ADMIN, User.Role.LEAD),
            },
        )
        if created:
            user.set_password("anlaufstelle2026")
            user.save()
        created_users.append(user)
    return created_users


def seed_super_admin() -> User:
    """Create (or return existing) installation-wide super-admin user.

    Refs #867: Persona Jonas — installation-weiter Systemadministrator,
    nicht an eine Einrichtung gebunden. Idempotent: nur anlegen, falls
    der Username noch nicht existiert.
    """
    user, created = User.objects.get_or_create(
        username="superadmin",
        defaults={
            "first_name": "Super",
            "last_name": "Admin",
            "email": "superadmin@example.org",
            "role": User.Role.SUPER_ADMIN,
            "facility": None,
            "is_staff": True,
            # Refs #1271: kein ``is_superuser`` — konsistent mit create_super_admin
            # (Prod) und der super_admin_user-Fixture; Autorisierung ueber die Rolle.
            "display_name": "Super Admin",
        },
    )
    if created:
        user.set_password("anlaufstelle2026")
        user.save()
    return user
