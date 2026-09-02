"""Fehlerisolation der Reporting-Pfade (Refs #2070).

Ein defektes Berichts-Backend darf **nur sich selbst** ausser Betrieb setzen —
nicht die Statistik-Uebersicht, nicht die uebrigen Berichte, nicht den
monatlichen Snapshot-Lauf aller Einrichtungen. Diese Datei buendelt alle
„kaputtes Backend"-Szenarien an EINER Stelle (statt sie ueber fuenf
Bestandsdateien zu verteilen), weil sie eine gemeinsame Sicherheitszusage
tragen:

**Die Kette ``build_dataset`` -> ``prepare_export`` ->
``enforce_suppression_contract`` ist atomar.** Entweder laeuft sie komplett und
liefert suppressierte Daten, oder es gibt eine Fehlerkarte bzw. eine
Fehlerantwort **ohne jede Datenzeile**. ``SuppressionContractError`` wird
deshalb ausdruecklich **nicht** sonderbehandelt: sie faellt in denselben
``except Exception`` wie jeder andere Backend-Fehler (ADR-038 § 5,
k-Anon-Paritaet aus #2069).
Ein Zwischenzustand — rohes ``build_dataset``-Ergebnis rendern, weil
``prepare_export`` scheiterte — waere genau die Regression, gegen die die
``_ROHWERT``-Assertions hier stehen.

``build_report_panel_context`` selbst **wirft weiterhin** (gepinnt in
``test_reporting_suppression_contract.py::TestPanelPfad``); die Isolation sitzt
in den **Views**.
"""

from datetime import datetime, timedelta

import pytest
from django.db import OperationalError, connection
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, Event, StatisticsSnapshot
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec, stamp_threshold
from core.reporting.db_guard import reporting_statement_timeout
from core.reporting.registry import register_backend
from core.services.dashboard import create_or_update_snapshot, ensure_snapshots_for_months
from tests.conftest import isolate_reporting_registry

pytestmark = pytest.mark.django_db

