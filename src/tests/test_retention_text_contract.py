"""Public privacy texts describe manual approval and its opt-in exception."""

import pytest
from django.urls import reverse
from django.utils.translation import override

from core.services.client import render_document
from core.services.compliance import get_activity

CONTRACT = (
    "zwei verschiedene",
    "aktive",
    "eigenen Einrichtung",
    "Leitung oder Anwendungsbetreuung",
    "Löschbestätigung",
    "1 von 2",
    "Zurückstellen",
    "Hold",
    "Ablehnung",
    "verwerfen die erste Freigabe",
    "mit Audit",
    "nur einem",
    "Ausnahme",
    "Opt-in, standardmäßig aus",
    "maximalen Zahl von Zurückstellungen und Ablauf der Wiedervorlage",
    "Genehmiger für den Aufbewahrungslauf",
    "Genehmiger für Löschanträge",
)


def assert_retention_contract(content):
    flat = " ".join(content.split())
    missing = [phrase for phrase in CONTRACT if phrase not in flat]
    assert missing == [], f"Missing retention guidance: {missing}"
    assert "Freigabe durch eine Person" not in flat
    assert "mindestens ein aktives Konto" not in flat


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["informationspflichten", "dsfa", "toms", "verarbeitungsverzeichnis", "loeschkonzept"])
def test_rendered_package_explains_two_person_contract(facility, settings_obj, slug):
    content, _ = render_document(slug, facility)
    assert_retention_contract(content)


@pytest.mark.django_db
def test_help_response_explains_two_person_contract(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("core:help_page", kwargs={"slug": "datenschutz"}))
    assert response.status_code == 200
    assert_retention_contract(response.content.decode())


def test_generated_vvt_explains_two_person_contract():
    with override("de"):
        assert_retention_contract(str(get_activity("klienten_stammdaten")["retention_period"]))


@pytest.mark.django_db
def test_vvt_explains_which_deferrals_count(facility, settings_obj):
    rendered, _ = render_document("verarbeitungsverzeichnis", facility)
    with override("de"):
        generated = str(get_activity("klienten_stammdaten")["retention_period"])
    for content in (rendered, generated):
        assert "Es zählen nur Zurückstellungen durch Personen mit dem Recht „Löschbestätigung“" in content
        assert "wenn kein Aufschub desselben Vorschlags mehr läuft" in content
