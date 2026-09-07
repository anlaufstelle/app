"""Die vier Domaenen-Berichtsschluessel des Beispiel-Sachberichts (Refs #2135).

Die Domaenen-Pakete Awareness/Frauenhaus/Jugendarbeit (Klammer
#2133) tragen
``report_key``-Werte, die der Bestand nicht kannte — ``incident``,
``protection``, ``group_activity`` und ``supply``. Weil der Werteraum die
Vereinigung der ``TypBindung``-Deklarationen ist
(``registry.get_report_key_vocabulary``), ist „Vokabular-Eintrag" und
„Backend-Bindung" **ein** Schritt: ein ``BindungsEintrag`` je Schluessel.

Diese Datei prueft die Zusagen des Tickets an genau den Stellen, an denen ein
neuer Schluessel wirken muss — und nur dort. Die Bestands-Pins (Zielmenge,
``CATEGORY_MAP``) liegen weiterhin in ``test_reporting_bindung.py``,
``test_reporting_sample_report_1.py`` und ``test_mutation_followup_export.py``.

Bewusst **kein** Test auf eine Endposition der neuen Zeilen: die publizierte
Fassung sortiert sichtbare Zeilen kanonisch nach Kategoriename
(``external_report.ORDER_CATEGORY`` in ``_ORDERS_CANONICAL_FOR_VISIBLE``), die
Deklarationsreihenfolge ist dort ohne Wirkung. Geprueft wird stattdessen, dass
die neuen Kategorien **wertunabhaengig** an derselben Stelle stehen — dieselbe
Zusage wie in ``TestZeilenpositionIstDatenunabhaengig``.
"""

from datetime import date, datetime

import pytest
from django.template.loader import render_to_string
from django.utils import timezone

from core.forms.config import DocumentTypeForm
from core.models import DocumentType, Event, Settings, StatisticsSnapshot, User
from core.reporting import registry
from core.reporting.base import EXCLUDED_KEY
from core.services.dashboard.snapshot import get_sample_report_1_statistics_hybrid

pytestmark = pytest.mark.django_db

# Die vier neuen Schluessel mit Label und Zielzeile — die eine Tabelle, gegen
# die alle Faelle dieser Datei laufen (Entscheid R-L1b-1).
DOMAENEN_SCHLUESSEL = (
    ("incident", "Gewaltvorfall", "Vorfälle"),
    ("protection", "Schutzmaßnahme", "Schutz"),
    ("group_activity", "Gruppenangebot", "Gruppenangebote"),
    # ``supply`` erweitert eine BESTEHENDE Zeile (wie ``medical``/
    # ``needle_exchange``) — keine neue Kategorie.
    ("supply", "Versorgung (Bedarf)", "Versorgung"),
)

DATE_FROM = date(2026, 2, 1)
DATE_TO = date(2026, 2, 28)


@pytest.fixture
def backend():
    return registry.get_backend("sample-report-1")


@pytest.fixture
def monatsverlauf():
    return registry.get_backend("monthly-trend")


@pytest.fixture
def user(facility):
    return User.objects.create_user(
        username="domain_keys_admin",
        password="test1234",
        role=User.Role.FACILITY_ADMIN,
        facility=facility,
    )


@pytest.fixture
def make_doc_type(facility):
    def _make(name, report_key=None, *, doc_facility=None):
        return DocumentType.objects.create(
            facility=doc_facility or facility,
            name=name,
            report_key=report_key,
        )

    return _make


@pytest.fixture
def make_event(facility, user):
    def _make(doc_type, *, day=12, month=2, year=2026, event_facility=None):
        return Event.objects.create(
            facility=event_facility or facility,
            document_type=doc_type,
            occurred_at=timezone.make_aware(datetime(year, month, day, 10, 0)),
            data_json={},
            created_by=user,
        )

    return _make


def _kategorie_zahlen(summary):
    """``by_category`` als Dict — der Rohdatensatz liefert Tupel."""
    return {name: count for name, count in summary["by_category"]}


class TestVokabular:
    """Akzeptanz 1: die vier Schluessel sind waehlbar, ``excluded`` bleibt draussen."""

    @pytest.mark.parametrize(("key", "label", "_ziel"), DOMAENEN_SCHLUESSEL)
    def test_schluessel_steht_mit_seinem_label_im_vokabular(self, key, label, _ziel):
        vokabular = registry.get_report_key_vocabulary()

        assert key in vokabular
        assert str(vokabular[key]) == label

    def test_sentinel_bleibt_ausserhalb_des_vokabulars(self):
        """Der Sentinel wird von der Auswahl gesondert angeboten, nie von einem Backend."""
        assert EXCLUDED_KEY not in registry.get_report_key_vocabulary()


