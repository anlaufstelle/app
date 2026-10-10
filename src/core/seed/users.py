"""User seeding per facility."""

from collections.abc import Sequence

from core.demo import DEMO_PASSWORD
from core.models import Facility, User
from core.seed.constants import USER_TEMPLATES

# Refs #2279: reduzierter Benutzerkreis fuer den Solo-Alltag
# (``core.seed.scenarios.DemoScenario.solo``) — genau zwei Konten statt der
# sechs Standardkonten aus ``USER_TEMPLATES``: die allein arbeitende
# Fachkraft (STAFF) plus eine Vertretung mit Freigabebefugnis
# (FACILITY_ADMIN, siehe ``can_confirm_deletion`` unten). Bewusst KEINE
# weiteren Fachkraft- oder Assistenz-Konten — die wuerden eine
# Team-/Schichtstruktur suggerieren, die ein echter Solo-Betrieb nicht hat.
SOLO_USER_TEMPLATES = [
    ("mila", "Mila", "Falk", User.Role.STAFF),
    ("vertretung", "Jonas", "Krüger", User.Role.FACILITY_ADMIN),
]


def _seed_users_from_templates(
    facility: Facility, facility_idx: int, templates: Sequence[tuple[str, str, str, str]]
) -> list[User]:
    """Shared creation loop for both the standard and the solo template set.

    For ``facility_idx > 0`` usernames get a ``_{idx}`` suffix to avoid
    collisions across facilities.
    """
    created_users: list[User] = []
    for username_base, first, last, role in templates:
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
            user.set_password(DEMO_PASSWORD)
            user.save()
        created_users.append(user)
    return created_users


def seed_users(facility: Facility, facility_idx: int) -> list[User]:
    """Create the six standard users for a facility.

    For ``facility_idx > 0`` usernames get a ``_{idx}`` suffix to avoid
    collisions across facilities.
    """
    return _seed_users_from_templates(facility, facility_idx, USER_TEMPLATES)


def seed_users_solo(facility: Facility, facility_idx: int) -> list[User]:
    """Create the two Solo-Betrieb users for a facility (Refs #2279).

    Fachkraft (STAFF) + Vertretung mit Freigabebefugnis (FACILITY_ADMIN)
    statt der sechs Standardkonten — siehe ``SOLO_USER_TEMPLATES``.
    """
    return _seed_users_from_templates(facility, facility_idx, SOLO_USER_TEMPLATES)


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
        user.set_password(DEMO_PASSWORD)
        user.save()
    return user
