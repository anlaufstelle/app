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
from core.services.dashboard import create_or_update_snapshot

pytestmark = pytest.mark.django_db

# Der Wert, den ein defektes Backend aus ``build_dataset`` mitbringt. Er darf in
# KEINER Fehlerantwort auftauchen — weder im Panel-HTML noch im Export-Body.
_ROHWERT = 4711


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

    def test_retention_goes_on_through_all_months_and_keeps_the_events(
        self, facility, staff_user, client_identified, _mit_kaputtem_snapshot_backend, monkeypatch
    ):
        """A broken backend used to abort the whole batch of months.

        Refs #2425: the backend cannot summarise deleted events either, so no
        event is deleted uncounted — each one is kept and logged, and the run
        still tries every month instead of stopping at the first.
        """
        import core.retention.enforcement as enforcement

        for month in (1, 2, 3):
            self._event(facility, staff_user, client_identified, timezone.make_aware(datetime(2025, month, 15, 10, 0)))
        attempted = []
        carry = enforcement.carry_deleted_event

        def recording_carry(facility_arg, event):
            attempted.append(timezone.localtime(event.occurred_at).month)
            return carry(facility_arg, event)

        monkeypatch.setattr(enforcement, "carry_deleted_event", recording_carry)
        count, _ = enforcement._soft_delete_events(
            Event.objects.filter(facility=facility), facility=facility, category="identified", retention_days=1
        )

        assert count == 0
        assert sorted(attempted) == [1, 2, 3]
        assert Event.objects.filter(facility=facility, is_deleted=False).count() == 3


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
    """Evaluate backend-owned values before sending CSV response headers.

    Refs #2070: a backend violating the ``DatasetResult`` contract could
    raise mid-stream, after ``safe_download_response`` had set HTTP 200
    and ``Content-Disposition`` and emitted metadata and column headers.
    The result was a truncated file instead of a clear 502 response.
    Suppression had already run, so this was not an anonymity leak.

    Evaluate ``records``, ``record.get(spec.key)`` and ``FieldSpec`` values
    before building the response. Only ``csv.writer`` chunking of strings
    produced by the application remains lazy.
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
        """A contract-compliant ``dict`` subclass whose ``get()`` raises."""

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
    """M1: the hybrid sample report PDF path calls ``build_dataset`` itself.

    The PDF of the sample report runs through the generic export
    (``format=pdf``): the hybrid reader calls
    ``get_backend("sample-report-1").build_dataset(...)`` in the live fallback.
    The catch of the generic download must cover this path as well
    (cross-review finding M1): HTTP 502 instead of 500, no attachment, no
    audit entry.
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

        response = client.get(reverse("core:statistics_report_export"), _export_params("sample-report-1", fmt="pdf"))

        assert response.status_code == 502
        assert "Content-Disposition" not in response

    def test_kein_export_audit_eintrag(self, client, admin_user, facility, kaputter_sachbericht):
        client.force_login(admin_user)

        client.get(reverse("core:statistics_report_export"), _export_params("sample-report-1", fmt="pdf"))

        assert not AuditLog.objects.filter(action=AuditLog.Action.EXPORT, target_type="DatasetExport").exists()

    def test_intaktes_pdf_bleibt_ein_download(self, client, admin_user, doc_type_contact, sample_event):
        """Regression: der Erfolgspfad liefert weiter das PDF."""
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), _export_params("sample-report-1", fmt="pdf"))

        assert response.status_code == 200
        assert "Content-Disposition" in response
        assert response.content.startswith(b"%PDF")


# ===================================================================
# Refs #2118 — dieselbe Isolation ausserhalb der Statistik-Views.
#
# ``is_available`` laeuft nicht nur auf dem Dashboard: die Registry ruft es in
# ``get_available_backends``/``get_backend_or_404`` und damit mittelbar in
# ``describe_type_bindings``/``describe_field_bindings`` — also auf zwei
# Konfigurationsseiten und in einem Wartungskommando. Dort war es bis #2118
# ungeschuetzt. Die Isolation sitzt seitdem in der Registry, nicht in den vier
# Aufrufern.
# ===================================================================