class TestSachbericht:
    """Akzeptanz 2: gezaehlt wird in der Zielzeile — ohne ``unknown_report_keys``."""

    @pytest.mark.parametrize(("key", "_label", "ziel"), DOMAENEN_SCHLUESSEL)
    def test_ereignisse_zaehlen_in_ihrer_zeile(self, backend, facility, make_doc_type, make_event, key, _label, ziel):
        typ = make_doc_type(f"Typ {key}", key)
        for _ in range(3):
            make_event(typ)

        result = backend.build_dataset(facility, DATE_FROM, DATE_TO)

        assert _kategorie_zahlen(result.summary)[ziel] == 3
        assert result.summary["total"] == 3

    @pytest.mark.parametrize(("key", "_label", "_ziel"), DOMAENEN_SCHLUESSEL)
    def test_kein_vollstaendigkeitshinweis_auf_unbekannten_schluessel(
        self, backend, facility, make_doc_type, make_event, key, _label, _ziel
    ):
        """Genau der Hinweis, den jedes Domaenen-Paket ohne dieses Ticket erzeugte."""
        make_event(make_doc_type(f"Typ {key}", key))

        codes = [issue.code for issue in backend.build_dataset(facility, DATE_FROM, DATE_TO).completeness]

        assert "unknown_report_keys" not in codes

    @pytest.mark.parametrize(("key", "_label", "ziel"), DOMAENEN_SCHLUESSEL)
    def test_monatsverlauf_zaehlt_dieselbe_zeile(
        self, monatsverlauf, facility, make_doc_type, make_event, key, _label, ziel
    ):
        """``monthly_trend`` importiert dieselbe Deklaration — es darf kein
        zweiter Nachzug noetig sein."""
        typ = make_doc_type(f"Typ {key}", key)
        for _ in range(2):
            make_event(typ)

        result = monatsverlauf.build_dataset(facility, DATE_FROM, DATE_TO)

        assert {"monat": "2026-02", "kategorie": ziel, "count": 2} in result.records
        assert "unknown_report_keys" not in [issue.code for issue in result.completeness]

    def test_alle_vier_zusammen_ergeben_sieben_kategoriezeilen(self, backend, facility, make_doc_type, make_event):
        """Der Vollbestand: sieben Alt-Schluessel plus die vier neuen fallen auf
        sieben Zeilen zusammen — ``supply`` teilt sich „Versorgung" mit
        ``medical``/``needle_exchange``.
        """
        for key in ("contact", "crisis", "medical", "accompaniment", "incident", "protection", "group_activity"):
            make_event(make_doc_type(f"Typ {key}", key))
        make_event(make_doc_type("Typ supply", "supply"))

        zahlen = _kategorie_zahlen(backend.build_dataset(facility, DATE_FROM, DATE_TO).summary)

        assert zahlen == {
            "Kontakte": 1,
            "Beratung": 1,
            "Versorgung": 2,
            "Vermittlung": 1,
            "Vorfälle": 1,
            "Schutz": 1,
            "Gruppenangebote": 1,
        }


class TestExportOrdnung:
    """R-L1b-2: die publizierte Position der neuen Zeilen ist wertunabhaengig.

    Kein Test auf „ans Ende angehaengt" — ``_canonicalize_suppressed_order``
    sortiert sichtbare Zeilen alphabetisch nach Kategoriename. Geprueft wird
    dieselbe Zusage wie fuer den Bestand: dieselbe Ordnung, egal welche Zahlen
    darunter liegen.
    """

    def _artefakt(self, backend, facility, make_doc_type, make_event, zahlen):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        for key, anzahl in zahlen.items():
            typ = make_doc_type(f"Typ {key}", key)
            for _ in range(anzahl):
                make_event(typ)
        roh = backend.build_dataset(facility, DATE_FROM, DATE_TO)
        return [row["name"] for row in backend.prepare_export(facility, roh).summary["by_category"]]

    def test_neue_kategorien_stehen_in_kanonischer_namensordnung(self, backend, facility, make_doc_type, make_event):
        ordnung = self._artefakt(
            backend,
            facility,
            make_doc_type,
            make_event,
            {"contact": 5, "incident": 3, "protection": 2, "group_activity": 4},
        )

        assert ordnung == ["Gruppenangebote", "Kontakte", "Schutz", "Vorfälle"]

    def test_dieselbe_ordnung_bei_vertauschten_zahlen(self, backend, facility, make_doc_type, make_event):
        """Gegenprobe: andere Werte, dieselbe Reihenfolge (K5)."""
        ordnung = self._artefakt(
            backend,
            facility,
            make_doc_type,
            make_event,
            {"contact": 2, "incident": 9, "protection": 7, "group_activity": 1},
        )

        assert ordnung == ["Gruppenangebote", "Kontakte", "Schutz", "Vorfälle"]


