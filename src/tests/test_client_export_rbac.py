"""RBAC-Filterung im Client-Export (DSGVO Art. 15/20).

Refs Matrix AUD-SEC-EXPORT-01 (Master #922).

Der Service :func:`core.services.client.export.export_client_data`
sammelt alle personenbezogenen Daten zu einem Klienten und filtert
Events ueber :meth:`Event.objects.visible_to(user)`. Damit gilt die
zentrale Sensitivity-Matrix aus ``core.services.compliance.sensitivity``:

- **ASSISTANT** (max rank 0 / NORMAL): sieht nur NORMAL-Events.
- **STAFF** (max rank 1 / NORMAL+ELEVATED): sieht NORMAL+ELEVATED.
- **LEAD/FACILITY_ADMIN** (max rank 2): sieht alles inkl. HIGH.

Die Tests verifizieren, dass der Service-Export diesen Filter
respektiert — kein Leak von ELEVATED-Eventdaten an Assistent:innen,
auch wenn die View-Schicht (Lead/Admin-Mixin) das in der Praxis bereits
blockt (Defense in Depth, Refs #734).
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import Event
from core.services.client import export_client_data


@pytest.fixture
def crisis_event_qualified(facility, client_qualified, doc_type_crisis, lead_user):
    """ELEVATED-Sensitivity-Event auf einem QUALIFIED-Klienten (Krisengespraech)."""
    return Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_crisis,
        occurred_at=timezone.now(),
        data_json={"notiz-krise": "Geheime Krise"},
        created_by=lead_user,
    )


@pytest.fixture
def crisis_event_identified(facility, client_identified, doc_type_crisis, lead_user):
    """ELEVATED-Sensitivity-Event auf einem IDENTIFIED-Klienten."""
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_crisis,
        occurred_at=timezone.now(),
        data_json={"notiz-krise": "Notiz fuer Staff"},
        created_by=lead_user,
    )


@pytest.fixture
def contact_event_qualified(facility, client_qualified, doc_type_contact, staff_user):
    """NORMAL-Sensitivity-Event auf einem QUALIFIED-Klienten (Kontakt)."""
    return Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 30, "notiz": "Standardkontakt"},
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestClientExportRbac:
    """Service-Layer-Tests fuer rollenbasierte Sichtbarkeit im Client-Export.

    API: ``export_client_data(client, facility, user)`` — der ``user``-
    Parameter ist Pflicht (Refs #734), damit die Sensitivity-Filterung
    greift. Die Filterung passiert in ``_gather_events`` ueber
    ``Event.objects.visible_to(user)``.
    """

    def test_lead_export_includes_sensitive_fields(
        self, facility, client_qualified, crisis_event_qualified, contact_event_qualified, lead_user
    ):
        """LEAD darf ELEVATED-Sensitivity-Events sehen; der Krisengespraech-
        Event muss im Export auftauchen, inkl. ``data``."""
        export = export_client_data(client_qualified, facility, lead_user)

        doc_types = [e["document_type"] for e in export["events"]]
        assert "Krisengespräch" in doc_types
        assert "Kontakt" in doc_types
        assert len(export["events"]) == 2

        # Die Daten der ELEVATED-Events landen im Export (decrypted/serialized).
        crisis = next(e for e in export["events"] if e["document_type"] == "Krisengespräch")
        assert "data" in crisis and crisis["data"]

    def test_assistant_export_redacts_sensitive_fields(
        self, facility, client_qualified, crisis_event_qualified, contact_event_qualified, assistant_user
    ):
        """ASSISTANT (max rank NORMAL) darf den ELEVATED-Krisen-Event nicht
        sehen; ``Event.objects.visible_to(assistant)`` filtert ihn raus.
        Nur der NORMAL-Kontakt-Event bleibt im Export.
        """
        export = export_client_data(client_qualified, facility, assistant_user)

        doc_types = [e["document_type"] for e in export["events"]]
        assert "Krisengespräch" not in doc_types, (
            "ELEVATED-Event darf nicht im Assistent:innen-Export landen — "
            "Sensitivity-Matrix gibt der Rolle nur NORMAL frei."
        )
        assert "Kontakt" in doc_types
        assert len(export["events"]) == 1

    def test_staff_export_includes_sensitive_for_identified_clients(
        self, facility, client_identified, crisis_event_identified, staff_user
    ):
        """STAFF (max rank ELEVATED) sieht den ELEVATED-Krisen-Event auch fuer
        IDENTIFIED-Klienten. Die ContactStage des Klienten beeinflusst die
        Sichtbarkeit hier *nicht* — entscheidend ist die DocumentType-
        Sensitivity vs. Rollen-Rank.
        """
        export = export_client_data(client_identified, facility, staff_user)

        doc_types = [e["document_type"] for e in export["events"]]
        assert "Krisengespräch" in doc_types, "STAFF darf ELEVATED-Events sehen (rank 1 >= ELEVATED rank 1)."
        assert len(export["events"]) == 1
