"""Tests fuer die Reporting-Views: Berichts-Panel + generischer Export (M5 P5, Refs #1061).

Deckt ``DatasetReportPanelView`` (HTMX-Partial-Vorschau, export-paritaetisch via
``prepare_export``) und ``DatasetExportView`` (CSV/JSON/XML-Download + Audit-Log) ab,
sowie die Berichte-Sektion in ``StatisticsView``. AuthZ-Zellen und der 5xx-Guard
laufen ueber ``test_authz_matrix.py`` (Eintraege in ``_authz_expectations.py``).
"""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from core.models import AuditLog
from core.reporting import registry
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends (``sample-report-1``) bleiben verfuegbar.

    Muster aus ``test_reporting_sample_report_1.py``: erst die echte Discovery ausloesen
    (``@register_backend`` ist ein Import-Seiteneffekt, laeuft je Modul nur beim
    *ersten* Import), dann mit einer Kopie isolieren, damit ad-hoc registrierte
    Test-Backends (Zeilen-Modus, Pflichtfall 8) nicht in andere Tests leaken.
    """
    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


def _mapped(doc_type):
    """Stellt die Berichtszuordnung von ``doc_type_contact`` sicher.

    Bis E4 (Refs #1850) setzte der Helfer den gleichnamigen ``system_type`` —
    den gibt es nicht mehr, und fuer die Zuordnung war er seit E3 (Refs #1849)
    ohnehin folgenlos: die Fixture bringt den ``report_key`` mit. Der Helfer
    bleibt trotzdem stehen, weil die Tests an ihm ablesbar machen, dass sie den
    ZUGEORDNETEN Fall meinen (Gegenstueck zu
    ``test_completeness_warnung_bei_luecken``).
    """
    doc_type.report_key = "contact"
    doc_type.save()
    return doc_type


class TestDatasetReportPanelView:
    """Pflichtfaelle 1-3: 200 + Inhalt, Completeness-Warnung, 404/400."""

    def test_ok_zeigt_backend_name_und_kategorien_tabelle(self, client, lead_user, doc_type_contact, sample_event):
        """Pflichtfall 1: aggregate-Modus zeigt die Kategorien-Tabelle (SampleReport1Backend)."""
        _mapped(doc_type_contact)
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        assert response.status_code == 200
        content = response.content.decode()
        assert "Beispiel-Sachbericht" in content
        assert "Kontakte" in content
        # prepare_export lief: die Fussnote zur K-Anon-Schwelle erscheint.
        assert "Kleinstfallzahl-Schwelle" in content
        assert "k-Anonymität" in content

    def test_completeness_warnung_bei_luecken(self, client, lead_user, doc_type_contact, sample_event):
        """Pflichtfall 2: DocType ohne system_type + Event im Zeitraum -> Warnung.

        Harte Assertion (Review I3): der Warnungstext "ohne Berichtszuordnung"
        steckt sowohl in der bezifferten als auch in der k-anon-unterdrückten
        Formulierung (``_EXPORT_MESSAGES``/``_SUPPRESSED_MESSAGES`` in
        ``sample_report_1.py``) — die Zusatzprüfung auf das Amber-Badge (CSS-Klasse
        der ``severity == "warning"``-Darstellung) macht die Assertion
        zusätzlich formunabhängig robust, statt sich auf einen "oder"-Fallback
        zu verlassen, der wegen der unbedingten K-Anon-Fußnote immer wahr wäre.
        """
        # doc_type_contact hat bewusst KEIN system_type (Fixture-Default) -> unmapped.
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        assert response.status_code == 200
        content = response.content.decode()
        assert "ohne Berichtszuordnung" in content
        assert "bg-amber-100 text-amber-800" in content

    def test_completeness_block_ist_datenunabhaengig(self, client, lead_user, doc_type_contact, sample_event):
        """K6 (Refs #1769): auch ohne jede Luecke stehen beide Meldungen im Panel.

        Frueher zeigte das Panel hier den gruenen Hinweis „Keine
        Vollstaendigkeitsprobleme erkannt" — und genau diese Fallunterscheidung
        war der Kanal: ihre bloße Gestalt sagte, ob es eine Luecke gibt. Der
        gruene Zweig des Templates bleibt fuer Backends ohne
        Vollstaendigkeitspruefung erhalten (z. B. ``example``).
        """
        _mapped(doc_type_contact)
        # Grosse Fallzahl, damit nichts unterdrueckt wird und keine Luecken entstehen.
        from core.models import Client, Event

        for index in range(9):
            client_obj = Client.objects.create(
                facility=doc_type_contact.facility,
                pseudonym=f"Panel-{index}",
                contact_stage=Client.ContactStage.QUALIFIED,
                created_by=sample_event.created_by,
            )
            Event.objects.create(
                facility=doc_type_contact.facility,
                client=client_obj,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={},
                created_by=sample_event.created_by,
            )
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        assert response.status_code == 200
        content = response.content.decode()
        assert "Keine Vollständigkeitsprobleme erkannt" not in content
        assert "ohne Berichtszuordnung im Zeitraum unterdrückt" in content
        assert "ohne Personenbezug im Zeitraum unterdrückt" in content

    def test_example_backend_ohne_pruefung_zeigt_gruenen_hinweis(self, client, lead_user, doc_type_contact):
        """Gegenprobe: das Referenz-Backend hat keine Vollstaendigkeits-Checks —
        dort steht weiterhin der gruene Hinweis.
        """
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "example"})

        assert response.status_code == 200
        assert "Keine Vollständigkeitsprobleme erkannt" in response.content.decode()

    def test_panel_weist_die_live_datenquelle_aus(self, client, lead_user, doc_type_contact, sample_event):
        """Refs #1743: die Vorschau liest live (``build_dataset``) und sagt das.

        Ohne den Ausweis stuenden Panel/CSV-Zahlen und die Zahlen des hybrid
        gelesenen Sachbericht-PDFs unkommentiert nebeneinander — dieselbe
        Kennzahl, verschiedene Quelle, keine Erklaerung.
        """
        _mapped(doc_type_contact)
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        assert response.status_code == 200
        content = response.content.decode()
        assert "Live-Abfrage zum Erstellungszeitpunkt" in content
        assert "bei jedem Aufruf neu aus den Ereignisdaten berechnet" in content

    def test_unbekanntes_backend_404(self, client, lead_user):
        """Pflichtfall 3a."""
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "nope"})

        assert response.status_code == 404

    def test_ohne_backend_400(self, client, lead_user):
        """Pflichtfall 3b."""
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"))

        assert response.status_code == 400

    def test_leerer_backend_param_400(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": ""})

        assert response.status_code == 400

    def test_nicht_verfuegbares_backend_404(self, client, lead_user):
        class _GesperrtesBackend(DatasetBackend):
            name = "Gesperrt"

            def get_field_specs(self):
                return []

            def build_dataset(self, facility, date_from, date_to):
                return DatasetResult(mode="rows", records=[], summary={}, completeness=[])

            def is_available(self, facility):
                return False

        register_backend("gesperrt")(_GesperrtesBackend)
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "gesperrt"})

        assert response.status_code == 404

    def test_rows_modus_zeigt_erste_10_und_gesamtzahl(self, client, lead_user):
        """Pflichtfall 8: ad-hoc Zeilen-Backend mit >10 Records -> 10 + Gesamtzahl."""

        class _VieleZeilenBackend(DatasetBackend):
            name = "Viele Zeilen"

            def get_field_specs(self):
                return [FieldSpec(key="nr", label="Nr")]

            def build_dataset(self, facility, date_from, date_to):
                records = [{"nr": index} for index in range(15)]
                return DatasetResult(mode="rows", records=records, summary={}, completeness=[])

        register_backend("viele_zeilen")(_VieleZeilenBackend)
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "viele_zeilen"})

        assert response.status_code == 200
        content = response.content.decode()
        assert "Viele Zeilen" in content
        # Nur die ersten 10 Zeilen-Werte erscheinen als Zellinhalt (0..9), 10..14 nicht.
        assert ">9<" in content
        assert ">10<" not in content
        assert ">14<" not in content
        # Der Vorschau-Hinweis nennt beide Zahlen. Bewusst der ganze Satz statt
        # nur "15": die nackte Zahl steht ohnehin in jedem Panel (Tailwind-Klasse
        # ``text-[15px]`` in der Ueberschrift) und pinnt ``preview_total`` nicht.
        assert "Zeigt 10 von 15 Zeilen" in content

    def test_none_zelle_rendert_gedankenstrich(self, client, lead_user, doc_type_contact, sample_event):
        """``None``-Zellen (K-Anon-Suppression) rendern als ``—``, nie als ``None``."""
        _mapped(doc_type_contact)
        client.force_login(lead_user)

        # Ein einzelnes Event liegt unter der Default-Schwelle (5) -> suppressiert.
        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        content = response.content.decode()
        assert "—" in content
        assert "None" not in content


_HIDDEN_INPUT_RE = re.compile(r'<input type="hidden" name="([^"]+)" value="([^"]*)">')


def _extract_report_section(html: str) -> str:
    start = html.index('id="report-section"')
    end = html.index("</section>", start)
    return html[start:end]


# Refs #1813 (Konzept #1801 §7.1): Die Berichte-Sektion haengt nicht mehr unten
# am Dashboard, sie ist ein eigener Reiter der Statistik-Reiterzeile. Ihr
# natuerliches Ziel ``core:statistics_report_panel`` scheidet als Reiter aus
# (HTMX-Partial ohne base.html, ohne ?backend= ein 400), deshalb dieser Modus
# auf derselben Route — Hausmuster Zeitstrom ``?view=`` (Refs #1124). Die Tests
# unten rufen die Seite entsprechend mit dem Modus auf; an ihren Aussagen
# aendert das nichts.
BERICHTE_VIEW = {"ansicht": "datensaetze"}


class TestBerichteFormZeitraumDurchreichung:
    """Review I2: Die Berichte-Form muss ``zeitraum=frei`` mitschicken.

    ``parse_statistics_period`` (services/dashboard/statistics.py) liest
    ``date_from``/``date_to`` NUR aus, wenn ``period == "frei"`` — sonst
    verwirft sie beide Werte und rechnet ihr eigenes Default-Fenster
    (``today - 30 Tage`` .. ``today``). Ohne ein hidden ``zeitraum=frei``-Feld
    in der Berichte-Form würde ``hx-include="closest form"`` beim Absenden nur
    ``date_from``/``date_to``/``backend`` einsammeln — die aufgelösten Werte
    kämen zwar korrekt im Formular an, würden vom Panel aber stillschweigend
    ignoriert.

    Der Test parst das ``#report-section``-Formular dynamisch (statt die
    Felder hart zu kodieren) und speist exakt das Ergebnis in die
    Panel-Request ein — genau das, was ``hx-include`` real einsammeln würde.
    Vor dem Fix schlägt bereits die erste Assertion fehl (kein ``zeitraum``-Feld
    im Formular); nach dem Fix propagieren die im Dashboard gewählten Daten
    unverändert bis in den Panel-Header.

    Seit K4a (Refs #1770) tragen die Export-Schaltflächen den Zeitraum nicht
    mehr selbst: sie liegen im selben GET-Formular und erben dessen
    Hidden-Felder beim Submit (siehe ``TestPanelExportOhneRoundtrip``).
    """

    def test_extrahierte_form_felder_liefern_den_gewaehlten_zeitraum_im_panel(self, client, lead_user):
        client.force_login(lead_user)

        dashboard = client.get(
            reverse("core:statistics"),
            {**BERICHTE_VIEW, "zeitraum": "frei", "date_from": "2020-01-01", "date_to": "2020-01-31"},
        )
        section_html = _extract_report_section(dashboard.content.decode())
        hidden_fields = dict(_HIDDEN_INPUT_RE.findall(section_html))

        # Sanity: die Sektion traegt ueberhaupt die aufgeloesten Daten.
        assert hidden_fields.get("date_from") == "2020-01-01"
        assert hidden_fields.get("date_to") == "2020-01-31"
        # Der eigentliche Fix: ohne dieses Feld verwirft parse_statistics_period
        # die beiden Werte oben komplett (Vorgabe zeitraum="letzte30").
        assert hidden_fields.get("zeitraum") == "frei"

        # Simuliert exakt, was ``hx-include="closest form"`` beim Absenden
        # zusammensammelt (alle Formularfelder + der gewaehlte Select-Wert).
        query = {**hidden_fields, "backend": "sample-report-1"}
        panel = client.get(reverse("core:statistics_report_panel"), query)
        panel_html = panel.content.decode()

        # Der Panel-Header weist den aufgeloesten Zeitraum aus (lokalisiert).
        assert date_format(date(2020, 1, 1)) in panel_html
        assert date_format(date(2020, 1, 31)) in panel_html
        # Die Export-Schaltflaechen erben ihn ueber dasselbe Formular: CSV/JSON/XML
        # plus — seit R2 (Refs #1804) — das hierher gewanderte Sachbericht-PDF.
        assert panel_html.count("formaction=") == 4


class TestPanelExportOhneRoundtrip:
    """K4a (Refs #1770): Empfaenger/Zweck duerfen nicht ueber einen HTMX-
    Roundtrip in die Export-Ziele gebacken werden.

    Vorher buk ``report_panel.html`` ``{{ release_query }}`` zur **Renderzeit**
    ins ``href``. Ein Mousedown auf den Link blurrt das Freitextfeld, der
    ``change``-Trigger startet den Panel-Neubau — der Klick folgt aber synchron
    dem ALTEN ``href``, also ohne ``&recipient=…``. Das EXPORT-Audit ist
    append-only: die Angabe ist dann dauerhaft verloren, die Freigabe-Uebersicht
    zeigt „—/—", und nichts weist auf den Verlust hin.

    Zweite Folge derselben Ursache: die beiden Freitextfelder trugen
    ``hx-get``+``hx-trigger="change"``; bei noch leerem Backend-Select antwortet
    ``DatasetReportPanelView`` mit ``400 backend erforderlich``, was
    ``htmx-errors.js`` als roten Fehler-Toast rendert.

    Loesung wie im Dashboard-Export (``dashboard_content.html``): ein
    GET-Formular, das das Panel umschliesst, mit ``formaction`` je
    Schaltflaeche. Die Werte werden erst beim Submit serialisiert.
    """

    def _panel(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})
        assert response.status_code == 200
        return response.content.decode()

    def test_panel_exportiert_ueber_submit_buttons_statt_links(self, client, lead_user):
        panel_html = self._panel(client, lead_user)
        export_url = reverse("core:statistics_report_export")

        assert export_url not in panel_html.split("<a ", 1)[-1] or "<a " not in panel_html, (
            "Die Export-Ziele duerfen keine <a href>-Links mehr sein — ihr href traegt den Race."
        )
        assert panel_html.count(f'formaction="{export_url}"') == 3
        for fmt in ("csv", "json", "xml"):
            assert f'name="format" value="{fmt}"' in panel_html

    def test_panel_backt_keinen_release_query_mehr_ein(self, client, lead_user):
        """``release_query`` war der Traeger des Race — er darf ganz verschwinden."""
        client.force_login(lead_user)
        response = client.get(
            reverse("core:statistics_report_panel"),
            {"backend": "sample-report-1", "recipient": "Stadt Musterstadt", "purpose": "Verwendungsnachweis"},
        )

        assert "release_query" not in response.context
        assert "Musterstadt" not in response.content.decode()

    def test_berichte_formular_umschliesst_das_panel(self, client, lead_user):
        """Sonst serialisiert der Submit die Freitextfelder gar nicht mit."""
        client.force_login(lead_user)
        section = _extract_report_section(client.get(reverse("core:statistics"), BERICHTE_VIEW).content.decode())

        assert section.index("<form") < section.index('id="report-panel"') < section.index("</form>")
        assert 'method="get"' in section[section.index("<form") : section.index(">", section.index("<form"))]

    def test_freitextfelder_loesen_keinen_panel_request_mehr_aus(self, client, lead_user):
        """Ohne gewaehltes Backend antwortet das Panel 400 → roter Fehler-Toast.

        Refs #1804: Empfaenger/Zweck erscheinen erst MIT einer Berichtsauswahl
        (R2 — die Felder gehoeren zu dem Export, den sie begleiten); der Aufruf
        traegt deshalb ``?bericht=``.
        """
        client.force_login(lead_user)
        section = _extract_report_section(
            client.get(reverse("core:statistics"), {**BERICHTE_VIEW, "bericht": "sample-report-1"}).content.decode()
        )

        for feld in ("recipient", "purpose"):
            start = section.index(f'name="{feld}"')
            tag = section[section.rindex("<input", 0, start) : section.index(">", start)]
            assert "hx-get" not in tag, f"{feld} triggert weiterhin einen Panel-Roundtrip: {tag}"
            assert "hx-trigger" not in tag

    def test_empfaenger_erreicht_ueber_den_panel_pfad_das_audit_detail(
        self, client, lead_user, facility, doc_type_contact, sample_event
    ):
        """Ende-zu-Ende: exakt das, was der Browser beim Submit serialisiert."""
        _mapped(doc_type_contact)
        client.force_login(lead_user)
        section = _extract_report_section(
            client.get(
                reverse("core:statistics"),
                {**BERICHTE_VIEW, "zeitraum": "frei", "date_from": "2026-01-01", "date_to": "2026-01-31"},
            ).content.decode()
        )
        felder = dict(_HIDDEN_INPUT_RE.findall(section))

        client.get(
            reverse("core:statistics_report_export"),
            {
                **felder,
                "backend": "sample-report-1",
                "recipient": "Stadt Musterstadt",
                "purpose": "Verwendungsnachweis 2026",
                # Submitter-name/value der angeklickten Schaltflaeche.
                "format": "csv",
            },
        )

        entry = AuditLog.objects.filter(facility=facility, action=AuditLog.Action.EXPORT).latest("timestamp")
        assert entry.detail["recipient"] == "Stadt Musterstadt"
        assert entry.detail["purpose"] == "Verwendungsnachweis 2026"
        assert entry.detail["date_from"] == "2026-01-01"


class TestDatasetExportView:
    """Pflichtfaelle 4-6: Downloads, Fehlerfaelle, Audit-Log."""

    @pytest.mark.parametrize(
        ("fmt", "content_type"),
        [
            ("csv", "text/csv; charset=utf-8"),
            ("json", "application/json"),
            ("xml", "application/xml; charset=utf-8"),
        ],
    )
    def test_export_liefert_download(self, client, admin_user, doc_type_contact, sample_event, fmt, content_type):
        """Pflichtfall 4."""
        _mapped(doc_type_contact)
        client.force_login(admin_user)
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "sample-report-1",
                "format": fmt,
                "date_from": str(today - timedelta(days=30)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 200
        assert response["Content-Type"] == content_type
        assert "sample-report-1_" in response["Content-Disposition"]

    def test_fehlende_daten_400(self, client, admin_user):
        """Pflichtfall 5a."""
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), {"backend": "sample-report-1", "format": "csv"})

        assert response.status_code == 400

    def test_unbekanntes_format_400(self, client, admin_user):
        """Pflichtfall 5b."""
        client.force_login(admin_user)
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "sample-report-1",
                "format": "pdf",
                "date_from": str(today - timedelta(days=30)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 400

    def test_unbekanntes_backend_404(self, client, admin_user):
        """Pflichtfall 5c."""
        client.force_login(admin_user)
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "nope",
                "format": "csv",
                "date_from": str(today - timedelta(days=30)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 404

    def test_nicht_verfuegbares_backend_404(self, client, admin_user):
        class _GesperrtesBackend(DatasetBackend):
            name = "Gesperrt"

            def get_field_specs(self):
                return []

            def build_dataset(self, facility, date_from, date_to):
                return DatasetResult(mode="rows", records=[], summary={}, completeness=[])

            def is_available(self, facility):
                return False

        register_backend("gesperrt_export")(_GesperrtesBackend)
        client.force_login(admin_user)
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "gesperrt_export",
                "format": "csv",
                "date_from": str(today - timedelta(days=30)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 404

    def test_audit_log_nach_export(self, client, admin_user, facility, doc_type_contact, sample_event):
        """Pflichtfall 6."""
        _mapped(doc_type_contact)
        client.force_login(admin_user)
        today = timezone.localdate()
        date_from = today - timedelta(days=30)

        client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "sample-report-1",
                "format": "json",
                "date_from": str(date_from),
                "date_to": str(today),
            },
        )

        entry = AuditLog.objects.get(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="DatasetExport",
        )
        assert entry.detail["backend"] == "sample-report-1"
        assert entry.detail["format"] == "json"
        assert entry.detail["date_from"] == str(date_from)
        assert entry.detail["date_to"] == str(today)


class TestIsAvailableFalsyGate:
    """Sichtbarkeits- und Zugriffs-Gate teilen dieselbe Falsy-Semantik.

    ``get_available_backends`` filtert per Truthiness (``registry.py``), Panel
    und Export mussten dieselbe Praedikat-Form benutzen: ein Backend, dessen
    ``is_available`` einen falsy Nicht-``False``-Wert liefert (typisch
    ``return facility.settings.report_enabled`` auf einem nullable
    ``BooleanField`` → ``None``), verschwand sonst nur aus dem Dropdown, blieb
    aber ueber ``?backend=<slug>`` mit HTTP 200 abrufbar (fail-open).
    """

    @pytest.fixture
    def _none_backend(self):
        class _NoneVerfuegbarkeitBackend(DatasetBackend):
            name = "None-Verfuegbarkeit"

            def get_field_specs(self):
                return [FieldSpec(key="wert", label="Wert")]

            def build_dataset(self, facility, date_from, date_to):
                return DatasetResult(mode="rows", records=[{"wert": 1}], summary={}, completeness=[])

            def is_available(self, facility):
                # Kein literal False — genau der Rueckgabewert eines nullable
                # BooleanField ohne gesetzten Wert.
                return None

        return register_backend("none_verfuegbarkeit")(_NoneVerfuegbarkeitBackend)

    def test_backend_fehlt_im_dropdown(self, client, admin_user, _none_backend):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200
        assert "None-Verfuegbarkeit" not in response.content.decode()

    def test_panel_antwortet_404(self, client, lead_user, _none_backend):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "none_verfuegbarkeit"})

        assert response.status_code == 404

    def test_export_antwortet_404(self, client, admin_user, _none_backend):
        client.force_login(admin_user)
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "none_verfuegbarkeit",
                "format": "csv",
                "date_from": str(today - timedelta(days=1)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 404


class TestPrepareExportRuftGenauEinmalAuf:
    """Review M4: Guard gegen doppelte ``prepare_export``-Transformation.

    Sicherheitskritisch (K-Anon, Refs #1278/T1): ``prepare_export`` ist NICHT
    idempotent — ein zweiter Aufruf auf demselben Ergebnis würde z. B. eine
    bereits suppressierte Zelle nicht "zurückholen", aber je nach Backend
    beliebige andere kumulative Effekte haben können. Panel-Request und
    Export-Request dürfen den Hook deshalb je genau einmal auslösen.
    """

    def test_panel_ruft_prepare_export_genau_einmal(self, client, lead_user):
        class _ZaehlBackend(DatasetBackend):
            name = "Zaehl-Backend"

            def __init__(self):
                self.calls = 0

            def get_field_specs(self):
                return [FieldSpec(key="wert", label="Wert")]

            def build_dataset(self, facility, date_from, date_to):
                return DatasetResult(mode="rows", records=[{"wert": 1}], summary={}, completeness=[])

            def prepare_export(self, facility, result):
                self.calls += 1
                return result

        register_backend("zaehler_panel")(_ZaehlBackend)
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "zaehler_panel"})

        assert response.status_code == 200
        assert registry.get_backend("zaehler_panel").calls == 1

    def test_export_ruft_prepare_export_genau_einmal(self, client, admin_user):
        class _ZaehlBackend(DatasetBackend):
            name = "Zaehl-Backend"

            def __init__(self):
                self.calls = 0

            def get_field_specs(self):
                return [FieldSpec(key="wert", label="Wert")]

            def build_dataset(self, facility, date_from, date_to):
                return DatasetResult(mode="rows", records=[{"wert": 1}], summary={}, completeness=[])

            def prepare_export(self, facility, result):
                self.calls += 1
                return result

        register_backend("zaehler_export")(_ZaehlBackend)
        client.force_login(admin_user)
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "zaehler_export",
                "format": "json",
                "date_from": str(today - timedelta(days=1)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 200
        assert registry.get_backend("zaehler_export").calls == 1


class TestStatisticsViewBerichteSektion:
    """Pflichtfall 7: Berichte-Sektion + registrierte Backends im Dropdown."""

    def test_zeigt_berichte_sektion_mit_beispiel_sachbericht(self, client, admin_user):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"), BERICHTE_VIEW)

        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="report-section"' in content
        assert "Beispiel-Sachbericht" in content

    def test_ohne_registrierte_backends_keine_sektion(self, client, admin_user, monkeypatch):
        monkeypatch.setattr(registry, "_REGISTRY", {})
        monkeypatch.setattr(registry, "_discovered", True)
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"), BERICHTE_VIEW)

        assert response.status_code == 200
        assert 'id="report-section"' not in response.content.decode()

    def test_dashboard_reiter_zeigt_die_sektion_nicht_mehr(self, client, admin_user):
        """Refs #1813: die Verzweigung ist der eigentliche Umbau — ohne diese
        Gegenprobe koennte die Sektion in beiden Reitern stehen und der neue
        Reiter waere eine Verdopplung statt eines Umzugs."""
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="report-section"' not in content
        assert 'id="dashboard-content"' in content
