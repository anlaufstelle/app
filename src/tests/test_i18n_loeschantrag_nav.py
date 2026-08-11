"""Refs #1144: i18n-Korrekturen.

1. Der Erfolgs-Flash beim Personen-Löschantrag muss den Umlaut „Löschantrag"
   tragen (Quelle war ASCII „Loeschantrag" — bei leerem DE-msgstr landete der
   ASCII-String direkt in der UI).
2. Englische Nav-Labels: „Dateien" → „Files" (war „Data"), „Löschfristen" →
   „Deletion deadlines" (war „Delete").
"""

import pytest
from django.urls import reverse
from django.utils.translation import gettext, override


@pytest.mark.django_db
class TestDeletionRequestFlashUmlaut:
    def test_flash_uses_umlaut(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_delete_request", kwargs={"pk": client_identified.pk}),
            {"reason": "Test-Begründung"},
            follow=True,
        )
        content = response.content.decode()
        assert "Löschantrag gestellt" in content
        assert "Loeschantrag gestellt" not in content


class TestEnglishNavLabels:
    def test_dateien_translates_to_files(self):
        with override("en"):
            assert gettext("Dateien") == "Files"

    def test_loeschfristen_translates_to_deletion_deadlines(self):
        with override("en"):
            assert gettext("Löschfristen") == "Deletion deadlines"


class TestEnglishHubLabels:
    """Refs #1813: die Labels der beiden Bereichs-Hubs und des umbenannten Reiters.

    Drei Entscheidungen, die der Test festhaelt, weil sie sonst beim naechsten
    ``makemessages``-Lauf unbemerkt kippen koennten:

    * „Datenschutz" nutzt die **bestehende** msgid (Fusszeile/Login-Seite) und
      bleibt damit „Privacy" — gettext kennt nur eine Uebersetzung je msgid,
      und eine zweite deutsche Vokabel nur fuer eine zweite englische zu
      erfinden waere Etikettenschwindel.
    * „Verwaltung" wird **nicht** „Administration": diese msgid existiert
      bereits fuer den Django-Admin-Link (``/admin-mgmt/``), auf den die
      Verwaltungs-Uebersicht selbst verweist. Zwei Dinge mit demselben Namen
      auf derselben Seite waeren schlechter als eine unscharfe Vokabel.
    * „Dokumentationstypen" war schon uebersetzt (Seiten-H1) — der Reitername
      erbt diese Uebersetzung, statt eine zweite einzufuehren.
    """

    def test_datenschutz_keeps_the_established_privacy_translation(self):
        with override("en"):
            assert gettext("Datenschutz") == "Privacy"

    def test_verwaltung_does_not_collide_with_administration(self):
        with override("en"):
            assert gettext("Verwaltung") == "Management"
            assert gettext("Administration") == "Administration"

    def test_dokumentationstypen_translates_to_document_types(self):
        with override("en"):
            assert gettext("Dokumentationstypen") == "Document types"

    def test_hub_subtitles_are_translated(self):
        with override("en"):
            assert gettext("Betroffenenrechte, Fristen und Nachweise.") == (
                "Data subject rights, deadlines and records."
            )
            assert gettext("Anwendungsbetreuung: Formulare, Felder, Datenübernahme und Zugänge.") == (
                "Application support: forms, fields, data transfer and access."
            )


class TestEnglishStatisticsTabLabels:
    """Refs #1813 (Etappe 3): die Reiter der Statistik-Reiterzeile und die zwei
    neuen Einstiege, die Etappe 3 sonst nur auf Deutsch hätte.

    Warum als Test und nicht „steht ja in der .po": ``makemessages`` schlägt
    für neue msgids **fuzzy** Übersetzungen aus ähnlichen Einträgen vor —
    „Datensatz-Berichte" bekam so „Data categories" (aus „Datenkategorien"),
    „Freigaben" das Verb „Release", „Statistik-Navigation" die
    „Privacy navigation" des Datenschutz-Bereichs. Fuzzy-Einträge kompiliert
    gettext nicht mit, der Reiter stünde also auf Englisch schlicht auf
    Deutsch da — und beim nächsten Lauf könnte jemand die Vorschläge
    unbesehen entfuzzyfizieren. Dieser Test hält die geprüften Vokabeln fest.
    """

    def test_statistics_tab_labels(self):
        with override("en"):
            assert gettext("Externer Bericht") == "External report"
            assert gettext("Datensatz-Berichte") == "Dataset reports"
            assert gettext("Freigaben") == "Releases"

    def test_statistics_subnav_aria_label_is_not_the_privacy_one(self):
        with override("en"):
            assert gettext("Statistik-Navigation") == "Statistics navigation"
            assert gettext("Datenschutz-Navigation") == "Privacy navigation"

    def test_search_footer_fallback_label(self):
        with override("en"):
            assert gettext("Erweiterte Suche öffnen") == "Open advanced search"

    def test_offline_conflict_entry_in_the_profile(self):
        with override("en"):
            assert gettext("Offline-Konflikte") == "Offline conflicts"
            assert gettext("Offline-Konflikte prüfen") == "Review offline conflicts"