# Der Wert, den ein defektes Backend aus ``build_dataset`` mitbringt. Er darf in
# KEINER Fehlerantwort auftauchen — weder im Panel-HTML noch im Export-Body.
_ROHWERT = 4711


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren (Muster ``tests.conftest.isolate_reporting_registry``)."""
    isolate_reporting_registry(monkeypatch)


class _BasisBackend(DatasetBackend):
    """Gemeinsame Basis der Testdoubles — liefert einen erkennbaren Rohwert."""

    name = "Testdouble"

    def get_field_specs(self):
        return [FieldSpec(key="wert", label="Wert")]

    def build_dataset(self, facility, date_from, date_to):
        return DatasetResult(mode="rows", records=[{"wert": _ROHWERT}], summary={}, completeness=[])

    def prepare_export(self, facility, result):
        return DatasetResult(
            mode=result.mode,
            records=result.records,
            summary=stamp_threshold(result.summary, 5),
            completeness=result.completeness,
        )


@pytest.fixture
def kaputtes_build_backend():
    """Backend, dessen ``build_dataset`` wirft — der haeufigste Realfall (DB-Fehler)."""

    class _KaputtesBuildBackend(_BasisBackend):
        name = "Kaputtes Build-Backend"

        def build_dataset(self, facility, date_from, date_to):
            raise RuntimeError("build_dataset kaputt")

    return register_backend("kaputt_build")(_KaputtesBuildBackend)


@pytest.fixture
def kaputtes_prepare_backend():
    """Backend, dessen ``prepare_export`` wirft — ``build_dataset`` lief bereits.

    Der gefaehrlichste Fall: die Rohdaten liegen zum Fehlerzeitpunkt bereits
    vor. Sie duerfen trotzdem nirgends erscheinen.
    """

    class _KaputtesPrepareBackend(_BasisBackend):
        name = "Kaputtes Prepare-Backend"

        def prepare_export(self, facility, result):
            raise RuntimeError("prepare_export kaputt")

    return register_backend("kaputt_prepare")(_KaputtesPrepareBackend)


@pytest.fixture
def backend_ohne_schwelle():
    """Backend ohne ausgewiesene k-Anon-Schwelle -> ``SuppressionContractError``."""

    class _OhneSchwelleBackend(_BasisBackend):
        name = "Backend ohne Schwelle"

        def prepare_export(self, facility, result):
            return result  # kein stamp_threshold -> fail-closed

    return register_backend("ohne_schwelle")(_OhneSchwelleBackend)


@pytest.fixture
def kaputte_verfuegbarkeit_backend():
    """Backend, dessen ``is_available`` wirft — laeuft auf JEDER Dashboard-Seite."""

    class _KaputteVerfuegbarkeitBackend(_BasisBackend):
        name = "Kaputte Verfuegbarkeit"

        def is_available(self, facility):
            raise RuntimeError("is_available kaputt")

    return register_backend("kaputt_verfuegbar")(_KaputteVerfuegbarkeitBackend)


def _export_params(slug, *, fmt="csv"):
    today = timezone.localdate()
    return {
        "backend": slug,
        "format": fmt,
        "date_from": str(today - timedelta(days=1)),
        "date_to": str(today),
    }


class TestPanelPartialIsoliert:
    """``DatasetReportPanelView``: Fehlerkarte statt 500 — und ohne Datenzeile."""

    @pytest.mark.parametrize("slug", ["kaputt_build", "kaputt_prepare", "ohne_schwelle"])
    def test_panel_zeigt_fehlerkarte_statt_500(
        self,
        client,
        lead_user,
        slug,
        kaputtes_build_backend,
        kaputtes_prepare_backend,
        backend_ohne_schwelle,
    ):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": slug})

        # 200, weil HTMX nur 2xx-Antworten einswappt — eine 5xx-Antwort liesse
        # das alte Panel stehen und der Fehler waere unsichtbar.
        assert response.status_code == 200
        html = response.content.decode()
        assert 'data-testid="report-panel-error"' in html

    @pytest.mark.parametrize("slug", ["kaputt_prepare", "ohne_schwelle"])
    def test_panel_rendert_keine_rohdaten(
        self, client, lead_user, slug, kaputtes_prepare_backend, backend_ohne_schwelle
    ):
        """Suppressions-Paritaet: ``build_dataset`` lief, ``prepare_export`` nicht."""
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": slug})

        assert str(_ROHWERT) not in response.content.decode()

    def test_fehlerkarte_nennt_den_bericht_aber_keine_exception_details(
        self, client, lead_user, kaputtes_build_backend
    ):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "kaputt_build"})

        html = response.content.decode()
        assert "Kaputtes Build-Backend" in html
        assert "RuntimeError" not in html
        assert "build_dataset kaputt" not in html

    def test_intaktes_backend_bleibt_unveraendert(self, client, lead_user, doc_type_contact, sample_event):
        """Regression: der Erfolgspfad rendert weiter das echte Panel."""
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        assert response.status_code == 200
        assert 'data-testid="report-panel-error"' not in response.content.decode()


class TestStatistikReiterIsoliert:
    """``StatisticsView`` im Datensaetze-Modus: die Seite bleibt bedienbar."""

    def test_reiter_laedt_mit_fehlerkarte(self, client, admin_user, kaputtes_build_backend):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"), {"ansicht": "datensaetze", "bericht": "kaputt_build"})

        assert response.status_code == 200
        html = response.content.decode()
        assert 'data-testid="report-panel-error"' in html
        # Die Karten-Liste (also die uebrigen Berichte) bleibt bedienbar.
        assert 'data-testid="report-card-list"' in html
        assert 'data-testid="report-card-sample-report-1"' in html

    def test_reiter_rendert_keine_rohdaten(self, client, admin_user, kaputtes_prepare_backend):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"), {"ansicht": "datensaetze", "bericht": "kaputt_prepare"})

        assert str(_ROHWERT) not in response.content.decode()


class TestVerfuegbarkeitIsoliert:
    """``is_available`` laeuft auf jeder Dashboard-Seite — ein Wurf darf sie nicht killen."""

    def test_dashboard_laedt_trotz_kaputter_verfuegbarkeit(self, client, admin_user, kaputte_verfuegbarkeit_backend):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200

    def test_kaputtes_backend_gilt_als_nicht_verfuegbar(self, client, admin_user, kaputte_verfuegbarkeit_backend):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"), {"ansicht": "datensaetze"})

        assert response.status_code == 200
        assert "Kaputte Verfuegbarkeit" not in response.content.decode()

    def test_panel_antwortet_404_statt_500(self, client, lead_user, kaputte_verfuegbarkeit_backend):
        """Gleiche Semantik wie die Liste: nicht verfuegbar -> 404, kein 500."""
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "kaputt_verfuegbar"})

        assert response.status_code == 404

    def test_export_antwortet_404_statt_500(self, client, admin_user, kaputte_verfuegbarkeit_backend):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), _export_params("kaputt_verfuegbar"))

        assert response.status_code == 404


class TestExportScheitertSauber:
    """``DatasetExportView``: 502 ohne ein Byte des Datensatzes, ohne Audit-Eintrag."""

    @pytest.mark.parametrize("slug", ["kaputt_build", "kaputt_prepare", "ohne_schwelle"])
    @pytest.mark.parametrize("fmt", ["csv", "json", "xml"])
    def test_export_antwortet_502(
        self,
        client,
        admin_user,
        slug,
        fmt,
        kaputtes_build_backend,
        kaputtes_prepare_backend,
        backend_ohne_schwelle,
    ):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), _export_params(slug, fmt=fmt))

        assert response.status_code == 502

    @pytest.mark.parametrize("slug", ["kaputt_prepare", "ohne_schwelle"])
    def test_export_liefert_keinen_download_und_keine_rohdaten(
        self, client, admin_user, slug, kaputtes_prepare_backend, backend_ohne_schwelle
    ):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), _export_params(slug))

        assert "Content-Disposition" not in response
        assert str(_ROHWERT).encode() not in response.content

    def test_werfende_field_specs_scheitern_vor_den_download_headern(self, client, admin_user):
        """Der CSV-Renderer streamt — der letzte Backend-Aufruf darf trotzdem nicht mitstreamen.

        ``get_field_specs()`` lag bis Refs #2070 **im** Generator. Ein Wurf
        dort fiel erst beim Auslesen von ``streaming_content`` an: Die View
        hatte dann bereits 200 samt ``Content-Disposition`` gebaut und der
        Empfaenger bekam eine abgebrochene Datei statt eines klaren Fehlers
        (dieselbe Sorge wie
        ``test_reporting_suppression_contract::test_streaming_bricht_nicht_erst_beim_konsumieren``).
        """

        class _KaputteFieldSpecsBackend(_BasisBackend):
            name = "Kaputte FieldSpecs"

            def get_field_specs(self):
                raise RuntimeError("get_field_specs kaputt")

        register_backend("kaputt_fieldspecs")(_KaputteFieldSpecsBackend)
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), _export_params("kaputt_fieldspecs"))

        assert response.status_code == 502
        assert "Content-Disposition" not in response

    def test_kein_export_audit_eintrag(self, client, admin_user, facility, kaputtes_build_backend):
        """Ein gescheiterter Export darf im append-only Audit nicht als Erfolg stehen."""
        client.force_login(admin_user)

        client.get(reverse("core:statistics_report_export"), _export_params("kaputt_build"))

        assert not AuditLog.objects.filter(action=AuditLog.Action.EXPORT, target_type="DatasetExport").exists()


class TestSnapshotUeberspringtDefektesBackend:
    """Der Monats-Snapshot speichert den Rest und markiert den Ausfall laut."""

    @pytest.fixture
    def _mit_kaputtem_snapshot_backend(self, monkeypatch):
        class _KaputtesSnapshotBackend(_BasisBackend):
            name = "Kaputtes Snapshot-Backend"
            snapshot_enabled = True

            def build_dataset(self, facility, date_from, date_to):
                raise RuntimeError("snapshot-build kaputt")

        return register_backend("kaputt_snapshot")(_KaputtesSnapshotBackend)

    def _event(self, facility, staff_user, client_identified, occurred_at):
        dt_contact, _ = DocumentType.objects.get_or_create(
            facility=facility, name="Kontaktaufnahme", defaults={"report_key": "contact"}
        )
        return Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt_contact,
            occurred_at=occurred_at,
            data_json={"dauer": 15},
            created_by=staff_user,
        )

    def test_snapshot_wird_trotzdem_geschrieben(
        self, facility, staff_user, client_identified, _mit_kaputtem_snapshot_backend
    ):
        self._event(facility, staff_user, client_identified, timezone.make_aware(datetime(2025, 1, 15, 10, 0)))

        create_or_update_snapshot(facility, 2025, 1)

        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert snap.data["total_contacts"] == 1
        assert snap.backend_data["sample-report-1"]["total"] == 1

    def test_marker_nennt_das_defekte_backend_generisch(
        self, facility, staff_user, client_identified, _mit_kaputtem_snapshot_backend
    ):
        """Marker im bestehenden JSON-Feld — keine Migration, keine Freitext-Exception.

        ``backend_data`` ist im PII-Residue-Sweep pauschal als non-PII
        klassifiziert (``src/tests/_residue_expectations.py``); eine roh
        uebernommene Exception-Message koennte Query-Parameter tragen. Deshalb
        nur der Klassenname.
        """
        self._event(facility, staff_user, client_identified, timezone.make_aware(datetime(2025, 1, 15, 10, 0)))

        create_or_update_snapshot(facility, 2025, 1)

        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert snap.backend_data["_errors"] == {"kaputt_snapshot": "RuntimeError"}
        assert "kaputt_snapshot" not in {slug for slug in snap.backend_data if slug != "_errors"}

    def test_ohne_fehler_kein_marker(self, facility, staff_user, client_identified):
        """Der Marker-Schluessel entsteht nur im Fehlerfall — sonst waere er Rauschen."""
        self._event(facility, staff_user, client_identified, timezone.make_aware(datetime(2025, 1, 15, 10, 0)))

        create_or_update_snapshot(facility, 2025, 1)

        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert "_errors" not in snap.backend_data

    def test_ensure_snapshots_for_months_laeuft_durch(
        self, facility, staff_user, client_identified, _mit_kaputtem_snapshot_backend
    ):
        """Ein defektes Backend riss bisher den kompletten Monats-Batch ab."""
        for month in (1, 2, 3):
            self._event(facility, staff_user, client_identified, timezone.make_aware(datetime(2025, month, 15, 10, 0)))

        ensure_snapshots_for_months(facility, Event.objects.filter(facility=facility))

        months = set(StatisticsSnapshot.objects.filter(facility=facility).values_list("year", "month"))
        assert {(2025, 1), (2025, 2), (2025, 3)} <= months


class TestStatementTimeout:
    """``reporting_statement_timeout``: transaktionslokaler Deckel, sauber zurueckgesetzt."""

    def _show(self):
        with connection.cursor() as cur:
            cur.execute("SHOW statement_timeout")
            return cur.fetchone()[0]

    def test_setzt_den_konfigurierten_wert(self, settings):
        settings.REPORTING_BACKEND_STATEMENT_TIMEOUT_MS = 1234

        with reporting_statement_timeout():
            assert self._show() == "1234ms"

    def test_raeumt_hinterher_auf(self, settings):
        settings.REPORTING_BACKEND_STATEMENT_TIMEOUT_MS = 1234
        vorher = self._show()

        with reporting_statement_timeout():
            pass

        assert self._show() == vorher

    def test_raeumt_auch_bei_exception_auf(self, settings):
        settings.REPORTING_BACKEND_STATEMENT_TIMEOUT_MS = 1234
        vorher = self._show()

        with pytest.raises(RuntimeError), reporting_statement_timeout():
            raise RuntimeError("kaputt")

        assert self._show() == vorher

    def test_langsame_query_wird_abgebrochen(self, settings):
        """Der Deckel wirkt wirklich — ``QueryCanceled`` kommt als ``OperationalError``."""
        settings.REPORTING_BACKEND_STATEMENT_TIMEOUT_MS = 50

        with pytest.raises(OperationalError), reporting_statement_timeout(), connection.cursor() as cur:
            cur.execute("SELECT pg_sleep(1)")

    def test_timeout_im_export_wird_isoliert(self, client, admin_user, settings):
        """``QueryCanceled`` ist kein Sonderfall — dieselbe 502-Antwort wie jeder Fehler."""

        class _LangsamesBackend(_BasisBackend):
            name = "Langsames Backend"

            def build_dataset(self, facility, date_from, date_to):
                with connection.cursor() as cur:
                    cur.execute("SELECT pg_sleep(1)")
                return DatasetResult(mode="rows", records=[{"wert": _ROHWERT}], summary={}, completeness=[])

        register_backend("langsam")(_LangsamesBackend)
        settings.REPORTING_BACKEND_STATEMENT_TIMEOUT_MS = 50
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), _export_params("langsam"))

        assert response.status_code == 502


class TestExportBrichtNieNachDenHeadernAb:
    """Fix-Runde 1 / H1: der CSV-Renderer streamt — nichts Backend-Eigenes darf mitstreamen.

    Cross-Review zu Refs #2070  + kimi-k3) hat reproduziert, dass ein
    Backend, das den ``DatasetResult``-Vertrag bricht, **mid-stream** wirft:
    ``safe_download_response`` hatte da bereits HTTP 200 und
    ``Content-Disposition`` gesetzt, Metadaten- und Kopfzeile waren schon im
    Body. Ergebnis war eine abgeschnittene Datei statt eines klaren Fehlers —
    kein k-Anon-Leck (die Suppression ist zu dem Zeitpunkt gelaufen), aber ein
    Bruch der zugesagten Semantik „502, keine halbe Datei".

    Der Schnitt liegt deshalb bei „backend-eigen": ``records``, die Projektion
    ``record.get(spec.key)`` und die ``FieldSpec``-Werte werden **vor** dem
    Response-Bau ausgewertet; lazy bleibt nur noch das Chunking durch den
    ``csv.writer`` ueber repo-eigene Strings.
    """

    @pytest.fixture
    def lazy_records_backend(self):
        """``records`` ist ein Generator, der erst nach zwei Zeilen wirft."""

        class _LazyRecordsBackend(_BasisBackend):
            name = "Lazy-Records-Backend"

            def build_dataset(self, facility, date_from, date_to):
                def _records():
                    yield {"wert": 100}
                    yield {"wert": 200}
                    raise RuntimeError("records kaputt")

                return DatasetResult(mode="rows", records=_records(), summary={}, completeness=[])

        return register_backend("lazy_records")(_LazyRecordsBackend)

    @pytest.fixture
    def kein_dict_record_backend(self):
        """Ein Record ist kein Mapping — ``record.get`` existiert gar nicht."""

        class _KeinDictRecordBackend(_BasisBackend):
            name = "Kein-Dict-Backend"

            def build_dataset(self, facility, date_from, date_to):
                return DatasetResult(mode="rows", records=[f"KEIN-DICT-{_ROHWERT}"], summary={}, completeness=[])

        return register_backend("kein_dict")(_KeinDictRecordBackend)

    @pytest.fixture
    def werfendes_record_get_backend(self):
        """Vertragskonformer ``dict``-Untertyp, dessen ``get()`` wirft Reproduktion)."""

        class _RaisingRecord(dict):
            def get(self, key, default=None):
                raise RuntimeError("record.get kaputt")

        class _WerfendesRecordGetBackend(_BasisBackend):
            name = "Werfendes-Record-Backend"

            def build_dataset(self, facility, date_from, date_to):
                return DatasetResult(mode="rows", records=[_RaisingRecord(wert=_ROHWERT)], summary={}, completeness=[])

        return register_backend("werfendes_record")(_WerfendesRecordGetBackend)

    @pytest.fixture
    def werfendes_label_backend(self):
        """``FieldSpec.label`` wirft beim Zugriff — die Kopfzeile lag bisher im Generator."""

        class _WerfendesLabel:
            key = "wert"

            @property
            def label(self):
                raise RuntimeError("label kaputt")

        class _WerfendesLabelBackend(_BasisBackend):
            name = "Werfendes-Label-Backend"

            def get_field_specs(self):
                return [_WerfendesLabel()]

        return register_backend("werfendes_label")(_WerfendesLabelBackend)

    @pytest.fixture
    def unbekannte_population_backend(self):
        """``population`` ausserhalb von ``POPULATION_NOTES`` — KeyError in der Metadatenzeile."""

        class _UnbekanntePopulationBackend(_BasisBackend):
            name = "Unbekannte-Population-Backend"
            population = "gibt-es-nicht"

        return register_backend("unbekannte_population")(_UnbekanntePopulationBackend)

    @pytest.mark.parametrize(
        "slug",
        ["lazy_records", "kein_dict", "werfendes_record", "werfendes_label", "unbekannte_population"],
    )
    def test_csv_antwortet_502_statt_abgeschnittener_datei(
        self,
        client,
        admin_user,
        slug,
        lazy_records_backend,
        kein_dict_record_backend,
        werfendes_record_get_backend,
        werfendes_label_backend,
        unbekannte_population_backend,
    ):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), _export_params(slug, fmt="csv"))

        assert response.status_code == 502
        assert "Content-Disposition" not in response
        # Kein Streaming-Rest: die Antwort ist eine gewoehnliche HttpResponse.
        assert not response.streaming
        assert str(_ROHWERT).encode() not in response.content

    @pytest.mark.parametrize("fmt", ["json", "xml"])
    def test_json_und_xml_verhalten_sich_gleich(self, client, admin_user, fmt, lazy_records_backend):
        """Paritaet der Formate: auch die eager Renderer duerfen keinen Header setzen."""
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), _export_params("lazy_records", fmt=fmt))

        assert response.status_code == 502
        assert "Content-Disposition" not in response

    def test_intakter_csv_export_bleibt_ein_stream(self, client, admin_user, doc_type_contact, sample_event):
        """Regression: der Erfolgspfad streamt weiter (keine Voll-Materialisierung der Datei)."""
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), _export_params("sample-report-1", fmt="csv"))

        assert response.status_code == 200
        assert response.streaming
        assert "Content-Disposition" in response
        assert b"sample-report-1" in b"".join(response.streaming_content)


class TestSachberichtPdfIsoliert:
    """Fix-Runde 1 / M1: der hybride Sachbericht-PDF-Pfad ruft ``build_dataset`` selbst.

    ``SampleReport1ExportView`` geht nicht ueber ``export_dataset``, sondern
    ueber ``generate_sample_report_1_pdf`` →
    ``get_sample_report_1_statistics_hybrid`` → im Live-Fallback
    ``get_backend("sample-report-1").build_dataset(...)``. Dieser Pfad war
    weder gefangen noch gedeckelt und lieferte reproduzierbar HTTP 500
    (Cross-Review Befund M1). Die Suppression laeuft dort unveraendert ueber
    ``suppress_sample_report_1_stats`` — die Isolation aendert daran nichts.
    """

    @pytest.fixture
    def kaputter_sachbericht(self, monkeypatch):
        """Ersetzt ``sample-report-1`` in der isolierten Registry durch ein werfendes Backend.

        Kein ``register_backend``: der Slug ist bereits vergeben, eine zweite
        Registrierung waere ``ImproperlyConfigured``. Der PDF-Pfad loest das
        Backend ueber ``get_backend("sample-report-1")`` auf, also genuegt der
        Austausch des Registry-Eintrags.
        """
        from core.reporting import registry

        class _KaputterSachbericht(_BasisBackend):
            slug = "sample-report-1"
            name = "Kaputter Sachbericht"
            snapshot_enabled = True

            def build_dataset(self, facility, date_from, date_to):
                raise RuntimeError("sachbericht kaputt")

        monkeypatch.setitem(registry._REGISTRY, "sample-report-1", _KaputterSachbericht())

    def test_pdf_antwortet_502_statt_500(self, client, admin_user, kaputter_sachbericht):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_sample_report_1_export"), _export_params("sample-report-1"))

        assert response.status_code == 502
        assert "Content-Disposition" not in response

    def test_kein_export_audit_eintrag(self, client, admin_user, facility, kaputter_sachbericht):
        client.force_login(admin_user)

        client.get(reverse("core:statistics_sample_report_1_export"), _export_params("sample-report-1"))

        assert not AuditLog.objects.filter(action=AuditLog.Action.EXPORT, target_type="Sachbericht-PDF").exists()

    def test_intaktes_pdf_bleibt_ein_download(self, client, admin_user, doc_type_contact, sample_event):
        """Regression: der Erfolgspfad liefert weiter das PDF."""
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_sample_report_1_export"), _export_params("sample-report-1"))

        assert response.status_code == 200
        assert "Content-Disposition" in response
        assert response.content.startswith(b"%PDF")