class TestRegistryVerfuegbarkeitIsoliert:
    """Die Registry selbst behandelt ein werfendes Backend als „nicht verfuegbar"."""

    def test_get_available_backends_laesst_das_defekte_backend_weg(self, facility, kaputte_verfuegbarkeit_backend):
        from core.reporting.registry import get_available_backends, get_backends

        slugs = {backend.slug for backend in get_available_backends(facility)}

        assert "kaputt_verfuegbar" not in slugs
        assert "kaputt_verfuegbar" in {backend.slug for backend in get_backends()}, (
            "Das Testdouble muss registriert sein, sonst ist der Test tautologisch"
        )

    def test_get_available_backends_meldet_den_fehler_an_den_aufrufer(self, facility, kaputte_verfuegbarkeit_backend):
        """``on_error`` ist der Weg, auf dem ein Kommando den Ausfall sichtbar macht."""
        from core.reporting.registry import get_available_backends

        gemeldet = []
        get_available_backends(facility, on_error=lambda backend: gemeldet.append(backend.slug))

        assert gemeldet == ["kaputt_verfuegbar"]

    def test_get_backend_or_404_antwortet_404_statt_500(self, facility, kaputte_verfuegbarkeit_backend):
        from django.http import Http404

        from core.reporting.registry import get_backend_or_404

        with pytest.raises(Http404):
            get_backend_or_404(facility, "kaputt_verfuegbar")

    def test_intaktes_backend_bleibt_erreichbar(self, facility, kaputte_verfuegbarkeit_backend):
        """Gegenprobe: der Fang nimmt nicht die uebrigen Backends mit."""
        from core.reporting.registry import get_backend_or_404

        assert get_backend_or_404(facility, "sample-report-1").slug == "sample-report-1"


class TestKonfigSeitenIsoliert:
    """``describe_*_bindings`` steht auf zwei Konfigurationsseiten (Refs #2118)."""

    def test_dokumenttyp_detail_laedt_trotz_kaputter_verfuegbarkeit(
        self, client, admin_user, facility, kaputte_verfuegbarkeit_backend
    ):
        doc_type = DocumentType.objects.create(
            facility=facility, name="Wegwerf-Bindungstyp", report_key="needle_exchange"
        )
        client.force_login(admin_user)

        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type.pk}))

        assert response.status_code == 200
        assert "Kaputte Verfuegbarkeit" not in response.content.decode()

    def test_dokumenttyp_formular_laedt_und_zeigt_die_intakten_bindungen(
        self, client, admin_user, facility, kaputte_verfuegbarkeit_backend
    ):
        doc_type = DocumentType.objects.create(
            facility=facility, name="Wegwerf-Bindungstyp", report_key="needle_exchange"
        )
        client.force_login(admin_user)

        response = client.get(reverse("core:config_doctype_update", kwargs={"pk": doc_type.pk}))

        assert response.status_code == 200
        # Der intakte Bestandsbericht bleibt in der Auskunft — nur das defekte
        # Backend faellt heraus (kein Totalausfall der Bindungsliste).
        assert "Beispiel-Sachbericht (Spritzentausch)" in response.content.decode()

    def test_feldvorlagen_formular_laedt_trotz_kaputter_verfuegbarkeit(
        self, client, admin_user, facility, kaputte_verfuegbarkeit_backend
    ):
        from core.models import FieldTemplate

        field_template = FieldTemplate.objects.create(facility=facility, name="Sonstiges Feld", field_type="text")
        client.force_login(admin_user)

        response = client.get(reverse("core:config_fieldtemplate_update", kwargs={"pk": field_template.pk}))

        assert response.status_code == 200

    def test_feldvorlagen_post_fehlerpfad_laedt_trotz_kaputter_verfuegbarkeit(
        self, client, admin_user, facility, kaputte_verfuegbarkeit_backend
    ):
        """Der zweite ``describe_field_bindings``-Aufruf sitzt im POST-Fehlerpfad."""
        from core.models import FieldTemplate

        field_template = FieldTemplate.objects.create(facility=facility, name="Sonstiges Feld", field_type="text")
        client.force_login(admin_user)

        response = client.post(
            reverse("core:config_fieldtemplate_update", kwargs={"pk": field_template.pk}),
            {"name": "", "field_type": "text", "options_json": "[]", "is_active": "on"},
        )

        assert response.status_code == 200
        assert response.context["form"].errors


