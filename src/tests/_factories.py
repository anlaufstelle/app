"""Schlanke Objekt-Factories fuer die drei haeufigsten Inline-Creates
(``Event``, ``DocumentType``, ``Client``) in der Testsuite (Refs #1839 E5b).

Kein factory-boy (YAGNI) — nur duenne Wrapper um
``Model.objects.create(...)``, die genau die je Modell fast immer
gesetzten Pflichtfelder als benannte Parameter erzwingen und alles
Uebrige unveraendert per ``**overrides`` durchreichen. Keine impliziten
Defaults fuer Felder, die ein Test heute explizit setzt (z. B.
``contact_stage``, ``occurred_at``, ``data_json``) — wer einen Wert
braucht, uebergibt ihn weiterhin selbst.

Jede Factory legt GENAU EIN Objekt an und gibt es zurueck — keine
Zusatzobjekte, keine Seiteneffekte. Bestehende ``.count()``-Assertions
bleiben deshalb unveraendert gueltig.
"""

from django.db import connection, transaction

from core.models import Client, DocumentType, Event

# Trigger name of migration 0199 (``event_ban_requires_person_insert``).
BAN_PERSON_INSERT_TRIGGER = "event_ban_requires_person_insert"


def insert_event_past_save(**fields):
    """Insert an event without ``Event.save()`` and without the ban person trigger.

    Only for fixtures that need a state the rule forbids for new rows, for
    example an anonymous ban event that predates the person rule. What is
    skipped is ``Event.save()`` and its person check, the ``pre_save``
    signals and the database trigger ``event_ban_requires_person_insert``
    (migration ``0199``), which is switched off for this one insert inside a
    transaction, so no other connection sees it off. The batch protection of
    ``Event.objects.bulk_create`` still encrypts sensitive fields and computes
    ``search_text``. Needs the table owner, which the test database user is.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {Event._meta.db_table} DISABLE TRIGGER {BAN_PERSON_INSERT_TRIGGER}")
        event = Event.objects.bulk_create([Event(**fields)])[0]
        cursor.execute(f"ALTER TABLE {Event._meta.db_table} ENABLE ALWAYS TRIGGER {BAN_PERSON_INSERT_TRIGGER}")
    return event


def make_event(*, facility, client, document_type, occurred_at, data_json, created_by, **overrides):
    """Erstellt ein ``Event`` mit den sechs praktisch immer gesetzten
    Kernfeldern (facility, client, document_type, occurred_at, data_json,
    created_by). Weitere Felder (``case``, ``episode``, ``is_deleted``,
    ``idempotency_key``, ...) nur ueber ``**overrides`` — kein Default.
    """
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=document_type,
        occurred_at=occurred_at,
        data_json=data_json,
        created_by=created_by,
        **overrides,
    )


def make_document_type(*, facility, name, **overrides):
    """Erstellt einen ``DocumentType`` mit den zwei stabilen Feldern
    (facility, name). Alle Konfigurationsfelder (``entity_type``,
    ``sensitivity``, ``system_type``, ``report_key``, ``tally_enabled``,
    ``is_active``, ``sort_order``, ``icon``, ``color``,
    ``retention_days``, ...) variieren stark je Testfall und haben
    deshalb bewusst KEINEN Default — nur ueber ``**overrides``.
    """
    # A ban type must carry a minimum contact stage (the model refuses it
    # otherwise); supply the lowest valid stage so ban fixtures stay terse. Tests
    # that exercise the rule pass ``min_contact_stage`` explicitly.
    if overrides.get("system_type") == DocumentType.SystemType.BAN:
        overrides.setdefault("min_contact_stage", "identified")
    return DocumentType.objects.create(facility=facility, name=name, **overrides)


def make_client(*, facility, pseudonym, created_by, **overrides):
    """Erstellt einen ``Client`` mit den drei stabilen Feldern (facility,
    pseudonym, created_by). ``contact_stage``/``age_cluster`` und andere
    Felder variieren je Testfall und haben deshalb keinen Default — nur
    ueber ``**overrides``.
    """
    return Client.objects.create(facility=facility, pseudonym=pseudonym, created_by=created_by, **overrides)
