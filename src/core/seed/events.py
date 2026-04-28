"""Event seeding: fixed demo events for ``small`` scale, bulk for the others."""

import copy
import random
from datetime import date, timedelta

from django.utils import timezone

from core.models import Client, DocumentType, Event, Facility, User
from core.seed.constants import EVENT_DATA_POOLS
from core.services.encryption import encrypt_event_data


def random_time_of_day(max_hour: int | None = None, max_minute: int | None = None) -> tuple[int, int]:
    """Weighted hour distribution matching typical opening hours (8-19h).

    Morgen-Anlauf → Vormittag-Peak → Nachmittag → Abend ausklingend.
    If ``max_hour`` is set, limits generated times (for today's events).
    """
    hours = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
    weights = [5, 8, 15, 15, 10, 8, 12, 12, 10, 8, 5, 3]
    if max_hour is not None:
        filtered = [(h, w) for h, w in zip(hours, weights) if h <= max_hour]
        if not filtered:
            return 8, 0
        hours, weights = zip(*filtered)
        hours, weights = list(hours), list(weights)
    hour = random.choices(hours, weights=weights, k=1)[0]
    if max_hour is not None and hour == max_hour and max_minute is not None:
        minute = random.randint(0, max(0, max_minute // 5)) * 5
    else:
        minute = random.randint(0, 11) * 5  # 0, 5, 10, ..., 55
    return hour, minute


def weighted_days_ago(zeitraum: int) -> int:
    """Weight towards recent: 40% last 30d, 30% 31-90d, 30% older."""
    r = random.random()
    if r < 0.40:
        return random.randint(0, min(30, zeitraum))
    elif r < 0.70:
        return random.randint(min(31, zeitraum), min(90, zeitraum))
    else:
        return random.randint(min(91, zeitraum), zeitraum)


def build_data_template(doc_type: DocumentType) -> list[dict]:
    """Return field metadata for a doc type, excluding encrypted fields."""
    fields = []
    for dtf in doc_type.fields.select_related("field_template").all():
        ft = dtf.field_template
        if ft.is_encrypted:
            continue
        fields.append(
            {
                "key": ft.slug,
                "type": ft.field_type,
                "options": ft.options_json or [],
                "required": ft.is_required,
            }
        )
    return fields


def random_data(dt_name: str, dt_data_templates: dict) -> dict:
    """Generate plausible random ``data_json`` for a given document type."""
    pool = EVENT_DATA_POOLS.get(dt_name)
    if pool:
        template = copy.deepcopy(random.choice(pool))
        # Minor variation on numeric fields
        if "dauer" in template:
            template["dauer"] = max(5, template["dauer"] + random.randint(-5, 10))
        if "ausgabe" in template:
            template["ausgabe"] = max(1, template["ausgabe"] + random.randint(-2, 5))
            template["rueckgabe"] = min(template["rueckgabe"], template["ausgabe"])
        # Dynamic dates for accompaniment
        if dt_name == "accompaniment":
            offset = random.randint(-7, 14)
            template["datum"] = (date.today() + timedelta(days=offset)).isoformat()
            template["uhrzeit"] = f"{random.randint(8, 16):02d}:{random.choice(['00', '30'])}"
        # Dynamic next appointment for counseling
        if dt_name == "counseling":
            template["naechster-termin"] = (date.today() + timedelta(days=random.randint(7, 28))).isoformat()
        return template

    # Fallback: generic generation for unknown document types
    fields = dt_data_templates.get(dt_name, [])
    data: dict = {}
    for f in fields:
        key = f["key"]
        ftype = f["type"]
        options = f["options"]
        if ftype == "number":
            data[key] = random.randint(1, 120)
        elif ftype == "select" and options:
            slugs = [o["slug"] if isinstance(o, dict) else o for o in options]
            data[key] = random.choice(slugs)
        elif ftype == "multi_select" and options:
            slugs = [o["slug"] if isinstance(o, dict) else o for o in options]
            k = random.randint(1, min(3, len(slugs)))
            data[key] = random.sample(slugs, k)
        elif ftype == "boolean":
            data[key] = random.choice([True, False])
        elif ftype == "textarea":
            data[key] = f"Seed-Notiz {random.randint(1, 9999)}"
        elif ftype == "text":
            data[key] = f"Seed-Text {random.randint(1, 9999)}"
        elif ftype == "date":
            offset = random.randint(1, 60)
            data[key] = (date.today() - timedelta(days=offset)).isoformat()
        elif ftype == "time":
            data[key] = f"{random.randint(8, 20):02d}:{random.choice(['00', '15', '30', '45'])}"
    return data


def seed_events_small(facility: Facility) -> None:
    """Create 25 fixed demo events over the last 80 days."""
    if Event.objects.filter(facility=facility).exists():
        return

    facility_users = list(User.objects.filter(facility=facility))
    staff_users = [u for u in facility_users if u.role in (User.Role.STAFF, User.Role.LEAD)]
    if not staff_users:
        staff_users = facility_users
    clients = list(Client.objects.filter(facility=facility))
    doc_types = {dt.name: dt for dt in DocumentType.objects.filter(facility=facility)}

    today = date.today()
    event_defs = [
        ("Kontakt", 0, False, 2, {"dauer": 15, "leistungen": ["beratung", "essen"], "notiz": "Erstbesuch"}),
        (
            "Kontakt",
            1,
            False,
            5,
            {"dauer": 30, "leistungen": ["kleidung", "sachspenden"], "notiz": "Winterjacke ausgegeben"},
        ),
        ("Kontakt", None, True, 8, {"dauer": 10, "leistungen": ["essen"], "notiz": "Kurzer Besuch"}),
        ("Kontakt", 2, False, 12, {"dauer": 20, "leistungen": ["duschen", "waesche"]}),
        ("Kontakt", 3, False, 18, {"dauer": 45, "leistungen": ["beratung", "telefon"]}),
        ("Kontakt", None, True, 25, {"dauer": 5, "leistungen": ["sonstiges"]}),
        ("Kontakt", 4, False, 35, {"dauer": 25, "leistungen": ["essen", "beratung"]}),
        ("Kontakt", 0, False, 50, {"dauer": 20, "leistungen": ["post", "sachspenden"]}),
        ("Kontakt", 5, False, 65, {"dauer": 15, "leistungen": ["essen"]}),
        ("Kontakt", None, True, 80, {"dauer": 10}),
        ("Krisengespräch", 0, False, 3, {"art-der-krise": "psychische-krise", "dauer": 60}),
        ("Krisengespräch", 1, False, 30, {"art-der-krise": "substanzkrise", "dauer": 45}),
        ("Medizinische Versorgung", 2, False, 7, {"art-der-versorgung": "wundversorgung"}),
        ("Medizinische Versorgung", 3, False, 40, {"art-der-versorgung": "medikamentenausgabe"}),
        ("Spritzentausch", None, True, 4, {"ausgabe": 10, "rueckgabe": 8}),
        ("Spritzentausch", 4, False, 20, {"ausgabe": 5, "rueckgabe": 5}),
        ("Spritzentausch", None, True, 55, {"ausgabe": 15, "rueckgabe": 12}),
        ("Begleitung", 0, False, 10, {"ziel": "Jobcenter"}),
        ("Begleitung", 1, False, 45, {"ziel": "Krankenhaus"}),
        ("Beratungsgespräch", 4, False, 6, {"dauer": 30}),
        ("Beratungsgespräch", 6, False, 22, {"dauer": 45}),
        ("Vermittlung", 3, False, 15, {}),
        ("Vermittlung", 0, False, 60, {}),
        ("Notiz", 1, False, 9, {"notiz": "Termin beim Arzt vereinbart"}),
        (
            "Hausverbot",
            2,
            False,
            1,
            {
                "grund": "Wiederholte Verstöße gegen die Hausordnung",
                "bis": (today + timedelta(days=30)).isoformat(),
                "aktiv": True,
            },
        ),
    ]

    for idx, (dt_name, client_idx, is_anonymous, days_ago, data_json) in enumerate(event_defs):
        doc_type = doc_types.get(dt_name)
        if doc_type is None:
            continue
        client = clients[client_idx] if client_idx is not None else None
        hour, minute = random_time_of_day()
        base_date = timezone.now() - timedelta(days=days_ago)
        occurred = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=occurred,
            data_json=data_json,
            is_anonymous=is_anonymous,
            created_by=staff_users[idx % len(staff_users)],
        )


def seed_events_bulk(facility: Facility, users: list[User], clients: list[Client], cfg: dict) -> int:
    """Generate events with ``bulk_create`` for non-small scales.

    Returns the number of newly created events (0 when the facility is
    already at or above the target).
    """
    target = cfg["events_per_facility"]
    zeitraum = cfg["zeitraum_days"]

    existing_count = Event.objects.filter(facility=facility).count()
    if existing_count >= target:
        return 0

    to_create_count = target - existing_count
    doc_types = list(DocumentType.objects.filter(facility=facility))
    if not doc_types or not clients:
        return 0

    # Pre-build data templates per document type (skip encrypted fields).
    dt_data_templates = {}
    for dt in doc_types:
        dt_data_templates[dt.system_type or dt.name] = build_data_template(dt)

    # Realistic weighting by system_type: bans extremely rare
    dt_weights = {
        "contact": 40,
        "crisis": 12,
        "medical": 10,
        "needle_exchange": 10,
        "accompaniment": 8,
        "counseling": 8,
        "referral": 5,
        "note": 6,
        "ban": 0.2,
    }
    weights = [dt_weights.get(dt.system_type, 5) for dt in doc_types]
    today = timezone.localdate()

    now = timezone.now()
    # Fachkräfte (STAFF/LEAD) für gleichmäßige Verteilung
    staff_users = [u for u in users if u.role in (User.Role.STAFF, User.Role.LEAD)]
    if not staff_users:
        staff_users = users
    batch: list[Event] = []
    hausverbot_active_count = 0
    for i in range(to_create_count):
        doc_type = random.choices(doc_types, weights=weights, k=1)[0]
        is_anonymous = random.random() < 0.15
        client = None if is_anonymous else random.choice(clients)
        days_ago = weighted_days_ago(zeitraum)
        if days_ago == 0:
            hour, minute = random_time_of_day(max_hour=now.hour, max_minute=now.minute)
        else:
            hour, minute = random_time_of_day()
        base_date = now - timedelta(days=days_ago)
        occurred = min(
            base_date.replace(hour=hour, minute=minute, second=0, microsecond=0),
            now,
        )
        data_json = random_data(doc_type.system_type or doc_type.name, dt_data_templates)

        # Bans realistic: most expired, max 2 active (grund from pool)
        if doc_type.system_type == "ban":
            if hausverbot_active_count < 2 and random.random() < 0.15:
                data_json["aktiv"] = True
                data_json["bis"] = (today + timedelta(days=random.randint(7, 90))).isoformat()
                hausverbot_active_count += 1
            else:
                data_json["aktiv"] = False
                data_json["bis"] = (today - timedelta(days=random.randint(30, 365))).isoformat()

        batch.append(
            Event(
                facility=facility,
                client=client,
                document_type=doc_type,
                occurred_at=occurred,
                data_json=data_json,
                is_anonymous=is_anonymous,
                created_by=staff_users[i % len(staff_users)],
            )
        )

    if batch:
        for event in batch:
            event.data_json = encrypt_event_data(event.document_type, event.data_json)
        Event.objects.bulk_create(batch, batch_size=1000)

    return len(batch)
