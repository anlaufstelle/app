"""Guard: Personenzuordnung bleibt nach Mindestkontaktstufen-Ablehnung erhalten (Refs #2345).

Reproduziert den Fresh-Install-Befund aus #2173 (Welle N): eine frisch
angelegte Person startet mit der Kontaktstufe "Nur Pseudonym". Mehrere Typen
des mitgelieferten Standardkatalogs verlangen "Mit Zusatzangaben" (#2270),
sodass der allererste Dokumentationsversuch abgewiesen wird. Beim erneut
angezeigten Formular blieben bisher alle Eingaben erhalten AUSSER der
Personenzuordnung — die Fachkraft musste die Person erneut suchen, und die
Meldung nannte den Grund, aber nicht den Weg zur Abhilfe.

Die Personenzuordnung haengt am Alpine-Autocomplete (``components/
_client_autocomplete.html``): das versteckte ``<input name="client">`` ist
reaktiv an ``selectedId`` gebunden, dessen Startwert allein aus den
``data-initial-client-id``/``data-initial-pseudonym``-Attributen des
umschliessenden ``<form>`` kommt (Refs #1836). Ein serverseitig im
Formular-Feld erhaltener Rohwert reicht deshalb NICHT — ohne die beiden
Kontextwerte initialisiert die Komponente mit einer leeren Auswahl und
ueberschreibt beim naechsten Submit den (eigentlich noch vorhandenen)
Personenbezug.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Client, DocumentType, DocumentTypeField, FieldTemplate


@pytest.mark.django_db
class TestEventFormMinContactLevelKeepsClient:
    @pytest.fixture
    def doc_type_min_stage(self, facility):
        return DocumentType.objects.create(
            facility=facility,
            name="Mit-Zusatzangaben-Pflicht",
            min_contact_stage=Client.ContactStage.QUALIFIED,
        )

    @pytest.fixture
    def client_pseudonym_only(self, facility, staff_user):
        return Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Nur-Pseudo-01",
            created_by=staff_user,
        )

    @pytest.fixture
    def doc_type_required_field(self, facility):
        """Kein Mindest-Kontaktstufen-Fehler — die Ablehnung kommt hier vom
        dynamischen Formular selbst (Pflichtfeld leer), Refs #2345."""
        doc_type = DocumentType.objects.create(facility=facility, name="Pflichtfeld-Pflicht")
        field = FieldTemplate.objects.create(
            facility=facility,
            name="Pflichtangabe",
            field_type=FieldTemplate.FieldType.TEXT,
            is_required=True,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=field, sort_order=0)
        return doc_type

    def _post(self, client, doc_type, client_obj, *, occurred_at="2026-09-23T10:00"):
        return client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": occurred_at,
                "client": str(client_obj.pk),
            },
        )

    def test_client_selection_survives_the_rejection(
        self, client, staff_user, doc_type_min_stage, client_pseudonym_only
    ):
        client.force_login(staff_user)
        response = self._post(client, doc_type_min_stage, client_pseudonym_only)
        assert response.status_code == 200
        html = response.content.decode()

        # (a) Die Personenzuordnung bleibt im gerenderten HTML erhalten. Die
        # Autocomplete-Komponente liest ihren Startzustand ausschliesslich aus
        # diesen data-Attributen (nicht aus dem rohen POST-Wert) — ohne sie
        # rendert der Hidden-Input beim naechsten Alpine-Tick wieder leer.
        assert f'data-initial-client-id="{client_pseudonym_only.pk}"' in html
        assert f'data-initial-pseudonym="{client_pseudonym_only.pseudonym}"' in html

    def test_message_names_the_remedy(self, client, staff_user, doc_type_min_stage, client_pseudonym_only):
        client.force_login(staff_user)
        response = self._post(client, doc_type_min_stage, client_pseudonym_only)
        html = response.content.decode()

        # (b) Die Meldung nennt Grund UND den Weg zur Abhilfe (Ort der
        # Kontaktstufen-Aenderung an den Personen-Stammdaten).
        assert "Person muss mindestens die Kontaktstufe" in html
        assert "Personen-Stammdaten" in html

    def test_other_inputs_stay_intact(self, client, staff_user, doc_type_min_stage, client_pseudonym_only):
        client.force_login(staff_user)
        response = self._post(client, doc_type_min_stage, client_pseudonym_only, occurred_at="2026-09-23T11:15")
        html = response.content.decode()

        # (c) Alle uebrigen Eingaben bleiben erhalten (Bestand, kein Regressionsziel).
        assert 'value="2026-09-23T11:15"' in html
        assert f'<option value="{doc_type_min_stage.pk}"' in html
        assert "selected" in html.split(f'<option value="{doc_type_min_stage.pk}"', 1)[1].split("</option>", 1)[0]

    def test_client_selection_survives_dynamic_form_rejection(
        self, client, staff_user, doc_type_required_field, client_pseudonym_only
    ):
        """Refs #2345: dieselbe Zuordnungs-Zusage gilt auch, wenn NICHT die
        Mindest-Kontaktstufe, sondern ein Pflichtfeld des dynamischen
        Formulars die Ablehnung ausloest (``not data_form.is_valid()``-Zweig,
        ``views/events.py``). Das gemeldete Pflichtfeld bleibt bewusst leer."""
        client.force_login(staff_user)
        response = self._post(client, doc_type_required_field, client_pseudonym_only)
        assert response.status_code == 200
        html = response.content.decode()
        assert "Dieses Feld ist zwingend erforderlich" in html or "erforderlich" in html

        assert f'data-initial-client-id="{client_pseudonym_only.pk}"' in html
        assert f'data-initial-pseudonym="{client_pseudonym_only.pseudonym}"' in html
