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
            ClientModel.objects.create(facility=facility, pseudonym=f"Batch-{i:02d}", created_by=staff_user).pk
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


@pytest.mark.django_db
class TestSearchThresholdBoundaries:
    """WP4: Grenzwerte für ``search_trigram_threshold`` (0.0, 1.0) und
    Query-Längen an der 2-Zeichen-Grenze.

    Am Code verifiziert:
    - ``search_similar_clients`` filtert ``similarity__gte=threshold`` und
      schließt anschließend ``pseudonym__icontains=query`` aus (siehe
      ``src/core/services/search.py``).
    - Die Mindest-Query-Länge liegt bei ``len(query) < 2 → []`` — d.h. "S"
      ist leer, "Sc" wird durchgelassen (der pg_trgm-Match kann trotzdem
      leer bleiben, aber der Service wirft keinen Fehler).
    - Der Settings-Validator erlaubt Werte im Bereich ``[0.0, 1.0]`` (siehe
      ``src/core/models/settings.py``: MinValueValidator(0.0), MaxValueValidator(1.0)).
    """

    def test_threshold_zero_returns_non_empty(self, facility, staff_user):
        """Bei threshold=0.0 wird jede Ähnlichkeit >= 0 akzeptiert; es muss
        mindestens ein Kandidat zurückkommen, solange es Clients gibt, die
        nicht bereits durch den icontains-Filter ausgeschlossen sind.
        """
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Anton", created_by=staff_user)
        ClientModel.objects.create(facility=facility, pseudonym="Zebra", created_by=staff_user)

        # Query "Baer" matcht weder "Anton" noch "Zebra" als Substring →
        # beide bleiben Fuzzy-Kandidaten. Bei threshold=0.0 muss der Service
        # mindestens einen liefern (und nicht leer sein), da similarity >= 0
        # immer erfüllt ist.
        result = search_similar_clients(facility, "Baer", threshold=0.0)
        assert len(result) >= 1
        # Keiner der Treffer darf als icontains-Match auftauchen — im Service
        # werden diese explizit ausgeschlossen.
        for c in result:
            assert "Baer".lower() not in c.pseudonym.lower()

    def test_threshold_one_excludes_fuzzy_exact_is_filtered(self, facility, staff_user):
        """Bei threshold=1.0 werden Fuzzy-Treffer nur akzeptiert, wenn die
        Ähnlichkeit exakt 1.0 ist. ``search_similar_clients`` schließt aber
        gleichzeitig ``icontains=query`` aus — ein exakter Treffer wie
        "Schmidt" bei Query "Schmidt" fällt dadurch raus. Ergebnis also leer.
        """
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)
        ClientModel.objects.create(facility=facility, pseudonym="Schmitt", created_by=staff_user)

        # Query exakt gleich einem Pseudonym: icontains-Match → Service
        # schließt "Schmidt" aus. "Schmitt" hat similarity < 1.0 → kein Match.
        result = search_similar_clients(facility, "Schmidt", threshold=1.0)
        assert result == []

    def test_threshold_one_near_duplicate_rejected(self, facility, staff_user):
        """Bei threshold=1.0 werden sogar leichte Variationen zurückgewiesen."""
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Mueller", created_by=staff_user)
        # Query "Muller" ist ähnlich, aber nicht identisch → bei threshold=1.0 leer.
        assert search_similar_clients(facility, "Muller", threshold=1.0) == []

    def test_query_length_two_does_not_raise(self, facility, staff_user):
        """Queries mit exakt 2 Zeichen werden durchgelassen (Mindestlänge
        ``len(query) < 2``). Der Service muss eine Liste zurückgeben, nie
        einen Fehler werfen."""
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)

        # 2 Zeichen: nicht geblockt, Return muss eine Liste sein
        result = search_similar_clients(facility, "Sc")
        assert isinstance(result, list)

    def test_query_length_one_returns_empty(self, facility, staff_user):
        """1-Zeichen-Queries werden vor pg_trgm abgefangen und sind leer."""
        from core.models import Client as ClientModel
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)
        assert search_similar_clients(facility, "S") == []
        assert search_similar_clients(facility, "") == []

    def test_threshold_zero_via_facility_settings(self, facility, staff_user):
        """Auch via facility.settings darf 0.0 konfiguriert werden (Validator-
        Minimum). Der Service nutzt den Settings-Wert, wenn kein Override
        übergeben wird."""
        from core.models import Client as ClientModel
        from core.models import Settings
        from core.services.search import search_similar_clients

        ClientModel.objects.create(facility=facility, pseudonym="Anton", created_by=staff_user)
        Settings.objects.update_or_create(facility=facility, defaults={"search_trigram_threshold": 0.0})

        # Bei 0.0 muss mindestens der Nicht-icontains-Match "Anton"
        # durchkommen, wenn die Query selbst kein Substring ist.
        result = search_similar_clients(facility, "Xyz")
        assert len(result) >= 1
