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

from core.models import Client, DocumentType, Event


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
    return DocumentType.objects.create(facility=facility, name=name, **overrides)


def make_client(*, facility, pseudonym, created_by, **overrides):
    """Erstellt einen ``Client`` mit den drei stabilen Feldern (facility,
    pseudonym, created_by). ``contact_stage``/``age_cluster`` und andere
    Felder variieren je Testfall und haben deshalb keinen Default — nur
    ueber ``**overrides``.
    """
    return Client.objects.create(facility=facility, pseudonym=pseudonym, created_by=created_by, **overrides)
