"""Randfall: Service-``ValidationError`` mit ``message_dict`` im Create-Pfad.

Review-Auflage aus dem DoD-Audit zu #1831. ``ClientCreateView.post`` und
``CaseCreateView.post`` fangen eine ``ValidationError`` aus
``create_client``/``create_case`` (bzw. aus dem vorgelagerten
``stage_uploads``-Policy-/Virus-Reject) und haengen sie als Non-Field-Error
ans Formular. Der Randfall ist die **feldbezogene** Variante: eine
``ValidationError({"feld": ["Nachricht"]})`` traegt kein ``.message``,
sondern ein ``message_dict``.

Zwei Fallen, die dieser Test zumauert:

1. ``form.add_error(None, str(e))`` wuerde bei einem ``message_dict`` das
   **dict-repr** in die UI schreiben (``{'feld': ['Nachricht']}``) — der
   Nutzerin waere nicht zu helfen. Die Views nutzen deshalb ``e.messages``,
   das feldfrei auf die reinen Meldungstexte abflacht.
2. Ein ``add_error(feld, …)``-Mapping ginge nicht: die Service-Feldnamen
   (Slugs der dynamischen Zusatzfelder) sind keine Felder DIESER Formulare —
   Django wuerde mit ``ValueError`` aussteigen (500).

Erwartet ist in beiden Views: 200-Formular-Redisplay, Meldung(en) als
Non-Field-Error ohne dict-repr, kein Datensatz angelegt, kein 500.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import Case, Client

# Slug eines dynamischen Zusatzfeldes — bewusst KEIN Feldname von
# ClientForm/CaseForm, damit ein (falsches) add_error-Mapping auffliegen wuerde.
SERVICE_FIELD = "zusatzfeld_slug"
SERVICE_MESSAGE = "Zusatzfeld gehoert nicht zu diesem Dokumentationstyp."


def _service_error():
    """Feldbezogene Service-``ValidationError`` (traegt ``message_dict``)."""
    return ValidationError({SERVICE_FIELD: [SERVICE_MESSAGE]})


def _assert_flat_non_field_errors(response, model, **exists_filter):
    """Gemeinsame Erwartung beider Views."""
    assert response.status_code == 200
    form = response.context["form"]
    non_field_errors = [str(message) for message in form.non_field_errors()]
    assert non_field_errors == [SERVICE_MESSAGE]
    # Kernaussage der Auflage: kein dict-repr im gerenderten Fehlertext.
    for message in non_field_errors:
        assert "{" not in message, f"dict-repr in Fehlermeldung: {message!r}"
    assert SERVICE_MESSAGE in response.content.decode()
    assert not model.objects.filter(**exists_filter).exists()


@pytest.mark.django_db
class TestClientCreateServiceValidationFallback:
    def test_message_dict_becomes_flat_non_field_error(self, client, staff_user, monkeypatch):
        def _raise(**kwargs):
            raise _service_error()

        monkeypatch.setattr("core.views.clients.create_client", _raise)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_create"),
            {"pseudonym": "Randfall-01", "contact_stage": "identified", "age_cluster": "unknown"},
        )
        _assert_flat_non_field_errors(response, Client, pseudonym="Randfall-01")


@pytest.mark.django_db
class TestCaseCreateServiceValidationFallback:
    def test_message_dict_becomes_flat_non_field_error(self, client, staff_user, client_identified, monkeypatch):
        def _raise(**kwargs):
            raise _service_error()

        monkeypatch.setattr("core.views.cases.create_case", _raise)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_create"),
            {
                "title": "Randfall-Fall",
                "description": "Beschreibung",
                "client": str(client_identified.pk),
            },
        )
        _assert_flat_non_field_errors(response, Case, title="Randfall-Fall")