class TestInventarKommandoIsoliert:
    """``inventory_report_bindings`` laeuft durch und benennt den Ausfall (Refs #2118)."""

    def test_lauf_geht_durch_und_warnt_auf_stderr(self, facility, kaputte_verfuegbarkeit_backend):
        from io import StringIO

        from django.core.management import call_command

        out, err = StringIO(), StringIO()
        call_command("inventory_report_bindings", "--facility", str(facility.pk), stdout=out, stderr=err)

        assert facility.name in out.getvalue()
        warnung = err.getvalue()
        assert "kaputt_verfuegbar" in warnung, warnung
        assert facility.name in warnung, warnung


class TestSnapshotKommandoIsoliertJeEinrichtung:
    """Ein Fehler in EINER Einrichtung darf die folgenden nicht mitreissen (Refs #2118)."""

    @pytest.fixture
    def zwei_einrichtungen_mit_settings(self, facility, second_facility):
        from core.models import Settings

        for einrichtung in (facility, second_facility):
            Settings.objects.get_or_create(
                facility=einrichtung,
                defaults={
                    "retention_anonymous_days": 90,
                    "retention_identified_days": 365,
                    "retention_qualified_days": 3650,
                },
            )
        return facility, second_facility

    @staticmethod
    def _nur_diese_einrichtung_wirft(monkeypatch, kaputte_facility):
        from core.management.commands import create_statistics_snapshots as cmd

        echt = cmd.create_or_update_snapshot

        def _fake(einrichtung, year, month):
            if einrichtung.pk == kaputte_facility.pk:
                raise RuntimeError("get_statistics kaputt")
            return echt(einrichtung, year, month)

        monkeypatch.setattr(cmd, "create_or_update_snapshot", _fake)

    def test_folge_einrichtung_bekommt_ihren_snapshot(self, zwei_einrichtungen_mit_settings, monkeypatch):
        from io import StringIO

        from django.core.management import call_command
        from django.core.management.base import CommandError

        kaputt, heil = zwei_einrichtungen_mit_settings
        self._nur_diese_einrichtung_wirft(monkeypatch, kaputt)

        err = StringIO()
        with pytest.raises(CommandError) as fehler:
            call_command(
                "create_statistics_snapshots", year=2025, month=1, stdout=StringIO(), stderr=err, skip_checks=True
            )

        assert StatisticsSnapshot.objects.filter(facility=heil, year=2025, month=1).exists(), (
            "Die zweite Einrichtung wurde vom Fehler der ersten mitgerissen (#2118)"
        )
        assert not StatisticsSnapshot.objects.filter(facility=kaputt).exists()
        assert kaputt.name in str(fehler.value), str(fehler.value)
        assert "1" in str(fehler.value)
        assert kaputt.name in err.getvalue()

    def test_lauf_ohne_fehler_bleibt_erfolgreich(self, zwei_einrichtungen_mit_settings):
        from io import StringIO

        from django.core.management import call_command

        kaputt, heil = zwei_einrichtungen_mit_settings

        # Refs #2583: only the due month proves a successful scheduled run.
        previous_month = timezone.localdate().replace(day=1) - timedelta(days=1)
        call_command("create_statistics_snapshots", stdout=StringIO(), stderr=StringIO())

        assert StatisticsSnapshot.objects.filter(year=previous_month.year, month=previous_month.month).count() == 2
        assert AuditLog.objects.filter(action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED).exists()

    def test_dry_run_bleibt_unveraendert(self, zwei_einrichtungen_mit_settings, monkeypatch):
        """``--dry-run`` ruft ``create_or_update_snapshot`` gar nicht auf."""
        from io import StringIO

        from django.core.management import call_command

        kaputt, _heil = zwei_einrichtungen_mit_settings
        self._nur_diese_einrichtung_wirft(monkeypatch, kaputt)

        call_command(
            "create_statistics_snapshots", year=2025, month=1, dry_run=True, stdout=StringIO(), stderr=StringIO()
        )

        assert StatisticsSnapshot.objects.count() == 0