class TestSnapshotHybrid:
    """Akzeptanz 4: eine neue Zeile fehlt in Alt-Snapshots — und zaehlt neutral.

    ``_merge_sample_report_1_stats`` baut die Kategorien je Segment frisch aus
    ``by_category`` auf; eine fehlende Kategorie traegt schlicht nichts bei.
    Der Hybridpfad ist damit ohne Migration alt-payload-fest — genau das
    behauptet das Ticket, und genau das wird hier belegt.
    """

    def _alt_snapshot(self, facility, jahr, monat, *, total, zeilen):
        return StatisticsSnapshot.objects.create(
            facility=facility,
            year=jahr,
            month=monat,
            data={},
            backend_data={
                "sample-report-1": {
                    "total": total,
                    # JSON-Roundtrip: Listen statt Tupel, wie im Bestand.
                    "by_category": [[name, count] for name, count in zeilen],
                    "by_age_cluster": [],
                    "unique_clients": total,
                }
            },
        )

    def test_alt_segment_ohne_neue_zeilen_addiert_nur_den_live_wert(self, facility, make_doc_type, make_event):
        # Januar: Alt-Aggregat, das die neuen Zeilen noch nicht kennt.
        self._alt_snapshot(facility, 2026, 1, total=4, zeilen=[("Kontakte", 4)])
        # Februar: live, mit den drei neuen Zeilen.
        for key, anzahl in (("incident", 3), ("protection", 2), ("group_activity", 5)):
            typ = make_doc_type(f"Typ {key}", key)
            for _ in range(anzahl):
                make_event(typ)

        merged = get_sample_report_1_statistics_hybrid(facility, date(2026, 1, 1), date(2026, 2, 28))

        zahlen = dict(merged["by_category"])
        assert zahlen["Vorfälle"] == 3
        assert zahlen["Schutz"] == 2
        assert zahlen["Gruppenangebote"] == 5
        # Das Alt-Segment bleibt unangetastet und die Summe stimmt.
        assert zahlen["Kontakte"] == 4
        assert merged["total"] == 14

    def test_alt_segment_bleibt_neutral_wenn_auch_live_nichts_liefert(self, facility):
        """Gegenprobe: ohne Live-Ereignisse gibt es die neuen Zeilen gar nicht —
        der Merge erfindet keine Null-Zeile."""
        self._alt_snapshot(facility, 2026, 1, total=4, zeilen=[("Kontakte", 4)])

        merged = get_sample_report_1_statistics_hybrid(facility, date(2026, 1, 1), date(2026, 2, 28))

        assert dict(merged["by_category"]) == {"Kontakte": 4}
        assert merged["total"] == 4


class TestKonfigurationsUI:
    """Akzeptanz 5: das Select speist sich aus der Registry, „Erscheint in …" folgt."""

    def test_select_bietet_die_vier_neuen_schluessel(self, facility):
        form = DocumentTypeForm(facility=facility)
        optionen = [wert for wert, _label in form.fields["report_key"].choices]

        for key, _label, _ziel in DOMAENEN_SCHLUESSEL:
            assert key in optionen

    @pytest.mark.parametrize(("key", "label", "_ziel"), DOMAENEN_SCHLUESSEL)
    def test_erscheint_in_nennt_den_beispiel_sachbericht(self, facility, key, label, _ziel):
        """Bestandsformat ist ``"Backend-Name (Label)"`` — die Zielzeile steht
        dort bewusst nicht (``registry.describe_type_bindings``).
        """
        typ = DocumentType.objects.create(
            facility=facility,
            name=f"Typ {key}",
            entity_type=DocumentType.EntityType.EVENT,
            report_key=key,
        )

        assert f"Beispiel-Sachbericht ({label})" in registry.describe_type_bindings(typ, facility)


class TestPdfVorlage:
    """Akzeptanz „PDF rendert sauber": das Template ist generisch, die
    zusaetzlichen Zeilen erscheinen ohne Template-Aenderung.

    Ohne WeasyPrint — gerendert wird die HTML-Vorlage, die der PDF-Pfad an die
    Engine reicht (dasselbe Muster wie in ``test_reporting_disclosure_regression``).
    """

    def test_alle_sieben_kategoriezeilen_erscheinen_in_der_tabelle(self, backend, facility, make_doc_type, make_event):
        """Sieben Zeilen statt der bisherigen vier — mehr sind es nicht, weil
        ``supply`` sich „Versorgung" mit ``medical``/``needle_exchange`` teilt
        (drei neue Kategorien + eine Erweiterung, Entscheid R-L1b-1).
        """
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        for key in (
            "contact",
            "crisis",
            "medical",
            "needle_exchange",
            "accompaniment",
            "referral",
            "incident",
            "protection",
            "group_activity",
            "supply",
        ):
            make_event(make_doc_type(f"Typ {key}", key))

        stats = backend.prepare_export(facility, backend.build_dataset(facility, DATE_FROM, DATE_TO)).summary
        seite = render_to_string("core/export/sample_report_1_pdf.html", {"stats": stats})
        abschnitt = seite.split("Leistungen nach Kategorie")[-1].split("Altersverteilung")[0]

        for name in ("Kontakte", "Beratung", "Versorgung", "Vermittlung", "Vorfälle", "Schutz", "Gruppenangebote"):
            assert f"<td>{name}</td>" in abschnitt
        # Eine Wertzelle je Datenzeile — die Kopfzeile traegt keine.
        assert abschnitt.count('class="num"') == 7
        assert not any(row["suppressed"] for row in stats["by_category"])
