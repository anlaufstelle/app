"""Tests für Suche (C.8)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Event


@pytest.mark.django_db
class TestSearch:
    def test_search_page_renders(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:search"))
        assert response.status_code == 200

    def test_search_finds_client_by_pseudonym(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:search"), {"q": "ID-01"})
        content = response.content.decode()
        assert "Test-ID-01" in content

    def test_search_finds_events_by_client_pseudonym(
        self, client, staff_user, facility, doc_type_contact, client_identified
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:search"), {"q": "ID-01"})
        content = response.content.decode()
        assert "Kontakt" in content

    def test_search_facility_scoping(self, client, staff_user, facility, organization):
        """Klientel anderer Facilities nicht sichtbar."""
        from core.models import Client as ClientModel
        from core.models import Facility

        other_facility = Facility.objects.create(organization=organization, name="Andere")
        ClientModel.objects.create(facility=other_facility, pseudonym="Geheim-01", created_by=staff_user)
        client.force_login(staff_user)
        response = client.get(reverse("core:search"), {"q": "Geheim"})
        assert "Geheim-01" not in response.content.decode()

    def test_search_empty_query(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:search"), {"q": ""})
        assert response.status_code == 200

    def test_search_finds_events_by_data_json(self, client, staff_user, facility, doc_type_contact, client_identified):
        """Search matches text inside data_json via RawSQL annotation."""
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "Spezialberatung durchgeführt"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:search"), {"q": "Spezialberatung"})
        content = response.content.decode()
        assert "Spezialberatung" in content or "Kontakt" in content

    def test_search_data_json_matches_values_not_keys(
        self, client, staff_user, facility, doc_type_contact, client_identified
    ):
        """Searching for a JSON key name must NOT match; only values count."""
        from core.services.search import search_clients_and_events

        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"vorname": "Max"},
            created_by=staff_user,
        )

        # "vorname" is a key but not a value → no event match
        _, events = search_clients_and_events(facility, staff_user, "vorname")
        assert len(events) == 0

        # "Max" is a value → should match
        _, events = search_clients_and_events(facility, staff_user, "Max")
        assert len(events) == 1

    def test_search_data_json_key_only_slug_no_results(
        self, client, staff_user, facility, doc_type_contact, client_identified
    ):
        """Slug that appears only as a key and nowhere as a value → 0 data results."""
        from core.services.search import search_clients_and_events

        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            created_by=staff_user,
        )

        # "dauer" only appears as a JSON key, not as a value → no event match
        _, events = search_clients_and_events(facility, staff_user, "dauer")
        assert len(events) == 0

    def test_search_excludes_events_matching_only_sensitive_fields(
        self, client, staff_user, facility, client_identified
    ):
        """Event with NORMAL doc_type but a HIGH-sensitivity field must not
        appear in results for a staff user when the match is only in the HIGH field."""
        from core.models import DocumentType, DocumentTypeField, FieldTemplate
        from core.services.search import search_clients_and_events

        doc_type = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.NOTE,
            sensitivity=DocumentType.Sensitivity.NORMAL,
            name="Notiz mit High-Feld",
        )
        ft_normal = FieldTemplate.objects.create(
            facility=facility,
            name="Kommentar",
            field_type=FieldTemplate.FieldType.TEXT,
            sensitivity="",  # inherits NORMAL from doc_type
        )
        ft_high = FieldTemplate.objects.create(
            facility=facility,
            name="Geheim",
            field_type=FieldTemplate.FieldType.TEXT,
            sensitivity="high",
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_normal, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_high, sort_order=1)

        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={ft_normal.slug: "harmloses Wort", ft_high.slug: "Geheimwort123"},
            created_by=staff_user,
        )

        # Staff (max ELEVATED) searches for value only in HIGH field → no match
        _, events = search_clients_and_events(facility, staff_user, "Geheimwort123")
        assert len(events) == 0

        # Staff searches for value in NORMAL field → match
        _, events = search_clients_and_events(facility, staff_user, "harmloses Wort")
        assert len(events) == 1

    def test_fuzzy_finds_typo_variant(self, client, staff_user, facility):
        """pg_trgm: 'Schmitt' findet 'Schmidt' (Tippfehler-Toleranz)."""
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)

        similar = search_similar_clients(facility, "Schmitt")
        assert [c.pseudonym for c in similar] == ["Schmidt"]

    def test_fuzzy_excludes_exact_matches(self, client, staff_user, facility):
        """exclude_pks verhindert Doppelung zwischen exakter und Fuzzy-Sektion."""
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        exact = ClientModel.objects.create(facility=facility, pseudonym="Müller", created_by=staff_user)

        similar = search_similar_clients(facility, "Müller", exclude_pks={exact.pk})
        assert similar == []

    def test_fuzzy_excludes_icontains_overflow(self, client, staff_user, facility):
        """Alle icontains-Treffer werden aus Fuzzy ausgeschlossen, nicht nur die
        angezeigten (Refs #580). Sonst würden Overflow-Exact-Hits als Fuzzy
        mislabeled und echte Fuzzy-Kandidaten verdrängen."""
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        # 10 exakte Treffer für "Batch"
        batch_pks = {
            ClientModel.objects.create(
                facility=facility, pseudonym=f"Batch-{i:02d}", created_by=staff_user
            ).pk
            for i in range(10)
        }

        # Nur die ersten 5 als displayed exact hits vortäuschen
        displayed = set(list(batch_pks)[:5])

        similar = search_similar_clients(facility, "Batch", exclude_pks=displayed, max_results=10)
        # Kein Batch-* darf mehr dabei sein — weder displayed noch überlauf.
        similar_pks = {c.pk for c in similar}
        assert similar_pks.isdisjoint(batch_pks)

    def test_fuzzy_respects_threshold(self, client, staff_user, facility):
        """Hoher Threshold filtert ähnliche, aber nicht identische Pseudonyme."""
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)

        # Niedrig: Match
        assert len(search_similar_clients(facility, "Schmitt", threshold=0.3)) == 1
        # Hoch: kein Match
        assert search_similar_clients(facility, "Schmitt", threshold=0.95) == []

    def test_fuzzy_facility_scoping(self, client, staff_user, facility, organization):
        """Fuzzy-Suche darf nur eigene Facility treffen."""
        from core.models import Client as ClientModel
        from core.models import Facility
        from core.services.search import search_similar_clients

        other = Facility.objects.create(organization=organization, name="Andere")
        ClientModel.objects.create(facility=other, pseudonym="Schmidt", created_by=staff_user)

        assert search_similar_clients(facility, "Schmitt") == []

    def test_fuzzy_excludes_inactive(self, client, staff_user, facility):
        """Inaktive Klienten erscheinen nicht in Fuzzy-Ergebnissen."""
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Schmidt", is_active=False, created_by=staff_user)
        assert search_similar_clients(facility, "Schmitt") == []

    def test_fuzzy_short_query_returns_empty(self, client, staff_user, facility):
        """Queries unter 2 Zeichen liefern keine Fuzzy-Treffer (Rauschen vermeiden)."""
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)
        assert search_similar_clients(facility, "") == []
        assert search_similar_clients(facility, "S") == []

    def test_fuzzy_uses_facility_threshold(self, client, staff_user, facility):
        """Threshold aus facility.settings wird genutzt, wenn kein Override."""
        from core.models import Client as ClientModel
        from core.models import Settings
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)
        Settings.objects.update_or_create(facility=facility, defaults={"search_trigram_threshold": 0.95})

        assert search_similar_clients(facility, "Schmitt") == []

    def test_search_view_surfaces_similar_section(self, client, staff_user, facility):
        """Integrationstest: View rendert „Ähnliche Pseudonyme" bei Tippfehler."""
        from core.models import Client as ClientModel

        ClientModel.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)
        client.force_login(staff_user)
        response = client.get(reverse("core:search"), {"q": "Schmitt"})
        content = response.content.decode()
        assert "Ähnliche Pseudonyme" in content
        assert "Schmidt" in content

    def test_search_htmx_returns_partial(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:search"),
            {"q": "ID-01"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "<!DOCTYPE html>" not in response.content.decode()
        assert "Test-ID-01" in response.content.decode()
