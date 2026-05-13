"""User seeding per facility."""

from core.models import Facility, User
from core.seed.constants import USER_TEMPLATES


def seed_users(facility: Facility, facility_idx: int) -> list[User]:
    """Create the four standard users for a facility.

    For ``facility_idx > 0`` usernames get a ``_{idx}`` suffix to avoid
    collisions across facilities.
    """
    created_users: list[User] = []
    for username_base, first, last, role, is_superuser in USER_TEMPLATES:
        username = username_base if facility_idx == 0 else f"{username_base}_{facility_idx}"
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "first_name": first,
                "last_name": last,
                "role": role,
                "facility": facility,
                "is_staff": True,
                "is_superuser": is_superuser,
                "display_name": f"{first} {last}",
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
            "is_superuser": True,
            "display_name": "Super Admin",
        },
    )
    if created:
        user.set_password("anlaufstelle2026")
        user.save()
    return user
