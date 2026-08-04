"""Refs #1744: Verhaltenstest der EN-Oberflaeche fuer die Berichts-Artefakte.

#1744 hat die Strings der
Reporting-Backends auf ``gettext_lazy`` umgestellt — Spaltenkoepfe
(``FieldSpec.label``), Vollstaendigkeits-Meldungen und den Quellenausweis aus
``core.reporting.data_source``. Das allein bewirkt nichts: ohne Katalog-Eintrag
liefert ``gettext`` die deutsche ``msgid`` zurueck, die englische Oberflaeche
saehe also unveraendert "Kategorie"/"Anzahl"/"Datenquelle". Erst der EN-Katalog
(Refs #1745) macht die
Umstellung wirksam.

Dieser Test prueft deshalb das **Verhalten** statt der Katalog-Statistik: was
kommt bei aktiver Sprache ``en`` tatsaechlich aus Panel und JSON-Export heraus.
Er ist die Abnahme fuer #1744 und zugleich der Regressions-Schutz dagegen, dass
ein spaeterer ``makemessages``-Lauf diese msgids wieder fuzzy macht (fuzzy wird
beim Kompilieren verworfen -> deutscher Fallback).

Drei Dinge bleiben bewusst **nicht** englisch und werden gegengeprueft:

* ``data_source`` ist ein stabiler Code (``"live"``), kein Text — er darf sich
  mit der Oberflaechensprache nie aendern, sonst muesste ein Konsument deutschen
  Fliesstext parsen.
* Die Kategorienamen aus ``CATEGORY_MAP`` ("Kontakte", "Beratung", …) sind
  **Daten**: sie landen in Snapshots und werden ueber Monate gemergt, eine
  locale-abhaengige Fassung waere ein Merge-Bug.
* Die k-Anon-Ersatztexte duerfen auch englisch **keine Ziffer** bekommen — ein
  Zahlwort oder eine Bezugsgroesse in der Uebersetzung wuerde die unterdrueckte
  Zahl wieder rueckrechenbar machen.

Die Sprache wird im Request-Pfad ueber ``User.preferred_language`` gesetzt —
das ist der Sprachumschalter der App. Weder ``translation.override`` noch der
``Accept-Language``-Header wirken dort: ``UserLanguageMiddleware`` (Refs #670)
laeuft nach ``LocaleMiddleware``, ignoriert den Header bewusst und aktiviert
die Nutzerpraeferenz. Ein umschliessendes ``override`` waere im View-Test
wirkungslos und der Test damit falsch-gruen. ``override`` steht deshalb nur um
die direkten Backend-Aufrufe.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from core.reporting.data_source import DATA_SOURCE_NOTES, LIVE
from core.reporting.registry import get_backend

pytestmark = pytest.mark.django_db

#: Deutsche Wortstaemme, die in einem englischen Berichts-Artefakt nichts zu
#: suchen haben. Bewusst Stamm-Formen (nicht ganze Saetze): sie greifen auch,
#: wenn eine msgid spaeter umformuliert wird.
DE_STEMS = ("Ereignis", "Kategorie", "Anzahl", "unterdrückt", "Live-Abfrage", "Berichtszuordnung")


def _de_stems_in(text: str) -> list[str]:
    return [stem for stem in DE_STEMS if stem in text]


def _speaks_english(user):
    """Stellt die Oberflaechensprache des Users auf Englisch (Sprachumschalter)."""
    user.preferred_language = "en"
    user.save(update_fields=["preferred_language"])
    return user


class TestBerichtsPanelEnglisch:
    """``DatasetReportPanelView`` fuer ``sample-report-1`` unter ``en``."""

    def test_panel_zeigt_keinen_deutschen_backend_string(self, client, lead_user, doc_type_contact, sample_event):
        """Spaltenkoepfe, Vollstaendigkeitshinweis und Quellenausweis sind englisch.

        ``doc_type_contact`` traegt bewusst keinen ``system_type`` (Fixture-Default)
        — damit erzeugt ``validate_completeness`` die Meldung "ohne
        Berichtszuordnung", die genau der Fall ist, den #1744 uebersetzbar
        gemacht hat.
        """
        client.force_login(_speaks_english(lead_user))

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        assert response.status_code == 200
        content = response.content.decode()
        assert not _de_stems_in(content), f"deutsche Backend-Strings im EN-Panel: {_de_stems_in(content)}"
        assert "Datenquelle" not in content
        # Gegenprobe: die englischen Fassungen sind wirklich da (der Test faellt
        # sonst auch dann gruen aus, wenn das Panel gar nichts gerendert hat).
        assert "Category" in content
        assert "Count" in content
        assert "Data source" in content
        assert "Example Activity Report" in content

    def test_panel_bleibt_auf_deutsch_ohne_praeferenz(self, client, lead_user, doc_type_contact, sample_event):
        """Gegenprobe: ohne EN-Praeferenz steht dort weiterhin Deutsch.

        Ohne diese Probe koennte der Test oben auch gruen sein, weil das Panel
        die Strings gar nicht mehr rendert.
        """
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        content = response.content.decode()
        assert "Kategorie" in content
        assert "Anzahl" in content
        assert "Datenquelle" in content

    def test_kategorienamen_bleiben_sprachunabhaengig(self, client, lead_user, doc_type_contact, sample_event):
        """``CATEGORY_MAP``-Werte sind Daten, keine UI-Strings — sie bleiben deutsch."""
        doc_type_contact.system_type = "contact"
        doc_type_contact.save()
        client.force_login(_speaks_english(lead_user))

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        assert "Kontakte" in response.content.decode()


class TestJsonExportEnglisch:
    """``DatasetExportView`` (JSON) unter ``en`` — Labels, Meldungen, Quellenausweis."""

    @pytest.fixture
    def payload(self, client, admin_user, doc_type_contact, sample_event):
        client.force_login(_speaks_english(admin_user))
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "sample-report-1",
                "format": "json",
                "date_from": str(today - timedelta(days=30)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 200
        return json.loads(response.content)

    def test_field_spec_labels_englisch(self, payload):
        labels = [spec["label"] for spec in payload["field_specs"]]
        assert not _de_stems_in(" ".join(labels)), labels
        assert labels == ["Category", "Count"]

    def test_completeness_meldungen_englisch(self, payload):
        messages = [issue["message"] for issue in payload["completeness"]]
        assert messages, "Erwartet mindestens die Meldung zum unmapped Dokumentationstyp"
        assert not _de_stems_in(" ".join(messages)), messages

    def test_data_source_note_englisch(self, payload):
        assert not _de_stems_in(payload["data_source_note"]), payload["data_source_note"]
        assert payload["data_source_note"] == "Live query at the time of creation"

    def test_data_source_bleibt_stabiler_code(self, payload):
        """Der Code ist Maschinen-Vertrag und darf sich mit der Sprache NICHT aendern."""
        assert payload["data_source"] == "live"


class TestBackendStringsUnterOverride:
    """Direkte Backend-Aufrufe unter ``override("en")`` — ohne Request-Pfad.

    Regressions-Lock auf Katalog-Ebene: schlaegt der Katalog fuer eine dieser
    msgids wieder auf fuzzy/leer um, faellt hier zuerst auf.
    """

    def test_field_spec_labels(self):
        backend = get_backend("sample-report-1")
        with override("en"):
            assert [str(spec.label) for spec in backend.get_field_specs()] == ["Category", "Count"]

    def test_beispiel_backend_labels(self):
        backend = get_backend("example")
        with override("en"):
            assert [str(spec.label) for spec in backend.get_field_specs()] == [
                "Document type",
                "Category",
                "System type",
                "Count",
            ]

    def test_data_source_notes(self):
        with override("en"):
            assert str(DATA_SOURCE_NOTES[LIVE]) == "Live query at the time of creation"
            assert str(DATA_SOURCE_NOTES["snapshot_hybrid"]) == "Monthly aggregates"

    def test_k_anon_ersatztexte_bleiben_ziffernfrei(self):
        """K-Anon-Invariante gilt je Sprache: keine Ziffer, keine Bezugsgroesse.

        Eine Uebersetzung, die die unterdrueckte Zahl oder ihr Komplement wieder
        nennt, haette die Unterdrueckung ausgehebelt. Seit K6
        (#1769) sind das
        die **einzigen** Meldungstexte, die ein externer Empfaenger je sieht —
        die frueheren bezifferten Export-Vorlagen (mit ``{count}``-Platzhalter)
        sind ersatzlos entfallen.
        """
        from core.reporting.backends.sample_report_1 import _EXPORT_ISSUE_CODES, _SUPPRESSED_MESSAGES

        with override("en"):
            texts = [str(_SUPPRESSED_MESSAGES[code]) for code in _EXPORT_ISSUE_CODES]

        assert len(texts) == 2
        for text in texts:
            assert not _de_stems_in(text), text
            assert not re.search(r"\d", text), f"Ziffer im k-Anon-Ersatztext: {text!r}"
